# Модель качества Slivin Harness 0.8.0a5 — Phase 3

## Основная формула

```text
explicit user intent
→ доказанная technical model
→ load-bearing Definition of Done
→ typed proof requirements
→ implementation
→ independent verification
```

Phase 3 реализует первые четыре звена как реальные structured artifacts.

## Что теперь нельзя потерять между этапами

### User acceptance

Explicit acceptance копируется в `ACCEPTANCE-1` напрямую из `task-contract.v1`. Planner может добавить technical mapping, но не заменить product outcome.

### User preservation

Explicit preservation, forbidden и owner boundaries попадают в `PRESERVE-1`. Они не становятся необязательными заметками Planner.

### Consumers и risks

Каждый material consumer и risk получает отдельный Contract item с собственным required proof. Это защищает от ситуации:

```text
Planner нашёл проблему
→ Implementer забыл её при написании patch
```

### Stateful semantics

Если Planner считает задачу stateful, один `STATE-1` объединяет representation, authority, lifecycle и reachable boundaries. Отдельные REP/AUTH/LIFE реестры не возвращаются.

## Typed proof вместо свободного текста

`verification-plan.v1` отвечает на два вопроса:

```text
каким уровнем нужно доказывать requirement?
какие capabilities для этого обязательны?
```

Уровни:

```text
LOCAL_DETERMINISTIC
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

Requirement может иметь несколько профилей одновременно. Это важно: Browser flow и fresh external readback не являются взаимозаменяемыми доказательствами.

## Fail-closed capability gate

Если required proof невозможно исполнить, Harness блокируется до Implementer:

```text
REQUIRED_CAPABILITY_MISSING
```

Это лучше, чем:

```text
Implementer сделал candidate
→ unit tests зелёные
→ обязательный runtime proof тихо пропущен
```

## Что Phase 3 доказывает

Phase 3 механически доказывает:

```text
raw request сохранён;
explicit claims имеют verbatim source;
Planner artifact соответствует planner.v4;
BUG/FEATURE diagnosis структурирован;
Task Contract не потерян при Contract compilation;
Contract items имеют typed proof;
Verification Plan согласован с Contract;
capability summary не подменён;
owner conflict и missing capability останавливают writable pipeline.
```

## Что Phase 3 ещё не доказывает

Пока не реализованы:

```text
полноценный runtime proof;
restricted OS-level execution всех agent-written tests;
open-world Contract expansion во время Implementer;
двухфазная blind evaluation;
clean-worktree semantic replan;
новая result-delivery transaction.
```

Поэтому `0.8.0a5` — промежуточная alpha-фаза, а не завершённый Quality Core.

## Compatibility layers

```text
implementer.v1
evaluator.v4
manifest version = 2
```

остаются для постепенного внедрения. Их наличие не означает, что утверждённые будущие Step 3–7 контракты уже полностью выполнены.

## Anti-monster rules

1. Поле существует только при downstream consequence.
2. Task Contract не содержит repository reasoning.
3. Planner context не превращается целиком в obligations.
4. Contract item count имеет soft threshold, но correctness не обрезается.
5. Runtime включается по required proof, а не по общей метке риска.
6. Missing executor блокирует задачу честно.

## Критерий Phase 3

```text
task-contract.v1 valid
planner.v4 valid
implementation-contract.v3 valid
verification-plan.v1 valid
owner/capability gate выполнен
pipeline integration tests PASS
docs-sync PASS
```
