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
from slivin_harness.protocol import EVALUATOR_PROTOCOL_VERSION, PLANNER_PROTOCOL_VERSION
from slivin_harness.workflow import StageResultCode, StageState


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
    ) -> tuple[int, Path, str]:
        root = Path(tempfile.mkdtemp(prefix="slivin-main-workflow-"))
        repo = self.make_repo(root)
        manifest = self.write_manifest(root, repo, benchmark=benchmark, risk=risk)
        run_root = root / "run"

        class _Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id: str) -> None:
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        def fake_planner(*_args, **_kwargs):
            return {
                "protocol_version": PLANNER_PROTOCOL_VERSION,
                "status": "READY",
                "summary": "target.txt contains the wrong baseline value",
                "observed_behavior": ["target.txt contains before"],
                "expected_behavior": ["target.txt contains after"],
                "root_cause": {
                    "claim": "The tracked fixture contains the old value",
                    "evidence": ["target.txt contains before"],
                    "confidence": "HIGH",
                },
                "change_plan": ["Replace the value in target.txt"],
                "preserve": ["Do not change other files"],
                "consumers_to_check": [],
                "risks": [],
                "test_plan": ["Run the configured candidate-content check"],
                "documentation": {
                    "required": False,
                    "paths": [],
                    "reason": "No public contract changes",
                },
                "likely_paths": ["target.txt"],
                "unknowns": [],
            }

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

        def fake_implementer_report(*_args, **kwargs):
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
                )
            )
            contract = kwargs["implementation_contract"]
            return {
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

        output = io.StringIO()
        with (
            mock.patch.object(task_runner, "RunRecorder", _Recorder),
            mock.patch.object(task_runner, "CodexAppServer", _FakeCodexAppServer),
            mock.patch.object(task_runner, "resolve_codex_cmd", return_value=Path(sys.executable)),
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
        self.assertEqual(state["revisions"]["plan"], 1)
        self.assertEqual(state["revisions"]["implementation_contract"], 1)
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


if __name__ == "__main__":
    unittest.main()
