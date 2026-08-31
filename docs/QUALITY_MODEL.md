# Модель качества 0.8.0a4 — Phase 2

## 1. Что доказывает Phase 1

Phase 1 не делает агента умнее напрямую. Она устраняет фундаментальную неоднозначность:

> Какой этап сейчас выполняется, какая версия Contract/candidate проверялась и какие прежние доказательства уже устарели?

До Phase 1 эти факты выводились из console flow. Теперь они являются machine state.

## 2. Один candidate — одно evidence lineage

Единый `candidate_id` связывает candidate с:

```text
baseline SHA
workspace HEAD
changed paths
bytes
удалениями
symlink targets
Git-visible file modes
```

`Git-visible` означает, что mode должен существовать в HEAD-to-working-tree diff. На native Windows/NTFS один `chmod` может не создавать такого изменения; в этом случае candidate действительно остаётся тем же с точки зрения Git. Self-check не маскирует настоящий mode change: он сначала подтверждает, что Git видит `100644 → 100755`, и только при отсутствии такой filesystem capability честно пропускает integration test.

Если candidate меняется, его identity меняется. Если Controller check, Evaluator или held-out изменяет candidate, run не может продолжить как PASS.

## 3. Версии и инвалидация

`run_state.json` хранит revision vector:

```text
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
candidate_rev
runtime_environment_rev
attempt_id
```

Phase 1 уже использует plan, implementation contract, candidate и runtime environment revisions. Остальные поля зарезервированы для следующих фаз и пока имеют `null`.

Главное правило:

```text
изменилось основание доказательства
→ старое evidence больше не считается текущим
```

Например:

```text
REPLAN_REQUIRED
→ Planner и downstream stages INVALIDATED
→ attempt_id увеличивается
→ Planner запускается заново
```

## 4. Stage result validation

`RunState` не принимает произвольный успешный code.

Нельзя записать:

```text
Intake / Preflight
→ EVALUATION_PASS
```

Для каждого этапа канонически задан допустимый success code. `RunState` также различает настоящий `PASSED` и разрешённый compatibility `SKIPPED`: skip-code нельзя записать как обычный PASS, а обязательный этап нельзя пропустить с его pass-code. Финальный код дополнительно связан с режимом run: production не может завершиться `HARNESS_BENCHMARK_PASS`, а benchmark — `HARNESS_TASK_PASS`. Это защищает от логически невозможного результата из-за ошибки Controller-кода.

## 5. Self-verify и Controller checks

Текущая модель 0.7.1 сохраняется:

```text
Implementer self-verify
≠
Controller deterministic verification
```

Self-verify помогает Implementer исправиться до сдачи. Controller затем повторяет checks независимо.

Phase 1 переводит fingerprint self-verify на единый `candidate_id`, но private Controller plane и dynamic check registration до `COMPLETE` ещё относятся к следующим фазам.

## 6. Runtime и Evaluator

Канонический workflow уже содержит:

```text
Step 5 Runtime / external verification
Step 6 Blind Evaluator
```

В 0.8.0a4:

- Runtime executor ещё не реализован и всегда получает explicit compatibility skip;
- Evaluator остаётся однопроходным `evaluator.v4` и получает прежний context;
- двухфазный blind/contract audit будет реализован позже.

Документация намеренно не выдаёт будущий target за текущую capability.

## 7. Production и benchmark

Workflow mode вычисляется Controller:

```text
нет benchmark/heldout → PRODUCTION
benchmark config или heldout → HISTORICAL_BENCHMARK
```

Финальные статусы разделены:

```text
HARNESS_TASK_PASS
HARNESS_BENCHMARK_PASS
```

Held-out по-прежнему не возвращается Implementer как repair feedback.

## 8. Что означает `HARNESS_SELF_CHECK_PASS`

Он доказывает:

```text
Python sources compile
manifest schemas valid
unit tests pass
generated workflow docs current
workflow definition internally consistent
benchmark calibration artifacts consistent
```

Он не доказывает:

```text
автономную надёжность на любом project;
готовность ещё не реализованных Phase 2–6 capabilities;
отсутствие всех semantic defects.
```

## 9. Метрики Phase 1

Полезные artifacts:

```text
run_state.json
workflow_snapshot.json
candidate_identity_current.json
execution_metrics.json
final_acceptance.json
```

Они позволяют измерять:

```text
attempts per task
repair/replan cycles
first_evaluation_pass
candidate revisions
terminal failure stage
```

После следующих фаз к ним добавятся Task Contract и Verification Plan revisions.

## 10. Anti-monster принцип

В Phase 1 не добавлен ни один новый model reviewer.

Сложность добавлена туда, где она детерминированна и проверяема:

```text
workflow definition
state ownership
artifact identity
invalidation
status routing
```

Planner/Implementer/Evaluator prompts будут меняться отдельными фазами и после каждой фазы проверяться на historical corpus.

## Private authority и execution honesty

Phase 2 добавляет два инварианта качества:

```text
agent-writable artifact != authoritative evidence

declared sandbox policy != enforced sandbox capability
```

Run State, current candidate identity, Implementation Contract copies и self-verify receipts находятся в Controller private plane. Execution policy публикуется без secrets и явно помечает enforcement как `ENFORCED`, `ADVISORY` или `UNAVAILABLE`. Это предотвращает ложный PASS, основанный на модифицированном агентом stamp, и ложные security-claims о ещё не реализованном restricted runner.

Self-verify receipt связан не только с bytes candidate, но и с revision vector. Contract/Verification Plan change автоматически требует новое доказательство.


Foundation protocol versions: `controller-plane.v1`, `execution-broker.v1`.

Canonical-path invariant: filesystem ownership и private-plane non-disclosure оцениваются по фактическому canonical location, а не по совпадению строковых/лексических `Path` representations. Это обязательно для native Windows, где один каталог может быть представлен несколькими эквивалентными путями. Проверки остаются fail-closed при cross-drive или неразрешимой canonicalization.
