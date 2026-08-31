# Документация Slivin Harness

Актуальная версия: **0.8.0a2 — Phase 1**.

Начните с:

- [WORKFLOW.md](WORKFLOW.md) — генерируемая понятная схема Step 0–7, переходы и invalidation rules;
- [`workflow.v1.json`](workflow.v1.json) — тот же workflow в machine-readable форме;
- [ARCHITECTURE.md](ARCHITECTURE.md) — как Phase 1 встроена в код;
- [QUALITY_MODEL.md](QUALITY_MODEL.md) — что новая state machine реально доказывает и чего ещё не доказывает;
- [PRACTICAL_GUIDE.md](PRACTICAL_GUIDE.md) — как запускать Harness и читать `run_state.json`;
- [WINDOWS_SETUP.md](WINDOWS_SETUP.md) — установка и Windows-диагностика;
- [HISTORY.md](HISTORY.md) — история упрощения и дальнейший маршрут.

Канонический workflow находится в `slivin_harness/workflow.py`. `docs/WORKFLOW.md` и `docs/workflow.v1.json` генерируются из него.

Проверка актуальности:

```bash
./py tools/render_workflow_docs.py --check
./py tools/check_docs_sync.py
```

Код, manifests и self-check остаются источником текущей исполняемой capability. В документации явно разделены уже реализованная Phase 1 foundation и target-контракты следующих фаз.
