# Канонический workflow Slivin Harness

> Этот файл генерируется из `slivin_harness/workflow.py`. Не редактируйте таблицы вручную; запустите `./py tools/render_workflow_docs.py`.

- Harness: **0.8.0a2**
- Workflow schema: **workflow.v1**
- Реализуемая фаза: **phase1-state-machine-foundation**

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

| Step | Machine id | Название | Единственная основная задача | Условный | Успешный result code | Состояние в Phase 1 |
| ---: | --- | --- | --- | :---: | --- | --- |
| 0 | `intake_preflight` | Intake / Preflight | Фиксирует задачу, baseline, workspace и доступные инструменты. | нет | PREFLIGHT_READY | COMPATIBILITY_IMPLEMENTED |
| 1 | `planner` | Planner | Read-only исследование и техническая модель задачи. | нет | PLANNER_READY / PLANNER_SKIPPED_FAST | COMPATIBILITY_IMPLEMENTED |
| 2 | `implementation_contract` | Implementation Contract | Controller превращает load-bearing выводы в обязательный минимум результата. | нет | IMPLEMENTATION_CONTRACT_READY | COMPATIBILITY_IMPLEMENTED |
| 3 | `implementer` | Implementer | Создаёт candidate, tests/docs и собственное evidence. | нет | IMPLEMENTATION_COMPLETE | COMPATIBILITY_IMPLEMENTED |
| 4 | `deterministic_checks` | Controller deterministic checks | Независимо запускает локальные машинные проверки candidate. | нет | DETERMINISTIC_VERIFICATION_PASS | COMPATIBILITY_IMPLEMENTED |
| 5 | `runtime_verification` | Runtime / external verification | Условно проверяет observable runtime outcome, если local checks недостаточны. | да | RUNTIME_VERIFICATION_PASS / RUNTIME_VERIFICATION_SKIPPED | PLANNED |
| 6 | `evaluator` | Blind Evaluator | Независимо пытается опровергнуть полноту и корректность candidate. | нет | EVALUATION_PASS / EVALUATION_SKIPPED_FAST | COMPATIBILITY_IMPLEMENTED |
| 7 | `final_gate` | Final Gate / result handoff | Сверяет identity доказательств и безопасно выдаёт принятый result. | нет | HARNESS_TASK_PASS / HARNESS_BENCHMARK_PASS | COMPATIBILITY_IMPLEMENTED |

`COMPATIBILITY_IMPLEMENTED` означает: существующий executor 0.7.1 отображён на новый Run State, но полный новый контракт этапа будет внедряться последующими фазами. `PLANNED` означает: этап присутствует в канонической state machine, но его executor ещё не реализован. В Phase 1 Runtime честно записывается как `RUNTIME_VERIFICATION_SKIPPED` с причиной `RUNTIME_LAYER_NOT_IMPLEMENTED_PHASE1`; это compatibility record, а не доказательство, что runtime конкретной будущей задачи не нужен.

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

В Phase 1 ещё не созданные будущие artifacts имеют revision `null`. Candidate получает отдельный `candidate_id`, который включает baseline SHA, изменённые пути, удаления, Git-visible file mode и SHA-256 фактических bytes. На filesystem, где Git не сообщает mode-only working-tree change в HEAD-to-working-tree diff, один `chmod` не меняет candidate identity.

## Правила инвалидации

| Trigger | Инвалидировать начиная с | Возобновить с | Новый attempt | Почему |
| --- | --- | --- | :---: | --- |
| `TASK_CONTRACT_CHANGED` | `planner` | `planner` | да | Изменение пользовательского контракта инвалидирует всё техническое reasoning. |
| `REPLAN_REQUIRED` | `planner` | `planner` | да | Ошибка технической модели требует нового независимого planning attempt. |
| `CONTRACT_EXPANDED` | `implementer` | `implementer` | нет | Новый consumer/risk меняет Definition of Done и обнуляет downstream evidence. |
| `CHECK_REGISTERED` | `implementer` | `implementer` | нет | Новая authoritative проверка должна войти в self-verify и все последующие gates. |
| `CANDIDATE_CHANGED` | `implementer` | `implementer` | нет | Любое изменение candidate делает прежние проверки устаревшими. |
| `DEPENDENCY_MANIFEST_CHANGED` | `implementer` | `implementer` | нет | Изменение dependency declaration требует rebuild runtime и повторного evidence. |
| `RUNTIME_ENV_CHANGED` | `implementer` | `implementer` | нет | Evidence другого runtime environment не переносится автоматически. |
| `RUNTIME_PROFILE_CHANGED` | `runtime_verification` | `runtime_verification` | нет | Новый runtime proof profile инвалидирует runtime, evaluator и final gate. |
| `SOURCE_CHANGED` | delivery only | `final_gate` | нет | Изменение source checkout не портит accepted candidate, но блокирует delivery. |
| `HIDDEN_GRADER_CHANGED` | `final_gate` | `final_gate` | нет | Изменение hidden grader требует новой calibration перед benchmark final gate. |
| `CANDIDATE_CHANGED_AFTER_EVALUATION` | `implementer` | `implementer` | нет | Candidate mutation после evaluation инвалидирует implementation evidence и все gates. |

## Что именно реализует Phase 1

```text
machine-readable workflow
+ единые enums статусов
+ versioned run_state.json
+ единый candidate_id
+ таблица invalidation rules
+ generated WORKFLOW.md / workflow.v1.json
```

Phase 1 **не меняет model prompts и не заявляет, что уже реализованы** User Task Contract, Verification Plan compiler, private Controller plane, Runtime executor или двухфазный Evaluator. Эти возможности будут добавляться по следующим фазам поверх уже фиксированной state machine.
