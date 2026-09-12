"""Replay sanitized real-model artifact-boundary failures without model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

REPLAY_TESTS = (
    "test_evaluator_impact_challenge.EvaluatorImpactChallengeTests."
    "test_captured_qe1_invalid_contract_handle_gets_exact_reference_correction",
    "test_evaluator_impact_challenge.EvaluatorImpactChallengeTests."
    "test_captured_qs1_unknown_name_gets_handle_correction_and_no_model_revision",
    "test_planner.PlannerCapabilityNegotiationTests."
    "test_captured_qe2_generic_symbol_replays_through_planner_admission",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    suite = unittest.defaultTestLoader.loadTestsFromNames(REPLAY_TESTS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {
        "schema_version": "model-artifact-transcript-replay.v1",
        "status": "PASS" if result.wasSuccessful() and result.testsRun == len(REPLAY_TESTS) else "FAIL",
        "tests_run": result.testsRun,
        "required_tests": list(REPLAY_TESTS),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "proves": "Known sanitized artifact-boundary failures replay through production admission paths",
        "does_not_prove": "Real-model semantic qualification or unseen transcript coverage",
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print("MODEL_ARTIFACT_REPLAY_" + summary["status"], "tests=", result.testsRun)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
