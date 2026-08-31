# Документация Slivin Harness 0.8.0a8

Актуальная версия: **0.8.0a8 — Phase 5**.

Основные документы:

- [`WORKFLOW.md`](WORKFLOW.md) — понятная Step 0–7 схема, генерируемая из кода;
- [`workflow.v4.json`](workflow.v4.json) — та же state machine в machine-readable виде;
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — ownership, artifacts и связи модулей;
- [`QUALITY_MODEL.md`](QUALITY_MODEL.md) — что именно доказывает каждый слой;
- [`PRACTICAL_GUIDE.md`](PRACTICAL_GUIDE.md) — запуск и чтение artifacts;
- [`WINDOWS_SETUP.md`](WINDOWS_SETUP.md) — Windows/Git Bash setup;
- [`HISTORY.md`](HISTORY.md) — история архитектурных изменений;
- [`PHASE4_EXECUTION.md`](PHASE4_EXECUTION.md) — Implementer/check foundation;
- [`PHASE5_CONTRACT_RUNTIME.md`](PHASE5_CONTRACT_RUNTIME.md) — Contract expansion, `.worktreeinclude` и воспроизводимая `.venv`.

Канонический workflow находится в `slivin_harness/workflow.py`. Generated-файлы создаются командой:

```bash
./py tools/render_workflow_docs.py
```

И проверяются в:

```bash
./py tools/self_check.py
```

Текущие контракты:

```text
task-contract.v1
planner.v4
implementer.v3
implementation-contract.v3
verification-plan.v1
project-runtime.v1
contract-expansion.v1
evaluator.v4
workflow.v4
run-state.v1
candidate.v1
controller-plane.v1
execution-broker.v1
```
