from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
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
CANDIDATE_BASELINE_MISSING = "CANDIDATE_BASELINE_MISSING"
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


def _normalize_rel(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def _excluded(rel: str, prefixes: Iterable[str]) -> bool:
    normalized = _normalize_rel(rel)
    for raw in prefixes:
        prefix = _normalize_rel(raw)
        if any(marker in prefix for marker in "*?["):
            if fnmatch.fnmatchcase(normalized, prefix) or fnmatch.fnmatchcase(
                normalized.casefold(), prefix.casefold()
            ):
                return True
            continue
        if prefix and (normalized == prefix or normalized.startswith(prefix + "/")):
            return True
    return False


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
        exclusions = tuple(sorted({_normalize_rel(item) for item in excluded_prefixes if item}))
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
        exclusions = tuple(sorted({_normalize_rel(item) for item in excluded_prefixes if item}))
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


@dataclass(frozen=True)
class _ControlFile:
    key: str
    path: Path
    kind: str
    payload: bytes | None
    mode: int | None

    @property
    def fingerprint(self) -> str:
        body = self.payload if self.payload is not None else b"<MISSING>"
        return hashlib.sha256(self.kind.encode() + b"\0" + body).hexdigest()


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


def _read_control_path(key: str, path: Path) -> _ControlFile:
    if path.is_symlink():
        raise GitControlIntegrityError(
            GIT_CONTROL_STATE_UNSUPPORTED, f"Git control path is an alias: {key}"
        )
    try:
        if path.exists() and bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_UNSUPPORTED, f"Git control path is a reparse point: {key}"
            )
    except OSError as exc:
        raise GitControlIntegrityError(
            GIT_CONTROL_STATE_UNSUPPORTED, f"Git control path cannot be inspected: {key}"
        ) from exc
    if not path.exists():
        return _ControlFile(key, path, "missing", None, None)
    if path.is_file():
        return _ControlFile(key, path, "file", path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    if path.is_dir():
        rows: list[dict[str, str]] = []
        for item in sorted(path.rglob("*"), key=lambda value: value.as_posix().casefold()):
            if (
                item.is_symlink()
                or bool(getattr(item.lstat(), "st_file_attributes", 0) & 0x400)
                or (item.exists() and not (item.is_file() or item.is_dir()))
            ):
                raise GitControlIntegrityError(
                    GIT_CONTROL_STATE_UNSUPPORTED, f"Unsupported Git control entry: {key}"
                )
            rel = item.relative_to(path).as_posix().encode("utf-8", errors="surrogateescape")
            if item.is_dir():
                rows.append(
                    {
                        "kind": "directory",
                        "path": rel.decode("utf-8", errors="surrogateescape"),
                        "mode": stat.S_IMODE(item.stat().st_mode),
                    }
                )
            else:
                rows.append(
                    {
                        "kind": "file",
                        "path": rel.decode("utf-8", errors="surrogateescape"),
                        "mode": stat.S_IMODE(item.stat().st_mode),
                        "payload": base64.b64encode(item.read_bytes()).decode("ascii"),
                    }
                )
        return _ControlFile(
            key,
            path,
            "directory",
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            None,
        )
    raise GitControlIntegrityError(GIT_CONTROL_STATE_UNSUPPORTED, f"Unsupported Git control path: {key}")


class GitControlIntegrityManager:
    """Freeze and guard worktree Git metadata independently from candidate files."""

    def __init__(self, *, workspace: Path, control_plane: ControllerPlane) -> None:
        self.workspace = canonical_path(workspace)
        self.control_plane = control_plane
        self.baseline: GitControlStateBaseline | None = None
        self._events: list[dict[str, str]] = []

    @property
    def active(self) -> bool:
        return self.baseline is not None

    def _control_paths(self) -> tuple[tuple[str, Path], ...]:
        dot_git = self.workspace / ".git"
        paths: dict[str, Path] = {
            "index": _git_path(self.workspace, "index"),
            "head": _git_path(self.workspace, "HEAD"),
            "config": _git_path(self.workspace, "config"),
            "config-worktree": _git_path(self.workspace, "config.worktree"),
            "info-exclude": _git_path(self.workspace, "info/exclude"),
            "info-attributes": _git_path(self.workspace, "info/attributes"),
            "sparse-checkout": _git_path(self.workspace, "info/sparse-checkout"),
            "harness-excludes": self.workspace / ".harness_git_excludes",
        }
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
            paths["core-excludes-file"] = canonical_path(path)
        attributes = _local_config_path(self.workspace, "core.attributesFile")
        if attributes:
            path = Path(attributes)
            if not path.is_absolute():
                path = self.workspace / path
            paths["core-attributes-file"] = canonical_path(path)
        hooks = _local_config_path(self.workspace, "core.hooksPath")
        hooks_path = Path(hooks) if hooks else _git_path(self.workspace, "hooks")
        if hooks and not hooks_path.is_absolute():
            hooks_path = self.workspace / hooks_path
        paths["hooks"] = canonical_path(hooks_path)
        index = paths["index"]
        for shared in sorted(index.parent.glob("sharedindex.*")):
            paths[f"shared-index:{shared.name}"] = shared
        return tuple(sorted(paths.items()))

    def _snapshot(self) -> GitControlStateBaseline:
        files = tuple(_read_control_path(key, path) for key, path in self._control_paths())
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
            "git-dir": _git_bytes(self.workspace, "rev-parse", "--absolute-git-dir"),
            "common-dir": _git_bytes(
                self.workspace, "rev-parse", "--path-format=absolute", "--git-common-dir"
            ),
        }
        payload = {
            "files": [(item.key, item.kind, item.fingerprint) for item in files],
            "semantic": {key: hashlib.sha256(value).hexdigest() for key, value in semantic.items()},
        }
        return GitControlStateBaseline(files, semantic, stable_fingerprint(payload, length=64))

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
            "control_paths": [item.key for item in self.baseline.files] if self.baseline else [],
        }
        self.control_plane.write_private_json("git_control_integrity_private.json", private)

    def _matches(self, current: GitControlStateBaseline) -> bool:
        return self.baseline is not None and current.fingerprint == self.baseline.fingerprint

    def _restore(self) -> None:
        if self.baseline is None:
            raise GitControlIntegrityError(GIT_CONTROL_STATE_BASELINE_MISMATCH, "Git baseline missing")
        baseline_paths = {item.key: item for item in self.baseline.files}
        current_paths = {key: path for key, path in self._control_paths()}
        git_dir = canonical_path(
            Path(
                self.baseline.semantic["git-dir"]
                .decode("utf-8", errors="surrogateescape")
                .strip()
            )
        )
        common_dir = canonical_path(
            Path(
                self.baseline.semantic["common-dir"]
                .decode("utf-8", errors="surrogateescape")
                .strip()
            )
        )
        for key, original in baseline_paths.items():
            path = current_paths.get(key, original.path)
            try:
                try:
                    current = _read_control_path(key, path)
                except GitControlIntegrityError:
                    current = None
                if current is not None and (
                    current.kind,
                    current.payload,
                    current.mode,
                ) == (original.kind, original.payload, original.mode):
                    continue
                canonical_parent = canonical_path(path.parent)
                if not any(
                    is_within(root, canonical_parent)
                    for root in (self.workspace, git_dir, common_dir)
                ):
                    raise GitControlIntegrityError(
                        GIT_CONTROL_STATE_RESTORE_FAILED,
                        "Git control state outside Controller-owned roots cannot be restored",
                    )
                if original.kind == "missing":
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    elif path.exists() or path.is_symlink():
                        path.unlink()
                elif original.kind == "file":
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original.payload or b"")
                    if original.mode is not None:
                        try:
                            path.chmod(original.mode)
                        except OSError:
                            pass
                elif original.kind == "directory":
                    if path.exists() and not path.is_dir():
                        path.unlink()
                    path.mkdir(parents=True, exist_ok=True)
                    for child in list(path.iterdir()):
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child)
                        else:
                            child.unlink(missing_ok=True)
                    rows = json.loads((original.payload or b"[]").decode("utf-8"))
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
        if not self._matches(self._snapshot()):
            raise GitControlIntegrityError(
                GIT_CONTROL_STATE_RESTORE_FAILED, "Restored Git control state did not match baseline"
            )

    def _restore_after_mutation(self, batch_id: str) -> tuple[str, ...]:
        try:
            self._restore()
        except GitControlIntegrityError:
            self._events.append(
                {"batch_id": batch_id, "event": GIT_CONTROL_STATE_RESTORE_FAILED}
            )
            return (GIT_CONTROL_STATE_RESTORE_FAILED,)
        return ()

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
