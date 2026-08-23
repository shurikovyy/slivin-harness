from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from task_runner import (
    _plan_validation_error,
    planner_benchmark_context,
    run_benchmark_baseline_gate,
)
from tests.test_protocol import valid_plan


class BenchmarkBaselineGateTests(unittest.TestCase):
    def test_current_baseline_failure_becomes_sanitized_controller_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            evidence = run_benchmark_baseline_gate(
                [
                    {
                        "name": "hidden regression",
                        "feedback": "heldout",
                        "command": [sys.executable, "-c", "raise SystemExit(1)"],
                        "timeout_seconds": 30,
                    }
                ],
                workspace=workspace,
                toolchain={},
                runtime_root=root / "runtime",
            )

            self.assertEqual(evidence["baseline_status"], "CONFIRMED_BROKEN")
            self.assertEqual(evidence["authority"], "CONTROLLER_HELDOUT")
            self.assertFalse(evidence["failure_details_exposed_to_planner"])

            sanitized = planner_benchmark_context(evidence)
            self.assertEqual(sanitized["baseline_status"], "CONFIRMED_BROKEN")
            self.assertNotIn("records", sanitized)
            self.assertFalse(sanitized["failure_details_exposed_to_planner"])

    def test_unexpected_baseline_pass_is_not_confirmed_broken(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            evidence = run_benchmark_baseline_gate(
                [
                    {
                        "name": "hidden regression",
                        "feedback": "heldout",
                        "command": [sys.executable, "-c", "raise SystemExit(0)"],
                        "timeout_seconds": 30,
                    }
                ],
                workspace=workspace,
                toolchain={},
                runtime_root=root / "runtime",
            )
            self.assertEqual(
                evidence["baseline_status"],
                "NOT_CONFIRMED_BROKEN",
            )

    def test_planner_cannot_block_confirmed_broken_benchmark_as_not_reproduced(self) -> None:
        plan = valid_plan()
        plan["status"] = "BLOCKED"
        plan["candidate_paths"] = []
        error = _plan_validation_error(
            plan,
            trusted_benchmark_evidence={
                "baseline_status": "CONFIRMED_BROKEN",
                "authority": "CONTROLLER_HELDOUT",
            },
        )
        self.assertIsNotNone(error)
        self.assertEqual(
            error["protocol_error"],
            "PLANNER_TRUSTED_BASELINE_CONFLICT",
        )

    def test_non_benchmark_blocked_plan_remains_allowed(self) -> None:
        plan = valid_plan()
        plan["status"] = "BLOCKED"
        plan["candidate_paths"] = []
        error = _plan_validation_error(plan, trusted_benchmark_evidence=None)
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
