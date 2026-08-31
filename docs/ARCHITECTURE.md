# Архитектура 0.8.0a3 — Phase 2

## 1. Цель Phase 1–2

Phase 1 ввела канонический workflow и versioned Run State. Phase 2 отделяет authoritative Controller state от agent-writable workspace и централизует execution boundaries, не меняя пока смысл model protocols.

До этой версии переходы между Planner, Implementer, checks, Evaluator и held-out были зашиты в `task_runner.py` и повторялись в документации вручную. Это позволяло коду и описанию расходиться.

Теперь источник истины один:

```text
slivin_harness/workflow.py
        ├── docs/WORKFLOW.md
        ├── docs/workflow.v1.json
        ├── workflow_snapshot.json каждого run
        └── RunState transition validation
```

## 2. Канонические компоненты

### `slivin_harness/workflow.py`

Определяет:

```text
Step 0–7
stage ids
stage success codes
общие routing outcomes
agent status enums
allowed stage transitions
invalidation triggers/rules
stage maturity
```

Схема имеет версию и phase id:

```text
workflow.v1
phase2-private-control-plane-and-execution-broker
```

`validate_workflow_definition()` запрещает пропущенный номер этапа, дублирующийся stage id, отсутствующий success transition или trigger без invalidation rule.

### `slivin_harness/run_state.py`

Определяет:

```text
run-state.v1
candidate.v1
```

`RunState` записывает Controller-owned `run_state.json` атомарно и хранит:

```text
workflow/harness version
mode: PRODUCTION | HISTORICAL_BENCHMARK
pipeline profile: FAST | FULL
attempt_id
stage states
revision vector
baseline
current candidate
event history
terminal result
```

### `CandidateIdentity`

Единая identity candidate содержит:

```text
baseline SHA
workspace HEAD
changed paths
file/deletion/symlink state
Git-visible mode
SHA-256 фактических bytes
```

Из identity вычисляется `candidate_id`.

`candidate.v1` фиксирует mode, который Git реально представляет в HEAD-to-working-tree diff. На файловых системах, где Git не наблюдает изменение executable-бита рабочего файла, один `chmod` не считается изменением candidate. Integration test сначала делает capability probe и выполняет mode assertions только когда Git действительно сообщает `100644 → 100755`.

Не входят:

```text
.harness_tmp/**
.venv/**
```

Так Controller может доказать, что self-verify, deterministic checks, Evaluator, held-out и Final Gate наблюдали один candidate.


### `slivin_harness/control_plane.py`

Определяет private control plane каждого run:

```text
RUN_DIR/controller_private/
→ authoritative Controller state

WORKSPACE/.harness_tmp/
→ non-authoritative agent/runtime scratch
```

Private plane использует atomic writes, run-relative path validation и HMAC-protected self-verification receipts. Public `run_state.json` остаётся диагностическим mirror; Controller читает authoritative copy из `controller_private/run_state.json`.

### `slivin_harness/execution.py`

`ExecutionBroker` формирует policy и environment для ролей:

```text
app_server / planner / implementer / controller_check / runtime / evaluator / heldout
```

Policy описывает readable/writable roots, scratch, network и external mutation, а также реальный enforcement level. `ADVISORY` не маскируется под `ENFORCED`; restricted OS runner будет отдельной последующей фазой.

### Controller-owned self-verification receipt

Agent-writable stamp является только claim. Controller повторно вычисляет candidate и выпускает private receipt, связанный с:

```text
candidate_id
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
runtime_env_id
attempt_id
```

Изменение любого измерения делает старый receipt непригодным.

### `task_runner.py`

Текущий executor 0.7.1 отображён на новый Step 0–7 Run State:

```text
Step 0  preflight
Step 1  planner либо FAST skip
Step 2  implementation contract
Step 3  implementer
Step 4  existing repair checks
Step 5  explicit Phase-1 runtime skip
Step 6  evaluator либо FAST skip
Step 7  held-out/result handoff
```

Каждая стадия обязана начинаться разрешённым переходом. Успешный result code проверяется против конкретного stage.

### Agent protocol modules

Статусы больше не дублируются строками в нескольких местах:

```text
planner.py     → PlannerStatus
implementer.py → ImplementerStatus
evaluator.py   → EvaluatorStatus
```

Сами schemas/prompts пока остаются:

```text
planner.v3
implementer.v1
implementation-contract.v2
evaluator.v4
```

## 3. Понятная схема выполнения

```text
manifest
  ↓
RunState.create
  ↓
Step 0 preflight
  ↓
Step 1 planner / compatibility skip
  ↓
Step 2 contract
  ↓
Step 3 implementer + current self-verify
  ↓
Step 4 checks; failure → same Implementer repair
  ↓
Step 5 explicit runtime skip in Phase 1
  ↓
Step 6 evaluator; finding → repair; replan → Planner
  ↓
Step 7 held-out if benchmark + result delivery
```

Точный граф генерируется в [WORKFLOW.md](WORKFLOW.md).

## 4. Revision vector

`run_state.json` содержит:

```text
task_contract
plan
implementation_contract
verification_plan
candidate
runtime_environment
```

В текущем compatibility executor реально изменяются:

```text
plan
implementation_contract
candidate
runtime_environment
```

`task_contract` и `verification_plan` пока могут оставаться `null`, потому что их новые protocols относятся к Phase 3. Наличие полей заранее фиксирует их место в общей state machine.

## 5. Invalidation model

Канонические triggers определены один раз:

```text
TASK_CONTRACT_CHANGED
REPLAN_REQUIRED
CONTRACT_EXPANDED
CHECK_REGISTERED
CANDIDATE_CHANGED
DEPENDENCY_MANIFEST_CHANGED
RUNTIME_ENV_CHANGED
RUNTIME_PROFILE_CHANGED
SOURCE_CHANGED
HIDDEN_GRADER_CHANGED
CANDIDATE_CHANGED_AFTER_EVALUATION
```

При invalidation устаревший stage больше не хранит активный PASS: result/outcome/artifacts очищаются, stage становится `INVALIDATED`, а подробности остаются в event log.

Полная таблица — в [WORKFLOW.md](WORKFLOW.md).

## 6. Stage state и routing outcome — разные понятия

Stage state показывает состояние записи:

```text
NOT_STARTED
IN_PROGRESS
PASSED
SKIPPED
STOPPED
FAILED
INVALIDATED
```

Routing outcome объясняет, куда идёт orchestration:

```text
PASS
REPAIR
REPLAN
BLOCKED
NEEDS_USER_DECISION
INVALID
```

Например:

```text
Evaluator FINDINGS
→ stage state FAILED
→ outcome REPAIR
→ следующий stage Implementer
```

Infrastructure или protocol corruption:

```text
→ outcome INVALID/BLOCKED
```

а не product finding.

## 7. Final Gate в Phase 1

Step 7 теперь:

1. фиксирует final `candidate_id`;
2. для benchmark повторно убеждается, что held-out не изменил candidate;
3. строит `candidate.patch` и его SHA-256;
4. убеждается, что packaging не изменил managed candidate;
5. создаёт `final_acceptance.json`;
6. выполняет текущий result handoff и пишет `delivery_record.json`;
7. повторно убеждается, что delivery не изменил managed candidate;
8. выдаёт отдельный `HARNESS_TASK_PASS` или `HARNESS_BENCHMARK_PASS`;
9. записывает terminal PASS в Run State.

Полный будущий Final Gate с patch reconstruction и delivery critical section относится к последующей фазе.

## 8. Generated documentation

Команда:

```bash
./py tools/render_workflow_docs.py
```

создаёт:

```text
docs/WORKFLOW.md
docs/workflow.v1.json
```

Проверка:

```bash
./py tools/render_workflow_docs.py --check
./py tools/check_docs_sync.py
```

`check_docs_sync.py` также проверяет версии:

```text
0.8.0a3
workflow.v1
run-state.v1
candidate.v1
planner.v3
implementer.v1
implementation-contract.v2
evaluator.v4
```

## 9. Что намеренно не реализовано в Phase 1

Phase 1 не утверждает готовность целевой архитектуры целиком. Пока отсутствуют:

```text
User Task Contract normalizer/alignment
Verification Plan compiler
private Controller control plane
Execution / Capability Broker
new Planner protocol
open-world Contract transaction
runtime executor
restricted Controller runner
two-phase Evaluator
clean-worktree semantic replan
new no-progress/watchdog policy
publication automation
```

Это защищает от большого недоказуемого rewrite: сначала фиксируется state ownership, затем по одной фазе меняются capabilities и model contracts.


Foundation protocol versions: `controller-plane.v1`, `execution-broker.v1`.
