from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from task_runner import (
    collect_changed_paths,
    current_diff_text,
    enforce_allowed_paths,
    pipeline_profile_for_manifest,
    workflow_mode_for_manifest,
)
from slivin_harness.workflow import PipelineProfile, WorkflowMode


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


class RunnerHelperTests(unittest.TestCase):
    def make_repo(self) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="slivin-runner-"))
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "baseline")
        return repo

    def test_workflow_mode_distinguishes_production_and_benchmark(self) -> None:
        production = {"checks": [{"feedback": "repair"}]}
        benchmark = {"checks": [{"feedback": "heldout"}]}
        explicit_benchmark = {"checks": [{"feedback": "repair"}], "benchmark": {"x": 1}}
        self.assertEqual(workflow_mode_for_manifest(production), WorkflowMode.PRODUCTION)
        self.assertEqual(
            workflow_mode_for_manifest(benchmark),
            WorkflowMode.HISTORICAL_BENCHMARK,
        )
        self.assertEqual(
            workflow_mode_for_manifest(explicit_benchmark),
            WorkflowMode.HISTORICAL_BENCHMARK,
        )

    def test_pipeline_profile_preserves_manifest_v2_compatibility(self) -> None:
        self.assertEqual(
            pipeline_profile_for_manifest({"risk": "low"}),
            PipelineProfile.FAST,
        )
        self.assertEqual(
            pipeline_profile_for_manifest({"risk": "medium"}),
            PipelineProfile.FULL,
        )
        self.assertEqual(
            pipeline_profile_for_manifest({"risk": "high"}),
            PipelineProfile.FULL,
        )

    def test_evaluator_diff_includes_untracked_files(self) -> None:
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
        (repo / "new.txt").write_text("new content\n", encoding="utf-8")
        diff = current_diff_text(repo)
        self.assertIn("tracked.txt", diff)
        self.assertIn("new.txt", diff)
        self.assertIn("new content", diff)
        self.assertEqual(git(repo, "diff", "--cached", "--name-only"), "")

    def test_changed_paths_include_tracked_and_untracked(self) -> None:
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
        (repo / "new.txt").write_text("new\n", encoding="utf-8")
        self.assertEqual(collect_changed_paths(repo), ["new.txt", "tracked.txt"])

    def test_owner_allowed_paths_are_a_hard_boundary(self) -> None:
        enforce_allowed_paths(["src/a.py", "src/tests/test_a.py"], ["src/"])
        with self.assertRaisesRegex(RuntimeError, "outside owner-defined"):
            enforce_allowed_paths(["src/a.py", "docs/contract.md"], ["src/"])


if __name__ == "__main__":
    unittest.main()
