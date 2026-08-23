from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from slivin_harness.app_server import CodexAppServer
from slivin_harness.protocol import IMPACT_PROTOCOL_VERSION, impact_schema_for_plan


IMPACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol_version": {
            "type": "string",
            "enum": [IMPACT_PROTOCOL_VERSION],
        },
        "plan_fingerprint": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["COMPLETE", "BLOCKED"],
        },
        "summary": {"type": "string"},
        "shared_change_detected": {"type": "boolean"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "component": {"type": "string"},
                    "consumer": {"type": "string"},
                    "reader_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "surfaces": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "visibility",
                                "count",
                                "summary",
                                "eligibility",
                                "payload",
                                "routing",
                                "mutation_target",
                                "readback_cleanup",
                                "other",
                            ],
                        },
                    },
                    "lifecycle_reachability": {"type": "string"},
                    "observed_reader_contract": {"type": "string"},
                    "required_contract": {"type": "string"},
                    "disposition": {
                        "type": "string",
                        "enum": [
                            "COMPATIBLE",
                            "VERIFY_REQUIRED",
                            "CHANGE_REQUIRED",
                            "OUT_OF_SCOPE",
                        ],
                    },
                    "required_candidate_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "verification_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                },
                "required": [
                    "id",
                    "component",
                    "consumer",
                    "reader_paths",
                    "surfaces",
                    "lifecycle_reachability",
                    "observed_reader_contract",
                    "required_contract",
                    "disposition",
                    "required_candidate_paths",
                    "verification_paths",
                    "evidence",
                    "confidence",
                ],
            },
        },
        "completeness_evidence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "unresolved": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "protocol_version",
        "plan_fingerprint",
        "status",
        "summary",
        "shared_change_detected",
        "items",
        "completeness_evidence",
        "unresolved",
    ],
}


IMPACT_INSTRUCTIONS = r"""
Ты Fresh Shared Impact Auditor внутри Slivin Harness.

Работай строго read-only. Ты НЕ Planner, НЕ Implementer и НЕ общий code reviewer.
Твоя единственная задача — независимо доказать полноту sibling-consumer impact
для ФАКТИЧЕСКОГО candidate diff перед Fresh Evaluator.

1. НЕ доверяй списку `affected_consumers` Planner как полному. Используй его только
   как гипотезу. Самостоятельно исследуй repository-wide consumers изменённых shared
   abstractions, state carriers, exported helpers, config opt-ins и representation
   semantics.

2. Начинай с actual changed paths и diff. Для каждого shared behavior change найди:
   - direct callers/importers/readers;
   - sibling table/config/module consumers;
   - custom handlers, stage/eligibility guards, inline editors;
   - backend readers, если frontend representation влияет на executable target.

3. Особенно проверяй representation mismatch. Если shared logic начинает считать
   новое representation authoritative (например token scope вместо materialized IDs),
   найди local readers, которые всё ещё понимают только старое representation.
   Проверь все surfaces: visibility, count, summary, eligibility, payload, routing,
   mutation target, readback/cleanup.

4. REACHABILITY обязательна. Не объявляй defect только потому, что поля можно
   синтетически поставить одновременно. `lifecycle_reachability` должен объяснять
   реальный writer/action path, по которому consumer достигает состояния.

5. `disposition`:
   - COMPATIBLE: consumer доказан совместимым с candidate semantics;
   - VERIFY_REQUIRED: production code совместим по trace, но release требует
     targeted verification/evidence;
   - CHANGE_REQUIRED: текущий local reader несовместим и должен быть изменён до release;
   - OUT_OF_SCOPE: только если consumer найден, но доказанно не затронут contract.

6. Для CHANGE_REQUIRED верни exact repo-relative `required_candidate_paths`.
   Включай production path и test path, если test реально требуется для безопасного
   исправления. Не пиши prose вместо path. Не требуй конкретную reference-реализацию.

7. Для каждого item дай concrete source evidence. Общая фраза «проверены consumers»
   недостаточна.

8. `completeness_evidence` должно перечислить repository-wide search/trace evidence,
   показывающее, как искались sibling consumers. Не ограничивайся changed directory.

9. Если mandatory repository inspection объективно невозможен, status=BLOCKED.
   Не используй BLOCKED просто потому, что не удалось быстро доказать совместимость.

10. Не меняй код и не используй hidden grader как источник known-answer.

Верни только JSON по output schema.
""".strip()


def run_impact_auditor(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    plan: dict,
    changed_paths: list[str],
    checks_summary: str,
    preflight: dict | None = None,
    baseline_snapshot: dict | None = None,
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
) -> dict:
    thread_id = codex.start_thread(
        cwd=workspace,
        sandbox="read-only",
        developer_instructions=IMPACT_INSTRUCTIONS,
        on_started=on_thread_started,
    )

    prompt = f"""
Исходная задача пользователя:

--- BEGIN TASK ---
{task_prompt}
--- END TASK ---

Trusted preflight:

--- BEGIN PREFLIGHT ---
{json.dumps(preflight or {}, ensure_ascii=False, indent=2)}
--- END PREFLIGHT ---

Controller-approved Planner artifact:

--- BEGIN PLAN ---
{json.dumps(plan, ensure_ascii=False, indent=2)}
--- END PLAN ---

Actual changed paths after deterministic checks:

--- BEGIN CHANGED PATHS ---
{json.dumps(changed_paths, ensure_ascii=False, indent=2)}
--- END CHANGED PATHS ---

Path-local baseline evidence:

--- BEGIN BASELINE SNAPSHOT ---
{json.dumps(baseline_snapshot or {}, ensure_ascii=False, indent=2)}
--- END BASELINE SNAPSHOT ---

Deterministic checks already run by Controller:

--- BEGIN CHECKS ---
{checks_summary}
--- END CHECKS ---

Independently inventory sibling consumers of the ACTUAL shared change. Do not merely
repeat Planner. Trace repository-wide readers and return one IMP-* item per material
consumer/consumer family whose compatibility must be proven before release.
""".strip()

    raw = codex.run_turn(
        thread_id=thread_id,
        prompt=prompt,
        output_schema=impact_schema_for_plan(IMPACT_SCHEMA, plan),
        skills=explicit_skills or [],
        on_heartbeat=on_heartbeat,
    )

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Impact Auditor returned invalid structured output.\n"
            f"Raw final response:\n{raw}"
        ) from exc
