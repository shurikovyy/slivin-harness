# Windows setup для Slivin Harness 0.8.0a20

## Целевая среда

```text
Windows 10/11
Git for Windows / Git Bash
Codex CLI / App Server
Python для Harness
configured bootstrap Python проекта
project-specific Node/Jest
owner-provided runtime wrappers when runtime proof is required
```

## Проверка release

```bash
cd ~/Tools/slivin-harness-080a11-phase7
./py -c "import slivin_harness; print(slivin_harness.__version__)"
./py tools/self_check.py
```

Ожидаемо:

```text
0.8.0a20
DOCS_SYNC_PASS harness=0.8.0a20 ...
HARNESS_SELF_CHECK_PASS
```

На native Windows несколько capability-dependent tests могут быть `skipped`; итог всё равно
обязан быть `OK` и `HARNESS_SELF_CHECK_PASS`.

## Python 3.12 проекта

```toml
[projects.sa_icover.runtime]
bootstrap_python = "C:/Users/Slivin.Aleksandr/AppData/Local/Programs/Python/Python312/python.exe"
expected_python = "3.12"
venv = ".venv"
dependency_files = ["requirements.txt"]
pip_install_args = ["--disable-pip-version-check"]
```

Harness использует абсолютный entry point:

```text
<worktree>/.venv/Scripts/python.exe
```

Activation через `source` не требуется. Ownership проверяется канонически: entry point обязан
принадлежать Controller-owned worktree `.venv`, а не совпадать с workspace по строковому
`startswith()`.

На POSIX/WSL `.venv/bin/python` может быть symlink на bootstrap interpreter.
Проверяется canonical parent/target, но probe и project checks запускают именно
lexical worktree entrypoint; `sys.prefix` должен указывать на этот venv.

## Runtime Verification scenarios

Пример local runtime:

```toml
[projects.sa_icover.runtime_verification]
enabled = true

[[projects.sa_icover.runtime_verification.scenarios]]
id = "local-app-smoke"
profile = "LIVE_LOCAL"
capabilities = ["LOCAL_APP"]
startup_command = ["{python}", "manage.py", "runserver", "127.0.0.1:{runtime_port}", "--noreload"]
health_command = ["{python}", "tools/runtime_health.py", "{runtime_port}"]
command = ["{python}", "tools/runtime_local_smoke.py"]
timeout_seconds = 300
startup_timeout_seconds = 60
```

Place wrapper-generated logs/results in the path passed through `runtime-request.v1`; do not
write tracked files. Runtime ports and scratch directories are task-local.

A `TEST_EXTERNAL` wrapper must use a test endpoint and either `disposable = true` or a
`cleanup_command`. A `PROD_OBSERVE` wrapper requires `read_only_enforced = true`; do not use a
production write-capable credential and rely on a prompt instruction.

## Windows command and environment details

- Use list-form commands in `harness.local.toml`; do not embed shell pipelines.
- Use `{python}` for the worktree-local Python and `{runtime_port}` for the Controller-assigned
  LIVE_LOCAL port.
- `preserve_env` is explicit. Only name variables required by the wrapper; their values remain
  machine-local and must not be printed.
- Startup is run without autoreload. A long-running server writes stdout/stderr to runtime
  scratch files rather than a bounded pipe.
- Controller distinguishes semantic result, command timeout and infrastructure failure.

## `.worktreeinclude`

```gitignore
.env
.env.local
```

Only matching ignored regular files are copied. Symlink/junction/reparse traversal is rejected
on every path component. Small runtime-only files are protected by Controller-private keyed HMAC
and restored if a role changes them.

## Source-owned Node/Jest runtime projection

For a historical benchmark with source-local Jest, declare the already installed
dependency tree only in the selected local profile:

```toml
[projects.sa_icover.workspace]
copy_untracked = ["node_modules"]
```

The path must be non-empty and repo-relative: no `..`, absolute path, drive
path or UNC path. Exact duplicates, slash/case-normalized duplicates and
parent/child overlaps are rejected before workspace creation, independent of
configuration order. Independent siblings such as `.env.local` and
`node_modules` remain valid. Do not put `node_modules` in tracked
`.worktreeinclude`.
Controller copies the tree into the managed workspace and rejects source or
destination symlink/junction/reparse aliases; it does not use symlink,
junction or hardlink projection. The copied dependency tree is ignored by Git,
never enters candidate/patch/delivery/reconstruction and disappears with the
managed workspace. `npm install`/`npm ci` is not run there.

Controller keeps a private keyed full-tree baseline. Before a trusted check it
verifies that source still matches that baseline and restores a changed
workspace projection by bounded delete + physical recopy. After the check it
re-hashes the full tree; any mutation invalidates that attempt even when the
command returned zero, then triggers restore. Fingerprinting rejects nested
symlink/junction/reparse and unsupported objects and detects case-colliding
relative names. Junctions and hardlinks are never used for projection.

This is source-safe detect/restore, not an NTFS ACL immutability claim. It costs
O(total projected bytes) before and after each trusted batch and retains a
limited check-to-exec TOCTOU boundary. Sharing-violation retries are bounded;
exhaustion returns `RUNTIME_PROJECTION_WORKSPACE_RESTORE_FAILED` and the partial
destination is not trusted.

After rebind, Stage 0 runs bounded `shell=False` probes from the managed
workspace with the Execution Broker environment: `git --version`, the required
Python/Node `--version`, `node jest --version`, and Jest `--showConfig` with an
explicit config or normal workspace-cwd discovery. The last command loads the
configured test environment but does not run tests. Projected probes use the
full-tree pre/post guard above; canonical candidate identity separately rejects
tracked/untracked/deleted project mutations. Complete stdout/stderr is stored
only under the Controller-private run plane, outside the managed workspace, and
combined output is hard-capped at 1 MiB. Overflow terminates the process group
and is reported as `STATIC_TOOLCHAIN_PROBE_OUTPUT_LIMIT` without raw output.
Only toolchain entries referenced by manifest commands are required at this
point; an unused configured `project_python` is not probed. New tool-backed
requirements can still be probed by the post-plan capability gate.
Planner receives this probe-backed capability set before selecting proof routes;
one infeasible READY gets a single corrective turn before Contract compilation.
An invalid optional `project_python` therefore cannot become available merely
because it is present in `harness.local.toml`.

## Canonical path checks

Windows paths can have different lexical spellings for the same location. Controller Plane,
Execution Broker, project runtime and runtime evidence use canonical containment and guard:

```text
relative escape ..
absolute drive path
UNC path
drive mismatch
private-root aliases
similar-prefix siblings
```

Git/NTFS may not expose a chmod-only diff. The executable-mode identity test performs a
capability probe and skips only that unsupported condition.

## Two-phase Evaluator

Evaluator uses a fresh scratch-only permission profile shared with the Planner execution
contract below. Phase A is persisted before Phase B. Both phase boundaries re-check
candidate identity. Evaluator scratch is writable; candidate files remain read-only.

## Native scoped scratch acceptance

Codex 0.153.4's generated experimental App Server schema supports `permissions` on
thread/start and turn/start, per-thread `config`, `activePermissionProfile` and
`runtimeWorkspaceRoots`. Harness enables that API, creates a unique named profile
with `:root = read` and the role scratch `= write`, disables network and keeps
`approvalPolicy=never`. It does not combine this with legacy `sandbox` overrides.

The native unelevated restricted-token runner rejects split writable roots when
the session cwd remains the project. Harness therefore uses
`<project>/.harness_tmp/<role>/session-*` as the session root and sole write root.
The agent sets command workdir to the project for repository-relative tests/search/Git.
Ancestor AGENTS.md discovery remains available; nested instructions are read in the
project context. A fresh session is empty and cannot inherit temporary AGENTS.md. Scratch uses ordinary
inherited mkdir permissions: Python 3.13+ mkdtemp mode 0700 creates a protected DACL
that the tested restricted-token runner cannot read. No manual ACL grants are added.
TEMP/TMP/TMPDIR, XDG and npm cache settings belong to that thread, not the shared
App Server scratch. Semantic reset archives old scoped threads before deleting their
session cwd; their rollouts remain in local Codex storage for audit. These scoped
sessions are materialized because 0.153.4 cannot archive an ephemeral thread.
Intake/Implementer retain their previous ephemeral setting. Reset uses validated extended-length paths on Windows
to remove long Jest cache names; cleanup errors are reported, not ignored. Corrective turns and Evaluator A/B retain the same context.

Requested settings and reported effective metadata are saved as
`harness_execution_context` in thread evidence. A mismatched/unsupported profile
stops with a typed diagnostic; there is no broad-write fallback. Metadata validation
does not label actual filesystem probes PASS. Read access remains the prior broad
read access: the tested unelevated runner rejects deny-read rules. This change does
not claim new OS-enforced private-read isolation or a universal Controller sandbox.
See [D-018](DECISIONS.md) for evidence and rejected alternatives.

Run the opt-in LLM-backed smoke with existing installed tools and an existing
`node_modules` tree. Use a new output directory outside Harness and the source project/runtime:

```powershell
py tools/smoke_readonly_scratch.py --codex C:\Tools\codex-cli\node_modules\.bin\codex.cmd --node C:\Tools\node\node.exe --runtime-source C:\DisposableRuntime\node_modules --output C:\Temp\scratch-acceptance-new
```

The tool physically copies dependencies, initializes only a disposable synthetic Git
repository and runs production `CodexAppServer`/`ExecutionBroker` with real Planner and
Evaluator instructions. It never installs packages or changes ACL/config/sandbox access.
It records actual command/cwd/exit/output, thread policy, versions, cached cold/warm and
fresh-role Jest passes, an executed intentional assertion failure, scratch write/read,
and denied absolute/relative project/test/dependency/Git/private/peer/neighbor operations.
Peer checks also target the granted scratch of another active role in both directions;
an ungranted sibling directory alone does not prove per-thread isolation.
Continuations check the same policy; fresh Planner follows scratch cleanup. Existing
unit/integration tests additionally cover real `run_planner` corrective/replan and
Evaluator blind A→immutable persistence→B routing; the native smoke does not certify
semantic role output or a full product trial. Child `spawnSync` is recorded separately.
Source runtime fingerprints and candidate/Git/runtime guards check invariance.
Logs remain outside Git. This opt-in check is not part of offline self-check.

## Final Gate и delivery

Git ignore rules и persistent index не являются candidate authority. Physical
inventory видит ignored additions и tracked bytes независимо от index flags;
HEAD/index/local/worktree config/info controls/hooks проверяются отдельным
Git-control guard. Patch staging использует disposable private
`GIT_INDEX_FILE`, `git add -f` и empty hooks directory, поэтому real workspace
index остаётся неизменным.

Trusted checks и agent self-verify тоже используют disposable index из `HEAD`.
Это особенно важно после semantic reset: `git diff --check` на Windows может
обновить stat cache даже при `GIT_OPTIONAL_LOCKS=0`, но теперь такая запись не
попадает в real worktree index.

Generated Harness-owned `self_verify.py` явно переводит stdout/stderr в UTF-8
до запуска checks. Поэтому Unicode Jest output (включая `√`) не зависит от
legacy console code page вроде CP1251.

Git-control restore использует только original paths baseline. Worktree/private
standalone controls могут быть восстановлены; common/source/external refs,
hooks, config и targets — detect-only. Loose/packed refs, replace refs, shallow,
alternates/grafts и split-index controls входят в bounded snapshot. Mutated
config target не сканируется и не изменяется. Candidate inventory исключает
только explicit Controller-owned roots; обычные cache names остаются candidate.

После Evaluator PASS Controller строит patch proof, materializes fresh proof
runtime и повторяет static preflight, repair checks и benchmark held-out в clean
reconstruction repo. Только `reconstructed-verification.v1=PASS` разрешает
`final-acceptance.v3`; перед Final Acceptance также обязателен `user-follow-up.v2`.
Для `apply_to_source` используется короткий cross-platform
delivery lock в Git common-dir. Controller повторно проверяет source HEAD, clean
state и preimages до apply, а затем exact patch/postimages.

Patch reconstruction не принуждает LF. Она читает effective `core.autocrlf`/`core.eol` и связанные безопасные checkout settings source repository до создания verification checkout. Поэтому accepted CRLF worktree и reconstructed worktree имеют одинаковые bytes; arbitrary source Git configuration при этом не копируется.

Если source изменён пользователем или IDE, Harness не перезаписывает его:

```text
RESULT_DELIVERY_BLOCKED
accepted candidate.patch retained
managed worktree retained
```

Historical benchmark не использует linked worktree: создаётся standalone one-commit repository без shared Git objects/refs. На native Windows baseline с symlink или submodule должен быть заранее материализован как обычный fixture.

## Private artifacts and current sandbox boundary

Authoritative evidence remains in:

```text
RUN_DIR/controller_private/
```

Execution Broker records `ENFORCED`, `ADVISORY` or `UNAVAILABLE`. Where native Windows lacks a
universal restricted Controller subprocess runner, the Harness does not claim OS-level
isolation. Runtime wrappers must therefore use scoped credentials and test/read-only endpoints.

## Intake recovery в реальном benchmark

Если Intake сначала спутал требования разных scopes с противоречием, `0.8.0a12` не завершает
benchmark сразу. Ожидаема строка `INTAKE_ARTIFACT_REPAIR`; после неё должен появиться валидный
`USER TASK CONTRACT` и запуск продолжится в Planner. Повторяемая ошибка после двух repair-turns
остаётся fail-closed.

## Перенос local config

`harness.local.toml` is not shipped. Copy only this file between installations. Do not copy
`runs`, `.git`, `.venv`, `.harness_tmp` or Controller-private artifacts.

## Следующий checkpoint

После `HARNESS_SELF_CHECK_PASS` версии `0.8.0a20` запускается historical `_90` case. Новая архитектурная фаза для этого не требуется.
