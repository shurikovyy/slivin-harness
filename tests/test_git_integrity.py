from __future__ import annotations

import os
import shutil
import subprocess
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from slivin_harness.control_plane import ControllerPlane
from slivin_harness.git_integrity import (
    CANDIDATE_EXCLUSION_OVERLAPS_TRACKED_PATH,
    GIT_CONTROL_STATE_DETECT_ONLY_MUTATION,
    GIT_CONTROL_STATE_DIRECTORY_LIMIT,
    GIT_CONTROL_STATE_MUTATED_BEFORE_BATCH,
    GIT_CONTROL_STATE_MUTATED_DURING_BATCH,
    CandidateInventoryError,
    CandidateWorkspaceBaseline,
    GitControlIntegrityError,
    GitControlIntegrityManager,
    isolated_git_index_environment,
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

    def test_trusted_git_diff_uses_disposable_index_and_preserves_real_index(self) -> None:
        with self.temp_directory() as raw:
            root = Path(raw)
            repo, _baseline_sha, _plane = self.make_repo(root)
            (repo / "tracked.js").write_text("candidate\n", encoding="utf-8")
            real_index = Path(
                git(
                    repo,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "index",
                ).strip()
            )
            before = real_index.read_bytes()
            scratch = root / "trusted-check"
            isolated_path: Path | None = None
            with isolated_git_index_environment(
                workspace=repo,
                scratch_root=scratch,
                environment=os.environ,
            ) as environment:
                isolated_path = Path(environment["GIT_INDEX_FILE"])
                self.assertNotEqual(isolated_path, real_index)
                completed = subprocess.run(
                    ["git", "diff", "--check"],
                    cwd=repo,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue(isolated_path.is_file())
            self.assertEqual(real_index.read_bytes(), before)
            self.assertIsNotNone(isolated_path)
            self.assertFalse(isolated_path.exists())

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

    def test_external_local_control_mutation_is_detect_only(self) -> None:
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
                GIT_CONTROL_STATE_DETECT_ONLY_MUTATION,
                caught.exception.secondary_reason_codes,
            )
            self.assertEqual(external.read_text(encoding="utf-8"), "changed\n")
            public = (plane.run_root / "git_control_integrity.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(str(external), public)

    def test_mutated_config_targets_never_select_restore_destination(self) -> None:
        for key, relative in (
            ("core.hooksPath", "victim-hooks"),
            ("core.excludesFile", "tracked.js"),
            ("core.attributesFile", "victim-attributes"),
        ):
            with self.subTest(key=key), self.temp_directory() as raw:
                root = Path(raw)
                repo, _baseline_sha, plane = self.make_repo(root)
                victim = repo / relative
                if key != "core.excludesFile":
                    victim.mkdir()
                    (victim / "sentinel").write_text("unchanged\n", encoding="utf-8")
                before = (
                    victim.read_bytes()
                    if victim.is_file()
                    else (victim / "sentinel").read_bytes()
                )
                manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
                manager.establish_baseline()
                with self.assertRaises(GitControlIntegrityError):
                    manager.run_batch(
                        key,
                        lambda: git(repo, "config", key, str(victim)),
                    )
                after = (
                    victim.read_bytes()
                    if victim.is_file()
                    else (victim / "sentinel").read_bytes()
                )
                self.assertEqual(after, before)

    def test_object_database_is_not_used_as_mutated_hooks_destination(self) -> None:
        with self.temp_directory() as raw:
            repo, _baseline_sha, plane = self.make_repo(Path(raw))
            objects = Path(git(repo, "rev-parse", "--git-path", "objects").strip())
            if not objects.is_absolute():
                objects = repo / objects
            sentinel = next(path for path in objects.rglob("*") if path.is_file())
            before = sentinel.read_bytes()
            manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
            manager.establish_baseline()
            with self.assertRaises(GitControlIntegrityError):
                manager.run_batch(
                    "retarget-objects",
                    lambda: git(repo, "config", "core.hooksPath", str(objects)),
                )
            self.assertEqual(sentinel.read_bytes(), before)

    def test_mutated_hooks_target_is_never_scanned(self) -> None:
        with self.temp_directory() as raw:
            root = Path(raw)
            repo, _baseline_sha, plane = self.make_repo(root)
            victim = root / "large-external-directory"
            victim.mkdir()
            (victim / "sentinel").write_text("unchanged\n", encoding="utf-8")
            manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
            manager.establish_baseline()
            original_scandir = os.scandir

            def guarded_scandir(path):
                if Path(path).absolute() == victim.absolute():
                    raise AssertionError("mutated hooks target was scanned")
                return original_scandir(path)

            def mutate() -> None:
                git(repo, "config", "core.hooksPath", str(victim))

            with patch("slivin_harness.git_integrity.os.scandir", side_effect=guarded_scandir):
                with self.assertRaises(GitControlIntegrityError) as caught:
                    manager.run_batch("retarget-large", mutate)
            self.assertEqual(
                caught.exception.reason_code,
                GIT_CONTROL_STATE_MUTATED_DURING_BATCH,
            )
            self.assertEqual(
                (victim / "sentinel").read_text(encoding="utf-8"), "unchanged\n"
            )

    def test_linked_worktree_common_ref_is_detect_only(self) -> None:
        with self.temp_directory() as raw:
            root = Path(raw)
            source, _baseline_sha, _plane = self.make_repo(root)
            worktree = root / "linked"
            git(source, "worktree", "add", "--detach", str(worktree), "HEAD")
            plane = ControllerPlane(root / "linked-run")
            manager = GitControlIntegrityManager(workspace=worktree, control_plane=plane)
            manager.establish_baseline()
            ref = source / ".git" / "refs" / "heads" / "hidden"

            with self.assertRaises(GitControlIntegrityError) as caught:
                manager.run_batch(
                    "linked-ref",
                    lambda: git(worktree, "update-ref", "refs/heads/hidden", "HEAD"),
                )

            self.assertEqual(
                caught.exception.reason_code,
                GIT_CONTROL_STATE_MUTATED_DURING_BATCH,
            )
            self.assertIn(
                GIT_CONTROL_STATE_DETECT_ONLY_MUTATION,
                caught.exception.secondary_reason_codes,
            )
            self.assertTrue(ref.is_file())

    def test_refs_replace_packed_refs_alternates_and_shallow_are_frozen(self) -> None:
        mutations = {
            "loose-ref": lambda repo: git(repo, "update-ref", "refs/heads/hidden", "HEAD"),
            "replace-ref": lambda repo: (
                (repo / ".git" / "refs" / "replace").mkdir(parents=True, exist_ok=True),
                (repo / ".git" / "refs" / "replace" / ("0" * 40)).write_text(
                    "1" * 40 + "\n", encoding="ascii"
                ),
            ),
            "packed-refs": lambda repo: (repo / ".git" / "packed-refs").write_text(
                "# pack-refs with: peeled fully-peeled sorted\n", encoding="ascii"
            ),
            "alternates": lambda repo: (
                (repo / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True),
                (repo / ".git" / "objects" / "info" / "alternates").write_text(
                    "outside\n", encoding="utf-8"
                ),
            ),
            "shallow": lambda repo: (repo / ".git" / "shallow").write_text(
                "0" * 40 + "\n", encoding="ascii"
            ),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label), self.temp_directory() as raw:
                repo, _baseline_sha, plane = self.make_repo(Path(raw))
                manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
                manager.establish_baseline()
                with self.assertRaises(GitControlIntegrityError) as caught:
                    manager.run_batch(label, lambda: mutation(repo))
                self.assertEqual(
                    caught.exception.reason_code,
                    GIT_CONTROL_STATE_MUTATED_DURING_BATCH,
                )

    def test_directory_snapshot_is_bounded(self) -> None:
        with self.temp_directory() as raw:
            repo, _baseline_sha, plane = self.make_repo(Path(raw))
            hooks = repo / ".git" / "hooks"
            (hooks / "extra-a").write_text("a", encoding="utf-8")
            (hooks / "extra-b").write_text("b", encoding="utf-8")
            manager = GitControlIntegrityManager(workspace=repo, control_plane=plane)
            with patch("slivin_harness.git_integrity.GIT_CONTROL_MAX_ENTRIES", 1):
                with self.assertRaises(GitControlIntegrityError) as caught:
                    manager.establish_baseline()
            self.assertEqual(caught.exception.reason_code, GIT_CONTROL_STATE_DIRECTORY_LIMIT)

    def test_explicit_exclusion_cannot_hide_tracked_source(self) -> None:
        with self.temp_directory() as raw:
            root = Path(raw)
            repo, baseline_sha, _plane = self.make_repo(root)
            (repo / "coverage").mkdir()
            (repo / "coverage" / "module.py").write_text("tracked\n", encoding="utf-8")
            git(repo, "add", "coverage/module.py")
            git(repo, "commit", "-m", "tracked coverage")
            baseline_sha = git(repo, "rev-parse", "HEAD").strip()
            with self.assertRaises(CandidateInventoryError) as caught:
                CandidateWorkspaceBaseline.capture(
                    repo,
                    baseline_sha=baseline_sha,
                    excluded_prefixes=(".git", "coverage"),
                )
            self.assertEqual(
                caught.exception.reason_code,
                CANDIDATE_EXCLUSION_OVERLAPS_TRACKED_PATH,
            )

    def test_cache_named_tracked_sources_remain_candidate_visible(self) -> None:
        with self.temp_directory() as raw:
            repo, _baseline_sha, plane = self.make_repo(Path(raw))
            (repo / "coverage").mkdir()
            (repo / "coverage" / "module.py").write_text("baseline\n", encoding="utf-8")
            nested = repo / "package" / "__pycache__"
            nested.mkdir(parents=True)
            (nested / "source.txt").write_text("baseline\n", encoding="utf-8")
            git(repo, "add", "coverage/module.py", "package/__pycache__/source.txt")
            git(repo, "commit", "-m", "cache-named source")
            baseline_sha = git(repo, "rev-parse", "HEAD").strip()
            CandidateWorkspaceBaseline.capture(
                repo,
                baseline_sha=baseline_sha,
                excluded_prefixes=(".git", ".harness_tmp", ".harness_git_excludes"),
                control_plane=plane,
            )
            (repo / "coverage" / "module.py").write_text("changed\n", encoding="utf-8")
            (nested / "source.txt").write_text("changed\n", encoding="utf-8")
            identity = build_candidate_identity(repo, baseline_sha=baseline_sha)
            self.assertEqual(
                set(identity.changed_paths),
                {"coverage/module.py", "package/__pycache__/source.txt"},
            )

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
