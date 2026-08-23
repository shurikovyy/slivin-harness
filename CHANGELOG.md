# Changelog

## Unreleased

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
