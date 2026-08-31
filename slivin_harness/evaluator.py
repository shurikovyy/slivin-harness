from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from slivin_harness.app_server import CodexAppServer
from slivin_harness.phase6 import BLIND_AUDIT_VERSION
from slivin_harness.protocol import EVALUATOR_PROTOCOL_VERSION
from slivin_harness.verification import PROOF_TARGET_SCHEMA, validate_proof_target
from slivin_harness.workflow import EvaluatorStatus, enum_values

_FINDING_CATEGORIES = ["DEFECT", "CONSUMER", "RISK", "EVIDENCE", "DOCS"]
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

BLIND_AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {"type": "string", "enum": [BLIND_AUDIT_VERSION]},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": _FINDING_SCHEMA},
        "advisories": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["protocol_version", "summary", "findings", "advisories"],
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
        "summary": {"type": "string"},
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
        "summary",
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
- самостоятельно исследуй repository и candidate через git diff/status/grep, callers,
  readers, writers, tests и canonical docs;
- ищи material defects, пропущенных reachable consumers, нарушение preservation,
  несогласованные representations/authority/lifecycle, false-green tests и docs drift;
- state/boundary проверки применяй условно и только к реально достижимым состояниям;
- finding допустим только с конкретным failure mode, reachability evidence и required action;
- blocking severity только HIGH или MEDIUM; вкусовые замечания — advisory, не finding.

PHASE B — contract audit:
- после фиксации blind audit получаешь active Implementation Contract, Verification Plan,
  Controller-normalized Contract Closure Record, deterministic evidence и runtime evidence;
- не получаешь Planner reasoning или Implementer prose;
- проверь качество доказательств и false-green risk;
- каждый blind finding обязан быть RETAINED либо DISMISSED_WITH_EVIDENCE;
- blind finding нельзя забыть только потому, что его нет в Contract.

Код, tests и docs не меняй. PASS разрешён только без material findings.
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


def validate_blind_audit(audit: Mapping[str, Any]) -> None:
    if set(audit) != {"protocol_version", "summary", "findings", "advisories"}:
        raise RuntimeError("Blind audit fields invalid")
    if audit["protocol_version"] != BLIND_AUDIT_VERSION:
        raise RuntimeError("Blind audit protocol mismatch")
    if not isinstance(audit["summary"], str) or not audit["summary"].strip():
        raise RuntimeError("Blind audit summary must be non-empty")
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


def validate_evaluation_artifact(
    evaluation: Mapping[str, Any], *, blind_audit: Mapping[str, Any]
) -> None:
    expected_fields = {
        "protocol_version",
        "status",
        "summary",
        "blind_finding_dispositions",
        "findings",
        "reason",
    }
    if set(evaluation) != expected_fields:
        raise RuntimeError("Evaluation fields invalid")
    if evaluation["protocol_version"] != EVALUATOR_PROTOCOL_VERSION:
        raise RuntimeError("Evaluator protocol mismatch")
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
    runtime_probe_guidance: list[str] | None = None,
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
    on_blind_audit: Callable[[dict[str, Any]], None] | None = None,
    on_phase_complete: Callable[[str], None] | None = None,
    timeout: float = 900,
) -> tuple[dict[str, Any], dict[str, Any]]:
    thread_id = codex.start_thread(
        cwd=workspace,
        sandbox="read-only",
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
Не вставляй весь diff в ответ. Верни immutable structured blind audit.
""".strip()
    raw_audit = codex.run_turn(
        thread_id=thread_id,
        prompt=phase_a_prompt,
        output_schema=BLIND_AUDIT_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    blind_audit = _parse_json(raw_audit, label="Evaluator Phase A")
    validate_blind_audit(blind_audit)
    # The Controller persists the blind artifact before any Contract/check framing
    # is disclosed to the same evaluator thread.
    if on_blind_audit is not None:
        on_blind_audit(dict(blind_audit))
    if on_phase_complete is not None:
        on_phase_complete("PHASE_A")

    phase_b_prompt = f"""
PHASE B — CONTRACT AUDIT.

Phase A blind audit уже зафиксирован и не может быть забыт:
{json.dumps(blind_audit, ensure_ascii=False, indent=2)}

Теперь раскрыты только Controller-normalized artifacts; Planner reasoning и Implementer prose
по-прежнему скрыты.

IMPLEMENTATION CONTRACT:
{json.dumps(implementation_contract, ensure_ascii=False, indent=2)}

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

Проверь известные obligations и качество evidence. Для каждого blind finding верни
RETAINED либо DISMISSED_WITH_EVIDENCE. Новые material findings разрешены.
""".strip()
    raw_verdict = codex.run_turn(
        thread_id=thread_id,
        prompt=phase_b_prompt,
        output_schema=EVALUATOR_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    verdict = _parse_json(raw_verdict, label="Evaluator Phase B")
    validate_evaluation_artifact(verdict, blind_audit=blind_audit)
    if on_phase_complete is not None:
        on_phase_complete("PHASE_B")
    return blind_audit, verdict
