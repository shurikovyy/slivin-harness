from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TypeVar

from slivin_harness.control_plane import ControllerPlane, canonical_path, is_within
from slivin_harness.protocol import stable_fingerprint


GIT_CONTROL_INTEGRITY_VERSION = "git-control-integrity.v1"
GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH = "GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH"
GIT_CONTROL_STATE_MUTATED_DURING_BATCH = "GIT_CONTROL_STATE_MUTATED_DURING_BATCH"
GIT_CONTROL_STATE_RESTORE_FAILED = "GIT_CONTROL_STATE_RESTORE_FAILED"
GIT_CONTROL_STATE_BASELINE_MISMATCH = "GIT_CONTROL_STATE_BASELINE_MISMATCH"
GIT_CONTROL_STATE_UNSUPPORTED = "GIT_CONTROL_STATE_UNSUPPORTED"
GIT_CONTROL_STATE_UNSAFE_RETARGET = "GIT_CONTROL_STATE_UNSAFE_RETARGET"
GIT_CONTROL_STATE_DETECT_ONLY_MUTATION = "GIT_CONTROL_STATE_DETECT_ONLY_MUTATION"
GIT_CONTROL_STATE_DIRECTORY_LIMIT = "GIT_CONTROL_STATE_DIRECTORY_LIMIT"
CANDIDATE_BASELINE_MISSING = "CANDIDATE_BASELINE_MISSING"
CANDIDATE_EXCLUSION_OVERLAPS_TRACKED_PATH = (
    "CANDIDATE_EXCLUSION_OVERLAPS_TRACKED_PATH"
)
TRUSTED_BATCH_MUTATED_CANDIDATE = "TRUSTED_BATCH_MUTATED_CANDIDATE"

_T = TypeVar("_T")


class GitControlIntegrityError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        secondary_reason_codes: Iterable[str] = (),
    ) -> None:
        self.reason_code = reason_code
        self.secondary_reason_codes = tuple(dict.fromkeys(secondary_reason_codes))
        super().__init__(message)


class CandidateInventoryError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class TrustedBatchIntegrityError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@contextmanager
def isolated_git_index_environment(
    *,
    workspace: Path,
    scratch_root: Path,
    environment: Mapping[str, str],
) -> Iterable[dict[str, str]]:
    """Give a trusted command a disposable HEAD index instead of the real index.

    Even read-looking Git commands such as ``git diff --check`` may refresh
    index stat data.  Trusted project commands therefore inherit a temporary
    index initialized from HEAD.  Explicit writes to repository control files
    remain visible to :class:`GitControlIntegrityManager`.
    """

    env = {str(key): str(value) for key, value in environment.items()}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=env,
    )
    if probe.returncode != 0 or probe.stdout.strip().lower() != b"true":
        yield env
        return

    scratch_root.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="git-index-", dir=scratch_root))
    index_path = temp_root / "index"
    hooks_path = temp_root / "hooks"
    hooks_path.mkdir()
    env.update(
        {
            "GIT_INDEX_FILE": str(index_path),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(hooks_path),
        }
    )
    try:
        initialized = subprocess.run(
            ["git", "read-tree", "HEAD"],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=env,
        )
        if initialized.returncode != 0:
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_UNSUPPORTED,
                "Trusted command isolated Git index could not be initialized",
            )
        yield env
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _normalize_rel(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def _excluded(rel: str, prefixes: Iterable[str]) -> bool:
    normalized = _normalize_rel(rel)
    for raw in prefixes:
        prefix = _normalize_rel(raw)
        if prefix and (normalized == prefix or normalized.startswith(prefix + "/")):
            return True
    return False


def _validated_exclusions(values: Iterable[str]) -> tuple[str, ...]:
    exclusions: set[str] = set()
    for raw in values:
        value = str(raw)
        normalized = _normalize_rel(value)
        path = Path(value.replace("\\", "/"))
        if (
            not normalized
            or path.is_absolute()
            or ".." in path.parts
            or any(marker in normalized for marker in "*?[")
        ):
            raise CandidateInventoryError(
                "CANDIDATE_EXCLUSION_INVALID",
                f"Candidate exclusion must be an explicit repo-relative path: {value!r}",
            )
        exclusions.add(normalized)
    return tuple(sorted(exclusions))


def _tracked_paths(workspace: Path, baseline_sha: str) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--name-only", baseline_sha],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        shell=False,
    )
    if completed.returncode != 0:
        raise CandidateInventoryError(
            CANDIDATE_BASELINE_MISSING, "Tracked candidate baseline is unavailable"
        )
    return tuple(
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in completed.stdout.split(b"\0")
        if item
    )


def _validate_exclusions_do_not_hide_tracked(
    workspace: Path,
    *,
    baseline_sha: str,
    exclusions: Iterable[str],
) -> None:
    tracked = _tracked_paths(workspace, baseline_sha)
    for prefix in exclusions:
        if prefix == ".git":
            continue
        for rel in tracked:
            if rel == prefix or rel.startswith(prefix + "/"):
                raise CandidateInventoryError(
                    CANDIDATE_EXCLUSION_OVERLAPS_TRACKED_PATH,
                    "Controller candidate exclusion overlaps tracked baseline path: "
                    + prefix,
                )


def _hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


@dataclass(frozen=True)
class CandidateInventoryEntry:
    path: str
    state: str
    mode: str
    sha256: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "state": self.state,
            "mode": self.mode,
            "sha256": self.sha256,
            "size": self.size,
        }


def scan_candidate_tree(
    workspace: Path,
    *,
    excluded_prefixes: Iterable[str],
) -> tuple[CandidateInventoryEntry, ...]:
    """Inventory physical candidate files without consulting Git ignore/index state."""

    root = canonical_path(workspace)
    entries: list[CandidateInventoryEntry] = []
    casefolded: dict[str, str] = {}

    def visit(directory: Path, relative_directory: str = "") -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise CandidateInventoryError(
                "CANDIDATE_INVENTORY_FAILED", "Candidate tree could not be enumerated"
            ) from exc
        for child in children:
            rel = f"{relative_directory}/{child.name}" if relative_directory else child.name
            rel = _normalize_rel(rel)
            if _excluded(rel, excluded_prefixes):
                continue
            folded = rel.casefold()
            previous = casefolded.get(folded)
            if previous is not None and previous != rel:
                raise CandidateInventoryError(
                    "CANDIDATE_CASE_COLLISION",
                    f"Candidate paths collide case-insensitively: {previous!r}, {rel!r}",
                )
            casefolded[folded] = rel
            path = Path(child.path)
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise CandidateInventoryError(
                    "CANDIDATE_INVENTORY_FAILED", f"Candidate entry vanished: {rel}"
                ) from exc
            mode = metadata.st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                raw = target.encode("utf-8", errors="surrogateescape")
                entries.append(
                    CandidateInventoryEntry(
                        rel, "symlink", "120000", hashlib.sha256(raw).hexdigest(), len(raw)
                    )
                )
            elif bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
                raise CandidateInventoryError(
                    "CANDIDATE_UNSUPPORTED_ENTRY",
                    f"Candidate reparse-point entry is unsupported: {rel}",
                )
            elif stat.S_ISREG(mode):
                digest, size = _hash_file(path)
                git_mode = "100755" if mode & stat.S_IXUSR else "100644"
                entries.append(CandidateInventoryEntry(rel, "file", git_mode, digest, size))
            elif stat.S_ISDIR(mode):
                visit(path, rel)
            else:
                raise CandidateInventoryError(
                    "CANDIDATE_UNSUPPORTED_ENTRY",
                    f"Unsupported candidate entry type: {rel}",
                )

    visit(root)
    return tuple(sorted(entries, key=lambda item: item.path))


@dataclass(frozen=True)
class CandidateWorkspaceBaseline:
    workspace: Path
    baseline_sha: str
    excluded_prefixes: tuple[str, ...]
    entries: tuple[CandidateInventoryEntry, ...]
    baseline_id: str

    @classmethod
    def capture(
        cls,
        workspace: Path,
        *,
        baseline_sha: str,
        excluded_prefixes: Iterable[str],
        control_plane: ControllerPlane | None = None,
    ) -> "CandidateWorkspaceBaseline":
        root = canonical_path(workspace)
        exclusions = _validated_exclusions(excluded_prefixes)
        _validate_exclusions_do_not_hide_tracked(
            root, baseline_sha=baseline_sha, exclusions=exclusions
        )
        entries = scan_candidate_tree(root, excluded_prefixes=exclusions)
        payload = {
            "baseline_sha": baseline_sha,
            "excluded_prefixes": list(exclusions),
            "entries": [item.to_dict() for item in entries],
        }
        baseline_id = stable_fingerprint(payload, length=64)
        baseline = cls(root, baseline_sha, exclusions, entries, baseline_id)
        _CANDIDATE_BASELINES[os.path.normcase(str(root))] = baseline
        if control_plane is not None:
            control_plane.write_private_json(
                "candidate_workspace_baseline.json",
                {
                    "schema_version": "candidate-workspace-baseline.v1",
                    "baseline_sha": baseline_sha,
                    "baseline_id": baseline_id,
                    "excluded_prefixes": list(exclusions),
                    "entries": [item.to_dict() for item in entries],
                },
            )
        return baseline

    @classmethod
    def from_git_tree(
        cls,
        workspace: Path,
        *,
        baseline_sha: str,
        excluded_prefixes: Iterable[str],
    ) -> "CandidateWorkspaceBaseline":
        """Compatibility baseline from immutable commit objects, never the real index."""

        root = canonical_path(workspace)
        exclusions = _validated_exclusions(excluded_prefixes)
        _validate_exclusions_do_not_hide_tracked(
            root, baseline_sha=baseline_sha, exclusions=exclusions
        )
        tree = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--long", baseline_sha],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            check=False,
        )
        if tree.returncode != 0:
            raise CandidateInventoryError(CANDIDATE_BASELINE_MISSING, "Git baseline tree is unavailable")
        entries: list[CandidateInventoryEntry] = []
        folded: dict[str, str] = {}
        for raw in tree.stdout.split(b"\0"):
            if not raw:
                continue
            metadata, raw_path = raw.split(b"\t", 1)
            mode, _kind, oid, raw_size = metadata.split()
            rel = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
            if _excluded(rel, exclusions):
                continue
            previous = folded.get(rel.casefold())
            if previous is not None and previous != rel:
                raise CandidateInventoryError(
                    "CANDIDATE_CASE_COLLISION",
                    f"Candidate paths collide case-insensitively: {previous!r}, {rel!r}",
                )
            folded[rel.casefold()] = rel
            blob = subprocess.run(
                ["git", "cat-file", "blob", oid.decode("ascii")],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
                check=False,
            )
            if blob.returncode != 0:
                raise CandidateInventoryError(
                    CANDIDATE_BASELINE_MISSING, f"Git baseline blob is unavailable: {rel}"
                )
            state = "symlink" if mode == b"120000" else "file"
            entries.append(
                CandidateInventoryEntry(
                    rel,
                    state,
                    mode.decode("ascii"),
                    hashlib.sha256(blob.stdout).hexdigest(),
                    int(raw_size),
                )
            )
        payload = {
            "baseline_sha": baseline_sha,
            "excluded_prefixes": list(exclusions),
            "entries": [item.to_dict() for item in entries],
        }
        baseline = cls(
            root,
            baseline_sha,
            exclusions,
            tuple(sorted(entries, key=lambda item: item.path)),
            stable_fingerprint(payload, length=64),
        )
        _CANDIDATE_BASELINES[os.path.normcase(str(root))] = baseline
        return baseline

    def current_entries(self) -> tuple[CandidateInventoryEntry, ...]:
        return scan_candidate_tree(self.workspace, excluded_prefixes=self.excluded_prefixes)

    def changed_entries(self) -> tuple[dict[str, Any], ...]:
        before = {item.path: item for item in self.entries}
        after = {item.path: item for item in self.current_entries()}
        changed: list[dict[str, Any]] = []
        for rel in sorted(set(before) | set(after)):
            old = before.get(rel)
            new = after.get(rel)
            if old == new:
                continue
            if new is None:
                changed.append({"path": rel, "state": "deleted", "mode": old.mode if old else None})
            else:
                changed.append(new.to_dict())
        return tuple(changed)


_CANDIDATE_BASELINES: dict[str, CandidateWorkspaceBaseline] = {}


def candidate_baseline_for(workspace: Path) -> CandidateWorkspaceBaseline | None:
    return _CANDIDATE_BASELINES.get(os.path.normcase(str(canonical_path(workspace))))


def clear_candidate_baseline(workspace: Path) -> None:
    _CANDIDATE_BASELINES.pop(os.path.normcase(str(canonical_path(workspace))), None)


GIT_CONTROL_MAX_ENTRIES = 8_192
GIT_CONTROL_MAX_TOTAL_BYTES = 64 * 1024 * 1024
GIT_CONTROL_MAX_DEPTH = 32
GIT_CONTROL_MAX_SINGLE_FILE_BYTES = 16 * 1024 * 1024


class GitControlOwnership(str, Enum):
    WORKTREE_CONTROLLER_OWNED = "WORKTREE_CONTROLLER_OWNED"
    SHARED_OR_EXTERNAL_DETECT_ONLY = "SHARED_OR_EXTERNAL_DETECT_ONLY"
    DISPOSABLE_STANDALONE_REPO = "DISPOSABLE_STANDALONE_REPO"


@dataclass(frozen=True)
class GitControlPath:
    key: str
    path: Path
    ownership: GitControlOwnership

    @property
    def restoreable(self) -> bool:
        return self.ownership in {
            GitControlOwnership.WORKTREE_CONTROLLER_OWNED,
            GitControlOwnership.DISPOSABLE_STANDALONE_REPO,
        }


@dataclass(frozen=True)
class _ControlFile:
    key: str
    path: Path
    ownership: GitControlOwnership
    kind: str
    payload: bytes | None
    restore_payload: bytes | None
    mode: int | None

    @property
    def fingerprint(self) -> str:
        body = self.payload if self.payload is not None else b"<MISSING>"
        return hashlib.sha256(self.kind.encode() + b"\0" + body).hexdigest()

    @property
    def restoreable(self) -> bool:
        return self.ownership in {
            GitControlOwnership.WORKTREE_CONTROLLER_OWNED,
            GitControlOwnership.DISPOSABLE_STANDALONE_REPO,
        }


@dataclass(frozen=True)
class GitControlStateBaseline:
    files: tuple[_ControlFile, ...]
    semantic: Mapping[str, bytes]
    fingerprint: str


def _git_bytes(workspace: Path, *args: str, check: bool = True) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if check and completed.returncode != 0:
        raise GitControlIntegrityError(
            GIT_CONTROL_STATE_UNSUPPORTED,
            "Git control state could not be inspected",
        )
    return completed.stdout


def _git_path(workspace: Path, name: str) -> Path:
    raw = _git_bytes(workspace, "rev-parse", "--path-format=absolute", "--git-path", name)
    return Path(raw.decode("utf-8", errors="surrogateescape").strip())


def _local_config_path(workspace: Path, key: str) -> str:
    """Return only repository/worktree config, never mutable global config."""

    for scope in ("--worktree", "--local"):
        raw = _git_bytes(
            workspace,
            "config",
            scope,
            "--path",
            "--get",
            key,
            check=False,
        ).decode("utf-8", errors="surrogateescape").strip()
        if raw:
            return raw
    return ""


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _read_file_bounded(path: Path, *, capture: bool) -> tuple[str, int, bytes | None]:
    digest = hashlib.sha256()
    size = 0
    chunks: list[bytes] | None = [] if capture else None
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > GIT_CONTROL_MAX_SINGLE_FILE_BYTES:
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_DIRECTORY_LIMIT,
                    "Git control file exceeds the bounded snapshot limit",
                )
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
    return digest.hexdigest(), size, b"".join(chunks) if chunks is not None else None


def _read_control_path(spec: GitControlPath) -> _ControlFile:
    key = spec.key
    path = spec.path
    cursor = path
    while True:
        if cursor.exists() or cursor.is_symlink():
            try:
                metadata = cursor.lstat()
            except OSError as exc:
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_UNSUPPORTED,
                    f"Git control path cannot be inspected: {key}",
                ) from exc
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_UNSUPPORTED,
                    f"Git control path contains an alias: {key}",
                )
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    try:
        if path.exists() and _is_reparse(path.lstat()):
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_UNSUPPORTED, f"Git control path is a reparse point: {key}"
            )
    except OSError as exc:
        raise GitControlIntegrityError(
            GIT_CONTROL_STATE_UNSUPPORTED, f"Git control path cannot be inspected: {key}"
        ) from exc
    if not path.exists():
        return _ControlFile(key, path, spec.ownership, "missing", None, None, None)
    if path.is_file():
        digest, size, content = _read_file_bounded(path, capture=spec.restoreable)
        payload = json.dumps(
            {"sha256": digest, "size": size}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return _ControlFile(
            key,
            path,
            spec.ownership,
            "file",
            payload,
            content,
            stat.S_IMODE(path.stat().st_mode),
        )
    if path.is_dir():
        rows: list[dict[str, Any]] = []
        restore_rows: list[dict[str, Any]] | None = [] if spec.restoreable else None
        entry_count = 0
        total_bytes = 0

        def visit(directory: Path, relative: str, depth: int) -> None:
            nonlocal entry_count, total_bytes
            if depth > GIT_CONTROL_MAX_DEPTH:
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_DIRECTORY_LIMIT,
                    "Git control directory exceeds the bounded depth limit",
                )
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
            except OSError as exc:
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_UNSUPPORTED,
                    f"Git control directory cannot be inspected: {key}",
                ) from exc
            for child in children:
                entry_count += 1
                if entry_count > GIT_CONTROL_MAX_ENTRIES:
                    raise GitControlIntegrityError(
                        GIT_CONTROL_STATE_DIRECTORY_LIMIT,
                        "Git control directory exceeds the bounded entry limit",
                    )
                rel = f"{relative}/{child.name}" if relative else child.name
                item = Path(child.path)
                metadata = item.lstat()
                if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                    raise GitControlIntegrityError(
                        GIT_CONTROL_STATE_UNSUPPORTED,
                        f"Unsupported Git control entry: {key}",
                    )
                mode = stat.S_IMODE(metadata.st_mode)
                if stat.S_ISDIR(metadata.st_mode):
                    row = {"kind": "directory", "path": rel, "mode": mode}
                    rows.append(row)
                    if restore_rows is not None:
                        restore_rows.append(dict(row))
                    visit(item, rel, depth + 1)
                elif stat.S_ISREG(metadata.st_mode):
                    digest, size, content = _read_file_bounded(
                        item, capture=spec.restoreable
                    )
                    total_bytes += size
                    if total_bytes > GIT_CONTROL_MAX_TOTAL_BYTES:
                        raise GitControlIntegrityError(
                            GIT_CONTROL_STATE_DIRECTORY_LIMIT,
                            "Git control directory exceeds the bounded byte limit",
                        )
                    rows.append(
                        {
                            "kind": "file",
                            "path": rel,
                            "mode": mode,
                            "sha256": digest,
                            "size": size,
                        }
                    )
                    if restore_rows is not None:
                        restore_rows.append(
                            {
                                "kind": "file",
                                "path": rel,
                                "mode": mode,
                                "payload": base64.b64encode(content or b"").decode("ascii"),
                            }
                        )
                else:
                    raise GitControlIntegrityError(
                        GIT_CONTROL_STATE_UNSUPPORTED,
                        f"Unsupported Git control entry: {key}",
                    )

        visit(path, "", 0)
        return _ControlFile(
            key,
            path,
            spec.ownership,
            "directory",
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            (
                json.dumps(restore_rows, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
                if restore_rows is not None
                else None
            ),
            None,
        )
    raise GitControlIntegrityError(GIT_CONTROL_STATE_UNSUPPORTED, f"Unsupported Git control path: {key}")


def _remove_control_tree(path: Path) -> None:
    """Remove a previously bounded control tree without following filesystem aliases."""

    entries = 0

    def remove(item: Path, depth: int) -> None:
        nonlocal entries
        if depth > GIT_CONTROL_MAX_DEPTH:
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_RESTORE_FAILED,
                "Git control restore exceeded the bounded depth limit",
            )
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            return
        entries += 1
        if entries > GIT_CONTROL_MAX_ENTRIES:
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_RESTORE_FAILED,
                "Git control restore exceeded the bounded entry limit",
            )
        if stat.S_ISLNK(metadata.st_mode):
            item.unlink()
            return
        if _is_reparse(metadata):
            if stat.S_ISDIR(metadata.st_mode):
                item.rmdir()
            else:
                item.unlink()
            return
        if stat.S_ISDIR(metadata.st_mode):
            for child in sorted(os.scandir(item), key=lambda entry: entry.name.casefold()):
                remove(Path(child.path), depth + 1)
            item.rmdir()
            return
        if stat.S_ISREG(metadata.st_mode):
            item.unlink()
            return
        raise GitControlIntegrityError(
            GIT_CONTROL_STATE_RESTORE_FAILED,
            "Git control restore encountered an unsupported entry",
        )

    remove(path, 0)


class GitControlIntegrityManager:
    """Freeze and guard worktree Git metadata independently from candidate files."""

    def __init__(self, *, workspace: Path, control_plane: ControllerPlane) -> None:
        self.workspace = canonical_path(workspace)
        self.control_plane = control_plane
        self.baseline: GitControlStateBaseline | None = None
        self._events: list[dict[str, str]] = []
        self._git_dir: Path | None = None
        self._common_dir: Path | None = None

    @property
    def active(self) -> bool:
        return self.baseline is not None

    @staticmethod
    def _lexically_within(root: Path, candidate: Path) -> bool:
        try:
            common = os.path.commonpath([str(root.absolute()), str(candidate.absolute())])
        except (OSError, ValueError):
            return False
        return os.path.normcase(common) == os.path.normcase(str(root.absolute()))

    def _ownership_for(
        self,
        key: str,
        path: Path,
        *,
        git_dir: Path,
        common_dir: Path,
        standalone: bool,
    ) -> GitControlOwnership:
        if standalone and (
            self._lexically_within(git_dir, path)
            or self._lexically_within(common_dir, path)
        ):
            return GitControlOwnership.DISPOSABLE_STANDALONE_REPO
        if key == "harness-excludes":
            return GitControlOwnership.WORKTREE_CONTROLLER_OWNED
        if key in {
            "index",
            "config-worktree",
            "head",
            "workspace-dot-git",
            "commondir-pointer",
            "gitdir-pointer",
        } and self._lexically_within(git_dir, path):
            return GitControlOwnership.WORKTREE_CONTROLLER_OWNED
        if key.startswith("shared-index:") and self._lexically_within(git_dir, path):
            return GitControlOwnership.WORKTREE_CONTROLLER_OWNED
        return GitControlOwnership.SHARED_OR_EXTERNAL_DETECT_ONLY

    def _control_paths(self) -> tuple[GitControlPath, ...]:
        dot_git = self.workspace / ".git"
        git_dir = canonical_path(
            Path(
                _git_bytes(self.workspace, "rev-parse", "--absolute-git-dir")
                .decode("utf-8", errors="surrogateescape")
                .strip()
            )
        )
        common_dir = canonical_path(
            Path(
                _git_bytes(
                    self.workspace,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
                .decode("utf-8", errors="surrogateescape")
                .strip()
            )
        )
        self._git_dir = git_dir
        self._common_dir = common_dir
        standalone = is_within(self.workspace, git_dir) and is_within(
            self.workspace, common_dir
        )
        paths: dict[str, Path] = {
            "index": _git_path(self.workspace, "index"),
            "head": _git_path(self.workspace, "HEAD"),
            "config": _git_path(self.workspace, "config"),
            "config-worktree": _git_path(self.workspace, "config.worktree"),
            "info-exclude": _git_path(self.workspace, "info/exclude"),
            "info-attributes": _git_path(self.workspace, "info/attributes"),
            "sparse-checkout": _git_path(self.workspace, "info/sparse-checkout"),
            "harness-excludes": self.workspace / ".harness_git_excludes",
            "packed-refs": common_dir / "packed-refs",
            "refs": common_dir / "refs",
            "shallow": common_dir / "shallow",
            "objects-info-alternates": common_dir / "objects" / "info" / "alternates",
            "objects-info-grafts": common_dir / "objects" / "info" / "grafts",
            "commondir-pointer": git_dir / "commondir",
            "gitdir-pointer": git_dir / "gitdir",
        }
        if git_dir != common_dir:
            paths["worktree-refs"] = git_dir / "refs"
        if dot_git.is_file() or dot_git.is_symlink():
            paths["workspace-dot-git"] = dot_git
        symbolic = _git_bytes(self.workspace, "symbolic-ref", "-q", "HEAD", check=False).decode(
            "utf-8", errors="replace"
        ).strip()
        if symbolic:
            paths["head-ref"] = _git_path(self.workspace, symbolic)
        excludes = _local_config_path(self.workspace, "core.excludesFile")
        if excludes:
            path = Path(excludes)
            if not path.is_absolute():
                path = self.workspace / path
            paths["core-excludes-file"] = path.absolute()
        attributes = _local_config_path(self.workspace, "core.attributesFile")
        if attributes:
            path = Path(attributes)
            if not path.is_absolute():
                path = self.workspace / path
            paths["core-attributes-file"] = path.absolute()
        hooks = _local_config_path(self.workspace, "core.hooksPath")
        hooks_path = Path(hooks) if hooks else _git_path(self.workspace, "hooks")
        if hooks and not hooks_path.is_absolute():
            hooks_path = self.workspace / hooks_path
        paths["hooks"] = hooks_path.absolute()
        index = paths["index"]
        for shared in sorted(index.parent.glob("sharedindex.*")):
            paths[f"shared-index:{shared.name}"] = shared
        return tuple(
            GitControlPath(
                key,
                path.absolute(),
                self._ownership_for(
                    key,
                    path.absolute(),
                    git_dir=git_dir,
                    common_dir=common_dir,
                    standalone=standalone,
                ),
            )
            for key, path in sorted(paths.items())
        )

    def _fixed_config_value(self, key: str, files: Iterable[_ControlFile]) -> bytes:
        by_key = {item.key: item for item in files}
        for config_key in ("config-worktree", "config"):
            item = by_key.get(config_key)
            if item is None or item.kind != "file":
                continue
            completed = subprocess.run(
                [
                    "git",
                    "config",
                    "--no-includes",
                    "--file",
                    str(item.path),
                    "--get",
                    key,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            )
            if completed.returncode == 0:
                return completed.stdout.rstrip(b"\r\n")
        return b""

    def _semantic_snapshot(self, files: tuple[_ControlFile, ...]) -> dict[str, bytes]:
        semantic = {
            "head": _git_bytes(self.workspace, "rev-parse", "HEAD"),
            "symbolic-head": _git_bytes(self.workspace, "symbolic-ref", "-q", "HEAD", check=False),
            "index-stage": _git_bytes(self.workspace, "ls-files", "--stage", "-z"),
            "index-flags": _git_bytes(self.workspace, "ls-files", "-v", "-z"),
            "index-debug": _git_bytes(self.workspace, "ls-files", "--debug", "-z"),
            "local-config": _git_bytes(self.workspace, "config", "--local", "--null", "--list"),
            "worktree-config": _git_bytes(
                self.workspace, "config", "--worktree", "--null", "--list", check=False
            ),
            "git-dir": str(self._git_dir).encode("utf-8", errors="surrogateescape"),
            "common-dir": str(self._common_dir).encode("utf-8", errors="surrogateescape"),
        }
        for key in ("core.hooksPath", "core.excludesFile", "core.attributesFile"):
            semantic[f"config-target:{key}"] = self._fixed_config_value(key, files)
        return semantic

    @staticmethod
    def _build_baseline(
        files: tuple[_ControlFile, ...], semantic: Mapping[str, bytes]
    ) -> GitControlStateBaseline:
        payload = {
            "files": [(item.key, item.kind, item.fingerprint) for item in files],
            "semantic": {key: hashlib.sha256(value).hexdigest() for key, value in semantic.items()},
        }
        return GitControlStateBaseline(files, semantic, stable_fingerprint(payload, length=64))

    def _snapshot(self) -> GitControlStateBaseline:
        if self.baseline is None:
            specs = self._control_paths()
        else:
            specs = tuple(
                GitControlPath(item.key, item.path, item.ownership)
                for item in self.baseline.files
            )
        files = tuple(_read_control_path(spec) for spec in specs)
        if self.baseline is not None:
            baseline_files = {
                item.key: (item.kind, item.payload, item.mode)
                for item in self.baseline.files
            }
            current_files = {
                item.key: (item.kind, item.payload, item.mode) for item in files
            }
            if current_files != baseline_files:
                # Do not execute Git object/config resolution after a fixed
                # control path has changed. In particular, a mutated config
                # target can never select a directory to inspect.
                return self._build_baseline(files, {})
        return self._build_baseline(files, self._semantic_snapshot(files))

    def establish_baseline(self) -> GitControlStateBaseline:
        baseline = self._snapshot()
        self.baseline = baseline
        self._events.append({"batch_id": "setup", "event": "GIT_CONTROL_STATE_BASELINE_ESTABLISHED"})
        self._persist()
        return baseline

    def _persist(self) -> None:
        status = "PASS" if not any("FAILED" in item["event"] or "MUTATED" in item["event"] for item in self._events) else "FAIL"
        public = {
            "schema_version": GIT_CONTROL_INTEGRITY_VERSION,
            "status": status,
            "events": list(self._events),
        }
        self.control_plane.write_public_json("git_control_integrity.json", public)
        private = {
            **public,
            "baseline_fingerprint": self.baseline.fingerprint if self.baseline else None,
            "control_paths": [
                {"key": item.key, "ownership": item.ownership.value}
                for item in self.baseline.files
            ] if self.baseline else [],
        }
        self.control_plane.write_private_json("git_control_integrity_private.json", private)

    def _matches(self, current: GitControlStateBaseline) -> bool:
        return self.baseline is not None and current.fingerprint == self.baseline.fingerprint

    def _assert_restore_path_safe(self, item: _ControlFile) -> None:
        if self._git_dir is None or self._common_dir is None:
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_RESTORE_FAILED, "Git control roots are unavailable"
            )
        if item.ownership == GitControlOwnership.DISPOSABLE_STANDALONE_REPO:
            allowed = (self.workspace, self._git_dir, self._common_dir)
        else:
            allowed = (self.workspace, self._git_dir)
        if not any(self._lexically_within(root, item.path) for root in allowed):
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_RESTORE_FAILED,
                "Original Git control path is outside its restore authority",
            )
        current = item.path.parent
        while True:
            if current.exists() or current.is_symlink():
                metadata = current.lstat()
                if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                    raise GitControlIntegrityError(
                        GIT_CONTROL_STATE_RESTORE_FAILED,
                        "Git control restore path contains an alias",
                    )
            if any(os.path.normcase(str(current)) == os.path.normcase(str(root)) for root in allowed):
                break
            if current.parent == current:
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_RESTORE_FAILED,
                    "Git control restore path escaped its authority",
                )
            current = current.parent

    def _restore(self) -> tuple[str, ...]:
        if self.baseline is None:
            raise GitControlIntegrityError(GIT_CONTROL_STATE_BASELINE_MISMATCH, "Git baseline missing")
        reasons: list[str] = []
        for key in ("core.hooksPath", "core.excludesFile", "core.attributesFile"):
            current_value = self._fixed_config_value(key, self.baseline.files)
            if current_value != self.baseline.semantic.get(f"config-target:{key}", b""):
                reasons.append(GIT_CONTROL_STATE_UNSAFE_RETARGET)
                break
        for original in self.baseline.files:
            path = original.path
            try:
                try:
                    current = _read_control_path(
                        GitControlPath(original.key, path, original.ownership)
                    )
                except GitControlIntegrityError:
                    current = None
                if current is not None and (
                    current.kind,
                    current.payload,
                    current.mode,
                ) == (original.kind, original.payload, original.mode):
                    continue
                if not original.restoreable:
                    reasons.append(GIT_CONTROL_STATE_DETECT_ONLY_MUTATION)
                    continue
                self._assert_restore_path_safe(original)
                if current is None:
                    raise GitControlIntegrityError(
                        GIT_CONTROL_STATE_RESTORE_FAILED,
                        "Mutated Git control path is unsafe to restore",
                    )
                if original.kind == "missing":
                    if path.exists() or path.is_symlink():
                        _remove_control_tree(path)
                elif original.kind == "file":
                    if path.exists() or path.is_symlink():
                        _remove_control_tree(path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original.restore_payload or b"")
                    if original.mode is not None:
                        try:
                            path.chmod(original.mode)
                        except OSError:
                            pass
                elif original.kind == "directory":
                    if path.exists() or path.is_symlink():
                        _remove_control_tree(path)
                    path.mkdir(parents=True, exist_ok=True)
                    rows = json.loads((original.restore_payload or b"[]").decode("utf-8"))
                    for row in rows:
                        target = path / str(row["path"])
                        if row["kind"] == "directory":
                            target.mkdir(parents=True, exist_ok=True)
                        elif row["kind"] == "file":
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(base64.b64decode(str(row["payload"])))
                        try:
                            target.chmod(int(row["mode"]))
                        except OSError:
                            pass
            except OSError as exc:
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_RESTORE_FAILED, "Git control state restore failed"
                ) from exc
        for original in self.baseline.files:
            if not original.restoreable:
                continue
            current = _read_control_path(
                GitControlPath(original.key, original.path, original.ownership)
            )
            if (current.kind, current.payload, current.mode) != (
                original.kind,
                original.payload,
                original.mode,
            ):
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_RESTORE_FAILED,
                    "Restored Git control state did not match its original path",
                )
        return tuple(dict.fromkeys(reasons))

    def _restore_after_mutation(self, batch_id: str) -> tuple[str, ...]:
        try:
            reasons = self._restore()
            for reason in reasons:
                self._events.append({"batch_id": batch_id, "event": reason})
            return reasons
        except GitControlIntegrityError:
            self._events.append(
                {"batch_id": batch_id, "event": GIT_CONTROL_STATE_RESTORE_FAILED}
            )
            return (GIT_CONTROL_STATE_RESTORE_FAILED,)

    def run_batch(self, batch_id: str, operation: Callable[[], _T]) -> _T:
        if self.baseline is None:
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_BASELINE_MISMATCH, "Git control baseline was not established"
            )
        try:
            before = self._snapshot()
        except GitControlIntegrityError as exc:
            secondary = self._restore_after_mutation(batch_id)
            self._events.append(
                {"batch_id": batch_id, "event": GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH}
            )
            self._persist()
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH,
                "Git control state could not be validated before the batch",
                secondary_reason_codes=(exc.reason_code, *secondary),
            ) from exc
        if not self._matches(before):
            secondary = self._restore_after_mutation(batch_id)
            self._events.append({"batch_id": batch_id, "event": GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH})
            self._persist()
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH,
                "Git control state changed before the batch",
                secondary_reason_codes=secondary,
            )

        result: _T | None = None
        operation_error: BaseException | None = None
        try:
            result = operation()
        except BaseException as exc:  # post-check is mandatory for every outcome
            operation_error = exc
        try:
            after = self._snapshot()
        except GitControlIntegrityError as exc:
            secondary = self._restore_after_mutation(batch_id)
            self._events.append(
                {"batch_id": batch_id, "event": GIT_CONTROL_STATE_MUTATED_DURING_BATCH}
            )
            self._persist()
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_MUTATED_DURING_BATCH,
                "Git control state could not be validated after the batch",
                secondary_reason_codes=(exc.reason_code, *secondary),
            ) from operation_error or exc
        if not self._matches(after):
            secondary = self._restore_after_mutation(batch_id)
            self._events.append({"batch_id": batch_id, "event": GIT_CONTROL_STATE_MUTATED_DURING_BATCH})
            self._persist()
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_MUTATED_DURING_BATCH,
                "Git control state changed during the batch",
                secondary_reason_codes=secondary,
            ) from operation_error
        self._events.append({"batch_id": batch_id, "event": "GIT_CONTROL_STATE_PRISTINE"})
        self._persist()
        if operation_error is not None:
            raise operation_error
        return result  # type: ignore[return-value]


class TrustedBatchIntegrityCoordinator:
    """Compose Git, projected-runtime, and read-only candidate boundaries."""

    def __init__(
        self,
        *,
        git_manager: GitControlIntegrityManager,
        runtime_manager: Any | None,
        candidate_identity: Callable[[], Any],
    ) -> None:
        self.git_manager = git_manager
        self.runtime_manager = runtime_manager
        self.candidate_identity = candidate_identity

    def _guarded(self, batch_id: str, operation: Callable[[], _T]) -> _T:
        runtime_operation = (
            (lambda: self.runtime_manager.run_batch(batch_id, operation))
            if self.runtime_manager is not None
            else operation
        )
        return self.git_manager.run_batch(batch_id, runtime_operation)

    def run_candidate_mutating(self, batch_id: str, operation: Callable[[], _T]) -> _T:
        return self._guarded(batch_id, operation)

    def run_read_only(self, batch_id: str, operation: Callable[[], _T]) -> _T:
        before = self.candidate_identity()
        result: _T | None = None
        operation_error: BaseException | None = None
        try:
            result = self._guarded(batch_id, operation)
        except BaseException as exc:
            operation_error = exc
        after = self.candidate_identity()
        if before.candidate_id != after.candidate_id:
            raise TrustedBatchIntegrityError(
                TRUSTED_BATCH_MUTATED_CANDIDATE,
                f"Read-only trusted batch changed the candidate: {batch_id}",
            ) from operation_error
        if operation_error is not None:
            raise operation_error
        return result  # type: ignore[return-value]
