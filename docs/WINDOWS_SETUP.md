# Настройка на Windows

## 1. Рекомендуемая структура

```text
C:\Users\<user>\Tools\slivin-harness
C:\Users\<user>\Tools\codex-cli\...
C:\Users\<user>\Tools\node\node.exe
C:\Users\<user>\.slivin-harness\workspaces
```

Короткий workspace root снижает риск Windows path-length проблем.

## 2. Локальный config

Создайте `harness.local.toml` рядом с `task_runner.py`:

```toml
[codex]
command = "C:/Users/<user>/Tools/codex-cli/node_modules/.bin/codex.cmd"

[workspace]
root = "C:/Users/<user>/.slivin-harness/workspaces"

[projects.sa_icover]
repo = "C:/Users/<user>/Documents/sa_icover"
base_ref = "HEAD"
result_mode = "keep_worktree"
require_clean_source = true

[projects.sa_icover.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "C:/Users/<user>/Tools/node/node.exe"
jest = "{project_root}/node_modules/jest/bin/jest.js"
```

Используйте `/` в TOML paths либо экранируйте обратные слеши.

## 3. Запуск

Git Bash:

```bash
./py tools/self_check.py
./run path/to/task.toml
```

CMD:

```cmd
py.cmd tools\self_check.py
run.cmd path\to\task.toml
```

Launchers сами определяют Harness root, поэтому запуск не зависит от текущего каталога.

## 4. Git Bash и native Windows executables

Если Git Bash искажает аргументы native command, запускайте с:

```bash
MSYS2_ARG_CONV_EXCL='*' ./run path/to/task.toml
```

## 5. TEMP и cache

Harness задаёт дочерним процессам task-local paths под:

```text
<workspace>/.harness_tmp/
```

Это уменьшает sandbox denials и не добавляет cache в candidate diff.

## 6. Worktree cleanup

Успешный `keep_worktree` и failed run сохраняют worktree для диагностики.

Удаляйте его через source repository:

```bash
git worktree list
git worktree remove --force "<worktree-path>"
git worktree prune
```

При ошибке во время подготовки worktree Harness сам удаляет незавершённый worktree.

## 7. EOL

Harness не нормализует project files самовольно. Добавляйте project-specific EOL check в manifest, например существующий `tools/check_changed_eol.py`.

Диагностика:

```bash
git ls-files --eol <file>
git diff --check
git diff --summary
```

## 8. Sensitive files

`.gitignore` не запрещает агенту читать `.env`. Не копируйте real secrets без необходимости.

Явный opt-in:

```toml
[projects.sa_icover.workspace]
copy_untracked = [".env"]
allow_sensitive_copy = true
```

Symlink/junction/reparse point в `copy_untracked` отклоняется.

## 9. Что проверить после установки

```bash
./py tools/self_check.py
./run examples/project-task.example.toml --validate-only
```

Реальный App Server turn проверяется только запуском небольшой project-задачи. Self-check не вызывает Codex и не расходует model tokens.

Для `thread/start` Harness использует wire values `read-only` / `workspace-write`. Если Codex CLI сообщает `unknown variant workspaceWrite`, установлена версия Harness до 0.6.1.

## 10. Если появляется `Permission denied` при записи workspace

Один Matrix-run на Windows показал `Permission denied` для tracked-файлов и `.harness_tmp`, хотя последующие controlled probes на той же машине успешно проверили: root write, nested write, запись существующего tracked-файла и `apply_patch`. Поэтому этот incident не считается доказанным постоянным ограничением nested paths или linked worktree.

Harness не переключается автоматически на `danger-full-access`: это сломало бы filesystem boundary. Если отказ повторяется, сначала выполните маленький write-probe в том же project/workspace и отделите воспроизводимую sandbox-проблему от разового turn failure.

При confirmed-broken benchmark Harness останавливается сразу, если Implementer не смог создать candidate diff, вместо повторных evaluator cycles.


## 11. Если progress появляется только после завершения run

Начиная с 0.6.4 Harness включает `PYTHONUNBUFFERED=1` в Git Bash/CMD launchers и line-buffered/write-through
stdout/stderr внутри Controller. Ожидаемое поведение:

- `TASK_STARTED`, стадии и heartbeat появляются сразу;
- Implementer agent-message deltas стримятся сразу;
- structured JSON Planner/Evaluator выводится после завершения соответствующего turn, а до этого виден heartbeat.

Если актуальная версия показывает весь stdout только в конце, проверьте, не запускается ли `./run` через
дополнительный внешний wrapper, который сам буферизует stdout.


## Временный обрыв response stream

Если Codex App Server печатает retryable transport error (`willRetry=true`), Harness начиная с 0.6.5 показывает `APP_SERVER_TURN_RETRY: ...` и продолжает текущий turn. Это не требует повторного запуска task вручную, пока App Server сам не исчерпал retries или общий turn timeout.

## Implementer timeout continuation

`IMPLEMENTER_TIMEOUT_CONTINUE` означает, что первый writable turn достиг Harness deadline. Уже сделанный worktree diff сохраняется; Harness продолжает тот же thread один раз, максимум на короткое continuation window. Это не Windows sandbox error и не новый repair cycle.

## 12. Git file mode на native Windows

`candidate.v1` учитывает file mode, который Git реально показывает в HEAD-to-working-tree diff.

На native Windows/NTFS команда Python `chmod` может не создавать observable mode-only working-tree change даже при `core.filemode=true`. Поэтому self-check сначала выполняет capability probe:

```text
Git сообщает mode change 100644 → 100755
→ все mode assertions выполняются

Git не сообщает такого изменения
→ integration test SKIPPED с явной причиной
```

Это ожидаемый platform skip. Он не превращает настоящий Git-visible mode defect в PASS: если Git сообщает mode change, test обязан проверить изменённый `candidate_id`, path и mode `100755`.

Диагностика вручную:

```bash
git config core.filemode true
chmod +x path/to/file
git diff --summary HEAD -- path/to/file
git diff --raw HEAD -- path/to/file
```

Если Git не показывает `mode change 100644 => 100755` или raw transition `:100644 100755`, один filesystem `chmod` не является observable candidate change на этой платформе. Сам Harness не включает index-only state в ordinary candidate: Implementer не должен использовать index-mutating команды в обычном workflow.

## 13. Phase 1 workflow artifacts

Начиная с 0.8.0a1 каждый реальный run пишет:

```text
runs/<task>/<run>/run_state.json
runs/<task>/<run>/workflow_snapshot.json
runs/<task>/<run>/candidate_identity_current.json
```

Они не находятся в project worktree и не входят в candidate patch. При ошибке Harness печатает `RUN_DIR`, поэтому сначала откройте `run_state.json` и проверьте `active_stage`, `cursor_stage`, `terminal` и последние `events`.

Проверка канонической схемы:

```bash
./py tools/render_workflow_docs.py --check
./py tools/check_docs_sync.py
```

Phase 1 пока сохраняет существующий timeout continuation 0.7.1. Inactivity watchdog будет отдельной последующей фазой; наличие его в target workflow не означает, что он уже включён.


## Phase 2: Windows capability boundary

Phase 2 централизует execution policy, но намеренно не называет Planner/Controller-check filesystem boundary `ENFORCED`, пока отдельный native Windows restricted runner не пройдёт capability smoke test. В `execution_policies.json` такие профили отображаются как `ADVISORY`. Это корректный статус, а не ошибка self-check.

Private Controller state размещается в `RUN_DIR/controller_private`, то есть вне managed worktree. Agent environment не получает этот path.
