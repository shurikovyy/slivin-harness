from __future__ import annotations

import py_compile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

for rel in (
    "task_runner.py",
    "slivin_harness/app_server.py",
    "slivin_harness/planner.py",
    "slivin_harness/evaluator.py",
):
    py_compile.compile(str(ROOT / rel), doraise=True)

with (ROOT / "cases/matrix-all-matching/task.toml").open("rb") as handle:
    task = tomllib.load(handle)

assert task["workspace"] == "cases/matrix-all-matching/workspace"
assert task["benchmark"]["calibration_certificate"] == (
    "hidden_checks/matrix_all_matching.calibration.json"
)
assert (ROOT / task["benchmark"]["calibration_certificate"]).is_file()

print("HARNESS_SELF_CHECK_PASS")
