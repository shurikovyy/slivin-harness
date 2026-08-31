from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from slivin_harness.app_server import CodexAppServer
from slivin_harness.protocol import ArtifactContractError, ensure_exact_keys, require_type, stable_fingerprint
from slivin_harness.workflow import TaskContractStatus, enum_values

TASK_CONTRACT_VERSION = "task-contract.v1"

_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim": {"type": "string"},
        "source_text": {"type": "string"},
    },
    "required": ["claim", "source_text"],
}

TASK_CONTRACT_NORMALIZER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {"type": "string", "enum": [TASK_CONTRACT_VERSION]},
        "status": {"type": "string", "enum": enum_values(TaskContractStatus)},
        "summary": {"type": "string"},
        "explicit_intent": {"type": "array", "maxItems": 6, "items": _CLAIM_SCHEMA},
        "explicit_acceptance": {"type": "array", "maxItems": 8, "items": _CLAIM_SCHEMA},
        "explicit_preservation": {"type": "array", "maxItems": 8, "items": _CLAIM_SCHEMA},
        "explicit_forbidden": {"type": "array", "maxItems": 8, "items": _CLAIM_SCHEMA},
        "owner_boundaries": {"type": "array", "maxItems": 8, "items": _CLAIM_SCHEMA},
        "non_goals": {"type": "array", "maxItems": 8, "items": _CLAIM_SCHEMA},
        "ambiguities": {"type": "array", "maxItems": 6, "items": _CLAIM_SCHEMA},
        "reason": {"type": "string"},
    },
    "required": [
        "protocol_version",
        "status",
        "summary",
        "explicit_intent",
        "explicit_acceptance",
        "explicit_preservation",
        "explicit_forbidden",
        "owner_boundaries",
        "non_goals",
        "ambiguities",
        "reason",
    ],
}

TASK_CONTRACT_NORMALIZER_INSTRUCTIONS = """
Ты узкий Intake Normalizer внутри Slivin Harness.

Ты видишь только исходный запрос пользователя. Не исследуй repository, не ищи решение и не
добавляй технические требования от себя. Извлеки только то, что пользователь сказал явно:
intent, acceptance, preservation, forbidden, owner boundaries и non-goals.

Для каждого claim приведи source_text — точный непрерывный фрагмент исходного запроса.
Можно исправить язык claim, но source_text нельзя перефразировать. Имена функций, пути,
команды, ID и значения в кавычках сохраняй буквально.

READY требует хотя бы один explicit_intent и один explicit_acceptance. Acceptance может
повторять intent, если короткий запрос сам однозначно описывает желаемый результат.
NEEDS_USER_DECISION используй только при прямом внутреннем противоречии исходного запроса.
Не объявляй неоднозначностью вопрос, который может разрешить Planner по repository.
""".strip()

_FIELDS = (
    "explicit_intent",
    "explicit_acceptance",
    "explicit_preservation",
    "explicit_forbidden",
    "owner_boundaries",
    "non_goals",
    "ambiguities",
)


def _validate_claim_rows(rows: object, *, field: str, raw_request: str) -> list[dict[str, str]]:
    require_type(rows, list, field=field)
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        require_type(row, dict, field=f"{field}[{index}]")
        ensure_exact_keys(
            row,
            allowed={"claim", "source_text"},
            required={"claim", "source_text"},
            field=f"{field}[{index}]",
        )
        claim = row["claim"]
        source = row["source_text"]
        require_type(claim, str, field=f"{field}[{index}].claim")
        require_type(source, str, field=f"{field}[{index}].source_text")
        if not claim.strip() or not source:
            raise ArtifactContractError(
                code="EMPTY_TASK_CONTRACT_CLAIM",
                field=f"{field}[{index}]",
                message="Task Contract claims and source_text must be non-empty",
                expected="Non-empty claim with exact source fragment",
                actual=row,
            )
        if source not in raw_request:
            raise ArtifactContractError(
                code="TASK_CONTRACT_SOURCE_MISMATCH",
                field=f"{field}[{index}].source_text",
                message="Task Contract source_text is not an exact fragment of the raw request",
                expected="Exact contiguous substring of RAW USER REQUEST",
                actual=source,
            )
        key = (claim.strip(), source)
        if key in seen:
            raise ArtifactContractError(
                code="DUPLICATE_TASK_CONTRACT_CLAIM",
                field=f"{field}[{index}]",
                message="Duplicate Task Contract claim",
                expected="Unique explicit claims",
                actual=row,
            )
        seen.add(key)
        result.append({"claim": claim.strip(), "source_text": source})
    return result


def build_task_contract(*, raw_request: str, normalized: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "protocol_version",
        "status",
        "summary",
        *_FIELDS,
        "reason",
    }
    ensure_exact_keys(normalized, allowed=allowed, required=allowed, field="task_contract_normalized")
    if normalized["protocol_version"] != TASK_CONTRACT_VERSION:
        raise ArtifactContractError(
            code="TASK_CONTRACT_VERSION",
            field="protocol_version",
            message="Task Contract protocol mismatch",
            expected=TASK_CONTRACT_VERSION,
            actual=normalized["protocol_version"],
        )
    if normalized["status"] not in set(enum_values(TaskContractStatus)):
        raise ArtifactContractError(
            code="TASK_CONTRACT_STATUS",
            field="status",
            message="Unknown Task Contract status",
            expected="/".join(enum_values(TaskContractStatus)),
            actual=normalized["status"],
        )
    require_type(normalized["summary"], str, field="summary")
    require_type(normalized["reason"], str, field="reason")
    claims = {
        field: _validate_claim_rows(normalized[field], field=field, raw_request=raw_request)
        for field in _FIELDS
    }
    if normalized["status"] == TaskContractStatus.READY.value:
        if not claims["explicit_intent"] or not claims["explicit_acceptance"]:
            raise ArtifactContractError(
                code="TASK_CONTRACT_INCOMPLETE",
                field="explicit_intent/explicit_acceptance",
                message="READY Task Contract requires intent and acceptance",
                expected="At least one explicit intent and acceptance claim",
                actual={
                    "explicit_intent": claims["explicit_intent"],
                    "explicit_acceptance": claims["explicit_acceptance"],
                },
            )
        if claims["ambiguities"] or normalized["reason"].strip():
            raise ArtifactContractError(
                code="TASK_CONTRACT_READY_WITH_AMBIGUITY",
                field="ambiguities/reason",
                message="READY Task Contract cannot retain a direct contradiction",
                expected="Empty ambiguities and reason",
                actual={"ambiguities": claims["ambiguities"], "reason": normalized["reason"]},
            )
    else:
        if not claims["ambiguities"] or not normalized["reason"].strip():
            raise ArtifactContractError(
                code="TASK_CONTRACT_DECISION_WITHOUT_REASON",
                field="ambiguities/reason",
                message="NEEDS_USER_DECISION requires a direct contradiction and reason",
                expected="Non-empty ambiguities and reason",
                actual={"ambiguities": claims["ambiguities"], "reason": normalized["reason"]},
            )

    payload: dict[str, Any] = {
        "protocol_version": TASK_CONTRACT_VERSION,
        "status": normalized["status"],
        "summary": normalized["summary"].strip(),
        "raw_user_request": raw_request,
        "raw_request_sha256": hashlib.sha256(raw_request.encode("utf-8")).hexdigest(),
        **claims,
        "reason": normalized["reason"].strip(),
    }
    payload["fingerprint"] = stable_fingerprint(payload)
    return payload


def validate_task_contract(contract: dict[str, Any]) -> None:
    allowed = {
        "protocol_version",
        "status",
        "summary",
        "raw_user_request",
        "raw_request_sha256",
        *_FIELDS,
        "reason",
        "fingerprint",
    }
    ensure_exact_keys(contract, allowed=allowed, required=allowed, field="task_contract")
    raw = contract["raw_user_request"]
    require_type(raw, str, field="raw_user_request")
    normalized = {key: contract[key] for key in allowed if key not in {"raw_user_request", "raw_request_sha256", "fingerprint"}}
    rebuilt = build_task_contract(raw_request=raw, normalized=normalized)
    if contract["raw_request_sha256"] != rebuilt["raw_request_sha256"]:
        raise ArtifactContractError(
            code="TASK_CONTRACT_RAW_HASH",
            field="raw_request_sha256",
            message="Raw request hash mismatch",
            expected=rebuilt["raw_request_sha256"],
            actual=contract["raw_request_sha256"],
        )
    if contract["fingerprint"] != rebuilt["fingerprint"]:
        raise ArtifactContractError(
            code="TASK_CONTRACT_FINGERPRINT",
            field="fingerprint",
            message="Task Contract fingerprint mismatch",
            expected=rebuilt["fingerprint"],
            actual=contract["fingerprint"],
        )


def run_task_contract_normalizer(
    codex: CodexAppServer,
    *,
    cwd: Path,
    raw_request: str,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
    timeout: float = 300,
) -> dict[str, Any]:
    thread_id = codex.start_thread(
        cwd=cwd,
        sandbox="read-only",
        developer_instructions=TASK_CONTRACT_NORMALIZER_INSTRUCTIONS,
        on_started=on_thread_started,
    )
    prompt = f"""
Извлеки только явный пользовательский контракт из текста ниже.

--- BEGIN RAW USER REQUEST ---
{raw_request}
--- END RAW USER REQUEST ---

Не используй repository context и не предлагай решение.
""".strip()
    raw = codex.run_turn(
        thread_id=thread_id,
        prompt=prompt,
        output_schema=TASK_CONTRACT_NORMALIZER_SCHEMA,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    try:
        normalized = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Task Contract normalizer returned invalid JSON.\n" + raw) from exc
    contract = build_task_contract(raw_request=raw_request, normalized=normalized)
    validate_task_contract(contract)
    return contract
