"""Private, reproducible candidate checkpoints; not acceptance or process resume."""
from __future__ import annotations

from slivin_harness.boundaries import boundary

import hashlib
import os
import json
import re
import stat
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .control_plane import ArtifactVisibility, ControllerPlane, is_within
from .impact import safe_impact_path
from .run_state import build_candidate_identity
from .build_identity import source_manifest
from .execution import EXECUTION_BROKER_VERSION, ROLE_EXECUTION_CONTEXT_VERSION
from .control_plane import CONTROL_PLANE_VERSION, SELF_VERIFY_RECEIPT_VERSION, safe_artifact_name

CHECKPOINT_VERSION = "candidate-checkpoint.v2"

# Controller-owned artifacts only. Never enumerate/copy the receipt key, local
# configuration or environment. Role caches/runtime copies are reproducible
# inputs, while authored proof scripts/results are sealed before scratch reset.
_CONTROLLER_EVIDENCE = re.compile(r"(?:check_registry|verification_plan(?:_\d+)?|execution_policy|project_runtime(?:_replan)?_\d+|runtime_evidence_\d+|contract_closure_\d+|planner_tool_evidence_\d+|self_verify_receipt.*)\.json\Z")
_SENSITIVE_NAMES = {".env", ".receipt_key", "credentials", "credentials.json", "auth.json", "config.toml", "id_rsa", "id_ed25519"}


def _link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _stable_file_bytes(path: Path, *, reason_code: str) -> bytes:
    """Read strict authoritative/registered evidence without accepting path replacement."""
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or _link_or_junction(path):
            raise RuntimeError(reason_code)
        with path.open("rb") as source:
            opened_before = os.fstat(source.fileno())
            raw = source.read()
            opened_after = os.fstat(source.fileno())
        after = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise RuntimeError(reason_code) from exc
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
    )
    if (identity(before) != identity(opened_before)
            or identity(opened_before) != identity(opened_after)
            or identity(opened_after) != identity(after)
            or len(raw) != after.st_size):
        raise RuntimeError(reason_code)
    return raw


def _registered_role_path(*, workspace: Path, path: Path) -> tuple[Path, str]:
    scratch = (workspace / ".harness_tmp").resolve()
    candidate = path if path.is_absolute() else workspace / path
    absolute = candidate.absolute()
    try:
        relative = absolute.relative_to(workspace.absolute())
    except ValueError as exc:
        raise RuntimeError("CHECKPOINT_DURABLE_EVIDENCE_BOUNDARY") from exc
    if relative.parts[:1] != (".harness_tmp",) or len(relative.parts) < 4:
        raise RuntimeError("CHECKPOINT_DURABLE_EVIDENCE_BOUNDARY")
    if relative.parts[1] not in {"planner", "implementer", "evaluator"}:
        raise RuntimeError("CHECKPOINT_DURABLE_EVIDENCE_BOUNDARY")
    current = workspace.absolute()
    for part in relative.parts:
        current = current / part
        if current.exists() and _link_or_junction(current):
            raise RuntimeError("CHECKPOINT_EVIDENCE_LINK")
    try:
        resolved = absolute.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeError("CHECKPOINT_DURABLE_EVIDENCE_UNAVAILABLE") from exc
    if not is_within(scratch, resolved) or not resolved.is_file():
        raise RuntimeError("CHECKPOINT_DURABLE_EVIDENCE_BOUNDARY")
    name = resolved.name.lower()
    if (name in _SENSITIVE_NAMES or name.startswith(".env.")
            or resolved.suffix.lower() in {".key", ".pem", ".pfx", ".p12"}):
        raise RuntimeError("CHECKPOINT_DURABLE_EVIDENCE_SENSITIVE")
    return resolved, resolved.relative_to(workspace.resolve()).as_posix()


def seal_evidence(
    *, plane: ControllerPlane, workspace: Path,
    durable_role_evidence: Sequence[Path] = (),
) -> tuple[list[dict], list[dict]]:
    evidence, excluded = [], []
    def seal(path: Path, kind: str, locator: str, *, strict_code: str):
        if _link_or_junction(path):
            raise RuntimeError("CHECKPOINT_EVIDENCE_LINK")
        raw = _stable_file_bytes(path, reason_code=strict_code)
        digest = hashlib.sha256(raw).hexdigest()
        artifact = f"checkpoints/evidence/{digest}"
        plane.write_bytes(artifact, raw, visibility=ArtifactVisibility.PRIVATE)
        evidence.append(dict(kind=kind, original_locator=locator, artifact=artifact, sha256=digest, size=len(raw)))
    for path in sorted(plane.private_root.glob("*.json")):
        if _CONTROLLER_EVIDENCE.fullmatch(path.name):
            seal(path, "CONTROLLER_EVIDENCE", path.name,
                 strict_code="CHECKPOINT_CONTROLLER_EVIDENCE_UNAVAILABLE")
    for role in ("planner", "implementer", "evaluator"):
        root = workspace / ".harness_tmp" / role
        if not root.exists():
            continue
        if not is_within(workspace / ".harness_tmp", root) or _link_or_junction(root):
            raise RuntimeError("CHECKPOINT_SCRATCH_BOUNDARY")
        excluded.append({
            "locator": root.relative_to(workspace).as_posix(),
            "reason": "UNREGISTERED_VOLATILE_ROLE_SCRATCH",
            "scope": "TREE",
            "authority": "FORENSIC_ONLY",
        })
    seen: set[Path] = set()
    for requested in durable_role_evidence:
        path, locator = _registered_role_path(workspace=workspace, path=Path(requested))
        if path in seen:
            raise RuntimeError("CHECKPOINT_DURABLE_EVIDENCE_DUPLICATE")
        seen.add(path)
        seal(path, "REGISTERED_DURABLE_ROLE_EVIDENCE", locator,
             strict_code="CHECKPOINT_DURABLE_EVIDENCE_UNAVAILABLE")
    return evidence, excluded


def verify_checkpoint_evidence(plane: ControllerPlane, checkpoint: dict) -> None:
    """Forensic integrity verification only; this never issues a fresh receipt."""
    for entry in [*checkpoint["evidence"], *[row for row in checkpoint["entries"] if "blob" in row]]:
        name = safe_artifact_name(entry.get("artifact", entry.get("blob")))
        if not name.startswith("checkpoints/"):
            raise RuntimeError("CHECKPOINT_EVIDENCE_BOUNDARY")
        path = plane.path_for(name, ArtifactVisibility.PRIVATE)
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("CHECKPOINT_EVIDENCE_MISSING_OR_CHANGED")


@boundary("B17")
def save_report_checkpoint(*, plane: ControllerPlane, workspace: Path, name: str,
                           contract: dict, run_state=None, stamp_path: Path | None = None,
                           check_registry_digest: str | None = None,
                           durable_role_evidence: Sequence[Path] = ()) -> dict:
    """Seal changed physical bytes and known evidence before report validation.

    Baseline SHA plus file blobs/deletions reconstruct the observed candidate.
    Blobs use digest filenames, never agent-supplied output paths. Runtime and
    credentials excluded by the Controller candidate baseline stay excluded.
    A second observation detects concurrent writes. Failure is not PASS.
    """
    before = build_candidate_identity(workspace)
    entries = []
    for item in before.entries:
        entry = dict(item)
        relative = safe_impact_path(item["path"], field="checkpoint.path")
        path = workspace / relative
        if not is_within(workspace, path.parent):
            raise RuntimeError("CHECKPOINT_PATH_ESCAPE")
        if item["state"] in {"file", "symlink"}:
            raw = (os.readlink(path).encode("utf-8", errors="surrogateescape")
                   if item["state"] == "symlink" else path.read_bytes())
            digest = hashlib.sha256(raw).hexdigest()
            if digest != item["sha256"]:
                raise RuntimeError("CHECKPOINT_CANDIDATE_CHANGED")
            blob = f"checkpoints/blobs/{digest}"
            destination = plane.path_for(blob, ArtifactVisibility.PRIVATE)
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise RuntimeError("CHECKPOINT_BLOB_CONFLICT")
            else:
                plane.write_bytes(blob, raw, visibility=ArtifactVisibility.PRIVATE)
            entry["blob"] = blob
        elif item["state"] != "deleted":
            raise RuntimeError("CHECKPOINT_UNSUPPORTED_ENTRY")
        entries.append(entry)
    evidence, exclusions = seal_evidence(
        plane=plane, workspace=workspace,
        durable_role_evidence=durable_role_evidence,
    )
    contract_raw = json.dumps(contract, ensure_ascii=False, sort_keys=True).encode("utf-8")
    contract_digest = hashlib.sha256(contract_raw).hexdigest()
    contract_artifact = f"checkpoints/evidence/{contract_digest}"
    plane.write_bytes(contract_artifact, contract_raw, visibility=ArtifactVisibility.PRIVATE)
    evidence.append(dict(kind="ACTIVE_IMPLEMENTATION_CONTRACT", artifact=contract_artifact, sha256=contract_digest))
    if stamp_path is not None and stamp_path.exists():
        # Only the Controller-generated stamp locator is accepted here. No
        # arbitrary report evidence string is interpreted as a filesystem path.
        if not is_within(workspace / ".harness_tmp", stamp_path) or not stamp_path.is_file():
            raise RuntimeError("CHECKPOINT_STAMP_BOUNDARY")
        raw = _stable_file_bytes(
            stamp_path, reason_code="CHECKPOINT_SELF_VERIFY_STAMP_UNAVAILABLE",
        )
        digest = hashlib.sha256(raw).hexdigest()
        artifact = f"checkpoints/evidence/{digest}.json"
        plane.write_bytes(artifact, raw, visibility=ArtifactVisibility.PRIVATE)
        evidence.append(dict(kind="UNTRUSTED_SELF_VERIFY_STAMP", artifact=artifact, sha256=digest))
    after = build_candidate_identity(workspace)
    if before != after:
        raise RuntimeError("CHECKPOINT_CANDIDATE_CHANGED")
    checkpoint = dict(schema_version=CHECKPOINT_VERSION, status="SAVED_UNVERIFIED",
                      harness_version=__version__, candidate=before.to_dict(), entries=entries,
                      protocol_versions=dict(controller=CONTROL_PLANE_VERSION, receipt=SELF_VERIFY_RECEIPT_VERSION,
                          execution=EXECUTION_BROKER_VERSION, role_context=ROLE_EXECUTION_CONTEXT_VERSION),
                      harness_source=source_manifest(Path(__file__).resolve().parents[1]),
                      contract_fingerprint=contract["fingerprint"],
                      source_inventory=contract["source_inventory"],
                      revision_binding=run_state.verification_binding(candidate_id=before.candidate_id,
                          check_registry_digest=check_registry_digest) if run_state else None,
                      evidence=evidence, evidence_exclusions=exclusions, cross_process_resume=False)
    verify_checkpoint_evidence(plane, checkpoint)
    plane.write_private_json(f"checkpoints/{name}.json", checkpoint)
    return checkpoint
