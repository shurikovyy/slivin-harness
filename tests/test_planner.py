from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from slivin_harness.implementer import build_implementation_contract
from slivin_harness.planner import (
    PlannerCapabilityInfeasible,
    manifest_repair_evidence,
    planner_capability_gaps,
    planner_required_capabilities,
    run_planner,
)
from slivin_harness.verification import available_capabilities
from test_protocol import valid_plan, valid_task_contract


class _FakeCodex:
    def __init__(self, plans: list[dict]) -> None:
        self.plans = list(plans)
        self.started = 0
        self.turns: list[dict] = []

    def start_thread(self, **kwargs) -> str:
        self.started += 1
        callback = kwargs.get("on_started")
        if callback:
            callback({"id": "planner-thread"})
        return "planner-thread"

    def run_turn(self, **kwargs) -> str:
        self.turns.append(dict(kwargs))
        return json.dumps(self.plans.pop(0), ensure_ascii=False)


def _plan_with_capability(capability: str, *, level: str = "LOCAL_DETERMINISTIC") -> dict:
    plan = valid_plan()
    plan["evidence_plan"]["regression"][0]["capabilities"] = [capability]
    plan["evidence_plan"]["regression"][0]["level"] = level
    return plan


class PlannerCapabilityNegotiationTests(unittest.TestCase):
    def _run(self, codex: _FakeCodex, available: list[str]) -> dict:
        return run_planner(
            codex,  # type: ignore[arg-type]
            workspace=Path.cwd(),
            task_prompt="Change target.txt.",
            task_contract=valid_task_contract(),
            preflight={"status": "READY"},
            owner_allowed_paths=["target.txt"],
            available_verification_capabilities=available,
            manifest_repair_evidence=[
                {
                    "check_name": "Node regression",
                    "command_family": "jest",
                    "verified_tool_capabilities": ["JEST", "NODE"],
                }
            ],
        )

    def test_planner_receives_authoritative_capabilities_and_repair_evidence(self) -> None:
        codex = _FakeCodex([valid_plan()])
        self._run(codex, ["DOCS_SYNC", "GIT", "JEST", "NODE"])
        prompt = codex.turns[0]["prompt"]
        self.assertIn("AVAILABLE_VERIFICATION_CAPABILITIES", prompt)
        self.assertIn('"DOCS_SYNC"', prompt)
        self.assertIn("MANIFEST_REPAIR_EVIDENCE", prompt)
        self.assertIn("Node regression", prompt)

    def test_one_corrective_turn_reuses_thread_and_accepts_feasible_plan(self) -> None:
        first = _plan_with_capability("PROJECT_PYTHON")
        corrected = _plan_with_capability("JEST")
        corrected["evidence_plan"]["regression"][0]["capabilities"] = [
            "NODE",
            "JEST",
        ]
        codex = _FakeCodex([first, corrected])
        result = self._run(codex, ["DOCS_SYNC", "GIT", "JEST", "NODE"])
        self.assertEqual(result, corrected)
        self.assertEqual(codex.started, 1)
        self.assertEqual(len(codex.turns), 2)
        self.assertEqual(
            {turn["thread_id"] for turn in codex.turns}, {"planner-thread"}
        )
        self.assertIn("CAPABILITY FEASIBILITY CORRECTION", codex.turns[1]["prompt"])
        self.assertIn("PROJECT_PYTHON", codex.turns[1]["prompt"])
        contract = build_implementation_contract(
            result, task_contract=valid_task_contract()
        )
        self.assertNotIn("PROJECT_PYTHON", json.dumps(contract))

    def test_second_infeasible_ready_plan_is_controlled(self) -> None:
        codex = _FakeCodex(
            [
                _plan_with_capability("PROJECT_PYTHON"),
                _plan_with_capability("PROJECT_PYTHON"),
            ]
        )
        with self.assertRaises(PlannerCapabilityInfeasible) as raised:
            self._run(codex, ["DOCS_SYNC", "GIT", "JEST", "NODE"])
        self.assertEqual(
            raised.exception.unavailable_capabilities, ("PROJECT_PYTHON",)
        )
        self.assertEqual(len(codex.turns), 2)

    def test_corrective_blocked_plan_is_preserved(self) -> None:
        blocked = copy.deepcopy(valid_plan())
        blocked["status"] = "BLOCKED"
        blocked["unknowns"] = [
            {
                "kind": "BLOCKING",
                "claim": "The required executor is unavailable.",
                "reason": "No available capability proves the required behavior.",
            }
        ]
        codex = _FakeCodex([_plan_with_capability("PROJECT_PYTHON"), blocked])
        result = self._run(codex, ["DOCS_SYNC", "GIT", "JEST", "NODE"])
        self.assertEqual(result["status"], "BLOCKED")

    def test_explicit_and_implicit_capabilities_are_collected(self) -> None:
        cases = [
            ("LIVE_LOCAL", "LIVE_LOCAL_RUNTIME"),
            ("TEST_EXTERNAL", "TEST_EXTERNAL_RUNTIME"),
            ("PROD_OBSERVE", "PROD_OBSERVE_RUNTIME"),
        ]
        for level, implicit in cases:
            with self.subTest(level=level):
                plan = _plan_with_capability("BROWSER_DOM", level=level)
                required = planner_required_capabilities(plan)
                self.assertIn("BROWSER_DOM", required)
                self.assertIn(implicit, required)
                self.assertEqual(
                    planner_capability_gaps(plan, available=["BROWSER_DOM"]),
                    [implicit],
                )

    def test_configured_tool_claim_without_probe_is_not_available(self) -> None:
        available = available_capabilities(
            toolchain={"project_python": "missing-python"},
            configured=["PROJECT_PYTHON", "JEST"],
            verified_tool_capabilities=[],
        )
        self.assertNotIn("PROJECT_PYTHON", available)
        self.assertNotIn("JEST", available)

    def test_manifest_repair_evidence_excludes_heldout_and_unverified_tools(self) -> None:
        evidence = manifest_repair_evidence(
            {
                "verified_capabilities": ["NODE"],
                "checks": [
                    {
                        "name": "Repair Jest",
                        "feedback": "repair",
                        "command_family": "jest",
                    },
                    {
                        "name": "Hidden",
                        "feedback": "heldout",
                        "command_family": "node",
                    },
                ],
            }
        )
        self.assertEqual(
            evidence,
            [
                {
                    "check_name": "Repair Jest",
                    "command_family": "jest",
                    "verified_tool_capabilities": ["NODE"],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
