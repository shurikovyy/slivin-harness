# Архитектура Slivin Harness 0.8.0a19 — Phase 7

## Назначение

`0.8.0a19` сохраняет согласованный Step 0–7 quality-core, проверяет strict Structured Outputs contract локально до App Server `turn/start` и ограничивает Planner доказанными executors без изменения protocol/workflow versions.

Machine phase id:

```text
phase7-final-gate-delivery-benchmark
```

## Канонический pipeline

```text
Step 0 — Intake / Preflight / User Task Contract
        ↓
Step 1 — fresh read-only Planner
        ↓
Step 2 — Implementation Contract + Verification Plan
        ↓
Step 3 — Implementer + self verification
        ↓
Step 4 — independent deterministic Controller checks
        ↓
Step 5 — Runtime / External Verification, conditional
        ↓
Step 6 — fresh two-phase Blind Evaluator
        ↓
Step 7 — Final Gate / result handoff / hidden benchmark exam
```

Полная генерируемая схема находится в [WORKFLOW.md](WORKFLOW.md), machine-readable snapshot — в [workflow.v6.json](workflow.v6.json).

## Версионные слои

```text
Harness                     0.8.0a19
Manifest                    version = 2
Workflow                    workflow.v6
Run State                   run-state.v1
Candidate                   candidate.v1
Controller plane            controller-plane.v1
Execution Broker            execution-broker.v1
Task Contract               task-contract.v1
Planner                     planner.v4
Implementer                 implementer.v3
Implementation Contract     implementation-contract.v3
Verification Plan           verification-plan.v1
Project runtime             project-runtime.v1
Contract expansion          contract-expansion.v1
Phase 5 controller          phase5-contract-runtime.v1
Phase 6 controller          phase6-runtime-evaluator.v1
Runtime scenario            runtime-scenario.v1
Runtime request             runtime-request.v1
Runtime result              runtime-result.v1
Runtime evidence            runtime-evidence.v1
Contract closure            contract-closure.v1
Blind audit                 blind-audit.v1
Evaluator                   evaluator.v5
Phase 7 controller          phase7-final-gate.v1
Patch proof                 patch-proof.v1
Final acceptance            final-acceptance.v2
Delivery record             delivery-record.v2
Held-out evidence           heldout-evidence.v2
Benchmark isolation         benchmark-isolation.v1
```

## 1. Control plane и data plane

### Agent workspace

```text
<WORKSPACE>/
```

Содержит candidate и agent scratch. Implementer имеет право менять project-файлы только здесь.

### Private Controller plane

```text
<RUN_DIR>/controller_private/
```

Содержит authoritative state:

```text
run_state.json
Task/Plan/Contract/Verification revisions
check registry
self-verify receipts
runtime evidence
contract closure
blind audit / evaluator verdict
quality reconciliation
patch proof
final acceptance
held-out evidence
```

Файл внутри agent-writable `.harness_tmp` не является authoritative evidence.

## 2. Identity и revisions

Каждый accepted artifact связан с revision vector:

```text
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
candidate_rev
runtime_environment_rev
attempt_id
```

`candidate.v1` строится из Controller-private physical workspace baseline и
включает:

```text
recorded baseline SHA
workspace HEAD
changed/new/deleted paths
physical executable/Git-compatible mode
SHA-256 фактических bytes или symlink target
```

Inventory не читает `.gitignore`, `.git/info/exclude`, `core.excludesFile` и не
доверяет real index. Поэтому ignored addition, tracked file с
`assume-unchanged`/`skip-worktree`, deletion, rename или type change остаются
candidate-visible. Исключаются только Controller-authorized roots: `.git`,
`.harness_tmp`, worktree `.venv`/configured project runtime, registered runtime
projections, `session.exposed_paths` и `.harness_git_excludes`. Wildcard/cache
эвристики не являются authority: cache вне конкретного Controller-owned root
считается candidate mutation. До baseline capture Controller отклоняет exclusion,
перекрывающий tracked path. Разрешённые roots не попадают в `candidate.patch`,
delivery или Phase 7 patch reconstruction.

Отдельный private `GitControlStateBaseline` фиксирует repository identity,
worktree/common Git directories, `.git` pointer, HEAD/symbolic ref, raw index и
`ls-files` stage/flags/debug semantics, local/worktree config, split-index
objects, info exclude/attributes/sparse definitions, local exclude/attribute
targets, hooks, `.harness_git_excludes`, packed/loose/worktree refs,
`refs/replace`, `shallow`, `objects/info/alternates` и `grafts`.
Baseline содержит immutable mapping logical key → original path → ownership →
restore policy. Worktree-private и standalone-private controls могут быть
best-effort восстановлены только в original path; common/source/external paths
detect-only. Изменённый config target не сканируется и не становится destination.
Directory snapshots ограничены по depth, entry count, total и single-file bytes.
`GitControlIntegrityManager` проверяет baseline до/после authoritative batch,
но mutation всегда инвалидирует текущий result. Public
`git-control-integrity.v1` содержит только batch/event codes; private paths,
bytes и fingerprints остаются в Controller plane.

Изменение candidate, Contract, Verification Plan или runtime environment инвалидирует downstream evidence согласно `workflow.v6`.

## 3. Step 0 — Intake / Preflight

`task-contract.v1` остаётся строгим: `READY` не может содержать direct contradiction. При этом
conditional requirements для разных scopes/states не считаются противоречием. Если Intake-модель
возвращает invalid artifact, Controller даёт точный `ArtifactContractError.feedback()` тому же
read-only Intake-thread и разрешает максимум две protocol-repair попытки. Невалидный промежуточный
ответ не получает revision и не становится authoritative artifact.

Controller:

```text
сохраняет raw request
фиксирует source baseline
создаёт managed worktree
копирует разрешённые .worktreeinclude files и local runtime projections
регистрирует Controller-only candidate exclusions
фиксирует private physical candidate baseline
фиксирует private Git control-state baseline
фиксирует private full-tree runtime projection baseline
создаёт worktree-local project runtime под read-only candidate/Git guard
resolve + historical sanitize/rebind toolchain
выполняет static-toolchain-preflight.v1 для всех manifest checks
только затем запускает semantic baseline command и Codex app-server
создаёт task-contract.v1
```

Historical benchmark вместо linked worktree получает standalone sanitized repository; подробнее ниже.

Static preflight использует тот же strict `string.Formatter`-совместимый
template contract, что и фактический check runner: только simple identifier
placeholders, включая escaped literal braces. Он fail-closed отклоняет unknown,
attribute/index access, conversion, format spec и malformed braces. Required
toolchain entries выводятся только из реально используемых placeholders;
optional configured entries получают `UNUSED_NOT_PROBED` и не блокируют run.

Известные command families проверяются без запуска tests: executable resolution,
Node/Python script inputs, Jest config и `--runTestsByPath` files. Bounded
Controller probes проверяют Git, harness Python, Node и при необходимости
project Python. Jest требует успешных Node/Jest version probes и `--showConfig`
для каждого explicit manifest config либо один cwd auto-discovery probe, что
загружает executable project-owned config/test environment, но не запускает
test suite или hidden oracle. Probes проходят через тот же
`RuntimeProjectionIntegrityManager` с batch id `static-toolchain-preflight`.
Combined stdout/stderr читается потоково и ограничен 1 MiB; overflow завершает
process group и даёт `STATIC_TOOLCHAIN_PROBE_OUTPUT_LIMIT`. Raw bytes остаются
только в Controller-private log.
Failure маршрутизирует Step 0 в `BLOCKED`; Task Contract/Planner artifacts ещё
не существуют.

Python placeholders имеют одну semantics во всех manifest checks и generated
dynamic Python checks: `{python}` выбирает `project_python`, затем configured
`python`, затем `sys.executable`; `{project_python}` требует явную entry и
`PROJECT_PYTHON` probe; `{harness_python}` всегда выбирает interpreter Harness.
Matrix EOL utility использует последний вариант как Harness-owned script.

Runtime guard защищает projected dependency tree, но project-owned Jest config
может менять candidate. Поэтому Controller строит canonical `candidate.v1` до
и после всего static preflight независимо от probe success, timeout или launcher
error. Tracked/untracked/deleted mutation добавляет
`STATIC_PREFLIGHT_MUTATED_CANDIDATE`, очищает accepted probe evidence и запрещает
semantic baseline/agent stages. Полный probe output записывается через
`ControllerPlane` только в `controller_private/preflight_logs`; public artifact
содержит лишь typed status, return code/timeout и безопасную version/failure
diagnostic.

До workspace/agent stages Controller также записывает
`harness-build-identity.v1`: package version `0.8.0a19`, exact Git HEAD и tracked
dirty state (`--untracked-files=no`). В архиве или без Git поля commit/dirty
остаются `null`, а `source_kind=ARCHIVE_OR_UNKNOWN`; absolute Harness path в
artifact не входит.

## 4. Step 1 — Planner

`planner.v4` исследует current behavior, intended contract, root cause или extension point, consumers, state model, risks и typed evidence plan.

Planner read-only относительно candidate и не получает previous solution/reference/held-out.
Controller передаёт ему probe-backed `AVAILABLE_VERIFICATION_CAPABILITIES` и
public-safe manifest repair evidence без held-out command. Explicit capabilities
и implicit runtime capability proof level проверяются до Contract compiler. Один
невыполнимый READY получает corrective turn в том же thread; второй даёт
`PLANNER_CAPABILITY_INFEASIBLE` и не создаёт Implementation Contract.

## 5. Step 2 — Contract compiler

Controller детерминированно строит:

```text
implementation-contract.v3
verification-plan.v1
```

Contract хранит load-bearing Definition of Done, а Verification Plan связывает каждый requirement с proof profile:

```text
LOCAL_DETERMINISTIC
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

Owner-boundary и post-plan capability gate выполняются до writable Implementer.
Tool-backed `GIT`, `PROJECT_PYTHON`, `NODE` и `JEST` считаются available только
по успешному Controller probe evidence. Раннее evidence переиспользуется;
новое требование Verification Plan пробуется on demand тем же registry.
Configured/runtime semantics non-tool capabilities остаются отдельными.
Этот retained gate не заменён Planner feasibility: он по-прежнему покрывает
contract expansion, registered checks и действительно новые runtime requirements.

## 6. Step 3 — Implementer

`implementer.v3` получает Task Contract, compact Planner context, active Contract и trusted capabilities.

Перед любым App Server `turn/start` Controller рекурсивно проверяет production
`outputSchema`: каждый object с `properties` обязан иметь
`additionalProperties=false` и `required`, в точности равный набору properties.
Проверка охватывает nested objects, array items и composition branches. Для
`implementer.v3` все поля обязательны на wire-level, но semantic completeness
остаётся status-dependent: non-COMPLETE status передаёт пустые ledgers, а не
фиктивное закрытие Contract. Agent всегда возвращает пустой `receipt_id`;
Controller-private self-verification receipt остаётся единственной authority.

Он:

```text
делает smallest complete fix
создаёт/обновляет tests/docs
регистрирует typed checks
сообщает material consumers/risks
закрывает Contract
запускает self verification
```

Open-world discovery расширяет Contract/Verification Plan транзакционно; старые items не удаляются.

Task-local `.venv` пересобирается при dependency/package drift. Runtime-only `.env` восстанавливается, если агент его изменил.

## 7. Step 4 — deterministic checks

Controller независимо запускает project gates и typed task checks на frozen candidate.

Результаты различаются:

```text
CHECK_PASS
CHECK_FAIL
CHECK_TIMEOUT
CHECK_INFRA_ERROR
CHECK_MUTATED_CANDIDATE
```

Green self-verify Implementer не заменяет Controller evidence.

## 8. Step 5 — Runtime Verification

Runtime запускается только если Verification Plan требует observable evidence, которое нельзя доказать local checks.

```text
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

`TEST_EXTERNAL` write требует fresh readback и cleanup/disposable boundary. `PROD_OBSERVE` допускается только с technically enforced read-only wrapper/credential. Runtime не может менять candidate или source.

## 9. Step 6 — Blind Evaluator

`evaluator.v5` работает в две фазы одного fresh thread.

### Phase A

Не видит Planner, Contract, Implementer Report, green checks, runtime evidence и previous findings. Самостоятельно исследует repository/candidate и сохраняет immutable `blind-audit.v1`.

### Phase B

Получает active Contract, Verification Plan, `contract-closure.v1`, deterministic и runtime evidence. Каждый blind finding должен быть retained или dismissed with evidence.

## 10. Repair и semantic replan

### Обычный repair

```text
technical model корректна
candidate локально ошибочен
        ↓
same Implementer thread
```

### Semantic replan

```text
Evaluator отверг technical model
        ↓
rejected patch сохраняется вне workspace
        ↓
candidate сбрасывается до recorded baseline
        ↓
task-specific registry очищается
project runtime пересобирается
        ↓
fresh Planner
        ↓
new Contract / Verification Plan
        ↓
fresh Implementer thread
```

Новый Planner не видит rejected diff; это устраняет anchoring на признанно неверной реализации.

## 11. Step 7 — Final Gate

Final Gate выполняет четыре независимых действия.

### Quality reconciliation

Проверяет, что Step 3–6 относятся к одному final candidate и текущему revision vector.

### Patch reconstruction

`candidate.patch` применяется к чистой verification-копии recorded baseline. Reconstructed `candidate.v1` должен точно совпасть с accepted candidate. Artifact: `patch-proof.v1`.

Так как `candidate.v1` учитывает реальные worktree bytes, Controller перед checkout зеркалирует узкий allowlist effective Git conversion settings source repository (`core.autocrlf`, `core.eol`, `core.safecrlf`, `core.filemode`, `core.symlinks`). Это сохраняет CRLF/LF semantics native Windows без копирования arbitrary Git configuration в private proof repository.

После byte-level proof Controller materializes runtime из исходных authoritative
источников: заново копирует source-owned projections/exposed files, при наличии
строит отдельный proof `.venv`, разрешает и rebind-ит toolchain. В proof repo без
agent turns выполняются static preflight, все active repair checks и benchmark
held-out. Candidate/Git/runtime guards должны остаться pristine; результат
фиксирует `reconstructed-verification.v1`. Original workspace runtime, `.env`,
`.harness_tmp` и loose unreferenced Git objects не переносятся.

### Immutable acceptance

После patch proof и `reconstructed-verification.v1=PASS` создаётся `final-acceptance.v2`. Он связывает candidate, revisions, stage artifacts и patch SHA-256 и не перезаписывается.

### Delivery

`delivery-record.v2` фиксирует `keep_worktree` или транзакционный `apply_to_source`.

`apply_to_source` использует:

```text
delivery lock
source HEAD/clean recheck
preimage comparison
git apply --check
apply
exact patch/postimage comparison
safe rollback при failure
```

Delivery conflict не делает accepted candidate плохим: source остаётся нетронутым, patch/worktree сохраняются.

Подробности: [Phase 7 Final Gate](PHASE7_FINAL_GATE.md).

## 12. Historical benchmark isolation

Linked Git worktree делит refs/object database с source repository. Hidden exam поэтому использует standalone sanitized repository:

```text
только baseline tree blobs
один detached synthetic commit
нет shared .git metadata
нет refs с reference solution
нет unrelated objects
нет previous attempt artifacts
```

Held-out запускается только после normal Step 0–6 PASS, требует oracle marker и различает:

```text
HELDOUT_PASS
HELDOUT_SEMANTIC_FAIL
HELDOUT_INFRA_ERROR
HELDOUT_TIMEOUT
HELDOUT_MUTATED_CANDIDATE
```

Hidden failure никогда не возвращается агентам текущего trial.
Semantic held-out failure получает terminal result
`HARNESS_BENCHMARK_SEMANTIC_FAIL`; infrastructure/invalid outcomes не используют
этот result code.

### Source-owned runtime projection

Локальный project profile может явно объявить directory в
`[projects.<name>.workspace] copy_untracked`, например `node_modules`.
Controller валидирует repo-relative путь, проверяет source tree на
symlink/junction/reparse objects и создаёт независимую physical copy в managed
workspace. `WorkspaceSession.runtime_projections` — единственная authority для
того, что этот root действительно разрешён, скопирован Controller-ом и
runtime-only; существование одноимённого workspace path само по себе ничего не
разрешает.

В historical mode absolute toolchain path внутри source repository сначала
превращается в canonical repo-relative path. Он rebind-ится только если лежит
под наиболее узким authorized projection, ожидаемый destination находится в
canonical workspace, имеет ожидаемый тип и ни один его ancestor не является
symlink/junction/reparse alias. Все остальные source-local paths удаляются;
external absolute paths и relative commands сохраняют прежнюю семантику.
Публичный `benchmark_toolchain_sanitization.json` v2 фиксирует только relative
destination rebind-а, без source absolute path.

Projection не использует symlink, junction или hardlink, а destination entry
проверяется как не совпадающий физически с source entry. Поэтому запись в
workspace copy не меняет source dependency files. Это граница source safety,
но сама по себе она не делает результаты checks доверенными.

Для evidence integrity Controller сразу после physical copy строит private
keyed full-tree fingerprint source и workspace projection. Baseline привязан к
конкретному run и relative projection root и не находится в agent-writable
workspace. Fingerprint детерминированно включает relative path и type каждого
entry, пустые directories, размеры и полное содержимое regular files; чтение
идёт потоково. Symlink/junction/reparse, special objects и case-collision paths
отклоняются fail-closed.

Один `RuntimeProjectionIntegrityManager` защищает authoritative batches:
static toolchain probes, historical baseline gate, Controller confirmation
self-verify, deterministic и dynamic repair checks, runtime scenarios и final
held-out. Перед batch он
проверяет неизменность source baseline, затем workspace; mismatch workspace
удаляется только в пределах projection и заменяется новой independent physical
copy из неизменного source. После batch повторная full-tree проверка запрещает
принять PASS, baseline evidence, receipt или held-out semantic result, если
runtime изменился, и восстанавливает projection для следующей диагностики.
Replan/repair не создают новый baseline.

Public `runtime_projection_integrity.json` содержит только relative roots,
batch identifiers и status/event codes; keyed fingerprints остаются в
`controller_private`. Модель является detect/restore, а не OS-level
immutability: между проверкой и запуском остаётся ограниченное TOCTOU-окно.
Стоимость — O(total projected bytes) до и после каждого trusted batch; на
произвольных LLM/UI phase boundaries дерево не хешируется. Projection остаётся
Git-excluded, disposable и не входит в candidate/patch/delivery/reconstruction.

## 13. Security boundary

Execution Broker задаёт role-specific cwd, scratch, environment и policy и честно сообщает:

```text
ENFORCED
ADVISORY
UNAVAILABLE
```

`0.8.0a19` не утверждает универсальный OS-enforced sandbox для любого Controller subprocess. Owner-configured external wrappers обязаны сами иметь scoped credential/environment boundary.

## 14. Что считается завершённым

После Phase 7 весь утверждённый Step 0–7 quality-core подключён к runtime.

Остаются не новые архитектурные фазы, а project/platform capabilities и измерение качества:

```text
готовые browser/DB/1С/Airflow wrappers
универсальный restricted OS runner
Publication Layer commit/push/PR/merge — optional future
несколько clean historical trials
```

Первый обязательный интеграционный checkpoint после Windows self-check — `_90`.
