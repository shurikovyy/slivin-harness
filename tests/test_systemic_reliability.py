"""Release regressions: progress is an assertion, a controlled stop is not PASS.

External model/receipt services are doubled in these report admission tests.
The production parser, validator, dispatcher and candidate inventory execute.
Source ownership tests use independently authored observations (added alongside
the referenced protocol); legacy fixtures here only establish the before case.
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest import mock
import hashlib

import test_runner_report_recovery as recovery_fixtures
from slivin_harness.implementer import materialize_post_patch_impact, validate_implementation_report
from slivin_harness.source_records import register_observations
from slivin_harness.protocol import ArtifactContractError
import task_runner


class BatchProgressTests(unittest.TestCase):
    setUp = recovery_fixtures.ReportRecoveryTests.setUp
    call_reports = recovery_fixtures.ReportRecoveryTests.call_reports

    def test_same_path_candidate_mutation_is_rejected_during_report_repair(self):
        invalid = copy.deepcopy(self.fixture.report)
        invalid["post_patch_impact"]["source_assessments"][0]["symbols"] = []
        def change_candidate(_options):
            (self.fixture.workspace / "state.py").write_text("def is_current(entry):\n    return True\n", encoding="utf-8")
            return json.dumps(self.fixture.report)
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, "MUTATED_CANDIDATE"):
            self.call_reports([invalid, change_candidate])

    def test_private_checkpoint_preserves_reproducible_candidate_bytes(self):
        self.call_reports([self.fixture.report])
        checkpoints = list((self.plane.private_root / "checkpoints").glob("*.json"))
        self.assertEqual(len(checkpoints), 1)
        checkpoint = json.loads(checkpoints[0].read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["status"], "SAVED_UNVERIFIED")
        self.assertFalse(checkpoint["cross_process_resume"])
        self.assertEqual(checkpoint["candidate"]["changed_paths"], ["state.py"])
        entry = checkpoint["entries"][0]
        raw = (self.plane.private_root / entry["blob"]).read_bytes()
        self.assertEqual(raw, (self.fixture.workspace / "state.py").read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])
        self.assertFalse((self.plane.run_root / "checkpoints").exists())

    def test_checkpoint_write_failure_keeps_raw_and_never_accepts(self):
        write = self.plane.write_private_json
        def fail_checkpoint(name, value):
            if name.startswith("checkpoints/"):
                raise OSError("Injected checkpoint persistence failure")
            return write(name, value)
        before = (self.fixture.workspace / "state.py").read_bytes()
        with mock.patch.object(self.plane, "write_private_json", side_effect=fail_checkpoint):
            with self.assertRaisesRegex(OSError, "checkpoint persistence"):
                self.call_reports([self.fixture.report])
        self.assertEqual((self.fixture.workspace / "state.py").read_bytes(), before)
        self.assertEqual(len(list(self.plane.private_root.glob("*.raw.json"))), 1)
        self.assertFalse((self.plane.private_root / "final_acceptance.json").exists())

    def test_unknown_transport_and_timeout_before_raw_preserve_actual_candidate(self):
        for kind in ("transport", "timeout"):
            with self.subTest(kind=kind):
                def failed_turn(*_args, **_kwargs):
                    (self.fixture.workspace / "reader_b.py").write_text("changed before " + kind + "\n", encoding="utf-8")
                    if kind == "timeout":
                        raise task_runner.TurnTimeoutError("Injected timeout")
                    raise RuntimeError("Injected transport outcome unknown")
                with mock.patch.object(task_runner, "run_agent_turn", side_effect=failed_turn) as turns:
                    with self.assertRaises((RuntimeError, task_runner.TurnTimeoutError)):
                        task_runner.run_implementer_report(mock.Mock(), thread_id="same", prompt="Implement",
                            timeout=30, label="FAULT", implementation_contract=self.fixture.contract,
                            self_verify_command=["self"], workspace=self.fixture.workspace,
                            stamp_path=self.fixture.workspace / ".harness_tmp/stamp.json", plan=self.fixture.plan,
                            control_plane=self.plane)
                self.assertEqual(turns.call_count, 2 if kind == "timeout" else 1)
                current = json.loads((self.plane.run_root / "candidate_identity_current.json").read_text(encoding="utf-8"))
                self.assertIn("reader_b.py", current["changed_paths"])
                self.assertTrue((self.plane.run_root / "terminal_candidate_observation.json").exists())
                self.assertTrue(list((self.plane.private_root / "checkpoints").glob("*_terminal.json")))

    def test_independent_errors_recover_in_one_batch(self):
        for count in (3, 5, 25):
            with self.subTest(count=count):
                valid = copy.deepcopy(self.fixture.report)
                valid["post_patch_impact"]["related_out_of_scope"] = [
                    *valid["post_patch_impact"]["related_out_of_scope"],
                    *[{
                        "observation_id": f"navigation-{i}",
                        "name": f"Navigation issue {i}", "paths": ["README.md"],
                        "symbols": ["README.md#notes"], "relation": "Project documentation",
                        "reason": "The heading navigation does not influence entry validity.",
                        "evidence": ["README.md has a Notes heading."],
                        "suggested_follow_up": f"Review navigation link {i}.",
                    } for i in range(count)],
                ]
                invalid = copy.deepcopy(valid)
                for row in invalid["post_patch_impact"]["related_out_of_scope"][-count:]:
                    row["symbols"] = []

                def corrected(options):
                    payload = json.loads(options["prompt"].splitlines()[-1])
                    self.assertEqual(len(payload["allowed_fields"]), count)
                    return json.dumps(valid)

                self.assertEqual(self.call_reports([invalid, corrected]), valid)
                self.assertEqual(self.turns, 2)


class SourceOwnershipTests(unittest.TestCase):
    setUp = recovery_fixtures.ReportRecoveryTests.setUp

    def validate(self, report):
        f = self.fixture
        validate_implementation_report(report, contract=f.contract, changed_paths=["state.py"],
                                       workspace=f.workspace, plan=f.plan, self_verification_ok=True)

    def test_independent_observations_preserve_origins_without_copying_text(self):
        report = self.fixture.report
        wire = report["post_patch_impact"]
        for group in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope"):
            self.assertEqual(wire[group], [])
        for index, assessment in enumerate(wire["source_assessments"]):
            assessment["observation"] = f"Проверка № {index}: текущие readers исследованы самостоятельно."
            assessment["evidence"] = ["Inspected the final definitions and followed their actual callers."]
        wire["source_assessments"].reverse()
        self.validate(report)
        canonical = materialize_post_patch_impact(report, contract=self.fixture.contract, plan=self.fixture.plan)
        self.assertEqual(canonical["related_out_of_scope"][0]["reason"],
                         "Zero division is independent of expiration and does not affect either reader.")
        self.assertEqual(len(canonical["in_scope_consumers"]), 2)

    def test_missing_duplicate_unknown_and_stale_origins_rejected(self):
        for mutation, code in (
            (lambda rows: rows.pop(), "SOURCE_ASSESSMENT_MISSING"),
            (lambda rows: rows.append(copy.deepcopy(rows[0])), "SOURCE_ASSESSMENT_DUPLICATE"),
            (lambda rows: rows[0]["source_ref"].update(source_id="foreign-run"), "SOURCE_REFERENCE_UNKNOWN"),
            (lambda rows: rows[0]["source_ref"].update(source_revision="0" * 64), "SOURCE_REVISION_STALE"),
        ):
            with self.subTest(code=code):
                report = copy.deepcopy(self.fixture.report)
                mutation(report["post_patch_impact"]["source_assessments"])
                with self.assertRaises(ArtifactContractError) as failure:
                    self.validate(report)
                self.assertEqual(failure.exception.code, code)

    def test_challenge_is_not_confirmation(self):
        report = self.fixture.report
        report["post_patch_impact"]["source_assessments"][0]["disposition"] = "CHALLENGE"
        with self.assertRaises(ArtifactContractError) as failure:
            self.validate(report)
        self.assertEqual(failure.exception.code, "SOURCE_MODEL_CHALLENGE")

    def test_discovery_idempotency_and_conflicting_payload(self):
        inventory = self.fixture.contract["source_inventory"]
        observations = {"related_out_of_scope": [{
            "observation_id": "documentation-navigation", "name": "Documentation navigation",
            "paths": ["README.md"], "symbols": ["README.md#notes"],
            "relation": "Repository documentation", "reason": "The navigation is independent of entry reads.",
            "evidence": ["README.md Notes heading exists."],
            "suggested_follow_up": "Link the Notes heading from the index.",
        }]}
        expanded, ids = register_observations(inventory, observations)
        self.assertEqual(len(ids), 1)
        repeated, again = register_observations(expanded, observations)
        self.assertEqual(repeated, expanded)
        self.assertEqual(again, [])
        self.assertEqual(expanded["records"][:len(inventory["records"])], inventory["records"])
        observations["related_out_of_scope"][0]["reason"] = "A materially different conclusion."
        with self.assertRaises(ArtifactContractError) as failure:
            register_observations(expanded, observations)
        self.assertEqual(failure.exception.code, "OBSERVATION_ID_CONFLICT")

    def test_variable_source_collections_and_order_preserve_exact_coverage(self):
        from slivin_harness.source_records import build_source_inventory, source_ref, resolve_assessments
        for count in (0, 1, 3, 5, 25):
            with self.subTest(count=count):
                inventory = build_source_inventory(None, task_fingerprint="collections")
                inventory, _ = register_observations(inventory, {"related_out_of_scope": [dict(
                    observation_id=f"entry-{i}", name=f"Наблюдение № {i}", paths=["README.md"], symbols=["notes"],
                    relation="Repository documentation", reason=f"Independent documentation concern {i}",
                    evidence=[f"Observed heading № {i}"], suggested_follow_up=f"Clarify heading {i}.",
                ) for i in range(count)]})
                assessments = [dict(source_ref=source_ref(row), disposition="CONFIRM") for row in inventory["records"]]
                result = resolve_assessments(inventory, list(reversed(assessments)), complete=True)
                self.assertEqual({row[0]["claim"]["name"] for row in result}, {f"Наблюдение № {i}" for i in range(count)})
                if count:
                    with self.assertRaises(ArtifactContractError):
                        resolve_assessments(inventory, assessments[:-1], complete=True)


if __name__ == "__main__":
    unittest.main()
