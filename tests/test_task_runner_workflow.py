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
from test_protocol import valid_plan, valid_task_contract


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
        pass

    def __enter__(self) -> "_FakeCodexAppServer":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def start_thread(self, **_kwargs) -> str:
        return "implementer-thread"


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
    ) -> Path:
        heldout = """

[[checks]]
name = "Hidden pass"
feedback = "heldout"
command = ["{python}", "-c", "print('ORACLE_REACHED')"]
timeout_seconds = 30
""" if benchmark else ""
        path = root / "task.toml"
        path.write_text(
            f'''version = 2

task_id = "WORKFLOW_INTEGRATION_{'BENCHMARK' if benchmark else 'PRODUCTION'}"
workspace = "{repo.as_posix()}"
risk = "{risk}"
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
    ) -> tuple[int, Path, str]:
        root = Path(tempfile.mkdtemp(prefix="slivin-main-workflow-"))
        repo = self.make_repo(root)
        manifest = self.write_manifest(root, repo, benchmark=benchmark, risk=risk)
        run_root = root / "run"

        class _Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id: str) -> None:
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        def fake_task_contract(*_args, **_kwargs):
            return valid_task_contract()

        def fake_planner(*_args, **_kwargs):
            return valid_plan()

        def fake_evaluator(*_args, **_kwargs):
            return {
                "protocol_version": EVALUATOR_PROTOCOL_VERSION,
                "status": "PASS",
                "summary": "candidate satisfies the task",
                "task_satisfied": True,
                "changed_files_reviewed": ["target.txt"],
                "checks_assessment": ["configured check passed"],
                "findings": [],
                "unverified": [],
                "replan_reason": "",
            }

        implementer_calls = 0

        def fake_implementer_report(*_args, **kwargs):
            nonlocal implementer_calls
            implementer_calls += 1
            workspace = Path(kwargs["workspace"])
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
        with (
            mock.patch.object(task_runner, "RunRecorder", _Recorder),
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
        self.assertTrue((run_root / "final_acceptance.json").is_file())
        self.assertTrue((run_root / "delivery_record.json").is_file())
        self.assertTrue((run_root / "controller_private" / "run_state.json").is_file())
        self.assertTrue(
            (run_root / "controller_private" / "self_verify_receipt_current.json").is_file()
        )
        self.assertTrue((run_root / "execution_policies.json").is_file())
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
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["terminal"]["result_code"],
            StageResultCode.HARNESS_BENCHMARK_PASS.value,
        )
        self.assertIn("HELDOUT_PASS", output)
        self.assertIn("HARNESS_BENCHMARK_PASS", output)
        self.assertTrue((run_root / "heldout_results.json").is_file())

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
command = ["{python}", "-c", "from pathlib import Path; assert Path('target.txt').read_text().strip() == 'after'"]
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
        patch = (run_root / "candidate.patch").read_text(encoding="utf-8")
        self.assertNotIn(".venv", patch)


if __name__ == "__main__":
    unittest.main()
