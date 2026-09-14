"""Replay captured Codex transport projections through production admission."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from slivin_harness.codex_transport import TRANSPORT_SCHEMA

FIXTURES = ROOT / "tests" / "fixtures" / "codex_transport"
REPLAY_CASES = (
    ("captured_5a12.json", "test_codex_transport.CodexTransportTests.test_captured_5a12_full_native_admission"),
    ("captured_565.json", "test_codex_transport.CodexTransportTests.test_captured_565_legacy_null_stays_adapter_only"),
    ("captured_420.json", "test_codex_transport.CodexTransportTests.test_captured_420_evaluator_pwsh_adapter_only"),
    ("synthetic_negative.json", "test_codex_transport.CodexTransportTests.test_synthetic_negative_corpus"),
)


def observed_admission(filename: str, fixture: dict) -> str:
    """Classify actual production-path admission, independently of expected metadata."""
    from slivin_harness.codex_transport import CodexTransportAdapter, CodexTransportError, require_output
    from test_codex_transport import CapturedTransportFixture, load_capture
    from tools import smoke_readonly_scratch as native

    with tempfile.TemporaryDirectory(prefix="shr-transport-observed-") as root:
        if filename == "synthetic_negative.json":
            base = CapturedTransportFixture(Path(root), load_capture(FIXTURES / "captured_5a12.json"))
            source = base.item(base.fixture["completed"][0])
            for mutation in fixture["mutations"]:
                command = source["command"]
                if "replace" in mutation:
                    command = command.replace(*mutation["replace"])
                command = mutation.get("prefix", "") + command + mutation.get("suffix", "")
                if "truncate" in mutation:
                    command = command[:-mutation["truncate"]]
                try:
                    CodexTransportAdapter().observe_completed(
                        {**source, "command": command}, phase="planner_initial_initial"
                    )
                except CodexTransportError as error:
                    if error.reason_code == mutation["reason_code"]:
                        continue
                    return "WRONG_TYPED_FAILURE"
                return "UNEXPECTED_ADMISSION"
            return "FAIL_CLOSED"

        capture = CapturedTransportFixture(Path(root), load_capture(FIXTURES / filename))
        commands = capture.replay().commands
        if filename == "captured_5a12.json":
            return native.validate_phase(commands, node=capture.node, project=capture.project,
                scratch=capture.scratch, sibling=capture.sibling, private=capture.private,
                peer=capture.peer, initial=True)["status"]
        if filename == "captured_565.json":
            native.validate_instruction_read_evidence(commands[:2], project=capture.project)
            return "PASS_ADAPTER_ONLY" if (
                not commands[2].output_observed and
                commands[2].transport_form == "powershell-noprofile-command"
            ) else "UNEXPECTED_LEGACY_SHAPE"
        if filename == "captured_420.json":
            return "PASS_ADAPTER_ONLY" if (
                len(commands) == 4 and
                all(row.transport_form == "pwsh-command" for row in commands) and
                [row.exit_code for row in commands] == [0, 0, 0, 1] and
                "PASS tests/arithmetic.test.cjs" in require_output(commands[2]) and
                "FAIL tests/failing.test.cjs" in require_output(commands[3])
            ) else "UNEXPECTED_EVALUATOR_SHAPE"
    return "UNKNOWN_FIXTURE"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases = []
    for filename, test_id in REPLAY_CASES:
        path = FIXTURES / filename
        raw = path.read_bytes()
        fixture = json.loads(raw.decode("utf-8"))
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromName(test_id)
        )
        successful = result.wasSuccessful() and result.testsRun == 1 and not result.skipped
        if successful:
            try:
                actual = observed_admission(filename, fixture)
            except Exception as error:
                actual = "ADMISSION_ERROR:" + (
                    error.reason_code if hasattr(error, "reason_code") else type(error).__name__
                )
        else:
            actual = "REPLAY_TEST_FAILURE"
        cases.append({
            "fixture_id": fixture["fixture_id"],
            "fixture_origin": fixture["fixture_origin"],
            "source_run_id": fixture["source_run_id"],
            "source_projection": fixture.get("source_projection", []),
            "expected_admission": fixture["expected_admission"],
            "actual_admission": actual,
            "adapter_schema": TRANSPORT_SCHEMA,
            "fixture_sha256": hashlib.sha256(raw).hexdigest(),
            "production_path_test": test_id,
            "tests_run": result.testsRun,
        })
    passed = all(case["actual_admission"] == case["expected_admission"] for case in cases)
    summary = {
        "schema_version": "codex-transport-replay.v1",
        "adapter_schema": TRANSPORT_SCHEMA,
        "status": "PASS" if passed else "FAIL",
        "fixtures": cases,
        "proves": "Known captured command envelope and nullable-output compatibility",
        "does_not_prove": "Unseen transport forms or native sandbox enforcement",
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("CODEX_TRANSPORT_REPLAY_" + summary["status"], "fixtures=", len(cases))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
