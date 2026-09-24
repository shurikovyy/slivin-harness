# Квалификация системной надёжности

Требование: `HARNESS_SYSTEMIC_RELIABILITY_CLOSURE`, пакет владельца
`HARNESS_SYSTEMIC_RELIABILITY_PACKAGE/CODEX_SYSTEM_STABILIZATION.md`.
Исходная сборка: `52ec0feba88c8f923e278265584df4823ed92952`, `0.8.0a30`.

Квалификация требует одновременно safety и positive progress. Controlled stop
является провалом позитивного сценария. Self-check, synthetic orchestration,
настоящие subprocess assertions, native role sandbox и real-model acceptance
имеют отдельные результаты. Обязательный NOT_RUN запрещает квалификацию.

## Фиксированный набор испытаний

Набор фиксируется до изменения production протокола. Исправление неверного
expectation требует установленной причины в evidence отчёте.

- Boundary families: input, output, freshness, recovery для всех production
  переходов, включая continuation/repair/expansion/replan, Evaluator A/B,
  handoff/reconstruction/delivery и terminal failure.
- Коллекции: 0, 1, 3, 5, 25 записей; independent observations, Unicode,
  перестановки unordered assessments, missing/duplicate/unknown/stale refs.
- Batch recovery: 3/5/25 independent evidence errors, один corrective turn;
  неизменные candidate/claims, отсутствие прогресса и malformed parent.
- Stateful: stdlib deterministic generator, seeds 1729 и 2718; длина до 12,
  100 последовательностей на seed; production adapters и независимые
  assertions freshness/obligation retention. Это конечная выборка.
- Mutations на disposable copy: native→Jest, skip stale refresh, premature B,
  drop source finding, stale receipt, always stop valid report, candidate write
  при report correction. Каждая должна сделать соответствующий control красным.
- Native Windows: positive/negative Node/Jest assertions, cold/warm cache,
  scoped scratch initial/continuation/replan и другая read-only роль;
  actual prohibited writes, exact executable/policy version.
- Real model: два различных small FULL tasks и повтор первого (3 runs),
  shared predicate и несколько readers, независимый legacy failure и
  documentation finding; mixed runners. Короткий product intent без указания
  конкретных readers/решения. До Final Acceptance, reconstruction и
  `keep_worktree`; timeout 900 секунд на роль, обычные bounded repair/replan.
  FAST отдельно проверяет declared-only handoff без fabricated Evaluator PASS.

## Preservation boundaries

User intent/owner gates, автономный impact closure, все IN_SCOPE obligations,
blind persistence до disclosure, hidden isolation, physical candidate/Git/runtime
integrity, current receipts, scoped role permissions и те же trusted checks при
reconstruction остаются обязательными. Внешние repositories/profiles/runtime
versions не изменяются. Cross-process resume не заявляется.

## Действующие протоколы

`planner.v6` имеет единственный `impact_closure.in_scope_consumers`. Controller
создаёт immutable `impact-sources.v1`: ID/revision, author, исходный claim и source
artifact. Namespace зависит от Task Contract и Planner model. Implementer observations
идемпотентны только при неизменном payload под тем же local ID. Независимые Evaluator
findings получают свой origin до следующего Implementer continuation.

`implementer.v6` передаёт новые observations и собственные `source_assessments`.
Controller компилирует canonical ledger из originals и current evidence. Повторение
inherited prose и `discovered_obligations` отсутствует в wire. COMPLETE требует
всех current refs: missing/duplicate/unknown/stale, CHALLENGE и INSUFFICIENT_EVIDENCE
не становятся подтверждением. PROMOTE сохраняет original outside claim и current
IN_SCOPE target; `evaluator.v8` требует отдельное подтверждение обоих. `user-follow-up.v2`
разделяет original provenance и current assessment. FAST не утверждает Evaluator PASS.

`implementation-contract.v4` хранит source inventory и историю proof routes;
`implementation-impact-closure.v2` связан с candidate, plan/Contract и revisions.
Старые `planner.v5`, `implementer.v5`, `implementation-contract.v3`,
`implementation-impact-closure.v1`, `evaluator.v6`, `evaluator.v7`, `user-follow-up.v1` несовместимы:
допустимо историческое чтение, но не admission, автоматический upgrade или receipt
reuse/resume. `workflow.v7` содержит current boundary map; старые snapshots исторические.

Planner initial artifact проходит bounded local-wire admission до отдельной capability
negotiation. Evaluator Phase B использует Controller-built `phase-b-origin-catalog.v1`:
model wire содержит exact `origin_ref`, а authority/classification/source ID/revision
canonicalize из current state. Unknown/incompatible handle исправляется только как exact
reference; semantic conflicts и integrity/catalog failures не становятся retries.
Status-compatible negative disposition, который уже объявляет material problem, но
не materialized final finding/ID binding, получает один отдельный claim-preserving
closure turn. Все semantic claims и existing findings frozen; `PASS` с negative,
semantic reversal и repeated/no-progress closure не retry.

## Recovery и checkpoints

Общий report validator собирает независимые leaf diagnostics в batch; malformed
parent закрывает собственную ветвь. Allowlist разрешает конкретные evidence/path/symbol
поля. Claims, refs, statuses, порядок records и candidate неизменны. Бюджет — два
corrective turns; повтор diagnostics без прогресса завершает correction. Product
repair и technical/proof replanning остаются отдельными маршрутами.

`proof-route-review.v1` выполняется независимым Planner на сохранённом candidate.
READY заменяет только effective proof targets по item ID/previous fingerprint,
сохраняя requirements, origins и owner gates. Новые revisions инвалидируют verification.
BLOCKED сохраняет candidate. Строго проверенный TECHNICAL_REPLAN_REQUIRED направляется
в semantic reset/fresh Planner; review и reset расходуют один replan cycle. Все replans
ограничены manifest `max_replan_cycles`.

При валидном BLOCKED фактический digest delta typed check registry/config разрешает
до двух check rebind continuations на stabilization. Controller заново компилирует
checks, выполняет capability gate и требует current self-verification. BLOCKED без
delta не считается прогрессом; owner assertions не исключаются.

До report validation сохраняется private `candidate-checkpoint.v2` SAVED_UNVERIFIED:
baseline и physical inventory, изменённые bytes/deletions, полный active Contract,
source inventory, revision/registry binding, source manifest Harness и версии
Controller/receipt/execution policy. Candidate bytes, Controller artifacts,
self-verify stamp и explicit Controller-registered durable role evidence читаются
stable и sealed строго. Произвольный unregistered role scratch не обходится и
machine-readably отмечается как forensic tree exclusion; его исчезновение/rename
не является availability dependency. Link/junction escape, disappearance/replacement
registered evidence и candidate drift fail closed. Sensitive material не читается.
Checkpoint не является приёмкой или cross-process resume.

Timeout/unknown transport до raw report сохраняет actual terminal candidate либо
UNKNOWN с invalidated прежней identity. Atomic writes повторяют только Windows
sharing/access violations с bounded budget. Ambiguous completed replace сверяется
по bytes и не повторяет model/check/delivery side effects.

## Запуск квалификации

`py.exe -3 tools/release_check.py --profile windows-local --qualification release`
требует native Windows и чистый Git commit. Совместимый default также выбирает
`release`. Используются текущие configured executables; доступны explicit
`--node`, `--jest`, `--codex`, `--output`. Evidence сохраняется вне checkout.
`--diagnostic` допускает dirty tree, но никогда не квалифицирует сборку.
`--qualification dev` выполняет те же deterministic stages и обязательный
`native_roles`, затем только `expiry-1` с `gpt-5.6-terra`/`medium` и fail-fast.
Его успешный статус — `DEV_QUALIFICATION_PASS`; он никогда не означает
`RELEASE_QUALIFIED`. `release` pin-ит `gpt-5.6-terra`/`medium`, запускает
expiry-1 → suspension-1 → expiry-2 и останавливается после первого неуспеха;
неисполненные cases фиксируются как `NOT_RUN`. На квалификационном профиле один
model turn ограничен 20 минутами, synthetic case — 45 минутами. Только прошедший
весь профиль может выдать `RELEASE_QUALIFIED`. Прежнее `gpt-5.6-sol`/`high` evidence сохраняется
как история и не квалифицирует поддерживаемую связку.
Default evidence root и внутренние имена FULL workspaces ограничены по длине; перед
model turns gate вычисляет фактический Windows path budget для physical `node_modules`
и завершает этап типизированным FAIL, если безопасного запаса нет.
Успешные stages не стримят полный log. При failure console показывает stage, absolute
stage-log path, существующий summary path, безопасно извлечённые typed status/reason и
redacted tail последних 60 строк. Summary читается только как bounded JSON object;
private artifacts не раскрываются, известные credential values и inline credential
assignments в console tail маскируются. Полный log остаётся в release evidence root.

`slivin_harness/boundary_contracts.json` фиксирует entrypoints, schemas, owner,
pre/postconditions, freshness/evidence, outcomes, recovery/checkpoint и обязательные
test IDs четырёх семейств каждой границы. AST проверяет точность hooks; runtime hooks
только наблюдают existing execution. RETURN не означает PASS: gate требует успешного
исполнения каждого required case ID. Missing/NOT_RUN/FAIL не заменяется соседним тестом.
RuntimeExecutor и Controller contract closure входят в B16.

Обязательны все двенадцать stages: self-check, boundary families, stateful/fault injection,
mutations, mixed Node/Jest, deterministic artifact transcript replay, deterministic
Codex transport replay, deterministic native command/recovery replay, exact executable
real-model failure replay, boot, native role sandbox и real-model FULL tasks. Все четыре
replay gates и `entrypoint_boot` обязаны PASS до любых model-backed turns.
Boot stage запускает `task_runner.py`, `tools/smoke_readonly_scratch.py` и
`tools/release_real_models.py` как реальные Python scripts с explicit
machine-readable `--boot-check`; каждый record требует `model_execution=NOT_RUN`.
Artifact gate воспроизводит sanitized
QE1/QS1/QE2 failures. Transport gate проверяет captured `shr-q-5654234e36`
(`-NoProfile -Command`, nullable legacy probe output) через adapter/AGENTS
admission и `shr-q-5a12a511f1` (`-Command`, legacy path-bearing probe, AGENTS и
Jest) как captured transport compatibility. Дополнительный captured Evaluator `shr-q-42026aa1d8`
доказывает bundled `pwsh.exe -Command` envelope только на уровне adapter:
его прежний probe contract не является доказательством текущего smoke.
Synthetic negatives помечены отдельно. Fixture SHA256, origin,
source run и expected/actual outcome записываются в `transport_replay/summary.json`.
Этот replay закрывает известные representation regressions, но не доказывает
native sandbox и не заменяет три FULL cases. Отдельный `native_command_replay`
сохраняет sanitized captured `shr-q-e082a9f137`: transport-valid legacy probe
исказил только peer spelling (`Slivin.Aleksandr` → `Slivin\Aleksandr`). Replay
обязан получить `ROLE_COMMAND_DRIFT`, доказать один same-thread exact-entrypoint
recovery, exhaustion при повторном drift и отсутствие retry для настоящего policy/
config-integrity failure. SHA-256 captured fixture и original mismatch остаются в
machine-readable summary. Mutation gate
требует unmutated PASS, затем конкретный typed failure и исполнение mutated target
на disposable source copy. Infrastructure ERROR не считается обнаружением дефекта.

`real_model_failure_replay.v2` использует sanitized captured evidence qualification
`shr-q-fb852e56ab`: QE1 проверяет hard conflict для exact captured `PASS`+negative и
bounded closure для status-compatible claim, QS1 — отсутствие checkpoint зависимости
от исчезнувшего `jest-access-cache/haste-map-*`, QE2 — mixed README authority.
Captured `shr-q-b181436a98` QE2 Phase-A fixture дополнительно проверяет, что
`IMPACT_PATH_MISSING` допускает только удаление точных Controller-diagnosed indexes:
survivors не заменяются, не удаляются и не переупорядочиваются; пустой survivor set
остаётся terminal. Каждый result связывает fixture bytes и retained source artifact SHA-256.

FULL fixtures фиксированы в `tools/release_real_models.py`: expiry, suspension,
повтор expiry; 900 секунд на turn, два fix и два replan cycles. Direct diagnostic
CLI принимает explicit `--case`, `--model`, `--effort`, `--fail-fast`; отсутствие
`--case` сохраняет полный трёх-case порядок без fail-fast. Public baseline
assertions должны реально выполниться и упасть. Delivered candidate проверяется
исходными frozen Node/Jest assertions, exact tests/config и независимой legacy
implementation. В mixed README eligibility section может изменяться по task semantics,
но unrelated legacy-label paragraph и deployment navigation сохраняются независимо.
Также требуются два разных follow-up, actual identity, полные stage payloads,
digests/current receipt, reconstruction и keep_worktree delivery. Каждый успешный
FULL payload затем обязан отвергнуть независимые in-memory подмены owner command,
Node, runtime/capability, Evaluator A/B и receipt; исходные artifacts и candidate
после fault injection должны остаться неизменны. Правильная задача,
завершившаяся controlled stop, остаётся FAIL.

Candidate checkpoint не трактует произвольный authored role scratch как authority.
Только explicit Controller-registered durable role path sealing до report validation;
остальное дерево scratch остаётся forensic/non-authoritative независимо от имени cache.

`candidate.patch` строится из exact physical blobs в disposable Git object database.
Пути, exact bytes которых configured Git worktree conversion не воспроизводит из
filtered blob, кодируются binary delta; остальные сохраняют читаемый text diff.
Истинно binary content также остается binary. Disposable loose objects удаляются,
включая снятие Windows read-only bit после завершения packaging. Controller-owned
`info/attributes` в disposable Git directory
не позволяет project diff driver отменить required binary delta. Reconstruction
применяет patch к private index, materializes его blobs без checkout filters и затем
требует прежний exact `candidate_id`.
`apply_to_source` сначала сопоставляет тот же patch и каждый blob с accepted candidate
в disposable index, затем переносит exact postimages при сохранении source HEAD/clean
rechecks, transactional rollback и защиты concurrent user edits. Реальный Git index и
source object database для этой проверки не используются как writable storage.

`qualification.json` связывает stages/logs с full Git SHA, source manifest,
Harness/workflow, qualification mode, explicit model/effort, Codex version,
requested/executed cases, fail-fast/release eligibility и exact executables. Эти же
model/effort поля обязательны в каждом real-model case summary. Для npm Codex
сверяется полная цепочка:
`codex.cmd`, фактически выбранный им Node, `codex.js`, package metadata,
native vendor payload со всеми helpers и command processor. Resolution и hashes
повторяются после прогона; неизвестный wrapper не получает qualification.
На Windows Controller кодирует `model` и `model_reasoning_effort` как validated
TOML literal strings (`model='…'`). Deterministic argv test проходит настоящий
`subprocess.list2cmdline → cmd.exe /c → disposable .cmd → child executable`, отдельно
фиксирует raw `%*` и final child argv и запрещает `\"`, split или truncation.
Native summary связывает hashes launch requests, role contexts и actual canary
results с этой сборкой. Изменение sources/executables во время прогона
запрещает RELEASE_QUALIFIED. Обязательный NOT_RUN/SKIP/FAIL запрещает qualification.
Отдельные unsupported platform checks отмечаются по exact test ID/reason; native
role и реальные runner assertions нельзя пропустить этим исключением.

## Evidence и пределы

Baseline probe на native Windows подтвердил exhaustion для 3/5 errors и
CHANGED_CLAIMS при исправлении трёх за один turn. Подробный JSON хранится вне Git.
Первый sandbox self-check упёрся в запрет Python temporary directories;
его результаты не считаются product failures или native acceptance.
Разработческие прогоны не являются qualification record. Review сохранил counterexamples
legal promotion, timeout-before-raw, proof→technical routing и ложного acceptance
по marker-файлам/infrastructure errors. Regression controls требуют positive progress
и rejection этих дефектов. При миграции исправлены fixtures, дублировавшие P/I origin,
смешивавшие discovery continuation с fresh semantic reset и использовавшие Windows
short-path alias в Jest selector. Owner assertions не ослаблены.

Конечные stateful sequences и три generic FULL runs не доказывают универсальную
полноту поиска, произвольный crash recovery или все варианты sandbox. Qualification
относится только к указанному immutable build и фактически выполненным уровням.
