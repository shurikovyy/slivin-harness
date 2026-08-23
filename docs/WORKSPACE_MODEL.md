# Workspace model, Git boundaries и local files

## 1. Основной и fallback режимы

Slivin Harness теперь использует один основной execution model:

```text
Managed project task
→ configured source Git repository
→ detached Git worktree per run
```

И один fallback:

```text
Static/legacy fixture
→ заранее подготовленная директория с собственным clean Git baseline
```

Для обычной разработки и для текущего Matrix historical benchmark используется
**managed Git worktree**.

Static mode нужен только когда source не представлен удобным Git repository/ref либо
нужен намеренно frozen filesystem fixture. Он не должен становиться способом вручную
копировать обычный project repository в `cases/`.

# 2. Managed project task — основной production-development mode

Machine-local project регистрируется в ignored:

```toml
# harness.local.toml

[projects.my_project]
repo = "~/Documents/my-project"
base_ref = "HEAD"
require_clean_source = true
result_mode = "apply_to_source"
```

Task manifest хранит только logical name:

```toml
project = "my_project"
workspace_mode = "git_worktree"
base_ref = "HEAD"
```

Committed manifest не знает путь `C:/Users/...` и переносим между машинами.

---

## 2.1. Что создаёт Controller

```text
source repo HEAD
      ↓
git worktree add --detach
      ↓
<workspace-root>/<project>/<task>/<timestamp-id>/
```

Default workspace root:

```text
<harness>/.workspaces/
```

Рекомендуется machine-local override:

```toml
[workspace]
root = "~/.slivin-harness/workspaces"
```

`.workspaces/` ignored Harness Git.

---

## 2.2. Что НЕ копируется

Worktree содержит tracked Git files.

Автоматически не копируются:

```text
.venv
node_modules
.env
IDE/runtime caches
любые другие untracked local files
```

Это решает прежнюю проблему тяжёлого ручного repository copy.

`.venv`/`node_modules` обычно вообще не нужны внутри worktree: trusted project toolchain может ссылаться на source repository paths.

---

# 3. Project toolchain не является частью workspace

Пример:

```toml
[projects.my_project.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "node"
jest = "{project_root}/node_modules/jest/bin/jest.js"
```

При выполнении command:

```text
cwd = disposable worktree
executable/dependency runtime = configured trusted source installation
```

Это позволяет тестировать candidate из worktree без копирования virtualenv/dependencies.

Ключевое различие:

```text
Controller Python
!=
project_python
```

`{python}` остаётся Python Harness process.

Backend task, требующая Django project environment, должна использовать explicit `{project_python}` либо другой declared project tool.

---

# 4. Opt-in exposure local/untracked files

Иногда Agent действительно нужен local file, например `.env`.

Это теперь не абсолютный запрет, а explicit trust decision:

```toml
[projects.my_project.workspace]
copy_untracked = [".env"]
```

Controller:

1. берёт указанный path из source repo;
2. копирует его в disposable worktree;
3. добавляет его в worktree-specific Git exclude policy;
4. не включает его в candidate patch;
5. не применяет его обратно в source.

### Security semantics

Если path указан в `copy_untracked`, его содержимое доступно:

```text
Planner
Implementer
Evaluator
model/tool process
```

Это сознательное разрешение пользователя.

Default — список пустой.

Не рекомендуется добавлять сюда большие directories вроде `.venv`/`node_modules`: это снова превратит worktree в тяжёлую копию.

---

# 5. Worktree-specific ignore policy

Git linked worktree использует `.git` file, а не собственный normal `.git/info/exclude` directory.

Harness поэтому создаёт внутри worktree:

```text
.harness_git_excludes
```

и устанавливает worktree-local:

```text
core.excludesFile = <workspace>/.harness_git_excludes
```

Для этого shared repository включает Git:

```text
extensions.worktreeConfig = true
```

В excludes входят как минимум:

```text
.harness_tmp/
.harness_git_excludes
__pycache__/
.pytest_cache/
.jest-cache*
coverage/
```

а также configured copied local paths.

В результате Agent и Controller видят clean task Git status без изменения project `.gitignore`.

---

# 6. Source repository clean contract

Managed worktree должен иметь известный committed baseline.

По default:

```toml
require_clean_source = true
```

Tracked/staged source changes блокируют task creation.

Configured `copy_untracked` paths являются исключением: они могут быть untracked даже если project `.gitignore` их не скрывает.

Почему:

> Если source содержит неизвестные product changes, Harness не знает, должны ли они входить в task baseline.

---

# 7. Result modes

## `keep_worktree`

После full PASS source working tree не меняется.

Harness сохраняет:

```text
runs/<task>/<run>/candidate.patch
```

и оставляет disposable worktree для inspection.

---

## `apply_to_source`

После full Harness PASS Controller:

1. собирает binary Git patch относительно immutable worktree HEAD;
2. временно включает новые untracked candidate files через intent-to-add только в worktree index;
3. исключает ignored/exposed local files;
4. проверяет source HEAD against start HEAD;
5. проверяет отсутствие concurrent tracked source changes;
6. применяет patch в исходный source working tree.

Результат:

```text
source working tree получает accepted code/test/docs changes
```

Harness не делает:

```text
git commit
git push
branch switch
PR creation
```

Это будущий publication/orchestration layer.

Если source изменился во время task, candidate не применяется и task publication становится `BLOCKED`; patch остаётся в run artifacts.

---

# 8. D-032: candidate paths и actual diff

Planner объявляет exact repo-relative:

```text
candidate_paths
```

После original plan Controller снимает pre-edit evidence.

После каждого write turn Controller вычисляет actual candidate paths через Git:

```text
tracked diff vs HEAD
+
untracked non-ignored files
```

Invariant:

```text
actual changed paths ⊆ planned candidate_paths
```

Если нарушен:

```text
unexpected path
→ record run artifact
→ rollback только unexpected path к baseline
→ read-only replan
→ updated candidate surface
→ trusted pre-path-edit snapshot
→ Implementer redoes required edit
```

### Snapshot fields

```text
captured_before_first_edit
captured_before_path_edit
worktree_snapshot_role
```

Late-added path после controlled rollback:

```text
captured_before_first_edit = false
captured_before_path_edit  = true
worktree_snapshot_role     = pre_path_edit_after_surface_reconciliation
```

Это честнее, чем либо:

- silently accepting unplanned edit;
- либо делать вид, что snapshot снят до первого edit всей задачи.

---

# 9. Task-local temp

Runtime temp:

```text
<worktree>/.harness_tmp/
```

Harness child env использует:

```text
TEMP
TMP
TMPDIR
XDG_CACHE_HOME
NPM_CONFIG_CACHE
PYTHONDONTWRITEBYTECODE
```

внутри task workspace.

Это одновременно поддерживает Windows sandbox и чистый Git diff.

---

# 10. Run artifacts

Outer Harness сохраняет:

```text
runs/<task_id>/<timestamp>/
```

Включая:

```text
manifest snapshot
preflight
plan/replans
baseline snapshot
change-surface violations
checks
evaluation
held-out result
candidate.patch (managed project mode)
workspace session metadata
```

`runs/` ignored Git.

---

# 11. Static/legacy fixture mode

Fallback mode:

```toml
workspace = "/path/to/already-prepared/static-fixture"
```

Harness не создаёт managed worktree автоматически.

Использовать его имеет смысл только когда:

- source не является Git repository/ref;
- fixture deliberately materialized как отдельное filesystem state;
- reproducer требует layout, который нельзя удобно представить source worktree.

**Текущий Matrix `_90` этот mode больше не использует.** Он хранится как отдельный
Git source repository и каждый trial получает managed worktree.

---

## 11.1. `prepare_workspace.py`

`prepare_workspace.py` — one-time helper именно для static/legacy folder без удобного
baseline Git state.

Пример:

```bash
./py tools/prepare_workspace.py /path/to/static-fixture
```

Он:

1. удаляет generated runtime caches;
2. создаёт inner Git baseline, если его нет;
3. добавляет local `.git/info/exclude` policy;
4. проверяет clean status.

Он не является обязательным шагом перед каждым managed project run.

`.venv`, `venv`, `env`, `node_modules` не удаляются — они исключаются из baseline Git.

---

## 11.2. `.env` в static mode

Default:

```text
real .env* → fail-fast
```

Explicit opt-in:

```bash
./py tools/prepare_workspace.py /path/to/static-fixture --allow-env
```

Файл остаётся доступен Agent и ignored Git.

Для managed project mode local-file exposure задаётся через project profile:

```toml
[projects.my_project.workspace]
copy_untracked = [".env"]
```

# 12. Повторные trials

## Managed worktree mode

Новый trial всегда создаёт новый detached worktree от configured source `base_ref`.

Не нужно reset'ить предыдущий task worktree.

Перед повтором достаточно убедиться, что source repository сохранил intended baseline:

```bash
cd /path/to/source
git status --short
git rev-parse HEAD
```

Для historical `result_mode = "keep_worktree"` source остаётся неизменным, поэтому
один и тот же `_90` можно использовать для нескольких независимых trials.

Старые worktrees можно сохранить для audit либо удалить через source repository:

```bash
git worktree list
git worktree remove --force "<worktree-path>"
git worktree prune
```

Windows cleanup/long-path nuances описаны в `docs/WINDOWS_SETUP.md`.

## Static/legacy mode

Только для static fixture перед повтором вернуть его inner Git к baseline:

```bash
git reset --hard HEAD
git clean -fd
rm -rf .harness_tmp
```

# 13. Outer Harness Git

Harness repository version-controls только:

```text
Controller source
docs
manifests
graders
unit tests
examples
```

Ignored:

```text
.workspaces/
cases/**/workspace/
runs/
harness.local.toml
```

---

# 14. CWD-independent launch

Использовать:

```bash
./run task.toml
```

или absolute launcher path.

Launchers больше не завязаны на `.venv` какого-либо project.

Bootstrap order:

```text
SLIVIN_HARNESS_PYTHON
→ python3
→ python
→ py -3
```
