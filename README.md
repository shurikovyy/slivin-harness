# Slivin Harness 0.8.0a5 — Phase 3

Slivin Harness управляет автономной работой Codex в изолированной Git-worktree и принимает результат только после заданного quality pipeline.

Версия **0.8.0a5** реализует Phase 3 согласованной архитектуры:

```text
RAW USER REQUEST
        ↓
USER TASK CONTRACT task-contract.v1
        ↓
PLANNER planner.v4
        ↓
IMPLEMENTATION CONTRACT implementation-contract.v3
        ↓
VERIFICATION PLAN verification-plan.v1
        ↓
owner-boundary + capability gates
        ↓
IMPLEMENTER implementer.v1
```

Machine-readable workflow: **workflow.v2**. Run state: **run-state.v1**. Candidate identity: **candidate.v1**. Private Controller foundation: **controller-plane.v1**. Execution policy foundation: **execution-broker.v1**. Evaluator пока остаётся **evaluator.v4**.

## Что изменилось в Phase 3

### User Task Contract

Перед Planner отдельный узкий Intake Normalizer сохраняет исходный запрос дословно и извлекает только явно сказанные требования:

```text
intent
acceptance
preservation
forbidden
owner boundaries
non-goals
```

Каждый explicit claim обязан ссылаться на точный непрерывный `source_text` из исходного запроса. Normalizer не читает repository и не придумывает техническое решение.

### Planner v4

Planner теперь отдельно формирует:

```text
characterization
bug root cause или feature extension point
design constraints
material assumptions
technical acceptance
derived preservation
affected consumers
conditional State Model
material risks
typed Evidence Plan
documentation decision
```

Planner остаётся read-only и не может заменить пользовательское acceptance собственной формулировкой.

### Implementation Contract v3

Controller детерминированно переносит load-bearing выводы в минимальный Definition of Done:

```text
ACCEPTANCE
PRESERVATION
STATE            условно
CONSUMER-N
RISK-N
DOCS             условно
```

Explicit user acceptance и preservation копируются напрямую из User Task Contract. Отдельного общего `EVIDENCE-1` больше нет: каждый item содержит собственное `required_proof`.

### Verification Plan v1

Typed Verification Plan не пытается угадывать способ проверки из свободного текста. Он сохраняет один или несколько proof-профилей для каждого Contract item:

```text
LOCAL_DETERMINISTIC
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

Разные runtime-профили не схлопываются в один «самый сильный»: requirement может одновременно требовать, например, `LIVE_LOCAL` и `TEST_EXTERNAL`.

До writable Implementer Controller проверяет:

```text
owner boundary совместим с планом?
все требуемые capabilities реально доступны?
```

Если нет — задача останавливается до расходования writable turn.

## Каноническая схема

Полный Step 0–7 workflow генерируется из `slivin_harness/workflow.py`:

- [Понятная схема workflow](docs/WORKFLOW.md)
- [Machine-readable workflow.v2](docs/workflow.v2.json)
- [Архитектура](docs/ARCHITECTURE.md)
- [Модель качества](docs/QUALITY_MODEL.md)
- [Практическое руководство](docs/PRACTICAL_GUIDE.md)
- [Windows setup](docs/WINDOWS_SETUP.md)

## Быстрая проверка

```bash
./py tools/self_check.py
```

Ожидаемый финал:

```text
DOCS_SYNC_PASS harness=0.8.0a5 ...
HARNESS_SELF_CHECK_PASS
```

## Проверка manifest

```bash
./run cases/matrix-all-matching/task.toml --validate-only
```

Manifest пока остаётся `version = 2` для совместимости.

## Что Phase 3 ещё не реализует

Phase 3 намеренно **не заявляет готовыми**:

```text
.worktreeinclude как автоматическую canonical copy policy;
автоматический bootstrap отдельной project .venv;
open-world register-obligation/register-check IPC;
inactivity watchdog вместо active wall-clock timeout;
universal restricted OS runner для Controller checks;
LIVE_LOCAL / TEST_EXTERNAL / PROD_OBSERVE executors;
двухфазный Blind Evaluator;
clean-worktree semantic replan;
финальную delivery transaction.
```

Если `verification-plan.v1` требует runtime capability, для которой executor ещё не реализован, Harness честно возвращает `REQUIRED_CAPABILITY_MISSING` **до Implementer**. Он не подменяет обязательный runtime proof зелёными unit-тестами.

## Локальная конфигурация

Machine-specific пути остаются в `harness.local.toml`, который не входит в release-архив. Пример: [`harness.local.example.toml`](harness.local.example.toml).

## Режимы результата

Текущий Harness поддерживает существующие режимы:

```text
keep_worktree
apply_to_source
```

Branch, commit, push и merge остаются ответственностью пользователя/будущего Publication Layer.
