# Slivin Harness 0.8.0a6 — Phase 4

Slivin Harness управляет автономной работой Codex в изолированной Git-worktree и принимает результат только после заданного quality pipeline.

Версия **0.8.0a6** реализует Phase 4 поверх уже принятого фундамента Phase 1–3:

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
IMPLEMENTER implementer.v2
        ↓
SELF VERIFY + Controller-private typed check registry
        ↓
CONTROLLER DETERMINISTIC CHECKS
```

Machine-readable workflow: **workflow.v3**. Реализуемая фаза: **phase4-implementer-controller-verification**. Run state: **run-state.v1**. Candidate identity: **candidate.v1**. Private Controller plane: **controller-plane.v1**. Execution policy foundation: **execution-broker.v1**. Evaluator пока остаётся **evaluator.v4**.

## Фундамент Phase 3

### User Task Contract

До Planner узкий Intake Normalizer сохраняет исходный запрос дословно и извлекает только явно сказанные требования:

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

Planner формирует:

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

Explicit user acceptance и preservation копируются напрямую из User Task Contract. Отдельного общего `EVIDENCE-1` нет: каждый item содержит собственное `required_proof`.

### Verification Plan v1

Typed Verification Plan сохраняет один или несколько proof-профилей для каждого Contract item:

```text
LOCAL_DETERMINISTIC
LIVE_LOCAL
TEST_EXTERNAL
PROD_OBSERVE
```

До writable Implementer Controller проверяет owner boundary и наличие требуемых capabilities. Неисполняемый обязательный runtime proof блокирует задачу до расходования writable turn.

## Что добавляет Phase 4

### Implementer v2

Implementer может завершить turn только одним из четырёх статусов:

```text
COMPLETE
REPLAN_REQUIRED
BLOCKED
NEEDS_USER_DECISION
```

Только `COMPLETE` требует полного закрытия active Contract и актуальной self-verification. Остальные статусы требуют конкретной причины и evidence, но не искусственного `BLOCKED`-ledger по каждому item.

### Typed check registry и self-verification receipts

Task-specific tests регистрируются как typed path/check ID в Controller-private registry. Произвольная authoritative shell-команда от агента не принимается. Self-verification receipt связывает:

```text
candidate
Task/Plan/Contract/Verification revisions
runtime environment
attempt
check-registry digest
```

Изменение candidate, Contract или набора проверок делает старый receipt недействительным.

### Inactivity watchdog

Активный Implementer больше не прерывается только из-за общего elapsed time. Watchdog учитывает реальную App Server/model/tool activity; выполняющаяся команда считается activity, а Controller heartbeat — нет.

### Независимые Controller checks

Controller повторно запускает trusted checks и классифицирует результат:

```text
CHECK_PASS
CHECK_FAIL
CHECK_TIMEOUT
CHECK_INFRA_ERROR
CHECK_MUTATED_CANDIDATE
```

Candidate фиксируется до и после suite. Проверка, которая переписала candidate, не принимается даже при зелёных assertions. Изменённые/new test-файлы должны быть покрыты project suite или typed task-check registry.

Подробности: [Phase 4 execution](docs/PHASE4_EXECUTION.md).

## Каноническая схема

Полный Step 0–7 workflow генерируется из `slivin_harness/workflow.py`:

- [Понятная схема workflow](docs/WORKFLOW.md)
- [Machine-readable workflow.v3](docs/workflow.v3.json)
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
DOCS_SYNC_PASS harness=0.8.0a6 ...
HARNESS_SELF_CHECK_PASS
```

## Проверка manifest

```bash
./run cases/matrix-all-matching/task.toml --validate-only
```

Manifest пока остаётся `version = 2` для совместимости.

## Границы Phase 4 alpha

`0.8.0a6` намеренно **не заявляет готовыми**:

```text
.worktreeinclude как автоматическую canonical copy policy;
автоматический bootstrap/rebuild отдельной project .venv;
автоматическую перекомпиляцию active Contract/Verification Plan из discoveries;
universal OS-enforced sandbox для Controller subprocess;
LIVE_LOCAL / TEST_EXTERNAL / PROD_OBSERVE executors;
двухфазный Blind Evaluator;
clean-worktree semantic replan;
финальную delivery transaction.
```

Execution Broker честно фиксирует `ENFORCED`, `ADVISORY` или `UNAVAILABLE`. Advisory subprocess не выдаётся за полноценную OS-изоляцию.

## Локальная конфигурация

Machine-specific пути остаются в `harness.local.toml`, который не входит в release-архив. Пример: [`harness.local.example.toml`](harness.local.example.toml).

## Режимы результата

Текущий Harness поддерживает:

```text
keep_worktree
apply_to_source
```

Branch, commit, push и merge остаются ответственностью пользователя/будущего Publication Layer.
