from __future__ import annotations

from slivin_harness.boundaries import boundary

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from slivin_harness.protocol import ArtifactContractError, ArtifactFailureKind, ensure_exact_keys, plan_fingerprint, require_string_list, require_type, safe_repo_relative, stable_fingerprint
from slivin_harness.impact import impact_error, impact_paths, impact_text, safe_impact_path, validate_owner_prose_boundary, validate_impact_structure
from slivin_harness.task_contract import validate_task_contract
from slivin_harness.verification import (
    PROOF_TARGET_SCHEMA,
    merged_required_proof,
    validate_merged_required_proof,
    validate_proof_target,
)
from slivin_harness.workflow import ImplementerStatus, enum_values
from slivin_harness.source_records import (
    SOURCE_REF_SCHEMA, SOURCE_GROUPS, build_source_inventory, resolve_assessments,
    register_observations, source_ref, source_error, validate_source_inventory,
)

IMPLEMENTER_PROTOCOL_VERSION = "implementer.v6"
IMPLEMENTER_TERMINAL_REASONS = {
    "COMPLETE": ("NONE",),
    "REPLAN_REQUIRED": ("TECHNICAL_MODEL_DIVERGENCE", "PROOF_MODEL_DIVERGENCE"),
    "BLOCKED": ("INFRASTRUCTURE_BLOCKED",),
    "NEEDS_USER_DECISION": ("USER_DECISION_REQUIRED",),
}
IMPLEMENTATION_IMPACT_CLOSURE_VERSION = "implementation-impact-closure.v2"
IMPLEMENTATION_CONTRACT_VERSION = "implementation-contract.v4"
CONTRACT_ITEM_TYPES = {"acceptance", "preservation", "state", "consumer", "risk", "documentation"}
CONTRACT_ITEM_SOURCES = {"USER", "PLANNER", "USER+PLANNER", "DISCOVERED"}


def _implementation_contract_failure(code: str, field: str, message: str, actual: object = None) -> None:
    raise ArtifactContractError(
        code=code, field=field, message=message,
        expected="An intact Controller-compiled Implementation Contract", actual=actual,
        failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
    )


def _implementer_semantic_failure(code: str, field: str, message: str, actual: object = None) -> None:
    raise ArtifactContractError(
        code=code, field=field, message=message,
        expected="A semantically consistent Implementer report", actual=actual,
        failure_kind=ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT,
    )


def _impact_rows(*, text: Sequence[str], lists: Sequence[str] = (), proof: bool = False, enums: Mapping[str, Sequence[str]] | None = None) -> dict:
    properties = {key: {"type": "string"} for key in text}
    properties.update({key: {"type": "array", "items": {"type": "string"}} for key in lists})
    if proof:
        properties["required_proof"] = PROOF_TARGET_SCHEMA
    properties.update({key: {"type": "string", "enum": list(values)} for key, values in (enums or {}).items()})
    return {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}}


CANONICAL_IMPACT_SCHEMA = {
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

# The wire owns only new observations. Prior source prose lives once in the
# Controller's active Contract; models return explicit assessments by reference.
POST_PATCH_IMPACT_SCHEMA = copy.deepcopy(CANONICAL_IMPACT_SCHEMA)
for _group in SOURCE_GROUPS:
    _row_schema = POST_PATCH_IMPACT_SCHEMA["properties"][_group]["items"]
    _row_schema["properties"]["observation_id"] = {"type": "string"}
    _row_schema["properties"].pop("source", None)
    _row_schema["required"] = list(_row_schema["properties"])
_assessment = _impact_rows(text=("observation", "promotion_id"), lists=("paths", "symbols", "evidence"),
                          enums={"disposition": ("CONFIRM", "CHALLENGE", "PROMOTE", "INSUFFICIENT_EVIDENCE")})
_assessment["items"]["properties"]["source_ref"] = SOURCE_REF_SCHEMA
_assessment["items"]["required"].append("source_ref")
POST_PATCH_IMPACT_SCHEMA["properties"]["source_assessments"] = _assessment
POST_PATCH_IMPACT_SCHEMA["required"].append("source_assessments")

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


@boundary("B04")
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

        for index, consumer in enumerate(plan["impact_closure"]["in_scope_consumers"], start=1):
            _add_item(
                items,
                item_id=f"CONSUMER-{index}",
                item_type="consumer",
                source="PLANNER",
                requirement=(
                    f"{consumer['name']}\n"
                    f"Why affected: {consumer['why_affected']}\n"
                    f"Must verify: {consumer['required_behavior']}"
                ),
                required_proof=merged_required_proof(
                    [consumer["required_proof"]],
                    fallback_claim=consumer["required_behavior"],
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
        "proof_routes": [],
        "source_inventory": build_source_inventory(plan, task_fingerprint=task_contract["fingerprint"]),
    }
    payload["fingerprint"] = stable_fingerprint(payload)
    validate_implementation_contract(payload)
    return payload


def validate_implementation_contract(contract: Mapping[str, Any]) -> None:
    ensure_exact_keys(
        dict(contract),
        allowed={"protocol_version", "task_contract_fingerprint", "items", "warnings", "fingerprint", "source_inventory", "proof_routes"},
        required={"protocol_version", "task_contract_fingerprint", "items", "warnings", "fingerprint", "source_inventory", "proof_routes"},
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
        _implementation_contract_failure("CONTRACT_ITEMS_MISSING", "implementation_contract.items",
                                         "Implementation Contract requires at least one item", contract["items"])
    validate_source_inventory(contract["source_inventory"])
    ids: set[str] = set()
    for index, item in enumerate(contract["items"]):
        if not isinstance(item, dict):
            _implementation_contract_failure("CONTRACT_ITEM_TYPE", f"implementation_contract.items[{index}]",
                                             f"Implementation Contract item {index} must be an object", item)
        ensure_exact_keys(
            item,
            allowed={"id", "type", "source", "requirement", "required_proof", "allow_not_affected"},
            required={"id", "type", "source", "requirement", "required_proof", "allow_not_affected"},
            field=f"implementation_contract.items[{index}]",
        )
        item_id = str(item["id"])
        if not item_id or item_id in ids:
            _implementation_contract_failure("CONTRACT_ITEM_ID", f"implementation_contract.items[{index}].id",
                                             f"Implementation Contract item ids must be non-empty and unique: {item_id!r}", item_id)
        ids.add(item_id)
        if item["type"] not in CONTRACT_ITEM_TYPES:
            _implementation_contract_failure("CONTRACT_ITEM_KIND", f"implementation_contract.items[{index}].type",
                                             f"Unknown Implementation Contract item type: {item['type']}", item["type"])
        if item["source"] not in CONTRACT_ITEM_SOURCES:
            _implementation_contract_failure("CONTRACT_ITEM_SOURCE", f"implementation_contract.items[{index}].source",
                                             f"Unknown Implementation Contract source: {item['source']}", item["source"])
        if not isinstance(item["requirement"], str) or not item["requirement"].strip():
            _implementation_contract_failure("CONTRACT_REQUIREMENT_EMPTY", f"implementation_contract.items[{index}].requirement",
                                             f"Implementation Contract item {item_id} requires requirement text", item["requirement"])
        validate_merged_required_proof(
            item["required_proof"],
            field=f"implementation_contract.items[{index}].required_proof",
        )
        if item["allow_not_affected"] is not (item["type"] == "consumer"):
            _implementation_contract_failure("CONTRACT_NOT_AFFECTED_POLICY", f"implementation_contract.items[{index}].allow_not_affected",
                                             f"Only consumer items may allow NOT_AFFECTED: {item_id}", item["allow_not_affected"])
    from .proof_routes import effective_proofs
    effective_proofs(contract)
    if not any(item["type"] == "acceptance" for item in contract["items"]):
        _implementation_contract_failure("CONTRACT_ACCEPTANCE_MISSING", "implementation_contract.items",
                                         "Implementation Contract must contain acceptance")
    if not isinstance(contract["warnings"], list) or not all(isinstance(item, str) for item in contract["warnings"]):
        _implementation_contract_failure("CONTRACT_WARNINGS_TYPE", "implementation_contract.warnings",
                                         "Implementation Contract warnings must be strings", contract["warnings"])
    expected = stable_fingerprint({key: value for key, value in contract.items() if key != "fingerprint"})
    if contract["fingerprint"] != expected:
        _implementation_contract_failure("CONTRACT_FINGERPRINT", "implementation_contract.fingerprint",
                                         "Implementation Contract fingerprint mismatch", contract["fingerprint"])


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


def _impact_mismatch(code: str, message: str, actual: object) -> None:
    impact_error(code, field="post_patch_impact", message=message, actual=actual)


def _impact_artifact_failure(code: str, message: str, actual: object) -> None:
    raise ArtifactContractError(
        code=code, field="implementation_impact_closure", message=message,
        expected="Current immutable Controller-normalized impact artifact", actual=actual,
        failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
    )


def materialize_post_patch_impact(report: Mapping[str, Any], *, contract: Mapping[str, Any],
                                  plan: dict | None, require_expanded: bool = False) -> dict:
    """Assemble a presentation from immutable claims and explicit assessments.

    No model-generated source prose participates in inherited identity. The
    original evidence stays in source_inventory; current evidence is separate.
    This function does not issue verification evidence or acceptance.
    """
    wire = report["post_patch_impact"]
    inventory = contract["source_inventory"]
    expected = build_source_inventory(plan, task_fingerprint=contract["task_contract_fingerprint"])
    if inventory["namespace"] != expected["namespace"] or [
            row for row in inventory["records"] if row["author"] == "PLANNER"] != expected["records"]:
        source_error("SOURCE_PLAN_STALE", "Controller source inventory does not bind the current Planner")
    complete = report.get("status") == "COMPLETE"
    assessments = resolve_assessments(inventory, wire["source_assessments"], complete=complete)
    expanded, added = register_observations(inventory, wire)
    if require_expanded and (added or expanded != inventory):
        source_error("POST_PATCH_DISCOVERY_CONTRACT", "Observations must be admitted before final COMPLETE", added)
    if complete and plan is not None and wire["changed_contracts"]:
        source_error("POST_PATCH_MODEL_DIVERGENCE", "A new technical model requires explicit semantic replan")
    canonical = {key: copy.deepcopy(wire[key]) for key in CANONICAL_IMPACT_SCHEMA["properties"]}
    for group in SOURCE_GROUPS:
        canonical[group] = []
    new_records = {row["source_id"]: row for row in expanded["records"] if row["source_id"] in added}

    def append(origin: dict, assessment: dict | None):
        group = origin["group"]
        row = copy.deepcopy(origin["claim"])
        row.pop("observation_id", None)
        if group == "changed_contracts" and origin["author"] == "PLANNER":
            row["paths"] = row.pop("evidence_paths")
            row["symbols"] = row.pop("evidence_symbols")
            row["evidence"] = []
        if assessment is not None:
            # Observations do not rewrite the original evidence inventory.
            row["paths"] = list(assessment["paths"])
            row["symbols"] = list(assessment["symbols"])
            row["evidence"] = list(assessment["evidence"])
        if group == "in_scope_consumers":
            row["source"] = "PLANNER" if origin["author"] == "PLANNER" else "DISCOVERED"
        row["source_ref"] = source_ref(origin)
        canonical[group].append(row)

    for origin, assessment in assessments:
        if assessment["disposition"] == "PROMOTE":
            target = f"I-{inventory['namespace']}-{assessment['promotion_id']}"
            promoted_record = next((row for row in expanded["records"] if row["source_id"] == target), None)
            if promoted_record is None or promoted_record["group"] != "in_scope_consumers":
                source_error("SOURCE_PROMOTION_INVALID", "PROMOTE requires an explicit in-scope observation", target)
            continue
        if assessment["promotion_id"]:
            source_error("SOURCE_PROMOTION_INVALID", "Only PROMOTE may carry promotion_id")
        append(origin, assessment)
    # Repeated known observations require assessments as well; they are not
    # another authority and do not create a second expansion side effect.
    for origin in new_records.values():
        append(origin, None)
    return canonical


def report_discoveries(report: Mapping[str, Any], *, contract: Mapping[str, Any], plan: dict | None) -> list[dict]:
    """Transient compiler input, derived once; not an agent wire ledger."""
    closure = materialize_post_patch_impact(report, contract=contract, plan=plan)
    result = []
    for group, kind, reason, behavior in (
        ("in_scope_consumers", "consumer", "why_affected", "required_behavior"),
        ("new_risks", "risk", "reason", "failure_mode"),
    ):
        for row in closure[group]:
            if group == "in_scope_consumers" and row["source"] != "DISCOVERED":
                continue
            result.append(dict(kind=kind, name=row["name"], reason=row[reason],
                               required_behavior=row[behavior], required_proof=copy.deepcopy(row["required_proof"]),
                               evidence=list(row["evidence"])))
    return result


def validate_post_patch_impact(
    report: Mapping[str, Any], *, workspace: Path, changed_paths: Sequence[str],
    plan: dict[str, Any] | None, contract: Mapping[str, Any],
    owner_allowed_paths: Sequence[str] = (), require_expanded: bool = False,
) -> None:
    """Reconcile actual-patch evidence; only Controller facts authorize COMPLETE."""
    closure = report.get("post_patch_impact")
    validate_impact_structure(closure, schema=POST_PATCH_IMPACT_SCHEMA,
                              workspace=workspace, field="post_patch_impact")
    closure = materialize_post_patch_impact(report, contract=contract, plan=plan, require_expanded=require_expanded)
    properties = CANONICAL_IMPACT_SCHEMA["properties"]
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
            if group == "search_evidence":
                search_paths.extend(safe_impact_path(path, field=row_field) for path in row["evidence_paths"])
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

    # Source assessments retain all origin claims. Discoveries are compiled by
    # the Controller and are never compared to a second model-authored copy.
    from slivin_harness.phase5 import _discovery_requirement
    discoveries = report_discoveries(report, contract=contract, plan=plan)
    active = {(item["type"], item["requirement"]): item for item in contract["items"] if item["source"] == "DISCOVERED"}
    declared = {(row["kind"], _discovery_requirement(row)): row for row in discoveries}
    if not active.keys() <= declared.keys() or (require_expanded and active.keys() != declared.keys()):
        _impact_mismatch("POST_PATCH_DISCOVERY_CONTRACT", "Every discovered obligation must remain and be expanded", sorted(declared))
    for key, item in active.items():
        row = declared[key]
        if item["required_proof"] != merged_required_proof([row["required_proof"]], fallback_claim=row["required_behavior"]):
            _impact_mismatch("POST_PATCH_DISCOVERY_CONTRACT", "A discovered proof cannot be silently changed", row)

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
        "post_patch_impact": materialize_post_patch_impact(report, contract=contract, plan=plan, require_expanded=True),
        "source_inventory": copy.deepcopy(contract["source_inventory"]),
        "source_assessments": copy.deepcopy(report["post_patch_impact"]["source_assessments"]),
        "revision_binding": dict(revision_binding or {}),
    }
    artifact["fingerprint"] = stable_fingerprint(artifact, length=64)
    return artifact


def validate_implementation_impact_closure(
    artifact: Mapping[str, Any], *, candidate_id: str, plan: dict[str, Any] | None,
    contract: Mapping[str, Any], changed_paths: Sequence[str], revision_binding: Mapping[str, Any] | None = None,
) -> None:
    keys = {"schema_version", "status", "candidate_id", "plan_fingerprint", "implementation_contract_fingerprint", "changed_paths", "post_patch_impact", "source_inventory", "source_assessments", "revision_binding", "fingerprint"}
    if set(artifact) != keys:
        _impact_artifact_failure("POST_PATCH_ARTIFACT_FIELDS", "Impact artifact fields are invalid", sorted(artifact))
    expected = stable_fingerprint({key: value for key, value in artifact.items() if key != "fingerprint"}, length=64)
    if artifact["fingerprint"] != expected:
        _impact_artifact_failure("POST_PATCH_ARTIFACT_FINGERPRINT", "Impact artifact fingerprint mismatch", artifact["fingerprint"])
    if (
        artifact["schema_version"] != IMPLEMENTATION_IMPACT_CLOSURE_VERSION or artifact["status"] != "PASS"
        or artifact["candidate_id"] != candidate_id
        or artifact["plan_fingerprint"] != (plan_fingerprint(plan) if plan is not None else None)
        or artifact["source_inventory"] != contract["source_inventory"]
        or artifact["implementation_contract_fingerprint"] != contract["fingerprint"]
        or artifact["changed_paths"] != sorted(safe_impact_path(path, field="changed_paths") for path in changed_paths)
        or artifact["revision_binding"] != dict(revision_binding or {})
    ):
        _impact_artifact_failure("POST_PATCH_ARTIFACT_STALE", "Impact artifact does not bind the current candidate/plan/contract/revisions", artifact)
    replay = copy.deepcopy(artifact["post_patch_impact"])
    for group in SOURCE_GROUPS:
        replay[group] = []
    replay["source_assessments"] = copy.deepcopy(artifact["source_assessments"])
    reconstructed = materialize_post_patch_impact(
        {"status": "COMPLETE", "post_patch_impact": replay}, contract=contract, plan=plan, require_expanded=True,
    )
    if reconstructed != artifact["post_patch_impact"]:
        _impact_artifact_failure("POST_PATCH_SOURCE_COVERAGE", "Canonical impact differs from immutable origins and explicit assessments", None)


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
    """Validate implementer.v6 against the active definition and actual candidate.

    COMPLETE is strict and must close every active item. Non-complete terminal
    statuses need one concrete reason/evidence package, not a fabricated row for
    every contract item.
    """
    validate_implementation_contract(contract)
    ensure_exact_keys(report, allowed=IMPLEMENTER_REPORT_SCHEMA["properties"],
                      required=IMPLEMENTER_REPORT_SCHEMA["required"], field="report")
    if report.get("protocol_version") != IMPLEMENTER_PROTOCOL_VERSION:
        raise ArtifactContractError(
            code="IMPLEMENTER_VERSION", field="protocol_version",
            message=f"Implementer protocol mismatch: {report.get('protocol_version')!r}",
            expected=IMPLEMENTER_PROTOCOL_VERSION, actual=report.get("protocol_version"),
        )

    status_value = report.get("status")
    if status_value not in set(enum_values(ImplementerStatus)):
        _implementer_semantic_failure(
            "IMPLEMENTER_STATUS", "status",
            "Implementer status must be COMPLETE, REPLAN_REQUIRED, BLOCKED, or NEEDS_USER_DECISION",
            status_value,
        )
    if report.get("terminal_reason_kind") not in IMPLEMENTER_TERMINAL_REASONS[status_value]:
        _implementer_semantic_failure(
            "IMPLEMENTER_TERMINAL_REASON", "terminal_reason_kind",
            f"{status_value} requires terminal_reason_kind in {IMPLEMENTER_TERMINAL_REASONS[status_value]!r}",
            report.get("terminal_reason_kind"),
        )
    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ArtifactContractError(code="IMPLEMENTER_SUMMARY_EMPTY", field="summary",
            message="Implementer summary must be a non-empty string",
            expected="A non-empty summary", actual=summary)
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
                _implementer_semantic_failure("PROOF_DIVERGENCE_REASON", "reason",
                                              "PROOF_MODEL_DIVERGENCE requires a concrete reason")
            evidence = report.get("evidence")
            if not isinstance(evidence, list) or not evidence or any(
                not isinstance(item, str) or not item.strip() for item in evidence
            ):
                _implementer_semantic_failure("PROOF_DIVERGENCE_EVIDENCE", "evidence",
                                              "PROOF_MODEL_DIVERGENCE requires concrete evidence", evidence)
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
            _implementer_semantic_failure("IMPLEMENTER_REASON_MISSING", "reason",
                                          f"{status_value} requires a concrete reason")
        if not evidence:
            _implementer_semantic_failure("IMPLEMENTER_EVIDENCE_MISSING", "evidence",
                                          f"{status_value} requires concrete evidence", evidence)
        validate_post_patch_impact(
            report, workspace=workspace, changed_paths=changed_paths, plan=plan, contract=contract,
            owner_allowed_paths=owner_allowed_paths,
        )
        return

    from .phase4 import Phase4ContractError, validate_implementer_report as validate_phase4_report

    try:
        validate_phase4_report(
            report,
            active_contract_items=contract["items"],
            require_receipt=False,
        )
    except Phase4ContractError as error:
        raise ArtifactContractError(
            code="IMPLEMENTER_REPORT_SEMANTIC_CONFLICT", field="report",
            message=str(error), expected="A report consistent with every active Contract item",
            failure_kind=ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT,
        ) from error
    if not self_verification_ok:
        raise ArtifactContractError(
            code="IMPLEMENTER_TRUSTED_VERIFICATION_MISSING", field="self_verification",
            message="Implementer COMPLETE requires trusted self-verification PASS on the current candidate",
            expected="Controller-confirmed self-verification PASS",
            failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
        )
    validate_post_patch_impact(
        report, workspace=workspace, changed_paths=changed_paths, plan=plan, contract=contract,
        owner_allowed_paths=owner_allowed_paths, require_expanded=require_expanded,
    )



def parse_implementation_report(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArtifactContractError(code="REPORT_INVALID_JSON", field="report", message="Implementer returned invalid JSON", expected="An Implementer JSON object") from exc
    if not isinstance(value, dict):
        raise ArtifactContractError(code="TYPE_MISMATCH", field="report", message="Implementer report must be an object", expected="An Implementer JSON object")
    return value
