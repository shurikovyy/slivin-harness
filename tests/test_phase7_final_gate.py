from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from slivin_harness.phase4 import CheckClassification
from slivin_harness.phase7 import (
    FINAL_ACCEPTANCE_VERSION,
    PATCH_PROOF_VERSION,
    Phase7Error,
    build_final_acceptance,
    build_patch_reconstruction_proof,
    classify_heldout_results,
    deliver_candidate_transaction,
    reconcile_quality_gate,
    reset_workspace_for_semantic_replan,
    sanitize_benchmark_toolchain,
)
from slivin_harness.run_state import RunState, build_candidate_identity
from slivin_harness.workflow import (
    PipelineProfile,
    RevisionKind,
    StageId,
    StageResultCode,
    WorkflowMode,
    WorkflowOutcome,
)
from slivin_harness.workspace import (
    WorkspaceSession,
    build_candidate_patch,
    prepare_workspace_session,
    remove_managed_workspace,
)


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "keep.txt").write_text("before\n", encoding="utf-8")
    (repo / "delete.txt").write_text("remove\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "baseline")
    return repo


class PhaseSevenFinalGateTests(unittest.TestCase):
    def _advanced_state(self, repo: Path) -> tuple[RunState, object]:
        state_root = Path(tempfile.mkdtemp(prefix="phase7-state-"))
        state = RunState.create(
            path=state_root / "run_state.json",
            task_id="TASK",
            harness_version="test",
            workflow_version="workflow.v6",
            mode=WorkflowMode.PRODUCTION,
            pipeline_profile=PipelineProfile.FULL,
        )
        head = git(repo, "rev-parse", "HEAD")
        state.begin_stage(StageId.INTAKE_PREFLIGHT)
        state.bump_revision(RevisionKind.TASK_CONTRACT, artifact="task_contract.json")
        state.pass_stage(StageId.INTAKE_PREFLIGHT, StageResultCode.PREFLIGHT_READY)
        state.begin_stage(StageId.PLANNER)
        state.bump_revision(RevisionKind.PLAN, artifact="plan.json")
        state.pass_stage(StageId.PLANNER, StageResultCode.PLANNER_READY)
        state.begin_stage(StageId.IMPLEMENTATION_CONTRACT)
        state.bump_revision(RevisionKind.IMPLEMENTATION_CONTRACT, artifact="contract.json")
        state.bump_revision(RevisionKind.VERIFICATION_PLAN, artifact="verification.json")
        state.bump_revision(RevisionKind.RUNTIME_ENVIRONMENT, artifact="runtime.json")
        state.pass_stage(
            StageId.IMPLEMENTATION_CONTRACT,
            StageResultCode.IMPLEMENTATION_CONTRACT_READY,
        )
        (repo / "keep.txt").write_text("after\n", encoding="utf-8")
        candidate = build_candidate_identity(repo, baseline_sha=head)
        state.observe_candidate(candidate, reason_code="test")
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
            reason_code="local",
        )
        state.begin_stage(StageId.EVALUATOR)
        state.pass_stage(StageId.EVALUATOR, StageResultCode.EVALUATION_PASS)
        state.begin_stage(StageId.FINAL_GATE)
        return state, candidate

    def test_quality_reconciliation_requires_one_candidate_and_current_revisions(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-reconcile-"))
        repo = make_repo(root)
        state, candidate = self._advanced_state(repo)
        record = reconcile_quality_gate(
            run_state_data=state.data,
            final_candidate=candidate,
            mode=WorkflowMode.PRODUCTION,
        )
        self.assertEqual(record["candidate_id"], candidate.candidate_id)
        self.assertEqual(record["attempt_id"], 1)
        self.assertEqual(len(record["stage_bindings"]), 7)

        state.data["stages"][StageId.DETERMINISTIC_CHECKS.value]["candidate_id"] = "wrong"
        with self.assertRaisesRegex(Phase7Error, "different candidate"):
            reconcile_quality_gate(
                run_state_data=state.data,
                final_candidate=candidate,
                mode=WorkflowMode.PRODUCTION,
            )

    def test_patch_reconstruction_proves_binary_add_delete_and_modify(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-patch-"))
        repo = make_repo(root)
        head = git(repo, "rev-parse", "HEAD")
        (repo / "keep.txt").write_text("after\n", encoding="utf-8")
        (repo / "delete.txt").unlink()
        (repo / "new.bin").write_bytes(b"\x00\xff\x01binary")
        session = WorkspaceSession(workspace=repo, mode="static", managed=False, base_sha=head)
        patch = build_candidate_patch(session)
        candidate = build_candidate_identity(repo, baseline_sha=head)
        proof = build_patch_reconstruction_proof(
            repository=repo,
            baseline_sha=head,
            patch=patch,
            expected_candidate=candidate,
            private_root=root / "private",
        )
        self.assertEqual(proof["schema_version"], PATCH_PROOF_VERSION)
        self.assertEqual(proof["status"], "PATCH_RECONSTRUCTION_PASS")
        self.assertEqual(proof["reconstructed_candidate_id"], candidate.candidate_id)

        with self.assertRaises(Phase7Error):
            build_patch_reconstruction_proof(
                repository=repo,
                baseline_sha=head,
                patch=patch.replace(b"+after", b"+wrong", 1),
                expected_candidate=candidate,
                private_root=root / "private2",
            )

    def test_patch_reconstruction_preserves_source_checkout_eol_policy(self) -> None:
        """Regression for native Windows ``core.autocrlf=true`` worktrees.

        The patch contains canonical LF lines, while ``candidate.v1`` binds the
        exact CRLF bytes visible in the accepted worktree.  Reconstruction must
        mirror the source checkout policy instead of forcing LF bytes.
        """

        root = Path(tempfile.mkdtemp(prefix="phase7-autocrlf-"))
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "core.autocrlf", "true")
        (repo / "target.txt").write_bytes(b"before\r\n")
        git(repo, "add", "target.txt")
        git(repo, "commit", "-m", "baseline")
        head = git(repo, "rev-parse", "HEAD")

        (repo / "target.txt").write_bytes(b"after\r\n")
        session = WorkspaceSession(
            workspace=repo,
            mode="static",
            managed=False,
            base_sha=head,
        )
        patch = build_candidate_patch(session)
        candidate = build_candidate_identity(repo, baseline_sha=head)
        target_entry = next(
            item for item in candidate.entries if item["path"] == "target.txt"
        )
        self.assertEqual(target_entry["size"], len(b"after\r\n"))

        proof = build_patch_reconstruction_proof(
            repository=repo,
            baseline_sha=head,
            patch=patch,
            expected_candidate=candidate,
            private_root=root / "private",
        )
        self.assertEqual(proof["status"], "PATCH_RECONSTRUCTION_PASS")
        self.assertEqual(proof["reconstructed_candidate_id"], candidate.candidate_id)

    def test_patch_reconstruction_accepts_identity_candidate(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-empty-patch-"))
        repo = make_repo(root)
        head = git(repo, "rev-parse", "HEAD")
        candidate = build_candidate_identity(repo, baseline_sha=head)
        proof = build_patch_reconstruction_proof(
            repository=repo,
            baseline_sha=head,
            patch=b"",
            expected_candidate=candidate,
            private_root=root / "private",
        )
        self.assertEqual(proof["status"], "PATCH_RECONSTRUCTION_PASS")
        self.assertEqual(proof["patch_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(proof["changed_paths"], [])

    def test_delivery_transaction_applies_exact_candidate(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-delivery-"))
        source = make_repo(root)
        local = {
            "projects": {
                "demo": {
                    "repo": str(source),
                    "result_mode": "apply_to_source",
                }
            },
            "workspace": {"root": str(root / "workspaces")},
        }
        session = prepare_workspace_session(
            manifest={
                "project": "demo",
                "workspace_mode": "git_worktree",
                "result_mode": "apply_to_source",
                "checks": [{"feedback": "repair"}],
            },
            local_config=local,
            harness_root=root,
            task_id="DELIVER",
        )
        try:
            (session.workspace / "keep.txt").write_text("after\n", encoding="utf-8")
            patch = build_candidate_patch(session)
            candidate = build_candidate_identity(
                session.workspace,
                baseline_sha=session.base_sha,
            )
            result = deliver_candidate_transaction(
                session=session,
                patch=patch,
                final_candidate=candidate,
            )
            self.assertEqual(result.status, StageResultCode.RESULT_DELIVERY_PASS.value)
            self.assertTrue(result.exact_patch_match)
            self.assertEqual((source / "keep.txt").read_text(encoding="utf-8"), "after\n")
            self.assertEqual(build_candidate_patch(WorkspaceSession(workspace=source, mode="static", managed=False)), patch)
        finally:
            remove_managed_workspace(session)

    def test_delivery_conflict_keeps_accepted_candidate_and_does_not_touch_source(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-delivery-blocked-"))
        source = make_repo(root)
        local = {
            "projects": {"demo": {"repo": str(source), "result_mode": "apply_to_source"}},
            "workspace": {"root": str(root / "workspaces")},
        }
        session = prepare_workspace_session(
            manifest={
                "project": "demo",
                "workspace_mode": "git_worktree",
                "result_mode": "apply_to_source",
                "checks": [{"feedback": "repair"}],
            },
            local_config=local,
            harness_root=root,
            task_id="BLOCKED",
        )
        try:
            (session.workspace / "keep.txt").write_text("candidate\n", encoding="utf-8")
            patch = build_candidate_patch(session)
            candidate = build_candidate_identity(session.workspace, baseline_sha=session.base_sha)
            (source / "keep.txt").write_text("user change\n", encoding="utf-8")
            result = deliver_candidate_transaction(
                session=session,
                patch=patch,
                final_candidate=candidate,
            )
            self.assertEqual(result.status, StageResultCode.RESULT_DELIVERY_BLOCKED.value)
            self.assertEqual(result.reason_code, "SOURCE_WORKTREE_NOT_CLEAN")
            self.assertEqual((source / "keep.txt").read_text(encoding="utf-8"), "user change\n")
            self.assertEqual((session.workspace / "keep.txt").read_text(encoding="utf-8"), "candidate\n")
        finally:
            (source / "keep.txt").write_text("before\n", encoding="utf-8")
            remove_managed_workspace(session)

    def test_delivery_mismatch_rolls_back_only_harness_postimage(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-delivery-rollback-"))
        source = make_repo(root)
        local = {
            "projects": {"demo": {"repo": str(source), "result_mode": "apply_to_source"}},
            "workspace": {"root": str(root / "workspaces")},
        }
        session = prepare_workspace_session(
            manifest={
                "project": "demo",
                "workspace_mode": "git_worktree",
                "result_mode": "apply_to_source",
                "checks": [{"feedback": "repair"}],
            },
            local_config=local,
            harness_root=root,
            task_id="ROLLBACK",
        )
        try:
            (session.workspace / "keep.txt").write_text("candidate\n", encoding="utf-8")
            patch = build_candidate_patch(session)
            candidate = build_candidate_identity(session.workspace, baseline_sha=session.base_sha)
            with mock.patch(
                "slivin_harness.phase7.build_repository_patch",
                return_value=b"unexpected diff",
            ):
                result = deliver_candidate_transaction(
                    session=session,
                    patch=patch,
                    final_candidate=candidate,
                )
            self.assertEqual(result.status, StageResultCode.RESULT_DELIVERY_FAIL.value)
            self.assertEqual(result.reason_code, "APPLIED_SOURCE_DIFF_MISMATCH")
            self.assertEqual(result.rollback_status, "ROLLBACK_PASS")
            self.assertEqual(result.conflict_paths, ())
            self.assertEqual((source / "keep.txt").read_text(encoding="utf-8"), "before\n")
            self.assertEqual(build_candidate_patch(WorkspaceSession(workspace=source, mode="static", managed=False)), b"")
        finally:
            remove_managed_workspace(session)

    def test_delivery_rollback_does_not_overwrite_concurrent_user_edit(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-delivery-conflict-rollback-"))
        source = make_repo(root)
        local = {
            "projects": {"demo": {"repo": str(source), "result_mode": "apply_to_source"}},
            "workspace": {"root": str(root / "workspaces")},
        }
        session = prepare_workspace_session(
            manifest={
                "project": "demo",
                "workspace_mode": "git_worktree",
                "result_mode": "apply_to_source",
                "checks": [{"feedback": "repair"}],
            },
            local_config=local,
            harness_root=root,
            task_id="ROLLBACK_CONFLICT",
        )
        try:
            (session.workspace / "keep.txt").write_text("candidate\n", encoding="utf-8")
            patch = build_candidate_patch(session)
            candidate = build_candidate_identity(session.workspace, baseline_sha=session.base_sha)

            def concurrent_edit(_repo: Path) -> bytes:
                (source / "keep.txt").write_text("user concurrent edit\n", encoding="utf-8")
                return b"unexpected diff"

            with mock.patch(
                "slivin_harness.phase7.build_repository_patch",
                side_effect=concurrent_edit,
            ):
                result = deliver_candidate_transaction(
                    session=session,
                    patch=patch,
                    final_candidate=candidate,
                )
            self.assertEqual(result.status, StageResultCode.RESULT_DELIVERY_FAIL.value)
            self.assertEqual(result.reason_code, "SOURCE_CHANGED_DURING_DELIVERY")
            self.assertEqual(result.rollback_status, "ROLLBACK_CONFLICT")
            self.assertEqual(result.conflict_paths, ("keep.txt",))
            self.assertEqual(
                (source / "keep.txt").read_text(encoding="utf-8"),
                "user concurrent edit\n",
            )
        finally:
            (source / "keep.txt").write_text("before\n", encoding="utf-8")
            remove_managed_workspace(session)

    def test_heldout_classification_requires_oracle_and_separates_semantic_fail(self) -> None:
        def result(*, rc: int, output: str, timeout: bool = False, infra: bool = False):
            classification = (
                CheckClassification.INFRA_ERROR
                if infra
                else CheckClassification.TIMEOUT
                if timeout
                else CheckClassification.PASS
                if rc == 0
                else CheckClassification.FAIL
            )
            return SimpleNamespace(
                name="oracle",
                returncode=rc,
                output=output,
                timed_out=timeout,
                infra_error=infra,
                duration_seconds=0.1,
                classification=classification,
            )

        passed = classify_heldout_results(
            results=[result(rc=0, output="ORACLE_REACHED\nPASS")],
            oracle_marker="ORACLE_REACHED",
            candidate_before="c",
            candidate_after="c",
        )
        self.assertEqual(passed["status"], "HELDOUT_PASS")
        semantic = classify_heldout_results(
            results=[result(rc=1, output="ORACLE_REACHED\nFAIL")],
            oracle_marker="ORACLE_REACHED",
            candidate_before="c",
            candidate_after="c",
        )
        self.assertEqual(semantic["status"], "HELDOUT_SEMANTIC_FAIL")
        infra = classify_heldout_results(
            results=[result(rc=1, output="node missing")],
            oracle_marker="ORACLE_REACHED",
            candidate_before="c",
            candidate_after="c",
        )
        self.assertEqual(infra["status"], "HELDOUT_INFRA_ERROR")
        mutated = classify_heldout_results(
            results=[result(rc=0, output="ORACLE_REACHED")],
            oracle_marker="ORACLE_REACHED",
            candidate_before="c1",
            candidate_after="c2",
        )
        self.assertEqual(mutated["status"], "HELDOUT_MUTATED_CANDIDATE")

    def test_benchmark_toolchain_removes_original_source_paths(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-toolchain-"))
        source = root / "source"
        workspace = root / "workspace"
        outside = root / "tools" / "node"
        source.mkdir()
        workspace.mkdir()
        outside.parent.mkdir()
        outside.write_text("tool", encoding="utf-8")
        source_python = source / ".venv" / "Scripts" / "python.exe"
        source_python.parent.mkdir(parents=True)
        source_python.write_text("python", encoding="utf-8")
        retained, removed = sanitize_benchmark_toolchain(
            toolchain={
                "project_python": str(source_python),
                "node": str(outside),
                "git": "git",
            },
            source_repo=source,
            workspace=workspace,
        )
        self.assertNotIn("project_python", retained)
        self.assertEqual(removed["project_python"], "SOURCE_REPOSITORY_PATH_REMOVED")
        self.assertEqual(retained["node"], str(outside))
        self.assertEqual(retained["git"], "git")

    def test_historical_workspace_is_standalone_and_hides_other_refs(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-benchmark-"))
        source = make_repo(root)
        baseline_branch = git(source, "branch", "--show-current")
        git(source, "checkout", "-b", "hidden-solution")
        (source / "reference_solution.txt").write_text("answer\n", encoding="utf-8")
        git(source, "add", "-A")
        git(source, "commit", "-m", "hidden answer")
        hidden_commit = git(source, "rev-parse", "HEAD")
        git(source, "checkout", baseline_branch)
        local = {
            "projects": {"bench": {"repo": str(source), "result_mode": "keep_worktree"}},
            "workspace": {"root": str(root / "workspaces")},
        }
        session = prepare_workspace_session(
            manifest={
                "project": "bench",
                "workspace_mode": "git_worktree",
                "result_mode": "keep_worktree",
                "benchmark": {"baseline_failure_marker": "ORACLE_REACHED"},
                "checks": [{"feedback": "heldout"}],
            },
            local_config=local,
            harness_root=root,
            task_id="BENCH",
        )
        try:
            self.assertTrue(session.benchmark_isolated)
            self.assertEqual(session.mode, "benchmark_isolated")
            self.assertTrue((session.workspace / ".git").is_dir())
            self.assertFalse((session.workspace / "reference_solution.txt").exists())
            self.assertEqual(
                git(session.workspace, "for-each-ref", "--format=%(refname)"),
                "",
            )
            probe = subprocess.run(
                ["git", "cat-file", "-e", hidden_commit],
                cwd=session.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(probe.returncode, 0)
            self.assertNotEqual(session.base_sha, session.source_base_sha)
        finally:
            remove_managed_workspace(session)

    def test_semantic_replan_reset_removes_rejected_candidate_but_keeps_runtime(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="phase7-replan-reset-"))
        repo = make_repo(root)
        head = git(repo, "rev-parse", "HEAD")
        (repo / ".gitignore").write_text(".env\n.venv/\n.harness_tmp/\n", encoding="utf-8")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-m", "runtime policy")
        head = git(repo, "rev-parse", "HEAD")
        (repo / ".env").write_text("SECRET=kept\n", encoding="utf-8")
        (repo / ".venv").mkdir()
        (repo / ".venv" / "state.txt").write_text("runtime\n", encoding="utf-8")
        (repo / ".harness_tmp").mkdir()
        (repo / ".harness_tmp" / "scratch.txt").write_text("scratch\n", encoding="utf-8")
        (repo / "keep.txt").write_text("rejected\n", encoding="utf-8")
        (repo / "new_test.py").write_text("assert False\n", encoding="utf-8")
        git(repo, "add", "keep.txt")

        record = reset_workspace_for_semantic_replan(
            workspace=repo,
            baseline_sha=head,
        )

        self.assertEqual(record["status"], "SEMANTIC_REPLAN_RESET_PASS")
        self.assertIn("new_test.py", record["removed_untracked_paths"])
        self.assertEqual((repo / "keep.txt").read_text(encoding="utf-8"), "before\n")
        self.assertFalse((repo / "new_test.py").exists())
        self.assertEqual((repo / ".env").read_text(encoding="utf-8"), "SECRET=kept\n")
        self.assertTrue((repo / ".venv" / "state.txt").is_file())
        self.assertTrue((repo / ".harness_tmp" / "scratch.txt").is_file())
        self.assertEqual(git(repo, "status", "--porcelain=v1", "--untracked-files=all"), "")

    def test_final_acceptance_is_built_after_patch_proof(self) -> None:
        candidate = SimpleNamespace(
            baseline_sha="b",
            workspace_head="w",
            candidate_id="c",
            changed_paths=("a.txt",),
        )
        payload = build_final_acceptance(
            task_id="T",
            harness_version="0.8.0a12",
            workflow_version="workflow.v6",
            mode=WorkflowMode.PRODUCTION,
            pipeline_profile="FULL",
            result_mode="keep_worktree",
            source_baseline_sha="source",
            final_candidate=candidate,
            quality_reconciliation={
                "status": "QUALITY_GATE_RECONCILIATION_PASS",
                "attempt_id": 1,
                "candidate_id": "c",
                "revision_snapshot": {},
                "stage_bindings": [],
            },
            patch_metadata={"sha256": "p", "path": "candidate.patch"},
            patch_proof={
                "status": "PATCH_RECONSTRUCTION_PASS",
                "patch_sha256": "p",
                "expected_candidate_id": "c",
                "reconstructed_candidate_id": "c",
            },
            artifact_bindings=[],
            heldout_evidence=None,
        )
        self.assertEqual(payload["schema_version"], FINAL_ACCEPTANCE_VERSION)
        self.assertEqual(payload["attempt_id"], 1)
        self.assertEqual(payload["patch_proof"]["status"], "PATCH_RECONSTRUCTION_PASS")

        bad_proof = dict(payload["patch_proof"])
        bad_proof["reconstructed_candidate_id"] = "other"
        with self.assertRaisesRegex(Phase7Error, "reconstructed candidate"):
            build_final_acceptance(
                task_id="T",
                harness_version="0.8.0a12",
                workflow_version="workflow.v6",
                mode=WorkflowMode.PRODUCTION,
                pipeline_profile="FULL",
                result_mode="keep_worktree",
                source_baseline_sha="source",
                final_candidate=candidate,
                quality_reconciliation={
                    "status": "QUALITY_GATE_RECONCILIATION_PASS",
                    "attempt_id": 1,
                    "candidate_id": "c",
                    "revision_snapshot": {},
                    "stage_bindings": [],
                },
                patch_metadata={"sha256": "p", "path": "candidate.patch"},
                patch_proof=bad_proof,
                artifact_bindings=[],
                heldout_evidence=None,
            )


if __name__ == "__main__":
    unittest.main()
