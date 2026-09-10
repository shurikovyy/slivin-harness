"""Independent contract/fault assertions required by release boundary families."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import task_runner
import test_runner_report_recovery as recovery
import test_task_runner_workflow as workflow
from test_protocol import valid_plan, valid_task_contract, proof
from slivin_harness.checkpoint import save_report_checkpoint, verify_checkpoint_evidence
from slivin_harness.implementer import build_implementation_contract
from slivin_harness.protocol import ArtifactContractError
from slivin_harness.proof_routes import apply_proof_review, validate_proof_review, effective_proofs
from slivin_harness.source_records import register_evaluator_findings, source_ref, resolve_assessments
from slivin_harness.task_contract import validate_task_contract
from slivin_harness.verification import compile_verification_plan
from tools.release_mutations import defect_detected
from tools.release_check import default_output_root
from tools.release_real_models import (
    WINDOWS_WORKSPACE_PATH_LIMIT,
    qualification_case_layout,
)


class ReleaseOrchestrationTests(unittest.TestCase):
    def test_default_real_model_layout_preserves_windows_runtime_copy_headroom(self):
        output = default_output_root() / "real_models"
        runtime_relative = Path(
            "@babel/plugin-bugfix-safari-id-destructuring-collision-in-function-expression/lib/index.js.map"
        )
        projected = []
        task_ids = set()
        folders = set()
        for label in ("expiry-1", "suspension-1", "expiry-2"):
            folder, workspace_root, project_name, task_id = qualification_case_layout(output, label)
            folders.add(folder)
            task_ids.add(task_id)
            projected.append(workspace_root / project_name / task_id /
                             "20000101-000000-00000000" / "node_modules" / runtime_relative)
        self.assertEqual(len(folders), 3)
        self.assertEqual(len(task_ids), 3)
        self.assertTrue(all(len(str(path.resolve())) <= WINDOWS_WORKSPACE_PATH_LIMIT
                            for path in projected))


class AdmissionMatrixTests(unittest.TestCase):
    setUp = recovery.ReportRecoveryTests.setUp
    call_reports = recovery.ReportRecoveryTests.call_reports

    def test_release_gate_rejects_marker_files_as_complete_stage_evidence(self):
        from tools.release_real_models import validate_stage_payloads
        self.plane.write_private_json('unrelated-marker.json', {'status':'PASS'})
        names = ('intake_preflight','planner','implementation_contract','implementer',
                 'deterministic_checks','runtime_verification','evaluator')
        acceptance = dict(artifact_bindings=[dict(artifact='unrelated-marker.json', authoritative=True)],
            stage_bindings=[dict(stage=name, state='PASSED', artifacts=['unrelated-marker.json']) for name in names])
        self.assertFalse(validate_stage_payloads(run=self.plane.run_root, acceptance=acceptance,
            handoff={}, candidate_id='candidate', workspace=self.fixture.workspace, node=Path('node')))

    def test_local_leaf_correction_cannot_turn_challenge_into_confirmation(self):
        invalid = copy.deepcopy(self.fixture.report)
        row = invalid['post_patch_impact']['source_assessments'][0]
        row.update(disposition='CHALLENGE', symbols=[])
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, 'CHANGED_CLAIMS'):
            self.call_reports([invalid, self.fixture.report])
        self.assertFalse((self.plane.run_root / 'final_acceptance.json').exists())

    def test_checkpoint_seals_evidence_before_scratch_cleanup_and_detects_tamper(self):
        root = self.fixture.workspace / '.harness_tmp/planner/session-test'
        root.mkdir(parents=True)
        original = root / 'proof.py'
        raw = b'assert 2 + 2 == 4\n'
        original.write_bytes(raw)
        cache = root / 'jest' / ('haste-map-' + 'a' * 40 + '-' + 'b' * 40)
        cache.parent.mkdir()
        cache.write_bytes(b'volatile')
        checkpoint = save_report_checkpoint(plane=self.plane, workspace=self.fixture.workspace,
            name='durable', contract=self.fixture.contract, check_registry_digest='registry')
        row = next(row for row in checkpoint['evidence'] if row['kind'] == 'UNTRUSTED_ROLE_SCRATCH')
        self.assertEqual(row['original_locator'], original.relative_to(self.fixture.workspace).as_posix())
        self.assertIn(dict(locator=cache.relative_to(self.fixture.workspace).as_posix(),
                           reason='REPRODUCIBLE_CACHE_OR_RUNTIME'), checkpoint['evidence_exclusions'])
        original.unlink()
        verify_checkpoint_evidence(self.plane, checkpoint)
        durable = self.plane.private_root / row['artifact']
        self.assertEqual(durable.read_bytes(), raw)
        durable.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'MISSING_OR_CHANGED'):
            verify_checkpoint_evidence(self.plane, checkpoint)
        durable.unlink()
        with self.assertRaisesRegex(RuntimeError, 'MISSING_OR_CHANGED'):
            verify_checkpoint_evidence(self.plane, checkpoint)

    def test_checkpoint_rejects_outside_locator_and_concurrent_candidate_change(self):
        outside = self.fixture.workspace.parent / 'outside-stamp.json'
        outside.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'STAMP_BOUNDARY'):
            save_report_checkpoint(plane=self.plane, workspace=self.fixture.workspace,
                name='outside', contract=self.fixture.contract, stamp_path=outside)
        observe = task_runner.build_candidate_identity
        candidate = observe(self.fixture.workspace)
        other = copy.deepcopy(candidate)
        object.__setattr__(other, 'candidate_id', 'changed')
        with mock.patch('slivin_harness.checkpoint.build_candidate_identity', side_effect=[candidate, other]):
            with self.assertRaisesRegex(RuntimeError, 'CANDIDATE_CHANGED'):
                save_report_checkpoint(plane=self.plane, workspace=self.fixture.workspace,
                    name='race', contract=self.fixture.contract)
        self.assertFalse((self.plane.private_root / 'checkpoints/race.json').exists())

    def test_evaluator_origin_duplicate_is_idempotent_new_revision_needs_assessment(self):
        evaluation = dict(findings=[dict(finding_id='F1', category='CONSUMER', title='Independent reader',
            failure_mode='Reader bypasses the state predicate', required_action='Use current eligibility',
            required_proof=proof('Execute both expired and active cases'), evidence=['reader_b.py caller inspected'])])
        inventory = register_evaluator_findings(self.fixture.contract['source_inventory'], evaluation)
        self.assertEqual(register_evaluator_findings(inventory, evaluation), inventory)
        changed = copy.deepcopy(evaluation)
        changed['findings'][0]['evidence'] = ['Fresh audit finds another invocation']
        current = register_evaluator_findings(inventory, changed)
        self.assertEqual(current['records'][:-1], inventory['records'])
        assessments = [dict(source_ref=source_ref(row), disposition='CONFIRM') for row in inventory['records']]
        with self.assertRaisesRegex(ArtifactContractError, 'Every current source'):
            resolve_assessments(current, assessments, complete=True)
        assessments.append(dict(source_ref=source_ref(current['records'][-1]), disposition='CONFIRM'))
        self.assertEqual(len(resolve_assessments(current, assessments, complete=True)), len(current['records']))


class ProofReviewMatrixTests(unittest.TestCase):
    def setUp(self):
        self.contract = build_implementation_contract(valid_plan(), task_contract=valid_task_contract())
        self.review = dict(protocol_version='proof-route-review.v1', candidate_id='candidate',
            contract_fingerprint=self.contract['fingerprint'], status='READY', reason='Same product behavior, different assertion route',
            evidence=['Original broad suite exercises unrelated labels'], changes=[dict(item_id='PRESERVE-1',
                reason='Use actual independent preservation assertions', evidence=['Both readers asserted'],
                proofs=[proof('Actual fresh and expired assertions for both readers')])])

    def test_current_override_preserves_claims_and_rejects_stale_or_tampered_input(self):
        updated = apply_proof_review(self.contract, self.review, candidate_id='candidate')
        self.assertEqual(updated['items'], self.contract['items'])
        self.assertEqual(updated['source_inventory'], self.contract['source_inventory'])
        old = compile_verification_plan(self.contract, project_checks=[{'name':'owner','command':['git','diff','--check']}])
        new = compile_verification_plan(updated, project_checks=[{'name':'owner','command':['git','diff','--check']}])
        self.assertNotEqual(old['fingerprint'], new['fingerprint'])
        for mutation in ('candidate', 'contract', 'item', 'duplicate', 'empty_proof', 'empty_evidence', 'unknown', 'unchanged', 'history'):
            with self.subTest(mutation=mutation):
                review, contract = copy.deepcopy(self.review), copy.deepcopy(self.contract)
                if mutation == 'candidate': review['candidate_id'] = 'old'
                elif mutation == 'contract': review['contract_fingerprint'] = 'old'
                elif mutation == 'item': review['changes'][0]['item_id'] = 'foreign'
                elif mutation == 'duplicate': review['changes'].append(copy.deepcopy(review['changes'][0]))
                elif mutation == 'empty_proof': review['changes'][0]['proofs'] = []
                elif mutation == 'empty_evidence': review['changes'][0]['evidence'] = []
                elif mutation == 'unknown': review['owner_checks'] = []
                elif mutation == 'unchanged':
                    contract = updated
                    review['contract_fingerprint'] = contract['fingerprint']
                else:
                    contract = copy.deepcopy(updated)
                    contract['proof_routes'][0]['previous_proof_fingerprint'] = 'old'
                    review['contract_fingerprint'] = contract['fingerprint']
                with self.assertRaises(RuntimeError):
                    apply_proof_review(contract, review, candidate_id='candidate')

    def test_non_ready_diagnosis_is_strict_before_reset_or_block(self):
        for status in ('BLOCKED', 'TECHNICAL_REPLAN_REQUIRED'):
            review = dict(self.review, status=status, changes=[])
            validate_proof_review(self.contract, review, candidate_id='candidate')
            for key, value in (('candidate_id','stale'), ('contract_fingerprint','stale'), ('reason',''), ('evidence',[]), ('changes',self.review['changes']), ('status','UNKNOWN')):
                with self.subTest(status=status, key=key), self.assertRaises(RuntimeError):
                    validate_proof_review(self.contract, dict(review, **{key:value}), candidate_id='candidate')

    def test_changed_raw_intent_rejects_previous_task_authority(self):
        previous = valid_task_contract()
        previous['raw_user_request'] = 'Different owner instruction'
        with self.assertRaises(RuntimeError):
            validate_task_contract(previous)

    def test_infrastructure_error_is_not_a_mutation_kill(self):
        for label, target in [('always_stop_valid','run_implementer_report'), ('stale_receipt','verify_self_verify_receipt')]:
            record = dict(tests_run=1, skipped=0, passed=False, target_calls=[target],
                defects=[dict(type='PermissionError', message='[WinError 5] setUp directory denied')])
            self.assertFalse(defect_detected(label, record))


class PersistenceTransitionTests(unittest.TestCase):
    def test_before_and_ambiguous_after_writes_recover_without_duplicate_stage_effects(self):
        from slivin_harness import control_plane
        for timing in ('before', 'after'):
            with self.subTest(timing=timing):
                replace = os.replace
                injected = set()
                prefixes = ('verification_plan_', 'implementation_contract_', 'contract_expansion_',
                            'contract_closure_', 'replan_', 'user_follow_up_report.', 'final_acceptance.', 'delivery_record.')
                def fail_once(source, destination):
                    path = Path(destination)
                    key = str(path)
                    eligible = 'controller_private' in path.parts and path.name.startswith(prefixes)
                    if eligible and key not in injected:
                        injected.add(key)
                        if timing == 'after': replace(source, destination)
                        error = PermissionError('Injected transient sharing violation')
                        error.winerror = 32
                        raise error
                    return replace(source, destination)
                with mock.patch.object(control_plane.os, 'replace', side_effect=fail_once):
                    result, root, output = workflow.TaskRunnerWorkflowIntegrationTests().run_case(
                        benchmark=False, risk='medium', with_discovery=timing == 'before', with_replan=timing == 'after', related=True)
                self.assertEqual(result, 0, output)
                names = {Path(name).name for name in injected}
                for prefix in ('verification_plan_', 'implementation_contract_', 'contract_expansion_' if timing == 'before' else 'replan_', 'user_follow_up_report.', 'final_acceptance.', 'delivery_record.'):
                    self.assertTrue(any(name.startswith(prefix) for name in names), (prefix, names))
                state = json.loads((root / 'run_state.json').read_text(encoding='utf-8'))
                self.assertEqual(state['terminal']['result_code'], 'HARNESS_TASK_PASS')
                self.assertTrue((root / 'final_acceptance.json').is_file())
                self.assertEqual(len(list(root.glob('replan_*_reset.json'))), int(timing == 'after'))


if __name__ == '__main__':
    unittest.main()
