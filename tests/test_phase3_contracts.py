from __future__ import annotations

import copy
import unittest

from slivin_harness.implementer import build_implementation_contract
from slivin_harness.protocol import ArtifactContractError, stable_fingerprint
from slivin_harness.task_contract import (
    TASK_CONTRACT_VERSION,
    build_task_contract,
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
