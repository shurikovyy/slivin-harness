# История развития Slivin Harness

Этот документ фиксирует **причинную историю**, а не просто номера версий.

Формат каждой стадии:

```text
что было
что сломалось / чего не хватало
какой общий вывод сделан
что добавлено
```

---

# 1. До Harness: Codex App + инструкции + reviewers

Исходный процесс уже имел:

- сильный project `AGENTS.md`;
- project skills;
- несколько custom reviewer roles;
- ручное управление ветками и Codex sessions.

Проблема:

```text
Agent PASS
→ человек независимо проверяет
→ находит gap
→ новый prompt
```

Шесть reviewers не решали correlated blind spots, потому что работали на той же модели состояния/тестах.

---

# 2. Execution environment proof

Первая цель была не PR automation, а доказать, что Controller вообще может безопасно управлять agent execution.

Проверено:

- Codex CLI login;
- `codex app-server --stdio`;
- protocol schema generation;
- read-only thread;
- workspace-write;
- file modifications;
- project Python;
- pytest;
- Node/Jest-compatible execution.

## Первый App Server adapter bug

RPC request ждал response, но notification backlog мог повторно отдавать один и тот же deferred event и starvation'ить настоящий response.

Исправление:

```text
RPC request читает новые messages из queue
unrelated notifications временно собирает локально
после response возвращает их в backlog
```

---

# 3. Windows workspace-write blocker

App Server/Codex создавал writable roots:

```text
[workdir, /tmp, $TMPDIR]
```

Windows unelevated restricted-token sandbox отказался применять split writable roots.

Admin elevation была недоступна.

## Решение

```text
sandbox_workspace_write.exclude_slash_tmp=true
sandbox_workspace_write.exclude_tmpdir_env_var=true
```

и temp перенесён в:

```text
workspace/.harness_tmp
```

После этого workspace-write стал реально usable без admin.

Это был один из наиболее трудоёмких infrastructure blockers.

---

# 4. Completion controller demo

Toy task:

```text
agent должен создать answer.txt=READY
```

Внешний gate дополнительно требовал `proof.txt=VERIFIED`.

Первый agent turn считал работу готовой без proof.

Harness:

```text
checks FAIL
→ вернул evidence agent
→ repair
→ checks PASS
```

Доказан главный принцип:

> Controller способен переопределить субъективный Done модели.

---

# 5. v0.2 universal runner

Появились:

- TOML manifests;
- clean Git preflight;
- generic checks;
- automatic repair;
- trusted temp;
- EOL/diff checks.

## Первый historical Matrix benchmark

Broken `_90`:

после «Выбрать все N найденных» исчезала «Подтвердить распред».

External hidden oracle:

- all-matching explicit selection;
- filter-only preservation;
- manual selection preservation.

v0.2:

1. Agent сделал shared fix.
2. Hidden oracle указал первый miss.
3. Agent repair.
4. Oracle указал `excluded_ids` shape.
5. Agent repair.
6. Harness объявил PASS.

## Human post-hoc audit

Обнаружены реальные gaps:

- stale/combined token semantics;
- Distribution stage guard;
- shared core impact.

Вывод:

```text
completion loop + known tests
```

недостаточен.

И ещё:

```text
hidden failure → repair
```

не является честным historical eval.

---

# 6. v0.3: Planner + Fresh Evaluator

Добавлены:

```text
read-only Planner
Implementer
checks
fresh read-only Evaluator
```

Planner начал явно искать:

- selectedRows;
- selectionAllMatching;
- filteredBulkSelection;
- scopeKey;
- consumers.

## Положительный результат

Planner заметно глубже моделировал state.

Evaluator начал находить false-green и shared-consumer risk.

## Проблемы

- structured turn adapter склеивал несколько agent messages → `JSONDecodeError: Extra data`;
- Planner artifact оставался «хорошим текстом», но не обязательным evidence contract;
- hidden oracle всё ещё мог учить agent во время repair.

---

# 7. v0.4: Characterization + evidence obligations + held-out

Добавлены:

- current contract;
- assumptions;
- release obligations;
- evidence ledger;
- held-out mode;
- `REPLAN_REQUIRED`.

## Failure: transport regression

В packaged App Server случайно вернулась старая логика join всех messages.

Урок:

> Harness transport — такой же product code и требует regression discipline.

## Failure: слишком строгий evidence

README smoke был заблокирован из-за невозможности доказать incidental historical CRLF.

Это привело к разделению:

```text
characterization observations
vs
release-critical obligations
```

---

# 8. Pre-edit snapshot

Добавлен independent snapshot planned paths до first edit.

Причина:

после edit нельзя честно восстановить физическое pre-edit состояние.

---

# 9. v0.4.4 observability

Добавлены:

- readable Implementer message separators;
- phase timings;
- per-check timings;
- overall duration;
- heartbeat;
- App Server health;
- run artifacts;
- repo `AGENTS.md`/skills discovery;
- graceful invalid-plan retry.

Это решило operational проблему:

```text
долгий REPLAN без вывода выглядел как зависание
```

---

# 10. Oracle problem: `excluded_ids: []`

Historical held-out требовал exact:

```json
{
  "selection_token": "...",
  "excluded_ids": []
}
```

Independent project audit показал:

- backend: missing `excluded_ids` → `[]`;
- reference `_92` тоже опускает empty field.

То есть grader проверял implementation shape, а не semantic contract.

Исправлен oracle.

Вывод:

> Historical grader — тоже software artifact, который может быть неправильным.

---

# 11. v0.4.5: Oracle calibration + REP/AUTH

Добавлены:

```text
broken → FAIL
known-good → PASS
```

calibration gate.

Также:

- `REP-*` representation-consumer audit;
- `AUTH-*` state authority audit.

## Результат

Planner самостоятельно обнаружил:

- downstream Distribution;
- multiple token representations;
- возможную precedence ambiguity.

Но вернул `NEEDS_USER_DECISION` на technical state conflict.

---

# 12. v0.4.6: LIFE lifecycle authority

Добавлена классификация:

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

Planner обязан доказать, почему конфликт действительно требует product decision.

Это позволило понять:

```text
selectionAllMatching = USER_INTENT
filteredBulkSelection = ACTION_LOCAL
```

и не спрашивать пользователя о technical lifecycle rule.

---

# 13. Successful historical milestone

Финальный Matrix historical trial:

```text
calibration certificate PASS
Planner READY
Implementer
deterministic checks PASS
Fresh Evaluator PASS
held-out PASS
HARNESS_TASK_PASS
```

Время:

```text
27:37
```

Planner/Implementer самостоятельно нашли Distribution consumer.

Candidate:

- исправил Matrix all-matching classification;
- сохранил manual/filter-only/stale/zero/exclusion semantics;
- добавил current token scope helper;
- сделал Distribution token-only state `hasSelection=true`, `hasSelectionData=false`;
- сохранил Distribution stage guard fail-closed.

Post-hoc independent audit material defect не обнаружил.

Это первый подтверждённый milestone качества.

---

# 14. Repo hygiene milestone

До этого Harness развивался через множество:

```text
task_runner_vXX.py
v0.4.x.zip
```

что быстро стало неудобно.

Принято:

```text
one repository
one current source
Git commits
tags
CHANGELOG
docs
```

Добавлены:

- `.gitignore`;
- `.gitattributes`;
- CWD-independent launchers;
- local config;
- workspace preparation;
- self-check;
- calibration certificate.

---

# 15. Что пока не доказано

Успешный historical benchmark — важный milestone, но corpus пока мал.

Не доказаны полноценно:

- несколько независимых trials одного case;
- другие classes historical bugs;
- browser/runtime tasks;
- cross-system tasks;
- production-read evidence;
- MCP;
- PR/CI orchestration.

Следующий рост качества должен быть driven реальными tasks/evals, а не добавлением scaffolding «на всякий случай».
