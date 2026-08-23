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

## Обязательно

### Git

Должен быть доступен из shell:

```bash
git --version
```

### Python 3.11+

Harness использует стандартный `tomllib`, поэтому Python должен быть не старше 3.11.

На текущей Windows-машине launcher по умолчанию использует:

```text
~/Documents/sa_icover/.venv/Scripts/python.exe
```

Проверка:

```bash
./py --version
```

### Codex CLI

Текущий default:

```text
~/Tools/codex-cli/node_modules/.bin/codex.cmd
```

Проверка:

```bash
~/Tools/codex-cli/node_modules/.bin/codex.cmd --version
~/Tools/codex-cli/node_modules/.bin/codex.cmd login status
```

### Node

Default:

```text
~/Tools/node/node.exe
```

### Jest

Default:

```text
~/Documents/sa_icover/node_modules/jest/bin/jest.js
```

---

# 5. Windows setup

Подробная история и troubleshooting:

```text
docs/WINDOWS_SETUP.md
```

Ключевые моменты:

- administrator access не требуется;
- Codex CLI может быть user-local;
- global `PATH` менять не обязательно;
- Harness использует unelevated Windows sandbox;
- task temp направляется внутрь `.harness_tmp`;
- Git Bash/MSYS имеет отдельные нюансы;
- Windows file mode и EOL — разные проблемы.

## Рекомендуемая локальная настройка Git на Windows

```bash
git config core.filemode false
```

При этом committed shell launchers `run` и `py` должны оставаться executable в Git index (`100755`), чтобы Linux/macOS clones получали правильный mode.

---

# 6. Первый setup после clone

```bash
git clone <repository-url> ~/Tools/slivin-harness
cd ~/Tools/slivin-harness
```

## 6.1. Проверить Python launcher

```bash
./py --version
```

Если default Python не существует, временно:

```bash
export SLIVIN_HARNESS_PYTHON="/path/to/python"
```

или в Git Bash на Windows:

```bash
export SLIVIN_HARNESS_PYTHON="$HOME/Documents/sa_icover/.venv/Scripts/python.exe"
```

`SLIVIN_HARNESS_PYTHON` используется только launchers `run` / `py`.

---

## 6.2. Настроить machine-local Codex/Node/Jest paths

Если default paths подходят — ничего делать не нужно.

Если отличаются:

```bash
cp harness.local.example.toml harness.local.toml
```

Пример:

```toml
[codex]
command = "~/Tools/codex-cli/node_modules/.bin/codex.cmd"

[toolchain]
node = "~/Tools/node/node.exe"
jest = "~/Documents/sa_icover/node_modules/jest/bin/jest.js"
```

`harness.local.toml` не коммитится.

---

## 6.3. Запустить self-check

```bash
./py tools/self_check.py
```

Ожидается:

```text
HARNESS_SELF_CHECK_PASS
```

`self_check.py`:

- компилирует `task_runner.py`;
- компилирует App Server adapter;
- компилирует Planner;
- компилирует Evaluator;
- парсит committed Matrix manifest;
- проверяет calibration certificate path.

Это **не полный integration test Harness**, а быстрый source/config sanity check.

---

# 7. Первый historical Matrix benchmark

Текущий repository содержит один основной historical case:

```text
cases/matrix-all-matching/
```

Полный project snapshot намеренно не коммитится.

## 7.1. Скопировать broken baseline

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
└── sa_icover_90/
    ├── static/
    └── ...
```

Если исходная копия содержит `.git`, его лучше не переносить.

---

## 7.2. Удалить реальные secrets

Agent workspace не должен содержать:

```text
.env
.env.local
.env.prod
private keys
credentials
```

Важно:

> `.gitignore` не защищает secret от чтения агентом.

Разрешённые template-файлы:

```text
.env.example
.env.sample
.env.template
```

---

## 7.3. Подготовить inner baseline repository

```bash
./py tools/prepare_workspace.py \
    cases/matrix-all-matching/workspace
```

Скрипт:

1. удаляет generated caches;
2. блокирует реальные `.env*`;
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

`prepare_workspace.py` удаляет generated/runtime directories, включая:

```text
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
.harness_tmp
node_modules
coverage
.jest-cache*
```

Это сделано для disposable historical workspace.

Если конкретная реальная задача требует **workspace-local `node_modules`**, не применять preparation script механически без понимания последствий. Текущий Matrix benchmark использует внешний trusted Node/Jest toolchain.

---

## 7.4. Проверить baseline вручную

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

## 7.5. Запустить benchmark

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

# 8. Почему нужно использовать `run`

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

# 9. Launchers

## `run`

Git Bash / Linux/macOS-style launcher.

```bash
./run MANIFEST
```

Определяет root через расположение самого script и запускает:

```text
<python> <harness-root>/task_runner.py <args>
```

Default Python:

```text
~/Documents/sa_icover/.venv/Scripts/python.exe
```

Override:

```bash
export SLIVIN_HARNESS_PYTHON="/path/to/python"
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
```

или default:

```text
%USERPROFILE%\Documents\sa_icover\.venv\Scripts\python.exe
```

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

# 10. `task_runner.py`

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

# 11. Task manifest

Минимальная структура:

```toml
version = 1

task_id = "MY_TASK"
workspace = "cases/my-task/workspace"

risk = "medium"

max_fix_cycles = 3
max_replan_cycles = 2
max_plan_validation_retries = 2
require_clean_git = true

prompt = """
Описание задачи.
"""

[[checks]]
name = "Git diff check"
feedback = "repair"
command = [
    "git",
    "diff",
    "--check",
]
timeout_seconds = 30
```

---

# 12. Основные manifest-поля

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

## `workspace`

Путь к task workspace.

Относительный путь вычисляется от Harness root.

Пример:

```toml
workspace = "cases/matrix-all-matching/workspace"
```

Можно использовать абсолютный путь.

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

# 13. Explicit skills

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

# 14. Checks

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

# 15. Command placeholders

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

# 16. Toolchain precedence

Итоговый toolchain строится:

```text
built-in defaults
    ↓
harness.local.toml
    ↓
task manifest [toolchain]
```

То есть task manifest имеет наивысший приоритет для конкретной задачи.

Все итоговые paths должны существовать.

---

# 17. Local config

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
command = "~/Tools/codex-cli/node_modules/.bin/codex.cmd"

[toolchain]
node = "~/Tools/node/node.exe"
jest = "~/Documents/sa_icover/node_modules/jest/bin/jest.js"
```

---

# 18. Codex path precedence

Codex command выбирается:

```text
SLIVIN_CODEX_CMD
    ↓
[codex].command в harness.local.toml
    ↓
built-in default
```

Override:

```bash
export SLIVIN_CODEX_CMD="/path/to/codex.cmd"
```

---

# 19. Oracle calibration

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

# 20. Что происходит при запуске medium-risk task

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

# 21. Planner validation retry

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

# 22. Repair loop

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

# 23. Evaluator routing

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

# 24. Exit codes

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

# 25. Debug mode

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

# 26. Run artifacts

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

# 27. Heartbeat

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

# 28. Repo instructions и skills

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

# 29. Sandbox и temp

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

# 30. Типовые ошибки

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

# 31. Создание нового task case

Минимальная схема:

```text
cases/my-task/
├── task.toml
├── README.md
└── workspace/   # ignored
```

## Шаг 1. Создать manifest

Не копировать исторический Matrix prompt бездумно.

Определить:

- task intent;
- workspace;
- risk;
- preservation requirements;
- deterministic checks;
- repair vs held-out checks;
- toolchain requirements.

---

## Шаг 2. Подготовить workspace

Для disposable snapshot:

```bash
./py tools/prepare_workspace.py \
    cases/my-task/workspace
```

Для другого execution model сначала свериться с:

```text
docs/WORKSPACE_MODEL.md
```

---

## Шаг 3. Запустить

```bash
./run cases/my-task/task.toml
```

---

# 32. Production task без `_92`

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

# 33. Historical benchmark без full good reference

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

# 34. Разработка самого Harness

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

# 35. Документация

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

# 36. Что пока сознательно не включено

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

# 37. Quick start — текущая машина

Если repository и `_90` уже находятся на месте:

```bash
cd ~/Tools/slivin-harness

./py tools/self_check.py

./py tools/prepare_workspace.py \
    cases/matrix-all-matching/workspace

./run cases/matrix-all-matching/task.toml
```

Если inner workspace baseline уже существует, `prepare_workspace.py` не создаёт новый commit — он только требует clean state и подтверждает готовность.

---

# 38. Quick start — новая машина

```bash
# 1. Clone
git clone <repo> ~/Tools/slivin-harness
cd ~/Tools/slivin-harness

# 2. Windows local Git behavior
git config core.filemode false

# 3. При необходимости настроить Python
export SLIVIN_HARNESS_PYTHON="/path/to/python"

# 4. При необходимости настроить Codex / Node / Jest
cp harness.local.example.toml harness.local.toml
# edit harness.local.toml

# 5. Sanity check
./py tools/self_check.py

# 6. Скопировать содержимое _90
# → cases/matrix-all-matching/workspace/

# 7. Убедиться, что в copy нет .env/secrets

# 8. Создать disposable baseline
./py tools/prepare_workspace.py \
    cases/matrix-all-matching/workspace

# 9. Run
./run cases/matrix-all-matching/task.toml
```

---

# 39. Куда смотреть при следующем изменении

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
