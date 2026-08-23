# Модель качества Slivin Harness

## 1. Почему green tests недостаточны

Исторический development cycle показал несколько отдельных классов false confidence:

1. Implementer исправил direct scenario, но shared consumer остался неверным.
2. Новый regression test оказался green уже на broken baseline.
3. Held-out grader проверял `excluded_ids: []`, хотя реальный backend contract допускал отсутствие поля.
4. Evaluator мог формально проверить backend token support, но не локальный stage guard consumer.
5. Синтетически возможная комбинация полей выглядела defect, но lifecycle показывал, что normal runtime path её не создаёт.

Поэтому текущая модель качества — это не «больше тестов», а явная state/evidence model.

---

## 2. Current Contract — `CC-*`

Planner сначала характеризует существующее поведение.

Пример:

```text
CC-001
state: ACTIVE
behavior: ...
evidence:
- source code
- existing test
compatibility_notes: ...
```

Current contract отделяется от желаемого target и assumptions.

---

## 3. Assumptions — `A-*`

Структура:

```text
id
claim
evidence
confidence = HIGH | MEDIUM | LOW
narrows_existing_behavior = true | false
```

Критическое правило:

```text
narrows_existing_behavior = true
+
confidence < HIGH
→ plan validation failure / replan
```

Нельзя превращать технически удобное предположение в новый compatibility contract.

---

## 4. Consumers — `CONS-*`

Shared change автоматически повышает требование к impact analysis.

Для materially affected consumer недостаточно подтвердить:

```text
backend endpoint умеет принять payload
```

Нужно проверить его собственные readers и observable semantics.

---

## 5. State Lifecycle — `LIFE-*`

Каждый materially relevant mechanism классифицируется:

```text
USER_INTENT
ACTION_LOCAL
DERIVED
CACHE
PERSISTED_SOURCE
EXTERNAL_SOURCE
LEGACY_COMPAT
UNKNOWN
```

Для LIFE фиксируются:

- owner;
- scope;
- created_when;
- valid_while;
- invalidated_when;
- authority_domains;
- frozen_after_action_start;
- supersession_rule;
- must_not_override;
- evidence;
- confidence.

### Базовые invariants

Если domain contract не говорит иначе:

```text
USER_INTENT
→ выбирает target нового action.

ACTION_LOCAL
→ authoritative только внутри создавшего его action instance.

ACTION_LOCAL + frozen target
→ новый global intent не ретаргетит уже начатый action.

ACTION_LOCAL residue
→ не должен определять другой новый action.

DERIVED/CACHE
→ не переопределяют source.

stale
→ never authoritative.

PERSISTED/EXTERNAL
→ authority определяется domain contract.
```

---

## 6. Representation Consumer Audit — `REP-*`

Используется, если один logical state имеет несколько carriers.

Например:

```text
manual materialized IDs
server-side token reference
```

REP требует найти у consumers:

- eligibility/stage guards;
- permissions;
- visibility/enabled;
- counts/summaries;
- payload/routing;
- mutation target;
- readback;
- cache;
- fail-open/fail-closed.

Главный lesson:

> Новый representation безопасен не тогда, когда его понимает backend, а когда все materially affected readers либо понимают его, либо сознательно остаются fail-closed.

---

## 7. Authority Audit — `AUTH-*`

Если mechanisms сосуществуют:

```text
state A
state B
state C
```

Planner определяет authority для reachable combinations.

Проверяем surfaces:

```text
visibility
count
summary
eligibility
payload
routing
mutation target
readback
cleanup
```

Material defect:

```text
visibility → A
payload     → B
```

без подтверждённого contract.

AUTH должен ссылаться на LIFE, а не только на наличие полей.

---

## 8. Reachability before Cartesian product

Нельзя автоматически считать defect каждую комбинацию:

```text
object.fieldA = ...
object.fieldB = ...
```

Сначала проследить writers/lifecycle:

```text
может ли normal runtime реально создать state A+B?
```

Если комбинация возможна только ручной конструкцией test object, она не обязана быть product bug.

---

## 9. Preservation Contract — `PRES-*`

Определяет, что нельзя случайно изменить.

Пример:

```text
PRES-001:
filter-only не получает normal action.

PRES-002:
manual IDs сохраняют precedence.

PRES-003:
wire protocol не меняется.
```

---

## 10. Interaction Matrix — `INT-*`

Для реально достижимых state combinations:

```text
current
stale
missing
empty
zero
partial
combined
```

Interaction должен быть material — не каждая наблюдаемая деталь обязана быть release gate.

---

## 11. Test Matrix — `TEST-*`

Planner формирует test/evidence plan до production edit.

Для regression bug предпочтительно:

```text
broken baseline → RED
candidate → GREEN
```

Evaluator должен проверять, что test не false-green.

---

## 12. Release Obligations — Controller-owned ledger

Не все `CC`/interaction observations становятся blockers, но Planner больше **не**
формирует отдельный free-form `release_obligations` array. Это cross-reference
representation оказалось ненадёжным и дублирующим.

Controller детерминированно включает все:

- `LIFE-*`;
- `REP-*`;
- `AUTH-*`;
- `CONS-*`;
- `PRES-*`;
- `TEST-*`.

Planner делает только локальную boolean-классификацию:

```text
current_contract[].release_critical
interaction_matrix[].release_critical
```

`true` CC/INT включаются Controller в blocking ledger; `false` остаются
characterization context. Fresh Evaluator независимо проверяет, не пометил ли Planner
material CC/INT как advisory.

Exact blocking IDs — machine-owned handoff Controller → Implementer/Evaluator.
Подробно: `HANDOFF_PROTOCOL.md`.

---

## 13. Evidence Ledger

Fresh Evaluator возвращает одну assessment на каждый release obligation:

```text
PASS
FAIL
UNVERIFIED
```

С evidence type и concrete evidence.

Medium/high-risk `UNVERIFIED` release claim обычно блокирует Done.

---

## 14. Evaluator statuses

### `PASS`

Все blocking obligations доказаны, material findings отсутствуют.

### `FINDINGS`

Проблема в candidate implementation/evidence.

Routing:

```text
Implementer repair
→ deterministic checks
→ NEW Fresh Evaluator
```

### `REPLAN_REQUIRED`

Проблема в planning/characterization model.

Routing:

```text
Planner revises artifact
→ validation
→ Fresh Evaluator
```

### `BLOCKED`

Не хватает обязательной technical capability/evidence.

### `NEEDS_USER_DECISION`

Нужно настоящее новое product rule.

Не использовать для implementation ambiguity, которую можно разрешить LIFE/ownership analysis.

---

## 15. Decision escalation

`NEEDS_USER_DECISION` требует explicit `decision_escalations`.

Planner должен доказать:

- какие peer states конфликтуют;
- почему lifecycle/ownership/temporal order не дают приоритета;
- какой именно product rule отсутствует;
- последствия вариантов.

---

## 16. Pre-edit baseline snapshot

После валидного plan, но до Implementer:

```text
head_sha
candidate path
exists
raw size
raw SHA-256
git EOL
index entry
baseline blob SHA
baseline blob size
```

Это independent evidence о pre-edit state.

Если replan позже добавил новый path, нельзя задним числом называть его snapshot «pre-edit».

### 16.1. D-032 path-local evidence reconciliation

Текущий Controller решает late-discovered consumer механически.

Если Agent уже изменил path вне `candidate_paths`, Controller не принимает это как готовое расширение. Он:

```text
rollback unexpected path к baseline
→ replan
→ required path входит в candidate_paths
→ snapshot ДО повторной edit этого path
```

Snapshot различает:

```text
captured_before_first_edit
captured_before_path_edit
```

Поэтому поздний path может быть:

```text
first_edit=false
path_edit=true
```

что является корректным evidence statement.

Machine invariant перед evaluation/final PASS:

```text
actual changed paths ⊆ candidate_paths
```

---

## 17. Deterministic checks

Check modes:

```text
feedback = "repair"
feedback = "heldout"
```

### repair

Failure показывается Implementer.

### heldout

Запускается после readiness.

Failure не используется как tutor в том же trial.

---

## 18. Held-out и calibration

Historical grader должен проверять semantics.

Calibration sanity check:

```text
broken → FAIL
known-good → PASS
```

Текущий Matrix case хранит hash-bound calibration certificate.

Если grader/check definition изменяется, certificate должен стать invalid.

Calibration — necessary sanity check, но один known-good не доказывает acceptance всех допустимых implementations.

---

## 19. Independent historical benchmark result

Matrix all-matching case стал первым реальным milestone:

Ранний Harness:

- исправлял direct Matrix flow;
- пропускал downstream Distribution stage semantics;
- принимал weak/false-green evidence;
- нуждался в human post-review.

Текущий Harness:

- самостоятельно определил current all-matching scope;
- построил LIFE/REP/AUTH;
- нашёл Distribution consumer;
- сохранил token-only fail-closed stage behavior;
- прошёл deterministic checks;
- прошёл Fresh Evaluator;
- прошёл calibrated held-out;
- прошёл отдельный post-hoc audit без material defect.

Это evidence жизнеспособности quality-core, но не доказательство универсальной надёжности.
