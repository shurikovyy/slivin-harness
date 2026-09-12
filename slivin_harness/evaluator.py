from __future__ import annotations

from slivin_harness.boundaries import boundary

import copy
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from slivin_harness.app_server import CodexAppServer
from slivin_harness.execution import ExecutionRole
from slivin_harness.impact import impact_paths, impact_text, safe_impact_path, validate_owner_prose_boundary, validate_impact_structure
from slivin_harness.implementer import validate_implementation_impact_closure
from slivin_harness.source_records import source_ref
from slivin_harness.report_recovery import ReportCorrectionState, ReportRecoveryStop, MAX_REPORT_CORRECTIONS, correction_prompt
from slivin_harness.protocol import (
    ArtifactContractError,
    ArtifactFailureKind,
    stable_fingerprint,
)
from slivin_harness.phase6 import BLIND_AUDIT_VERSION
from slivin_harness.protocol import EVALUATOR_PROTOCOL_VERSION, ensure_exact_keys, require_string_list, require_type
from slivin_harness.verification import PROOF_TARGET_SCHEMA, validate_proof_target
from slivin_harness.workflow import EvaluatorStatus, enum_values

_FINDING_CATEGORIES = ["DEFECT", "CONSUMER", "RISK", "EVIDENCE", "DOCS", "MODEL"]
_FINDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "finding_id": {"type": "string"},
        "severity": {"type": "string", "enum": ["HIGH", "MEDIUM"]},
        "category": {"type": "string", "enum": _FINDING_CATEGORIES},
        "title": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "failure_mode": {"type": "string"},
        "required_action": {"type": "string"},
        "required_proof": PROOF_TARGET_SCHEMA,
    },
    "required": [
        "finding_id",
        "severity",
        "category",
        "title",
        "evidence",
        "failure_mode",
        "required_action",
        "required_proof",
    ],
}

def _rows(*, text: Sequence[str], lists: Sequence[str] = (), enums: Mapping[str, Sequence[str]] | None = None) -> dict:
    properties = {key: {"type": "string"} for key in text}
    properties.update({key: {"type": "array", "items": {"type": "string"}} for key in lists})
    properties.update({key: {"type": "string", "enum": list(values)} for key, values in (enums or {}).items()})
    return {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}}


BLIND_IMPACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "applicable": {"type": "boolean"},
        "changed_contracts": _rows(text=("impact_id", "name", "before", "after"), lists=("paths", "symbols", "evidence")),
        "affected_consumers": _rows(text=("impact_id", "name", "relation", "required_behavior"), lists=("paths", "symbols", "evidence")),
        "not_affected_consumers": _rows(text=("impact_id", "name", "why_considered", "reason"), lists=("paths", "symbols", "evidence")),
        "related_out_of_scope": _rows(text=("impact_id", "name", "relation", "reason", "suggested_follow_up"), lists=("paths", "symbols", "evidence")),
        "changed_path_review": _rows(text=("path", "observed_role", "impact"), lists=("evidence",)),
        "search_evidence": _rows(text=("target", "method", "conclusion"), lists=("evidence_paths",)),
        "closure_summary": {"type": "string"},
    },
    "required": ["applicable", "changed_contracts", "affected_consumers", "not_affected_consumers", "related_out_of_scope", "changed_path_review", "search_evidence", "closure_summary"],
}

_CHALLENGE_DISPOSITIONS = {
    "blind_contract_dispositions": ("COVERED", "MATERIAL_GAP", "MODEL_CONFLICT"),
    "blind_consumer_dispositions": ("COVERED_IN_SCOPE", "MISCLASSIFIED_NOT_AFFECTED", "MISCLASSIFIED_OUT_OF_SCOPE", "MISSING"),
    "planner_consumer_dispositions": ("CONFIRMED", "UNSUPPORTED", "IMPLEMENTATION_GAP"),
    "implementer_consumer_dispositions": ("CONFIRMED", "UNSUPPORTED", "INCOMPLETE"),
    "not_affected_dispositions": ("CONFIRMED_NOT_AFFECTED", "ACTUALLY_AFFECTED", "INSUFFICIENT_EVIDENCE", "PROMOTED_IN_SCOPE"),
    "related_follow_up_dispositions": ("CONFIRMED_OUT_OF_SCOPE", "ACTUALLY_IN_SCOPE", "UNSUPPORTED", "PROMOTED_IN_SCOPE"),
    "changed_path_dispositions": ("UNDERSTOOD", "SUSPICIOUS", "UNJUSTIFIED"),
}


def _canonical_challenge_schema() -> dict:
    properties: dict[str, Any] = {}
    for group, dispositions in _CHALLENGE_DISPOSITIONS.items():
        enums: dict[str, Sequence[str]] = {"disposition": dispositions}
        if group.startswith("blind_"):
            reference = "impact_id"
        elif group in {"not_affected_dispositions", "related_follow_up_dispositions"}:
            reference = "reference"
            enums["source"] = ("BLIND", "PLANNER", "IMPLEMENTER")
        else:
            reference = "path" if group == "changed_path_dispositions" else "reference"
        schema = _rows(text=((reference, "reason") if group.startswith("blind_") or group == "changed_path_dispositions" else (reference, "source_revision", "reason")), lists=("evidence_paths", "evidence", "finding_ids"), enums=enums)
        if group.startswith("blind_"):
            fields = schema["items"]["properties"]
            fields["matches"] = _rows(
                text=("reference", "source_revision"),
                enums={"source": ("PLANNER", "IMPLEMENTER"), "classification": ("CHANGED_CONTRACT", "IN_SCOPE", "NOT_AFFECTED", "RELATED_OUT_OF_SCOPE")},
            )
            schema["items"]["required"].append("matches")
        properties[group] = schema
    properties["coverage_summary"] = {"type": "string"}
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


CANONICAL_IMPACT_CHALLENGE_SCHEMA = _canonical_challenge_schema()


PHASE_B_ORIGIN_CATALOG_VERSION = "phase-b-origin-catalog.v1"
_ORIGIN_CLASSIFICATION_GROUP = {
    "CHANGED_CONTRACT": "changed_contracts",
    "IN_SCOPE": "in_scope_consumers",
    "NOT_AFFECTED": "not_affected_consumers",
    "RELATED_OUT_OF_SCOPE": "related_out_of_scope",
}


def _origin_handles(catalog: Mapping[str, Any] | None, predicate) -> list[str]:
    if catalog is None:
        return []
    return [row["origin_ref"] for row in catalog["origins"] if predicate(row)]


def _origin_ref_rows(*, handles: Sequence[str], lists: Sequence[str] = (),
                     matches: Sequence[str] | None = None, exact_cardinality: bool = False) -> dict:
    enums = {"origin_ref": handles} if handles else None
    schema = _rows(text=("reason",) if handles else ("origin_ref", "reason"), lists=lists, enums=enums)
    if exact_cardinality:
        schema.update(minItems=len(handles), maxItems=len(handles))
    if matches is not None:
        match_enums = {"origin_ref": matches} if matches else None
        schema["items"]["properties"]["matches"] = _rows(
            text=() if matches else ("origin_ref",), enums=match_enums,
        )
        if exact_cardinality and not matches:
            schema["items"]["properties"]["matches"]["maxItems"] = 0
        schema["items"]["required"].append("matches")
    return schema


def _wire_challenge_schema(catalog: Mapping[str, Any] | None = None) -> dict:
    exact = catalog is not None
    origins = lambda authority, classification: _origin_handles(
        catalog, lambda row: (authority is None or row["authority"] == authority)
        and (classification is None or row["classification"] in classification),
    )
    non_blind = lambda classifications: _origin_handles(
        catalog, lambda row: row["authority"] != "BLIND" and row["classification"] in classifications,
    )
    properties = {
        "blind_contract_dispositions": _origin_ref_rows(
            handles=origins("BLIND", {"CHANGED_CONTRACT"}),
            matches=non_blind({"CHANGED_CONTRACT"}),
            lists=("evidence_paths", "evidence", "finding_ids"),
            exact_cardinality=exact,
        ),
        "blind_consumer_dispositions": _origin_ref_rows(
            handles=origins("BLIND", {"IN_SCOPE"}),
            matches=non_blind({"IN_SCOPE", "NOT_AFFECTED", "RELATED_OUT_OF_SCOPE"}),
            lists=("evidence_paths", "evidence", "finding_ids"),
            exact_cardinality=exact,
        ),
        "planner_consumer_dispositions": _origin_ref_rows(
            handles=origins("PLANNER", {"IN_SCOPE"}), lists=("evidence_paths", "evidence", "finding_ids"),
            exact_cardinality=exact,
        ),
        "implementer_consumer_dispositions": _origin_ref_rows(
            handles=_origin_handles(catalog, lambda row: row["authority"] == "IMPLEMENTER"
                                   and row["classification"] == "IN_SCOPE"
                                   and row["current_row"].get("source") == "DISCOVERED"),
            lists=("evidence_paths", "evidence", "finding_ids"),
            exact_cardinality=exact,
        ),
        "not_affected_dispositions": _origin_ref_rows(
            handles=origins(None, {"NOT_AFFECTED"}), lists=("evidence_paths", "evidence", "finding_ids"),
            exact_cardinality=exact,
        ),
        "related_follow_up_dispositions": _origin_ref_rows(
            handles=origins(None, {"RELATED_OUT_OF_SCOPE"}), lists=("evidence_paths", "evidence", "finding_ids"),
            exact_cardinality=exact,
        ),
        "changed_path_dispositions": _rows(
            text=("path", "reason"), lists=("evidence_paths", "evidence", "finding_ids"),
            enums={"disposition": _CHALLENGE_DISPOSITIONS["changed_path_dispositions"]},
        ),
        "coverage_summary": {"type": "string"},
    }
    for group, dispositions in _CHALLENGE_DISPOSITIONS.items():
        if group != "changed_path_dispositions":
            properties[group]["items"]["properties"]["disposition"] = {
                "type": "string", "enum": list(dispositions),
            }
            properties[group]["items"]["required"].append("disposition")
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


IMPACT_CHALLENGE_SCHEMA = _wire_challenge_schema()

BLIND_AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {"type": "string", "enum": [BLIND_AUDIT_VERSION]},
        "candidate_id": {"type": "string"},
        "summary": {"type": "string"},
        "impact_analysis": BLIND_IMPACT_SCHEMA,
        "findings": {"type": "array", "items": _FINDING_SCHEMA},
        "advisories": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["protocol_version", "candidate_id", "summary", "impact_analysis", "findings", "advisories"],
}

EVALUATOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {
            "type": "string",
            "enum": [EVALUATOR_PROTOCOL_VERSION],
        },
        "status": {"type": "string", "enum": enum_values(EvaluatorStatus)},
        "candidate_id": {"type": "string"},
        "summary": {"type": "string"},
        "impact_challenge": IMPACT_CHALLENGE_SCHEMA,
        "blind_finding_dispositions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "finding_id": {"type": "string"},
                    "disposition": {
                        "type": "string",
                        "enum": ["RETAINED", "DISMISSED_WITH_EVIDENCE"],
                    },
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["finding_id", "disposition", "evidence"],
            },
        },
        "findings": {"type": "array", "items": _FINDING_SCHEMA},
        "reason": {"type": "string"},
    },
    "required": [
        "protocol_version",
        "status",
        "candidate_id",
        "summary",
        "impact_challenge",
        "blind_finding_dispositions",
        "findings",
        "reason",
    ],
}


def build_evaluator_schema(catalog: Mapping[str, Any]) -> dict:
    schema = copy.deepcopy(EVALUATOR_SCHEMA)
    schema["properties"]["impact_challenge"] = _wire_challenge_schema(catalog)
    return schema

EVALUATOR_INSTRUCTIONS = """
Ты независимый read-only Evaluator внутри Slivin Harness.

Работа состоит из двух фаз в одном fresh thread.

PHASE A — blind discovery:
- не получаешь Planner, Implementation Contract, Implementer Report, Controller checks,
  runtime evidence, previous findings, hidden grader или reference solution;
- самостоятельно восстанови impact_analysis по repository и actual candidate/diff:
  changed semantic/state contracts, shared symbols/API, callers/readers/writers,
  decision points, sibling consumers, tests и canonical docs;
- changed paths — только starting navigation hint: проследи impact наружу, не ограничивай
  исследование diff files. Рассмотри каждый changed path ровно один раз, включая deletions;
- changed contracts, affected consumers, NOT_AFFECTED и RELATED_OUT_OF_SCOPE получают
  собственные stable impact_id CONTRACT-N / CONSUMER-N / NOT-AFFECTED-N / RELATED-N.
  Имена выбирай самостоятельно. Все rows требуют concrete paths/symbols/evidence;
  приложи search_evidence нового outward sweep и concrete closure_summary;
- applicable=false разрешён только при owner allowed_paths, которые независимо ограничивают
  задачу существующими regular prose files .md/.rst/.txt/.adoc внутри workspace. Search и
  changed paths должны входить в эту boundary; code/directory/glob/mixed/escape запрещены.
  Behavioral/runtime obligations недопустимы; prose summary не создаёт authority;
- ищи material defects, пропущенных reachable consumers, нарушение preservation,
  несогласованные representations/authority/lifecycle, false-green tests и docs drift;
- state/boundary проверки применяй условно и только к реально достижимым состояниям;
- finding допустим только с конкретным failure mode, reachability evidence и required action;
- blocking severity только HIGH или MEDIUM; вкусовые замечания — advisory, не finding.
- Зелёный regression test не является доказательством, пока assertion не проверяет semantics
  affected consumer. Локальный helper test может пропустить неправильный reachable sibling;
  такой пробел требует CONSUMER/EVIDENCE finding, а не доверия тесту.

PHASE B — independent impact challenge:
- после фиксации blind audit получаешь active Implementation Contract, Verification Plan,
  Controller-normalized Contract Closure Record, deterministic/runtime evidence, normalized
  Planner impact_closure и implementation-impact-closure.v1;
- не получаешь Planner reasoning или Implementer prose;
- проверь качество доказательств и false-green risk;
- каждый blind finding обязан быть RETAINED либо DISMISSED_WITH_EVIDENCE;
- blind finding нельзя забыть только потому, что его нет в Contract.
- Сопоставь независимые blind contracts/consumers с Controller origin catalog; одинаковые имена
  не требуются. Каждая disposition и match ссылается только на exact origin_ref. Authority,
  classification, source_id и source_revision принадлежат Controller и не повторяются моделью.
  COVERED_IN_SCOPE требует match на допустимый IN_SCOPE origin_ref.
- Каждый blind contract, blind affected consumer, Planner IN_SCOPE и Implementer DISCOVERED
  consumer получает evidence-backed disposition. Independently challenge каждый NOT_AFFECTED
  и RELATED_OUT_OF_SCOPE из всех трёх ledgers через их origin_ref. Never regenerate source,
  classification, source_id, source_revision, inherited names or text. Не считай согласие двух
  прежних агентов доказательством и не сопоставляй origins по похожим names/text.
- Каждый actual changed path получает UNDERSTOOD/SUSPICIOUS/UNJUSTIFIED с repository evidence.
  Dispositions содержат concrete reason, existing evidence_paths и evidence. coverage_summary
  объясняет полноту challenge, но не заменяет ни одной строки.
- Любая negative disposition требует finding_ids соответствующих final material findings
  с failure_mode, required_action и typed proof. Negative disposition запрещает PASS.
  Missing consumer, неверный NOT_AFFECTED/out-of-scope, false-green coverage не могут исчезнуть.
- FINDINGS означает исправимый candidate gap: consumer/risk findings поступают в Controller
  expansion и Implementer repair. MATERIAL_GAP или MODEL_CONFLICT в blind changed contract
  разрешены только со status=REPLAN_REQUIRED и concrete reason: technical model недостаточна,
  требуется semantic reset и fresh Planner/Implementer. Другие statuses запрещены.
  Не создавай новый product intent. BLOCKED/NEEDS_USER_DECISION требуют concrete reason
  и не могут использоваться при этих changed-contract dispositions.
- Сохрани все blind related follow-ups, даже если их нет в прежних ledgers. Их mandatory
  user-facing delivery выполняется отдельно; отсутствие delivery сейчас не отменяет finding.

Код, tests и docs не меняй. Оба reports должны содержать переданный current candidate_id.
PASS требует полного positive impact challenge и достаточных deterministic/runtime proofs,
а не просто согласованных Planner/Implementer ledgers. После repair нужен fresh blind audit.
""".strip()


def _parse_json(raw: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArtifactContractError(
            code="REPORT_INVALID_JSON", field="report",
            message=f"{label} returned invalid JSON structured output",
            expected="A JSON object",
        ) from exc
    if not isinstance(value, dict):
        raise ArtifactContractError(
            code="TYPE_MISMATCH", field="report",
            message=f"{label} structured output must be an object",
            expected="object", actual=type(value).__name__,
        )
    return value


def _validate_finding(value: Mapping[str, Any], *, field: str) -> str:
    required = {
        "finding_id",
        "severity",
        "category",
        "title",
        "evidence",
        "failure_mode",
        "required_action",
        "required_proof",
    }
    if set(value) != required:
        raise ArtifactContractError(code="FINDING_FIELDS", field=field,
            message=f"{field} fields invalid", expected="The exact finding fields", actual=sorted(value))
    finding_id = str(value["finding_id"]).strip()
    if not finding_id:
        raise ArtifactContractError(code="FINDING_ID_EMPTY", field=f"{field}.finding_id",
            message=f"{field}.finding_id must be non-empty", expected="A stable non-empty finding ID")
    if value["severity"] not in {"HIGH", "MEDIUM"}:
        raise ArtifactContractError(code="FINDING_SEVERITY", field=f"{field}.severity",
            message=f"{field}.severity must be HIGH or MEDIUM", expected="HIGH or MEDIUM", actual=value["severity"])
    if value["category"] not in set(_FINDING_CATEGORIES):
        raise ArtifactContractError(code="FINDING_CATEGORY", field=f"{field}.category",
            message=f"{field}.category is unsupported", expected="A supported finding category", actual=value["category"])
    for name in ("title", "failure_mode", "required_action"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise ArtifactContractError(code="FINDING_TEXT_EMPTY", field=f"{field}.{name}",
                message=f"{field}.{name} must be non-empty", expected="Concrete finding text", actual=value[name])
    validate_proof_target(value["required_proof"], field=f"{field}.required_proof")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        raise ArtifactContractError(code="IMPACT_EVIDENCE_EMPTY", field=f"{field}.evidence",
            message=f"{field}.evidence must contain concrete strings", expected="Concrete evidence strings", actual=evidence)
    return finding_id


def _name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _model_conflict(code: str, *, field: str, message: str, actual: object = None) -> None:
    raise ArtifactContractError(
        code=code, field=field, message=message,
        expected="A semantically consistent agent artifact", actual=actual,
        failure_kind=ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT,
    )


def _integrity_failure(code: str, *, field: str, message: str, actual: object = None) -> None:
    raise ArtifactContractError(
        code=code, field=field, message=message,
        expected="Current immutable Controller-owned state", actual=actual,
        failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
    )


def _validate_rows(rows: object, schema: Mapping[str, Any], *, workspace: Path, field: str) -> None:
    require_type(rows, list, field=field)
    fields = schema["items"]["properties"]
    for index, row in enumerate(rows):
        row_field = f"{field}[{index}]"
        require_type(row, dict, field=row_field)
        ensure_exact_keys(row, allowed=fields, required=fields, field=row_field)
        for key, kind in fields.items():
            value = row[key]
            key_field = f"{row_field}.{key}"
            if kind["type"] == "string":
                if key == "source_revision" and row.get("source") == "BLIND":
                    require_type(value, str, field=key_field)
                else:
                    impact_text(value, field=key_field)
                if "enum" in kind and value not in kind["enum"]:
                    raise ArtifactContractError(code="ENUM_INVALID", field=key_field,
                        message=f"{key_field} enum invalid", expected="One declared enum value", actual=value)
            elif kind["items"]["type"] == "object":
                _validate_rows(value, kind, workspace=workspace, field=key_field)
            else:
                values = require_string_list(value, field=key_field)
                if not values and key != "finding_ids":
                    raise ArtifactContractError(code="IMPACT_EVIDENCE_EMPTY", field=key_field,
                        message=f"{key_field} requires concrete evidence", expected="Non-empty concrete evidence", actual=values)
                for entry in values:
                    impact_text(entry, field=key_field)
                if key in {"paths", "evidence_paths"}:
                    impact_paths(values, field=key_field, workspace=workspace)
                if key == "symbols" and any(
                    any(char.isspace() for char in entry) or not any(char.isalnum() for char in entry)
                    for entry in values
                ):
                    raise ArtifactContractError(code="IMPACT_SYMBOL_GENERIC", field=key_field,
                        message=f"{key_field} requires concrete identifiers", expected="Concrete identifiers", actual=values)


def _exact_paths(paths: Sequence[str], changed_paths: Sequence[str], *, workspace: Path, field: str) -> None:
    normalized = [safe_impact_path(path, field=field) for path in paths]
    actual = {safe_impact_path(path, field="changed_paths") for path in changed_paths}
    if len(normalized) != len(set(normalized)) or set(normalized) != actual:
        raise ArtifactContractError(code="IMPACT_PATH_SET_MISMATCH", field=field,
            message=f"{field} must cover every actual changed path exactly once",
            expected="Every actual changed path exactly once", actual=normalized,
            failure_kind=ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT)
    impact_paths(normalized, field=field, workspace=workspace, allow_missing=True)


@boundary("B10")
def validate_blind_audit(
    audit: Mapping[str, Any], *, workspace: Path, candidate_id: str,
    changed_paths: Sequence[str], owner_allowed_paths: Sequence[str] = (),
) -> None:
    if set(audit) != set(BLIND_AUDIT_SCHEMA["required"]):
        raise ArtifactContractError(code="BLIND_FIELDS", field="blind_audit",
            message="Blind audit fields invalid", expected="The exact blind audit fields", actual=sorted(audit))
    if audit["protocol_version"] != BLIND_AUDIT_VERSION:
        raise ArtifactContractError(code="BLIND_VERSION", field="blind_audit.protocol_version",
            message="Blind audit protocol mismatch", expected=BLIND_AUDIT_VERSION, actual=audit["protocol_version"])
    if not isinstance(audit["summary"], str) or not audit["summary"].strip():
        raise ArtifactContractError(code="BLIND_SUMMARY_EMPTY", field="blind_audit.summary",
            message="Blind audit summary must be non-empty", expected="A non-empty summary", actual=audit["summary"])
    impact_text(audit["candidate_id"], field="blind_audit.candidate_id")
    if audit["candidate_id"] != candidate_id:
        _integrity_failure("BLIND_CANDIDATE_STALE", field="blind_audit.candidate_id",
                           message="Blind audit is stale for the current candidate", actual=audit["candidate_id"])
    analysis = audit["impact_analysis"]
    validate_impact_structure(analysis, schema=BLIND_IMPACT_SCHEMA, workspace=workspace, field="impact_analysis")
    require_type(analysis, dict, field="impact_analysis")
    fields = BLIND_IMPACT_SCHEMA["properties"]
    ensure_exact_keys(analysis, allowed=fields, required=fields, field="impact_analysis")
    require_type(analysis["applicable"], bool, field="impact_analysis.applicable")
    impact_text(analysis["closure_summary"], field="impact_analysis.closure_summary")
    for group, schema in fields.items():
        if schema["type"] == "array":
            _validate_rows(analysis[group], schema, workspace=workspace, field=f"impact_analysis.{group}")
    prefixes = {"changed_contracts": "CONTRACT", "affected_consumers": "CONSUMER", "not_affected_consumers": "NOT-AFFECTED", "related_out_of_scope": "RELATED"}
    classifications: set[str] = set()
    for group, prefix in prefixes.items():
        names: set[str] = set()
        impact_ids: set[str] = set()
        for row in analysis[group]:
            impact_id = row["impact_id"]
            if not re.fullmatch(prefix + r"-[1-9][0-9]*", impact_id) or impact_id in impact_ids:
                _model_conflict("BLIND_IMPACT_ID", field=f"impact_analysis.{group}",
                                message=f"{group} requires unique safe {prefix} impact IDs", actual=impact_id)
            impact_ids.add(impact_id)
            name = _name(row["name"])
            if name in names or (group != "changed_contracts" and name in classifications):
                _model_conflict("BLIND_CLASSIFICATION_DUPLICATE", field=f"impact_analysis.{group}",
                                message="Blind impact names must be unique and classifications disjoint", actual=name)
            names.add(name)
            if group != "changed_contracts":
                classifications.add(name)
            if group == "changed_contracts" and " ".join(row["before"].split()) == " ".join(row["after"].split()):
                _model_conflict("BLIND_CONTRACT_UNCHANGED", field=f"impact_analysis.{group}",
                                message="Blind changed contract requires distinct before/after semantics", actual=impact_id)
    _exact_paths([row["path"] for row in analysis["changed_path_review"]], changed_paths, workspace=workspace, field="Blind changed_path_review")
    if not analysis["search_evidence"]:
        _model_conflict("BLIND_SEARCH_MISSING", field="impact_analysis.search_evidence",
                        message="Blind impact requires independent search evidence")
    if analysis["applicable"] and not analysis["changed_contracts"]:
        _model_conflict("BLIND_CONTRACTS_MISSING", field="impact_analysis.changed_contracts",
                        message="Blind engineering impact requires actual changed contracts")
    findings = audit["findings"]
    if not isinstance(findings, list):
        raise ArtifactContractError(code="TYPE_MISMATCH", field="blind_audit.findings",
            message="Blind audit findings must be a list", expected="list", actual=type(findings).__name__)
    ids: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            raise ArtifactContractError(code="TYPE_MISMATCH", field=f"blind_audit.findings[{index}]",
                message=f"blind_audit.findings[{index}] must be an object", expected="object", actual=type(finding).__name__)
        finding_id = _validate_finding(finding, field=f"blind_audit.findings[{index}]")
        if finding_id in ids:
            _model_conflict("BLIND_FINDING_DUPLICATE", field="blind_audit.findings",
                            message="Blind audit finding ids must be unique", actual=finding_id)
        ids.add(finding_id)
    advisories = audit["advisories"]
    if not isinstance(advisories, list) or not all(
        isinstance(item, str) for item in advisories
    ):
        raise ArtifactContractError(code="TYPE_MISMATCH", field="blind_audit.advisories",
            message="Blind audit advisories must be strings", expected="A list of strings", actual=advisories)
    if not analysis["applicable"]:
        search_paths = [
            safe_impact_path(path, field="impact_analysis.search_evidence")
            for row in analysis["search_evidence"] for path in row["evidence_paths"]
        ]
        validate_owner_prose_boundary(
            owner_allowed_paths, workspace=workspace,
            search_paths=[*search_paths, *(safe_impact_path(path, field="changed_paths") for path in changed_paths)],
            field="impact_analysis",
        )
        behavioral = any(analysis[group] for group in prefixes) or any(
            row["category"] in {"DEFECT", "CONSUMER", "RISK", "MODEL"}
            or row["required_proof"]["level"] != "LOCAL_DETERMINISTIC"
            or set(row["required_proof"]["capabilities"]) - {"GIT", "DOCS_SYNC"}
            for row in findings
        )
        summary = analysis["closure_summary"]
        explanation = summary
        for path in search_paths:
            explanation = explanation.replace(path, "")
        if behavioral or len(explanation.split()) < 6 or not any(path in summary for path in search_paths):
            _model_conflict("BLIND_PROSE_EXCEPTION_INVALID", field="impact_analysis",
                            message="Blind prose exception requires specific explanation and no behavioral/runtime obligations")


def _impact_sources(
    blind_audit: Mapping[str, Any], planner_impact_closure: Mapping[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    planner = copy.deepcopy(planner_impact_closure or {})
    records = implementation_impact_closure["source_inventory"]["records"]
    for group in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope"):
        origins = [record for record in records if record["author"] == "PLANNER" and record["group"] == group]
        if [record["claim"] for record in origins] != planner.get(group, []):
            raise ArtifactContractError(
                code="ORIGIN_LEDGER_MISMATCH", field=f"source_inventory.{group}",
                message="Evaluator Planner origins do not match the current source records",
                expected="Controller source records equal the current normalized Planner ledger",
                failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
            )
        planner[group] = [dict(copy.deepcopy(record["claim"]), source_ref=source_ref(record)) for record in origins]
    implementer = copy.deepcopy(implementation_impact_closure["post_patch_impact"])
    promoted = {event["source_ref"]["source_id"] for event in implementation_impact_closure["source_inventory"]["transitions"]}
    for record in records:
        if record["author"] != "PLANNER" and record["source_id"] in promoted:
            implementer[record["group"]].append(dict(copy.deepcopy(record["claim"]), source_ref=source_ref(record)))
    return {
        "BLIND": blind_audit["impact_analysis"],
        "PLANNER": planner,
        "IMPLEMENTER": implementer,
    }


@boundary("B11")
def build_phase_b_origin_catalog(
    blind_audit: Mapping[str, Any], planner_impact_closure: Mapping[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the immutable Controller namespace used by the Phase-B model wire."""
    sources = _impact_sources(blind_audit, planner_impact_closure, implementation_impact_closure)
    origins: list[dict[str, Any]] = []
    for authority in ("BLIND", "PLANNER", "IMPLEMENTER"):
        for classification, canonical_group in _ORIGIN_CLASSIFICATION_GROUP.items():
            group = "affected_consumers" if authority == "BLIND" and classification == "IN_SCOPE" else canonical_group
            for row in sources[authority].get(group, []):
                if authority == "BLIND":
                    source_id = row["impact_id"]
                    source_revision = stable_fingerprint(
                        {"candidate_id": blind_audit["candidate_id"], "classification": classification,
                         "current_row": row}, length=64,
                    )
                else:
                    reference = row.get("source_ref")
                    if not isinstance(reference, Mapping):
                        raise ArtifactContractError(
                            code="ORIGIN_SOURCE_REF_MISSING", field=f"{authority}.{group}",
                            message="A current Controller origin has no source_ref",
                            expected="Controller-owned source_id and source_revision",
                            actual=reference,
                            failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
                        )
                    source_id = reference.get("source_id")
                    source_revision = reference.get("source_revision")
                identity = [authority, classification, source_id, source_revision]
                origins.append({
                    "origin_ref": "ORIGIN-" + stable_fingerprint(identity, length=24),
                    "authority": authority,
                    "classification": classification,
                    "source_id": source_id,
                    "source_revision": source_revision,
                    "current_row": copy.deepcopy(row),
                })
    handles = [row["origin_ref"] for row in origins]
    if len(handles) != len(set(handles)):
        raise ArtifactContractError(
            code="ORIGIN_REF_COLLISION", field="origin_catalog.origins",
            message="Controller origin handles are not unique",
            expected="One stable handle per current authority/classification/revision",
            failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
        )
    catalog: dict[str, Any] = {
        "schema_version": PHASE_B_ORIGIN_CATALOG_VERSION,
        "candidate_id": blind_audit["candidate_id"],
        "origins": origins,
    }
    catalog["fingerprint"] = stable_fingerprint(catalog, length=64)
    return catalog


def validate_phase_b_origin_catalog(
    catalog: Mapping[str, Any], *, blind_audit: Mapping[str, Any],
    planner_impact_closure: Mapping[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any],
) -> None:
    expected = build_phase_b_origin_catalog(
        blind_audit, planner_impact_closure, implementation_impact_closure,
    )
    if dict(catalog) != expected:
        raise ArtifactContractError(
            code="ORIGIN_CATALOG_TAMPERED", field="origin_catalog",
            message="Phase-B origin catalog does not match current authoritative state",
            expected="The exact Controller-derived catalog and fingerprint",
            failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
        )


def _origin_allowed(row: Mapping[str, Any], group: str, *, match: bool = False) -> bool:
    authority, classification = row["authority"], row["classification"]
    if match:
        if authority == "BLIND":
            return False
        if group == "blind_contract_dispositions":
            return classification == "CHANGED_CONTRACT"
        return group == "blind_consumer_dispositions" and classification != "CHANGED_CONTRACT"
    if group == "blind_contract_dispositions":
        return authority == "BLIND" and classification == "CHANGED_CONTRACT"
    if group == "blind_consumer_dispositions":
        return authority == "BLIND" and classification == "IN_SCOPE"
    if group == "planner_consumer_dispositions":
        return authority == "PLANNER" and classification == "IN_SCOPE"
    if group == "implementer_consumer_dispositions":
        return (authority == "IMPLEMENTER" and classification == "IN_SCOPE"
                and row["current_row"].get("source") == "DISCOVERED")
    if group == "not_affected_dispositions":
        return classification == "NOT_AFFECTED"
    if group == "related_follow_up_dispositions":
        return classification == "RELATED_OUT_OF_SCOPE"
    return False


def _resolve_origin_ref(
    catalog: Mapping[str, Any], raw: object, *, group: str, field: str, match: bool = False,
) -> Mapping[str, Any]:
    require_type(raw, str, field=field)
    entry = next((row for row in catalog["origins"] if row["origin_ref"] == raw), None)
    if entry is None:
        raise ArtifactContractError(
            code="ORIGIN_REF_UNKNOWN", field=field,
            message="Evaluator origin_ref is not in the current Controller catalog",
            expected="An exact current origin_ref from the disclosed catalog", actual=raw,
        )
    if not _origin_allowed(entry, group, match=match):
        raise ArtifactContractError(
            code="ORIGIN_REF_INCOMPATIBLE", field=field,
            message="Evaluator origin_ref has the wrong authority or classification for this field",
            expected="A handle allowed for this exact disposition/match group", actual=raw,
        )
    return entry


@boundary("B11")
def admit_evaluation_artifact(
    evaluation: Mapping[str, Any], *, origin_catalog: Mapping[str, Any],
    blind_audit: Mapping[str, Any], workspace: Path, candidate_id: str,
    changed_paths: Sequence[str], planner_impact_closure: Mapping[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any], owner_allowed_paths: Sequence[str] = (),
) -> dict[str, Any]:
    """Resolve the handle-only model wire into Controller-owned canonical metadata."""
    validate_phase_b_origin_catalog(
        origin_catalog, blind_audit=blind_audit,
        planner_impact_closure=planner_impact_closure,
        implementation_impact_closure=implementation_impact_closure,
    )
    require_type(evaluation, dict, field="evaluation")
    required = set(EVALUATOR_SCHEMA["required"])
    ensure_exact_keys(dict(evaluation), allowed=required, required=required, field="evaluation")
    challenge = evaluation["impact_challenge"]
    require_type(challenge, dict, field="impact_challenge")
    fields = IMPACT_CHALLENGE_SCHEMA["properties"]
    ensure_exact_keys(challenge, allowed=fields, required=fields, field="impact_challenge")
    validate_impact_structure(
        challenge, schema=IMPACT_CHALLENGE_SCHEMA, workspace=workspace,
        field="impact_challenge",
    )
    canonical = copy.deepcopy(dict(evaluation))
    canonical_challenge: dict[str, Any] = {"coverage_summary": challenge["coverage_summary"]}
    for group in _CHALLENGE_DISPOSITIONS:
        if group == "changed_path_dispositions":
            canonical_challenge[group] = copy.deepcopy(challenge[group])
            continue
        allowed = [row for row in origin_catalog["origins"]
                   if _origin_allowed(row, group)]
        if len(challenge[group]) != len(allowed):
            raise ArtifactContractError(
                code="ORIGIN_DISPOSITION_CARDINALITY", field=f"impact_challenge.{group}",
                message="Evaluator disposition count must equal the current authoritative origin count",
                expected=len(allowed), actual=len(challenge[group]),
                failure_kind=ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT,
            )
        _validate_rows(challenge[group], fields[group], workspace=workspace,
                       field=f"impact_challenge.{group}")
        admitted_rows = []
        for index, wire_row in enumerate(challenge[group]):
            row_field = f"impact_challenge.{group}[{index}]"
            entry = _resolve_origin_ref(
                origin_catalog, wire_row["origin_ref"], group=group,
                field=f"{row_field}.origin_ref",
            )
            admitted = {key: copy.deepcopy(value) for key, value in wire_row.items()
                        if key not in {"origin_ref", "matches"}}
            if group.startswith("blind_"):
                admitted["impact_id"] = entry["source_id"]
                admitted["matches"] = []
                if wire_row["matches"] and not any(
                    _origin_allowed(row, group, match=True)
                    for row in origin_catalog["origins"]
                ):
                    raise ArtifactContractError(
                        code="ORIGIN_MATCH_UNAVAILABLE", field=f"{row_field}.matches",
                        message="No compatible authoritative origin exists for this match group",
                        expected="No matches", actual=len(wire_row["matches"]),
                        failure_kind=ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT,
                    )
                for match_index, match in enumerate(wire_row["matches"]):
                    matched = _resolve_origin_ref(
                        origin_catalog, match["origin_ref"], group=group, match=True,
                        field=f"{row_field}.matches[{match_index}].origin_ref",
                    )
                    admitted["matches"].append({
                        "source": matched["authority"],
                        "classification": matched["classification"],
                        "reference": matched["source_id"],
                        "source_revision": matched["source_revision"],
                    })
            elif group in {"not_affected_dispositions", "related_follow_up_dispositions"}:
                admitted.update(source=entry["authority"], reference=entry["source_id"],
                                source_revision=entry["source_revision"])
            else:
                admitted.update(reference=entry["source_id"], source_revision=entry["source_revision"])
            admitted_rows.append(admitted)
        canonical_challenge[group] = admitted_rows
    canonical["impact_challenge"] = canonical_challenge
    validate_evaluation_artifact(
        canonical, blind_audit=blind_audit, workspace=workspace, candidate_id=candidate_id,
        changed_paths=changed_paths, planner_impact_closure=planner_impact_closure,
        implementation_impact_closure=implementation_impact_closure,
        owner_allowed_paths=owner_allowed_paths,
    )
    return canonical



def _validate_origin_reference(row: Mapping[str, Any], origins: Sequence[Mapping[str, Any]]) -> str:
    identifier = row["reference"]
    record = next((item for item in origins if item["source_ref"]["source_id"] == identifier), None)
    if record is None or record["source_ref"]["source_revision"] != row["source_revision"]:
        raise ArtifactContractError(
            code="ORIGIN_METADATA_MISMATCH", field="impact_challenge.origin",
            message="Evaluator reference is unknown or has a stale source revision",
            expected="Controller-derived current source metadata",
            actual=identifier,
            failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
        )
    return identifier


def validate_impact_challenge(
    evaluation: Mapping[str, Any], *, blind_audit: Mapping[str, Any], workspace: Path,
    planner_impact_closure: Mapping[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any], changed_paths: Sequence[str],
) -> None:
    challenge = evaluation["impact_challenge"]
    validate_impact_structure(challenge, schema=CANONICAL_IMPACT_CHALLENGE_SCHEMA, workspace=workspace, field="impact_challenge")
    require_type(challenge, dict, field="impact_challenge")
    fields = CANONICAL_IMPACT_CHALLENGE_SCHEMA["properties"]
    ensure_exact_keys(challenge, allowed=fields, required=fields, field="impact_challenge")
    impact_text(challenge["coverage_summary"], field="impact_challenge.coverage_summary")
    sources = _impact_sources(blind_audit, planner_impact_closure, implementation_impact_closure)
    if not sources["BLIND"]["applicable"] and any(sources[source].get("applicable") for source in ("PLANNER", "IMPLEMENTER")):
        _model_conflict("BLIND_APPLICABILITY_CONFLICT", field="impact_challenge",
                        message="Blind non-applicability conflicts with the engineering candidate")
    expected = {
        "blind_contract_dispositions": {row["impact_id"] for row in sources["BLIND"]["changed_contracts"]},
        "blind_consumer_dispositions": {row["impact_id"] for row in sources["BLIND"]["affected_consumers"]},
        "planner_consumer_dispositions": {row["source_ref"]["source_id"] for row in sources["PLANNER"].get("in_scope_consumers", [])},
        "implementer_consumer_dispositions": {row["source_ref"]["source_id"] for row in sources["IMPLEMENTER"]["in_scope_consumers"] if row["source"] == "DISCOVERED"},
        "changed_path_dispositions": {safe_impact_path(path, field="changed_paths") for path in changed_paths},
    }
    for group, input_group in (("not_affected_dispositions", "not_affected_consumers"), ("related_follow_up_dispositions", "related_out_of_scope")):
        expected[group] = {
            (source, row["impact_id"] if source == "BLIND" else row["source_ref"]["source_id"])
            for source, ledger in sources.items() for row in ledger.get(input_group, [])
        }
    final_ids = {row["finding_id"] for row in evaluation["findings"]}
    classifications = {"CHANGED_CONTRACT": "changed_contracts", "IN_SCOPE": "in_scope_consumers", "NOT_AFFECTED": "not_affected_consumers", "RELATED_OUT_OF_SCOPE": "related_out_of_scope"}
    for group, dispositions in _CHALLENGE_DISPOSITIONS.items():
        rows = challenge[group]
        _validate_rows(rows, fields[group], workspace=workspace, field=f"impact_challenge.{group}")
        seen: set = set()
        for row in rows:
            if group.startswith("blind_"):
                key = row["impact_id"]
                for match in row["matches"]:
                    _validate_origin_reference(match, sources[match["source"]].get(classifications[match["classification"]], []))
                    contract_match = match["classification"] == "CHANGED_CONTRACT"
                    if contract_match != (group == "blind_contract_dispositions"):
                        _integrity_failure("ORIGIN_MATCH_CLASSIFICATION", field=f"impact_challenge.{group}.matches",
                                           message="Blind contract/consumer match classification is invalid", actual=match)
                if group == "blind_consumer_dispositions" and row["disposition"] == "COVERED_IN_SCOPE" and not any(match["classification"] == "IN_SCOPE" for match in row["matches"]):
                    _model_conflict("COVERED_WITHOUT_IN_SCOPE", field=f"impact_challenge.{group}",
                                    message="COVERED_IN_SCOPE requires an actual IN_SCOPE ledger reference")
            elif "source" in row:
                key = (row["source"], row["reference"])
                if row["source"] == "BLIND":
                    pass
                else:
                    input_group = "not_affected_consumers" if group == "not_affected_dispositions" else "related_out_of_scope"
                    _validate_origin_reference(row, sources[row["source"]].get(input_group, []))
            elif "path" in row:
                key = safe_impact_path(row["path"], field="changed_path_dispositions.path")
            else:
                source = "PLANNER" if group == "planner_consumer_dispositions" else "IMPLEMENTER"
                key = _validate_origin_reference(row, sources[source].get("in_scope_consumers", []))
            if key in seen:
                _model_conflict("DISPOSITION_DUPLICATE", field=f"impact_challenge.{group}",
                                message=f"{group} contains duplicate dispositions", actual=key)
            seen.add(key)
            finding_ids = row["finding_ids"]
            if len(finding_ids) != len(set(finding_ids)) or not set(finding_ids) <= final_ids:
                _model_conflict("FINDING_REFERENCE_INVALID", field=f"impact_challenge.{group}.finding_ids",
                                message="Impact finding_ids must uniquely reference existing final findings", actual=finding_ids)
            negative = row["disposition"] != dispositions[0]
            if row["disposition"] == "PROMOTED_IN_SCOPE":
                transition = next((event for event in implementation_impact_closure["source_inventory"]["transitions"]
                                   if event["source_ref"] == {"source_id": row["reference"],
                                                              "source_revision": row["source_revision"]}), None)
                target_id = transition["target_ref"]["source_id"] if transition else None
                target = next((item for item in sources["IMPLEMENTER"]["in_scope_consumers"]
                               if item["source_ref"]["source_id"] == target_id), None)
                confirmation = next((item for item in challenge["implementer_consumer_dispositions"]
                                     if item["reference"] == target_id and item["disposition"] == "CONFIRMED"), None)
                if row["source"] == "BLIND" or target is None or confirmation is None:
                    _model_conflict("PROMOTION_UNCONFIRMED", field=f"impact_challenge.{group}",
                                    message="PROMOTED_IN_SCOPE requires an admitted promotion and independently confirmed current target")
                negative = False
            if negative and not finding_ids:
                _model_conflict("NEGATIVE_WITHOUT_FINDING", field=f"impact_challenge.{group}.finding_ids",
                                message="Every negative impact disposition requires a corresponding final finding")
            if negative and evaluation["status"] == EvaluatorStatus.PASS.value:
                _model_conflict("PASS_WITH_NEGATIVE_DISPOSITION", field=f"impact_challenge.{group}",
                                message="Evaluator PASS forbids negative impact dispositions")
            if (
                group == "blind_contract_dispositions"
                and row["disposition"] in {"MATERIAL_GAP", "MODEL_CONFLICT"}
                and evaluation["status"] != EvaluatorStatus.REPLAN_REQUIRED.value
            ):
                _model_conflict("CONTRACT_GAP_STATUS", field="status",
                                message=f"Blind contract {row['disposition']} requires status REPLAN_REQUIRED",
                                actual=evaluation["status"])
        if seen != expected[group]:
            _model_conflict("DISPOSITION_SET_MISMATCH", field=f"impact_challenge.{group}",
                            message=f"{group} must disposition every and only authoritative input row",
                            actual={"seen": sorted(seen, key=str), "expected": sorted(expected[group], key=str)})
    _exact_paths([row["path"] for row in challenge["changed_path_dispositions"]], changed_paths, workspace=workspace, field="changed_path_dispositions")


@boundary("B11")
def validate_evaluation_artifact(
    evaluation: Mapping[str, Any], *, blind_audit: Mapping[str, Any], workspace: Path,
    candidate_id: str, changed_paths: Sequence[str],
    planner_impact_closure: Mapping[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any],
    owner_allowed_paths: Sequence[str] = (),
) -> None:
    validate_blind_audit(blind_audit, workspace=workspace, candidate_id=candidate_id, changed_paths=changed_paths, owner_allowed_paths=owner_allowed_paths)
    expected_fields = {
        "protocol_version",
        "status",
        "summary",
        "candidate_id",
        "impact_challenge",
        "blind_finding_dispositions",
        "findings",
        "reason",
    }
    if set(evaluation) != expected_fields:
        raise ArtifactContractError(code="EVALUATION_FIELDS", field="evaluation",
            message="Evaluation fields invalid", expected="The exact Evaluator fields", actual=sorted(evaluation))
    if evaluation["protocol_version"] != EVALUATOR_PROTOCOL_VERSION:
        raise ArtifactContractError(code="EVALUATOR_VERSION", field="protocol_version",
            message="Evaluator protocol mismatch", expected=EVALUATOR_PROTOCOL_VERSION,
            actual=evaluation["protocol_version"])
    if evaluation["candidate_id"] != candidate_id or implementation_impact_closure["candidate_id"] != candidate_id:
        _integrity_failure("EVALUATION_CANDIDATE_STALE", field="candidate_id",
                           message="Evaluation/implementation impact is stale for the current candidate",
                           actual=evaluation["candidate_id"])
    status = evaluation["status"]
    if status not in set(enum_values(EvaluatorStatus)):
        _model_conflict("EVALUATOR_STATUS", field="status", message="Evaluator status invalid", actual=status)
    if not isinstance(evaluation["summary"], str) or not evaluation["summary"].strip():
        raise ArtifactContractError(code="EVALUATOR_SUMMARY_EMPTY", field="summary",
            message="Evaluator summary must be non-empty", expected="A non-empty summary", actual=evaluation["summary"])
    blind_ids = {str(item["finding_id"]) for item in blind_audit["findings"]}
    dispositions = evaluation["blind_finding_dispositions"]
    if not isinstance(dispositions, list):
        raise ArtifactContractError(code="TYPE_MISMATCH", field="blind_finding_dispositions",
            message="blind_finding_dispositions must be a list", expected="list", actual=type(dispositions).__name__)
    disposition_by_id: dict[str, str] = {}
    for index, row in enumerate(dispositions):
        if not isinstance(row, Mapping) or set(row) != {
            "finding_id",
            "disposition",
            "evidence",
        }:
            raise ArtifactContractError(code="BLIND_FINDING_DISPOSITION_FIELDS",
                field=f"blind_finding_dispositions[{index}]",
                message=f"blind_finding_dispositions[{index}] fields invalid",
                expected="finding_id, disposition and evidence", actual=row)
        finding_id = str(row["finding_id"])
        if finding_id in disposition_by_id:
            _model_conflict("BLIND_FINDING_DISPOSITION_DUPLICATE", field="blind_finding_dispositions",
                            message="Blind finding dispositions must be unique", actual=finding_id)
        if row["disposition"] not in {"RETAINED", "DISMISSED_WITH_EVIDENCE"}:
            _model_conflict("BLIND_FINDING_DISPOSITION_INVALID", field=f"blind_finding_dispositions[{index}].disposition",
                            message="Blind finding disposition invalid", actual=row["disposition"])
        evidence = row["evidence"]
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise ArtifactContractError(code="IMPACT_EVIDENCE_EMPTY",
                field=f"blind_finding_dispositions[{index}].evidence",
                message="Every blind finding disposition requires evidence",
                expected="Concrete evidence strings", actual=evidence)
        disposition_by_id[finding_id] = str(row["disposition"])
    if set(disposition_by_id) != blind_ids:
        _model_conflict("BLIND_FINDING_SET_MISMATCH", field="blind_finding_dispositions",
                        message="Phase B must disposition every and only Phase A blind finding",
                        actual=sorted(disposition_by_id))

    findings = evaluation["findings"]
    if not isinstance(findings, list):
        raise ArtifactContractError(code="TYPE_MISMATCH", field="findings",
            message="Evaluation findings must be a list", expected="list", actual=type(findings).__name__)
    final_ids: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            raise ArtifactContractError(code="TYPE_MISMATCH", field=f"evaluation.findings[{index}]",
                message=f"evaluation.findings[{index}] must be an object", expected="object", actual=type(finding).__name__)
        finding_id = _validate_finding(finding, field=f"evaluation.findings[{index}]")
        if finding_id in final_ids:
            _model_conflict("FINAL_FINDING_DUPLICATE", field="findings",
                            message="Final finding ids must be unique", actual=finding_id)
        final_ids.add(finding_id)
    retained = {
        finding_id
        for finding_id, disposition in disposition_by_id.items()
        if disposition == "RETAINED"
    }
    dismissed = blind_ids - retained
    if not retained.issubset(final_ids):
        _model_conflict("RETAINED_FINDING_DROPPED", field="findings",
                        message="Every retained blind finding must remain in final findings", actual=sorted(retained-final_ids))
    if dismissed & final_ids:
        _model_conflict("DISMISSED_FINDING_RETAINED", field="findings",
                        message="Dismissed blind findings cannot remain in final findings", actual=sorted(dismissed & final_ids))

    reason = evaluation["reason"]
    if not isinstance(reason, str):
        raise ArtifactContractError(code="TYPE_MISMATCH", field="reason",
            message="Evaluator reason must be a string", expected="string", actual=type(reason).__name__)
    if status == EvaluatorStatus.PASS.value:
        if findings:
            _model_conflict("PASS_WITH_FINDINGS", field="findings", message="Evaluator PASS requires no findings")
        if retained:
            _model_conflict("PASS_WITH_RETAINED_FINDINGS", field="blind_finding_dispositions",
                            message="Evaluator PASS cannot retain blind findings")
    elif status == EvaluatorStatus.FINDINGS.value:
        if not findings:
            _model_conflict("FINDINGS_EMPTY", field="findings",
                            message="Evaluator FINDINGS requires at least one finding")
    elif status in {
        EvaluatorStatus.REPLAN_REQUIRED.value,
        EvaluatorStatus.BLOCKED.value,
        EvaluatorStatus.NEEDS_USER_DECISION.value,
    }:
        if not reason.strip():
            _model_conflict("EVALUATOR_REASON_EMPTY", field="reason",
                            message=f"Evaluator {status} requires a concrete reason", actual=reason)
    validate_impact_challenge(
        evaluation, blind_audit=blind_audit, workspace=workspace,
        planner_impact_closure=planner_impact_closure,
        implementation_impact_closure=implementation_impact_closure, changed_paths=changed_paths,
    )


@boundary("B10")
def run_evaluator(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    task_contract: Mapping[str, Any],
    preflight: Mapping[str, Any],
    owner_allowed_paths: list[str],
    changed_paths: list[str],
    candidate_id: str,
    implementation_contract: Mapping[str, Any],
    verification_plan: Mapping[str, Any],
    contract_closure: Mapping[str, Any],
    checks_evidence: object,
    runtime_evidence: object,
    plan: dict[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any],
    revision_binding: Mapping[str, Any],
    on_blind_audit: Callable[[dict[str, Any]], None],
    on_phase_complete: Callable[[str], None],
    on_origin_catalog: Callable[[dict[str, Any]], None],
    on_raw_report: Callable[[str, int, str], None] | None = None,
    runtime_probe_guidance: list[str] | None = None,
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
    timeout: float = 900,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (not callable(on_blind_audit) or not callable(on_phase_complete)
            or not callable(on_origin_catalog)):
        _integrity_failure("EVALUATOR_GUARDS_MISSING", field="run_evaluator",
                           message="Evaluator requires immutable blind persistence and current-candidate guards")
    thread_id = codex.start_thread(
        cwd=workspace,
        execution_role=ExecutionRole.EVALUATOR,
        developer_instructions=EVALUATOR_INSTRUCTIONS,
        on_started=on_thread_started,
    )

    def admit_phase(phase: str, prompt: str, schema: dict, validate) -> dict:
        correction = ReportCorrectionState()
        for attempt in range(MAX_REPORT_CORRECTIONS + 1):
            raw = codex.run_turn(
                thread_id=thread_id, prompt=prompt, output_schema=schema,
                skills=explicit_skills, on_heartbeat=on_heartbeat,
                timeout=timeout if not attempt else min(timeout, 300),
            )
            if on_raw_report is not None:
                on_raw_report(phase, attempt, raw)
            on_phase_complete(phase)
            report = _parse_json(raw, label=f"Evaluator {phase}")
            correction.observe(report)
            try:
                admitted = validate(report)
                return report if admitted is None else admitted
            except ArtifactContractError as error:
                if error.failure_kind is not ArtifactFailureKind.LOCAL_WIRE_ERROR:
                    raise
                fields = correction.next_fields(error, attempt=attempt)
                prompt = correction_prompt(error, fields=fields, role=f"Evaluator {phase}")
        raise ReportRecoveryStop("REPORT_CORRECTION_EXHAUSTED")
    phase_a_prompt = f"""
PHASE A — BLIND DISCOVERY.

Исходная задача пользователя:
--- BEGIN RAW TASK ---
{task_prompt}
--- END RAW TASK ---

USER TASK CONTRACT:
{json.dumps(task_contract, ensure_ascii=False, indent=2)}

Sanitized preflight:
{json.dumps(preflight, ensure_ascii=False, indent=2)}

Owner allowed_paths:
{json.dumps(owner_allowed_paths, ensure_ascii=False, indent=2)}

Current candidate id:
{candidate_id}

Фактически изменённые paths (только навигационная подсказка, не граница review):
{json.dumps(changed_paths, ensure_ascii=False, indent=2)}

Не получая Contract/check results, самостоятельно исследуй repository и current diff.
Не вставляй весь diff в ответ. Верни independent impact_analysis с собственными IDs,
outward search evidence и exact changed-path review в immutable structured blind audit.
""".strip()
    blind_audit = admit_phase("PHASE_A", phase_a_prompt, BLIND_AUDIT_SCHEMA,
        lambda value: validate_blind_audit(value, workspace=workspace, candidate_id=candidate_id,
                                         changed_paths=changed_paths, owner_allowed_paths=owner_allowed_paths))
    # The Controller persists the blind artifact before any Contract/check framing
    # is disclosed to the same evaluator thread.
    on_blind_audit(copy.deepcopy(blind_audit))
    # Only the Controller-normalized, current artifact may enter Phase B. Full
    # Planner reasoning is used nowhere in either prompt.
    validate_implementation_impact_closure(
        implementation_impact_closure, candidate_id=candidate_id, plan=plan,
        contract=implementation_contract, changed_paths=changed_paths, revision_binding=revision_binding,
    )
    planner_impact = copy.deepcopy(plan["impact_closure"]) if plan is not None else None
    disclosed_sources = _impact_sources(blind_audit, planner_impact, implementation_impact_closure)
    origin_catalog = build_phase_b_origin_catalog(
        blind_audit, planner_impact, implementation_impact_closure,
    )
    on_origin_catalog(copy.deepcopy(origin_catalog))

    phase_b_prompt = f"""
PHASE B — INDEPENDENT IMPACT CHALLENGE.

Phase A blind audit уже зафиксирован и не может быть забыт:
{json.dumps(blind_audit, ensure_ascii=False, indent=2)}

Теперь раскрыты только Controller-normalized artifacts; Planner reasoning и Implementer prose
по-прежнему скрыты.

IMPLEMENTATION CONTRACT:
{json.dumps(implementation_contract, ensure_ascii=False, indent=2)}

NORMALIZED PLANNER IMPACT CLOSURE:
{json.dumps(disclosed_sources["PLANNER"], ensure_ascii=False, indent=2)}

CONTROLLER-NORMALIZED IMPLEMENTATION IMPACT CLOSURE:
{json.dumps(implementation_impact_closure, ensure_ascii=False, indent=2)}

CONTROLLER PHASE-B ORIGIN CATALOG (use exact origin_ref only; do not reproduce metadata):
{json.dumps(origin_catalog, ensure_ascii=False, indent=2)}

VERIFICATION PLAN:
{json.dumps(verification_plan, ensure_ascii=False, indent=2)}

CONTRACT CLOSURE RECORD:
{json.dumps(contract_closure, ensure_ascii=False, indent=2)}

DETERMINISTIC CONTROLLER EVIDENCE:
{json.dumps(checks_evidence, ensure_ascii=False, indent=2)}

RUNTIME EVIDENCE / SKIP RECORD:
{json.dumps(runtime_evidence, ensure_ascii=False, indent=2)}

Safe runtime probe guidance, если настроено:
{json.dumps(runtime_probe_guidance or [], ensure_ascii=False, indent=2)}

Проверь прежние impact models независимо от их согласованности. Для каждого blind finding
верни RETAINED либо DISMISSED_WITH_EVIDENCE. impact_challenge должен disposition каждый
blind contract/consumer ID, каждый Planner IN_SCOPE, Implementer DISCOVERED, NOT_AFFECTED
и RELATED_OUT_OF_SCOPE всех источников и каждый actual changed path ровно один раз.
Во всех origin-bearing rows и matches укажи только exact origin_ref из допустимого dynamic enum.
Не возвращай source/classification/source_id/source_revision: Controller canonicalizes их сам.
Negative dispositions требуют final finding_ids и запрещают PASS. Новые findings разрешены.
""".strip()
    verdict = admit_phase("PHASE_B", phase_b_prompt, build_evaluator_schema(origin_catalog),
        lambda value: admit_evaluation_artifact(
            value, origin_catalog=origin_catalog, blind_audit=blind_audit,
            workspace=workspace, candidate_id=candidate_id, changed_paths=changed_paths,
            planner_impact_closure=planner_impact,
            implementation_impact_closure=implementation_impact_closure,
            owner_allowed_paths=owner_allowed_paths,
        ))
    return blind_audit, verdict
