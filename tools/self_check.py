from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

for rel in (
    "task_runner.py",
    "slivin_harness/app_server.py",
    "slivin_harness/console.py",
    "slivin_harness/planner.py",
    "slivin_harness/evaluator.py",
    "slivin_harness/impact.py",
    "slivin_harness/protocol.py",
    "slivin_harness/workspace.py",
    "tools/prepare_workspace.py",
):
    path = ROOT / rel
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")

with (ROOT / "cases/matrix-all-matching/task.toml").open("rb") as handle:
    task = tomllib.load(handle)

assert task["project"] == "matrix_baseline"
assert task["workspace_mode"] == "git_worktree"
assert task["result_mode"] == "keep_worktree"
assert task["benchmark"]["calibration_certificate"] == (
    "hidden_checks/matrix_all_matching.calibration.json"
)
assert task["benchmark"]["confirm_current_baseline_broken"] is True
assert (ROOT / task["benchmark"]["calibration_certificate"]).is_file()

with (ROOT / "harness.local.example.toml").open("rb") as handle:
    local_example = tomllib.load(handle)
assert "projects" in local_example
assert "example" in local_example["projects"]

with (ROOT / "examples/project-task.example.toml").open("rb") as handle:
    project_task_example = tomllib.load(handle)
assert project_task_example["project"] == "example"
assert project_task_example["workspace_mode"] == "git_worktree"

result = subprocess.run(
    [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_*.py",
    ],
    cwd=ROOT,
)
if result.returncode != 0:
    raise SystemExit(result.returncode)

print("HARNESS_SELF_CHECK_PASS")
