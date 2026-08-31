from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from slivin_harness.protocol import safe_repo_relative, stable_fingerprint
from slivin_harness.workflow import ImplementerStatus, enum_values

IMPLEMENTER_PROTOCOL_VERSION = "implementer.v1"
IMPLEMENTATION_CONTRACT_VERSION = "implementation-contract.v2"

IMPLEMENTER_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {
            "type": "string",
            "enum": [IMPLEMENTER_PROTOCOL_VERSION],
        },
        "status": {
            "type": "string",
            "enum": enum_values(ImplementerStatus),
        },
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
                        "enum": ["VERIFIED", "NOT_APPLICABLE", "BLOCKED"],
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
        "additional_check_paths": {
            "type": "array",
            "items": {"type": "string"},
        },
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "protocol_version",
        "status",
        "summary",
        "contract_evidence",
        "self_verification",
        "additional_check_paths",
        "blockers",
    ],
}


def _joined(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def build_implementation_contract(plan: dict[str, Any] | None, *, task_prompt: str) -> dict[str, Any]:
    """Build a small execution contract from Planner discoveries.

    The contract intentionally groups outcome/preservation/test-plan text while keeping
    each materially affected consumer explicit. This prevents the old 30-40 obligation
    explosion while making Planner discoveries load-bearing for the Implementer.
    """
    items: list[dict[str, Any]] = []

    def add(item_id: str, kind: str, text: str, *, allow_not_applicable: bool = False) -> None:
        items.append(
            {
                "id": item_id,
                "kind": kind,
                "text": text.strip(),
                "allow_not_applicable": allow_not_applicable,
            }
        )

    if plan is None:
        add(
            "OUTCOME-1",
            "outcome",
            "Выполнить исходную задачу целиком в пределах заданного product scope.",
        )
        add(
            "EVIDENCE-1",
            "evidence",
            "До завершения Implementer запустить trusted self-verification Controller checks.",
        )
        risks: list[str] = []
    else:
        add(
            "OUTCOME-1",
            "outcome",
            "Ожидаемое поведение:\n" + _joined(list(plan["expected_behavior"])),
        )
        if plan["preserve"]:
            add(
                "PRESERVE-1",
                "preservation",
                "Сохранить существующий контракт:\n" + _joined(list(plan["preserve"])),
            )
        for index, consumer in enumerate(plan["consumers_to_check"], start=1):
            add(
                f"CONSUMER-{index}",
                "consumer",
                consumer,
                allow_not_applicable=True,
            )
        risks = list(plan["risks"])
        for index, risk in enumerate(risks, start=1):
            add(
                f"RISK-{index}",
                "risk",
                risk,
                allow_not_applicable=False,
            )
        add(
            "EVIDENCE-1",
            "evidence",
            "План проверки:\n" + _joined(list(plan["test_plan"])),
        )
        documentation = plan["documentation"]
        if documentation["required"]:
            add(
                "DOCS-1",
                "documentation",
                "Синхронизировать документацию: "
                + ", ".join(documentation["paths"])
                + ". Причина: "
                + documentation["reason"],
            )

    payload: dict[str, Any] = {
        "protocol_version": IMPLEMENTATION_CONTRACT_VERSION,
        "items": items,
        "risks": risks,
    }
    payload["fingerprint"] = stable_fingerprint(payload)
    return payload


def compact_plan_context(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "summary": plan["summary"],
        "root_cause": plan["root_cause"],
        "change_plan": plan["change_plan"],
        "risks": plan["risks"],
        "unknowns": plan["unknowns"],
    }


def validate_implementation_report(
    report: dict[str, Any],
    *,
    contract: dict[str, Any],
    changed_paths: list[str],
    self_verification_ok: bool,
    documentation_paths: list[str],
) -> None:
    if report.get("protocol_version") != IMPLEMENTER_PROTOCOL_VERSION:
        raise RuntimeError(
            f"Implementer protocol mismatch: {report.get('protocol_version')!r}"
        )
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
        item_id = row.get("item_id")
        if item_id in by_id:
            raise RuntimeError(f"Duplicate Implementer contract evidence: {item_id}")
        by_id[str(item_id)] = row
    if set(by_id) != set(items):
        raise RuntimeError(
            "Implementer must account for every Implementation Contract item exactly once: "
            f"expected={sorted(items)}, actual={sorted(by_id)}"
        )

    blocked_items: list[str] = []
    for item_id, contract_item in items.items():
        row = by_id[item_id]
        status = row.get("status")
        evidence = row.get("evidence")
        if status not in {"VERIFIED", "NOT_APPLICABLE", "BLOCKED"}:
            raise RuntimeError(f"Invalid contract status for {item_id}: {status}")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(line, str) and line.strip() for line in evidence
        ):
            raise RuntimeError(f"Contract item {item_id} requires concrete evidence")
        if status == "NOT_APPLICABLE" and not contract_item["allow_not_applicable"]:
            raise RuntimeError(f"Contract item {item_id} cannot be NOT_APPLICABLE")
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
            raise RuntimeError(
                "Implementer COMPLETE cannot retain blocked contract items or blockers"
            )
        if self_verification.get("status") != "PASS" or not self_verification_ok:
            raise RuntimeError(
                "Implementer COMPLETE requires trusted self-verification PASS on the current candidate"
            )
        actual_changed = sorted(
            safe_repo_relative(item, field="changed_path") for item in changed_paths
        )
        for raw in documentation_paths:
            normalized = safe_repo_relative(raw, field="documentation.path")
            if normalized not in actual_changed:
                raise RuntimeError(
                    f"Planner required documentation update but path was not changed: {normalized}"
                )
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
