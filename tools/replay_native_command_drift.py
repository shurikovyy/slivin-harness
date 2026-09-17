"""Replay captured native role-command drift through production recovery."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from slivin_harness.codex_transport import TRANSPORT_SCHEMA
from tools.smoke_readonly_scratch import (
    NATIVE_COMMAND_CORRECTION_BUDGET, NATIVE_ROLE_COMMAND_ADMISSION_SCHEMA,
)


FIXTURE = ROOT / "tests" / "fixtures" / "native_command" / "captured_e082.json"
REPLAY_TESTS = (
    "test_native_command_recovery.NativeCommandRecoveryTests.test_captured_e082_transport_passes_and_classifies_role_drift",
    "test_native_command_recovery.NativeCommandRecoveryTests.test_one_same_thread_correction_recovers_and_records_original",
    "test_native_command_recovery.NativeCommandRecoveryTests.test_missing_command_recovers_but_repeated_drift_stops",
    "test_native_command_recovery.NativeCommandRecoveryTests.test_exact_policy_failure_and_config_tamper_never_retry",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    raw = FIXTURE.read_bytes()
    fixture = json.loads(raw.decode("utf-8"))
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromName(name) for name in REPLAY_TESTS
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful() and result.testsRun == len(REPLAY_TESTS)
    actual = "RECOVERED_AFTER_ROLE_COMMAND_DRIFT" if passed else "REPLAY_TEST_FAILURE"
    summary = {
        "schema_version": "native-command-replay.v1",
        "status": "PASS" if actual == fixture["expected_admission"] else "FAIL",
        "fixture_id": fixture["fixture_id"],
        "fixture_origin": fixture["fixture_origin"],
        "source_run_id": fixture["source_run_id"],
        "source_projection": fixture["source_projection"],
        "expected_admission": fixture["expected_admission"],
        "actual_admission": actual,
        "transport_schema": TRANSPORT_SCHEMA,
        "admission_schema": NATIVE_ROLE_COMMAND_ADMISSION_SCHEMA,
        "correction_budget": NATIVE_COMMAND_CORRECTION_BUDGET,
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "production_path_tests": list(REPLAY_TESTS),
        "tests_run": result.testsRun,
        "proves": "Captured transport-valid role command drift is boundedly recoverable",
        "does_not_prove": "Native Windows sandbox enforcement or unseen command drift forms",
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("NATIVE_COMMAND_REPLAY_" + summary["status"],
          "fixture=", fixture["fixture_id"], "tests=", result.testsRun)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
