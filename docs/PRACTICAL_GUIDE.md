# Практическая работа с Harness 0.8.0a3

## 1. Что изменилось для пользователя

Команды запуска не изменились:

```bash
./py tools/self_check.py
./run path/to/task.toml --validate-only
./run path/to/task.toml
```

Главное изменение — после старта появляется понятная нумерованная схема Step 0–7 и Controller сохраняет `run_state.json`.

## 2. Текущий compatibility pipeline

### `risk = "low"`

```text
0 Preflight
→ 1 Planner SKIPPED
→ 2 Contract
→ 3 Implementer
→ 4 Checks
→ 5 Runtime SKIPPED
→ 6 Evaluator SKIPPED
→ 7 Final
```

### `risk = "medium"` или `"high"`

```text
0 Preflight
→ 1 Planner
→ 2 Contract
→ 3 Implementer
→ 4 Checks
→ 5 Runtime SKIPPED
→ 6 Evaluator
→ 7 Final
```

Это compatibility mapping 0.7.1. Будущий FULL quality mode и conditional Runtime будут внедрены позже.

## 3. Что смотреть во время run

В начале Controller печатает:

```text
WORKFLOW_MODE
PIPELINE_PROFILE
PIPELINE
WORKSPACE
RUN_DIR
```

При ошибке `RUN_DIR` также печатается в stderr.

## 4. Как читать `run_state.json`

Основные поля:

```text
cursor_stage     — последний завершённый этап
active_stage     — этап, выполняющийся сейчас
attempt_id       — номер technical attempt
revisions        — версии artifacts/candidate/environment
current_candidate— identity текущего candidate
stages           — последнее состояние каждого Step 0–7
events           — полная последовательность переходов
terminal         — итог run
```

Пример успешного terminal:

```json
{
  "outcome": "PASS",
  "result_code": "HARNESS_TASK_PASS",
  "reason_code": null
}
```

## 5. Как читать stage state

```text
NOT_STARTED  — этап ещё не запускался
IN_PROGRESS  — выполняется сейчас
PASSED       — успешный обязательный этап
SKIPPED      — осознанный успешный skip
STOPPED      — BLOCKED или NEEDS_USER_DECISION
FAILED       — repair/replan/invalid route
INVALIDATED  — прежнее evidence устарело
```

`INVALIDATED` не означает, что старый artifact удалён. Он остаётся в event history, но больше не считается действующим.

## 6. Candidate identity

Файл:

```text
candidate_identity_current.json
```

показывает:

```text
baseline_sha
workspace_head
candidate_id
changed_paths
entries
```

Если agent неожиданно сделал commit и изменил workspace HEAD, Harness останавливается.

## 7. Результаты

Production:

```text
HARNESS_TASK_PASS
```

Historical benchmark:

```text
HARNESS_BENCHMARK_PASS
```

Файлы:

```text
candidate.patch
final_acceptance.json
run_state.json
```

`final_acceptance.json` связывает final candidate с patch SHA-256 и result mode.

## 8. Repair и replan в текущей версии

Current 0.7.1 behavior пока сохранён:

- deterministic failure возвращается тому же Implementer;
- Evaluator finding возвращается тому же Implementer;
- Evaluator `REPLAN_REQUIRED` запускает fresh Planner, но остаётся в той же worktree;
- fixed `max_fix_cycles`, `max_replan_cycles` и timeout continuation пока действуют.

Run State уже отражает эти переходы и invalidation, но clean-worktree replan и no-progress/watchdog policy будут следующими фазами.

## 9. Runtime Step 5

В 0.8.0a3 он записывается:

```text
state = SKIPPED
result_code = RUNTIME_VERIFICATION_SKIPPED
reason_code = RUNTIME_LAYER_NOT_IMPLEMENTED_PHASE1
```

Это не product verdict. Runtime executor ещё не существует.

## 10. Документация workflow

Не редактируйте `WORKFLOW.md` вручную.

После изменения workflow:

```bash
./py tools/render_workflow_docs.py
./py tools/render_workflow_docs.py --check
./py tools/check_docs_sync.py
```

## 11. Что писать в задаче сейчас

Manifest v2 и prompt пока остаются прежними. Пользователь описывает product outcome и preservation обычным языком.

Пример:

```text
После «Выбрать все N найденных» исчезает «Подтвердить распред».
Исправь и не ломай остальные сценарии выбора.
```

User Task Contract normalizer появится в следующей фазе; в Phase 1 raw prompt по-прежнему напрямую передаётся существующим roles.

## 12. Что не считать доказанным

Наличие Step 5/двухфазного Evaluator в target documentation не означает, что они уже работают. Ориентируйтесь на колонку «Состояние в Phase 1» в [WORKFLOW.md](WORKFLOW.md).


## Как читать Phase 2 artifacts

Для обычной диагностики используйте public mirror:

```text
runs/<task>/<run>/run_state.json
execution_policies.json
```

Authoritative Controller state находится в:

```text
runs/<task>/<run>/controller_private/
```

Его не нужно копировать в prompt или редактировать вручную. `.harness_tmp` внутри worktree — только scratch; его наличие или содержимое никогда не является основанием для `PASS`.

В `execution_policies.json` проверяйте поле enforcement. `ADVISORY` означает, что Harness сформировал правильный intent/environment, но OS-level boundary ещё не доказана на данной платформе.
