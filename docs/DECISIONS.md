# Decision log

Этот файл хранит долгоживущие архитектурные решения и отвергнутые альтернативы.

Статусы:

```text
ACCEPTED
REJECTED
DEFERRED
SUPERSEDED
```

---

## D-001 — Harness хранится отдельно от `sa_icover`

**Status:** ACCEPTED

### Решение

Slivin Harness — отдельный repository/user tool.

Project repository остаётся источником:

- product code;
- tests;
- `AGENTS.md`;
- project skills;
- shared documentation.

### Почему

Harness экспериментален и персонален. Его evolution не должен менять team workflow до доказанной зрелости.

### Отвергнуто

Встраивать Harness orchestration прямо в `sa_icover`.

---

## D-002 — Codex App Server вместо управления terminal UI

**Status:** ACCEPTED

### Решение

Controller использует:

```text
codex app-server --stdio
```

и JSON-RPC lifecycle.

### Почему

Нужны authoritative:

- thread lifecycle;
- turn completion;
- structured output;
- notifications;
- health;
- explicit sandbox.

Terminal scraping слишком неоднозначен.

---

## D-003 — Controller владеет Definition of Done

**Status:** ACCEPTED

Agent `PASS` никогда не завершает task сам по себе.

Done определяется checks/evidence/Evaluator/held-out.

### Origin

Первый completion-loop demo: agent считал задачу завершённой после `answer.txt`, но внешний gate требовал ещё `proof.txt`.

---

## D-004 — Базовые роли: Planner → Implementer → Fresh Evaluator

**Status:** ACCEPTED

### Почему

Self-review имеет confirmation bias.

Fresh Evaluator получает результат без необходимости защищать reasoning Implementer.

### Отвергнуто

Один agent, который сам реализует, сам оценивает и сам объявляет PASS.

---

## D-005 — Не запускать шесть specialist reviewers на каждую задачу

**Status:** ACCEPTED

Specialists подключаются risk-based.

### Почему

Несколько ролей с одинаковым context могут иметь correlated blind spot.

Также это увеличивает latency/cost.

### Отвергнуто

Ритуальный запуск QA/Architecture/Security/Ops/Data/Business на каждое изменение.

---

## D-006 — Historical hidden grader не используется как tutor

**Status:** ACCEPTED

`feedback = heldout`:

```text
FAIL → trial stops
```

Assertion не возвращается Implementer в том же trial.

### Origin

v0.2 проходил historical case только после того, как hidden oracle поочерёдно сообщил отсутствующие детали.

Это не было честным доказательством generalization.

---

## D-007 — Не добавлять known-answer hidden test после каждого human miss

**Status:** ACCEPTED

Вместо этого выясняется missing general capability.

### Origin

После v0.2 post-hoc audit нашёл downstream gaps.

Просто добавить tests именно на них означало бы overfit Harness к известному ответу.

---

## D-008 — Characterization before root cause

**Status:** ACCEPTED

Planner обязан разделять:

```text
current contract
assumptions
user requirements
unknowns
```

### Origin

Planner однажды превратил отсутствие `scopeKey` в invalid state без достаточного evidence.

---

## D-009 — Release obligations вместо блокирования на каждом observation

**Status:** ACCEPTED

Не каждый `CC-*` обязан быть release gate.

Blocking claims перечисляются отдельно.

### Origin

Ранняя строгая модель заблокировала trivial README task из-за невозможности постфактум доказать incidental CRLF detail.

---

## D-010 — REP representation-consumer audit

**Status:** ACCEPTED

При расширении representation logical state нужно искать downstream local readers, а не только endpoint compatibility.

### Origin

Matrix selection стал token-based explicit scope, но Distribution продолжал читать только materialized IDs для stage state.

Это был реальный escaped HIGH defect.

---

## D-011 — AUTH authority/precedence audit

**Status:** ACCEPTED

Coexisting state mechanisms должны иметь единый authority contract across surfaces.

### Почему

Опасно, если:

```text
visibility → state A
payload    → state B
```

---

## D-012 — LIFE lifecycle/ownership audit

**Status:** ACCEPTED

State mechanisms классифицируются как:

```text
USER_INTENT
ACTION_LOCAL
DERIVED
CACHE
PERSISTED_SOURCE
EXTERNAL_SOURCE
LEGACY_COMPAT
UNKNOWN
```

### Origin

AUTH audit нашёл конфликт all-matching vs temporary filtered token и сначала вернул `NEEDS_USER_DECISION`.

Lifecycle analysis показал, что один state — global user intent, другой — action-local transient context; это технически разрешимый ownership вопрос, а не product decision.

---

## D-013 — NEEDS_USER_DECISION только для настоящей product semantics

**Status:** ACCEPTED

Planner обязан доказать через `decision_escalations`, что lifecycle/ownership не разрешает конфликт.

### Отвергнуто

Спрашивать пользователя о внутреннем technical precedence только потому, что codebase имеет несколько state variables.

---

## D-014 — Pre-edit baseline snapshot

**Status:** ACCEPTED

После plan и до first edit сохраняется independent path evidence.

### Origin

После implementation невозможно было независимо доказать физическое pre-edit CRLF состояние.

---

## D-015 — Held-out grader проверяет semantic contract

**Status:** ACCEPTED

Exact representation проверяется только если она contract-critical.

### Origin

Historical oracle требовал:

```text
excluded_ids: []
```

хотя:

- backend трактовал absent field как `[]`;
- known-good `_92` тоже опускал поле.

Oracle был over-specified.

---

## D-016 — Oracle calibration + hash-bound certificate

**Status:** ACCEPTED

Historical grader должен быть заранее sanity-checked:

```text
broken → FAIL
known-good → PASS
```

После калибровки можно хранить hash-bound certificate вместо полной `_92`.

### Ограничение

Один known-good не доказывает, что grader принимает все допустимые реализации.

---

## D-017 — Windows sandbox остаётся включённым; admin не требуется

**Status:** ACCEPTED

Используем unelevated sandbox.

### Настройка

```text
sandbox_workspace_write.exclude_slash_tmp=true
sandbox_workspace_write.exclude_tmpdir_env_var=true
```

и task-local temp.

### Отвергнуто

Отключить sandbox либо требовать administrator elevation ради простоты.

---

## D-018 — Trusted toolchain должен быть явным

**Status:** ACCEPTED

Harness знает paths к Node/Jest/etc и делает их legible agent'у.

### Origin

Ранняя версия Implementer утверждала, что Jest недоступен, хотя Harness сам мог его запустить.

---

## D-019 — Harness source Git отделён от task/project Git

**Status:** ACCEPTED / UPDATED BY D-035

Harness repository version-control'ит только Harness.

Task execution использует отдельный project Git context:

- managed mode — linked detached worktree source repository;
- static/legacy mode — отдельный inner Git baseline fixture.

### Почему

Project snapshot/candidate не должен попадать в Harness source history.

Первоначальная формулировка «inner disposable Git внутри `cases/.../workspace`» была
слишком привязана к старому static workflow. D-035 заменил основной execution model
на managed Git worktree.
---

## D-020 — `.env` физически запрещён в agent workspace

**Status:** SUPERSEDED BY D-036

### Исходное решение

Ранний Harness полностью запрещал real `.env*` в agent workspace, потому что
`.gitignore` не является security boundary.

### Почему superseded

Позже был принят более точный contract: visibility local files является explicit
user-controlled trust decision.

Текущий contract определён D-036:

- default — `.env` не экспонируется;
- managed project может opt-in через `copy_untracked`;
- static/legacy fixture может opt-in через `--allow-env`;
- exposed local file не входит в candidate patch/result publication.

Исходный security lesson сохраняется:

> ignored файл всё равно доступен Agent, если физически присутствует в workspace.
---

## D-021 — Git history вместо source-файлов с version suffix

**Status:** ACCEPTED

Одна актуальная версия source.

История — commits/tags/CHANGELOG.

### Origin

`task_runner_v03.py`, `v0.4.6.1.zip` и подобные копии быстро стали источником путаницы.

---

## D-022 — Machine-local paths вынесены из task manifests

**Status:** ACCEPTED

Используется `harness.local.toml`, ignored Git.

### Почему

Clone на другой машине не должен требовать редактировать committed task.

---

## D-023 — PR automation не является первым quality step

**Status:** DEFERRED

### Почему

Автоматический PR с неполным diff не повышает correctness.

Сначала требовался доказанный quality-core.

---

## D-024 — GitHub Issues / task supervisor

**Status:** DEFERRED

Task tracker даст usability/queueing/ownership, но не correctness.

Переход имеет смысл после текущего milestone и документации.

Вопрос будет разобран отдельно.

---

## D-025 — Linear как основной tracker

**Status:** REJECTED для текущего окружения

### Причина

Сервис недоступен из текущего региона.

Также архитектура не должна жёстко зависеть от одного tracker.

Предпочтительный будущий abstraction:

```text
TrackerAdapter
```

первый practical backend — GitHub Issues.

---

## D-026 — Symphony прямо сейчас

**Status:** DEFERRED

Symphony-подобный supervisor полезен для issue lifecycle/workspace/retry/reconciliation, но не должен владеть quality logic.

Сначала требовалось доказать Harness quality-core.

---

## D-027 — MCP / external-system access

**Status:** DEFERRED

Порядок:

```text
typed observation
→ test-write
→ production read-only
→ более сильные mutations только через governed boundaries
```

### Отвергнуто

Сразу дать generic SSH / arbitrary SQL / arbitrary HTTP/OData write.

---

## D-028 — Browser/runtime verification

**Status:** DEFERRED, ожидается важным следующим capability

Для UI, где Jest не доказывает реальный DOM/user flow, понадобится browser/runtime layer.

Не внедрялся до доказательства core quality на historical case.

---

## D-029 — CI

**Status:** DEFERRED

CI рассматривается как clean/repeatable enforcement, а не как способ «сделать тесты умнее».

---

## D-030 — Full `_92` рядом с agent

**Status:** REJECTED для постоянной схемы

Known-good implementation не должен быть доступен agent'у как источник готового fix.

Для historical calibration достаточно controlled reference/certificate.


---

## D-031 — Task agents не владеют Git history/publication по умолчанию

**Status:** ACCEPTED

### Решение

Текущий task-agent может изменять disposable workspace, но без отдельного explicit orchestration contract не должен самостоятельно:

```text
switch/create branches
commit
push
create PR
```

### Почему

`workspace-write` — filesystem capability, а не authority над team Git history.

Это также соответствует текущему разделению:

```text
quality core
vs
publication/orchestration layer
```

### Revisit when

При проектировании GitHub Issues/task supervisor и PR publisher.

---

## D-032 — Actual diff должен reconciled с planned change surface

**Status:** ACCEPTED / ENFORCED

### Решение

Final changed paths должны механически сопоставляться с `Planner.candidate_paths`.

Новый required path не должен появляться незаметно после pre-edit snapshot.

### Origin

В successful Matrix trial Planner не включил Distribution paths в `candidate_paths`, но Implementer корректно обнаружил consumer и изменил:

```text
static/js/distribution/index.js
static/js/distribution/__tests__/selection-stage.test.cjs
```

Pre-edit snapshot существовал только для original planned paths.

### Implemented behavior

После каждого write turn Controller сравнивает actual changed paths с planned surface.

Если path незапланирован:

```text
record violation
→ rollback unexpected path к baseline
→ read-only replan
→ required path явно входит в candidate_paths
→ trusted pre-path-edit snapshot
→ Implementer повторяет изменение
```

Если revised plan исключает старый changed path, Controller откатывает его.

Финальный PASS также имеет machine guard actual diff ⊆ candidate_paths.

---

## D-033 — Harness runtime Python и target-project runtime — разные capabilities

**Status:** ACCEPTED / PARTIALLY IMPLEMENTED

### Решение

Не считать `{python}` универсальным Python target project.

Текущий `{python}` — `sys.executable` Harness process.

Backend tasks при необходимости должны получать отдельный trusted project runtime, например:

```text
project_python
project_pytest
project_test_env
```

### Origin

В successful Matrix run backend token test нельзя было выполнить из workspace/system Python из-за отсутствия Django.

Для той задачи backend code не менялся, поэтому это не было release blocker.

### Current implementation

Project profile может объявить отдельные:

```toml
[projects.my_project.toolchain]
project_python = "{project_root}/.venv/Scripts/python.exe"
project_pytest = "..."
```

Harness bootstrap Python больше не зависит от target-project `.venv`.

Автоматическое discovery project runtimes сознательно не добавлялось: explicit trusted toolchain остаётся более предсказуемым.


---

## D-034 — Machine/project paths живут в `harness.local.toml`, не в source/manifests

**Status:** ACCEPTED / ENFORCED

### Решение

Committed Harness не содержит project-specific defaults для:

```text
Codex path
project repo path
project Python
Node
Jest
```

Machine-local configuration:

```text
harness.local.toml
```

Project profiles:

```toml
[projects.<name>]
repo = "..."

[projects.<name>.toolchain]
project_python = "{project_root}/..."
```

Bare executable names могут разрешаться через PATH.

### Почему не root `.env`

`.env` удобен для плоских environment variables, но плохо описывает:

- несколько проектов;
- nested toolchains;
- workspace/result policies;
- typed lists вроде `copy_untracked`.

TOML уже является существующим configuration layer Harness и не смешивает machine configuration с application secrets.

Bootstrap Python остаётся отдельным environment override `SLIVIN_HARNESS_PYTHON`, потому что TOML нельзя прочитать до запуска Python.

---

## D-035 — Обычные project tasks используют managed Git worktree

**Status:** ACCEPTED / ENFORCED

### Решение

Для manifest:

```toml
project = "my_project"
workspace_mode = "git_worktree"
```

Controller создаёт isolated detached worktree из configured source repository.

### Почему

Устраняется ручной lifecycle:

```text
копировать repo
чистить dependencies
запускать Harness
копировать candidate обратно
```

При этом сохраняются clean baseline и independent diff.

### Static/legacy fallback

Static `workspace = "..."` сохраняется только как fallback для intentionally
materialized filesystem fixtures или source без удобного Git ref.

Текущий Matrix historical `_90` уже использует managed project profile +
`git_worktree`; он больше не копируется в `cases/.../workspace`.

---

## D-036 — `.env` visibility является explicit opt-in, а не абсолютным запретом

**Status:** ACCEPTED / ENFORCED

### Решение

Managed project profile может объявить:

```toml
[projects.my_project.workspace]
copy_untracked = [".env"]
```

Файл копируется в disposable worktree, ignored task Git и исключён из candidate patch.

Static `prepare_workspace.py` по умолчанию остаётся fail-closed, но поддерживает:

```text
--allow-env
```

### Security semantics

Opt-in означает, что Agent/model **может прочитать содержимое** файла.

Это не утверждение, что `.env` безопасен; это explicit user-controlled trust decision.

---

## D-037 — Accepted worktree candidate может автоматически применяться в source working tree

**Status:** ACCEPTED / ENFORCED

### Решение

Project `result_mode`:

```text
keep_worktree
apply_to_source
```

Для `apply_to_source` Controller после полного Harness PASS:

1. создаёт binary Git patch candidate;
2. повторно проверяет source HEAD;
3. проверяет source working tree на concurrent product changes (кроме явно exposed local paths);
4. применяет patch;
5. не commit/push/branch/PR.

### Почему

Убирает ручное копирование результата, но не смешивает quality layer с Git publication authority.
