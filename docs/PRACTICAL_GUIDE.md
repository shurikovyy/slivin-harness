# Практическая работа с Slivin Harness 0.8.0a12

## Установка и self-check

```bash
cd ~/Tools/slivin-harness-080a11-phase7
./py -c "import slivin_harness; print(slivin_harness.__version__)"
./py tools/self_check.py
```

Ожидаемый финал:

```text
0.8.0a12
DOCS_SYNC_PASS harness=0.8.0a12 ...
HARNESS_SELF_CHECK_PASS
```

## Intake artifact repair

При редком несогласованном ответе normalizer консоль показывает, например:

```text
INTAKE_ARTIFACT_REPAIR: attempt=1/2 code=TASK_CONTRACT_READY_WITH_AMBIGUITY field=ambiguities/reason
```

Это не новый Planner и не изменение пользовательского запроса. Тот же read-only Intake-thread
получает machine validation feedback и обязан заново вернуть полный `task-contract.v1`. Если две
repair-попытки не помогли, run останавливается как protocol failure, а не продолжает работу с
сомнительным Task Contract.

## FULL workflow

```text
0. Intake / Preflight
   → managed worktree, .worktreeinclude, optional worktree-local .venv
   → User Task Contract

1. fresh Planner v4
2. Implementation Contract v3 + Verification Plan v1
3. Implementer v3 + Contract/check expansion + SELF VERIFY
4. independent Controller deterministic checks
5. Runtime Verification when the active proof requires it
6A. fresh blind evaluator discovery
6B. Contract/evidence audit in the same fresh evaluator thread
7. Final Gate / patch proof / safe result handoff
```

Runtime is not a ritual step. A local-only Verification Plan records:

```text
RUNTIME_VERIFICATION_SKIPPED
reason = NO_RUNTIME_PROOF_REQUIRED
```

A runtime proof is executed only when a Contract item requires `LIVE_LOCAL`,
`TEST_EXTERNAL` or `PROD_OBSERVE`.

## Project Python runtime

Configure one project-level profile:

```toml
[projects.sa_icover.runtime]
bootstrap_python = "C:/Users/Slivin.Aleksandr/AppData/Local/Programs/Python/Python312/python.exe"
expected_python = "3.12"
venv = ".venv"
dependency_files = ["requirements.txt"]
pip_install_args = ["--disable-pip-version-check"]
```

Harness creates the authoritative project Python inside each worktree:

```text
<worktree>/.venv/Scripts/python.exe
```

Controller does not depend on shell activation and does not silently fall back to the
Harness Python. Dependency-manifest changes or hidden package drift trigger a clean rebuild
before a new self-verification receipt can be accepted.

## `.worktreeinclude`

Repository-owned ignored runtime files can be listed in `.worktreeinclude`:

```gitignore
.env
.env.local
```

Only matching ignored regular files are copied. Symlink/junction/reparse traversal is
rejected. These files do not enter the candidate or patch. If a role changes one of them,
Controller restores the original value and invalidates the related evidence.

## Runtime Verification configuration

The project owner configures typed scenarios in `harness.local.toml`. Scenario commands are
Controller-owned wrappers, not free-form commands supplied by an agent.

### LIVE_LOCAL example

```toml
[projects.sa_icover.runtime_verification]
enabled = true

[[projects.sa_icover.runtime_verification.scenarios]]
id = "local-ui-flow"
profile = "LIVE_LOCAL"
capabilities = ["LOCAL_APP", "BROWSER_DOM", "BROWSER_NETWORK"]
startup_command = ["{python}", "manage.py", "runserver", "127.0.0.1:{runtime_port}", "--noreload"]
health_command = ["{python}", "tools/runtime_health.py", "{runtime_port}"]
command = ["{python}", "tools/runtime_browser_scenario.py"]
timeout_seconds = 300
startup_timeout_seconds = 60
cleanup_timeout_seconds = 60
```

### TEST_EXTERNAL example

```toml
[[projects.sa_icover.runtime_verification.scenarios]]
id = "test-onec-write-readback"
profile = "TEST_EXTERNAL"
capabilities = ["TEST_EXTERNAL_WRITE", "TEST_EXTERNAL_FRESH_READ"]
command = ["{python}", "tools/runtime_test_onec.py"]
cleanup_command = ["{python}", "tools/runtime_test_onec_cleanup.py"]
timeout_seconds = 300
cleanup_timeout_seconds = 120
preserve_env = ["ONEC_TEST_TOKEN"]
```

A non-disposable `TEST_EXTERNAL` scenario must define cleanup. The wrapper must perform:

```text
known initial state
→ write/action
→ fresh authoritative readback
→ assertions
→ cleanup + cleanup confirmation
```

### PROD_OBSERVE example

```toml
[[projects.sa_icover.runtime_verification.scenarios]]
id = "prod-read-only-facts"
profile = "PROD_OBSERVE"
capabilities = ["PROD_READ_ONLY"]
command = ["{python}", "tools/runtime_prod_observe.py"]
read_only_enforced = true
preserve_env = ["PROD_READ_ONLY_TOKEN"]
```

`read_only_enforced = true` is an owner assertion about a technical boundary such as a
read-only DB role or GET-only wrapper. It does not make an arbitrary production credential
safe. Production write verification is not part of the base Harness.

## Runtime wrapper protocol

Controller passes `runtime-request.v1`. The wrapper writes `runtime-result.v1` into the
Controller-specified result path. It reports each assigned requirement as `PASS` or `FAIL`
with concrete evidence. A valid semantic result must still exit with code `0`; non-zero,
timeout or a missing/invalid result are classified as infrastructure failure, not product
evidence.

## Two-phase Evaluator

The fresh evaluator works in two turns.

### Phase A — blind discovery

It sees:

```text
raw request
User Task Contract
sanitized preflight
current repository/candidate
changed paths as navigation
```

It does not see Planner reasoning, Implementation Contract, Implementer report, deterministic
results, runtime evidence, previous findings or held-out assertions. Controller persists the
returned `blind-audit.v1` before any additional evidence is revealed.

### Phase B — Contract audit

The same thread then receives only Controller-normalized artifacts:

```text
Implementation Contract
Verification Plan
Contract Closure Record
deterministic evidence
runtime PASS/SKIPPED evidence
```

Every blind finding must be `RETAINED` or `DISMISSED_WITH_EVIDENCE`. Findings use only
`HIGH`/`MEDIUM` severity and the compact categories `DEFECT`, `CONSUMER`, `RISK`, `EVIDENCE`
and `DOCS`.

## Authoritative artifacts

Controller-owned evidence is stored in:

```text
RUN_DIR/controller_private/
```

Typical Phase 7 artifacts:

```text
task_contract_*.json
plan_*.json
implementation_contract_*.json
verification_plan_*.json
contract_closure_*.json
runtime_scenarios.json
runtime_verification_*.json
blind_audit_*.json
evaluation_*.json
check_registry.json
self_verify_receipt_current.json
quality_gate_reconciliation.json
patch_proof.json
final_acceptance.json
delivery_record.json
heldout_evidence.json        # benchmark only
benchmark_isolation.json     # benchmark only
```

Public artifacts are sanitized mirrors. Agent scratch under `.harness_tmp` is never
authoritative.

## Repair and replan

```text
ordinary candidate defect
→ same Implementer
→ self verify
→ full deterministic checks
→ full runtime step
→ fresh two-phase Evaluator
```

```text
new CONSUMER/RISK finding
→ Controller expands Contract and Verification Plan
→ repeats boundary/capability gates
→ same Implementer closes the new revision
```

```text
wrong technical model
→ REPLAN_REQUIRED
→ rejected patch saved outside workspace
→ candidate reset to recorded baseline
→ task-specific checks/runtime attempt reset
→ fresh Planner
→ new Contract/Verification Plan
→ fresh Implementer thread
```

The replacement agents do not see the rejected diff. Ordinary repair still reuses the same Implementer thread because the technical model remains valid.

## Workflow documentation

```bash
./py tools/render_workflow_docs.py
./py tools/check_docs_sync.py
```

Do not edit `WORKFLOW.md` or `workflow.v6.json` manually.

## Final Gate и result handoff

После `EVALUATION_PASS` Controller:

```text
reconciles one candidate/revision vector
→ builds candidate.patch
→ reconstructs candidate from clean baseline
→ creates immutable final-acceptance.v2
→ delivers via keep_worktree or transactional apply_to_source
```

`apply_to_source` не переключает ветку, не делает commit/push и не перезаписывает dirty source. При конфликте accepted patch и worktree сохраняются, а status становится `RESULT_DELIVERY_BLOCKED`.

Historical benchmark всегда использует standalone sanitized repository и `keep_worktree`; hidden held-out failure не возвращается агентам. Подробнее: [Phase 7 Final Gate](PHASE7_FINAL_GATE.md).

## Honest Phase 7 boundaries

```text
no universal browser/PostgreSQL/1C/Airflow wrappers are shipped;
no universal OS-enforced Controller subprocess sandbox on every platform;
no automatic commit/push/PR/merge or deployment;
no production write verification;
owner wrappers must avoid emitting secrets in logs/results;
one passing historical trial is not proof of universal reliability.
```

После Windows self-check следующая команда проверки качества — полный historical `_90` trial, а не новая фаза разработки.
