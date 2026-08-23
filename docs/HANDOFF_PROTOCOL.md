# Strict cross-stage handoff protocol

## 1. Зачем существует этот protocol

Planner, Implementer, Controller и Fresh Evaluator — разные execution stages.
Между ними нельзя передавать semantically important state в форме, которую следующая
стадия должна «угадать» или нормализовать.

Исторический failure:

```text
Planner.release_obligations = [
  "CC-1 — preserve Matrix persistent checkbox...",
  "CC-1, CC-2 and CC-3 ..."
]
```

Controller ожидал exact IDs, поэтому несколько дорогих Planner turns завершились
`planner_artifact_invalid` ещё до implementation.

Вывод:

> Cross-stage reference data должно либо быть Controller-owned, либо иметь schema,
> которая допускает только одно однозначное representation.

---

## 2. Planner protocol — `planner.v2`

Planner возвращает structured artifact с:

```text
protocol_version = "planner.v2"
```

ID объектов остаются bare identifiers:

```text
CC-1
A-1
CONS-1
LIFE-1
REP-1
AUTH-1
PRES-1
INT-1
TEST-1
DEC-1
```

Запрещено:

```text
CC-1 — explanation
CC-1, CC-2
TEST-1 must pass
```

Пояснение хранится только в semantic fields объекта (`behavior`, `claim`,
`scenario`, `expected`, и т. п.). Controller независимо проверяет формат каждого ID.

---

## 3. Planner больше не формирует `release_obligations`

Поле удалено из Planner output schema.

Причина: список был дублирующим cross-reference representation тех же объектов и не
добавлял информации, которую нельзя выразить локально у владельца semantics.

Вместо этого:

### Всегда blocking

Controller автоматически включает все IDs из:

```text
CONS-*
LIFE-*
REP-*
AUTH-*
PRES-*
TEST-*
```

### Planner классифицирует только CC/INT

Каждый `current_contract` и `interaction_matrix` имеет:

```json
"release_critical": true | false
```

Controller включает только `release_critical=true` CC/INT.

Итоговый blocking ledger вычисляется **детерминированно Controller'ом**.
Planner не может записать туда prose, объединённые IDs или забыть mandatory class.

---

## 4. Controller-owned plan contract

После успешной validation Controller формирует отдельный contract:

```text
protocol_version
plan_fingerprint
blocking_obligation_ids
candidate_paths
```

`plan_fingerprint` — короткий SHA-256 fingerprint canonical Planner JSON.

Он связывает последующие stages с **конкретной ревизией plan**, особенно после:

```text
REPLAN_REQUIRED
change-surface replan
```

Implementer получает:

```text
approved plan
PLAN_FINGERPRINT
Controller obligation IDs
baseline snapshot
```

а не должен реконструировать blocking ledger самостоятельно.

---

## 5. Evaluator protocol — `evaluator.v2`

Fresh Evaluator возвращает:

```text
protocol_version = "evaluator.v2"
plan_fingerprint = exact current plan fingerprint
```

Controller динамически связывает Evaluator output schema с текущим plan:

```text
planner_assumption_audit[].id
→ enum exact current A-* IDs

obligation_assessment[].id
→ enum exact Controller blocking obligation IDs

plan_fingerprint
→ enum exact current plan fingerprint
```

После model output Controller повторно проверяет set equality:

```text
Evaluator obligations == Controller obligations
Evaluator assumptions == Planner assumptions
Evaluator plan_fingerprint == current plan fingerprint
```

Никакого fuzzy matching или extraction IDs из prose нет.

---

## 6. Почему Controller не «чинит» malformed artifact автоматически

Harness сознательно не делает:

```text
"CC-1 — preserve..." → regex → "CC-1"
```

и не разбирает:

```text
"CC-1, CC-2 and CC-3"
```

Почему:

1. это скрывает нарушение protocol boundary;
2. Controller начинает угадывать intent модели;
3. malformed artifact может быть семантически неоднозначен;
4. ошибки schema становятся менее наблюдаемыми.

Правило:

```text
strict generation
→ strict validation
→ explicit retry or BLOCKED
```

а не silent repair.

---

## 7. Validation retry

Если Planner artifact нарушает semantic Controller invariant, retry получает
**компактный structured error**, например:

```json
{
  "protocol_error": "PLANNER_CANDIDATE_PATHS_MISSING",
  "field": "candidate_paths",
  "message": "...",
  "expected": "At least one exact repo-relative path.",
  "actual": [],
  "repair_rule": "Return a fresh artifact..."
}
```

В retry не пересылается целиком огромный malformed artifact.
Передаётся только:

```text
original revision context
structured protocol error
compact ID/path summary
```

Это уменьшает token cost и не провоцирует модель копировать неправильное formatting
из предыдущей попытки.

Полный invalid artifact при этом остаётся в `runs/...` для audit.

---

## 8. Replan → Implementer handoff

После revised plan Implementer не должен узнавать новую semantics только из текста
Evaluator finding.

Любой repair prompt после Fresh Evaluator содержит текущие:

```text
approved plan
PLAN_FINGERPRINT
Controller blocking obligation IDs
baseline snapshot
Evaluator findings
```

Change-surface repair получает тот же contract.

То есть source of truth между stages — **current Controller-approved plan**, а не
conversation memory старого Implementer thread.

---

## 9. Authority

```text
Planner
→ owns characterization/proposed semantics

Controller
→ owns protocol validation, exact obligation ledger, plan identity and stage routing

Implementer
→ owns code changes only inside current approved change surface

Fresh Evaluator
→ owns independent verdict for exact plan fingerprint
```

Ни одна stage не должна переопределять contract другой стадии через свободный текст.

---

## 10. Regression policy

`tools/self_check.py` запускает protocol tests, которые проверяют минимум:

- Planner schema больше не содержит free-form `release_obligations`;
- Controller детерминированно строит blocking IDs;
- advisory `CC/INT` не попадают в blocking ledger;
- prose/grouped ID отвергается, даже если output schema когда-либо обойдена;
- legacy `release_obligations` field отвергается;
- Evaluator schema bound к exact Controller IDs;
- Evaluator verdict bound к current plan fingerprint;
- Planner/Evaluator protocol versions explicit.

Любое изменение handoff format требует:

```text
protocol version decision
regression tests
README/docs update
historical benchmark rerun
```
