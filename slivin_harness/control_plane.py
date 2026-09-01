from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

CONTROL_PLANE_VERSION = "controller-plane.v1"
SELF_VERIFY_RECEIPT_VERSION = "self-verify-receipt.v2"


class ControlPlaneError(RuntimeError):
    """The Controller's private state or path boundary is invalid."""


class ArtifactVisibility(str, Enum):
    PRIVATE = "PRIVATE"
    PUBLIC = "PUBLIC"
    SCRATCH = "SCRATCH"


def _looks_windows_absolute(raw: str) -> bool:
    value = PureWindowsPath(raw)
    return bool(value.drive or value.root)


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_artifact_name(raw: str, *, field: str = "artifact") -> str:
    """Validate a run-relative artifact name on POSIX and Windows.

    This intentionally rejects absolute paths, drive-qualified paths, UNC paths,
    dot traversal, and empty path components before touching the filesystem.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ControlPlaneError(f"{field} must be a non-empty relative path")
    if "\x00" in raw:
        raise ControlPlaneError(f"Unsafe {field}: NUL is forbidden")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or _looks_windows_absolute(raw):
        raise ControlPlaneError(f"{field} must be run-relative: {raw!r}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ControlPlaneError(f"Unsafe {field}: {raw!r}")
    for part in parts:
        if ":" in part or part.endswith((".", " ")):
            raise ControlPlaneError(f"Unsafe Windows {field} segment: {part!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise ControlPlaneError(f"Reserved Windows {field} segment: {part!r}")
    return "/".join(parts)


def canonical_path(path: Path) -> Path:
    """Return a filesystem-canonical path without requiring the leaf to exist.

    Native Windows may expose the same directory through different lexical
    spellings (case, junctions, long/short-name aliases, or an unresolved
    temporary-directory path).  Security and ownership checks must compare
    canonical filesystem identities rather than ``Path.is_relative_to()``,
    which is intentionally lexical.
    """
    return path.expanduser().resolve(strict=False)


def is_within(root: Path, candidate: Path) -> bool:
    """Return True when candidate canonically resolves under root.

    ``os.path.commonpath`` also rejects cross-drive comparisons on Windows.
    """
    try:
        root_resolved = canonical_path(root)
        candidate_resolved = canonical_path(candidate)
        common = os.path.commonpath([str(root_resolved), str(candidate_resolved)])
        return os.path.normcase(common) == os.path.normcase(str(root_resolved))
    except (OSError, RuntimeError, ValueError):
        return False


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    temp.write_bytes(payload)
    os.replace(temp, path)


@dataclass(frozen=True)
class SelfVerifyBinding:
    candidate_id: str
    task_contract_rev: int | None
    plan_rev: int | None
    implementation_contract_rev: int | None
    verification_plan_rev: int | None
    runtime_env_id: int | None
    attempt_id: int
    check_registry_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ControllerPlane:
    """Controller-owned state outside the agent-writable workspace.

    Public artifacts remain in the run root for compatibility. Authoritative
    state is stored in ``controller_private`` and must never be passed into an
    agent prompt or execution environment.
    """

    def __init__(self, run_root: Path) -> None:
        self.run_root = canonical_path(run_root)
        self.private_root = self.run_root / "controller_private"
        self.private_root.mkdir(parents=True, exist_ok=True)
        self._secret_path = self.private_root / ".receipt_key"
        if not self._secret_path.exists():
            _atomic_write(self._secret_path, secrets.token_bytes(32))
            try:
                self._secret_path.chmod(0o600)
            except OSError:
                # Native Windows ACLs are outside pathlib chmod semantics; the
                # file still remains outside the agent-writable worktree.
                pass
        self.write_private_json(
            "control_plane.json",
            {
                "schema_version": CONTROL_PLANE_VERSION,
                "private_root": "controller_private",
                "authoritative": True,
            },
        )

    def path_for(self, name: str, visibility: ArtifactVisibility) -> Path:
        rel = safe_artifact_name(name)
        if visibility == ArtifactVisibility.PRIVATE:
            path = self.private_root / rel
        elif visibility == ArtifactVisibility.PUBLIC:
            path = self.run_root / rel
        else:
            path = self.run_root / "scratch" / rel
        if not is_within(
            self.private_root if visibility == ArtifactVisibility.PRIVATE else self.run_root,
            path,
        ):
            raise ControlPlaneError(f"Artifact escaped its root: {name!r}")
        return path

    def write_json(
        self,
        name: str,
        value: object,
        *,
        visibility: ArtifactVisibility,
    ) -> Path:
        path = self.path_for(name, visibility)
        payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        _atomic_write(path, payload)
        return path

    def write_text(
        self,
        name: str,
        value: str,
        *,
        visibility: ArtifactVisibility,
    ) -> Path:
        path = self.path_for(name, visibility)
        _atomic_write(path, value.encode("utf-8"))
        return path

    def write_bytes(
        self,
        name: str,
        value: bytes,
        *,
        visibility: ArtifactVisibility,
    ) -> Path:
        path = self.path_for(name, visibility)
        _atomic_write(path, value)
        return path

    def write_private_json(self, name: str, value: object) -> Path:
        return self.write_json(name, value, visibility=ArtifactVisibility.PRIVATE)

    def write_public_json(self, name: str, value: object) -> Path:
        return self.write_json(name, value, visibility=ArtifactVisibility.PUBLIC)

    def write_json_once(
        self,
        name: str,
        value: object,
        *,
        visibility: ArtifactVisibility,
    ) -> Path:
        path = self.path_for(name, visibility)
        if path.exists():
            raise ControlPlaneError(f"Immutable artifact already exists: {name}")
        return self.write_json(name, value, visibility=visibility)

    def _receipt_key(self) -> bytes:
        return self._secret_path.read_bytes()

    def keyed_fingerprint(self, payload: bytes, *, context: str) -> str:
        """Return a private domain-separated HMAC fingerprint.

        Sensitive runtime inputs such as `.env` must be compared without
        publishing a reusable plain SHA-256 digest that could support offline
        guessing of low-entropy values.  The key never leaves the Controller
        private plane and `context` prevents digest reuse across paths/types.
        """

        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if not isinstance(context, str) or not context:
            raise ValueError("context must be a non-empty string")
        message = context.encode("utf-8") + b"\0" + payload
        return hmac.new(self._receipt_key(), message, hashlib.sha256).hexdigest()

    def issue_self_verify_receipt(
        self,
        *,
        binding: SelfVerifyBinding,
        claim: Mapping[str, Any],
        name: str = "self_verify_receipt_current.json",
    ) -> Path:
        body = {
            "schema_version": SELF_VERIFY_RECEIPT_VERSION,
            "passed": True,
            "binding": binding.to_dict(),
            "claim_sha256": hashlib.sha256(
                json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest(),
        }
        mac = hmac.new(
            self._receipt_key(),
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
            hashlib.sha256,
        ).hexdigest()
        receipt = {**body, "receipt_hmac_sha256": mac}
        return self.write_private_json(name, receipt)

    def verify_self_verify_receipt(
        self,
        *,
        binding: SelfVerifyBinding,
        name: str = "self_verify_receipt_current.json",
    ) -> bool:
        path = self.path_for(name, ArtifactVisibility.PRIVATE)
        if not path.is_file():
            return False
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            received_mac = str(receipt.pop("receipt_hmac_sha256"))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
        expected_mac = hmac.new(
            self._receipt_key(),
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
            hashlib.sha256,
        ).hexdigest()
        return bool(
            hmac.compare_digest(received_mac, expected_mac)
            and receipt.get("passed") is True
            and receipt.get("binding") == binding.to_dict()
        )
