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

slivin_harness/workspace.py
    Managed project profiles/worktrees, local-file exposure, candidate patch/apply.

tools/prepare_workspace.py
    One-time preparation helper для static/legacy fixture без готового Git baseline.

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
CHANGE-SURFACE RECONCILIATION (D-032)
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

## 12. D-032: planned vs actual change surface — machine-enforced

`Planner.candidate_paths` теперь является не справочным списком, а planned change surface.

После каждого Implementer/repair turn Controller сравнивает:

```text
actual changed paths
vs
plan.candidate_paths
```

Если найден новый path:

```text
unexpected edit
    ↓
Controller records violation
    ↓
rollback только unexpected path к trusted baseline
    ↓
read-only change-surface replan
    ↓
если path нужен → добавить в candidate_paths
    ↓
снять trusted pre-path-edit snapshot
    ↓
Implementer повторяет необходимое изменение
```

Если revised plan исключает ранее изменённый path, Controller также возвращает его к baseline.

Deterministic checks не обходят этот gate: если check сам создаёт новый non-ignored candidate path, цикл возвращается в reconciliation до Fresh Evaluator.

Перед финальным PASS Controller повторно проверяет отсутствие changed paths вне planned surface.

### Evidence semantics

Baseline snapshot теперь различает:

```text
captured_before_first_edit
captured_before_path_edit
worktree_snapshot_role
```

Поздно найденный consumer может получить честное:

```text
captured_before_first_edit = false
captured_before_path_edit  = true
role = pre_path_edit_after_surface_reconciliation
```

То есть Harness больше не притворяется, что весь task ещё pre-edit, но сохраняет trusted evidence именно для нового path до повторного изменения.

---

## 13. Managed project workspace: Git worktree вместо копирования repository

Для обычной project development Harness больше не требует:

```text
copy repository
→ delete .venv/node_modules/.env
→ run
→ copy candidate назад вручную
```

Task manifest указывает logical project name:

```toml
project = "my_project"
workspace_mode = "git_worktree"
```

Machine-specific source path хранится только в ignored:

```text
harness.local.toml
```

Controller создаёт detached Git worktree из committed source `HEAD`.

### Почему worktree

Он даёт одновременно:

- clean known baseline;
- independent task diff;
- отсутствие user dirty state;
- отсутствие тяжёлого копирования `.venv`/`node_modules`;
- возможность discard failed candidate;
- shared Git objects с source repo.

### Local/untracked exposure

Project profile может opt-in скопировать отдельные ignored/untracked paths:

```toml
[projects.my_project.workspace]
copy_untracked = [".env"]
```

Они доступны Agent, но excluded из task Git status/candidate patch.

Default остаётся opt-in: `.env` не экспонируется автоматически.

### Result publication

Два режима:

```text
keep_worktree
apply_to_source
```

`apply_to_source` после полного Harness PASS:

1. строит binary candidate patch, включая новые non-ignored files;
2. проверяет, что source `HEAD` не изменился;
3. проверяет отсутствие новых source changes, кроме configured local exposures;
4. применяет patch в исходный working tree;
5. не делает commit/push/branch/PR.

Таким образом quality/execution layer остаётся отделён от будущего Git publication/task supervisor.

---

## 14. Portable configuration model

Harness source больше не привязан к `sa_icover/.venv`, конкретному portable Node или конкретному Jest path.

Bootstrap Python:

```text
SLIVIN_HARNESS_PYTHON
→ python3
→ python
→ py -3
```

Codex:

```text
SLIVIN_CODEX_CMD
→ [codex].command
→ codex.cmd/codex from PATH
```

Project/tool paths:

```text
harness.local.toml
[projects.<name>.toolchain]
```

Поддерживается `{project_root}`.

Это позволяет одному Harness repository обслуживать несколько проектов и несколько машин без редактирования committed manifests/source.


---

## 13. Cross-stage protocol

Strict Planner/Controller/Implementer/Evaluator handoff описан в `HANDOFF_PROTOCOL.md`.
Planner protocol `planner.v2` не содержит свободного cross-reference списка obligations; Controller owns exact ledger and plan fingerprint. Evaluator protocol `evaluator.v2` bound к exact current IDs/fingerprint.
