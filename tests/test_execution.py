from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from slivin_harness.control_plane import is_within
from slivin_harness.execution import (
    EnforcementLevel,
    ExecutionBroker,
    ExecutionRole,
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


if __name__ == "__main__":
    unittest.main()
