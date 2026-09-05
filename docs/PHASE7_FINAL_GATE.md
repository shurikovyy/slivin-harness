# Phase 7 — Final Gate, delivery и benchmark isolation

Phase 7 завершает согласованный Step 0–7 quality-core. Она не добавляет нового LLM-reviewer. Все операции выполняет Controller.

Версии слоя:

```text
phase7-final-gate.v1
patch-proof.v1
final-acceptance.v2
delivery-record.v2
heldout-evidence.v2
benchmark-isolation.v1
```

## Понятная схема

### Обычная production-задача

```text
Step 6: EVALUATION_PASS
        ↓
Controller фиксирует final candidate
        ↓
проверяет, что Step 3–6 относятся
к одному candidate и текущим ревизиям
        ↓
QUALITY GATE RECONCILIATION PASS
        ↓
строит candidate.patch
        ↓
применяет patch к чистой копии baseline
        ↓
reconstructed candidate == accepted candidate?
 ├─ нет → FINAL GATE FAIL
 └─ да
        ↓
создаёт immutable final_acceptance.json
        ↓
доставка:
 ├─ keep_worktree
 └─ apply_to_source под delivery lock
        ↓
RESULT_DELIVERY_PASS
        ↓
HARNESS_TASK_PASS
```

### Historical benchmark

```text
normal Step 0–6 pipeline PASS
        ↓
standalone sanitized benchmark repository
        ↓
final candidate freeze
        ↓
hidden held-out exam
        ↓
semantic / infra / timeout / mutation classification
        ↓
HELDOUT_PASS?
 ├─ нет → trial закончен, feedback агентам не выдаётся
 └─ да
        ↓
patch reconstruction + immutable acceptance
        ↓
HARNESS_BENCHMARK_PASS
```

## 1. Один candidate для всех доказательств

Final Gate читает authoritative `run_state.json` из private Controller plane и требует:

```text
Step 3 IMPLEMENTATION_COMPLETE
Step 4 DETERMINISTIC_VERIFICATION_PASS
Step 5 RUNTIME_VERIFICATION_PASS или explicit SKIPPED
Step 6 EVALUATION_PASS
```

Для Step 3–6 должны совпасть:

```text
candidate_id
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
runtime_environment_rev
attempt_id
```

Если candidate или active Definition of Done менялись после любого PASS, старое evidence не принимается.

## 2. Patch reconstruction proof

`candidate.patch` не считается правильным только потому, что `git diff` был успешно записан.

Controller получает canonical changed-set из physical inventory и помещает его
в одноразовый private index через `GIT_INDEX_FILE`: `read-tree` загружает
recorded baseline, additions добавляются с `-f`, deletions удаляются только из
temp index, hooks направлены в пустой Controller-owned directory. Real worktree
index до/после не меняется. Поэтому ignored candidate files не теряются между
identity, evaluator diff, patch и reconstruction.

Controller создаёт новую временную repository-копию, получает recorded baseline, применяет patch и снова вычисляет `candidate.v1`:

```text
recorded baseline
        +
candidate.patch
        ↓
reconstructed candidate.v1
```

Принимается только точное совпадение:

```text
reconstructed candidate_id
==
final candidate_id
```

`candidate.v1` связывает фактические bytes worktree. Поэтому private proof-repository до checkout baseline зеркалирует только ограниченный allowlist effective Git worktree-conversion settings исходного repository:

```text
core.autocrlf
core.eol
core.safecrlf
core.filemode
core.symlinks
```

Это необходимо на native Windows: patch хранит канонические LF-строки, но accepted worktree при `core.autocrlf=true` содержит CRLF. Принудительный `core.autocrlf=false` реконструировал бы другой byte-level candidate. Hooks, aliases, transport/credential settings и arbitrary filter configuration в proof-repository не копируются.

Proof сохраняется как `patch-proof.v1`.

Если production-задача корректно завершается без изменения файлов, пустой patch
рассматривается как identity transform: Controller всё равно пересобирает baseline
candidate и требует точного совпадения identity. Historical benchmark отдельно
требует material candidate change ещё до Final Gate.

После identity proof Controller materializes только authoritative runtime:

```text
source-owned projection → fresh physical copy в proof repo
source-owned exposed file → pristine source copy
project runtime → fresh proof-repo .venv
external executable → original configured external entry
```

Toolchain разрешается заново и historical source-local entries rebind-ятся к
proof projections. Original candidate workspace `node_modules`, `.env`, `.venv`,
`.harness_tmp` и loose objects не копируются. Затем без agent/Codex stages
выполняются static preflight, все active repair specs и, для historical
benchmark, final held-out specs. Каждый batch защищён physical candidate,
Git-control и runtime-projection guards. Public
`reconstructed-verification.v1` содержит только статусы и IDs; hidden output и
command diagnostics остаются private.

## 3. Immutable final acceptance

`final_acceptance.json` создаётся только после patch reconstruction и
`reconstructed-verification.v1=PASS` и больше не перезаписывается.

Перед созданием Controller повторно проверяет, что quality reconciliation,
patch SHA-256, expected/reconstructed candidate и, для benchmark, `HELDOUT_PASS`
относятся к одному final candidate. Несогласованные artifacts не могут быть просто
связаны в формально корректный JSON.

Он содержит компактные ссылки на:

```text
Task/Plan/Contract/Verification revisions
final candidate_id
stage bindings
artifact SHA-256
patch SHA-256
patch reconstruction proof
reconstructed verification proof
held-out evidence — только benchmark
```

Это `final-acceptance.v2`: он не дублирует все логи и reasoning, а связывает уже существующие доказательства.

## 4. Result delivery

### keep_worktree

```text
source checkout не меняется
managed worktree сохраняется
candidate.patch сохраняется
```

### apply_to_source

Доставка отделена от качества candidate. Перед apply Controller берёт короткий lock в Git common-dir и повторно проверяет:

```text
source HEAD == HEAD на старте task
source clean с учётом разрешённых local files
preimage затрагиваемых файлов не изменился
git apply --check PASS
```

После apply проверяется:

```text
actual source patch == accepted candidate.patch
actual source postimages == accepted candidate postimages
source HEAD не изменился
```

Если пользователь или IDE изменили source параллельно, результат не перезаписывается. Harness возвращает `RESULT_DELIVERY_BLOCKED`, сохраняет accepted patch и worktree.

Если apply частично изменил source, Controller откатывает только те файлы, которые всё ещё совпадают с ожидаемым post-apply состоянием. Сторонние конкурентные изменения не затираются.

Delivery artifact использует `delivery-record.v2`.

## 5. Semantic replan без anchoring

`REPLAN_REQUIRED` означает, что отвергнута сама техническая модель, а не только отдельная строка реализации.

Поэтому Controller:

```text
сохраняет rejected candidate.patch как audit artifact
        ↓
сбрасывает tracked/index state до recorded baseline
        ↓
удаляет untracked candidate files
        ↓
сохраняет runtime-only .env / .venv / Harness scratch policy
        ↓
очищает task-specific check registry
        ↓
пересобирает authoritative project runtime
        ↓
запускает fresh Planner
        ↓
строит новый Contract/Verification Plan
        ↓
запускает fresh Implementer thread
```

Новый Planner не видит rejected patch. Это отличает semantic replan от обычного repair, который продолжает тот же Implementer thread.

## 6. Historical benchmark isolation

Обычный Git worktree делит object database и refs с source repository. Для hidden historical exam это недостаточная изоляция: reference solution может находиться в другой ветке или unreachable object.

Phase 7 создаёт standalone one-commit repository:

```text
только baseline tree blobs
один synthetic detached commit
нет source refs
нет shared .git metadata
нет unrelated objects
нет previous attempt artifacts
```

Submodules и небезопасные native-Windows symlink fixtures блокируются до trial; их нужно заранее материализовать как обычный benchmark fixture.

Benchmark всегда использует `keep_worktree`.

### Authorized source-owned toolchain rebind

Для historical project local profile может объявить `copy_untracked =
["node_modules"]`. Controller физически копирует directory в managed
workspace; это не dependency installation и не изменение baseline.

Перед capability/check execution `sanitize_benchmark_toolchain()` применяет
три ветви к каждой entry: absolute source-local path rebind-ится на workspace
copy только по Controller-authoritative projection provenance; source-local
path без корректной projection удаляется fail-closed; relative command и
absolute external path сохраняются. Rebind проверяет canonical containment,
safe repo-relative path, наиболее узкий projection root, существование и тип
destination, отсутствие symlink/junction/reparse aliases в ancestor-ах и
отсутствие physical alias с source entry.

`benchmark_toolchain_sanitization.json` имеет schema
`benchmark-toolchain-sanitization.v2`: `removed`, внешние `retained_keys` и
`rebound_to_workspace` разделены. Последнее содержит только repo-relative
destination, а `fresh_dependency_install_performed` всегда `false` для этой
projection. Direct source absolute paths в public artifact не записываются.

После sanitize/rebind и structural validation, но до historical baseline
semantic command, Controller выполняет `static-toolchain-preflight.v1` для всех
repair/held-out templates. Source-owned rebound entry принимается только если
его признаёт established runtime projection authority. Node/Jest probes и Jest
`--showConfig` (explicit config или cwd discovery) защищены тем же full-tree
integrity batch guard и canonical candidate identity до/после; hidden held-out
script лишь проверяется как существующий input и не исполняется. Candidate
mutation очищает probe evidence и останавливает run до semantic baseline.
Failure останавливает Step 0 до Codex app-server, Task Contract и Planner.

Physical independence защищает source, но не достаточна для trusted evidence.
После initial copy Controller сохраняет private keyed fingerprint полного
projection tree. Перед каждым historical baseline, Controller-confirmed
self-verify, static probe, deterministic/dynamic check batch, runtime scenario и
held-out он:

1. требует совпадения текущего source tree с immutable-for-run baseline;
2. при workspace mismatch полностью заменяет только destination projection
   новой physical copy;
3. повторно проверяет containment, regular-tree shape, отсутствие aliases и
   точное совпадение fingerprint;
4. после batch снова проверяет workspace tree независимо от exit code.

Mutation во время batch инвалидирует весь текущий result: green exit code не
становится PASS, self-verify receipt не выпускается, baseline не считается
semantic broken, а held-out не становится semantic PASS/FAIL. После обнаружения
Controller восстанавливает runtime без скрытого retry самого check. Distinct
integrity reasons:

```text
RUNTIME_PROJECTION_SOURCE_CHANGED
RUNTIME_PROJECTION_WORKSPACE_RESTORE_FAILED
RUNTIME_PROJECTION_BASELINE_MISMATCH
RUNTIME_PROJECTION_MUTATED_DURING_CHECK
RUNTIME_PROJECTION_UNSUPPORTED_ENTRY
```

Safe public events находятся в `runtime_projection_integrity.json`; source
absolute paths, file contents и private fingerprints туда не попадают.
Full-tree pre/post proof стоит O(total projected bytes) на trusted batch
boundary. Это detect/restore boundary, не заявление OS-level immutability;
ограниченное TOCTOU-окно сохраняется.

## 7. Held-out — экзамен, а не repair tool

Held-out запускается только после normal pipeline PASS и никогда не передаётся Planner, Implementer или Evaluator.

Результаты различаются:

```text
HELDOUT_PASS
HELDOUT_SEMANTIC_FAIL
HELDOUT_INFRA_ERROR
HELDOUT_TIMEOUT
HELDOUT_MUTATED_CANDIDATE
```

Semantic fail требует oracle marker: обычная ошибка запуска Node/Python не может выдаваться за доказательство неправильного candidate.

`HELDOUT_SEMANTIC_FAIL` завершает текущий trial. Hidden assertions не возвращаются агентам для ещё одной попытки. Evidence сохраняется как `heldout-evidence.v2` для разработчика Harness.

## 8. Что Phase 7 не делает

Phase 7 не выполняет:

```text
commit
push
PR
merge
deployment
production write verification
```

Это отдельный будущий Publication Layer, если он вообще понадобится.

Также остаются project/platform capabilities, а не обязательные части core:

```text
универсальный OS-enforced subprocess sandbox
готовые browser wrappers
готовые PostgreSQL/1С/Airflow wrappers
```

Execution Broker продолжает честно различать `ENFORCED`, `ADVISORY` и `UNAVAILABLE`.

## 9. Следующий checkpoint

После Windows `HARNESS_SELF_CHECK_PASS` версии `0.8.0a18` новых архитектурных фаз quality-core не требуется.

Следующий шаг:

```text
historical _90 baseline
        ↓
полный Step 0–7 trial
        ↓
скрытый calibrated Matrix oracle
        ↓
анализ first-pass / repairs / held-out result
```

Этот trial покажет, повысили ли новые контракты качество реальной работы, а не только внутренние unit-тесты Harness.
