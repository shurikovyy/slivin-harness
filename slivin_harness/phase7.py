from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from slivin_harness.control_plane import is_within, safe_artifact_name
from slivin_harness.run_state import CandidateIdentity, build_candidate_identity
from slivin_harness.workflow import (
    HeldoutStatus,
    RevisionKind,
    StageId,
    StageResultCode,
    StageState,
    WorkflowMode,
)
from slivin_harness.workspace import WorkspaceSession, build_repository_patch

PHASE7_VERSION = "phase7-final-gate.v1"
PATCH_PROOF_VERSION = "patch-proof.v1"
FINAL_ACCEPTANCE_VERSION = "final-acceptance.v2"
DELIVERY_RECORD_VERSION = "delivery-record.v2"
HELDOUT_EVIDENCE_VERSION = "heldout-evidence.v2"
BENCHMARK_ISOLATION_VERSION = "benchmark-isolation.v1"


class Phase7Error(RuntimeError):
    """The final quality/delivery contract is inconsistent or unsafe."""


def sanitize_benchmark_toolchain(
    *,
    toolchain: Mapping[str, str],
    source_repo: Path | None,
    workspace: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    """Remove executable paths that reveal the unsanitized benchmark source.

    Historical agents must operate on the standalone baseline repository.  A
    project-local Python/Jest path from the original source checkout would both
    disclose that checkout and provide a path around the sanitized repository.
    Relative command names and paths outside the source repository are retained.
    """

    retained: dict[str, str] = {}
    removed: dict[str, str] = {}
    for name, raw in toolchain.items():
        path = Path(str(raw)).expanduser()
        if (
            source_repo is not None
            and path.is_absolute()
            and is_within(source_repo, path)
            and not is_within(workspace, path)
        ):
            removed[str(name)] = "SOURCE_REPOSITORY_PATH_REMOVED"
            continue
        retained[str(name)] = str(raw)
    return retained, removed


@dataclass(frozen=True)
class DeliveryResult:
    schema_version: str
    status: str
    result_mode: str
    destination: str
    reason_code: str | None
    source_head_before: str | None
    source_head_after: str | None
    lock_path: str | None
    exact_patch_match: bool
    rollback_status: str | None
    conflict_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["conflict_paths"] = list(self.conflict_paths)
        return payload


@dataclass(frozen=True)
class _PathSnapshot:
    state: str
    sha256: str | None
    size: int | None
    mode: int | None
    payload: bytes | None = None
    link_target: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "sha256": self.sha256,
            "size": self.size,
            "mode": self.mode,
        }


def _run_git(
    repo: Path,
    *args: str,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=input_bytes is None,
        encoding="utf-8" if input_bytes is None else None,
        errors="replace" if input_bytes is None else None,
    )
    if check and result.returncode != 0:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise Phase7Error(
            f"git {' '.join(args)} failed in {repo}:\n{str(stderr).strip()}"
        )
    return result


def _status_porcelain(repo: Path) -> str:
    return str(
        _run_git(
            repo,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ).stdout
    )


def _safe_relative(raw: str) -> Path:
    rel = Path(str(raw).replace("\\", "/"))
    if not str(raw).strip() or rel.is_absolute() or ".." in rel.parts:
        raise Phase7Error(f"Unsafe candidate path: {raw!r}")
    return rel


def _status_without_exposed(status_text: str, exposed_paths: Iterable[str]) -> str:
    allowed = [
        _safe_relative(item).as_posix().rstrip("/")
        for item in exposed_paths
    ]
    kept: list[str] = []
    for line in status_text.splitlines():
        if not line.startswith("?? "):
            kept.append(line)
            continue
        raw = line[3:].strip().strip('"').replace("\\", "/")
        if any(raw == item or raw.startswith(item + "/") for item in allowed):
            continue
        kept.append(line)
    return "\n".join(kept)


def _artifact_path(run_root: Path, private_root: Path, name: str) -> Path:
    rel = safe_artifact_name(name)
    private = private_root / rel
    if private.is_file():
        return private
    public = run_root / rel
    if public.is_file():
        return public
    raise Phase7Error(f"Required final-gate artifact is missing: {name}")


def artifact_digest(run_root: Path, private_root: Path, name: str) -> dict[str, Any]:
    path = _artifact_path(run_root, private_root, name)
    raw = path.read_bytes()
    return {
        "artifact": safe_artifact_name(name),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "authoritative": path.is_relative_to(private_root),
    }


def reconcile_quality_gate(
    *,
    run_state_data: Mapping[str, Any],
    final_candidate: CandidateIdentity,
    mode: WorkflowMode,
) -> dict[str, Any]:
    """Validate that every accepted stage belongs to one active definition/candidate."""

    if run_state_data.get("active_stage") not in {None, StageId.FINAL_GATE.value}:
        raise Phase7Error("Final Gate cannot reconcile while another stage is active")
    expected_mode = mode.value
    if run_state_data.get("mode") != expected_mode:
        raise Phase7Error("Run State mode does not match Final Gate mode")
    current = run_state_data.get("current_candidate") or {}
    if current.get("candidate_id") != final_candidate.candidate_id:
        raise Phase7Error("Run State current candidate does not match Final Gate candidate")

    required = (
        StageId.INTAKE_PREFLIGHT,
        StageId.PLANNER,
        StageId.IMPLEMENTATION_CONTRACT,
        StageId.IMPLEMENTER,
        StageId.DETERMINISTIC_CHECKS,
        StageId.RUNTIME_VERIFICATION,
        StageId.EVALUATOR,
    )
    current_revisions = dict(run_state_data.get("revisions") or {})
    revision_keys = tuple(item.value for item in RevisionKind)
    bindings: list[dict[str, Any]] = []
    for stage in required:
        record = dict((run_state_data.get("stages") or {}).get(stage.value) or {})
        state = record.get("state")
        if state not in {StageState.PASSED.value, StageState.SKIPPED.value}:
            raise Phase7Error(
                f"Final Gate requires successful {stage.value}; got {state!r}"
            )
        result_code = record.get("result_code")
        if not result_code:
            raise Phase7Error(f"Stage {stage.value} has no result code")
        stage_candidate = record.get("candidate_id")
        if stage in {
            StageId.IMPLEMENTER,
            StageId.DETERMINISTIC_CHECKS,
            StageId.RUNTIME_VERIFICATION,
            StageId.EVALUATOR,
        }:
            if stage_candidate != final_candidate.candidate_id:
                raise Phase7Error(
                    f"Stage {stage.value} evidence belongs to a different candidate"
                )
            snapshot = dict(record.get("revision_snapshot") or {})
            for key in revision_keys:
                if current_revisions.get(key) is not None and snapshot.get(key) != current_revisions.get(key):
                    raise Phase7Error(
                        f"Stage {stage.value} used stale {key} revision"
                    )
        bindings.append(
            {
                "stage": stage.value,
                "state": state,
                "result_code": result_code,
                "candidate_id": stage_candidate,
                "revision_snapshot": dict(record.get("revision_snapshot") or {}),
                "artifacts": list(record.get("artifacts") or []),
            }
        )

    return {
        "schema_version": "quality-gate-reconciliation.v1",
        "mode": mode.value,
        "attempt_id": int(run_state_data.get("attempt_id") or 0),
        "candidate_id": final_candidate.candidate_id,
        "revision_snapshot": current_revisions,
        "stage_bindings": bindings,
        "status": "QUALITY_GATE_RECONCILIATION_PASS",
    }


def build_patch_reconstruction_proof(
    *,
    repository: Path,
    baseline_sha: str,
    patch: bytes,
    expected_candidate: CandidateIdentity,
    private_root: Path,
) -> dict[str, Any]:
    """Apply the patch to a fresh baseline checkout and compare candidate identity."""

    proof_parent = private_root / "patch_reconstruction"
    proof_parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="proof-", dir=proof_parent))
    proof_repo = temp_root / "repo"
    try:
        _run_git(temp_root, "init", str(proof_repo))
        _run_git(proof_repo, "config", "core.autocrlf", "false")
        _run_git(proof_repo, "config", "core.longpaths", "true", check=False)
        _run_git(
            proof_repo,
            "fetch",
            "--depth=1",
            "--no-tags",
            str(repository),
            baseline_sha,
        )
        _run_git(proof_repo, "checkout", "--detach", "FETCH_HEAD")
        # An empty production candidate is uncommon but can be legitimate when
        # the requested state already exists.  ``git apply`` rejects an empty
        # stream as "No valid patches", so treat it as the identity transform
        # and still prove the resulting candidate identity below.  Historical
        # benchmarks separately require a material candidate change.
        if patch:
            applied = subprocess.run(
                ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
                cwd=proof_repo,
                input=patch,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if applied.returncode != 0:
                raise Phase7Error(
                    "Accepted patch cannot reconstruct from the recorded baseline:\n"
                    + applied.stderr.decode("utf-8", errors="replace")
                )
        reconstructed = build_candidate_identity(
            proof_repo,
            baseline_sha=baseline_sha,
        )
        if reconstructed.candidate_id != expected_candidate.candidate_id:
            raise Phase7Error(
                "Patch reconstruction produced a candidate different from the accepted candidate"
            )
        if reconstructed.changed_paths != expected_candidate.changed_paths:
            raise Phase7Error("Patch reconstruction changed-path set does not match")
        return {
            "schema_version": PATCH_PROOF_VERSION,
            "baseline_sha": baseline_sha,
            "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "expected_candidate_id": expected_candidate.candidate_id,
            "reconstructed_candidate_id": reconstructed.candidate_id,
            "changed_paths": list(reconstructed.changed_paths),
            "status": "PATCH_RECONSTRUCTION_PASS",
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
        try:
            proof_parent.rmdir()
        except OSError:
            pass


def classify_heldout_results(
    *,
    results: Sequence[Any],
    oracle_marker: str,
    candidate_before: str,
    candidate_after: str,
) -> dict[str, Any]:
    """Classify hidden exam output without turning setup failures into semantic evidence."""

    if not results:
        raise Phase7Error("Historical benchmark requires at least one held-out check")
    if not oracle_marker.strip():
        raise Phase7Error("Historical benchmark requires a non-empty oracle marker")

    records: list[dict[str, Any]] = []
    for item in results:
        records.append(
            {
                "name": str(item.name),
                "returncode": item.returncode,
                "timed_out": bool(item.timed_out),
                "infra_error": bool(item.infra_error),
                "classification": str(item.classification.value),
                "oracle_reached": oracle_marker in str(item.output),
                "duration_seconds": float(item.duration_seconds),
            }
        )

    if candidate_before != candidate_after:
        status = HeldoutStatus.MUTATED_CANDIDATE.value
        reason = "HELDOUT_MUTATED_CANDIDATE"
    elif any(item.timed_out for item in results):
        status = HeldoutStatus.TIMEOUT.value
        reason = "HELDOUT_TIMEOUT"
    elif any(item.infra_error for item in results):
        status = HeldoutStatus.INFRA_ERROR.value
        reason = "HELDOUT_INFRA_ERROR"
    elif not all(oracle_marker in str(item.output) for item in results):
        status = HeldoutStatus.INFRA_ERROR.value
        reason = "HELDOUT_ORACLE_NOT_REACHED"
    elif all(item.returncode == 0 for item in results):
        status = HeldoutStatus.PASS.value
        reason = None
    else:
        status = HeldoutStatus.SEMANTIC_FAIL.value
        reason = "HELDOUT_SEMANTIC_FAIL"

    return {
        "schema_version": HELDOUT_EVIDENCE_VERSION,
        "status": status,
        "reason_code": reason,
        "candidate_id": candidate_before,
        "candidate_unchanged": candidate_before == candidate_after,
        "oracle_marker": oracle_marker,
        "oracle_reached": all(item["oracle_reached"] for item in records),
        "feedback_exposed_to_agents": False,
        "records": records,
    }


def _snapshot_path(path: Path) -> _PathSnapshot:
    if path.is_symlink():
        target = os.readlink(path)
        raw = target.encode("utf-8", errors="surrogateescape")
        return _PathSnapshot(
            state="symlink",
            sha256=hashlib.sha256(raw).hexdigest(),
            size=len(raw),
            mode=None,
            payload=raw,
            link_target=target,
        )
    if path.is_file():
        raw = path.read_bytes()
        return _PathSnapshot(
            state="file",
            sha256=hashlib.sha256(raw).hexdigest(),
            size=len(raw),
            mode=path.stat().st_mode & 0o777,
            payload=raw,
        )
    if not path.exists():
        return _PathSnapshot(
            state="absent",
            sha256=None,
            size=None,
            mode=None,
        )
    raise Phase7Error(f"Unsupported delivery path object: {path}")


def _restore_snapshot(path: Path, snapshot: _PathSnapshot) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        raise Phase7Error(f"Refusing to replace directory during rollback: {path}")
    if snapshot.state == "absent":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.state == "file":
        assert snapshot.payload is not None
        path.write_bytes(snapshot.payload)
        if snapshot.mode is not None:
            try:
                path.chmod(snapshot.mode)
            except OSError:
                pass
        return
    if snapshot.state == "symlink":
        if snapshot.link_target is None:
            raise Phase7Error("Symlink rollback snapshot lacks target")
        os.symlink(snapshot.link_target, path)
        return
    raise Phase7Error(f"Unsupported rollback snapshot state: {snapshot.state}")


def _git_common_dir(source: Path) -> Path:
    raw = str(_run_git(source, "rev-parse", "--git-common-dir").stdout).strip()
    path = Path(raw)
    return (path if path.is_absolute() else source / path).resolve()


@contextmanager
def _delivery_lock(source: Path, *, timeout_seconds: float = 30.0) -> Iterator[Path]:
    lock_path = _git_common_dir(source) / "slivin-harness-delivery.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    acquired = False
    try:
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise Phase7Error("SOURCE_DELIVERY_LOCK_BUSY")
                time.sleep(0.05)
        yield lock_path
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _rollback_delivery(
    *,
    source: Path,
    preimages: Mapping[str, _PathSnapshot],
    expected_postimages: Mapping[str, _PathSnapshot],
) -> tuple[str, tuple[str, ...]]:
    conflicts: list[str] = []
    for rel in sorted(preimages):
        path = source / _safe_relative(rel)
        current = _snapshot_path(path)
        pre = preimages[rel]
        expected = expected_postimages[rel]
        if current == pre:
            continue
        if current == expected:
            _restore_snapshot(path, pre)
            continue
        conflicts.append(rel)
    return (
        "ROLLBACK_PASS" if not conflicts else "ROLLBACK_CONFLICT",
        tuple(conflicts),
    )


def deliver_candidate_transaction(
    *,
    session: WorkspaceSession,
    patch: bytes,
    final_candidate: CandidateIdentity,
    timeout_seconds: float = 30.0,
) -> DeliveryResult:
    """Deliver an already accepted candidate without conflating quality and delivery."""

    if session.result_mode == "keep_worktree":
        return DeliveryResult(
            schema_version=DELIVERY_RECORD_VERSION,
            status=StageResultCode.RESULT_DELIVERY_PASS.value,
            result_mode=session.result_mode,
            destination=str(session.workspace),
            reason_code=None,
            source_head_before=session.source_head,
            source_head_after=session.source_head,
            lock_path=None,
            exact_patch_match=True,
            rollback_status=None,
            conflict_paths=(),
        )
    if session.source_repo is None or session.source_head is None:
        return DeliveryResult(
            schema_version=DELIVERY_RECORD_VERSION,
            status=StageResultCode.RESULT_DELIVERY_FAIL.value,
            result_mode=session.result_mode,
            destination="",
            reason_code="SOURCE_METADATA_MISSING",
            source_head_before=None,
            source_head_after=None,
            lock_path=None,
            exact_patch_match=False,
            rollback_status=None,
            conflict_paths=(),
        )

    source = session.source_repo
    lock_path: Path | None = None
    try:
        with _delivery_lock(source, timeout_seconds=timeout_seconds) as acquired_lock:
            lock_path = acquired_lock
            source_head_before = str(_run_git(source, "rev-parse", "HEAD").stdout).strip()
            if source_head_before != session.source_head:
                return DeliveryResult(
                    schema_version=DELIVERY_RECORD_VERSION,
                    status=StageResultCode.RESULT_DELIVERY_BLOCKED.value,
                    result_mode=session.result_mode,
                    destination=str(source),
                    reason_code="SOURCE_HEAD_CHANGED",
                    source_head_before=source_head_before,
                    source_head_after=source_head_before,
                    lock_path=str(lock_path),
                    exact_patch_match=False,
                    rollback_status=None,
                    conflict_paths=(),
                )
            dirty = _status_without_exposed(
                _status_porcelain(source),
                session.exposed_paths,
            )
            if dirty.strip():
                return DeliveryResult(
                    schema_version=DELIVERY_RECORD_VERSION,
                    status=StageResultCode.RESULT_DELIVERY_BLOCKED.value,
                    result_mode=session.result_mode,
                    destination=str(source),
                    reason_code="SOURCE_WORKTREE_NOT_CLEAN",
                    source_head_before=source_head_before,
                    source_head_after=source_head_before,
                    lock_path=str(lock_path),
                    exact_patch_match=False,
                    rollback_status=None,
                    conflict_paths=(),
                )
            if not patch:
                return DeliveryResult(
                    schema_version=DELIVERY_RECORD_VERSION,
                    status=StageResultCode.RESULT_DELIVERY_PASS.value,
                    result_mode=session.result_mode,
                    destination=str(source),
                    reason_code=None,
                    source_head_before=source_head_before,
                    source_head_after=source_head_before,
                    lock_path=str(lock_path),
                    exact_patch_match=True,
                    rollback_status=None,
                    conflict_paths=(),
                )

            changed_paths = tuple(final_candidate.changed_paths)
            preimages = {
                rel: _snapshot_path(source / _safe_relative(rel))
                for rel in changed_paths
            }
            postimages = {
                rel: _snapshot_path(session.workspace / _safe_relative(rel))
                for rel in changed_paths
            }
            check = subprocess.run(
                ["git", "apply", "--check", "--binary", "--whitespace=nowarn", "-"],
                cwd=source,
                input=patch,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if check.returncode != 0:
                return DeliveryResult(
                    schema_version=DELIVERY_RECORD_VERSION,
                    status=StageResultCode.RESULT_DELIVERY_BLOCKED.value,
                    result_mode=session.result_mode,
                    destination=str(source),
                    reason_code="PATCH_APPLY_CHECK_FAILED",
                    source_head_before=source_head_before,
                    source_head_after=source_head_before,
                    lock_path=str(lock_path),
                    exact_patch_match=False,
                    rollback_status=None,
                    conflict_paths=(),
                )

            # Close the time-of-check/time-of-use gap as far as an external editor
            # can be observed without taking ownership of the user's filesystem.
            source_head_recheck = str(_run_git(source, "rev-parse", "HEAD").stdout).strip()
            dirty_recheck = _status_without_exposed(
                _status_porcelain(source),
                session.exposed_paths,
            )
            preimages_recheck = {
                rel: _snapshot_path(source / _safe_relative(rel))
                for rel in changed_paths
            }
            if (
                source_head_recheck != source_head_before
                or dirty_recheck.strip()
                or preimages_recheck != preimages
            ):
                return DeliveryResult(
                    schema_version=DELIVERY_RECORD_VERSION,
                    status=StageResultCode.RESULT_DELIVERY_BLOCKED.value,
                    result_mode=session.result_mode,
                    destination=str(source),
                    reason_code="SOURCE_CHANGED_DURING_DELIVERY_PREFLIGHT",
                    source_head_before=source_head_before,
                    source_head_after=source_head_recheck,
                    lock_path=str(lock_path),
                    exact_patch_match=False,
                    rollback_status=None,
                    conflict_paths=(),
                )

            applied = subprocess.run(
                ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
                cwd=source,
                input=patch,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if applied.returncode != 0:
                rollback_status, conflicts = _rollback_delivery(
                    source=source,
                    preimages=preimages,
                    expected_postimages=postimages,
                )
                return DeliveryResult(
                    schema_version=DELIVERY_RECORD_VERSION,
                    status=StageResultCode.RESULT_DELIVERY_FAIL.value,
                    result_mode=session.result_mode,
                    destination=str(source),
                    reason_code="PATCH_APPLY_FAILED",
                    source_head_before=source_head_before,
                    source_head_after=str(_run_git(source, "rev-parse", "HEAD").stdout).strip(),
                    lock_path=str(lock_path),
                    exact_patch_match=False,
                    rollback_status=rollback_status,
                    conflict_paths=conflicts,
                )

            actual_patch = build_repository_patch(source)
            actual_postimages = {
                rel: _snapshot_path(source / _safe_relative(rel))
                for rel in changed_paths
            }
            exact = actual_patch == patch and actual_postimages == postimages
            source_head_after = str(_run_git(source, "rev-parse", "HEAD").stdout).strip()
            if exact and source_head_after == source_head_before:
                return DeliveryResult(
                    schema_version=DELIVERY_RECORD_VERSION,
                    status=StageResultCode.RESULT_DELIVERY_PASS.value,
                    result_mode=session.result_mode,
                    destination=str(source),
                    reason_code=None,
                    source_head_before=source_head_before,
                    source_head_after=source_head_after,
                    lock_path=str(lock_path),
                    exact_patch_match=True,
                    rollback_status=None,
                    conflict_paths=(),
                )

            rollback_status, conflicts = _rollback_delivery(
                source=source,
                preimages=preimages,
                expected_postimages=postimages,
            )
            return DeliveryResult(
                schema_version=DELIVERY_RECORD_VERSION,
                status=StageResultCode.RESULT_DELIVERY_FAIL.value,
                result_mode=session.result_mode,
                destination=str(source),
                reason_code=(
                    "SOURCE_CHANGED_DURING_DELIVERY"
                    if conflicts
                    else "APPLIED_SOURCE_DIFF_MISMATCH"
                ),
                source_head_before=source_head_before,
                source_head_after=source_head_after,
                lock_path=str(lock_path),
                exact_patch_match=False,
                rollback_status=rollback_status,
                conflict_paths=conflicts,
            )
    except Phase7Error as exc:
        source_head_after: str | None = None
        if session.source_repo is not None and session.source_repo.is_dir():
            try:
                source_head_after = str(
                    _run_git(session.source_repo, "rev-parse", "HEAD").stdout
                ).strip()
            except (OSError, Phase7Error):
                # Preserve the original delivery failure.  A secondary failure
                # while collecting diagnostics must never mask it.
                source_head_after = None
        return DeliveryResult(
            schema_version=DELIVERY_RECORD_VERSION,
            status=StageResultCode.RESULT_DELIVERY_BLOCKED.value,
            result_mode=session.result_mode,
            destination=str(session.source_repo or ""),
            reason_code=str(exc),
            source_head_before=session.source_head,
            source_head_after=source_head_after,
            lock_path=str(lock_path) if lock_path else None,
            exact_patch_match=False,
            rollback_status=None,
            conflict_paths=(),
        )


def reset_workspace_for_semantic_replan(
    *,
    workspace: Path,
    baseline_sha: str,
    preserve_prefixes: Sequence[str] = (".venv", ".harness_tmp"),
) -> dict[str, Any]:
    """Restore the managed candidate to its recorded baseline for a fresh replan.

    A semantic replan means the previous technical model was rejected.  Keeping
    its diff visible to the next Planner/Implementer would reintroduce the exact
    anchoring that the fresh-thread contract is meant to remove.  The rejected
    patch is preserved by the caller as a run artifact; this function resets
    tracked/index state and removes only non-ignored untracked candidate files.
    Task-local runtime and Harness scratch directories are preserved and are
    separately reconciled by the Controller.
    """

    root = Path(workspace).resolve()
    before = build_candidate_identity(root, baseline_sha=baseline_sha)
    raw_untracked = str(
        _run_git(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).stdout
    )
    preserve = tuple(item.replace("\\", "/").strip("/") for item in preserve_prefixes)
    removable: list[str] = []
    for raw in raw_untracked.split("\0"):
        if not raw:
            continue
        rel = _safe_relative(raw).as_posix()
        if any(rel == prefix or rel.startswith(prefix + "/") for prefix in preserve):
            continue
        removable.append(rel)

    _run_git(root, "reset", "--hard", baseline_sha)
    for rel in sorted(removable, key=lambda value: (value.count("/"), value), reverse=True):
        target = root / _safe_relative(rel)
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)
        parent = target.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    after = build_candidate_identity(root, baseline_sha=baseline_sha)
    head = str(_run_git(root, "rev-parse", "HEAD").stdout).strip()
    if head != baseline_sha:
        raise Phase7Error("Semantic replan reset moved workspace to the wrong baseline")
    if after.changed_paths:
        raise Phase7Error(
            "Semantic replan reset left candidate changes: "
            + ", ".join(after.changed_paths)
        )
    return {
        "schema_version": "semantic-replan-reset.v1",
        "baseline_sha": baseline_sha,
        "rejected_candidate_id": before.candidate_id,
        "rejected_changed_paths": list(before.changed_paths),
        "removed_untracked_paths": removable,
        "clean_candidate_id": after.candidate_id,
        "status": "SEMANTIC_REPLAN_RESET_PASS",
    }


def build_final_acceptance(
    *,
    task_id: str,
    harness_version: str,
    workflow_version: str,
    mode: WorkflowMode,
    pipeline_profile: str,
    result_mode: str,
    source_baseline_sha: str | None,
    final_candidate: CandidateIdentity,
    quality_reconciliation: Mapping[str, Any],
    patch_metadata: Mapping[str, Any],
    patch_proof: Mapping[str, Any],
    artifact_bindings: Sequence[Mapping[str, Any]],
    heldout_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    candidate_id = final_candidate.candidate_id
    if quality_reconciliation.get("status") != "QUALITY_GATE_RECONCILIATION_PASS":
        raise Phase7Error("Final acceptance requires a successful quality reconciliation")
    if quality_reconciliation.get("candidate_id") != candidate_id:
        raise Phase7Error("Quality reconciliation belongs to a different candidate")
    if patch_proof.get("status") != "PATCH_RECONSTRUCTION_PASS":
        raise Phase7Error("Final acceptance requires a successful patch reconstruction")
    if patch_proof.get("expected_candidate_id") != candidate_id:
        raise Phase7Error("Patch proof expected candidate does not match final candidate")
    if patch_proof.get("reconstructed_candidate_id") != candidate_id:
        raise Phase7Error("Patch proof reconstructed candidate does not match final candidate")
    patch_sha = str(patch_metadata.get("sha256") or "")
    if not patch_sha or patch_proof.get("patch_sha256") != patch_sha:
        raise Phase7Error("Patch metadata and patch proof digests do not match")
    if mode == WorkflowMode.HISTORICAL_BENCHMARK:
        if heldout_evidence is None or heldout_evidence.get("status") != HeldoutStatus.PASS.value:
            raise Phase7Error("Historical final acceptance requires HELDOUT_PASS")
        if heldout_evidence.get("candidate_id") != candidate_id:
            raise Phase7Error("Held-out evidence belongs to a different candidate")
    elif heldout_evidence is not None:
        raise Phase7Error("Production final acceptance cannot contain held-out evidence")

    return {
        "schema_version": FINAL_ACCEPTANCE_VERSION,
        "phase_version": PHASE7_VERSION,
        "task_id": task_id,
        "harness_version": harness_version,
        "workflow_version": workflow_version,
        "mode": mode.value,
        "pipeline_profile": pipeline_profile,
        "result_mode": result_mode,
        "source_baseline_sha": source_baseline_sha,
        "workspace_baseline_sha": final_candidate.baseline_sha,
        "workspace_head": final_candidate.workspace_head,
        "candidate_id": candidate_id,
        "changed_paths": list(final_candidate.changed_paths),
        "attempt_id": int(quality_reconciliation["attempt_id"]),
        "revision_snapshot": dict(quality_reconciliation["revision_snapshot"]),
        "stage_bindings": list(quality_reconciliation["stage_bindings"]),
        "artifact_bindings": list(artifact_bindings),
        "patch": dict(patch_metadata),
        "patch_proof": dict(patch_proof),
        "heldout": dict(heldout_evidence) if heldout_evidence is not None else None,
        "quality_gate_status": StageResultCode.FINAL_ACCEPTANCE_PASS.value,
        "expected_terminal_result": (
            StageResultCode.HARNESS_BENCHMARK_PASS.value
            if mode == WorkflowMode.HISTORICAL_BENCHMARK
            else StageResultCode.HARNESS_TASK_PASS.value
        ),
    }
