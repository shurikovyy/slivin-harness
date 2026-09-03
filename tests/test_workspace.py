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
from slivin_harness.phase4 import git_changed_paths
from slivin_harness.phase7 import build_patch_reconstruction_proof
from slivin_harness.run_state import build_candidate_identity


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

    def test_worktreeinclude_copies_ignored_env_without_second_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            git(source, "init")
            git(source, "config", "user.email", "test@example.com")
            git(source, "config", "user.name", "Test")
            (source / ".gitignore").write_text(".env\n", encoding="utf-8")
            (source / ".worktreeinclude").write_text(".env\n", encoding="utf-8")
            (source / ".env").write_text("SECRET=runtime-only\n", encoding="utf-8")
            (source / "tracked.txt").write_text("base\n", encoding="utf-8")
            git(source, "add", ".gitignore", ".worktreeinclude", "tracked.txt")
            git(source, "commit", "-m", "base")
            local = {
                "workspace": {"root": str(root / "workspaces")},
                "projects": {
                    "demo": {
                        "repo": str(source),
                        "workspace": {"copy_untracked": [], "allow_sensitive_copy": False},
                    }
                },
            }
            session = prepare_workspace_session(
                manifest={"project": "demo", "workspace_mode": "git_worktree"},
                local_config=local,
                harness_root=root,
                task_id="WORKTREE_INCLUDE",
            )
            try:
                self.assertEqual((session.workspace / ".env").read_text(encoding="utf-8"), "SECRET=runtime-only\n")
                self.assertEqual(session.exposed_paths, (".env",))
                self.assertEqual(git(session.workspace, "status", "--porcelain"), "")
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

    def test_copy_untracked_runtime_directory_is_independent_and_candidate_excluded(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix="slivin-runtime-projection-"))
        source = make_source(sandbox)
        source_jest = source / "node_modules" / "jest" / "bin" / "jest.js"
        source_jest.parent.mkdir(parents=True)
        source_jest.write_text("source dependency\n", encoding="utf-8")
        local_config = {
            "workspace": {"root": str(sandbox / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(source),
                    "workspace": {"copy_untracked": ["node_modules"]},
                }
            },
        }
        session = prepare_workspace_session(
            manifest={"project": "demo", "workspace_mode": "git_worktree"},
            local_config=local_config,
            harness_root=sandbox,
            task_id="RUNTIME_PROJECTION",
        )
        try:
            workspace_jest = session.workspace / "node_modules" / "jest" / "bin" / "jest.js"
            self.assertTrue(workspace_jest.is_file())
            self.assertFalse(workspace_jest.is_symlink())
            self.assertFalse(os.path.samefile(source_jest, workspace_jest))
            self.assertEqual(len(session.runtime_projections), 1)
            self.assertEqual(session.runtime_projections[0].relative_path, "node_modules")
            self.assertEqual(session.runtime_projections[0].copy_mode, "physical_copy")

            workspace_jest.write_text("workspace-only dependency\n", encoding="utf-8")
            self.assertEqual(source_jest.read_text(encoding="utf-8"), "source dependency\n")
            self.assertEqual(git(session.workspace, "status", "--porcelain"), "")
            self.assertEqual(build_candidate_identity(session.workspace).changed_paths, ())
            self.assertNotIn(b"workspace-only dependency", build_candidate_patch(session))

            baseline = git(session.workspace, "rev-parse", "HEAD")
            (session.workspace / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            candidate = build_candidate_identity(session.workspace, baseline_sha=baseline)
            self.assertEqual(candidate.changed_paths, ("app.py",))
            self.assertEqual(git_changed_paths(session.workspace), ("app.py",))
            patch = build_candidate_patch(session)
            proof = build_patch_reconstruction_proof(
                repository=session.workspace,
                baseline_sha=baseline,
                patch=patch,
                expected_candidate=candidate,
                private_root=sandbox / "private-proof",
            )
            self.assertEqual(proof["status"], "PATCH_RECONSTRUCTION_PASS")
        finally:
            remove_managed_workspace(session)

    def test_runtime_projection_rejects_unsafe_or_missing_config_before_leaking_worktree(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix="slivin-runtime-projection-invalid-"))
        source = make_source(sandbox)
        before = git(source, "worktree", "list", "--porcelain")
        base_config = {
            "workspace": {"root": str(sandbox / "workspaces")},
            "projects": {"demo": {"repo": str(source), "workspace": {}}},
        }
        for unsafe in ("../outside", str(sandbox / "outside")):
            base_config["projects"]["demo"]["workspace"] = {
                "copy_untracked": [unsafe]
            }
            with self.assertRaisesRegex(RuntimeError, "repo-relative"):
                prepare_workspace_session(
                    manifest={"project": "demo", "workspace_mode": "git_worktree"},
                    local_config=base_config,
                    harness_root=sandbox,
                    task_id="RUNTIME_PROJECTION_UNSAFE",
                )
            self.assertEqual(git(source, "worktree", "list", "--porcelain"), before)

        base_config["projects"]["demo"]["workspace"] = {
            "copy_untracked": ["node_modules"]
        }
        with self.assertRaisesRegex(RuntimeError, "runtime path does not exist"):
            prepare_workspace_session(
                manifest={"project": "demo", "workspace_mode": "git_worktree"},
                local_config=base_config,
                harness_root=sandbox,
                task_id="RUNTIME_PROJECTION_MISSING",
            )
        self.assertEqual(git(source, "worktree", "list", "--porcelain"), before)

    @unittest.skipIf(os.name == "nt", "Creating symlinks is not reliably permitted on Windows")
    def test_nested_symlink_ancestor_cannot_expose_regular_file(self) -> None:
        sandbox = Path(tempfile.mkdtemp(prefix="slivin-symlink-parent-"))
        source = make_source(sandbox)
        outside = sandbox / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
        os.symlink(outside, source / "local-link", target_is_directory=True)
        before = git(source, "worktree", "list", "--porcelain")
        local_config = {
            "workspace": {"root": str(sandbox / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(source),
                    "require_clean_source": False,
                    "workspace": {
                        "copy_untracked": ["local-link/secret.txt"],
                        "allow_sensitive_copy": True,
                    },
                }
            },
        }
        with self.assertRaisesRegex(RuntimeError, "symlink/junction/reparse"):
            prepare_workspace_session(
                manifest={"project": "demo", "workspace_mode": "git_worktree"},
                local_config=local_config,
                harness_root=sandbox,
                task_id="NESTED_SYMLINK",
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
