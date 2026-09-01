# История и дальнейший маршрут

## Phase 7 — 0.8.0a10

Phase 7 завершила Step 0–7 quality-core. Final Gate теперь механически связывает Step 3–6 с одним candidate и текущим revision vector, проверяет `candidate.patch` реконструкцией с recorded baseline, создаёт immutable `final-acceptance.v2` и отделяет качество candidate от доставки через `delivery-record.v2`.

`apply_to_source` использует короткий delivery lock, повторные source HEAD/clean/preimage guards, `git apply --check`, exact diff/postimage comparison и safe rollback без безусловного `reset --hard`. Dirty или параллельно изменённый source получает `RESULT_DELIVERY_BLOCKED`; accepted patch и managed worktree сохраняются.

Historical benchmark больше не использует linked worktree с общей object database. Controller материализует standalone one-commit repository только из baseline tree blobs, удаляет refs и не раскрывает previous attempts/hidden grader. Held-out различает semantic fail, infra error, timeout и candidate mutation; hidden failure завершает trial без repair feedback.

Semantic replan также усилен: rejected patch сохраняется как audit artifact, candidate очищается до baseline, task-specific registry/runtime attempt сбрасываются, а новый plan и implementation выполняют fresh Planner и fresh Implementer threads.

После Windows self-check этой версии следующим checkpoint становится реальный `_90` trial; отдельной Phase 8 quality-core не требуется.

## Phase 6 — 0.8.0a9

Phase 6 made typed runtime proof executable instead of merely fail-closed. Controller now routes `verification-plan.v1` requirements to configured `LIVE_LOCAL`, `TEST_EXTERNAL` and `PROD_OBSERVE` scenarios, binds runtime evidence to the candidate/plan, enforces startup-health-action-cleanup lifecycle and rejects candidate, source-checkout or runtime-only-file mutation. Infrastructure failures remain distinct from semantic behavior failures.

The evaluator moved from `evaluator.v4` to the two-turn `evaluator.v5`. A fresh thread first produces Controller-persisted `blind-audit.v1` without Planner, Contract or green-check framing. Only then does Phase B receive the Implementation Contract, Verification Plan, `contract-closure.v1`, deterministic evidence and runtime PASS/SKIPPED evidence. Every blind finding must be retained or dismissed with concrete evidence; `CONSUMER` and `RISK` findings expand the active Definition of Done before repair.

Machine phase id: `phase6-runtime-two-phase-evaluator`. Generic owner wrappers are supported, but universal browser/1C/PostgreSQL/Airflow wrappers, universal OS-enforced Controller subprocess isolation, clean-worktree semantic replan and the final delivery transaction remain explicit later work.

## Phase 5 portability — 0.8.0a8

Native Windows exposed one remaining lexical-path assumption in the Phase 5 project-runtime tests. `ProjectRuntimeManager` already canonicalized the worktree and built `.venv` inside it, but the test compared the serialized Python path with the unresolved `tempfile` workspace through string `startswith()`. Equivalent NTFS/temp paths can have different lexical spellings, so the assertion failed although the runtime was correctly worktree-local.

The test now proves the actual ownership invariant: the serialized Python entry point belongs to the Controller-owned worktree `.venv`. A platform-independent alias regression covers the same class of mismatch. `project-runtime.v1` is unchanged.

## Phase 5 — 0.8.0a7

Phase 5 closed two gaps left explicit in 0.8.0a6. First, material discoveries and typed checks now change the active Definition of Done instead of remaining report prose: Controller revises Implementation Contract and Verification Plan together, repeats owner/capability gates and requires the same Implementer to verify the new revision. Second, Python evidence can be bound to a disposable worktree-local `.venv`; dependency changes or hidden package drift trigger a clean rebuild before completion.

`.worktreeinclude` became the canonical repository policy for ignored runtime files required in managed worktrees. These files are not candidate output and cannot silently make a task pass through a temporary `.env` edit.
Phase 5 also made typed verification fail-closed: a registered path/ID must resolve to a real trusted check before it can revise the Verification Plan. Runtime-file paths reject symlink/junction ancestors, and sensitive comparisons use private keyed HMACs.

Machine phase id: `phase5-contract-runtime-reproducibility`. Runtime scenario executors and the two-phase Evaluator remain subsequent phases.

## Phase 4 — 0.8.0a6

Phase 4 перевела writable execution loop на `implementer.v2`, добавила Controller-private typed check registry, revision-bound self-verification receipts, activity-based watchdog, deterministic check classifications и candidate freeze guards. Эта фаза напрямую закрывает два исторических execution gaps Matrix: потерю полезной работы из-за фиксированного 900-секундного timeout и слишком позднее подключение найденных consumer tests.

Machine phase id: `phase4-implementer-controller-verification`. Автоматическая перекомпиляция active Contract/Verification Plan, worktree-local environment rebuild и universal OS-enforced Controller subprocess isolation остаются честно обозначенной alpha-границей.

## Phase 3 — 0.8.0a5

Phase 3 впервые провела обычный пользовательский запрос через отдельный `task-contract.v1`, затем через `planner.v4`, Controller-owned `implementation-contract.v3` и `verification-plan.v1`.

Ключевое изменение — перенос product intent больше не зависит от того, насколько удачно Planner пересказал запрос. Explicit acceptance/preservation копируются напрямую, а technical mapping добавляется рядом.

Typed proof routing устранил следующую неоднозначность: `LIVE_LOCAL`, `TEST_EXTERNAL` и `PROD_OBSERVE` не являются ступенями одной шкалы и могут требоваться одновременно. Verification Plan хранит все необходимые profiles и блокирует writable работу при отсутствии обязательной capability.

Фаза намеренно не реализовала runtime executor и следующие Step 3–7 contracts целиком. Это остаётся последовательной дальнейшей работой, а не скрытым ограничением PASS.


## 1. Почему 0.5.x стал перегруженным

Предыдущая архитектура содержала:

```text
Planner
→ Implementer
→ deterministic checks
→ отдельный Impact Auditor
→ Evaluator
→ held-out
```

Planner формировал десятки obligations и подробные state ledgers. Это увеличивало время и токены, но не гарантировало истинность вывода.

Historical Matrix run занял почти 24 минуты. Узкий held-out прошёл, однако последующий аудит обнаружил два material defects:

1. интерфейс мог показывать current all-matching scope, а payload брать из старого filter-action state;
2. Distribution token-only selection обходил stage guard.

Следовательно, `HARNESS_TASK_PASS` был ложноположительным.

## 2. Что из строгой версии оказалось полезным

Сохранены:

- clean isolated worktree;
- external checks;
- fresh Planner;
- отдельный Evaluator;
- hidden held-out without tutoring;
- bounded repair/replan;
- run artifacts;
- controlled publication.

Удалены повторяющиеся prose layers.

## 3. Что изменено в 0.6.0

```text
low:
Implementer → checks

medium/high:
Planner → Implementer → checks → blind Evaluator
```

Impact Auditor удалён. Planner artifact компактный. Evaluator не видит plan. Structured `PASS` механически запрещён при `unverified`.


## 3a. Исправление 0.6.1

Первый реальный Windows smoke-run обнаружил protocol bug: `thread/start.sandbox` ошибочно отправлял `workspaceWrite`. App Server 0.148.0 ожидает `SandboxMode` `workspace-write`. В 0.6.1 mapping удалён, а thread mode передаётся в canonical kebab-case.

## 3b. Matrix Windows write-blocker и исправление 0.6.2

Первый real Matrix run 0.6.1 выявил два harness gaps. Held-out baseline check упал до выполнения oracle из-за отсутствующей `SLIVIN_HARNESS_WORKSPACE`, но Controller ошибочно засчитал любой non-zero exit как доказательство broken baseline. Затем Implementer получил `Permission denied` при записи в вложенные пути, а Controller продолжил checks/evaluator/repair на пустом diff и потратил около 33 минут.

В 0.6.2 checks получают workspace env, baseline gate требует ожидаемый oracle marker, а confirmed-broken benchmark с пустым candidate diff останавливается сразу после IMPLEMENT. Последующие controlled probes успешно записали root/nested paths и existing tracked-файл через `apply_patch`, поэтому первый `Permission denied` считаем неповторившимся turn incident, а не доказанным постоянным ограничением linked worktree. Harness не ослабляет sandbox автоматически.


## 3c. Semantic benchmark 0.6.3

Matrix run 0.6.2 показал полезное поведение simplified pipeline: blind Evaluator поймал false-green regression test, а held-out затем заблокировал ещё одну semantic regression. Но сам historical oracle оставался слишком узким и `_92` нельзя было использовать как gold standard.

В 0.6.3 pipeline не усложняется. Вместо новых model roles Matrix benchmark получил standalone semantic held-out с несколькими независимыми lifecycle properties. Calibration использовала известные неполные `_90`, `_92`, `workspace_14` и candidate 0.6.2 как negative controls, а два отдельно построенных и не распространяемых semantic-good fixtures — как positive controls.

Параллельно Planner/Evaluator получили только generic policy: shared state нужно трассировать до local guards, а action-local target — проверять на ретаргетинг после старта.

## 3d. Planner uncertainty и live console 0.6.4

Первый blind Matrix run 0.6.3 корректно подтвердил baseline semantic grader (`3/7`), но затем остановился на protocol guard: Planner вернул `READY` вместе с честными `unknowns`. Такой guard оказался избыточным — неизвестность должна блокировать только когда без неё нельзя выбрать product semantics, получить обязательное evidence или безопасно продолжить. `READY` теперь может содержать non-blocking unknowns; `NEEDS_USER_DECISION`/`BLOCKED` сохраняют строгую семантику.

Тот же run показал, что под Git Bash обычный Python stdout мог буферизоваться до конца процесса. Launchers теперь задают `PYTHONUNBUFFERED=1`, а Controller включает line buffering/write-through. Pipeline не получил новых ролей или стадий.

## 3e. Retryable App Server stream errors 0.6.5

Реальный Matrix-run на Codex App Server 0.148.0 получил `error` notification с `willRetry=true` и `responseStreamDisconnected`, после чего Controller преждевременно завершил task. По App Server contract такой event промежуточный: server автоматически retry и turn не должен считаться завершённым. 0.6.5 продолжает ждать тот же `turn/completed` в пределах исходного timeout и печатает `APP_SERVER_TURN_RETRY`. Terminal error остаётся fatal.

## 3f. Outcome-based Matrix oracle 0.6.6

Blind run 0.6.5 дошёл до нескольких полезных repair cycles: Evaluator самостоятельно нашёл click-routing defect и затем Distribution stage-risk. Implementer выбрал безопасную Matrix-only изоляцию, но старый held-out всё равно требовал, чтобы Distribution внутренне принял token-only selection как `hasSelection=true`. Это оказалось implementation bias самого benchmark: исходная Matrix-задача допускает как общий fail-closed contract, так и Matrix-only изоляцию, если Distribution action не становится доступным.

0.6.6 делает held-out outcome-based и перекалибрует его на двух архитектурно разных positive fixtures. Candidate 0.6.5 добавлен как negative control и после исправления oracle всё равно получает `5/7`: остаются реальные defects new-action authority при filter residue и zero/all-excluded target safety. То есть grader стал менее overfit, но не был ослаблен под конкретный candidate.

Evaluator получает одну короткую общую policy для reachable coexistence и empty/zero/all-excluded boundaries. При исчерпании repair budget historical benchmark дополнительно запускает held-out только диагностически, без feedback Implementer.


## 3g. Execution-first 0.7.0

После 0.6.x стало ясно, что Harness уже намного лучше блокирует плохой result, но первый Implementer всё ещё часто использует только часть хорошего Planner analysis. В Matrix run Planner заранее находил Distribution, action lifecycle и sibling consumers, однако эти выводы оставались prose и становились обязательными только после Evaluator findings.

0.7.0 не добавляет новый reviewer. Controller автоматически превращает fresh Planner artifact в маленький Implementation Contract: outcome и preservation группируются, каждый materially affected consumer остаётся отдельным пунктом, test plan и required docs становятся evidence items. Implementer обязан закрыть весь contract before COMPLETE.

Второй capability gap был toolchain: Implementer сообщал, что Jest/Python недоступны, хотя Controller сразу после turn успешно запускал их по configured paths. Теперь Controller заранее создаёт Harness-owned self-verify runner из trusted repair checks. Implementer выполняет те же commands внутри своего turn, видит failures и может исправить их до первого Evaluator. Stamp привязан к current candidate fingerprint.

Третий gap — tests найденных consumers. Implementer теперь может вернуть только repo-relative test paths; Harness строит поддерживаемые commands сам из trusted toolchain/templates. Arbitrary agent shell на стороне Controller не исполняется.

Новая целевая метрика — `first_evaluation_pass`: final PASS недостаточно, если почти каждая medium-задача требует двух-трёх дорогих Evaluator repair cycles.

## 3h. Execution continuity 0.7.1

Последний 0.7.0 Matrix trial показал два execution gaps: Planner risk про coexistence `filteredBulkSelection` был найден, но не стал обязательным contract item; длинный Implementer turn дошёл до расширенных consumer tests и lifecycle snapshot, но был прерван на timeout до self-verification.

0.7.1 делает Planner risks load-bearing (`RISK-*`) и один раз продолжает тот же Implementer thread после timeout, сохраняя workspace/diff. Matrix held-out/calibration при этом не меняются.

## 3i. Canonical workflow foundation 0.8.0a1

После согласования Step 0–7 обнаружилось, что отдельные контракты были сильными, но переходы, identity evidence и invalidation не имели одного machine-readable владельца. Документация уже расходилась: Runtime был вставлен перед Evaluator в целевой схеме, но старые разделы продолжали направлять Step 4 сразу в Evaluator.

0.8.0a1 решает только этот фундаментальный класс проблем:

```text
workflow.v1
→ stage order / transitions / statuses / invalidation

run-state.v1
→ versioned execution lineage

candidate.v1
→ одна identity candidate для всех evidence stages
```

Существующие model protocols 0.7.1 пока не переписываются. Runtime executor, User Task Contract, Verification Plan, private Controller plane, новый Planner/Implementer/Evaluator contract и clean-worktree replan будут внедряться отдельными фазами поверх фиксированной state machine.

Generated `WORKFLOW.md` и `workflow.v1.json` теперь проверяются docs-sync, поэтому нумерация и переходы не должны снова расходиться вручную.

## 3l. Native Windows canonical-path fix 0.8.0a4

Первый self-check Phase 2 на целевой Windows-машине выявил четыре failures. Два теста ошибочно использовали лексический `Path.is_relative_to()` для сравнения с уже resolved paths. Ещё один failure обнаружил реальный security gap: Broker искал private root только как строковый resolved marker и не распознавал эквивалентный unresolved Windows path.

`0.8.0a4` вводит единое canonical containment, проверяет исходный и resolved aliases private root и разрешает path-valued environment entries перед сравнением. Одновременно добавлена component-boundary проверка, чтобы `controller_private_backup` не считался private plane. Это portability/correctness fix Phase 2; serialized versions `controller-plane.v1` и `execution-broker.v1` не изменились.

## 3k. Private Controller plane и Execution Broker 0.8.0a3

Authoritative Run State и receipts вынесены в `RUN_DIR/controller_private`. `.harness_tmp` окончательно закреплён как non-authoritative scratch. Добавлен role-aware Execution Broker, environment filtering, Windows-safe path validation и enforcement levels `ENFORCED / ADVISORY / UNAVAILABLE`. Self-verify claim теперь повышается Controller в HMAC receipt, связанный с candidate и revision vector.

## 3j. Native Windows file-mode portability 0.8.0a2

Первый self-check упакованной `0.8.0a1` на native Windows обнаружил, что POSIX-воспроизведение mode-only change через `Path.chmod()` непереносимо: NTFS/Git for Windows может не сообщать executable-bit рабочего файла как изменение, даже если test принудительно выставил `core.filemode=true`.

`0.8.0a2` не удаляет file-mode identity и не ослабляет production-контракт. Integration test теперь сначала спрашивает сам Git, появился ли в HEAD-to-working-tree diff переход:

```text
100644 → 100755
```

Если переход существует, все прежние assertions обязательны. Если Git/filesystem не умеет представить такой `chmod` как working-tree change, test получает явный platform `SKIP`. Документация использует точный термин `Git-visible file mode`: filesystem-only изменение, отсутствующее в Git diff, не является наблюдаемым candidate change для Git-based Harness.

## 4. Fresh Planner и накопленное знание

Fresh Planner остаётся независимым. Старые reasoning reports и reference fixes ему не передаются.

Принятые свойства должны переходить в:

```text
canonical docs
public regression tests
project checks
controller-owned hidden graders
```

Это сохраняет fresh reasoning и не заставляет каждый run повторно открывать уже доказанный contract.

## 5. Ближайший маршрут

### Stage A — доказать базовую пригодность

- 3–5 небольших `risk=low` задач;
- проверить скорость, понятность output и качество diff;
- исправлять только реальные capability gaps.

### Stage B — medium-задачи

- несколько задач со shared state/API contract;
- измерять Evaluator findings и escaped defects;
- добавлять contract tests, а не новые универсальные роли.

### Stage C — runtime capabilities

- project app per worktree;
- browser/API checks;
- test 1C/DB/ClickHouse read/write boundaries;
- production read-only observation.

### Stage D — Git tasks и orchestration

Только после устойчивого quality-core:

- task pickup;
- controlled branch/commit/PR publication;
- issue tracker/Symphony;
- CI enforcement.

## 6. Критерий упрощения

Каждый новый слой должен отвечать на вопрос:

```text
Какой измеримый класс ошибок он ловит,
который дешевле не поймать test/runtime capability?
```

Если ответа нет, слой не добавляется.
