# Windows setup для Slivin Harness 0.8.0a5

## Поддерживаемая среда

Целевая пользовательская среда:

```text
Windows 10/11
Git for Windows / Git Bash
Codex CLI / App Server
локальный Python для Harness
project-specific toolchain из harness.local.toml
```

## Проверка release

```bash
cd ~/Tools/slivin-harness-080a5-phase3
./py -c "import slivin_harness; print(slivin_harness.__version__)"
./py tools/self_check.py
```

Ожидаемо:

```text
0.8.0a5
DOCS_SYNC_PASS harness=0.8.0a5 ...
HARNESS_SELF_CHECK_PASS
```

## Git executable bit на NTFS

Git/NTFS может не показывать chmod-only изменение. Тест candidate identity сначала делает capability probe. Если mode-only diff недоступен, тест ожидаемо `skip`; content/path/deletion identity продолжает проверяться.

## Canonical paths

Начиная с 0.8.0a4 Controller Plane и Execution Broker сравнивают Windows пути по каноническому расположению, а не через лексический `Path.is_relative_to()`.

Проверяются:

```text
relative escape ..
absolute drive path
UNC path
drive mismatch
private-root aliases
sibling with similar prefix
```

## Phase 3 artifacts

Новые authoritative artifacts:

```text
task_contract_01.json
plan_01.json
implementation_contract_01.json
verification_plan_01.json
capability_gate_01.json
```

Они хранятся в private Controller plane, а не внутри agent-writable worktree.

## App Server sandbox boundary

Phase 3 сохраняет честный enforcement status из Phase 2. Там, где native Windows restricted runner ещё не реализован, policy остаётся `ADVISORY`, а не ложно объявляется `ENFORCED`.

## Runtime capabilities

Запись capability в `harness.local.toml` не включает несуществующий executor. До отдельной Runtime-фазы Browser/test-external/prod-observe proof будет заблокирован перед Implementer.

## Project Python

Phase 3 ещё не автоматизирует утверждённый per-worktree `.venv` bootstrap. Используется существующий resolver toolchain. До внедрения runtime bootstrap продолжайте указывать рабочий project Python в локальном project profile.

## Локальная конфигурация

`harness.local.toml` не входит в ZIP. После распаковки перенесите его из предыдущей установки. Не копируйте `runs`, `.git`, `.venv`, `.harness_tmp` или Controller private artifacts между версиями.
