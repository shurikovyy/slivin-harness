from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = os.environ.copy()
ENV["PYTHONDONTWRITEBYTECODE"] = "1"


def run(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===")
    result = subprocess.run(command, cwd=ROOT, env=ENV)
    if result.returncode != 0:
        raise SystemExit(f"HARNESS_SELF_CHECK_FAIL: {label} exit={result.returncode}")


def compile_sources() -> None:
    print("=== PYTHON COMPILE ===")
    sources = [ROOT / "task_runner.py"]
    sources.extend(sorted((ROOT / "slivin_harness").glob("*.py")))
    sources.extend(sorted((ROOT / "tools").glob("*.py")))
    for path in sources:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    print(f"COMPILE_PASS files={len(sources)}")


def main() -> int:
    compile_sources()
    run(
        "MANIFEST VALIDATION",
        [
            sys.executable,
            "task_runner.py",
            "examples/project-task.example.toml",
            "--validate-only",
        ],
    )
    run(
        "MATRIX MANIFEST VALIDATION",
        [
            sys.executable,
            "task_runner.py",
            "cases/matrix-all-matching/task.toml",
            "--validate-only",
        ],
    )
    run(
        "UNIT TESTS",
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
            "-v",
        ],
    )
    run("DOCUMENTATION SYNC", [sys.executable, "tools/check_docs_sync.py"])
    print("\nHARNESS_SELF_CHECK_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
