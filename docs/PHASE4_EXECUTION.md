# Phase 4 — Implementer and deterministic Controller verification

> Historical Phase 4 document. Phase 5 implemented active Contract/Verification Plan recompilation and project runtime; Phase 6 implemented runtime/evaluator; Phase 7 completed semantic replan and Final Gate. See [Phase 7 Final Gate](PHASE7_FINAL_GATE.md).

Phase 4 connects the approved Step 3 and Step 4 contracts to the executable Harness.
It does not add another model role. It strengthens the writable implementation loop and
moves authoritative verification state into the Controller private plane.

## Canonical flow

```text
IMPLEMENTATION_CONTRACT_READY
        ↓
IMPLEMENTER v2 (historical Phase 4 protocol; current release uses implementer.v3)
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

`{python}` is the project-first compatibility alias (`project_python`, then a
configured `python`, then the Harness interpreter). `{project_python}` is an
explicit probe-backed requirement; `{harness_python}` always selects the
Controller/Harness interpreter. Static expansion and actual manifest,
self-verify, deterministic, held-out and generated dynamic check expansion share
this resolver.

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

## Current alpha boundary

`0.8.0a6` introduced the Phase 4 primitives. Phase 5 (`0.8.0a8`) implemented automatic Contract/Verification Plan recompilation and optional worktree-local `.venv` bootstrap/rebuild. Phase 6 (`0.8.0a9`) adds executable runtime proof and the two-phase Evaluator. A universal OS-enforced Controller subprocess sandbox remains pending; the Broker still records `ADVISORY`/`UNAVAILABLE` honestly instead of labelling it enforced.
