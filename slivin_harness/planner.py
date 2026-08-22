from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from slivin_harness.app_server import CodexAppServer


PLANNER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "READY",
                "BLOCKED",
                "NEEDS_USER_DECISION",
            ],
        },
        "summary": {"type": "string"},
        "reproduction": {
            "type": "array",
            "items": {"type": "string"},
        },
        "relevant_state": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "meaning": {"type": "string"},
                    "writers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "readers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "name",
                    "meaning",
                    "writers",
                    "readers",
                ],
            },
        },
        "current_contract": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "state": {"type": "string"},
                    "behavior": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "source": {
                        "type": "string",
                        "enum": [
                            "user_requirement",
                            "test",
                            "code",
                            "documentation",
                            "observed_runtime",
                            "mixed",
                        ],
                    },
                    "compatibility_notes": {"type": "string"},
                },
                "required": [
                    "id",
                    "state",
                    "behavior",
                    "evidence",
                    "source",
                    "compatibility_notes",
                ],
            },
        },
        "assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "claim": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                    "narrows_existing_behavior": {"type": "boolean"},
                },
                "required": [
                    "id",
                    "claim",
                    "evidence",
                    "confidence",
                    "narrows_existing_behavior",
                ],
            },
        },
        "root_cause": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "hypothesis": {"type": "string"},
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
                "hypothesis",
                "evidence",
                "confidence",
            ],
        },
        "local_owner": {"type": "string"},
        "shared_components": {
            "type": "array",
            "items": {"type": "string"},
        },
        "affected_consumers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "consumer": {"type": "string"},
                    "why_affected": {"type": "string"},
                },
                "required": ["id", "consumer", "why_affected"],
            },
        },
        "state_lifecycle_audit": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "mechanism": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": [
                            "USER_INTENT",
                            "ACTION_LOCAL",
                            "DERIVED",
                            "CACHE",
                            "PERSISTED_SOURCE",
                            "EXTERNAL_SOURCE",
                            "LEGACY_COMPAT",
                            "UNKNOWN",
                        ],
                    },
                    "owner": {"type": "string"},
                    "scope": {"type": "string"},
                    "created_when": {"type": "string"},
                    "valid_while": {"type": "string"},
                    "invalidated_when": {"type": "string"},
                    "authority_domains": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "frozen_after_action_start": {"type": "boolean"},
                    "supersession_rule": {"type": "string"},
                    "must_not_override": {
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
                    "mechanism",
                    "role",
                    "owner",
                    "scope",
                    "created_when",
                    "valid_while",
                    "invalidated_when",
                    "authority_domains",
                    "frozen_after_action_start",
                    "supersession_rule",
                    "must_not_override",
                    "evidence",
                    "confidence",
                ],
            },
        },
        "decision_escalations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "mechanisms": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "why_lifecycle_cannot_resolve": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "user_semantics_required": {"type": "string"},
                    "consequences": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "id",
                    "question",
                    "mechanisms",
                    "why_lifecycle_cannot_resolve",
                    "evidence",
                    "user_semantics_required",
                    "consequences",
                ],
            },
        },
        "representation_consumer_audit": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "logical_state": {"type": "string"},
                    "representations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "semantics": {"type": "string"},
                                "evidence": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["name", "semantics", "evidence"],
                        },
                    },
                    "change_or_extension": {"type": "string"},
                    "consumers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "consumer": {"type": "string"},
                                "local_readers": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "expected_behavior": {"type": "string"},
                                "risk": {"type": "string"},
                                "evidence": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "consumer",
                                "local_readers",
                                "expected_behavior",
                                "risk",
                                "evidence",
                            ],
                        },
                    },
                },
                "required": [
                    "id",
                    "logical_state",
                    "representations",
                    "change_or_extension",
                    "consumers",
                ],
            },
        },
        "authority_matrix": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "coexisting_states": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "authority_rule": {"type": "string"},
                    "surfaces": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "expected_consistency": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "id",
                    "coexisting_states",
                    "authority_rule",
                    "surfaces",
                    "expected_consistency",
                    "evidence",
                ],
            },
        },
        "preservation_contract": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "claim": {"type": "string"},
                },
                "required": ["id", "claim"],
            },
        },
        "interaction_matrix": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "scenario": {"type": "string"},
                    "expected": {"type": "string"},
                    "risk": {"type": "string"},
                },
                "required": ["id", "scenario", "expected", "risk"],
            },
        },
        "test_matrix": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "scenario": {"type": "string"},
                    "expected": {"type": "string"},
                    "test_level": {
                        "type": "string",
                        "enum": [
                            "unit",
                            "contract",
                            "integration",
                            "ui",
                            "static",
                        ],
                    },
                },
                "required": [
                    "id",
                    "scenario",
                    "expected",
                    "test_level",
                ],
            },
        },
        "candidate_paths": {
            "type": "array",
            "items": {"type": "string"},
        },
        "release_obligations": {
            "type": "array",
            "items": {"type": "string"},
        },
        "proposed_change_surface": {
            "type": "array",
            "items": {"type": "string"},
        },
        "unknowns": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "status",
        "summary",
        "reproduction",
        "relevant_state",
        "current_contract",
        "assumptions",
        "root_cause",
        "local_owner",
        "shared_components",
        "affected_consumers",
        "state_lifecycle_audit",
        "decision_escalations",
        "representation_consumer_audit",
        "authority_matrix",
        "preservation_contract",
        "interaction_matrix",
        "test_matrix",
        "candidate_paths",
        "release_obligations",
        "proposed_change_surface",
        "unknowns",
    ],
}


PLANNER_INSTRUCTIONS = """
Ты Planner/Characterizer внутри Slivin Harness.

Работай строго read-only.

Твоя задача — ДО реализации охарактеризовать контракт, logical state,
representation, lifecycle и authority. Не исправляй код и не предлагай готовый patch.

1. CHARACTERIZE CURRENT CONTRACT.
   Найди current/legacy/compatibility states по коду, tests, docs и user requirement.
   Проверь optional/missing/null/empty/zero/stale semantics.

2. TRACE LOGICAL STATE.
   Определи writers/readers и representations logical state:
   materialized/token, persisted/generated, cached/fresh, local/remote, legacy/new.

3. STATE SCOPE / LIFECYCLE AUDIT.
   Для каждого materially relevant state mechanism создай LIFE-*.
   Классифицируй `role`:
   - USER_INTENT: текущее явное намерение пользователя/оператора;
   - ACTION_LOCAL: state, созданный/зафиксированный для конкретного action instance;
   - DERIVED: вычисляемое представление другого state;
   - CACHE: ускоряющий/временный cache, не source of truth;
   - PERSISTED_SOURCE: persisted domain source-of-truth;
   - EXTERNAL_SOURCE: authoritative external system/source;
   - LEGACY_COMPAT: совместимое legacy representation;
   - UNKNOWN: только если evidence реально недостаточно.

   Для каждого state зафиксируй:
   owner, scope, created_when, valid_while, invalidated_when,
   authority_domains, frozen_after_action_start, supersession_rule,
   must_not_override и evidence.

4. LIFECYCLE AUTHORITY RESOLUTION.
   Перед NEEDS_USER_DECISION попытайся разрешить конфликт механически по ownership
   и lifecycle. Используй следующие engineering invariants, если код/контракт им
   не противоречит:

   a) USER_INTENT определяет target для НОВОГО действия в своей authority domain.
   b) ACTION_LOCAL authoritative только внутри action instance, который его создал.
      Если target уже зафиксирован для начатого action и
      `frozen_after_action_start=true`, последующее изменение global intent не должно
      молча ретаргетить этот уже начатый action.
   c) ACTION_LOCAL state не должен переопределять выбор target для другого нового
      action только потому, что остался в памяти/cache.
   d) DERIVED/CACHE не переопределяет source state, из которого он выведен.
   e) stale/invalidated state никогда не authoritative.
   f) PERSISTED_SOURCE / EXTERNAL_SOURCE могут быть authoritative в своей domain
      даже против USER_INTENT, если это следует из domain contract; не предполагай
      обратное без evidence.
   g) LEGACY_COMPAT сохраняется только в пределах подтверждённой compatibility domain.

   Эти правила — про технический ownership/lifecycle, а не про продуктовый выбор.

5. WHEN NEEDS_USER_DECISION IS ALLOWED.
   `NEEDS_USER_DECISION` допустим только если после LIFE-* анализа остаются
   два или более СЕМАНТИЧЕСКИ РАВНОПРАВНЫХ состояния:
   - они претендуют на одну и ту же authority domain;
   - их lifecycle одновременно действителен;
   - ownership/temporal order не даёт приоритета;
   - current contract/tests/docs/user requirement не разрешают конфликт;
   - выбор реально меняет продуктовую семантику.

   Тогда обязательно заполни `decision_escalations` с конкретным доказательством,
   почему lifecycle/ownership не может разрешить вопрос.
   Не эскалируй пользователю внутреннюю implementation ambiguity, которую можно
   разрешить по lifecycle.

6. REPRESENTATION-CONSUMER AUDIT.
   Если representation logical state добавляется/расширяется/заменяется, создай REP-*.
   Для каждого materially affected consumer проверь локальные readers:
   stage/eligibility guards, permissions, visibility, count/summary,
   payload/routing, readback/cache, fail-open/fail-closed.
   Backend transport compatibility сама по себе недостаточна.

7. AUTHORITY / PRECEDENCE AUDIT.
   Если mechanisms могут сосуществовать, создай AUTH-*.
   Каждое AUTH-* должно опираться на LIFE-* и давать единое решение для:
   visibility, count, summary, eligibility, payload, routing, mutation target,
   readback/cleanup. Один surface не должен выбирать state A, а другой исполнять
   state B без подтверждённого contract.

8. CHALLENGE ASSUMPTIONS.
   Всё вне current contract → assumptions с evidence/confidence и
   `narrows_existing_behavior`. Не сужай compatibility молча.

9. ROOT CAUSE / OWNERSHIP / IMPACT.
   Ищи контрпример root cause. Предпочитай local owner. Для shared changes найди
   materially affected consumers и создай CONS-*.

10. UNIQUE IDS.
    Используй уникальные IDs:
    CC-*, A-*, CONS-*, LIFE-*, REP-*, AUTH-*, PRES-*, INT-*, TEST-*.
    decision_escalations использует DEC-*.

11. INTERACTIONS.
    Проверь current/stale/missing/empty/partial/combined и coexisting mechanisms.

12. TEST VALIDITY.
    Test matrix строится ДО production edit. Для regression bug evidence должен
    различать broken baseline и candidate. Green-on-baseline test не доказывает fix.

13. UNKNOWN SEMANTICS.
    Неизвестная настоящая product semantics → NEEDS_USER_DECISION.
    Недостаточное technical evidence/capability → BLOCKED.
    Technical lifecycle/ownership ambiguity → сначала LIFE/AUTH resolution, не user.

14. BASELINE VS WORKTREE.
    Различай canonical baseline blob и filesystem representation.

15. RELEASE OBLIGATIONS.
    Обязательно включай:
    - все materially relevant LIFE-*;
    - все REP-*;
    - все AUTH-*;
    - все materially affected CONS-*;
    - все PRES-*;
    - все TEST-*.
    INT-* — если interaction влияет на requested/preserved behavior.
    CC-* — только если final correctness зависит от этого факта.

16. CANDIDATE PATHS.
    Только точные repo-relative paths предполагаемых изменений.

17. REPLAN.
    После REPLAN_REQUIRED исправляй artifact по independent feedback.
    Baseline определяй через preflight/baseline_snapshot/head_sha.

18. proposed_change_surface — owners/files/modules, не готовый patch.

Верни только JSON по output schema.
""".strip()


def run_planner(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    toolchain: dict[str, str] | None = None,
    preflight: dict | None = None,
    baseline_snapshot: dict | None = None,
    revision_context: dict | None = None,
    explicit_skills: list[dict[str, str]] | None = None,
    on_heartbeat: Callable[[dict], None] | None = None,
    on_thread_started: Callable[[dict], None] | None = None,
) -> dict:
    thread_id = codex.start_thread(
        cwd=workspace,
        sandbox="read-only",
        developer_instructions=PLANNER_INSTRUCTIONS,
        on_started=on_thread_started,
    )

    toolchain_text = "\n".join(
        f"- {name}: {path}"
        for name, path in (toolchain or {}).items()
    ) or "(not declared)"

    preflight_json = json.dumps(
        preflight or {},
        ensure_ascii=False,
        indent=2,
    )

    baseline_snapshot_json = json.dumps(
        baseline_snapshot or {},
        ensure_ascii=False,
        indent=2,
    )

    revision_json = json.dumps(
        revision_context or {},
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

Pre-edit filesystem evidence, если Harness уже смог его снять:

--- BEGIN BASELINE SNAPSHOT ---
{baseline_snapshot_json}
--- END BASELINE SNAPSHOT ---

Trusted toolchain, если потребуется для понимания доступного verification:

--- BEGIN TOOLCHAIN ---
{toolchain_text}
--- END TOOLCHAIN ---

Feedback предыдущего Fresh Evaluator для перепланирования, если есть:

--- BEGIN REVISION CONTEXT ---
{revision_json}
--- END REVISION CONTEXT ---

Сначала охарактеризуй существующий contract, затем подготовь planning artifact.
Если workspace уже содержит candidate changes, canonical pre-change baseline —
это preflight.head_sha, а не текущая рабочая копия.
Код не изменяй.
""".strip()

    raw = codex.run_turn(
        thread_id=thread_id,
        prompt=prompt,
        output_schema=PLANNER_SCHEMA,
        skills=explicit_skills,
        on_heartbeat=on_heartbeat,
    )

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Planner returned invalid structured output.\n"
            f"Raw final response:\n{raw}"
        ) from exc
