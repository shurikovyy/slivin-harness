# Как дальше развивать Slivin Harness

## 1. Главное правило изменения

Не добавлять новый Harness mechanism только потому, что он выглядит полезно.

Изменение должно иметь origin:

```text
Observed failure / missing capability
→ root cause
→ general mechanism
→ eval evidence
```

---

## 2. Перед изменением Harness

Проверить:

```bash
git status --short
```

Не работать поверх непонятного dirty state.

---

## 3. После изменения

Минимум:

```bash
./py tools/self_check.py
```

Для architecture/quality change:

1. neutral smoke, если изменился controller/schema;
2. relevant historical eval;
3. проверить, что improvement не требует known-answer prompt;
4. зафиксировать evidence.

---

## 4. Что документировать

### Если изменился architecture contract

Обновить:

```text
docs/ARCHITECTURE.md
docs/QUALITY_MODEL.md
docs/DECISIONS.md
```

### Если найден новый infrastructure nuance

Обновить:

```text
docs/WINDOWS_SETUP.md
или
docs/WORKSPACE_MODEL.md
```

### Если произошёл milestone/failure

Обновить:

```text
docs/HISTORY.md
CHANGELOG.md
```

---

## 5. Decision record

Для нового существенного решения использовать `DECISION_TEMPLATE.md`.

Особенно если:

- был выбор альтернатив;
- решение неочевидно;
- решение ограничивает future architecture;
- решение появилось из production/eval miss;
- позже разработчик может спросить «почему не сделали проще?».

---

## 6. Не создавать parallel version files

Запрещённый pattern:

```text
task_runner_v047.py
planner_old.py
evaluator_new2.py
```

Правильно:

```text
git commit
git tag для milestone
```

---

## 7. Не обучать historical benchmark known answer

После miss нельзя автоматически:

```text
human нашёл gap
→ hidden test именно на gap
→ rerun того же benchmark
```

Сначала спросить:

```text
Почему REP/AUTH/LIFE/Evaluator не нашёл этот класс самостоятельно?
```

Если меняется general capability — после этого можно проводить clean eval.

---

## 8. Oracle maintenance

Если held-out grader меняется:

- semantic contract должен быть понятен;
- calibration certificate станет invalid;
- провести explicit recalibration against broken + known-good;
- сохранить новый certificate.

Не ослаблять grader просто для candidate PASS.

Не делать exact reference shape requirement без contract evidence.

---

## 9. Harness rule — гипотеза

Каждый слой имеет latency/cost:

```text
Planner
REP
AUTH
LIFE
Evaluator
held-out
specialist
```

По мере улучшения models нужно периодически проверять:

```text
слой всё ещё даёт measurable lift?
```

Удаление ненужного scaffolding — нормальная эволюция.

---

## 10. Когда делать new tag

Tag оправдан для milestone:

- новый externally meaningful capability;
- изменён task-manifest/schema contract;
- новая trust/security boundary;
- benchmark milestone.

Мелкие docs/fix commits не обязаны повышать версию.

---

## 11. Run evidence

Для интересного failure сохранять:

```text
run directory
task manifest snapshot
plan
evaluation
check outputs
final workspace/diff при необходимости
```

Но не коммитить `runs/`.

В documentation записывать выводы, а не полный transcript.

---

## 12. Security hygiene

Перед новым external capability ответить:

- какие credentials нужны;
- может ли быть read-only;
- можно ли typed tool вместо shell;
- куда попадают logs;
- может ли secret оказаться в transcript/artifact;
- что физически ограничивает права.

Prompt «не меняй production» не является boundary.

---

## 13. Критерий хорошего нового capability

Новый механизм хорош, если:

1. решает общий класс failures;
2. не содержит known answer конкретного case;
3. механически наблюдаем;
4. имеет понятный failure mode;
5. можно проверить historical/production evidence;
6. его можно позже удалить, если model станет достаточно сильной.
