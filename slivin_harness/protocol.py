from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Iterable

PLANNER_PROTOCOL_VERSION = "planner.v2"
EVALUATOR_PROTOCOL_VERSION = "evaluator.v3"
IMPACT_PROTOCOL_VERSION = "impact.v1"

ID_PATTERNS = {
    "current_contract": r"^CC-[0-9]+$",
    "assumptions": r"^A-[0-9]+$",
    "affected_consumers": r"^CONS-[0-9]+$",
    "state_lifecycle_audit": r"^LIFE-[0-9]+$",
    "decision_escalations": r"^DEC-[0-9]+$",
    "representation_consumer_audit": r"^REP-[0-9]+$",
    "authority_matrix": r"^AUTH-[0-9]+$",
    "preservation_contract": r"^PRES-[0-9]+$",
    "interaction_matrix": r"^INT-[0-9]+$",
    "test_matrix": r"^TEST-[0-9]+$",
    "impact_items": r"^IMP-[0-9]+$",
}



class ArtifactContractError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        field: str,
        message: str,
        expected: str,
        actual: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message
        self.expected = expected
        self.actual = actual

    def feedback(self) -> dict:
        return {
            "protocol_error": self.code,
            "field": self.field,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "repair_rule": (
                "Return a fresh artifact that satisfies the schema literally. "
                "Do not preserve malformed formatting and do not encode prose in ID fields."
            ),
        }

OBLIGATION_FIELDS_ALWAYS = (
    "affected_consumers",
    "state_lifecycle_audit",
    "representation_consumer_audit",
    "authority_matrix",
    "preservation_contract",
    "test_matrix",
)


def id_schema(field: str) -> dict:
    # Keep App Server outputSchema inside the conservative subset already proven
    # by this Harness (type/enum/object/array/required/additionalProperties).
    # Exact ID syntax is enforced independently by Controller validation.
    _ = field
    return {"type": "string"}


def finding_id_schema(prefix: str) -> dict:
    _ = prefix
    return {"type": "string"}


def collect_ids(plan: dict, field: str) -> list[str]:
    return [str(item["id"]) for item in plan.get(field, [])]


def required_obligation_ids(plan: dict) -> list[str]:
    """Derive the blocking ledger deterministically from the Planner artifact.

    Planner no longer restates IDs in a separate free-form list. Every LIFE/REP/AUTH/
    CONS/PRES/TEST item is blocking by contract. CC and INT items opt in explicitly
    with `release_critical=true` on the item that owns the semantics.
    """
    result: list[str] = []

    for item in plan.get("current_contract", []):
        if item.get("release_critical") is True:
            result.append(str(item["id"]))

    for field in OBLIGATION_FIELDS_ALWAYS:
        result.extend(collect_ids(plan, field))

    for item in plan.get("interaction_matrix", []):
        if item.get("release_critical") is True:
            result.append(str(item["id"]))

    return result



def plan_fingerprint(plan: dict) -> str:
    payload = json.dumps(
        plan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]



def impact_fingerprint(impact: dict) -> str:
    payload = json.dumps(
        impact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def impact_obligation_ids(impact: dict | None) -> list[str]:
    if not impact:
        return []
    return [str(item["id"]) for item in impact.get("items", [])]


def impact_required_candidate_paths(impact: dict | None) -> list[str]:
    if not impact:
        return []
    result: list[str] = []
    for item in impact.get("items", []):
        if item.get("disposition") != "CHANGE_REQUIRED":
            continue
        for raw in item.get("required_candidate_paths", []):
            path = str(raw)
            if path not in result:
                result.append(path)
    return result


def impact_schema_for_plan(base_schema: dict, plan: dict) -> dict:
    schema = deepcopy(base_schema)
    schema["properties"]["plan_fingerprint"]["enum"] = [plan_fingerprint(plan)]
    return schema

def evaluator_schema_for_plan(
    base_schema: dict,
    plan: dict,
    impact: dict | None = None,
) -> dict:
    """Bind evaluator cross-references to exact Controller-approved IDs."""
    schema = deepcopy(base_schema)
    schema["properties"]["plan_fingerprint"]["enum"] = [plan_fingerprint(plan)]
    schema["properties"]["impact_fingerprint"]["enum"] = [
        impact_fingerprint(impact or {})
    ]

    obligation_ids = required_obligation_ids(plan)
    obligation_array = schema["properties"]["obligation_assessment"]
    obligation_id = obligation_array["items"]["properties"]["id"]
    if obligation_ids:
        obligation_id["enum"] = obligation_ids

    assumption_ids = collect_ids(plan, "assumptions")
    assumption_array = schema["properties"]["planner_assumption_audit"]
    assumption_id = assumption_array["items"]["properties"]["id"]
    if assumption_ids:
        assumption_id["enum"] = assumption_ids

    impact_ids = impact_obligation_ids(impact)
    impact_array = schema["properties"]["impact_assessment"]
    impact_id = impact_array["items"]["properties"]["id"]
    if impact_ids:
        impact_id["enum"] = impact_ids

    return schema


def compact_plan_retry_context(plan: dict) -> dict:
    """Small retry context: enough to repair protocol shape without replaying huge JSON."""
    return {
        "status": plan.get("status"),
        "ids": {
            field: collect_ids(plan, field)
            for field in (
                "current_contract",
                "assumptions",
                "affected_consumers",
                "state_lifecycle_audit",
                "decision_escalations",
                "representation_consumer_audit",
                "authority_matrix",
                "preservation_contract",
                "interaction_matrix",
                "test_matrix",
            )
        },
        "candidate_paths": [str(path) for path in plan.get("candidate_paths", [])],
    }


def format_exact_id_contract(ids: Iterable[str]) -> str:
    values = list(ids)
    if not values:
        return "(none)"
    return "[" + ", ".join(repr(value) for value in values) + "]"
