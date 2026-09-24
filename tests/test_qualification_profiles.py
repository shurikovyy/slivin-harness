from __future__ import annotations

from pathlib import Path
import unittest
import signal
from unittest import mock

import task_runner
from slivin_harness.app_server import CodexAppServer
from slivin_harness.qualification import (
    REAL_MODEL_CASES,
    codex_config_overrides,
    qualification_profile,
    validate_real_model_selection,
)
from tools.release_check import (
    model_backed_stage_commands,
    qualification_stage_identity_matches,
    qualification_terminal_status,
)
from tools.release_real_models import (
    QUALIFICATION_CASE_WALL_LIMIT_SECONDS,
    QUALIFICATION_TURN_WALL_LIMIT_SECONDS,
    execute_case_sequence,
    real_model_run_passed,
    resolve_run_selection,
    run_bounded_case_process,
)


class QualificationProfileTests(unittest.TestCase):
    def commands(self, mode: str):
        return model_backed_stage_commands(
            qualification_profile(mode), python="python", node=Path("node.exe"),
            codex=Path("codex.exe"), runtime=Path("node_modules"),
            output=Path("qualification-output"),
        )

    def test_dev_selects_only_expiry_one_with_terra_medium_and_fail_fast(self):
        profile = qualification_profile("dev")
        self.assertEqual(profile.real_model_cases, ("expiry-1",))
        self.assertEqual((profile.model, profile.model_reasoning_effort),
                         ("gpt-5.6-terra", "medium"))
        command = self.commands("dev")["real_models"][0]
        self.assertEqual([command[index + 1] for index, value in enumerate(command)
                          if value == "--case"], ["expiry-1"])
        self.assertIn("--fail-fast", command)
        self.assertIn("gpt-5.6-terra", command)
        self.assertIn("medium", command)
        native_command = self.commands("dev")["native_roles"][0]
        self.assertEqual(native_command[native_command.index("--qualification-mode") + 1], "dev")

    def test_dev_failure_stops_case_sequence(self):
        visited = []

        def execute(name):
            visited.append(name)
            return {"label": name, "status": "FAIL" if name == "expiry-1" else "PASS"}

        records = execute_case_sequence(
            REAL_MODEL_CASES, fail_fast=True, execute=execute,
        )
        self.assertEqual(visited, ["expiry-1"])
        self.assertEqual([row["status"] for row in records], ["FAIL", "NOT_RUN", "NOT_RUN"])

    def test_dev_can_never_emit_release_qualified(self):
        profile = qualification_profile("dev")
        self.assertFalse(profile.release_qualifying)
        self.assertEqual(qualification_terminal_status(profile, passed=True),
                         "DEV_QUALIFICATION_PASS")
        self.assertNotEqual(qualification_terminal_status(profile, passed=True),
                            "RELEASE_QUALIFIED")

    def test_release_selects_all_cases_and_pins_terra_medium_with_fail_fast(self):
        profile = qualification_profile("release")
        self.assertEqual(profile.real_model_cases, REAL_MODEL_CASES)
        self.assertEqual((profile.model, profile.model_reasoning_effort),
                         ("gpt-5.6-terra", "medium"))
        command = self.commands("release")["real_models"][0]
        self.assertEqual([command[index + 1] for index, value in enumerate(command)
                          if value == "--case"], list(REAL_MODEL_CASES))
        self.assertIn("--fail-fast", command)
        self.assertTrue(profile.release_qualifying)

    def test_profile_and_stage_evidence_bind_model_effort_cases_and_policy(self):
        for mode in ("dev", "release"):
            profile = qualification_profile(mode)
            evidence = profile.to_dict()
            evidence.update({
                "real_model_cases_executed": list(profile.real_model_cases),
                "ambient_model_inheritance": False,
                "codex_version": "codex-cli synthetic",
            })
            self.assertTrue(qualification_stage_identity_matches(evidence, profile))
            self.assertIn("selected_model", evidence)
            self.assertIn("selected_reasoning_effort", evidence)
            changed = dict(evidence, selected_reasoning_effort="low")
            self.assertFalse(qualification_stage_identity_matches(changed, profile))
            if mode == "release":
                partial = dict(evidence, real_model_cases_executed=["expiry-1"])
                self.assertFalse(qualification_stage_identity_matches(partial, profile))

    def test_ambient_model_cannot_override_controller_cli_identity(self):
        config = {"codex": {
            "command": "codex.exe", "model": "gpt-5.6-terra",
            "model_reasoning_effort": "medium",
        }}
        self.assertEqual(task_runner.resolve_codex_model_identity(config),
                         ("gpt-5.6-terra", "medium"))
        server = CodexAppServer(
            Path("codex.exe"), model="gpt-5.6-terra",
            model_reasoning_effort="medium",
        )
        command = " ".join(server._command())
        self.assertIn("model=", command)
        self.assertIn("gpt-5.6-terra", command)
        self.assertIn("model_reasoning_effort", command)
        self.assertNotIn("ambient-model", command)

    def test_model_overrides_use_transport_safe_toml_literals(self):
        self.assertEqual(
            codex_config_overrides("gpt-5.6-terra", "medium"),
            ("model='gpt-5.6-terra'", "model_reasoning_effort='medium'"),
        )
        self.assertEqual(
            codex_config_overrides("gpt-5.6-sol", "high"),
            ("model='gpt-5.6-sol'", "model_reasoning_effort='high'"),
        )

    def test_direct_selection_contract_rejects_profile_drift(self):
        self.assertTrue(validate_real_model_selection(
            mode="release", model="gpt-5.6-terra", effort="medium",
            cases=REAL_MODEL_CASES, fail_fast=True,
        ))
        with self.assertRaises(ValueError):
            validate_real_model_selection(
                mode="release", model="gpt-5.6-sol", effort="high",
                cases=REAL_MODEL_CASES, fail_fast=False,
            )
        self.assertFalse(validate_real_model_selection(
            mode="focused", model="gpt-5.6-terra", effort="medium",
            cases=("expiry-1",), fail_fast=True,
        ))

    def test_direct_tool_default_remains_full_release(self):
        cases, mode, release_qualifying = resolve_run_selection(
            cases=None, qualification_mode=None, model="gpt-5.6-terra",
            effort="medium", fail_fast=True,
        )
        self.assertEqual(cases, REAL_MODEL_CASES)
        self.assertEqual(mode, "release")
        self.assertTrue(release_qualifying)

    def test_release_is_blocked_if_any_mandatory_case_fails(self):
        cases = [
            {"label": "expiry-1", "status": "PASS"},
            {"label": "suspension-1", "status": "FAIL"},
            {"label": "expiry-2", "status": "PASS"},
        ]
        self.assertFalse(real_model_run_passed(
            REAL_MODEL_CASES, cases, runtime_source_unchanged=True,
        ))
        visited = []
        stopped = execute_case_sequence(
            REAL_MODEL_CASES, fail_fast=qualification_profile("release").fail_fast,
            execute=lambda name: visited.append(name) or {
                "label": name, "status": "FAIL" if name == "suspension-1" else "PASS",
            },
        )
        self.assertEqual([row["label"] for row in stopped], list(REAL_MODEL_CASES))
        self.assertEqual([row["status"] for row in stopped], ["PASS", "FAIL", "NOT_RUN"])
        self.assertEqual(visited, ["expiry-1", "suspension-1"])

    def test_qualification_limits_and_owned_process_tree_termination(self):
        self.assertEqual(QUALIFICATION_TURN_WALL_LIMIT_SECONDS, 20 * 60)
        self.assertEqual(QUALIFICATION_CASE_WALL_LIMIT_SECONDS, 45 * 60)
        process = mock.Mock(pid=54321, returncode=-1)
        process.wait.side_effect = [
            __import__("subprocess").TimeoutExpired("fixture", 2700),
            __import__("subprocess").TimeoutExpired("fixture", 15),
            -1,
        ]
        with (
            mock.patch("tools.release_real_models.os.name", "nt"),
            mock.patch("tools.release_real_models.subprocess.Popen", return_value=process),
            mock.patch("tools.release_real_models.subprocess.run") as taskkill,
        ):
            exit_code, stop_reason = run_bounded_case_process(
                ["python", "task_runner.py"], cwd=Path("."), env={}, log=mock.Mock(),
            )
        self.assertEqual(exit_code, -1)
        self.assertEqual(stop_reason, "SYNTHETIC_CASE_WALL_LIMIT")
        process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
        taskkill.assert_called_once()
        self.assertEqual(taskkill.call_args.args[0][:4], ["taskkill.exe", "/PID", "54321", "/T"])
        self.assertEqual(
            qualification_terminal_status(qualification_profile("release"), passed=False),
            "NOT_QUALIFIED",
        )


if __name__ == "__main__":
    unittest.main()
