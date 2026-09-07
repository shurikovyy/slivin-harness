from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from slivin_harness.protocol import ArtifactContractError, ensure_exact_keys, plan_fingerprint, require_string_list, require_type, safe_repo_relative, stable_fingerprint
from slivin_harness.impact import impact_error, impact_paths, impact_text, safe_impact_path, validate_owner_prose_boundary
from slivin_harness.task_contract import validate_task_contract
from slivin_harness.verification import (
    PROOF_TARGET_SCHEMA,
    merged_required_proof,
    validate_merged_required_proof,
    validate_proof_target,
)
from slivin_harness.workflow import ImplementerStatus, enum_values

IMPLEMENTER_PROTOCOL_VERSION = "implementer.v5"
IMPLEMENTER_TERMINAL_REASONS = {
    "COMPLETE": ("NONE",),
    "REPLAN_REQUIRED": ("TECHNICAL_MODEL_DIVERGENCE", "PROOF_MODEL_DIVERGENCE"),
    "BLOCKED": ("INFRASTRUCTURE_BLOCKED",),
    "NEEDS_USER_DECISION": ("USER_DECISION_REQUIRED",),
}
IMPLEMENTATION_IMPACT_CLOSURE_VERSION = "implementation-impact-closure.v1"
IMPLEMENTATION_CONTRACT_VERSION = "implementation-contract.v3"
CONTRACT_ITEM_TYPES = {"acceptance", "preservation", "state", "consumer", "risk", "documentation"}
CONTRACT_ITEM_SOURCES = {"USER", "PLANNER", "USER+PLANNER", "DISCOVERED"}


def _impact_rows(*, text: Sequence[str], lists: Sequence[str] = (), proof: bool = False, enums: Mapping[str, Sequence[str]] | None = None) -> dict:
    properties = {key: {"type": "string"} for key in text}
    properties.update({key: {"type": "array", "items": {"type": "string"}} for key in lists})
    if proof:
        properties["required_proof"] = PROOF_TARGET_SCHEMA
    properties.update({key: {"type": "string", "enum": list(values)} for key, values in (enums or {}).items()})
    return {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}}


POST_PATCH_IMPACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "applicable": {"type": "boolean"},
        "changed_contracts": _impact_rows(text=("name", "before", "after"), lists=("paths", "symbols", "evidence")),
        "in_scope_consumers": _impact_rows(
            text=("name", "why_affected", "required_behavior"), lists=("paths", "symbols", "evidence"),
            proof=True, enums={"source": ("PLANNER", "DISCOVERED")},
        ),
        "not_affected_consumers": _impact_rows(text=("name", "why_considered", "reason"), lists=("paths", "symbols", "evidence")),
        "related_out_of_scope": _impact_rows(text=("name", "relation", "reason", "suggested_follow_up"), lists=("paths", "symbols", "evidence")),
        "new_risks": _impact_rows(text=("name", "reason", "failure_mode"), lists=("paths", "symbols", "evidence"), proof=True),
        "changed_path_review": _impact_rows(
            text=("path", "reason"), lists=("evidence",),
            enums={"role": ("IMPLEMENTATION", "REGRESSION_TEST", "DOCUMENTATION", "DEPENDENCY", "GENERATED", "OTHER_JUSTIFIED")},
        ),
        "search_evidence": _impact_rows(text=("target", "method", "conclusion"), lists=("evidence_paths",)),
        "closure_summary": {"type": "string"},
    },
    "required": ["applicable", "changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope", "new_risks", "changed_path_review", "search_evidence", "closure_summary"],
}

IMPLEMENTER_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {"type": "string", "enum": [IMPLEMENTER_PROTOCOL_VERSION]},
        "status": {"type": "string", "enum": enum_values(ImplementerStatus)},
        "terminal_reason_kind": {
            "type": "string",
            "enum": [kind for kinds in IMPLEMENTER_TERMINAL_REASONS.values() for kind in kinds],
        },
        "summary": {"type": "string"},
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "contract_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "item_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        # NOT_APPLICABLE is accepted as a compatibility alias.
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
                # Agent output cannot mint Controller receipt authority.
                "receipt_id": {"type": "string", "enum": [""]},
            },
            "required": ["status", "command", "evidence", "receipt_id"],
        },
        "additional_check_paths": {"type": "array", "items": {"type": "string"}},
        "registered_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["path", "check_id"]},
                    "value": {"type": "string"},
                },
                "required": ["kind", "value"],
            },
        },
        "discovered_obligations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["consumer", "risk"]},
                    "name": {"type": "string"},
                    "reason": {"type": "string"},
                    "required_behavior": {"type": "string"},
                    "required_proof": PROOF_TARGET_SCHEMA,
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "kind", "name", "reason", "required_behavior", "required_proof", "evidence"
                ],
            },
        },
        "post_patch_impact": POST_PATCH_IMPACT_SCHEMA,
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
    # Structured Outputs requires every wire property. Status-specific semantic
    # completeness remains in Controller code, so non-COMPLETE reports use empty
    # collections/strings instead of fabricating a full contract ledger.
    "required": [
        "protocol_version",
        "status",
        "terminal_reason_kind",
        "summary",
        "reason",
        "evidence",
        "contract_evidence",
        "self_verification",
        "additional_check_paths",
        "registered_checks",
        "discovered_obligations",
        "post_patch_impact",
        "blockers",
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
        "impact_closure": plan["impact_closure"],
    }


def _impact_name(row: Mapping[str, Any]) -> str:
    return impact_text(row["name"], field="name").casefold()


def _same_impact_text(left: str, right: str) -> bool:
    return " ".join(left.split()) == " ".join(right.split())


def _same_impact_proof(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        _same_impact_text(left["claim"], right["claim"])
        and left["level"] == right["level"]
        and set(left["capabilities"]) == set(right["capabilities"])
    )


def _evidence_set(values: Sequence[str]) -> set[str]:
    return {" ".join(value.split()) for value in values}


def _impact_mismatch(code: str, message: str, actual: object) -> None:
    impact_error(code, field="post_patch_impact", message=message, actual=actual)


def validate_post_patch_impact(
    report: Mapping[str, Any], *, workspace: Path, changed_paths: Sequence[str],
    plan: dict[str, Any] | None, contract: Mapping[str, Any],
    owner_allowed_paths: Sequence[str] = (), require_expanded: bool = False,
) -> None:
    """Reconcile actual-patch evidence; only Controller facts authorize COMPLETE."""
    closure = report.get("post_patch_impact")
    require_type(closure, dict, field="post_patch_impact")
    properties = POST_PATCH_IMPACT_SCHEMA["properties"]
    ensure_exact_keys(closure, allowed=properties, required=properties, field="post_patch_impact")
    require_type(closure["applicable"], bool, field="post_patch_impact.applicable")
    require_type(closure["closure_summary"], str, field="post_patch_impact.closure_summary")
    by_group: dict[str, dict[str, Any]] = {}
    search_paths: list[str] = []
    reviewed: list[str] = []
    for group, schema in properties.items():
        if schema["type"] != "array":
            continue
        field = f"post_patch_impact.{group}"
        require_type(closure[group], list, field=field)
        by_group[group] = {}
        for index, row in enumerate(closure[group]):
            row_field = f"{field}[{index}]"
            require_type(row, dict, field=row_field)
            fields = schema["items"]["properties"]
            ensure_exact_keys(row, allowed=fields, required=fields, field=row_field)
            for key, kind in fields.items():
                value = row[key]
                key_field = f"{row_field}.{key}"
                if key == "required_proof":
                    validate_proof_target(value, field=key_field)
                elif kind["type"] == "string":
                    impact_text(value, field=key_field)
                    if "enum" in kind and value not in kind["enum"]:
                        _impact_mismatch("POST_PATCH_ENUM", f"Invalid {key_field}", value)
                else:
                    values = require_string_list(value, field=key_field)
                    if not values:
                        _impact_mismatch("IMPACT_EVIDENCE_EMPTY", f"{key_field} requires concrete evidence", value)
                    for entry in values:
                        impact_text(entry, field=key_field)
                    if key in {"paths", "evidence_paths"}:
                        paths = impact_paths(values, field=key_field, workspace=workspace)
                        if group == "search_evidence":
                            search_paths.extend(paths)
                    if key == "symbols" and any(any(char.isspace() for char in entry) or not any(char.isalnum() for char in entry) for entry in values):
                        _impact_mismatch("IMPACT_SYMBOL_GENERIC", "Post-patch symbols must name concrete identifiers", values)
            if "name" in row:
                name = _impact_name(row)
                if name in by_group[group]:
                    _impact_mismatch("POST_PATCH_DUPLICATE", f"Duplicate {group} name", name)
                by_group[group][name] = row
            if group == "changed_contracts" and _same_impact_text(row["before"], row["after"]):
                _impact_mismatch("IMPACT_CONTRACT_UNCHANGED", "Actual changed contract requires distinct before/after semantics", row)
            if group == "changed_path_review":
                # A Controller-known deletion legitimately has no final file.
                path = safe_impact_path(row["path"], field=f"{row_field}.path")
                reviewed.append(path)
                if row["role"] == "OTHER_JUSTIFIED" and (
                    len(row["reason"].replace(path, "").split()) < 6
                    or not any(path in evidence for evidence in row["evidence"])
                ):
                    _impact_mismatch("POST_PATCH_OTHER_UNJUSTIFIED", "OTHER_JUSTIFIED needs a specific explanation and path-linked evidence", row)

    if report.get("status") != ImplementerStatus.COMPLETE.value:
        return
    impact_text(closure["closure_summary"], field="post_patch_impact.closure_summary")
    actual_paths = {safe_impact_path(path, field="changed_paths") for path in changed_paths}
    if len(reviewed) != len(set(reviewed)) or set(reviewed) != actual_paths:
        _impact_mismatch("POST_PATCH_PATH_COVERAGE", "Every Controller changed path must be reviewed exactly once", {"actual": sorted(actual_paths), "reviewed": reviewed})
    if not closure["search_evidence"]:
        _impact_mismatch("POST_PATCH_SEARCH_MISSING", "COMPLETE requires a post-patch repository impact sweep", [])

    consumers = by_group["in_scope_consumers"]
    unaffected = by_group["not_affected_consumers"]
    related = by_group["related_out_of_scope"]
    if set(consumers) & set(unaffected) or set(consumers) & set(related) or set(unaffected) & set(related) or set(related) & set(by_group["new_risks"]):
        _impact_mismatch("POST_PATCH_CLASSIFICATION_CONFLICT", "Consumer dispositions must be disjoint", closure)
    expected = plan["impact_closure"] if plan is not None else None
    if expected is not None:
        if closure["applicable"] != expected["applicable"]:
            _impact_mismatch("POST_PATCH_MODEL_DIVERGENCE", "Planner applicability changed; return REPLAN_REQUIRED with evidence", closure["applicable"])
        contracts = {_impact_name(row): row for row in expected["changed_contracts"]}
        actual_contracts = by_group["changed_contracts"]
        if contracts.keys() != actual_contracts.keys() or any(
            not _same_impact_text(source[key], actual_contracts[name][key])
            for name, source in contracts.items() for key in ("before", "after")
        ):
            _impact_mismatch("POST_PATCH_MODEL_DIVERGENCE", "Changed contract model differs from Planner; return REPLAN_REQUIRED", actual_contracts)
        planned = {_impact_name(row): row for row in expected["in_scope_consumers"]}
        declared = {name: row for name, row in consumers.items() if row["source"] == "PLANNER"}
        if planned.keys() != declared.keys():
            _impact_mismatch("POST_PATCH_PLANNER_CONSUMERS", "Every Planner IN_SCOPE consumer must be retained as PLANNER", declared)
        for name, source in planned.items():
            target = declared[name]
            if any(not _same_impact_text(source[key], target[key]) for key in ("why_affected", "required_behavior")) or not _same_impact_proof(source["required_proof"], target["required_proof"]):
                _impact_mismatch("POST_PATCH_MODEL_DIVERGENCE", "Planner consumer behavior/proof changed; return REPLAN_REQUIRED", target)
        discovered_names = {name for name, row in consumers.items() if row["source"] == "DISCOVERED"}
        if any(_impact_name(row) not in set(unaffected) | discovered_names for row in expected["not_affected_consumers"]):
            _impact_mismatch("POST_PATCH_NOT_AFFECTED_MISSING", "Every Planner NOT_AFFECTED consumer must be reconsidered or promoted", unaffected)
        for source in expected["related_out_of_scope"]:
            target = related.get(_impact_name(source))
            if target is None or any(not _same_impact_text(source[key], target[key]) for key in ("relation", "reason", "suggested_follow_up")) or not _evidence_set(source["evidence"]).issubset(_evidence_set(target["evidence"])):
                _impact_mismatch("POST_PATCH_FOLLOW_UP_MISSING", "Planner related findings and follow-up evidence must be preserved", source)
    elif any(row["source"] == "PLANNER" for row in consumers.values()):
        _impact_mismatch("POST_PATCH_PLANNER_CONSUMERS", "FAST has no Planner consumers; material consumers are DISCOVERED", consumers)

    discoveries = report.get("discovered_obligations", [])
    require_type(discoveries, list, field="discovered_obligations")
    from slivin_harness.phase5 import _canonical_discovery, _discovery_requirement

    discovery_rows: dict[tuple[str, str], dict] = {}
    for index, raw in enumerate(discoveries):
        require_type(raw, dict, field=f"discovered_obligations[{index}]")
        row = _canonical_discovery(raw, index=index)
        key = (row["kind"], _impact_name(row))
        if key in discovery_rows:
            _impact_mismatch("POST_PATCH_DUPLICATE", "Discovered obligations must be one-to-one", key)
        discovery_rows[key] = row
    expected_discoveries = {
        ("consumer", name): row for name, row in consumers.items() if row["source"] == "DISCOVERED"
    }
    expected_discoveries.update({("risk", name): row for name, row in by_group["new_risks"].items()})
    if discovery_rows.keys() != expected_discoveries.keys():
        _impact_mismatch("POST_PATCH_DISCOVERY_MISMATCH", "Post-patch discoveries and discovered obligations must correspond one-to-one", sorted(discovery_rows))
    for key, source in expected_discoveries.items():
        target = discovery_rows[key]
        reason_key, behavior_key = ("why_affected", "required_behavior") if key[0] == "consumer" else ("reason", "failure_mode")
        if (
            not _same_impact_text(source[reason_key], target["reason"])
            or not _same_impact_text(source[behavior_key], target["required_behavior"])
            or not _same_impact_proof(source["required_proof"], target["required_proof"])
            or _evidence_set(source["evidence"]) != _evidence_set(target["evidence"])
        ):
            _impact_mismatch("POST_PATCH_DISCOVERY_MISMATCH", "Discovered reason/behavior/proof/evidence must match", target)

    # Retain previous discoveries after expansion, including their proof. The
    # existing compiler owns items; the report cannot drop or silently weaken one.
    active_discoveries = {(row["type"], " ".join(row["requirement"].split())): row for row in contract["items"] if row["source"] == "DISCOVERED"}
    reported_items = {(row["kind"], " ".join(_discovery_requirement(row).split())): row for row in discovery_rows.values()}
    if not active_discoveries.keys() <= reported_items.keys() or (require_expanded and active_discoveries.keys() != reported_items.keys()):
        _impact_mismatch("POST_PATCH_DISCOVERY_CONTRACT", "Every discovery must remain in the report and be expanded before final COMPLETE", sorted(reported_items))
    for key, item in active_discoveries.items():
        row = reported_items[key]
        proof = merged_required_proof([row["required_proof"]], fallback_claim=row["required_behavior"])
        if item["required_proof"] != proof:
            _impact_mismatch("POST_PATCH_DISCOVERY_CONTRACT", "Existing discovered proof cannot be silently changed", row)

    if closure["applicable"]:
        if not closure["changed_contracts"]:
            _impact_mismatch("POST_PATCH_CONTRACTS_MISSING", "Engineering COMPLETE requires actual changed contracts", [])
    else:
        validate_owner_prose_boundary(owner_allowed_paths, workspace=workspace, search_paths=[*search_paths, *actual_paths], field="post_patch_impact")
        explanation = closure["closure_summary"]
        for path in search_paths:
            explanation = explanation.replace(path, "")
        behavioral = any(closure[group] for group in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope", "new_risks"))
        behavioral = behavioral or any(item["type"] in {"state", "consumer", "risk"} or any(
            proof["level"] != "LOCAL_DETERMINISTIC" or set(proof["capabilities"]) - {"GIT", "DOCS_SYNC"}
            for proof in item["required_proof"]["profiles"]
        ) for item in contract["items"])
        if behavioral or len(explanation.split()) < 6 or not any(path in closure["closure_summary"] for path in search_paths):
            _impact_mismatch("IMPACT_NOT_APPLICABLE_UNJUSTIFIED", "Prose exception requires concrete explanation and no behavioral/state/runtime obligations", closure)


def build_implementation_impact_closure(
    report: dict[str, Any], *, workspace: Path, changed_paths: list[str], candidate_id: str,
    plan: dict[str, Any] | None, contract: dict[str, Any], self_verification_ok: bool,
    owner_allowed_paths: Sequence[str] = (), revision_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_implementation_report(
        report, contract=contract, changed_paths=changed_paths, self_verification_ok=self_verification_ok,
        workspace=workspace, plan=plan, owner_allowed_paths=owner_allowed_paths, require_expanded=True,
    )
    if report["status"] != ImplementerStatus.COMPLETE.value:
        _impact_mismatch("POST_PATCH_NOT_COMPLETE", "Only final validated COMPLETE can mint an impact artifact", report["status"])
    artifact = {
        "schema_version": IMPLEMENTATION_IMPACT_CLOSURE_VERSION, "status": "PASS",
        "candidate_id": candidate_id, "plan_fingerprint": plan_fingerprint(plan) if plan is not None else None,
        "implementation_contract_fingerprint": contract["fingerprint"],
        "changed_paths": sorted(safe_impact_path(path, field="changed_paths") for path in changed_paths),
        "post_patch_impact": copy.deepcopy(report["post_patch_impact"]),
        "revision_binding": dict(revision_binding or {}),
    }
    artifact["fingerprint"] = stable_fingerprint(artifact, length=64)
    return artifact


def validate_implementation_impact_closure(
    artifact: Mapping[str, Any], *, candidate_id: str, plan: dict[str, Any] | None,
    contract: Mapping[str, Any], changed_paths: Sequence[str], revision_binding: Mapping[str, Any] | None = None,
) -> None:
    keys = {"schema_version", "status", "candidate_id", "plan_fingerprint", "implementation_contract_fingerprint", "changed_paths", "post_patch_impact", "revision_binding", "fingerprint"}
    ensure_exact_keys(dict(artifact), allowed=keys, required=keys, field="implementation_impact_closure")
    expected = stable_fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"}, length=64)
    if artifact["fingerprint"] != expected:
        _impact_mismatch("POST_PATCH_ARTIFACT_FINGERPRINT", "Impact artifact fingerprint mismatch", artifact["fingerprint"])
    if (
        artifact["schema_version"] != IMPLEMENTATION_IMPACT_CLOSURE_VERSION or artifact["status"] != "PASS"
        or artifact["candidate_id"] != candidate_id
        or artifact["plan_fingerprint"] != (plan_fingerprint(plan) if plan is not None else None)
        or artifact["implementation_contract_fingerprint"] != contract["fingerprint"]
        or artifact["changed_paths"] != sorted(safe_impact_path(path, field="changed_paths") for path in changed_paths)
        or artifact["revision_binding"] != dict(revision_binding or {})
    ):
        _impact_mismatch("POST_PATCH_ARTIFACT_STALE", "Impact artifact does not bind the current candidate/plan/contract/revisions", artifact)


def validate_implementation_report(
    report: dict[str, Any],
    *,
    contract: dict[str, Any],
    changed_paths: list[str],
    self_verification_ok: bool,
    documentation_paths: list[str] | None = None,
    workspace: Path,
    plan: dict[str, Any] | None = None,
    owner_allowed_paths: Sequence[str] = (),
    require_expanded: bool = False,
) -> None:
    """Validate implementer.v5 against the active definition and actual candidate.

    COMPLETE is strict and must close every active item. Non-complete terminal
    statuses need one concrete reason/evidence package, not a fabricated row for
    every contract item.
    """
    validate_implementation_contract(contract)
    if report.get("protocol_version") != IMPLEMENTER_PROTOCOL_VERSION:
        raise RuntimeError(f"Implementer protocol mismatch: {report.get('protocol_version')!r}")

    status_value = report.get("status")
    if status_value not in set(enum_values(ImplementerStatus)):
        raise RuntimeError(
            "Implementer status must be COMPLETE, REPLAN_REQUIRED, BLOCKED, or "
            "NEEDS_USER_DECISION"
        )
    if report.get("terminal_reason_kind") not in IMPLEMENTER_TERMINAL_REASONS[status_value]:
        raise RuntimeError(
            f"{status_value} requires terminal_reason_kind in "
            f"{IMPLEMENTER_TERMINAL_REASONS[status_value]!r}"
        )
    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise RuntimeError("Implementer summary must be a non-empty string")
    require_type(report.get("post_patch_impact"), dict, field="post_patch_impact")

    # Validate paths regardless of terminal status; report data must not escape
    # the repository even when the agent is blocked.
    for index, raw in enumerate(report.get("additional_check_paths", [])):
        safe_repo_relative(raw, field=f"additional_check_paths[{index}]")
    for raw in changed_paths:
        safe_repo_relative(raw, field="changed_path")

    if status_value != ImplementerStatus.COMPLETE.value:
        if report["terminal_reason_kind"] == "PROOF_MODEL_DIVERGENCE":
            # These fields are the allowlisted feedback carried to fresh Planner.
            # A generic blocker/ledger row cannot substitute for the proof diagnosis.
            if not isinstance(report.get("reason"), str) or not report["reason"].strip():
                raise RuntimeError("PROOF_MODEL_DIVERGENCE requires a concrete reason")
            evidence = report.get("evidence")
            if not isinstance(evidence, list) or not evidence or any(
                not isinstance(item, str) or not item.strip() for item in evidence
            ):
                raise RuntimeError("PROOF_MODEL_DIVERGENCE requires concrete evidence")
        blockers = report.get("blockers", [])
        reason = report.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            if isinstance(blockers, list):
                reason = next((str(item).strip() for item in blockers if str(item).strip()), "")
        evidence = report.get("evidence")
        if not isinstance(evidence, list) or not any(str(item).strip() for item in evidence):
            derived: list[str] = []
            for row in report.get("contract_evidence", []):
                if isinstance(row, Mapping):
                    derived.extend(
                        str(item).strip()
                        for item in row.get("evidence", [])
                        if str(item).strip()
                    )
            if isinstance(blockers, list):
                derived.extend(str(item).strip() for item in blockers if str(item).strip())
            evidence = derived
        if not reason:
            raise RuntimeError(f"{status_value} requires a concrete reason")
        if not evidence:
            raise RuntimeError(f"{status_value} requires concrete evidence")
        validate_post_patch_impact(
            report, workspace=workspace, changed_paths=changed_paths, plan=plan, contract=contract,
            owner_allowed_paths=owner_allowed_paths,
        )
        return

    from .phase4 import validate_implementer_report as validate_phase4_report

    validate_phase4_report(
        report,
        active_contract_items=contract["items"],
        require_receipt=False,
    )
    if not self_verification_ok:
        raise RuntimeError(
            "Implementer COMPLETE requires trusted self-verification PASS on the current candidate"
        )
    validate_post_patch_impact(
        report, workspace=workspace, changed_paths=changed_paths, plan=plan, contract=contract,
        owner_allowed_paths=owner_allowed_paths, require_expanded=require_expanded,
    )



def parse_implementation_report(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Implementer returned invalid JSON structured output.\n" + raw) from exc
    if not isinstance(value, dict):
        raise RuntimeError("Implementer structured output must be an object")
    return value
