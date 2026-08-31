# Практическая работа с Slivin Harness 0.8.0a6

## Установка

Распакуйте release в отдельный каталог и перенесите только локальный `harness.local.toml`.

```bash
cd ~/Tools/slivin-harness-080a6-phase4
./py -c "import slivin_harness; print(slivin_harness.__version__)"
./py tools/self_check.py
```

Ожидаемая версия:

```text
0.8.0a6
```

## Что происходит при FULL-задаче

```text
1. Manifest и repository preflight
2. User Task Contract normalizer
3. Fresh Planner v4
4. Implementation Contract v3
5. Verification Plan v1
6. owner/capability gates
7. Implementer v2
8. typed check registration + revision-bound SELF VERIFY
9. independent Controller deterministic checks
10. runtime skip для local-only proof
11. Evaluator v4
12. Final Gate compatibility executor
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

Это корректный fail-closed outcome Phase 4, а не причина вручную снижать proof до unit-теста.

## FAST compatibility profile

Manifest `risk = "low"` пока сохраняет старый FAST pipeline. User Task Contract всё равно создаётся, Planner/Evaluator могут быть compatibility-skipped. Обычный будущий production workflow будет переведён на FULL после последующих фаз; Phase 4 не скрывает эту совместимость.

## Документация workflow

После изменения state machine:

```bash
./py tools/render_workflow_docs.py
./py tools/check_docs_sync.py
```

Не редактируйте generated таблицы `WORKFLOW.md` и `workflow.v3.json` вручную.

## Ограничения Phase 4

Пока не реализованы полностью:

```text
автоматическая .worktreeinclude copy policy;
worktree-local .venv bootstrap/rebuild;
автоматическая перекомпиляция active Contract/Verification Plan из discoveries;
universal OS-enforced sandbox для Controller subprocess;
LIVE_LOCAL / TEST_EXTERNAL / PROD_OBSERVE executors;
двухфазный Evaluator;
clean-worktree semantic replan;
финальная delivery transaction.
```

## Implementer и Controller checks

Implementer делает smallest complete fix, регистрирует material tests до `COMPLETE`, запускает self-verification и указывает конкретное evidence для каждого active Contract item. `REPLAN_REQUIRED`, `BLOCKED` и `NEEDS_USER_DECISION` являются отдельными terminal explanations и не требуют искусственных `BLOCKED`-строк по каждому item.

Typed check registry хранится в private Controller plane. После добавления нового check старый self-verification receipt становится stale. Controller затем независимо повторяет trusted suite в изолированных temp/cache-каталогах и отклоняет check, который изменил candidate. Активный Implementer контролируется inactivity watchdog: running tool считается activity, Controller heartbeat — нет.
