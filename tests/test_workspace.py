from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from slivin_harness.workspace import (
    apply_candidate_to_source,
    build_candidate_patch,
    prepare_workspace_session,
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


class WorkspaceTests(unittest.TestCase):
    def test_managed_worktree_isolated_and_can_apply_result(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix="slivin-worktree-"))
        source = sandbox / "source"
        source.mkdir()
        git(source, "init")
        git(source, "config", "user.name", "Test")
        git(source, "config", "user.email", "test@example.invalid")

        (source / ".gitignore").write_text(
            ".venv/\nnode_modules/\n",
            encoding="utf-8",
        )
        (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(source, "add", "-A")
        git(source, "commit", "-m", "baseline")

        (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (source / ".venv").mkdir()
        (source / ".venv" / "marker.txt").write_text("venv", encoding="utf-8")
        (source / "node_modules").mkdir()
        (source / "node_modules" / "marker.txt").write_text("node", encoding="utf-8")

        local_config = {
            "workspace": {"root": str(sandbox / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(source),
                    "result_mode": "apply_to_source",
                    "workspace": {"copy_untracked": [".env"]},
                }
            },
        }
        manifest = {
            "project": "demo",
            "workspace_mode": "git_worktree",
        }

        session = prepare_workspace_session(
            manifest=manifest,
            local_config=local_config,
            harness_root=sandbox,
            task_id="demo-task",
        )

        self.assertTrue(session.managed)
        self.assertTrue((session.workspace / "app.py").is_file())
        self.assertEqual(
            (session.workspace / ".env").read_text(encoding="utf-8"),
            "TOKEN=secret\n",
        )
        self.assertFalse((session.workspace / ".venv").exists())
        self.assertFalse((session.workspace / "node_modules").exists())
        self.assertEqual(git(session.workspace, "status", "--short"), "")

        (session.workspace / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        (session.workspace / "new.py").write_text("NEW = True\n", encoding="utf-8")
        (session.workspace / ".env").write_text("TOKEN=changed-only-in-worktree\n", encoding="utf-8")

        patch = build_candidate_patch(session)
        self.assertIn(b"app.py", patch)
        self.assertIn(b"new.py", patch)
        self.assertNotIn(b"TOKEN=changed-only-in-worktree", patch)

        apply_candidate_to_source(session, patch=patch)

        self.assertEqual((source / "app.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual((source / "new.py").read_text(encoding="utf-8"), "NEW = True\n")
        self.assertEqual((source / ".env").read_text(encoding="utf-8"), "TOKEN=secret\n")


if __name__ == "__main__":
    unittest.main()
