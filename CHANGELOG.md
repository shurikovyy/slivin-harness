# Changelog

## Unreleased


### D-032 and managed-project workspace increment

- Enforced `actual changed paths ⊆ Planner.candidate_paths` for medium/high-risk tasks.
- Unexpected changed paths are recorded, rolled back to task baseline, replanned and
  snapshotted before the Implementer may re-apply a required change.
- Added per-path evidence (`captured_before_path_edit`) for late discovered consumers.
- Added final change-surface guard before task PASS.
- Removed project-specific hardcoded Python/Node/Jest defaults from Harness source.
- Added portable machine/project profiles in ignored `harness.local.toml` with
  `{project_root}` toolchain expansion.
- Decoupled Harness bootstrap Python from target-project virtualenv.
- Added managed detached Git-worktree mode for ordinary project tasks.
- Added opt-in `copy_untracked` local file exposure (including `.env` when explicitly
  allowed) without including those files in candidate patches.
- Added `keep_worktree` and `apply_to_source` result modes; `apply_to_source` applies
  the accepted binary candidate diff to the original clean source working tree without
  commit/push/branch changes.
- `prepare_workspace.py` no longer deletes `.venv`/`node_modules`; static historical
  `.env` visibility can be opted into with `--allow-env`.
- Added stdlib tests for D-032 rollback/evidence semantics and managed worktree/apply.

Validation status: local `tools/self_check.py` passes in the development environment;
Windows/App Server historical regression is required before tagging this increment as a
new validated milestone.

### Documentation baseline

- Added architecture documentation under `docs/`.
- Recorded accepted, rejected and deferred decisions.
- Recorded Windows sandbox/workspace setup and troubleshooting history.
- Recorded the causal development history from the first App Server/sandbox probes
  through the successful Matrix historical benchmark.
- Added a maintenance policy so future Harness changes retain their rationale.


### Continuation-context audit

- Added `docs/CURRENT_STATE.md` as the canonical cross-session handoff.
- Documented the current Git/publication trust boundary for task agents.
- Documented the target-project Python/Django runtime gap.
- Documented repeated historical-workspace reset procedure.
- Recorded the successful Matrix trial's planned-vs-actual path mismatch:
  Distribution was correctly added by the Implementer but was outside the
  initial `candidate_paths`, so its pre-edit evidence was not captured.
- Accepted a future mechanical final-diff-to-plan reconciliation contract.
- Recorded the README newline oracle incident as a second example of semantic
  grader over-specification.

## v0.4.6

Quality-core milestone.

Current pipeline includes:

- Planner / Characterization;
- current-contract and assumption validation;
- pre-edit baseline snapshots;
- release obligation evidence ledger;
- LIFE state lifecycle/ownership audit;
- REP representation-consumer audit;
- AUTH authority/precedence audit;
- Fresh Evaluator;
- deterministic repair loops and replan loops;
- held-out checks without tutoring;
- hash-bound historical grader calibration;
- heartbeat, timings, health checks and run artifacts;
- repository instruction/skill discovery;
- disposable workspace preparation and secret hygiene.

### Proven historical milestone

The Matrix all-matching historical case was completed autonomously:

```text
calibration certificate PASS
→ Planner
→ Implementer
→ deterministic checks PASS
→ Fresh Evaluator PASS
→ held-out PASS
→ HARNESS_TASK_PASS
```

The final candidate also passed an independent post-hoc audit without a material
escaped defect. In particular, the shared-selection change preserved the
Distribution stage guard as fail-closed for token-only selection.

This is an intermediate quality milestone, not proof of universal reliability.
