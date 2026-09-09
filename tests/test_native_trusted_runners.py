"""Opt-in installed Node/Jest acceptance. Agent replies are doubles; commands are real."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import task_runner
from slivin_harness.control_plane import ControllerPlane
from slivin_harness.execution import ExecutionBroker
from slivin_harness.phase4 import CheckRegistry
from slivin_harness.preflight import ToolProbeRegistry, run_static_toolchain_preflight
import test_task_runner_workflow as workflow_fixtures


@unittest.skipUnless(os.environ.get("SLIVIN_SMOKE_NODE") and os.environ.get("SLIVIN_SMOKE_JEST"), "Opt-in installed Node/Jest smoke; see tools/smoke_trusted_test_runners.py")
class NativeTrustedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tools = {"node": os.environ["SLIVIN_SMOKE_NODE"], "jest": os.environ["SLIVIN_SMOKE_JEST"]}
        self.root = Path(tempfile.mkdtemp(prefix="slivin-trusted-runner-"))
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.plane = ControllerPlane(self.root / "diagnostics")
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        def save_log():
            (self.root / "commands_full.txt").write_text(self.output.getvalue(), encoding="utf-8")
            print("NATIVE_TRUSTED_RUNNER_EVIDENCE:", self.root, file=sys.stderr)
        self.addCleanup(save_log)

    def test_real_assertions_generated_runner_controller_and_rebind(self):
        root = self.workspace
        git = workflow_fixtures.git
        git(root, "init")
        git(root, "config", "user.name", "Test")
        git(root, "config", "user.email", "test@example.invalid")
        (root / "jest.config.cjs").write_text("module.exports = {testRegex: 'unit.*test\\\\.cjs$', testEnvironment: 'node'};", encoding="utf-8")
        for passed in (True, False):
            expected = 1 if passed else 2
            suffix = "pass" if passed else "fail"
            (root / f"native_{suffix}.test.cjs").write_text(f"const {{test}} = require('node:test'); const assert = require('node:assert/strict'); test('actual assertion', () => assert.equal(1, {expected}));", encoding="utf-8")
            (root / f"unit_{suffix}.test.cjs").write_text(f"test('actual assertion', () => expect(1).toBe({expected}));", encoding="utf-8")
        git(root, "add", "--all")
        git(root, "commit", "-m", "Synthetic assertions")
        before = task_runner.candidate_content_fingerprint(root)
        registry = CheckRegistry(self.plane.private_root / "checks.json", workspace=root)
        paths = [f"{runner}_{result}.test.cjs" for runner in ("native", "unit") for result in ("pass", "fail")]
        specs, notes = task_runner.build_dynamic_check_specs(paths, workspace=root, toolchain=self.tools, base_specs=[])
        self.assertFalse(notes)
        for path in paths:
            registry.register_path(path)
        registry.bind_compiled_specs(specs)
        evidence = []
        for spec in specs:
            script, stamp, command = task_runner.prepare_self_verify_runner(workspace=root, specs=[spec], toolchain=self.tools)
            generated = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.output.write(generated.stdout + generated.stderr)
            results = task_runner.run_checks([spec], workspace=root, toolchain=self.tools, runtime_root=self.root / "checks", label=spec["name"])
            expected_pass = "_pass." in spec["check_path"]
            self.assertEqual(generated.returncode == 0, expected_pass)
            self.assertEqual(results[0].passed, expected_pass)
            self.assertEqual(task_runner.verify_self_verification_stamp(workspace=root, stamp_path=stamp, control_plane=self.plane, check_registry_digest=registry.digest()), expected_pass)
            combined = generated.stdout + generated.stderr
            self.assertIn("actual assertion", combined)
            if spec["runner"] == "NODE_TEST":
                self.assertNotIn("--test", spec["command"])
            evidence.append({"spec": spec, "command": list(command), "cwd": str(root), "generated_exit": generated.returncode, "controller": task_runner.check_records(results)})
        self.plane.write_public_json("native_runner_results.json", evidence)
        self.assertEqual(task_runner.candidate_content_fingerprint(root), before)
        # Template replay selects only the reconstructed tree, never original paths.
        reconstructed = self.root / "reconstructed"
        subprocess.run(["git", "clone", "--local", str(root), str(reconstructed)], check=True, capture_output=True)
        passed_specs = [spec for spec in specs if "_pass." in spec["check_path"]]
        for spec in passed_specs:
            argv = task_runner.expand_command(spec["command"], workspace=reconstructed, toolchain=self.tools)
            self.assertIn(str(reconstructed.resolve()), " ".join(argv))
            self.assertNotIn(str(root.resolve()), " ".join(argv))
        result = task_runner.run_checks(passed_specs, workspace=reconstructed, toolchain=self.tools, runtime_root=self.root / "replay", label="RECONSTRUCTED SYNTHETIC")
        self.assertTrue(all(row.passed for row in result))
        failed_specs = [spec for spec in specs if "_fail." in spec["check_path"]]
        negative = task_runner.run_checks(failed_specs, workspace=reconstructed, toolchain=self.tools, runtime_root=self.root / "replay-negative", label="RECONSTRUCTED NEGATIVE")
        self.assertEqual(len(negative), 2)
        self.assertTrue(all(not row.passed for row in negative))
        _, stamp, command = task_runner.prepare_self_verify_runner(workspace=root, specs=passed_specs, toolchain=self.tools)
        subprocess.run(command, cwd=root, check=True, capture_output=True)
        (root / "native_pass.test.cjs").write_text("require('node:test');\n", encoding="utf-8")
        self.assertFalse(task_runner.verify_self_verification_stamp(workspace=root, stamp_path=stamp, control_plane=self.plane, check_registry_digest=registry.digest()))

    def test_native_only_capability_probe_does_not_require_jest(self):
        (self.workspace / "native.test.cjs").write_text("const {test} = require('node:test'); test('ok', () => {});", encoding="utf-8")
        tools = {"node": self.tools["node"]}
        specs, notes = task_runner.build_dynamic_check_specs(["native.test.cjs"], workspace=self.workspace, toolchain=tools, base_specs=[])
        registry = ToolProbeRegistry(workspace=self.workspace, harness_root=task_runner.HARNESS_ROOT, toolchain=tools, source_repo=self.workspace, execution_broker=ExecutionBroker(workspace=self.workspace, run_root=self.plane.run_root, private_root=self.plane.private_root), control_plane=self.plane)
        result = run_static_toolchain_preflight(specs, workspace=self.workspace, harness_root=task_runner.HARNESS_ROOT, toolchain=tools, probe_registry=registry)
        self.assertTrue(result.passed, result.public_dict())
        self.assertIn("NODE", registry.verified_capabilities)
        self.assertNotIn("JEST", registry.verified_capabilities)

    def test_repair_framework_config_rebind_reaches_current_reconstruction(self):
        result, run_root, output = workflow_fixtures.TaskRunnerWorkflowIntegrationTests().run_case(
            benchmark=False, risk='medium', trusted_js_toolchain=self.tools,
            malformed_report=True, check_repair=True, framework_rebind=True)
        self.output.write(output)
        self.assertEqual(result, 0, output)
        registry = json.loads((run_root / 'controller_private/check_registry.json').read_text(encoding='utf-8'))
        self.assertEqual({row['runner'] for row in registry['compiled_specs']}, {'JEST'})
        acceptance = json.loads((run_root / 'final_acceptance.json').read_text(encoding='utf-8'))
        self.assertEqual(acceptance['reconstructed_verification']['status'], 'PASS')
        self.assertIn('IMPLEMENTER_REPORT_CORRECTED', output)

    def test_combined_expansion_report_correction_evaluator_final_gate(self):
        fixture = workflow_fixtures.TaskRunnerWorkflowIntegrationTests(methodName="runTest")
        result, run_root, output = fixture.run_case(benchmark=False, risk="medium", trusted_js_toolchain=self.tools, malformed_report=True)
        self.output.write(output)
        self.assertEqual(result, 0, output)
        acceptance = json.loads((run_root / "final_acceptance.json").read_text(encoding="utf-8"))
        self.assertEqual(acceptance["schema_version"], "final-acceptance.v3")
        self.assertIn("IMPLEMENTER_REPORT_CORRECTED: 1", output)
        self.assertFalse(list(run_root.glob("replan_*.json")))
        registry = json.loads((run_root / "controller_private/check_registry.json").read_text(encoding="utf-8"))
        self.assertEqual({r["runner"] for r in registry["compiled_specs"]}, {"NODE_TEST", "JEST"})
        self.assertEqual(len(list((run_root / "controller_private").glob("*.raw.json"))), 3)
        handoff = json.loads((run_root / "user_follow_up_report.json").read_text(encoding="utf-8"))
        self.assertEqual(handoff["count"], 1)
        self.plane.write_public_json("combined_workflow.json", {"run_root": str(run_root), "status": "PASS"})
