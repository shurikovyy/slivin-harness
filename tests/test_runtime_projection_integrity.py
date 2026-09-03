from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from slivin_harness.control_plane import ControllerPlane
from slivin_harness.runtime_projection import (
    RUNTIME_PROJECTION_BASELINE_MISMATCH,
    RUNTIME_PROJECTION_MUTATED_DURING_CHECK,
    RUNTIME_PROJECTION_SOURCE_CHANGED,
    RUNTIME_PROJECTION_UNSUPPORTED_ENTRY,
    RUNTIME_PROJECTION_WORKSPACE_RESTORE_FAILED,
    RuntimeProjectionIntegrityError,
    RuntimeProjectionIntegrityManager,
    fingerprint_runtime_tree,
    validate_runtime_casefold_paths,
)
from slivin_harness.workspace import RuntimeProjection, WorkspaceSession
from slivin_harness.phase7 import classify_heldout_results
from task_runner import HarnessControlledStop, run_benchmark_baseline_gate, run_checks


def write_tree(root: Path) -> None:
    jest = root / "node_modules" / "jest" / "bin" / "jest.js"
    dependency = root / "node_modules" / "dep" / "index.js"
    jest.parent.mkdir(parents=True)
    dependency.parent.mkdir(parents=True)
    (root / "node_modules" / "empty").mkdir(parents=True)
    jest.write_text("jest baseline\n", encoding="utf-8")
    dependency.write_text("dependency baseline\n", encoding="utf-8")


class RuntimeProjectionIntegrityTests(unittest.TestCase):
    def test_windows_case_collision_logic_is_platform_independent(self) -> None:
        with self.assertRaises(RuntimeProjectionIntegrityError) as raised:
            validate_runtime_casefold_paths(
                ["Node_Modules/jest.js", "node_modules/jest.js"]
            )
        self.assertEqual(raised.exception.reason_code, RUNTIME_PROJECTION_UNSUPPORTED_ENTRY)
        validate_runtime_casefold_paths(["node_modules/jest.js", "vendor/jest.js"])

    def make_manager(self) -> tuple[Path, Path, Path, RuntimeProjectionIntegrityManager]:
        root = Path(tempfile.mkdtemp(prefix="slivin-runtime-integrity-"))
        source = root / "source"
        workspace = root / "workspace"
        source.mkdir()
        workspace.mkdir()
        write_tree(source)
        write_tree(workspace)
        projection = RuntimeProjection(
            relative_path="node_modules",
            source_kind="workspace.copy_untracked",
            destination=workspace / "node_modules",
            is_directory=True,
            copy_mode="physical_copy",
            runtime_only=True,
        )
        session = WorkspaceSession(
            workspace=workspace,
            mode="benchmark_isolated",
            managed=True,
            source_repo=source,
            runtime_projections=(projection,),
        )
        manager = RuntimeProjectionIntegrityManager(
            session=session,
            control_plane=ControllerPlane(root / "run"),
            retry_delay_seconds=0,
        )
        manager.establish_baseline()
        return root, source, workspace, manager

    def test_initial_source_workspace_mismatch_cannot_establish_baseline(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-runtime-baseline-mismatch-"))
        source = root / "source"
        workspace = root / "workspace"
        source.mkdir()
        workspace.mkdir()
        write_tree(source)
        write_tree(workspace)
        (workspace / "node_modules" / "dep" / "index.js").write_text(
            "different", encoding="utf-8"
        )
        projection = RuntimeProjection(
            relative_path="node_modules",
            source_kind="workspace.copy_untracked",
            destination=workspace / "node_modules",
            is_directory=True,
            copy_mode="physical_copy",
            runtime_only=True,
        )
        manager = RuntimeProjectionIntegrityManager(
            session=WorkspaceSession(
                workspace=workspace,
                mode="benchmark_isolated",
                managed=True,
                source_repo=source,
                runtime_projections=(projection,),
            ),
            control_plane=ControllerPlane(root / "run"),
        )
        with self.assertRaises(RuntimeProjectionIntegrityError) as raised:
            manager.establish_baseline()
        self.assertEqual(raised.exception.reason_code, RUNTIME_PROJECTION_BASELINE_MISMATCH)

    def test_tree_fingerprint_covers_full_tree_shape_and_contents(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-tree-fingerprint-"))
        left = root / "left"
        right = root / "right"
        left.mkdir()
        right.mkdir()
        # Opposite creation order proves that scandir order is not authority.
        (left / "b").mkdir()
        (left / "a").mkdir()
        (right / "a").mkdir()
        (right / "b").mkdir()
        (left / "a" / "jest.js").write_text("same", encoding="utf-8")
        (right / "a" / "jest.js").write_text("same", encoding="utf-8")
        (left / "b" / "dependency.js").write_text("dep", encoding="utf-8")
        (right / "b" / "dependency.js").write_text("dep", encoding="utf-8")
        baseline = fingerprint_runtime_tree(left).sha256
        self.assertEqual(baseline, fingerprint_runtime_tree(right).sha256)

        (right / "a" / "jest.js").write_text("changed", encoding="utf-8")
        self.assertNotEqual(baseline, fingerprint_runtime_tree(right).sha256)
        (right / "a" / "jest.js").write_text("same", encoding="utf-8")
        (right / "b" / "dependency.js").write_text("changed dep", encoding="utf-8")
        self.assertNotEqual(baseline, fingerprint_runtime_tree(right).sha256)
        (right / "b" / "dependency.js").write_text("dep", encoding="utf-8")

        (right / "new.js").write_text("new", encoding="utf-8")
        self.assertNotEqual(baseline, fingerprint_runtime_tree(right).sha256)
        (right / "new.js").unlink()
        (right / "b" / "dependency.js").unlink()
        self.assertNotEqual(baseline, fingerprint_runtime_tree(right).sha256)
        (right / "b" / "renamed.js").write_text("dep", encoding="utf-8")
        self.assertNotEqual(baseline, fingerprint_runtime_tree(right).sha256)

    def test_large_file_is_streamed_without_path_read_bytes(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-tree-stream-"))
        tree = root / "tree"
        tree.mkdir()
        (tree / "large.bin").write_bytes(b"x" * (2 * 1024 * 1024 + 17))
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("not streamed")):
            value = fingerprint_runtime_tree(tree, chunk_size=8192)
        self.assertEqual(value.total_file_bytes, 2 * 1024 * 1024 + 17)

    @unittest.skipIf(os.name == "nt", "Creating symlinks is not reliably permitted on Windows")
    def test_tree_fingerprint_rejects_symlink_without_following_it(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-tree-link-"))
        tree = root / "tree"
        outside = root / "outside.txt"
        tree.mkdir()
        outside.write_text("outside", encoding="utf-8")
        os.symlink(outside, tree / "alias")
        with self.assertRaises(RuntimeProjectionIntegrityError) as raised:
            fingerprint_runtime_tree(tree)
        self.assertEqual(raised.exception.reason_code, RUNTIME_PROJECTION_UNSUPPORTED_ENTRY)

    def test_precheck_restores_entire_projection_and_keeps_physical_independence(self) -> None:
        _root, source, workspace, manager = self.make_manager()
        workspace_jest = workspace / "node_modules" / "jest" / "bin" / "jest.js"
        workspace_dep = workspace / "node_modules" / "dep" / "index.js"
        workspace_jest.write_text("tampered", encoding="utf-8")
        workspace_dep.unlink()
        (workspace / "node_modules" / "added.js").write_text("added", encoding="utf-8")

        manager.prepare_before_batch("SELF_VERIFY")

        self.assertEqual(workspace_jest.read_text(encoding="utf-8"), "jest baseline\n")
        self.assertEqual(workspace_dep.read_text(encoding="utf-8"), "dependency baseline\n")
        self.assertFalse((workspace / "node_modules" / "added.js").exists())
        self.assertEqual(
            fingerprint_runtime_tree(source / "node_modules").sha256,
            fingerprint_runtime_tree(workspace / "node_modules").sha256,
        )
        self.assertFalse(
            os.path.samefile(
                source / "node_modules" / "dep" / "index.js",
                workspace_dep,
            )
        )

    def test_precheck_rejects_same_content_hardlink_alias(self) -> None:
        _root, source, workspace, manager = self.make_manager()
        source_dep = source / "node_modules" / "dep" / "index.js"
        workspace_dep = workspace / "node_modules" / "dep" / "index.js"
        workspace_dep.unlink()
        os.link(source_dep, workspace_dep)
        self.assertTrue(os.path.samefile(source_dep, workspace_dep))
        manager.prepare_before_batch("HARDLINK_ALIAS")
        self.assertFalse(os.path.samefile(source_dep, workspace_dep))
        self.assertEqual(workspace_dep.read_text(encoding="utf-8"), "dependency baseline\n")

    def test_green_batch_that_mutates_runtime_is_invalid_and_restored(self) -> None:
        _root, _source, workspace, manager = self.make_manager()
        dependency = workspace / "node_modules" / "dep" / "index.js"

        def mutate_and_pass() -> int:
            dependency.write_text("mutated during check", encoding="utf-8")
            return 0

        with self.assertRaises(RuntimeProjectionIntegrityError) as raised:
            manager.run_batch("HELDOUT", mutate_and_pass)
        self.assertEqual(
            raised.exception.reason_code,
            RUNTIME_PROJECTION_MUTATED_DURING_CHECK,
        )
        self.assertEqual(dependency.read_text(encoding="utf-8"), "dependency baseline\n")

    def test_controller_check_mutation_is_typed_infrastructure_not_pass(self) -> None:
        root, _source, workspace, manager = self.make_manager()
        results = run_checks(
            [
                {
                    "name": "mutating green check",
                    "command": [
                        sys.executable,
                        "-c",
                        (
                            "from pathlib import Path; "
                            "Path('node_modules/dep/index.js').write_text('mutated')"
                        ),
                    ],
                    "timeout_seconds": 30,
                }
            ],
            workspace=workspace,
            toolchain={},
            runtime_root=root / "check-runtime",
            label="MUTATING CHECK",
            runtime_integrity_manager=manager,
            batch_id="MUTATING_CHECK",
        )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].passed)
        self.assertTrue(results[0].infra_error)
        self.assertEqual(
            results[0].runtime_integrity_reason_code,
            RUNTIME_PROJECTION_MUTATED_DURING_CHECK,
        )
        self.assertEqual(results[0].output, "")
        self.assertEqual(
            (workspace / "node_modules" / "dep" / "index.js").read_text(
                encoding="utf-8"
            ),
            "dependency baseline\n",
        )

    def test_baseline_and_heldout_do_not_accept_mutated_runtime_as_semantic_evidence(self) -> None:
        root, _source, workspace, manager = self.make_manager()
        spec = {
            "name": "mutating hidden check",
            "feedback": "heldout",
            "command": [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('node_modules/dep/index.js').unlink(); "
                    "print('ORACLE_REACHED'); raise SystemExit(1)"
                ),
            ],
            "timeout_seconds": 30,
        }
        with self.assertRaises(HarnessControlledStop) as raised:
            run_benchmark_baseline_gate(
                [spec],
                workspace=workspace,
                toolchain={},
                runtime_root=root / "baseline-runtime",
                failure_marker="ORACLE_REACHED",
                runtime_integrity_manager=manager,
            )
        self.assertEqual(str(raised.exception), RUNTIME_PROJECTION_MUTATED_DURING_CHECK)

        results = run_checks(
            [spec],
            workspace=workspace,
            toolchain={},
            runtime_root=root / "heldout-runtime",
            label="HELDOUT",
            runtime_integrity_manager=manager,
            batch_id="HELDOUT",
        )
        evidence = classify_heldout_results(
            results=results,
            oracle_marker="ORACLE_REACHED",
            candidate_before="candidate",
            candidate_after="candidate",
        )
        self.assertEqual(evidence["status"], "HELDOUT_INFRA_ERROR")
        self.assertEqual(
            evidence["reason_code"],
            RUNTIME_PROJECTION_MUTATED_DURING_CHECK,
        )
        self.assertFalse(evidence["oracle_reached"])

    def test_deleted_runtime_file_during_batch_is_invalid_and_restored(self) -> None:
        _root, _source, workspace, manager = self.make_manager()
        dependency = workspace / "node_modules" / "dep" / "index.js"
        with self.assertRaises(RuntimeProjectionIntegrityError) as raised:
            manager.run_batch("REPAIR", lambda: dependency.unlink())
        self.assertEqual(
            raised.exception.reason_code,
            RUNTIME_PROJECTION_MUTATED_DURING_CHECK,
        )
        self.assertTrue(dependency.is_file())

    def test_pristine_batch_passes_without_recopy(self) -> None:
        _root, _source, workspace, manager = self.make_manager()
        dependency = workspace / "node_modules" / "dep" / "index.js"
        before = dependency.stat()
        self.assertEqual(manager.run_batch("PRISTINE", lambda: 7), 7)
        after = dependency.stat()
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))

    def test_source_change_is_not_promoted_to_new_baseline(self) -> None:
        _root, source, workspace, manager = self.make_manager()
        source_dep = source / "node_modules" / "dep" / "index.js"
        workspace_dep = workspace / "node_modules" / "dep" / "index.js"
        workspace_dep.write_text("workspace tamper", encoding="utf-8")
        source_dep.write_text("external source change", encoding="utf-8")
        with self.assertRaises(RuntimeProjectionIntegrityError) as raised:
            manager.prepare_before_batch("SOURCE_CHECK")
        self.assertEqual(raised.exception.reason_code, RUNTIME_PROJECTION_SOURCE_CHANGED)
        self.assertEqual(workspace_dep.read_text(encoding="utf-8"), "workspace tamper")

    def test_deleted_source_file_is_a_controlled_source_change(self) -> None:
        _root, source, workspace, manager = self.make_manager()
        source_dep = source / "node_modules" / "dep" / "index.js"
        workspace_dep = workspace / "node_modules" / "dep" / "index.js"
        source_dep.unlink()
        workspace_dep.write_text("workspace tamper", encoding="utf-8")
        with self.assertRaises(RuntimeProjectionIntegrityError) as raised:
            manager.prepare_before_batch("SOURCE_DELETE")
        self.assertEqual(raised.exception.reason_code, RUNTIME_PROJECTION_SOURCE_CHANGED)
        self.assertEqual(workspace_dep.read_text(encoding="utf-8"), "workspace tamper")

    def test_failed_restore_removes_partial_destination(self) -> None:
        _root, _source, workspace, manager = self.make_manager()
        destination = workspace / "node_modules"
        (destination / "dep" / "index.js").write_text("tampered", encoding="utf-8")

        def partial_copy(_source: Path, target: Path, **_kwargs: object) -> None:
            target.mkdir(parents=True)
            (target / "partial.txt").write_text("partial", encoding="utf-8")
            raise OSError("copy failed")

        with mock.patch("slivin_harness.runtime_projection.shutil.copytree", side_effect=partial_copy):
            with self.assertRaises(RuntimeProjectionIntegrityError) as raised:
                manager.prepare_before_batch("RESTORE_FAIL")
        self.assertEqual(
            raised.exception.reason_code,
            RUNTIME_PROJECTION_WORKSPACE_RESTORE_FAILED,
        )
        self.assertFalse(destination.exists())

    def test_public_artifact_contains_only_safe_events(self) -> None:
        root, source, workspace, manager = self.make_manager()
        (workspace / "node_modules" / "dep" / "index.js").write_text(
            "tampered", encoding="utf-8"
        )
        manager.prepare_before_batch("PUBLIC_EVENT")
        artifact = json.loads(
            (root / "run" / "runtime_projection_integrity.json").read_text(
                encoding="utf-8"
            )
        )
        text = json.dumps(artifact, sort_keys=True)
        self.assertEqual(artifact["projection_roots"], ["node_modules"])
        self.assertNotIn(str(source), text)
        self.assertNotIn("sha256", text)
        self.assertIn("RUNTIME_PROJECTION_RESTORED_BEFORE_TRUSTED_CHECK", text)

    def test_project_without_projection_is_a_noop(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-runtime-no-projection-"))
        workspace = root / "workspace"
        workspace.mkdir()
        manager = RuntimeProjectionIntegrityManager(
            session=WorkspaceSession(
                workspace=workspace,
                mode="git_worktree",
                managed=False,
            ),
            control_plane=ControllerPlane(root / "run"),
        )
        manager.establish_baseline()
        self.assertEqual(manager.run_batch("NOOP", lambda: "ok"), "ok")


if __name__ == "__main__":
    unittest.main()
