from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from slivin_harness.run_state import (
    RunState,
    WorkflowStateError,
    build_candidate_identity,
)
from slivin_harness.workflow import (
    InvalidationTrigger,
    PipelineProfile,
    RevisionKind,
    StageId,
    StageResultCode,
    StageState,
    WorkflowMode,
    WorkflowOutcome,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


class CandidateIdentityTests(unittest.TestCase):
    def make_repo(self) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="slivin-candidate-id-"))
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        (repo / "delete.txt").write_text("delete\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "baseline")
        return repo

    def test_candidate_id_binds_baseline_paths_content_and_deletions(self) -> None:
        repo = self.make_repo()
        baseline = git(repo, "rev-parse", "HEAD")
        clean = build_candidate_identity(repo, baseline_sha=baseline)
        self.assertEqual(clean.changed_paths, ())

        (repo / "a.txt").write_text("two\n", encoding="utf-8")
        (repo / "new.bin").write_bytes(b"\x00\x01\xff")
        (repo / "delete.txt").unlink()
        changed = build_candidate_identity(repo, baseline_sha=baseline)
        self.assertNotEqual(clean.candidate_id, changed.candidate_id)
        self.assertEqual(
            list(changed.changed_paths),
            ["a.txt", "delete.txt", "new.bin"],
        )
        states = {item["path"]: item["state"] for item in changed.entries}
        self.assertEqual(states["a.txt"], "file")
        self.assertEqual(states["delete.txt"], "deleted")
        self.assertEqual(states["new.bin"], "file")
        modes = {item["path"]: item.get("mode") for item in changed.entries}
        self.assertEqual(modes["a.txt"], "100644")
        self.assertEqual(modes["delete.txt"], "100644")

    def test_candidate_id_distinguishes_executable_mode_changes_when_supported(
        self,
    ) -> None:
        """Exercise chmod only when Git/filesystem expose the mode change."""
        repo = self.make_repo()
        baseline = git(repo, "rev-parse", "HEAD")
        target = repo / "a.txt"
        original_mode = stat.S_IMODE(target.stat().st_mode)
        git(repo, "config", "core.filemode", "true")

        try:
            target.chmod(original_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            raw = git(
                repo,
                "diff",
                "--raw",
                "--no-renames",
                "HEAD",
                "--",
                "a.txt",
            )
            fields = raw.split(None, 5)
            mode_change_is_visible = (
                len(fields) >= 2
                and fields[0] == ":100644"
                and fields[1] == "100755"
            )
            if not mode_change_is_visible:
                self.skipTest(
                    "Git/filesystem does not expose chmod-only executable-bit "
                    "changes (expected on native Windows/NTFS)"
                )

            executable = build_candidate_identity(repo, baseline_sha=baseline)
            self.assertEqual(executable.changed_paths, ("a.txt",))
            self.assertEqual(executable.entries[0]["mode"], "100755")
        finally:
            target.chmod(original_mode)

        clean = build_candidate_identity(repo, baseline_sha=baseline)
        self.assertEqual(clean.changed_paths, ())
        self.assertNotEqual(executable.candidate_id, clean.candidate_id)

    def test_harness_runtime_paths_do_not_change_candidate_id(self) -> None:
        repo = self.make_repo()
        baseline = git(repo, "rev-parse", "HEAD")
        before = build_candidate_identity(repo, baseline_sha=baseline)
        (repo / ".harness_tmp" / "planner").mkdir(parents=True)
        (repo / ".harness_tmp" / "planner" / "probe.txt").write_text(
            "probe", encoding="utf-8"
        )
        (repo / ".venv").mkdir()
        (repo / ".venv" / "runtime.txt").write_text("runtime", encoding="utf-8")
        after = build_candidate_identity(repo, baseline_sha=baseline)
        self.assertEqual(before.candidate_id, after.candidate_id)
        self.assertEqual(after.changed_paths, ())


class RunStateTests(unittest.TestCase):
    def make_state(self) -> RunState:
        root = Path(tempfile.mkdtemp(prefix="slivin-run-state-"))
        return RunState.create(
            path=root / "run_state.json",
            task_id="TASK",
            harness_version="test",
            workflow_version="workflow.v4",
            mode=WorkflowMode.PRODUCTION,
            pipeline_profile=PipelineProfile.FULL,
        )

    def advance_to_contract(self, state: RunState) -> None:
        state.begin_stage(StageId.INTAKE_PREFLIGHT)
        state.bump_revision(RevisionKind.TASK_CONTRACT, artifact="task_contract_01.json")
        state.pass_stage(StageId.INTAKE_PREFLIGHT, StageResultCode.PREFLIGHT_READY)
        state.begin_stage(StageId.PLANNER)
        state.bump_revision(RevisionKind.PLAN, artifact="plan_01.json")
        state.pass_stage(StageId.PLANNER, StageResultCode.PLANNER_READY)
        state.begin_stage(StageId.IMPLEMENTATION_CONTRACT)
        state.bump_revision(
            RevisionKind.IMPLEMENTATION_CONTRACT,
            artifact="implementation_contract_01.json",
        )
        state.bump_revision(
            RevisionKind.VERIFICATION_PLAN,
            artifact="verification_plan_01.json",
        )
        state.pass_stage(
            StageId.IMPLEMENTATION_CONTRACT,
            StageResultCode.IMPLEMENTATION_CONTRACT_READY,
        )

    def test_run_state_persists_revisions_and_legal_transitions(self) -> None:
        state = self.make_state()
        self.advance_to_contract(state)
        payload = json.loads(state.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["revisions"]["task_contract"], 1)
        self.assertEqual(payload["revisions"]["plan"], 1)
        self.assertEqual(payload["revisions"]["implementation_contract"], 1)
        self.assertEqual(payload["revisions"]["verification_plan"], 1)
        self.assertEqual(payload["cursor_stage"], StageId.IMPLEMENTATION_CONTRACT.value)
        self.assertGreaterEqual(len(payload["events"]), 8)

    def test_illegal_transition_is_rejected(self) -> None:
        state = self.make_state()
        with self.assertRaises(WorkflowStateError):
            state.begin_stage(StageId.FINAL_GATE)

    def test_contract_expansion_invalidates_implementer_and_downstream(self) -> None:
        state = self.make_state()
        self.advance_to_contract(state)
        state.begin_stage(StageId.IMPLEMENTER)
        state.pass_stage(StageId.IMPLEMENTER, StageResultCode.IMPLEMENTATION_COMPLETE)
        state.begin_stage(StageId.DETERMINISTIC_CHECKS)
        state.pass_stage(
            StageId.DETERMINISTIC_CHECKS,
            StageResultCode.DETERMINISTIC_VERIFICATION_PASS,
        )
        state.invalidate(InvalidationTrigger.CONTRACT_EXPANDED, detail="new consumer")
        payload = state.data
        self.assertEqual(payload["stages"]["implementer"]["state"], StageState.INVALIDATED.value)
        self.assertEqual(
            payload["stages"]["deterministic_checks"]["state"],
            StageState.INVALIDATED.value,
        )
        self.assertEqual(payload["cursor_stage"], StageId.PLANNER.value)
        self.assertIsNone(payload["stages"]["implementer"]["result_code"])
        self.assertEqual(
            payload["stages"]["implementer"]["invalidation"]["trigger"],
            InvalidationTrigger.CONTRACT_EXPANDED.value,
        )
        state.begin_stage(StageId.IMPLEMENTATION_CONTRACT)
        state.pass_stage(
            StageId.IMPLEMENTATION_CONTRACT,
            StageResultCode.IMPLEMENTATION_CONTRACT_READY,
        )
        state.begin_stage(StageId.IMPLEMENTER)

    def test_stage_success_code_must_match_stage(self) -> None:
        state = self.make_state()
        state.begin_stage(StageId.INTAKE_PREFLIGHT)
        with self.assertRaises(WorkflowStateError):
            state.pass_stage(
                StageId.INTAKE_PREFLIGHT,
                StageResultCode.EVALUATION_PASS,
            )

    def test_skip_requires_a_declared_skip_code(self) -> None:
        state = self.make_state()
        state.begin_stage(StageId.INTAKE_PREFLIGHT)
        with self.assertRaises(WorkflowStateError):
            state.skip_stage(
                StageId.INTAKE_PREFLIGHT,
                StageResultCode.PREFLIGHT_READY,
                reason_code="not optional",
            )

    def test_skip_code_cannot_be_recorded_as_passed(self) -> None:
        state = self.make_state()
        state.begin_stage(StageId.INTAKE_PREFLIGHT)
        state.bump_revision(RevisionKind.TASK_CONTRACT, artifact="task_contract_01.json")
        state.pass_stage(StageId.INTAKE_PREFLIGHT, StageResultCode.PREFLIGHT_READY)
        state.begin_stage(StageId.PLANNER)
        with self.assertRaises(WorkflowStateError):
            state.pass_stage(
                StageId.PLANNER,
                StageResultCode.PLANNER_SKIPPED_FAST,
            )

    def test_final_result_must_match_workflow_mode(self) -> None:
        state = self.make_state()
        self.advance_to_contract(state)
        state.begin_stage(StageId.IMPLEMENTER)
        state.pass_stage(StageId.IMPLEMENTER, StageResultCode.IMPLEMENTATION_COMPLETE)
        state.begin_stage(StageId.DETERMINISTIC_CHECKS)
        state.pass_stage(
            StageId.DETERMINISTIC_CHECKS,
            StageResultCode.DETERMINISTIC_VERIFICATION_PASS,
        )
        state.begin_stage(StageId.RUNTIME_VERIFICATION)
        state.skip_stage(
            StageId.RUNTIME_VERIFICATION,
            StageResultCode.RUNTIME_VERIFICATION_SKIPPED,
            reason_code="test",
        )
        state.begin_stage(StageId.EVALUATOR)
        state.pass_stage(StageId.EVALUATOR, StageResultCode.EVALUATION_PASS)
        state.begin_stage(StageId.FINAL_GATE)
        with self.assertRaises(WorkflowStateError):
            state.pass_stage(
                StageId.FINAL_GATE,
                StageResultCode.HARNESS_BENCHMARK_PASS,
            )

    def test_terminal_pass_requires_completed_final_gate(self) -> None:
        state = self.make_state()
        with self.assertRaises(WorkflowStateError):
            state.mark_terminal(
                outcome=WorkflowOutcome.PASS,
                result_code=StageResultCode.HARNESS_TASK_PASS,
            )

    def test_full_production_path_reaches_terminal_task_pass(self) -> None:
        state = self.make_state()
        self.advance_to_contract(state)
        state.begin_stage(StageId.IMPLEMENTER)
        state.pass_stage(StageId.IMPLEMENTER, StageResultCode.IMPLEMENTATION_COMPLETE)
        state.begin_stage(StageId.DETERMINISTIC_CHECKS)
        state.pass_stage(
            StageId.DETERMINISTIC_CHECKS,
            StageResultCode.DETERMINISTIC_VERIFICATION_PASS,
        )
        state.begin_stage(StageId.RUNTIME_VERIFICATION)
        state.skip_stage(
            StageId.RUNTIME_VERIFICATION,
            StageResultCode.RUNTIME_VERIFICATION_SKIPPED,
            reason_code="phase1",
        )
        state.begin_stage(StageId.EVALUATOR)
        state.pass_stage(StageId.EVALUATOR, StageResultCode.EVALUATION_PASS)
        state.begin_stage(StageId.FINAL_GATE)
        state.pass_stage(StageId.FINAL_GATE, StageResultCode.HARNESS_TASK_PASS)
        state.mark_terminal(
            outcome=WorkflowOutcome.PASS,
            result_code=StageResultCode.HARNESS_TASK_PASS,
        )
        self.assertEqual(state.data["cursor_stage"], StageId.FINAL_GATE.value)
        self.assertEqual(
            state.data["terminal"]["result_code"],
            StageResultCode.HARNESS_TASK_PASS.value,
        )

    def test_benchmark_semantic_fail_is_a_valid_terminal_failure(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-run-state-benchmark-fail-"))
        state = RunState.create(
            path=root / "run_state.json",
            task_id="BENCH",
            harness_version="test",
            workflow_version="workflow.v6",
            mode=WorkflowMode.HISTORICAL_BENCHMARK,
            pipeline_profile=PipelineProfile.FULL,
        )
        state.begin_stage(StageId.INTAKE_PREFLIGHT)
        state.route_stage(
            StageId.INTAKE_PREFLIGHT,
            outcome=WorkflowOutcome.FAIL,
            result_code=StageResultCode.HARNESS_BENCHMARK_FAIL,
            reason_code="HELDOUT_SEMANTIC_FAIL",
        )
        self.assertEqual(
            state.data["terminal"]["result_code"],
            StageResultCode.HARNESS_BENCHMARK_FAIL.value,
        )
        self.assertEqual(state.data["terminal"]["outcome"], WorkflowOutcome.FAIL.value)

    def test_error_after_routed_terminal_preserves_specific_reason(self) -> None:
        state = self.make_state()
        state.begin_stage(StageId.INTAKE_PREFLIGHT)
        state.route_stage(
            StageId.INTAKE_PREFLIGHT,
            outcome=WorkflowOutcome.BLOCKED,
            result_code=StageResultCode.BLOCKED,
            reason_code="SPECIFIC_BLOCKER",
        )
        state.fail_active_stage(reason_code="HARNESS_EXCEPTION", detail="boom")
        self.assertEqual(state.data["terminal"]["reason_code"], "SPECIFIC_BLOCKER")
        self.assertEqual(state.data["events"][-1]["event"], "RUN_ERROR")

    def test_replan_creates_new_attempt_and_restarts_at_planner(self) -> None:
        state = self.make_state()
        self.advance_to_contract(state)
        state.begin_stage(StageId.IMPLEMENTER)
        state.pass_stage(StageId.IMPLEMENTER, StageResultCode.IMPLEMENTATION_COMPLETE)
        state.begin_stage(StageId.DETERMINISTIC_CHECKS)
        state.pass_stage(
            StageId.DETERMINISTIC_CHECKS,
            StageResultCode.DETERMINISTIC_VERIFICATION_PASS,
        )
        state.begin_stage(StageId.RUNTIME_VERIFICATION)
        state.skip_stage(
            StageId.RUNTIME_VERIFICATION,
            StageResultCode.RUNTIME_VERIFICATION_SKIPPED,
            reason_code="test",
        )
        state.begin_stage(StageId.EVALUATOR)
        state.route_stage(
            StageId.EVALUATOR,
            outcome=WorkflowOutcome.REPLAN,
            result_code=StageResultCode.REPLAN_REQUIRED,
            reason_code="wrong model",
        )
        state.invalidate(InvalidationTrigger.REPLAN_REQUIRED, detail="wrong model")
        self.assertEqual(state.data["attempt_id"], 2)
        self.assertEqual(state.data["cursor_stage"], StageId.INTAKE_PREFLIGHT.value)
        state.begin_stage(StageId.PLANNER)


if __name__ == "__main__":
    unittest.main()
