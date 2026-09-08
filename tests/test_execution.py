from __future__ import annotations

import tempfile
import os
import unittest
from pathlib import Path

from slivin_harness.control_plane import is_within
from slivin_harness.execution import (
    EnforcementLevel,
    ExecutionBroker,
    ExecutionRole,
    ScopedExecutionPolicyError,
)


class ExecutionBrokerTests(unittest.TestCase):
    def make_broker(self) -> tuple[ExecutionBroker, Path, Path]:
        root = Path(tempfile.mkdtemp(prefix="slivin-execution-"))
        workspace = root / "workspace"
        run_root = root / "runs" / "task"
        private = run_root / "controller_private"
        workspace.mkdir(parents=True)
        private.mkdir(parents=True)
        broker = ExecutionBroker(
            workspace=workspace,
            run_root=run_root,
            private_root=private,
            base_env={
                "PATH": "test-path",
                "HOME": str(root / "home"),
                "EXAMPLE_TOKEN": "must-not-leak",
                "SLIVIN_HARNESS_PRIVATE_ROOT": str(private),
            },
        )
        return broker, workspace, private

    def test_environment_filters_secrets_and_private_controller_paths(self) -> None:
        broker, workspace, private = self.make_broker()
        env = broker.environment_for(ExecutionRole.PLANNER)
        self.assertNotIn("EXAMPLE_TOKEN", env)
        self.assertNotIn("SLIVIN_HARNESS_PRIVATE_ROOT", env)
        self.assertEqual(env["SLIVIN_HARNESS_WORKSPACE"], str(workspace.resolve()))
        self.assertFalse(any(str(private.resolve()) in value for value in env.values()))

    def test_extra_environment_cannot_expose_private_path(self) -> None:
        broker, _, private = self.make_broker()
        aliases = (
            private / "secret.json",
            private.parent / "lexical-alias" / ".." / private.name / "secret.json",
        )
        for alias in aliases:
            with self.subTest(alias=alias), self.assertRaisesRegex(RuntimeError, "private path"):
                broker.environment_for(
                    ExecutionRole.IMPLEMENTER,
                    extra={"BAD": str(alias)},
                )

    def test_private_path_prefix_does_not_reject_unrelated_sibling(self) -> None:
        broker, _, private = self.make_broker()
        sibling = private.with_name(private.name + "_backup") / "public.json"
        env = broker.environment_for(
            ExecutionRole.IMPLEMENTER,
            extra={"PUBLIC_CACHE": str(sibling)},
        )
        self.assertEqual(env["PUBLIC_CACHE"], str(sibling))

    def test_sensitive_extra_environment_requires_explicit_preservation(self) -> None:
        broker, _, _ = self.make_broker()
        with self.assertRaisesRegex(RuntimeError, "preserve_sensitive"):
            broker.environment_for(
                ExecutionRole.RUNTIME,
                extra={"SERVICE_TOKEN": "secret"},
            )
        env = broker.environment_for(
            ExecutionRole.RUNTIME,
            extra={"SERVICE_TOKEN": "secret"},
            preserve_sensitive=("SERVICE_TOKEN",),
        )
        self.assertEqual(env["SERVICE_TOKEN"], "secret")

    def test_phase_two_does_not_claim_unimplemented_os_sandbox(self) -> None:
        broker, _, _ = self.make_broker()
        for role in (
            ExecutionRole.PLANNER,
            ExecutionRole.CONTROLLER_CHECK,
            ExecutionRole.EVALUATOR,
            ExecutionRole.HELDOUT,
        ):
            policy = broker.policy_for(role)
            self.assertNotEqual(
                policy.filesystem_enforcement,
                EnforcementLevel.ENFORCED.value,
                role,
            )

    def test_implementer_policy_is_workspace_write_not_private_plane_write(self) -> None:
        broker, workspace, private = self.make_broker()
        policy = broker.policy_for(ExecutionRole.IMPLEMENTER)
        self.assertEqual(policy.writable_roots, (str(workspace.resolve()),))
        self.assertNotIn(str(private.resolve()), policy.writable_roots)

    def test_each_role_gets_task_local_scratch(self) -> None:
        broker, workspace, _ = self.make_broker()
        roots = {broker.scratch_root(role) for role in ExecutionRole}
        self.assertEqual(len(roots), len(ExecutionRole))
        self.assertTrue(all(is_within(workspace / ".harness_tmp", root) for root in roots))

    def test_scoped_context_aligns_only_write_root_and_all_cache_paths(self) -> None:
        broker, workspace, private = self.make_broker()
        for role in (ExecutionRole.PLANNER, ExecutionRole.EVALUATOR):
            context = broker.prepare_readonly_role(role)
            self.assertEqual(context.project_root, workspace.resolve())
            self.assertNotEqual(context.scratch_root, context.project_root)
            config = context.thread_config()
            self.assertEqual(config[f"permissions.{context.profile_id}.filesystem"], {
                ":root": "read", str(context.scratch_root): "write",
            })
            self.assertNotIn(str(private), str(config))
            env = context.cache_environment()
            for key in ("TEMP", "TMP", "TMPDIR", "XDG_CACHE_HOME", "NPM_CONFIG_CACHE"):
                self.assertTrue(is_within(context.scratch_root, Path(env[key])), key)
            self.assertEqual(context.thread_settings()["cwd"], env["TEMP"])
            self.assertNotIn("sandbox", context.thread_settings())

    def test_fresh_roles_and_replans_do_not_inherit_temporary_files(self) -> None:
        broker, _, _ = self.make_broker()
        old = broker.prepare_readonly_role(ExecutionRole.PLANNER)
        (old.scratch_root / "rejected-probe.txt").write_text("old model", encoding="utf-8")
        fresh = broker.prepare_readonly_role(ExecutionRole.PLANNER)
        evaluator = broker.prepare_readonly_role(ExecutionRole.EVALUATOR)
        self.assertEqual(len({old.scratch_root, fresh.scratch_root, evaluator.scratch_root}), 3)
        self.assertFalse(list(fresh.scratch_root.iterdir()))
        self.assertFalse(list(evaluator.scratch_root.iterdir()))
        self.assertNotEqual(old.profile_id, fresh.profile_id)
        self.assertNotEqual(old.cache_environment()["TEMP"], evaluator.cache_environment()["TEMP"])

    def test_intake_and_implementer_cannot_accidentally_select_scoped_role(self) -> None:
        broker, _, _ = self.make_broker()
        for role in (ExecutionRole.INTAKE, ExecutionRole.IMPLEMENTER, ExecutionRole.CONTROLLER_CHECK):
            with self.assertRaises(ScopedExecutionPolicyError):
                broker.prepare_readonly_role(role)

    def test_reset_removes_long_jest_cache_paths_without_touching_peer_or_project(self) -> None:
        broker, workspace, _ = self.make_broker()
        context = broker.prepare_readonly_role(ExecutionRole.PLANNER)
        cache = context.scratch_root / "jest"
        cache.mkdir()
        path = cache / ("haste-map-" + "a" * 180)
        target = "\\\\?\\" + str(path) if os.name == "nt" else str(path)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("cached")
        peer = broker.prepare_readonly_role(ExecutionRole.EVALUATOR)
        canary = workspace / "source.txt"
        canary.write_text("source", encoding="utf-8")
        broker.clear_role_scratch(ExecutionRole.PLANNER)
        self.assertFalse(context.scratch_root.exists())
        self.assertEqual(list(broker.scratch_root(ExecutionRole.PLANNER).iterdir()), [])
        self.assertTrue(peer.scratch_root.is_dir())
        self.assertEqual(canary.read_text(encoding="utf-8"), "source")


if __name__ == "__main__":
    unittest.main()
