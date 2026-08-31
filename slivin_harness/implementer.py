from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from slivin_harness.protocol import ArtifactContractError, ensure_exact_keys, safe_repo_relative, stable_fingerprint
from slivin_harness.task_contract import validate_task_contract
from slivin_harness.verification import merged_required_proof, validate_merged_required_proof
from slivin_harness.workflow import ImplementerStatus, enum_values

IMPLEMENTER_PROTOCOL_VERSION = "implementer.v1"
IMPLEMENTATION_CONTRACT_VERSION = "implementation-contract.v3"
CONTRACT_ITEM_TYPES = {"acceptance", "preservation", "state", "consumer", "risk", "documentation"}
CONTRACT_ITEM_SOURCES = {"USER", "PLANNER", "USER+PLANNER", "DISCOVERED"}

IMPLEMENTER_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {"type": "string", "enum": [IMPLEMENTER_PROTOCOL_VERSION]},
        "status": {"type": "string", "enum": enum_values(ImplementerStatus)},
        "summary": {"type": "string"},
        "contract_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "item_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        # NOT_APPLICABLE remains accepted as an implementer.v1
                        # compatibility alias; v3 prompts use NOT_AFFECTED.
                        "enum": ["VERIFIED", "NOT_AFFECTED", "NOT_APPLICABLE", "BLOCKED"],
                    },
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["item_id", "status", "evidence"],
            },
        },
        "self_verification": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_RUN"]},
                "command": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "command", "evidence"],
        },
        "additional_check_paths": {"type": "array", "items": {"type": "string"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "protocol_version", "status", "summary", "contract_evidence",
        "self_verification", "additional_check_paths", "blockers",
    ],
}


def _claims(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(item["claim"]).strip() for item in rows if str(item["claim"]).strip()]


def _format_requirement(*, explicit: Iterable[str] = (), technical: Iterable[str] = ()) -> str:
    blocks: list[str] = []
    explicit_rows = [item for item in explicit if item]
    technical_rows = [item for item in technical if item]
    if explicit_rows:
        blocks.append("Explicit user contract:\n" + "\n".join(f"- {item}" for item in explicit_rows))
    if technical_rows:
        blocks.append("Technical mapping:\n" + "\n".join(f"- {item}" for item in technical_rows))
    return "\n\n".join(blocks).strip()


def _add_item(
    items: list[dict[str, Any]],
    *,
    item_id: str,
    item_type: str,
    source: str,
    requirement: str,
    required_proof: dict[str, Any],
    allow_not_affected: bool = False,
) -> None:
    items.append(
        {
            "id": item_id,
            "type": item_type,
            "source": source,
            "requirement": requirement.strip(),
            "required_proof": required_proof,
            "allow_not_affected": allow_not_affected,
        }
    )


def build_implementation_contract(
    plan: dict[str, Any] | None,
    *,
    task_contract: dict[str, Any],
) -> dict[str, Any]:
    """Compile the minimum load-bearing Definition of Done.

    User acceptance/preservation are copied directly from the Task Contract.
    Planner context is deliberately not converted wholesale into obligations.
    """
    validate_task_contract(task_contract)
    items: list[dict[str, Any]] = []
    user_acceptance = _claims(task_contract["explicit_acceptance"])
    user_preservation = _claims(task_contract["explicit_preservation"])
    user_forbidden = _claims(task_contract["explicit_forbidden"])
    user_boundaries = _claims(task_contract["owner_boundaries"])

    if plan is None:
        technical_acceptance: list[str] = []
        derived_preservation: list[str] = []
        regression_proofs: list[dict[str, Any]] = []
        preservation_proofs: list[dict[str, Any]] = []
    else:
        technical_acceptance = list(plan["technical_contract"]["technical_acceptance"])
        derived_preservation = list(plan["technical_contract"]["derived_preservation"])
        regression_proofs = list(plan["evidence_plan"]["regression"])
        preservation_proofs = list(plan["evidence_plan"]["preservation"])

    _add_item(
        items,
        item_id="ACCEPTANCE-1",
        item_type="acceptance",
        source="USER+PLANNER" if technical_acceptance else "USER",
        requirement=_format_requirement(explicit=user_acceptance, technical=technical_acceptance),
        required_proof=merged_required_proof(
            regression_proofs,
            fallback_claim="The explicit user acceptance is observable on the final candidate.",
        ),
    )

    combined_user_preservation = [*user_preservation, *user_forbidden, *user_boundaries]
    if combined_user_preservation or derived_preservation:
        _add_item(
            items,
            item_id="PRESERVE-1",
            item_type="preservation",
            source="USER+PLANNER" if combined_user_preservation and derived_preservation else ("USER" if combined_user_preservation else "PLANNER"),
            requirement=_format_requirement(
                explicit=combined_user_preservation,
                technical=derived_preservation,
            ),
            required_proof=merged_required_proof(
                preservation_proofs,
                fallback_claim="Existing behavior named by preservation requirements remains unchanged.",
            ),
        )

    if plan is not None:
        state = plan["state_model"]
        if state["applicable"]:
            state_requirement = _format_requirement(
                technical=[
                    *(f"Representation: {item}" for item in state["representations"]),
                    *(f"Authority: {item}" for item in state["authority"]),
                    *(f"Lifecycle: {item}" for item in state["lifecycle"]),
                    *(f"Boundary: {item}" for item in state["boundaries"]),
                ]
            )
            _add_item(
                items,
                item_id="STATE-1",
                item_type="state",
                source="PLANNER",
                requirement=state_requirement,
                required_proof=merged_required_proof(
                    [state["required_proof"], *plan["evidence_plan"]["boundaries"]],
                    fallback_claim="The authoritative state model and reachable boundaries are preserved.",
                ),
            )

        for index, consumer in enumerate(plan["affected_consumers"], start=1):
            _add_item(
                items,
                item_id=f"CONSUMER-{index}",
                item_type="consumer",
                source="PLANNER",
                requirement=(
                    f"{consumer['name']}\n"
                    f"Why affected: {consumer['why_affected']}\n"
                    f"Must verify: {consumer['must_verify']}"
                ),
                required_proof=merged_required_proof(
                    [consumer["required_proof"]],
                    fallback_claim=consumer["must_verify"],
                ),
                allow_not_affected=True,
            )

        for index, risk in enumerate(plan["risks"], start=1):
            _add_item(
                items,
                item_id=f"RISK-{index}",
                item_type="risk",
                source="PLANNER",
                requirement=(
                    f"Condition: {risk['condition']}\n"
                    f"Failure mode to exclude: {risk['failure_mode']}"
                ),
                required_proof=merged_required_proof(
                    [risk["required_proof"]],
                    fallback_claim=risk["failure_mode"],
                ),
            )

        documentation = plan["documentation"]
        if documentation["required"]:
            _add_item(
                items,
                item_id="DOCS-1",
                item_type="documentation",
                source="PLANNER",
                requirement=(
                    "Synchronize the canonical documentation with final behavior. "
                    f"Reason: {documentation['reason']}"
                ),
                required_proof=merged_required_proof(
                    [documentation["required_proof"]],
                    fallback_claim="Canonical documentation matches the final candidate.",
                ),
            )

    warnings: list[str] = []
    if len(items) > 14:
        warnings.append(
            "Implementation Contract exceeds the soft review threshold of 14 items; "
            "material obligations were retained and should be semantically grouped if possible."
        )
    payload: dict[str, Any] = {
        "protocol_version": IMPLEMENTATION_CONTRACT_VERSION,
        "task_contract_fingerprint": task_contract["fingerprint"],
        "items": items,
        "warnings": warnings,
    }
    payload["fingerprint"] = stable_fingerprint(payload)
    validate_implementation_contract(payload)
    return payload


def validate_implementation_contract(contract: Mapping[str, Any]) -> None:
    ensure_exact_keys(
        dict(contract),
        allowed={"protocol_version", "task_contract_fingerprint", "items", "warnings", "fingerprint"},
        required={"protocol_version", "task_contract_fingerprint", "items", "warnings", "fingerprint"},
        field="implementation_contract",
    )
    if contract["protocol_version"] != IMPLEMENTATION_CONTRACT_VERSION:
        raise ArtifactContractError(
            code="IMPLEMENTATION_CONTRACT_VERSION",
            field="implementation_contract.protocol_version",
            message="Implementation Contract version mismatch",
            expected=IMPLEMENTATION_CONTRACT_VERSION,
            actual=contract["protocol_version"],
        )
    if not isinstance(contract["items"], list) or not contract["items"]:
        raise RuntimeError("Implementation Contract requires at least one item")
    ids: set[str] = set()
    for index, item in enumerate(contract["items"]):
        if not isinstance(item, dict):
            raise RuntimeError(f"Implementation Contract item {index} must be an object")
        ensure_exact_keys(
            item,
            allowed={"id", "type", "source", "requirement", "required_proof", "allow_not_affected"},
            required={"id", "type", "source", "requirement", "required_proof", "allow_not_affected"},
            field=f"implementation_contract.items[{index}]",
        )
        item_id = str(item["id"])
        if not item_id or item_id in ids:
            raise RuntimeError(f"Implementation Contract item ids must be non-empty and unique: {item_id!r}")
        ids.add(item_id)
        if item["type"] not in CONTRACT_ITEM_TYPES:
            raise RuntimeError(f"Unknown Implementation Contract item type: {item['type']}")
        if item["source"] not in CONTRACT_ITEM_SOURCES:
            raise RuntimeError(f"Unknown Implementation Contract source: {item['source']}")
        if not isinstance(item["requirement"], str) or not item["requirement"].strip():
            raise RuntimeError(f"Implementation Contract item {item_id} requires requirement text")
        validate_merged_required_proof(
            item["required_proof"],
            field=f"implementation_contract.items[{index}].required_proof",
        )
        if item["allow_not_affected"] is not (item["type"] == "consumer"):
            raise RuntimeError(
                f"Only consumer items may allow NOT_AFFECTED: {item_id}"
            )
    if not any(item["type"] == "acceptance" for item in contract["items"]):
        raise RuntimeError("Implementation Contract must contain acceptance")
    if not isinstance(contract["warnings"], list) or not all(isinstance(item, str) for item in contract["warnings"]):
        raise RuntimeError("Implementation Contract warnings must be strings")
    expected = stable_fingerprint({key: value for key, value in contract.items() if key != "fingerprint"})
    if contract["fingerprint"] != expected:
        raise RuntimeError("Implementation Contract fingerprint mismatch")


def compact_plan_context(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "summary": plan["summary"],
        "diagnosis": plan["diagnosis"],
        "assumptions": plan["assumptions"],
        "unknowns": [item for item in plan["unknowns"] if item["kind"] == "NON_BLOCKING"],
    }


def validate_implementation_report(
    report: dict[str, Any],
    *,
    contract: dict[str, Any],
    changed_paths: list[str],
    self_verification_ok: bool,
    documentation_paths: list[str] | None = None,
) -> None:
    validate_implementation_contract(contract)
    if report.get("protocol_version") != IMPLEMENTER_PROTOCOL_VERSION:
        raise RuntimeError(f"Implementer protocol mismatch: {report.get('protocol_version')!r}")
    if report.get("status") not in set(enum_values(ImplementerStatus)):
        raise RuntimeError("Implementer status must be COMPLETE or BLOCKED")
    if not isinstance(report.get("summary"), str):
        raise RuntimeError("Implementer summary must be a string")

    items = {item["id"]: item for item in contract["items"]}
    evidence_rows = report.get("contract_evidence")
    if not isinstance(evidence_rows, list):
        raise RuntimeError("Implementer contract_evidence must be an array")
    by_id: dict[str, dict[str, Any]] = {}
    for row in evidence_rows:
        if not isinstance(row, dict):
            raise RuntimeError("Implementer contract_evidence rows must be objects")
        item_id = str(row.get("item_id"))
        if item_id in by_id:
            raise RuntimeError(f"Duplicate Implementer contract evidence: {item_id}")
        by_id[item_id] = row
    if set(by_id) != set(items):
        raise RuntimeError(
            "Implementer must account for every Implementation Contract item exactly once: "
            f"expected={sorted(items)}, actual={sorted(by_id)}"
        )

    blocked_items: list[str] = []
    for item_id, contract_item in items.items():
        row = by_id[item_id]
        status = row.get("status")
        if status == "NOT_APPLICABLE":
            status = "NOT_AFFECTED"
        evidence = row.get("evidence")
        if status not in {"VERIFIED", "NOT_AFFECTED", "BLOCKED"}:
            raise RuntimeError(f"Invalid contract status for {item_id}: {status}")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(line, str) and line.strip() for line in evidence
        ):
            raise RuntimeError(f"Contract item {item_id} requires concrete evidence")
        if status == "NOT_AFFECTED" and not contract_item["allow_not_affected"]:
            raise RuntimeError(f"Contract item {item_id} cannot be NOT_AFFECTED")
        if status == "BLOCKED":
            blocked_items.append(item_id)

    additional = report.get("additional_check_paths")
    if not isinstance(additional, list):
        raise RuntimeError("additional_check_paths must be an array")
    for index, raw in enumerate(additional):
        safe_repo_relative(raw, field=f"additional_check_paths[{index}]")

    blockers = report.get("blockers")
    if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
        raise RuntimeError("blockers must be an array of strings")
    self_verification = report.get("self_verification")
    if not isinstance(self_verification, dict):
        raise RuntimeError("self_verification must be an object")

    if report["status"] == ImplementerStatus.COMPLETE.value:
        if blocked_items or blockers:
            raise RuntimeError("Implementer COMPLETE cannot retain blocked items or blockers")
        if self_verification.get("status") != "PASS" or not self_verification_ok:
            raise RuntimeError(
                "Implementer COMPLETE requires trusted self-verification PASS on the current candidate"
            )
        # Documentation correctness is a semantic Contract item. Phase 3 no
        # longer requires changing a Planner-guessed filename.
        sorted(safe_repo_relative(item, field="changed_path") for item in changed_paths)
    else:
        if not blockers and not blocked_items:
            raise RuntimeError("Implementer BLOCKED requires a concrete blocker")


def parse_implementation_report(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Implementer returned invalid JSON structured output.\n" + raw) from exc
    if not isinstance(value, dict):
        raise RuntimeError("Implementer structured output must be an object")
    return value
