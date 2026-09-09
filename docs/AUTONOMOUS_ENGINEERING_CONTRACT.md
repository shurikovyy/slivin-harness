# Autonomous Engineering Contract

> Slivin Harness предназначен для автономного engineering agent. Пользователь описывает проблему и требуемое поведение, но не обязан заранее знать технический root cause, полный impact radius, затронутых consumers, необходимые файлы или regression cases. Harness обязан заставить агент самостоятельно исследовать эти связи до принятия результата.

Это нормативный документ проекта. Он определяет обязанности engineering agent и
границы ответственности пользователя независимо от конкретного проекта или benchmark.
Текущие механизмы описаны в [ARCHITECTURE.md](ARCHITECTURE.md), границы доказательств —
в [QUALITY_MODEL.md](QUALITY_MODEL.md), порядок исполнения — в [WORKFLOW.md](WORKFLOW.md).

## Mission

Harness организует автономное исследование, реализацию и независимую проверку
engineering-задачи. Его цель — полное корректное решение исходного intent с
сохранением explicit ограничений, а не выполнение заранее разжёванного patch plan.
Локально объяснить симптом недостаточно, если исправление меняет shared contract.

## User responsibility

Пользователь сообщает наблюдаемый дефект или требуемое observable поведение и
известные ограничения. User Task Contract остаётся единственным authoritative
источником explicit product intent, acceptance, preservation и owner boundaries;
verbatim claims и intake normalization не подменяются выводами Planner.

Пользователь не обязан перечислять root cause, файлы, shared components, callers,
readers, writers, consumer modules, edge cases, regression scenarios, необходимые
тесты или связанные in-scope дефекты. Указанный пользователем файл — отправная
точка исследования, если пользователь явно не задал hard owner boundary.

## Agent responsibility

Агент самостоятельно исследует текущий repository, проверяет гипотезы evidence,
устанавливает причину и technical impact radius, классифицирует зависимости и
выбирает минимальное полное решение. Он не придумывает новую feature и не расширяет
product intent: исследует технические следствия исходного требования.

Три разных понятия нельзя отождествлять:

| Понятие | Нормативное значение |
| --- | --- |
| User scope | Какой observable результат нужен и какие explicit ограничения даны. |
| Technical impact radius | Какие части системы обязаны быть исследованы из-за найденной причины. |
| Patch size | Минимальный набор изменений после доказанного impact closure. |

«Минимальный patch» не означает «исследовать минимальное количество файлов».
Planner не имеет права сокращать impact radius только потому, что один локальный
extension point объясняет исходный симптом. Если корректное решение несовместимо
с explicit owner boundary, агент сообщает конфликт, а не ослабляет boundary.

## Impact closure

До `READY` Planner проходит цепочку:

```text
root cause / feature extension point
→ изменяемый semantic/state contract (before → after)
→ concrete writers/readers/decision points/consumers
→ рассмотрение sibling consumers и достижимых boundaries
→ scope classification с repository evidence
→ доказанное closure
→ минимальное полное решение
```

Для каждого изменяемого contract нужны существующие repository-relative file
paths и concrete symbol/state/API/field names. Для каждого materially plausible
consumer нужны конкретные paths, symbols и объяснение зависимости или её отсутствия.
Разные behaviorally distinct consumers не схлопываются в «shared consumers» ради
компактности. Единственный extension point не доказывает полноту impact radius.

`search_evidence` показывает target, метод исследования, evidence paths и вывод.
Метод может быть любым пригодным способом исследования repository; конкретная
shell-команда и grep не обязательны. `closure_summary` объясняет, почему radius
достаточно исследован, и не заменяет structured entries.

## Scope classification

- **IN_SCOPE** — consumer непосредственно зависит от исправляемого semantic/state
  contract, и требуемое изменение может изменить его предпосылки, решения или
  observable behavior. Такой consumer обязан быть проверен и при необходимости
  исправлен в текущей задаче.
- **NOT_AFFECTED** — consumer реально рассмотрен, но repository evidence показывает,
  что требуемое изменение не меняет его контракт или достижимое поведение. Нужны
  `why_considered`, `reason`, concrete paths/symbols и непустое evidence. Это не
  перечисление всего repository: включаются только materially plausible consumers.
- **RELATED_OUT_OF_SCOPE** — отдельная связанная проблема, которая не является
  следствием исправляемого root cause и не требуется для сохранения корректности
  изменения. Нужны `relation`, `reason`, evidence и `suggested_follow_up`. Она не
  исправляется молча и сохраняется как явная follow-up finding.

Если агент обнаружил связанную проблему:

- IN_SCOPE → обязан решить/проверить сейчас;
- RELATED_OUT_OF_SCOPE → обязан явно сообщить как follow-up;
- не обнаружил существенного reachable consumer → это failure автономного
  исследования, а не повод дописывать consumer в user prompt задним числом.

## Planner obligations

Planner работает read-only относительно candidate. Он сверяет RAW USER REQUEST
с User Task Contract, исследует текущий repository и возвращает `planner.v6`.
`READY` требует обязательный typed `impact_closure`:

| Поле | Содержание |
| --- | --- |
| `applicable` | Применимость graph анализа к задаче. |
| `changed_contracts` | `name`, `before`, `after`, `evidence_paths`, `evidence_symbols`. |
| `in_scope_consumers` | `name`, `paths`, `symbols`, `why_affected`, `required_behavior`, `evidence`, `required_proof`. |
| `not_affected_consumers` | `name`, `paths`, `symbols`, `why_considered`, `reason`, `evidence`. |
| `related_out_of_scope` | `name`, `paths`, `symbols`, `relation`, `reason`, `evidence`, `suggested_follow_up`. |
| `search_evidence` | `target`, `method`, `evidence_paths`, `conclusion`. |
| `closure_summary` | Конкретное обоснование достаточности исследования. |

`required_proof` использует общий `PROOF_TARGET_SCHEMA`: `claim`, `level`,
`capabilities`. Все wire-level properties обязательны, каждый object запрещает
additional properties. Незавершённый Planner возвращает соответствующий stop
status с причиной и может оставить impact arrays пустыми; `READY` так не закрывается.

Controller для applicable `READY` требует непустые changed contracts и search
evidence. Он проверяет все path-bearing поля: safe repo-relative paths, наличие
файла в current workspace и containment после разрешения symlinks. Пустые строки,
пустые evidence lists и symbols в виде общей прозы/placeholder отклоняются.
`before` и `after` должны различаться. Consumer names уникальны, классификации
не пересекаются по имени.

В `planner.v6` единственный consumer ledger — `impact_closure.in_scope_consumers`.
Controller непосредственно компилирует его claims и proof, сохраняя immutable
origin в `impact-sources.v1`. Второй `affected_consumers` в Planner wire запрещён.
Compiler сохраняет каждую строку IN_SCOPE; ни один consumer
не может быть потерян до компиляции. Число consumers не ограничено искусственным
порогом компактности. NOT_AFFECTED и RELATED_OUT_OF_SCOPE не компилируются в
obligations. Follow-up findings сохраняются в полном Planner artifact и входят
в обязательный Controller-owned user handoff текущей финальной модели.

Для READY engineering/code task default — `applicable=true`. `applicable=false`
не требует fake consumer ledger, но разрешён только при независимом основании:
Controller передаёт непустой `owner_allowed_paths` из manifest `allowed_paths`.
Каждый owner path обязан быть safe repo-relative существующим regular file с
расширением `.md`, `.rst`, `.txt` или `.adoc`; canonical resolution остаётся внутри
workspace и сохраняет prose extension. Directories, globs, отсутствующие файлы,
code/config files, symlink/junction escape и смешанная prose/code boundary запрещают
исключение. Search evidence paths должны быть subset этих конкретных owner paths.
Planner-generated paths, очищенные declarations и длинное объяснение не создают
authority. Даже genuine prose task без owner boundary не может получить READY с
`applicable=false`: нужен applicable closure либо другой честный status.

Этот маршрут также требует summary с evidence path и конкретным объяснением
отсутствия behavioral impact. Механический минимум summary — хотя бы
шесть слов объяснения помимо path. Planner обязан проверить, что текст не служит
исполняемым/config/API/state contract; расширение файла само по себе этого не доказывает.
Все четыре contract/consumer arrays, risks, State Model
collections и consumer/boundary proof arrays пусты; state model неприменим.
Proofs только `LOCAL_DETERMINISTIC` с `GIT`/`DOCS_SYNC` либо без capabilities.
Поведенческий code change требует `applicable=true`, даже если patch очень мал.

Deterministic validation доказывает структуру и согласованность declarations,
но не может сама доказать истинность prose, существование заявленной semantic
связи или исчерпывающий поиск всех consumers. Эти обязанности остаются у агента
и независимого review; fabricated evidence не становится корректным от прохождения schema.

Capability-aware planning сохраняется: Controller сообщает только подтверждённые
`AVAILABLE_VERIFICATION_CAPABILITIES`; один corrective capability turn возвращает
полный artifact, сохраняя closure и синхронизируя proof в обеих consumer arrays.
Повторный невыполнимый READY даёт `PLANNER_CAPABILITY_INFEASIBLE` до compiler.

Controller обязан готовить актуальное tool evidence до каждого initial/fresh
Planner: current candidate binding → завершённый reset/runtime rebuild → current
owner toolchain/config references → refresh stale probes → проверка результата →
available capabilities. Инвалидация означает необходимость перепроверки, а не
отсутствие инструмента. Прежний descriptor разрешает запустить probe, но прежний
PASS или configured path не заменяют свежего evidence. Rejected candidate-only
config/test paths не переносятся в новую модель. Required probe failure даёт
controlled stop до Planner; owner gates и post-plan gates не ослабляются.
Controller tool/config probe PASS, возможность exploratory команды в read-only
Planner sandbox и PASS продуктовых tests — три разных утверждения.

Semantic preservation requirement и proof route не равны. `PRESERVE-1` сохраняет
обязательное user behavior; `evidence_plan.preservation` выбирает способ доказать его
и не расширяет product scope до исправления всего baseline debt. Absolute PASS broad
suite допустим как hard proof только для owner-configured project gate либо при
concrete evidence зелёного baseline именно этого suite. При baseline-red или неизвестном
baseline нужны targeted contract/consumer regressions или другой достаточный proof.
Assertions проверяют affected observable behavior, а не только соседний helper.
Baseline-red не доказывает, что failing consumer unrelated: impact sweep остаётся полным.

## Implementer obligations

Implementer реализует все acceptance, preservation, state, consumer и risk
obligations, проверяет их typed proofs и синхронизирует документацию поведения.
Implementation Contract остаётся open-world: найденные material obligations
добавляются через существующий Controller-owned expansion, а не теряются.
Technical evidence не создаёт новый explicit user intent.

`implementer.v6` требует `terminal_reason_kind` с точным соответствием status:

| Status | terminal_reason_kind |
| --- | --- |
| COMPLETE | NONE |
| REPLAN_REQUIRED | TECHNICAL_MODEL_DIVERGENCE или PROOF_MODEL_DIVERGENCE |
| BLOCKED | INFRASTRUCTURE_BLOCKED |
| NEEDS_USER_DECISION | USER_DECISION_REQUIRED |

Planner proof plan is a hypothesis. Доказанно непригодный Planner-derived proof route
из-за pre-existing unrelated baseline failures требует `REPLAN_REQUIRED` с
`PROOF_MODEL_DIVERGENCE`, concrete reason и evidence: route, baseline facts, независимость
failing area от текущего impact, target regression и owner-check results. Semantic model
может оставаться верной. Нельзя исправлять unrelated code/tests ради green proof, объявлять
preservation выполненным без evidence или возвращать `BLOCKED` из-за плохого proof route.
`INFRASTRUCTURE_BLOCKED` означает недоступную обязательную capability/операцию/system,
которую autonomous repair/replan не может восстановить.

Для `PROOF_MODEL_DIVERGENCE` Controller сохраняет candidate и вызывает независимый
read-only Planner с `proof-route-review.v1`. READY означает подтверждение прежней
product model. Planner возвращает только proof changes по item IDs; Controller
сохраняет исходные requirements, source records и checks, добавляет proof revision,
инвалидирует downstream evidence и продолжает тот же Implementer thread с новым
self-verify. `TECHNICAL_MODEL_DIVERGENCE` использует отдельный clean semantic reset.
Неопределённый proof review сохраняет candidate и даёт controlled stop.
Owner-configured checks остаются обязательными даже при baseline-red. IN_SCOPE
regressions и semantic preservation не становятся advisory. Независимые baseline defects
сохраняются как RELATED_OUT_OF_SCOPE в текущей final model и доставляются через user handoff.

Existing tests регистрируются только как material evidence для active Contract
consumer/risk/state/acceptance requirement.
Exploratory broad suites, baseline-red unrelated tests и RELATED_OUT_OF_SCOPE diagnostics
не продвигаются в authoritative `registered_checks`. Changed/new regressions по-прежнему
требуют trusted verification.

Planner impact closure is a hypothesis to verify against the actual patch.
В `implementer.v6` COMPLETE требует обязательный `post_patch_impact`: новый sweep
фактического candidate/diff после реализации и до final self-verification.
Planner closure задаёт starting technical model, но не границу исследования.
Implementer проверяет реально изменённые contracts/shared symbols/state/API,
их writers/readers/decision points, sibling consumers и новые material risks.

Controller переносит Planner claims без изменений и выдаёт `source_id`/`source_revision`.
Implementer возвращает для каждого origin явную `source_assessments` запись:
CONFIRM, CHALLENGE, PROMOTE либо INSUFFICIENT_EVIDENCE, собственное observation и
paths/symbols/evidence текущего workspace. Отсутствие assessment не означает CONFIRM.
Unknown/duplicate/stale references отклоняются. CHALLENGE и INSUFFICIENT_EVIDENCE
несовместимы с COMPLETE. Новая product model требует `REPLAN_REQUIRED` с evidence.
Controller использует общий semantic reset → fresh Planner → новый Contract → fresh
Implementer; такое расхождение не устраняется молчаливым редактированием Contract.

NOT_AFFECTED и RELATED_OUT_OF_SCOPE origin можно явно PROMOTE в новую IN_SCOPE
observation. Controller сохраняет исходную запись и transition к target reference;
последующий report не может молча вернуть прежнюю outside classification.
Новые observations имеют уникальный `observation_id` и передаются один раз.
`discovered_obligations` отсутствует в wire: Controller сам выводит obligations из
новых observations, включая исходные Evaluator findings до continuation, и расширяет
Contract и Verification Plan, инвалидирует self-verification и продолжает тот же
Implementer thread. COMPLETE закрывает все новые items; повторное событие с тем же
ID/payload idempotent, изменённый payload с тем же ID отвергается. Все origins
остаются явно assessed в последующих reports. RELATED_OUT_OF_SCOPE findings,
включая Planner relation/reason/evidence/follow-up, сохраняются отдельно и не
становятся obligations.

Каждый фактически changed path reviewed exactly once в `changed_path_review`.
Для Controller-known deletion existence не требуется; остальные evidence paths
обязаны существовать и оставаться внутри workspace. `OTHER_JUSTIFIED` требует
конкретного объяснения и path-linked evidence. Summary не заменяет structured rows.

FULL сохраняет Planner applicability. Если Planner считал impact неприменимым,
обнаруженный behavioral impact требует REPLAN_REQUIRED. FAST без Planner сам
строит closure и проводит material consumers/risks через DISCOVERED expansion.
Исключение `applicable=false` использует ту же owner-backed prose-only policy:
search и actual changed paths являются subset непустой safe regular-prose-file
boundary. Behavioral/state/runtime obligations не допускаются. Non-COMPLETE
reports требуют reason/evidence, но могут оставлять impact arrays пустыми/частичными.

После validated final COMPLETE Controller сохраняет private authoritative
`implementation_impact_closure_NN.json` (`implementation-impact-closure.v2`),
связывающий candidate_id, Planner/Contract fingerprints, exact changed paths,
post_patch_impact и текущий revision binding стабильным fingerprint. Artifact
входит в Implementer stage evidence. Изменение candidate/Plan/Contract/revisions
делает его stale: после repair требуется новый report, sweep и self-verification.
Current Controller-private receipt и все integrity checks остаются обязательными.

Не отождествлять test framework с расширением файла или доступностью Jest. Все
material registered checks сохраняются и получают подтверждённый Controller runner;
ошибка compiler не исправляется новой продуктовой реализацией или выбором случайно
зелёной команды. Owner gates и assertions остаются обязательными.

Локальное неполное evidence отчёта допускает не более двух report-only corrections
в том же Implementer thread. Уточняются только указанные evidence поля; candidate,
семантика, findings, consumers и checks сохраняются. Реальные documentation link
targets/anchors могут служить identifiers. Пустые arrays не принимаются автоматически.
Full validation, trusted verification и expansion после correction обязательны.
Исходные invalid artifacts сохраняются private; terminal observation честно отражает
фактический candidate либо невозможность безопасного наблюдения. См. D-025/D-026
в [журнале решений](DECISIONS.md) и [Phase 4](PHASE4_EXECUTION.md).

## Evaluator obligations

Evaluator независимо проверяет candidate и достаточность evidence, ищет
необнаруженных consumers, достижимые регрессии и false-green assertions. Наличие
Planner ledger не доказывает корректность реализации и не заменяет blind audit.
Planner closure ≠ proof of full impact. Implementer post-patch closure ≠ proof of full impact.
Evaluator independently reconstructs the impact model from the actual candidate
before seeing either closure. Evaluator PASS requires evidence-backed challenge of
both prior closures.

В Phase A `evaluator.v7` самостоятельно исследует changed semantic/state contracts,
shared symbols/API, readers/writers/decision points и sibling consumers. Changed paths
служат seed для outward sweep, а не границей review. Каждый changed path рассматривается
ровно один раз, включая deletion. Остальные evidence paths должны быть существующими
regular files с canonical target внутри workspace. Engineering impact обязателен;
prose-only exception использует shared owner-backed policy из `impact.py`.

`blind-audit.v2` содержит independent `impact_analysis`, собственные stable impact IDs
и current candidate ID. Controller валидирует и immutable-сохраняет audit **до** раскрытия
normalized Planner impact, implementation impact artifact, Contract и checks/runtime
evidence. Planner reasoning и raw Implementer report в обеих фазах скрыты.

Phase B обязана disposition каждый blind contract и affected consumer, Planner IN_SCOPE,
Implementer DISCOVERED, все NOT_AFFECTED и RELATED_OUT_OF_SCOPE из трёх ledgers и каждый
changed path. Blind names не обязаны повторять Planner vocabulary: используются собственные
IDs и explicit matches к normalized prior names. NOT_AFFECTED требует независимого
подтверждения; согласие Planner и Implementer не является доказательством.

Negative disposition требует соответствующих final material findings с failure mode,
required action и typed proof. Она запрещает PASS. Ошибочно вынесенная out-of-scope проблема
возвращается в scope; подтверждённые отдельные follow-ups сохраняются вместе с immutable
blind model и Phase B dispositions. Наличие regression test не доказывает корректность,
если assertion покрывает только helper и пропускает affected consumer semantics.

Исправимые CONSUMER/RISK findings используют Controller expansion и Implementer repair.
MATERIAL_GAP и MODEL_CONFLICT в blind changed-contract dispositions допустимы только с
REPLAN_REQUIRED, concrete reason и соответствующим final material finding. PASS, FINDINGS,
BLOCKED и NEEDS_USER_DECISION с этими dispositions запрещены: текущая technical model
недостаточна и требует semantic reset, fresh Planner, нового Contract и fresh Implementer.
Candidate change
делает прежние blind audit/challenge stale: после repair нужен fresh Evaluator thread.
Перед Phase B Controller повторно проверяет candidate/Plan/Contract/revision binding
implementation-impact-closure. FAST policy не меняется и Evaluator не запускается.

## User follow-up delivery

IN_SCOPE → solve now. RELATED_OUT_OF_SCOPE → preserve → independently challenge
where Evaluator exists → mandatory user follow-up delivery → suggested next task.
User is not responsible for reading internal artifacts to discover follow-up work.
Пользователь получает описание проблемы, её связь с задачей, причину исключения из
текущего scope, repository paths/symbols/evidence и конкретную следующую задачу.

Controller создаёт `user-follow-up.v2` в private authoritative copy и public immutable
`user_follow_up_report.json` после final agent loop и до held-out. Console всегда
показывает путь и count, в том числе 0, а для каждой finding — title и next task.
FULL использует только current Planner/Implementer/blind findings с независимой
Evaluator disposition CONFIRMED_OUT_OF_SCOPE. FAST не запускает Evaluator и честно
маркирует Implementer findings DECLARED_OUT_OF_SCOPE_FAST.

Дедупликация разрешена только по точной нормализованной relation/reason/next-task
и paths/symbols; evidence и provenance объединяются. Название само по себе не
доказывает тождество. Current IN_SCOPE obligation нельзя спрятать в follow-up.
Rejected attempts не добавляются в final inventory. Candidate, attempt, revisions
и source fingerprints привязаны к report; stale, tampered или неполный handoff
запрещает `final-acceptance.v3`, даже при count=0. Hidden held-out output не является
источником handoff. Report сохраняется при последующем held-out/reconstruction/
delivery failure и не входит в project patch.

## Definition of COMPLETE

Нормативно полное engineering-решение удовлетворяет исходному intent, сохраняет
explicit ограничения, проверяет/исправляет все IN_SCOPE последствия и сохраняет
RELATED_OUT_OF_SCOPE follow-ups. Локальное устранение симптома при пропущенном
существенном reachable consumer не является полным результатом.

Wire-status Implementer COMPLETE остаётся его отчётом по действующему protocol,
а не финальным authority. Принятие результата требует действующих independent
checks, runtime proof при необходимости, Evaluator и Final Gate на одном candidate.
Candidate/Git integrity, runtime projection, static preflight, reconstructed
verification и benchmark isolation не ослабляются этим документом.

## Журнал решений и execution evidence

Существенное решение и его изменение записываются в [DECISIONS.md](DECISIONS.md)
в том же patch: проблема, проверенные основания, выбор и причина, реально
рассмотренные альтернативы, издержки, preservation boundaries, статус решения
отдельно от реализации и условия пересмотра. Stable D-IDs и опровергнутые выводы
сохраняются. REJECTED/SUPERSEDED подход нельзя возвращать без новых evidence или
изменения intent владельцем. Достаточно читать применимые записи по карте решений.

Planner/Evaluator вправе выполнять исследовательские проверки в собственном
Controller-selected writable scratch при read-only project/tests/dependencies.
Environment path не является permission grant. Requested policy, reported policy,
фактические filesystem операции, Controller probe и выполненный product assertion
доказывают разные свойства. Native acceptance описан в
[Windows setup](WINDOWS_SETUP.md#native-scoped-scratch-acceptance), rationale — D-018.

## Anti-coaching / benchmark integrity

Нельзя улучшать качество агента путём добавления в пользовательский prompt
конкретных hidden edge cases, файлов, функций или решения, которые агент
должен был самостоятельно обнаружить по repository.

Benchmark должен измерять autonomous discovery, а не способность следовать
подсказанному patch plan. Пропущенный consumer — дефект autonomous investigation,
а не основание задним числом раскрыть его в user prompt ради зелёного результата.
Агенту не предоставляются hidden grader, reference solution, previous fixed commit
или другие копии решения. Generic Harness regressions используют synthetic
repositories и не кодируют hidden scenarios конкретного benchmark.

## Failure modes Harness must reject

- «Нашёл место, где ломается кнопка → исправление локально здесь → READY» без
  исследования consumers изменяемого shared contract/state.
- Сведение technical impact radius к user-mentioned files или размеру patch.
- Abstract «shared consumers» без concrete paths/symbols/evidence/proof.
- READY без changed contracts/search evidence или с escaping/nonexistent paths.
- Потеря IN_SCOPE consumer, ослабление его required behavior/proof при handoff.
- NOT_AFFECTED без evidence; скрытый перевод независимой проблемы в obligation.
- RELATED_OUT_OF_SCOPE без relation/reason/evidence/follow-up или её молчаливая потеря.
- Behavioral code change, механически спрятанный за `applicable=false`.
- Evaluator PASS по согласованным прежним ledgers без независимого blind impact sweep.
- Missing/duplicate/extra impact dispositions, negative disposition без material finding.
- Потеря blind impact model или его повторное использование после candidate change.
- Новая feature от Planner, ослабление verbatim user claims или owner boundary.
- Подгонка prompt/tests под hidden benchmark вместо исправления автономного исследования.
