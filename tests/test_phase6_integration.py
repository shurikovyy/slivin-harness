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

import slivin_harness
import task_runner
from slivin_harness.implementer import IMPLEMENTER_PROTOCOL_VERSION
from slivin_harness.phase6 import (
    BLIND_AUDIT_VERSION,
    CONTRACT_CLOSURE_VERSION,
    PHASE6_VERSION,
    RUNTIME_EVIDENCE_VERSION,
)
from slivin_harness.protocol import EVALUATOR_PROTOCOL_VERSION
from slivin_harness.workflow import (
    RuntimeStatus,
    StageResultCode,
    WORKFLOW_PHASE,
    WORKFLOW_VERSION,
)
from test_protocol import valid_blind_audit, valid_pass, valid_plan, valid_task_contract


ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


RUNTIME_PROBE = r'''from pathlib import Path
import json
import os
request = json.loads(Path(os.environ["SLIVIN_RUNTIME_REQUEST"]).read_text(encoding="utf-8"))
result = {
    "protocol_version": "runtime-result.v1",
    "scenario_id": request["scenario_id"],
    "profile": request["profile"],
    "status": "RUNTIME_VERIFICATION_PASS",
    "summary": "live local target flow passed",
    "oracle_reached": True,
    "candidate_id": request["candidate_id"],
    "requirement_results": [
        {"item_id": item["item_id"], "status": "PASS", "evidence": ["live target value observed"]}
        for item in request["requirements"]
    ],
    "initial_state_confirmed": True,
    "fresh_readback_confirmed": False,
    "cleanup": {"required": False, "confirmed": True, "evidence": ["read-only local fixture"]},
    "read_only_confirmed": False,
}
Path(os.environ["SLIVIN_RUNTIME_RESULT"]).write_text(json.dumps(result), encoding="utf-8")
'''


class _FakeCodexAppServer:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def start_thread(self, **_kwargs) -> str:
        return "implementer-thread"


class Phase6ExecutableIntegrationTests(unittest.TestCase):
    def test_release_and_protocol_versions_are_phase6(self) -> None:
        self.assertEqual(slivin_harness.__version__, "0.8.0a9")
        self.assertEqual(WORKFLOW_VERSION, "workflow.v5")
        self.assertEqual(WORKFLOW_PHASE, "phase6-runtime-two-phase-evaluator")
        self.assertEqual(EVALUATOR_PROTOCOL_VERSION, "evaluator.v5")
        self.assertEqual(PHASE6_VERSION, "phase6-runtime-evaluator.v1")
        self.assertEqual(RUNTIME_EVIDENCE_VERSION, "runtime-evidence.v1")
        self.assertEqual(CONTRACT_CLOSURE_VERSION, "contract-closure.v1")
        self.assertEqual(BLIND_AUDIT_VERSION, "blind-audit.v1")

    def test_task_runner_connects_runtime_and_two_phase_evaluator(self) -> None:
        source = (ROOT / "task_runner.py").read_text(encoding="utf-8")
        for marker in (
            "RuntimeExecutor(",
            "runtime_requirement_gaps",
            "build_contract_closure_record",
            "blind_audit_",
            "on_phase_complete=evaluator_phase_guard",
            "RUNTIME_REPAIR_REQUIRED",
        ):
            self.assertIn(marker, source)

    def test_full_pipeline_executes_runtime_before_two_phase_evaluator(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-phase6-workflow-"))
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        (repo / "target.txt").write_text("before\n", encoding="utf-8")
        (repo / "runtime_probe.py").write_text(RUNTIME_PROBE, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "baseline")

        manifest = root / "task.toml"
        manifest.write_text(
            '''version = 2

task_id = "PHASE6_RUNTIME_INTEGRATION"
project = "demo"
workspace_mode = "git_worktree"
base_ref = "HEAD"
result_mode = "keep_worktree"
risk = "medium"
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
        local_config = {
            "workspace": {"root": str(root / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(repo),
                    "base_ref": "HEAD",
                    "result_mode": "keep_worktree",
                    "require_clean_source": True,
                    "toolchain": {"python": sys.executable},
                    "runtime_verification": {
                        "enabled": True,
                        "scenarios": [
                            {
                                "id": "live-target",
                                "profile": "LIVE_LOCAL",
                                "capabilities": ["LOCAL_APP"],
                                "command": ["{python}", "runtime_probe.py"],
                                "timeout_seconds": 30,
                            }
                        ],
                    },
                }
            },
        }

        class _Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id: str) -> None:
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        def fake_planner(*_args, **_kwargs):
            plan = valid_plan()
            plan["evidence_plan"]["regression"] = [
                {
                    "claim": "The live candidate exposes the new target value.",
                    "level": "LIVE_LOCAL",
                    "capabilities": ["LOCAL_APP"],
                }
            ]
            return plan

        def fake_implementer(*_args, **kwargs):
            workspace = Path(kwargs["workspace"])
            (workspace / "target.txt").write_text("after\n", encoding="utf-8")
            command = list(kwargs["self_verify_command"])
            subprocess.run(command, cwd=workspace, check=True)
            contract = kwargs["implementation_contract"]
            return {
                "protocol_version": IMPLEMENTER_PROTOCOL_VERSION,
                "status": "COMPLETE",
                "summary": "candidate ready",
                "contract_evidence": [
                    {
                        "item_id": item["id"],
                        "status": "VERIFIED",
                        "evidence": ["phase6 integration evidence"],
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

        evaluator_seen_runtime: list[str] = []

        def fake_evaluator(*_args, **kwargs):
            evaluator_seen_runtime.append(kwargs["runtime_evidence"]["status"])
            callback = kwargs.get("on_phase_complete")
            audit = valid_blind_audit()
            blind_callback = kwargs.get("on_blind_audit")
            if blind_callback:
                blind_callback(audit)
            if callback:
                callback("PHASE_A")
                callback("PHASE_B")
            return audit, valid_pass(blind_audit=audit)

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
                task_runner,
                "run_task_contract_normalizer",
                return_value=valid_task_contract(),
            ),
            mock.patch.object(task_runner, "run_planner", side_effect=fake_planner),
            mock.patch.object(
                task_runner, "run_implementer_report", side_effect=fake_implementer
            ),
            mock.patch.object(task_runner, "run_evaluator", side_effect=fake_evaluator),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            result = task_runner.main([str(manifest)])

        self.assertEqual(result, 0, output.getvalue())
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["stages"]["runtime_verification"]["result_code"],
            StageResultCode.RUNTIME_VERIFICATION_PASS.value,
        )
        self.assertEqual(
            state["stages"]["evaluator"]["result_code"],
            StageResultCode.EVALUATION_PASS.value,
        )
        runtime = json.loads(
            (run_root / "runtime_evidence_01.json").read_text(encoding="utf-8")
        )
        self.assertEqual(runtime["status"], RuntimeStatus.PASS.value)
        self.assertEqual(evaluator_seen_runtime, [RuntimeStatus.PASS.value])
        self.assertTrue(
            (run_root / "controller_private" / "contract_closure_01.json").is_file()
        )
        self.assertTrue(
            (run_root / "controller_private" / "blind_audit_01.json").is_file()
        )
        self.assertTrue(
            (run_root / "controller_private" / "evaluation_01.json").is_file()
        )

    def test_phase6_documentation_exists(self) -> None:
        doc = ROOT / "docs" / "PHASE6_RUNTIME_EVALUATOR.md"
        self.assertTrue(doc.is_file())


class PhaseSixTaskRunnerIntegrationTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout.strip()

    def test_full_pipeline_executes_runtime_then_two_phase_evaluator(self) -> None:
        import contextlib
        import io
        import sys
        from unittest import mock

        import task_runner
        from slivin_harness.implementer import IMPLEMENTER_PROTOCOL_VERSION
        from slivin_harness.workflow import StageResultCode
        from test_protocol import proof, valid_blind_audit, valid_pass, valid_plan, valid_task_contract

        root = Path(tempfile.mkdtemp(prefix="slivin-phase6-task-runner-"))
        repo = root / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.name", "Test")
        self._git(repo, "config", "user.email", "test@example.invalid")
        (repo / "target.txt").write_text("before\n", encoding="utf-8")
        (repo / "runtime_probe.py").write_text(
            """from __future__ import annotations
import json
import os
from pathlib import Path
request = json.loads(Path(os.environ['SLIVIN_RUNTIME_REQUEST']).read_text(encoding='utf-8'))
result = {
    'protocol_version': 'runtime-result.v1',
    'scenario_id': request['scenario_id'],
    'profile': request['profile'],
    'status': 'RUNTIME_VERIFICATION_PASS',
    'summary': 'live-local fixture observed the candidate',
    'oracle_reached': True,
    'candidate_id': request['candidate_id'],
    'requirement_results': [
        {'item_id': row['item_id'], 'status': 'PASS', 'evidence': ['runtime fixture observed after']}
        for row in request['requirements']
    ],
    'initial_state_confirmed': True,
    'fresh_readback_confirmed': False,
    'cleanup': {'required': False, 'confirmed': True, 'evidence': []},
    'read_only_confirmed': False,
}
Path(os.environ['SLIVIN_RUNTIME_RESULT']).write_text(json.dumps(result), encoding='utf-8')
""",
            encoding="utf-8",
            newline="\n",
        )
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "baseline")

        manifest = root / "task.toml"
        manifest.write_text(
            f'''version = 2

task_id = "PHASE6_RUNTIME_PIPELINE"
project = "demo"
workspace_mode = "git_worktree"
base_ref = "HEAD"
result_mode = "keep_worktree"
risk = "medium"
max_fix_cycles = 0
max_replan_cycles = 0
turn_timeout_seconds = 60
require_clean_git = true

prompt = """
Change target.txt from before to after. Do not change other files.
"""

[[checks]]
name = "Candidate content"
feedback = "repair"
command = ["{{python}}", "-c", "from pathlib import Path; assert Path('target.txt').read_text().strip() == 'after'"]
timeout_seconds = 30
''',
            encoding="utf-8",
            newline="\n",
        )
        run_root = root / "run"
        local_config = {
            "workspace": {"root": str(root / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(repo),
                    "base_ref": "HEAD",
                    "result_mode": "keep_worktree",
                    "require_clean_source": True,
                    "toolchain": {"project_python": sys.executable},
                    "runtime_verification": {
                        "enabled": True,
                        "scenarios": [
                            {
                                "id": "live-local-fixture",
                                "profile": "LIVE_LOCAL",
                                "capabilities": ["LOCAL_APP"],
                                "command": [sys.executable, "runtime_probe.py"],
                                "timeout_seconds": 30,
                            }
                        ],
                    },
                }
            },
        }

        class Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id: str) -> None:
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        class FakeCodex:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def start_thread(self, **_kwargs):
                return "thread"

        plan = valid_plan()
        plan["evidence_plan"]["regression"] = [
            proof(
                "The live candidate is observed through the local application boundary.",
                level="LIVE_LOCAL",
                capabilities=["LOCAL_APP"],
            )
        ]

        def fake_implementer(*_args, **kwargs):
            workspace = Path(kwargs["workspace"])
            (workspace / "target.txt").write_text("after\n", encoding="utf-8")
            command = list(kwargs["self_verify_command"])
            subprocess.run(command, cwd=workspace, check=True)
            contract = kwargs["implementation_contract"]
            return {
                "protocol_version": IMPLEMENTER_PROTOCOL_VERSION,
                "status": "COMPLETE",
                "summary": "candidate ready",
                "contract_evidence": [
                    {
                        "item_id": item["id"],
                        "status": "VERIFIED",
                        "evidence": ["integration fixture"],
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

        def fake_evaluator(*_args, **kwargs):
            self.assertEqual(
                kwargs["runtime_evidence"]["status"],
                "RUNTIME_VERIFICATION_PASS",
            )
            self.assertEqual(
                kwargs["contract_closure"]["candidate_id"],
                kwargs["candidate_id"],
            )
            audit = valid_blind_audit()
            callback = kwargs.get("on_blind_audit")
            if callback is not None:
                callback(audit)
            phase_callback = kwargs.get("on_phase_complete")
            if phase_callback is not None:
                phase_callback("PHASE_A")
                phase_callback("PHASE_B")
            return audit, valid_pass(blind_audit=audit)

        output = io.StringIO()
        with (
            mock.patch.object(task_runner, "RunRecorder", Recorder),
            mock.patch.object(task_runner, "CodexAppServer", FakeCodex),
            mock.patch.object(task_runner, "resolve_codex_cmd", return_value=Path(sys.executable)),
            mock.patch.object(task_runner, "load_local_config", return_value=(local_config, root / "harness.local.toml")),
            mock.patch.object(task_runner, "run_task_contract_normalizer", return_value=valid_task_contract()),
            mock.patch.object(task_runner, "run_planner", return_value=plan),
            mock.patch.object(task_runner, "run_implementer_report", side_effect=fake_implementer),
            mock.patch.object(task_runner, "run_evaluator", side_effect=fake_evaluator),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            result = task_runner.main([str(manifest)])

        self.assertEqual(result, 0, output.getvalue())
        state = json.loads((run_root / "run_state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            state["stages"]["runtime_verification"]["result_code"],
            StageResultCode.RUNTIME_VERIFICATION_PASS.value,
        )
        self.assertEqual(
            state["stages"]["evaluator"]["result_code"],
            StageResultCode.EVALUATION_PASS.value,
        )
        runtime = json.loads((run_root / "runtime_evidence_01.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["status"], "RUNTIME_VERIFICATION_PASS")
        self.assertTrue((run_root / "blind_audit_01.json").is_file())
        self.assertTrue((run_root / "evaluation_01.json").is_file())
        self.assertTrue((run_root / "contract_closure_01.json").is_file())


if __name__ == "__main__":
    unittest.main()
