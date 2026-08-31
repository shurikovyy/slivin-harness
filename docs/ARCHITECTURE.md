# Архитектура Slivin Harness 0.8.0a6 — Phase 4

## Назначение Phase 4

Machine phase id: `phase4-implementer-controller-verification`.

Phase 1 ввела каноническую Step 0–7 state machine. Phase 2 вынесла authoritative Controller state из agent-writable worktree и централизовала execution policy. Phase 3 подключила User Task Contract, Planner v4, Implementation Contract v3 и typed Verification Plan. Phase 4 связывает этот фундамент с writable Implementer v2, Controller-private typed check registry, activity watchdog и независимыми deterministic Controller checks.

```text
workflow.v3
run-state.v1
candidate.v1
controller-plane.v1
execution-broker.v1
        ↓
task-contract.v1
planner.v4
implementation-contract.v3
verification-plan.v1
```

## Реальный pipeline Phase 4

```text
MANIFEST version = 2
        ↓
project/worktree/toolchain preflight
        ↓
USER TASK CONTRACT
        ↓
Planner v4 или FAST compatibility skip
        ↓
Implementation Contract v3
        ↓
Verification Plan v1
        ↓
owner-boundary gate
        ↓
capability gate
        ↓
Implementer v2
        ↓
typed check registration + revision-bound SELF VERIFY
        ↓
independent Controller deterministic checks
        ↓
Runtime SKIPPED для local-only proof
или BLOCKED до Implementer для недоступного runtime proof
        ↓
Evaluator v4
        ↓
Final Gate compatibility executor
```

## Ownership

### User Task Contract

Владелец: Controller.

Intake Normalizer возвращает structured proposal, после чего Controller проверяет:

```text
source_text является точным substring raw request;
READY имеет intent и acceptance;
NEEDS_USER_DECISION имеет прямое противоречие и reason;
fingerprint соответствует содержимому.
```

Authoritative artifact хранится в private Controller plane.

### Planner artifact

Владелец semantic reasoning: fresh Planner.

Владелец валидации и сохранения: Controller.

Planner получает raw request, Task Contract и repository. Он не получает reference solution или hidden grader.

### Implementation Contract

Владелец: Controller.

Он строится детерминированно из:

```text
Task Contract explicit acceptance/preservation
+
Planner technical acceptance/preservation/consumers/state/risks/docs
```

Planner не может удалить explicit user requirements.

### Verification Plan

Владелец: Controller.

Он связывает каждый Contract item с typed proof profiles и capability set. План привязан к fingerprint Implementation Contract.

## Task Contract

Формат: **task-contract.v1**.

```text
raw_user_request
raw_request_sha256
explicit_intent[]
explicit_acceptance[]
explicit_preservation[]
explicit_forbidden[]
owner_boundaries[]
non_goals[]
ambiguities[]
fingerprint
```

Каждый explicit row:

```json
{
  "claim": "Нормализованная формулировка",
  "source_text": "точный фрагмент исходного запроса"
}
```

Normalizer не имеет repository и не определяет technical scope.

## Planner v4

Формат Planner содержит только поля с downstream consequence:

```text
characterization
diagnosis
assumptions
technical_contract
affected_consumers
state_model
risks
evidence_plan
documentation
owner_boundary_assessment
unknowns
```

Для BUG необходим root cause. Для FEATURE — extension point и design constraints. READY с LOW-confidence bug diagnosis запрещён. Assumption, сужающее compatibility, требует HIGH confidence.

## Implementation Contract v3

Допустимые item types:

```text
acceptance
preservation
state
consumer
risk
documentation
```

Каждый item:

```text
id
type
source
requirement
required_proof
allow_not_affected
```

`NOT_AFFECTED` разрешён только consumer. Лимит 14 является soft review threshold: material obligation не отбрасывается ради числа.

## Typed proof model

Planner выдаёт отдельный proof target:

```text
claim
level
capabilities
```

Contract compiler сохраняет:

```text
claims[]
profiles[]
```

Каждый profile:

```text
level
capabilities
```

Это необходимо, потому что `LIVE_LOCAL`, `TEST_EXTERNAL` и `PROD_OBSERVE` — разные execution routes, а не одна линейная шкала риска.

## Verification Plan v1

```text
implementation_contract_fingerprint
requirements[]
project_gates[]
task_checks[]
required_capabilities[]
runtime_profiles[]
runtime_required
fingerprint
```

Для каждого non-local profile компилятор автоматически добавляет executor capability:

```text
LIVE_LOCAL      → LIVE_LOCAL_RUNTIME
TEST_EXTERNAL   → TEST_EXTERNAL_RUNTIME
PROD_OBSERVE    → PROD_OBSERVE_RUNTIME
```

Plan validator сверяет summaries с фактическими requirement profiles. Подмена `required_capabilities` с пересчитанным fingerprint всё равно отклоняется.

## Capability gate

Phase 4 предоставляет существующие local capabilities и подключает их к typed check registry:

```text
GIT
PROJECT_PYTHON
NODE
JEST
DOCS_SYNC
```

Объявление будущего runtime capability в config не считается executor implementation. Поэтому required Browser/test-external/prod-observe proof блокируется до Implementer.

## Private Controller plane

Authoritative artifacts продолжают храниться вне agent-writable worktree:

```text
RUN_DIR/controller_private/
```

Agent scratch в `.harness_tmp` не является доказательством. Self-verify receipt привязывается к revision vector и candidate identity.

## Версии

```text
Harness                     0.8.0a6
Manifest                    version = 2
Workflow                    workflow.v3
Run State                   run-state.v1
Candidate                   candidate.v1
Controller plane            controller-plane.v1
Execution Broker            execution-broker.v1
Task Contract               task-contract.v1
Planner                     planner.v4
Implementer                 implementer.v2
Implementation Contract     implementation-contract.v3
Verification Plan           verification-plan.v1
Evaluator                   evaluator.v4
```

## Implementer v2 и deterministic Controller verification

Authoritative check registry, revision bindings, self-verification receipts и Controller check records находятся в `RUN_DIR/controller_private`. Agent-writable `.harness_tmp` остаётся scratch и не является источником окончательной истины. Implementer может предложить typed test path или trusted check ID, но не произвольную authoritative shell-команду.

Self-verification receipt относится одновременно к candidate, revision vector, runtime environment, attempt и check-registry digest. Controller независимо повторяет проверки, фиксирует candidate до/после suite и различает behavioral failure, timeout, infrastructure error и mutation candidate. Активная работа Implementer регулируется inactivity watchdog, а не коротким total wall-clock deadline.

## Границы Phase 4 alpha

Phase 4 не объявляет полностью готовыми автоматическую перекомпиляцию active Contract/Verification Plan из discoveries, worktree-local `.venv` bootstrap/rebuild, universal OS-enforced Controller subprocess sandbox, runtime executors, двухфазный Evaluator, clean-worktree semantic replan или новую Final Gate delivery transaction. Execution Broker сохраняет фактический статус `ENFORCED` / `ADVISORY` / `UNAVAILABLE` вместо ложного заявления об изоляции.
