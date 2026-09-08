from __future__ import annotations

import contextlib
import copy
import dataclasses
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import task_runner
from slivin_harness.implementer import IMPLEMENTER_PROTOCOL_VERSION
from slivin_harness.planner import PlannerCapabilityInfeasible
from slivin_harness.protocol import EVALUATOR_PROTOCOL_VERSION
from slivin_harness.workflow import StageResultCode, StageState
from test_protocol import attach_post_patch_impact, valid_blind_audit, valid_pass, valid_plan, valid_task_contract, write_plan_evidence
from test_user_follow_up_handoff import related_finding, write_related_evidence
from slivin_harness.handoff import USER_FOLLOW_UP_ARTIFACT
from slivin_harness.phase7 import ReconstructedVerificationResult


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


class _FakeCodexAppServer:
    def __init__(self, *_args, **_kwargs) -> None:
        self._thread_index = 0
        self.retire_count = 0

    def __enter__(self) -> "_FakeCodexAppServer":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def start_thread(self, **_kwargs) -> str:
        self._thread_index += 1
        return f"thread-{self._thread_index}"

    def retire_readonly_threads(self) -> None:
        self.retire_count += 1


class TaskRunnerWorkflowIntegrationTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        write_plan_evidence(repo)
        write_related_evidence(repo)
        (repo / "reader_b.py").write_text("from reader import read_target\n\ndef read_summary():\n    return 'Value: ' + read_target()\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "baseline")
        return repo

    def write_manifest(
        self,
        root: Path,
        repo: Path,
        *,
        benchmark: bool,
        risk: str = "low",
        with_replan: bool = False,
        benchmark_fail: bool = False,
        owner_allowed_paths: list[str] | None = None,
        with_check_repair: bool = False,
    ) -> Path:
        heldout_command = (
            "import sys; print('ORACLE_REACHED'); sys.exit(1)"
            if benchmark_fail
            else "print('ORACLE_REACHED')"
        )
        heldout = f"""

[benchmark]
baseline_failure_marker = "ORACLE_REACHED"

[[checks]]
name = "Hidden pass"
feedback = "heldout"
command = ["{{python}}", "-c", "{heldout_command}"]
timeout_seconds = 30
""" if benchmark else ""
        path = root / "task.toml"
        path.write_text(
            f'''version = 2

task_id = "WORKFLOW_INTEGRATION_{'BENCHMARK' if benchmark else 'PRODUCTION'}"
project = "demo"
workspace_mode = "git_worktree"
result_mode = "keep_worktree"
risk = "{risk}"
max_fix_cycles = {1 if with_check_repair else 0}
max_replan_cycles = {1 if with_replan else 0}
turn_timeout_seconds = 60
require_clean_git = true
allowed_paths = {json.dumps(owner_allowed_paths or [])}

prompt = """
Change target.txt from before to after.
"""

[[checks]]
name = "Candidate content"
feedback = "repair"
command = ["{{python}}", "-c", "from pathlib import Path; assert Path('target.txt').read_text().strip() == 'after'"]
timeout_seconds = 30
{heldout}''',
            encoding="utf-8",
            newline="\n",
        )
        return path

    def run_case(
        self,
        *,
        benchmark: bool,
        risk: str = "low",
        with_discovery: bool = False,
        with_runtime_discovery: bool = False,
        with_replan: bool = False,
        benchmark_fail: bool = False,
        projected_jest: bool = False,
        planner_exception: PlannerCapabilityInfeasible | None = None,
        owner_allowed_paths: list[str] | None = None,
        promote_not_affected: bool = False,
        discovery_risk: bool = False,
        implementer_replan: bool = False,
        check_repair: bool = False,
        reuse_old_impact: bool = False,
        related: bool = False,
        blind_related: bool = False,
        terminal_failure: str | None = None,
        jest_refresh: bool = False,
        jest_refresh_failure: str | None = None,
        runtime_rebuild: str | None = None,
    ) -> tuple[int, Path, str]:
        root = Path(tempfile.mkdtemp(prefix="slivin-main-workflow-"))
        repo = self.make_repo(root)
        if projected_jest:
            jest = repo / "node_modules" / "jest" / "bin" / "jest.js"
            jest.parent.mkdir(parents=True)
            jest.write_text("fake jest\n", encoding="utf-8")
        manifest = self.write_manifest(
            root,
            repo,
            benchmark=benchmark,
            risk=risk,
            with_replan=with_replan or implementer_replan,
            benchmark_fail=benchmark_fail,
            owner_allowed_paths=owner_allowed_paths,
            with_check_repair=check_repair,
        )
        if jest_refresh:
            # Deterministic subprocess fixture, not an installed Node/Jest smoke.
            jest = root / "probe_jest.py"
            jest.write_text(
                "import sys\nfrom pathlib import Path\n"
                "failure = Path(__file__).with_suffix('.failure')\n"
                "mode = failure.read_text() if failure.exists() else ''\n"
                "if '--version' in sys.argv:\n    print('29.7.0')\n"
                "elif '--showConfig' in sys.argv:\n"
                "    if mode == 'config':\n        raise SystemExit(8)\n"
                "    if mode == 'candidate':\n        Path('target.txt').write_text('probe mutation')\n"
                "    if mode == 'git':\n"
                "        import subprocess\n"
                "        path = subprocess.check_output(['git', 'rev-parse', '--git-path', 'info/exclude'], text=True).strip()\n"
                "        with Path(path).open('a') as handle:\n            handle.write('probe-mutation\\n')\n"
                "    config = Path(sys.argv[sys.argv.index('--config') + 1])\n"
                "    assert config.read_text() == 'module.exports = {};\\n'\n"
                "    print('{}')\n"
                "else:\n    assert Path('target.txt').read_text().strip() == 'after'\n",
                encoding="utf-8",
            )
            (repo / "jest.config.cjs").write_text("module.exports = {};\n", encoding="utf-8")
            git(repo, "add", "jest.config.cjs")
            git(repo, "commit", "-m", "Synthetic owner config")
            with manifest.open("a", encoding="utf-8") as handle:
                handle.write('\n[[checks]]\nname = "Owner Jest regression"\nfeedback = "repair"\n'
                             'command = ["{node}", "{jest}", "--config", "{workspace}/jest.config.cjs"]\n'
                             'timeout_seconds = 30\n')
        if runtime_rebuild == "unused":
            manifest.write_text(manifest.read_text(encoding="utf-8").replace("{python}", "{harness_python}"), encoding="utf-8")
        run_root = root / "run"

        class _Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id: str) -> None:
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        def fake_task_contract(*_args, **_kwargs):
            return valid_task_contract()

        def fake_planner(*_args, **_kwargs):
            if (run_root / "replan_01_reset.json").exists():
                self.assertEqual(_args[0].retire_count, 1, "Retire old scoped sessions before preparing a fresh Planner")
            if jest_refresh:
                self.assertIn("JEST", _kwargs["available_verification_capabilities"],
                              "Each Planner needs refreshed Jest version/config evidence after reset")
                if jest_refresh_failure and (run_root / "replan_01_reset.json").exists():
                    self.fail("A failing mandatory refresh must stop before the fresh Planner")
                if runtime_rebuild:
                    self.assertEqual(
                        "PROJECT_PYTHON" in _kwargs["available_verification_capabilities"],
                        runtime_rebuild == "required",
                    )
            if planner_exception is not None:
                raise planner_exception
            plan = valid_plan()
            if related:
                plan["impact_closure"]["related_out_of_scope"] = [related_finding()]
            if promote_not_affected:
                plan["impact_closure"]["not_affected_consumers"] = [{
                    "name": "Integration sibling", "paths": ["reader_b.py"], "symbols": ["read_summary"],
                    "why_considered": "The sibling calls the shared reader.", "reason": "Initially assumed to use only a static title.",
                    "evidence": ["reader_b.py read_summary needs post-patch reachability review."],
                }]
            return plan

        evaluator_calls = 0

        def fake_evaluator(*_args, **kwargs):
            nonlocal evaluator_calls
            evaluator_calls += 1
            audit = valid_blind_audit(candidate_id=kwargs["candidate_id"], changed_paths=kwargs["changed_paths"])
            if blind_related:
                row = related_finding()
                row.update(impact_id="RELATED-1", name="Independent writer issue", relation="The writer emits an independent diagnostic value.")
                audit["impact_analysis"]["related_out_of_scope"] = [row]
            blind_callback = kwargs.get("on_blind_audit")
            if blind_callback:
                blind_callback(audit)
            callback = kwargs.get("on_phase_complete")
            if callback:
                callback("PHASE_A")
                callback("PHASE_B")
            verdict = valid_pass(blind_audit=audit, planner_impact=kwargs["plan"]["impact_closure"], implementation_impact=kwargs["implementation_impact_closure"])
            if with_replan and evaluator_calls == 1:
                verdict.update(status="REPLAN_REQUIRED", summary="The technical model must be rebuilt from baseline.", reason="The first technical model is intentionally rejected by the integration fixture.")
            return audit, verdict

        implementer_calls = 0
        implementer_threads: list[str] = []
        previous_impact = None

        def fake_implementer_report(*_args, **kwargs):
            nonlocal implementer_calls, previous_impact
            implementer_calls += 1
            workspace = Path(kwargs["workspace"])
            implementer_threads.append(str(kwargs["thread_id"]))
            if (with_replan or implementer_replan) and implementer_calls == 2:
                self.assertEqual(
                    (workspace / "target.txt").read_text(encoding="utf-8"),
                    "before\n",
                    "Fresh semantic replan must hide the rejected candidate diff",
                )
                self.assertNotEqual(
                    implementer_threads[0],
                    implementer_threads[1],
                    "Semantic replan must start a fresh Implementer thread",
                )
            elif implementer_calls > 1:
                self.assertEqual(implementer_threads[-1], implementer_threads[0])
                self.assertFalse(Path(kwargs["stamp_path"]).exists(), "Continuation must invalidate the previous self-verify stamp")
            (workspace / "target.txt").write_text("after\n", encoding="utf-8")
            if check_repair and implementer_calls > 1:
                reader = workspace / "reader.py"
                reader.write_text(reader.read_text(encoding="utf-8") + "\n# Reader reviewed during repair.\n", encoding="utf-8")
            command = list(kwargs["self_verify_command"])
            subprocess.run(command, cwd=workspace, check=True)
            self.assertTrue(
                task_runner.verify_self_verification_stamp(
                    workspace=workspace,
                    stamp_path=Path(kwargs["stamp_path"]),
                    control_plane=kwargs.get("control_plane"),
                    run_state=kwargs.get("run_state"),
                    check_registry_digest=kwargs.get("check_registry_digest"),
                )
            )
            contract = kwargs["implementation_contract"]
            report = {
                "protocol_version": IMPLEMENTER_PROTOCOL_VERSION,
                "status": "COMPLETE",
                "summary": "candidate ready",
                "contract_evidence": [
                    {
                        "item_id": item["id"],
                        "status": "VERIFIED",
                        "evidence": ["integration test fixture"],
                    }
                    for item in contract["items"]
                ],
                "self_verification": {
                    "status": "PASS",
                    "command": " ".join(command),
                    "evidence": ["SELF_VERIFY_PASS"],
                },
                "additional_check_paths": [],
                "blockers": [],
            }
            if with_discovery or with_runtime_discovery or promote_not_affected or discovery_risk:
                report["registered_checks"] = (
                    [{"kind": "check_id", "value": "git.diff-check"}]
                    if with_discovery
                    else []
                )
                report["discovered_obligations"] = [
                    {
                        "kind": "risk" if discovery_risk else "consumer",
                        "name": "Integration sibling",
                        "reason": "Uses the same target state.",
                        "required_behavior": "Existing behavior remains unchanged.",
                        "required_proof": {
                            "claim": "The sibling behavior remains unchanged.",
                            "level": (
                                "LIVE_LOCAL"
                                if with_runtime_discovery
                                else "LOCAL_DETERMINISTIC"
                            ),
                            "capabilities": (
                                ["BROWSER_DOM"] if with_runtime_discovery else []
                            ),
                        },
                        "evidence": ["integration fixture reachability"],
                    }
                ]
            else:
                report["registered_checks"] = []
                report["discovered_obligations"] = []
            attach_post_patch_impact(report, plan=kwargs["plan"], changed_paths=task_runner.collect_changed_paths(workspace))
            if related and kwargs["plan"] is None:
                report["post_patch_impact"]["related_out_of_scope"] = [related_finding()]
            for row in report["post_patch_impact"]["in_scope_consumers"] + report["post_patch_impact"]["new_risks"]:
                if row["name"] == "Integration sibling":
                    row.update(paths=["reader_b.py"], symbols=["read_summary"])
            if promote_not_affected:
                report["post_patch_impact"]["not_affected_consumers"] = []
            if implementer_replan and implementer_calls == 1:
                report.update(status="REPLAN_REQUIRED", terminal_reason_kind="TECHNICAL_MODEL_DIVERGENCE", reason="The actual patch requires a fresh technical model.", evidence=["reader.py assumptions must be rechecked from baseline."])
            if jest_refresh_failure and implementer_calls == 1:
                if jest_refresh_failure == "missing":
                    jest.unlink()
                else:
                    jest.with_suffix(".failure").write_text(jest_refresh_failure, encoding="utf-8")
            if reuse_old_impact and previous_impact is not None:
                report["post_patch_impact"] = previous_impact
            previous_impact = copy.deepcopy(report["post_patch_impact"])
            return report

        original_run_checks = task_runner.run_checks

        def controller_checks(*args, **kwargs):
            results = original_run_checks(*args, **kwargs)
            if check_repair and kwargs.get("label") == "CHECKS #1":
                results[0].returncode = 1
                results[0].output = "A synthetic Controller assertion requires the reader to be reviewed."
            if terminal_failure == "heldout" and kwargs.get("label") == "HELD-OUT":
                results[0].returncode = 2
                results[0].output = "Synthetic held-out infrastructure unavailable."
            return results

        original_reconstruction = task_runner.run_authoritative_reconstructed_verification
        original_delivery = task_runner.deliver_candidate_transaction

        def reconstructed_verification(**kwargs):
            if terminal_failure == "reconstruction":
                return ReconstructedVerificationResult(
                    public={"status": "FAIL", "reason_code": "SYNTHETIC_RECONSTRUCTION_FAILURE"}, private={},
                )
            return original_reconstruction(**kwargs)

        def delivery(**kwargs):
            result = original_delivery(**kwargs)
            if terminal_failure in {"RESULT_DELIVERY_BLOCKED", "RESULT_DELIVERY_FAIL"}:
                return dataclasses.replace(result, status=terminal_failure, reason_code="SYNTHETIC_DELIVERY_FAILURE")
            return result

        output = io.StringIO()
        local_config = {
            "projects": {
                "demo": {
                    "repo": str(repo),
                    "base_ref": "HEAD",
                    "result_mode": "keep_worktree",
                    "toolchain": (
                        {"jest": "{project_root}/node_modules/jest/bin/jest.js"}
                        if projected_jest
                        else {}
                    ),
                    "workspace": (
                        {"copy_untracked": ["node_modules"]}
                        if projected_jest
                        else {}
                    ),
                }
            },
            "workspace": {"root": str(root / "workspaces")},
        }
        if jest_refresh:
            local_config["projects"]["demo"]["toolchain"] = {
                "node": sys.executable, "jest": str(jest),
            }
        if runtime_rebuild:
            local_config["projects"]["demo"]["runtime"] = {
                "bootstrap_python": sys.executable,
                "expected_python": f"{sys.version_info.major}.{sys.version_info.minor}",
                "venv": ".venv", "dependency_files": [],
            }
        with (
            mock.patch.object(task_runner, "RunRecorder", _Recorder),
            mock.patch.object(task_runner, "load_local_config", return_value=(local_config, None)),
            mock.patch.object(task_runner, "CodexAppServer", _FakeCodexAppServer),
            mock.patch.object(task_runner, "resolve_codex_cmd", return_value=Path(sys.executable)),
            mock.patch.object(task_runner, "run_task_contract_normalizer", side_effect=fake_task_contract),
            mock.patch.object(task_runner, "run_planner", side_effect=fake_planner),
            mock.patch.object(task_runner, "validate_planner_artifact", wraps=task_runner.validate_planner_artifact) as planner_validation,
            mock.patch.object(task_runner, "run_evaluator", side_effect=fake_evaluator),
            mock.patch.object(task_runner, "run_implementer_report", side_effect=fake_implementer_report),
            mock.patch.object(task_runner, "run_checks", side_effect=controller_checks),
            mock.patch.object(task_runner, "run_authoritative_reconstructed_verification", side_effect=reconstructed_verification),
            mock.patch.object(task_runner, "deliver_candidate_transaction", side_effect=delivery),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            result = task_runner.main([str(manifest)])
        if owner_allowed_paths is not None:
            self.assertEqual(planner_validation.call_count, 2 if with_replan else 1, output.getvalue())
            for call in planner_validation.call_args_list:
                self.assertEqual(call.kwargs["owner_allowed_paths"], owner_allowed_paths)
        return result, run_root, output.getvalue()

    def test_infeasible_corrected_plan_stops_before_contract_and_implementer(self) -> None:
        plan = valid_plan()
        plan["evidence_plan"]["regression"][0]["capabilities"] = [
            "PROJECT_PYTHON"
        ]
        result, run_root, output = self.run_case(
            benchmark=False,
            risk="medium",
            planner_exception=PlannerCapabilityInfeasible(
                ["PROJECT_PYTHON"], plan=plan
            ),
        )
        self.assertEqual(result, 2, output)
        self.assertIn("PLANNER_CAPABILITY_INFEASIBLE PROJECT_PYTHON", output)
        self.assertTrue((run_root / "plan_01.json").is_file())
        self.assertFalse((run_root / "implementation_contract_01.json").exists())
        self.assertFalse((run_root / "verification_plan_01.json").exists())
        self.assertFalse((run_root / "implementation_report_01.json").exists())

    def test_unsupported_role_policy_is_a_controlled_failure_before_implementation(self) -> None:
        from slivin_harness.execution import ScopedExecutionPolicyError

        result, run_root, output = self.run_case(
            benchmark=False, risk="medium",
            planner_exception=ScopedExecutionPolicyError(
                "ROLE_EXECUTION_POLICY_UNAVAILABLE", "Installed runtime rejected the scoped profile"
            ),
        )
        self.assertEqual(result, 2, output)
        self.assertIn("ROLE_EXECUTION_POLICY_UNAVAILABLE", output)
        self.assertNotIn("Traceback", output)
        diagnostic = json.loads((run_root / "role_execution_policy_failure.json").read_text(encoding="utf-8"))
        self.assertEqual(diagnostic["reason_code"], "ROLE_EXECUTION_POLICY_UNAVAILABLE")
        self.assertEqual(diagnostic["status"], "FAIL")
        self.assertFalse((run_root / "implementation_report_01.json").exists())

    def test_production_run_records_all_steps_and_terminal_pass(self) -> None:
        result, run_root, output = self.run_case(benchmark=False)
        self.assertEqual(result, 0, output)
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["terminal"]["result_code"],
            StageResultCode.HARNESS_TASK_PASS.value,
        )
        self.assertEqual(
            state["stages"]["runtime_verification"]["state"],
            StageState.SKIPPED.value,
        )
        self.assertEqual(
            state["stages"]["evaluator"]["state"],
            StageState.SKIPPED.value,
        )
        self.assertEqual(
            state["stages"]["final_gate"]["state"],
            StageState.PASSED.value,
        )
        self.assertIn("HARNESS_TASK_PASS", output)
        self.assert_handoff(run_root, output, count=0)
        self.assertTrue((run_root / "candidate.patch").is_file())
        self.assertTrue((run_root / "patch_proof.json").is_file())
        self.assertTrue((run_root / "reconstructed_verification.json").is_file())
        self.assertTrue((run_root / "quality_gate_reconciliation.json").is_file())
        self.assertTrue((run_root / "final_acceptance.json").is_file())
        self.assertTrue((run_root / "delivery_record.json").is_file())
        acceptance = json.loads((run_root / "final_acceptance.json").read_text(encoding="utf-8"))
        self.assertEqual(acceptance["schema_version"], "final-acceptance.v3")
        self.assertEqual(acceptance["patch_proof"]["status"], "PATCH_RECONSTRUCTION_PASS")
        self.assertEqual(acceptance["reconstructed_verification"]["status"], "PASS")
        self.assertEqual(
            acceptance["reconstructed_verification"]["repair_checks_status"], "PASS"
        )
        self.assertTrue((run_root / "controller_private" / "run_state.json").is_file())
        self.assertTrue(
            (run_root / "controller_private" / "self_verify_receipt_current.json").is_file()
        )
        self.assertTrue((run_root / "execution_policies.json").is_file())
        build_identity = json.loads(
            (run_root / "harness_build_identity.json").read_text(encoding="utf-8")
        )
        self.assertEqual(build_identity["schema_version"], "harness-build-identity.v1")
        self.assertEqual(build_identity["version"], "0.8.0a29")
        if build_identity["source_kind"] == "GIT_CHECKOUT":
            self.assertRegex(build_identity["git_commit"], r"^[0-9a-f]{40}$")
            self.assertIsInstance(build_identity["git_dirty"], bool)
        else:
            self.assertEqual(build_identity["source_kind"], "ARCHIVE_OR_UNKNOWN")
            self.assertIsNone(build_identity["git_commit"])
            self.assertIsNone(build_identity["git_dirty"])
        self.assertNotIn(str(task_runner.HARNESS_ROOT), json.dumps(build_identity))
        self.assertIn("HARNESS_GIT_COMMIT:", output)
        private_state = json.loads(
            (run_root / "controller_private" / "run_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(private_state, state)

    def test_full_production_run_executes_planner_runtime_skip_and_evaluator(self) -> None:
        result, run_root, output = self.run_case(benchmark=False, risk="medium")
        self.assertEqual(result, 0, output)
        self.assert_handoff(run_root, output, count=0)
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["stages"]["planner"]["result_code"],
            StageResultCode.PLANNER_READY.value,
        )
        self.assertEqual(
            state["stages"]["runtime_verification"]["result_code"],
            StageResultCode.RUNTIME_VERIFICATION_SKIPPED.value,
        )
        self.assertEqual(
            state["stages"]["evaluator"]["result_code"],
            StageResultCode.EVALUATION_PASS.value,
        )
        self.assertEqual(
            state["stages"]["final_gate"]["result_code"],
            StageResultCode.HARNESS_TASK_PASS.value,
        )
        self.assertEqual(state["revisions"]["task_contract"], 1)
        self.assertEqual(state["revisions"]["plan"], 1)
        self.assertEqual(state["revisions"]["implementation_contract"], 1)
        self.assertEqual(state["revisions"]["verification_plan"], 1)
        self.assertTrue((run_root / "task_contract_01.json").is_file())
        self.assertTrue((run_root / "verification_plan_01.json").is_file())
        self.assertIn("0 PREFLIGHT → 1 PLANNER → 2 CONTRACT", output)

    def test_benchmark_run_has_distinct_terminal_status_and_heldout_artifact(self) -> None:
        result, run_root, output = self.run_case(benchmark=True)
        self.assertEqual(result, 0, output)
        self.assert_handoff(run_root, output, count=0)
        self.assertLess(
            output.index("STATIC_TOOLCHAIN_PREFLIGHT_PASS"),
            output.index("=== USER TASK CONTRACT ==="),
        )
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["terminal"]["result_code"],
            StageResultCode.HARNESS_BENCHMARK_PASS.value,
        )
        self.assertIn("HELDOUT_PASS", output)
        self.assertIn("HARNESS_BENCHMARK_PASS", output)
        self.assertTrue((run_root / "heldout_results.json").is_file())
        self.assertTrue((run_root / "heldout_evidence.json").is_file())
        self.assertTrue((run_root / "benchmark_isolation.json").is_file())
        static_preflight = json.loads(
            (run_root / "static_toolchain_preflight.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            static_preflight["schema_version"], "static-toolchain-preflight.v1"
        )
        self.assertEqual(static_preflight["status"], "PASS")
        self.assertFalse(static_preflight["tests_executed"])
        self.assertFalse(static_preflight["hidden_commands_executed"])
        heldout = json.loads((run_root / "heldout_evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(heldout["status"], "HELDOUT_PASS")
        reconstructed = json.loads(
            (run_root / "reconstructed_verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(reconstructed["status"], "PASS")
        self.assertEqual(reconstructed["static_preflight_status"], "PASS")
        self.assertEqual(reconstructed["repair_checks_status"], "PASS")
        self.assertEqual(reconstructed["heldout_status"], "HELDOUT_PASS")
        isolation = json.loads((run_root / "benchmark_isolation.json").read_text(encoding="utf-8"))
        self.assertFalse(isolation["shared_git_metadata"])

    def test_historical_workflow_records_projected_jest_rebind_without_source_path(self) -> None:
        result, run_root, output = self.run_case(benchmark=True, projected_jest=True)
        self.assertEqual(result, 0, output)
        sanitization = json.loads(
            (run_root / "benchmark_toolchain_sanitization.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sanitization["schema_version"], "benchmark-toolchain-sanitization.v2")
        self.assertEqual(
            sanitization["rebound_to_workspace"],
            {"jest": "node_modules/jest/bin/jest.js"},
        )
        self.assertEqual(sanitization["retained_keys"], [])
        self.assertEqual(sanitization["removed"], {})
        self.assertNotIn(str(run_root.parent / "repo"), json.dumps(sanitization))
        self.assertFalse(sanitization["fresh_dependency_install_performed"])
        integrity = json.loads(
            (run_root / "runtime_projection_integrity.json").read_text(encoding="utf-8")
        )
        self.assertEqual(integrity["schema_version"], "runtime-projection-integrity.v1")
        self.assertEqual(integrity["projection_roots"], ["node_modules"])
        public_integrity = json.dumps(integrity, sort_keys=True)
        self.assertNotIn(str(run_root.parent / "repo"), public_integrity)
        self.assertNotIn("keyed_hmac_sha256", public_integrity)
        private_baseline = json.loads(
            (run_root / "controller_private" / "runtime_projection_baseline.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(private_baseline["projections"][0]["relative_path"], "node_modules")
        self.assertIn("keyed_hmac_sha256", private_baseline["projections"][0])

    def test_production_workflow_does_not_emit_historical_toolchain_sanitization(self) -> None:
        result, run_root, output = self.run_case(benchmark=False, projected_jest=True)
        self.assertEqual(result, 0, output)
        self.assertFalse((run_root / "benchmark_toolchain_sanitization.json").exists())

    def test_benchmark_semantic_fail_stops_without_final_acceptance(self) -> None:
        result, run_root, output = self.run_case(
            benchmark=True,
            benchmark_fail=True,
            related=True,
        )
        self.assertEqual(result, 1, output)
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["terminal"]["result_code"],
            StageResultCode.HARNESS_BENCHMARK_SEMANTIC_FAIL.value,
        )
        evidence = json.loads((run_root / "heldout_evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "HELDOUT_SEMANTIC_FAIL")
        self.assertFalse(evidence["feedback_exposed_to_agents"])
        self.assertFalse((run_root / "final_acceptance.json").exists())
        self.assertIn("HARNESS_BENCHMARK_SEMANTIC_FAIL", output)
        self.assertNotIn("\nHARNESS_BENCHMARK_FAIL\n", output)
        report = self.assert_handoff(run_root, output, count=1)
        self.assertNotIn("ORACLE_REACHED", json.dumps(report))
        self.assertNotIn("heldout", json.dumps(report))
        self.assertLess(output.index("USER_FOLLOW_UP_COUNT"), output.index("HELDOUT_STATUS:"))

    def assert_handoff(self, run_root, output, *, count):
        public = run_root / USER_FOLLOW_UP_ARTIFACT
        private = run_root / "controller_private" / USER_FOLLOW_UP_ARTIFACT
        self.assertEqual(public.read_bytes(), private.read_bytes())
        report = json.loads(public.read_text(encoding="utf-8"))
        self.assertEqual(report["count"], count)
        self.assertIn(f"USER_FOLLOW_UP_REPORT: {public.resolve()}", output)
        self.assertIn(f"USER_FOLLOW_UP_COUNT: {count}", output)
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertIn(USER_FOLLOW_UP_ARTIFACT, state["stages"]["final_gate"]["artifacts"])
        return report

    def test_full_follow_up_is_delivered_without_entering_project_patch(self):
        result, run_root, output = self.run_case(benchmark=False, risk="medium", related=True, blind_related=True)
        self.assertEqual(result, 0, output)
        report = self.assert_handoff(run_root, output, count=2)
        acceptance = json.loads((run_root / "final_acceptance.json").read_text(encoding="utf-8"))
        self.assertEqual(acceptance["user_follow_up"]["fingerprint"], report["fingerprint"])
        bindings = {row["artifact"]: row for row in acceptance["artifact_bindings"]}
        self.assertEqual(acceptance["user_follow_up"]["sha256"], bindings[USER_FOLLOW_UP_ARTIFACT]["sha256"])
        for row in report["follow_ups"]:
            self.assertEqual(row["review_status"], "CONFIRMED_OUT_OF_SCOPE")
            self.assertIn(f"USER_FOLLOW_UP: {row['follow_up_id']} | {row['title']} | NEXT: {row['suggested_next_task']}", output)
        sources = [row["source"] for row in report["follow_ups"][0]["provenance"] + report["follow_ups"][1]["provenance"]]
        self.assertCountEqual(sources, ["PLANNER", "IMPLEMENTER", "BLIND_EVALUATOR"])
        self.assertNotIn(USER_FOLLOW_UP_ARTIFACT, (run_root / "candidate.patch").read_text(encoding="utf-8"))
        self.assertNotIn(USER_FOLLOW_UP_ARTIFACT, acceptance["changed_paths"])
        workspaces = list((run_root.parent / "workspaces").rglob("target.txt"))
        self.assertTrue(workspaces)
        for target in workspaces:
            self.assertFalse((target.parent / USER_FOLLOW_UP_ARTIFACT).exists())

    def test_fast_follow_up_is_delivered_as_implementer_declaration(self):
        result, run_root, output = self.run_case(benchmark=False, related=True)
        self.assertEqual(result, 0, output)
        report = self.assert_handoff(run_root, output, count=1)
        self.assertEqual(report["follow_ups"][0]["review_status"], "DECLARED_OUT_OF_SCOPE_FAST")
        self.assertFalse((run_root / "evaluation_01.json").exists())

    def test_fast_semantic_replan_preserves_implementer_only_handoff(self):
        result, run_root, output = self.run_case(benchmark=False, related=True, implementer_replan=True)
        self.assertEqual(result, 0, output)
        report = self.assert_handoff(run_root, output, count=1)
        self.assertIsNotNone(report["source_bindings"]["plan_fingerprint"])
        self.assertIsNone(report["source_bindings"]["evaluation_fingerprint"])
        self.assertEqual(report["follow_ups"][0]["review_status"], "DECLARED_OUT_OF_SCOPE_FAST")
        self.assertEqual([row["source"] for row in report["follow_ups"][0]["provenance"]], ["IMPLEMENTER"])
        self.assertTrue((run_root / "replan_01_reset.json").exists())
        self.assertFalse((run_root / "evaluation_01.json").exists())

    def test_follow_up_survives_later_final_gate_failures(self):
        for failure in ("heldout", "reconstruction", "RESULT_DELIVERY_BLOCKED", "RESULT_DELIVERY_FAIL"):
            with self.subTest(failure=failure):
                result, run_root, output = self.run_case(benchmark=failure == "heldout", related=True, terminal_failure=failure)
                self.assertEqual(result, 2, output)
                self.assert_handoff(run_root, output, count=1)
                self.assertEqual((run_root / "final_acceptance.json").exists(), failure.startswith("RESULT_DELIVERY"))

    def test_discovery_recompiles_active_contract_and_verification_plan(self) -> None:
        result, run_root, output = self.run_case(
            benchmark=False,
            risk="medium",
            with_discovery=True,
        )
        self.assertEqual(result, 0, output)
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["revisions"]["implementation_contract"], 2)
        self.assertEqual(state["revisions"]["verification_plan"], 2)
        contract = json.loads(
            (run_root / "controller_private" / "implementation_contract_02.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(
            "CONSUMER-DISCOVERED-1",
            [item["id"] for item in contract["items"]],
        )
        plan = json.loads(
            (run_root / "controller_private" / "verification_plan_02.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plan["task_checks"], ["check_id:git.diff-check"])
        self.assertTrue(
            (run_root / "controller_private" / "capability_gate_02.json").is_file()
        )
        self.assertIn("ACTIVE_DEFINITION_EXPANDED", output)

    def test_new_runtime_requirement_rechecks_capability_gate_before_continuation(self) -> None:
        result, run_root, output = self.run_case(
            benchmark=False,
            risk="medium",
            with_runtime_discovery=True,
        )
        self.assertEqual(result, 2, output)
        gate = json.loads(
            (run_root / "controller_private" / "capability_gate_02.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("LIVE_LOCAL_RUNTIME", gate["missing"])
        self.assertIn("BROWSER_DOM", gate["missing"])
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["stages"]["implementation_contract"]["reason_code"],
            "REQUIRED_CAPABILITY_MISSING",
        )
        self.assertIn("HARNESS_TASK_STOPPED: REQUIRED_CAPABILITY_MISSING", output)

    def test_semantic_replan_resets_candidate_and_uses_fresh_implementer(self) -> None:
        result, run_root, output = self.run_case(
            benchmark=False,
            risk="medium",
            with_replan=True,
            owner_allowed_paths=["target.txt", "reader.py"],
        )
        self.assertEqual(result, 0, output)
        self.assertTrue((run_root / "replan_01_rejected_candidate.patch").is_file())
        reset = json.loads((run_root / "replan_01_reset.json").read_text(encoding="utf-8"))
        self.assertEqual(reset["status"], "SEMANTIC_REPLAN_RESET_PASS")
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["attempt_id"], 2)
        self.assertEqual(state["terminal"]["result_code"], StageResultCode.HARNESS_TASK_PASS.value)
        self.assertIn("=== REPLAN #1 ===", output)

    def test_post_patch_promoted_consumer_expands_in_same_thread(self) -> None:
        result, root, output = self.run_case(benchmark=False, risk="medium", promote_not_affected=True)
        self.assertEqual(result, 0, output)
        self.assertFalse((root / "implementation_impact_closure_01.json").exists())
        artifact = json.loads((root / "implementation_impact_closure_02.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["schema_version"], "implementation-impact-closure.v1")
        self.assertEqual(artifact["post_patch_impact"]["not_affected_consumers"], [])
        final = json.loads((root / "implementation_report_02.json").read_text(encoding="utf-8"))
        self.assertTrue(any(row["item_id"] == "CONSUMER-DISCOVERED-1" and row["status"] == "VERIFIED" for row in final["contract_evidence"]))
        self.assertEqual(len(list(root.glob("contract_expansion_*.json"))), 1)

    def test_post_patch_new_risk_expands_before_final_complete(self) -> None:
        result, root, output = self.run_case(benchmark=False, risk="medium", discovery_risk=True)
        self.assertEqual(result, 0, output)
        final = json.loads((root / "implementation_report_02.json").read_text(encoding="utf-8"))
        self.assertTrue(any(row["item_id"] == "RISK-DISCOVERED-1" for row in final["contract_evidence"]))
        self.assertTrue((root / "implementation_impact_closure_02.json").is_file())

    def test_fast_discovered_consumer_expands_before_final_complete(self) -> None:
        result, root, output = self.run_case(benchmark=False, risk="low")
        self.assertEqual(result, 0, output)
        artifact = json.loads((root / "implementation_impact_closure_02.json").read_text(encoding="utf-8"))
        self.assertIsNone(artifact["plan_fingerprint"])
        self.assertEqual(artifact["post_patch_impact"]["in_scope_consumers"][0]["source"], "DISCOVERED")
        state = json.loads((root / "run_state.json").read_text(encoding="utf-8"))
        self.assertIn("implementation_impact_closure_02.json", json.dumps(state["stages"]["implementer"]))

    def test_implementer_model_divergence_uses_fresh_semantic_replan(self) -> None:
        result, root, output = self.run_case(benchmark=False, risk="medium", implementer_replan=True)
        self.assertEqual(result, 0, output)
        self.assertTrue((root / "replan_01_reset.json").exists())
        self.assertFalse((root / "implementation_impact_closure_01.json").exists())
        final = json.loads((root / "implementation_impact_closure_02.json").read_text(encoding="utf-8"))
        self.assertEqual(final["revision_binding"]["attempt_id"], 2)

    def test_controller_repair_requires_new_candidate_impact_artifact(self) -> None:
        result, root, output = self.run_case(benchmark=False, risk="medium", check_repair=True)
        self.assertEqual(result, 0, output)
        before = json.loads((root / "implementation_impact_closure_01.json").read_text(encoding="utf-8"))
        after = json.loads((root / "implementation_impact_closure_02.json").read_text(encoding="utf-8"))
        self.assertNotEqual(before["candidate_id"], after["candidate_id"])
        self.assertEqual(after["changed_paths"], ["reader.py", "target.txt"])

    def test_controller_rejects_repair_with_reused_pre_repair_impact(self) -> None:
        result, root, output = self.run_case(benchmark=False, risk="medium", check_repair=True, reuse_old_impact=True)
        self.assertEqual(result, 1, output)
        self.assertIn("Every Controller changed path", output)
        self.assertFalse((root / "implementation_impact_closure_02.json").exists())

    def test_project_profile_builds_and_uses_worktree_local_python_runtime(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-project-runtime-workflow-"))
        repo = self.make_repo(root)
        (repo / "requirements.txt").write_text("", encoding="utf-8")
        git(repo, "add", "requirements.txt")
        git(repo, "commit", "-m", "runtime declaration")
        manifest = root / "task.toml"
        manifest.write_text(
            '''version = 2

task_id = "PROJECT_RUNTIME_INTEGRATION"
project = "demo"
workspace_mode = "git_worktree"
base_ref = "HEAD"
result_mode = "keep_worktree"
risk = "low"
max_fix_cycles = 0
max_replan_cycles = 0
turn_timeout_seconds = 60
require_clean_git = true

prompt = """
Change target.txt from before to after.
"""

[[checks]]
name = "Candidate content"
feedback = "repair"
command = ["{python}", "-c", "import sys; from pathlib import Path; p = Path('.harness_tmp/project_python_runs.txt'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text((p.read_text(encoding='utf-8') if p.exists() else '') + sys.executable + chr(10), encoding='utf-8'); assert Path('target.txt').read_text().strip() == 'after'"]
timeout_seconds = 30
''',
            encoding="utf-8",
            newline="\n",
        )
        run_root = root / "run"
        workspace_root = root / "workspaces"
        local_config = {
            "workspace": {"root": str(workspace_root)},
            "projects": {
                "demo": {
                    "repo": str(repo),
                    "base_ref": "HEAD",
                    "result_mode": "keep_worktree",
                    "require_clean_source": True,
                    "runtime": {
                        "bootstrap_python": sys.executable,
                        "expected_python": (
                            f"{sys.version_info.major}.{sys.version_info.minor}"
                        ),
                        "venv": ".venv",
                        "dependency_files": ["requirements.txt"],
                        "pip_install_args": ["--disable-pip-version-check"],
                    },
                }
            },
        }

        class _Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id: str) -> None:
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        def fake_implementer_report(*_args, **kwargs):
            workspace = Path(kwargs["workspace"])
            (workspace / "target.txt").write_text("after\n", encoding="utf-8")
            command = list(kwargs["self_verify_command"])
            subprocess.run(command, cwd=workspace, check=True)
            self.assertTrue(task_runner.verify_self_verification_stamp(
                workspace=workspace, stamp_path=Path(kwargs["stamp_path"]),
                control_plane=kwargs.get("control_plane"), run_state=kwargs.get("run_state"),
                check_registry_digest=kwargs.get("check_registry_digest"),
            ))
            contract = kwargs["implementation_contract"]
            return attach_post_patch_impact({
                "protocol_version": IMPLEMENTER_PROTOCOL_VERSION,
                "status": "COMPLETE",
                "summary": "candidate ready in project runtime",
                "contract_evidence": [
                    {
                        "item_id": item["id"],
                        "status": "VERIFIED",
                        "evidence": ["project runtime integration fixture"],
                    }
                    for item in contract["items"]
                ],
                "self_verification": {
                    "status": "PASS",
                    "command": " ".join(command),
                    "evidence": ["SELF_VERIFY_PASS"],
                },
                "additional_check_paths": [],
                "registered_checks": [],
                "discovered_obligations": [],
                "blockers": [],
            }, plan=kwargs["plan"], changed_paths=task_runner.collect_changed_paths(workspace))

        output = io.StringIO()
        with (
            mock.patch.object(task_runner, "RunRecorder", _Recorder),
            mock.patch.object(task_runner, "CodexAppServer", _FakeCodexAppServer),
            mock.patch.object(
                task_runner, "resolve_codex_cmd", return_value=Path(sys.executable)
            ),
            mock.patch.object(
                task_runner,
                "load_local_config",
                return_value=(local_config, root / "harness.local.toml"),
            ),
            mock.patch.object(
                task_runner, "run_task_contract_normalizer", return_value=valid_task_contract()
            ),
            mock.patch.object(
                task_runner, "run_implementer_report", side_effect=fake_implementer_report
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            result = task_runner.main([str(manifest)])

        self.assertEqual(result, 0, output.getvalue())
        runtime = json.loads(
            (run_root / "controller_private" / "project_runtime_01.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(runtime["mode"], "WORKTREE_VENV")
        project_python = Path(runtime["project_python"])
        self.assertTrue(project_python.is_file())
        self.assertIn(".venv", project_python.parts)
        self.assertNotEqual(project_python.resolve(), Path(sys.executable).resolve())
        workspace = project_python.parent.parent.parent
        marker = workspace / ".harness_tmp" / "project_python_runs.txt"
        executions = [Path(item).resolve() for item in marker.read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(executions), 2)
        self.assertTrue(all(item == project_python.resolve() for item in executions))
        patch = (run_root / "candidate.patch").read_text(encoding="utf-8")
        self.assertNotIn(".venv", patch)


if __name__ == "__main__":
    unittest.main()
