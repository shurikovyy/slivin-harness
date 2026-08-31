# Модель качества Slivin Harness 0.8.0a8 — Phase 5

## Основная формула

```text
explicit user intent
→ доказанная technical model
→ load-bearing Definition of Done
→ typed proof requirements
→ smallest complete implementation
→ reproducible project runtime
→ independent Controller verification
```

Phase 5 не добавляет reviewer. Она гарантирует, что уже найденные требования, проверки и runtime действительно становятся активным Definition of Done до `COMPLETE`.

## Что нельзя потерять

### User acceptance и preservation

`task-contract.v1` остаётся высшим product contract. Explicit acceptance копируется в `ACCEPTANCE-1`, explicit preservation/forbidden — в `PRESERVE-1`. Planner может добавить техническую конкретизацию, но не заменить исходный outcome.

### Consumers, state и risks

Material consumer/risk получает отдельный Contract item с собственным typed proof. Stateful change использует один компактный `STATE-1`, объединяющий representation, authority, lifecycle и reachable boundaries.

### Discoveries после Planner

Implementation Contract — open-world minimum. Если Implementer находит нового consumer/risk, `implementer.v3` обязан вернуть structured discovery. Controller транзакционно:

```text
валидирует discovery
→ расширяет Contract
→ пересобирает Verification Plan
→ повторяет owner/capability gates
→ инвалидирует прежний self-verify
→ возвращает тому же Implementer новую revision
```

Новый typed check проходит тот же путь. Candidate может не измениться, но старое evidence всё равно устаревает, потому что Definition of Done изменился.

## Typed proof model

`verification-plan.v1` отвечает:

```text
какой observable claim доказывается?
какой proof profile нужен?
какие capabilities обязательны?
```

Профили:

```text
LOCAL_DETERMINISTIC
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

Несколько профилей могут быть обязательны одновременно. Browser и fresh external readback не заменяют друг друга.

## Fail-closed gates

До продолжения writable работы Controller повторно проверяет:

```text
owner boundaries;
required capabilities;
Contract/Plan consistency.
```

Новый runtime requirement при отсутствующем executor приводит к:

```text
REQUIRED_CAPABILITY_MISSING
```

а не к тихому снижению proof до unit-test.

## Reproducible Python evidence

При настроенном project runtime каждая managed worktree получает собственную `.venv` от configured bootstrap Python и dependency declarations.

Controller записывает:

```text
bootstrap/project Python versions;
dependency declaration digest;
pip check;
pip freeze --all digest;
runtime_id.
```

Перед каждым `COMPLETE` проверяется drift. Изменённый `requirements.txt` или скрытый `pip install` вызывает clean rebuild, новую runtime revision и повторный self-verify. Harness Python не используется как silent fallback для project tests.

## Runtime-only local files

`.worktreeinclude` является repository-owned policy для ignored runtime files. Такие файлы:

```text
копируются в worktree;
не входят в candidate/patch;
приватно fingerprinted;
восстанавливаются Controller при изменении;
требуют нового self-verify после восстановления.
```

Это исключает ложный PASS, полученный только изменением `.env`.

## Независимые Controller checks

Self-verification остаётся development feedback. Controller отдельно запускает trusted checks, классифицирует:

```text
CHECK_PASS
CHECK_FAIL
CHECK_TIMEOUT
CHECK_INFRA_ERROR
CHECK_MUTATED_CANDIDATE
```

и связывает evidence с теми же candidate, Contract, Verification Plan, runtime, attempt и check-registry digest.

## Anti-monster rules

1. Отдельное поле существует только при downstream consequence.
2. Task Contract не содержит repository reasoning.
3. Planner context не превращается целиком в obligations.
4. Contract size 14 — soft review threshold, а не correctness cutoff.
5. Duplicate discoveries idempotent.
6. Runtime включается по required proof, а не по общей метке риска.
7. Нет нового agent layer для Contract expansion или runtime reconciliation.
8. Нереализованный executor блокирует задачу честно.

## Что Phase 5 доказывает

```text
Task Contract и Planner artifacts валидны;
active Contract содержит user + Planner + discovered obligations;
Verification Plan соответствует active Contract и typed checks;
owner/capability gates повторены после expansion;
self-verify receipt относится к активным revisions;
project tests используют worktree-local runtime, если он настроен;
hidden package drift не переживает clean rebuild;
runtime-only files не становятся candidate, а сравниваются по private keyed HMAC;
Controller checks независимо проходят на frozen candidate.
```

## Что Phase 5 ещё не доказывает

Пока не реализованы полностью:

```text
universal OS-enforced execution agent-written tests;
LIVE_LOCAL / TEST_EXTERNAL / PROD_OBSERVE scenarios;
two-phase Blind Evaluator;
clean-worktree semantic replan;
final delivery critical section.
```

Поэтому `0.8.0a8` — alpha quality-core, а не завершённый production orchestrator.

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
evaluator.v4
workflow.v4
```
