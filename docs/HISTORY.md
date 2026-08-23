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


---

# 16. v0.4.6 smoke: второй пример over-specified oracle

Простой smoke contract требовал:

```text
README.txt содержит единственную строку READY
```

Planner/Implementer создал семантически допустимое representation, но ранний held-out был привязан к exact bytes:

```text
READY\n
```

Этот case усилил вывод Matrix `excluded_ids` incident:

> Grader может быть слишком узким даже после `broken → FAIL / one known-good → PASS`.

Corrected semantic grader принимал:

```text
READY
READY\n
READY\r\n
```

и отклонял:

```text
BASE
missing
empty
partial
extra line
BOM/extra content
```

---

# 17. Successful Matrix run: target-project runtime gap

Implementer отдельно пытался проверить backend token unit-test.

Run не имел usable Django environment внутри workspace/system Python:

```text
backend token test → not executed
```

Backend code/wire contract не менялись, поэтому конкретный task сохранил PASS.

Это выявило будущий capability:

```text
Harness runtime Python
!=
target project Python
```

для backend tasks.

---

# 18. Post-milestone documentation audit: candidate-path evidence gap

При повторном разборе final successful run обнаружено:

Planner initial `candidate_paths` не включал Distribution files, хотя `affected_consumers` и `TEST-008` уже указывали на Distribution risk.

Implementer позже самостоятельно обнаружил необходимость change и изменил Distribution.

Product result был правильным.

Но Controller не:

- расширил planned path set до edit;
- снял trusted pre-edit snapshot Distribution path;
- потребовал replan;
- mechanically reconciled final diff with planned paths.

Это **не отменяет successful product benchmark**, но уточняет состояние Harness:

```text
quality reasoning milestone → достигнут
change-surface evidence hardening → ещё нужен
```

Следующий core hardening должен решить общий класс:

```text
planned change surface
vs
actual final diff
```

до масштабирования orchestration.

---

# 19. D-032 и workspace usability redesign

После documentation audit были объединены три связанных improvement:

```text
A. planned-vs-actual change surface
B. machine/project path portability
C. неудобный manual repository copy lifecycle
```

## D-032 implementation

Controller теперь после каждого write turn сравнивает:

```text
actual Git diff paths
vs
Planner.candidate_paths
```

Unexpected path:

```text
record
→ rollback только unexpected path
→ read-only replan
→ explicit revised candidate surface
→ per-path trusted snapshot
→ Implementer redo
```

Добавлены stdlib tests на tracked/untracked rollback и late pre-path-edit evidence.

## Portable configuration

Удалены hardcoded defaults на `sa_icover/.venv`, portable Node и Jest.

Принято:

```text
Harness bootstrap Python → PATH/env
harness.local.toml        → machine config
projects.<name>            → source repo + toolchain
```

## Managed Git worktree

Для ordinary project task repository больше не копируется в Harness `cases/`.

Controller создаёт detached Git worktree из source HEAD.

Heavy local dependencies остаются в source location, а executable paths задаются через project toolchain.

## `.env`

Absolute ban заменён explicit opt-in policy.

Managed worktree:

```toml
copy_untracked = [".env"]
```

Static historical preparation:

```text
--allow-env
```

## Accepted result

`result_mode=apply_to_source` позволяет после полного quality PASS применить binary candidate patch обратно в исходный working tree без commit/push.

На момент записи source/unit self-check этого increment проходит в development environment. Windows + real App Server regression должен быть выполнен перед новым validated tag.


---

# 20. Первый real managed-worktree trial на Windows

После D-032/worktree redesign Matrix benchmark был переведён с локальной копии
`cases/.../workspace` на external source repository + managed detached worktree.

Run подтвердил:

```text
local project profile resolution
→ worktree creation outside Harness repo
→ opt-in .env copy
→ project Python/Jest resolution from source
→ Planner
→ Implementer
→ deterministic checks PASS
→ Fresh Evaluator
→ REPLAN_REQUIRED
→ revised Planner
```

Fresh Evaluator снова обнаружил два реальных gap initial candidate:

- Distribution stage/eligibility representation;
- stale all-matching selection counter.

Это подтвердило, что evaluator/replan quality logic работает и в новом workspace model.

## Failure: Windows console encoding

После `REPLAN_1_DONE` Controller упал не на reasoning, а при печати Unicode revised
plan:

```text
UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'
```

Run artifacts при этом содержали корректный UTF-8. Root cause находился в Python
stdout/stderr encoding under Git Bash/Windows.

Добавлены:

```text
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
Controller stdout/stderr reconfigure
console regression test
LF/CRLF-neutral test assertion
```

## Failure: long path при cleanup старого worktree

Первый managed path включал полный длинный `task_id`; `git worktree remove --force`
на Windows получил `Filename too long`.

Добавлены:

- bounded/hash-suffixed filesystem path segments;
- short workspace-root recommendation;
- repository-local `core.longpaths=true`;
- explicit failed-worktree location diagnostics.

## Validation nuance

Этот run не вызвал D-032 unexpected-path route: Implementer изменил только planned
files, а missing consumers были обнаружены Fresh Evaluator и добавлены через ordinary
`REPLAN_REQUIRED`.

Полный rerun после UTF/path fixes остаётся следующим validation gate.
