# Slivin Harness — документация проекта

Эта директория хранит **архитектурные знания и причины решений**, а не только инструкции запуска.

Цель: после смены чата, машины или разработчика ключевой контекст Harness должен восстанавливаться из репозитория, а не из памяти участников.

## Слои документации

| Файл | Назначение |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Текущая архитектура Harness, роли, state machine и границы ответственности |
| [QUALITY_MODEL.md](QUALITY_MODEL.md) | Модель качества: current contract, assumptions, LIFE/REP/AUTH, obligations, Evaluator, held-out |
| [DECISIONS.md](DECISIONS.md) | Принятые, отклонённые и отложенные архитектурные решения с причинами |
| [HISTORY.md](HISTORY.md) | История развития и реальные failures, которые породили текущие механизмы |
| [WINDOWS_SETUP.md](WINDOWS_SETUP.md) | Проверенная конфигурация Windows/Git Bash/Codex sandbox |
| [WORKSPACE_MODEL.md](WORKSPACE_MODEL.md) | Outer/inner Git, disposable workspace, secrets, temp, snapshots и run artifacts |
| [MAINTAINING_HARNESS.md](MAINTAINING_HARNESS.md) | Как дальше изменять Harness и не потерять причины решений |
| [DECISION_TEMPLATE.md](DECISION_TEMPLATE.md) | Шаблон новой архитектурной записи |

## Что относится к другим слоям

Корневой `README.md` — operational onboarding: что запустить, какие скрипты существуют, как развернуть Harness.

`CHANGELOG.md` — краткая история пользовательски/архитектурно значимых milestone.

`docs/` — **почему система устроена именно так**, какие альтернативы рассматривались и какие реальные failures подтверждают необходимость механизма.

## Правило обновления

При каждом существенном изменении Harness ответьте в репозитории на четыре вопроса:

1. Какой observable failure или missing capability обнаружен?
2. Почему существующая архитектура его не поймала?
3. Какое общее, а не case-specific изменение принято?
4. Каким eval/benchmark доказано улучшение?

Если ответ меняет архитектуру или долгосрочный contract — обновить `DECISIONS.md` или создать запись по `DECISION_TEMPLATE.md`.

Если ответ относится к эксплуатации Windows/workspace — обновить соответствующий operations-документ.

Если меняется milestone — обновить `HISTORY.md` и `CHANGELOG.md`.

## Принцип

Документация Harness должна объяснять не только:

> «Что сейчас есть в коде?»

но и:

> «Почему это появилось, какой failure это предотвращает и почему мы не выбрали более простой/другой путь?»
