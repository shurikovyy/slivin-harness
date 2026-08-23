# Workspace model, Git boundaries и security hygiene

## 1. Два разных Git repository

Slivin Harness использует вложенную модель:

```text
~/Tools/slivin-harness/.git
    Outer repository:
    source самого Harness.

~/Tools/slivin-harness/cases/.../workspace/.git
    Inner disposable repository:
    baseline конкретного project/eval task.
```

Это осознанная архитектура.

Outer repo не должен version-control'ить project snapshot.

Inner repo нужен для:

- known clean baseline;
- task-local `git diff`;
- EOL checks;
- pre-edit snapshots;
- reproducible historical eval.

---

## 2. Почему project workspace не является submodule

Historical `_90` — disposable input, а не dependency Harness.

Он:

- может содержать большой/private project;
- меняется между cases;
- не должен попадать в Harness history;
- на другой машине может быть получен отдельно.

Поэтому:

```text
cases/**/workspace/
```

игнорируется outer `.gitignore`.

---

## 3. Подготовка workspace

После копирования содержимого historical/project snapshot:

```bash
./py tools/prepare_workspace.py \
  cases/matrix-all-matching/workspace
```

Скрипт:

1. удаляет generated caches;
2. ищет опасные `.env*`;
3. создаёт inner `git init`, если его нет;
4. задаёт локального baseline author;
5. обновляет `.git/info/exclude`;
6. создаёт первый baseline commit;
7. если baseline уже есть — требует clean status;
8. проверяет `.harness_tmp` ignore.

---

## 4. Почему `.env` — hard stop

`.gitignore` защищает только от случайного commit.

Он **не мешает агенту прочитать файл**.

Поэтому:

```text
workspace/.env
```

даже если ignored, остаётся secret exposure.

`prepare_workspace.py` разрешает только шаблоны:

```text
.env.example
.env.sample
.env.template
```

и блокирует реальные `.env*`.

Оригинальный project `.env` удалять не нужно — удаляется только копия в agent workspace.

---

## 5. `.gitignore` vs `.git/info/exclude`

### Outer `.gitignore`

Repository-wide policy Harness:

```text
runs/
workspaces/
archives
node_modules
__pycache__
machine-local config
.env*
```

Коммитится.

### Inner `.git/info/exclude`

Локальные правила disposable project baseline:

```text
.harness_tmp/
generated runtime artifacts
```

Не меняют historical baseline source files.

---

## 6. Clean baseline

Перед implementation:

```text
known HEAD
clean status
known tracked paths
no secrets
temp ignored
```

Если existing inner repo dirty:

```text
prepare_workspace.py → RuntimeError
```

Harness не должен гадать, кому принадлежат уже существующие changes.

---

## 7. Preflight

До Planner/Implementer Controller фиксирует:

```text
head_sha
working_tree_clean
status_porcelain
tracked_paths
```

Это trusted independent evidence.

---

## 8. Candidate paths и baseline snapshot

Planner объявляет:

```text
candidate_paths
```

После plan, но до first edit Harness снимает по каждому path:

```text
exists
is_file
worktree size
worktree SHA-256
git EOL
index entry
baseline blob SHA
baseline blob size
```

Это нужно для доказательства pre-edit state.

### Replan nuance

Если после first edit revised plan добавил новый candidate path:

```text
его уже нельзя честно назвать pre-edit snapshot.
```

Поэтому такой snapshot должен быть отмечен как candidate-state-at-replan, а original pre-edit evidence не перезаписывается.

---

## 9. `.harness_tmp`

Runtime-only directory:

```text
workspace/.harness_tmp/
```

Используется для:

- temp;
- caches;
- test runtime;
- agent runtime temp.

Не должна попадать в inner diff.

---

## 10. `runs/`

Outer Harness сохраняет audit artifacts:

```text
runs/<task_id>/<timestamp>/
```

Возможные файлы:

```text
manifest snapshot
preflight
repo context
plan attempts
validation errors
baseline snapshot
deterministic check results
evaluator artifacts
held-out result
calibration verification
```

`runs/` не входит в Git.

---

## 11. Historical `_92`

Полная `_92` больше не требуется на каждой машине для Matrix benchmark.

Held-out grader был вручную проверен:

```text
_90 → FAIL
_92 → PASS
```

и текущий repository хранит hash-bound calibration certificate.

Certificate привязан к grader/check definition.

Если grader меняется:

```text
certificate invalid
→ требуется explicit recalibration
```

Это снижает риск держать known-good implementation рядом с agent workspace.

---

## 12. Benchmark certificate не является production dependency

Calibration certificate нужен только historical eval.

Для production task known-good reference обычно отсутствует и не нужен.

---

## 13. Local tool paths

Task manifests не должны содержать пользовательские абсолютные пути.

Machine-local override:

```text
harness.local.toml
```

Этот файл ignored.

Default paths могут работать на текущей Windows layout, но не являются архитектурным contract.

---

## 14. CWD-independent launch

Надёжный запуск:

```bash
./run cases/.../task.toml
```

или:

```bash
~/Tools/slivin-harness/run cases/.../task.toml
```

Launcher сам определяет root.

Не использовать:

```bash
python task_runner.py ...
```

из произвольного project subdirectory, потому что Python сначала ищет `task_runner.py` относительно current working directory.

---

## 15. Archive/review hygiene

В review/benchmark archive не должны попадать:

```text
.env*
.git/
.harness_tmp/
__pycache__/
*.pyc
.pytest_cache/
.jest-cache*
node_modules/
runtime caches
```

Если архив нужен для human audit, предпочтительно создавать его из explicit allowlist/diff либо отдельным packaging script, а не архивировать project root «как есть».


---

## 16. Повторный historical trial: reset к baseline

После успешного/неуспешного Agent run inner workspace обычно dirty.

Перед новым независимым trial:

```bash
cd cases/matrix-all-matching/workspace

git reset --hard HEAD
git clean -fd
rm -rf .harness_tmp

git status --short
```

Ожидается пустой status.

`HEAD` здесь — inner baseline commit, созданный `prepare_workspace.py`.

Не использовать final candidate предыдущего trial как следующий baseline.

Не делать новый baseline commit поверх candidate: это разрушит historical comparison.
