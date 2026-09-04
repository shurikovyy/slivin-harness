# Модель качества Slivin Harness 0.8.0a13 — Phase 7

## Основная формула

```text
explicit user intent
→ statically executable manifest toolchain
→ доказанная technical model
→ load-bearing Definition of Done
→ typed proof routes
→ smallest complete implementation
→ reproducible project runtime
→ independent deterministic verification
→ required observable runtime proof
→ blind semantic challenge
→ one-candidate Final Gate
→ reconstructed patch
→ safe result delivery
```

Ни один слой не заменяет следующий:

```text
SELF VERIFY
≠ Controller deterministic PASS
≠ Runtime PASS/SKIPPED
≠ Evaluator PASS
≠ Final Gate PASS
```

## Static Toolchain Preflight

`static-toolchain-preflight.v1` доказывает только, что manifest command
templates однозначно раскрываются, required executables и известные inputs
доступны, а lightweight tool probes проходят до agent stages. Для projected
runtime probes действуют full-tree pre/post integrity guard и JIT restore.
Preflight не запускает tests/hidden oracle, не доказывает product correctness и
не заменяет post-plan capability gate для требований, появившихся из Planner и
Verification Plan.

Jest config выполняется как project code даже при `--showConfig`. Поэтому второй
независимый guard сравнивает canonical candidate identity до/после preflight:
tracked mutation, deletion или untracked addition отменяет любое зелёное probe
evidence. Runtime-only exclusions сохраняются. Raw probe output остаётся private,
а public artifact содержит только typed diagnostic. Explicit Jest config и cwd
auto-discovery поддерживаются без запуска tests.

## Что доказывает каждый слой

### User Task Contract

`task-contract.v1` сохраняет явно сказанные intent, acceptance, preservation, forbidden и owner boundaries с verbatim source text. Он не доказывает technical root cause.

Разные conditions/scopes не являются direct contradiction. `READY` по-прежнему требует пустые
`ambiguities` и `reason`; если модель нарушила это или другое semantic правило artifact, Controller
возвращает validation feedback в тот же Intake-thread и принимает только исправленный полный объект.
Две bounded repair-попытки относятся к protocol correction, а не к product implementation loop.

### Planner

`planner.v4` характеризует current behavior, existing contract, root cause/extension point, consumers, state model, risks и evidence plan. Planner не доказывает корректность future candidate.

### Implementation Contract

`implementation-contract.v3` хранит минимальный обязательный Definition of Done:

```text
ACCEPTANCE
PRESERVATION
STATE optional
CONSUMERS
RISKS
DOCS optional
```

Каждый item имеет typed required proof в `verification-plan.v1`. Contract open-world: новый material consumer/risk добавляется, а старые obligations не ослабляются.

### Implementer self verification

`implementer.v3` использует trusted check registry и worktree-local project runtime, чтобы исправляться до сдачи. Controller-private receipt связан с candidate, revisions, runtime environment, attempt и registry digest.

Self-verify остаётся assertion builder-а, а не финальным authority.

### Controller deterministic checks

Step 4 независимо запускает local machine assertions на frozen candidate. Infrastructure errors, timeout, assertion failure и mutation не смешиваются.

### Runtime Verification

Step 5 выполняет только proof profiles, которые local checks не покрывают:

```text
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

`TEST_EXTERNAL` требует fresh readback и cleanup/disposable boundary. `PROD_OBSERVE` требует technically scoped read-only access. Local-only task получает explicit `RUNTIME_VERIFICATION_SKIPPED`.

### Blind Evaluator

`evaluator.v5` сначала независимо исследует candidate без Contract/check framing и фиксирует `blind-audit.v1`. Затем проверяет active Contract, `contract-closure.v1`, deterministic и runtime evidence. Green test не является authority: Evaluator ищет false-green fixtures, missed consumers и reachable boundary gaps.

### Final Gate

`phase7-final-gate.v1` доказывает:

```text
Step 3–6 относятся к одному candidate
использованы текущие Contract/Verification/Runtime revisions
candidate не изменился после Evaluation
candidate.patch воспроизводит тот же candidate с baseline
final acceptance создан после patch proof
result delivery не смешала accepted patch с user changes
```

## Evidence identity

Каждый authoritative artifact связан с:

```text
candidate_id
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
runtime_environment_rev
attempt_id
```

Изменение любой load-bearing оси делает соответствующее downstream evidence stale.

## Candidate identity

`candidate.v1` учитывает:

```text
baseline SHA
workspace HEAD
changed/new/deleted paths
Git-visible mode
file bytes / symlink target
```

Не учитывает Harness/runtime artifacts:

```text
.venv/
.harness_tmp/
```

## Contract closure

`contract-closure.v1` нормализует Controller-accepted status каждого item:

```text
VERIFIED
NOT_AFFECTED — только consumer с evidence
```

Implementer prose не передаётся Evaluator как authority.

## Repair vs semantic replan

```text
technical model корректна,
candidate ошибочен
→ same Implementer repair
```

```text
technical model отвергнута
→ rejected patch сохраняется
→ candidate очищается до baseline
→ task checks/runtime attempt сбрасываются
→ fresh Planner
→ new Contract/Verification Plan
→ fresh Implementer
```

Это препятствует anchoring на признанно неверной реализации.

## Final Gate invariants

### Quality reconciliation

Step 3–6 должны быть PASSED/SKIPPED допустимым способом и иметь один `candidate_id` и текущие revisions.

### Patch proof

```text
baseline + candidate.patch
→ reconstructed candidate.v1
→ exact equality with accepted candidate.v1
```

Reconstruction использует тот же effective Git worktree conversion policy, что и source candidate, для ограниченного списка content/mode settings. Поэтому exact equality остаётся строгой и одновременно переносимой между Windows checkout с CRLF и POSIX checkout с LF.

Artifact: `patch-proof.v1`.

### Immutable acceptance

`final-acceptance.v2` создаётся один раз после patch proof. Он содержит artifact bindings и patch SHA-256, но не дублирует reasoning/logs.

### Delivery

`delivery-record.v2` отделяет качество candidate от доставки.

```text
RESULT_DELIVERY_PASS
RESULT_DELIVERY_BLOCKED
RESULT_DELIVERY_FAIL
```

Dirty/changed source приводит к BLOCKED, а не к перезаписи пользовательских файлов. Частичный apply допускает только safe rollback по preimage/postimage invariants.

## Historical benchmark quality

`benchmark-isolation.v1` требует standalone repository без shared refs/object database. Historical trial всегда использует `keep_worktree`.

Hidden grader запускается только после normal pipeline PASS и создаёт `heldout-evidence.v2`:

```text
HELDOUT_PASS
HELDOUT_SEMANTIC_FAIL
HELDOUT_INFRA_ERROR
HELDOUT_TIMEOUT
HELDOUT_MUTATED_CANDIDATE
```

Semantic failure требует oracle marker. Held-out feedback не возвращается Planner/Implementer/Evaluator текущего trial.

## Anti-monster rules

1. Отдельный field/item существует только при downstream consequence.
2. Controller компилирует Contract/Verification Plan детерминированно; новый LLM Contract Reviewer отсутствует.
3. Runtime запускается только по typed proof requirement.
4. Один Evaluator thread выполняет две фазы, а не два reviewer-а.
5. Planner и Implementer prose скрыты от Evaluator.
6. Contract size 14 — soft review threshold; material obligation не отбрасывается.
7. Duplicate discoveries idempotent.
8. Нет universal E2E для каждого task.
9. Infrastructure failure не становится product evidence.
10. Advisory isolation не называется enforced.
11. Final Gate не делает нового semantic review.
12. Held-out — exam, а не repair tool.

## Что Phase 7 доказывает

```text
active Task/Plan/Contract/Verification revisions согласованы;
Contract items имеют Controller-normalized closure;
self-verify/Controller/runtime/evaluator evidence связано с candidate;
semantic replan не показывает fresh agents rejected diff;
required runtime proof выполнен или явно не требуется;
Evaluator blind phase предшествует Contract/check framing;
Final Gate принимает только один неизменённый candidate;
patch реконструирует именно этот candidate;
source delivery не смешивает accepted result с parallel user changes;
historical benchmark не раскрывает другие refs/objects/held-out feedback.
```

## Что Phase 7 не доказывает

```text
универсальную OS sandbox-изоляцию любого subprocess;
безопасность owner wrapper с чрезмерными credentials;
наличие готового browser/1С/DB/Airflow wrapper без project config;
отсутствие любого неизвестного defect во всём repository;
универсальную надёжность Harness по одному historical trial;
успешный CI/deployment/production rollout;
```

Практическая надёжность измеряется по нескольким clean trials и реальным escaped defects. После Windows self-check `0.8.0a13` первый такой checkpoint — historical `_90`.

## Версии

```text
manifest version = 2
task-contract.v1
planner.v4
implementer.v3
implementation-contract.v3
verification-plan.v1
project-runtime.v1
contract-expansion.v1
runtime-scenario.v1
runtime-request.v1
runtime-result.v1
runtime-evidence.v1
contract-closure.v1
blind-audit.v1
evaluator.v5
workflow.v6
run-state.v1
candidate.v1
controller-plane.v1
execution-broker.v1
phase5-contract-runtime.v1
phase6-runtime-evaluator.v1
phase7-final-gate.v1
patch-proof.v1
final-acceptance.v2
delivery-record.v2
heldout-evidence.v2
benchmark-isolation.v1
```
