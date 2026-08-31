# Phase 5 — Contract expansion and reproducible project runtime

Phase 5 closes two gaps left intentionally open after Phase 4:

```text
Implementer found a new material consumer/risk
        ↓
active Definition of Done must expand before COMPLETE
```

and:

```text
Tests passed in a locally mutated Python environment
        ↓
the same result must survive a clean worktree-local runtime rebuild
```

No new model role is added. Controller owns both mechanisms.

## Canonical flow

```text
IMPLEMENTER COMPLETE proposal
        ↓
register typed checks in Controller-private registry
        ↓
validate discovered consumer/risk + typed required proof
        ↓
CONTRACT_EXPANDED or CHECK_REGISTERED
        ↓
invalidate Step 2 and all downstream evidence
        ↓
compile active Implementation Contract revision
        ↓
compile active Verification Plan revision
        ↓
repeat owner-boundary gate
        ↓
repeat required-capability gate
        ↓
same Implementer thread receives the expanded Definition of Done
        ↓
new SELF VERIFY receipt bound to new revisions
```

A new runtime proof cannot be hidden inside prose. If a discovery requires `LIVE_LOCAL`,
`TEST_EXTERNAL`, or `PROD_OBSERVE`, the recompiled Verification Plan records that profile
and its capabilities. When the executor/capability is unavailable, Step 2 becomes
`BLOCKED` before the Implementer is allowed to claim completion.

## Open-world Contract transaction

Implementer protocol is now `implementer.v3`. A discovered obligation contains:

```text
kind                 consumer | risk
name
reason
required_behavior
required_proof       typed proof target
evidence
```

Controller creates only:

```text
CONSUMER-DISCOVERED-N
RISK-DISCOVERED-N
```

Existing items are immutable. Exact duplicate discoveries are idempotent and do not
create obligation explosion. The 14-item size is a soft review threshold; a material
obligation is never dropped to satisfy the threshold.

Typed check registration participates in the same transaction. A new check path or
trusted check ID changes the Verification Plan and invalidates the old self-verification
receipt even when candidate bytes did not change. Every accepted reference must resolve
before it enters the private registry: test paths need a trusted runner, and the only
built-in check ID in this alpha is `git.diff-check`. A safe-looking unknown ID is rejected
instead of becoming non-executable proof metadata.

## `.worktreeinclude`

A repository-level `.worktreeinclude` is the canonical policy for ignored local runtime
files required by a managed worktree.

Example:

```gitignore
.env
.env.local
```

Harness copies only paths that are all of the following:

```text
matched by .worktreeinclude;
Git-ignored;
inside the source repository;
regular files/directories without symlink, junction or reparse traversal in the leaf or any ancestor;
not already present in the new worktree.
```

An `.env` explicitly listed there does not require a second
`allow_sensitive_copy=true`: the repository owner has already opted it in. Manual
`copy_untracked` remains an additional machine-local override and retains its sensitive
opt-in rule.

Copied runtime files are excluded from candidate identity and patch output. Controller
stores only path-bound keyed HMAC fingerprints in the private plane; no reusable plain
content hash is published. If Implementer changes one,
Controller restores it from the unchanged source checkout and requires a fresh
self-verification; a hidden `.env` edit cannot become the reason a candidate passes.

## Worktree-local Python runtime

A project can define an authoritative runtime once in `harness.local.toml`:

```toml
[projects.sa_icover.runtime]
bootstrap_python = "C:/Users/<user>/AppData/Local/Programs/Python/Python312/python.exe"
expected_python = "3.12"
venv = ".venv"
dependency_files = ["requirements.txt"]
pip_install_args = ["--disable-pip-version-check"]
```

Controller then performs:

```text
configured bootstrap Python
        ↓
version check
        ↓
create <worktree>/.venv
        ↓
install declared requirements
        ↓
pip check
        ↓
pip freeze --all snapshot
        ↓
PROJECT_PYTHON = <worktree>/.venv/Scripts/python.exe
```

Harness Python remains Controller-only. There is no silent fallback from a missing or
invalid project runtime to Harness Python.

### Windows path ownership

Native Windows may expose the same temporary/worktree directory through different lexical
spellings. Runtime ownership is therefore not inferred with string-prefix comparison.
Controller preserves the worktree-local venv entry-point path and verifies the actual
structural invariant:

```text
canonical parent(project_python)
→ belongs to Controller-owned worktree venv
```

This also preserves the POSIX requirement not to resolve a venv Python symlink back to the
global bootstrap interpreter: checks execute the worktree entry point and its isolated
site-packages.

## Runtime reconciliation before COMPLETE

At every `COMPLETE` proposal Controller compares the current environment with the
pre-implementation runtime record:

```text
dependency declaration digest;
project Python version;
installed package snapshot;
pip check.
```

If `requirements.txt` changed, or an undeclared `pip install` changed the package set,
Controller destroys and rebuilds the task-local `.venv` from the active declarations.
This causes `DEPENDENCY_MANIFEST_CHANGED` or `RUNTIME_ENV_CHANGED`, increments the runtime
revision, invalidates the previous self-verification receipt, and returns the same
Implementer thread to Step 3.

A candidate is accepted only after self-verification succeeds in the final rebuilt
runtime.

## Evidence identity

The authoritative self-verification receipt remains Controller-private and is bound to:

```text
candidate_id
task_contract_rev
plan_rev
implementation_contract_rev
verification_plan_rev
runtime_environment_rev
attempt_id
check_registry_digest
```

Therefore neither unchanged candidate bytes nor a stale stamp can carry evidence across
a Contract, check-registry, or runtime change.

## Current alpha boundary

`0.8.0a8` does not yet claim:

```text
universal OS-enforced sandbox for Controller subprocesses;
LIVE_LOCAL / TEST_EXTERNAL / PROD_OBSERVE scenario executors;
two-phase Blind Evaluator;
fresh clean worktree for semantic replan;
final delivery critical section.
```

Phase 5 prepares reliable inputs for those stages. Execution Broker still records the
actual `ENFORCED`, `ADVISORY`, or `UNAVAILABLE` level instead of overstating isolation.
