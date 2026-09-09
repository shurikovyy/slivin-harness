"""Finite state-space sampling over production state, source, registry and receipt APIs.

Fixed before execution: seeds 1729/2718, 100 sequences each, length 1..12.
Expectations are recorded from requested actions, not copied from current outputs.
Failing sequences are minimized by replay and retained outside Git.
"""
from __future__ import annotations

import copy
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from slivin_harness.control_plane import ControllerPlane, SelfVerifyBinding, _atomic_write
from slivin_harness.phase4 import CheckRegistry
from slivin_harness.run_state import RunState, build_candidate_identity
from slivin_harness.source_records import build_source_inventory, register_observations, source_ref, resolve_assessments
from slivin_harness.workflow import WorkflowMode, PipelineProfile, RevisionKind, InvalidationTrigger, StageId
import test_run_state as state_fixtures


class StatefulReliabilityTests(unittest.TestCase):
    def setUp(self):
        fixture = state_fixtures.CandidateIdentityTests()
        self.repo = fixture.make_repo()
        self.addCleanup(fixture.doCleanups)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="shr-state-")))

    def replay(self, sequence):
        case = self.root / str(len(list(self.root.iterdir())))
        plane = ControllerPlane(case)
        state = RunState.create(path=plane.private_root / "run_state.json", task_id="STATEFUL",
                               harness_version="test", workflow_version="test", mode=WorkflowMode.PRODUCTION,
                               pipeline_profile=PipelineProfile.FULL)
        state_fixtures.RunStateTests().advance_to_contract(state)
        registry = CheckRegistry(plane.private_root / "check_registry.json", workspace=self.repo, trusted_check_ids={"project:one", "project:two"})
        inventory = build_source_inventory(None, task_fingerprint="stateful")
        expected_ids, registered = set(), set()
        revisions = dict(task_contract=1, plan=1, implementation_contract=1, verification_plan=1, runtime_environment=None, candidate=1)
        candidate_generation = 0
        state.observe_candidate(build_candidate_identity(self.repo), reason_code="INITIAL")
        saved = None
        for action in sequence:
            if action == "candidate":
                candidate_generation += 1
                (self.repo / "a.txt").write_text(f"candidate {candidate_generation}\n", encoding="utf-8")
                state.observe_candidate(build_candidate_identity(self.repo), reason_code="CHANGE")
                state.invalidate(InvalidationTrigger.CANDIDATE_CHANGED)
                revisions["candidate"] += 1
            elif action in {"runtime", "proof"}:
                key = "runtime_environment" if action == "runtime" else "verification_plan"
                revisions[key] = (revisions[key] or 0) + 1
                state.bump_revision(RevisionKind(key))
                state.invalidate(InvalidationTrigger.RUNTIME_ENV_CHANGED if action == "runtime" else InvalidationTrigger.PROOF_ROUTE_CHANGED)
            elif action.startswith("discover"):
                local = action[-1]
                old = copy.deepcopy(inventory)
                inventory, added = register_observations(inventory, {"new_risks": [dict(observation_id="risk" + local,
                    name="Risk " + local, reason="Independent branch observed.", failure_mode="Branch rejects valid state.",
                    paths=["a.txt"], symbols=["state"], evidence=["Observed branch " + local],
                    required_proof=dict(claim="Exercise valid branch", level="LOCAL_DETERMINISTIC", capabilities=[]))]})
                self.assertEqual(bool(added), local not in expected_ids)
                if local in expected_ids:
                    self.assertEqual(inventory, old)
                expected_ids.add(local)
                if added:
                    state.bump_revision(RevisionKind.IMPLEMENTATION_CONTRACT)
                    state.invalidate(InvalidationTrigger.CONTRACT_EXPANDED)
                    revisions["implementation_contract"] += 1
            elif action.startswith("check"):
                key = "project:" + ("one" if action.endswith("1") else "two")
                before = registry.digest()
                registry.register_id(key)
                self.assertEqual(registry.digest() != before, key not in registered)
                registered.add(key)
            elif action == "receipt":
                binding = SelfVerifyBinding(**state.verification_binding(candidate_id=state.data["current_candidate"]["candidate_id"], check_registry_digest=registry.digest()))
                plane.issue_self_verify_receipt(binding=binding, claim={"assertions": "synthetic proof"})
                saved = (dict(revisions), frozenset(registered), candidate_generation)
            elif action == "duplicate_observation":
                state.observe_candidate(build_candidate_identity(self.repo), reason_code="DUPLICATE")
            self.assertEqual(state.data["revisions"], revisions)
            self.assertEqual(len(inventory["records"]), len(expected_ids))
            assessments = [dict(source_ref=source_ref(row), disposition="CONFIRM") for row in inventory["records"]]
            self.assertEqual(len(resolve_assessments(inventory, list(reversed(assessments)), complete=True)), len(expected_ids))
            binding = SelfVerifyBinding(**state.verification_binding(candidate_id=state.data["current_candidate"]["candidate_id"], check_registry_digest=registry.digest()))
            expected_current = saved == (revisions, frozenset(registered), candidate_generation)
            self.assertEqual(plane.verify_self_verify_receipt(binding=binding), expected_current)
            self.assertIsNone(state.data["terminal"])
            self.assertNotEqual(state.data["stages"][StageId.FINAL_GATE.value]["state"], "PASSED")

    def test_fixed_stateful_sequences(self):
        actions = ("candidate", "runtime", "proof", "discover1", "discover2", "check1", "check2", "receipt", "duplicate_observation")
        for seed in (1729, 2718):
            randomizer = random.Random(seed)
            for index in range(100):
                # Force a known starting candidate per replay, independent of earlier sequences.
                sequence = [randomizer.choice(actions) for _ in range(randomizer.randint(1, 12))]
                try:
                    (self.repo / "a.txt").write_text("initial sequence\n", encoding="utf-8")
                    self.replay(sequence)
                except AssertionError:
                    minimal = sequence[:]
                    for position in range(len(minimal) - 1, -1, -1):
                        candidate = minimal[:position] + minimal[position + 1:]
                        try:
                            (self.repo / "a.txt").write_text("initial sequence\n", encoding="utf-8")
                            self.replay(candidate)
                        except AssertionError:
                            minimal = candidate
                    output = Path(__file__).resolve().parents[1] / ".harness_tmp/systemic-reliability/stateful-counterexample.json"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(dict(seed=seed, index=index, sequence=sequence, minimal=minimal), indent=2), encoding="utf-8")
                    raise


class PersistenceFaultTests(unittest.TestCase):
    def test_ambiguous_committed_replace_is_reconciled_without_second_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            replace = os.replace
            def committed_then_error(source, target):
                replace(source, target)
                raise PermissionError("Injected ambiguous completion")
            with mock.patch("slivin_harness.control_plane.os.replace", side_effect=committed_then_error) as operation:
                _atomic_write(path, b'{"state":"known"}')
            self.assertEqual(operation.call_count, 1)
            self.assertEqual(path.read_bytes(), b'{"state":"known"}')
            self.assertEqual(list(Path(folder).glob("*.tmp-*")), [])

    def test_write_failure_before_replace_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_bytes(b"previous")
            with mock.patch("slivin_harness.control_plane.os.replace", side_effect=OSError("Injected persistence fault")):
                with self.assertRaises(OSError):
                    _atomic_write(path, b"next")
            self.assertEqual(path.read_bytes(), b"previous")
