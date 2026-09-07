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
с User Task Contract, исследует текущий repository и возвращает `planner.v5`.
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

IN_SCOPE ↔ `affected_consumers` — взаимно однозначное соответствие по имени
(с нормализацией whitespace и регистра). `why_affected` и
`required_behavior` ↔ `must_verify` совпадают после нормализации whitespace.
Proof claim совпадает после той же нормализации, level совпадает точно,
capabilities сравниваются как множество. Это намеренно детерминированное
соответствие вместо предположения о сходстве двух разных формулировок.
Compiler сохраняет каждую строку `affected_consumers`; ни один IN_SCOPE consumer
не может быть потерян до компиляции. Число consumers не ограничено искусственным
порогом компактности. NOT_AFFECTED и RELATED_OUT_OF_SCOPE не компилируются в
obligations. Follow-up findings сохраняются в полном Planner artifact; отдельный
механизм обязательного final surfacing здесь ещё не реализован.

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
Все четыре contract/consumer arrays, `affected_consumers`, risks, State Model
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

## Implementer obligations

Implementer реализует все acceptance, preservation, state, consumer и risk
obligations, проверяет их typed proofs и синхронизирует документацию поведения.
Implementation Contract остаётся open-world: найденные material obligations
добавляются через существующий Controller-owned expansion, а не теряются.
Technical evidence не создаёт новый explicit user intent.

`implementer.v3` COMPLETE semantics и существующие правила self-verification
сохраняются. Отдельный post-patch Implementer impact closure этим механизмом
Planner не вводится и не считается уже выполненным.

## Evaluator obligations

Evaluator независимо проверяет candidate и достаточность evidence, ищет
необнаруженных consumers, достижимые регрессии и false-green assertions. Наличие
Planner ledger не доказывает корректность реализации и не заменяет blind audit.
`evaluator.v5` PASS semantics сохраняются; отдельное усиление evaluator impact
closure не считается реализованным только благодаря `planner.v5`.

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
- Новая feature от Planner, ослабление verbatim user claims или owner boundary.
- Подгонка prompt/tests под hidden benchmark вместо исправления автономного исследования.
