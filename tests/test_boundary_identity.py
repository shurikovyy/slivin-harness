"""Execution-mode invariant boundary identity and exact release executable boot."""
from __future__ import annotations

import inspect
import json
import pathlib as pathlib_module
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from slivin_harness.boundaries import BOUNDARIES, boundary
from slivin_harness.entrypoint_identity import (
    EntrypointIdentityError,
    canonical_boundary_entrypoint,
    canonical_module_from_source,
    runtime_boundary_entrypoint,
)
from slivin_harness.native_role_admission import admit_native_phase_with_recovery
from tools.check_release_entrypoints import BOOT_TARGETS
from tools.release_suite import check_inventory


ROOT = Path(__file__).resolve().parents[1]


class BoundaryIdentityAndBootTests(unittest.TestCase):
    def run_script(self, relative: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, relative, *args], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )

    def test_source_relative_module_identity_is_controller_owned(self) -> None:
        self.assertEqual(canonical_module_from_source(ROOT / "task_runner.py"), "task_runner")
        self.assertEqual(canonical_module_from_source(
            ROOT / "tools/smoke_readonly_scratch.py"), "tools.smoke_readonly_scratch")
        self.assertEqual(canonical_module_from_source(
            ROOT / "slivin_harness/evaluator.py"), "slivin_harness.evaluator")

    def test_imported_b21_runtime_identity_matches_static_source_identity(self) -> None:
        expected = "slivin_harness.native_role_admission.admit_native_phase_with_recovery"
        self.assertEqual(admit_native_phase_with_recovery.__boundary_entrypoint__, expected)
        self.assertEqual(runtime_boundary_entrypoint(admit_native_phase_with_recovery), expected)
        source = inspect.getsourcefile(inspect.unwrap(admit_native_phase_with_recovery))
        self.assertEqual(canonical_boundary_entrypoint(
            source, inspect.unwrap(admit_native_phase_with_recovery).__qualname__), expected)
        self.assertIn(expected, BOUNDARIES["B21"][1])

    def test_smoke_script_help_and_boot_are_model_free_and_identity_stable(self) -> None:
        help_result = self.run_script("tools/smoke_readonly_scratch.py", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("usage: smoke_readonly_scratch.py", help_result.stdout)

        boot = self.run_script("tools/smoke_readonly_scratch.py", "--boot-check")
        self.assertEqual(boot.returncode, 0, boot.stderr)
        payload = json.loads(boot.stdout)
        self.assertEqual(payload["source"], "tools/smoke_readonly_scratch.py")
        self.assertEqual(payload["module"], "tools.smoke_readonly_scratch")
        self.assertEqual(payload["model_execution"], "NOT_RUN")
        self.assertEqual(payload["boundary_entrypoints"], [
            admit_native_phase_with_recovery.__boundary_entrypoint__,
        ])

    def test_task_runner_script_mode_keeps_task_runner_identities(self) -> None:
        result = self.run_script("task_runner.py", "--boot-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["module"], "task_runner")
        self.assertEqual(payload["model_execution"], "NOT_RUN")
        self.assertIn("task_runner.run_checks", payload["boundary_entrypoints"])
        self.assertNotIn("__main__", "\n".join(payload["boundary_entrypoints"]))
        self.assertEqual(canonical_boundary_entrypoint(
            ROOT / "task_runner.py", "main.<locals>.prepare_planner_capabilities"),
            "task_runner.main.prepare_planner_capabilities")

    def test_task_runner_runpy_main_mode_has_the_same_identity(self) -> None:
        script = (
            "import runpy,sys;"
            "sys.argv=['task_runner.py','--boot-check'];"
            "runpy.run_path('task_runner.py',run_name='__main__')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["module"], "task_runner")
        self.assertIn("task_runner.run_checks", payload["boundary_entrypoints"])

    def test_unrelated_main_is_not_rewritten_to_task_runner(self) -> None:
        result = self.run_script(
            "tests/fixtures/boundary_identity/unrelated_main.py")
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn(
            "tests.fixtures.boundary_identity.unrelated_main.admit_native_phase_with_recovery",
            combined,
        )
        self.assertNotIn("task_runner.admit_native_phase_with_recovery", combined)

    def test_outside_root_decorated_function_fails_closed(self) -> None:
        outside = pathlib_module.__file__
        self.assertIsNotNone(outside)

        def candidate():
            return None

        with patch("slivin_harness.entrypoint_identity.inspect.getsourcefile",
                   return_value=outside):
            with self.assertRaisesRegex(
                EntrypointIdentityError, "escapes the Harness source tree"
            ):
                boundary("B21")(candidate)

    def test_static_inventory_uses_the_same_canonical_identity(self) -> None:
        inventory = check_inventory()
        b21 = next(row for row in inventory["boundaries"] if row["boundary_id"] == "B21")
        self.assertEqual(b21["entrypoints"], [
            admit_native_phase_with_recovery.__boundary_entrypoint__,
        ])

    def test_runtime_decorator_contains_no_main_or_module_alias(self) -> None:
        source = inspect.getsource(boundary)
        self.assertNotIn("__main__", source)
        self.assertNotIn("__module__", source)
        self.assertIn("runtime_boundary_entrypoint", source)

    def test_exact_boot_gate_covers_all_later_model_executables(self) -> None:
        output = ROOT / (".entrypoint-boot-test-" + uuid.uuid4().hex)
        self.addCleanup(shutil.rmtree, output, ignore_errors=True)
        result = self.run_script(
            "tools/check_release_entrypoints.py", "--output", str(output))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["model_execution"], "NOT_RUN")
        self.assertEqual([row["source"] for row in summary["targets"]],
                         [target[1] for target in BOOT_TARGETS])
        self.assertTrue(all(row["status"] == "PASS" for row in summary["targets"]))


if __name__ == "__main__":
    unittest.main()
