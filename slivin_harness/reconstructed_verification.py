from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from slivin_harness.control_plane import ControllerPlane
from slivin_harness.execution import ExecutionBroker, ExecutionRole
from slivin_harness.git_integrity import (
    GitControlIntegrityManager,
    TrustedBatchIntegrityCoordinator,
)
from slivin_harness.phase4 import CheckClassification
from slivin_harness.phase5 import ProjectRuntimeManager
from slivin_harness.phase7 import (
    ReconstructionPreparation,
    ReconstructedVerificationResult,
    classify_heldout_results,
    run_reconstructed_verification,
    sanitize_benchmark_toolchain,
)
from slivin_harness.preflight import ToolProbeRegistry, run_static_toolchain_preflight
from slivin_harness.run_state import CandidateIdentity, build_candidate_identity
from slivin_harness.runtime_projection import RuntimeProjectionIntegrityManager
from slivin_harness.workflow import WorkflowMode
from slivin_harness.workspace import (
    WorkspaceSession,
    add_worktree_excludes,
    materialize_authoritative_runtime_copy,
)


def run_authoritative_reconstructed_verification(
    *,
    source_session: WorkspaceSession,
    repository: Path,
    expected_candidate: CandidateIdentity,
    patch: bytes,
    private_root: Path,
    proof_run_root: Path,
    harness_root: Path,
    local_config: Mapping[str, Any],
    manifest: Mapping[str, Any],
    runtime_config: Any | None,
    workflow_mode: WorkflowMode,
    benchmark_failure_marker: str,
    heldout_specs: Sequence[Mapping[str, Any]],
    active_repair_specs: Callable[[], list[dict[str, Any]]],
    local_runtime_files_baseline: Mapping[str, str],
    resolve_toolchain: Callable[..., dict[str, str]],
    validate_toolchain: Callable[[dict[str, str]], None],
    exposed_runtime_file_snapshot: Callable[[WorkspaceSession], Mapping[str, str]],
    run_checks: Callable[..., list[Any]],
    check_records: Callable[[list[Any]], list[dict[str, Any]]],
) -> ReconstructedVerificationResult:
    """Materialize and replay final evidence in a clean proof repository."""

    repair_specs = active_repair_specs()

    def prepare(proof_workspace: Path) -> ReconstructionPreparation:
        proof_session = materialize_authoritative_runtime_copy(
            source_session,
            workspace=proof_workspace,
        )
        proof_excludes = {
            ".git",
            ".harness_tmp",
            ".harness_git_excludes",
            *proof_session.exposed_paths,
            *(item.relative_path for item in proof_session.runtime_projections),
        }
        if runtime_config is not None:
            add_worktree_excludes(proof_workspace, [runtime_config.venv_relative])
            proof_excludes.add(runtime_config.venv_relative)
        return ReconstructionPreparation(
            context=proof_session,
            excluded_prefixes=tuple(sorted(proof_excludes)),
        )

    def verify(
        proof_workspace: Path,
        reconstructed_candidate: CandidateIdentity,
        proof_session: WorkspaceSession,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        proof_plane = ControllerPlane(proof_run_root)
        proof_broker = ExecutionBroker(
            workspace=proof_workspace,
            run_root=proof_run_root,
            private_root=proof_plane.private_root,
        )
        proof_git = GitControlIntegrityManager(
            workspace=proof_workspace,
            control_plane=proof_plane,
        )
        proof_git.establish_baseline()
        proof_runtime = RuntimeProjectionIntegrityManager(
            session=proof_session,
            control_plane=proof_plane,
        )
        proof_runtime.establish_baseline()
        proof_coordinator = TrustedBatchIntegrityCoordinator(
            git_manager=proof_git,
            runtime_manager=proof_runtime,
            candidate_identity=lambda: build_candidate_identity(
                proof_workspace,
                baseline_sha=reconstructed_candidate.baseline_sha,
            ),
        )

        proof_runtime_state = None
        if runtime_config is not None:
            proof_project_runtime = ProjectRuntimeManager(
                workspace=proof_workspace,
                config=runtime_config,
                environment=proof_broker.environment_for(ExecutionRole.RUNTIME),
            )
            proof_runtime_state = proof_coordinator.run_read_only(
                "RECONSTRUCTED_PROJECT_RUNTIME_BOOTSTRAP",
                lambda: proof_project_runtime.build(clean=True),
            )

        if (
            exposed_runtime_file_snapshot(proof_session)
            != local_runtime_files_baseline
        ):
            raise RuntimeError("RECONSTRUCTED_EXPOSED_RUNTIME_MISMATCH")

        proof_toolchain = resolve_toolchain(
            local_config,
            manifest,
            project_name=proof_session.project_name,
            project_root=proof_session.source_repo or proof_workspace,
        )
        if proof_runtime_state is not None:
            proof_toolchain["project_python"] = proof_runtime_state.project_python
        proof_rebound: dict[str, str] = {}
        if workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK:
            proof_sanitization = sanitize_benchmark_toolchain(
                toolchain=proof_toolchain,
                source_repo=proof_session.source_repo,
                workspace=proof_workspace,
                runtime_projections=proof_session.runtime_projections,
            )
            proof_toolchain = proof_sanitization.toolchain
            proof_rebound = dict(proof_sanitization.rebound_to_workspace)
        validate_toolchain(proof_toolchain)
        proof_probes = ToolProbeRegistry(
            workspace=proof_workspace,
            harness_root=harness_root,
            source_repo=proof_session.source_repo,
            toolchain=proof_toolchain,
            execution_broker=proof_broker,
            control_plane=proof_plane,
            runtime_integrity_manager=proof_runtime,
            git_integrity_manager=proof_git,
            integrity_coordinator=proof_coordinator,
            historical=workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK,
            rebound_to_workspace=proof_rebound,
        )
        proof_preflight = run_static_toolchain_preflight(
            [*repair_specs, *heldout_specs],
            workspace=proof_workspace,
            harness_root=harness_root,
            toolchain=proof_toolchain,
            probe_registry=proof_probes,
            candidate_baseline_sha=reconstructed_candidate.baseline_sha,
        )
        public = {
            "static_preflight_status": proof_preflight.status,
            "repair_checks_status": "NOT_RUN",
            "heldout_status": "NOT_APPLICABLE",
            "runtime_projection_status": "PASS",
            "git_control_status": "PASS",
            "reason_code": None,
        }
        private: dict[str, Any] = {
            "static_preflight": proof_preflight.private_dict(),
            "repair_checks": [],
            "heldout_checks": [],
        }
        if not proof_preflight.passed:
            public["reason_code"] = "RECONSTRUCTED_STATIC_PREFLIGHT_FAILED"
            return public, private

        proof_repair_results = run_checks(
            repair_specs,
            workspace=proof_workspace,
            toolchain=proof_toolchain,
            runtime_root=proof_run_root / "repair_checks",
            label="RECONSTRUCTED REPAIR CHECKS",
            execution_broker=proof_broker,
            execution_role=ExecutionRole.CONTROLLER_CHECK,
            runtime_integrity_manager=proof_runtime,
            git_integrity_manager=proof_git,
            batch_id="RECONSTRUCTED_REPAIR_CHECKS",
            publish_output=False,
        )
        private["repair_checks"] = check_records(proof_repair_results)
        repair_git_reason = next(
            (
                item.git_integrity_reason_code
                for item in proof_repair_results
                if item.git_integrity_reason_code
            ),
            None,
        )
        repair_runtime_reason = next(
            (
                item.runtime_integrity_reason_code
                for item in proof_repair_results
                if item.runtime_integrity_reason_code
            ),
            None,
        )
        if repair_git_reason:
            public["git_control_status"] = "FAIL"
            public["reason_code"] = repair_git_reason
        if repair_runtime_reason:
            public["runtime_projection_status"] = "FAIL"
            public["reason_code"] = repair_runtime_reason
        repair_pass = bool(proof_repair_results) and all(
            item.classification == CheckClassification.PASS
            for item in proof_repair_results
        )
        public["repair_checks_status"] = "PASS" if repair_pass else "FAIL"
        if not repair_pass:
            if not public["reason_code"]:
                public["reason_code"] = "RECONSTRUCTED_REPAIR_CHECKS_FAILED"
            return public, private

        if workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK:
            before_heldout = build_candidate_identity(
                proof_workspace,
                baseline_sha=reconstructed_candidate.baseline_sha,
            )
            proof_heldout_results = run_checks(
                heldout_specs,
                workspace=proof_workspace,
                toolchain=proof_toolchain,
                runtime_root=proof_run_root / "heldout_checks",
                label="RECONSTRUCTED HELD-OUT CHECKS",
                execution_broker=proof_broker,
                execution_role=ExecutionRole.HELDOUT,
                runtime_integrity_manager=proof_runtime,
                git_integrity_manager=proof_git,
                batch_id="RECONSTRUCTED_HELDOUT_CHECKS",
                publish_output=False,
            )
            after_heldout = build_candidate_identity(
                proof_workspace,
                baseline_sha=reconstructed_candidate.baseline_sha,
            )
            proof_heldout = classify_heldout_results(
                results=proof_heldout_results,
                oracle_marker=benchmark_failure_marker,
                candidate_before=before_heldout.candidate_id,
                candidate_after=after_heldout.candidate_id,
            )
            private["heldout_checks"] = check_records(proof_heldout_results)
            private["heldout_evidence"] = proof_heldout
            public["heldout_status"] = proof_heldout["status"]
            heldout_git_reason = next(
                (
                    item.git_integrity_reason_code
                    for item in proof_heldout_results
                    if item.git_integrity_reason_code
                ),
                None,
            )
            heldout_runtime_reason = next(
                (
                    item.runtime_integrity_reason_code
                    for item in proof_heldout_results
                    if item.runtime_integrity_reason_code
                ),
                None,
            )
            if heldout_git_reason:
                public["git_control_status"] = "FAIL"
                public["reason_code"] = heldout_git_reason
            if heldout_runtime_reason:
                public["runtime_projection_status"] = "FAIL"
                public["reason_code"] = heldout_runtime_reason
            if proof_heldout["status"] != "HELDOUT_PASS" and not public["reason_code"]:
                public["reason_code"] = str(
                    proof_heldout.get("reason_code") or "RECONSTRUCTED_HELDOUT_FAILED"
                )
        return public, private

    return run_reconstructed_verification(
        repository=repository,
        baseline_sha=expected_candidate.baseline_sha,
        patch=patch,
        expected_candidate=expected_candidate,
        private_root=private_root,
        prepare_workspace=prepare,
        verify=verify,
    )
