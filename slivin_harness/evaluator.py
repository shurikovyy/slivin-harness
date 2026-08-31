from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from slivin_harness.app_server import CodexAppServer
from slivin_harness.protocol import EVALUATOR_PROTOCOL_VERSION
from slivin_harness.workflow import EvaluatorStatus, enum_values

EVALUATOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {
            "type": "string",
            "enum": [EVALUATOR_PROTOCOL_VERSION],
        },
        "status": {
            "type": "string",
            "enum": enum_values(EvaluatorStatus),
        },
        "summary": {"type": "string"},
        "task_satisfied": {"type": "boolean"},
        "changed_files_reviewed": {"type": "array", "items": {"type": "string"}},
        "checks_assessment": {"type": "array", "items": {"type": "string"}},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                    "title": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "required_action": {"type": "string"},
                },
                "required": ["severity", "title", "evidence", "required_action"],
            },
        },
        "unverified": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "reason": {"type": "string"},
                    "required_evidence": {"type": "string"},
                },
                "required": ["claim", "reason", "required_evidence"],
            },
        },
        "replan_reason": {"type": "string"},
    },
    "required": [
        "protocol_version",
        "status",
        "summary",
        "task_satisfied",
        "changed_files_reviewed",
        "checks_assessment",
        "findings",
        "unverified",
        "replan_reason",
    ],
}

EVALUATOR_INSTRUCTIONS = """
Ты независимый read-only Evaluator внутри Slivin Harness.

Не защищай решение Implementer и не опирайся на Planner framing: Planner artifact тебе
не передаётся. Самостоятельно прочитай исходную задачу, актуальную документацию,
финальный diff, изменённые файлы, соседних consumers и результаты checks.

Пытайся опровергнуть решение:
- действительно ли задача решена наблюдаемым поведением;
- не расходятся ли visibility/count/payload/authority или другие representations;
- для stateful scope/selection проверить достижимое сосуществование representations и границы empty/zero/all-excluded; отдельно различать authority нового действия и frozen target уже начатого action;
- не пропущен ли достижимый sibling consumer; для shared state/representation проверить его локальные eligibility/stage/permission guards, а не только совместимость transport;
- сохранено ли явно требуемое старое поведение и не ретаргетится ли уже начатое stateful action поздним global state;
- тесты проверяют контракт, а не только конкретный patch;
- нет ли release-critical утверждения без evidence.

PASS разрешён только когда нет findings и unverified. Код не меняй.
""".strip()


def run_evaluator(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    preflight: dict,
    changed_paths: list[str],
    diff_text: str,
    checks_summary: str,
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
    timeout: float = 900,
) -> dict:
    thread_id = codex.start_thread(
        cwd=workspace,
        sandbox="read-only",
        developer_instructions=EVALUATOR_INSTRUCTIONS,
        on_started=on_thread_started,
    )
    prompt = f"""
Исходная задача пользователя:

--- BEGIN TASK ---
{task_prompt}
--- END TASK ---

Trusted preflight до изменений:
{json.dumps(preflight, ensure_ascii=False, indent=2)}

Фактически изменённые paths:
{json.dumps(changed_paths, ensure_ascii=False, indent=2)}

Финальный diff:
--- BEGIN DIFF ---
{diff_text}
--- END DIFF ---

Deterministic checks внешнего Controller:
--- BEGIN CHECKS ---
{checks_summary}
--- END CHECKS ---

Проведи blind-first review текущего candidate и верни structured verdict.
""".strip()
    raw = codex.run_turn(
        thread_id=thread_id,
        prompt=prompt,
        output_schema=EVALUATOR_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Evaluator returned invalid JSON structured output.\n" + raw
        ) from exc
