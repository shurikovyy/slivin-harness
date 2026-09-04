from __future__ import annotations

import os
import shutil
import subprocess
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from slivin_harness.control_plane import ControllerPlane
from slivin_harness.git_integrity import (
    GIT_CONTROL_STATE_RESTORE_FAILED,
    GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH,
    GIT_CONTROL_STATE_MUTATED_DURING_BATCH,
    CandidateWorkspaceBaseline,
    GitControlIntegrityError,
    GitControlIntegrityManager,
)
from slivin_harness.run_state import build_candidate_identity
from slivin_harness.workspace import WorkspaceSession, build_candidate_patch


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout


class GitIntegrityTests(unittest.TestCase):
    @contextmanager
    def temp_directory(self):
        root = Path.cwd() / ".harness_tmp" / "git_integrity_tests"
        root.mkdir(parents=True, exist_ok=True)
        path = root / uuid.uuid4().hex
        path.mkdir()
        try:
            yield str(path)
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def make_repo(self, root: Path) -> tuple[Path, str, ControllerPlane]:
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Integrity Test")
        git(repo, "config", "user.email", "integrity@example.invalid")
        (repo / "tracked.js").write_text("baseline\n", encoding="utf-8")
        (repo / ".gitignore").write_text("ignored.js\n", encoding="utf-8")
        (repo / ".harness_git_excludes").write_text(".venv/\n", encoding="utf-8")
        git(repo, "add", "tracked.js", ".gitignore")
        git(repo, "commit", "-m", "baseline")
        baseline_sha = git(repo, "rev-parse", "HEAD").strip()
        plane = ControllerPlane(root / "run")
        CandidateWorkspaceBaseline.capture(
            repo,
            baseline_sha=baseline_sha,
            excluded_prefixes=(
                ".git",
                ".harness_git_excludes",
                ".harness_tmp",
                ".venv",
            ),
            control_plane=plane,
        )
        return repo, baseline_sha, plane

    def test_physical_inventory_ignores_git_ignore_and_index_flags(self) -> None:
        with self.temp_directory() as raw:
            repo, baseline_sha, _plane = self.make_repo(Path(raw))
            (repo / ".gitignore").write_text(
                "ignored.js\nsecond-hidden.js\n", encoding="utf-8"
            )
            (repo / "ignored.js").write_text("candidate\n", encoding="utf-8")
            (repo / "second-hidden.js").write_text("candidate\n", encoding="utf-8")
            git(repo, "update-index", "--assume-unchanged", "tracked.js")
            (repo / "tracked.js").write_text("modified\n", encoding="utf-8")
            identity = build_candidate_identity(repo, baseline_sha=baseline_sha)
            self.assertEqual(
                identity.changed_paths,
                (".gitignore", "ignored.js", "second-hidden.js", "tracked.js"),
            )

    def test_nested_ignore_and_core_excludes_do_not_hide_candidate(self) -> None:
        with self.temp_directory() as raw:
            repo, baseline_sha, _plane = self.make_repo(Path(raw))
            (repo / ".git" / "info" / "exclude").write_text("info-hidden.js\n", encoding="utf-8")
            external_ignore = repo / ".stealth-ignore"
            external_ignore.write_text("core-hidden.js\n", encoding="utf-8")
            git(repo, "config", "core.excludesFile", str(external_ignore))
            (repo / "info-hidden.js").write_text("info\n", encoding="utf-8")
            (repo / "core-hidden.js").write_text("core\n", encoding="utf-8")
            identity = build_candidate_identity(repo, baseline_sha=baseline_sha)
            self.assertEqual(
                set(identity.changed_paths),
                {".stealth-ignore", "core-hidden.js", "info-hidden.js"},
            )

    def test_control_mutation_during_green_batch_is_restored_and_fails(self) -> None:
        with self.temp_directory() as raw:
            repo, _baseline_sha, plane = self.make_repo(Path(raw))
            manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
            manager.establish_baseline()
            exclude = repo / ".git" / "info" / "exclude"
            before = exclude.read_bytes()

            def mutate() -> int:
                exclude.write_bytes(before + b"stealth.js\n")
                return 0

            with self.assertRaises(GitControlIntegrityError) as caught:
                manager.run_batch("probe", mutate)
            self.assertEqual(caught.exception.reason_code, GIT_CONTROL_STATE_MUTATED_DURING_BATCH)
            self.assertEqual(exclude.read_bytes(), before)
            public = (plane.run_root / "git_control_integrity.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(str(repo), public)

    def test_control_mutation_is_reported_when_operation_raises(self) -> None:
        with self.temp_directory() as raw:
            repo, _baseline_sha, plane = self.make_repo(Path(raw))
            manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
            manager.establish_baseline()

            def mutate_and_raise() -> None:
                (repo / ".harness_git_excludes").write_text("changed\n", encoding="utf-8")
                raise OSError("operation failed")

            with self.assertRaises(GitControlIntegrityError) as caught:
                manager.run_batch("raising-operation", mutate_and_raise)
            self.assertEqual(caught.exception.reason_code, GIT_CONTROL_STATE_MUTATED_DURING_BATCH)

    def test_info_attributes_and_local_config_are_frozen(self) -> None:
        for label, mutation in (
            (
                "info-attributes",
                lambda repo: (repo / ".git" / "info" / "attributes").write_text(
                    "*.js -diff\n", encoding="utf-8"
                ),
            ),
            (
                "local-config",
                lambda repo: git(repo, "config", "core.attributesFile", ".attributes-local"),
            ),
        ):
            with self.subTest(label=label), self.temp_directory() as raw:
                repo, _baseline_sha, plane = self.make_repo(Path(raw))
                manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
                manager.establish_baseline()
                with self.assertRaises(GitControlIntegrityError) as caught:
                    manager.run_batch(label, lambda: mutation(repo))
                self.assertEqual(
                    caught.exception.reason_code, GIT_CONTROL_STATE_MUTATED_DURING_BATCH
                )

    def test_control_mutation_before_batch_blocks_operation(self) -> None:
        with self.temp_directory() as raw:
            repo, _baseline_sha, plane = self.make_repo(Path(raw))
            manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
            manager.establish_baseline()
            git(repo, "config", "core.fileMode", "false")
            called = False

            def operation() -> None:
                nonlocal called
                called = True

            with self.assertRaises(GitControlIntegrityError) as caught:
                manager.run_batch("later", operation)
            self.assertEqual(caught.exception.reason_code, GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH)
            self.assertFalse(called)

    def test_assume_unchanged_and_skip_worktree_are_control_mutations(self) -> None:
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag), self.temp_directory() as raw:
                repo, _baseline_sha, plane = self.make_repo(Path(raw))
                manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
                manager.establish_baseline()
                with self.assertRaises(GitControlIntegrityError):
                    manager.run_batch(
                        flag,
                        lambda: git(repo, "update-index", flag, "tracked.js"),
                    )

    def test_head_index_and_worktree_config_are_frozen(self) -> None:
        mutations = (
            ("head", lambda repo: git(repo, "commit", "--allow-empty", "-m", "unexpected")),
            (
                "index",
                lambda repo: (
                    (repo / "staged.js").write_text("staged\n", encoding="utf-8"),
                    git(repo, "add", "staged.js"),
                ),
            ),
            (
                "worktree-config",
                lambda repo: git(repo, "config", "--worktree", "core.fileMode", "false"),
            ),
        )
        for label, mutation in mutations:
            with self.subTest(label=label), self.temp_directory() as raw:
                repo, _baseline_sha, plane = self.make_repo(Path(raw))
                if label == "worktree-config":
                    git(repo, "config", "extensions.worktreeConfig", "true")
                manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
                manager.establish_baseline()
                with self.assertRaises(GitControlIntegrityError) as caught:
                    manager.run_batch(label, lambda: mutation(repo))
                self.assertEqual(
                    caught.exception.reason_code, GIT_CONTROL_STATE_MUTATED_DURING_BATCH
                )

    def test_external_local_control_restore_failure_is_distinct(self) -> None:
        with self.temp_directory() as raw:
            root = Path(raw)
            repo, _baseline_sha, plane = self.make_repo(root)
            external = root / "external-ignore"
            external.write_text("baseline\n", encoding="utf-8")
            git(repo, "config", "core.excludesFile", str(external))
            manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
            manager.establish_baseline()

            with self.assertRaises(GitControlIntegrityError) as caught:
                manager.run_batch(
                    "external-control",
                    lambda: external.write_text("changed\n", encoding="utf-8"),
                )

            self.assertEqual(caught.exception.reason_code, GIT_CONTROL_STATE_MUTATED_DURING_BATCH)
            self.assertIn(
                GIT_CONTROL_STATE_RESTORE_FAILED,
                caught.exception.secondary_reason_codes,
            )
            public = (plane.run_root / "git_control_integrity.json").read_text(
                encoding="utf-8"
            )
            self.assertIn(GIT_CONTROL_STATE_RESTORE_FAILED, public)

    def test_ignored_file_patch_uses_isolated_index_and_reconstructs(self) -> None:
        with self.temp_directory() as raw:
            root = Path(raw)
            repo, baseline_sha, _plane = self.make_repo(root)
            real_index = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
            before_index = real_index.read_bytes()
            (repo / "ignored.js").write_text("candidate\n", encoding="utf-8")
            # A release archive may already have a long extraction root.  Keep
            # enough nesting here to guard against reintroducing verbose
            # temporary-index components that exceed Windows MAX_PATH.
            scratch = root / ("controller-private-" + ("x" * 48))
            patch = build_candidate_patch(
                WorkspaceSession(repo, "test", False, base_sha=baseline_sha),
                scratch_root=scratch,
            )
            self.assertIn(b"ignored.js", patch)
            self.assertEqual(real_index.read_bytes(), before_index)
            self.assertTrue(not scratch.exists() or list(scratch.iterdir()) == [])

            proof = root / "proof"
            proof.mkdir()
            shutil.copytree(repo / ".git", proof / ".git")
            git(proof, "checkout-index", "--all", "--force")
            applied = subprocess.run(
                ["git", "apply", "--binary", "-"],
                cwd=proof,
                input=patch,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(applied.returncode, 0, applied.stderr.decode(errors="replace"))
            self.assertEqual((proof / "ignored.js").read_text(encoding="utf-8"), "candidate\n")


if __name__ == "__main__":
    unittest.main()
