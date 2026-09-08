from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from slivin_harness.app_server import CodexAppServer
from slivin_harness.execution import ExecutionRole
from slivin_harness.impact import impact_paths, impact_text, safe_impact_path, validate_owner_prose_boundary
from slivin_harness.implementer import validate_implementation_impact_closure
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
    "not_affected_dispositions": ("CONFIRMED_NOT_AFFECTED", "ACTUALLY_AFFECTED", "INSUFFICIENT_EVIDENCE"),
    "related_follow_up_dispositions": ("CONFIRMED_OUT_OF_SCOPE", "ACTUALLY_IN_SCOPE", "UNSUPPORTED"),
    "changed_path_dispositions": ("UNDERSTOOD", "SUSPICIOUS", "UNJUSTIFIED"),
}


def _challenge_schema() -> dict:
    properties: dict[str, Any] = {}
    for group, dispositions in _CHALLENGE_DISPOSITIONS.items():
        enums: dict[str, Sequence[str]] = {"disposition": dispositions}
        if group.startswith("blind_"):
            reference = "impact_id"
        elif group in {"not_affected_dispositions", "related_follow_up_dispositions"}:
            reference = "reference"
            enums["source"] = ("BLIND", "PLANNER", "IMPLEMENTER")
        else:
            reference = "path" if group == "changed_path_dispositions" else "name"
        schema = _rows(text=(reference, "reason"), lists=("evidence_paths", "evidence", "finding_ids"), enums=enums)
        if group.startswith("blind_"):
            fields = schema["items"]["properties"]
            fields["matches"] = _rows(
                text=("name",),
                enums={"source": ("PLANNER", "IMPLEMENTER"), "classification": ("CHANGED_CONTRACT", "IN_SCOPE", "NOT_AFFECTED", "RELATED_OUT_OF_SCOPE")},
            )
            schema["items"]["required"].append("matches")
        properties[group] = schema
    properties["coverage_summary"] = {"type": "string"}
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


IMPACT_CHALLENGE_SCHEMA = _challenge_schema()

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
- Сопоставь независимые blind contracts/consumers с прежними ledgers; одинаковые имена
  не требуются. Dispositions ссылаются на свои blind impact_id, а matches — на concrete
  normalized Planner/Implementer names и classification. COVERED_IN_SCOPE требует IN_SCOPE match.
- Каждый blind contract, blind affected consumer, Planner IN_SCOPE и Implementer DISCOVERED
  consumer получает evidence-backed disposition. Independently challenge каждый NOT_AFFECTED
  и RELATED_OUT_OF_SCOPE из всех трёх ledgers; source=BLIND references используют impact_id,
  source=PLANNER/IMPLEMENTER — name. Не считай согласие двух прежних агентов доказательством.
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
        raise RuntimeError(f"{label} returned invalid JSON structured output.\n" + raw) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} structured output must be an object")
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
        raise RuntimeError(f"{field} fields invalid")
    finding_id = str(value["finding_id"]).strip()
    if not finding_id:
        raise RuntimeError(f"{field}.finding_id must be non-empty")
    if value["severity"] not in {"HIGH", "MEDIUM"}:
        raise RuntimeError(f"{field}.severity must be HIGH or MEDIUM")
    if value["category"] not in set(_FINDING_CATEGORIES):
        raise RuntimeError(f"{field}.category is unsupported")
    for name in ("title", "failure_mode", "required_action"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise RuntimeError(f"{field}.{name} must be non-empty")
    validate_proof_target(value["required_proof"], field=f"{field}.required_proof")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        raise RuntimeError(f"{field}.evidence must contain concrete strings")
    return finding_id


def _name(value: str) -> str:
    return " ".join(value.split()).casefold()


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
                impact_text(value, field=key_field)
                if "enum" in kind and value not in kind["enum"]:
                    raise RuntimeError(f"{key_field} enum invalid")
            elif kind["items"]["type"] == "object":
                _validate_rows(value, kind, workspace=workspace, field=key_field)
            else:
                values = require_string_list(value, field=key_field)
                if not values and key != "finding_ids":
                    raise RuntimeError(f"{key_field} requires concrete evidence")
                for entry in values:
                    impact_text(entry, field=key_field)
                if key in {"paths", "evidence_paths"}:
                    impact_paths(values, field=key_field, workspace=workspace)
                if key == "symbols" and any(
                    any(char.isspace() for char in entry) or not any(char.isalnum() for char in entry)
                    for entry in values
                ):
                    raise RuntimeError(f"{key_field} requires concrete identifiers")


def _exact_paths(paths: Sequence[str], changed_paths: Sequence[str], *, workspace: Path, field: str) -> None:
    normalized = [safe_impact_path(path, field=field) for path in paths]
    actual = {safe_impact_path(path, field="changed_paths") for path in changed_paths}
    if len(normalized) != len(set(normalized)) or set(normalized) != actual:
        raise RuntimeError(f"{field} must cover every actual changed path exactly once")
    impact_paths(normalized, field=field, workspace=workspace, allow_missing=True)


def validate_blind_audit(
    audit: Mapping[str, Any], *, workspace: Path, candidate_id: str,
    changed_paths: Sequence[str], owner_allowed_paths: Sequence[str] = (),
) -> None:
    if set(audit) != set(BLIND_AUDIT_SCHEMA["required"]):
        raise RuntimeError("Blind audit fields invalid")
    if audit["protocol_version"] != BLIND_AUDIT_VERSION:
        raise RuntimeError("Blind audit protocol mismatch")
    if not isinstance(audit["summary"], str) or not audit["summary"].strip():
        raise RuntimeError("Blind audit summary must be non-empty")
    impact_text(audit["candidate_id"], field="blind_audit.candidate_id")
    if audit["candidate_id"] != candidate_id:
        raise RuntimeError("Blind audit is stale for the current candidate")
    analysis = audit["impact_analysis"]
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
                raise RuntimeError(f"{group} requires unique safe {prefix} impact IDs")
            impact_ids.add(impact_id)
            name = _name(row["name"])
            if name in names or (group != "changed_contracts" and name in classifications):
                raise RuntimeError("Blind impact names must be unique and classifications disjoint")
            names.add(name)
            if group != "changed_contracts":
                classifications.add(name)
            if group == "changed_contracts" and " ".join(row["before"].split()) == " ".join(row["after"].split()):
                raise RuntimeError("Blind changed contract requires distinct before/after semantics")
    _exact_paths([row["path"] for row in analysis["changed_path_review"]], changed_paths, workspace=workspace, field="Blind changed_path_review")
    if not analysis["search_evidence"]:
        raise RuntimeError("Blind impact requires independent search evidence")
    if analysis["applicable"] and not analysis["changed_contracts"]:
        raise RuntimeError("Blind engineering impact requires actual changed contracts")
    findings = audit["findings"]
    if not isinstance(findings, list):
        raise RuntimeError("Blind audit findings must be a list")
    ids: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            raise RuntimeError(f"blind_audit.findings[{index}] must be an object")
        finding_id = _validate_finding(finding, field=f"blind_audit.findings[{index}]")
        if finding_id in ids:
            raise RuntimeError("Blind audit finding ids must be unique")
        ids.add(finding_id)
    advisories = audit["advisories"]
    if not isinstance(advisories, list) or not all(
        isinstance(item, str) for item in advisories
    ):
        raise RuntimeError("Blind audit advisories must be strings")
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
            raise RuntimeError("Blind prose exception requires specific explanation and no behavioral/runtime obligations")


def _impact_sources(
    blind_audit: Mapping[str, Any], planner_impact_closure: Mapping[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    return {
        "BLIND": blind_audit["impact_analysis"],
        "PLANNER": planner_impact_closure or {},
        "IMPLEMENTER": implementation_impact_closure["post_patch_impact"],
    }


def validate_impact_challenge(
    evaluation: Mapping[str, Any], *, blind_audit: Mapping[str, Any], workspace: Path,
    planner_impact_closure: Mapping[str, Any] | None,
    implementation_impact_closure: Mapping[str, Any], changed_paths: Sequence[str],
) -> None:
    challenge = evaluation["impact_challenge"]
    require_type(challenge, dict, field="impact_challenge")
    fields = IMPACT_CHALLENGE_SCHEMA["properties"]
    ensure_exact_keys(challenge, allowed=fields, required=fields, field="impact_challenge")
    impact_text(challenge["coverage_summary"], field="impact_challenge.coverage_summary")
    sources = _impact_sources(blind_audit, planner_impact_closure, implementation_impact_closure)
    if not sources["BLIND"]["applicable"] and any(sources[source].get("applicable") for source in ("PLANNER", "IMPLEMENTER")):
        raise RuntimeError("Blind non-applicability conflicts with the engineering candidate")
    expected = {
        "blind_contract_dispositions": {row["impact_id"] for row in sources["BLIND"]["changed_contracts"]},
        "blind_consumer_dispositions": {row["impact_id"] for row in sources["BLIND"]["affected_consumers"]},
        "planner_consumer_dispositions": {_name(row["name"]) for row in sources["PLANNER"].get("in_scope_consumers", [])},
        "implementer_consumer_dispositions": {_name(row["name"]) for row in sources["IMPLEMENTER"]["in_scope_consumers"] if row["source"] == "DISCOVERED"},
        "changed_path_dispositions": {safe_impact_path(path, field="changed_paths") for path in changed_paths},
    }
    for group, input_group in (("not_affected_dispositions", "not_affected_consumers"), ("related_follow_up_dispositions", "related_out_of_scope")):
        expected[group] = {
            (source, row["impact_id"] if source == "BLIND" else _name(row["name"]))
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
                    valid_names = {_name(item["name"]) for item in sources[match["source"]].get(classifications[match["classification"]], [])}
                    if _name(match["name"]) not in valid_names:
                        raise RuntimeError("Blind impact match must reference an existing normalized ledger row")
                    contract_match = match["classification"] == "CHANGED_CONTRACT"
                    if contract_match != (group == "blind_contract_dispositions"):
                        raise RuntimeError("Blind contract/consumer match classification is invalid")
                if group == "blind_consumer_dispositions" and row["disposition"] == "COVERED_IN_SCOPE" and not any(match["classification"] == "IN_SCOPE" for match in row["matches"]):
                    raise RuntimeError("COVERED_IN_SCOPE requires an actual IN_SCOPE ledger reference")
            elif "source" in row:
                key = (row["source"], row["reference"] if row["source"] == "BLIND" else _name(row["reference"]))
            elif "path" in row:
                key = safe_impact_path(row["path"], field="changed_path_dispositions.path")
            else:
                key = _name(row["name"])
            if key in seen:
                raise RuntimeError(f"{group} contains duplicate dispositions")
            seen.add(key)
            finding_ids = row["finding_ids"]
            if len(finding_ids) != len(set(finding_ids)) or not set(finding_ids) <= final_ids:
                raise RuntimeError("Impact finding_ids must uniquely reference existing final findings")
            negative = row["disposition"] != dispositions[0]
            if negative and not finding_ids:
                raise RuntimeError("Every negative impact disposition requires a corresponding final finding")
            if negative and evaluation["status"] == EvaluatorStatus.PASS.value:
                raise RuntimeError("Evaluator PASS forbids negative impact dispositions")
            if (
                group == "blind_contract_dispositions"
                and row["disposition"] in {"MATERIAL_GAP", "MODEL_CONFLICT"}
                and evaluation["status"] != EvaluatorStatus.REPLAN_REQUIRED.value
            ):
                raise RuntimeError(f"Blind contract {row['disposition']} requires status REPLAN_REQUIRED")
        if seen != expected[group]:
            raise RuntimeError(f"{group} must disposition every and only authoritative input row")
    _exact_paths([row["path"] for row in challenge["changed_path_dispositions"]], changed_paths, workspace=workspace, field="changed_path_dispositions")


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
        raise RuntimeError("Evaluation fields invalid")
    if evaluation["protocol_version"] != EVALUATOR_PROTOCOL_VERSION:
        raise RuntimeError("Evaluator protocol mismatch")
    if evaluation["candidate_id"] != candidate_id or implementation_impact_closure["candidate_id"] != candidate_id:
        raise RuntimeError("Evaluation/implementation impact is stale for the current candidate")
    status = evaluation["status"]
    if status not in set(enum_values(EvaluatorStatus)):
        raise RuntimeError("Evaluator status invalid")
    if not isinstance(evaluation["summary"], str) or not evaluation["summary"].strip():
        raise RuntimeError("Evaluator summary must be non-empty")
    blind_ids = {str(item["finding_id"]) for item in blind_audit["findings"]}
    dispositions = evaluation["blind_finding_dispositions"]
    if not isinstance(dispositions, list):
        raise RuntimeError("blind_finding_dispositions must be a list")
    disposition_by_id: dict[str, str] = {}
    for index, row in enumerate(dispositions):
        if not isinstance(row, Mapping) or set(row) != {
            "finding_id",
            "disposition",
            "evidence",
        }:
            raise RuntimeError(f"blind_finding_dispositions[{index}] fields invalid")
        finding_id = str(row["finding_id"])
        if finding_id in disposition_by_id:
            raise RuntimeError("Blind finding dispositions must be unique")
        if row["disposition"] not in {"RETAINED", "DISMISSED_WITH_EVIDENCE"}:
            raise RuntimeError("Blind finding disposition invalid")
        evidence = row["evidence"]
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise RuntimeError("Every blind finding disposition requires evidence")
        disposition_by_id[finding_id] = str(row["disposition"])
    if set(disposition_by_id) != blind_ids:
        raise RuntimeError(
            "Phase B must disposition every and only Phase A blind finding"
        )

    findings = evaluation["findings"]
    if not isinstance(findings, list):
        raise RuntimeError("Evaluation findings must be a list")
    final_ids: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            raise RuntimeError(f"evaluation.findings[{index}] must be an object")
        finding_id = _validate_finding(finding, field=f"evaluation.findings[{index}]")
        if finding_id in final_ids:
            raise RuntimeError("Final finding ids must be unique")
        final_ids.add(finding_id)
    retained = {
        finding_id
        for finding_id, disposition in disposition_by_id.items()
        if disposition == "RETAINED"
    }
    dismissed = blind_ids - retained
    if not retained.issubset(final_ids):
        raise RuntimeError("Every retained blind finding must remain in final findings")
    if dismissed & final_ids:
        raise RuntimeError("Dismissed blind findings cannot remain in final findings")

    reason = evaluation["reason"]
    if not isinstance(reason, str):
        raise RuntimeError("Evaluator reason must be a string")
    if status == EvaluatorStatus.PASS.value:
        if findings:
            raise RuntimeError("Evaluator PASS requires no findings")
        if retained:
            raise RuntimeError("Evaluator PASS cannot retain blind findings")
    elif status == EvaluatorStatus.FINDINGS.value:
        if not findings:
            raise RuntimeError("Evaluator FINDINGS requires at least one finding")
    elif status in {
        EvaluatorStatus.REPLAN_REQUIRED.value,
        EvaluatorStatus.BLOCKED.value,
        EvaluatorStatus.NEEDS_USER_DECISION.value,
    }:
        if not reason.strip():
            raise RuntimeError(f"Evaluator {status} requires a concrete reason")
    validate_impact_challenge(
        evaluation, blind_audit=blind_audit, workspace=workspace,
        planner_impact_closure=planner_impact_closure,
        implementation_impact_closure=implementation_impact_closure, changed_paths=changed_paths,
    )


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
    runtime_probe_guidance: list[str] | None = None,
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
    timeout: float = 900,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not callable(on_blind_audit) or not callable(on_phase_complete):
        raise RuntimeError("Evaluator requires immutable blind persistence and current-candidate guards")
    thread_id = codex.start_thread(
        cwd=workspace,
        execution_role=ExecutionRole.EVALUATOR,
        developer_instructions=EVALUATOR_INSTRUCTIONS,
        on_started=on_thread_started,
    )
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
    raw_audit = codex.run_turn(
        thread_id=thread_id,
        prompt=phase_a_prompt,
        output_schema=BLIND_AUDIT_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    on_phase_complete("PHASE_A")
    blind_audit = _parse_json(raw_audit, label="Evaluator Phase A")
    validate_blind_audit(blind_audit, workspace=workspace, candidate_id=candidate_id, changed_paths=changed_paths, owner_allowed_paths=owner_allowed_paths)
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

    phase_b_prompt = f"""
PHASE B — INDEPENDENT IMPACT CHALLENGE.

Phase A blind audit уже зафиксирован и не может быть забыт:
{json.dumps(blind_audit, ensure_ascii=False, indent=2)}

Теперь раскрыты только Controller-normalized artifacts; Planner reasoning и Implementer prose
по-прежнему скрыты.

IMPLEMENTATION CONTRACT:
{json.dumps(implementation_contract, ensure_ascii=False, indent=2)}

NORMALIZED PLANNER IMPACT CLOSURE:
{json.dumps(planner_impact, ensure_ascii=False, indent=2)}

CONTROLLER-NORMALIZED IMPLEMENTATION IMPACT CLOSURE:
{json.dumps(implementation_impact_closure, ensure_ascii=False, indent=2)}

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
Negative dispositions требуют final finding_ids и запрещают PASS. Новые findings разрешены.
""".strip()
    raw_verdict = codex.run_turn(
        thread_id=thread_id,
        prompt=phase_b_prompt,
        output_schema=EVALUATOR_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    on_phase_complete("PHASE_B")
    verdict = _parse_json(raw_verdict, label="Evaluator Phase B")
    validate_evaluation_artifact(
        verdict, blind_audit=blind_audit, workspace=workspace, candidate_id=candidate_id,
        changed_paths=changed_paths, planner_impact_closure=planner_impact,
        implementation_impact_closure=implementation_impact_closure, owner_allowed_paths=owner_allowed_paths,
    )
    return blind_audit, verdict
