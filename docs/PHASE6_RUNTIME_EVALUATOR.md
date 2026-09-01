# Phase 6 — Runtime Verification и двухфазный Blind Evaluator

> Historical Phase 6 document. Phase 7 (`0.8.0a10`) completed clean semantic replan, one-candidate Final Gate, transactional delivery and benchmark isolation. See [Phase 7 Final Gate](PHASE7_FINAL_GATE.md).

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
blind-audit.v1 фиксируется Controller
        ↓
PHASE B — CONTRACT AUDIT
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

## Двухфазный Evaluator v5

### Phase A — blind discovery

Fresh read-only evaluator получает:

```text
RAW USER REQUEST
USER TASK CONTRACT
sanitized preflight
repository/current candidate
changed paths как навигацию
```

Он **не получает**:

```text
Planner reasoning
Implementation Contract
Implementer Report
Controller checks
runtime evidence
previous findings
held-out/reference solution
```

Phase A возвращает `blind-audit.v1`. Controller проверяет candidate и записывает audit в
private/public artifact **до** раскрытия Contract/evidence.

### Phase B — contract audit

Тот же fresh thread затем получает только Controller-normalized artifacts:

```text
Implementation Contract
Verification Plan
Contract Closure Record
deterministic Controller evidence
runtime PASS/SKIPPED evidence
```

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
```

`CONSUMER` и `RISK` транзакционно расширяют active Implementation Contract и Verification
Plan до repair. Обычный candidate defect возвращается тому же Implementer. Ошибка самой
technical model возвращает `REPLAN_REQUIRED`.

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
