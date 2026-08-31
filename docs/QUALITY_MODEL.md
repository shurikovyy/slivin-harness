# Модель качества Slivin Harness 0.8.0a9 — Phase 6

## Основная формула

```text
explicit user intent
→ доказанная technical model
→ load-bearing Definition of Done
→ typed proof routes
→ smallest complete implementation
→ reproducible project runtime
→ independent deterministic verification
→ required observable runtime proof
→ blind semantic challenge
```

Ни один слой не заменяет следующий.

```text
SELF VERIFY
≠ Controller deterministic PASS
≠ Runtime PASS
≠ Evaluator PASS
```

## Что доказывает каждый слой

### User Task Contract

Фиксирует то, что пользователь сказал явно. Planner не может заменить acceptance или ослабить preservation. Task Contract не доказывает technical root cause.

### Planner

Доказывает current behavior, intended contract и technical model. Для bug нужен root cause; для feature — current extension point/design constraints. Planner не доказывает, что future candidate корректен.

### Implementation Contract

Сохраняет минимальный набор требований, которые Implementer не имеет права забыть:

```text
ACCEPTANCE
PRESERVATION
STATE optional
CONSUMERS
RISKS
DOCS optional
```

Каждый item имеет собственный typed `required_proof`. Contract open-world: material discovery расширяет Definition of Done.

### Implementer SELF VERIFY

Помогает агенту исправляться до сдачи. Receipt связан с candidate, Contract/Verification revisions, runtime environment и check registry. Это assertion builder-а, а не финальный authority.

### Controller deterministic checks

Независимо запускают локальные machine assertions на frozen candidate. Controller различает product failure, timeout, infrastructure error и mutation. Green suite доказывает только запущенные assertions.

### Runtime Verification

Запускается только для proof profiles, которые local checks честно не покрывают. Он проверяет observable outcome current candidate и сохраняет `runtime-evidence.v1`.

```text
LIVE_LOCAL
→ current worktree application/local integration

TEST_EXTERNAL
→ configured test boundary + fresh readback + cleanup/disposable

PROD_OBSERVE
→ technically scoped read-only observation
```

Отсутствие runtime requirement фиксируется как explicit `RUNTIME_VERIFICATION_SKIPPED`, а не как потерянный этап.

### Blind Evaluator

Phase A независимо ищет пропуски без Planner/Contract/check framing. Phase B проверяет active Contract и качество Controller evidence. Green test не является authority: Evaluator обязан искать false-green fixtures/assertions и missed consumers.

### Final Gate

Следующая фаза должна доказать, что все accepted artifacts относятся к одному final candidate и безопасно доставить ровно этот result.

## Runtime proof invariants

### Capability routing

Requirement считается покрытым только одним scenario нужного profile, который содержит весь capability set. Нельзя сложить `LOCAL_APP` одного scenario и `BROWSER_DOM` другого.

### Structured result

Scenario wrapper должен:

```text
прочитать runtime-request.v1
выполнить observable scenario
записать runtime-result.v1
завершиться exit code 0
```

Non-zero/timeout/missing result — infrastructure error. Semantic failure кодируется в structured status.

### Candidate/source immutability

Runtime не может менять candidate, source checkout или runtime-only local files. Mutation всегда инвалидирует downstream evidence.

### TEST_EXTERNAL

HTTP 200 недостаточен. PASS требует:

```text
known initial state
→ action/write
→ fresh authoritative readback
→ cleanup/readback либо disposable environment
```

### PROD_OBSERVE

Read-only должен быть технически ограничен owner capability. Prompt «не пиши production» не является границей.

## Evaluator invariants

### Blindness

Phase A не видит:

```text
Planner
Implementation Contract
Implementer Report
Controller checks
runtime evidence
previous findings
hidden grader
```

### Immutable blind audit

`blind-audit.v1` сохраняется Controller до раскрытия Phase B. Каждый finding обязан быть retained либо dismissed with evidence; он не может просто исчезнуть после показа зелёных тестов.

### Findings

Blocking severity только:

```text
HIGH
MEDIUM
```

Категории:

```text
DEFECT
CONSUMER
RISK
EVIDENCE
DOCS
```

Finding требует concrete reachability/failure mode/evidence/action. Naming/style preference не блокирует acceptance.

## Contract Closure Record

Evaluator получает не свободный self-report Implementer, а Controller-normalized `contract-closure.v1`:

```text
item_id
VERIFIED / допустимый NOT_AFFECTED
accepted evidence
candidate + Contract + Verification identities
```

Это не доказывает semantic достаточность evidence; именно Phase B её проверяет.

## Repair vs replan

```text
technical model корректна, candidate ошибочен
→ same Implementer repair

technical model неверна
→ REPLAN_REQUIRED
```

Новый consumer/risk обычно repair через Contract expansion, а не полный replan.

## Anti-monster rules

1. Отдельное поле существует только при downstream consequence.
2. Runtime запускается только по typed proof requirement.
3. Три runtime profile достаточно; новые не добавляются без evidence.
4. Один fresh Evaluator, а не два reviewer-а: две фазы одного thread.
5. Planner/Implementer prose скрыт от Evaluator.
6. Contract size 14 — soft review threshold, material item не отбрасывается.
7. Duplicate discoveries idempotent.
8. Нет universal E2E для каждого task.
9. Infrastructure failure не превращается в product evidence.
10. Advisory isolation не называется enforced.

## Что Phase 6 доказывает

```text
active Contract/Verification Plan валидны;
все Contract items имеют Controller-normalized closure;
local-only proof явно пропускает Runtime;
required runtime proof исполняется покрывающим scenario;
runtime evidence связано с frozen candidate;
TEST_EXTERNAL PASS имеет fresh readback и cleanup/disposable boundary;
PROD_OBSERVE требует read-only assertion;
runtime не изменил candidate/source/local config;
Phase A выполнена без Planner/Contract/check framing;
Phase B disposition-ит каждое blind finding;
Evaluator PASS не содержит material findings.
```

## Что Phase 6 ещё не доказывает

```text
универсальную OS sandbox-изоляцию любых Controller subprocess;
безопасность owner wrapper, если он фактически использует чрезмерные credentials;
готовность browser/1С/DB/Airflow capability без project configuration;
полную независимость semantic replan worktree;
атомарную final delivery transaction;
универсальную надёжность Harness по одному benchmark trial.
```

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
workflow.v5
```
