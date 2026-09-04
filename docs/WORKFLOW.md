# Канонический workflow Slivin Harness

> Этот файл генерируется из `slivin_harness/workflow.py`. Не редактируйте таблицы вручную; запустите `./py tools/render_workflow_docs.py`.

- Harness: **0.8.0a16**
- Workflow schema: **workflow.v6**
- Реализуемая фаза: **phase7-final-gate-delivery-benchmark**

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
| 0 | `intake_preflight` | Intake / Preflight | Фиксирует задачу/workspace и проверяет static toolchain до agent stages. | нет | PREFLIGHT_READY | IMPLEMENTED |
| 1 | `planner` | Planner | Read-only исследование и техническая модель задачи. | нет | PLANNER_READY / PLANNER_SKIPPED_FAST | IMPLEMENTED |
| 2 | `implementation_contract` | Implementation Contract | Controller превращает load-bearing выводы в обязательный минимум результата. | нет | IMPLEMENTATION_CONTRACT_READY | IMPLEMENTED |
| 3 | `implementer` | Implementer | Создаёт candidate, tests/docs и собственное evidence. | нет | IMPLEMENTATION_COMPLETE | IMPLEMENTED |
| 4 | `deterministic_checks` | Controller deterministic checks | Независимо запускает локальные машинные проверки candidate. | нет | DETERMINISTIC_VERIFICATION_PASS | IMPLEMENTED |
| 5 | `runtime_verification` | Runtime / external verification | Условно проверяет observable runtime outcome, если local checks недостаточны. | да | RUNTIME_VERIFICATION_PASS / RUNTIME_VERIFICATION_SKIPPED | IMPLEMENTED |
| 6 | `evaluator` | Blind Evaluator | Независимо пытается опровергнуть полноту и корректность candidate. | нет | EVALUATION_PASS / EVALUATION_SKIPPED_FAST | IMPLEMENTED |
| 7 | `final_gate` | Final Gate / result handoff | Сверяет identity доказательств и безопасно выдаёт принятый result. | нет | HARNESS_TASK_PASS / HARNESS_BENCHMARK_PASS | IMPLEMENTED |

`IMPLEMENTED` означает: executor этапа подключён к текущему alpha-pipeline и его фактические границы описаны ниже. `COMPATIBILITY_IMPLEMENTED` означает: compatibility executor отображён на Run State, но полный утверждённый контракт этапа ещё не внедрён. `PLANNED` означает: этап присутствует в state machine, но его executor ещё не реализован. В Phase 7 весь Step 0–7 quality-core исполняется: Runtime запускает только Controller-configured typed scenarios, local-only Verification Plan получает явный `RUNTIME_VERIFICATION_SKIPPED`, Blind Evaluator фиксирует независимый audit до раскрытия Contract/evidence, а Final Gate связывает все доказательства с одним candidate и безопасно выдаёт принятый patch.

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
| `TASK_CONTRACT_CHANGED` | `planner` | `planner` | да | Изменение пользовательского контракта инвалидирует всё техническое reasoning. |
| `REPLAN_REQUIRED` | `planner` | `planner` | да | Ошибка технической модели требует нового независимого planning attempt. |
| `CONTRACT_EXPANDED` | `implementation_contract` | `implementation_contract` | нет | Новый consumer/risk меняет Definition of Done и обнуляет downstream evidence. |
| `CHECK_REGISTERED` | `implementation_contract` | `implementation_contract` | нет | Новая authoritative проверка должна войти в self-verify и все последующие gates. |
| `CANDIDATE_CHANGED` | `implementer` | `implementer` | нет | Любое изменение candidate делает прежние проверки устаревшими. |
| `DEPENDENCY_MANIFEST_CHANGED` | `implementer` | `implementer` | нет | Изменение dependency declaration требует rebuild runtime и повторного evidence. |
| `RUNTIME_ENV_CHANGED` | `implementer` | `implementer` | нет | Evidence другого runtime environment не переносится автоматически. |
| `RUNTIME_PROFILE_CHANGED` | `runtime_verification` | `runtime_verification` | нет | Новый runtime proof profile инвалидирует runtime, evaluator и final gate. |
| `SOURCE_CHANGED` | delivery only | `final_gate` | нет | Изменение source checkout не портит accepted candidate, но блокирует delivery. |
| `HIDDEN_GRADER_CHANGED` | `final_gate` | `final_gate` | нет | Изменение hidden grader требует новой calibration перед benchmark final gate. |
| `CANDIDATE_CHANGED_AFTER_EVALUATION` | `implementer` | `implementer` | нет | Candidate mutation после evaluation инвалидирует implementation evidence и все gates. |

## Что именно реализует Phase 7

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
+ strict static toolchain preflight before semantic baseline and agent stages
+ project-first Python placeholders plus explicit Harness interpreter
+ static candidate pre/post identity and Controller-private raw probe logs
+ exact harness version/Git build identity for each run
+ probe-backed tool capabilities with retained post-plan on-demand gate
+ worktree-local project-runtime bootstrap and drift reconciliation
+ Controller-private Contract Closure Record
+ LIVE_LOCAL / TEST_EXTERNAL / PROD_OBSERVE runtime scenario executor
+ fresh readback / cleanup / read-only result contracts
+ candidate, source and runtime-only-file immutability guards
+ two-phase BLIND EVALUATOR evaluator.v5
+ immutable blind-audit.v1 before Contract/check framing
+ Controller evidence audit without Planner/Implementer prose
+ Final Gate quality reconciliation bound to one candidate/revision vector
+ patch reconstruction from the recorded baseline
+ immutable final-acceptance.v2 and delivery-record.v2
+ transactional apply_to_source with source guards and safe rollback
+ standalone sanitized historical benchmark repository
+ classified hidden held-out exam without repair feedback
+ clean semantic replan reset with fresh Planner and Implementer threads
+ generated WORKFLOW.md / workflow.v6.json
```

Phase 7 завершает quality-core. Universal OS-enforced Controller subprocess sandbox, встроенная browser automation и универсальные typed wrappers для 1С/БД/Airflow остаются отдельными platform/project capabilities; Harness не выдаёт advisory isolation за OS-enforced sandbox. После Windows self-check этой версии следующий обязательный checkpoint — реальный historical `_90` trial, а не новая архитектурная фаза.
