from __future__ import annotations

from enum import Enum
import shutil
from typing import Any, Iterable, Mapping

from slivin_harness.protocol import (
    ArtifactContractError,
    ensure_exact_keys,
    require_string_list,
    require_type,
    stable_fingerprint,
)

VERIFICATION_PLAN_VERSION = "verification-plan.v1"


class ProofLevel(str, Enum):
    LOCAL_DETERMINISTIC = "LOCAL_DETERMINISTIC"
    LIVE_LOCAL = "LIVE_LOCAL"
    TEST_EXTERNAL = "TEST_EXTERNAL"
    PROD_OBSERVE = "PROD_OBSERVE"


class Capability(str, Enum):
    GIT = "GIT"
    PROJECT_PYTHON = "PROJECT_PYTHON"
    NODE = "NODE"
    JEST = "JEST"
    DOCS_SYNC = "DOCS_SYNC"
    LIVE_LOCAL_RUNTIME = "LIVE_LOCAL_RUNTIME"
    LOCAL_APP = "LOCAL_APP"
    BROWSER_DOM = "BROWSER_DOM"
    BROWSER_NETWORK = "BROWSER_NETWORK"
    TEST_DATABASE = "TEST_DATABASE"
    TEST_EXTERNAL_RUNTIME = "TEST_EXTERNAL_RUNTIME"
    TEST_EXTERNAL_WRITE = "TEST_EXTERNAL_WRITE"
    TEST_EXTERNAL_FRESH_READ = "TEST_EXTERNAL_FRESH_READ"
    PROD_OBSERVE_RUNTIME = "PROD_OBSERVE_RUNTIME"
    PROD_READ_ONLY = "PROD_READ_ONLY"


_LEVEL_ORDER = {
    ProofLevel.LOCAL_DETERMINISTIC.value: 0,
    ProofLevel.LIVE_LOCAL.value: 1,
    ProofLevel.TEST_EXTERNAL.value: 2,
    ProofLevel.PROD_OBSERVE.value: 3,
}
_LEVEL_RUNTIME_CAPABILITY = {
    ProofLevel.LIVE_LOCAL.value: Capability.LIVE_LOCAL_RUNTIME.value,
    ProofLevel.TEST_EXTERNAL.value: Capability.TEST_EXTERNAL_RUNTIME.value,
    ProofLevel.PROD_OBSERVE.value: Capability.PROD_OBSERVE_RUNTIME.value,
}

PROOF_TARGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim": {"type": "string"},
        "level": {"type": "string", "enum": [item.value for item in ProofLevel]},
        "capabilities": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "enum": [item.value for item in Capability]},
        },
    },
    "required": ["claim", "level", "capabilities"],
}


def validate_proof_target(value: object, *, field: str) -> dict[str, Any]:
    require_type(value, dict, field=field)
    target = value
    ensure_exact_keys(
        target,
        allowed={"claim", "level", "capabilities"},
        required={"claim", "level", "capabilities"},
        field=field,
    )
    require_type(target["claim"], str, field=f"{field}.claim")
    if not target["claim"].strip():
        raise ArtifactContractError(
            code="EMPTY_PROOF_CLAIM",
            field=f"{field}.claim",
            message="Proof claim must be non-empty",
            expected="Observable claim",
            actual=target["claim"],
        )
    _validate_level_and_capabilities(
        level=target["level"], capabilities=target["capabilities"], field=field
    )
    return target


def _validate_level_and_capabilities(
    *, level: object, capabilities: object, field: str
) -> list[str]:
    if level not in _LEVEL_ORDER:
        raise ArtifactContractError(
            code="UNKNOWN_PROOF_LEVEL",
            field=f"{field}.level",
            message="Unknown proof level",
            expected="/".join(_LEVEL_ORDER),
            actual=level,
        )
    values = require_string_list(capabilities, field=f"{field}.capabilities")
    allowed = {item.value for item in Capability}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ArtifactContractError(
            code="UNKNOWN_CAPABILITY",
            field=f"{field}.capabilities",
            message="Unknown verification capability",
            expected=", ".join(sorted(allowed)),
            actual=unknown,
        )
    if len(values) != len(set(values)):
        raise ArtifactContractError(
            code="DUPLICATE_CAPABILITY",
            field=f"{field}.capabilities",
            message="Proof capabilities must be unique",
            expected="Unique capability names",
            actual=values,
        )
    return values


def _merge_proofs(
    proofs: Iterable[Mapping[str, Any]], *, fallback_claim: str
) -> dict[str, Any]:
    """Merge claims without collapsing distinct runtime proof profiles.

    LIVE_LOCAL, TEST_EXTERNAL and PROD_OBSERVE are different execution routes,
    not interchangeable points on one scalar risk axis.  A requirement may
    legitimately need more than one profile, so the compiled Contract retains
    every distinct level and unions capabilities only within that level.
    """
    rows = [dict(item) for item in proofs]
    if not rows:
        return {
            "claims": [fallback_claim],
            "profiles": [
                {
                    "level": ProofLevel.LOCAL_DETERMINISTIC.value,
                    "capabilities": [],
                }
            ],
        }
    claims: list[str] = []
    by_level: dict[str, set[str]] = {}
    for index, item in enumerate(rows):
        validate_proof_target(item, field=f"proofs[{index}]")
        claim = str(item["claim"]).strip()
        if claim not in claims:
            claims.append(claim)
        by_level.setdefault(str(item["level"]), set()).update(
            str(value) for value in item.get("capabilities", [])
        )
    profiles = [
        {"level": level, "capabilities": sorted(by_level[level])}
        for level in sorted(by_level, key=_LEVEL_ORDER.__getitem__)
    ]
    return {"claims": claims or [fallback_claim], "profiles": profiles}


def merged_required_proof(
    proofs: Iterable[Mapping[str, Any]], *, fallback_claim: str
) -> dict[str, Any]:
    """Public deterministic proof merge used by the Contract compiler."""
    return _merge_proofs(proofs, fallback_claim=fallback_claim)


def validate_merged_required_proof(value: object, *, field: str) -> dict[str, Any]:
    require_type(value, dict, field=field)
    proof = value
    ensure_exact_keys(
        proof,
        allowed={"claims", "profiles"},
        required={"claims", "profiles"},
        field=field,
    )
    claims = require_string_list(proof["claims"], field=f"{field}.claims")
    if not claims or any(not claim.strip() for claim in claims):
        raise ArtifactContractError(
            code="EMPTY_REQUIRED_PROOF",
            field=f"{field}.claims",
            message="Compiled proof requires at least one non-empty claim",
            expected="Non-empty observable claims",
            actual=claims,
        )
    require_type(proof["profiles"], list, field=f"{field}.profiles")
    if not proof["profiles"]:
        raise ArtifactContractError(
            code="EMPTY_PROOF_PROFILES",
            field=f"{field}.profiles",
            message="Compiled proof requires at least one execution profile",
            expected="At least one typed proof profile",
            actual=proof["profiles"],
        )
    seen_levels: set[str] = set()
    previous_order = -1
    for index, profile in enumerate(proof["profiles"]):
        require_type(profile, dict, field=f"{field}.profiles[{index}]")
        ensure_exact_keys(
            profile,
            allowed={"level", "capabilities"},
            required={"level", "capabilities"},
            field=f"{field}.profiles[{index}]",
        )
        level = str(profile["level"])
        _validate_level_and_capabilities(
            level=level,
            capabilities=profile["capabilities"],
            field=f"{field}.profiles[{index}]",
        )
        if level in seen_levels:
            raise ArtifactContractError(
                code="DUPLICATE_PROOF_PROFILE",
                field=f"{field}.profiles[{index}].level",
                message="A compiled proof may contain each level only once",
                expected="Unique proof levels",
                actual=level,
            )
        order = _LEVEL_ORDER[level]
        if order <= previous_order:
            raise ArtifactContractError(
                code="UNSORTED_PROOF_PROFILES",
                field=f"{field}.profiles",
                message="Compiled proof profiles must use canonical order",
                expected="LOCAL_DETERMINISTIC, LIVE_LOCAL, TEST_EXTERNAL, PROD_OBSERVE",
                actual=[item.get("level") for item in proof["profiles"]],
            )
        previous_order = order
        seen_levels.add(level)
    return proof


def _required_capabilities(level: str, capabilities: Iterable[str]) -> set[str]:
    result = set(capabilities)
    runtime_capability = _LEVEL_RUNTIME_CAPABILITY.get(level)
    if runtime_capability:
        result.add(runtime_capability)
    return result


def compile_verification_plan(
    implementation_contract: Mapping[str, Any],
    *,
    project_checks: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    requirements: list[dict[str, Any]] = []
    required_capabilities: set[str] = set()
    runtime_profiles: set[str] = set()
    for item in implementation_contract.get("items", []):
        proof = validate_merged_required_proof(
            item["required_proof"], field=f"implementation_contract.items[{item['id']}].required_proof"
        )
        profiles: list[dict[str, Any]] = []
        for profile in proof["profiles"]:
            level = str(profile["level"])
            capabilities = sorted(
                _required_capabilities(level, profile.get("capabilities", []))
            )
            required_capabilities.update(capabilities)
            if level != ProofLevel.LOCAL_DETERMINISTIC.value:
                runtime_profiles.add(level)
            profiles.append({"level": level, "capabilities": capabilities})
        requirements.append(
            {
                "item_id": item["id"],
                "profiles": profiles,
                "claims": list(proof["claims"]),
            }
        )
    project_gate_names: list[str] = []
    for spec in project_checks:
        name = str(spec["name"])
        if name not in project_gate_names:
            project_gate_names.append(name)
    payload: dict[str, Any] = {
        "protocol_version": VERIFICATION_PLAN_VERSION,
        "implementation_contract_fingerprint": implementation_contract["fingerprint"],
        "requirements": requirements,
        "project_gates": project_gate_names,
        "task_checks": [],
        "required_capabilities": sorted(required_capabilities),
        "runtime_profiles": sorted(runtime_profiles, key=_LEVEL_ORDER.__getitem__),
        "runtime_required": bool(runtime_profiles),
    }
    payload["fingerprint"] = stable_fingerprint(payload)
    validate_verification_plan(payload)
    return payload


def validate_verification_plan(plan: Mapping[str, Any]) -> None:
    ensure_exact_keys(
        dict(plan),
        allowed={
            "protocol_version",
            "implementation_contract_fingerprint",
            "requirements",
            "project_gates",
            "task_checks",
            "required_capabilities",
            "runtime_profiles",
            "runtime_required",
            "fingerprint",
        },
        required={
            "protocol_version",
            "implementation_contract_fingerprint",
            "requirements",
            "project_gates",
            "task_checks",
            "required_capabilities",
            "runtime_profiles",
            "runtime_required",
            "fingerprint",
        },
        field="verification_plan",
    )
    if plan["protocol_version"] != VERIFICATION_PLAN_VERSION:
        raise ArtifactContractError(
            code="VERIFICATION_PLAN_VERSION",
            field="verification_plan.protocol_version",
            message="Verification Plan version mismatch",
            expected=VERIFICATION_PLAN_VERSION,
            actual=plan["protocol_version"],
        )
    require_type(
        plan["implementation_contract_fingerprint"],
        str,
        field="verification_plan.implementation_contract_fingerprint",
    )
    if not plan["implementation_contract_fingerprint"]:
        raise ArtifactContractError(
            code="EMPTY_CONTRACT_FINGERPRINT",
            field="verification_plan.implementation_contract_fingerprint",
            message="Verification Plan must bind an Implementation Contract",
            expected="Non-empty fingerprint",
            actual=plan["implementation_contract_fingerprint"],
        )
    require_type(plan["requirements"], list, field="verification_plan.requirements")
    seen_items: set[str] = set()
    expected_capabilities: set[str] = set()
    expected_runtime_profiles: set[str] = set()
    for index, item in enumerate(plan["requirements"]):
        require_type(item, dict, field=f"verification_plan.requirements[{index}]")
        ensure_exact_keys(
            item,
            allowed={"item_id", "profiles", "claims"},
            required={"item_id", "profiles", "claims"},
            field=f"verification_plan.requirements[{index}]",
        )
        require_type(
            item["item_id"], str, field=f"verification_plan.requirements[{index}].item_id"
        )
        item_id = item["item_id"]
        if not item_id or item_id in seen_items:
            raise ArtifactContractError(
                code="DUPLICATE_VERIFICATION_ITEM",
                field=f"verification_plan.requirements[{index}].item_id",
                message="Verification Plan item ids must be non-empty and unique",
                expected="Unique Contract item id",
                actual=item_id,
            )
        seen_items.add(item_id)
        claims = require_string_list(
            item["claims"], field=f"verification_plan.requirements[{index}].claims"
        )
        if not claims or any(not claim.strip() for claim in claims):
            raise ArtifactContractError(
                code="EMPTY_VERIFICATION_CLAIM",
                field=f"verification_plan.requirements[{index}].claims",
                message="Verification requirement needs observable claims",
                expected="Non-empty claims",
                actual=claims,
            )
        require_type(
            item["profiles"], list, field=f"verification_plan.requirements[{index}].profiles"
        )
        if not item["profiles"]:
            raise ArtifactContractError(
                code="EMPTY_VERIFICATION_PROFILES",
                field=f"verification_plan.requirements[{index}].profiles",
                message="Verification requirement needs at least one profile",
                expected="Typed proof profiles",
                actual=item["profiles"],
            )
        previous_order = -1
        seen_levels: set[str] = set()
        for profile_index, profile in enumerate(item["profiles"]):
            require_type(
                profile,
                dict,
                field=f"verification_plan.requirements[{index}].profiles[{profile_index}]",
            )
            ensure_exact_keys(
                profile,
                allowed={"level", "capabilities"},
                required={"level", "capabilities"},
                field=f"verification_plan.requirements[{index}].profiles[{profile_index}]",
            )
            level = str(profile["level"])
            capabilities = _validate_level_and_capabilities(
                level=level,
                capabilities=profile["capabilities"],
                field=f"verification_plan.requirements[{index}].profiles[{profile_index}]",
            )
            expected_with_implicit = _required_capabilities(level, capabilities)
            if set(capabilities) != expected_with_implicit:
                raise ArtifactContractError(
                    code="MISSING_IMPLICIT_RUNTIME_CAPABILITY",
                    field=f"verification_plan.requirements[{index}].profiles[{profile_index}].capabilities",
                    message="Compiled runtime profile must include its executor capability",
                    expected=sorted(expected_with_implicit),
                    actual=capabilities,
                )
            order = _LEVEL_ORDER[level]
            if level in seen_levels or order <= previous_order:
                raise ArtifactContractError(
                    code="INVALID_VERIFICATION_PROFILE_ORDER",
                    field=f"verification_plan.requirements[{index}].profiles",
                    message="Verification profiles must be unique and canonically ordered",
                    expected="LOCAL_DETERMINISTIC, LIVE_LOCAL, TEST_EXTERNAL, PROD_OBSERVE",
                    actual=[entry.get("level") for entry in item["profiles"]],
                )
            seen_levels.add(level)
            previous_order = order
            expected_capabilities.update(capabilities)
            if level != ProofLevel.LOCAL_DETERMINISTIC.value:
                expected_runtime_profiles.add(level)
    for field in ("project_gates", "task_checks", "required_capabilities", "runtime_profiles"):
        values = require_string_list(plan[field], field=f"verification_plan.{field}")
        if len(values) != len(set(values)):
            raise ArtifactContractError(
                code="DUPLICATE_VERIFICATION_LIST_ITEM",
                field=f"verification_plan.{field}",
                message="Verification Plan lists must be unique",
                expected="Unique values",
                actual=values,
            )
    required_capabilities = list(plan["required_capabilities"])
    allowed_capabilities = {item.value for item in Capability}
    unknown_capabilities = sorted(set(required_capabilities) - allowed_capabilities)
    if unknown_capabilities:
        raise ArtifactContractError(
            code="UNKNOWN_REQUIRED_CAPABILITY",
            field="verification_plan.required_capabilities",
            message="Verification Plan requires unknown capabilities",
            expected=sorted(allowed_capabilities),
            actual=unknown_capabilities,
        )
    if required_capabilities != sorted(expected_capabilities):
        raise ArtifactContractError(
            code="REQUIRED_CAPABILITY_SET_MISMATCH",
            field="verification_plan.required_capabilities",
            message="Verification Plan capability summary does not match its requirements",
            expected=sorted(expected_capabilities),
            actual=required_capabilities,
        )
    runtime_profiles = list(plan["runtime_profiles"])
    expected_profiles = sorted(expected_runtime_profiles, key=_LEVEL_ORDER.__getitem__)
    if runtime_profiles != expected_profiles:
        raise ArtifactContractError(
            code="RUNTIME_PROFILE_SET_MISMATCH",
            field="verification_plan.runtime_profiles",
            message="Runtime profile summary does not match its requirements",
            expected=expected_profiles,
            actual=runtime_profiles,
        )
    require_type(plan["runtime_required"], bool, field="verification_plan.runtime_required")
    expected_runtime = bool(expected_profiles)
    if plan["runtime_required"] != expected_runtime:
        raise ArtifactContractError(
            code="RUNTIME_FLAG_MISMATCH",
            field="verification_plan.runtime_required",
            message="runtime_required must match runtime_profiles",
            expected=str(expected_runtime),
            actual=plan["runtime_required"],
        )
    fingerprint_payload = {key: value for key, value in plan.items() if key != "fingerprint"}
    expected_fingerprint = stable_fingerprint(fingerprint_payload)
    if plan["fingerprint"] != expected_fingerprint:
        raise ArtifactContractError(
            code="VERIFICATION_PLAN_FINGERPRINT",
            field="verification_plan.fingerprint",
            message="Verification Plan fingerprint mismatch",
            expected=expected_fingerprint,
            actual=plan["fingerprint"],
        )


def available_capabilities(
    *,
    toolchain: Mapping[str, str],
    configured: Iterable[str] = (),
) -> set[str]:
    result = {Capability.DOCS_SYNC.value}
    if shutil.which("git"):
        result.add(Capability.GIT.value)
    if toolchain.get("project_python") or toolchain.get("python"):
        result.add(Capability.PROJECT_PYTHON.value)
    if toolchain.get("node"):
        result.add(Capability.NODE.value)
    if toolchain.get("jest"):
        result.add(Capability.JEST.value)
    allowed = {item.value for item in Capability}
    phase3_implemented = {
        Capability.GIT.value,
        Capability.PROJECT_PYTHON.value,
        Capability.NODE.value,
        Capability.JEST.value,
        Capability.DOCS_SYNC.value,
    }
    for raw in configured:
        value = str(raw)
        if value not in allowed:
            raise RuntimeError(f"Unknown configured capability: {value}")
        # Phase 3 records future runtime capability declarations but does not
        # claim an executor that does not exist yet. Required runtime proof
        # therefore remains blocked before Implementer.
        if value in phase3_implemented:
            result.add(value)
    return result


def required_capability_gaps(
    verification_plan: Mapping[str, Any], *, available: Iterable[str]
) -> list[str]:
    return sorted(set(verification_plan["required_capabilities"]) - set(available))


def configured_capabilities(
    local_config: Mapping[str, Any], *, project_name: str | None
) -> list[str]:
    values: set[str] = set()

    def add_from(table: object) -> None:
        if isinstance(table, list):
            values.update(str(item) for item in table)
        elif isinstance(table, dict):
            values.update(str(key) for key, enabled in table.items() if enabled is True)

    add_from(local_config.get("capabilities"))
    if project_name:
        project = local_config.get("projects", {}).get(project_name, {})
        if isinstance(project, dict):
            add_from(project.get("capabilities"))
    return sorted(values)
