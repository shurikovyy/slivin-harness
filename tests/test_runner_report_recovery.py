"""Generic runner and report-boundary regressions; no product-case fixtures."""
from __future__ import annotations

import copy
import contextlib
import io
import shutil
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import task_runner
import test_implementer_impact_closure as fixtures


class RunnerResolutionTests(unittest.TestCase):
    def test_same_suffix_different_frameworks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "native.test.cjs").write_text("const {test} = require('node:test'); test('ok', () => {});", encoding="utf-8")
            (root / "unit.test.cjs").write_text("test('ok', () => expect(1).toBe(1));", encoding="utf-8")
            specs, notes = task_runner.build_dynamic_check_specs(
                ["native.test.cjs", "unit.test.cjs"], workspace=root,
                toolchain={"node": "node", "jest": "jest.cjs"}, base_specs=[],
            )
            self.assertEqual(notes, [])
            expanded = task_runner.expand_command(specs[0]["command"], workspace=root, toolchain={"node": "node", "jest": "jest.cjs"})
            self.assertEqual(expanded[0], "node")
            self.assertEqual(Path(expanded[1]).resolve(), (root / "native.test.cjs").resolve())
            self.assertEqual(specs[1]["command"][:2], ["{node}", "{jest}"])

    def test_native_import_forms_and_literal_comments(self):
        from slivin_harness.test_runners import resolve_javascript_runner, TestRunnerResolutionError
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.test.cjs"
            native = [
                "const test = require('node:test');",
                "const {test: check} = require( /* module */ 'node:test');",
                "import test from 'node:test';",
                "import {test as check, describe} from 'node:test';",
                "import * as suite from 'node:test';",
                "const {test} = await import('node:test');",
            ]
            for source in native:
                with self.subTest(source=source):
                    path.write_text(source, encoding="utf-8")
                    self.assertEqual(resolve_javascript_runner(path, relative=path.name), "NODE_TEST")
            for decoy in ["// require('node:test');", "/* import t from 'node:test'; */", 'const text = "node:test";', "const example = `require('node:test')`;", r"const pattern = /require('node:test')/;"]:
                path.write_text(decoy + "\ntest('ok', () => expect(1).toBe(1));", encoding="utf-8")
                self.assertEqual(resolve_javascript_runner(path, relative=path.name), "JEST")
            for source in ["const {test} = require('node:test'); expect(1).toBe(1);", "import {test, expect} from 'vitest';", "console.log('no assertions');", "// node:test"]:
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(TestRunnerResolutionError):
                    resolve_javascript_runner(path, relative=path.name)

    def test_native_without_jest_and_missing_node(self):
        from slivin_harness.test_runners import TestRunnerResolutionError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "first.test.mjs").write_text("import test from 'node:test';", encoding="utf-8")
            (root / "second.test.cjs").write_text("require('node:test');", encoding="utf-8")
            specs, notes = task_runner.build_dynamic_check_specs(["first.test.mjs", "second.test.cjs"], workspace=root, toolchain={"node": "node"}, base_specs=[])
            self.assertEqual(len(specs), 2)
            self.assertEqual([len(s["command"]) for s in specs], [2, 2])
            self.assertFalse(notes)
            with self.assertRaises(TestRunnerResolutionError) as failure:
                task_runner.build_dynamic_check_specs(["first.test.mjs"], workspace=root, toolchain={}, base_specs=[])
            self.assertEqual(failure.exception.code, "TEST_RUNNER_TOOL_MISSING")

    def test_owner_commands_unchanged_and_no_substring_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.test.cjs").write_text("require('node:test');", encoding="utf-8")
            owner = [{"name": "Owner", "command": ["custom", "a.test.cjs.backup"]}]
            original = copy.deepcopy(owner)
            specs, _ = task_runner.build_dynamic_check_specs(["a.test.cjs"], workspace=root, toolchain={"node": "node"}, base_specs=owner)
            self.assertEqual(len(specs), 1)
            self.assertEqual(owner, original)
            owner[0]["command"][-1] = "a.test.cjs"
            specs, notes = task_runner.build_dynamic_check_specs(["a.test.cjs"], workspace=root, toolchain={}, base_specs=owner)
            self.assertFalse(specs)
            self.assertIn("ALREADY_COVERED", notes[0])

    def test_registry_binds_compiled_runner_idempotently(self):
        from slivin_harness.phase4 import CheckRegistry
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.test.cjs").write_text("require('node:test');", encoding="utf-8")
            registry = CheckRegistry(root / "registry.json", workspace=root)
            registry.register_path("a.test.cjs")
            specs, _ = task_runner.build_dynamic_check_specs(["a.test.cjs"], workspace=root, toolchain={"node": "node", "jest": "jest"}, base_specs=[])
            registry.bind_compiled_specs(specs)
            digest = registry.digest()
            registry.bind_compiled_specs(specs)
            self.assertEqual(registry.digest(), digest)
            (root / "a.test.cjs").write_text("test('ok', () => expect(1).toBe(1));", encoding="utf-8")
            specs, _ = task_runner.build_dynamic_check_specs(["a.test.cjs"], workspace=root, toolchain={"node": "node", "jest": "jest"}, base_specs=[])
            registry.bind_compiled_specs(specs)
            self.assertNotEqual(registry.digest(), digest)


class ReportRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ImplementerImpactClosureTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.plane = task_runner.ControllerPlane(fixture.workspace.parent / (fixture.workspace.name + "-test-report"))
        self.addCleanup(shutil.rmtree, self.plane.run_root)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_empty_related_symbols_corrected_in_same_thread(self):
        f = self.fixture
        valid = copy.deepcopy(f.report)
        valid["post_patch_impact"]["related_out_of_scope"].append({
            "name": "Documentation navigation", "paths": ["README.md"],
            "symbols": ["README.md#notes"], "relation": "Reader documentation navigation",
            "reason": "The prose navigation issue does not affect entry validity.",
            "evidence": ["README.md contains the Notes heading."],
            "suggested_follow_up": "Clarify the navigation below the Notes heading.",
        })
        invalid = copy.deepcopy(valid)
        invalid["post_patch_impact"]["related_out_of_scope"][-1]["symbols"] = []
        with mock.patch.object(task_runner, "run_agent_turn", side_effect=[json.dumps(invalid), json.dumps(valid)]) as turn, mock.patch.object(task_runner, "verify_self_verification_stamp", return_value=True):
            result = task_runner.run_implementer_report(
                mock.Mock(), thread_id="same-thread", prompt="Implement", timeout=30, label="TEST",
                implementation_contract=f.contract, self_verify_command=["self"], workspace=f.workspace,
                stamp_path=f.workspace / ".harness_tmp/stamp.json", plan=f.plan, control_plane=self.plane,
            )
        self.assertEqual(result, valid)
        self.assertEqual(turn.call_count, 2)
        self.assertEqual(turn.call_args.kwargs["thread_id"], "same-thread")

    def call_reports(self, responses, **options):
        f = self.fixture
        def answer(*_args, **kwargs):
            response = responses.pop(0)
            return response(kwargs) if callable(response) else json.dumps(response)
        with mock.patch.object(task_runner, "run_agent_turn", side_effect=answer) as turn, mock.patch.object(task_runner, "verify_self_verification_stamp", return_value=True):
            try:
                return task_runner.run_implementer_report(
                    mock.Mock(), thread_id="same-thread", prompt="Implement", timeout=30, label="TEST",
                    implementation_contract=f.contract, self_verify_command=["self"], workspace=f.workspace,
                    stamp_path=f.workspace / ".harness_tmp/stamp.json", plan=f.plan, control_plane=self.plane, **options,
                )
            finally:
                self.turns = turn.call_count

    def invalid(self):
        report = copy.deepcopy(self.fixture.report)
        report["post_patch_impact"]["related_out_of_scope"][0]["symbols"] = []
        return report

    def test_two_turn_limit_preserves_raw_and_terminal_inventory(self):
        invalid = self.invalid()
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, "CORRECTION_EXHAUSTED"):
            self.call_reports([invalid, invalid, invalid])
        self.assertEqual(self.turns, 3)
        raw = list(self.plane.private_root.glob("*.raw.json"))
        self.assertEqual(len(raw), 3)
        self.assertFalse(list(self.plane.run_root.glob("*.raw.json")))
        public = json.loads((self.plane.run_root / "terminal_candidate_observation.json").read_text(encoding="utf-8"))
        self.assertEqual(public["status"], "OBSERVED")
        self.assertEqual(public["candidate"]["changed_paths"], ["state.py"])
        self.assertFalse((self.plane.run_root / "final_acceptance.json").exists())

    def test_correction_cannot_drop_findings_consumers_or_checks(self):
        invalid = self.invalid()
        for group in ("related_out_of_scope", "in_scope_consumers", "changed_contracts"):
            with self.subTest(group=group):
                altered = copy.deepcopy(self.fixture.report)
                altered["post_patch_impact"][group] = []
                with self.assertRaisesRegex(task_runner.HarnessControlledStop, "CHANGED_CLAIMS"):
                    self.call_reports([invalid, altered])
        altered = copy.deepcopy(self.fixture.report)
        altered["registered_checks"] = [{"kind": "check_id", "value": "git.diff-check"}]
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, "CHANGED_CLAIMS"):
            self.call_reports([invalid, altered])

    def test_candidate_mutation_during_correction_stops_before_receipt(self):
        invalid = self.invalid()
        def mutation(kwargs):
            self.assertIn("Do not modify project files", kwargs["prompt"])
            (self.fixture.workspace / "reader_a.py").write_text("changed in correction\n", encoding="utf-8")
            return json.dumps(self.fixture.report)
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, "MUTATED_CANDIDATE"):
            self.call_reports([invalid, mutation])
        observation = json.loads((self.plane.run_root / "terminal_candidate_observation.json").read_text(encoding="utf-8"))
        self.assertIn("reader_a.py", observation["candidate"]["changed_paths"])
        self.assertFalse((self.plane.private_root / "self_verify_receipt_current.json").exists())

    def test_semantic_conflict_and_missing_consumer_are_not_cosmetic_retries(self):
        for group in ("changed_contracts", "in_scope_consumers"):
            invalid = copy.deepcopy(self.fixture.report)
            invalid["post_patch_impact"][group] = []
            with self.assertRaisesRegex(task_runner.HarnessControlledStop, "REPORT_INVALID"):
                self.call_reports([invalid])
            self.assertEqual(self.turns, 1)

    def test_full_validator_still_rejects_untrusted_verification(self):
        f = self.fixture
        with mock.patch.object(task_runner, "run_agent_turn", return_value=json.dumps(self.invalid())), mock.patch.object(task_runner, "verify_self_verification_stamp", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "trusted self-verification"):
                task_runner.run_implementer_report(mock.Mock(), thread_id="thread", prompt="Implement", timeout=30, label="TEST", implementation_contract=f.contract, self_verify_command=[], workspace=f.workspace, stamp_path=f.workspace / "stamp", plan=f.plan, control_plane=self.plane)

    def test_correctable_field_does_not_relax_empty_evidence(self):
        from slivin_harness.report_recovery import correctable_report_field
        from slivin_harness.protocol import ArtifactContractError
        for code in ("POST_PATCH_MODEL_DIVERGENCE", "POST_PATCH_PLANNER_CONSUMERS", "UNSAFE_PATH"):
            error = ArtifactContractError(code=code, field="post_patch_impact.related_out_of_scope[0].symbols", message="bad", expected="evidence")
            self.assertIsNone(correctable_report_field(error))

    def test_missing_evidence_key_can_be_corrected_without_changing_row(self):
        invalid = self.invalid()
        del invalid["post_patch_impact"]["related_out_of_scope"][0]["symbols"]
        self.assertEqual(self.call_reports([invalid, self.fixture.report]), self.fixture.report)
        self.assertEqual(self.turns, 2)

    def test_corrected_leaf_does_not_skip_remaining_consumer_validation(self):
        invalid = self.invalid()
        invalid["post_patch_impact"]["in_scope_consumers"] = []
        corrected = copy.deepcopy(invalid)
        corrected["post_patch_impact"]["related_out_of_scope"][0]["symbols"] = ["ratio"]
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, "REPORT_INVALID"):
            self.call_reports([invalid, corrected])
        self.assertEqual(self.turns, 2)

    def test_report_correction_timeout_has_no_extra_continuation(self):
        def timeout(_kwargs):
            raise task_runner.TurnTimeoutError("Synthetic inactivity")
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, "CORRECTION_TIMEOUT"):
            self.call_reports([self.invalid(), timeout])
        self.assertEqual(self.turns, 2)

    def test_git_integrity_guard_also_wraps_correction(self):
        from slivin_harness.git_integrity import GitControlIntegrityManager
        manager = GitControlIntegrityManager(workspace=self.fixture.workspace, control_plane=self.plane)
        manager.establish_baseline()
        def mutate(_kwargs):
            with (self.fixture.workspace / ".git/info/exclude").open("a", encoding="utf-8") as handle:
                handle.write("concealed.py\n")
            return json.dumps(self.fixture.report)
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, "GIT_CONTROL_STATE_MUTATED"):
            self.call_reports([self.invalid(), mutate], git_integrity_manager=manager)
        self.assertEqual(self.turns, 2)

    def test_failed_terminal_observation_explicitly_marks_previous_identity_stale(self):
        state = mock.Mock()
        state.data = {"current_candidate": {"candidate_id": "previous"}}
        with mock.patch.object(task_runner, "build_candidate_identity", side_effect=OSError("unreadable fixture")):
            task_runner.observe_terminal_report_candidate(self.fixture.workspace, self.plane, state, "REPORT_INVALID")
        observation = json.loads((self.plane.run_root / "terminal_candidate_observation.json").read_text(encoding="utf-8"))
        self.assertEqual(observation["status"], "UNKNOWN")
        self.assertTrue(observation["previous_identity_stale"])
        self.assertIsNone(state.data["current_candidate"])

    def test_receipt_recheck_failure_cannot_follow_validation_with_complete(self):
        f = self.fixture
        with mock.patch.object(task_runner, "run_agent_turn", return_value=json.dumps(f.report)), mock.patch.object(task_runner, "verify_self_verification_stamp", side_effect=[True, False]):
            with self.assertRaisesRegex(RuntimeError, "current trusted self-verification receipt"):
                task_runner.run_implementer_report(mock.Mock(), thread_id="thread", prompt="Implement", timeout=30, label="TEST", implementation_contract=f.contract, self_verify_command=[], workspace=f.workspace, stamp_path=f.workspace / "stamp", plan=f.plan, control_plane=self.plane)


if __name__ == "__main__":
    unittest.main()
