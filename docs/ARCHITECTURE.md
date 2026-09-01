# Архитектура Slivin Harness 0.8.0a11 — Phase 7

## Назначение

`0.8.0a11` завершает согласованный Step 0–7 quality-core. Phase 7 не добавляет нового reviewer-а: она делает финальную приёмку, patch proof, безопасную доставку и historical benchmark isolation детерминированной ответственностью Controller.

Machine phase id:

```text
phase7-final-gate-delivery-benchmark
```

## Канонический pipeline

```text
Step 0 — Intake / Preflight / User Task Contract
        ↓
Step 1 — fresh read-only Planner
        ↓
Step 2 — Implementation Contract + Verification Plan
        ↓
Step 3 — Implementer + self verification
        ↓
Step 4 — independent deterministic Controller checks
        ↓
Step 5 — Runtime / External Verification, conditional
        ↓
Step 6 — fresh two-phase Blind Evaluator
        ↓
Step 7 — Final Gate / result handoff / hidden benchmark exam
```

Полная генерируемая схема находится в [WORKFLOW.md](WORKFLOW.md), machine-readable snapshot — в [workflow.v6.json](workflow.v6.json).

## Версионные слои

```text
Harness                     0.8.0a11
Manifest                    version = 2
Workflow                    workflow.v6
Run State                   run-state.v1
Candidate                   candidate.v1
Controller plane            controller-plane.v1
Execution Broker            execution-broker.v1
Task Contract               task-contract.v1
Planner                     planner.v4
Implementer                 implementer.v3
Implementation Contract     implementation-contract.v3
Verification Plan           verification-plan.v1
Project runtime             project-runtime.v1
Contract expansion          contract-expansion.v1
Phase 5 controller          phase5-contract-runtime.v1
Phase 6 controller          phase6-runtime-evaluator.v1
Runtime scenario            runtime-scenario.v1
Runtime request             runtime-request.v1
Runtime result              runtime-result.v1
Runtime evidence            runtime-evidence.v1
Contract closure            contract-closure.v1
Blind audit                 blind-audit.v1
Evaluator                   evaluator.v5
Phase 7 controller          phase7-final-gate.v1
Patch proof                 patch-proof.v1
Final acceptance            final-acceptance.v2
Delivery record             delivery-record.v2
Held-out evidence           heldout-evidence.v2
Benchmark isolation         benchmark-isolation.v1
```

## 1. Control plane и data plane

### Agent workspace

```text
<WORKSPACE>/
```

Содержит candidate и agent scratch. Implementer имеет право менять project-файлы только здесь.

### Private Controller plane

```text
<RUN_DIR>/controller_private/
```

Содержит authoritative state:

```text
run_state.json
Task/Plan/Contract/Verification revisions
check registry
self-verify receipts
runtime evidence
contract closure
blind audit / evaluator verdict
quality reconciliation
patch proof
final acceptance
held-out evidence
```

Файл внутри agent-writable `.harness_tmp` не является authoritative evidence.

## 2. Identity и revisions

Каждый accepted artifact связан с revision vector:

```text
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
candidate_rev
runtime_environment_rev
attempt_id
```

`candidate.v1` включает:

```text
recorded baseline SHA
workspace HEAD
changed/new/deleted paths
Git-visible mode
SHA-256 фактических bytes или symlink target
```

`.venv` и `.harness_tmp` не входят в candidate.

Изменение candidate, Contract, Verification Plan или runtime environment инвалидирует downstream evidence согласно `workflow.v6`.

## 3. Step 0 — Intake / Preflight

Controller:

```text
сохраняет raw request
создаёт task-contract.v1
фиксирует source baseline
создаёт managed worktree
копирует разрешённые .worktreeinclude files
создаёт worktree-local project runtime
проверяет toolchain/capabilities
```

Historical benchmark вместо linked worktree получает standalone sanitized repository; подробнее ниже.

## 4. Step 1 — Planner

`planner.v4` исследует current behavior, intended contract, root cause или extension point, consumers, state model, risks и typed evidence plan.

Planner read-only относительно candidate и не получает previous solution/reference/held-out.

## 5. Step 2 — Contract compiler

Controller детерминированно строит:

```text
implementation-contract.v3
verification-plan.v1
```

Contract хранит load-bearing Definition of Done, а Verification Plan связывает каждый requirement с proof profile:

```text
LOCAL_DETERMINISTIC
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

Owner-boundary и capability gates выполняются до writable Implementer.

## 6. Step 3 — Implementer

`implementer.v3` получает Task Contract, compact Planner context, active Contract и trusted capabilities.

Он:

```text
делает smallest complete fix
создаёт/обновляет tests/docs
регистрирует typed checks
сообщает material consumers/risks
закрывает Contract
запускает self verification
```

Open-world discovery расширяет Contract/Verification Plan транзакционно; старые items не удаляются.

Task-local `.venv` пересобирается при dependency/package drift. Runtime-only `.env` восстанавливается, если агент его изменил.

## 7. Step 4 — deterministic checks

Controller независимо запускает project gates и typed task checks на frozen candidate.

Результаты различаются:

```text
CHECK_PASS
CHECK_FAIL
CHECK_TIMEOUT
CHECK_INFRA_ERROR
CHECK_MUTATED_CANDIDATE
```

Green self-verify Implementer не заменяет Controller evidence.

## 8. Step 5 — Runtime Verification

Runtime запускается только если Verification Plan требует observable evidence, которое нельзя доказать local checks.

```text
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

`TEST_EXTERNAL` write требует fresh readback и cleanup/disposable boundary. `PROD_OBSERVE` допускается только с technically enforced read-only wrapper/credential. Runtime не может менять candidate или source.

## 9. Step 6 — Blind Evaluator

`evaluator.v5` работает в две фазы одного fresh thread.

### Phase A

Не видит Planner, Contract, Implementer Report, green checks, runtime evidence и previous findings. Самостоятельно исследует repository/candidate и сохраняет immutable `blind-audit.v1`.

### Phase B

Получает active Contract, Verification Plan, `contract-closure.v1`, deterministic и runtime evidence. Каждый blind finding должен быть retained или dismissed with evidence.

## 10. Repair и semantic replan

### Обычный repair

```text
technical model корректна
candidate локально ошибочен
        ↓
same Implementer thread
```

### Semantic replan

```text
Evaluator отверг technical model
        ↓
rejected patch сохраняется вне workspace
        ↓
candidate сбрасывается до recorded baseline
        ↓
task-specific registry очищается
project runtime пересобирается
        ↓
fresh Planner
        ↓
new Contract / Verification Plan
        ↓
fresh Implementer thread
```

Новый Planner не видит rejected diff; это устраняет anchoring на признанно неверной реализации.

## 11. Step 7 — Final Gate

Final Gate выполняет четыре независимых действия.

### Quality reconciliation

Проверяет, что Step 3–6 относятся к одному final candidate и текущему revision vector.

### Patch reconstruction

`candidate.patch` применяется к чистой verification-копии recorded baseline. Reconstructed `candidate.v1` должен точно совпасть с accepted candidate. Artifact: `patch-proof.v1`.

Так как `candidate.v1` учитывает реальные worktree bytes, Controller перед checkout зеркалирует узкий allowlist effective Git conversion settings source repository (`core.autocrlf`, `core.eol`, `core.safecrlf`, `core.filemode`, `core.symlinks`). Это сохраняет CRLF/LF semantics native Windows без копирования arbitrary Git configuration в private proof repository.

### Immutable acceptance

После patch proof создаётся `final-acceptance.v2`. Он связывает candidate, revisions, stage artifacts и patch SHA-256 и не перезаписывается.

### Delivery

`delivery-record.v2` фиксирует `keep_worktree` или транзакционный `apply_to_source`.

`apply_to_source` использует:

```text
delivery lock
source HEAD/clean recheck
preimage comparison
git apply --check
apply
exact patch/postimage comparison
safe rollback при failure
```

Delivery conflict не делает accepted candidate плохим: source остаётся нетронутым, patch/worktree сохраняются.

Подробности: [Phase 7 Final Gate](PHASE7_FINAL_GATE.md).

## 12. Historical benchmark isolation

Linked Git worktree делит refs/object database с source repository. Hidden exam поэтому использует standalone sanitized repository:

```text
только baseline tree blobs
один detached synthetic commit
нет shared .git metadata
нет refs с reference solution
нет unrelated objects
нет previous attempt artifacts
```

Held-out запускается только после normal Step 0–6 PASS, требует oracle marker и различает:

```text
HELDOUT_PASS
HELDOUT_SEMANTIC_FAIL
HELDOUT_INFRA_ERROR
HELDOUT_TIMEOUT
HELDOUT_MUTATED_CANDIDATE
```

Hidden failure никогда не возвращается агентам текущего trial.

## 13. Security boundary

Execution Broker задаёт role-specific cwd, scratch, environment и policy и честно сообщает:

```text
ENFORCED
ADVISORY
UNAVAILABLE
```

`0.8.0a11` не утверждает универсальный OS-enforced sandbox для любого Controller subprocess. Owner-configured external wrappers обязаны сами иметь scoped credential/environment boundary.

## 14. Что считается завершённым

После Phase 7 весь утверждённый Step 0–7 quality-core подключён к runtime.

Остаются не новые архитектурные фазы, а project/platform capabilities и измерение качества:

```text
готовые browser/DB/1С/Airflow wrappers
универсальный restricted OS runner
Publication Layer commit/push/PR/merge — optional future
несколько clean historical trials
```

Первый обязательный интеграционный checkpoint после Windows self-check — `_90`.
