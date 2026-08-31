# Windows setup для Slivin Harness 0.8.0a8

## Целевая среда

```text
Windows 10/11
Git for Windows / Git Bash
Codex CLI / App Server
Python для Harness
configured bootstrap Python проекта
project-specific Node/Jest при необходимости
```

## Проверка release

```bash
cd ~/Tools/slivin-harness-080a8-phase5
./py -c "import slivin_harness; print(slivin_harness.__version__)"
./py tools/self_check.py
```

Ожидаемо:

```text
0.8.0a8
DOCS_SYNC_PASS harness=0.8.0a8 ...
HARNESS_SELF_CHECK_PASS
```

## Python 3.12 проекта

Пример `harness.local.toml`:

```toml
[projects.sa_icover.runtime]
bootstrap_python = "C:/Users/Slivin.Aleksandr/AppData/Local/Programs/Python/Python312/python.exe"
expected_python = "3.12"
venv = ".venv"
dependency_files = ["requirements.txt"]
```

Harness сам выполняет эквивалент:

```bash
"$PY312" -m venv "$WORKSPACE/.venv"
"$WORKSPACE/.venv/Scripts/python.exe" -m pip install -r requirements.txt
"$WORKSPACE/.venv/Scripts/python.exe" -m pip check
```

Controller использует абсолютный executable; activation через `source` не требуется. `.venv` каждой worktree изолирована от основной project `.venv` и других parallel tasks.

## Worktree runtime path на native Windows

`tempfile`, NTFS и Windows path canonicalization могут представить один и тот же каталог разными строками. Поэтому принадлежность `project_python` worktree больше не проверяется через:

```text
str(project_python).startswith(str(workspace))
```

Такое сравнение лексическое и может дать `False` для эквивалентных путей. Проверяется фактический runtime-инвариант:

```text
project_python entry point
→ его parent directory канонически находится
  внутри Controller-owned <worktree>/.venv
```

Сам runtime-контракт не ослаблен: project checks по-прежнему запускаются через абсолютный entry point собственной `.venv` worktree.

## `.worktreeinclude`

Repository-owned ignored runtime files можно перечислить в `.worktreeinclude`:

```gitignore
.env
.env.local
```

Harness копирует их в managed worktree, но не включает в candidate. Symlink/junction/reparse traversal отклоняется на любом компоненте пути, а не только на leaf. Existing worktree files не перезаписываются.

## Git executable bit на NTFS

Git/NTFS может не показывать chmod-only изменение. Candidate identity test сначала делает capability probe. Если mode-only diff недоступен, тест ожидаемо `skip`; content/path/deletion identity продолжает проверяться.

## Canonical paths

Controller Plane и Execution Broker сравнивают Windows paths по каноническому расположению. Проверяются:

```text
relative escape ..
absolute drive path
UNC path
drive mismatch
private-root aliases
sibling with similar prefix
```

## Private artifacts

Authoritative artifacts включают:

```text
implementation_contract_*.json
verification_plan_*.json
capability_gate_*.json
project_runtime_*.json
check_registry.json
self_verify_receipt_current.json
```

Они находятся в `RUN_DIR/controller_private`, не в agent-writable worktree.

## App Server / subprocess boundary

Execution Broker сохраняет фактический enforcement. Где native Windows restricted runner ещё не реализован, policy остаётся `ADVISORY`, а не объявляется `ENFORCED`.

## Runtime capabilities

Запись capability в config не включает отсутствующий executor. Browser/test-external/prod-observe proof будет блокирован до соответствующей Runtime-фазы.

## Перенос local config

`harness.local.toml` не входит в ZIP. Переносите только его; не копируйте `runs`, `.git`, `.venv`, `.harness_tmp` или Controller private artifacts между версиями.
