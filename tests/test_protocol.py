from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from slivin_harness.evaluator import EVALUATOR_SCHEMA
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
from task_runner import (
    build_implementation_prompt,
    validate_evaluation_artifact,
    validate_plan_artifact,
)


def valid_plan() -> dict:
    return {
        "protocol_version": PLANNER_PROTOCOL_VERSION,
        "status": "READY",
        "summary": "Root cause confirmed and the change is small.",
        "observed_behavior": ["The normal action disappears after token selection."],
        "expected_behavior": ["A current token selection remains explicit."],
        "root_cause": {
            "claim": "One reader only checks materialized row IDs.",
            "evidence": ["src/selection.js reads selectedRows only."],
            "confidence": "HIGH",
        },
        "change_plan": ["Unify selection resolution at the shared reader."],
        "preserve": ["Filter-only mode remains restricted."],
        "consumers_to_check": ["Matrix", "Distribution"],
        "risks": ["A stale token must not become authoritative."],
        "test_plan": ["Add current-token and filter-only regression cases."],
        "documentation": {
            "required": False,
            "paths": [],
            "reason": "The documented contract is restored, not changed.",
        },
        "likely_paths": ["src/selection.js", "tests/test_selection.js"],
        "unknowns": [],
    }


def valid_pass() -> dict:
    return {
        "protocol_version": EVALUATOR_PROTOCOL_VERSION,
        "status": "PASS",
        "summary": "The task is satisfied and checks cover the changed contract.",
        "task_satisfied": True,
        "changed_files_reviewed": ["src/selection.js", "tests/test_selection.js"],
        "checks_assessment": ["Unit and preservation tests passed."],
        "findings": [],
        "unverified": [],
        "replan_reason": "",
    }


class ProtocolContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="slivin-protocol-"))

    def test_protocol_versions_are_explicit(self) -> None:
        self.assertEqual(
            PLANNER_SCHEMA["properties"]["protocol_version"]["enum"],
            [PLANNER_PROTOCOL_VERSION],
        )
        self.assertEqual(
            EVALUATOR_SCHEMA["properties"]["protocol_version"]["enum"],
            [EVALUATOR_PROTOCOL_VERSION],
        )
        self.assertEqual(
            IMPLEMENTER_REPORT_SCHEMA["properties"]["protocol_version"]["enum"],
            [IMPLEMENTER_PROTOCOL_VERSION],
        )

    def test_compact_ready_plan_is_accepted(self) -> None:
        plan = valid_plan()
        validate_plan_artifact(plan, workspace=self.workspace)
        self.assertEqual(len(plan_fingerprint(plan)), 16)

    def test_ready_plan_can_keep_non_blocking_unknowns(self) -> None:
        plan = valid_plan()
        plan["unknowns"] = [
            "A non-blocking sibling runtime cannot be executed in this workspace."
        ]
        validate_plan_artifact(plan, workspace=self.workspace)

    def test_ready_plan_requires_root_cause_evidence(self) -> None:
        plan = valid_plan()
        plan["root_cause"]["evidence"] = []
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "PLANNER_ROOT_CAUSE_MISSING")

    def test_blocked_plan_requires_a_concrete_reason(self) -> None:
        plan = valid_plan()
        plan["status"] = "BLOCKED"
        plan["unknowns"] = []
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "PLANNER_STOP_WITHOUT_REASON")

    def test_unknown_plan_fields_are_rejected(self) -> None:
        plan = valid_plan()
        plan["release_obligations"] = ["CC-1"]
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace)
        self.assertEqual(ctx.exception.code, "UNKNOWN_FIELDS")

    def test_pass_is_mechanically_strict(self) -> None:
        validate_evaluation_artifact(valid_pass())

        evaluation = valid_pass()
        evaluation["unverified"] = [
            {
                "claim": "Backend stage invariant holds.",
                "reason": "No project runtime was available.",
                "required_evidence": "Run the backend contract test.",
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "Evaluator PASS is invalid"):
            validate_evaluation_artifact(evaluation)

    def test_findings_status_requires_a_finding(self) -> None:
        evaluation = valid_pass()
        evaluation.update({"status": "FINDINGS", "task_satisfied": False})
        with self.assertRaisesRegex(RuntimeError, "requires at least one finding"):
            validate_evaluation_artifact(evaluation)

    def test_implementation_handoff_is_compact_and_contains_owner_boundary(self) -> None:
        plan = valid_plan()
        contract = build_implementation_contract(plan, task_prompt="task")
        prompt = build_implementation_prompt(
            "task",
            plan,
            implementation_contract=contract,
            self_verify_command=["python", ".harness_tmp/self_verify.py"],
            toolchain={"project_python": "python"},
            allowed_paths=["src/"],
        )
        self.assertIn("BEGIN COMPACT PLAN CONTEXT", prompt)
        self.assertIn("BEGIN IMPLEMENTATION CONTRACT", prompt)
        self.assertIn(plan_fingerprint(plan), prompt)
        self.assertIn('"src/"', prompt)
        self.assertIn("SELF_VERIFY_COMMAND", prompt)
        self.assertNotIn("release_obligations", prompt)

    def test_safe_repo_relative_rejects_escape(self) -> None:
        self.assertEqual(safe_repo_relative("src/file.py"), "src/file.py")
        with self.assertRaises(ArtifactContractError):
            safe_repo_relative("../../secret")


if __name__ == "__main__":
    unittest.main()
