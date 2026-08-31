from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from slivin_harness.app_server import CodexAppServer
from slivin_harness.protocol import PLANNER_PROTOCOL_VERSION
from slivin_harness.workflow import PlannerStatus, enum_values

PLANNER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {
            "type": "string",
            "enum": [PLANNER_PROTOCOL_VERSION],
        },
        "status": {
            "type": "string",
            "enum": enum_values(PlannerStatus),
        },
        "summary": {"type": "string"},
        "observed_behavior": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "expected_behavior": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
        "root_cause": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "claim": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "confidence": {
                    "type": "string",
                    "enum": ["HIGH", "MEDIUM", "LOW"],
                },
            },
            "required": ["claim", "evidence", "confidence"],
        },
        "change_plan": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "preserve": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "consumers_to_check": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "risks": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "test_plan": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "documentation": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "required": {"type": "boolean"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["required", "paths", "reason"],
        },
        "likely_paths": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
        "unknowns": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
    },
    "required": [
        "protocol_version",
        "status",
        "summary",
        "observed_behavior",
        "expected_behavior",
        "root_cause",
        "change_plan",
        "preserve",
        "consumers_to_check",
        "risks",
        "test_plan",
        "documentation",
        "likely_paths",
        "unknowns",
    ],
}

PLANNER_INSTRUCTIONS = """
Ты read-only Planner внутри Slivin Harness.

Твоя задача — коротко понять задачу до изменения кода:
- прочитать repository instructions и актуальную документацию;
- подтвердить фактическое поведение по коду, тестам или воспроизведению;
- отделить доказанный root cause от предположений;
- найти общие компоненты и соседних consumers, которые реально достижимы; для shared state/representation проследить consumers до локальных eligibility/stage/permission guards, а не только до endpoint/payload;
- назвать, что должно сохраниться, включая target уже начатого stateful action при последующей смене global state;
- для state/count/token/selection логики проверить только достижимые boundary/coexistence случаи: empty/zero/all-excluded, stale и конкурирующие current/resident states; важные случаи включить в risks/test_plan;
- consumers_to_check держи коротким: максимум 8 materially distinct consumer families, однотипные readers объединяй;
- предложить небольшой test plan; остановись, когда evidence достаточно для безопасной реализации, не создавай repository-wide prose audit;
- определить, нужна ли синхронизация документации.

Не пиши готовый patch и не диктуй конкретные строки реализации. Не используй другие
копии проекта, старые Planner reports, reference patches или hidden checks. Код не меняй.
`unknowns` — это честный список оставшихся неопределённостей. READY может содержать
non-blocking unknowns, если они не мешают безопасно выбрать change semantics и проверить
результат. Если unknown требует решения пользователя и меняет product semantics — верни
NEEDS_USER_DECISION. Если без него нельзя получить обязательное evidence или безопасно
продолжать — BLOCKED.
""".strip()


def run_planner(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    preflight: dict,
    replan_context: str = "",
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
    timeout: float = 900,
) -> dict:
    thread_id = codex.start_thread(
        cwd=workspace,
        sandbox="read-only",
        developer_instructions=PLANNER_INSTRUCTIONS,
        on_started=on_thread_started,
    )
    prompt = f"""
Исходная задача:

--- BEGIN TASK ---
{task_prompt}
--- END TASK ---

Trusted preflight до изменений:
{json.dumps(preflight, ensure_ascii=False, indent=2)}

{replan_context}

Исследуй текущий repository независимо и верни компактный structured plan.
""".strip()
    raw = codex.run_turn(
        thread_id=thread_id,
        prompt=prompt,
        output_schema=PLANNER_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
        timeout=timeout,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Planner returned invalid JSON structured output.\n" + raw
        ) from exc
