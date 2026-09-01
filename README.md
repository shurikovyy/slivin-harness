# Slivin Harness 0.8.0a12 — Phase 7

Slivin Harness управляет автономной работой Codex в изолированной Git-worktree и принимает результат только после заданного quality pipeline.

Версия **0.8.0a12** сохраняет завершённый Phase 7 quality-core и добавляет первый post-trial stabilization fix для Intake:

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
IMPLEMENTER implementer.v3
        ↓
transactional Contract / Verification Plan expansion
        ↓
worktree-local project runtime reconciliation
        ↓
SELF VERIFY + Controller-private typed check registry
        ↓
CONTROLLER DETERMINISTIC CHECKS
        ↓
RUNTIME / EXTERNAL VERIFICATION (условно)
        ↓
TWO-PHASE BLIND EVALUATOR evaluator.v5
        ↓
FINAL GATE phase7-final-gate.v1
        ↓
patch reconstruction + transactional result delivery
```

Machine-readable workflow: **workflow.v6**. Реализуемая фаза: **phase7-final-gate-delivery-benchmark**. Run state: **run-state.v1**. Candidate identity: **candidate.v1**. Private Controller plane: **controller-plane.v1**. Execution policy foundation: **execution-broker.v1**. Runtime evidence: **runtime-evidence.v1**. Blind audit: **blind-audit.v1**. Final Gate: **phase7-final-gate.v1**.

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

Условия разных scopes не являются прямым противоречием: explicit selection может разрешать action,
а filter-only запрещать его; current state может быть valid, а stale state — invalid. Если модель
возвращает schema-valid, но семантически невалидное сочетание полей, Controller не завершает весь
run немедленно: тот же Intake-thread получает точный код validation failure и до двух раз возвращает
полный исправленный artifact. RAW USER REQUEST остаётся неизменным, а authoritative становится
только валидный финальный `task-contract.v1`.

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

## Фундамент Phase 4

### Implementer v3

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

## Что добавляет Phase 5

### Transactional open-world expansion

Новый consumer/risk или typed check больше не остаётся только заметкой в отчёте. Controller пересобирает active Implementation Contract и Verification Plan, повторяет owner/capability gates, инвалидирует старый self-verify и возвращает тому же Implementer новую обязательную ревизию. Check reference принимается только если он реально исполним: test path должен иметь trusted runner, а текущий встроенный trusted ID — `git.diff-check`.

### Canonical `.worktreeinclude`

Ignored runtime-файлы, явно перечисленные repository owner, автоматически копируются в managed worktree. `.env` из `.worktreeinclude` не требует второго sensitive opt-in, не входит в candidate/patch и восстанавливается Controller, если агент его изменил. Symlink/junction проверяется по всей цепочке родителей, а private comparison использует path-bound keyed HMAC.

### Worktree-local Python runtime

Проект может задать bootstrap Python и dependency declarations один раз в `harness.local.toml`. Harness создаёт собственную `.venv` в каждой worktree, использует её как authoritative `PROJECT_PYTHON`, обнаруживает скрытый `pip install`/изменение requirements и перед `COMPLETE` делает clean rebuild + повторный self-verify.

Подробности: [Phase 5 contract/runtime](docs/PHASE5_CONTRACT_RUNTIME.md).

## Что добавляет Phase 6

### Controller-owned Runtime Verification

Typed Verification Plan теперь исполняется для `LIVE_LOCAL`, `TEST_EXTERNAL` и `PROD_OBSERVE`, если owner настроил покрывающий scenario. Controller связывает scenario с конкретными Contract items, фиксирует candidate/source до и после, требует structured result, fresh readback для test-external write и cleanup/disposable boundary. Local-only proof получает явный `RUNTIME_VERIFICATION_SKIPPED`, а не молчаливое отсутствие evidence.

### Contract Closure Record

Перед независимой проверкой Controller создаёт `contract-closure.v1`: каждый active item связан с candidate, Contract/Verification fingerprints и принятым `VERIFIED`/допустимым `NOT_AFFECTED` evidence. Evaluator не получает свободное объяснение Implementer.

### Двухфазный Blind Evaluator v5

Один fresh read-only evaluator сначала выполняет blind discovery без Planner, Implementation Contract, Implementer Report, зелёных checks и runtime evidence. `blind-audit.v1` сохраняется Controller до раскрытия framing. Затем Phase B получает только Controller-normalized Contract, Closure Record, deterministic и runtime evidence; каждый blind finding обязан быть сохранён или снят с конкретным evidence.

Подробности: [Phase 6 runtime/evaluator](docs/PHASE6_RUNTIME_EVALUATOR.md).

## Что добавляет Phase 7

### One-candidate Final Gate

Controller принимает результат только если Step 3–6 относятся к одному `candidate_id` и к текущим ревизиям Task/Plan/Contract/Verification/Runtime. После `EVALUATION_PASS` любое изменение candidate инвалидирует приёмку.

### Patch reconstruction

`candidate.patch` применяется в отдельной чистой verification-копии recorded baseline. Полученный `candidate_id` обязан побайтово совпасть с уже проверенным candidate. Только после этого создаётся immutable `final-acceptance.v2`.

Private reconstruction repository зеркалирует только effective Git settings, которые определяют worktree bytes/mode (`core.autocrlf`, `core.eol`, `core.safecrlf`, `core.filemode`, `core.symlinks`). Это сохраняет строгий CRLF/LF byte-level proof на native Windows, не перенося arbitrary hooks, aliases, transports или credentials.

### Безопасная доставка

`keep_worktree` сохраняет доказанный result без изменения source. `apply_to_source` выполняется под коротким delivery lock: повторно проверяются source HEAD/clean state и preimages, затем `git apply --check`, apply, exact diff/postimage comparison. При конфликте source не перезаписывается, а accepted patch сохраняется.

### Изолированный historical benchmark

Benchmark запускается в standalone one-commit repository без shared Git metadata, посторонних refs и объектов с reference solution. Held-out исполняется только после normal pipeline PASS, классифицирует semantic/infra/timeout/mutation отдельно и никогда не возвращает hidden feedback агентам.

### Чистый semantic replan

Если Evaluator отверг саму техническую модель, Controller сохраняет rejected patch как artifact, очищает candidate до baseline, сбрасывает task-specific registry и runtime, а затем запускает fresh Planner и fresh Implementer thread.

Подробности: [Phase 7 Final Gate](docs/PHASE7_FINAL_GATE.md).

## Каноническая схема

Полный Step 0–7 workflow генерируется из `slivin_harness/workflow.py`:

- [Понятная схема workflow](docs/WORKFLOW.md)
- [Machine-readable workflow.v6](docs/workflow.v6.json)
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
DOCS_SYNC_PASS harness=0.8.0a12 ...
HARNESS_SELF_CHECK_PASS
```

## Проверка manifest

```bash
./run cases/matrix-all-matching/task.toml --validate-only
```

Manifest пока остаётся `version = 2` для совместимости.

## Границы Phase 7 alpha

`0.8.0a12` завершает согласованный Step 0–7 quality-core, но намеренно **не заявляет готовыми**:

```text
universal OS-enforced sandbox для каждого Controller subprocess;
готовые универсальные browser/1С/PostgreSQL/Airflow wrappers;
автоматическую публикацию commit/push/PR/merge;
production write verification;
универсальную надёжность по одному benchmark trial.
```

Следующий checkpoint после Windows self-check — реальный historical `_90` trial. Это проверка всего quality-core, а не новая архитектурная фаза.

Runtime scenarios — Controller-owned project configuration, а не произвольные команды агента. `PROD_OBSERVE` принимается только при явно заявленной технической read-only границе; Execution Broker по-прежнему честно фиксирует `ENFORCED`, `ADVISORY` или `UNAVAILABLE`.

## Локальная конфигурация

Machine-specific пути остаются в `harness.local.toml`, который не входит в release-архив. Пример: [`harness.local.example.toml`](harness.local.example.toml).

## Режимы результата

Текущий Harness поддерживает:

```text
keep_worktree
apply_to_source
```

Branch, commit, push и merge остаются ответственностью пользователя/будущего Publication Layer.
