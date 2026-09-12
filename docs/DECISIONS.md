# Журнал инженерных решений Slivin Harness

> Читать после [AUTONOMOUS_ENGINEERING_CONTRACT.md](AUTONOMOUS_ENGINEERING_CONTRACT.md) и до проектирования изменений. Здесь хранится не только выбранная архитектура, но и причины выбора, отвергнутые варианты, принятые издержки и границы доказанного.

**Дата ретроспективной записи:** 2026-09-08. Это дата регистрации, а не придуманная дата принятия каждого исторического решения.

**Проверенный срез репозитория:** `256f9e789b4b33d13431fa36b8e178380c959e46`, Harness `0.8.0a28`.

**Текущая сверка:** исходный срез сохранён; дополнения версий `0.8.0a29` и `0.8.0a30` отмечены явно. Перечисленные short commit IDs сверены с локальной историей Harness; исторические product fixes не открывались. Ниже ссылки на код/tests удостоверяют наличие механизма, а не повторное исполнение каждого исторического trial.

**Основания:** явные решения владельца в рабочем обсуждении, [CHANGELOG](../CHANGELOG.md), [HISTORY](HISTORY.md), нормативный контракт, тематическая документация, упомянутые ниже commits и сохранённые материалы испытаний. Наличие реализации и её описания не приравнивается к независимо пройденному end-to-end испытанию.

**Назначение:** новый разработчик, Codex или новый чат должен понять, какую проблему решали, что выбрали, почему не выбрали альтернативы и какие факты могут обосновать пересмотр. Журнал не является инструкцией с готовым решением какой-либо продуктовой benchmark-задачи.

## Как читать статусы

Статус решения и состояние реализации — разные поля.

- **ACCEPTED** — выбранное действующее решение.
- **REJECTED** — рассмотренный, но отвергнутый подход; это не означает, что его успели внедрить.
- **SUPERSEDED** — прежнее решение или вывод заменены; обязательно указать заменяющую запись и основание.
- **DEFERRED** — отложено, а не отвергнуто навсегда.

Состояние реализации: **IMPLEMENTED**, **PARTIAL**, **NOT_IMPLEMENTED** или **NOT_VERIFIED**. Отдельно записывается уровень проверки: review кода, unit/integration, Controller smoke, настоящий role sandbox, полный trial. Не объединять эти уровни одним словом PASS.

Записи ниже имеют ACCEPTED, если явно не указано другое. Перечисленные внутри них отвергнутые варианты имеют локальный статус REJECTED с указанной причиной.

## Правило изменения решений

Не возвращаться к отвергнутой альтернативе только потому, что она проще, выглядит привычнее или была забыта. Пересмотр допустим при новых проверенных фактах, изменении исходного требования владельцем либо доказанной несостоятельности текущего решения. Сначала указать номер записи, новое основание и последствия для зависимых механизмов.

Не переписывать прошлое так, будто ошибочного вывода никогда не было. При замене оставить прежний вывод, пометить SUPERSEDED, связать с новым и объяснить, какое предположение оказалось неверным. При этом действующая документация должна описывать актуальное поведение, а не одновременно рекомендовать старый и новый варианты.

## Карта решений

| ID | Решение | Состояние на срезе 0.8.0a28; обновления отмечены |
| --- | --- | --- |
| [D-001](#d-001) | Пользователь задаёт результат, агент самостоятельно исследует последствия | IMPLEMENTED; качество автономности проверяется испытаниями |
| [D-002](#d-002) | Существующие роли и Controller вместо новых дублирующих модельных ролей | IMPLEMENTED |
| [D-003](#d-003) | Прямое сохранение пользовательского intent | IMPLEMENTED |
| [D-004](#d-004) | Planner impact closure и независимое основание исключения | IMPLEMENTED |
| [D-005](#d-005) | Post-patch closure, расширение Contract и повторная проверка | IMPLEMENTED |
| [D-006](#d-006) | Независимый двухфазный Evaluator | IMPLEMENTED |
| [D-007](#d-007) | Обязательная доставка связанных out-of-scope находок | IMPLEMENTED |
| [D-008](#d-008) | Source-owned Node/Jest через независимую физическую копию | IMPLEMENTED |
| [D-009](#d-009) | Полная проверка projected runtime перед/после trusted batch | IMPLEMENTED; discrete detect/restore |
| [D-010](#d-010) | Разрешённые runtime-файлы, включая .env, не являются результатом patch | IMPLEMENTED; минимизация доступа — отдельный долг |
| [D-011](#d-011) | Project Python и Harness Python не взаимозаменяемы | IMPLEMENTED |
| [D-012](#d-012) | Физический candidate, защищённый Git state, безопасный restore | IMPLEMENTED |
| [D-013](#d-013) | Controller владеет проверками, receipts и authoritative evidence | IMPLEMENTED |
| [D-014](#d-014) | Одинаковый candidate должен пройти reconstruction и повторные проверки | IMPLEMENTED; реальный полный успешный trial не следует из unit tests |
| [D-015](#d-015) | Benchmark проверяет результат, а не подсказывает агенту решение | IMPLEMENTED / действующая политика испытаний |
| [D-016](#d-016) | Возможности инструментов подтверждаются и обновляются до Planner | IMPLEMENTED |
| [D-017](#d-017) | Семантическое требование и способ его доказательства различаются | IMPLEMENTED; трактовка конкретных suite требует causal analysis |
| [D-018](#d-018) | Проект read-only, собственный scratch writable | На исходном срезе PARTIAL; IMPLEMENTED в 0.8.0a29, подтверждён native Windows acceptance |
| [D-019](#d-019) | Strict wire schema не отменяет status-dependent semantics | IMPLEMENTED |
| [D-020](#d-020) | Активная работа, ошибки транспорта и результаты тестов не смешиваются | IMPLEMENTED |
| [D-021](#d-021) | Идентичность сборки, платформы и уровни доказательств явные | IMPLEMENTED / действующая review-политика |
| [D-022](#d-022) | Прямые consumers и синхронизация docs входят в полный patch | Действующая политика разработки |
| [D-023](#d-023) | Исправленные выводы сохраняются, а не превращаются в вечные правила | Действующая политика; реестр ниже |
| [D-024](#d-024) | Существенное решение включает rationale и rejected alternatives | IMPLEMENTED в 0.8.0a29 как documentation policy; links и structural check |
| [D-025](#d-025) | Framework-aware trusted runner и replay | IMPLEMENTED в 0.8.0a30 |
| [D-026](#d-026) | Bounded report-only evidence correction | IMPLEMENTED в 0.8.0a30 |
| [D-027](#d-027) | Immutable origins, recovery с progress и обязательная квалификация сборки | IMPLEMENTED в 0.8.0a31; qualification определяется отдельным release record |
| [D-028](#d-028) | Owner check inputs и production toolchain остаются властью Controller | IMPLEMENTED; qualification конкретного SHA ещё требуется |
| [D-029](#d-029) | Model wire ссылается на Controller origins, local correction отделена от semantics | IMPLEMENTED в 0.8.0a32; qualification конкретного SHA ещё требуется |

<a id="d-001"></a>

## D-001. Автономность — самостоятельное исследование, а не выполнение подсказанного diff

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Правдоподобный локальный fix, зелёные видимые тесты и согласие предыдущих ролей не гарантировали проверки связанных consumers. Владелец прямо отверг перенос этой исследовательской работы на пользователя.

**Решение.** Пользователь сообщает симптом, требуемое observable поведение и известные ограничения. Агент сам устанавливает root cause, изменяемый shared contract/state, writers, readers и существенных consumers. Минимальное полное решение выбирается после анализа влияния. `IN_SCOPE` проверяется и исправляется сейчас; `NOT_AFFECTED` требует доказательства; `RELATED_OUT_OF_SCOPE` сохраняется для пользователя. Нормативное состояние «consumer не затронут» означает inspected/verified unaffected, а не «мы туда не смотрели».

**Отвергнуто и почему.** Требовать от пользователя перечень файлов, edge cases или скрытых дефектов — лишает агента его назначения. Считать scope равным названному файлу или видимой кнопке — игнорирует последствия общей причины. Расширять задачу на весь repository без causal связи — тоже неверно.

**Последствия и цена.** Исследование может быть шире patch и требовать больше времени. Explicit product intent и owner boundaries при этом не расширяются самовольно.

**Реализация / доказательства.** `2d97b73`, `59ab73d`, `b94e894`; [AUTONOMOUS_ENGINEERING_CONTRACT](AUTONOMOUS_ENGINEERING_CONTRACT.md), generic impact tests. Structured ledger проверяет полноту перечисления и согласованность, но не доказывает, что модель фактически нашла всех consumers.

**Пересмотр.** Изменение роли пользователя возможно только явным изменением цели владельцем; не из-за очередного провала benchmark.

<a id="d-002"></a>

## D-002. Не добавлять модельную роль для каждого нового требования качества

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** [HISTORY](HISTORY.md) фиксирует, что дополнительные prose layers и отдельный Impact Auditor увеличивали объём работы, не гарантируя корректность.

**Решение.** Полноту исследования обеспечивают существующие Planner, Implementer и независимый Evaluator; Controller выполняет детерминированные проверки, связывает artifacts и управляет переходами. FULL и FAST сохраняют разные обещания: отсутствие Evaluator в FAST обозначается явно.

**Отвергнуто и почему.** Ещё один похожий модельный аудитор не считается исправлением сам по себе. С другой стороны, прежний отказ от дублирующих layers не означает запрета на содержательные obligations в существующих ролях.

**Последствия и цена.** Нужны проверяемые handoff-контракты и независимость Evaluator. Ни схема JSON, ни число ролей не дают гарантию истины.

**Реализация / доказательства.** Историческое решение 0.6.x; нынешние impact layers 0.8.0a21–a26. См. [HISTORY](HISTORY.md), [WORKFLOW](WORKFLOW.md).

**Пересмотр.** Новая роль потребует доказанной самостоятельной функции, которую нельзя закрыть существующим разделением ответственности; не просто ещё одного отрицательного trial.

<a id="d-003"></a>

## D-003. Пользовательский intent нельзя передавать только через пересказ Planner

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** При последовательных пересказах explicit acceptance, preservation и ограничения могут незаметно исчезнуть или измениться.

**Решение.** Хранить raw request и источник нормализованных claims; User Task Contract — authority для explicit intent. Technical mapping добавляется отдельно. Controller компилирует обязательства, не отдавая ownership над пользовательской целью Planner или Implementer.

**Отвергнуто и почему.** Заменять исходный запрос планом реализации — позволяет технической гипотезе менять критерии успеха. Подменять runtime proof зелёными unit-тестами — не доказывает требуемое runtime-поведение.

**Последствия и цена.** Больше явных связей между claims, Contract и evidence; несовместимость с реальным owner boundary требует честного разрешения, а не обхода.

**Реализация / доказательства.** Phase 3 / `task-contract.v1`, `implementation-contract.v3`, `verification-plan.v1`; [HISTORY](HISTORY.md), [PHASE5_CONTRACT_RUNTIME](PHASE5_CONTRACT_RUNTIME.md).

**Пересмотр.** Только при явном изменении product intent владельцем; техническая удобность не является основанием.

<a id="d-004"></a>

## D-004. Planner READY требует impact closure; исключение не выдаётся самому себе

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Одна строка «shared consumers» могла формально закрыть поле плана. Первоначальная схема также позволяла Planner объявить impact analysis неприменимым на основании собственных очищенных declarations.

**Решение.** `planner.v5` требует concrete contracts, paths, symbols, scope classifications, search evidence и двунаправленное соответствие `IN_SCOPE ↔ affected_consumers`. `applicable=false` разрешён только при независимой Controller-owned prose-only boundary и остальных ограничениях исключения.

**Отвергнуто и почему.** Доверять самому Planner решение «анализ не нужен» — циклическое доказательство. Ограничивать список material consumers ради компактности — может удалить реальное обязательство. Фабриковать consumers для простой редакторской задачи — тоже неверно.

**Последствия и цена.** Консервативное исключение может требовать малого applicable closure даже для текстовой задачи без owner boundary. Проверка paths/symbols не доказывает истинность semantic evidence.

**Реализация / доказательства.** `2d97b73`, `53457e2`; `slivin_harness/planner.py`, `tests/test_planner_impact_closure.py`.

**Пересмотр.** Более удобное исключение допустимо только с независимым основанием, сохраняющим отсутствие self-authorization.

<a id="d-005"></a>

## D-005. Implementer пересматривает impact по фактическому patch

> Часть текущего механизма заменена D-027 в 0.8.0a31; исходная запись ниже сохранена как историческое основание.

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** План — гипотеза до реализации; реальное изменение может затронуть новых consumers или опровергнуть NOT_AFFECTED. Добровольного `discovered_obligations` недостаточно.

**Решение.** Перед COMPLETE обязательны post-patch sweep, exact changed-path review и reconciliation с Planner. Новые material consumers/risks расширяют Contract и Verification Plan через Controller; тот же Implementer закрывает новые items и проходит свежую self-verification. Смена технической модели требует REPLAN_REQUIRED, а не тихого исправления Contract агентом.

**Отвергнуто и почему.** «Закрыл исходный список и мои тесты зелёные» не доказывает полный impact. Каждый новый consumer автоматически отправлять в отдельную пользовательскую задачу — уводит in-scope работу из исходной цели. Каждый discovery превращать в полный replan — смешивает добавление обязательства со сменой root-cause модели.

**Последствия и цена.** Изменение candidate или Contract делает прежний impact artifact и receipt устаревшими; expansion должен быть idempotent. Deleted paths поддерживаются без требования существования удалённого файла.

**Реализация / доказательства.** `59ab73d`; `implementation-impact-closure.v1`, `slivin_harness/implementer.py`, generic Implementer/expansion tests.

**Пересмотр.** Только при доказанном конфликте схемы с корректным workflow; не путём добровольного отказа от post-patch sweep.

<a id="d-006"></a>

## D-006. Evaluator сначала строит независимую модель, затем проверяет предыдущие

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Согласованные Planner/Implementer ledgers и зелёные тесты способны закрепить одну ошибочную модель. Пустой `findings=[]` не доказывает полноту review.

**Решение.** Fresh Evaluator Phase A исследует actual repository/diff без prior ledgers и check framing. Валидированный blind audit сохраняется до раскрытия Phase B. Затем Evaluator disposition-ит contracts, consumers, NOT_AFFECTED, related findings и changed paths; negative disposition требует material finding и несовместима с PASS. Blind contract MATERIAL_GAP/MODEL_CONFLICT требуют REPLAN_REQUIRED.

**Отвергнуто и почему.** Показать prior solution, а затем попросить «проверять независимо» — не сохраняет blindness. Согласие имён не является независимым доказательством. Обычный repair на существенно неверной technical model оставляет источник ошибки неизменным.

**Последствия и цена.** Два этапа review, immutable persistence и привязка к candidate. После repair нужен fresh audit. Generic agent-double тест доказывает routing, но не способность реальной LLM находить все пропуски.

**Реализация / доказательства.** `b94e894`, `0badb85`; `blind-audit.v2`, `evaluator.v6`, `tests/test_evaluator_impact_challenge.py`.

**Пересмотр.** Можно улучшать формат evidence, но не раскрывать Phase B до фиксации независимой Phase A.

<a id="d-007"></a>

## D-007. Связанные out-of-scope находки обязательно получает пользователь

> Часть текущего механизма заменена D-027 в 0.8.0a31; исходная запись ниже сохранена как историческое основание.

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Находка в промежуточном JSON не является доставленным результатом; пользователь не обязан искать её во внутренних artifacts.

**Решение.** Controller строит `user-follow-up.v1` по текущей модели до held-out, даже при нуле находок. FULL требует подтверждения Evaluator; FAST честно маркируется как Implementer-only. Exact duplicates объединяются с provenance. Final Acceptance связывает отчёт с candidate и источниками. Последующий semantic/delivery failure не удаляет уже сформированный handoff.

**Отвергнуто и почему.** Молчаливое отсутствие исправления — теряет полезный результат. Свалить текущий IN_SCOPE дефект в follow-up — обход выполнения задачи. Смешивать findings отвергнутых attempts без повторной проверки — может вернуть уже неверную модель. Дедуплицировать только по названию — может слить разные проблемы.

**Последствия и цена.** Нужны binding/stale checks и понятный suggested next task. FAST не выдаётся за independently reviewed FULL.

**Реализация / доказательства.** `83afae0`; `slivin_harness/handoff.py`, `final-acceptance.v3`, [PHASE7_FINAL_GATE](PHASE7_FINAL_GATE.md).

**Пересмотр.** Cross-attempt accumulation отложен; потребует отдельной проверки актуальности старых findings, а не чтения всех прошлых JSON подряд.

<a id="d-008"></a>

## D-008. Node/Jest принадлежат source environment; worktree получает физическую копию runtime

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Владелец требует разрабатывать и проверять проект в согласованной среде; отдельная установка зависимостей для каждого worktree может дать другие версии. Historical isolation запрещает произвольное исполнение source-local paths.

**Решение.** Разрешённый source-owned runtime, например `node_modules`, физически копируется в managed workspace; Controller фиксирует provenance и rebind toolchain к копии. Внешний Node используется согласно configured toolchain. Несанкционированные source-local entries удаляются fail-closed.

**Отвергнуто и почему.** Новый `npm install`/`npm ci` как универсальное решение missing Jest меняет среду и не решает provenance. Общая writable ссылка на source runtime даёт aliasing и риск изменения source. Исполнение из посторонней копии проекта нарушает historical isolation.

**Последствия и цена.** Копирование расходует диск/время; runtime не входит в candidate/patch/delivery. Отсутствие fresh install само по себе не доказывает package-lock coherence.

**Реализация / доказательства.** `e6e7984`, `27c18d6`; `workspace.copy_untracked`, `benchmark-toolchain-sanitization.v2`, [CHANGELOG](../CHANGELOG.md).

**Пересмотр.** Cache или иной способ материализации допустим только при сохранении подтверждённых версий, физической/правовой независимости от source и проверяемого provenance. Сейчас это не основание ставить новый Jest.

<a id="d-009"></a>

## D-009. Проверять весь projected runtime, а не один launcher

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Исключение runtime из candidate identity не должно позволять подменить тестовую среду и получить ложный PASS.

**Решение.** Private baseline покрывает пути, типы, содержимое файлов и empty directories полного projection. Перед trusted batch расхождение копии восстанавливается из неизменного source; source mismatch останавливает работу. Mutation во время batch инвалидирует результат независимо от exit code; restore не превращает этот attempt в PASS.

**Отвергнуто и почему.** Одна независимая dev-копия без проверки целостности допускает незаметную подмену. Хешировать только `jest.js` недостаточно из-за зависимостей. Проверять другую среду, чем использовал разработчик, без reconciliation — даёт несогласованные доказательства.

**Последствия и цена.** O(total projected bytes) на full-tree proof; discrete detect/restore не равен OS-level immutability и не исключает все TOCTOU. Универсальная immutability/monitoring отложена, а не объявлена реализованной.

**Реализация / доказательства.** `27c18d6`, runtime projection tests, [CHANGELOG](../CHANGELOG.md).

**Пересмотр.** Оптимизация допустима после доказательства эквивалентного trust boundary; не просто потому, что полный обход дорог.

<a id="d-010"></a>

## D-010. `.env` и прочие явно разрешённые runtime-файлы — вход, а не скрытая часть решения

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Worktree может нуждаться в локальной среде проекта. Владелец выбрал repository policy `.worktreeinclude`; отбор минимально нужных secrets для каждой задачи оставлен отдельной последующей работой.

**Решение.** Копировать только пути, разрешённые действующей repository/local policy. `.worktreeinclude` — явный opt-in владельца; `copy_untracked` сохраняет свои sensitive opt-in правила. Runtime-файлы исключаются из candidate, сравниваются private HMAC, при изменении восстанавливаются из unchanged source с новой verification. Reconstruction получает pristine inputs, а не изменённые файлы candidate workspace.

**Отвергнуто и почему.** Изменить `.env`, чтобы тесты стали зелёными, и не включить это в patch — невоспроизводимый результат. Публиковать secrets или их повторно используемые fingerprints — не evidence для пользователя. Эта запись не разрешает копировать любые secrets без policy.

**Последствия и цена.** Доступ модели к явно переданным runtime inputs остаётся реальным фактором риска; минимизация доступа — осознанный долг, а не реализованная функция.

**Реализация / доказательства.** Phase 5; [PHASE5_CONTRACT_RUNTIME](PHASE5_CONTRACT_RUNTIME.md), exposed runtime snapshot/restore.

**Пересмотр.** При реализации per-task least-privilege доступа сохранить работоспособность среды и доказать отсутствие нового скрытого runtime dependency.

<a id="d-011"></a>

## D-011. Не подменять project Python интерпретатором Harness

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Проверка не в той Python-среде может пройти/упасть по другой причине. Canonical resolution POSIX venv entrypoint также может непреднамеренно запустить bootstrap interpreter.

**Решение.** `{python}`: project Python → configured Python → Harness fallback по установленному контракту. Явный `{project_python}` не имеет молчаливого fallback; `{harness_python}` обозначает служебный interpreter. Configured ProjectRuntimeManager создаёт worktree-local venv и проверяет binding; lexical POSIX venv leaf сохраняется для исполнения.

**Отвергнуто и почему.** Безусловный `sys.executable` для project checks подменяет среду. Ненужная venv для каждой JS-задачи не является обязательной capability. Копирование произвольной mutable source venv не равно воспроизводимому runtime bootstrap.

**Последствия и цена.** Runtime rebuild инвалидирует evidence. Windows/POSIX различия должны проверяться структурно, а не сравнением строкового префикса.

**Реализация / доказательства.** Phase 5, `8aab2a6`, `517f9ed`; [PHASE5_CONTRACT_RUNTIME](PHASE5_CONTRACT_RUNTIME.md).

**Пересмотр.** Новый способ materialization требует сохранения version/dependency/binding semantics; не изменения значения placeholder ради одного PASS.

<a id="d-012"></a>

## D-012. Candidate определяется физическими файлами; Git control state не является доверенным списком изменений

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Mutable ignore rules, index flags и metadata способны скрывать файлы от `git status/diff`. Restore по пути из уже изменённой config способен повредить не тот объект.

**Решение.** Physical candidate baseline с explicit Controller exclusions; tracked collision отклоняется. Git control state проверяется отдельно. Restore использует original fixed paths и ownership policy; shared/source/external controls — detect-only. Refs/object lookup controls проверяются bounded scans. Patch и trusted checks используют disposable indexes, не меняя real index.

**Отвергнуто и почему.** `git status` как единственная authority допускает hidden changes. Wildcard исключение любого `coverage`/cache-path может скрыть tracked source. Текущая mutated config не может выбирать destination restore. Безусловно восстанавливать source/common Git controls опасно. Ослабить freeze real index из-за служебных Git writes — не нужно, когда есть isolated index.

**Последствия и цена.** Сложнее inventory и ownership; нельзя заявлять неизменность всего object database или global/system Git config. Unreferenced object dependency дополнительно проверяется clean reconstruction.

**Реализация / доказательства.** `517f9ed`, `792882d`, `1ec6c1d`; `git_integrity.py`, `run_state.py`, `workspace.py`.

**Пересмотр.** Только с доказанным эквивалентом physical completeness и безопасной ownership-моделью.

<a id="d-013"></a>

## D-013. Только Controller выдаёт authoritative evidence и receipts

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Agent-provided «тест прошёл», writable JSON или произвольная команда не могут сами удостоверять quality gate.

**Решение.** Controller владеет typed check registry, обязательными проверками, revision/candidate-bound receipts и private authoritative artifacts. Agent self-verification — development feedback до требуемого Controller confirmation. Ссылки на проверки должны разрешаться в executable trusted specs. Raw probe output хранится private и bounded.

**Отвергнуто и почему.** Agent-minted receipt превращает самозаявление в authority. Зарегистрировать непроверяемый check ID — ложное доказательство. Считать evidence от прежнего candidate актуальным — смешивает разные результаты. Публиковать полный private output — может раскрыть secrets или hidden данные.

**Последствия и цена.** Изменение Contract/check registry/runtime инвалидирует downstream evidence даже без изменения source bytes. Сохранение приватности не отменяет user-facing typed diagnostics.

**Реализация / доказательства.** Phases 2/4/5, `8aab2a6`, `3f380aa`, `27c18d6`; [ARCHITECTURE](ARCHITECTURE.md), [PHASE4_EXECUTION](PHASE4_EXECUTION.md).

**Пересмотр.** Изменение формата допускается; ownership доказательства не передаётся проверяемому агенту.

<a id="d-014"></a>

## D-014. Принимается реконструируемый candidate, а не только успешный рабочий каталог

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** PASS может зависеть от runtime/temp state, не попадающего в patch. Даже корректный patch может дать другие worktree bytes при иной checkout conversion policy.

**Решение.** Final Gate связывает evidence с одним candidate/revision vector. На clean baseline применяет patch, проверяет identity, заново готовит authoritative runtime и повторяет active repair/held-out проверки. Checkout conversion переносится ограниченным allowlist, без произвольных hooks/config. Качество результата отделено от physical delivery с source preimage checks и safe rollback.

**Отвергнуто и почему.** Проверить только identity reconstruction без повторного исполнения — не выявляет все скрытые зависимости. Копировать mutable runtime candidate в proof repo — переносит их вместе с кандидатом. Безусловный `reset --hard` source при delivery failure — недопустим. Original held-out FAIL не требует продолжать acceptance packaging.

**Последствия и цена.** Дополнительный runtime bootstrap и replay стоят времени. Если original held-out не прошёл, reconstruction закономерно не запускается; это не её успешная проверка.

**Реализация / доказательства.** Phases 7/a11, `792882d`, `83afae0`; [PHASE7_FINAL_GATE](PHASE7_FINAL_GATE.md). Unit/integration и прошедший оригинальный pipeline — разные уровни evidence.

**Пересмотр.** Оптимизация только при сохранении proof одного доставляемого candidate и отсутствия зависимости от отвергнутого scratch.

<a id="d-015"></a>

## D-015. Не упрощать экзамен после неполного решения агента

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** В обсуждении предлагалось дописать конкретные пропущенные scenarios/consumers в открытую задачу и сменить case после серии неудач. Владелец отверг это как подмену цели автономного discovery.

**Решение.** Для текущего эксперимента сохранять исходный task, baseline, oracle и calibration. Источник решения — только разрешённый workspace. Historical mode материализует изолированный baseline без доступа к reference commits/previous candidates. Hidden проверка измеряет observable correctness и не становится repair-feedback того же trial.

**Отвергнуто и почему.** Подсказать агенту hidden consumers или reference fix — меняет измеряемое свойство. Связать historical worktree с доступной fixed history — создаёт утечку. Объявлять любую неудачу oracle доказательством «тест слишком строгий» — не причинный анализ.

**Последствия и цена.** Honest semantic fail остаётся полезным отрицательным результатом. Но исторически исправленный implementation-biased oracle не запрещает вообще когда-либо исправлять invalid benchmark: это допустимо только при независимом доказательстве его несоответствия intended task, с новой версией и calibration, без ретроактивного изменения результатов.

**Реализация / доказательства.** [HISTORY](HISTORY.md): outcome-based calibration и historical isolation; `c4fb0d4`; нормативный anti-coaching contract и явное отклонение Matrix v2 coaching в обсуждении.

**Пересмотр.** Требуется доказательство invalid/out-of-scope acceptance criterion, а не само по себе 0/N успехов.

<a id="d-016"></a>

## D-016. Устаревшее подтверждение инструмента требует refresh, а не объявления инструмента отсутствующим

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Planner требовал неподтверждённые capabilities; затем реальный replan потерял JEST, потому что candidate invalidation удалила evidence, а fresh Planner запускался до refresh.

**Решение.** Static preflight до дорогих model stages; только probe-backed capabilities. Planner получает available set и один corrective capability turn до compilation. Post-plan/dynamic gates сохраняются. Initial/fresh Planner использует один маршрут: current binding, завершённый reset/rebuild, stale probes, проверка результата, затем available set. Descriptor позволяет проверить инструмент, но не заменяет PASS.

**Отвергнуто и почему.** Конфигурационный путь/boolean не доказывает доступность. Просто вернуть JEST в set или отключить invalidation — stale trust. Проверять после Planner слишком поздно. Пробовать все capabilities создаёт несуществующие обязательные зависимости. Переносить rejected config paths — возвращает отвергнутую среду.

**Последствия и цена.** Refresh может честно блокировать реальный tool/config failure до Planner. Успешный Controller probe не доказывает права Planner sandbox и не доказывает product assertions.

**Реализация / доказательства.** `5efd463`, `c1be587`, `256f9e7`; `preflight.py`, `tests/test_planner_tool_evidence_refresh.py`; run `20260908-102707-82ca5b5e` как evidence старого failure, без публикации hidden деталей.

**Пересмотр.** Новая cache policy должна сохранять различие unknown/stale/verified/failed и все affected call sites.

<a id="d-017"></a>

## D-017. Не путать product requirement, обязательный запуск проверки и выбранный proof route

> Часть текущего механизма заменена D-027 в 0.8.0a31; исходная запись ниже сохранена как историческое основание.

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Широкий suite с baseline failures был превращён в препятствие завершению. Первоначальный вывод, будто suite придумал только Planner, позже уточнён: repository instructions действительно требовали соответствующий full suite.

**Решение.** Owner-configured gates и semantic obligations остаются обязательными. Доказанно непригодный Planner-derived proof route может требовать PROOF_MODEL_DIVERGENCE → fresh plan, не отменяя продуктовый контракт. Но «выполнить suite» и «устранить все исторические дефекты repository» нельзя автоматически считать одним требованием: authority и смысл проверяются по исходным инструкциям.

**Отвергнуто и почему.** `baseline red → ignore` не доказывает независимость failure. «Файл не менялся» не исключает влияние shared state. Один и тот же fail count до/после не гарантирует те же ошибки. Узкие tests не заменяют обязательный широкий запуск. И наоборот, любой exploratory fail не становится автоматически новым product intent.

**Последствия и цена.** Нужны exact command/config/environment и сопоставление ошибок baseline/candidate; отдельно разбираются runner/setup errors, in-scope defects и действительно независимый debt. Нельзя изменить корректный assertion ради PASS. Внешний конкретный suite не объявлен целиком unrelated этим журналом.

**Реализация / доказательства.** `c9b4054`, `implementer.v5`, `tests/test_proof_model_replan.py`; уточнение основано на repository instructions и дополнительных test logs из обсуждения. Кодовый routing принят; полная корректность классификации каждого реального failure ещё не установлена.

**Пересмотр.** Изменять proof route только после доказанного несоответствия; обязательное условие владельца не отменяется Planner-ом. Этот уточнённый вывод заменяет чрезмерно общий совет просто сузить suite.

<a id="d-018"></a>

## D-018. Read-only проект не означает запрет необходимых временных файлов

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED в 0.8.0a29 в проверенном native Windows execution contract; ограничения ниже.

**Исходное состояние.** На срезе 0.8.0a28 целевая scratch-write policy декларируется, но фактический read-only Planner не может писать кеш. На момент ретроспективного среза патч `HARNESS_READONLY_PROJECT_WRITABLE_SCRATCH_08` был поручен, но механизм/native acceptance ещё не были представлены. Текущее evidence ниже не переписывает этот исходный вывод.

**Проблема и основание.** Native smoke 2026-09-08 через production Planner воспроизвёл EPERM mkdir/open до Jest assertion. Существующий directory не помог: запись haste-map также запрещена. Controller version/showConfig probes проходят в другом execution context.

**Решение.** Для Planner и Evaluator проект, tests, dependencies и Git controls остаются read-only; только Controller-selected role scratch должен быть writable. Policy, environment и effective session должны совпадать. Исправление проверяется одновременно реальным cached assertion и отклонением forbidden writes.

**Отвергнуто и почему.** TEMP/TMP сами не предоставляют прав. Предварительный mkdir не разрешает последующую запись. `--showConfig` не выполняет test. `--no-cache` не доказан как полный способ устранить все записи и не заменяет целевой contract. Общий workspace-write, danger-full-access, отключение sandbox и «потом проверим, не поменял ли agent проект» ослабляют границу вместо исправления. Неподдерживаемые поля sandbox нельзя придумывать.

**Последствия и цена.** Механизм необходимо проверить на установленной версии runtime; role contexts должны сохранять repository instructions, cwd/path semantics, fresh scratch и blind isolation. Direct child-process EPERM — отдельное наблюдение, не доказанный общий low-level root cause.

**Доказательства.** `execution.py`, `app_server.py`, `planner.py`, `evaluator.py` на `256f9e7`; диагностический пакет `HARNESS_PLANNER_SANDBOX_JEST_SMOKE`: request/response policy, commands, invariance. Secret-bearing artifacts в журнал не копируются.

**Условия IMPLEMENTED.** Реальный Planner и Evaluator выполняют cached тест; project/dependency/neighbor writes реально denied; failing assertion остаётся FAIL; evidence cold/warm/fresh roles сохранено. До этого не заявлять PASS execution fix.

### D-018: проверка механизма в текущем patch

Установленный `Codex 0.153.4 app-server generate-json-schema --experimental`
подтвердил named `permissions`, thread-local `config`, `activePermissionProfile`
и `runtimeWorkspaceRoots`. Это версия установленного runtime, не перенос latest schema.
Native mechanism probes 2026-09-08 рассмотрели три варианта:

- **REJECTED:** дополнительно deny-read для private/peer paths — unelevated Windows
  сообщает, что deny-read restrictions не поддержаны, и отказывается выполнять команду.
- **REJECTED:** project session cwd плюс отдельный writable scratch — профиль принят,
  но runner отвергает split writable root sets до запуска процесса.
- **ACCEPTED:** session cwd равен единственному writable role scratch внутри project;
  `:root=read`, scratch=write, network=false, approvalPolicy=never. Команды используют
  project workdir; ancestor AGENTS discovery сохраняется. В mechanism smoke cached
  Jest cold/warm выполнил assertion; 20 canary mutations denied, intentional assertion FAIL.

Дополнительный production probe опроверг предположение, что тот же per-thread profile
нужно повторно передавать в turn/start: 0.153.4 перезагружает base config без этого
profile и отвечает `default_permissions requires a [permissions] table`.
**SUPERSEDED:** повторно выбирать профиль каждый turn. **Действует:** наследовать
validated thread context; публичный `run_turn` не принимает cwd/permission overrides.
Отказ не скрыт: diagnostics `production-native-02` сохранены отдельно от нового запуска.

Ещё один integration probe выявил отдельный `realpath EPERM`: `tempfile.mkdtemp()`
создал на Windows/Python 3.13+ protected owner-only DACL. Read-only ACL comparison
подтвердил отличие от успешного scratch, созданного обычным `mkdir`.
**SUPERSEDED:** считать эти два способа создания directory эквивалентными для
restricted-token runner. **Действует:** уникальный UUID path и обычный inherited
`mkdir`, без ручных ACL grants; фактические права ограничивает named sandbox profile.

Native lifecycle probe `slivin-native-scratch-ca38102639` выполнил initial и
continuation Planner assertions/denied writes, затем обнаружил `WinError 145`:
обычный Python cleanup оставлял реальный haste-map с путём длиной 261 символ.
**SUPERSEDED:** считать `rmtree(ignore_errors=True)` доказательством очищенного scratch.
Controller теперь проверяет root containment/link boundary и удаляет роль через
extended-length Windows path; ошибка очистки является typed failure. Regression
создаёт длинный cache path, проверяет удаление и сохранность peer/project. Отдельная
native cleanup проверка того же оставшегося файла прошла без изменения ACL.

Следующий lifecycle probe `slivin-native-scratch-c7437e126c` снова подтвердил
initial/continuation assertions и denied writes, но обнаружил `WinError 32`:
живой старый thread удерживал рабочий каталог scratch. Удаление его файлов
не завершает session. Выбран штатный `thread/archive` перед очисткой: rollout
сохраняется, а повторное использование retired thread запрещено. Installed schema
подтверждает этот RPC; освобождение directory handle требует отдельного native
результата, а не только успешного mock response. `thread/unsubscribe` не выбран
как гарантия немедленного shutdown; новый App Server или широкие permissions
не вводятся ради удерживаемого directory handle. Проба `slivin-native-scratch-f034698428`
опровергла применимость archive к ephemeral thread: `no rollout found`.
**SUPERSEDED:** ephemeral scoped session плюс гарантированный archive при reset.
**Действует:** Planner/Evaluator materialized sessions; их rollout хранится в локальном
Codex storage. Это принятая дополнительная издержка хранения, не новый writable grant
агенту. Intake/Implementer сохраняют прежний ephemeral setting.

**Цена и границы.** Session root отличается от command cwd; это явно отражено в
Controller instructions. Fresh роли получают уникальные пустые scratch; temporary
AGENTS sources отклоняются. Broad read access прежнего read-only sandbox сохраняется;
новая OS-level deny-read isolation не заявляется. Controller/native guards остаются
дополнительной защитой. Intake/Implementer/Controller scopes не расширяются.

**Evidence текущей интеграции.** [execution.py](../slivin_harness/execution.py),
[app_server.py](../slivin_harness/app_server.py),
[tests/test_app_server.py](../tests/test_app_server.py),
[воспроизводимый native smoke](../tools/smoke_readonly_scratch.py).
В synthetic native acceptance Controller задаёт отдельные точные PowerShell
`ReadAllText` commands для `AGENTS.md` и `src/AGENTS.md`. Authoritative evidence
каждого чтения — соответствующий завершённый `commandExecution` с exact payload,
project cwd и `exitCode=0`; marker в `aggregatedOutput` только дополняет evidence и
не обязателен. Echo или другая команда, содержащая имя/marker instruction file,
не принимается. Это уточняет Controller evidence D-013 и не меняет permission,
Jest/cache или invariance boundaries решения.
То же command-evidence authority действует для synthetic sandbox canary: immutable
`sandbox_probe.cjs` получает Controller-selected project/scratch/sibling/private/peer,
сам валидирует cwd, TEMP/TMP/os.tmpdir, обе positive и все 20 negative operations,
включая EPERM/EACCES reason, и завершает non-zero при нарушении. Controller принимает
ровно один exact probe command с project cwd и `exitCode=0`; `PROBE_RESULT` —
необязательная диагностика. Отсутствие stdout не превращает успешный self-validating
probe в FAIL и не даёт права придумывать per-operation details. Это уточнение
существующего D-013/D-018, а не расширение sandbox или child-process contract.
Локальные пакеты `slivin-scoped-scratch-391996c9b6/mechanism-*` и
`production-native-*` содержат запрос, reported profile, command/exit/output и invariance.
Исходный `slivin-planner-sandbox-smoke-3dbfda4104` не изменялся.
Итоговый пакет `slivin-native-scratch-6c74b54c0f/diagnostics` имеет
`NATIVE_SCOPED_SCRATCH_PASS` на Codex 0.153.4, Node v24.15.0 и Jest 29.7.0:

- шесть execution phases: initial/continuation Planner, fresh Planner после archive
  и очистки, initial/continuation Evaluator, новый Planner при активном Evaluator;
- 10 успешных cached Jest запусков с выполненным assertion и 4 намеренных assertion
  FAIL; cache не отключён, реальные haste-map/perf-cache сохранены;
- 120 write attempts отклонены с EPERM/EACCES, включая absolute/relative paths и
  запись в реально выделенный scratch другой активной роли в обоих направлениях;
- `invariance.json` подтверждает неизменность project/candidate/source runtime и
  canaries; Git status пуст. `result.json` содержит SHA-256 использованных execution
  sources, совпадающие с проверенным кодом;
- `commands.jsonl` сохраняет точные команды, cwd, exit code и полный output;
  `requests.jsonl` и thread metadata разделяют requested/reported permissions.

Offline self-check: 472 tests, 6 штатных platform skips, `HARNESS_SELF_CHECK_PASS`;
docs sync PASS. Native smoke использует реальные role instructions и production
ExecutionBroker/CodexAppServer. Semantic `run_planner` corrective/replan и
Evaluator blind A→immutable persistence→B проверяются отдельно generic integration
tests; этот smoke не является продуктовым verdict или full benchmark trial.
Ранний полный пакет `slivin-native-scratch-653bd9ba84` также PASS, но его обычный
peer-canary дополнен проверкой активных granted roots в итоговом пакете.

Отдельный `spawnSync` по-прежнему возвращает EPERM. Его низкоуровневая причина
не установлена; обязательный cached `--runInBand` route выполняется без него.
Это ограничение не переименовано в исправленную проблему.

**Пересмотр.** Project session cwd или deny-read можно вернуть только после реального
подтверждения поддержки новым runtime с теми же отрицательными операциями; не через
отключение sandbox/широкие ACL. Native acceptance после wire/environment изменений
нужен вновь; unit wiring не является доказательством OS permissions.

<a id="d-019"></a>

## D-019. Strict wire protocol и смысл статуса проверяются отдельно

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** App Server отклонял output schema уже при запуске роли. Одновременно non-COMPLETE report не должен фабриковать закрытый ledger ради формата.

**Решение.** Рекурсивно валидировать все реально передаваемые output schemas до turn/start; все declared wire properties присутствуют согласно поддерживаемому strict contract. Controller отдельно проверяет status-dependent semantics. Agent receipt не становится authoritative. Несовместимые пары terminal status/reason_kind отклоняются.

**Отвергнуто и почему.** Добавить только одно поле из сообщения HTTP 400 — оставляет тот же дефект в соседних properties. Обязательный wire field не означает обязанность выдумать VERIFIED evidence. `BLOCKED + PROOF_MODEL_DIVERGENCE` скрывает доступный replan. Неверный fundamental contract нельзя отправлять в обычный repair без смены модели.

**Последствия и цена.** Wire-изменения versioned; semantic hardening не требует автоматически повышать все остальные protocols. Схема не валидирует истинность natural-language claims.

**Реализация / доказательства.** `3f380aa`, `0badb85`, `c9b4054`; `output_schema.py`, Implementer/Evaluator status matrix tests.

**Пересмотр.** При изменении App Server schema сначала проверить версионный контракт и всех callers, а не патчить один failing payload вслепую.

<a id="d-020"></a>

## D-020. Не терять активную работу из-за транспорта, timeout или кодировки

> Часть текущего механизма заменена D-027 в 0.8.0a31; исходная запись ниже сохранена как историческое основание.

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Исторические прогоны выявляли premature abort на retryable stream event, фиксированном wall-clock timeout и Unicode output в Windows console.

**Решение.** Retryable event не равен terminal failure; ориентироваться на фактическое завершение turn. Inactivity watchdog различает активную модель/tool и отсутствие прогресса; permitted continuation сохраняет тот же thread/workspace в ограниченном recovery route. Harness-owned output использует явную корректную кодировку; JSON-RPC request deadlines не подменяются turn watchdog.

**Отвергнуто и почему.** Любой error event сразу завершать fatal — теряет успешный server retry. Heartbeat Controller не доказывает активность модели. Бесконечное ожидание без диагностики — не автономность. Unicode в логах не должен превращать прошедший check в infrastructure failure.

**Последствия и цена.** Нужны понятные activity/error records, bounded recovery и различие infrastructure/semantic outcomes. Точное policy поведение проверяется по актуальному коду, не по старому release note.

**Реализация / доказательства.** [HISTORY](HISTORY.md): stream recovery и Phase 4; `8fbd179` UTF-8 runner; `app_server.py`, transport/console tests.

**Пересмотр.** На основе воспроизводимого зависания или потери активной работы; не заменой всех timeout одним большим числом.

<a id="d-021"></a>

## D-021. Версия, environment и уровень проверки должны быть восстанавливаемы

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED.

**Проблема и основание.** Одинаковая semantic version не определяет exact code; Gitless copy не имеет SHA. Controller smoke, unit tests и настоящий agent sandbox показывали разные результаты.

**Решение.** Записывать Harness version, exact commit и tracked dirty state; archive/unknown честно даёт null. Фиксировать фактические tool/runtime версии и условия trial. Разделять review, synthetic tests, Controller probe, native role smoke, semantic held-out и delivery. Сохранять неуспешные попытки и считать end-to-end failures, даже когда semantic score ещё отсутствует.

**Отвергнуто и почему.** «Все тесты зелёные — всё готово» подменяет уровни проверки. `NOT_RUN` не равно PASS и не равно 0/7. Отсутствие Git в архиве не означает функциональный дефект. Исключать все unsuccessful orchestration attempts из оценки автономного выполнения — скрывает эксплуатационную ненадёжность.

**Последствия и цена.** Более подробные artifacts и честные ограничения сравнения разных Harness/Codex/model/environment версий. Нужны platform-specific observations; Windows PASS не доказывает POSIX и наоборот.

**Реализация / доказательства.** `8aab2a6`, `517f9ed`, `c4fb0d4`; `build_identity.py`, `QUALITY_MODEL.md`; сохранённые review-оговорки и native smoke.

**Пересмотр.** Менять метрики можно явно, не удаляя историю и не переименовывая infrastructure failure в успешный trial.

<a id="d-022"></a>

## D-022. Полный patch включает напрямую затронутых consumers и текущую документацию

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED (documentation policy; без нового runtime gate).

**Проблема и основание.** Жёсткий список Allowed changes неоднократно заставлял агента останавливаться на синхронизации общего fixture или устаревшей protocol reference. Это превращало end-to-end цель в ручное сопровождение каждого файла.

**Решение.** Functional scope и Preservation Contract определяют границу. Прямые callers, tests, fixtures и current docs, необходимые для корректности общего изменения, исследуются и синхронизируются агентом без нового вопроса на каждый путь. Новый root cause не закрывается исчезновением исходного симптома. Перед завершением повторяется impact sweep по фактическому diff.

**Отвергнуто и почему.** Малый diff не равен малому полному решению. Отдельный commit/version bump на каждую опечатку не нужен. Но «автономность» не даёт права на destructive operations, production changes, новые credentials, ослабление acceptance или unrelated refactor.

**Последствия и цена.** Reviewer проверяет preservation boundaries и affected consumers, а не только allowlist имён. Документация обновляется в том же функциональном patch; исторические release notes остаются историческими.

**Реализация / доказательства.** Явные разрешения владельца и последующие промпты 07/08; история fixture/doc-sync corrective commits. Это политика выполнения задач, а не заявление о новом runtime enforcement.

**Пересмотр.** Новое разрешение нужно для реально новой authority/цели, а не обычной синхронизации непосредственного consumer.

<a id="d-023"></a>

## D-023. Исправленные выводы: что нельзя воспроизводить без новых оснований

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED (documentation policy; без нового runtime gate).

**Статус / реализация.** ACCEPTED / действующая интерпретация evidence. Ниже не «внедрённые функции», а история отвергнутых или уточнённых выводов.

**Проблема и основания.** Потеря контекста возвращает уже опровергнутые предположения.

**Решение.** Сохранить прежние выводы и причинные связи с D-записями в таблице.

**Отвергнутые варианты.** Стирать прошлые ошибки или молча делать их текущими правилами; основания отказа перечислены построчно.

**Последствия.** Таблица ограничивает интерпретацию evidence, не создавая новый runtime enforcement.

| Прежний вывод / подход | Статус | Почему отказались / что действует теперь |
| --- | --- | --- |
| После 0/N решений надо раскрыть агенту пропущенные hidden scenarios и сделать case проще | REJECTED | Цель — autonomous discovery. Улучшать исследование/исполнение агента; benchmark корректировать только по независимому доказательству его invalidity. D-001, D-015. |
| Pipeline дошёл до semantic fail — автономная инженерная система в целом уже готова | SUPERSEDED | Это доказывает работоспособность конкретного execution route, не полноту исследования или устойчивую end-to-end работу. D-021. |
| Зелёный self-check или общий ledger доказывает отсутствие связанных дефектов | REJECTED | Проверяются конкретные paths/conditions, а не всё возможное поведение. Нужны independent review и held-out. D-004–D-006. |
| Full suite в спорном запуске — только произвольная эскалация Planner | SUPERSEDED | Сверка AGENTS.md подтвердила требование широкого запуска. Его смысл и exact failing cases нужно разбирать; нельзя просто убрать suite. D-017. |
| Те же baseline failures и unchanged файлы позволяют объявить всё unrelated | REJECTED | Общий semantic/state contract может затрагивать неизменённого consumer; runner/setup failure отдельно от продуктового. D-017. |
| После reset JEST отсутствует в verified set, значит инструмент недоступен | SUPERSEDED | Evidence было инвалидировано; нужен guarded refresh до fresh Planner. Реализовано `256f9e7`. D-016. |
| Если создан scratch и назначен TEMP, Planner может в него писать | SUPERSEDED | Native role smoke воспроизвёл EPERM mkdir/open. Environment не является permission grant. D-018. |
| Controller --showConfig PASS доказывает выполнение Jest assertion в Planner sandbox | REJECTED | Контекст и стадия исполнения различаются. D-016, D-018, D-021. |
| Просто workspace-write на проекте или отключение sandbox закрывает cache defect | REJECTED | Нарушается project read-only preservation boundary. Требуется scoped scratch. D-018. |
| Повторная передача thread-local permissions в turn/start эквивалентна наследованию thread | SUPERSEDED | Native 0.153.4 reload теряет local profile; turns наследуют validated thread без overrides. D-018. |
| Любой способ создания temporary directory эквивалентен для sandbox | SUPERSEDED | Windows mkdtemp 0700 создаёт protected DACL; normal mkdir наследует permissions. Scoped write boundary всё равно задаёт sandbox. D-018. |
| Успешный вызов rmtree с ignore_errors доказывает очистку role scratch | SUPERSEDED | Native haste-map с длинным путём оставался после cleanup. Используются проверенные extended-length paths и явный failure при ошибке. D-018. |
| Ephemeral scoped thread можно гарантированно завершить через archive | SUPERSEDED | 0.153.4 отвечает no rollout found. Scoped sessions materialized; archive освобождает cwd перед reset, rollout сохраняется. D-018. |
| Ещё один speculative hardening всегда важнее реального полного trial | REJECTED | Исправляются доказанные blockers/нарушения; гипотетический риск не становится бесконечным новым DoD. D-022. |

**Пересмотр.** Новая evidence может изменить вывод; указать, какую строку она опровергает. Нельзя реабилитировать вывод только из-за потери контекста в новом чате.

<a id="d-024"></a>

## D-024. Существенное решение не завершено без причины и отвергнутых альтернатив

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED (documentation policy; без нового runtime gate).

**Исходное состояние.** Переданный владельцем ретроспективный текст ещё не был включён в repository. Текущий patch добавляет журнал, ссылки, root AGENTS.md и небольшую structural docs-sync проверку. Это documentation policy, без нового runtime gate; commit и проверки фиксируются по факту.

**Проблема и основание.** Changelog объясняет «что поменяли», но не сохраняет причины выбора. Без этого новый разработчик заново предлагает уже отвергнутую альтернативу или принимает временную гипотезу за действующий контракт.

**Решение.** Для каждого существенного изменения записывать problem/evidence, choice, rationale, alternatives with reasons, consequences, preservation, implementation/evidence state и условия пересмотра. Изменение существующего решения обновляет его запись/ссылку, а не создаёт противоречивую параллельную инструкцию. В README/docs index/AGENTS даётся короткий маршрут чтения.

**Отвергнуто и почему.** Один список commits не сохраняет reasoning. Архив всего чата — не навигационный документ и может раскрыть secrets/hidden hints. Новый тяжёлый runtime gate или model role для ведения журнала не требуется. На каждую механическую правку отдельный ADR не нужен: журнал фиксирует решения, а не каждую строку diff.

**Последствия и цена.** Documentation review — часть того же завершённого patch. Structural docs-sync может проверять наличие headings/IDs/links/status fields, но не выдаётся за автоматическое доказательство качества rationale.

**Проверка.** В repo должны появиться журнал, читаемые ссылки и правило обновления; принятые, но не реализованные решения нельзя переименовать в IMPLEMENTED без commit/test evidence.

**Пересмотр.** Возможна разбивка на отдельные ADR-файлы при росте объёма; сохранить стабильные IDs, обратные ссылки и историю замен.

<a id="d-025"></a>

## D-025. Framework определяет trusted runner, расширение файла — только navigation hint

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED в 0.8.0a30.

**Проблема и основание.** На исходном `04d7359` generic regression с одинаковыми
`.test.cjs` показал: native node:test получает Jest argv только из-за наличия Jest.
Это ошибка Controller compiler. Изменённая формулировка Planner не меняет эту команду.

**Решение.** Source-backed lexical descriptor различает CommonJS/ESM node:test imports
и Jest syntax. Native route — один configured Node process на файл; Jest сохраняет
config/argv. Unknown/mixed evidence даёт typed controlled diagnostic. Owner explicit
commands не переписываются. Command templates с workspace/toolchain placeholders
служат общей authority для generated runner, Controller и reconstruction. Compiled
specs входят в digest registry; runner drift заменяет прежнюю команду и инвалидирует
verification, а не добавляет ещё один старый runner.

**Отвергнуто и почему.** REJECTED: удалить native tests, переписать их на Jest,
ослабить assertions, выбирать runner по случайному PASS или бесконечно replanning
product patch — каждый вариант скрывает compiler defect. Не требовать `node --test`
как единственный route: direct file execution сохраняет assertions и не требует
дополнительного spawn-маршрута. Новый универсальный framework plugin system отложен:
для подтверждённого класса достаточно conservative supported descriptors.

**Последствия и цена.** Неподдерживаемые syntactic forms требуют явного owner check;
resolver не является полным JS parser или доказательством качества assertions.
Все необходимые checks сохраняются. Owner gates, changed/new tests, runtime/Git guards,
receipt binding и reconstruction остаются обязательными. Baseline failures нельзя
объявить unrelated по runner/extension или unchanged paths (D-017).

**Реализация / доказательства.** `test_runners.py`, `task_runner.build_dynamic_check_specs`,
`CheckRegistry.bind_compiled_specs`; `test_runner_report_recovery.py` воспроизводит
before/after. `test_native_trusted_runners.py` и `tools/smoke_trusted_test_runners.py`
проверяют установленные Node/Jest, passing/failing assertions, generated runner,
Controller и reconstruction; combined workflow использует agent doubles. Это не
новый Planner sandbox smoke и не product benchmark PASS. См. D-013/D-014/D-018.

**Пересмотр.** Добавление runner/form только с проверяемым descriptor и runnable
регрессией обоих исходов. Нельзя заменить failure fallback-ом на установленный runner.

<a id="d-026"></a>

## D-026. Неполное локальное evidence отчёта исправляется без новой реализации продукта

> Часть текущего механизма заменена D-027 в 0.8.0a31; исходная запись ниже сохранена как историческое основание.

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED в 0.8.0a30.

**Проблема и основание.** На исходном `04d7359` populated related finding с `symbols=[]`
корректно отклоняется validator, но теряет bounded recovery и завершает run исключением.
Сохранённый run state старого trial имел HARNESS_EXCEPTION и пустой previous candidate
inventory. Generic regression отдельно воспроизводит именно artifact boundary.

**Решение.** Не более двух report-only turns в том же Implementer thread для точных
allowlisted leaf evidence errors. Candidate и все остальные поля report frozen;
Controller не придумывает symbols и не удаляет findings. Реальные documentation
link targets/anchors — identifiers, fake code function не требуется. Каждый raw attempt
сохраняется private до validation, typed outcome публикуется без private values.
После correction полный validator, checks, binding и expansion сохраняются.
Terminal candidate observation использует физические текущие files либо явный UNKNOWN
со stale previous identity; алгоритм candidate identity не меняется.

**Отвергнуто и почему.** REJECTED: автоматически принять пустое evidence, удалить
finding/test/consumer, изменить assertions, перезапустить всю продуктовую реализацию
ради ссылки, catch-all RuntimeError → retry, бесконечная correction, доверие прежнему
receipt после mutation. Semantic gaps, missing consumers/capabilities, failing checks
и integrity violations не являются косметическими JSON errors.

**Последствия и цена.** Дополнительные bounded model turns и private forensic artifacts.
Только локальные evidence arrays, указанные Controller, разрешено уточнять; смена
остальных claims или candidate даёт controlled failure. Correction timeout не получает
ещё один recovery turn. Role wire `implementer.v5` и требование concrete evidence не
меняются. Scratch permissions не расширяются. Связь с D-005/D-013/D-019/D-020/D-023.

**Реализация / доказательства.** `report_recovery.py`, `run_implementer_report`,
`observe_terminal_report_candidate`; focused adversarial tests проверяют exhaustion,
claim retention, mutation и обязательную полную validation. Combined synthetic workflow
регистрирует native+Jest, исправляет documentation locator, проходит Evaluator/Final Gate;
agent replies — doubles, subprocess checks — реальные. Это не доказательство semantic
полноты конкретного внешнего candidate или исправности всех возможных malformed reports.

**Пересмотр.** Расширять allowlist только для доказанной локальной ошибки с сохранением
identity/semantic fields, guards и конечного retry budget. Новая technical model требует
существующего replan, а не косметического переписывания claims.

<a id="d-027"></a>

## D-027. Immutable origins и квалификация safety вместе с positive progress

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED; факт qualification конкретного SHA
определяется отдельным `qualification.json`, а не этой записью.
**Дата регистрации:** 2026-09-10. **Заменяет:** части D-005/D-007/D-017/D-020/D-026,
перечисленные ниже; прежние решения и причины сохранены как история.

**Проблема и проверенные основания.** Пакет владельца
`HARNESS_SYSTEMIC_RELIABILITY_PACKAGE/CODEX_SYSTEM_STABILIZATION.md` изменил критерий
приёмки с локальных fixes на qualified build. Native baseline probe показал, что
first-error recovery исчерпывает два исправления при 3/5 независимых errors; исправление
трёх сразу отклонялось как CHANGED_CLAIMS. Cross-role копии prose создавали новые
ошибки identity. Independent review выявил тупики legal promotion, timeout до raw и
proof→technical transition; defect probes также показали ложную qualification по
marker files, неполным stage evidence и infrastructure-only mutation ERROR.

**Решение.** Controller владеет immutable original claims и стабильными ID/revision.
Роли передают собственные explicit assessments и новые observations, не переписывают
prior prose. COMPLETE требует каждого current source; CHALLENGE/missing не превращается
в CONFIRM. Promotion сохраняет original outside claim и текущий IN_SCOPE target.
Evaluator A остаётся blind и durable до B; B оценивает current source refs и promotion.
Handoff компилируется из origins/current dispositions с отдельным original provenance.

Report correction собирает batch безопасных independent diagnostics, сохраняет
candidate/claims и имеет два corrective turns/no-progress guard. До validation
сохраняются private candidate/evidence checkpoints. Timeout/unknown transport без raw
не позволяет назвать baseline текущим candidate. Bounded atomic persistence допускает
только Windows transient sharing/access failures и сверяет ambiguous completed write.
Изменённый typed check/config допускает bounded rebind на BLOCKED до current verification.

Proof-only revision сохраняет candidate и immutable requirements. Независимый Planner
возвращает `proof-route-review.v1`; Controller применяет только effective proof routes,
инвалидирует downstream evidence и сохраняет owner gates. Лишь независимо установленная
technical model divergence направляется в существующий semantic reset. Review и reset
используют один manifest replan cycle; BLOCKED не разрешает reset.

Обязательный `release_check.py --profile windows-local` проверяет frozen clean SHA,
все boundary families, stateful/fault scenarios, семь mutation controls, actual mixed
Node/Jest, native scoped roles и два generic FULL task плюс повтор первого до final
acceptance/reconstruction/safe delivery. Actual delivered candidate проверяется frozen
public assertions и полными current artifacts. Mandatory NOT_RUN/SKIP/FAIL запрещает
qualification; controlled stop корректной задачи считается FAIL.

**Почему.** Это сохраняет независимость ролей, устраняя зависимость identity от точного
копирования текста. Batch correction восстанавливает корректный результат без изменения
claims. Разделение proof/product routes избегает потери пригодного candidate и сохраняет
проверку affected behavior. Проверяемая boundary map защищает обязательность case families,
а независимый replay результата обнаруживает false-positive release summaries.

**Отвергнутые варианты.** REJECTED: увеличение retries вместо batch diagnostics;
неявное CONFIRM пропущенных records; нормализация текста, маскирующая semantic drift;
удаление source findings или owner checks для PASS; always-stop validator; blanket reset
candidate из-за proof route; Controller smoke вместо native role/real-model acceptance;
подсчёт любого ERROR как killed mutant; benchmark coaching/reference fixes. DEFERRED:
cross-process resume и универсальная crash recovery — checkpoint не утверждает их наличие.

**Последствия и цена.** Wire versions несовместимы с предыдущими role artifacts:
planner.v6, implementer.v6, implementation-contract.v4, implementation-impact-closure.v2,
evaluator.v7, user-follow-up.v2; workflow.v7 содержит новую map. Старые runs читаются
исторически и не получают автоматического upgrade/reuse. Дополнительные private bytes,
Controller validation и real-model qualification увеличивают стоимость. Product intent,
все IN_SCOPE obligations, owner checks, hidden isolation, runtime/Git/candidate integrity,
scoped permissions и reconstructed assertions сохраняются.

**Реализация / evidence.** `source_records.py`, `report_recovery.py`, `checkpoint.py`,
`proof_routes.py`, production boundary hooks и `boundary_contracts.json`; mandatory
tests связаны в map. [SYSTEMIC_RELIABILITY](SYSTEMIC_RELIABILITY.md) задаёт конкретный
release workflow, fixture/budget inventory и уровни доказательств. Developer/reviewer
probes сохранены отдельно; они не приравниваются к квалификации неизменного build.

Executable binding включает разрешённую installed цепочку launcher/Node/native
payload/helpers, а не только hash `codex.cmd` и строку версии. Review первого
frozen запуска установил, что неизменный shim не замечал подмены native executable;
тот запуск остановлен как NOT_QUALIFIED до model stages. Release entrypoint заново
определяет цепочку и hashes после испытаний; неизвестные wrappers не квалифицируются.
Цена — два локальных `--version` probes и hashing native payload без изменения
installed runtime/settings. Tests изменяют только disposable payload и проверяют
helper/native/launcher replacement, PATH/local Node и platform retarget.

Итоговая requirements review выявила два недостатка frozen coverage без нового
runtime counterexample: предел двух check rebinds и direct rejection отдельных
counterfeit stage payloads были доказаны лишь reviewer probes. Поэтому named native
Node/Jest orchestration теперь требует stop без registry delta и после двух rebinds;
каждый из трёх real-model FULL результатов запускает fault controls на своём actual
payload. Prompt, число model runs и budgets не меняются. Подмены выполняются только
in-memory, после них сверяются bytes artifacts и physical candidate.
Qualification использует короткие внутренние case/workspace identifiers и заранее
проверяет фактическую длину самого глубокого runtime path. Поэтому Windows path limit
не может подменить real-model execution быстрым инфраструктурным FAIL.
Real-model replay также выявил два production boundary дефекта. B17 больше не пытается
sealing исчезающие reserved Jest cache-файлы, но продолжает сохранять authored scratch.
Patch packaging помещает raw physical blobs в disposable object database и использует
binary delta только когда configured worktree conversion не может воспроизвести exact
physical bytes; воспроизводимые text paths остаются читаемыми для Evaluator. Windows
read-only loose objects очищаются вместе с disposable store. Reconstruction применяет patch
к private index, materializes exact blobs без filters и не ослабляет сравнение candidate.
Delivery валидирует patch/blob mapping в отдельном index, после чего переносит accepted
postimages с прежними source rechecks, rollback и concurrent-edit guards.
При pre-freeze авторинге контроль третьего delta сначала ошибочно ожидал прежний
compiled runner, затем отдельный post-stop registry digest. Контракт ограничивает
continuations: правильный negative assertion фиксирует три чередующихся physical
изменения, distinct digests на трёх вызовах, последний candidate/checkpoint, ровно
два продолжения и отсутствие acceptance. Hash берётся из записанных bytes, чтобы
Windows newline normalization не подменяла проверку состояния сравнением с LF-строкой.

**Superseded boundaries.** D-005/D-007: cross-role копирование текста и DTO уступают
Controller origins; обязательная полнота/follow-ups сохранены. D-017: proof-only reset
заменён сохранением candidate и независимым review; настоящий technical reset сохранён.
D-020: terminal inventory/checkpoints и bounded idempotent persistence дополняют
transport distinction. D-026: first-error/one-field correction заменены batch диагностикой;
bounded retries, semantic allowlist и guards сохранены.

**Пересмотр.** Новое изменение ownership/recovery допустимо только с проверенным
counterexample, сохранением positive и adversarial cases, обновлением boundary map и
повторной qualification нового SHA. Конечные generic runs не доказывают универсальную
полноту поиска или успешность отдельного исторического product trial.

<a id="d-028"></a>

## D-028. Owner check inputs и production toolchain остаются властью Controller

> Phase-B blind-revision correction из этой записи заменена Controller-owned origin
> catalog в D-029; owner check/toolchain решения D-028 остаются действующими.

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED; qualification конкретного SHA
определяется отдельным `qualification.json`.
**Дата регистрации:** 2026-09-10. **Связанные решения:** D-008, D-013, D-014,
D-017, D-026, D-027.

**Проблема и проверенные основания.** Real-model qualification сборки `ec81d02`
выявила два false-positive пути в успешных product candidates. Production
`{project_root}` ссылался на source checkout, хотя Controller уже скопировал runtime
в managed workspace. Одновременно модели изменили direct owner test inputs, и Harness
исполнил эти изменённые assertions. В третьем scenario Evaluator присвоил трём
`source=BLIND` rows вымышленный revision; локальная protocol ошибка остановила
корректный candidate вместо bounded report correction.

**Решение.** Production и reconstructed proof разрешают project-relative toolchain
от managed workspace. Historical benchmark сохраняет source-relative resolution лишь
до обязательного recorded sanitize/rebind. Static preflight сохраняет exact bytes,
размер и digest известных direct Node/Python scripts, explicit Jest config и выбранных
test files. Перед deterministic, held-out и reconstructed batch Controller проверяет
baseline; drift даёт `OWNER_CHECK_INPUT_CHANGED`, не исполняет изменённый owner input
и использует существующий bounded product repair. Новое покрытие добавляется отдельным
registered test file.

Planner требует docs update только когда существующая canonical документация станет
фактически ложной или materially неполной от final semantics. Уже корректное общее
описание не превращается в обязательство расширить prose ради подробности. Для
Evaluator все non-empty `source_revision` у `source=BLIND` собираются в один typed
batch; correction allowlist содержит только точные поля этих revisions.

**Почему.** Исполняемый runtime и acceptance assertions снова выводятся из текущего
Controller-managed workspace и immutable owner evidence. Bounded correction устраняет
локальную ошибку wire evidence без semantic replan, изменения candidate или прежних
claims. Это закрывает наблюдавшиеся stops, сохраняя исходные owner checks и внешний
frozen validator.

**Отвергнутые варианты.** REJECTED: ослабить frozen validator; считать добавленные
model assertions доверенными owner assertions; продолжать production execution из
source checkout; удалять изменённые tests из evidence после выполнения; требовать
расширение любой уже корректной документации; разрешить generic Evaluator retries или
revision correction за пределами exact blind fields.

**Последствия и цена.** Direct check input, который одновременно является изменяемым
product file, нельзя использовать как саморедактируемое доказательство: owner должен
вынести assertion в отдельный check или явно изменить будущую policy. Hashing известных
inputs и их повторная проверка добавляют ограниченный I/O. Runtime semantics продукта,
task prompts, budgets, qualification oracle, permissions и число model runs не меняются.

**Реализация / evidence.** `preflight.py`, `task_runner.run_checks`,
`reconstructed_verification.py`, `evaluator.py`, `report_recovery.py`, `planner.py`;
mandatory boundary map включает owner-input drift и batched blind-revision recovery.
Focused tests проверяют запрет исполнения изменённого input, exact correction allowlist,
one-turn batch recovery и historical source rebind. Полная qualification нового clean
SHA остаётся обязательной и не подменяется этими tests.

**Пересмотр.** Расширять список замороженных inputs только для Controller-known
acceptance definitions. Новые correction fields требуют отдельного typed
counterexample, узкого allowlist и проверки сохранности candidate/claims.

<a id="d-029"></a>

## D-029. Model artifact admission использует Controller origins и typed failure ownership

**Статус:** ACCEPTED. **Реализация:** IMPLEMENTED в `0.8.0a32`; real-model
qualification нового SHA не выполнялась и определяется отдельным `qualification.json`.
**Дата регистрации:** 2026-09-12. **Связанные решения:** D-002, D-019, D-026,
D-027, D-028.

**Проблема и проверенные основания.** Обязательная qualification сборки `2742aad`
дошла до role artifacts после PASS пяти authoritative Controller checks в QE1 и QS1,
но остановила корректные candidates из-за model-authored classification/revision,
которые уже однозначно следовали из current Controller records. QE2 остановился до
Implementer на локально неверном Planner symbol, потому что initial Planner artifact
не имел общего bounded admission. Это три сохранённых counterexample: QE1
`20260911-151830-5b371bc8`, QS1 `20260911-155350-321a99ce`, QE2
`20260911-163221-c816da9c`.

**Решение.** Перед Evaluator Phase B Controller строит fingerprinted
`phase-b-origin-catalog.v1`. Stable exact `origin_ref` связывает authority,
classification, source ID/revision и current ledger row. Model wire выбирает только
handle из dynamic compatible enum; disposition arrays имеют exact Controller cardinality,
а пустые origin/match groups — `maxItems=0`. Controller после admission canonicalize
current metadata. Blind origins используют те же handles. Unknown/incompatible handle может
получить bounded correction только exact reference field при неизменных disposition,
reason и findings. Stale revision нельзя передать по wire.

Initial Planner проходит bounded local-wire correction до отдельной capability
negotiation. Allowlist ограничен независимо диагностируемыми evidence/path/symbol leaf
fields; diagnosis, technical model, impact classifications, required behavior, proof,
status и owner intent заморожены. Общая taxonomy разделяет `LOCAL_WIRE_ERROR`,
`SEMANTIC_MODEL_CONFLICT` и `INTEGRITY_OR_INFRA_FAILURE`; только первый класс допускает
report-only correction. Из трёх sanitized artifacts создан обязательный deterministic
transcript replay stage перед всеми model-backed stages: `native_roles` и `real_models`.
При нулевых допустимых origins невозможная строка fail closed как semantic conflict,
не запрашивая безрезультатную local correction.

**Почему.** Identity, classification и revision являются функцией current authoritative
Controller state, а не semantic claim модели. Их повторное авторство расширяло invalid
state space без независимого evidence. Handle сохраняет модельное решение о disposition,
reasoning и новых findings, одновременно исключая fabricated stale metadata. Раздельные
local и semantic routes исправляют форму отчёта, не превращая protocol retry в product
repair или обучение конкретному qualification answer.

**Отвергнутые варианты.** REJECTED: merely allowlist два текущих текста `RuntimeError` —
это оставляет тот же ownership defect в новых validator paths; увеличить retry count —
invalid state и semantic mutation сохраняются; fuzzy matching model names/text — результат
становится недетерминированным; доверять stale `source_revision`, присланному моделью —
нарушается freshness; удалить validation — допускаются missing/duplicate/wrong-authority
dispositions; ослабить Phase-B independence — зелёные prior claims подменяют audit;
обучить prompts трём qualification answers — это leakage и не закрывает системный класс.

**Последствия и цена.** `evaluator.v8` несовместим с прежним Phase-B wire; canonical
admitted artifact сохраняет downstream semantics. Dynamic schema и catalog fingerprint
добавляют bounded Controller work и private artifact. Planner может сделать не более двух
local corrections, затем по-прежнему bounded capability correction. Сохраняются blind
Phase A, скрытие Planner reasoning/Implementer prose, exact disposition completeness,
negative-finding rules, candidate/Git/runtime guards, owner checks, reconstruction,
fixed prompts/tasks/count/order трёх FULL cases и отсутствие hidden/reference leakage.

**Реализация / evidence.** `evaluator.py`, `planner.py`, `protocol.py`,
`report_recovery.py`, `source_records.py`, `task_runner.py`, B03/B11 contracts и
`tools/replay_model_artifact_transcripts.py`. Sanitized QE1/QS1/QE2 fixtures проходят
production admission entrypoints; negative tests проверяют unknown/wrong-type handles,
semantic mutation, duplicate/missing dispositions, catalog tamper и correction budgets.
Deterministic PASS доказывает только закрытие известных artifact-boundary regressions,
не model quality и не `RELEASE_QUALIFIED`.

**Пересмотр.** Новые model-owned metadata разрешать только если Controller не может
однозначно вывести их из authoritative state и model claim действительно несёт новую
semantics. Расширение local allowlist требует concrete counterexample, exact leaf ownership,
frozen semantic siblings, no-progress/budget tests и повторную qualification нового SHA.

## Шаблон новой записи

```markdown
## D-NNN. Краткое решение

**Статус:** ACCEPTED / REJECTED / SUPERSEDED / DEFERRED.
**Реализация:** IMPLEMENTED / PARTIAL / NOT_IMPLEMENTED / NOT_VERIFIED.
**Дата регистрации:** YYYY-MM-DD.
**Дата решения:** подтверждённая дата или «не восстановлена».
**Заменяет / заменена:** ID либо «нет».

**Проблема и проверенные основания.** Что реально наблюдалось; что остаётся гипотезой.
**Решение.** Что выбрано и в каких границах.
**Почему.** Причинная связь с исходной целью и сравнение с альтернативами.
**Отвергнутые / отложенные варианты.** По каждому: статус и конкретная причина.
**Последствия и цена.** Что сохраняется; какие consumers затронуты; новые издержки/риски.
**Реализация / evidence.** Commit, code paths, tests, run ID; что именно они доказывают.
**Неподтверждённое.** Что ещё не проверено; не маскировать пустое поле как PASS.
**Условия пересмотра.** Какие новые факты или изменение owner intent достаточны.
```

## Связи с дополнительными историческими решениями

- Intake bounded artifact repair (`3d38bf3`) относится к D-003/D-019: исправляется
  несогласованный protocol artifact, не меняется user intent; retries ограничены.
- Windows checkout conversion (`a91e5a7`) относится к D-012/D-014: exact candidate
  остаётся обязательным, переносится только узкий Git conversion allowlist.
- Platform capability skip и gitless identity относятся к D-021: unsupported
  mode-bit или отсутствие Git metadata не маскируются как выполненная проверка.
- Исторический отказ от отдельного Impact Auditor и от повторяющихся prose layers
  относится к D-002; нынешние impact obligations существующих ролей его не отменяют.
- Native transport recovery и UTF-8 stabilization относятся к D-020; отказ от
  безусловного fatal на промежуточном retry не даёт бесконечный execution budget.

Эти связи сверены по HISTORY/CHANGELOG и текущим модулям task_contract,
output_schema, phase7, git_integrity, build_identity и app_server. Полные historical
trials заново не запускались; исходные исторические разделы оставлены без изменений.

## Источники и ограничения ретроспективы

1. Документы на срезе `256f9e789b4b33d13431fa36b8e178380c959e46`: [AUTONOMOUS_ENGINEERING_CONTRACT](AUTONOMOUS_ENGINEERING_CONTRACT.md), [ARCHITECTURE](ARCHITECTURE.md), [QUALITY_MODEL](QUALITY_MODEL.md), [HISTORY](HISTORY.md), [CHANGELOG](../CHANGELOG.md), [PHASE5_CONTRACT_RUNTIME](PHASE5_CONTRACT_RUNTIME.md), [PHASE7_FINAL_GATE](PHASE7_FINAL_GATE.md).
2. Явные решения владельца и смена выводов в рабочем обсуждении. Отвергнутые предложения не выдаются за когда-либо реализованный code path.
3. Статус IMPLEMENTED здесь означает наличие реализации согласно проверенному repository record; не каждое перечисленное историческое исправление заново исполнено на всех платформах при составлении журнала.
4. Saved trial logs и native smoke являются отдельным evidence, не production configuration. Их доступность за пределами исходной среды не предполагается. Если пакет недоступен, нужно сохранить квалификацию evidence или повторить generic reproduction, а не заявлять собственный PASS.
5. Journal не хранит hidden assertion text, reference patch, secret values, `.receipt_key` или полный dump environment. Generic решение и его provenance достаточны для проектирования Harness.
6. D-018 перешёл из PARTIAL в IMPLEMENTED только после code/integration review и сохранённого native acceptance. Это не подтверждение универсального sandbox, всех subprocess APIs или успешного полного product trial.
