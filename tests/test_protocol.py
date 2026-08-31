from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from slivin_harness.evaluator import EVALUATOR_SCHEMA
from slivin_harness.phase6 import BLIND_AUDIT_VERSION
from slivin_harness.implementer import (
    IMPLEMENTER_PROTOCOL_VERSION,
    IMPLEMENTER_REPORT_SCHEMA,
    build_implementation_contract,
)
from slivin_harness.planner import PLANNER_SCHEMA
from slivin_harness.protocol import (
    ArtifactContractError,
    EVALUATOR_PROTOCOL_VERSION,
    PLANNER_PROTOCOL_VERSION,
    plan_fingerprint,
    safe_repo_relative,
)
from slivin_harness.task_contract import (
    TASK_CONTRACT_VERSION,
    build_task_contract,
    validate_task_contract,
)
from slivin_harness.verification import (
    VERIFICATION_PLAN_VERSION,
    compile_verification_plan,
)
from task_runner import (
    build_implementation_prompt,
    validate_evaluation_artifact,
    validate_plan_artifact,
)


def proof(claim: str, *, level: str = "LOCAL_DETERMINISTIC", capabilities=None) -> dict:
    return {
        "claim": claim,
        "level": level,
        "capabilities": list(capabilities or []),
    }


def valid_task_contract(raw: str = "Change target.txt from before to after. Do not change other files.") -> dict:
    normalized = {
        "protocol_version": TASK_CONTRACT_VERSION,
        "status": "READY",
        "summary": "Change the target value and preserve all other files.",
        "explicit_intent": [
            {"claim": "Change target.txt from before to after.", "source_text": "Change target.txt from before to after."}
        ],
        "explicit_acceptance": [
            {"claim": "target.txt contains after.", "source_text": "Change target.txt from before to after."}
        ],
        "explicit_preservation": [
            {"claim": "Do not change other files.", "source_text": "Do not change other files."}
        ],
        "explicit_forbidden": [],
        "owner_boundaries": [],
        "non_goals": [],
        "ambiguities": [],
        "reason": "",
    }
    return build_task_contract(raw_request=raw, normalized=normalized)


def valid_plan() -> dict:
    return {
        "protocol_version": PLANNER_PROTOCOL_VERSION,
        "status": "READY",
        "summary": "The old fixture value is the complete root cause.",
        "task_contract_alignment": {
            "status": "ALIGNED",
            "evidence": ["The raw request and explicit Task Contract both require after."],
            "reason": "",
        },
        "characterization": {
            "observed_behavior": ["target.txt contains before."],
            "existing_contract": ["The task requires target.txt to contain after."],
            "evidence": ["target.txt is tracked and contains before."],
        },
        "diagnosis": {
            "kind": "BUG",
            "root_cause": {
                "claim": "The tracked fixture contains the old value.",
                "evidence": ["target.txt contains before."],
                "confidence": "HIGH",
            },
            "extension_point": {"claim": "", "evidence": [], "confidence": "LOW"},
            "design_constraints": ["Preserve all other files."],
            "high_level_approach": ["Replace only the old fixture value."],
        },
        "assumptions": [],
        "technical_contract": {
            "technical_acceptance": ["target.txt contains exactly after."],
            "derived_preservation": ["No sibling file is changed."],
        },
        "affected_consumers": [
            {
                "name": "Target fixture consumer",
                "why_affected": "It reads target.txt.",
                "must_verify": "It observes after.",
                "required_proof": proof("The configured target-content check passes."),
            }
        ],
        "state_model": {
            "applicable": False,
            "representations": [],
            "authority": [],
            "lifecycle": [],
            "boundaries": [],
            "required_proof": proof("No state model is required."),
        },
        "risks": [
            {
                "condition": "Another file is changed.",
                "failure_mode": "The explicit preservation contract is violated.",
                "required_proof": proof("Only target.txt changes."),
            }
        ],
        "evidence_plan": {
            "regression": [proof("target.txt contains after.")],
            "preservation": [proof("Only target.txt changes.")],
            "consumers": [proof("The target fixture consumer observes after.")],
            "boundaries": [],
        },
        "documentation": {
            "required": False,
            "reason": "No documented product contract changes.",
            "required_proof": proof("No documentation update is required."),
        },
        "owner_boundary_assessment": {
            "compatible": True,
            "reason": "The task can be completed inside target.txt.",
        },
        "unknowns": [],
    }


def evaluator_finding(finding_id: str = "BLIND-1") -> dict:
    return {
        "finding_id": finding_id,
        "severity": "MEDIUM",
        "category": "EVIDENCE",
        "title": "Regression evidence is incomplete",
        "evidence": ["The configured test does not execute the production reader."],
        "failure_mode": "A false-green test could accept a broken candidate.",
        "required_action": "Add evidence through the real production path.",
        "required_proof": proof("The production reader observes the changed value."),
    }


def valid_blind_audit(*, findings=None) -> dict:
    return {
        "protocol_version": BLIND_AUDIT_VERSION,
        "summary": "Independent candidate audit completed.",
        "findings": list(findings or []),
        "advisories": [],
    }


def valid_pass(*, blind_audit=None) -> dict:
    audit = blind_audit or valid_blind_audit()
    return {
        "protocol_version": EVALUATOR_PROTOCOL_VERSION,
        "status": "PASS",
        "summary": "The task is satisfied and checks cover the changed contract.",
        "blind_finding_dispositions": [
            {
                "finding_id": item["finding_id"],
                "disposition": "DISMISSED_WITH_EVIDENCE",
                "evidence": ["Repository evidence disproves the original reachability claim."],
            }
            for item in audit["findings"]
        ],
        "findings": [],
        "reason": "",
    }


class ProtocolContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="slivin-protocol-"))
        self.task_contract = valid_task_contract()

    def test_protocol_versions_are_explicit(self) -> None:
        self.assertEqual(PLANNER_SCHEMA["properties"]["protocol_version"]["enum"], [PLANNER_PROTOCOL_VERSION])
        self.assertEqual(EVALUATOR_SCHEMA["properties"]["protocol_version"]["enum"], [EVALUATOR_PROTOCOL_VERSION])
        self.assertEqual(IMPLEMENTER_REPORT_SCHEMA["properties"]["protocol_version"]["enum"], [IMPLEMENTER_PROTOCOL_VERSION])
        self.assertEqual(TASK_CONTRACT_VERSION, "task-contract.v1")
        self.assertEqual(VERIFICATION_PLAN_VERSION, "verification-plan.v1")

    def test_task_contract_requires_exact_source_text(self) -> None:
        validate_task_contract(self.task_contract)
        broken = valid_task_contract()
        broken["explicit_acceptance"][0]["source_text"] = "not in raw"
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_task_contract(broken)
        self.assertEqual(ctx.exception.code, "TASK_CONTRACT_SOURCE_MISMATCH")

    def test_compact_ready_plan_is_accepted(self) -> None:
        plan = valid_plan()
        validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(len(plan_fingerprint(plan)), 16)

    def test_ready_plan_can_keep_non_blocking_unknowns(self) -> None:
        plan = valid_plan()
        plan["unknowns"] = [{"kind": "NON_BLOCKING", "claim": "Browser unavailable", "reason": "Local proof is sufficient."}]
        validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)

    def test_ready_plan_requires_root_cause_evidence(self) -> None:
        plan = valid_plan()
        plan["diagnosis"]["root_cause"]["evidence"] = []
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(ctx.exception.code, "PLANNER_DIAGNOSIS_MISSING")

    def test_feature_uses_extension_point_instead_of_root_cause(self) -> None:
        plan = valid_plan()
        plan["diagnosis"].update({
            "kind": "FEATURE",
            "root_cause": {"claim": "", "evidence": [], "confidence": "LOW"},
            "extension_point": {"claim": "Existing target loader", "evidence": ["loader reads target.txt"], "confidence": "HIGH"},
        })
        validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)

    def test_compatibility_narrowing_assumption_requires_high_confidence(self) -> None:
        plan = valid_plan()
        plan["assumptions"] = [{
            "claim": "Legacy state never occurs",
            "evidence": ["Only the new writer was found"],
            "confidence": "MEDIUM",
            "narrows_compatibility": True,
            "compatibility_impact": "Legacy state would be rejected.",
        }]
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(ctx.exception.code, "UNSAFE_COMPATIBILITY_ASSUMPTION")

    def test_blocked_plan_requires_a_concrete_reason(self) -> None:
        plan = valid_plan()
        plan["status"] = "BLOCKED"
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(ctx.exception.code, "PLANNER_STOP_WITHOUT_REASON")

    def test_unknown_plan_fields_are_rejected(self) -> None:
        plan = valid_plan()
        plan["release_obligations"] = ["CC-1"]
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(ctx.exception.code, "UNKNOWN_FIELDS")

    def test_pass_is_mechanically_strict(self) -> None:
        audit = valid_blind_audit()
        validate_evaluation_artifact(valid_pass(blind_audit=audit), blind_audit=audit)

        finding = evaluator_finding()
        audit = valid_blind_audit(findings=[finding])
        evaluation = valid_pass(blind_audit=audit)
        evaluation["blind_finding_dispositions"][0]["disposition"] = "RETAINED"
        evaluation["findings"] = [finding]
        with self.assertRaisesRegex(RuntimeError, "PASS requires no findings"):
            validate_evaluation_artifact(evaluation, blind_audit=audit)

    def test_findings_status_requires_a_finding(self) -> None:
        audit = valid_blind_audit()
        evaluation = valid_pass(blind_audit=audit)
        evaluation["status"] = "FINDINGS"
        with self.assertRaisesRegex(RuntimeError, "requires at least one finding"):
            validate_evaluation_artifact(evaluation, blind_audit=audit)

    def test_phase_b_must_disposition_every_blind_finding(self) -> None:
        audit = valid_blind_audit(findings=[evaluator_finding()])
        evaluation = valid_pass(blind_audit=audit)
        evaluation["blind_finding_dispositions"] = []
        with self.assertRaisesRegex(RuntimeError, "disposition every"):
            validate_evaluation_artifact(evaluation, blind_audit=audit)

    def test_implementation_handoff_contains_task_contract_and_verification_plan(self) -> None:
        plan = valid_plan()
        contract = build_implementation_contract(plan, task_contract=self.task_contract)
        verification = compile_verification_plan(contract, project_checks=[{"name": "Target"}])
        prompt = build_implementation_prompt(
            self.task_contract["raw_user_request"],
            plan,
            task_contract=self.task_contract,
            implementation_contract=contract,
            verification_plan=verification,
            self_verify_command=["python", ".harness_tmp/self_verify.py"],
            toolchain={"project_python": "python"},
            allowed_paths=["target.txt"],
        )
        self.assertIn("BEGIN USER TASK CONTRACT", prompt)
        self.assertIn("BEGIN COMPACT PLAN CONTEXT", prompt)
        self.assertIn("BEGIN IMPLEMENTATION CONTRACT", prompt)
        self.assertIn("BEGIN VERIFICATION PLAN", prompt)
        self.assertIn(plan_fingerprint(plan), prompt)
        self.assertIn('"target.txt"', prompt)
        self.assertNotIn("release_obligations", prompt)

    def test_safe_repo_relative_rejects_escape(self) -> None:
        self.assertEqual(safe_repo_relative("src/file.py"), "src/file.py")
        with self.assertRaises(ArtifactContractError):
            safe_repo_relative("../../secret")


if __name__ == "__main__":
    unittest.main()
