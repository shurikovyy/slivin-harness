from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from slivin_harness.implementer import build_implementation_contract
from slivin_harness.planner import (
    PlannerCapabilityInfeasible,
    manifest_repair_evidence,
    planner_capability_gaps,
    planner_required_capabilities,
    run_planner,
    validate_plan_artifact,
)
from slivin_harness.protocol import ArtifactContractError, ArtifactFailureKind
from slivin_harness.report_recovery import ReportRecoveryStop
from slivin_harness.verification import available_capabilities
from test_protocol import valid_plan, valid_task_contract, write_plan_evidence
from test_planner_impact_closure import synthetic_task_contract


class _FakeCodex:
    def __init__(self, plans: list[dict]) -> None:
        self.plans = list(plans)
        self.started = 0
        self.turns: list[dict] = []

    def start_thread(self, **kwargs) -> str:
        assert kwargs["execution_role"].value == "planner"
        assert "sandbox" not in kwargs
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
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="slivin-planner-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        write_plan_evidence(self.workspace)

    def _run(self, codex: _FakeCodex, available: list[str]) -> dict:
        return run_planner(
            codex,  # type: ignore[arg-type]
            workspace=self.workspace,
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
        self.assertIn("entrypoint/config loading in Controller context", prompt)
        self.assertIn("not evidence that the executable is absent", prompt)

    def test_local_symbol_correction_preserves_semantics(self) -> None:
        invalid, corrected = valid_plan(), valid_plan()
        invalid["impact_closure"]["in_scope_consumers"][0]["symbols"] = ["public reader"]
        codex = _FakeCodex([invalid, corrected])
        result = self._run(codex, ["DOCS_SYNC", "GIT"])
        self.assertEqual(result, corrected)
        self.assertEqual(len(codex.turns), 2)
        self.assertIn("REPORT-ONLY CORRECTION", codex.turns[1]["prompt"])
        self.assertIn("impact_closure.in_scope_consumers[0].symbols", codex.turns[1]["prompt"])
        self.assertNotIn("CAPABILITY FEASIBILITY CORRECTION", codex.turns[1]["prompt"])

    def test_local_evidence_path_and_type_errors_are_correctable(self) -> None:
        variants = (
            ("empty evidence", lambda plan: plan["impact_closure"]["in_scope_consumers"][0].update(evidence=[])),
            ("missing path", lambda plan: plan["impact_closure"]["in_scope_consumers"][0].update(paths=["missing.py"])),
            ("wrong list type", lambda plan: plan["impact_closure"]["in_scope_consumers"][0].update(paths="reader.py")),
            ("missing local field", lambda plan: plan["impact_closure"]["in_scope_consumers"][0].pop("symbols")),
        )
        for label, mutate in variants:
            with self.subTest(label=label):
                invalid, corrected = valid_plan(), valid_plan()
                mutate(invalid)
                codex = _FakeCodex([invalid, corrected])
                self.assertEqual(self._run(codex, ["DOCS_SYNC", "GIT"]), corrected)
                self.assertEqual(len(codex.turns), 2)

    def test_local_correction_no_progress_and_exhaustion_are_bounded(self) -> None:
        invalid = valid_plan()
        invalid["impact_closure"]["in_scope_consumers"][0]["symbols"] = ["public reader"]
        with self.assertRaisesRegex(ReportRecoveryStop, "NO_PROGRESS"):
            self._run(_FakeCodex([invalid, copy.deepcopy(invalid)]), ["DOCS_SYNC", "GIT"])

        second = valid_plan()
        second["impact_closure"]["in_scope_consumers"][0]["symbols"] = "read_target"
        third = valid_plan()
        third["impact_closure"]["in_scope_consumers"][0]["symbols"] = []
        with self.assertRaisesRegex(ReportRecoveryStop, "EXHAUSTED"):
            self._run(_FakeCodex([invalid, second, third]), ["DOCS_SYNC", "GIT"])

    def test_local_correction_rejects_planner_semantic_mutation(self) -> None:
        invalid, mutated = valid_plan(), valid_plan()
        invalid["impact_closure"]["in_scope_consumers"][0]["symbols"] = ["public reader"]
        mutated["diagnosis"]["root_cause"]["claim"] = "A different root cause"
        with self.assertRaisesRegex(ReportRecoveryStop, "CHANGED_CLAIMS"):
            self._run(_FakeCodex([invalid, mutated]), ["DOCS_SYNC", "GIT"])

    def test_local_admission_precedes_separate_capability_correction(self) -> None:
        invalid = _plan_with_capability("PROJECT_PYTHON")
        invalid["impact_closure"]["in_scope_consumers"][0]["symbols"] = ["public reader"]
        locally_corrected = _plan_with_capability("PROJECT_PYTHON")
        feasible = valid_plan()
        codex = _FakeCodex([invalid, locally_corrected, feasible])
        self.assertEqual(self._run(codex, ["DOCS_SYNC", "GIT"]), feasible)
        self.assertEqual(len(codex.turns), 3)
        self.assertIn("REPORT-ONLY CORRECTION", codex.turns[1]["prompt"])
        self.assertIn("CAPABILITY FEASIBILITY CORRECTION", codex.turns[2]["prompt"])

    def test_artifact_failure_taxonomy_distinguishes_local_semantic_and_integrity(self) -> None:
        cases = []
        local = valid_plan()
        local["impact_closure"]["in_scope_consumers"][0]["symbols"] = ["public reader"]
        cases.append((local, ArtifactFailureKind.LOCAL_WIRE_ERROR))
        semantic = valid_plan()
        semantic["impact_closure"]["changed_contracts"][0]["after"] = (
            semantic["impact_closure"]["changed_contracts"][0]["before"]
        )
        cases.append((semantic, ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT))
        integrity = valid_plan()
        integrity["impact_closure"]["changed_contracts"][0]["evidence_paths"] = ["../outside.py"]
        cases.append((integrity, ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE))
        for plan, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(ArtifactContractError) as raised:
                validate_plan_artifact(
                    plan, workspace=self.workspace, task_contract=valid_task_contract(),
                    owner_allowed_paths=["target.txt"],
                )
            self.assertIs(raised.exception.failure_kind, expected)

    def test_captured_qe2_generic_symbol_replays_through_planner_admission(self) -> None:
        fixture = json.loads((Path(__file__).parent / "fixtures/model_artifact_admission/qe2_expiry_2.json").read_text(encoding="utf-8"))
        observed = fixture["observed_legacy_shape"]
        self.assertEqual((observed["failing_group"], observed["failing_row_index"], observed["failing_field"]),
                         ("not_affected_consumers", 0, "symbols"))
        invalid = fixture["wire_artifact"]
        self.assertEqual(invalid["impact_closure"]["not_affected_consumers"][0]["symbols"], observed["invalid_symbols"])
        corrected = copy.deepcopy(invalid)
        corrected["impact_closure"]["not_affected_consumers"][0]["symbols"] = [fixture["corrected_symbol"]]
        for path in ("state.py", "reader_a.py", "reader_b.py", "sibling.py", "metrics.py"):
            (self.workspace / path).write_text("def title(): return 'synthetic'\n", encoding="utf-8")
        codex = _FakeCodex([invalid, corrected])
        result = run_planner(
            codex, workspace=self.workspace,
            task_prompt="Expired entries must not be returned.",
            task_contract=synthetic_task_contract(), preflight={"status": "READY"},
            owner_allowed_paths=[], manifest_repair_evidence=[],
            available_verification_capabilities=["DOCS_SYNC", "GIT", "JEST", "NODE"],
        )
        self.assertEqual(result, corrected)
        self.assertEqual(planner_required_capabilities(result), {"JEST", "NODE"})
        self.assertEqual(len(codex.turns), 2)
        diagnostic = json.loads(codex.turns[1]["prompt"].splitlines()[-1])
        self.assertEqual(diagnostic["code"], fixture["expected_route"])
        self.assertEqual(diagnostic["allowed_fields"], ["impact_closure.not_affected_consumers[0].symbols"])
        self.assertNotIn("CAPABILITY FEASIBILITY CORRECTION", codex.turns[1]["prompt"])

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

    def test_corrective_turn_updates_both_copies_of_consumer_proof(self) -> None:
        first, corrected = valid_plan(), valid_plan()
        for plan, capabilities in ((first, ["PROJECT_PYTHON"]), (corrected, ["JEST", "NODE"])):
            plan["impact_closure"]["in_scope_consumers"][0]["required_proof"]["capabilities"] = list(reversed(capabilities))
        codex = _FakeCodex([first, corrected])
        result = self._run(codex, ["JEST", "NODE"])
        self.assertEqual(result, corrected)
        self.assertEqual(len(codex.turns), 2)
        contract = build_implementation_contract(result, task_contract=valid_task_contract())
        self.assertNotIn("PROJECT_PYTHON", json.dumps(contract))
        consumer = next(row for row in contract["items"] if row["type"] == "consumer")
        self.assertEqual(consumer["required_proof"]["profiles"][0]["capabilities"], ["JEST", "NODE"])

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
