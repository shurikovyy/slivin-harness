# Slivin Harness

Локальный Harness для coding-agent workflows поверх **Codex App Server**.

Slivin Harness управляет полным циклом задачи:

```text
clean workspace
    ↓
Characterization / Planner
    ↓
pre-edit baseline snapshot
    ↓
Implementer
    ↓
deterministic checks
    ↓
Fresh Evaluator
    ↓
repair / replan при необходимости
    ↓
held-out / runtime acceptance
    ↓
DONE
```

Главный принцип:

> Сообщение агента `PASS` не является Definition of Done.
> Задача считается завершённой только после внешних checks/evidence и независимой evaluation.

---

# 1. Статус проекта

Текущий quality-core доказал работоспособность на реальном historical benchmark Matrix all-matching selection:

```text
calibration certificate PASS
→ Planner
→ Implementer
→ deterministic checks PASS
→ Fresh Evaluator PASS
→ held-out PASS
→ HARNESS_TASK_PASS
```

Это **промежуточный milestone**, а не доказательство универсальной надёжности на любых задачах.

Следующий рост должен идти через новые реальные задачи/eval cases, а не через добавление scaffolding «на всякий случай».

---

# 2. Структура repository

```text
slivin-harness/
├── task_runner.py
│   Главный Controller/state machine.
│
├── slivin_harness/
│   ├── app_server.py
│   │   JSON-RPC adapter к Codex App Server.
│   ├── planner.py
│   │   Read-only Characterizer / Planner.
│   ├── evaluator.py
│   │   Fresh read-only independent Evaluator.
│   └── workspace.py
│       Managed Git-worktree lifecycle и result publication.
│
├── tools/
│   ├── prepare_workspace.py
│   │   One-time helper для static/legacy folder без Git baseline.
│   └── self_check.py
│       Быстрая source/config/regression самопроверка Harness.
│
├── cases/
│   └── matrix-all-matching/
│       ├── task.toml
│       └── README.md
│       # source `_90` находится вне Harness и задаётся local project profile.
│
├── examples/
│   └── project-task.example.toml
│
├── hidden_checks/
│   ├── matrix_all_matching.test.cjs
│   ├── jest.config.cjs
│   └── matrix_all_matching.calibration.json
│
├── docs/
│   ├── CURRENT_STATE.md
│   ├── ARCHITECTURE.md
│   ├── QUALITY_MODEL.md
│   ├── DECISIONS.md
│   ├── HISTORY.md
│   ├── WINDOWS_SETUP.md
│   ├── WORKSPACE_MODEL.md
│   ├── MAINTAINING_HARNESS.md
│   └── DECISION_TEMPLATE.md
│
├── harness.local.example.toml
│   Template machine/project-local configuration.
│
├── run / run.cmd
├── py / py.cmd
├── .gitignore
├── .gitattributes
├── CHANGELOG.md
└── README.md
```

`harness.local.toml`, managed worktrees и `runs/` существуют только локально и не
являются source repository content.

# 3. Что хранится в Git, а что нет

Repository хранит:

- Harness source;
- task manifests;
- hidden graders;
- calibration certificates;
- документацию;
- небольшие deterministic fixtures.

Repository **не хранит**:

- реальные project workspaces;
- `_90` / `_92` полными копиями;
- run logs/artifacts;
- `.harness_tmp`;
- secrets;
- `.env*`;
- virtualenv;
- `node_modules`;
- Python/Jest caches;
- generated protocol schemas;
- архивы.

Полный список см. в `.gitignore`.

---

# 4. Требования

## Git

Должен быть доступен из shell:

```bash
git --version
```

## Python 3.11+

Harness core не зависит от Python target project и использует только stdlib.

Launchers `run` / `py` выбирают Python так:

```text
SLIVIN_HARNESS_PYTHON
→ python3 из PATH
→ python из PATH
→ py -3
```

Проверка:

```bash
./py --version
```

Если Python не находится автоматически:

```bash
export SLIVIN_HARNESS_PYTHON="C:/path/to/python.exe"
```

Важно:

```text
Python Harness Controller
!=
Python target project
```

Project runtime задаётся отдельно через project profile/toolchain.

## Codex CLI

Harness больше не содержит machine-specific default path к Codex.

Разрешение:

```text
SLIVIN_CODEX_CMD
→ [codex].command в harness.local.toml
→ codex.cmd / codex из PATH
```

## Node / Jest / project Python

Глобальных hardcoded путей нет.

Tools задаются:

```text
[toolchain]                         # общие/static benchmark tools
[projects.<name>.toolchain]        # tools конкретного project
[toolchain] task manifest          # task-specific override
```

Bare executable names (`node`, `python`) ищутся через PATH.
Paths могут использовать `{project_root}`.

---

# 5. Windows setup

Подробности и история sandbox troubleshooting:

```text
docs/WINDOWS_SETUP.md
```

Ключевые моменты:

- administrator access не требуется;
- global `PATH` менять не обязательно;
- Harness использует unelevated Windows sandbox;
- writable temp остаётся внутри task workspace;
- Git Bash/MSYS имеет отдельные нюансы;
- EOL и executable file mode — разные Git properties.

Рекомендуемая локальная настройка Git на Windows:

```bash
git config core.filemode false
```

Committed shell launchers `run` и `py` при этом остаются `100755` в Git index.

---

# 6. Первый setup после clone

```bash
git clone <repository-url> ~/Tools/slivin-harness
cd ~/Tools/slivin-harness
```

## 6.1. Создать machine-local configuration

```bash
cp harness.local.example.toml harness.local.toml
```

`harness.local.toml` ignored Git и является **единственным основным местом для machine/project-specific paths**.

Пример общей части:

```toml
[codex]
command = "codex" # либо абсолютный user-local path

[workspace]
root = "~/.slivin-harness/workspaces"

[toolchain]
node = "node"
```

Пути поддерживают:

```text
~
$HOME / %USERPROFILE%
{home}
{harness_root}
{project_root}    # внутри project profile/toolchain
```

## 6.2. Добавить project profile

Например:

```toml
[projects.my_project]
repo = "~/Documents/my-project"
base_ref = "HEAD"
require_clean_source = true
result_mode = "apply_to_source"

[projects.my_project.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "C:/path/to/node.exe" # или "node", если он в PATH
jest = "{project_root}/node_modules/jest/bin/jest.js"

[projects.my_project.workspace]
copy_untracked = [".env"]
```

Это заменяет hardcoded project paths в Harness source/manifests.

### `copy_untracked`

Opt-in список local/ignored paths, которые копируются в disposable worktree и доступны Agent.

Например:

```toml
copy_untracked = [".env"]
```

Они:

- не попадают в candidate diff/patch;
- не копируются обратно в source repo;
- не требуют добавления в project `.gitignore`;
- **доступны для чтения Agent/model**.

Harness исключает их из Git candidate/patch, но не может обещать, что Agent никогда не выведет содержимое в собственный tool output/transcript. Поэтому список должен быть осознанным. По умолчанию `.env` не экспонируется.

`.venv` и `node_modules` обычно сюда добавлять не нужно: project toolchain может ссылаться на них прямо в source repository.

## 6.3. Проверить Harness

```bash
./py tools/self_check.py
```

Ожидается:

```text
HARNESS_SELF_CHECK_PASS
```

Self-check:

- компилирует Controller/App Server/Planner/Evaluator/workspace manager;
- парсит historical Matrix manifest;
- проверяет calibration certificate;
- запускает stdlib unit tests D-032 и managed Git-worktree lifecycle.

---

# 7. Работа с реальным проектом без копирования repository

Для обычной разработки **не нужно** копировать repository в `cases/.../workspace` и не нужно запускать `prepare_workspace.py`.

Используется Git worktree.

## 7.1. Task manifest

Пример:

```toml
version = 1

task_id = "MY_PROJECT_TASK"
project = "my_project"
workspace_mode = "git_worktree"
base_ref = "HEAD"

risk = "medium"
max_fix_cycles = 3
max_replan_cycles = 2
max_change_surface_cycles = 2
max_plan_validation_retries = 2
require_clean_git = true

prompt = """
Описание observable engineering task.
"""

[[checks]]
name = "Git diff check"
feedback = "repair"
command = ["git", "diff", "--check"]
```

Готовый template:

```text
examples/project-task.example.toml
```

## 7.2. Что делает Harness автоматически

Для `project = "..."`:

```text
source repository
      │
      ├── .venv / node_modules / .env остаются на месте
      │
      └── Git tracked HEAD
              ↓
      disposable detached Git worktree
              ↓
      Planner / Implementer / checks / Evaluator
```

Worktree создаётся в:

```text
[workspace].root
```

и содержит:

- tracked project files;
- только явно перечисленные `copy_untracked` files;
- task-local `.harness_tmp`.

Нет этапа:

```text
copy repo
→ delete .venv/node_modules
→ run
→ copy result back вручную
```

## 7.3. Result modes

### `keep_worktree`

Source working tree не меняется.

Harness сохраняет:

```text
runs/.../candidate.patch
```

и печатает путь к retained worktree.

### `apply_to_source`

После полного:

```text
checks PASS
Fresh Evaluator PASS
held-out/runtime PASS (если есть)
```

Controller автоматически применяет candidate patch к исходному working tree.

Перед apply он повторно проверяет:

```text
source HEAD не изменился
source tracked working tree не стал dirty
```

Configured `copy_untracked` paths (например `.env`) не блокируют apply и никогда не входят в patch.

Результат:

```text
Agent changes появляются в исходном project working tree
```

но Harness **не делает**:

```text
git commit
git push
branch switch
PR
```

Это отдельный future publication/orchestration layer.

## 7.4. Почему worktree лучше direct-edit source repo

Сохраняются одновременно:

- clean immutable task baseline;
- isolation от случайного user edit;
- нормальный `git diff`;
- возможность безопасного rejected candidate;
- отсутствие тяжёлого копирования `.venv/node_modules`;
- автоматическое возвращение accepted diff в source tree.

---

# 8. Historical Matrix benchmark

Matrix benchmark теперь использует тот же **managed Git-worktree mode**, что и обычные
project tasks. Специальной копии repository внутри `cases/` больше нет.

Committed case содержит только:

```text
cases/matrix-all-matching/
├── task.toml
└── README.md
```

## 8.1. Подготовить source broken baseline

Нужен отдельный Git repository с historical `_90`, например:

```text
C:/Users/<user>/Downloads/sa_icover_90
```

Он должен быть:

```text
Git repository
HEAD = historical broken baseline
git status --short = empty
```

Если `_90` получен как папка без `.git`, `prepare_workspace.py` можно использовать
**один раз**, чтобы создать baseline commit. После этого benchmark запускается напрямую
от source repository и копирование в Harness не требуется.

---

## 8.2. Настроить `matrix_baseline` local profile

В ignored `harness.local.toml`:

```toml
[workspace]
# На Windows лучше короткий root.
root = "C:/Users/<user>/.slivin/w"

[projects.matrix_baseline]
repo = "C:/Users/<user>/Downloads/sa_icover_90"
base_ref = "HEAD"
require_clean_source = true
result_mode = "keep_worktree"

[projects.matrix_baseline.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "C:/path/to/node.exe"
jest = "{project_root}/node_modules/jest/bin/jest.js"

[projects.matrix_baseline.workspace]
# Только если Agent должен видеть local .env.
copy_untracked = [".env"]
```

`{project_root}` автоматически разрешается в `repo` этого project profile.

---

## 8.3. Запустить benchmark

```bash
cd ~/Tools/slivin-harness

./py tools/self_check.py
./run cases/matrix-all-matching/task.toml
```

Harness автоматически создаёт unique detached worktree от `_90`:

```text
source `_90` HEAD
        ↓
managed worktree
        ↓
Planner / Implementer / checks / Evaluator / held-out
```

`{workspace}` внутри `task.toml` — runtime path этого worktree. Его не нужно и нельзя
заполнять вручную.

---

## 8.4. Где искать результат

Matrix case использует:

```toml
result_mode = "keep_worktree"
```

поэтому исходный `_90` после run не меняется.

Controller печатает path managed worktree, а audit artifacts сохраняются в:

```text
runs/MATRIX_DATATABLE_ALL_MATCHING_BULK_ACTION_SCOPE_BENCHMARK/<run-id>/
```

При failed run worktree сохраняется для диагностики.

---

## 8.5. Повторный independent trial

Reset старого worktree не нужен. Проверить только source baseline:

```bash
cd /path/to/sa_icover_90
git status --short
```

и снова:

```bash
cd ~/Tools/slivin-harness
./run cases/matrix-all-matching/task.toml
```

Каждый run создаёт новый worktree от того же clean source `HEAD`.

Удаление старых worktrees описано в `cases/matrix-all-matching/README.md` и
`docs/WINDOWS_SETUP.md`.

# 9. Почему нужно использовать `run`

Ненадёжный запуск:

```bash
python task_runner.py cases/matrix-all-matching/task.toml
```

из произвольной директории.

Python ищет `task_runner.py` относительно **current working directory ещё до запуска Harness**.

Например из произвольной project/worktree директории он попробует открыть:

```text
<current-directory>/task_runner.py
```

и завершится:

```text
can't open file ... task_runner.py
```

Поэтому default interface:

```bash
./run <manifest>
```

---

# 10. Launchers

## `run`

Git Bash / Linux/macOS-style launcher.

```bash
./run MANIFEST
```

Определяет root через расположение самого script и запускает:

```text
<python> <harness-root>/task_runner.py <args>
```

Python bootstrap order:

```text
SLIVIN_HARNESS_PYTHON
→ python3
→ python
→ py -3
```

Explicit override:

```bash
export SLIVIN_HARNESS_PYTHON="C:/path/to/python.exe"
```

---

## `run.cmd`

Windows `cmd.exe` launcher.

```cmd
run.cmd cases\matrix-all-matching\task.toml
```

Использует:

```text
%SLIVIN_HARNESS_PYTHON%
→ py -3
→ python
```

Project `.venv` не является bootstrap dependency Harness.

---

## `py`

Запускает utility Python command через тот же configured Python.

Примеры:

```bash
./py --version

./py tools/self_check.py

./py tools/prepare_workspace.py \
    /path/to/static-fixture
```

---

## `py.cmd`

Windows `cmd.exe` equivalent `py`.

---

# 11. `task_runner.py`

Главный Controller.

CLI на текущий момент имеет один обязательный positional argument:

```bash
task_runner.py MANIFEST
```

Рекомендуется запускать через `run`.

Прямой вызов из Harness root:

```bash
./py task_runner.py \
    cases/matrix-all-matching/task.toml
```

---

# 12. Task manifest

Основной mode — managed project task:

```toml
version = 1

task_id = "MY_TASK"
project = "my_project"
workspace_mode = "git_worktree"
base_ref = "HEAD"

risk = "medium"
max_fix_cycles = 3
max_replan_cycles = 2
max_change_surface_cycles = 2
max_plan_validation_retries = 2
require_clean_git = true

prompt = """
Описание observable задачи и preservation contract.
"""

[[checks]]
name = "Git diff check"
feedback = "repair"
command = ["git", "diff", "--check"]
timeout_seconds = 30
```

Project repository и toolchain задаются отдельно в ignored `harness.local.toml`.

Static path mode всё ещё поддерживается как **legacy/fixture escape hatch**:

```toml
workspace = "/path/to/already-prepared/static-workspace"
```

Он не является текущим Matrix workflow и не рекомендуется для обычной project
development, если source уже является Git repository.

# 13. Основные manifest-поля

## `version`

Текущая manifest schema marker.

Пример:

```toml
version = 1
```

---

## `task_id`

Читаемый идентификатор задачи.

Используется, в частности, при создании:

```text
runs/<task_id>/<timestamp>/
```

Если отсутствует, fallback — имя manifest-файла.

---

## `project`

Имя machine-local project profile из `harness.local.toml`.

```toml
project = "my_project"
```

При наличии `project` и отсутствии `workspace` Harness создаёт managed Git worktree.

---

## `workspace_mode`

Для project mode сейчас поддерживается:

```toml
workspace_mode = "git_worktree"
```

Это default при `project = "..."`.

---

## `base_ref`

Git ref, из которого создаётся detached worktree. Default:

```toml
base_ref = "HEAD"
```

Для `result_mode = "apply_to_source"` ref должен разрешаться в текущий source `HEAD`.

---

## `result_mode`

Можно задать в task manifest либо в `[projects.<name>]` local profile:

```text
keep_worktree
apply_to_source
```

`apply_to_source` применяется только после полного Harness PASS и не делает commit.

---

## `workspace`

Legacy/static workspace path для historical/fixture mode.

```toml
workspace = "/path/to/already-prepared/static-fixture"
```

Если задан `workspace`, managed project worktree не создаётся.

---

## `risk`

Допустимо:

```text
low
medium
high
```

### `low`

Planner/Fresh Evaluator могут быть пропущены Controller.

Использовать только для действительно низкорисковой механики.

### `medium`

Default для нетривиального behavior:

```text
Planner
→ Implementer
→ checks
→ Fresh Evaluator
```

### `high`

Harness принимает значение, но высокий риск означает, что для реальной задачи могут требоваться дополнительные integration/runtime/human gates, которых одного current core недостаточно.

---

## `max_fix_cycles`

Максимум autonomous repair cycles.

Default:

```text
3
```

---

## `max_replan_cycles`

Максимум циклов:

```text
Evaluator → REPLAN_REQUIRED → Planner
```

Default:

```text
2
```

---

## `max_change_surface_cycles`

Максимум циклов machine-enforced reconciliation:

```text
actual changed paths outside candidate_paths
→ rollback unexpected paths
→ Planner replan
→ path-local baseline snapshot
→ Implementer reapply
```

По умолчанию равен `max_replan_cycles`, но счётчик отдельный: обнаружение нового material consumer не расходует budget Fresh Evaluator → `REPLAN_REQUIRED`.

---

## `max_plan_validation_retries`

Сколько раз Planner может пересобрать invalid structured artifact после Controller validation failure.

Default:

```text
2
```

---

## `require_clean_git`

Default:

```toml
true
```

Если `true`, Harness отказывается запускать implementation поверх dirty workspace.

Для нормальной разработки оставлять включённым.

---

## `prompt`

Исходный user/task intent.

Важно:

- описывать observable behavior;
- preservation requirements;
- запрещённые scope expansions;
- не подсказывать known implementation answer исторического benchmark.

---

# 14. Explicit skills

Manifest может содержать:

```toml
skills = [
    "some-repo-skill",
]
```

Harness:

1. делает `skills/list` через App Server;
2. проверяет, что repo skill обнаружен и enabled;
3. передаёт его turn'у явно.

Различать:

```text
skill discovered
!=
skill definitely auto-used by model
```

Если skill нужен как обязательный contract — указывать его явно.

---

# 15. Checks

Каждый check:

```toml
[[checks]]
name = "..."
feedback = "repair"
command = ["..."]
timeout_seconds = 120
```

---

## `feedback = "repair"`

Failure возвращается Implementer:

```text
check FAIL
→ repair prompt
→ checks again
→ fresh evaluation
```

Используется для обычных deterministic gates.

---

## `feedback = "heldout"`

Check запускается только после готовности candidate.

Failure:

```text
HARNESS_HELDOUT_FAIL
```

и **не передаётся Implementer как tutoring feedback в том же trial**.

Используется для historical/independent acceptance.

---

# 16. Command placeholders

В `command` доступны:

```text
{workspace}
{harness_root}
{python}
```

а также keys из toolchain, например:

```text
{node}
{jest}
```

Пример:

```toml
command = [
    "{node}",
    "{jest}",
    "--config",
    "{harness_root}/hidden_checks/jest.config.cjs",
]
```

### `{python}`

Это Python process, которым запущен `task_runner.py` (`sys.executable`).

### Custom placeholder

Можно добавить через local config или manifest:

```toml
[toolchain]
some_tool = "/absolute/path/to/tool"
```

и использовать:

```text
{some_tool}
```

---

# 17. Toolchain precedence

Итоговый toolchain строится:

```text
[toolchain] global local config
    ↓
[projects.<name>.toolchain]
    ↓
task manifest [toolchain]
```

Hardcoded project defaults в source отсутствуют. Task manifest имеет наивысший
приоритет для конкретной задачи.

Все итоговые paths должны существовать.

---

# 18. Local config

Default file:

```text
harness.local.toml
```

Override:

```bash
export SLIVIN_HARNESS_CONFIG="/path/to/another-config.toml"
```

Пример:

```toml
[codex]
command = "codex" # либо absolute user-local path

[workspace]
root = "~/.slivin-harness/workspaces"

[projects.my_project]
repo = "~/Documents/my-project"
result_mode = "apply_to_source"

[projects.my_project.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "node"
jest = "{project_root}/node_modules/jest/bin/jest.js"
```

---

# 19. Codex path precedence

Codex command выбирается:

```text
SLIVIN_CODEX_CMD
    ↓
[codex].command в harness.local.toml
    ↓
codex.cmd / codex из PATH
```

Override:

```bash
export SLIVIN_CODEX_CMD="/path/to/codex.cmd"
```

---

# 20. Oracle calibration

Historical benchmark может использовать один из двух режимов.

## Calibration certificate

Текущий Matrix case:

```toml
[benchmark]
calibration_certificate = "hidden_checks/matrix_all_matching.calibration.json"
confirm_current_baseline_broken = true
```

Harness проверяет:

- check definition hash;
- held-out file hashes;
- certificate attestation:
  ```text
  broken = FAIL
  good = PASS
  ```

Если grader/check изменился — run блокируется до recalibration.

---

## Live calibration

Поддерживается Controller:

```toml
[benchmark]
calibrate_heldout = true
good_reference_workspace = "cases/.../reference_good"
```

В этом режиме перед App Server:

```text
held-out(broken workspace) → должен FAIL
held-out(good reference)   → должен PASS
```

Нельзя одновременно использовать live calibration и certificate.

Для постоянного Matrix benchmark используется certificate, чтобы не держать `_92` рядом с агентом.

---

# 21. Что происходит при запуске medium-risk task

Типичный console lifecycle:

```text
TASK_STARTED
TASK / WORKSPACE / RUN_DIR
LOCAL_CONFIG
CODEX_CMD / TOOLCHAIN
ORACLE CALIBRATION

APP_SERVER
REPO CONTEXT

=== PLANNING ===
heartbeat...
PLAN RESULT

=== BASELINE SNAPSHOT ===

=== IMPLEMENTATION ===

=== DETERMINISTIC CHECKS ===

=== FRESH EVALUATION ===
heartbeat...
EVALUATION RESULT

=== HELD-OUT EVALUATION ===

HARNESS_TASK_PASS
TOTAL_ELAPSED
```

---

# 22. Planner validation retry

Planner structured artifact дополнительно проверяется Controller.

Planner/Controller handoff использует protocol `planner.v2`.

Planner больше не формирует свободный `release_obligations` list. Вместо этого он
ставит boolean `release_critical` у CC/INT, а Controller детерминированно строит exact
blocking IDs из всех LIFE/REP/AUTH/CONS/PRES/TEST + critical CC/INT.

Например блокируются:

- malformed/duplicate IDs;
- legacy/free-form `release_obligations`;
- unsafe narrowing assumption без достаточного evidence;
- invalid `release_critical`;
- `READY` без required candidate paths;
- `NEEDS_USER_DECISION` без доказанного decision escalation.

После validation Controller публикует отдельный `PLAN CONTRACT`:

```text
protocol_version
plan_fingerprint
blocking_obligation_ids
candidate_paths
```

Evaluator использует `evaluator.v2`; его obligation/assumption IDs и
`plan_fingerprint` schema-bound к exact текущему plan.

Если artifact invalid:

```text
INVALID PLAN SUMMARY
→ structured PLAN VALIDATION ERROR
→ fresh Planner attempt
```

Полный invalid JSON сохраняется в `runs/`, но в retry передаётся compact structured
feedback, а не огромный malformed artifact. См. `docs/HANDOFF_PROTOCOL.md`.

до:

```text
max_plan_validation_retries
```

---

# 23. Repair loop

Если deterministic repair-check упал:

```text
=== REPAIR CYCLE N: CHECK FAILURES ===
```

Implementer получает exact failure evidence.

После изменения:

```text
все repair checks запускаются заново
```

---

# 24. Evaluator routing

Evaluator может вернуть:

## `PASS`

Переход к held-out/runtime acceptance либо Done.

## `FINDINGS`

Проблема candidate:

```text
Implementer repair
→ checks
→ NEW Fresh Evaluator
```

## `REPLAN_REQUIRED`

Проблема planning/characterization:

```text
Planner
→ validation
→ NEW Fresh Evaluator
```

## `BLOCKED`

Недоступно обязательное technical evidence/capability.

## `NEEDS_USER_DECISION`

Нужно настоящее product/business rule, которое нельзя корректно вывести из current contract/lifecycle.

---

# 25. Exit codes

Текущая реализация использует:

| Code | Значение |
|---:|---|
| `0` | `HARNESS_TASK_PASS` |
| `1` | task fail / исчерпан repair или replan budget |
| `2` | `HARNESS_TASK_BLOCKED` |
| `3` | `HARNESS_TASK_NEEDS_USER_DECISION` |
| `4` | `HARNESS_HELDOUT_FAIL` |
| `99` | internal Harness exception |

Эти codes — operational contract текущей реализации; если они будут использоваться внешним supervisor/CI, изменение нужно документировать как interface change.

---

# 26. Debug mode

По умолчанию unexpected internal exception печатается как:

```text
HARNESS_INTERNAL_ERROR: ...
```

и process выходит `99`.

Чтобы получить Python traceback:

```bash
export SLIVIN_HARNESS_DEBUG=1
./run cases/.../task.toml
```

---

# 27. Run artifacts

Каждый запуск создаёт:

```text
runs/<task_id>/<timestamp>/
```

Там могут быть:

- manifest snapshot;
- preflight;
- repo context;
- planning attempts;
- validation errors;
- baseline snapshot;
- deterministic check outputs;
- evaluation artifacts;
- held-out/calibration verification.

`runs/` не коммитится.

Использовать его при debugging/audit вместо копирования всей console history в source repository.

---

# 28. Heartbeat

Planner/Evaluator могут долго не иметь user-facing output.

Harness показывает:

```text
[PLANNING] working...
elapsed=06:10
app-server=alive
last-event=00:03
```

Поля:

- `elapsed` — длительность turn;
- `app-server=alive` — process жив;
- `last-event` — сколько прошло с последней protocol activity.

Это позволяет отличать model latency от умершего App Server.

---

# 29. Repo instructions и skills

При старте Harness печатает:

```text
=== REPO CONTEXT ===

AGENTS:
...

REPO SKILLS:
...

EXPLICIT_ACTIVE_SKILLS:
...
```

Harness проверяет discovery repository skills через App Server.

Но:

```text
AUTO_SKILL_USAGE: not asserted
```

означает:

> Нельзя доказать автоматическое применение skill только по факту его наличия.

Для обязательного skill используйте manifest `skills = [...]`.

---

# 30. Sandbox и temp

Implementer работает с `workspace-write`.

Planner/Evaluator — read-only.

App Server запускается с Windows-specific sandbox config, исключающим внешние temp roots.

Runtime temp:

```text
<workspace>/.harness_tmp/
```

Harness checks также получают:

```text
TEMP
TMP
TMPDIR
XDG_CACHE_HOME
NPM_CONFIG_CACHE
PYTHONDONTWRITEBYTECODE
```

на task-local paths.

Подробности:

```text
docs/WINDOWS_SETUP.md
```

---

# 31. Типовые ошибки

## `.env files are present`

Эта ошибка относится к `prepare_workspace.py` в static/legacy mode.

По умолчанию script fail-closed при real `.env*`. Если visibility осознанно разрешена:

```bash
./py tools/prepare_workspace.py WORKSPACE --allow-env
```

В managed project mode `.env` задаётся через explicit local opt-in:

```toml
[projects.my_project.workspace]
copy_untracked = [".env"]
```

---

## `Source repository is not a Git repository`

Managed worktree требует Git source repository.

Если historical snapshot получен без `.git`, можно один раз создать baseline через
`prepare_workspace.py`, затем использовать этот repository как `[projects.<name>].repo`.

---

## `Source repository is not clean`

При `require_clean_source = true` source HEAD должен быть clean относительно tracked
product files.

Проверить:

```bash
cd /path/to/source
git status --short
```

Explicit `copy_untracked` paths вроде `.env` обрабатываются отдельно.

---

## `.harness_tmp/ must be ignored`

Обычно относится к static/legacy workspace. Managed worktree сам настраивает task-local
runtime ignore.

---

## `Codex CLI not found`

Создать/исправить:

```toml
[codex]
command = "C:/path/to/codex.cmd"
```

в `harness.local.toml` либо задать `SLIVIN_CODEX_CMD`.

---

## `Toolchain executable/file does not exist`

Проверить project-specific local config:

```toml
[projects.my_project.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "..."
jest = "{project_root}/node_modules/jest/bin/jest.js"
```

---

## `Unknown command placeholder`

Manifest использует `{some_tool}`, но такой key отсутствует в resolved toolchain.

Добавить его в global/project/task `[toolchain]`.

---

## `Held-out check definition changed since calibration`

Historical grader или его check definition изменились.

Calibration certificate больше не действителен. Нужна explicit recalibration, а не
ручное отключение guard.

---

## `can't open file ... task_runner.py`

Использован относительный `task_runner.py` из неправильного CWD.

Использовать:

```bash
./run ...
```

или absolute Harness launcher.

---

## Битая кириллица / `UnicodeEncodeError: 'charmap'`

Windows/Git Bash может дать legacy console encoding.

Текущие launchers и Controller принудительно используют UTF-8. Проверка:

```bash
./py -c "import sys; print(sys.stdout.encoding); print('Русский текст → UTF-8')"
```

Подробности: `docs/WINDOWS_SETUP.md`.

---

## `Filename too long` при удалении старого worktree

Текущий Harness сокращает filesystem segments и включает repository-local long-path
support на Windows.

Для worktree, созданного старой версией, см. cleanup procedure в
`docs/WINDOWS_SETUP.md`.

---

## `modified: py / run` на Windows при одинаковом содержимом

Проверить:

```bash
git ls-files --eol py run
git diff --summary -- py run
```

Если причина `100755 → 100644`, это file mode, а не EOL.

Local setting:

```bash
git config core.filemode false
```

Committed launchers должны оставаться executable в Git index.

# 32. Создание нового project task

Для обычной разработки project repository вручную не копируется.

## Шаг 1. Добавить local project profile

В ignored `harness.local.toml`:

```toml
[projects.my_project]
repo = "~/Documents/my-project"
base_ref = "HEAD"
require_clean_source = true
result_mode = "apply_to_source"

[projects.my_project.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
```

## Шаг 2. Создать manifest

Начать с:

```text
examples/project-task.example.toml
```

Определить:

- `project`;
- task intent;
- `risk`;
- preservation requirements;
- deterministic checks;
- repair vs held-out checks;
- target-project toolchain requirements.

## Шаг 3. Запустить

```bash
./run path/to/my-task.toml
```

Harness сам создаст isolated Git worktree.

## Static/legacy fixture

Static `workspace = ...` оставлен только для случаев, где intentionally нужен уже
подготовленный filesystem fixture и managed Git source repository неприменим.

Это **не** текущая схема Matrix `_90`.

# 33. Production task без `_92`

Known-good reference нужен только historical eval/calibration.

Для обычной новой задачи никакого `_92` нет и не требуется.

Confidence строится через:

```text
current contract
→ reproduction/evidence
→ Planner
→ implementation
→ deterministic checks
→ Preservation / LIFE / REP / AUTH evidence
→ Fresh Evaluator
→ runtime/integration acceptance при необходимости
```

---

# 34. Historical benchmark без full good reference

Текущий Matrix case использует:

```text
broken `_90` source Git repository
+
managed detached worktree per trial
+
held-out grader
+
hash-bound calibration certificate
```

Полная `_92` рядом с Agent не требуется.

Known-good использовался только при explicit calibration grader. Если grader/check
definition меняется, certificate инвалидируется и требует recalibration.

# 35. Разработка самого Harness

Перед изменением:

```bash
git status --short
```

После coherent change:

```bash
./py tools/self_check.py
```

Для quality/controller change дополнительно нужен соответствующий smoke/historical eval.

Версионирование:

```text
Git commits
Git tags
CHANGELOG
```

Не создавать:

```text
task_runner_v047.py
planner_old.py
slivin-harness-v0.4.6.2.zip
```

как рабочий version-control mechanism.

---

# 36. Документация

Если нужно понять **как пользоваться**:

```text
README.md
```

Если нужно понять **как устроено**:

```text
docs/ARCHITECTURE.md
```

Почему текущая quality model такая:

```text
docs/QUALITY_MODEL.md
```

Почему принимались конкретные решения:

```text
docs/DECISIONS.md
```

Как мы до этого дошли:

```text
docs/HISTORY.md
```

Windows/sandbox:

```text
docs/WINDOWS_SETUP.md
```

Workspace/Git/security:

```text
docs/WORKSPACE_MODEL.md
```

Как развивать Harness дальше:

```text
docs/MAINTAINING_HARNESS.md
```

---

# 37. Что пока сознательно не включено

Текущий repository ещё не является полной autonomous engineering platform.

Отложены отдельными следующими слоями:

- GitHub Issues / task supervisor;
- automatic commit/push/PR;
- CI integration;
- browser/Playwright runtime;
- production read-only DB/1C/Airflow;
- typed MCP;
- deployment automation;
- постоянный specialist-review fan-out.

Почему:

> Сначала был необходим доказанный quality-core. Первый real historical milestone уже получен; дальнейшие слои должны добавляться по отдельным требованиям и eval evidence.

---

# 38. Quick start — реальный project

```bash
cd ~/Tools/slivin-harness

# 1. One-time machine config
cp harness.local.example.toml harness.local.toml
# edit [codex], [workspace], [projects.<name>], project toolchain

# 2. Sanity check
./py tools/self_check.py

# 3. Create/copy a project task manifest
cp examples/project-task.example.toml my-task.toml
# edit project=..., prompt, checks

# 4. Run
./run my-task.toml
```

При `result_mode = "apply_to_source"` accepted diff появится в исходном project working tree автоматически.

Никакого manual repository copy/cleanup/copy-back нет.

---

# 39. Quick start — historical Matrix benchmark

One-time local configuration:

```toml
[projects.matrix_baseline]
repo = "C:/path/to/sa_icover_90"
base_ref = "HEAD"
require_clean_source = true
result_mode = "keep_worktree"

[projects.matrix_baseline.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "C:/path/to/node.exe"
jest = "{project_root}/node_modules/jest/bin/jest.js"
```

Затем каждый trial:

```bash
cd ~/Tools/slivin-harness

./py tools/self_check.py
./run cases/matrix-all-matching/task.toml
```

Никакого copy → cleanup → reset `cases/.../workspace` больше нет.

`_90` остаётся immutable source baseline, а каждый run получает новый managed worktree.

# 40. Куда смотреть при следующем изменении

Перед изменением architecture/quality layer:

```text
docs/MAINTAINING_HARNESS.md
docs/DECISIONS.md
docs/HISTORY.md
```

Правило проекта:

```text
observable failure
→ root cause
→ general capability
→ eval evidence
→ documentation
→ Git commit
```

а не:

```text
ещё один case-specific prompt/test
→ ещё одна копия source file
```


---

# 41. Повторный запуск historical case

В managed Matrix mode старый worktree не является baseline следующего trial.

Перед повторным запуском достаточно:

```bash
cd /path/to/sa_icover_90
git status --short
```

Source должен быть clean.

Затем:

```bash
cd ~/Tools/slivin-harness
./run cases/matrix-all-matching/task.toml
```

Harness создаст новый unique detached worktree.

Старый worktree:

- можно оставить как audit artifact;
- либо удалить через `git worktree remove --force <path>` из source repository;
- на Windows старые очень длинные paths могут требовать procedure из
  `docs/WINDOWS_SETUP.md`.

Не делать новый baseline commit из candidate прошлого trial.

# 42. Текущий Git permission contract

Task-agent работает в disposable workspace, но по умолчанию **не владеет Git history/publication**.

Без отдельного orchestration решения не поручать Agent автоматически:

```text
git switch / checkout branch
git branch
git commit
git push
PR creation
```

Будущий GitHub Issues/task layer должен вводить это как отдельную capability/trust boundary, а не как побочный эффект `workspace-write`.

---

# 43. Harness Python и project Python

Placeholder:

```text
{python}
```

означает Python, которым запущен Harness (`sys.executable`).

Он не гарантирует наличие dependencies target project.

Например Django backend task может требовать отдельный:

```text
project_python
project_pytest
```

toolchain entry.

В текущем Matrix historical run backend token test не запускался именно потому, что usable Django runtime не был доступен из workspace/system Python; backend code при этом не менялся.

---

# 44. Current continuation state

Перед продолжением архитектурной разработки в новой сессии прочитать:

```text
docs/CURRENT_STATE.md
```

Там зафиксированы:

- текущий milestone;
- open hardening gaps;
- Git trust boundary;
- target-project runtime gap;
- следующий рекомендуемый шаг.


---

# 44. Windows Git Bash: битая кириллица / `UnicodeEncodeError: 'charmap'`

Harness console contract — UTF-8.

Launchers `run` / `py` set:

```text
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
```

а Controller дополнительно reconfigure'ит stdout/stderr в UTF-8.

Это нужно потому, что Windows Python под Git Bash иногда стартует с legacy
ANSI/charmap encoding. Симптомы:

```text
Русский текст → ▒▒▒▒
UnicodeEncodeError: 'charmap' codec can't encode character ...
```

При managed worktree run Harness также всегда печатает:

```text
MANAGED_WORKTREE_ON_EXIT: ...
```

даже если task завершился ошибкой. `runs/.../workspace_session.json` содержит
тот же путь для последующего audit.
