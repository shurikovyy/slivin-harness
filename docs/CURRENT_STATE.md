# Current State / Continuation Context

> Этот файл — **оперативный снимок состояния проекта**, который должен позволять продолжить разработку Slivin Harness в новом чате/сессии без знания предыдущей переписки.
>
> В отличие от `ARCHITECTURE.md` и `DECISIONS.md`, содержимое этого файла может меняться достаточно часто.

---

# 1. Текущий milestone

Quality-core прошёл первый серьёзный historical benchmark на реальном escaped UI defect.

Финальный Matrix all-matching trial:

```text
calibration certificate PASS
→ Planner READY
→ Implementer
→ deterministic checks PASS
→ Fresh Evaluator PASS
→ held-out PASS
→ HARNESS_TASK_PASS
```

Общее время historical trial:

```text
27:37
```

Post-hoc independent audit final workspace не нашёл material product defect.

Это означает:

```text
quality-core жизнеспособен
```

но **не**:

```text
Harness универсально надёжен на любых задачах
```

Corpus пока содержит слишком мало независимых задач/trials.

---

# 2. Что именно доказал Matrix benchmark

Historical defect:

```text
Matrix
→ filters
→ page checkbox
→ "Выбрать все N найденных"
→ selectedRows очищается
→ повторный filter-chip refresh
→ normal "Подтвердить распред" ошибочно скрывалась
```

Текущий Harness без human tutoring:

- охарактеризовал current contract;
- классифицировал all-matching как `USER_INTENT`;
- классифицировал temporary filtered token как `ACTION_LOCAL`;
- построил `REP-*`, `AUTH-*`, `LIFE-*`;
- нашёл downstream Distribution stage consumer;
- сохранил manual/filter-only/stale/zero/exclusions semantics;
- сделал Distribution token-only selection fail-closed;
- прошёл Fresh Evaluator;
- прошёл held-out, который проверял только public Matrix regression contract.

Важно:

> Distribution не был прописан как hidden known-answer test. Его обнаружил generic representation/lifecycle analysis.

---

# 3. Текущий validation status и environment

Последний полностью подтверждённый **quality-core** milestone остаётся lineage 0.4.6
historical Matrix PASS.

После него development increment добавил:

```text
D-032 machine-enforced change-surface reconciliation
portable project profiles/toolchain
managed Git worktrees
opt-in local-file/.env exposure
keep_worktree / apply_to_source
Windows UTF-8 console hardening
Windows managed-worktree path shortening / long-path support
```

## Что уже доказал первый real managed-worktree run

На Windows 10 / Git Bash / Codex CLI 0.148.0 Harness успешно:

1. разрешил `matrix_baseline` из `harness.local.toml`;
2. создал detached worktree вне Harness repository;
3. скопировал opt-in `.env`;
4. использовал project Python/Jest из configured `{project_root}`;
5. выполнил Planner;
6. выполнил Implementer;
7. прошёл deterministic checks;
8. запустил Fresh Evaluator;
9. получил корректный `REPLAN_REQUIRED`;
10. выполнил read-only replan.

То есть managed-worktree execution и project-toolchain resolution фактически работали.

## Почему trial не завершился

После успешного replan Controller попытался вывести revised structured plan в console и
упал на Windows legacy `charmap`:

```text
UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'
```

Сами JSON run artifacts сохраняли корректный UTF-8; повреждалась только console boundary.

После этого в source добавлены:

- UTF-8 launch/runtime enforcement;
- console regression test;
- LF/CRLF-neutral assertion;
- managed-worktree metadata at run start;
- `MANAGED_WORKTREE_ON_EXIT` diagnostics;
- short hashed filesystem segments;
- repository-local `core.longpaths=true` на Windows.

**Текущий статус:** source/unit hardening реализован; полный Matrix rerun после этих
Windows fixes ещё нужен до нового validated tag.

Machine/project-specific paths являются local configuration:

```text
harness.local.toml
SLIVIN_HARNESS_PYTHON
SLIVIN_CODEX_CMD
```

Project dependencies задаются через `[projects.<name>.toolchain]`.

# 4. Текущий ownership / Git contract

Harness — персональный repository, отдельный от team `sa_icover`.

Текущий task-agent contract:

```text
Planner      → read-only
Evaluator    → read-only
Implementer  → workspace-write
```

Но `workspace-write` **не означает permission на Git history/publication**.

До отдельного решения task agents не должны самостоятельно:

```text
git switch / checkout branch
git branch
git commit
git push
создавать PR
```

Branch/publication lifecycle остаётся human/explicit orchestration concern.

Это особенно важно перед переходом к GitHub Issues/tasks: task tracker не должен незаметно менять этот trust boundary.

Production writes также не являются частью текущего Harness core.

---

# 5. D-032 hardening — implemented, end-to-end exercise pending

Historical origin: successful Matrix Implementer однажды изменил Distribution files
вне initial `candidate_paths`, поэтому trusted pre-edit evidence для них отсутствовал.

Текущий Controller machine-enforce'ит:

```text
actual changed paths ⊆ plan.candidate_paths
```

Если write turn создаёт незапланированный path:

```text
record
→ rollback unexpected path
→ read-only change-surface replan
→ revised candidate_paths
→ trusted pre-path-edit snapshot
→ Implementer повторяет требуемое изменение
```

Final PASS тоже имеет actual-diff-to-plan guard.

## Что показал последний managed Matrix run

В первой implementation текущего trial Agent изменил только initial planned paths.
Поэтому D-032 unexpected-path route **не был вызван**.

Fresh Evaluator самостоятельно обнаружил, что initial plan недостаточно покрывает
Distribution и stale selection counter, и вернул обычный:

```text
REPLAN_REQUIRED
```

Replanner затем расширил `candidate_paths`, включая `selection/core.js` и Distribution.

Это правильный evaluator/replan route, но не прямое доказательство D-032 rollback +
late-snapshot path.

**Status:** implementation/unit coverage есть; end-to-end unexpected-path exercise ещё
не наблюдался на реальном App Server trial.

# 6. Другие известные незакрытые capability gaps

## 6.1. Target-project runtime

Архитектурное разделение теперь реализовано:

```text
Controller Python
!=
target-project Python/toolchain
```

Harness bootstrap Python берётся из environment/PATH и нужен только для Controller.

Project profile может объявить:

```toml
[projects.my_project.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
project_pytest = "..."
node = "..."
jest = "{project_root}/node_modules/jest/bin/jest.js"
```

Это устраняет прежнюю привязку Harness к `.venv` конкретного repository.

Открытый вопрос остаётся только capability-level: конкретная backend task должна явно включить нужный `project_python`/checks; Harness не пытается автоматически угадать project runtime.

---

## 6.2. Browser/runtime UI verification

Текущий Matrix milestone доказан Jest/runtime-source checks + evaluator + held-out.

Полного browser/Playwright flow пока нет.

Для UI task, где DOM/network/runtime semantics нельзя доказать Jest, Harness должен `BLOCKED`/понижать confidence, а не симулировать browser evidence.

---

## 6.3. External systems

Пока нет Harness-owned typed access к:

```text
PostgreSQL
1C
Airflow
observability
```

Целевой порядок:

```text
typed observation
→ test-write
→ production read-only
→ governed mutation
```

а не generic SSH/SQL/HTTP super-access.

---

## 6.4. PR / CI / issue supervisor

Не реализованы:

```text
GitHub Issues task pickup
automatic branches
commit/push
PR publication
CI supervisor
```

Это сознательно отложенный usability/orchestration layer.

---

# 7. Minor known observations, которые не нужно превращать в defect без evidence

## UTF-8 BOM drift

В final Matrix candidate `core.js` потерял исходный UTF-8 BOM.

Функционального JS defect не найдено; EOL checker прошёл.

Classification:

```text
LOW hygiene observation
```

Не добавлять отдельный BOM gate только из-за одного случая.

Если encoding drift повторяется на других tasks — тогда это general capability candidate.

---

## SearchableSelectCell concern

На одном из ранних planning cycles был замечен возможный stale-token routing concern вокруг SearchableSelectCell.

Он **не был подтверждён как material reachable defect** финального candidate.

Fresh Evaluator проверил searchable-select routing и material finding не оставил.

Не превращать этот исторический concern в «известный defect» без нового reproduction/evidence.

---

## Broad sibling baseline failures

Во время successful implementation широкий sibling run видел unrelated failures в existing:

```text
odata-response-state
grouped-page-selection
```

Focused release gates при этом были green.

Если в будущем эти suites становятся required gates, сначала нужно определить:

```text
broken baseline тоже fail?
```

Нельзя автоматически приписывать любой historical baseline failure новому candidate.

---

# 8. Oracle lessons, которые нельзя потерять

Было два разных over-specification incident.

## `excluded_ids: []`

Matrix oracle сначала требовал exact empty field.

Реальный backend contract допускал:

```text
missing excluded_ids ≡ []
```

Known-good reference тоже мог опускать field.

Исправление:

```text
oracle проверяет semantics, а не exact reference syntax
```

## README `READY\n`

Smoke-task intent требовал логическую единственную строку `READY`.

Одна реализация grader требовала конкретные bytes:

```text
READY\n
```

хотя `READY`, `READY\n`, `READY\r\n` были семантически допустимы.

Этот случай показал:

> `broken → FAIL` и один `known-good → PASS` ещё не доказывают, что grader принимает всё множество корректных решений.

---

# 9. App Server context, который важно перепроверять после upgrade

Tested Codex CLI:

```text
0.148.0
```

App Server protocol/schema version-sensitive.

После update Codex CLI не считать старую совместимость гарантированной.

Минимальный requalification:

1. schema generation / inspection;
2. initialize;
3. read-only thread;
4. workspace-write sandbox probe;
5. Python/temp probe;
6. structured Planner result;
7. structured Evaluator result;
8. heartbeat/process-death handling.

Также в 0.148.0:

```text
instructionSources
```

не являлся надёжным audit signal.

---

# 10. Project integration details для `sa_icover`

Текущий project repository имеет собственные:

```text
AGENTS.md
.agents/skills/
tools/check_changed_eol.py
```

На успешном Matrix run App Server обнаруживал четыре repo skills:

```text
sa-icover-independent-review
sa-icover-odata-change
sa-icover-readonly-data-collection
sa-icover-targeted-review
```

Для Matrix task explicit active skill был:

```text
none
```

и это было корректно: OData/data skills не относились к задаче, independent/targeted skills имеют собственные activation contracts.

Не считать auto-skill usage доказанным только по discovery.

Project EOL environment исторически использует:

```text
core.autocrlf=true
```

а project checker:

```text
tools/check_changed_eol.py
```

является более сильным project-specific release gate, чем простое визуальное сравнение line endings.

---

# 11. Historical benchmark discipline

Текущий Matrix benchmark использует managed project profile:

```text
harness.local.toml
[projects.matrix_baseline]
repo = <path-to-broken-_90>
result_mode = "keep_worktree"
```

Source `_90` должен оставаться clean и неизменным:

```bash
cd /path/to/sa_icover_90
git status --short
git rev-parse HEAD
```

Каждый trial:

```bash
cd ~/Tools/slivin-harness
./run cases/matrix-all-matching/task.toml
```

создаёт новый unique detached worktree. Reset `cases/.../workspace` больше не нужен.

После failed/successful run retained worktree можно:

- сохранить для audit;
- удалить через `git worktree remove --force <path>` из source repository.

Не использовать candidate worktree как baseline следующего trial.

Held-out assertion не передаётся Implementer как tutoring feedback.

Если grader меняется:

```text
calibration certificate invalid
→ explicit recalibration
```

# 12. Current roadmap order

Состояние на момент этой записи:

```text
1. Quality-core historical milestone                    DONE (0.4.6 lineage)
2. Knowledge-base / architecture / operational docs     DONE
3. Context gap audit                                    DONE
4. D-032 change-surface reconciliation                  IMPLEMENTED; E2E PATH-EXPANSION PROOF PENDING
5. Portable profiles / managed worktree                 REAL WINDOWS EXECUTION PROVEN
6. UTF-8 + Windows long-path hardening                  IMPLEMENTED; FULL MATRIX RERUN PENDING
7. Documentation refactor to managed-worktree model     DONE IN CURRENT CHANGE
8. GitHub Issues / task orchestration                    AFTER FULL MATRIX RERUN
9. Additional historical eval corpus                    IMPORTANT
10. Browser/runtime capability                          AS TASKS REQUIRE
11. External typed observation/MCP                      LATER
12. PR/CI publication                                   AFTER task orchestration/quality evidence
```

Не начинать task supervisor до успешного полного rerun текущего Matrix case после
Windows UTF/path fixes.

# 13. Что должен прочитать новый чат перед продолжением

Минимальный пакет:

1. knowledge-base page про Harness engineering;
2. root `README.md`;
3. `docs/CURRENT_STATE.md`;
4. `docs/ARCHITECTURE.md`;
5. `docs/QUALITY_MODEL.md`;
6. `docs/DECISIONS.md`;
7. `docs/HISTORY.md`;
8. `docs/WINDOWS_SETUP.md`;
9. `docs/WORKSPACE_MODEL.md`;
10. `docs/MAINTAINING_HARNESS.md`.

После этого новый чат не должен требовать пересказа старой переписки для продолжения архитектурной работы.

---

# 14. Что сознательно НЕ переносится из старого чата

Не нужно дублировать в Harness docs всю бизнес-архитектуру `sa_icover/im_odata`:

```text
Step1/Step4/Step5
reserve/supply semantics
marketplace metadata
shipping-date business rules
```

Эти знания принадлежат project repository/docs и должны читаться из него при соответствующей task.

Harness documentation хранит только integration contracts, необходимые для самого Harness.

Это предотвращает быстрое устаревание копии product knowledge.
