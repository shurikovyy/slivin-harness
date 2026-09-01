from __future__ import annotations

import unittest
from pathlib import Path

import slivin_harness
from slivin_harness.phase7 import (
    BENCHMARK_ISOLATION_VERSION,
    DELIVERY_RECORD_VERSION,
    FINAL_ACCEPTANCE_VERSION,
    HELDOUT_EVIDENCE_VERSION,
    PATCH_PROOF_VERSION,
    PHASE7_VERSION,
)
from slivin_harness.workflow import WORKFLOW_PHASE, WORKFLOW_VERSION


ROOT = Path(__file__).resolve().parents[1]


class Phase7ExecutableIntegrationTests(unittest.TestCase):
    def test_release_and_contract_versions_are_phase7(self) -> None:
        self.assertEqual(slivin_harness.__version__, "0.8.0a11")
        self.assertEqual(WORKFLOW_VERSION, "workflow.v6")
        self.assertEqual(WORKFLOW_PHASE, "phase7-final-gate-delivery-benchmark")
        self.assertEqual(PHASE7_VERSION, "phase7-final-gate.v1")
        self.assertEqual(PATCH_PROOF_VERSION, "patch-proof.v1")
        self.assertEqual(FINAL_ACCEPTANCE_VERSION, "final-acceptance.v2")
        self.assertEqual(DELIVERY_RECORD_VERSION, "delivery-record.v2")
        self.assertEqual(HELDOUT_EVIDENCE_VERSION, "heldout-evidence.v2")
        self.assertEqual(BENCHMARK_ISOLATION_VERSION, "benchmark-isolation.v1")

    def test_phase7_documentation_exists(self) -> None:
        doc = ROOT / "docs" / "PHASE7_FINAL_GATE.md"
        self.assertTrue(doc.is_file())
        text = doc.read_text(encoding="utf-8")
        for marker in (
            "Patch reconstruction proof",
            "Immutable final acceptance",
            "Semantic replan без anchoring",
            "Historical benchmark isolation",
            "Held-out — экзамен, а не repair tool",
        ):
            self.assertIn(marker, text)

    def test_task_runner_connects_final_gate_and_transactional_delivery(self) -> None:
        source = (ROOT / "task_runner.py").read_text(encoding="utf-8")
        for marker in (
            "reconcile_quality_gate(",
            "build_patch_reconstruction_proof(",
            "build_final_acceptance(",
            "deliver_candidate_transaction(",
            "write_once_authoritative_json(",
            "classify_heldout_results(",
            "reset_workspace_for_semantic_replan(",
        ):
            self.assertIn(marker, source)

    def test_historical_workspace_uses_sanitized_standalone_repository(self) -> None:
        source = (ROOT / "slivin_harness" / "workspace.py").read_text(encoding="utf-8")
        for marker in (
            "_materialize_sanitized_benchmark_repo",
            'mode="benchmark_isolated"',
            "Historical benchmark requires result_mode=keep_worktree",
            "Sanitized benchmark repository unexpectedly retains refs",
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
