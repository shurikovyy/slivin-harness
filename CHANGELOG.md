# Changelog

## 0.8.0a6 — Phase 4: Implementer and deterministic Controller verification

- Upgraded the writable protocol to `implementer.v2` with `COMPLETE`, `REPLAN_REQUIRED`, `BLOCKED`, and `NEEDS_USER_DECISION`; non-COMPLETE outcomes require concrete reason/evidence without a fake per-item blocked ledger.
- Added a Controller-private typed check registry. Agents may register validated repository test paths or trusted check IDs, never arbitrary authoritative shell commands.
- Bound self-verification receipts to candidate identity, Task/Plan/Contract/Verification revisions, runtime environment, attempt, and check-registry digest.
- Replaced short active-turn wall-clock interruption with activity-aware watchdog behavior; an active tool suppresses inactivity interruption.
- Added deterministic Controller result classes: `CHECK_PASS`, `CHECK_FAIL`, `CHECK_TIMEOUT`, `CHECK_INFRA_ERROR`, and `CHECK_MUTATED_CANDIDATE`.
- Added candidate freeze guards around Controller checks, changed-test coverage validation, and progress/no-progress repair guards instead of a normal two-cycle cutoff.
- Added `docs/PHASE4_EXECUTION.md`, upgraded the canonical workflow to `workflow.v3`, and added executable Phase 4 unit/integration regressions.
- Kept the alpha boundary explicit: automatic Contract/Verification Plan recompilation, worktree-local environment rebuild, and universal OS-enforced Controller subprocess isolation remain follow-up work.

## 0.8.0a5 — Phase 3: task, planning and verification contracts

- Added `task-contract.v1`: raw user request is preserved verbatim and every explicit normalized claim is bound to exact `source_text`.
- Upgraded Planner to `planner.v4`: characterization, bug root cause / feature extension point, material assumptions, technical acceptance, consumers, conditional State Model, risks and typed evidence plan.
- Upgraded the Controller-owned Definition of Done to `implementation-contract.v3`; explicit user acceptance/preservation can no longer disappear through Planner paraphrase.
- Added `verification-plan.v1` with typed `LOCAL_DETERMINISTIC`, `LIVE_LOCAL`, `TEST_EXTERNAL` and `PROD_OBSERVE` profiles. Distinct runtime profiles are preserved rather than collapsed into one scalar level.
- Added owner-boundary and required-capability gates before the writable Implementer turn. Runtime proof whose executor is not implemented remains fail-closed.
- Bumped canonical workflow to `workflow.v2`; generated docs and docs-sync now bind all Phase 3 protocol versions.
- Added Phase 3 contract, tamper and pipeline integration regressions.


## 0.8.0a4 — Phase 2: native Windows canonical-path fix

- Fixed four native Windows self-check failures caused by comparing resolved Controller/Broker paths with unresolved `tempfile` paths through lexical `Path.is_relative_to()` semantics.
- Added one canonical filesystem-containment primitive for Controller artifact roots and role scratch roots; cross-drive/path-alias comparisons remain fail-closed.
- Fixed a real private-plane filtering gap: environment values are now checked against both lexical and canonical private-root aliases, including paths containing `..`.
- Added component-boundary matching so an unrelated sibling such as `controller_private_backup` is not rejected as the private plane.
- Added platform-independent regressions for canonical aliases, private-path environment rejection, sibling-prefix safety and scratch/private separation.
- Kept `controller-plane.v1` and `execution-broker.v1`: serialized policy/receipt schemas are unchanged; this is a portability and enforcement-correctness fix.

## 0.8.0a3 — Phase 2: private Controller plane and execution broker

- authoritative Run State, candidate/contract snapshots and self-verification receipts live under `RUN_DIR/controller_private`; public `run_state.json` is a diagnostic mirror;
- added `ControllerPlane` with atomic writes, artifact visibility classes and Windows/UNC/path-traversal guards;
- added HMAC-protected self-verify receipts bound to candidate, attempt and revision vector;
- added role-aware `ExecutionBroker` for App Server, Planner, Implementer, Controller checks, Runtime, Evaluator and held-out;
- centralized task-local temp/cache and sensitive environment filtering; private Controller paths are rejected from agent environments;
- execution policies report `ENFORCED`, `ADVISORY` or `UNAVAILABLE` instead of claiming a sandbox that is not yet implemented;
- App Server and Controller checks consume brokered environments; held-out checks use their own role policy;
- added unit/integration coverage for private-plane separation, tamper/staleness rejection, policy honesty, path containment and release workflow;
- Planner/Implementer/Evaluator protocols and Runtime executor remain unchanged for compatibility; the restricted native Windows check runner is explicitly deferred.


## 0.8.0a2 — Phase 1: native Windows file-mode portability

- Fixed the Phase 1 self-check on native Windows/NTFS: the executable-bit integration test now performs a real Git capability probe and skips only when Git does not expose `chmod` as a mode-only working-tree change.
- Kept the `candidate.v1` behavior unchanged: file mode remains part of candidate identity whenever Git reports that mode in the HEAD-to-working-tree diff.
- Clarified the platform boundary in README, architecture, quality-model and Windows setup documentation; the skip is explicit and does not convert a real Git-visible mode change into PASS.

## 0.8.0a1 — Phase 1: canonical workflow and versioned Run State

- Added `workflow.v1` as the single machine-readable Step 0–7 definition: stage order, success transitions, routing outcomes, status enums and invalidation rules are no longer duplicated only in prose and `task_runner.py`.
- Added atomic `run-state.v1` artifacts for every real run, including stage states, attempt lineage, revision vector, baseline binding, candidate identity, event history and terminal result.
- Added canonical `candidate.v1`: baseline SHA, workspace HEAD, paths, deletions, symlink targets, file modes and file bytes are bound into one `candidate_id`; `.harness_tmp` and `.venv` are excluded.
- Planner, Implementer and Evaluator JSON schemas now import their status enums from the canonical workflow instead of maintaining duplicate string lists. Model prompts/protocol shapes remain unchanged in Phase 1.
- Existing 0.7.1 execution is mapped onto Step 0–7. Runtime Step 5 is explicitly recorded as compatibility `RUNTIME_VERIFICATION_SKIPPED`; no runtime executor is claimed yet.
- Controller observes candidate identity before/after deterministic checks, Evaluator and held-out; candidate mutation or workspace HEAD movement aborts the run.
- Final Gate now records `final_acceptance.json`, patch SHA-256, terminal Run State and distinct `HARNESS_TASK_PASS` / `HARNESS_BENCHMARK_PASS`.
- Run State rejects undeclared stage skips, rejects skip codes recorded as ordinary PASS, and enforces mode-specific final results.
- Added generated `docs/WORKFLOW.md` and `docs/workflow.v1.json`; docs-sync rejects drift from `slivin_harness/workflow.py`.
- Added unit coverage for canonical transitions, invalidation, candidate identity (including deletion, binary content and executable mode), schema enum reuse and generated workflow output.

## 0.7.1 RC — load-bearing Planner risks and Implementer timeout continuation

- `Implementation Contract` upgraded to `implementation-contract.v2`: each Planner `risk` becomes an explicit `RISK-*` item that Implementer must verify before `COMPLETE`; risks are no longer informational prose that can be lost between Planner and execution;
- Planner policy adds generic reachable state boundaries for state/count/token/selection work: empty/zero/all-excluded, stale and current/resident coexistence should enter risks/test plan when material;
- App Server exposes a typed `TurnTimeoutError`; Implementer timeout no longer discards a nearly finished candidate;
- Controller automatically performs one short continuation turn in the **same Implementer thread** after timeout, preserving current worktree changes and asking only for unfinished contract/risk items plus self-verification;
- continuation is capped at one attempt and at most 300 seconds; a second timeout remains a hard FAIL, so this is recovery rather than unbounded latency;
- no new model role, repair cycle or manifest field was added;
- Matrix semantic held-out and calibration are byte-for-byte unchanged from 0.7.0, so the next trial measures execution improvement against the same exam.
## 0.7.0 RC — execution contract and in-turn self-verification

- Planner remains compact, but Controller now converts its expected behavior, preservation, materially affected consumers and test plan into a small `Implementation Contract`; no extra model role is added;
- Implementer protocol `implementer.v1` requires structured evidence for every contract item before `COMPLETE`; consumers may be `NOT_APPLICABLE` only with concrete evidence;
- Controller generates a Harness-owned `SELF_VERIFY_COMMAND` from the same trusted repair checks/toolchain used after the turn; Implementer must reach `SELF_VERIFY_PASS` before completion;
- self-verification stamp is bound to current candidate file contents, so edits after PASS invalidate it;
- Implementer may report additional repo-relative test paths for discovered sibling consumers; Harness builds supported Jest/Python commands from trusted toolchain/templates instead of executing arbitrary agent commands;
- supported dynamic checks join the ordinary repair loop and their results are visible to the blind Evaluator;
- Evaluator remains blind to Planner artifact, Implementation Contract and Implementer justification;
- run artifacts now include implementation contracts/reports and output `first_evaluation_pass` / repair metrics;
- Planner output arrays are capped and instructions explicitly stop after sufficient evidence to avoid returning to the old prose/obligation explosion;
- Matrix semantic held-out/calibration are unchanged from 0.6.6, so the next benchmark measures execution improvement rather than a moved grader.

## 0.6.6 RC — outcome-based Matrix oracle and state-boundary review

- Matrix semantic grader no longer requires one implementation strategy: it accepts both a generic all-matching contract with fail-closed Distribution and a safe Matrix-only opt-in that keeps Distribution stage actions unavailable;
- Matrix fixture reads literal boolean selection opt-ins from the actual `MatrixTableConfig`, so an in-scope config gate is exercised instead of being invisible to the oracle;
- Distribution held-out checks the observable safety outcome (stage-dependent action is hidden or disabled), not a specific internal `hasSelection` representation;
- candidate 0.6.5 is added as a negative calibration control; it still FAILs two independent semantic properties, while two architecturally different positive fixtures PASS;
- calibration fingerprint surface now includes `tableConfigs/matrix.js` because Matrix-scoped opt-in is a contract-bearing implementation choice;
- Evaluator policy adds one compact generic rule for reachable state coexistence and empty/zero/all-excluded targets, without restoring REP/AUTH/LIFE ledgers;
- when a historical benchmark exhausts `max_fix_cycles` after green checks, Harness runs held-out once as diagnostic-only evidence; the result is recorded but never returned to Implementer;
- main low/medium/high pipeline, repair limits and App Server transport behavior are unchanged.

## 0.6.5 RC — retryable App Server stream recovery

- `error` notification with `willRetry=true` is transient: Harness logs `APP_SERVER_TURN_RETRY` and keeps waiting for the same turn;
- transient retries stay inside the original turn timeout, so repeated disconnects cannot extend a task indefinitely;
- `willRetry=false` / terminal App Server errors remain fatal;
- regression tests cover both retryable recovery and terminal error handling;
- Matrix semantic grader, calibration and Planner/Evaluator policies are unchanged from 0.6.4.

## 0.6.4 RC — non-blocking Planner unknowns and live console

- `READY` Planner artifact may keep honest non-blocking `unknowns`; product-semantic uncertainty still requires `NEEDS_USER_DECISION`, and missing mandatory evidence still requires `BLOCKED`;
- removed the `READY + unknowns => protocol failure` guard that stopped the first 0.6.3 Matrix trial before implementation;
- Git Bash/CMD launchers now set `PYTHONUNBUFFERED=1`; Controller stdout/stderr are UTF-8 line-buffered with write-through so stages, heartbeat and Implementer output appear live;
- console regression test verifies line buffering/write-through; Planner protocol regression verifies non-blocking unknowns;
- semantic Matrix held-out/calibration remain unchanged from 0.6.3.

## 0.6.3 RC — semantic Matrix benchmark and shared-state review policy

- bundled Matrix held-out rewritten as a standalone semantic Node grader: no reference patch matching and no hidden Jest dependency;
- `_92` is no longer a positive gold fixture: calibration now requires `_90`, `_92`, `workspace_14` and the 0.6.2 candidate to FAIL, while two non-distributed semantic-good variants PASS;
- semantic grader covers the real filter-chips refresh path, stale/zero-target states, selection authority consistency, an async filter-action target race and Distribution token-only fail-closed stage semantics;
- calibration certificate schema v2 records multiple hash-bound positive/negative controls instead of only `broken=FAIL / good=PASS`;
- Planner/Evaluator policy gains two generic checks only: shared representations are traced through local eligibility/stage/permission guards, and stateful action targets are checked across lifecycle changes;
- Matrix benchmark documentation now explicitly separates historical held-out from ordinary production tasks.

## 0.6.2 RC — benchmark gate integrity and blocked-write fail-fast

- все checks получают `SLIVIN_HARNESS_WORKSPACE` и `SLIVIN_HARNESS_ROOT`; bundled Matrix held-out больше не падает из-за отсутствующей workspace env;
- baseline benchmark считается доказанно broken только если failing held-out дошёл до ожидаемого oracle marker; setup/infrastructure failure больше не считается defect evidence;
- confirmed-broken benchmark с пустым diff после IMPLEMENT останавливается до checks/evaluator, чтобы не тратить десятки минут на заведомо неисправимый candidate;
- Implementer прекращает повторять write attempts после двух разных `Permission denied`/`Access denied`;
- Windows docs фиксируют отдельную диагностику nested-directory write из-за известных ограничений native Codex workspace-write.

## 0.6.1 RC — App Server SandboxMode compatibility

- исправлен `thread/start.sandbox`: теперь отправляются wire values `read-only` / `workspace-write`;
- удалено ошибочное преобразование в `readOnly` / `workspaceWrite`, которые являются типами `SandboxPolicy`, а не `SandboxMode`;
- regression test проверяет оба допустимых thread sandbox mode и отклоняет camelCase policy type;
- документация синхронизирована с фактическим App Server contract.

## 0.6.0 RC — упрощение quality-core

### Удалено

- отдельная роль Impact Auditor;
- protocol `impact.v1`;
- десятки обязательных `CC/LIFE/REP/AUTH/PRES/INT/TEST` records;
- dynamic change-surface negotiation и pre-edit snapshot ledger;
- поля manifest `max_change_surface_cycles`, `max_impact_cycles`, `max_plan_validation_retries`;
- передача Planner artifact в независимый Evaluator;
- устаревшие и дублирующие документы.

### Новый pipeline

- `low`: `Implementer → checks`;
- `medium/high`: `Planner → Implementer → checks → Evaluator`;
- repair после failed checks или Evaluator findings;
- не более одного replan по умолчанию;
- held-out остаётся финальным экзаменом без feedback.

### Качество

- компактный `planner.v3`;
- blind-first `evaluator.v4`;
- `PASS` запрещён при findings или `unverified`;
- Evaluator получает фактический diff, включая новые untracked files;
- strict manifest version 2 и hard error на неизвестные поля;
- owner-defined `allowed_paths` вместо guessed hard boundary;
- documentation sync check;
- обновлённый regression corpus Harness.

### Workspace и публикация

- sensitive local path требует `allow_sensitive_copy = true`;
- symlink, junction и reparse point не копируются в agent workspace;
- failed workspace preparation удаляет созданный worktree;
- App Server stderr сохраняется в task-local runtime;
- timeout сначала отправляет `turn/interrupt`;
- публикация выполняет `git apply --check` и сравнивает итоговый source diff с accepted candidate.

### Исправление historical claims

Предыдущий Matrix trial был ложноположительным: узкий held-out прошёл, но внешний аудит нашёл рассогласование selection authority и Distribution fail-open. Он больше не описывается как доказанный successful benchmark.

## 0.5.x

Строгий многостадийный Harness с Planner, Implementer, Impact Auditor, Evaluator, obligation ledger и historical Matrix benchmark. Эта архитектура дала полезные наблюдения, но оказалась слишком дорогой и всё равно не исключила ложный `PASS`.
