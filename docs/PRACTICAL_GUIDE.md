# Практическая работа с Slivin Harness 0.8.0a8

## Установка

```bash
cd ~/Tools/slivin-harness-080a7-phase5
./py -c "import slivin_harness; print(slivin_harness.__version__)"
./py tools/self_check.py
```

Ожидаемая версия:

```text
0.8.0a8
```

## FULL workflow

```text
1. Manifest/project preflight
2. .worktreeinclude + managed worktree
3. optional worktree-local project runtime bootstrap
4. User Task Contract
5. fresh Planner v4
6. Implementation Contract v3
7. Verification Plan v1
8. owner/capability gates
9. Implementer v3
10. discovery/check transaction when needed
11. runtime reconciliation + SELF VERIFY
12. independent Controller deterministic checks
13. Runtime step: SKIPPED for local-only proof or BLOCKED until executor exists
14. Evaluator v4 compatibility stage
15. Final Gate compatibility stage
```

## Project runtime configuration

Для Python-проекта добавьте один project-level profile:

```toml
[projects.sa_icover.runtime]
bootstrap_python = "C:/Users/Slivin.Aleksandr/AppData/Local/Programs/Python/Python312/python.exe"
expected_python = "3.12"
venv = ".venv"
dependency_files = ["requirements.txt"]
pip_install_args = ["--disable-pip-version-check"]
```

Harness создаёт `.venv` внутри каждой managed worktree и использует абсолютный:

```text
<worktree>/.venv/Scripts/python.exe
```

`source .venv/Scripts/activate` Controller не требуется.

Если runtime table отсутствует, сохраняется compatibility resolver существующего `project_python`; автоматический rebuild тогда не заявляется.

## `.worktreeinclude`

В корне project repository:

```gitignore
.env
.env.local
```

Только matching ignored files копируются автоматически. Они не входят в patch. `.env`, явно включённый repository owner, не требует второго `allow_sensitive_copy=true`.

`copy_untracked` остаётся дополнительным local override. Sensitive path вне `.worktreeinclude` всё ещё требует explicit opt-in.

## Как читать artifacts

Authoritative artifacts находятся в:

```text
RUN_DIR/controller_private/
```

Типичный порядок:

```text
task_contract_01.json
plan_01.json
implementation_contract_01.json
verification_plan_01.json
capability_gate_01.json
project_runtime_01.json          если runtime configured
check_registry.json
self_verify_receipt_current.json
```

После discovery/check registration могут появиться:

```text
implementation_contract_02.json
verification_plan_02.json
capability_gate_02.json
contract_expansion_02.json
```

Их presence означает не duplicate documentation, а новую active revision Definition of Done.

## Как читать discovery

`implementer.v3` передаёт:

```json
{
  "kind": "consumer",
  "name": "Distribution",
  "reason": "Uses the changed shared authority",
  "required_behavior": "Stage guard remains fail-closed",
  "required_proof": {
    "claim": "Stage guard remains fail-closed",
    "level": "LOCAL_DETERMINISTIC",
    "capabilities": []
  },
  "evidence": ["reachable caller in static/js/distribution/index.js"]
}
```

Controller, а не Implementer, создаёт `CONSUMER-DISCOVERED-N`.

## Что происходит при runtime drift

```text
Implementer installed an undeclared package
или changed requirements.txt
        ↓
Controller detects digest/package drift
        ↓
destroy and rebuild worktree .venv
        ↓
pip install declared requirements
        ↓
pip check
        ↓
new runtime revision
        ↓
old self-verify stale
        ↓
same Implementer repeats verification
```

## Capability gate

Если active proof требует Browser/test external/prod observe, а executor ещё не реализован:

```text
HARNESS_TASK_STOPPED: REQUIRED_CAPABILITY_MISSING ...
```

Это корректный fail-closed result. Не снижайте proof вручную до local-only.

## FAST compatibility profile

Manifest `risk = "low"` пока сохраняет FAST compatibility pipeline. Task Contract всё равно создаётся; Planner/Evaluator могут быть skipped. Это временная совместимость manifest version 2, а не рекомендуемый production quality mode.

## Workflow docs

```bash
./py tools/render_workflow_docs.py
./py tools/check_docs_sync.py
```

Не редактируйте `WORKFLOW.md` и `workflow.v4.json` вручную.

## Ограничения Phase 5

```text
нет universal OS-enforced Controller sandbox;
нет LIVE_LOCAL / TEST_EXTERNAL / PROD_OBSERVE executor;
нет two-phase Evaluator;
semantic replan ещё не получает clean fresh worktree;
final delivery transaction ещё compatibility implementation.
```
