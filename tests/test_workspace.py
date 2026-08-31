from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from slivin_harness.workspace import (
    apply_candidate_to_source,
    build_candidate_patch,
    prepare_workspace_session,
    remove_managed_workspace,
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


def make_source(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.name", "Test")
    git(source, "config", "user.email", "test@example.invalid")
    (source / ".gitignore").write_text(".env\n.venv/\nnode_modules/\n", encoding="utf-8")
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(source, "add", "-A")
    git(source, "commit", "-m", "baseline")
    return source


class WorkspaceTests(unittest.TestCase):
    def test_managed_worktree_isolated_and_can_apply_exact_result(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix="slivin-worktree-"))
        source = make_source(sandbox)
        (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (source / ".venv").mkdir()
        (source / ".venv" / "marker.txt").write_text("venv", encoding="utf-8")

        local_config = {
            "workspace": {"root": str(sandbox / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(source),
                    "result_mode": "apply_to_source",
                    "workspace": {
                        "copy_untracked": [".env"],
                        "allow_sensitive_copy": True,
                    },
                }
            },
        }
        session = prepare_workspace_session(
            manifest={"project": "demo", "workspace_mode": "git_worktree"},
            local_config=local_config,
            harness_root=sandbox,
            task_id="MATRIX_" + ("VERY_LONG_TASK_NAME_" * 8),
        )
        try:
            self.assertTrue(session.managed)
            self.assertLessEqual(len(session.workspace.parent.name), 40)
            self.assertEqual(
                (session.workspace / ".env").read_text(encoding="utf-8"),
                "TOKEN=secret\n",
            )
            self.assertFalse((session.workspace / ".venv").exists())
            self.assertEqual(git(session.workspace, "status", "--short"), "")

            (session.workspace / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            (session.workspace / "new.py").write_text("NEW = True\n", encoding="utf-8")
            (session.workspace / ".env").write_text(
                "TOKEN=changed-only-in-worktree\n", encoding="utf-8"
            )

            patch = build_candidate_patch(session)
            self.assertIn(b"app.py", patch)
            self.assertIn(b"new.py", patch)
            self.assertNotIn(b"changed-only-in-worktree", patch)

            apply_candidate_to_source(session, patch=patch)
            self.assertEqual((source / "app.py").read_text(encoding="utf-8"), "VALUE = 2\n")
            self.assertEqual((source / "new.py").read_text(encoding="utf-8"), "NEW = True\n")
            self.assertEqual((source / ".env").read_text(encoding="utf-8"), "TOKEN=secret\n")
        finally:
            remove_managed_workspace(session)

    def test_sensitive_copy_requires_opt_in_and_failed_setup_leaks_no_worktree(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix="slivin-sensitive-"))
        source = make_source(sandbox)
        (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        before = git(source, "worktree", "list", "--porcelain")
        local_config = {
            "workspace": {"root": str(sandbox / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(source),
                    "workspace": {"copy_untracked": [".env"]},
                }
            },
        }
        with self.assertRaisesRegex(RuntimeError, "allow_sensitive_copy=true"):
            prepare_workspace_session(
                manifest={"project": "demo", "workspace_mode": "git_worktree"},
                local_config=local_config,
                harness_root=sandbox,
                task_id="DEMO_TASK",
            )
        self.assertEqual(git(source, "worktree", "list", "--porcelain"), before)

    def test_sensitive_file_nested_in_exposed_directory_requires_opt_in(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix="slivin-sensitive-tree-"))
        source = make_source(sandbox)
        local = source / "local-config"
        local.mkdir()
        (local / "settings.txt").write_text("safe=true\n", encoding="utf-8")
        (local / "credentials.json").write_text("{\"token\": \"secret\"}\n", encoding="utf-8")
        before = git(source, "worktree", "list", "--porcelain")
        local_config = {
            "workspace": {"root": str(sandbox / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(source),
                    "workspace": {"copy_untracked": ["local-config"]},
                }
            },
        }
        with self.assertRaisesRegex(RuntimeError, "credentials.json"):
            prepare_workspace_session(
                manifest={"project": "demo", "workspace_mode": "git_worktree"},
                local_config=local_config,
                harness_root=sandbox,
                task_id="DEMO_TASK",
            )
        self.assertEqual(git(source, "worktree", "list", "--porcelain"), before)

    @unittest.skipIf(os.name == "nt", "Creating symlinks is not reliably permitted on Windows")
    def test_symlink_exposure_is_rejected_and_cleaned_up(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix="slivin-symlink-"))
        source = make_source(sandbox)
        outside = sandbox / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        os.symlink(outside, source / "local-link")
        before = git(source, "worktree", "list", "--porcelain")
        local_config = {
            "workspace": {"root": str(sandbox / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(source),
                    "workspace": {"copy_untracked": ["local-link"]},
                }
            },
        }
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            prepare_workspace_session(
                manifest={"project": "demo", "workspace_mode": "git_worktree"},
                local_config=local_config,
                harness_root=sandbox,
                task_id="DEMO_TASK",
            )
        self.assertEqual(git(source, "worktree", "list", "--porcelain"), before)


if __name__ == "__main__":
    unittest.main()
