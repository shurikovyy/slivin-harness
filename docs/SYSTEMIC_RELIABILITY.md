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
IN_SCOPE target; `evaluator.v7` требует отдельное подтверждение обоих. `user-follow-up.v2`
разделяет original provenance и current assessment. FAST не утверждает Evaluator PASS.

`implementation-contract.v4` хранит source inventory и историю proof routes;
`implementation-impact-closure.v2` связан с candidate, plan/Contract и revisions.
Старые `planner.v5`, `implementer.v5`, `implementation-contract.v3`,
`implementation-impact-closure.v1`, `evaluator.v6`, `user-follow-up.v1` несовместимы:
допустимо историческое чтение, но не admission, автоматический upgrade или receipt
reuse/resume. `workflow.v7` содержит current boundary map; старые snapshots исторические.

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

До report validation сохраняется private `candidate-checkpoint.v1` SAVED_UNVERIFIED:
baseline и physical inventory, изменённые bytes/deletions, полный active Contract,
source inventory, revision/registry binding, source manifest Harness и версии
Controller/receipt/execution policy. Controller evidence и authored role scratch
копируются в digest blobs до cleanup. Воспроизводимые caches/runtime исключаются с
inventory; sensitive material не читается. `verify_checkpoint_evidence` обнаруживает
удаление/подмену durable bytes и не создаёт fresh receipt. Checkpoint не является
приёмкой или cross-process resume.

Timeout/unknown transport до raw report сохраняет actual terminal candidate либо
UNKNOWN с invalidated прежней identity. Atomic writes повторяют только Windows
sharing/access violations с bounded budget. Ambiguous completed replace сверяется
по bytes и не повторяет model/check/delivery side effects.

## Запуск квалификации

`py.exe -3 tools/release_check.py --profile windows-local` требует native Windows и
чистый Git commit. Используются текущие configured executables; доступны explicit
`--node`, `--jest`, `--codex`, `--output`. Evidence сохраняется вне checkout.
`--diagnostic` допускает dirty tree, но никогда не квалифицирует сборку.

`slivin_harness/boundary_contracts.json` фиксирует entrypoints, schemas, owner,
pre/postconditions, freshness/evidence, outcomes, recovery/checkpoint и обязательные
test IDs четырёх семейств каждой границы. AST проверяет точность hooks; runtime hooks
только наблюдают existing execution. RETURN не означает PASS: gate требует успешного
исполнения каждого required case ID. Missing/NOT_RUN/FAIL не заменяется соседним тестом.
RuntimeExecutor и Controller contract closure входят в B16.

Обязательны все семь stages: self-check, boundary families, stateful/fault injection,
mutations, mixed Node/Jest, native role sandbox, real-model FULL tasks. Mutation gate
требует unmutated PASS, затем конкретный typed failure и исполнение mutated target
на disposable source copy. Infrastructure ERROR не считается обнаружением дефекта.

FULL fixtures фиксированы в `tools/release_real_models.py`: expiry, suspension,
повтор expiry; 900 секунд на turn, два fix и два replan cycles. Public baseline
assertions должны реально выполниться и упасть. Delivered candidate проверяется
исходными frozen Node/Jest assertions, сохранностью tests/config и независимого
legacy дефекта, двумя разными follow-ups, actual identity, полными stage payloads,
digests/current receipt, reconstruction и keep_worktree delivery. Правильная задача,
завершившаяся controlled stop, остаётся FAIL.

`qualification.json` связывает stages/logs с full Git SHA, source manifest,
Harness/workflow и exact executables. Для npm Codex сверяется полная цепочка:
`codex.cmd`, фактически выбранный им Node, `codex.js`, package metadata,
native vendor payload со всеми helpers и command processor. Resolution и hashes
повторяются после прогона; неизвестный wrapper не получает qualification.
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
