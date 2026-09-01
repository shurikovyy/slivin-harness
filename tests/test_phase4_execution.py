from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from slivin_harness.control_plane import is_within
from slivin_harness.phase4 import (
    ActivityWatchdog,
    CheckClassification,
    CheckRegistry,
    ControllerCheckRunner,
    ImplementerStatus,
    Phase4ContractError,
    RevisionBinding,
    SelfVerifyReceipt,
    classify_check_result,
    validate_implementer_report,
)


class ImplementerProtocolV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [
            {"id": "ACCEPTANCE-1", "kind": "acceptance", "allow_not_affected": False},
            {"id": "CONSUMER-1", "kind": "consumer", "allow_not_affected": True},
        ]

    def test_complete_requires_full_contract_and_receipt(self) -> None:
        report = {
            "protocol_version": "implementer.v3",
            "status": "COMPLETE",
            "summary": "done",
            "contract_evidence": [
                {"item_id": "ACCEPTANCE-1", "status": "VERIFIED", "evidence": ["test pass"]},
                {"item_id": "CONSUMER-1", "status": "NOT_AFFECTED", "evidence": ["unreachable"]},
            ],
            "self_verification": {"status": "PASS", "receipt_id": "receipt-1"},
            "discovered_obligations": [],
            "registered_checks": [],
        }
        validated = validate_implementer_report(report, active_contract_items=self.items)
        self.assertEqual(validated["status"], "COMPLETE")

    def test_non_complete_status_does_not_require_contract_ledger(self) -> None:
        for status in (
            ImplementerStatus.REPLAN_REQUIRED.value,
            ImplementerStatus.BLOCKED.value,
            ImplementerStatus.NEEDS_USER_DECISION.value,
        ):
            report = {
                "protocol_version": "implementer.v3",
                "status": status,
                "summary": "cannot finish",
                "reason": "concrete reason",
                "evidence": ["repository fact"],
            }
            validate_implementer_report(report, active_contract_items=self.items)

    def test_non_consumer_cannot_be_not_affected(self) -> None:
        report = {
            "protocol_version": "implementer.v3",
            "status": "COMPLETE",
            "summary": "done",
            "contract_evidence": [
                {"item_id": "ACCEPTANCE-1", "status": "NOT_AFFECTED", "evidence": ["x"]},
                {"item_id": "CONSUMER-1", "status": "VERIFIED", "evidence": ["x"]},
            ],
            "self_verification": {"status": "PASS", "receipt_id": "receipt-1"},
            "discovered_obligations": [],
            "registered_checks": [],
        }
        with self.assertRaises(Phase4ContractError):
            validate_implementer_report(report, active_contract_items=self.items)


class CheckRegistryTests(unittest.TestCase):
    def test_registry_is_typed_deduplicated_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            private = root / "controller_private"
            workspace.mkdir()
            (workspace / "tests").mkdir()
            (workspace / "tests" / "test_example.py").write_text("def test_x(): pass\n")
            registry = CheckRegistry(
                private / "check_registry.json",
                workspace=workspace,
                trusted_check_ids={"project:smoke"},
            )
            registry.register_path("tests/test_example.py")
            registry.register_path("tests/test_example.py")
            registry.register_id("project:smoke")
            data = registry.load()
            self.assertEqual(data["revision"], 2)
            self.assertEqual(len(data["checks"]), 2)
            self.assertTrue(is_within(private, registry.path))
            self.assertFalse(is_within(workspace, registry.path))

    def test_registry_reset_starts_a_new_empty_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            private = root / "controller_private"
            workspace.mkdir()
            (workspace / "test_example.py").write_text("assert True\n", encoding="utf-8")
            registry = CheckRegistry(
                private / "check_registry.json",
                workspace=workspace,
                trusted_check_ids={"project:smoke"},
            )
            registry.register_path("test_example.py")
            digest_before = registry.digest()
            revision_before = registry.load()["revision"]

            registry.reset()

            data = registry.load()
            self.assertEqual(data["checks"], [])
            self.assertEqual(data["revision"], revision_before + 1)
            self.assertNotEqual(registry.digest(), digest_before)

    def test_registry_rejects_escape_and_arbitrary_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            private = root / "controller_private"
            workspace.mkdir()
            registry = CheckRegistry(private / "check_registry.json", workspace=workspace)
            with self.assertRaises(Phase4ContractError):
                registry.register_path("../outside.py")
            with self.assertRaises(Phase4ContractError):
                registry.register_id("rm -rf /;")
            with self.assertRaisesRegex(Phase4ContractError, "Unknown trusted check id"):
                registry.register_id("project:safe-but-unknown")


class ReceiptTests(unittest.TestCase):
    def test_receipt_is_bound_to_candidate_contract_plan_and_registry(self) -> None:
        binding = RevisionBinding(
            candidate_id="candidate-1",
            task_contract_rev=1,
            plan_rev=1,
            implementation_contract_rev=2,
            verification_plan_rev=3,
            runtime_env_id="env-1",
            attempt_id="attempt-1",
        )
        receipt = SelfVerifyReceipt.create(
            binding=binding,
            registry_digest="registry-a",
            checks=["project:smoke"],
            now=1.0,
        )
        self.assertTrue(receipt.matches(binding=binding, registry_digest="registry-a"))
        changed = RevisionBinding(**{**binding.as_dict(), "verification_plan_rev": 4})
        self.assertFalse(receipt.matches(binding=changed, registry_digest="registry-a"))
        self.assertFalse(receipt.matches(binding=binding, registry_digest="registry-b"))


class WatchdogTests(unittest.TestCase):
    def test_active_tool_prevents_inactivity_interrupt(self) -> None:
        watchdog = ActivityWatchdog(inactivity_timeout_seconds=10, started_at=0, last_real_activity_at=0)
        watchdog.tool_started(now=1)
        self.assertFalse(watchdog.should_interrupt(process_alive=True, now=100))
        watchdog.tool_completed(now=100)
        self.assertFalse(watchdog.should_interrupt(process_alive=True, now=105))
        self.assertTrue(watchdog.should_interrupt(process_alive=True, now=111))

    def test_elapsed_time_alone_does_not_interrupt_active_turn(self) -> None:
        watchdog = ActivityWatchdog(inactivity_timeout_seconds=10, started_at=0, last_real_activity_at=95)
        self.assertFalse(watchdog.should_interrupt(process_alive=True, now=100))


class ControllerCheckRunnerTests(unittest.TestCase):
    def test_classification_precedence(self) -> None:
        self.assertEqual(
            classify_check_result(returncode=0, timed_out=False, infra_error=False, candidate_changed=True),
            CheckClassification.MUTATED_CANDIDATE,
        )
        self.assertEqual(
            classify_check_result(returncode=None, timed_out=False, infra_error=True, candidate_changed=False),
            CheckClassification.INFRA_ERROR,
        )
        self.assertEqual(
            classify_check_result(returncode=None, timed_out=True, infra_error=False, candidate_changed=False),
            CheckClassification.TIMEOUT,
        )

    def test_runner_detects_candidate_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate.txt"
            candidate.write_text("before", encoding="utf-8")

            def fingerprint() -> str:
                return candidate.read_text(encoding="utf-8")

            runner = ControllerCheckRunner(
                cwd=root,
                base_env={},
                fingerprint=fingerprint,
                execution_enforcement="ADVISORY",
            )
            result = runner.run(
                name="mutation",
                command=[sys.executable, "-c", "from pathlib import Path; Path('candidate.txt').write_text('after')"],
                timeout_seconds=10,
                temp_dir=root / "tmp",
            )
            self.assertEqual(result.classification, CheckClassification.MUTATED_CANDIDATE)

    def test_runner_classifies_timeout_and_infra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = ControllerCheckRunner(
                cwd=root,
                base_env={},
                fingerprint=lambda: "same",
            )
            timeout_result = runner.run(
                name="timeout",
                command=[sys.executable, "-c", "import time; time.sleep(2)"],
                timeout_seconds=0.05,
                temp_dir=root / "tmp1",
            )
            self.assertEqual(timeout_result.classification, CheckClassification.TIMEOUT)
            infra_result = runner.run(
                name="infra",
                command=[str(root / "missing-executable")],
                timeout_seconds=1,
                temp_dir=root / "tmp2",
            )
            self.assertEqual(infra_result.classification, CheckClassification.INFRA_ERROR)


if __name__ == "__main__":
    unittest.main()


class BaselinePolicyTests(unittest.TestCase):
    def test_required_green_and_no_regression_are_distinct(self) -> None:
        from slivin_harness.phase4 import BaselinePolicy, evaluate_baseline_policy
        strict = evaluate_baseline_policy(
            policy=BaselinePolicy.REQUIRED_GREEN,
            baseline_failures=["legacy"],
            candidate_failures=["legacy"],
        )
        self.assertFalse(strict.passed)
        no_regression = evaluate_baseline_policy(
            policy=BaselinePolicy.NO_REGRESSION,
            baseline_failures=["legacy"],
            candidate_failures=["legacy"],
        )
        self.assertTrue(no_regression.passed)
        regressed = evaluate_baseline_policy(
            policy=BaselinePolicy.NO_REGRESSION,
            baseline_failures=["legacy"],
            candidate_failures=["legacy", "new"],
        )
        self.assertFalse(regressed.passed)

    def test_expected_broken_requires_marker_and_green_candidate(self) -> None:
        from slivin_harness.phase4 import BaselinePolicy, evaluate_baseline_policy
        value = evaluate_baseline_policy(
            policy=BaselinePolicy.EXPECTED_BROKEN,
            baseline_failures=["ORACLE_REACHED"],
            candidate_failures=[],
            expected_failure_marker="ORACLE_REACHED",
            candidate_oracle_passed=True,
        )
        self.assertTrue(value.passed)


class ChangedTestCoverageTests(unittest.TestCase):
    def test_changed_test_requires_registry_or_project_gate(self) -> None:
        from slivin_harness.phase4 import CheckReference, Phase4ContractError, ensure_changed_tests_are_covered
        path = "static/js/__tests__/thing.test.cjs"
        with self.assertRaises(Phase4ContractError):
            ensure_changed_tests_are_covered(
                changed_paths=[path],
                registered_references=[],
            )
        ensure_changed_tests_are_covered(
            changed_paths=[path],
            registered_references=[CheckReference("path", path)],
        )
        ensure_changed_tests_are_covered(
            changed_paths=[path],
            registered_references=[],
            project_check_text="jest static/js/__tests__",
        )
