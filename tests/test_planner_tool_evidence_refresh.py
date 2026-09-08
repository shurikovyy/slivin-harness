from __future__ import annotations

import json
import sys
import unittest
from unittest import mock

import test_task_runner_workflow as workflow
import test_static_toolchain_preflight as static_tests
from slivin_harness.preflight import JestConfigProbe, run_static_toolchain_preflight
from slivin_harness.run_state import build_candidate_identity
from slivin_harness.phase7 import reset_workspace_for_semantic_replan


class PlannerToolEvidenceWorkflowTests(unittest.TestCase):
    def assert_refresh(self, root, output):
        initial = json.loads((root / "planner_tool_evidence_01.json").read_text(encoding="utf-8"))
        refreshed = json.loads((root / "planner_tool_evidence_02.json").read_text(encoding="utf-8"))
        self.assertEqual(initial["status"], "PASS")
        self.assertEqual(initial["tool_probe_evidence"]["probes"], [])
        self.assertIn("JEST", initial["tool_probe_evidence"]["reused_capabilities"])
        self.assertEqual(refreshed["status"], "PASS")
        self.assertIn("JEST", refreshed["available_capabilities"])
        self.assertEqual(
            (root / "planner_tool_evidence_02.json").read_bytes(),
            (root / "controller_private" / "planner_tool_evidence_02.json").read_bytes(),
        )
        self.assertNotIn(str(root.parent), json.dumps(refreshed))
        self.assertEqual(
            [row["id"] for row in refreshed["tool_probe_evidence"]["probes"]],
            ["jest.version", "jest.config.1"],
        )
        self.assertIn("NODE", refreshed["tool_probe_evidence"]["reused_capabilities"])
        self.assertEqual(initial["candidate_id"], refreshed["candidate_id"])
        self.assertIn("PLANNER_TOOL_EVIDENCE_PASS: SEMANTIC_REPLAN_01", output)
        state = json.loads((root / "run_state.json").read_text(encoding="utf-8"))
        self.assertIn("planner_tool_evidence_02.json", json.dumps(state["stages"]))

    def test_jest_evidence_refreshes_before_implementer_triggered_replan(self) -> None:
        result, root, output = workflow.TaskRunnerWorkflowIntegrationTests().run_case(
            benchmark=False, risk="medium", implementer_replan=True, jest_refresh=True,
        )
        self.assertEqual(result, 0, output)
        self.assertTrue((root / "final_acceptance.json").is_file(), output)
        self.assert_refresh(root, output)

    def test_evaluator_replan_uses_the_same_preparation(self) -> None:
        result, root, output = workflow.TaskRunnerWorkflowIntegrationTests().run_case(
            benchmark=False, risk="medium", with_replan=True, jest_refresh=True,
        )
        self.assertEqual(result, 0, output)
        self.assert_refresh(root, output)

    def test_failed_refresh_stops_before_planner_with_typed_evidence(self) -> None:
        for failure, reason in (
            ("config", "STATIC_JEST_CONFIG_PROBE_FAILED"),
            ("missing", "STATIC_TOOLCHAIN_PATH_NOT_FOUND"),
            ("candidate", "STATIC_PREFLIGHT_MUTATED_CANDIDATE"),
            ("git", "STATIC_GIT_CONTROL_INTEGRITY_FAILED"),
        ):
            with self.subTest(failure=failure):
                result, root, output = workflow.TaskRunnerWorkflowIntegrationTests().run_case(
                    benchmark=False, risk="medium", implementer_replan=True,
                    jest_refresh=True, jest_refresh_failure=failure,
                )
                self.assertEqual(result, 2, output)
                record = json.loads((root / "planner_tool_evidence_02.json").read_text(encoding="utf-8"))
                self.assertEqual(record["status"], "FAIL")
                self.assertIn(reason, record["reason_codes"])
                self.assertEqual(record["available_capabilities"], [])
                self.assertFalse((root / "replan_01.json").exists())
                self.assertFalse((root / "final_acceptance.json").exists())
                self.assertIn("PLANNER_TOOL_EVIDENCE_FAIL", output)
                self.assertNotIn("Traceback", output)

    def test_real_runtime_rebuild_refreshes_only_required_project_python(self) -> None:
        for usage in ("required", "unused"):
            with self.subTest(usage=usage):
                result, root, output = workflow.TaskRunnerWorkflowIntegrationTests().run_case(
                    benchmark=False, risk="medium", implementer_replan=True,
                    jest_refresh=True, runtime_rebuild=usage,
                )
                self.assertEqual(result, 0, output)
                self.assertTrue((root / "project_runtime_replan_01.json").is_file())
                refreshed = json.loads((root / "planner_tool_evidence_02.json").read_text(encoding="utf-8"))
                probes = {row["id"] for row in refreshed["tool_probe_evidence"]["probes"]}
                self.assertTrue({"jest.version", "jest.config.1"} <= probes)
                self.assertEqual("project-python.version" in probes, usage == "required")
                self.assertEqual("project-python.venv-binding" in probes, usage == "required")
                self.assertIsNotNone(refreshed["runtime_id"])
                initial = json.loads((root / "planner_tool_evidence_01.json").read_text(encoding="utf-8"))
                self.assertNotEqual(initial["revision_snapshot"], refreshed["revision_snapshot"])


class PlannerToolEvidenceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reuse only fixture builders; real subprocess/registry/candidate guards.
        self.fixture = static_tests.StaticToolchainPreflightTests()
        self.fixture.setUp()
        f = self.fixture
        self.jest = f.fake_jest()
        self.spec = f.jest_check()
        self.baseline = f.commit_candidate()
        self.registry = f.registry({"node": sys.executable, "jest": str(self.jest)})

    def prepare(self, checks=None):
        f = self.fixture
        return run_static_toolchain_preflight(
            [self.spec] if checks is None else checks,
            workspace=f.workspace, harness_root=f.harness_root,
            toolchain=self.registry.toolchain, probe_registry=self.registry,
            candidate_baseline_sha=self.baseline, refresh_known_tools=True,
        )

    def test_initial_and_unchanged_evidence_reuse(self) -> None:
        self.assertTrue(self.prepare().passed)
        repeated = self.prepare()
        self.assertTrue(repeated.passed)
        self.assertEqual(repeated.probes, ())
        self.assertEqual(set(repeated.tool_probe_evidence.reused_capabilities), {"JEST", "NODE"})

    def test_candidate_binding_precedes_refresh_without_late_invalidation(self) -> None:
        self.assertTrue(self.prepare().passed)
        (self.fixture.workspace / "candidate.txt").write_text("changed", encoding="utf-8")
        changed = self.prepare()  # No manual registry bind by the caller.
        self.assertTrue(changed.passed)
        self.assertEqual([p.probe_id for p in changed.probes], ["jest.version", "jest.config.1"])
        self.assertIn("JEST", self.registry.verified_capabilities)
        self.assertEqual(self.prepare().probes, ())

    def test_changed_toolchain_reference_requires_new_probe(self) -> None:
        self.assertTrue(self.prepare().passed)
        self.registry.toolchain["node"] = str(self.fixture.root / "missing-node.exe")
        failed = self.prepare()
        self.assertFalse(failed.passed)
        self.assertNotIn("NODE", self.registry.verified_capabilities)
        self.assertNotIn("JEST", self.registry.verified_capabilities)

    def test_failed_additional_config_revokes_previous_jest_pass(self) -> None:
        self.assertTrue(self.prepare().passed)
        bad = self.fixture.workspace / "bad.config.cjs"
        bad.write_text("INVALID", encoding="utf-8")
        failed = self.registry.ensure_capabilities(
            ["JEST"], batch_id="new-config",
            jest_configs=[JestConfigProbe("bad", bad)],
        )
        self.assertFalse(failed.passed)
        self.assertNotIn("JEST", self.registry.verified_capabilities)

    def test_rejected_config_is_not_carried_into_fresh_explicit_or_auto_probe(self) -> None:
        for current_checks in ([self.spec], []):
            with self.subTest(explicit=bool(current_checks)):
                self.assertTrue(self.prepare().passed)
                rejected = self.fixture.workspace / "rejected.config.cjs"
                rejected.write_text("module.exports = {};", encoding="utf-8")
                self.registry.bind_candidate_identity(build_candidate_identity(
                    self.fixture.workspace, baseline_sha=self.baseline,
                ).candidate_id)
                self.assertTrue(self.registry.ensure_capabilities(
                    ["JEST"], batch_id="rejected-config",
                    jest_configs=[JestConfigProbe("rejected", rejected)],
                ).passed)
                reset_workspace_for_semantic_replan(workspace=self.fixture.workspace, baseline_sha=self.baseline)
                self.assertFalse(rejected.exists())
                start = len(self.registry.private_probe_commands)
                refreshed = self.prepare(current_checks)
                self.assertTrue(refreshed.passed, refreshed.public_dict())
                commands = self.registry.private_probe_commands[start:]
                self.assertNotIn(str(rejected), json.dumps(commands))
                config = next(row for row in commands if row["id"].startswith("jest.config."))
                self.assertEqual("--config" in config["argv"], bool(current_checks))

    def test_runtime_projection_mutation_during_refresh_revokes_evidence(self) -> None:
        f = self.fixture
        manager, source, _, projected = f.projection_manager()
        registry = f.registry(
            {"node": sys.executable, "jest": str(projected)},
            manager=manager, source_repo=source, historical=True,
            rebound={"jest": "runtime/jest.py"},
        )
        self.assertTrue(registry.ensure_capabilities(["JEST"], batch_id="initial").passed)
        registry.invalidate_runtime_environment_evidence()
        original = registry._run_probe

        def mutate(**kwargs):
            result = original(**kwargs)
            (projected.parent / "dep.js").write_text("mutated", encoding="utf-8")
            return result

        with mock.patch.object(registry, "_run_probe", side_effect=mutate):
            result = registry.ensure_capabilities(["JEST"], batch_id="refresh")
        self.assertIn("STATIC_RUNTIME_INTEGRITY_FAILED", result.reason_codes)
        self.assertFalse(registry.verified_capabilities)


if __name__ == "__main__":
    unittest.main()
