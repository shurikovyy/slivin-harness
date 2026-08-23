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

# 3. Текущий tested environment

Проверено на:

```text
Windows 10 build 19045
Git Bash / MINGW64
Codex CLI 0.148.0
Slivin Harness runtime lineage 0.4.6
Python 3.11-compatible runtime
portable Node
project-external Jest
```

Основные default paths исходной Windows-машины:

```text
Codex:
~/Tools/codex-cli/node_modules/.bin/codex.cmd

Node:
~/Tools/node/node.exe

Harness/project Python launcher default:
~/Documents/sa_icover/.venv/Scripts/python.exe

Jest:
~/Documents/sa_icover/node_modules/jest/bin/jest.js
```

Machine-local differences должны идти через:

```text
harness.local.toml
SLIVIN_HARNESS_PYTHON
SLIVIN_CODEX_CMD
```

а не через committed task manifests.

---

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

# 5. Самый важный текущий незакрытый hardening gap

## Planned candidate paths не reconciled с actual final diff

В успешном Matrix plan были объявлены:

```text
static/js/components/datatable/selection/core.js
static/js/components/datatable/selection/bulk_edit.js
static/js/components/datatable/__tests__/selection-across-pages.test.cjs
static/js/components/datatable/__tests__/bulk-edit-filter-scope.test.cjs
```

Pre-edit baseline snapshot был снят только для них.

Во время implementation Agent самостоятельно обнаружил Distribution consumer и дополнительно изменил:

```text
static/js/distribution/index.js
static/js/distribution/__tests__/selection-stage.test.cjs
```

Это было правильное product изменение, но Controller:

- не потребовал replan до edit;
- не потребовал расширить `candidate_paths`;
- не имел pre-edit snapshot для этих поздно добавленных paths;
- не сравнил final changed paths с planned candidate surface.

### Почему это важно

Сейчас `candidate_paths` — evidence/planning declaration, но не mechanical scope boundary.

То есть возможно:

```text
Planner planned A/B
Implementer changed A/B/C
Evaluator accepted C
```

без trusted evidence о pre-edit C.

### Требуемое общее решение

Перед тем как считать evidence layer полностью hardened, нужен controller-level reconciliation.

Минимальный contract:

```text
actual changed path ∉ planned candidate_paths
→ нельзя молча продолжать
```

Возможные корректные routing:

```text
1. Implementer обнаруживает новый required path ДО edit
   → explicit change-surface expansion
   → Controller records/snapshots path while still pre-edit
   → continue

или

2. path уже изменён
   → REPLAN_REQUIRED / evidence downgrade
   → snapshot честно маркируется candidate-state-at-discovery,
     но не "pre-edit"
   → Evaluator обязан учитывать потерю pre-edit evidence
```

Не решать это простым prompt:

```text
"не меняй другие файлы"
```

Нужен mechanical diff-to-plan reconciliation.

### Priority

```text
HIGH для evidence integrity
не material defect финального Matrix product candidate
```

Перед масштабированием на Git task supervisor это желательно закрыть.

---

# 6. Другие известные незакрытые capability gaps

## 6.1. Target-project Python environment

В успешном Matrix trial Implementer хотел проверить backend token test, но:

```text
workspace не содержал .venv
system Python не содержал Django
```

Backend code при этом не менялся, поэтому это не заблокировало конкретный task.

Но для будущих backend/Django задач нужен explicit concept:

```text
Harness controller Python
!=
target-project Python/runtime
```

Текущий `{python}` — `sys.executable` Harness process.

Желательное развитие:

```text
project_python
project_pytest
project_env / test-only env
```

как trusted toolchain capabilities.

Не добавлять их до реальной backend task без необходимости, но новый чат должен знать, что gap существует.

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

Перед новым независимым trial того же case:

```bash
cd cases/matrix-all-matching/workspace

git reset --hard HEAD
git clean -fd
rm -rf .harness_tmp

git status --short
```

Должен быть clean baseline.

После этого:

```bash
cd ~/Tools/slivin-harness
./run cases/matrix-all-matching/task.toml
```

Не использовать final candidate от прошлого trial как новый baseline.

Не показывать held-out assertion Implementer внутри trial.

Если grader меняется:

```text
calibration certificate invalid
→ explicit recalibration
```

---

# 12. Current roadmap order

Состояние на момент этой записи:

```text
1. Quality-core historical milestone           DONE
2. Knowledge-base guide                        DONE
3. Architecture/decision/history docs          DONE
4. Operational README                          DONE
5. Context gap audit                           THIS FILE
6. Planned-vs-actual change-surface hardening  RECOMMENDED NEXT CORE FIX
7. GitHub Issues / task orchestration           NEXT USABILITY LAYER
8. Additional historical eval corpus            IMPORTANT
9. Browser/runtime capability                   AS TASKS REQUIRE
10. External typed observation/MCP              LATER
11. PR/CI publication                           AFTER task orchestration/quality evidence
```

Если GitHub Issues design обсуждается до пункта 6, не позволять tracker layer менять Definition of Done или Git trust boundary.

---

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
