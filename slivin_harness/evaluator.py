from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from slivin_harness.app_server import CodexAppServer


EVALUATOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "PASS",
                "FINDINGS",
                "REPLAN_REQUIRED",
                "BLOCKED",
                "NEEDS_USER_DECISION",
            ],
        },
        "summary": {"type": "string"},
        "changed_contract": {"type": "string"},
        "planner_assumption_audit": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["CONFIRMED", "REJECTED", "UNVERIFIED"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": ["id", "status", "evidence"],
            },
        },
        "obligation_assessment": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["PASS", "FAIL", "UNVERIFIED"],
                    },
                    "evidence_type": {
                        "type": "string",
                        "enum": [
                            "test",
                            "runtime",
                            "code_trace",
                            "static",
                            "not_available",
                        ],
                    },
                    "evidence": {"type": "string"},
                },
                "required": [
                    "id",
                    "status",
                    "evidence_type",
                    "evidence",
                ],
            },
        },
        "shared_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "component": {"type": "string"},
                    "consumers_checked": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "risk": {"type": "string"},
                },
                "required": [
                    "component",
                    "consumers_checked",
                    "risk",
                ],
            },
        },
        "plan_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["BLOCKER", "HIGH", "MEDIUM", "LOW"],
                    },
                    "evidence": {"type": "string"},
                    "impact": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": [
                    "id",
                    "severity",
                    "evidence",
                    "impact",
                    "recommendation",
                ],
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["BLOCKER", "HIGH", "MEDIUM", "LOW"],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                    "evidence": {"type": "string"},
                    "scenario": {"type": "string"},
                    "impact": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": [
                    "id",
                    "severity",
                    "confidence",
                    "evidence",
                    "scenario",
                    "impact",
                    "recommendation",
                ],
            },
        },
        "unverified_risks": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "status",
        "summary",
        "changed_contract",
        "planner_assumption_audit",
        "obligation_assessment",
        "shared_changes",
        "plan_findings",
        "findings",
        "unverified_risks",
    ],
}


EVALUATOR_INSTRUCTIONS = """
Ты независимый Fresh Evaluator внутри Slivin Harness.

Работай строго read-only. Ты НЕ implementer и не защищаешь его решение.

1. Перепроверь observable contract, final diff, surrounding code/tests и A-*.

2. OBLIGATION LEDGER.
   Верни ровно по одной assessment для каждого release obligation.

3. LIFE-* AUDIT.
   Независимо перепроверь role/scope/lifecycle каждого state mechanism:
   USER_INTENT, ACTION_LOCAL, DERIVED, CACHE, PERSISTED_SOURCE,
   EXTERNAL_SOURCE, LEGACY_COMPAT, UNKNOWN.

   Проверь:
   - кто создаёт state;
   - для какого scope/action instance;
   - когда он становится valid/invalid;
   - frozen ли target после action start;
   - какую authority domain он реально имеет;
   - не продолжает ли transient/action-local state влиять за пределами своей domain.

4. LIFECYCLE AUTHORITY.
   Для НОВОГО действия USER_INTENT обычно определяет target в своей domain.
   ACTION_LOCAL state authoritative только для action instance, который его создал.
   Frozen in-flight target не должен молча ретаргетиться новым global intent.
   DERIVED/CACHE не переопределяет source; stale state не authoritative.
   PERSISTED/EXTERNAL source может иметь более высокий domain authority по contract.

   Если Planner эскалировал NEEDS_USER_DECISION, проверь, действительно ли конфликт
   остался между peer states после lifecycle/ownership анализа. Implementation-only
   ambiguity не является product decision.

5. REP-*.
   Проверь downstream local readers representations, не только backend endpoint:
   stage/eligibility/permission/visibility/count/summary/payload/routing/readback.

6. AUTH-*.
   Проверь reachable combinations и consistency across surfaces.
   UI state A + executable payload B без подтверждённого contract → material finding.

7. CHANGE-SURFACE INTEGRITY.
   Независимо сравни final changed paths с `plan.candidate_paths`. Каждый реально
   изменённый non-ignored path должен быть в planned surface. Для поздно добавленного
   path baseline snapshot должен честно содержать `captured_before_path_edit=true`;
   не принимай candidate-state-after-edit за pre-edit evidence. Несогласованность
   planned vs actual surface → REPLAN_REQUIRED/BLOCKED в зависимости от ownership.

8. TEST VALIDITY.
   Regression evidence должен различать broken contract и candidate.

9. CONS-* / INT-* / PRES-*.
   Проверяй каждого consumer и preservation independently.

10. ROUTING.
   FINDINGS — candidate defect.
   REPLAN_REQUIRED — plan пропустил/неверно классифицировал material LIFE/REP/AUTH.
   BLOCKED — нет mandatory technical capability/evidence.
   NEEDS_USER_DECISION — только реальная unresolved product semantics между peer states.

11. PASS допустим только если:
    - blocking findings отсутствуют;
    - все release obligations PASS;
    - narrowing assumptions подтверждены;
    - LIFE/REP/AUTH не имеют material gaps;
    - нет release-blocking unverified risks.

Верни только JSON по output schema.
""".strip()


def run_evaluator(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    plan: dict,
    checks_summary: str,
    required_obligation_ids: list[str],
    preflight: dict | None = None,
    baseline_snapshot: dict | None = None,
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
) -> dict:
    thread_id = codex.start_thread(
        cwd=workspace,
        sandbox="read-only",
        developer_instructions=EVALUATOR_INSTRUCTIONS,
        on_started=on_thread_started,
    )

    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
    obligation_json = json.dumps(required_obligation_ids, ensure_ascii=False, indent=2)
    preflight_json = json.dumps(preflight or {}, ensure_ascii=False, indent=2)
    baseline_snapshot_json = json.dumps(
        baseline_snapshot or {},
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""
Исходная задача пользователя:

--- BEGIN TASK ---
{task_prompt}
--- END TASK ---

Trusted Harness preflight, зафиксированный ДО первой production edit:

--- BEGIN PREFLIGHT ---
{preflight_json}
--- END PREFLIGHT ---

Pre-edit filesystem evidence, снятое Harness ПОСЛЕ planning и ДО implementation:

--- BEGIN BASELINE SNAPSHOT ---
{baseline_snapshot_json}
--- END BASELINE SNAPSHOT ---

Planning/characterization artifact:

--- BEGIN PLAN ---
{plan_json}
--- END PLAN ---

Обязательные obligation IDs для evidence ledger:

--- BEGIN REQUIRED OBLIGATIONS ---
{obligation_json}
--- END REQUIRED OBLIGATIONS ---

Deterministic checks, выполненные внешним Harness:

--- BEGIN CHECKS ---
{checks_summary}
--- END CHECKS ---

Это свежий независимый review финального candidate.

Самостоятельно изучи git diff, changed files, relevant consumers, existing tests и
surrounding runtime code. Не доверяй объяснению implementer.

Построй полный obligation ledger и попытайся опровергнуть решение.
Код не изменяй.
""".strip()

    raw = codex.run_turn(
        thread_id=thread_id,
        prompt=prompt,
        output_schema=EVALUATOR_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
    )

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Evaluator returned invalid structured output.\n"
            f"Raw final response:\n{raw}"
        ) from exc
