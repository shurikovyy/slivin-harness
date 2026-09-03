from __future__ import annotations

import hashlib
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from slivin_harness.control_plane import ControllerPlane, canonical_path, is_within
from slivin_harness.workspace import (
    RuntimeProjection,
    WorkspaceSession,
    _assert_independent_copy,
    _is_reparse_point,
    _safe_relative_path,
    assert_safe_runtime_path,
)

RUNTIME_PROJECTION_INTEGRITY_VERSION = "runtime-projection-integrity.v1"
RUNTIME_PROJECTION_BASELINE_VERSION = "runtime-projection-baseline.v1"

RUNTIME_PROJECTION_SOURCE_CHANGED = "RUNTIME_PROJECTION_SOURCE_CHANGED"
RUNTIME_PROJECTION_WORKSPACE_RESTORE_FAILED = (
    "RUNTIME_PROJECTION_WORKSPACE_RESTORE_FAILED"
)
RUNTIME_PROJECTION_BASELINE_MISMATCH = "RUNTIME_PROJECTION_BASELINE_MISMATCH"
RUNTIME_PROJECTION_MUTATED_DURING_CHECK = (
    "RUNTIME_PROJECTION_MUTATED_DURING_CHECK"
)
RUNTIME_PROJECTION_UNSUPPORTED_ENTRY = "RUNTIME_PROJECTION_UNSUPPORTED_ENTRY"

_FINGERPRINT_CHUNK_SIZE = 1024 * 1024
_T = TypeVar("_T")


class RuntimeProjectionIntegrityError(RuntimeError):
    """A safe, typed failure at the projected-runtime trust boundary."""

    def __init__(
        self,
        reason_code: str,
        *,
        relative_path: str,
        batch_id: str | None = None,
        restore_failed: bool = False,
    ) -> None:
        self.reason_code = reason_code
        self.relative_path = relative_path
        self.batch_id = batch_id
        self.restore_failed = restore_failed
        location = f" projection={relative_path}"
        batch = f" batch={batch_id}" if batch_id else ""
        super().__init__(reason_code + location + batch)


@dataclass(frozen=True)
class RuntimeTreeFingerprint:
    sha256: str
    entry_count: int
    total_file_bytes: int


def _feed_field(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _entry_is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _casefold_key(relative: str) -> str:
    return relative.replace("\\", "/").casefold()


def validate_runtime_casefold_paths(paths: list[str]) -> None:
    """Reject path spellings that collide on a Windows workspace."""

    seen: dict[str, str] = {}
    for relative in paths:
        key = _casefold_key(relative)
        previous = seen.get(key)
        if previous is not None and previous != relative:
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_UNSUPPORTED_ENTRY,
                relative_path=relative,
            )
        seen[key] = relative


def fingerprint_runtime_tree(
    root: Path,
    *,
    chunk_size: int = _FINGERPRINT_CHUNK_SIZE,
) -> RuntimeTreeFingerprint:
    """Stream a deterministic digest of a regular directory tree.

    The digest covers relative names, entry types, empty directories, file
    sizes, and complete regular-file contents. Filesystem iteration order is
    normalized explicitly. Links, reparse points, and special objects are
    rejected without following them.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    tree_root = Path(root)
    digest = hashlib.sha256()
    seen_casefold: dict[str, str] = {}
    entry_count = 0
    total_file_bytes = 0

    def fail(relative: str) -> RuntimeProjectionIntegrityError:
        return RuntimeProjectionIntegrityError(
            RUNTIME_PROJECTION_UNSUPPORTED_ENTRY,
            relative_path=relative or ".",
        )

    def visit(path: Path, relative: str) -> None:
        nonlocal entry_count, total_file_bytes
        try:
            info = path.lstat()
        except OSError as exc:
            raise fail(relative) from exc
        if stat.S_ISLNK(info.st_mode) or _entry_is_reparse(info):
            raise fail(relative)

        key = _casefold_key(relative)
        previous = seen_casefold.get(key)
        if previous is not None and previous != relative:
            raise fail(relative)
        seen_casefold[key] = relative
        encoded = relative.encode("utf-8", errors="surrogateescape")

        if stat.S_ISDIR(info.st_mode):
            entry_count += 1
            _feed_field(digest, b"directory")
            _feed_field(digest, encoded)
            try:
                with os.scandir(path) as entries:
                    children = sorted(
                        entries,
                        key=lambda item: (item.name.casefold(), item.name),
                    )
            except OSError as exc:
                raise fail(relative) from exc
            child_relatives = [
                child.name if not relative else f"{relative}/{child.name}"
                for child in children
            ]
            validate_runtime_casefold_paths(child_relatives)
            for child, child_relative in zip(children, child_relatives):
                visit(Path(child.path), child_relative)
            return

        if not stat.S_ISREG(info.st_mode):
            raise fail(relative)
        entry_count += 1
        total_file_bytes += int(info.st_size)
        _feed_field(digest, b"file")
        _feed_field(digest, encoded)
        _feed_field(digest, str(int(info.st_size)).encode("ascii"))
        bytes_read = 0
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise fail(relative) from exc
        if bytes_read != int(info.st_size):
            raise fail(relative)

    visit(tree_root, "")
    return RuntimeTreeFingerprint(
        sha256=digest.hexdigest(),
        entry_count=entry_count,
        total_file_bytes=total_file_bytes,
    )


@dataclass(frozen=True)
class _ProjectionBaseline:
    projection: RuntimeProjection
    relative_path: str
    keyed_fingerprint: str
    entry_count: int
    total_file_bytes: int


class RuntimeProjectionIntegrityManager:
    """Controller-private full-tree baseline plus just-in-time batch guard."""

    def __init__(
        self,
        *,
        session: WorkspaceSession,
        control_plane: ControllerPlane,
        removal_attempts: int = 3,
        retry_delay_seconds: float = 0.05,
    ) -> None:
        self.session = session
        self.control_plane = control_plane
        self.removal_attempts = max(1, int(removal_attempts))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self._baselines: dict[str, _ProjectionBaseline] = {}
        self._events: list[dict[str, Any]] = []
        self._established = False

    @property
    def active(self) -> bool:
        return bool(self.session.runtime_projections)

    def _record_event(
        self,
        event_code: str,
        *,
        relative_path: str,
        batch_id: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "event_code": event_code,
            "relative_path": relative_path,
        }
        if batch_id is not None:
            event["batch_id"] = str(batch_id)
        self._events.append(event)
        self.control_plane.write_public_json(
            "runtime_projection_integrity.json",
            {
                "schema_version": RUNTIME_PROJECTION_INTEGRITY_VERSION,
                "projection_roots": sorted(self._baselines),
                "events": list(self._events),
                "private_fingerprints_exposed": False,
            },
        )

    def _projection_paths(self, projection: RuntimeProjection) -> tuple[str, Path, Path]:
        if self.session.source_repo is None:
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_BASELINE_MISMATCH,
                relative_path=projection.relative_path,
            )
        relative = _safe_relative_path(projection.relative_path).as_posix()
        if not (
            projection.source_kind == "workspace.copy_untracked"
            and projection.is_directory
            and projection.copy_mode == "physical_copy"
            and projection.runtime_only
        ):
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_BASELINE_MISMATCH,
                relative_path=relative,
            )
        source_repo = canonical_path(self.session.source_repo)
        workspace = canonical_path(self.session.workspace)
        source = self.session.source_repo / relative
        destination = self.session.workspace / relative
        if not is_within(source_repo, source):
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_BASELINE_MISMATCH,
                relative_path=relative,
            )
        expected_text = os.path.normcase(os.path.abspath(str(destination)))
        recorded_text = os.path.normcase(os.path.abspath(str(projection.destination)))
        if expected_text != recorded_text:
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_BASELINE_MISMATCH,
                relative_path=relative,
            )
        assert_safe_runtime_path(self.session.source_repo, source, include_leaf=True)
        assert_safe_runtime_path(self.session.workspace, destination, include_leaf=False)
        return relative, source, destination

    def _keyed(self, fingerprint: RuntimeTreeFingerprint, *, relative: str) -> str:
        return self.control_plane.keyed_fingerprint(
            bytes.fromhex(fingerprint.sha256),
            context=f"runtime-projection-tree.v1:{relative}",
        )

    def establish_baseline(self) -> None:
        if self._established:
            raise RuntimeError("Runtime projection baseline is already established")
        if not self.active:
            self._established = True
            return
        rows: list[dict[str, Any]] = []
        for projection in sorted(
            self.session.runtime_projections,
            key=lambda item: item.relative_path.casefold(),
        ):
            relative, source, destination = self._projection_paths(projection)
            try:
                if not source.is_dir() or not destination.is_dir():
                    raise RuntimeError("runtime projection root is not a directory")
                source_fingerprint = fingerprint_runtime_tree(source)
                workspace_fingerprint = fingerprint_runtime_tree(destination)
                _assert_independent_copy(source, destination, relative=Path(relative))
            except RuntimeProjectionIntegrityError:
                self._record_event(
                    RUNTIME_PROJECTION_UNSUPPORTED_ENTRY,
                    relative_path=relative,
                )
                raise
            except (RuntimeError, OSError) as exc:
                self._record_event(
                    RUNTIME_PROJECTION_BASELINE_MISMATCH,
                    relative_path=relative,
                )
                raise RuntimeProjectionIntegrityError(
                    RUNTIME_PROJECTION_BASELINE_MISMATCH,
                    relative_path=relative,
                ) from exc
            source_keyed = self._keyed(source_fingerprint, relative=relative)
            workspace_keyed = self._keyed(workspace_fingerprint, relative=relative)
            if source_keyed != workspace_keyed:
                self._record_event(
                    RUNTIME_PROJECTION_BASELINE_MISMATCH,
                    relative_path=relative,
                )
                raise RuntimeProjectionIntegrityError(
                    RUNTIME_PROJECTION_BASELINE_MISMATCH,
                    relative_path=relative,
                )
            baseline = _ProjectionBaseline(
                projection=projection,
                relative_path=relative,
                keyed_fingerprint=source_keyed,
                entry_count=source_fingerprint.entry_count,
                total_file_bytes=source_fingerprint.total_file_bytes,
            )
            self._baselines[relative] = baseline
            rows.append(
                {
                    "relative_path": relative,
                    "keyed_hmac_sha256": source_keyed,
                    "entry_count": source_fingerprint.entry_count,
                    "total_file_bytes": source_fingerprint.total_file_bytes,
                    "source_kind": projection.source_kind,
                    "copy_mode": projection.copy_mode,
                    "runtime_only": projection.runtime_only,
                }
            )
        self.control_plane.write_private_json(
            "runtime_projection_baseline.json",
            {
                "schema_version": RUNTIME_PROJECTION_BASELINE_VERSION,
                "run_binding": self.control_plane.keyed_fingerprint(
                    b"runtime-projection-baseline",
                    context="runtime-projection-baseline.v1",
                ),
                "projections": rows,
            },
        )
        self._established = True
        for relative in self._baselines:
            self._record_event(
                "RUNTIME_PROJECTION_BASELINE_ESTABLISHED",
                relative_path=relative,
            )

    def _fingerprint_source(
        self,
        baseline: _ProjectionBaseline,
        *,
        batch_id: str,
    ) -> RuntimeTreeFingerprint:
        try:
            _, source, _ = self._projection_paths(baseline.projection)
            fingerprint = fingerprint_runtime_tree(source)
        except (RuntimeProjectionIntegrityError, OSError, RuntimeError) as exc:
            self._record_event(
                RUNTIME_PROJECTION_SOURCE_CHANGED,
                relative_path=baseline.relative_path,
                batch_id=batch_id,
            )
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_SOURCE_CHANGED,
                relative_path=baseline.relative_path,
                batch_id=batch_id,
            ) from exc
        if self._keyed(fingerprint, relative=baseline.relative_path) != baseline.keyed_fingerprint:
            self._record_event(
                RUNTIME_PROJECTION_SOURCE_CHANGED,
                relative_path=baseline.relative_path,
                batch_id=batch_id,
            )
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_SOURCE_CHANGED,
                relative_path=baseline.relative_path,
                batch_id=batch_id,
            )
        return fingerprint

    def _workspace_matches(self, baseline: _ProjectionBaseline) -> bool:
        try:
            _, source, destination = self._projection_paths(baseline.projection)
            fingerprint = fingerprint_runtime_tree(destination)
            _assert_independent_copy(
                source,
                destination,
                relative=Path(baseline.relative_path),
            )
        except (RuntimeProjectionIntegrityError, OSError, RuntimeError):
            return False
        return self._keyed(fingerprint, relative=baseline.relative_path) == baseline.keyed_fingerprint

    @staticmethod
    def _remove_tree_no_follow(path: Path) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or _entry_is_reparse(info):
            try:
                path.unlink()
            except IsADirectoryError:
                path.rmdir()
            return
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                children = [Path(item.path) for item in entries]
            for child in children:
                RuntimeProjectionIntegrityManager._remove_tree_no_follow(child)
            path.rmdir()
            return
        path.unlink()

    def _remove_destination(self, destination: Path) -> None:
        error: OSError | None = None
        for attempt in range(self.removal_attempts):
            try:
                self._remove_tree_no_follow(destination)
                return
            except OSError as exc:
                error = exc
                if attempt + 1 < self.removal_attempts:
                    time.sleep(self.retry_delay_seconds)
        assert error is not None
        raise error

    def _restore(self, baseline: _ProjectionBaseline, *, batch_id: str) -> None:
        self._fingerprint_source(baseline, batch_id=batch_id)
        relative = baseline.relative_path
        destination = self.session.workspace / relative
        destination_parent_safe = False
        try:
            _, source, destination = self._projection_paths(baseline.projection)
            destination_parent_safe = True
            self._remove_destination(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, symlinks=False)
            assert_safe_runtime_path(self.session.workspace, destination, include_leaf=True)
            _assert_independent_copy(source, destination, relative=Path(relative))
            self._fingerprint_source(baseline, batch_id=batch_id)
            if not self._workspace_matches(baseline):
                raise RuntimeError("restored fingerprint mismatch")
        except RuntimeProjectionIntegrityError:
            if destination_parent_safe:
                try:
                    self._remove_destination(destination)
                except OSError:
                    pass
            raise
        except (OSError, RuntimeError, shutil.Error) as exc:
            if destination_parent_safe:
                try:
                    self._remove_destination(destination)
                except OSError:
                    pass
            self._record_event(
                RUNTIME_PROJECTION_WORKSPACE_RESTORE_FAILED,
                relative_path=relative,
                batch_id=batch_id,
            )
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_WORKSPACE_RESTORE_FAILED,
                relative_path=relative,
                batch_id=batch_id,
            ) from exc
        self._record_event(
            "RUNTIME_PROJECTION_RESTORED_BEFORE_TRUSTED_CHECK",
            relative_path=relative,
            batch_id=batch_id,
        )

    def prepare_before_batch(self, batch_id: str) -> None:
        if not self.active:
            return
        if not self._established:
            raise RuntimeError("Runtime projection baseline has not been established")
        pristine = True
        for baseline in self._baselines.values():
            self._fingerprint_source(baseline, batch_id=batch_id)
            if not self._workspace_matches(baseline):
                pristine = False
                self._restore(baseline, batch_id=batch_id)
        if pristine:
            for relative in self._baselines:
                self._record_event(
                    "RUNTIME_PROJECTION_PRISTINE_BEFORE_TRUSTED_CHECK",
                    relative_path=relative,
                    batch_id=batch_id,
                )

    def verify_after_batch(self, batch_id: str) -> None:
        if not self.active:
            return
        for baseline in self._baselines.values():
            self._fingerprint_source(baseline, batch_id=batch_id)
            if self._workspace_matches(baseline):
                continue
            self._record_event(
                RUNTIME_PROJECTION_MUTATED_DURING_CHECK,
                relative_path=baseline.relative_path,
                batch_id=batch_id,
            )
            restore_failed = False
            try:
                self._restore(baseline, batch_id=batch_id)
            except RuntimeProjectionIntegrityError:
                restore_failed = True
            raise RuntimeProjectionIntegrityError(
                RUNTIME_PROJECTION_MUTATED_DURING_CHECK,
                relative_path=baseline.relative_path,
                batch_id=batch_id,
                restore_failed=restore_failed,
            )

    def run_batch(self, batch_id: str, operation: Callable[[], _T]) -> _T:
        """Execute one authoritative batch with full-tree pre/post validation."""

        if not self.active:
            return operation()
        self.prepare_before_batch(batch_id)
        operation_error: BaseException | None = None
        try:
            return operation()
        except BaseException as exc:
            operation_error = exc
            raise
        finally:
            try:
                self.verify_after_batch(batch_id)
            except RuntimeProjectionIntegrityError as integrity_error:
                if operation_error is not None:
                    raise integrity_error from operation_error
                raise
