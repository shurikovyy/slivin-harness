# Практическая работа с Slivin Harness 0.8.0a5

## Установка

Распакуйте release в отдельный каталог и перенесите только локальный `harness.local.toml`.

```bash
cd ~/Tools/slivin-harness-080a5-phase3
./py -c "import slivin_harness; print(slivin_harness.__version__)"
./py tools/self_check.py
```

Ожидаемая версия:

```text
0.8.0a5
```

## Что происходит при FULL-задаче

```text
1. Manifest и repository preflight
2. User Task Contract normalizer
3. Fresh Planner v4
4. Implementation Contract v3
5. Verification Plan v1
6. owner/capability gates
7. Implementer v1
8. existing Controller checks
9. runtime skip для local-only proof
10. Evaluator v4
11. Final Gate compatibility executor
```

## Какие artifacts искать

В run directory:

```text
controller_private/task_contract_01.json
controller_private/plan_01.json
controller_private/implementation_contract_01.json
controller_private/verification_plan_01.json
controller_private/capability_gate_01.json
```

Публичное зеркало зависит от текущего Recorder policy. Authoritative версия всегда находится в private Controller plane.

## Как читать Task Contract

Проверьте:

```text
raw_user_request не изменён;
explicit acceptance отражает слова пользователя;
explicit preservation не превратился в общую фразу;
source_text дословно присутствует в raw request;
normalizer не добавил техническое решение.
```

## Как читать Planner v4

Для bug должны быть:

```text
observed behavior
existing intended contract
root cause + evidence
confidence HIGH или допустимый MEDIUM
```

Для feature:

```text
extension point
design constraints
```

Для shared/stateful change смотрите consumers, State Model и risks.

## Как читать Implementation Contract

Он не должен быть пересказом всего Planner. Основные items:

```text
ACCEPTANCE-1
PRESERVE-1
STATE-1          если применимо
CONSUMER-N
RISK-N
DOCS-1           если нужно
```

Каждый item содержит claims и один или несколько typed proof profiles.

## Как читать Verification Plan

Пример local-only требования:

```json
{
  "profiles": [
    {"level": "LOCAL_DETERMINISTIC", "capabilities": []}
  ]
}
```

Пример двух разных runtime proofs:

```json
{
  "profiles": [
    {"level": "LIVE_LOCAL", "capabilities": ["BROWSER_DOM", "LIVE_LOCAL_RUNTIME"]},
    {"level": "TEST_EXTERNAL", "capabilities": ["TEST_EXTERNAL_FRESH_READ", "TEST_EXTERNAL_RUNTIME"]}
  ]
}
```

## Capability gate

Если runtime executor ещё не реализован, ожидаемый результат:

```text
HARNESS_TASK_STOPPED: REQUIRED_CAPABILITY_MISSING ...
```

Это корректный fail-closed outcome Phase 3, а не причина вручную снижать proof до unit-теста.

## FAST compatibility profile

Manifest `risk = "low"` пока сохраняет старый FAST pipeline. User Task Contract всё равно создаётся, Planner/Evaluator могут быть compatibility-skipped. Обычный будущий production workflow будет переведён на FULL после последующих фаз; Phase 3 не скрывает эту совместимость.

## Документация workflow

После изменения state machine:

```bash
./py tools/render_workflow_docs.py
./py tools/check_docs_sync.py
```

Не редактируйте generated таблицы `WORKFLOW.md` и `workflow.v2.json` вручную.

## Ограничения Phase 3

Не ожидайте пока:

```text
автоматической .worktreeinclude copy policy;
автоматического venv bootstrap;
Browser/test-external execution;
нового Implementer protocol;
двухфазного Evaluator;
полного inactivity watchdog.
```

Они должны быть реализованы последовательно и проверены отдельными historical trials.
