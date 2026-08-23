from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from slivin_harness.evaluator import EVALUATOR_SCHEMA
from slivin_harness.impact import IMPACT_SCHEMA
from slivin_harness.protocol import (
    EVALUATOR_PROTOCOL_VERSION,
    IMPACT_PROTOCOL_VERSION,
    impact_fingerprint,
    impact_obligation_ids,
    impact_required_candidate_paths,
    impact_schema_for_plan,
    plan_fingerprint,
)
from task_runner import (
    build_impact_repair_prompt,
    missing_impact_candidate_paths,
    validate_evaluation_artifact,
    validate_impact_artifact,
)
from test_protocol import valid_plan


def valid_impact(plan: dict) -> dict:
    return {
        "protocol_version": IMPACT_PROTOCOL_VERSION,
        "plan_fingerprint": plan_fingerprint(plan),
        "status": "COMPLETE",
        "summary": "shared impact audited",
        "shared_change_detected": True,
        "items": [
            {
                "id": "IMP-1",
                "component": "shared helper",
                "consumer": "sibling consumer",
                "reader_paths": ["src/consumer.py"],
                "surfaces": ["eligibility", "payload"],
                "lifecycle_reachability": "normal action reaches this reader",
                "observed_reader_contract": "reads old representation",
                "required_contract": "must understand new representation",
                "disposition": "CHANGE_REQUIRED",
                "required_candidate_paths": [
                    "src/consumer.py",
                    "tests/test_consumer.py",
                ],
                "verification_paths": ["tests/test_consumer.py"],
                "evidence": ["src/consumer.py:10"],
                "confidence": "HIGH",
            },
            {
                "id": "IMP-2",
                "component": "shared helper",
                "consumer": "compatible sibling",
                "reader_paths": ["src/compatible.py"],
                "surfaces": ["count"],
                "lifecycle_reachability": "same shared helper call",
                "observed_reader_contract": "already token-aware",
                "required_contract": "preserve behavior",
                "disposition": "VERIFY_REQUIRED",
                "required_candidate_paths": [],
                "verification_paths": ["tests/test_compatible.py"],
                "evidence": ["src/compatible.py:20"],
                "confidence": "HIGH",
            },
        ],
        "completeness_evidence": ["repo-wide search for shared helper readers"],
        "unresolved": [],
    }


class SharedImpactProtocolTests(unittest.TestCase):
    def test_impact_schema_is_bound_to_plan_fingerprint(self) -> None:
        plan = valid_plan()
        schema = impact_schema_for_plan(IMPACT_SCHEMA, plan)
        self.assertEqual(
            schema["properties"]["plan_fingerprint"]["enum"],
            [plan_fingerprint(plan)],
        )

    def test_controller_derives_impact_ids_and_required_paths(self) -> None:
        plan = valid_plan()
        impact = valid_impact(plan)
        self.assertEqual(impact_obligation_ids(impact), ["IMP-1", "IMP-2"])
        self.assertEqual(
            impact_required_candidate_paths(impact),
            ["src/consumer.py", "tests/test_consumer.py"],
        )
        self.assertEqual(
            missing_impact_candidate_paths(plan, impact),
            ["src/consumer.py", "tests/test_consumer.py"],
        )

    def test_change_required_without_paths_is_rejected(self) -> None:
        plan = valid_plan()
        impact = valid_impact(plan)
        impact["items"][0]["required_candidate_paths"] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception) as ctx:
                validate_impact_artifact(impact, plan=plan, workspace=Path(tmp))
        self.assertIn("CHANGE_REQUIRED", str(ctx.exception))


    def test_impact_prose_id_is_rejected(self) -> None:
        plan = valid_plan()
        impact = valid_impact(plan)
        impact["items"][0]["id"] = "IMP-1 — sibling"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                validate_impact_artifact(impact, plan=plan, workspace=Path(tmp))

    def test_shared_change_cannot_return_empty_inventory(self) -> None:
        plan = valid_plan()
        impact = valid_impact(plan)
        impact["items"] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                validate_impact_artifact(impact, plan=plan, workspace=Path(tmp))

    def test_impact_repair_handoff_is_bound_to_plan_and_impact(self) -> None:
        plan = valid_plan()
        impact = valid_impact(plan)
        prompt = build_impact_repair_prompt(
            impact,
            plan=plan,
            baseline_snapshot={"head_sha": "abc", "files": {}},
        )
        self.assertIn(f"PLAN_FINGERPRINT: {plan_fingerprint(plan)}", prompt)
        self.assertIn(f"IMPACT_FINGERPRINT: {impact_fingerprint(impact)}", prompt)
        self.assertIn("IMP-1", prompt)

    def test_evaluator_pass_requires_exact_impact_ledger(self) -> None:
        plan = valid_plan()
        impact = valid_impact(plan)
        evaluation = {
            "protocol_version": EVALUATOR_PROTOCOL_VERSION,
            "plan_fingerprint": plan_fingerprint(plan),
            "impact_fingerprint": impact_fingerprint(impact),
            "status": "PASS",
            "summary": "pass",
            "changed_contract": "contract",
            "planner_assumption_audit": [
                {"id": "A-1", "status": "CONFIRMED", "evidence": "code"}
            ],
            "obligation_assessment": [
                {
                    "id": item_id,
                    "status": "PASS",
                    "evidence_type": "code_trace",
                    "evidence": "code",
                }
                for item_id in (
                    "CC-1", "CONS-1", "LIFE-1", "REP-1",
                    "AUTH-1", "PRES-1", "TEST-1", "INT-1",
                )
            ],
            "impact_assessment": [
                {
                    "id": "IMP-1",
                    "status": "PASS",
                    "evidence_type": "test",
                    "evidence": "verified",
                },
                {
                    "id": "IMP-2",
                    "status": "PASS",
                    "evidence_type": "code_trace",
                    "evidence": "verified",
                },
            ],
            "shared_changes": [],
            "plan_findings": [],
            "findings": [],
            "unverified_risks": [],
        }
        validate_evaluation_artifact(
            evaluation,
            plan=plan,
            impact_audit=impact,
            risk="medium",
        )

        evaluation["impact_assessment"][1]["status"] = "UNVERIFIED"
        with self.assertRaises(RuntimeError):
            validate_evaluation_artifact(
                evaluation,
                plan=plan,
                impact_audit=impact,
                risk="medium",
            )


if __name__ == "__main__":
    unittest.main()
