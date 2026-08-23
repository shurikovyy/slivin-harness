# Архитектура Slivin Harness

## 1. Назначение

Slivin Harness — отдельный персональный orchestration/quality layer вокруг Codex App Server.

Он не является частью `sa_icover` и не навязывается командному repository workflow.

Цель:

> Пользователь один раз делегирует инженерную задачу, после чего Harness автономно проводит исследование, реализацию, проверки, независимое review и repair/replan до доказуемого результата либо корректно останавливается с `BLOCKED` / `NEEDS_USER_DECISION`.

Основная пользовательская метрика:

```text
human interventions per accepted task ≈ 1
```

---

## 2. Trust boundaries

```text
Human
  │
  │ intent / product semantics / high-risk approval
  ▼
Slivin Harness Controller
  │
  ├── task contract
  ├── workspace boundary
  ├── trusted toolchain
  ├── Planner
  ├── Implementer
  ├── deterministic checks
  ├── Fresh Evaluator
  ├── evidence ledger
  ├── repair / replan
  └── held-out / runtime acceptance
        │
        ▼
Codex App Server
        │
        ▼
Disposable project workspace
```

Controller, а не модель, владеет Definition of Done.

Сообщение агента `PASS` означает только завершение turn.

---

## 3. Основные компоненты repository

```text
task_runner.py
    Главный Controller / state machine.

slivin_harness/app_server.py
    JSON-RPC adapter к Codex App Server:
    lifecycle, queues, health, heartbeats, structured turn output.

slivin_harness/planner.py
    Read-only Characterizer / Planner.

slivin_harness/evaluator.py
    Fresh read-only independent Evaluator.

tools/prepare_workspace.py
    Подготовка disposable project workspace/baseline.

tools/self_check.py
    Быстрая самопроверка Harness source/manifests.

run / run.cmd
    CWD-independent launcher.

py / py.cmd
    Python launcher для Harness utility scripts.

cases/
    Historical evaluation cases и manifests.
    Полные project workspaces в Git Harness не входят.

hidden_checks/
    Harness-owned held-out graders и calibration certificates.

runs/
    Runtime artifacts. Не входят в Git.
```

---

## 4. Medium-risk state machine

Упрощённо:

```text
TASK ACCEPTED
    ↓
PREFLIGHT
    ↓
REPO CONTEXT / SKILL DISCOVERY
    ↓
CHARACTERIZE + PLAN (read-only)
    ↓
PLAN VALIDATION
    ↓
PRE-EDIT BASELINE SNAPSHOT
    ↓
IMPLEMENT (workspace-write)
    ↓
DETERMINISTIC CHECKS
   / \
 FAIL PASS
  │    │
  ▼    ▼
REPAIR FRESH EVALUATOR
  │       │
  └───┐   ├── PASS ─────→ HELD-OUT/RUNTIME → DONE
      │   ├── FINDINGS ─→ IMPLEMENTER → checks → fresh evaluation
      │   ├── REPLAN_REQUIRED → Planner → validation → fresh evaluation
      │   ├── BLOCKED
      │   └── NEEDS_USER_DECISION
      └──────────────────────────────────────────────────────────────
```

Ключевой contract:

> Любое изменение candidate после review инвалидирует старый review.

---

## 5. Роли

### Planner / Characterizer

Sandbox: `read-only`.

Не пишет patch.

Строит модель:

- current contract;
- assumptions;
- root-cause evidence;
- state writers/readers;
- lifecycle;
- representations;
- authority;
- consumers;
- preservation;
- test matrix;
- release obligations;
- candidate paths.

### Implementer

Sandbox: `workspace-write`.

Получает task + plan + trusted toolchain + baseline snapshot.

Может отклонить техническую гипотезу Planner, если код её опровергает, но не должен самостоятельно изобретать новую product semantics.

### Fresh Evaluator

Sandbox: `read-only`.

Новый thread/context.

Получает task, plan, final workspace, deterministic evidence.

Его задача — **опровергнуть candidate**, а не подтвердить explanation Implementer.

---

## 6. Риск-модель

### Low

Примеры: docs, rename, локальная механика.

```text
Implementer → deterministic checks
```

### Medium

Примеры: UI state, shared helper, multi-file behavior, API contract.

```text
Planner → Implementer → checks → Fresh Evaluator
```

### High

Примеры: cross-system mutations, DB schema, auth, финансовое/бизнес-состояние, deploy.

Текущий quality-core ещё не должен автоматически объявлять такие задачи полностью доказанными без достаточной integration/runtime capability.

Целевая схема:

```text
Planner
→ Implementer
→ deterministic
→ runtime/integration
→ Fresh Evaluator
→ specialist if needed
→ human gate
```

---

## 7. Definition of Done

Для medium-risk:

```text
valid plan
+
all required deterministic checks PASS
+
all release obligations independently evidenced
+
no blocking findings
+
Fresh Evaluator PASS
+
held-out/runtime acceptance PASS, если такой gate определён
```

Agent message не входит в формулу.

---

## 8. App Server transport model

App Server асинхронный JSON-RPC.

Controller поддерживает:

- request matching по `id`;
- separate notification backlog;
- thread/turn lifecycle;
- process-health checks;
- heartbeat;
- `final_answer` semantics.

Нельзя:

```text
склеивать все agentMessage как один structured JSON
```

Потому что turn может иметь commentary + final answer.

Правило:

```text
phase=final_answer
→ authoritative structured response

fallback:
→ last completed agent message
```

---

## 9. Repo context / skills

Harness сканирует repository:

- `AGENTS.md`;
- `.agents/skills/*/SKILL.md`;
- `skills/list` через App Server.

Различать:

```text
skill discovered
!=
skill definitely applied automatically
```

В Codex App Server 0.148.0 `instructionSources` в нашем thread result не давал надёжного подтверждения.

Если конкретный skill должен применяться гарантированно/auditable — его нужно передавать turn'у явно.

---

## 10. Benchmark и production mode

### Historical benchmark

Есть:

- broken historical state;
- held-out grader;
- ранее проверенный known-good результат или calibration certificate.

Цель — оценивать Harness.

Known-good implementation агенту не показывается.

### Production task

Known-good implementation обычно отсутствует.

Confidence строится через:

- characterization;
- reproduction;
- tests;
- preservation;
- consumer/runtime evidence;
- Fresh Evaluator.

`_92` не является обязательной частью реального workflow.

---

## 11. Что пока сознательно не является core architecture

Отложены до следующего этапа:

- GitHub Issues/task supervisor;
- automatic commit/push/PR;
- CI integration;
- browser/Playwright runtime;
- DB/1C/Airflow MCP;
- production read-only connectors;
- deployment automation;
- постоянный запуск шести specialist reviewers.

Причина: сначала требовалось доказать quality-core на реальном historical escape.

Эта цель достигнута на Matrix all-matching benchmark, но одного case недостаточно для универсальной надёжности.


---

## 12. Current evidence-integrity limitation: planned vs actual change surface

Текущий Controller снимает pre-edit snapshot по `Planner.candidate_paths`.

Но на successful Matrix historical run Implementer после собственного consumer discovery изменил дополнительные Distribution paths, которых не было в initial `candidate_paths`.

Controller пока не делает mechanical:

```text
final changed paths
vs
planned candidate_paths
```

reconciliation.

Следствие:

- product candidate может быть корректным;
- Fresh Evaluator может принять расширение;
- но trusted pre-edit evidence для поздно добавленного path отсутствует.

Это зафиксированный hardening gap.

Целевой contract:

```text
actual path outside planned candidate surface
→ explicit expansion/replan/evidence downgrade
```

а не молчаливое принятие.

До реализации этого guard `candidate_paths` нужно считать planning/evidence declaration, а не жёстким filesystem allowlist.
