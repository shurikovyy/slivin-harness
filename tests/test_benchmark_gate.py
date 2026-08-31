from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from task_runner import (
    planner_benchmark_context,
    require_candidate_change_for_confirmed_benchmark,
    run_benchmark_baseline_gate,
    verify_oracle_calibration_certificate,
)


class FakeRecorder:
    def __init__(self) -> None:
        self.writes: dict[str, object] = {}

    def write_json(self, name: str, value: object) -> Path:
        self.writes[name] = value
        return Path(name)


class BenchmarkBaselineGateTests(unittest.TestCase):
    def test_baseline_failure_becomes_sanitized_controller_fact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            evidence = run_benchmark_baseline_gate(
                [
                    {
                        "name": "hidden regression",
                        "feedback": "heldout",
                        "command": [
                            sys.executable,
                            "-c",
                            "print('ORACLE_REACHED'); raise SystemExit(1)",
                        ],
                        "timeout_seconds": 30,
                    }
                ],
                workspace=workspace,
                toolchain={},
                runtime_root=root / "runtime",
                failure_marker="ORACLE_REACHED",
            )
            self.assertEqual(evidence["baseline_status"], "CONFIRMED_BROKEN")
            context = planner_benchmark_context(evidence)
            self.assertIn("CONFIRMED_BROKEN", context)
            self.assertNotIn("raise SystemExit", context)
            self.assertNotIn("records", context)

    def test_unexpected_baseline_pass_blocks_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            with self.assertRaisesRegex(RuntimeError, "unexpectedly passes"):
                run_benchmark_baseline_gate(
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
                    failure_marker="ORACLE_REACHED",
                )


    def test_infrastructure_failure_cannot_confirm_broken_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            with self.assertRaisesRegex(RuntimeError, "infrastructure/setup failure"):
                run_benchmark_baseline_gate(
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
                    failure_marker="ORACLE_REACHED",
                )

    def test_checks_receive_workspace_environment(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            evidence = run_benchmark_baseline_gate(
                [
                    {
                        "name": "hidden regression",
                        "feedback": "heldout",
                        "command": [
                            sys.executable,
                            "-c",
                            (
                                "import os; "
                                "assert os.environ['SLIVIN_HARNESS_WORKSPACE']; "
                                "print('ORACLE_REACHED'); raise SystemExit(1)"
                            ),
                        ],
                        "timeout_seconds": 30,
                    }
                ],
                workspace=workspace,
                toolchain={},
                runtime_root=root / "runtime",
                failure_marker="ORACLE_REACHED",
            )
            self.assertEqual(evidence["baseline_status"], "CONFIRMED_BROKEN")


    def test_confirmed_broken_benchmark_with_empty_candidate_fails_fast(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no candidate changes"):
            require_candidate_change_for_confirmed_benchmark(
                {"baseline_status": "CONFIRMED_BROKEN"},
                [],
            )

        require_candidate_change_for_confirmed_benchmark(
            {"baseline_status": "CONFIRMED_BROKEN"},
            ["src/fix.py"],
        )
        require_candidate_change_for_confirmed_benchmark(None, [])

    def test_calibration_certificate_is_bound_to_check_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            hidden = root / "hidden.test"
            hidden.write_text("assert contract\n", encoding="utf-8")
            spec = {
                "name": "hidden",
                "feedback": "heldout",
                "command": [sys.executable, str(hidden)],
                "timeout_seconds": 30,
            }
            from task_runner import _stable_sha256

            certificate = root / "certificate.json"
            certificate.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "heldout_checks": [
                            {
                                "name": "hidden",
                                "spec_sha256": _stable_sha256(spec),
                                "files": [
                                    {
                                        "path": str(hidden),
                                        "sha256": hashlib.sha256(hidden.read_bytes()).hexdigest(),
                                    }
                                ],
                                "calibration_cases": [
                                    {
                                        "name": "broken",
                                        "role": "broken_baseline",
                                        "expected_result": "FAIL",
                                        "fixture_fingerprint": "a" * 64,
                                    },
                                    {
                                        "name": "good-a",
                                        "role": "positive_reference",
                                        "expected_result": "PASS",
                                        "fixture_fingerprint": "b" * 64,
                                    },
                                    {
                                        "name": "good-b",
                                        "role": "positive_reference",
                                        "expected_result": "PASS",
                                        "fixture_fingerprint": "c" * 64,
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            recorder = FakeRecorder()
            verify_oracle_calibration_certificate(
                [spec], certificate_path=certificate, recorder=recorder
            )
            self.assertIn("oracle_calibration_certificate_verified.json", recorder.writes)

            hidden.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                verify_oracle_calibration_certificate(
                    [spec], certificate_path=certificate, recorder=FakeRecorder()
                )

    def test_semantic_calibration_requires_positive_and_negative_controls(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            hidden = root / "hidden.test"
            hidden.write_text("assert contract\n", encoding="utf-8")
            spec = {
                "name": "hidden",
                "feedback": "heldout",
                "command": [sys.executable, str(hidden)],
                "timeout_seconds": 30,
            }
            from task_runner import _stable_sha256

            certificate = root / "certificate.json"
            certificate.write_text(
                json.dumps({
                    "schema_version": 2,
                    "heldout_checks": [{
                        "name": "hidden",
                        "spec_sha256": _stable_sha256(spec),
                        "files": [{
                            "path": str(hidden),
                            "sha256": hashlib.sha256(hidden.read_bytes()).hexdigest(),
                        }],
                        "calibration_cases": [{
                            "name": "broken",
                            "role": "broken_baseline",
                            "expected_result": "FAIL",
                            "fixture_fingerprint": "d" * 64,
                        }],
                    }],
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "multiple calibration_cases"):
                verify_oracle_calibration_certificate(
                    [spec], certificate_path=certificate, recorder=FakeRecorder()
                )



if __name__ == "__main__":
    unittest.main()
