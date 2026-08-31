# Документация Slivin Harness 0.8.0a5

Актуальная версия: **0.8.0a5 — Phase 3**.

Основные документы:

- [`WORKFLOW.md`](WORKFLOW.md) — понятная Step 0–7 схема, генерируемая из кода;
- [`workflow.v2.json`](workflow.v2.json) — та же state machine в machine-readable виде;
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — ownership, artifacts и связи модулей;
- [`QUALITY_MODEL.md`](QUALITY_MODEL.md) — что именно доказывает каждый слой;
- [`PRACTICAL_GUIDE.md`](PRACTICAL_GUIDE.md) — запуск и чтение artifacts;
- [`WINDOWS_SETUP.md`](WINDOWS_SETUP.md) — Windows/Git Bash setup и ограничения;
- [`HISTORY.md`](HISTORY.md) — история архитектурных изменений.

Канонический workflow находится в `slivin_harness/workflow.py`. Файлы `WORKFLOW.md` и `workflow.v2.json` генерируются командой:

```bash
./py tools/render_workflow_docs.py
```

И проверяются в:

```bash
./py tools/self_check.py
```

Phase 3 добавляет:

```text
task-contract.v1
planner.v4
implementation-contract.v3
verification-plan.v1
```

`implementer.v1`, `evaluator.v4`, `run-state.v1`, `candidate.v1`, `controller-plane.v1` и `execution-broker.v1` сохраняются.
