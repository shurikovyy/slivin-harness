# Matrix all-matching historical benchmark

`workspace/` intentionally не tracked Harness repository.

## Purpose

Historical defect:

```text
Matrix filters
→ page selection
→ "Выбрать все N найденных"
→ all-matching token active
→ selectedRows empty
→ filter-chip refresh
→ normal "Подтвердить распред" исчезала
```

Case проверяет, способен ли Harness исправить bug без доступа к known-good implementation.

## Setup

После clone:

1. Скопировать **содержимое** broken `_90` в:

   ```text
   cases/matrix-all-matching/workspace/
   ```

2. Убедиться, что реальные `.env*`/secrets отсутствуют.

3. Подготовить baseline:

   ```bash
   ./py tools/prepare_workspace.py \
       cases/matrix-all-matching/workspace
   ```

4. Run:

   ```bash
   ./run cases/matrix-all-matching/task.toml
   ```

## Known-good reference

Полная `_92` рядом с Agent не нужна.

Held-out был отдельно calibrated:

```text
_90 → FAIL
_92 → PASS
```

Repository хранит hash-bound calibration certificate.

Если grader/check definition меняется, Harness откажется запускать case до recalibration.

## Held-out scope

Held-out проверяет только public Matrix contract:

1. all-matching остаётся explicit selection;
2. ordinary filter-only не показывает normal confirm action;
3. manual checkbox сохраняет normal action.

Он **не содержит** known-answer assertion про Distribution.

В successful historical run Distribution consumer был найден generic Planner/Evaluator analysis.

## Successful milestone

Один clean trial завершился:

```text
Planner
→ Implementer
→ deterministic checks
→ Fresh Evaluator
→ held-out
→ HARNESS_TASK_PASS
```

Post-hoc audit material product defect не нашёл.

## Known benchmark nuances

Во время broader sibling exploration встречались unrelated historical failures в existing suites вокруг:

```text
odata-response-state
grouped-page-selection
```

Они не являлись release gates final trial.

Перед превращением такого failure в blocker всегда проверять broken baseline.

## Current Harness hardening gap discovered from this case

Planner initial `candidate_paths` не включал Distribution source/test paths, а Implementer позже корректно изменил их после consumer discovery.

Следовательно текущий Controller пока не mechanically reconciles:

```text
planned candidate_paths
vs
actual final changed paths
```

Подробно:

```text
docs/CURRENT_STATE.md
D-032 в docs/DECISIONS.md
```

## Reset before another independent trial

```bash
cd cases/matrix-all-matching/workspace

git reset --hard HEAD
git clean -fd
rm -rf .harness_tmp

git status --short
```

После reset status должен быть пустым.

Не делать новый baseline commit из прошлого candidate.
