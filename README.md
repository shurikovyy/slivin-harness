# Slivin Harness 0.8.0a4 — Phase 2

Slivin Harness — Controller вокруг Codex App Server для автономной работы над coding-задачами в изолированной Git worktree.

Версия **0.8.0a4** завершает Phase 2 согласованного рефакторинга quality-core и исправляет обнаруженную native Windows несовместимость `0.8.0a3`: filesystem-boundaries теперь сравниваются по каноническому расположению, а не по лексическому написанию `Path`. Model protocols пока не переписываются: сначала authoritative Controller state отделён от agent-writable workspace и введён единый Execution / Capability Broker.

```text
Phase 1: machine-readable workflow + versioned Run State + candidate_id
Phase 2: private Controller plane + execution policies + private self-verify receipts
```

## Что реализовано в Phase 2

Каждый run теперь разделён на две плоскости:

```text
runs/<task>/<run>/
├── controller_private/       # authoritative Controller state
│   ├── run_state.json
│   ├── candidate_identity_current.json
│   ├── implementation_contract_*.json
│   └── self_verify_receipt_current.json
├── run_state.json            # public diagnostic mirror
├── execution_policies.json   # без секретов и env values
└── остальные public artifacts

<WORKSPACE>/.harness_tmp/     # agent/runtime scratch; never authoritative
```

`ExecutionBroker` формирует role-specific policy для Planner, Implementer, Controller checks, Runtime, Evaluator и held-out. Он централизует scratch/temp/cache, фильтрует чувствительные environment variables и никогда не передаёт путь private plane в agent environment. Политика честно различает `ENFORCED`, `ADVISORY` и `UNAVAILABLE`: Phase 2 не заявляет OS-sandbox там, где restricted native Windows runner ещё не реализован.

На native Windows один каталог может иметь несколько эквивалентных представлений пути: исходное имя из `tempfile`, resolved/real path, другое написание регистра или filesystem alias. Поэтому security- и ownership-проверки используют каноническое containment. Private-path filtering проверяет как исходный, так и canonical alias и не путает соседний каталог вроде `controller_private_backup` с private plane.

Self-verification внутри worktree остаётся удобным claim агента, но финальным доказательством становится Controller-owned HMAC receipt, привязанный одновременно к `candidate_id`, attempt и revision vector. Поэтому старый PASS нельзя переиспользовать после изменения Contract или Verification Plan даже при неизменном коде.

## Канонический workflow

```text
0. Intake / Preflight
        ↓ PREFLIGHT_READY
1. Planner
        ↓ PLANNER_READY
2. Implementation Contract
        ↓ IMPLEMENTATION_CONTRACT_READY
3. Implementer
        ↓ IMPLEMENTATION_COMPLETE
4. Controller deterministic checks
        ↓ DETERMINISTIC_VERIFICATION_PASS
5. Runtime / external verification (условно)
        ↓ RUNTIME_VERIFICATION_PASS или RUNTIME_VERIFICATION_SKIPPED
6. Blind Evaluator
        ↓ EVALUATION_PASS
7. Final Gate / result handoff
        ↓ HARNESS_TASK_PASS или HARNESS_BENCHMARK_PASS
```

Полная таблица этапов, переходов и инвалидации находится в [каноническом workflow](docs/WORKFLOW.md). Его machine-readable форма — [`docs/workflow.v1.json`](docs/workflow.v1.json).

## Что уже реализовано в фундаменте Phase 1

Каждый run теперь создаёт Controller-owned:

```text
runs/<task_id>/<run_id>/
├── workflow_snapshot.json
├── run_state.json
├── candidate_identity_current.json
├── ... существующие plan/check/evaluation artifacts
├── candidate.patch
├── final_acceptance.json
└── delivery_record.json
```

`run_state.json` хранит:

- текущий этап и состояние всех Step 0–7;
- историю переходов и попыток;
- `attempt_id`;
- revision vector для plan, contract, candidate и runtime environment;
- baseline source/workspace;
- текущий `candidate_id`;
- terminal outcome.

`candidate_id` — единая identity candidate. Она связана с baseline SHA и учитывает изменённые пути, фактические bytes, удаления, symlink target и **Git-visible file mode**. На native Windows/NTFS обычный `chmod` может не создавать mode-only working-tree change; если Git не показывает такого изменения в HEAD-to-working-tree diff, для Git-based Harness candidate действительно не изменился. `.harness_tmp/` и `.venv/` не входят в candidate.

Если Controller checks, Evaluator или held-out изменили candidate, run останавливается. Если workspace HEAD изменился, Harness также останавливает run: Git history остаётся зоной Controller.

## Что пока сохранено для совместимости 0.7.1

Phase 2 сохраняет совместимость model roles:

- manifest остаётся `version = 2`;
- `risk = "low"` всё ещё выбирает FAST compatibility pipeline;
- `risk = "medium"`/`"high"` выбирает FULL compatibility pipeline;
- Planner остаётся `planner.v3`;
- Implementer остаётся `implementer.v1`;
- Implementation Contract остаётся `implementation-contract.v2`;
- Evaluator остаётся `evaluator.v4`;
- текущие fixed repair/replan budgets и turn-timeout continuation пока сохранены;
- dynamic checks всё ещё регистрируются через существующий Implementation Report;
- semantic replan пока использует существующую worktree;
- Runtime Step 5 ещё не имеет executor и пока фиксируется как compatibility skip. Phase 2 также не заявляет, что Controller checks уже исполняются в универсальном OS-level restricted runner.

Такое отображение не утверждает, что будущей production-задаче runtime не нужен. Оно лишь честно показывает текущее состояние executor-а.

## Что Phase 2 ещё не реализует

Следующие согласованные компоненты относятся к последующим фазам:

```text
USER TASK CONTRACT
Verification Plan compiler
новый Planner contract
open-world Contract expansion
inactivity watchdog вместо hard active timeout
restricted Controller check runner
Runtime / external verification executor
двухфазный Blind Evaluator
clean-worktree semantic replan
усиленный Final Gate / delivery critical section
```

Новых model roles в Phase 1 нет.

## Быстрый старт

### 1. Локальная конфигурация

Скопируйте:

```text
harness.local.example.toml
→ harness.local.toml
```

Пример:

```toml
[codex]
command = "C:/Users/<user>/Tools/codex-cli/node_modules/.bin/codex.cmd"

[workspace]
root = "C:/Users/<user>/.slivin-harness/workspaces"

[projects.example]
repo = "C:/Users/<user>/Documents/example-repo"
base_ref = "HEAD"
result_mode = "keep_worktree"
require_clean_source = true

[projects.example.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "C:/Users/<user>/Tools/node/node.exe"
jest = "{project_root}/node_modules/jest/bin/jest.js"
```

### 2. Проверка Harness

```bash
./py tools/self_check.py
```

### 3. Проверка manifest

```bash
./run examples/project-task.example.toml --validate-only
```

### 4. Запуск задачи

```bash
./run path/to/task.toml
```

## Текущий manifest v2

```toml
version = 2

task_id = "EXAMPLE_SMALL_FIX"
project = "example"
workspace_mode = "git_worktree"
result_mode = "keep_worktree"

risk = "low"
max_fix_cycles = 1
max_replan_cycles = 0
turn_timeout_seconds = 900
require_clean_git = true

prompt = """
Исправь конкретный дефект. Не ломай существующее поведение.
Добавь регрессионный тест и обнови документацию, если меняется контракт.
"""

[[checks]]
name = "Unit tests"
feedback = "repair"
command = ["{python}", "-m", "pytest", "tests/test_target.py", "-q"]
timeout_seconds = 180

[[checks]]
name = "Git diff check"
feedback = "repair"
command = ["git", "diff", "--check"]
timeout_seconds = 30
```

`feedback = "repair"` — trusted check, доступный текущему self-verify и затем повторяемый Controller.

`feedback = "heldout"` — hidden historical exam. Его assertion не возвращается Implementer в том же benchmark trial.

## Как читать итог

Production run завершается:

```text
HARNESS_TASK_PASS
```

Historical benchmark завершается отдельным status:

```text
HARNESS_BENCHMARK_PASS
```

При ошибке путь к `RUN_DIR` печатается в консоль. Основной диагностический файл:

```text
runs/<task>/<run>/run_state.json
```

Смотрите в нём:

```text
active_stage
cursor_stage
stages
revisions
current_candidate
terminal
events
```

## Документация workflow

После изменения `slivin_harness/workflow.py`:

```bash
./py tools/render_workflow_docs.py
./py tools/render_workflow_docs.py --check
./py tools/check_docs_sync.py
```

Ручное редактирование таблиц в `docs/WORKFLOW.md` запрещено: файл генерируется из кода.

## Result modes

- `keep_worktree` — source repository не изменяется, worktree и patch сохраняются;
- `apply_to_source` — accepted patch применяется только через существующие source guards.

Harness не делает commit, push, merge или PR.

## Документы

- [Канонический workflow](docs/WORKFLOW.md)
- [Архитектура Phase 1](docs/ARCHITECTURE.md)
- [Модель качества](docs/QUALITY_MODEL.md)
- [Практическая работа](docs/PRACTICAL_GUIDE.md)
- [Настройка Windows](docs/WINDOWS_SETUP.md)
- [История и roadmap](docs/HISTORY.md)
- [Historical Matrix benchmark](cases/matrix-all-matching/README.md)


Foundation protocol versions: `controller-plane.v1`, `execution-broker.v1`.
