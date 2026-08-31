# Архитектура Slivin Harness 0.8.0a9 — Phase 6

## Назначение Phase 6

Machine phase id: `phase6-runtime-two-phase-evaluator`.

Phase 1 ввела каноническую Step 0–7 state machine. Phase 2 вынесла authoritative Controller state из agent-writable worktree и добавила Execution Broker. Phase 3 подключила User Task Contract, Planner v4, Implementation Contract v3 и typed Verification Plan. Phase 4 замкнула writable Implementer и deterministic checks. Phase 5 добавила open-world Contract expansion, `.worktreeinclude` и воспроизводимую worktree-local `.venv`. Phase 6 исполняет required runtime proof и делает semantic review действительно независимым.

```text
workflow.v5
run-state.v1
candidate.v1
controller-plane.v1
execution-broker.v1
        ↓
task-contract.v1
planner.v4
implementation-contract.v3
verification-plan.v1
implementer.v3
        ↓
contract-closure.v1
runtime-evidence.v1
blind-audit.v1
evaluator.v5
```

## Реальный pipeline Phase 6

```text
MANIFEST version = 2
        ↓
Step 0 — preflight / Task Contract / worktree / optional project runtime
        ↓
Step 1 — fresh read-only Planner v4
        ↓
Step 2 — Implementation Contract v3 + Verification Plan v1
        ↓
Step 3 — Implementer v3 + transactional discoveries + SELF VERIFY
        ↓
Step 4 — independent deterministic Controller checks
        ↓
Step 5 — runtime scenarios либо explicit SKIPPED
        ↓
Step 6A — blind-audit.v1 без Contract/check framing
        ↓
Step 6B — evaluator.v5 Contract/evidence audit
        ↓
Step 7 — compatibility Final Gate / result handoff
```

## Ownership

### User Task Contract

Controller хранит raw request и `task-contract.v1`. Intake Normalizer извлекает только explicit intent/acceptance/preservation/forbidden/boundaries с точным `source_text`; repository reasoning туда не попадает.

### Planner

Fresh Planner владеет технической гипотезой, но не Definition of Done. Он формирует characterization, bug root cause либо feature extension point, material assumptions, technical acceptance, derived preservation, consumers, conditional State Model, risks и typed Evidence Plan.

### Implementation Contract и Verification Plan

Controller детерминированно компилирует:

```text
Task Contract + Planner load-bearing output
        ↓
implementation-contract.v3
        ↓
verification-plan.v1
```

Explicit user requirements нельзя ослабить. Каждый item имеет typed proof profiles. `LIVE_LOCAL`, `TEST_EXTERNAL` и `PROD_OBSERVE` являются разными routes и не схлопываются в «более высокий риск».

### Implementer

Implementer работает только в managed worktree, создаёт smallest complete candidate, tests/docs, регистрирует typed checks и structured discoveries. Existing Contract immutable; Controller может только добавить `CONSUMER-DISCOVERED-N` или `RISK-DISCOVERED-N` и пересобрать Verification Plan.

### Controller deterministic verification

Self-verify является development feedback. Controller отдельно замораживает candidate и запускает trusted checks. Check может быть `CHECK_PASS`, `CHECK_FAIL`, `CHECK_TIMEOUT`, `CHECK_INFRA_ERROR` или `CHECK_MUTATED_CANDIDATE`. Agent-written test path допускается только через trusted runner; arbitrary authoritative shell-команда от агента запрещена.

### Runtime Verification

Runtime scenarios настраивает owner в `harness.local.toml`. Они являются Controller-owned capabilities, а не model-generated commands.

```text
Verification Plan obligation
        ↓
profile + required capability set
        ↓
один покрывающий RuntimeScenarioConfig
        ↓
runtime-request.v1
        ↓
owner wrapper
        ↓
runtime-result.v1
        ↓
runtime-evidence.v1
```

Capability union разных scenarios не считается единым proof. Command executable разрешается до Implementer; unknown/missing capability блокирует Step 2.

### Contract Closure Record

Перед deterministic/runtime/evaluator этапами Controller нормализует accepted implementation evidence:

```text
contract-closure.v1
candidate_id
Implementation Contract fingerprint
Verification Plan fingerprint
item → VERIFIED / NOT_AFFECTED + evidence
```

Evaluator не получает Implementer Report и его самооценку.

### Blind Evaluator

Один fresh read-only thread работает в две фазы.

Phase A видит только raw task, Task Contract, sanitized preflight, candidate/repository и changed paths. Она не видит Planner, Contract, Implementer Report, checks, runtime evidence, previous findings или hidden oracle. `blind-audit.v1` сохраняется Controller до Phase B.

Phase B видит Controller-normalized Contract, Verification Plan, Closure Record, deterministic и runtime evidence. Каждый blind finding получает `RETAINED` либо `DISMISSED_WITH_EVIDENCE`. Planner reasoning и Implementer prose остаются скрыты.

## Runtime profiles

### LIVE_LOCAL

Запускает current worktree candidate. Optional startup command получает task-local port; Controller polling health до bounded deadline, запускает scenario, сохраняет server logs и останавливает process. Startup output направляется в scratch-файлы, чтобы server не заблокировался заполненным pipe.

### TEST_EXTERNAL

Предназначен только для configured test boundary. PASS требует known initial state и fresh readback. Non-disposable scenario обязан иметь cleanup command; он выполняется даже после timeout/failure, поскольку external mutation могла примениться частично.

### PROD_OBSERVE

Только read-only observation. Scenario обязан иметь `read_only_enforced = true` и не может содержать startup/cleanup mutation lifecycle. Это owner assertion о реальной технической boundary — read-only role/token/wrapper. Harness не считает произвольный command с production superuser безопасным.

## Runtime immutability

До и после Runtime проверяются:

```text
candidate_id
workspace HEAD
source HEAD
source working-tree status
runtime-only exposed files (.env и т.п.)
```

`.harness_tmp/runtime` исключён из candidate. Runtime, изменивший code/source/local config, получает `RUNTIME_MUTATED_CANDIDATE`; `.env` восстанавливается из unchanged source и весь downstream evidence повторяется.

## Runtime result contract

Wrapper обязан вернуть structured result и exit code `0`, если protocol корректно исполнен. Non-zero/timeout/missing JSON — infrastructure error, а не semantic failure. `TEST_EXTERNAL` PASS требует fresh readback; cleanup command success Controller добавляет как cleanup evidence. `PROD_OBSERVE` PASS требует read-only confirmation.

## Private Controller plane

Authoritative artifacts находятся в:

```text
RUN_DIR/controller_private/
```

Включая:

```text
run_state.json
Task/Plan/Contract/Verification revisions
check_registry.json
self_verify_receipts
contract_closure_*.json
private runtime evidence
blind_audit_*.json
evaluation_*.json
```

`.harness_tmp` остаётся scratch и не является authority.

## Evidence identity

Self-verify receipt связывает:

```text
candidate_id
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
runtime_environment_rev
attempt_id
check_registry_digest
```

Runtime evidence дополнительно связано с Verification Plan fingerprint. Blind/Evaluator artifacts относятся к frozen candidate и инвалидируются после любого candidate change.

## Execution boundary

Execution Broker задаёт role-specific cwd, scratch, environment, network declaration и filesystem policy. Он честно различает:

```text
ENFORCED
ADVISORY
UNAVAILABLE
```

Phase 6 не заявляет универсальный OS-enforced sandbox для Controller subprocess на всех платформах. Owner-configured runtime wrappers должны использовать scoped credentials и не печатать secrets в structured result/logs.

## Версии

```text
Harness                     0.8.0a9
Manifest                    version = 2
Workflow                    workflow.v5
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
```

## Что остаётся следующей фазе

```text
universal restricted OS runner для agent-written Controller tests;
готовые typed browser/DB/1С/Airflow wrappers;
independent runtime tools непосредственно в blind Phase A;
clean worktree при semantic replan;
immutable final acceptance + delivery critical section;
benchmark isolation/hardening на нескольких clean trials.
```
