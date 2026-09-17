"""Captured Codex command transport and adversarial admission regressions."""
from __future__ import annotations

from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

from slivin_harness.app_server import CodexAppServer
from slivin_harness.codex_transport import (
    TRANSPORT_SCHEMA, CodexTransportAdapter, CodexTransportError,
    require_output, same_windows_path, windows_path_key,
)
from tools import smoke_readonly_scratch as native


FIXTURES = Path(__file__).parent / "fixtures" / "codex_transport"
CAPTURED_5A = FIXTURES / "captured_5a12.json"
CAPTURED_565 = FIXTURES / "captured_565.json"
CAPTURED_420 = FIXTURES / "captured_420.json"
SYNTHETIC_NEGATIVES = FIXTURES / "synthetic_negative.json"


def load_capture(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["fixture_origin"] != "captured_real" or not payload["source_run_id"].startswith("shr-q-"):
        raise AssertionError("Captured transport fixture provenance missing")
    return payload


class CapturedTransportFixture:
    """Rebind only sanitized path/output placeholders, retaining raw envelopes."""

    def __init__(self, root: Path, fixture: dict):
        self.fixture = fixture
        self.project = root / "project"
        self.scratch = root / "scratch"
        self.sibling = root / "sibling"
        self.private = root / "private"
        self.peer = root / "peer" / "canary.txt"
        self.node = Path("C:/fixture/node.exe")
        self.pwsh = Path("C:/fixture/runtime/pwsh.exe")
        self.project.mkdir(parents=True)
        (self.scratch / "jest").mkdir(parents=True)
        (self.scratch / "jest" / "cache").write_text("captured-cache", encoding="utf-8")
        self.mapping = {
            "NODE": self.node, "PWSH": self.pwsh, "PROJECT": self.project, "SCRATCH": self.scratch,
            "SIBLING": self.sibling, "PRIVATE": self.private, "PEER": self.peer,
        }

    def item(self, source: dict) -> dict:
        item = dict(source)
        item.pop("phase", None)
        for name, path in self.mapping.items():
            item["command"] = item["command"].replace("{{" + name + "}}", str(path).replace("\\", "\\\\"))
        item["cwd"] = str(self.project)
        if item.get("aggregatedOutput") == "{{PROBE_OUTPUT}}":
            rows = [dict(name=name, result=result, **({"code": code} if code else {}))
                    for name, result, code in self.fixture["probe_output_rows"]]
            item["aggregatedOutput"] = "PROBE_RESULT=" + json.dumps(
                {"rows": rows, "errors": [], "child": None}, separators=(",", ":")
            )
        return item

    def replay(self) -> CodexTransportAdapter:
        adapter = CodexTransportAdapter(expected_shells=(
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            str(self.pwsh),
        ))
        pending = {row["itemId"]: [] for row in self.fixture["deltas"]}
        for row in self.fixture["deltas"]:
            pending[row["itemId"]].append(row)
        for source in self.fixture["completed"]:
            phase = source.get("phase", self.fixture.get("phase", "planner_initial_initial"))
            for delta in pending.pop(source["id"], []):
                params = {key: value for key, value in delta.items() if key != "phase"}
                adapter.observe_delta(params, phase=phase)
            adapter.observe_completed(self.item(source), phase=phase)
        if pending:
            raise AssertionError("Fixture has orphan deltas")
        for phase in {row.phase for row in adapter.commands}:
            adapter.assert_phase_complete(phase)
        return adapter


class CodexTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / (".codex-transport-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.current = CapturedTransportFixture(self.root, load_capture(CAPTURED_5A))

    def test_captured_command_and_noprofile_forms(self) -> None:
        current = self.current.replay()
        old = CapturedTransportFixture(self.root / "legacy", load_capture(CAPTURED_565)).replay()
        self.assertEqual(current.commands[0].transport_form, "powershell-command")
        self.assertEqual(old.commands[0].transport_form, "powershell-noprofile-command")
        self.assertEqual(current.commands[0].payload, native.INSTRUCTION_READ_SPECS[0][3])
        self.assertEqual(old.commands[0].payload, native.INSTRUCTION_READ_SPECS[0][3])
        self.assertEqual(current.commands[0].completion_state, "completed")
        self.assertEqual(TRANSPORT_SCHEMA, "codex-executed-command.v1")

    def test_delta_aggregation_and_null(self) -> None:
        current = self.current.replay()
        root, nested = current.commands[:2]
        self.assertEqual(root.output_sources, ("aggregated", "delta"))
        self.assertIn("ROOT_INSTRUCTIONS_READ", require_output(root))
        self.assertFalse(nested.output_observed)
        self.assertEqual(nested.output_sources, ())
        self.assertEqual(nested.aggregated_output_field, "null")
        with self.assertRaisesRegex(CodexTransportError, "OUTPUT_UNAVAILABLE"):
            require_output(nested)
        missing_field = self.current.item(self.current.fixture["completed"][1])
        missing_field.pop("aggregatedOutput")
        absent = CodexTransportAdapter().observe_completed(missing_field, phase="phase")
        self.assertEqual(absent.aggregated_output_field, "missing")
        self.assertFalse(absent.output_observed)
        delta_only = CodexTransportAdapter()
        item = self.current.item(self.current.fixture["completed"][1])
        for chunk in ("first ", "second"):
            delta_only.observe_delta({"threadId": "thread", "turnId": "turn",
                "itemId": item["id"], "delta": chunk}, phase="phase")
        record = delta_only.observe_completed(item, phase="phase",
            thread_id="thread", turn_id="turn")
        self.assertEqual(require_output(record), "first second")
        self.assertEqual(record.output_sources, ("delta",))

    def test_captured_5a12_legacy_transport_evidence(self) -> None:
        commands = self.current.replay().commands
        evidence = native.validate_instruction_read_evidence(commands[:2],
            project=self.current.project)
        self.assertFalse(evidence["nested"]["output_marker_observed"])
        self.assertEqual(commands[2].exit_code, 0)
        self.assertTrue(commands[2].output_observed)
        with self.assertRaisesRegex(native.NativeRoleEvidenceError,
                                    "ROLE_COMMAND_DRIFT"):
            native.validate_phase(commands, node=self.current.node,
                project=self.current.project, scratch=self.current.scratch,
                sibling=self.current.sibling, private=self.current.private,
                peer=self.current.peer, initial=True)

    def test_observed_server_routes_captured_events_through_adapter(self) -> None:
        source = self.current.fixture["completed"][0]
        item = self.current.item(source)
        events = [
            {"method": "item/commandExecution/outputDelta", "params": dict(delta)}
            for delta in self.current.fixture["deltas"]
        ]
        events.append({"method": "item/completed", "params": {
            "threadId": "synthetic-planner-thread", "turnId": "synthetic-planner-turn",
            "item": item,
        }})
        server = native.ObservedServer(Path("codex"), log_root=self.root)
        server.phase = self.current.fixture["phase"]
        with patch.object(CodexAppServer, "_receive_raw_optional", side_effect=events):
            for _ in events:
                server._receive_raw_optional(0)
        self.assertEqual(len(server.commands), 1)
        self.assertEqual(server.commands[0].output_sources, ("aggregated", "delta"))
        self.assertEqual(server.commands[0].payload, native.INSTRUCTION_READ_SPECS[0][3])

    def test_observed_server_rejects_malformed_command_event_typed(self) -> None:
        server = native.ObservedServer(Path("codex"), log_root=self.root)
        with patch.object(CodexAppServer, "_receive_raw_optional", return_value={
            "method": "item/completed", "params": {"item": None},
        }):
            with self.assertRaises(CodexTransportError) as caught:
                server._receive_raw_optional(0)
        self.assertEqual(caught.exception.reason_code, "MALFORMED_TRANSPORT_EVENT")

    def test_captured_565_legacy_null_stays_adapter_only(self) -> None:
        old = CapturedTransportFixture(self.root / "legacy", load_capture(CAPTURED_565))
        commands = old.replay().commands
        evidence = native.validate_instruction_read_evidence(commands[:2], project=old.project)
        self.assertEqual(set(evidence), {"root", "nested"})
        self.assertFalse(commands[2].output_observed)
        self.assertEqual(commands[2].transport_form, "powershell-noprofile-command")
        self.assertNotEqual(commands[2].payload, native.probe_command())

    def test_captured_420_evaluator_pwsh_adapter_only(self) -> None:
        capture = CapturedTransportFixture(self.root / "evaluator", load_capture(CAPTURED_420))
        commands = capture.replay().commands
        self.assertEqual(len(commands), 4)
        self.assertTrue(all(row.transport_form == "pwsh-command" for row in commands))
        self.assertEqual([row.exit_code for row in commands], [0, 0, 0, 1])
        self.assertIn("ROOT_INSTRUCTIONS_READ", require_output(commands[0]))
        self.assertIn("PASS tests/arithmetic.test.cjs", require_output(commands[2]))
        self.assertIn("FAIL tests/failing.test.cjs", require_output(commands[3]))

    def test_default_pwsh_admission_requires_controller_selected_installed_path(self) -> None:
        with patch.dict(os.environ, {"USERPROFILE": r"C:\fixture", "SystemRoot": r"C:\Windows"}), (
            patch("slivin_harness.codex_transport.Path.is_file", return_value=True)
        ):
            installed = CodexTransportAdapter()
        self.assertEqual(len(installed.expected_shells), 2)
        self.assertTrue(same_windows_path(installed.expected_shells[1],
            r"C:\fixture\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe"))
        with patch.dict(os.environ, {"USERPROFILE": r"C:\fixture", "SystemRoot": r"C:\Windows"}), (
            patch("slivin_harness.codex_transport.Path.is_file", return_value=False)
        ):
            missing = CodexTransportAdapter()
        self.assertEqual(len(missing.expected_shells), 1)

    def test_jest_output_and_cache_route_remain_required(self) -> None:
        commands = self.current.replay().commands
        commands[2] = replace(commands[2], payload=native.probe_command())
        pass_row = commands[3]
        no_output = [replace(row, output=None, output_observed=False, output_sources=())
                     if row.item_id == pass_row.item_id else row for row in commands]
        with self.assertRaisesRegex(native.NativeRoleEvidenceError,
                                    "ASSERTION_EVIDENCE_FAILURE"):
            native.validate_phase(no_output, node=self.current.node,
                project=self.current.project, scratch=self.current.scratch,
                sibling=self.current.sibling, private=self.current.private,
                peer=self.current.peer, initial=True)
        forbidden = [replace(row, payload=row.payload + " --no-cache")
                     if row.item_id == pass_row.item_id else row for row in commands]
        with self.assertRaisesRegex(native.NativeRoleEvidenceError,
                                    "ROLE_COMMAND_DRIFT"):
            native.validate_phase(forbidden, node=self.current.node,
                project=self.current.project, scratch=self.current.scratch,
                sibling=self.current.sibling, private=self.current.private,
                peer=self.current.peer, initial=True)

    def test_malformed_envelope_and_completion_fail_typed(self) -> None:
        source = self.current.fixture["completed"][0]
        valid = self.current.item(source)
        altered = (
            valid["command"].replace("powershell.exe", "pwsh.exe"),
            valid["command"].replace(" -Command ", " -ExecutionPolicy Bypass -Command "),
            valid["command"].replace(" -Command ", " -EncodedCommand "),
            "prefix " + valid["command"],
            valid["command"] + " suffix",
            valid["command"][:-1],
        )
        for command in altered:
            with self.subTest(command=command[:75]):
                adapter = CodexTransportAdapter()
                with self.assertRaises(CodexTransportError) as caught:
                    adapter.observe_completed({**valid, "command": command}, phase="planner_initial_initial")
                self.assertEqual(caught.exception.reason_code, "UNSUPPORTED_ENVELOPE")
        for field, value in (("exitCode", None), ("exitCode", True), ("cwd", None), ("id", None)):
            adapter = CodexTransportAdapter()
            with self.subTest(field=field), self.assertRaises(CodexTransportError) as caught:
                adapter.observe_completed({**valid, field: value}, phase="planner_initial_initial")
            self.assertEqual(caught.exception.reason_code, "MALFORMED_TRANSPORT_EVENT")
        with self.assertRaises(CodexTransportError) as caught:
            CodexTransportAdapter().observe_completed({**valid, "cwd": r".\project"},
                phase="planner_initial_initial")
        self.assertEqual(caught.exception.reason_code, "MALFORMED_TRANSPORT_EVENT")

    def test_synthetic_negative_corpus(self) -> None:
        corpus = json.loads(SYNTHETIC_NEGATIVES.read_text(encoding="utf-8"))
        self.assertEqual(corpus["fixture_origin"], "synthetic_negative")
        source = self.current.item(self.current.fixture["completed"][0])
        for mutation in corpus["mutations"]:
            command = source["command"]
            if "replace" in mutation:
                command = command.replace(*mutation["replace"])
            command = mutation.get("prefix", "") + command + mutation.get("suffix", "")
            if "truncate" in mutation:
                command = command[:-mutation["truncate"]]
            adapter = CodexTransportAdapter()
            with self.subTest(mutation=mutation["id"]), self.assertRaises(CodexTransportError) as caught:
                adapter.observe_completed({**source, "command": command}, phase="planner_initial_initial")
            self.assertEqual(caught.exception.reason_code, mutation["reason_code"])

    def test_wrong_identity_and_cwd_fail_closed(self) -> None:
        current = self.current.replay()
        root = current.commands[0]
        self.assertTrue(same_windows_path(str(self.current.project).swapcase().replace("\\", "/"),
                                          str(self.current.project)))
        self.assertEqual(windows_path_key("\\\\?\\" + str(self.current.project)),
                         windows_path_key(str(self.current.project)))
        with self.assertRaisesRegex(native.NativeRoleEvidenceError,
                                    "ROLE_COMMAND_DRIFT"):
            native.validate_instruction_read_evidence(
                [replace(root, cwd=windows_path_key(str(self.root / "wrong"))), current.commands[1]],
                project=self.current.project,
            )
        with self.assertRaisesRegex(native.NativeRoleEvidenceError,
                                    "ROLE_COMMAND_DRIFT"):
            native.validate_instruction_read_evidence(
                [replace(root, payload="Write-Output 'AGENTS.md'"), current.commands[1]],
                project=self.current.project,
            )
        wrapped = self.current.item(self.current.fixture["completed"][0])
        wrapped["command"] = wrapped["command"].replace(
            native.INSTRUCTION_READ_SPECS[0][3],
            "Write-Output " + native.INSTRUCTION_READ_SPECS[0][3],
        )
        echoed = CodexTransportAdapter().observe_completed(wrapped, phase="phase")
        with self.assertRaisesRegex(native.NativeRoleEvidenceError,
                                    "ROLE_COMMAND_DRIFT"):
            native.validate_instruction_read_evidence([echoed, current.commands[1]],
                project=self.current.project)
        adapter = CodexTransportAdapter()
        item = self.current.item(self.current.fixture["completed"][0])
        adapter.observe_delta({"threadId": "thread", "turnId": "turn", "itemId": item["id"],
                               "delta": "output"}, phase="phase")
        with self.assertRaisesRegex(CodexTransportError, "COMMAND_IDENTITY_MISMATCH"):
            adapter.observe_completed(item, phase="other", thread_id="thread", turn_id="turn")

    def test_duplicate_and_late_delta_fail(self) -> None:
        item = self.current.item(self.current.fixture["completed"][1])
        adapter = CodexTransportAdapter()
        adapter.observe_completed(item, phase="phase")
        with self.assertRaisesRegex(CodexTransportError, "DUPLICATE_EXECUTION"):
            adapter.observe_completed(item, phase="phase")
        with self.assertRaisesRegex(CodexTransportError, "TRANSPORT_EVIDENCE_INTEGRITY_FAILURE"):
            adapter.observe_delta({"threadId": "thread", "turnId": "turn", "itemId": item["id"],
                                   "delta": "late"}, phase="phase")
        other = CodexTransportAdapter()
        other.observe_delta({"threadId": "thread", "turnId": "turn", "itemId": "other-item",
                             "delta": "wrong output"}, phase="phase")
        other.observe_completed(item, phase="phase")
        self.assertFalse(other.commands[0].output_observed)
        with self.assertRaisesRegex(CodexTransportError, "TRANSPORT_EVIDENCE_INTEGRITY_FAILURE"):
            other.assert_phase_complete("phase")
        mismatch = CodexTransportAdapter()
        mismatch.observe_delta({"threadId": "thread", "turnId": "turn", "itemId": item["id"],
                                "delta": "different"}, phase="phase")
        with self.assertRaisesRegex(CodexTransportError, "TRANSPORT_EVIDENCE_INTEGRITY_FAILURE"):
            mismatch.observe_completed({**item, "aggregatedOutput": "expected"}, phase="phase")

    def test_no_native_consumer_parses_raw_envelope(self) -> None:
        native_source = inspect.getsource(native)
        self.assertNotIn("_POWERSHELL_COMMAND", native_source)
        self.assertNotIn("re.compile(", native_source)
        self.assertEqual(native_source.count('"aggregatedOutput"'), 1)  # raw diagnostic logger
        for function in (native.validate_instruction_read_evidence,
                         native.validate_probe_evidence, native.validate_phase):
            source = inspect.getsource(function)
            self.assertNotIn("aggregatedOutput", source)
            self.assertNotIn("powershell.exe", source)
            self.assertNotIn("-Command", source)


if __name__ == "__main__":
    unittest.main()
