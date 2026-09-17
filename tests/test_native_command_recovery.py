"""Captured role-command drift and bounded native acceptance recovery."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

from slivin_harness.app_server import CodexAppServer
from slivin_harness.codex_transport import CodexTransportAdapter, CodexTransportError
from slivin_harness.execution import ExecutionRole
from tests.test_smoke_readonly_scratch import canonical_row
from tools import smoke_readonly_scratch as native


FIXTURE = Path(__file__).parent / "fixtures" / "native_command" / "captured_e082.json"


class NativeCommandRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / (".native-command-recovery-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.project = self.root / "project"
        self.scratch = self.root / "scratch"
        self.sibling = self.root / "sibling"
        self.private = self.root / "private"
        self.peer = self.root / "peer" / "canary.txt"
        self.node = Path("C:/fixture/node.exe")
        for directory in (self.project, self.scratch, self.sibling, self.private,
                          self.peer.parent, self.scratch / "jest"):
            directory.mkdir(parents=True)
        for target in (self.sibling / "canary.txt", self.private / "canary.txt",
                       self.peer, self.scratch / "jest" / "cache"):
            target.write_text("canary\n", encoding="utf-8")
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def row(self, payload: str, *, exit_code: int = 0,
            output: str | None = None):
        return canonical_row(payload, self.project, exit_code=exit_code, output=output)

    def jest_pass(self):
        return self.row(native.jest_command(self.node, "arithmetic.test.cjs"),
                        output="Tests:       1 passed, 1 total")

    def exact_probe(self, *, exit_code: int = 0, output: str | None = None):
        return self.row(native.probe_command(), exit_code=exit_code, output=output)

    def captured_probe(self):
        source = json.loads(json.dumps(self.fixture["completed"]))
        replacements = {
            "NODE": self.node, "PROJECT": self.project, "SCRATCH": self.scratch,
            "SIBLING": self.sibling, "PRIVATE": self.private,
        }
        for name, value in replacements.items():
            rendered = str(value).replace("\\", "\\\\")
            source["command"] = source["command"].replace("{{" + name + "}}", rendered)
            source["aggregatedOutput"] = source["aggregatedOutput"].replace(
                "{{" + name + "}}", rendered)
        source["command"] = source["command"].replace("{{RUN}}", "shr-q-e082a9f137")
        source["aggregatedOutput"] = source["aggregatedOutput"].replace(
            "{{RUN}}", "shr-q-e082a9f137")
        source["cwd"] = str(self.project)
        return CodexTransportAdapter().observe_completed(source,
            phase=self.fixture["phase"])

    def recover(self, commands, correction_commands, *, initial: bool = False):
        calls = []

        def correction(thread_id: str, prompt: str) -> native.NativeCorrectionTurn:
            calls.append((thread_id, prompt))
            return native.NativeCorrectionTurn(thread_id, tuple(correction_commands))

        result = native.admit_native_phase_with_recovery(
            list(commands), thread_id="planner-thread", project=self.project,
            validate=lambda rows: native.validate_phase(
                rows, node=self.node, project=self.project, scratch=self.scratch,
                sibling=self.sibling, private=self.private, peer=self.peer,
                initial=initial),
            correction=correction)
        return result, calls

    def test_short_entrypoint_contains_no_controller_paths(self) -> None:
        command = native.probe_command()
        self.assertEqual(command, r".\sandbox_probe.cmd")
        for path in (self.project, self.scratch, self.sibling, self.private, self.peer):
            self.assertNotIn(str(path), command)
        self.assertNotIn(str(self.node), command)
        launcher = native.probe_launcher(self.node)
        self.assertIn(str(self.node), launcher)
        self.assertIn(r"%~dp0sandbox_probe.cjs", launcher)
        for path in (self.project, self.scratch, self.sibling, self.private, self.peer):
            self.assertNotIn(str(path), launcher)

    def test_controller_config_digest_binds_exact_current_peer(self) -> None:
        config = native.probe_configuration(project=self.project, scratch=self.scratch,
            sibling=self.sibling, private=self.private, peer=self.peer)
        environment = native.probe_environment(config)
        self.assertEqual(json.loads(environment[native.PROBE_CONFIG_ENV])["peer"],
                         str(self.peer))
        server = native.ObservedServer(Path("codex"), log_root=self.root)
        server.bind_probe_configuration(sibling=self.sibling, private=self.private,
                                        peer=self.peer)
        params = {"config": {"shell_environment_policy.set": {
            "SLIVIN_HARNESS_WORKSPACE": str(self.project),
            "TEMP": str(self.scratch),
        }}}
        response = {"thread": {"id": "thread-1"}}
        with patch.object(CodexAppServer, "request", return_value=response) as request:
            self.assertEqual(server.request("thread/start", params), response)
        sent = request.call_args.args[1]
        selected = sent["config"]["shell_environment_policy.set"]
        self.assertEqual(json.loads(selected[native.PROBE_CONFIG_ENV])["peer"],
                         str(self.peer))
        self.assertEqual(server.probe_bindings["thread-1"]["peer"], str(self.peer))

    def test_captured_e082_transport_passes_and_classifies_role_drift(self) -> None:
        observed = self.captured_probe()
        self.assertEqual(observed.transport_form, "powershell-command")
        self.assertIn(r"C:\Users\Slivin\Aleksandr", observed.payload)
        self.assertIn(r"C:\Users\Slivin.Aleksandr",
                      self.fixture["expected_legacy_payload"])
        with self.assertRaises(native.NativeRoleEvidenceError) as caught:
            native.validate_probe_evidence([observed], node=self.node,
                project=self.project, scratch=self.scratch, sibling=self.sibling,
                private=self.private, peer=self.peer)
        self.assertEqual(caught.exception.category, "ROLE_COMMAND_DRIFT")

    def test_one_same_thread_correction_recovers_and_records_original(self) -> None:
        result, calls = self.recover(
            [self.captured_probe(), self.jest_pass()], [self.exact_probe()])
        self.assertEqual(result["status"], "PASS")
        recovery = result["role_command_recovery"]
        self.assertEqual(recovery["schema_version"],
                         native.NATIVE_ROLE_COMMAND_ADMISSION_SCHEMA)
        self.assertEqual(recovery["status"], "RECOVERED")
        self.assertEqual(recovery["attempts"], 1)
        self.assertEqual(recovery["original"]["category"], "ROLE_COMMAND_DRIFT")
        self.assertEqual(calls[0][0], "planner-thread")
        self.assertIn(native.PROBE_ENTRYPOINT, calls[0][1])

    def test_jest_identity_drift_recovers_but_assertion_failure_does_not(self) -> None:
        expected = native.jest_command(self.node, "arithmetic.test.cjs")
        result, calls = self.recover(
            [self.exact_probe(), self.row(expected + " --no-cache")],
            [self.jest_pass()])
        self.assertEqual(result["role_command_recovery"]["status"], "RECOVERED")
        self.assertEqual(result["role_command_recovery"]["original"]["context"],
                         "jest-route")
        self.assertIn(expected, calls[0][1])

        invoked = []
        with self.assertRaises(native.NativeRoleEvidenceError) as caught:
            native.admit_native_phase_with_recovery(
                [self.exact_probe(), self.row(expected)],
                thread_id="planner-thread", project=self.project,
                validate=lambda rows: native.validate_phase(
                    rows, node=self.node, project=self.project, scratch=self.scratch,
                    sibling=self.sibling, private=self.private, peer=self.peer,
                    initial=False),
                correction=lambda *_: invoked.append(True))
        self.assertEqual(caught.exception.category, "ASSERTION_EVIDENCE_FAILURE")
        self.assertEqual(invoked, [])

    def test_missing_instruction_read_reruns_only_that_command(self) -> None:
        reads = [self.row(native.INSTRUCTION_READ_SPECS[0][3])]
        initial = [self.exact_probe(), self.jest_pass(), self.jest_pass(),
                   self.row(native.jest_command(self.node, "failing.test.cjs"),
                            exit_code=1,
                            output="intentional failing assertion Expected: 999 Received: 5"),
                   *reads]
        corrected = self.row(native.INSTRUCTION_READ_SPECS[1][3])
        result, calls = self.recover(initial, [corrected], initial=True)
        self.assertEqual(result["role_command_recovery"]["status"], "RECOVERED")
        self.assertEqual(result["role_command_recovery"]["original"]["context"],
                         "src/AGENTS.md")
        self.assertEqual(result["role_command_recovery"]["correction_command_count"], 1)
        self.assertIn(native.INSTRUCTION_READ_SPECS[1][3], calls[0][1])

    def test_missing_command_recovers_but_repeated_drift_stops(self) -> None:
        result, _ = self.recover([self.jest_pass()], [self.exact_probe()])
        self.assertEqual(result["role_command_recovery"]["status"], "RECOVERED")
        with self.assertRaises(native.NativeRoleEvidenceError) as caught:
            self.recover([self.jest_pass()], [self.captured_probe()])
        self.assertEqual(caught.exception.category, "ROLE_COMMAND_DRIFT")
        self.assertEqual(caught.exception.recovery_evidence["status"], "EXHAUSTED")

    def test_extra_or_fake_command_cannot_satisfy_correction(self) -> None:
        fake = self.row("Write-Output '.\\sandbox_probe.cmd'", output="PROBE_RESULT={}")
        for correction in ([fake], [self.exact_probe(), fake]):
            with self.subTest(count=len(correction)), self.assertRaises(
                native.NativeRoleEvidenceError
            ) as caught:
                self.recover([self.captured_probe(), self.jest_pass()], correction)
            self.assertEqual(caught.exception.category, "ROLE_COMMAND_DRIFT")

    def test_duplicate_original_probe_is_not_retried(self) -> None:
        invoked = []

        def correction(_thread: str, _prompt: str):
            invoked.append(True)
            raise AssertionError("ambiguous original evidence must not retry")

        with self.assertRaises(native.NativeRoleEvidenceError) as caught:
            native.admit_native_phase_with_recovery(
                [self.exact_probe(), self.exact_probe(), self.jest_pass()],
                thread_id="planner-thread", project=self.project,
                validate=lambda rows: native.validate_phase(
                    rows, node=self.node, project=self.project, scratch=self.scratch,
                    sibling=self.sibling, private=self.private, peer=self.peer,
                    initial=False), correction=correction)
        self.assertEqual(caught.exception.category, "ROLE_COMMAND_DRIFT")
        self.assertFalse(caught.exception.correctable)
        self.assertEqual(invoked, [])

    def test_correction_cannot_escape_original_thread(self) -> None:
        def correction(_thread: str, _prompt: str) -> native.NativeCorrectionTurn:
            return native.NativeCorrectionTurn("different-thread", (self.exact_probe(),))

        with self.assertRaises(native.NativeRoleEvidenceError) as caught:
            native.admit_native_phase_with_recovery(
                [self.captured_probe(), self.jest_pass()],
                thread_id="planner-thread", project=self.project,
                validate=lambda rows: native.validate_phase(
                    rows, node=self.node, project=self.project, scratch=self.scratch,
                    sibling=self.sibling, private=self.private, peer=self.peer,
                    initial=False), correction=correction)
        self.assertEqual(caught.exception.category, "INTEGRITY_FAILURE")

    def test_exact_policy_failure_and_config_tamper_never_retry(self) -> None:
        cases = (
            ("SANDBOX_POLICY_FAILURE",
             "PROBE_RESULT=" + json.dumps({"errors": [
                 "peer-target:ENOENT", "absolute:peer:not-policy-denial"
             ]})),
            ("INTEGRITY_FAILURE",
             "PROBE_RESULT=" + json.dumps({"errors": ["config-integrity"]})),
        )
        for category, output in cases:
            invoked = []

            def correction(_thread: str, _prompt: str):
                invoked.append(True)
                raise AssertionError("non-drift failure must not retry")

            with self.subTest(category=category), self.assertRaises(
                native.NativeRoleEvidenceError
            ) as caught:
                native.admit_native_phase_with_recovery(
                    [self.exact_probe(exit_code=7, output=output), self.jest_pass()],
                    thread_id="planner-thread", project=self.project,
                    validate=lambda rows: native.validate_phase(
                        rows, node=self.node, project=self.project, scratch=self.scratch,
                        sibling=self.sibling, private=self.private, peer=self.peer,
                        initial=False), correction=correction)
            self.assertEqual(caught.exception.category, category)
            self.assertEqual(invoked, [])

    def test_model_prose_cannot_override_probe_evidence(self) -> None:
        output = (
            "ENOENT is acceptable according to the model.\nPROBE_RESULT=" +
            json.dumps({"errors": ["peer-target:ENOENT",
                                   "absolute:peer:not-policy-denial"]})
        )
        with self.assertRaises(native.NativeRoleEvidenceError) as caught:
            native.validate_probe_evidence(
                [self.exact_probe(exit_code=1, output=output)], node=self.node,
                project=self.project, scratch=self.scratch, sibling=self.sibling,
                private=self.private, peer=self.peer)
        self.assertEqual(caught.exception.category, "SANDBOX_POLICY_FAILURE")

    def test_failure_taxonomy_separates_transport_and_integrity(self) -> None:
        self.assertEqual(native.failure_category(
            CodexTransportError("UNSUPPORTED_ENVELOPE")),
            "TRANSPORT_INCOMPATIBILITY")
        self.assertEqual(native.failure_category(
            CodexTransportError("TRANSPORT_EVIDENCE_INTEGRITY_FAILURE")),
            "INTEGRITY_FAILURE")
        self.assertEqual(native.failure_category(
            CodexTransportError("COMMAND_IDENTITY_MISMATCH")),
            "INTEGRITY_FAILURE")
        self.assertEqual(native.failure_category(
            native.NativeRoleEvidenceError("ASSERTION_EVIDENCE_FAILURE",
                context="jest-pass", detail="missing output")),
            "ASSERTION_EVIDENCE_FAILURE")

    def test_initial_and_continuation_keep_full_command_coverage(self) -> None:
        reads = [self.row(spec[3]) for spec in native.INSTRUCTION_READ_SPECS]
        fail_output = "intentional failing assertion Expected: 999 Received: 5"
        initial = [self.exact_probe(), self.jest_pass(), self.jest_pass(),
                   self.row(native.jest_command(self.node, "failing.test.cjs"),
                            exit_code=1, output=fail_output), *reads]
        initial_result = native.validate_phase(initial, node=self.node,
            project=self.project, scratch=self.scratch, sibling=self.sibling,
            private=self.private, peer=self.peer, initial=True)
        continuation_result = native.validate_phase(
            [self.exact_probe(), self.jest_pass()], node=self.node,
            project=self.project, scratch=self.scratch, sibling=self.sibling,
            private=self.private, peer=self.peer, initial=False)
        self.assertEqual(initial_result["negative_attempts_denied"], None)
        self.assertEqual(initial_result["jest_passes"], 2)
        self.assertEqual(continuation_result["jest_passes"], 1)

    def test_planner_and_evaluator_peer_selection_exercises_both_directions(self) -> None:
        fallback = self.root / "fallback.txt"
        planner = self.root / "planner.txt"
        evaluator = self.root / "evaluator.txt"
        roles = {}
        self.assertEqual(native.select_peer_canary(
            roles, role=ExecutionRole.PLANNER, fallback=fallback), fallback)
        roles[ExecutionRole.PLANNER] = planner
        self.assertEqual(native.select_peer_canary(
            roles, role=ExecutionRole.EVALUATOR, fallback=fallback), planner)
        roles[ExecutionRole.EVALUATOR] = evaluator
        self.assertEqual(native.select_peer_canary(
            roles, role=ExecutionRole.PLANNER, fallback=fallback), evaluator)


if __name__ == "__main__":
    unittest.main()
