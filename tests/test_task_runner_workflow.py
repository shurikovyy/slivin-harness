from __future__ import annotations

import contextlib
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
from slivin_harness.protocol import EVALUATOR_PROTOCOL_VERSION
from slivin_harness.workflow import StageResultCode, StageState
from test_protocol import valid_blind_audit, valid_pass, valid_plan, valid_task_contract


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

    def __enter__(self) -> "_FakeCodexAppServer":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def start_thread(self, **_kwargs) -> str:
        self._thread_index += 1
        return f"thread-{self._thread_index}"


class TaskRunnerWorkflowIntegrationTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        (repo / "target.txt").write_text("before\n", encoding="utf-8")
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
max_fix_cycles = 0
max_replan_cycles = {1 if with_replan else 0}
turn_timeout_seconds = 60
require_clean_git = true

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
            with_replan=with_replan,
            benchmark_fail=benchmark_fail,
        )
        run_root = root / "run"

        class _Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id: str) -> None:
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        def fake_task_contract(*_args, **_kwargs):
            return valid_task_contract()

        def fake_planner(*_args, **_kwargs):
            return valid_plan()

        evaluator_calls = 0

        def fake_evaluator(*_args, **kwargs):
            nonlocal evaluator_calls
            evaluator_calls += 1
            audit = valid_blind_audit()
            blind_callback = kwargs.get("on_blind_audit")
            if blind_callback:
                blind_callback(audit)
            callback = kwargs.get("on_phase_complete")
            if callback:
                callback("PHASE_A")
                callback("PHASE_B")
            if with_replan and evaluator_calls == 1:
                return audit, {
                    "protocol_version": EVALUATOR_PROTOCOL_VERSION,
                    "status": "REPLAN_REQUIRED",
                    "summary": "The technical model must be rebuilt from baseline.",
                    "blind_finding_dispositions": [],
                    "findings": [],
                    "reason": "The first technical model is intentionally rejected by the integration fixture.",
                }
            return audit, valid_pass(blind_audit=audit)

        implementer_calls = 0
        implementer_threads: list[str] = []

        def fake_implementer_report(*_args, **kwargs):
            nonlocal implementer_calls
            implementer_calls += 1
            workspace = Path(kwargs["workspace"])
            implementer_threads.append(str(kwargs["thread_id"]))
            if with_replan and implementer_calls == 2:
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
            (workspace / "target.txt").write_text("after\n", encoding="utf-8")
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
            if (with_discovery or with_runtime_discovery) and implementer_calls == 1:
                report["registered_checks"] = (
                    [{"kind": "check_id", "value": "git.diff-check"}]
                    if with_discovery
                    else []
                )
                report["discovered_obligations"] = [
                    {
                        "kind": "consumer",
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
            return report

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
        with (
            mock.patch.object(task_runner, "RunRecorder", _Recorder),
            mock.patch.object(task_runner, "load_local_config", return_value=(local_config, None)),
            mock.patch.object(task_runner, "CodexAppServer", _FakeCodexAppServer),
            mock.patch.object(task_runner, "resolve_codex_cmd", return_value=Path(sys.executable)),
            mock.patch.object(task_runner, "run_task_contract_normalizer", side_effect=fake_task_contract),
            mock.patch.object(task_runner, "run_planner", side_effect=fake_planner),
            mock.patch.object(task_runner, "run_evaluator", side_effect=fake_evaluator),
            mock.patch.object(task_runner, "run_implementer_report", side_effect=fake_implementer_report),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            result = task_runner.main([str(manifest)])
        return result, run_root, output.getvalue()

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
        self.assertTrue((run_root / "candidate.patch").is_file())
        self.assertTrue((run_root / "patch_proof.json").is_file())
        self.assertTrue((run_root / "quality_gate_reconciliation.json").is_file())
        self.assertTrue((run_root / "final_acceptance.json").is_file())
        self.assertTrue((run_root / "delivery_record.json").is_file())
        acceptance = json.loads((run_root / "final_acceptance.json").read_text(encoding="utf-8"))
        self.assertEqual(acceptance["schema_version"], "final-acceptance.v2")
        self.assertEqual(acceptance["patch_proof"]["status"], "PATCH_RECONSTRUCTION_PASS")
        self.assertTrue((run_root / "controller_private" / "run_state.json").is_file())
        self.assertTrue(
            (run_root / "controller_private" / "self_verify_receipt_current.json").is_file()
        )
        self.assertTrue((run_root / "execution_policies.json").is_file())
        build_identity = json.loads(
            (run_root / "harness_build_identity.json").read_text(encoding="utf-8")
        )
        self.assertEqual(build_identity["schema_version"], "harness-build-identity.v1")
        self.assertEqual(build_identity["version"], "0.8.0a13")
        self.assertRegex(build_identity["git_commit"], r"^[0-9a-f]{40}$")
        self.assertNotIn(str(task_runner.HARNESS_ROOT), json.dumps(build_identity))
        self.assertIn("HARNESS_GIT_COMMIT:", output)
        private_state = json.loads(
            (run_root / "controller_private" / "run_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(private_state, state)

    def test_full_production_run_executes_planner_runtime_skip_and_evaluator(self) -> None:
        result, run_root, output = self.run_case(benchmark=False, risk="medium")
        self.assertEqual(result, 0, output)
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
        )
        self.assertEqual(result, 1, output)
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["terminal"]["result_code"],
            StageResultCode.HARNESS_BENCHMARK_FAIL.value,
        )
        evidence = json.loads((run_root / "heldout_evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "HELDOUT_SEMANTIC_FAIL")
        self.assertFalse(evidence["feedback_exposed_to_agents"])
        self.assertFalse((run_root / "final_acceptance.json").exists())
        self.assertIn("HARNESS_BENCHMARK_FAIL", output)

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
        )
        self.assertEqual(result, 0, output)
        self.assertTrue((run_root / "replan_01_rejected_candidate.patch").is_file())
        reset = json.loads((run_root / "replan_01_reset.json").read_text(encoding="utf-8"))
        self.assertEqual(reset["status"], "SEMANTIC_REPLAN_RESET_PASS")
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["attempt_id"], 2)
        self.assertEqual(state["terminal"]["result_code"], StageResultCode.HARNESS_TASK_PASS.value)
        self.assertIn("=== REPLAN #1 ===", output)

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
            contract = kwargs["implementation_contract"]
            return {
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
            }

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
