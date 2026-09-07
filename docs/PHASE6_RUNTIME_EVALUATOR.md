# Phase 6 — Runtime Verification и двухфазный Blind Evaluator

> Historical Phase 6 document. Phase 7 (`0.8.0a12`) completed clean semantic replan, one-candidate Final Gate, transactional delivery and benchmark isolation. See [Phase 7 Final Gate](PHASE7_FINAL_GATE.md).

Phase 6 закрывает два оставшихся quality-gap после локальных Controller checks:

```text
локальные tests зелёные
        ↓
но требование зависит от реального application/external outcome
```

и:

```text
Planner + Implementer + tests считают candidate готовым
        ↓
нужен fresh reviewer, который сначала не знает их технической модели
```

Новых model-ролей не добавляется. Runtime исполняет Controller, а один fresh Evaluator
работает в две строго разделённые фазы.

## Общая схема

```text
DETERMINISTIC_VERIFICATION_PASS
        ↓
Verification Plan требует runtime?
 ├─ нет → RUNTIME_VERIFICATION_SKIPPED
 └─ да
      ↓
   Controller-owned runtime scenarios
      ↓
   RUNTIME_VERIFICATION_PASS
        ↓
FRESH EVALUATOR THREAD
        ↓
PHASE A — BLIND DISCOVERY
        ↓
blind-audit.v2 фиксируется Controller
        ↓
PHASE B — INDEPENDENT IMPACT CHALLENGE
        ↓
EVALUATION_PASS / FINDINGS / REPLAN_REQUIRED / BLOCKED / NEEDS_USER_DECISION
```

## Runtime scenario configuration

Runtime не включается по общей метке риска. `verification-plan.v1` определяет профиль и
capabilities для конкретного Contract item. Project owner настраивает Controller-owned
scenario в `harness.local.toml`.

```toml
[projects.sa_icover.runtime_verification]
enabled = true

[[projects.sa_icover.runtime_verification.scenarios]]
id = "local-browser-flow"
profile = "LIVE_LOCAL"
capabilities = ["LOCAL_APP", "BROWSER_DOM", "BROWSER_NETWORK"]
startup_command = ["{python}", "manage.py", "runserver", "127.0.0.1:{runtime_port}", "--noreload"]
health_command = ["{python}", "tools/runtime_health.py", "{runtime_port}"]
command = ["{python}", "tools/runtime_browser_scenario.py"]
timeout_seconds = 300
startup_timeout_seconds = 60
cleanup_timeout_seconds = 60
```

Поддерживаются только три proof profile:

```text
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

Scenario capability gate проходит только если **один конкретный scenario** покрывает
профиль и весь required capability set одного obligation. Нельзя сложить возможности двух
несвязанных сценариев и выдать их за одно доказательство.

Controller проверяет executable/placeholders и обязательные `preserve_env` inputs только для
scenarios, выбранных активным Verification Plan. Неиспользуемый optional scenario с
недоступным credential или executable не блокирует несвязанную задачу.

## Structured request/result

Controller передаёт scenario только structured request:

```text
runtime-request.v1
scenario_id
profile
candidate_id
verification_plan_fingerprint
requirements[]
runtime_port
```

Scenario обязан записать `runtime-result.v1`. Для каждого requirement указываются
`PASS/FAIL` и concrete evidence. Wrapper, который успешно сформировал structured semantic
result, завершает процесс с exit code `0`; non-zero, timeout или отсутствующий/невалидный
result классифицируются как infrastructure failure, а не как доказанный product verdict.

## LIVE_LOCAL

`LIVE_LOCAL` запускает current candidate из managed worktree. Если указан
`startup_command`, Controller:

```text
выделяет task-local port
→ запускает service без autoreload
→ polling health command до bounded deadline
→ запускает observable scenario
→ останавливает service
```

Startup stdout/stderr пишутся в runtime scratch-файлы, а не в ограниченный pipe, поэтому
долго работающий server не блокируется заполненным буфером. Scenario обязан подтвердить
known initial state. Browser не является встроенным автоматически: он появляется только
как явно настроенная typed capability/owner wrapper.

## TEST_EXTERNAL

`TEST_EXTERNAL` предназначен только для configured test boundary. PASS требует:

```text
known initial state
write/action
fresh authoritative readback
requirement assertions
cleanup или disposable environment
```

Non-disposable scenario обязан иметь Controller-owned `cleanup_command`. Cleanup запускается
даже после timeout/failure основного action, потому что внешняя система могла частично
применить mutation. Успешный cleanup command считается authoritative только если сам wrapper
выполняет cleanup и его readback/verification; обычный HTTP 200 без fresh state недостаточен.

## PROD_OBSERVE

`PROD_OBSERVE` не допускает startup/cleanup mutation lifecycle и требует:

```text
read_only_enforced = true
```

Это owner assertion о технической границе capability: отдельная read-only DB role,
GET-only wrapper, scoped token или эквивалент. Phase 6 не превращает произвольную shell
команду с production superuser credential в безопасный read-only capability. Execution
Broker продолжает отличать `ENFORCED` от `ADVISORY`.

## Sensitive output

Runtime scenario может получить только явно разрешённые `preserve_env` variables. Controller
проверяет наличие этих inputs до Implementer и редактирует их точные значения в structured
result, stdout/stderr и cleanup/startup logs перед сохранением даже в private runtime artifact.
Scenario wrapper всё равно не должен намеренно печатать credentials; redaction является
defense-in-depth, а не способом безопасно логировать секреты.

## Immutability

Runtime evidence принимается только если после scenario неизменны:

```text
candidate_id
workspace HEAD
source HEAD и source working-tree status
.worktreeinclude/runtime-only files (.env и т.п.)
```

Runtime scratch, logs и result JSON находятся в `.harness_tmp/runtime` и не входят в
candidate. Если runtime изменил code, source checkout или runtime-only local config,
результат становится `RUNTIME_MUTATED_CANDIDATE`; local config восстанавливается, а
candidate возвращается в Implementer loop.

## Contract Closure Record

До Controller checks формируется `contract-closure.v1`:

```text
candidate_id
Implementation Contract fingerprint
Verification Plan fingerprint
каждый item → VERIFIED / допустимый NOT_AFFECTED
Controller-accepted evidence
```

Evaluator не получает свободное оправдание Implementer. Phase B видит именно этот
Controller-normalized record.

## Двухфазный Evaluator v6

### Phase A — blind discovery

Fresh read-only evaluator получает:

```text
RAW USER REQUEST
USER TASK CONTRACT
sanitized preflight
owner allowed_paths
current candidate ID
repository/current candidate
changed paths как навигацию
```

Он **не получает**:

```text
Planner reasoning
Planner impact_closure
Implementation Contract
Implementer Report
implementation-impact-closure.v1
Controller checks
runtime evidence
previous findings
held-out/reference solution
```

Phase A возвращает `blind-audit.v2` с обязательными `candidate_id`, `impact_analysis`,
summary, findings и advisories. `impact_analysis` содержит:

```text
applicable
changed_contracts          impact_id/name/before/after/paths/symbols/evidence
affected_consumers         impact_id/name/paths/symbols/relation/required_behavior/evidence
not_affected_consumers     impact_id/name/paths/symbols/why_considered/reason/evidence
related_out_of_scope       impact_id/name/paths/symbols/relation/reason/evidence/suggested_follow_up
changed_path_review        path/observed_role/impact/evidence
search_evidence            target/method/evidence_paths/conclusion
closure_summary
```

IDs имеют форму CONTRACT-N, CONSUMER-N, NOT-AFFECTED-N, RELATED-N с positive integer N.
Они уникальны в category; consumer classification names уникальны и disjoint. Независимые
names не обязаны повторять Planner vocabulary. Before/after различаются. Paths/symbols
и evidence concrete; regular evidence files разрешаются canonical внутри workspace.
Каждый actual changed path independently reviewed exactly once. Для deletion отсутствие
final file допустимо только в changed-path review; search evidence не может ссылаться
на удалённый file. Changed paths — seed для outward sweep, не review boundary.

Engineering impact требует changed contracts и independent search evidence. Исключение
`applicable=false` использует `impact.validate_owner_prose_boundary`: non-empty owner
boundary только из safe existing prose files .md/.rst/.txt/.adoc; search и changed paths
входят в boundary. Behavioral/runtime obligations исключают этот route. Summary должен
содержать конкретное объяснение и evidence path.

Controller проверяет current candidate и schema, затем записывает audit через
`write_once_authoritative_json` в private/public artifact **до** раскрытия Contract/evidence.
Persistence callback обязателен; его ошибка предотвращает Phase B. Phase guards и внешний
integrity coordinator не позволяют Evaluator менять candidate.

### Phase B — independent impact challenge

Тот же fresh thread затем получает только Controller-normalized artifacts:

```text
Implementation Contract
Verification Plan
Contract Closure Record
Planner normalized impact_closure
Controller-normalized implementation-impact-closure.v1
deterministic Controller evidence
runtime PASS/SKIPPED evidence
```

Перед раскрытием implementation impact повторно валидируется against current candidate,
Plan/Contract fingerprints, exact changed paths и revision binding. Stale artifact не
попадает в Phase B. Full Planner reasoning и raw Implementer report не раскрываются.

`evaluator.v6` содержит mandatory `candidate_id` и `impact_challenge`:

| Dispositions | Authoritative exact set | Positive result for PASS |
| --- | --- | --- |
| blind_contract_dispositions | Каждый blind changed contract impact_id | COVERED |
| blind_consumer_dispositions | Каждый blind affected consumer impact_id | COVERED_IN_SCOPE |
| planner_consumer_dispositions | Каждый Planner IN_SCOPE normalized name | CONFIRMED |
| implementer_consumer_dispositions | Каждый Implementer DISCOVERED normalized name | CONFIRMED |
| not_affected_dispositions | BLIND IDs + PLANNER/IMPLEMENTER names, source отдельно | CONFIRMED_NOT_AFFECTED |
| related_follow_up_dispositions | BLIND IDs + PLANNER/IMPLEMENTER names, source отдельно | CONFIRMED_OUT_OF_SCOPE |
| changed_path_dispositions | Каждый actual changed path | UNDERSTOOD |

Все rows требуют reason, existing evidence_paths, evidence и finding_ids. Blind dispositions
дополнительно содержат `matches` с source/classification/name существующих prior rows;
COVERED_IN_SCOPE требует actual IN_SCOPE match. COVERED contract допускает независимое
repository evidence без совпадения имени. Exact sets запрещают missing/extra/duplicate rows.
`coverage_summary` обязателен и не заменяет structured challenge.

MATERIAL_GAP/MODEL_CONFLICT, MISSING/misclassified consumer, UNSUPPORTED/implementation
gap, ACTUALLY_AFFECTED/INSUFFICIENT_EVIDENCE, ACTUALLY_IN_SCOPE и SUSPICIOUS/UNJUSTIFIED
paths требуют соответствующих final finding_ids. Negative disposition запрещает PASS.
Каждый finding содержит failure_mode, required_action и typed required_proof. Assertions
проверяются через affected consumer semantics: зелёный helper test не закрывает неправильный
reachable sibling. Согласованность двух прежних ledgers не является independent evidence.

Planner reasoning и Implementer prose остаются скрыты. Каждый Phase A finding обязан стать:

```text
RETAINED
или
DISMISSED_WITH_EVIDENCE
```

Нельзя забыть blind finding только потому, что его нет в Contract. Blocking severity —
только `HIGH` или `MEDIUM`; style/naming preference не является finding.

## Finding routing

Категории:

```text
DEFECT
CONSUMER
RISK
EVIDENCE
DOCS
MODEL
```

`CONSUMER` и `RISK` транзакционно расширяют active Implementation Contract и Verification
Plan до repair. Обычный candidate defect возвращается тому же Implementer. Ошибка самой
technical model возвращает `REPLAN_REQUIRED`.
MODEL_CONFLICT запрещён в обычном FINDINGS repair и требует semantic replan либо честный
BLOCKED/NEEDS_USER_DECISION status с reason. Оба Evaluator reports связаны с current candidate.
После repair прежние audit/challenge stale: новый Implementer impact, checks/runtime и
fresh Evaluator Phase A/Phase B обязательны. FAST по-прежнему пропускает Evaluator.
Все related follow-ups сохраняются в immutable audit/prior artifacts и exact dispositions;
mandatory user-facing delivery ещё не реализована.

## Runtime statuses

```text
RUNTIME_VERIFICATION_PASS
RUNTIME_VERIFICATION_SKIPPED
RUNTIME_BEHAVIOR_FAIL
RUNTIME_START_FAIL
RUNTIME_TIMEOUT
RUNTIME_INFRA_ERROR
RUNTIME_INVALID_RESULT
RUNTIME_READBACK_FAIL
RUNTIME_CLEANUP_FAIL
RUNTIME_MUTATED_CANDIDATE
```

Infrastructure failure никогда не считается semantic evidence. Отсутствующий, malformed или
слишком большой structured result получает отдельный `RUNTIME_INVALID_RESULT`; Controller не
пытается принять частично прочитанный/неограниченный artifact. Runtime repair меняет candidate,
поэтому после него заново выполняются self-verify, весь Step 4 и весь Step 5.

## Historical Phase 6 boundary

`0.8.0a9` implemented the generic runtime executor and two-phase evaluator but did not ship universal browser/PostgreSQL/1C/Airflow wrappers. Phase 7 later completed semantic replan and final delivery. Universal OS-enforced subprocess isolation and ready-made project wrappers remain explicit platform/project capabilities; owner wrappers must not emit secrets in scenario results/logs.
