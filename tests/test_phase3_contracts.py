from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from slivin_harness.implementer import build_implementation_contract
from slivin_harness.protocol import ArtifactContractError, stable_fingerprint
from slivin_harness.task_contract import (
    TASK_CONTRACT_VERSION,
    build_task_contract,
    run_task_contract_normalizer,
    validate_task_contract,
)
from slivin_harness.verification import (
    Capability,
    ProofLevel,
    available_capabilities,
    compile_verification_plan,
    merged_required_proof,
    required_capability_gaps,
    validate_verification_plan,
)


def ready_task_contract(raw: str = "Исправь кнопку. Остальное не ломай.") -> dict:
    normalized = {
        "protocol_version": TASK_CONTRACT_VERSION,
        "status": "READY",
        "summary": "Исправить кнопку без соседних регрессий.",
        "explicit_intent": [{"claim": "Исправить кнопку.", "source_text": "Исправь кнопку."}],
        "explicit_acceptance": [{"claim": "Кнопка исправлена.", "source_text": "Исправь кнопку."}],
        "explicit_preservation": [
            {"claim": "Остальное поведение сохраняется.", "source_text": "Остальное не ломай."}
        ],
        "explicit_forbidden": [],
        "owner_boundaries": [],
        "non_goals": [],
        "ambiguities": [],
        "reason": "",
    }
    return build_task_contract(raw_request=raw, normalized=normalized)


class PhaseThreeContractTests(unittest.TestCase):
    def test_ready_task_contract_preserves_raw_and_verbatim_sources(self) -> None:
        raw = "Исправь кнопку. Остальное не ломай."
        contract = ready_task_contract(raw)

        validate_task_contract(contract)
        self.assertEqual(contract["raw_user_request"], raw)
        self.assertEqual(contract["explicit_preservation"][0]["source_text"], "Остальное не ломай.")
        self.assertTrue(contract["fingerprint"])

    def test_normalizer_repairs_ready_artifact_that_mislabels_conditional_scope_as_ambiguity(self) -> None:
        raw = (
            "Explicit selection may use normal actions. "
            "Filter-only scope must not use normal actions."
        )
        invalid = {
            "protocol_version": TASK_CONTRACT_VERSION,
            "status": "READY",
            "summary": "Keep selection scopes distinct.",
            "explicit_intent": [
                {
                    "claim": "Explicit selection may use normal actions.",
                    "source_text": "Explicit selection may use normal actions.",
                }
            ],
            "explicit_acceptance": [
                {
                    "claim": "Explicit selection may use normal actions.",
                    "source_text": "Explicit selection may use normal actions.",
                }
            ],
            "explicit_preservation": [
                {
                    "claim": "Filter-only scope must not use normal actions.",
                    "source_text": "Filter-only scope must not use normal actions.",
                }
            ],
            "explicit_forbidden": [],
            "owner_boundaries": [],
            "non_goals": [],
            "ambiguities": [
                {
                    "claim": "The technical distinction between scopes needs repository analysis.",
                    "source_text": "Filter-only scope must not use normal actions.",
                }
            ],
            "reason": "Planner must determine how the scopes are represented.",
        }
        repaired = copy.deepcopy(invalid)
        repaired["ambiguities"] = []
        repaired["reason"] = ""

        class FakeCodex:
            def __init__(self) -> None:
                self.turns: list[dict] = []
                self.responses = [json.dumps(invalid), json.dumps(repaired)]

            def start_thread(self, **_kwargs) -> str:
                return "intake-thread"

            def run_turn(self, **kwargs) -> str:
                self.turns.append(kwargs)
                return self.responses.pop(0)

        codex = FakeCodex()
        contract = run_task_contract_normalizer(
            codex,  # type: ignore[arg-type]
            cwd=Path.cwd(),
            raw_request=raw,
        )

        self.assertEqual(contract["status"], "READY")
        self.assertEqual(contract["ambiguities"], [])
        self.assertEqual(contract["reason"], "")
        self.assertEqual(len(codex.turns), 2)
        self.assertEqual(
            {turn["thread_id"] for turn in codex.turns},
            {"intake-thread"},
        )
        self.assertIn(
            "TASK_CONTRACT_READY_WITH_AMBIGUITY",
            codex.turns[1]["prompt"],
        )
        self.assertIn("Разные conditions/scopes", codex.turns[1]["prompt"])

    def test_normalizer_artifact_repair_is_bounded(self) -> None:
        raw = "Исправь кнопку."
        invalid = {
            "protocol_version": TASK_CONTRACT_VERSION,
            "status": "READY",
            "summary": "Исправить кнопку.",
            "explicit_intent": [
                {"claim": "Исправить кнопку.", "source_text": "Исправь кнопку."}
            ],
            "explicit_acceptance": [
                {"claim": "Кнопка исправлена.", "source_text": "Исправь кнопку."}
            ],
            "explicit_preservation": [],
            "explicit_forbidden": [],
            "owner_boundaries": [],
            "non_goals": [],
            "ambiguities": [],
            "reason": "READY should not include a reason.",
        }

        class FakeCodex:
            def __init__(self) -> None:
                self.calls = 0

            def start_thread(self, **_kwargs) -> str:
                return "intake-thread"

            def run_turn(self, **_kwargs) -> str:
                self.calls += 1
                return json.dumps(invalid)

        codex = FakeCodex()
        with self.assertRaisesRegex(RuntimeError, "exhausted artifact-repair attempts"):
            run_task_contract_normalizer(
                codex,  # type: ignore[arg-type]
                cwd=Path.cwd(),
                raw_request=raw,
                max_artifact_repairs=2,
            )
        self.assertEqual(codex.calls, 3)

    def test_direct_contradiction_still_requires_user_decision(self) -> None:
        raw = "Оставь кнопку видимой. Спрячь кнопку в этом же состоянии."
        contract = build_task_contract(
            raw_request=raw,
            normalized={
                "protocol_version": TASK_CONTRACT_VERSION,
                "status": "NEEDS_USER_DECISION",
                "summary": "Запрос содержит прямое противоречие.",
                "explicit_intent": [
                    {
                        "claim": "Оставить кнопку видимой.",
                        "source_text": "Оставь кнопку видимой.",
                    }
                ],
                "explicit_acceptance": [],
                "explicit_preservation": [],
                "explicit_forbidden": [],
                "owner_boundaries": [],
                "non_goals": [],
                "ambiguities": [
                    {
                        "claim": "Кнопка должна одновременно быть видимой и скрытой.",
                        "source_text": "Спрячь кнопку в этом же состоянии.",
                    }
                ],
                "reason": "Один и тот же UI-state требует несовместимых результатов.",
            },
        )

        validate_task_contract(contract)
        self.assertEqual(contract["status"], "NEEDS_USER_DECISION")

    def test_task_contract_rejects_non_verbatim_source(self) -> None:
        raw = "Исправь кнопку."
        normalized = {
            "protocol_version": TASK_CONTRACT_VERSION,
            "status": "READY",
            "summary": "Исправить кнопку.",
            "explicit_intent": [{"claim": "Исправить кнопку.", "source_text": "Исправь кнопку."}],
            "explicit_acceptance": [{"claim": "Кнопка работает.", "source_text": "кнопка должна работать"}],
            "explicit_preservation": [],
            "explicit_forbidden": [],
            "owner_boundaries": [],
            "non_goals": [],
            "ambiguities": [],
            "reason": "",
        }

        with self.assertRaisesRegex(ArtifactContractError, "source_text"):
            build_task_contract(raw_request=raw, normalized=normalized)

    def test_local_verification_plan_requires_no_runtime(self) -> None:
        contract = build_implementation_contract(None, task_contract=ready_task_contract())
        plan = compile_verification_plan(contract, project_checks=[{"name": "Unit"}])

        self.assertFalse(plan["runtime_required"])
        self.assertEqual(plan["runtime_profiles"], [])
        self.assertEqual(plan["required_capabilities"], [])
        validate_verification_plan(plan)

    def test_multiple_runtime_profiles_are_preserved(self) -> None:
        contract = build_implementation_contract(None, task_contract=ready_task_contract())
        contract = copy.deepcopy(contract)
        contract["items"][0]["required_proof"] = merged_required_proof(
            [
                {
                    "claim": "Проверить пользовательский flow.",
                    "level": ProofLevel.LIVE_LOCAL.value,
                    "capabilities": [Capability.BROWSER_DOM.value],
                },
                {
                    "claim": "Проверить внешнюю запись fresh readback.",
                    "level": ProofLevel.TEST_EXTERNAL.value,
                    "capabilities": [Capability.TEST_EXTERNAL_FRESH_READ.value],
                },
            ],
            fallback_claim="unused",
        )
        contract["fingerprint"] = stable_fingerprint(
            {key: value for key, value in contract.items() if key != "fingerprint"}
        )

        plan = compile_verification_plan(contract, project_checks=[])

        self.assertEqual(plan["runtime_profiles"], ["LIVE_LOCAL", "TEST_EXTERNAL"])
        self.assertIn(Capability.LIVE_LOCAL_RUNTIME.value, plan["required_capabilities"])
        self.assertIn(Capability.TEST_EXTERNAL_RUNTIME.value, plan["required_capabilities"])
        self.assertTrue(plan["runtime_required"])

    def test_phase3_capability_gate_refuses_unimplemented_runtime(self) -> None:
        contract = build_implementation_contract(None, task_contract=ready_task_contract())
        contract = copy.deepcopy(contract)
        contract["items"][0]["required_proof"] = merged_required_proof(
            [
                {
                    "claim": "Проверить browser DOM.",
                    "level": ProofLevel.LIVE_LOCAL.value,
                    "capabilities": [Capability.BROWSER_DOM.value],
                }
            ],
            fallback_claim="unused",
        )
        contract["fingerprint"] = stable_fingerprint(
            {key: value for key, value in contract.items() if key != "fingerprint"}
        )
        plan = compile_verification_plan(contract, project_checks=[])
        available = available_capabilities(
            toolchain={},
            configured=[Capability.BROWSER_DOM.value, Capability.LIVE_LOCAL_RUNTIME.value],
        )

        self.assertEqual(
            required_capability_gaps(plan, available=available),
            [Capability.BROWSER_DOM.value, Capability.LIVE_LOCAL_RUNTIME.value],
        )

    def test_tampered_capability_summary_is_rejected(self) -> None:
        contract = build_implementation_contract(None, task_contract=ready_task_contract())
        plan = compile_verification_plan(contract, project_checks=[])
        tampered = copy.deepcopy(plan)
        tampered["required_capabilities"] = [Capability.GIT.value]
        tampered["fingerprint"] = stable_fingerprint(
            {key: value for key, value in tampered.items() if key != "fingerprint"}
        )

        with self.assertRaisesRegex(ArtifactContractError, "capability summary"):
            validate_verification_plan(tampered)


if __name__ == "__main__":
    unittest.main()
