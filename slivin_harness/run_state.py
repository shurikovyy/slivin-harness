from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from slivin_harness.protocol import stable_fingerprint
from slivin_harness.workflow import (
    INVALIDATION_RULES,
    STAGES,
    STAGE_BY_ID,
    InvalidationTrigger,
    PipelineProfile,
    RevisionKind,
    StageId,
    StageResultCode,
    StageState,
    WorkflowMode,
    WorkflowOutcome,
    is_allowed_transition,
    stage_number,
    stages_from,
)

RUN_STATE_VERSION = "run-state.v1"
CANDIDATE_IDENTITY_VERSION = "candidate.v1"
DEFAULT_CANDIDATE_EXCLUDES = (".harness_tmp", ".venv")


class WorkflowStateError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _excluded(rel: str, prefixes: Iterable[str]) -> bool:
    normalized = rel.replace("\\", "/").strip("/")
    for raw in prefixes:
        prefix = raw.replace("\\", "/").strip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return False


def collect_candidate_paths(
    workspace: Path,
    *,
    excluded_prefixes: Iterable[str] = DEFAULT_CANDIDATE_EXCLUDES,
) -> list[str]:
    paths = {
        item.replace("\\", "/")
        for item in _git(
            workspace,
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            "HEAD",
            "--",
        ).split("\0")
        if item
    }
    paths.update(
        item.replace("\\", "/")
        for item in _git(
            workspace,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).split("\0")
        if item
    )
    return sorted(path for path in paths if not _excluded(path, excluded_prefixes))


def _candidate_mode(root: Path, rel: str, path: Path) -> str | None:
    """Return the Git-visible mode used by candidate identity.

    A chmod-only executable-bit change is observable only on filesystems where
    Git reports it in the HEAD-to-working-tree diff.
    """
    if path.is_symlink():
        return "120000"
    if path.is_file():
        try:
            tracked = _git(root, "ls-files", "--error-unmatch", "--", rel).strip()
        except RuntimeError:
            tracked = ""
        if tracked:
            raw = _git(
                root,
                "diff",
                "--raw",
                "--no-renames",
                "HEAD",
                "--",
                rel,
            ).strip()
            if raw.startswith(":"):
                fields = raw.split(None, 5)
                if len(fields) >= 2 and fields[1] != "000000":
                    return fields[1]
        executable = bool(path.stat().st_mode & stat.S_IXUSR)
        return "100755" if executable else "100644"
    if not path.exists():
        raw = _git(root, "ls-tree", "HEAD", "--", rel).strip()
        if raw:
            return raw.split(None, 1)[0]
        return None
    return None


@dataclass(frozen=True)
class CandidateIdentity:
    schema_version: str
    baseline_sha: str
    workspace_head: str
    candidate_id: str
    changed_paths: tuple[str, ...]
    entries: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "changed_paths": list(self.changed_paths),
            "entries": list(self.entries),
        }


def build_candidate_identity(
    workspace: Path,
    *,
    baseline_sha: str | None = None,
    excluded_prefixes: Iterable[str] = DEFAULT_CANDIDATE_EXCLUDES,
) -> CandidateIdentity:
    root = workspace.resolve()
    workspace_head = _git(root, "rev-parse", "HEAD").strip()
    baseline = baseline_sha or workspace_head
    entries: list[dict[str, Any]] = []
    changed_paths = collect_candidate_paths(root, excluded_prefixes=excluded_prefixes)
    for rel in changed_paths:
        path = root / rel
        if path.is_symlink():
            target = os.readlink(path)
            raw = target.encode("utf-8", errors="surrogateescape")
            entries.append(
                {
                    "path": rel,
                    "state": "symlink",
                    "mode": _candidate_mode(root, rel, path),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                }
            )
        elif path.is_file():
            raw = path.read_bytes()
            entries.append(
                {
                    "path": rel,
                    "state": "file",
                    "mode": _candidate_mode(root, rel, path),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                }
            )
        elif not path.exists():
            entries.append(
                {
                    "path": rel,
                    "state": "deleted",
                    "mode": _candidate_mode(root, rel, path),
                }
            )
        else:
            entries.append(
                {
                    "path": rel,
                    "state": "non_file",
                    "mode": None,
                }
            )

    payload = {
        "schema_version": CANDIDATE_IDENTITY_VERSION,
        "baseline_sha": baseline,
        "workspace_head": workspace_head,
        "entries": entries,
    }
    candidate_id = stable_fingerprint(payload, length=64)
    return CandidateIdentity(
        schema_version=CANDIDATE_IDENTITY_VERSION,
        baseline_sha=baseline,
        workspace_head=workspace_head,
        candidate_id=candidate_id,
        changed_paths=tuple(changed_paths),
        entries=tuple(entries),
    )


class RunState:
    def __init__(
        self,
        *,
        path: Path,
        data: dict[str, Any],
        public_mirror_path: Path | None = None,
    ) -> None:
        self.path = path
        self.public_mirror_path = public_mirror_path
        self.data = data

    @classmethod
    def create(
        cls,
        *,
        path: Path,
        task_id: str,
        harness_version: str,
        workflow_version: str,
        mode: WorkflowMode,
        pipeline_profile: PipelineProfile,
        public_mirror_path: Path | None = None,
    ) -> "RunState":
        created_at = _utc_now()
        stages = {
            stage.stage_id.value: {
                "number": stage.number,
                "state": StageState.NOT_STARTED.value,
                "attempts": 0,
                "result_code": None,
                "outcome": None,
                "reason_code": None,
                "candidate_id": None,
                "revision_snapshot": {},
                "artifacts": [],
                "started_at": None,
                "completed_at": None,
                "invalidation": None,
            }
            for stage in STAGES
        }
        data: dict[str, Any] = {
            "schema_version": RUN_STATE_VERSION,
            "workflow_version": workflow_version,
            "harness_version": harness_version,
            "task_id": task_id,
            "mode": mode.value,
            "pipeline_profile": pipeline_profile.value,
            "created_at": created_at,
            "updated_at": created_at,
            "attempt_id": 1,
            "cursor_stage": None,
            "active_stage": None,
            "revisions": {kind.value: None for kind in RevisionKind},
            "baseline": None,
            "current_candidate": None,
            "stages": stages,
            "events": [],
            "terminal": None,
        }
        state = cls(
            path=path,
            data=data,
            public_mirror_path=public_mirror_path,
        )
        state._append_event("RUN_CREATED")
        state.persist()
        return state

    def _revision_snapshot(self) -> dict[str, int | None]:
        return dict(self.data["revisions"])

    def _append_event(self, event_type: str, **details: Any) -> None:
        events: list[dict[str, Any]] = self.data["events"]
        events.append(
            {
                "sequence": len(events) + 1,
                "at": _utc_now(),
                "event": event_type,
                "attempt_id": self.data["attempt_id"],
                "candidate_id": (
                    self.data["current_candidate"]["candidate_id"]
                    if self.data["current_candidate"]
                    else None
                ),
                "revisions": self._revision_snapshot(),
                **details,
            }
        )
        self.data["updated_at"] = events[-1]["at"]

    def persist(self) -> None:
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(temp, self.path)
        # The public file is a diagnostic mirror. The private path above is the
        # only authoritative state used by the Controller.
        if self.public_mirror_path is not None:
            self.public_mirror_path.parent.mkdir(parents=True, exist_ok=True)
            mirror_temp = self.public_mirror_path.with_suffix(
                self.public_mirror_path.suffix + ".tmp"
            )
            mirror_temp.write_text(payload, encoding="utf-8", newline="\n")
            os.replace(mirror_temp, self.public_mirror_path)

    def verification_binding(
        self,
        *,
        candidate_id: str,
        check_registry_digest: str | None = None,
    ) -> dict[str, Any]:
        revisions = self._revision_snapshot()
        return {
            "candidate_id": candidate_id,
            "task_contract_rev": revisions.get(RevisionKind.TASK_CONTRACT.value),
            "plan_rev": revisions.get(RevisionKind.PLAN.value),
            "implementation_contract_rev": revisions.get(
                RevisionKind.IMPLEMENTATION_CONTRACT.value
            ),
            "verification_plan_rev": revisions.get(RevisionKind.VERIFICATION_PLAN.value),
            "runtime_env_id": revisions.get(RevisionKind.RUNTIME_ENVIRONMENT.value),
            "attempt_id": int(self.data["attempt_id"]),
            "check_registry_digest": check_registry_digest,
        }

    def set_baseline(
        self,
        *,
        source_head: str | None,
        workspace_head: str,
        source_repo: str | None,
        workspace: str,
    ) -> None:
        self.data["baseline"] = {
            "source_head": source_head,
            "workspace_head": workspace_head,
            "source_repo": source_repo,
            "workspace": workspace,
        }
        self._append_event("BASELINE_BOUND")
        self.persist()

    def bump_revision(self, kind: RevisionKind, *, artifact: str | None = None) -> int:
        current = self.data["revisions"][kind.value]
        revision = 1 if current is None else int(current) + 1
        self.data["revisions"][kind.value] = revision
        self._append_event(
            "REVISION_BUMPED",
            revision_kind=kind.value,
            revision=revision,
            artifact=artifact,
        )
        self.persist()
        return revision

    def observe_candidate(self, identity: CandidateIdentity, *, reason_code: str) -> bool:
        previous = self.data["current_candidate"]
        changed = previous is None or previous.get("candidate_id") != identity.candidate_id
        self.data["current_candidate"] = identity.to_dict()
        if changed:
            current = self.data["revisions"][RevisionKind.CANDIDATE.value]
            self.data["revisions"][RevisionKind.CANDIDATE.value] = (
                1 if current is None else int(current) + 1
            )
        self._append_event(
            "CANDIDATE_OBSERVED",
            reason_code=reason_code,
            changed=changed,
            changed_paths=list(identity.changed_paths),
        )
        self.persist()
        return changed

    def begin_stage(self, stage: StageId) -> None:
        active = self.data.get("active_stage")
        if active is not None:
            raise WorkflowStateError(f"Cannot begin {stage.value}; {active} is still active")
        previous_raw = self.data.get("cursor_stage")
        previous = StageId(previous_raw) if previous_raw else None
        if not is_allowed_transition(previous, stage):
            raise WorkflowStateError(
                f"Illegal workflow transition {previous_raw or '<START>'} -> {stage.value}"
            )
        record = self.data["stages"][stage.value]
        record["state"] = StageState.IN_PROGRESS.value
        record["attempts"] = int(record["attempts"]) + 1
        record["result_code"] = None
        record["outcome"] = None
        record["reason_code"] = None
        record["candidate_id"] = (
            self.data["current_candidate"]["candidate_id"]
            if self.data["current_candidate"]
            else None
        )
        record["revision_snapshot"] = self._revision_snapshot()
        record["artifacts"] = []
        record["started_at"] = _utc_now()
        record["completed_at"] = None
        record["invalidation"] = None
        self.data["active_stage"] = stage.value
        self._append_event("STAGE_STARTED", stage=stage.value)
        self.persist()

    def _validate_success_result(
        self,
        *,
        stage: StageId,
        state: StageState,
        result_code: StageResultCode,
    ) -> None:
        definition = STAGE_BY_ID[stage]
        if result_code not in definition.success_codes:
            raise WorkflowStateError(
                f"Result code {result_code.value} is not a success code for {stage.value}"
            )
        if state == StageState.SKIPPED and result_code not in definition.skip_codes:
            raise WorkflowStateError(
                f"Stage {stage.value} cannot be skipped with {result_code.value}"
            )
        if state == StageState.PASSED and result_code in definition.skip_codes:
            raise WorkflowStateError(
                f"Skip result code {result_code.value} requires SKIPPED state"
            )
        if stage == StageId.FINAL_GATE:
            expected = (
                StageResultCode.HARNESS_BENCHMARK_PASS
                if self.data["mode"] == WorkflowMode.HISTORICAL_BENCHMARK.value
                else StageResultCode.HARNESS_TASK_PASS
            )
            if result_code != expected:
                raise WorkflowStateError(
                    f"Final result {result_code.value} does not match run mode {self.data['mode']}"
                )

    def finish_stage(
        self,
        stage: StageId,
        *,
        state: StageState,
        outcome: WorkflowOutcome,
        result_code: StageResultCode,
        reason_code: str | None = None,
        artifacts: Iterable[str] = (),
    ) -> None:
        if self.data.get("active_stage") != stage.value:
            raise WorkflowStateError(f"Stage {stage.value} is not active")
        if state not in {
            StageState.PASSED,
            StageState.SKIPPED,
            StageState.STOPPED,
            StageState.FAILED,
        }:
            raise WorkflowStateError(f"Invalid terminal stage state: {state.value}")
        if state in {StageState.PASSED, StageState.SKIPPED}:
            if outcome != WorkflowOutcome.PASS:
                raise WorkflowStateError(
                    f"Successful stage state requires PASS outcome, got {outcome.value}"
                )
            self._validate_success_result(
                stage=stage,
                state=state,
                result_code=result_code,
            )
        elif outcome == WorkflowOutcome.PASS:
            raise WorkflowStateError(
                f"PASS outcome cannot use terminal state {state.value}"
            )
        record = self.data["stages"][stage.value]
        record["state"] = state.value
        record["result_code"] = result_code.value
        record["outcome"] = outcome.value
        record["reason_code"] = reason_code
        record["candidate_id"] = (
            self.data["current_candidate"]["candidate_id"]
            if self.data["current_candidate"]
            else None
        )
        record["revision_snapshot"] = self._revision_snapshot()
        record["artifacts"] = list(artifacts)
        record["completed_at"] = _utc_now()
        self.data["active_stage"] = None
        self.data["cursor_stage"] = stage.value
        self._append_event(
            "STAGE_FINISHED",
            stage=stage.value,
            state=state.value,
            outcome=outcome.value,
            result_code=result_code.value,
            reason_code=reason_code,
            artifacts=list(artifacts),
        )
        if outcome in {
            WorkflowOutcome.BLOCKED,
            WorkflowOutcome.NEEDS_USER_DECISION,
            WorkflowOutcome.INVALID,
        }:
            self.data["terminal"] = {
                "outcome": outcome.value,
                "result_code": result_code.value,
                "reason_code": reason_code,
                "at": _utc_now(),
            }
        self.persist()

    def pass_stage(
        self,
        stage: StageId,
        result_code: StageResultCode,
        *,
        artifacts: Iterable[str] = (),
    ) -> None:
        self.finish_stage(
            stage,
            state=StageState.PASSED,
            outcome=WorkflowOutcome.PASS,
            result_code=result_code,
            artifacts=artifacts,
        )

    def skip_stage(
        self,
        stage: StageId,
        result_code: StageResultCode,
        *,
        reason_code: str,
        artifacts: Iterable[str] = (),
    ) -> None:
        self.finish_stage(
            stage,
            state=StageState.SKIPPED,
            outcome=WorkflowOutcome.PASS,
            result_code=result_code,
            reason_code=reason_code,
            artifacts=artifacts,
        )

    def route_stage(
        self,
        stage: StageId,
        *,
        outcome: WorkflowOutcome,
        result_code: StageResultCode,
        reason_code: str,
        artifacts: Iterable[str] = (),
    ) -> None:
        state = (
            StageState.STOPPED
            if outcome in {WorkflowOutcome.BLOCKED, WorkflowOutcome.NEEDS_USER_DECISION}
            else StageState.FAILED
        )
        self.finish_stage(
            stage,
            state=state,
            outcome=outcome,
            result_code=result_code,
            reason_code=reason_code,
            artifacts=artifacts,
        )

    def invalidate(self, trigger: InvalidationTrigger, *, detail: str = "") -> None:
        rule = INVALIDATION_RULES[trigger]
        invalidated_at = _utc_now()
        if rule.invalidate_from is not None:
            for stage in stages_from(rule.invalidate_from):
                record = self.data["stages"][stage.value]
                if record["state"] != StageState.NOT_STARTED.value:
                    record["state"] = StageState.INVALIDATED.value
                    record["result_code"] = None
                    record["outcome"] = None
                    record["reason_code"] = None
                    record["candidate_id"] = None
                    record["revision_snapshot"] = {}
                    record["artifacts"] = []
                    record["started_at"] = None
                    record["completed_at"] = None
                    record["invalidation"] = {
                        "trigger": trigger.value,
                        "detail": detail,
                        "at": invalidated_at,
                    }
            previous_number = stage_number(rule.invalidate_from) - 1
            self.data["cursor_stage"] = (
                STAGES[previous_number].stage_id.value if previous_number >= 0 else None
            )
            self.data["active_stage"] = None
            self.data["terminal"] = None
        if rule.new_attempt_required:
            self.data["attempt_id"] = int(self.data["attempt_id"]) + 1
        self._append_event(
            "INVALIDATED",
            trigger=trigger.value,
            invalidate_from=(
                rule.invalidate_from.value if rule.invalidate_from is not None else None
            ),
            restart_at=rule.restart_at.value if rule.restart_at is not None else None,
            new_attempt_required=rule.new_attempt_required,
            delivery_only=rule.delivery_only,
            detail=detail,
        )
        self.persist()

    def mark_terminal(
        self,
        *,
        outcome: WorkflowOutcome,
        result_code: StageResultCode,
        reason_code: str | None = None,
    ) -> None:
        if outcome == WorkflowOutcome.PASS:
            if self.data.get("active_stage") is not None:
                raise WorkflowStateError("Cannot mark PASS while a stage is still active")
            final = self.data["stages"][StageId.FINAL_GATE.value]
            if (
                self.data.get("cursor_stage") != StageId.FINAL_GATE.value
                or final["state"] != StageState.PASSED.value
                or final["result_code"] != result_code.value
            ):
                raise WorkflowStateError(
                    "Run PASS requires a completed Final Gate with the same result code"
                )
            expected = (
                StageResultCode.HARNESS_BENCHMARK_PASS
                if self.data["mode"] == WorkflowMode.HISTORICAL_BENCHMARK.value
                else StageResultCode.HARNESS_TASK_PASS
            )
            if result_code != expected:
                raise WorkflowStateError(
                    f"Terminal result {result_code.value} does not match run mode {self.data['mode']}"
                )
        self.data["terminal"] = {
            "outcome": outcome.value,
            "result_code": result_code.value,
            "reason_code": reason_code,
            "at": _utc_now(),
        }
        self._append_event(
            "RUN_TERMINAL",
            outcome=outcome.value,
            result_code=result_code.value,
            reason_code=reason_code,
        )
        self.persist()

    def fail_active_stage(self, *, reason_code: str, detail: str) -> None:
        active = self.data.get("active_stage")
        if active is not None:
            self.finish_stage(
                StageId(active),
                state=StageState.FAILED,
                outcome=WorkflowOutcome.INVALID,
                result_code=StageResultCode.INVALID,
                reason_code=reason_code,
                artifacts=(),
            )
        elif self.data.get("terminal") is None:
            self.mark_terminal(
                outcome=WorkflowOutcome.INVALID,
                result_code=StageResultCode.INVALID,
                reason_code=reason_code,
            )
        self._append_event("RUN_ERROR", reason_code=reason_code, detail=detail)
        self.persist()
