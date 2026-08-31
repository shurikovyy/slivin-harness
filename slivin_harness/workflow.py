from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable, TypeVar

WORKFLOW_VERSION = "workflow.v5"
WORKFLOW_PHASE = "phase6-runtime-two-phase-evaluator"


class _TextEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


E = TypeVar("E", bound=_TextEnum)


def enum_values(enum_type: type[E]) -> list[str]:
    return [item.value for item in enum_type]


class WorkflowMode(_TextEnum):
    PRODUCTION = "PRODUCTION"
    HISTORICAL_BENCHMARK = "HISTORICAL_BENCHMARK"


class PipelineProfile(_TextEnum):
    FAST = "FAST"
    FULL = "FULL"


class WorkflowOutcome(_TextEnum):
    PASS = "PASS"
    REPAIR = "REPAIR"
    REPLAN = "REPLAN"
    BLOCKED = "BLOCKED"
    NEEDS_USER_DECISION = "NEEDS_USER_DECISION"
    INVALID = "INVALID"


class StageState(_TextEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"


class StageMaturity(_TextEnum):
    IMPLEMENTED = "IMPLEMENTED"
    COMPATIBILITY_IMPLEMENTED = "COMPATIBILITY_IMPLEMENTED"
    PLANNED = "PLANNED"


class StageId(_TextEnum):
    INTAKE_PREFLIGHT = "intake_preflight"
    PLANNER = "planner"
    IMPLEMENTATION_CONTRACT = "implementation_contract"
    IMPLEMENTER = "implementer"
    DETERMINISTIC_CHECKS = "deterministic_checks"
    RUNTIME_VERIFICATION = "runtime_verification"
    EVALUATOR = "evaluator"
    FINAL_GATE = "final_gate"


class StageResultCode(_TextEnum):
    PREFLIGHT_READY = "PREFLIGHT_READY"
    PLANNER_READY = "PLANNER_READY"
    PLANNER_SKIPPED_FAST = "PLANNER_SKIPPED_FAST"
    IMPLEMENTATION_CONTRACT_READY = "IMPLEMENTATION_CONTRACT_READY"
    IMPLEMENTATION_COMPLETE = "IMPLEMENTATION_COMPLETE"
    DETERMINISTIC_VERIFICATION_PASS = "DETERMINISTIC_VERIFICATION_PASS"
    CHECK_REPAIR_REQUIRED = "CHECK_REPAIR_REQUIRED"
    RUNTIME_VERIFICATION_PASS = "RUNTIME_VERIFICATION_PASS"
    RUNTIME_VERIFICATION_SKIPPED = "RUNTIME_VERIFICATION_SKIPPED"
    RUNTIME_REPAIR_REQUIRED = "RUNTIME_REPAIR_REQUIRED"
    EVALUATION_PASS = "EVALUATION_PASS"
    EVALUATION_SKIPPED_FAST = "EVALUATION_SKIPPED_FAST"
    EVALUATOR_FINDINGS = "EVALUATOR_FINDINGS"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    FINAL_ACCEPTANCE_PASS = "FINAL_ACCEPTANCE_PASS"
    RESULT_DELIVERY_PASS = "RESULT_DELIVERY_PASS"
    HARNESS_TASK_PASS = "HARNESS_TASK_PASS"
    HARNESS_BENCHMARK_PASS = "HARNESS_BENCHMARK_PASS"
    BLOCKED = "BLOCKED"
    NEEDS_USER_DECISION = "NEEDS_USER_DECISION"
    INVALID = "INVALID"


class TaskContractStatus(_TextEnum):
    READY = "READY"
    NEEDS_USER_DECISION = "NEEDS_USER_DECISION"


class PlannerStatus(_TextEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    NEEDS_USER_DECISION = "NEEDS_USER_DECISION"
    TASK_CONTRACT_INVALID = "TASK_CONTRACT_INVALID"


class ImplementerStatus(_TextEnum):
    COMPLETE = "COMPLETE"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    BLOCKED = "BLOCKED"
    NEEDS_USER_DECISION = "NEEDS_USER_DECISION"


class EvaluatorStatus(_TextEnum):
    PASS = "PASS"
    FINDINGS = "FINDINGS"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    BLOCKED = "BLOCKED"
    NEEDS_USER_DECISION = "NEEDS_USER_DECISION"


class CheckStatus(_TextEnum):
    PASS = "CHECK_PASS"
    FAIL = "CHECK_FAIL"
    TIMEOUT = "CHECK_TIMEOUT"
    INFRA_ERROR = "CHECK_INFRA_ERROR"
    MUTATED_CANDIDATE = "CHECK_MUTATED_CANDIDATE"


class RuntimeStatus(_TextEnum):
    PASS = "RUNTIME_VERIFICATION_PASS"
    SKIPPED = "RUNTIME_VERIFICATION_SKIPPED"
    BEHAVIOR_FAIL = "RUNTIME_BEHAVIOR_FAIL"
    START_FAIL = "RUNTIME_START_FAIL"
    TIMEOUT = "RUNTIME_TIMEOUT"
    INFRA_ERROR = "RUNTIME_INFRA_ERROR"
    INVALID_RESULT = "RUNTIME_INVALID_RESULT"
    READBACK_FAIL = "RUNTIME_READBACK_FAIL"
    CLEANUP_FAIL = "RUNTIME_CLEANUP_FAIL"
    MUTATED_CANDIDATE = "RUNTIME_MUTATED_CANDIDATE"


class HeldoutStatus(_TextEnum):
    PASS = "HELDOUT_PASS"
    SEMANTIC_FAIL = "HELDOUT_SEMANTIC_FAIL"
    INFRA_ERROR = "HELDOUT_INFRA_ERROR"
    TIMEOUT = "HELDOUT_TIMEOUT"
    MUTATED_CANDIDATE = "HELDOUT_MUTATED_CANDIDATE"


class RevisionKind(_TextEnum):
    TASK_CONTRACT = "task_contract"
    PLAN = "plan"
    IMPLEMENTATION_CONTRACT = "implementation_contract"
    VERIFICATION_PLAN = "verification_plan"
    CANDIDATE = "candidate"
    RUNTIME_ENVIRONMENT = "runtime_environment"


class InvalidationTrigger(_TextEnum):
    TASK_CONTRACT_CHANGED = "TASK_CONTRACT_CHANGED"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    CONTRACT_EXPANDED = "CONTRACT_EXPANDED"
    CHECK_REGISTERED = "CHECK_REGISTERED"
    CANDIDATE_CHANGED = "CANDIDATE_CHANGED"
    DEPENDENCY_MANIFEST_CHANGED = "DEPENDENCY_MANIFEST_CHANGED"
    RUNTIME_ENV_CHANGED = "RUNTIME_ENV_CHANGED"
    RUNTIME_PROFILE_CHANGED = "RUNTIME_PROFILE_CHANGED"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    HIDDEN_GRADER_CHANGED = "HIDDEN_GRADER_CHANGED"
    CANDIDATE_CHANGED_AFTER_EVALUATION = "CANDIDATE_CHANGED_AFTER_EVALUATION"


@dataclass(frozen=True)
class StageDefinition:
    number: int
    stage_id: StageId
    title: str
    purpose: str
    success_codes: tuple[StageResultCode, ...]
    skip_codes: tuple[StageResultCode, ...]
    optional: bool
    maturity: StageMaturity


@dataclass(frozen=True)
class InvalidationRule:
    trigger: InvalidationTrigger
    invalidate_from: StageId | None
    restart_at: StageId | None
    new_attempt_required: bool
    delivery_only: bool
    reason: str


STAGES: tuple[StageDefinition, ...] = (
    StageDefinition(
        0,
        StageId.INTAKE_PREFLIGHT,
        "Intake / Preflight",
        "Фиксирует задачу, baseline, workspace и доступные инструменты.",
        (StageResultCode.PREFLIGHT_READY,),
        (),
        False,
        StageMaturity.IMPLEMENTED,
    ),
    StageDefinition(
        1,
        StageId.PLANNER,
        "Planner",
        "Read-only исследование и техническая модель задачи.",
        (StageResultCode.PLANNER_READY, StageResultCode.PLANNER_SKIPPED_FAST),
        (StageResultCode.PLANNER_SKIPPED_FAST,),
        False,
        StageMaturity.IMPLEMENTED,
    ),
    StageDefinition(
        2,
        StageId.IMPLEMENTATION_CONTRACT,
        "Implementation Contract",
        "Controller превращает load-bearing выводы в обязательный минимум результата.",
        (StageResultCode.IMPLEMENTATION_CONTRACT_READY,),
        (),
        False,
        StageMaturity.IMPLEMENTED,
    ),
    StageDefinition(
        3,
        StageId.IMPLEMENTER,
        "Implementer",
        "Создаёт candidate, tests/docs и собственное evidence.",
        (StageResultCode.IMPLEMENTATION_COMPLETE,),
        (),
        False,
        StageMaturity.COMPATIBILITY_IMPLEMENTED,
    ),
    StageDefinition(
        4,
        StageId.DETERMINISTIC_CHECKS,
        "Controller deterministic checks",
        "Независимо запускает локальные машинные проверки candidate.",
        (StageResultCode.DETERMINISTIC_VERIFICATION_PASS,),
        (),
        False,
        StageMaturity.COMPATIBILITY_IMPLEMENTED,
    ),
    StageDefinition(
        5,
        StageId.RUNTIME_VERIFICATION,
        "Runtime / external verification",
        "Условно проверяет observable runtime outcome, если local checks недостаточны.",
        (
            StageResultCode.RUNTIME_VERIFICATION_PASS,
            StageResultCode.RUNTIME_VERIFICATION_SKIPPED,
        ),
        (StageResultCode.RUNTIME_VERIFICATION_SKIPPED,),
        True,
        StageMaturity.IMPLEMENTED,
    ),
    StageDefinition(
        6,
        StageId.EVALUATOR,
        "Blind Evaluator",
        "Независимо пытается опровергнуть полноту и корректность candidate.",
        (StageResultCode.EVALUATION_PASS, StageResultCode.EVALUATION_SKIPPED_FAST),
        (StageResultCode.EVALUATION_SKIPPED_FAST,),
        False,
        StageMaturity.IMPLEMENTED,
    ),
    StageDefinition(
        7,
        StageId.FINAL_GATE,
        "Final Gate / result handoff",
        "Сверяет identity доказательств и безопасно выдаёт принятый result.",
        (StageResultCode.HARNESS_TASK_PASS, StageResultCode.HARNESS_BENCHMARK_PASS),
        (),
        False,
        StageMaturity.COMPATIBILITY_IMPLEMENTED,
    ),
)

STAGE_BY_ID = {stage.stage_id: stage for stage in STAGES}

SUCCESS_NEXT: dict[StageId, StageId | None] = {
    StageId.INTAKE_PREFLIGHT: StageId.PLANNER,
    StageId.PLANNER: StageId.IMPLEMENTATION_CONTRACT,
    StageId.IMPLEMENTATION_CONTRACT: StageId.IMPLEMENTER,
    StageId.IMPLEMENTER: StageId.DETERMINISTIC_CHECKS,
    StageId.DETERMINISTIC_CHECKS: StageId.RUNTIME_VERIFICATION,
    StageId.RUNTIME_VERIFICATION: StageId.EVALUATOR,
    StageId.EVALUATOR: StageId.FINAL_GATE,
    StageId.FINAL_GATE: None,
}

ALLOWED_STAGE_TRANSITIONS: dict[StageId | None, frozenset[StageId]] = {
    None: frozenset({StageId.INTAKE_PREFLIGHT}),
    StageId.INTAKE_PREFLIGHT: frozenset({StageId.PLANNER}),
    StageId.PLANNER: frozenset({StageId.IMPLEMENTATION_CONTRACT}),
    StageId.IMPLEMENTATION_CONTRACT: frozenset({StageId.IMPLEMENTER}),
    StageId.IMPLEMENTER: frozenset({StageId.DETERMINISTIC_CHECKS}),
    StageId.DETERMINISTIC_CHECKS: frozenset(
        {StageId.IMPLEMENTER, StageId.RUNTIME_VERIFICATION}
    ),
    StageId.RUNTIME_VERIFICATION: frozenset({StageId.IMPLEMENTER, StageId.EVALUATOR}),
    StageId.EVALUATOR: frozenset(
        {StageId.IMPLEMENTER, StageId.PLANNER, StageId.FINAL_GATE}
    ),
    StageId.FINAL_GATE: frozenset(),
}

INVALIDATION_RULES: dict[InvalidationTrigger, InvalidationRule] = {
    InvalidationTrigger.TASK_CONTRACT_CHANGED: InvalidationRule(
        InvalidationTrigger.TASK_CONTRACT_CHANGED,
        StageId.PLANNER,
        StageId.PLANNER,
        True,
        False,
        "Изменение пользовательского контракта инвалидирует всё техническое reasoning.",
    ),
    InvalidationTrigger.REPLAN_REQUIRED: InvalidationRule(
        InvalidationTrigger.REPLAN_REQUIRED,
        StageId.PLANNER,
        StageId.PLANNER,
        True,
        False,
        "Ошибка технической модели требует нового независимого planning attempt.",
    ),
    InvalidationTrigger.CONTRACT_EXPANDED: InvalidationRule(
        InvalidationTrigger.CONTRACT_EXPANDED,
        StageId.IMPLEMENTATION_CONTRACT,
        StageId.IMPLEMENTATION_CONTRACT,
        False,
        False,
        "Новый consumer/risk меняет Definition of Done и обнуляет downstream evidence.",
    ),
    InvalidationTrigger.CHECK_REGISTERED: InvalidationRule(
        InvalidationTrigger.CHECK_REGISTERED,
        StageId.IMPLEMENTATION_CONTRACT,
        StageId.IMPLEMENTATION_CONTRACT,
        False,
        False,
        "Новая authoritative проверка должна войти в self-verify и все последующие gates.",
    ),
    InvalidationTrigger.CANDIDATE_CHANGED: InvalidationRule(
        InvalidationTrigger.CANDIDATE_CHANGED,
        StageId.IMPLEMENTER,
        StageId.IMPLEMENTER,
        False,
        False,
        "Любое изменение candidate делает прежние проверки устаревшими.",
    ),
    InvalidationTrigger.DEPENDENCY_MANIFEST_CHANGED: InvalidationRule(
        InvalidationTrigger.DEPENDENCY_MANIFEST_CHANGED,
        StageId.IMPLEMENTER,
        StageId.IMPLEMENTER,
        False,
        False,
        "Изменение dependency declaration требует rebuild runtime и повторного evidence.",
    ),
    InvalidationTrigger.RUNTIME_ENV_CHANGED: InvalidationRule(
        InvalidationTrigger.RUNTIME_ENV_CHANGED,
        StageId.IMPLEMENTER,
        StageId.IMPLEMENTER,
        False,
        False,
        "Evidence другого runtime environment не переносится автоматически.",
    ),
    InvalidationTrigger.RUNTIME_PROFILE_CHANGED: InvalidationRule(
        InvalidationTrigger.RUNTIME_PROFILE_CHANGED,
        StageId.RUNTIME_VERIFICATION,
        StageId.RUNTIME_VERIFICATION,
        False,
        False,
        "Новый runtime proof profile инвалидирует runtime, evaluator и final gate.",
    ),
    InvalidationTrigger.SOURCE_CHANGED: InvalidationRule(
        InvalidationTrigger.SOURCE_CHANGED,
        None,
        StageId.FINAL_GATE,
        False,
        True,
        "Изменение source checkout не портит accepted candidate, но блокирует delivery.",
    ),
    InvalidationTrigger.HIDDEN_GRADER_CHANGED: InvalidationRule(
        InvalidationTrigger.HIDDEN_GRADER_CHANGED,
        StageId.FINAL_GATE,
        StageId.FINAL_GATE,
        False,
        False,
        "Изменение hidden grader требует новой calibration перед benchmark final gate.",
    ),
    InvalidationTrigger.CANDIDATE_CHANGED_AFTER_EVALUATION: InvalidationRule(
        InvalidationTrigger.CANDIDATE_CHANGED_AFTER_EVALUATION,
        StageId.IMPLEMENTER,
        StageId.IMPLEMENTER,
        False,
        False,
        "Candidate mutation после evaluation инвалидирует implementation evidence и все gates.",
    ),
}


def stage_number(stage: StageId) -> int:
    return STAGE_BY_ID[stage].number


def is_allowed_transition(previous: StageId | None, current: StageId) -> bool:
    return current in ALLOWED_STAGE_TRANSITIONS[previous]


def stages_from(stage: StageId) -> tuple[StageId, ...]:
    minimum = stage_number(stage)
    return tuple(item.stage_id for item in STAGES if item.number >= minimum)


def validate_workflow_definition() -> None:
    expected_numbers = list(range(8))
    actual_numbers = [stage.number for stage in STAGES]
    if actual_numbers != expected_numbers:
        raise RuntimeError(
            f"Workflow stages must be numbered 0..7 exactly; got {actual_numbers}"
        )
    if len(STAGE_BY_ID) != len(STAGES):
        raise RuntimeError("Workflow stage ids must be unique")
    stage_ids = set(STAGE_BY_ID)
    if set(SUCCESS_NEXT) != stage_ids:
        raise RuntimeError("Every stage must have exactly one success transition definition")
    if set(ALLOWED_STAGE_TRANSITIONS) != {None, *stage_ids}:
        raise RuntimeError("Allowed transition sources must be <START> plus every stage")
    seen_success_codes: set[StageResultCode] = set()
    for stage in STAGES:
        if stage.stage_id not in ALLOWED_STAGE_TRANSITIONS:
            raise RuntimeError(f"Missing transition definition for {stage.stage_id}")
        if not set(stage.skip_codes).issubset(stage.success_codes):
            raise RuntimeError(f"Skip codes must be successful codes for {stage.stage_id}")
        overlap = seen_success_codes.intersection(stage.success_codes)
        if overlap:
            raise RuntimeError(
                f"Successful result codes must identify one stage only: {sorted(item.value for item in overlap)}"
            )
        seen_success_codes.update(stage.success_codes)
        expected_next = SUCCESS_NEXT[stage.stage_id]
        if expected_next is not None and expected_next not in ALLOWED_STAGE_TRANSITIONS[stage.stage_id]:
            raise RuntimeError(
                f"Success transition {stage.stage_id} -> {expected_next} is not allowed"
            )
    if set(INVALIDATION_RULES) != set(InvalidationTrigger):
        raise RuntimeError("Every invalidation trigger must have exactly one rule")


def _enum_snapshot(enum_type: type[_TextEnum]) -> list[str]:
    return [item.value for item in enum_type]


def workflow_snapshot(*, harness_version: str) -> dict[str, object]:
    validate_workflow_definition()
    from slivin_harness.control_plane import CONTROL_PLANE_VERSION
    from slivin_harness.execution import EXECUTION_BROKER_VERSION
    from slivin_harness.implementer import (
        IMPLEMENTATION_CONTRACT_VERSION,
        IMPLEMENTER_PROTOCOL_VERSION,
    )
    from slivin_harness.phase5 import (
        CONTRACT_EXPANSION_VERSION,
        PHASE5_VERSION,
        PROJECT_RUNTIME_VERSION,
    )
    from slivin_harness.phase6 import (
        BLIND_AUDIT_VERSION,
        CONTRACT_CLOSURE_VERSION,
        PHASE6_VERSION,
        RUNTIME_EVIDENCE_VERSION,
        RUNTIME_SCENARIO_VERSION,
    )
    from slivin_harness.protocol import (
        EVALUATOR_PROTOCOL_VERSION,
        PLANNER_PROTOCOL_VERSION,
    )
    from slivin_harness.task_contract import TASK_CONTRACT_VERSION
    from slivin_harness.verification import VERIFICATION_PLAN_VERSION

    return {
        "schema_version": WORKFLOW_VERSION,
        "harness_version": harness_version,
        "phase": WORKFLOW_PHASE,
        "controller_foundation": {
            "control_plane": CONTROL_PLANE_VERSION,
            "execution_broker": EXECUTION_BROKER_VERSION,
        },
        "contract_versions": {
            "task_contract": TASK_CONTRACT_VERSION,
            "planner": PLANNER_PROTOCOL_VERSION,
            "implementer": IMPLEMENTER_PROTOCOL_VERSION,
            "implementation_contract": IMPLEMENTATION_CONTRACT_VERSION,
            "verification_plan": VERIFICATION_PLAN_VERSION,
            "evaluator": EVALUATOR_PROTOCOL_VERSION,
            "blind_audit": BLIND_AUDIT_VERSION,
            "contract_closure": CONTRACT_CLOSURE_VERSION,
            "runtime_scenario": RUNTIME_SCENARIO_VERSION,
            "runtime_evidence": RUNTIME_EVIDENCE_VERSION,
        },
        "phase_layers": {
            "phase5": PHASE5_VERSION,
            "contract_expansion": CONTRACT_EXPANSION_VERSION,
            "project_runtime": PROJECT_RUNTIME_VERSION,
            "phase6": PHASE6_VERSION,
        },
        "stages": [
            {
                **asdict(stage),
                "stage_id": stage.stage_id.value,
                "success_codes": [item.value for item in stage.success_codes],
                "skip_codes": [item.value for item in stage.skip_codes],
                "maturity": stage.maturity.value,
            }
            for stage in STAGES
        ],
        "success_next": {
            stage.value: next_stage.value if next_stage is not None else None
            for stage, next_stage in SUCCESS_NEXT.items()
        },
        "allowed_transitions": {
            (stage.value if stage is not None else "<START>"): sorted(
                item.value for item in targets
            )
            for stage, targets in ALLOWED_STAGE_TRANSITIONS.items()
        },
        "routing_outcomes": _enum_snapshot(WorkflowOutcome),
        "stage_states": _enum_snapshot(StageState),
        "stage_result_codes": _enum_snapshot(StageResultCode),
        "agent_statuses": {
            "task_contract": _enum_snapshot(TaskContractStatus),
            "planner": _enum_snapshot(PlannerStatus),
            "implementer": _enum_snapshot(ImplementerStatus),
            "evaluator": _enum_snapshot(EvaluatorStatus),
        },
        "verification_statuses": {
            "checks": _enum_snapshot(CheckStatus),
            "runtime": _enum_snapshot(RuntimeStatus),
            "heldout": _enum_snapshot(HeldoutStatus),
        },
        "revision_kinds": _enum_snapshot(RevisionKind),
        "invalidation_rules": [
            {
                **asdict(rule),
                "trigger": rule.trigger.value,
                "invalidate_from": (
                    rule.invalidate_from.value if rule.invalidate_from is not None else None
                ),
                "restart_at": rule.restart_at.value if rule.restart_at is not None else None,
            }
            for rule in INVALIDATION_RULES.values()
        ],
    }


def _success_codes(values: Iterable[StageResultCode]) -> str:
    return " / ".join(item.value for item in values)


def render_workflow_markdown(*, harness_version: str) -> str:
    validate_workflow_definition()
    rows = []
    for stage in STAGES:
        rows.append(
            "| "
            + " | ".join(
                [
                    str(stage.number),
                    f"`{stage.stage_id.value}`",
                    stage.title,
                    stage.purpose,
                    "да" if stage.optional else "нет",
                    _success_codes(stage.success_codes),
                    stage.maturity.value,
                ]
            )
            + " |"
        )

    invalidation_rows = []
    for rule in INVALIDATION_RULES.values():
        invalidation_rows.append(
            "| "
            + " | ".join(
                [
                    f"`{rule.trigger.value}`",
                    f"`{rule.invalidate_from.value}`" if rule.invalidate_from else "delivery only",
                    f"`{rule.restart_at.value}`" if rule.restart_at else "—",
                    "да" if rule.new_attempt_required else "нет",
                    rule.reason,
                ]
            )
            + " |"
        )

    return f"""# Канонический workflow Slivin Harness

> Этот файл генерируется из `slivin_harness/workflow.py`. Не редактируйте таблицы вручную; запустите `./py tools/render_workflow_docs.py`.

- Harness: **{harness_version}**
- Workflow schema: **{WORKFLOW_VERSION}**
- Реализуемая фаза: **{WORKFLOW_PHASE}**

## Понятная схема

```text
0. Intake / Preflight
        ↓ PREFLIGHT_READY
1. Planner
        ↓ PLANNER_READY
2. Implementation Contract
        ↓ IMPLEMENTATION_CONTRACT_READY
3. Implementer
        ↓ IMPLEMENTATION_COMPLETE
4. Controller deterministic checks
        ↓ DETERMINISTIC_VERIFICATION_PASS
5. Runtime / external verification (условно)
        ↓ RUNTIME_VERIFICATION_PASS или RUNTIME_VERIFICATION_SKIPPED
6. Blind Evaluator
        ↓ EVALUATION_PASS
7. Final Gate / result handoff
        ↓ HARNESS_TASK_PASS или HARNESS_BENCHMARK_PASS
```

## Этапы

| Step | Machine id | Название | Единственная основная задача | Условный | Успешный result code | Состояние в текущей фазе |
| ---: | --- | --- | --- | :---: | --- | --- |
{chr(10).join(rows)}

`IMPLEMENTED` означает: executor этапа подключён к текущему alpha-pipeline и его фактические границы описаны ниже. `COMPATIBILITY_IMPLEMENTED` означает: compatibility executor отображён на Run State, но полный утверждённый контракт этапа ещё не внедрён. `PLANNED` означает: этап присутствует в state machine, но его executor ещё не реализован. В Phase 6 Runtime исполняет только Controller-configured typed scenarios, а local-only Verification Plan получает явный `RUNTIME_VERIFICATION_SKIPPED` с причиной `NO_RUNTIME_PROOF_REQUIRED`. Blind Evaluator работает в две фазы: независимый audit фиксируется до раскрытия Contract/evidence.

## Разрешённые петли

```text
Controller check / runtime / Evaluator finding
        → REPAIR
        → тот же Implementer
        → candidate меняется
        → все downstream evidence повторяются

Ошибка самой технической модели
        → REPLAN
        → новый Planner attempt
        → новый Implementation Contract

BLOCKED
        → остановка из-за capability/environment/boundary

NEEDS_USER_DECISION
        → остановка только из-за product-semantic выбора
```

## Версии Run State

Каждый authoritative artifact привязывается к revision vector:

```text
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
candidate_rev
runtime_environment_rev
attempt_id
```

Ещё не созданные artifacts последующих фаз имеют revision `null`. Candidate получает отдельный `candidate_id`, который включает baseline SHA, изменённые пути, удаления, Git-visible file mode и SHA-256 фактических bytes. На filesystem, где Git не сообщает mode-only working-tree change в HEAD-to-working-tree diff, один `chmod` не меняет candidate identity.

## Правила инвалидации

| Trigger | Инвалидировать начиная с | Возобновить с | Новый attempt | Почему |
| --- | --- | --- | :---: | --- |
{chr(10).join(invalidation_rows)}

## Что именно реализует Phase 6

```text
machine-readable workflow и versioned Run State
+ private Controller plane / Execution Broker foundation
+ USER TASK CONTRACT task-contract.v1
+ PLANNER planner.v4
+ IMPLEMENTATION CONTRACT implementation-contract.v3
+ typed VERIFICATION PLAN verification-plan.v1
+ IMPLEMENTER implementer.v3
+ transactional Contract / Verification Plan expansion
+ canonical .worktreeinclude exposure policy
+ worktree-local project-runtime bootstrap and drift reconciliation
+ Controller-private Contract Closure Record
+ LIVE_LOCAL / TEST_EXTERNAL / PROD_OBSERVE runtime scenario executor
+ fresh readback / cleanup / read-only result contracts
+ candidate, source and runtime-only-file immutability guards
+ two-phase BLIND EVALUATOR evaluator.v5
+ immutable blind-audit.v1 before Contract/check framing
+ Controller evidence audit without Planner/Implementer prose
+ generated WORKFLOW.md / workflow.v5.json
```

Phase 6 **не заявляет полностью готовыми** universal OS-enforced Controller subprocess sandbox, встроенную browser automation, универсальные typed wrappers для 1С/БД/Airflow, clean-worktree semantic replan или финальную delivery transaction. Runtime commands являются owner-configured Controller capabilities; `PROD_OBSERVE` требует явно заявленной технической read-only границы, но Harness не выдаёт advisory isolation за OS-enforced sandbox.
"""
