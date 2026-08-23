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
│   │
│   ├── planner.py
│   │   Read-only Characterizer / Planner.
│   │
│   ├── evaluator.py
│   │   Fresh read-only independent Evaluator.
│   │
│   └── __init__.py
│
├── tools/
│   ├── prepare_workspace.py
│   │   Подготавливает disposable project workspace/baseline.
│   │
│   └── self_check.py
│       Проверяет Harness source и committed Matrix manifest.
│
├── cases/
│   └── matrix-all-matching/
│       ├── task.toml
│       ├── README.md
│       └── workspace/              # локально, Git Harness его игнорирует
│
├── hidden_checks/
│   ├── matrix_all_matching.test.cjs
│   ├── jest.config.cjs
│   └── matrix_all_matching.calibration.json
│
├── docs/
│   ├── README.md
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
│   Пример machine-local paths.
│
├── run / run.cmd
│   CWD-independent запуск task runner.
│
├── py / py.cmd
│   CWD-independent Python launcher для utility scripts.
│
├── .gitignore
├── .gitattributes
├── CHANGELOG.md
└── README.md
```

---

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

# 8. Первый historical Matrix benchmark

Текущий repository содержит один основной historical case:

```text
cases/matrix-all-matching/
```

Полный project snapshot намеренно не коммитится.

## 8.1. Скопировать broken baseline

Содержимое исторического `_90` поместить непосредственно в:

```text
cases/matrix-all-matching/workspace/
```

Правильно:

```text
workspace/
├── AGENTS.md
├── static/
├── api/
├── tools/
└── ...
```

Неправильно:

```text
workspace/
└── project_snapshot_90/
    ├── static/
    └── ...
```

Если исходная копия содержит `.git`, его лучше не переносить.

---

## 8.2. Решить, должен ли Agent видеть `.env`

Для historical/static preparation безопасный default остаётся fail-closed:

```bash
./py tools/prepare_workspace.py WORKSPACE
# real .env* → stop
```

Если для конкретного benchmark вы осознанно разрешаете Agent читать `.env`:

```bash
./py tools/prepare_workspace.py WORKSPACE --allow-env
```

Файл будет ignored baseline Git и не попадёт в candidate diff, но его содержимое
будет доступно Agent/model.

Для обычного managed-project режима предпочтительнее не копировать весь repository,
а использовать `[projects.<name>.workspace].copy_untracked = [".env"]`.

---

## 8.3. Подготовить inner baseline repository

```bash
./py tools/prepare_workspace.py \
    cases/matrix-all-matching/workspace
```

Скрипт:

1. удаляет только generated caches/runtime temp;
2. по умолчанию блокирует реальные `.env*`, либо разрешает их через `--allow-env`;
3. создаёт inner Git repo, если `.git` отсутствует;
4. задаёт local baseline author;
5. добавляет Harness runtime patterns в inner `.git/info/exclude`;
6. делает первый baseline commit;
7. либо проверяет, что уже существующий inner repo clean;
8. проверяет `.harness_tmp` ignore.

Ожидаемый новый workspace:

```text
slivin-harness/.git
    Git самого Harness.

cases/matrix-all-matching/workspace/.git
    Отдельный disposable Git baseline проекта.
```

### Параметры `prepare_workspace.py`

```bash
./py tools/prepare_workspace.py WORKSPACE
```

Опционально:

```bash
--commit-message "..."
--allow-env
```

Default:

```text
harness benchmark baseline
```

Пример:

```bash
./py tools/prepare_workspace.py \
    cases/matrix-all-matching/workspace \
    --commit-message "matrix broken baseline"
```

### Важная особенность

`prepare_workspace.py` удаляет только generated/runtime cache directories, например:

```text
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
.harness_tmp
coverage
.jest-cache*
```

`.venv`, `venv`, `env` и `node_modules` больше не удаляются этим script. Они
добавляются в inner exclude policy и не попадают в historical baseline commit.

Для обычной разработки этот script вообще не нужен: managed Git-worktree mode
создаёт isolated workspace напрямую из source Git repository.

---

## 8.4. Проверить baseline вручную

```bash
cd cases/matrix-all-matching/workspace

git rev-parse --show-toplevel
git status --short
git log -1 --oneline
git check-ignore -v .harness_tmp/
```

`git status --short` должен быть пустым.

Вернуться:

```bash
cd ~/Tools/slivin-harness
```

---

## 8.5. Запустить benchmark

Из Harness root:

```bash
./run cases/matrix-all-matching/task.toml
```

Или из любой другой директории:

```bash
~/Tools/slivin-harness/run \
    cases/matrix-all-matching/task.toml
```

`run` сам определяет Harness root.

---

# 9. Почему нужно использовать `run`

Ненадёжный запуск:

```bash
python task_runner.py cases/matrix-all-matching/task.toml
```

из произвольной директории.

Python ищет `task_runner.py` относительно **current working directory ещё до запуска Harness**.

Например из:

```text
cases/matrix-all-matching/workspace/
```

он попробует открыть:

```text
cases/matrix-all-matching/workspace/task_runner.py
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
    cases/matrix-all-matching/workspace
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

Есть два workspace mode.

### Managed project task — рекомендуемый для обычной разработки

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
Описание задачи.
"""

[[checks]]
name = "Git diff check"
feedback = "repair"
command = ["git", "diff", "--check"]
timeout_seconds = 30
```

### Static workspace — historical/fixture mode

```toml
workspace = "cases/my-task/workspace"
```

Static mode не создаёт worktree автоматически и сохраняется для historical eval cases.

---

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
workspace = "cases/matrix-all-matching/workspace"
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

Например блокируются:

- duplicate obligation IDs;
- unsafe narrowing assumption без достаточного evidence;
- invalid release obligations;
- `READY` без required candidate paths;
- `NEEDS_USER_DECISION` без доказанного decision escalation.

Если artifact invalid:

```text
INVALID PLAN ARTIFACT
→ validation error
→ fresh Planner attempt
```

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

Пример:

```text
Refusing to prepare workspace while .env files are present
```

Причина:

реальный secret физически находится в agent workspace.

Решение:

удалить `.env` **из benchmark/task copy**, не из оригинального project repository.

---

## `Workspace is not a Git repository`

Не выполнен `prepare_workspace.py` либо workspace path неправильный.

---

## `Workspace is not clean`

Inner project Git содержит изменения.

Проверить:

```bash
cd <workspace>
git status --short
```

Harness не смешивает existing user changes с agent changes.

---

## `.harness_tmp/ must be ignored`

Inner repository не имеет Harness exclude rules.

Обычно решается повторным:

```bash
./py tools/prepare_workspace.py <workspace>
```

---

## `Codex CLI not found`

Создать:

```bash
cp harness.local.example.toml harness.local.toml
```

и указать правильный:

```toml
[codex]
command = "..."
```

---

## `Toolchain executable/file does not exist`

Проверить local config:

```toml
[toolchain]
node = "..."
jest = "..."
```

---

## `Unknown command placeholder`

Manifest использует:

```text
{some_tool}
```

но `some_tool` не объявлен.

Добавить его в:

```toml
[toolchain]
some_tool = "..."
```

---

## `Held-out check definition changed since calibration`

Grader или `[[checks]]` definition изменились.

Calibration certificate больше не действителен.

Не обходить guard вручную: требуется explicit recalibration.

---

## `can't open file ... task_runner.py`

Использовался относительный `task_runner.py` из неправильного CWD.

Использовать:

```bash
./run ...
```

или absolute Harness launcher.

---

## `modified: py / run` на Windows при одинаковом содержимом

Проверить:

```bash
git ls-files --eol py run
git diff --summary -- py run
```

Если причина:

```text
100755 → 100644
```

это file mode, а не EOL.

Local Windows setting:

```bash
git config core.filemode false
```

Committed launchers должны храниться executable в index.

---

# 32. Создание нового project task

Для обычной разработки достаточно manifest-файла. Project repository вручную не копируется.

Например:

```text
tasks/
└── my-task.toml
```

Можно хранить task manifests и в другом committed/ignored каталоге — Controllerу нужен только путь к TOML.

## Шаг 1. Убедиться, что project profile есть локально

```toml
[projects.my_project]
repo = "~/Documents/my-project"
result_mode = "apply_to_source"

[projects.my_project.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
```

## Шаг 2. Создать manifest

Начать можно с:

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

## Historical/fixture case

Если нужен frozen historical snapshot вроде Matrix `_90`, используется старый static layout:

```text
cases/my-case/
├── task.toml
├── README.md
└── workspace/
```

и при необходимости:

```bash
./py tools/prepare_workspace.py cases/my-case/workspace
```

---

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

Текущий Matrix case использует calibration certificate.

Это позволяет после clone иметь:

```text
broken `_90` workspace
+
held-out grader
+
certificate
```

без локальной полной `_92`.

Если grader изменился — certificate специально перестанет проходить.

---

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

Historical case остаётся специальным frozen-fixture workflow:

```bash
cd ~/Tools/slivin-harness

./py tools/self_check.py

# Один раз положить _90 в:
# cases/matrix-all-matching/workspace/

./py tools/prepare_workspace.py \
    cases/matrix-all-matching/workspace

./run cases/matrix-all-matching/task.toml
```

Если в historical copy `.env` должен быть доступен Agent:

```bash
./py tools/prepare_workspace.py \
    cases/matrix-all-matching/workspace \
    --allow-env
```

---

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

После task workspace содержит candidate changes.

Перед новым **независимым** trial вернуть inner repo к baseline:

```bash
cd ~/Tools/slivin-harness/cases/matrix-all-matching/workspace

git reset --hard HEAD
git clean -fd
rm -rf .harness_tmp

git status --short
```

Status должен быть пустым.

Затем:

```bash
cd ~/Tools/slivin-harness
./run cases/matrix-all-matching/task.toml
```

Не создавать новый baseline commit из результата прошлого trial.

---

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
