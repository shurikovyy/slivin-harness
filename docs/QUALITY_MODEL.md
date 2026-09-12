# Модель качества Slivin Harness 0.8.0a32 — Phase 7

Квалификация сборки требует safety и завершения корректных задач. Единый
[release gate](SYSTEMIC_RELIABILITY.md) выполняет boundary families, stateful faults,
defect mutations, настоящие runners/role sandbox и три FULL model runs.
Controlled stop в positive case, mandatory skip или NOT_RUN запрещает RELEASE_QUALIFIED.
Согласованные JSON и exit 0 без current evidence, assertions, reconstruction и
safe delivery недостаточны.

Для Planner/Evaluator различаются requested scratch-only policy, reported App Server
policy и результаты реальных sandbox operations. Metadata PASS не означает запуск
assertion: opt-in [native acceptance](WINDOWS_SETUP.md#native-scoped-scratch-acceptance)
проверяет cached Jest и denied project/dependency/neighbor mutations в одном execution
path. Controller tool probes не заменяют этот слой. Scratch исключён из candidate/patch;
candidate/Git/runtime guards остаются дополнительной проверкой. Rationale и границы
доказанного записаны в [D-018 и D-021](DECISIONS.md).

Нормативный autonomy contract: **[AUTONOMOUS_ENGINEERING_CONTRACT.md](AUTONOMOUS_ENGINEERING_CONTRACT.md)**.
User scope задаёт observable результат и explicit ограничения; technical impact
radius определяет обязательное исследование последствий найденной причины;
patch size — минимальные изменения после impact closure. Эти понятия не равны.
«Минимальный patch» не означает «исследовать минимальное количество файлов».

## Основная формула

```text
explicit user intent
→ statically executable manifest toolchain
→ доказанная technical model
→ load-bearing Definition of Done
→ typed proof routes
→ smallest complete implementation
→ reproducible project runtime
→ independent deterministic verification
→ required observable runtime proof
→ blind semantic challenge
→ one-candidate Final Gate
→ reconstructed patch
→ safe result delivery
```

Ни один слой не заменяет следующий:

```text
SELF VERIFY
≠ Controller deterministic PASS
≠ Runtime PASS/SKIPPED
≠ Evaluator PASS
≠ Final Gate PASS
```

## Static Toolchain Preflight

`static-toolchain-preflight.v1` доказывает только, что manifest command
templates однозначно раскрываются, required executables и известные inputs
доступны, а lightweight tool probes проходят до agent stages. Для projected
runtime probes действуют full-tree pre/post integrity guard и JIT restore.
Git metadata защищены независимым pre/post `git-control-integrity.v1`, а raw
probe stream ограничен 1 MiB до записи в private log.
Preflight не запускает tests/hidden oracle, не доказывает product correctness и
не заменяет post-plan capability gate для требований, появившихся из Planner и
Verification Plan.

Jest config выполняется как project code даже при `--showConfig`. Поэтому второй
независимый guard сравнивает canonical candidate identity до/после preflight:
tracked mutation, deletion или untracked addition отменяет любое зелёное probe
evidence. Runtime-only exclusions сохраняются. Raw probe output остаётся private,
а public artifact содержит только typed diagnostic. Explicit Jest config и cwd
auto-discovery поддерживаются без запуска tests.

После semantic reset или runtime rebuild старое tool evidence не является PASS
и не является доказательством отсутствия инструмента. До fresh Planner Controller
перепроверяет необходимые stale probes по current owner references. Initial и
replan используют один preparation route; текущие подтверждения переиспользуются.
Typed `planner-tool-evidence.v1` фиксирует результат и candidate/runtime/revision
binding. Failed required tool/config probe запрещает запуск Planner. Successful
Controller probe не доказывает доступность cache write в Planner sandbox и не
заменяет обязательные owner/product checks.

## Что доказывает каждый слой

### User Task Contract

`task-contract.v1` сохраняет явно сказанные intent, acceptance, preservation, forbidden и owner boundaries с verbatim source text. Он не доказывает technical root cause.

Разные conditions/scopes не являются direct contradiction. `READY` по-прежнему требует пустые
`ambiguities` и `reason`; если модель нарушила это или другое semantic правило artifact, Controller
возвращает validation feedback в тот же Intake-thread и принимает только исправленный полный объект.
Две bounded repair-попытки относятся к protocol correction, а не к product implementation loop.

Все JSON schemas, реально передаваемые как App Server Structured Outputs для
Task Contract, Planner, Implementer и двух Evaluator turns, проходят одну
recursive strict-schema validation до `turn/start`. Wire-level обязательность
всех properties не заменяет Controller semantic validation по status и не даёт
agent-provided `receipt_id` Controller authority.

### Planner

`planner.v6` характеризует current behavior, existing contract, root cause/extension point, consumers, state model, risks и evidence plan. Planner не доказывает корректность future candidate. Controller заранее сообщает только подтверждённые executors; READY proof не может стохастически потребовать отсутствующую capability. Первый overreach получает один corrective turn в том же thread, повторный блокируется до Contract compiler.

До `READY` обязательный typed `impact_closure` фиксирует changed contracts,
concrete consumers, классификации IN_SCOPE / NOT_AFFECTED / RELATED_OUT_OF_SCOPE,
search evidence и closure summary. Paths должны быть безопасными существующими
repo-relative файлами; symbols называют конкретные identifiers. Controller требует
взаимно однозначное соответствие IN_SCOPE ↔ affected_consumers с тем же required
behavior и proof. Проверка выполняется для initial plan, corrective turn и replan.

NOT_AFFECTED требует repository evidence и не становится obligation. Независимые
RELATED_OUT_OF_SCOPE findings сохраняются с follow-up в Planner artifact. Для
нетехнической задачи допустим `applicable=false` без fake consumers только при
непустом Controller-owned `owner_allowed_paths` из manifest `allowed_paths`, целиком
ограниченном существующими safe prose files (`.md`, `.rst`, `.txt`, `.adoc`).
Canonical paths остаются внутри workspace; search evidence — subset owner paths.
Без этой границы даже genuine prose task не получает исключение. Planner prose
и очищенные declarations не являются authority; остальные проверки отсутствия
behavioral/state/runtime obligations и concrete summary сохраняются.
Полные mechanical eligibility и границы проверки определены в normative contract.
Обычный behavioral code change не может обойти closure пустым false-ledger.
Один локальный extension point не даёт Planner права сокращать impact radius.
Полная ledger не доказывает истинность evidence или корректность future patch.

### Implementation Contract

`implementation-contract.v4` хранит минимальный обязательный Definition of Done:

```text
ACCEPTANCE
PRESERVATION
STATE optional
CONSUMERS
RISKS
DOCS optional
```

Каждый item имеет typed required proof в `verification-plan.v1`. Contract open-world: новый material consumer/risk добавляется, а старые obligations не ослабляются.

Semantic obligation и выбранный proof route различны. Broad preservation wording не
требует абсолютного PASS всего repository suite без owner gate или evidence зелёного
baseline именно этого suite. При доказанно unrelated baseline-red Planner-derived route
Implementer возвращает `REPLAN_REQUIRED` + `PROOF_MODEL_DIVERGENCE` с reason/evidence.
Clean semantic reset запускает fresh Planner и Implementer с новым proof plan, сохраняя
Task Contract, `PRESERVE-1` semantics и все owner checks. Это не правило «baseline-red
можно игнорировать»: affected regressions и достаточный preservation proof обязательны.

### Implementer self verification

`implementer.v6` использует trusted check registry и worktree-local project runtime, чтобы исправляться до сдачи. Controller-private receipt связан с candidate, revisions, runtime environment, attempt и registry digest.

COMPLETE дополнительно требует `post_patch_impact` по actual diff: reconciliation
Planner changed contracts/IN_SCOPE, повторный disposition NOT_AFFECTED, сохранение
RELATED_OUT_OF_SCOPE и exact changed-path review. DISCOVERED consumers и новые risks
совпадают с discovered obligations и проходят Controller Contract expansion до
final COMPLETE. Изменившаяся technical model требует REPLAN_REQUIRED.
FAST подчиняется тому же owner-backed prose-only исключению; отсутствие Planner
не освобождает от engineering impact sweep. После repair sweep и self-verify повторяются.

Controller-normalized `implementation-impact-closure.v2` привязан к candidate,
Planner/Contract fingerprints, changed paths и revision binding. Это доказательство
структуры, согласованности и актуальности declarations; оно не доказывает истинность
semantic evidence или исчерпывающий discovery. Evaluator независимо challenge-ит эти
declarations. Controller доставляет current related findings пользователю через обязательный `user-follow-up.v2` до held-out, включая valid zero report. FULL требует независимого CONFIRMED_OUT_OF_SCOPE; FAST явно сообщает об отсутствии Evaluator review.

Self-verify остаётся assertion builder-а, а не финальным authority.

### Controller deterministic checks

Step 4 независимо запускает local machine assertions на frozen candidate. Infrastructure errors, timeout, assertion failure и mutation не смешиваются.

### Runtime Verification

Step 5 выполняет только proof profiles, которые local checks не покрывают:

```text
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

`TEST_EXTERNAL` требует fresh readback и cleanup/disposable boundary. `PROD_OBSERVE` требует technically scoped read-only access. Local-only task получает explicit `RUNTIME_VERIFICATION_SKIPPED`.

### Blind Evaluator

`evaluator.v8` сначала независимо восстанавливает `impact_analysis` по actual candidate,
без обоих impact ledgers и Contract/check framing. `blind-audit.v2` содержит собственные
IDs, changed contracts, affected/NOT_AFFECTED/related consumers, exact changed-path review,
search evidence и summary. Controller использует shared safe-path/prose policy и
write-once persistence до Phase B.

Phase B получает normalized Planner impact и текущий `implementation-impact-closure.v2`
после повторной проверки binding, а также Contract, `contract-closure.v1` и deterministic/
runtime evidence. `impact_challenge` обязан покрыть exact sets blind contracts/consumers,
Planner IN_SCOPE, Implementer DISCOVERED, NOT_AFFECTED и related rows всех источников,
changed paths. Controller-owned `phase-b-origin-catalog.v1` назначает exact handles и
canonical metadata. Model wire выбирает только compatible `origin_ref`; exact row
cardinality и `maxItems=0` для пустых groups не оставляют impossible local correction.
Source, classification и revision не дублируются моделью. Names могут различаться без fuzzy matching.

PASS требует positive dispositions и достаточных proofs. Negative disposition требует
существующий final finding. Blind changed-contract MATERIAL_GAP и MODEL_CONFLICT допустимы
только с REPLAN_REQUIRED и concrete reason; остальные statuses отклоняются. Green test не
является authority: assertion должен проверять observable semantics affected consumer,
а не только локальный helper. Controller доказывает структуру, references и current-candidate
binding, но не истинность semantic conclusions. После repair нужны новые blind audit и
challenge в fresh thread. FAST policy и Final Gate остаются прежними.

Локальная wire correction и semantic correction — разные routes. Только точные
allowlisted leaf fields могут получить bounded correction с frozen claims и no-progress
guard. `SEMANTIC_MODEL_CONFLICT` сохраняет repair/replan semantics, а tampered catalog,
stale Controller state и другие `INTEGRITY_OR_INFRA_FAILURE` hard-stop.

### Final Gate

`phase7-final-gate.v1` доказывает:

```text
Step 3–6 относятся к одному candidate
использованы текущие Contract/Verification/Runtime revisions
candidate не изменился после Evaluation
candidate.patch воспроизводит тот же candidate с baseline
final acceptance создан после patch proof
result delivery не смешала accepted patch с user changes
```

## Evidence identity

Каждый authoritative artifact связан с:

```text
candidate_id
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
runtime_environment_rev
attempt_id
```

Изменение любой load-bearing оси делает соответствующее downstream evidence stale.

## Candidate identity

`candidate.v1` сравнивает физическое workspace tree с private baseline и
учитывает:

```text
baseline SHA
workspace HEAD
changed/new/deleted paths
physical executable/Git-compatible mode
file bytes / symlink target
```

Inventory не использует Git ignore/index как authority: ignored additions и
tracked mutations под `assume-unchanged`/`skip-worktree` остаются видимыми всем
consumers changed-set. Отдельный Git control-state baseline обнаруживает и
инвалидирует изменение самих ignore/config/index/HEAD controls.

Исключаются только конкретные Controller-authorized roots:

```text
.git/
.harness_tmp/
.harness_git_excludes
configured project venv
registered runtime projections
session exposed paths
```

Cache-name wildcards не применяются. Exclusion, перекрывающий tracked baseline
path, является controlled setup failure.

## Contract closure

`contract-closure.v1` нормализует Controller-accepted status каждого item:

```text
VERIFIED
NOT_AFFECTED — только consumer с evidence
```

Implementer prose не передаётся Evaluator как authority.

## Repair vs semantic replan

```text
technical model корректна,
candidate ошибочен
→ same Implementer repair
```

```text
technical model отвергнута
→ rejected patch сохраняется
→ candidate очищается до baseline
→ task checks/runtime attempt сбрасываются
→ fresh Planner
→ new Contract/Verification Plan
→ fresh Implementer
```

Это препятствует anchoring на признанно неверной реализации.

## Final Gate invariants

### Quality reconciliation

Step 3–6 должны быть PASSED/SKIPPED допустимым способом и иметь один `candidate_id` и текущие revisions.

### Patch proof

```text
baseline + candidate.patch
→ reconstructed candidate.v1
→ exact equality with accepted candidate.v1
```

Patch staging использует только disposable Controller-private `GIT_INDEX_FILE`;
persistent worktree index не меняется, hooks отключены, ignored additions
добавляются принудительно.

Reconstruction использует тот же effective Git worktree conversion policy, что и source candidate, для ограниченного списка content/mode settings. Поэтому exact equality остаётся строгой и одновременно переносимой между Windows checkout с CRLF и POSIX checkout с LF.

Artifact: `patch-proof.v1`.

### Reconstructed verification

Proof repository получает pristine source-owned projections/exposed files и
отдельно rebuilt project runtime. После static preflight Controller повторяет
все active repair checks и benchmark held-out. Candidate, Git controls и runtime
projection должны остаться pristine; иначе `reconstructed-verification.v1`
блокирует Final Acceptance. Это выявляет зависимости от original-only
`.harness_tmp`, изменённой runtime `.env` или loose unreferenced Git objects.

### Immutable acceptance

`final-acceptance.v3` создаётся один раз после patch proof и reconstructed verification PASS. Он содержит artifact bindings и patch SHA-256, но не дублирует reasoning/logs.

Обязательный `user_follow_up` связывает current `user-follow-up.v2` через fingerprint
и SHA-256 в artifact bindings. Даже count=0 требует valid private/public report.
Missing, tampered, stale или потерявший current related finding handoff запрещает acceptance.

### Delivery

`delivery-record.v2` отделяет качество candidate от доставки.

```text
RESULT_DELIVERY_PASS
RESULT_DELIVERY_BLOCKED
RESULT_DELIVERY_FAIL
```

Dirty/changed source приводит к BLOCKED, а не к перезаписи пользовательских файлов. Частичный apply допускает только safe rollback по preimage/postimage invariants.

## Historical benchmark quality

`benchmark-isolation.v1` требует standalone repository без shared refs/object database. Historical trial всегда использует `keep_worktree`.

Hidden grader запускается только после normal pipeline PASS и создаёт `heldout-evidence.v2`:

```text
HELDOUT_PASS
HELDOUT_SEMANTIC_FAIL
HELDOUT_INFRA_ERROR
HELDOUT_TIMEOUT
HELDOUT_MUTATED_CANDIDATE
```

Semantic failure требует oracle marker и завершается terminal result
`HARNESS_BENCHMARK_SEMANTIC_FAIL`. Held-out feedback не возвращается
Planner/Implementer/Evaluator текущего trial; infrastructure failure сохраняет
отдельную invalid/blocked классификацию.

## Anti-monster rules

1. Отдельный field/item существует только при downstream consequence.
2. Controller компилирует Contract/Verification Plan детерминированно; новый LLM Contract Reviewer отсутствует.
3. Runtime запускается только по typed proof requirement.
4. Один Evaluator thread выполняет две фазы, а не два reviewer-а.
5. Planner и Implementer prose скрыты от Evaluator.
6. Contract size 14 — soft review threshold; material obligation не отбрасывается.
7. Duplicate discoveries idempotent.
8. Нет universal E2E для каждого task.
9. Infrastructure failure не становится product evidence.
10. Advisory isolation не называется enforced.
11. Final Gate не делает нового semantic review.
12. Held-out — exam, а не repair tool.

## Что Phase 7 доказывает

```text
active Task/Plan/Contract/Verification revisions согласованы;
Contract items имеют Controller-normalized closure;
self-verify/Controller/runtime/evaluator evidence связано с candidate;
semantic replan не показывает fresh agents rejected diff;
required runtime proof выполнен или явно не требуется;
Evaluator blind phase предшествует Contract/check framing;
Final Gate принимает только один неизменённый candidate;
patch реконструирует именно этот candidate;
source delivery не смешивает accepted result с parallel user changes;
historical benchmark не раскрывает другие refs/objects/held-out feedback.
```

## Что Phase 7 не доказывает

```text
универсальную OS sandbox-изоляцию любого subprocess;
безопасность owner wrapper с чрезмерными credentials;
наличие готового browser/1С/DB/Airflow wrapper без project config;
отсутствие любого неизвестного defect во всём repository;
универсальную надёжность Harness по одному historical trial;
успешный CI/deployment/production rollout;
```

Практическая надёжность измеряется по нескольким clean trials и реальным escaped defects.
Planner Impact Closure проверяется synthetic Harness tests. Historical benchmark
служит независимым checkpoint после согласованного набора autonomy изменений;
его hidden scenarios не используются для проектирования этого механизма.

## Версии

```text
manifest version = 2
task-contract.v1
planner.v6
implementer.v6
implementation-contract.v4
verification-plan.v1
project-runtime.v1
contract-expansion.v1
runtime-scenario.v1
runtime-request.v1
runtime-result.v1
runtime-evidence.v1
contract-closure.v1
blind-audit.v2
evaluator.v8
workflow.v7
run-state.v1
candidate.v1
controller-plane.v1
execution-broker.v1
phase5-contract-runtime.v1
phase6-runtime-evaluator.v1
phase7-final-gate.v1
patch-proof.v1
final-acceptance.v3
user-follow-up.v2
delivery-record.v2
heldout-evidence.v2
benchmark-isolation.v1
```

Discovered checks используют общий framework-aware compiler и replayable Controller
command templates. Native node:test и Jest не выбираются по одному extension.
Compiled registry drift обновляет verification evidence, owner commands сохраняются.
Локальное evidence report исправляется bounded correction в том же thread без
изменения candidate/claims; invalid attempts сохраняются, COMPLETE по-прежнему
требует полный validator и trusted evidence. Детали и native smoke:
[Phase 4 — runner resolution / report correction](PHASE4_EXECUTION.md).
