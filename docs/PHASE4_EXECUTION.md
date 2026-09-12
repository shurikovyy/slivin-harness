# Phase 4 — Implementer and deterministic Controller verification

> Historical Phase 4 document. Phase 5 implemented active Contract/Verification Plan recompilation and project runtime; Phase 6 implemented runtime/evaluator; Phase 7 completed semantic replan and Final Gate. See [Phase 7 Final Gate](PHASE7_FINAL_GATE.md).

Phase 4 connects the approved Step 3 and Step 4 contracts to the executable Harness.
It does not add another model role. It strengthens the writable implementation loop and
moves authoritative verification state into the Controller private plane.

Current `implementer.v6` COMPLETE also requires post-patch impact closure against the
actual candidate. The report reconciles Planner contracts/consumers, reviews every
changed path (including deletions), preserves related follow-ups and maps all newly
discovered consumers/risks to Controller Contract expansion. A technical-model
divergence requires REPLAN_REQUIRED. After any repair the impact sweep and final
self-verification run again; previous candidate evidence is stale. Controller records
the validated result as candidate-bound `implementation-impact-closure.v2`.

The strict report also requires `terminal_reason_kind`: COMPLETE → NONE;
REPLAN_REQUIRED → TECHNICAL_MODEL_DIVERGENCE or PROOF_MODEL_DIVERGENCE;
BLOCKED → INFRASTRUCTURE_BLOCKED; NEEDS_USER_DECISION → USER_DECISION_REQUIRED.
Controller rejects every mismatched pair. Non-COMPLETE still requires concrete
reason/evidence. A Planner-derived proof route invalidated by proven unrelated
baseline failures requires proof-model replan, not infrastructure BLOCKED.
Semantic preservation and owner-configured gates remain mandatory.

## Canonical flow

```text
IMPLEMENTATION_CONTRACT_READY
        ↓
IMPLEMENTER v2 (historical Phase 4 protocol; current release uses implementer.v6)
        │
        ├─ COMPLETE
        ├─ REPLAN_REQUIRED
        ├─ BLOCKED
        └─ NEEDS_USER_DECISION
        ↓
new consumer / risk?
        ├─ yes → Controller validates and records the discovery privately
        │         → the discovery cannot be lost from the run artifacts
        │         → Phase 5+ recompiles active Contract/Verification Plan transactionally
        └─ no
        ↓
register typed checks before COMPLETE
        ↓
SELF VERIFY
receipt binds candidate + Contract + Verification Plan + runtime environment
        ↓
CONTROLLER DETERMINISTIC CHECKS
        ├─ candidate freeze
        ├─ isolated temp/cache
        ├─ per-check timeout
        ├─ PASS / FAIL / TIMEOUT / INFRA / MUTATED classification
        └─ candidate freeze verification
        ↓
DETERMINISTIC_VERIFICATION_PASS
```

## Authority boundaries

```text
Agent-writable workspace
→ production edits and non-authoritative scratch

RUN_DIR/controller_private
→ active Contract revisions
→ typed check registry
→ self-verification receipts
→ Controller check artifacts
```

An agent may request a typed test path or check ID. It cannot register an arbitrary
Controller shell command. The Controller validates the request and updates the private
registry.

## Implementer terminal statuses

`COMPLETE` is the only status that requires full Contract closure and a current
Controller-owned self-verification receipt.

`REPLAN_REQUIRED`, `BLOCKED`, and `NEEDS_USER_DECISION` require a concrete reason and
evidence, but do not require artificial `BLOCKED` rows for every Contract item.

## Inactivity watchdog and tool activity

An active Implementer is not interrupted only because an elapsed wall-clock threshold
was reached. The inactivity watchdog is based on real App Server/model/tool activity. A running
long test is activity. A Controller heartbeat is not activity. An optional emergency
ceiling remains an owner safety setting, not the normal completion policy.

## Controller check result model

```text
CHECK_PASS
CHECK_FAIL
CHECK_TIMEOUT
CHECK_INFRA_ERROR
CHECK_MUTATED_CANDIDATE
```

Checks run with task-local temp/cache and a frozen candidate identity. If a check changes
candidate files, its result is not accepted even if its assertions pass.

Candidate freeze — physical inventory относительно Controller baseline, а не
`git status`/ignore/index view. Поэтому project code не может скрыть helper через
`.gitignore`, info exclude, local excludes file или index flags. Параллельный
Git-control guard проверяет HEAD/ref, persistent index, local/worktree config и
repository control files до/после self-verify, deterministic/dynamic/runtime и
held-out batches. Green command, изменившая Git control state, классифицируется
как infrastructure/integrity failure и не выпускает receipt.

Trusted command subprocesses и Harness-owned self-verify получают отдельный
одноразовый `GIT_INDEX_FILE`, initialized from `HEAD`, с отключёнными hooks.
Это сохраняет прежнюю `git diff` semantics для candidate, но stat-cache refresh
от `git diff --check` или вложенного project utility затрагивает только temporary
index. Real index остаётся frozen; explicit mutation `.git` по-прежнему видит
Git-control guard.

Generated Harness-owned `self_verify.py` reconfigures its own stdout/stderr to
UTF-8 before running checks. Unicode output from Jest or another project check
therefore cannot turn an otherwise valid self-verification into a legacy Windows
console-encoding crash.

Static и все post-plan/on-demand tool probes используют один
`TrustedBatchIntegrityCoordinator.run_read_only`: capability evidence не
принимается при mutation candidate, Git controls или runtime projection.

Manifest check commands have one strict expansion contract shared by execution
and `static-toolchain-preflight.v1`. Before any agent stage the Controller parses
all repair/held-out templates, resolves required executables, validates only
known input shapes (Node/Python scripts and Jest config/test paths), and runs
bounded version/config probes. It does not execute a check body, test suite or
hidden oracle. A newly registered dynamic check remains protected by the normal
Controller batch guard and its post-plan capability gate.

For known direct Node/Python scripts, explicit Jest config and selected
`--runTestsByPath` files, preflight records a Controller-private byte baseline.
The deterministic and reconstructed gates compare that baseline before executing
owner checks. Drift returns `OWNER_CHECK_INPUT_CHANGED`, executes none of the
changed owner inputs, and uses the existing bounded check-repair route. An
Implementer may add coverage in separate registered files; it may not rewrite the
owner assertion source that defines acceptance.

`{python}` is the project-first compatibility alias (`project_python`, then a
configured `python`, then the Harness interpreter). `{project_python}` is an
explicit probe-backed requirement; `{harness_python}` always selects the
Controller/Harness interpreter. Static expansion and actual manifest,
self-verify, deterministic, held-out and generated dynamic check expansion share
this resolver.

Production resolves project-relative toolchain entries against the managed
workspace, so a Controller-copied runtime is the executable authority. Historical
benchmark resolution remains source-relative until its explicit provenance-bearing
sanitize/rebind step. Reconstructed verification applies the same mode-specific rule.

Because Jest config is executable project code, static preflight freezes the
canonical `candidate.v1` before and after its runtime-guarded probe batch.
Tracked changes, deletions or untracked additions invalidate every successful
probe and stop the run. Only concrete Controller-owned paths such as
`.harness_tmp`, configured worktree `.venv`, registered projections and exposed
paths are excluded; cache-name wildcards are not exclusions. Raw probe output is
Controller-private and hard-capped at 1 MiB; public failure evidence is typed
and contains no raw output.

## Enforcement honesty

The Execution Broker records whether a filesystem/network boundary is `ENFORCED`,
`ADVISORY`, or `UNAVAILABLE`. Phase 4 never labels an advisory subprocess as an
OS-enforced sandbox. Projects may require an enforced runner through their capability
policy; otherwise the actual enforcement level is preserved in Controller evidence.

Planner/Evaluator use the shared scratch-only `RoleExecutionContext`: a fresh named
profile, role-local cache environment and session root, with explicit project command
cwd. Intake retains read-only mode; Implementer retains managed-candidate workspace-write.
Controller checks and held-out guards are unchanged. The
[native acceptance command](WINDOWS_SETUP.md#native-scoped-scratch-acceptance) checks
actual denied writes and cached assertions separately from reported policy metadata.

## Current alpha boundary

`0.8.0a6` introduced the Phase 4 primitives. Phase 5 (`0.8.0a8`) implemented automatic Contract/Verification Plan recompilation and optional worktree-local `.venv` bootstrap/rebuild. Phase 6 (`0.8.0a9`) adds executable runtime proof and the two-phase Evaluator. A universal OS-enforced Controller subprocess sandbox remains pending; the Broker still records `ADVISORY`/`UNAVAILABLE` honestly instead of labelling it enforced.

## Discovered runner resolution and replay

`test_runners.py` resolves supported JavaScript frameworks from executable lexical
module evidence: CommonJS `require('node:test')`, ESM static/dynamic imports, or
Jest imports/test/assertion syntax. Comments, literal examples and filename extensions
are not framework evidence. Mixed/unknown syntax gets `TEST_RUNNER_AMBIGUOUS` or
`TEST_RUNNER_UNSUPPORTED`; missing configured tools get `TEST_RUNNER_TOOL_MISSING`.
This conservative resolver is not a full JS parser or proof that assertions cover semantics.
Explicit owner commands bypass inference and remain mandatory exactly as configured.

Each native file compiles to `{node} {workspace}/path`, without `--test` child-process
routing; multiple files become multiple checks. Jest keeps its existing config and
argv behavior. Python templates keep project-first interpreter resolution. These
Controller templates are expanded in self_verify.py, Controller confirmation/checks
and reconstructed verification against their respective workspace/toolchain.
Registry digests include compiled specs. Re-registration recompiles all active paths,
replaces stale derived commands, retains owner gates and probes required tools/configs.
A compiler mismatch is repaired in the compiler, not by deleting tests or replanning
product code until a different runner happens to pass.

## Bounded report-only evidence correction

`run_implementer_report()` persists raw output privately before validation. For typed
local errors in populated post-patch `symbols`, `evidence` or `evidence_paths`, Controller
returns all independently diagnosable safe field/code errors in one batch and permits
at most two corrective turns in the same thread. A malformed parent blocks only its
children. Mixed local/semantic diagnostics cannot become a cosmetic correction.
Planner performs equivalent bounded local admission before its separate capability
negotiation; semantic model and proof requirements remain frozen. Evaluator Phase B
uses Controller-owned `origin_ref` handles, so model wire contains no classification or
source revision. Unknown/incompatible handles may change only their exact reference field;
Controller catalog tamper and semantic conclusion changes are not correction candidates.
Only identified evidence arrays can change. Findings, order, names, classification,
behavior, proof, discoveries, registered checks and all other report fields are frozen.
Concrete documentation link targets/heading anchors are valid identifiers; empty arrays
and invented code functions are not a substitute for repository evidence.

Correction does not authorize project/test/runtime/Git writes or permission changes.
Existing runtime/Git guards wrap the turn; physical candidate comparison occurs before
any receipt validation. Mutation or changed claims yields a controlled stop. Correction
timeouts do not get additional timeout continuation turns. Repeated diagnostics without
progress stop with `REPORT_CORRECTION_NO_PROGRESS`. Exhaustion produces
`IMPLEMENTER_REPORT_CORRECTION_EXHAUSTED`, without Final Acceptance. Semantic model
conflicts, missing consumers, failing checks and capabilities are not cosmetic retries.

Every attempt has a separate private raw artifact and diagnostic; public outcomes expose
codes/fields without raw private values. `terminal_candidate_observation.json` records
current physical files, or UNKNOWN with an explicitly stale previous identity if observation
is unsafe/unavailable, including timeout/unknown transport before any raw report.
`candidate-checkpoint.v1` privately seals physical changed file bytes/deletions, baseline,
source inventory, build digest and known evidence before validation. It is unverified
and does not authorize acceptance or cross-process resume. Successful correction still runs the full validator and existing
trusted checks, binding and Contract expansion; it does not itself prove COMPLETE.

Real installed-tool acceptance (no LLM/sandbox turns):

```powershell
py tools/smoke_trusted_test_runners.py --node C:/path/node.exe --jest C:/path/node_modules/jest/bin/jest.js
```

The disposable smoke runs passing/failing assertions through compiled checks, generated
self-verification and Controller; it checks replay paths, stale stamps, native-only
capabilities, then a mixed-runner expansion/report correction/Evaluator/Final Gate flow.
Agent responses in that workflow are doubles. Logs distinguish that from real subprocess
results. Installed runtime fingerprints must remain unchanged. Scoped sandbox integration
is preserved and is separately covered by `smoke_readonly_scratch.py`.
