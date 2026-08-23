from __future__ import annotations

import copy
import unittest

from slivin_harness.evaluator import EVALUATOR_SCHEMA
from slivin_harness.planner import PLANNER_SCHEMA
from slivin_harness.protocol import (
    ArtifactContractError,
    EVALUATOR_PROTOCOL_VERSION,
    PLANNER_PROTOCOL_VERSION,
    evaluator_schema_for_plan,
    plan_fingerprint,
    required_obligation_ids,
)
from task_runner import (
    build_evaluator_repair_prompt,
    build_implementation_prompt,
    validate_evaluation_artifact,
    validate_plan_artifact,
)


def valid_plan() -> dict:
    return {
        "protocol_version": PLANNER_PROTOCOL_VERSION,
        "status": "READY",
        "summary": "summary",
        "reproduction": ["repro"],
        "relevant_state": [],
        "current_contract": [
            {
                "id": "CC-1",
                "state": "CONFIRMED",
                "behavior": "critical contract",
                "evidence": ["code"],
                "source": "code",
                "compatibility_notes": "",
                "release_critical": True,
            },
            {
                "id": "CC-2",
                "state": "CONFIRMED",
                "behavior": "advisory contract",
                "evidence": ["code"],
                "source": "code",
                "compatibility_notes": "",
                "release_critical": False,
            },
        ],
        "assumptions": [
            {
                "id": "A-1",
                "claim": "assumption",
                "evidence": ["code"],
                "confidence": "HIGH",
                "narrows_existing_behavior": False,
            }
        ],
        "root_cause": {
            "hypothesis": "cause",
            "evidence": ["code"],
            "confidence": "HIGH",
        },
        "local_owner": "owner",
        "shared_components": [],
        "affected_consumers": [
            {"id": "CONS-1", "consumer": "c", "why_affected": "why"}
        ],
        "state_lifecycle_audit": [
            {
                "id": "LIFE-1",
                "mechanism": "m",
                "role": "USER_INTENT",
                "owner": "o",
                "scope": "s",
                "created_when": "c",
                "valid_while": "v",
                "invalidated_when": "i",
                "authority_domains": ["d"],
                "frozen_after_action_start": False,
                "supersession_rule": "r",
                "must_not_override": [],
                "evidence": ["code"],
                "confidence": "HIGH",
            }
        ],
        "decision_escalations": [],
        "representation_consumer_audit": [
            {
                "id": "REP-1",
                "logical_state": "s",
                "representations": [
                    {"name": "r", "semantics": "s", "evidence": ["code"]}
                ],
                "change_or_extension": "c",
                "consumers": [
                    {
                        "consumer": "c",
                        "local_readers": ["r"],
                        "expected_behavior": "e",
                        "risk": "r",
                        "evidence": ["code"],
                    }
                ],
            }
        ],
        "authority_matrix": [
            {
                "id": "AUTH-1",
                "coexisting_states": ["LIFE-1"],
                "authority_rule": "r",
                "surfaces": ["payload"],
                "expected_consistency": "c",
                "evidence": ["LIFE-1"],
            }
        ],
        "preservation_contract": [
            {"id": "PRES-1", "claim": "preserve"}
        ],
        "interaction_matrix": [
            {
                "id": "INT-1",
                "scenario": "critical interaction",
                "expected": "expected",
                "risk": "risk",
                "release_critical": True,
            },
            {
                "id": "INT-2",
                "scenario": "advisory interaction",
                "expected": "expected",
                "risk": "risk",
                "release_critical": False,
            },
        ],
        "test_matrix": [
            {
                "id": "TEST-1",
                "scenario": "test",
                "expected": "expected",
                "test_level": "unit",
            }
        ],
        "candidate_paths": ["src/file.py"],
        "proposed_change_surface": ["src/file.py"],
        "unknowns": [],
    }


class ProtocolContractTests(unittest.TestCase):
    def test_planner_schema_forbids_free_form_release_obligation_list(self) -> None:
        properties = PLANNER_SCHEMA["properties"]
        self.assertNotIn("release_obligations", properties)
        self.assertNotIn("release_obligations", PLANNER_SCHEMA["required"])
        self.assertEqual(
            properties["current_contract"]["items"]["properties"]["id"],
            {"type": "string"},
        )
        self.assertIn(
            "release_critical",
            properties["current_contract"]["items"]["required"],
        )
        self.assertIn(
            "release_critical",
            properties["interaction_matrix"]["items"]["required"],
        )

    def test_controller_derives_exact_obligation_ids_without_restatement(self) -> None:
        plan = valid_plan()
        validate_plan_artifact(plan)
        self.assertEqual(
            required_obligation_ids(plan),
            [
                "CC-1",
                "CONS-1",
                "LIFE-1",
                "REP-1",
                "AUTH-1",
                "PRES-1",
                "TEST-1",
                "INT-1",
            ],
        )
        self.assertNotIn("CC-2", required_obligation_ids(plan))
        self.assertNotIn("INT-2", required_obligation_ids(plan))

    def test_controller_rejects_legacy_release_obligations_field(self) -> None:
        plan = valid_plan()
        plan["release_obligations"] = ["CC-1 — preserve Matrix"]
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan)
        self.assertEqual(
            ctx.exception.code,
            "PLANNER_FORBIDDEN_RELEASE_OBLIGATIONS_FIELD",
        )

    def test_controller_rejects_prose_in_id_even_if_schema_is_bypassed(self) -> None:
        plan = valid_plan()
        plan["current_contract"][0]["id"] = "CC-1 — preserve Matrix"
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan)
        self.assertEqual(ctx.exception.code, "PLANNER_ID_FORMAT_INVALID")
        self.assertEqual(ctx.exception.field, "current_contract[].id")

    def test_controller_rejects_grouped_id_even_if_schema_is_bypassed(self) -> None:
        plan = valid_plan()
        plan["test_matrix"][0]["id"] = "TEST-1, TEST-2"
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan)
        self.assertEqual(ctx.exception.code, "PLANNER_ID_FORMAT_INVALID")

    def test_evaluator_schema_is_bound_to_exact_controller_ids(self) -> None:
        plan = valid_plan()
        schema = evaluator_schema_for_plan(EVALUATOR_SCHEMA, plan)
        obligations = required_obligation_ids(plan)
        self.assertEqual(
            schema["properties"]["plan_fingerprint"]["enum"],
            [plan_fingerprint(plan)],
        )
        obligation_array = schema["properties"]["obligation_assessment"]
        self.assertEqual(
            obligation_array["items"]["properties"]["id"]["enum"],
            obligations,
        )
        assumptions = schema["properties"]["planner_assumption_audit"]
        self.assertEqual(
            assumptions["items"]["properties"]["id"]["enum"],
            ["A-1"],
        )



    def test_implementation_handoff_contains_controller_plan_contract(self) -> None:
        plan = valid_plan()
        prompt = build_implementation_prompt(
            "task",
            plan,
            {},
            {"head_sha": "abc", "files": {}},
        )
        self.assertIn(f"PLAN_FINGERPRINT: {plan_fingerprint(plan)}", prompt)
        for obligation_id in required_obligation_ids(plan):
            self.assertIn(f'"{obligation_id}"', prompt)

    def test_evaluator_repair_handoff_contains_current_approved_plan(self) -> None:
        plan = valid_plan()
        prompt = build_evaluator_repair_prompt(
            {"status": "FINDINGS", "findings": []},
            plan=plan,
            baseline_snapshot={"head_sha": "abc", "files": {}},
        )
        self.assertIn(f"PLAN_FINGERPRINT: {plan_fingerprint(plan)}", prompt)
        self.assertIn("BEGIN CURRENT PLAN", prompt)
        self.assertIn("BEGIN CONTROLLER OBLIGATION IDS", prompt)

    def test_evaluator_verdict_is_bound_to_current_plan_fingerprint(self) -> None:
        plan = valid_plan()
        obligations = required_obligation_ids(plan)
        evaluation = {
            "protocol_version": EVALUATOR_PROTOCOL_VERSION,
            "plan_fingerprint": "wrong",
            "status": "FINDINGS",
            "summary": "summary",
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
                for item_id in obligations
            ],
            "shared_changes": [],
            "plan_findings": [],
            "findings": [],
            "unverified_risks": [],
        }
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_evaluation_artifact(evaluation, plan=plan, risk="medium")
        self.assertEqual(ctx.exception.code, "EVALUATOR_PLAN_FINGERPRINT_MISMATCH")

    def test_protocol_versions_are_explicit(self) -> None:
        self.assertEqual(
            PLANNER_SCHEMA["properties"]["protocol_version"]["enum"],
            [PLANNER_PROTOCOL_VERSION],
        )
        self.assertEqual(
            EVALUATOR_SCHEMA["properties"]["protocol_version"]["enum"],
            [EVALUATOR_PROTOCOL_VERSION],
        )


if __name__ == "__main__":
    unittest.main()
