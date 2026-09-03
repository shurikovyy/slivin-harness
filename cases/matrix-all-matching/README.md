# Matrix all-matching historical benchmark

Этот case проверяет **сам Slivin Harness** на historical defect в shared DataTable selection.
Он не является шаблоном production prompt и не требует нового held-out для каждой новой задачи.

Исходный пользовательский дефект:

```text
filters
→ page selection
→ «Выбрать все N найденных»
→ token selection active
→ selectedRows empty
→ normal «Подтвердить распред» исчезает
```

## Зачем здесь semantic held-out

Historical `_92` больше не считается gold standard. Последующий аудит показал, что `_92`, `workspace_14`, candidate 0.6.2 и candidate 0.6.5 каждый исправляют часть контракта, но сохраняют разные defects.

Поэтому финальный grader проверяет **observable semantics**, а не сходство с одним reference patch.

Bundled semantic held-out проверяет семь свойств:

1. current all-matching остаётся explicit selection через реальный `filter chips → bulk visibility` refresh path;
2. current all-matching с exclusions согласованно определяет visibility / count / summary / payload даже при resident filter residue;
3. ordinary filter-only scope не получает normal confirm action;
4. manual checkbox selection остаётся authoritative поверх resident filter-action residue;
5. stale / zero-target / all-excluded token не авторизует normal action;
6. filter action сохраняет target, захваченный до более поздней смены selection во время async fetch;
7. Distribution token-only selection не делает stage-dependent action доступным без materialized stage data. Допустимы как полноценный fail-closed stage guard, так и безопасная изоляция Matrix, при которой такой action в Distribution вообще не экспонируется.

Grader намеренно не диктует helper names, file ownership или форму patch. Matrix fixture подхватывает literal boolean opt-ins из фактического `MatrixTableConfig`, поэтому безопасный Matrix-only design не отклоняется только за то, что shared DataTable по умолчанию остаётся conservative.

## Что held-out НЕ делает

В benchmark mode:

```text
agent work
→ normal checks
→ blind Evaluator
→ hidden semantic held-out
```

Если held-out падает, trial заканчивается. Конкретный assertion не возвращается Implementer в том же trial.

Held-out нужен разработчику Harness, чтобы измерять качество pipeline. При новой production-задаче новый hidden grader заранее писать не требуется.

## Calibration (0.6.6)

Calibration больше не использует `_92` как positive reference.

Новый certificate подтверждает:

```text
_90 broken baseline          → FAIL
_92 known-incomplete         → FAIL
workspace_14 known-incomplete→ FAIL
0.6.2 candidate incomplete   → FAIL
0.6.5 candidate incomplete   → FAIL
semantic-good fixture A      → PASS
semantic-good fixture B      → PASS
```

Оба positive fixture построены только для калибровки observable contract и архитектурно различаются: один поддерживает explicit all-matching как общий DataTable contract с fail-closed Distribution, второй использует Matrix-only opt-in и сохраняет conservative поведение Distribution. Они **не входят в release archive**. В certificate сохраняются только hash-bound fingerprints и наблюдённые PASS/FAIL. Fingerprint вычисляется из SHA-256 четырёх contract-bearing файлов (`selection/core.js`, `selection/bulk_edit.js`, `distribution/index.js`, `tableConfigs/matrix.js`) через canonical JSON.

Это защищает от двух ошибок одновременно:

- grader, который пропускает известный плохой вариант;
- grader, который принимает только форму одного «любимого» исправления.

## Подготовка broken baseline

Нужен отдельный clean Git repository с historical `_90`.

В `harness.local.toml`:

```toml
[projects.matrix_baseline]
repo = "C:/Users/<user>/Downloads/sa_icover_90"
base_ref = "HEAD"
require_clean_source = true
result_mode = "keep_worktree"

[projects.matrix_baseline.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
node = "C:/Users/<user>/Tools/node/node.exe"
jest = "{project_root}/node_modules/jest/bin/jest.js"

[projects.matrix_baseline.workspace]
copy_untracked = ["node_modules"]
```

`node_modules` должен уже существовать в source `_90`. Controller физически
копирует этот dependency tree в standalone historical workspace и rebind-ит
source-local `jest` только на эту копию. `npm install` и `npm ci` в worktree
не выполняются; `.worktreeinclude` для `node_modules` не используется.

Controller хранит private full-tree baseline всего `node_modules`, а не только
`jest/bin/jest.js`. Перед authoritative batch изменённая workspace copy
полностью восстанавливается из неизменного source; mutation во время batch
инвалидирует результат и также приводит runtime к baseline. Поэтому
транзитивные зависимости Jest входят в ту же integrity boundary. Проверка имеет
стоимость O(total projected bytes) до и после trusted batch и не является
заявлением OS-level immutability.

Если baseline ещё не Git repository:

```bash
./py tools/prepare_workspace.py C:/path/to/sa_icover_90
```

Реальный `.env` для frontend benchmark не нужен.

## Запуск

```bash
./py tools/self_check.py
./run cases/matrix-all-matching/task.toml
```

Каждый run создаёт новый detached worktree. `result_mode = "keep_worktree"`, поэтому source `_90` не меняется.

## Ограничения benchmark

Этот case не доказывает корректность всего `sa_icover`. В частности, semantic held-out не является repository-wide OData, DB или performance audit.

Его назначение узкое: проверять, способен ли текущий Harness автономно сохранить связанные selection/lifecycle/stage semantics на реальном historical bug без reference solution в контексте агента.


## 0.6.6 note

0.6.6 recalibrates the same seven semantic properties to remove one implementation bias discovered by the 0.6.5 trial: the grader no longer requires Distribution to adopt the new explicit-token representation if the candidate safely isolates the behavior to Matrix. The 0.6.5 candidate is added as a negative control and still fails two independent properties (new-action authority with filter residue and zero/all-excluded target safety).

When a benchmark exhausts its repair budget after deterministic checks are green, Harness may run the held-out once more as **diagnostic-only** evidence for the Harness developer. That output is never returned to Implementer.

## 0.7.0 execution note

0.7.0 deliberately does **not** change the seven semantic properties or their calibration. The experiment changes the solver side: Planner discoveries become an Implementation Contract, Implementer must run trusted self-verification in-turn, and supported tests discovered for sibling consumers are added to Controller checks. This keeps the next blind Matrix trial comparable with 0.6.6: a better result should come from better execution, not a weaker oracle.
