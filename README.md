# Slivin Harness

Local coding-agent harness built around Codex App Server.

The repository stores only Harness source, task manifests, hidden graders and small
calibration fixtures. Real project workspaces, run logs, secrets and generated caches
are intentionally excluded from Git.

## Repository policy

**Do not create source copies such as `task_runner_v046.py`.**
Version Harness with Git commits/tags. `CHANGELOG.md` records notable milestones.

## First setup on a machine

Default local paths match the current Windows/Git-Bash setup:

- Codex CLI: `~/Tools/codex-cli/node_modules/.bin/codex.cmd`
- Node: `~/Tools/node/node.exe`
- Jest: `~/Documents/sa_icover/node_modules/jest/bin/jest.js`
- Harness Python: the Python executable used to launch `task_runner.py`

If paths differ:

```bash
cp harness.local.example.toml harness.local.toml
```

Edit `harness.local.toml`. It is ignored by Git.

## Prepare the historical `_90` benchmark

After clone, copy **the contents** of `_90` into:

```text
cases/matrix-all-matching/workspace/
```

Do not copy `_90/.git` if the archive contains one.

Then initialize the disposable inner baseline repository:

```bash
./py tools/prepare_workspace.py cases/matrix-all-matching/workspace
```

If you prefer to call Python directly:

```bash
~/Documents/sa_icover/.venv/Scripts/python.exe \
  tools/prepare_workspace.py \
  cases/matrix-all-matching/workspace
```

`prepare_workspace.py` removes generated caches, refuses `.env*` files, creates the
inner baseline commit and adds `.harness_tmp/` plus runtime patterns to
`.git/info/exclude`.

A full `_92` copy is **not required**. The historical held-out grader has a
hash-bound calibration certificate under `hidden_checks/`. The certificate records
that the current grader/check definition produced `_90 → FAIL` and `_92 → PASS`.
If the grader changes, Harness invalidates the certificate and requires explicit
recalibration before another historical benchmark.

## Run the current Matrix benchmark

From anywhere:

```bash
~/Tools/slivin-harness/run cases/matrix-all-matching/task.toml
```

or from the Harness root:

```bash
./run cases/matrix-all-matching/task.toml
```

`run` resolves the Harness root itself, so the current shell directory does not
matter.

If you launch Python manually, use the full runner path when you are not in the
Harness root:

```bash
~/Documents/sa_icover/.venv/Scripts/python.exe \
  ~/Tools/slivin-harness/task_runner.py \
  cases/matrix-all-matching/task.toml
```

## Why the previous command failed

This command was run while the shell was inside the project workspace:

```bash
python task_runner.py cases/matrix-all-matching/task.toml
```

Python resolves `task_runner.py` relative to the **current directory before Harness
starts**, so it searched for:

```text
cases/matrix-all-matching/workspace/task_runner.py
```

That file does not exist. Use `./run` or the absolute path shown above.

## What is intentionally not in Git

See `.gitignore`. In particular:

- `cases/**/workspace/` and full local `_90`/`_92` copies;
- `runs/`, `.harness_tmp/`, logs and scratch files;
- `.env*`, keys and machine-local `harness.local.toml`;
- Python/Jest caches, virtualenvs, `node_modules/`;
- generated `protocol-schema/`;
- archives and old version-copy files.

## Development workflow

Before a Harness change:

```bash
git status --short
```

After a coherent change:

```bash
git add <files>
git commit -m "..."
```

Use tags for milestones, e.g. `v0.4.6`. Do not keep parallel versioned source files.
