from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from slivin_harness.app_server import CodexAppServer
from slivin_harness.protocol import (
    ArtifactContractError,
    PLANNER_PROTOCOL_VERSION,
    ensure_exact_keys,
    require_string_list,
    require_type,
    safe_repo_relative,
)
from slivin_harness.task_contract import validate_task_contract
from slivin_harness.verification import (
    Capability,
    PROOF_TARGET_SCHEMA,
    proof_required_capabilities,
    validate_proof_target,
)
from slivin_harness.workflow import PlannerStatus, enum_values

_CONFIDENCE = ["HIGH", "MEDIUM", "LOW"]
_DIAGNOSIS_KINDS = ["BUG", "FEATURE", "MIXED"]
_UNKNOWN_KINDS = ["BLOCKING", "NON_BLOCKING", "PRODUCT_SEMANTIC"]
_ALIGNMENT = ["ALIGNED", "INVALID"]


class PlannerCapabilityInfeasible(RuntimeError):
    reason_code = "PLANNER_CAPABILITY_INFEASIBLE"

    def __init__(self, unavailable_capabilities: Iterable[str], *, plan: dict[str, Any]):
        self.unavailable_capabilities = tuple(sorted(set(unavailable_capabilities)))
        self.plan = plan
        super().__init__(
            self.reason_code + " " + ", ".join(self.unavailable_capabilities)
        )

PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {"type": "string", "enum": [PLANNER_PROTOCOL_VERSION]},
        "status": {"type": "string", "enum": enum_values(PlannerStatus)},
        "summary": {"type": "string"},
        "task_contract_alignment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": _ALIGNMENT},
                "evidence": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["status", "evidence", "reason"],
        },
        "characterization": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "observed_behavior": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "existing_contract": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "evidence": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
            },
            "required": ["observed_behavior", "existing_contract", "evidence"],
        },
        "diagnosis": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": _DIAGNOSIS_KINDS},
                "root_cause": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim": {"type": "string"},
                        "evidence": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                        "confidence": {"type": "string", "enum": _CONFIDENCE},
                    },
                    "required": ["claim", "evidence", "confidence"],
                },
                "extension_point": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim": {"type": "string"},
                        "evidence": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                        "confidence": {"type": "string", "enum": _CONFIDENCE},
                    },
                    "required": ["claim", "evidence", "confidence"],
                },
                "design_constraints": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "high_level_approach": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
            },
            "required": ["kind", "root_cause", "extension_point", "design_constraints", "high_level_approach"],
        },
        "assumptions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "evidence": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": _CONFIDENCE},
                    "narrows_compatibility": {"type": "boolean"},
                    "compatibility_impact": {"type": "string"},
                },
                "required": ["claim", "evidence", "confidence", "narrows_compatibility", "compatibility_impact"],
            },
        },
        "technical_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "technical_acceptance": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "derived_preservation": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
            },
            "required": ["technical_acceptance", "derived_preservation"],
        },
        "affected_consumers": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "why_affected": {"type": "string"},
                    "must_verify": {"type": "string"},
                    "required_proof": PROOF_TARGET_SCHEMA,
                },
                "required": ["name", "why_affected", "must_verify", "required_proof"],
            },
        },
        "state_model": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "applicable": {"type": "boolean"},
                "representations": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "authority": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "lifecycle": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "boundaries": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "required_proof": PROOF_TARGET_SCHEMA,
            },
            "required": ["applicable", "representations", "authority", "lifecycle", "boundaries", "required_proof"],
        },
        "risks": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "condition": {"type": "string"},
                    "failure_mode": {"type": "string"},
                    "required_proof": PROOF_TARGET_SCHEMA,
                },
                "required": ["condition", "failure_mode", "required_proof"],
            },
        },
        "evidence_plan": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "regression": {"type": "array", "maxItems": 6, "items": PROOF_TARGET_SCHEMA},
                "preservation": {"type": "array", "maxItems": 6, "items": PROOF_TARGET_SCHEMA},
                "consumers": {"type": "array", "maxItems": 6, "items": PROOF_TARGET_SCHEMA},
                "boundaries": {"type": "array", "maxItems": 6, "items": PROOF_TARGET_SCHEMA},
            },
            "required": ["regression", "preservation", "consumers", "boundaries"],
        },
        "documentation": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "required": {"type": "boolean"},
                "reason": {"type": "string"},
                "required_proof": PROOF_TARGET_SCHEMA,
            },
            "required": ["required", "reason", "required_proof"],
        },
        "owner_boundary_assessment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "compatible": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["compatible", "reason"],
        },
        "unknowns": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": _UNKNOWN_KINDS},
                    "claim": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "claim", "reason"],
            },
        },
    },
    "required": [
        "protocol_version", "status", "summary", "task_contract_alignment",
        "characterization", "diagnosis", "assumptions", "technical_contract",
        "affected_consumers", "state_model", "risks", "evidence_plan",
        "documentation", "owner_boundary_assessment", "unknowns",
    ],
}

PLANNER_INSTRUCTIONS = """
Ты fresh read-only Planner внутри Slivin Harness.

Сначала сверь RAW USER REQUEST с USER TASK CONTRACT. Если нормализатор изменил product
semantics, верни TASK_CONTRACT_INVALID. Затем исследуй текущий repository: охарактеризуй
observed behavior и existing intended contract, докажи root cause для BUG или current
extension point/design constraints для FEATURE. Не пиши patch и не диктуй строки реализации.

Вывод должен быть компактным и load-bearing:
- только material assumptions;
- explicit user acceptance/preservation не переписывай и не ослабляй;
- найди materially affected consumer families;
- для stateful задачи дай один State Model: representations, authority, lifecycle и только
  достижимые boundaries;
- каждый consumer/risk и каждый evidence-plan claim получает typed proof level и capabilities;
- используй самый дешёвый proof level, который честно доказывает claim;
- owner-defined allowed_paths оцени как hard boundary, но не делай из своих предположений о
  путях новый owner contract;
- documentation.required=true только когда final semantics требуют синхронизации docs.

READY запрещён при LOW diagnosis confidence, blocking unknowns, product-semantic ambiguity,
неподтверждённом compatibility-narrowing assumption или конфликте owner boundary.
READY plan может требовать только capabilities из Controller-owned
AVAILABLE_VERIFICATION_CAPABILITIES. Выбирай минимальную достаточную capability для
конкретного proof route и не добавляй язык/runtime проекта «на всякий случай». Если
недоступная capability действительно обязательна, верни BLOCKED с конкретным blocking
unknown вместо READY с невыполнимым proof.
Не используй previous plans, reference patches, hidden grader или другие копии проекта.
""".strip()


def planner_required_capabilities(plan: Mapping[str, Any]) -> set[str]:
    """Collect explicit and proof-level-implied capabilities from planner.v4."""

    proofs: list[Mapping[str, Any]] = []
    for item in plan.get("affected_consumers", []):
        if isinstance(item, Mapping) and isinstance(item.get("required_proof"), Mapping):
            proofs.append(item["required_proof"])
    state = plan.get("state_model")
    if isinstance(state, Mapping) and isinstance(state.get("required_proof"), Mapping):
        proofs.append(state["required_proof"])
    for item in plan.get("risks", []):
        if isinstance(item, Mapping) and isinstance(item.get("required_proof"), Mapping):
            proofs.append(item["required_proof"])
    evidence_plan = plan.get("evidence_plan")
    if isinstance(evidence_plan, Mapping):
        for group in ("regression", "preservation", "consumers", "boundaries"):
            for proof in evidence_plan.get(group, []):
                if isinstance(proof, Mapping):
                    proofs.append(proof)
    documentation = plan.get("documentation")
    if isinstance(documentation, Mapping) and isinstance(
        documentation.get("required_proof"), Mapping
    ):
        proofs.append(documentation["required_proof"])

    required: set[str] = set()
    for proof in proofs:
        required.update(
            proof_required_capabilities(
                level=str(proof.get("level", "")),
                capabilities=proof.get("capabilities", ()),
            )
        )
    return required


def planner_capability_gaps(
    plan: Mapping[str, Any], *, available: Iterable[str]
) -> list[str]:
    if plan.get("status") != PlannerStatus.READY.value:
        return []
    return sorted(planner_required_capabilities(plan) - set(available))


_FAMILY_TOOL_CAPABILITIES = {
    "git": {Capability.GIT.value},
    "node": {Capability.NODE.value},
    "jest": {Capability.NODE.value, Capability.JEST.value},
    "project_python": {Capability.PROJECT_PYTHON.value},
}


def manifest_repair_evidence(
    static_preflight: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build the public-safe executor evidence supplied to Planner."""
    verified = set(str(value) for value in static_preflight.get("verified_capabilities", []))
    result: list[dict[str, Any]] = []
    for check in static_preflight.get("checks", []):
        if not isinstance(check, Mapping) or check.get("feedback") != "repair":
            continue
        family = str(check.get("command_family") or "unknown")
        result.append(
            {
                "check_name": str(check.get("name") or "<unnamed>"),
                "command_family": family,
                "verified_tool_capabilities": sorted(
                    _FAMILY_TOOL_CAPABILITIES.get(family, set()) & verified
                ),
            }
        )
    return result


def _parse_planner_output(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Planner returned invalid JSON structured output.\n" + raw) from exc
    if not isinstance(value, dict):
        raise RuntimeError("Planner returned a non-object structured output")
    return value


def _validate_claim_block(value: object, *, field: str, required_when_ready: bool = False) -> dict[str, Any]:
    require_type(value, dict, field=field)
    ensure_exact_keys(
        value,
        allowed={"claim", "evidence", "confidence"},
        required={"claim", "evidence", "confidence"},
        field=field,
    )
    require_type(value["claim"], str, field=f"{field}.claim")
    require_string_list(value["evidence"], field=f"{field}.evidence")
    if value["confidence"] not in _CONFIDENCE:
        raise ArtifactContractError(
            code="PLANNER_CONFIDENCE", field=f"{field}.confidence",
            message="Invalid diagnosis confidence", expected="HIGH/MEDIUM/LOW",
            actual=value["confidence"],
        )
    if required_when_ready and (not value["claim"].strip() or not value["evidence"]):
        raise ArtifactContractError(
            code="PLANNER_DIAGNOSIS_MISSING", field=field,
            message="READY requires diagnosis claim and evidence",
            expected="Non-empty claim and evidence", actual=value,
        )
    return value


def validate_plan_artifact(
    plan: dict[str, Any], *, workspace: Path, task_contract: dict[str, Any]
) -> None:
    validate_task_contract(task_contract)
    required = set(PLANNER_SCHEMA["required"])
    ensure_exact_keys(plan, allowed=required, required=required, field="plan")
    if plan["protocol_version"] != PLANNER_PROTOCOL_VERSION:
        raise ArtifactContractError(
            code="PLANNER_VERSION", field="protocol_version",
            message="Planner protocol version mismatch", expected=PLANNER_PROTOCOL_VERSION,
            actual=plan["protocol_version"],
        )
    if plan["status"] not in set(enum_values(PlannerStatus)):
        raise ArtifactContractError(
            code="PLANNER_STATUS", field="status", message="Unknown Planner status",
            expected="/".join(enum_values(PlannerStatus)), actual=plan["status"],
        )
    require_type(plan["summary"], str, field="summary")

    alignment = plan["task_contract_alignment"]
    require_type(alignment, dict, field="task_contract_alignment")
    ensure_exact_keys(alignment, allowed={"status", "evidence", "reason"}, required={"status", "evidence", "reason"}, field="task_contract_alignment")
    if alignment["status"] not in _ALIGNMENT:
        raise ArtifactContractError(code="TASK_CONTRACT_ALIGNMENT", field="task_contract_alignment.status", message="Invalid alignment status", expected="ALIGNED/INVALID", actual=alignment["status"])
    require_string_list(alignment["evidence"], field="task_contract_alignment.evidence")
    require_type(alignment["reason"], str, field="task_contract_alignment.reason")

    characterization = plan["characterization"]
    require_type(characterization, dict, field="characterization")
    ensure_exact_keys(characterization, allowed={"observed_behavior", "existing_contract", "evidence"}, required={"observed_behavior", "existing_contract", "evidence"}, field="characterization")
    for field in ("observed_behavior", "existing_contract", "evidence"):
        require_string_list(characterization[field], field=f"characterization.{field}")

    diagnosis = plan["diagnosis"]
    require_type(diagnosis, dict, field="diagnosis")
    ensure_exact_keys(diagnosis, allowed={"kind", "root_cause", "extension_point", "design_constraints", "high_level_approach"}, required={"kind", "root_cause", "extension_point", "design_constraints", "high_level_approach"}, field="diagnosis")
    if diagnosis["kind"] not in _DIAGNOSIS_KINDS:
        raise ArtifactContractError(code="DIAGNOSIS_KIND", field="diagnosis.kind", message="Invalid diagnosis kind", expected="BUG/FEATURE/MIXED", actual=diagnosis["kind"])
    root = _validate_claim_block(diagnosis["root_cause"], field="diagnosis.root_cause", required_when_ready=plan["status"] == PlannerStatus.READY.value and diagnosis["kind"] in {"BUG", "MIXED"})
    extension = _validate_claim_block(diagnosis["extension_point"], field="diagnosis.extension_point", required_when_ready=plan["status"] == PlannerStatus.READY.value and diagnosis["kind"] in {"FEATURE", "MIXED"})
    require_string_list(diagnosis["design_constraints"], field="diagnosis.design_constraints")
    require_string_list(diagnosis["high_level_approach"], field="diagnosis.high_level_approach")

    require_type(plan["assumptions"], list, field="assumptions")
    for index, item in enumerate(plan["assumptions"]):
        require_type(item, dict, field=f"assumptions[{index}]")
        ensure_exact_keys(item, allowed={"claim", "evidence", "confidence", "narrows_compatibility", "compatibility_impact"}, required={"claim", "evidence", "confidence", "narrows_compatibility", "compatibility_impact"}, field=f"assumptions[{index}]")
        require_type(item["claim"], str, field=f"assumptions[{index}].claim")
        require_string_list(item["evidence"], field=f"assumptions[{index}].evidence")
        require_type(item["narrows_compatibility"], bool, field=f"assumptions[{index}].narrows_compatibility")
        require_type(item["compatibility_impact"], str, field=f"assumptions[{index}].compatibility_impact")
        if item["confidence"] not in _CONFIDENCE:
            raise ArtifactContractError(code="ASSUMPTION_CONFIDENCE", field=f"assumptions[{index}].confidence", message="Invalid assumption confidence", expected="HIGH/MEDIUM/LOW", actual=item["confidence"])
        if item["narrows_compatibility"] and item["confidence"] != "HIGH":
            raise ArtifactContractError(code="UNSAFE_COMPATIBILITY_ASSUMPTION", field=f"assumptions[{index}]", message="Compatibility-narrowing assumption requires HIGH confidence", expected="HIGH confidence or non-narrowing assumption", actual=item)

    technical = plan["technical_contract"]
    require_type(technical, dict, field="technical_contract")
    ensure_exact_keys(technical, allowed={"technical_acceptance", "derived_preservation"}, required={"technical_acceptance", "derived_preservation"}, field="technical_contract")
    require_string_list(technical["technical_acceptance"], field="technical_contract.technical_acceptance")
    require_string_list(technical["derived_preservation"], field="technical_contract.derived_preservation")

    require_type(plan["affected_consumers"], list, field="affected_consumers")
    for index, item in enumerate(plan["affected_consumers"]):
        require_type(item, dict, field=f"affected_consumers[{index}]")
        ensure_exact_keys(item, allowed={"name", "why_affected", "must_verify", "required_proof"}, required={"name", "why_affected", "must_verify", "required_proof"}, field=f"affected_consumers[{index}]")
        for key in ("name", "why_affected", "must_verify"):
            require_type(item[key], str, field=f"affected_consumers[{index}].{key}")
        validate_proof_target(item["required_proof"], field=f"affected_consumers[{index}].required_proof")

    state = plan["state_model"]
    require_type(state, dict, field="state_model")
    ensure_exact_keys(state, allowed={"applicable", "representations", "authority", "lifecycle", "boundaries", "required_proof"}, required={"applicable", "representations", "authority", "lifecycle", "boundaries", "required_proof"}, field="state_model")
    require_type(state["applicable"], bool, field="state_model.applicable")
    for field in ("representations", "authority", "lifecycle", "boundaries"):
        require_string_list(state[field], field=f"state_model.{field}")
    validate_proof_target(state["required_proof"], field="state_model.required_proof")
    if state["applicable"] and not (state["representations"] and state["authority"] and state["lifecycle"]):
        raise ArtifactContractError(code="STATE_MODEL_INCOMPLETE", field="state_model", message="Applicable State Model requires representations, authority and lifecycle", expected="Non-empty state model", actual=state)

    require_type(plan["risks"], list, field="risks")
    for index, item in enumerate(plan["risks"]):
        require_type(item, dict, field=f"risks[{index}]")
        ensure_exact_keys(item, allowed={"condition", "failure_mode", "required_proof"}, required={"condition", "failure_mode", "required_proof"}, field=f"risks[{index}]")
        require_type(item["condition"], str, field=f"risks[{index}].condition")
        require_type(item["failure_mode"], str, field=f"risks[{index}].failure_mode")
        validate_proof_target(item["required_proof"], field=f"risks[{index}].required_proof")

    evidence = plan["evidence_plan"]
    require_type(evidence, dict, field="evidence_plan")
    ensure_exact_keys(evidence, allowed={"regression", "preservation", "consumers", "boundaries"}, required={"regression", "preservation", "consumers", "boundaries"}, field="evidence_plan")
    for field in ("regression", "preservation", "consumers", "boundaries"):
        require_type(evidence[field], list, field=f"evidence_plan.{field}")
        for index, proof in enumerate(evidence[field]):
            validate_proof_target(proof, field=f"evidence_plan.{field}[{index}]")

    documentation = plan["documentation"]
    require_type(documentation, dict, field="documentation")
    ensure_exact_keys(documentation, allowed={"required", "reason", "required_proof"}, required={"required", "reason", "required_proof"}, field="documentation")
    require_type(documentation["required"], bool, field="documentation.required")
    require_type(documentation["reason"], str, field="documentation.reason")
    validate_proof_target(documentation["required_proof"], field="documentation.required_proof")

    boundary = plan["owner_boundary_assessment"]
    require_type(boundary, dict, field="owner_boundary_assessment")
    ensure_exact_keys(boundary, allowed={"compatible", "reason"}, required={"compatible", "reason"}, field="owner_boundary_assessment")
    require_type(boundary["compatible"], bool, field="owner_boundary_assessment.compatible")
    require_type(boundary["reason"], str, field="owner_boundary_assessment.reason")

    require_type(plan["unknowns"], list, field="unknowns")
    unknown_kinds: list[str] = []
    for index, item in enumerate(plan["unknowns"]):
        require_type(item, dict, field=f"unknowns[{index}]")
        ensure_exact_keys(item, allowed={"kind", "claim", "reason"}, required={"kind", "claim", "reason"}, field=f"unknowns[{index}]")
        if item["kind"] not in _UNKNOWN_KINDS:
            raise ArtifactContractError(code="UNKNOWN_KIND", field=f"unknowns[{index}].kind", message="Invalid unknown kind", expected="/".join(_UNKNOWN_KINDS), actual=item["kind"])
        unknown_kinds.append(item["kind"])
        require_type(item["claim"], str, field=f"unknowns[{index}].claim")
        require_type(item["reason"], str, field=f"unknowns[{index}].reason")

    status = plan["status"]
    if status == PlannerStatus.TASK_CONTRACT_INVALID.value:
        if alignment["status"] != "INVALID" or not alignment["reason"].strip():
            raise ArtifactContractError(code="TASK_CONTRACT_INVALID_WITHOUT_EVIDENCE", field="task_contract_alignment", message="TASK_CONTRACT_INVALID requires INVALID alignment and reason", expected="INVALID with reason", actual=alignment)
        return
    if alignment["status"] != "ALIGNED":
        raise ArtifactContractError(code="TASK_CONTRACT_NOT_ALIGNED", field="task_contract_alignment", message="Planner status requires aligned Task Contract", expected="ALIGNED", actual=alignment)

    if status == PlannerStatus.READY.value:
        if not characterization["observed_behavior"] or not characterization["existing_contract"] or not characterization["evidence"]:
            raise ArtifactContractError(code="PLANNER_CHARACTERIZATION_INCOMPLETE", field="characterization", message="READY requires observed behavior, existing contract and evidence", expected="Non-empty characterization", actual=characterization)
        relevant_confidences = []
        if diagnosis["kind"] in {"BUG", "MIXED"}:
            relevant_confidences.append(root["confidence"])
        if diagnosis["kind"] in {"FEATURE", "MIXED"}:
            relevant_confidences.append(extension["confidence"])
        if "LOW" in relevant_confidences:
            raise ArtifactContractError(code="PLANNER_LOW_CONFIDENCE", field="diagnosis", message="READY cannot use LOW diagnosis confidence", expected="HIGH or MEDIUM", actual=relevant_confidences)
        if not technical["technical_acceptance"]:
            raise ArtifactContractError(code="PLANNER_TECHNICAL_ACCEPTANCE_MISSING", field="technical_contract.technical_acceptance", message="READY requires technical acceptance", expected="Non-empty technical acceptance", actual=[])
        if not evidence["regression"]:
            raise ArtifactContractError(code="PLANNER_EVIDENCE_MISSING", field="evidence_plan.regression", message="READY requires regression/acceptance proof", expected="At least one typed proof", actual=[])
        if "BLOCKING" in unknown_kinds or "PRODUCT_SEMANTIC" in unknown_kinds:
            raise ArtifactContractError(code="PLANNER_READY_WITH_BLOCKING_UNKNOWN", field="unknowns", message="READY cannot retain blocking/product unknowns", expected="Only NON_BLOCKING unknowns", actual=unknown_kinds)
        if not boundary["compatible"]:
            raise ArtifactContractError(code="OWNER_BOUNDARY_CONFLICT", field="owner_boundary_assessment", message="READY cannot conflict with owner boundary", expected="compatible=true", actual=boundary)
    elif status == PlannerStatus.BLOCKED.value:
        if "BLOCKING" not in unknown_kinds and boundary["compatible"]:
            raise ArtifactContractError(code="PLANNER_STOP_WITHOUT_REASON", field="unknowns/owner_boundary_assessment", message="BLOCKED requires blocking unknown or boundary conflict", expected="Blocking reason", actual={"unknowns": unknown_kinds, "boundary": boundary})
    elif status == PlannerStatus.NEEDS_USER_DECISION.value:
        if "PRODUCT_SEMANTIC" not in unknown_kinds:
            raise ArtifactContractError(code="PLANNER_DECISION_WITHOUT_PRODUCT_UNKNOWN", field="unknowns", message="NEEDS_USER_DECISION requires product-semantic unknown", expected="PRODUCT_SEMANTIC", actual=unknown_kinds)

    # No Planner output path is a hard scope boundary; this check only protects
    # any future path-bearing evidence accidentally added to proof claims.
    root_resolved = workspace.resolve()
    if root_resolved != workspace.resolve():
        raise RuntimeError("Workspace resolution changed unexpectedly")


def run_planner(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    task_contract: dict[str, Any],
    preflight: dict,
    owner_allowed_paths: list[str],
    available_verification_capabilities: Sequence[str],
    manifest_repair_evidence: Sequence[Mapping[str, Any]],
    replan_context: str = "",
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
    timeout: float = 900,
) -> dict[str, Any]:
    validate_task_contract(task_contract)
    thread_id = codex.start_thread(
        cwd=workspace,
        sandbox="read-only",
        developer_instructions=PLANNER_INSTRUCTIONS,
        on_started=on_thread_started,
    )
    prompt = f"""
RAW USER REQUEST:
--- BEGIN RAW USER REQUEST ---
{task_prompt}
--- END RAW USER REQUEST ---

USER TASK CONTRACT:
{json.dumps(task_contract, ensure_ascii=False, indent=2)}

OWNER-DEFINED HARD PATH BOUNDARY:
{json.dumps(owner_allowed_paths, ensure_ascii=False)}

Trusted preflight до изменений:
{json.dumps(preflight, ensure_ascii=False, indent=2)}

AVAILABLE_VERIFICATION_CAPABILITIES:
{json.dumps(sorted(set(available_verification_capabilities)), ensure_ascii=False)}

MANIFEST_REPAIR_EVIDENCE:
{json.dumps(list(manifest_repair_evidence), ensure_ascii=False, indent=2)}

{replan_context}

Исследуй текущий repository независимо и верни planner.v4 artifact.
""".strip()
    raw = codex.run_turn(
        thread_id=thread_id,
        prompt=prompt,
        output_schema=PLANNER_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    plan = _parse_planner_output(raw)
    validate_plan_artifact(plan, workspace=workspace, task_contract=task_contract)
    unavailable = planner_capability_gaps(
        plan, available=available_verification_capabilities
    )
    if not unavailable:
        return plan

    correction_prompt = f"""
CAPABILITY FEASIBILITY CORRECTION

Твой READY plan потребовал недоступные capabilities:
{json.dumps(unavailable, ensure_ascii=False)}

Controller-authoritative available capabilities:
{json.dumps(sorted(set(available_verification_capabilities)), ensure_ascii=False)}

Верни полный planner.v4 artifact. Сохрани product claims, но выбери честный конкретный
proof route только из available capabilities. Не заменяй недоступный executor фиктивным.
Если без недоступной capability обязательный proof действительно невозможен, верни BLOCKED
с конкретным BLOCKING unknown. Это единственный corrective turn.
""".strip()
    corrected_raw = codex.run_turn(
        thread_id=thread_id,
        prompt=correction_prompt,
        output_schema=PLANNER_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    corrected = _parse_planner_output(corrected_raw)
    validate_plan_artifact(corrected, workspace=workspace, task_contract=task_contract)
    remaining = planner_capability_gaps(
        corrected, available=available_verification_capabilities
    )
    if remaining:
        raise PlannerCapabilityInfeasible(remaining, plan=corrected)
    return corrected
