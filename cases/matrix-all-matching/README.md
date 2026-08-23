# Matrix all-matching historical benchmark

Этот case проверяет quality-core Harness на реальном historical escaped defect.

**Текущий execution mode: managed Git worktree.**

`cases/matrix-all-matching/` больше не содержит и не требует локальный `workspace/`.
Broken baseline хранится как отдельный Git repository, путь к которому задаётся только
в `harness.local.toml`.

---

## Purpose

Historical defect:

```text
Matrix filters
→ page selection
→ "Выбрать все N найденных"
→ all-matching token active
→ selectedRows empty
→ filter-chip refresh
→ normal "Подтвердить распред" исчезала
```

Case проверяет, способен ли Harness исправить bug без доступа к known-good implementation.

---

## Source baseline

Нужен отдельный Git repository с historical broken `_90`, например:

```text
C:/Users/<user>/Downloads/sa_icover_90
```

Требования:

```text
Git repository
HEAD = broken historical baseline
git status --short = empty
```

Baseline не нужно копировать в Harness repository.

Если `_90` получен как обычная папка без `.git`, его можно **один раз** превратить
в Git baseline через `tools/prepare_workspace.py`. После этого benchmark работает
напрямую от этого source repository и `prepare_workspace.py` больше не участвует
в каждом запуске.

---

## Local project profile

Machine-specific binding находится в ignored `harness.local.toml`.

Пример:

```toml
[codex]
command = "C:/path/to/codex.cmd"

[workspace]
# На Windows рекомендуется короткий root.
root = "C:/Users/<user>/.slivin/w"

[projects.matrix_baseline]
repo = "C:/Users/<user>/Downloads/sa_icover_90"
base_ref = "HEAD"
require_clean_source = true

# Historical source должен остаться `_90`.
result_mode = "keep_worktree"

[projects.matrix_baseline.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "C:/path/to/node.exe"
jest = "{project_root}/node_modules/jest/bin/jest.js"

[projects.matrix_baseline.workspace]
# Optional explicit trust decision.
copy_untracked = [".env"]
```

`{project_root}` Harness подставляет из:

```toml
[projects.matrix_baseline]
repo = "..."
```

В `task.toml` ничего вместо `{workspace}` писать не нужно: `{workspace}` — это
runtime path автоматически созданного disposable worktree текущего run.

---

## Run

Из Harness root:

```bash
./py tools/self_check.py
./run cases/matrix-all-matching/task.toml
```

Harness автоматически:

```text
source `_90` HEAD
    ↓
git worktree add --detach
    ↓
новый unique managed workspace
    ↓
Planner / Implementer / checks / Evaluator / held-out
```

Физический worktree создаётся под configured `[workspace].root`, а не внутри
`cases/matrix-all-matching/`.

---

## Result

Case использует:

```toml
result_mode = "keep_worktree"
```

Поэтому source `_90` **не изменяется** после успешного run.

Accepted/intermediate candidate остаётся в managed worktree, а run artifacts
сохраняются под:

```text
runs/MATRIX_DATATABLE_ALL_MATCHING_BULK_ACTION_SCOPE_BENCHMARK/<run-id>/
```

После запуска Controller печатает location managed worktree. При failed run worktree
тоже сохраняется для диагностики.

---

## Re-run

Новый независимый trial не требует reset старого worktree.

Достаточно убедиться, что source baseline всё ещё clean:

```bash
cd /path/to/sa_icover_90
git status --short
```

и снова выполнить:

```bash
cd ~/Tools/slivin-harness
./run cases/matrix-all-matching/task.toml
```

Каждый run получает новый detached worktree от того же source `HEAD`.

Старый retained/failed worktree можно оставить для audit либо удалить корректно через
source repository:

```bash
cd /path/to/sa_icover_90
git worktree list
git worktree remove --force "<worktree-path>"
git worktree prune
```

На Windows для уже созданного очень длинного path может потребоваться
`core.longpaths=true`; подробности — `docs/WINDOWS_SETUP.md`.

---

## Known-good reference и calibration

Полная `_92` рядом с Agent не нужна.

Held-out был отдельно calibrated:

```text
_90 → FAIL
_92 → PASS
```

Repository хранит hash-bound calibration certificate.

Если grader/check definition меняется, Harness должен отказаться от historical
acceptance до explicit recalibration.

---

## Held-out scope

Held-out проверяет public Matrix contract:

1. all-matching остаётся explicit selection;
2. ordinary filter-only не показывает normal confirm action;
3. manual checkbox сохраняет normal action.

Он не кодирует ready-made implementation и не используется как tutoring feedback
внутри trial.

---

## Historical milestone

На lineage 0.4.6 один clean trial завершился:

```text
Planner
→ Implementer
→ deterministic checks
→ Fresh Evaluator
→ held-out
→ HARNESS_TASK_PASS
```

Позднее case был переведён на managed-worktree infrastructure и используется как
regression benchmark для следующих Harness increments.

Текущий validation status новой infrastructure см. в:

```text
docs/CURRENT_STATE.md
```
