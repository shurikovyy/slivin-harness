from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from task_runner import (
    capture_baseline_snapshot,
    capture_preflight,
    find_unplanned_changed_paths,
    restore_unplanned_paths_to_baseline,
)


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


class ChangeSurfaceTests(unittest.TestCase):
    def make_repo(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="slivin-change-surface-"))
        git(root, "init")
        git(root, "config", "user.name", "Test")
        git(root, "config", "user.email", "test@example.invalid")
        (root / "a.txt").write_text("A0\n", encoding="utf-8")
        (root / "b.txt").write_text("B0\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-m", "baseline")
        return root

    def test_unplanned_paths_are_detected_and_restored_only(self) -> None:
        repo = self.make_repo()
        preflight = capture_preflight(repo)
        plan = {"candidate_paths": ["a.txt"]}

        (repo / "a.txt").write_text("A1\n", encoding="utf-8")
        (repo / "b.txt").write_text("B1\n", encoding="utf-8")
        (repo / "new.txt").write_text("NEW\n", encoding="utf-8")

        unexpected = find_unplanned_changed_paths(repo, plan)
        self.assertEqual(unexpected, ["b.txt", "new.txt"])

        restore_unplanned_paths_to_baseline(
            repo,
            preflight=preflight,
            paths=unexpected,
        )

        self.assertEqual((repo / "a.txt").read_text(encoding="utf-8"), "A1\n")
        self.assertEqual((repo / "b.txt").read_text(encoding="utf-8"), "B0\n")
        self.assertFalse((repo / "new.txt").exists())
        self.assertEqual(find_unplanned_changed_paths(repo, plan), [])

    def test_late_planned_path_can_receive_pre_path_edit_evidence(self) -> None:
        repo = self.make_repo()
        preflight = capture_preflight(repo)
        initial = capture_baseline_snapshot(
            repo,
            preflight=preflight,
            candidate_paths=["a.txt"],
            captured_before_first_edit=True,
        )

        (repo / "a.txt").write_text("A1\n", encoding="utf-8")
        (repo / "b.txt").write_text("B1\n", encoding="utf-8")
        restore_unplanned_paths_to_baseline(
            repo,
            preflight=preflight,
            paths=["b.txt"],
        )

        expanded = capture_baseline_snapshot(
            repo,
            preflight=preflight,
            candidate_paths=["a.txt", "b.txt"],
            existing_snapshot=initial,
            captured_before_first_edit=False,
            captured_before_path_edit=True,
            snapshot_role="pre_path_edit_after_surface_reconciliation",
        )

        a = expanded["files"]["a.txt"]
        b = expanded["files"]["b.txt"]
        self.assertTrue(a["captured_before_first_edit"])
        self.assertTrue(a["captured_before_path_edit"])
        self.assertFalse(b["captured_before_first_edit"])
        self.assertTrue(b["captured_before_path_edit"])
        self.assertEqual(
            b["worktree_snapshot_role"],
            "pre_path_edit_after_surface_reconciliation",
        )


if __name__ == "__main__":
    unittest.main()
