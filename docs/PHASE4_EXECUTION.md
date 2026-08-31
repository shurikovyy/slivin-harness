# Phase 4 — Implementer and deterministic Controller verification

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
        │         → automatic active Contract/Verification Plan recompilation is pending
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

## Enforcement honesty

The Execution Broker records whether a filesystem/network boundary is `ENFORCED`,
`ADVISORY`, or `UNAVAILABLE`. Phase 4 never labels an advisory subprocess as an
OS-enforced sandbox. Projects may require an enforced runner through their capability
policy; otherwise the actual enforcement level is preserved in Controller evidence.

## Current alpha boundary

`0.8.0a6` introduced the Phase 4 primitives. Phase 5 (`0.8.0a8`) now implements the previously pending automatic Contract/Verification Plan recompilation and optional worktree-local `.venv` bootstrap/rebuild. A universal OS-enforced Controller subprocess sandbox remains pending; the Broker still records `ADVISORY`/`UNAVAILABLE` honestly instead of labelling it enforced.
