"""Captured QS1 admission: a repaired leaf must not strand a technical conflict."""
from __future__ import annotations

import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import task_runner
from slivin_harness.implementer import validate_implementation_report
from slivin_harness.protocol import ArtifactContractError, ArtifactFailureKind
from slivin_harness.run_state import build_candidate_identity
from tools.release_real_models import case_failure_evidence
from test_implementer import git

FIXTURE = Path(__file__).parent / 'fixtures/implementer_conflict/qs1_implementer_model_conflict.json'


class CapturedQS1RoutingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        tmp = tempfile.TemporaryDirectory(prefix='qs1-admission-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        for rel, contents in self.fixture['source_files'].items():
            target = self.workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding='utf-8')
        git(self.workspace, 'init')
        git(self.workspace, 'config', 'user.name', 'Test')
        git(self.workspace, 'config', 'user.email', 'test@example.invalid')
        git(self.workspace, 'add', '-A')
        git(self.workspace, 'commit', '-m', 'Synthetic qualification baseline')
        for rel, contents in self.fixture['candidate_files'].items():
            (self.workspace / rel).write_text(contents, encoding='utf-8')
        self.plane = task_runner.ControllerPlane(self.root / 'run')

    def run_reports(self, reports, *, label='REPAIR EVALUATION #1'):
        responses = [json.dumps(r) for r in reports]
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
            task_runner, 'run_agent_turn', side_effect=responses
        ) as turn, mock.patch.object(task_runner, 'verify_self_verification_stamp') as receipt:
            try:
                return task_runner.run_implementer_report(
                    mock.Mock(), thread_id='captured-thread', prompt='Repair', timeout=30,
                    label=label, implementation_contract=self.fixture['contract'],
                    self_verify_command=['self'], workspace=self.workspace,
                    stamp_path=self.workspace / '.harness_tmp/stamp.json',
                    plan=self.fixture['plan'], control_plane=self.plane,
                )
            finally:
                self.turn_count = turn.call_count
                self.receipt_count = receipt.call_count

    def test_captured_leaf_correction_routes_conflict_without_third_model_turn(self):
        original, corrected = self.fixture['original'], self.fixture['corrected']
        before = build_candidate_identity(self.workspace)
        result = self.run_reports([original, corrected])
        self.assertEqual(result['status'], 'REPLAN_REQUIRED')
        self.assertEqual(result['terminal_reason_kind'], 'TECHNICAL_MODEL_DIVERGENCE')
        self.assertEqual(result['post_patch_impact'], corrected['post_patch_impact'])
        for key in set(corrected) - {'status', 'terminal_reason_kind', 'reason', 'evidence'}:
            self.assertEqual(result[key], corrected[key], key)
        self.assertEqual(self.turn_count, 2)
        self.assertEqual(self.receipt_count, 0, 'A rejected candidate cannot earn a receipt')
        self.assertEqual(before, build_candidate_identity(self.workspace))
        self.assertFalse((self.plane.run_root / 'final_acceptance.json').exists())
        self.assertEqual(len(list(self.plane.private_root.glob('*.raw.json'))), 2)
        self.assertTrue(list(self.plane.private_root.glob('checkpoints/*.json')))
        routes = [json.loads(p.read_text()) for p in self.plane.run_root.glob('*replan*.json')]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['reason_code'], 'POST_PATCH_MODEL_DIVERGENCE')
        validate_implementation_report(result, contract=self.fixture['contract'],
            changed_paths=list(before.changed_paths), workspace=self.workspace,
            plan=self.fixture['plan'], self_verification_ok=False)

    def test_shared_admission_routes_initial_continuation_and_all_repairs(self):
        for label in ('IMPLEMENT #1', 'IMPLEMENT CONTRACT EXPANSION',
                      'REPAIR CHECKS #1', 'REPAIR RUNTIME #1', 'REPAIR EVALUATION #1'):
            with self.subTest(label=label):
                report = self.run_reports([self.fixture['original'], self.fixture['corrected']], label=label)
                self.assertEqual(report['status'], 'REPLAN_REQUIRED')
                self.assertEqual(self.turn_count, 2)
                self.assertEqual(self.receipt_count, 0)

    def test_original_complete_remains_invalid_in_full_validator(self):
        with self.assertRaises(ArtifactContractError) as failure:
            validate_implementation_report(self.fixture['corrected'], contract=self.fixture['contract'],
                changed_paths=list(build_candidate_identity(self.workspace).changed_paths),
                workspace=self.workspace, plan=self.fixture['plan'], self_verification_ok=True)
        self.assertEqual(failure.exception.code, 'POST_PATCH_MODEL_DIVERGENCE')

    def test_unrelated_semantic_error_cannot_become_replan(self):
        report = copy.deepcopy(self.fixture['corrected'])
        report['post_patch_impact']['source_assessments'][0]['source_ref']['source_revision'] = 'stale'
        with self.assertRaises(task_runner.HarnessControlledStop):
            self.run_reports([report])
        self.assertEqual(self.turn_count, 1)
        self.assertEqual(self.receipt_count, 0)

    def test_invalid_new_observation_is_not_hidden_by_replan(self):
        report = copy.deepcopy(self.fixture['corrected'])
        row = report['post_patch_impact']['changed_contracts'][0]
        row['after'] = row['before']
        with self.assertRaises(task_runner.HarnessControlledStop):
            self.run_reports([report])
        self.assertEqual(self.turn_count, 1)
        self.assertEqual(self.receipt_count, 0)

    def test_claim_mutation_during_leaf_correction_remains_terminal(self):
        corrected = copy.deepcopy(self.fixture['corrected'])
        corrected['post_patch_impact']['changed_contracts'][0]['after'] = 'A different business intent'
        with self.assertRaisesRegex(task_runner.HarnessControlledStop, 'CHANGED_CLAIMS'):
            self.run_reports([self.fixture['original'], corrected])
        self.assertEqual(self.turn_count, 2)
        self.assertEqual(self.receipt_count, 0)


class TerminalDiagnosticAuthorityTests(unittest.TestCase):
    def test_old_evaluator_failure_is_not_a_terminal_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'evaluator_01_PHASE_A_00.failure.json').write_text(json.dumps({
                'status':'INVALID', 'phase':'PHASE_A', 'reason_code':'IMPACT_EVIDENCE_EMPTY',
                'field':'impact_analysis.related_out_of_scope[1].symbols', 'correctable':True,
            }))
            result = case_failure_evidence(run=root, exit_code=2, independent_validation=None)
            self.assertEqual(result['reason_code'], 'TASK_RUNNER_FAILED_WITHOUT_TYPED_PUBLIC_DETAIL')
            self.assertEqual(result['historical_artifact_failure']['reason_code'], 'IMPACT_EVIDENCE_EMPTY')

    def test_controlled_stop_preserves_causal_code_attempt_and_phase(self):
        diagnostic = ArtifactContractError(code='SOURCE_REVISION_STALE', field='source_assessments',
            message='Private detail must not be published', expected='Current revision',
            failure_kind=ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT)
        error = task_runner.HarnessControlledStop('IMPLEMENTER_REPORT_INVALID',
            diagnostic=diagnostic, phase='REPAIR EVALUATION #1', correction_attempt=1)
        record = task_runner.terminal_failure_record(error)
        self.assertEqual(record['reason_code'], 'SOURCE_REVISION_STALE')
        self.assertEqual(record['stop_reason_code'], 'IMPLEMENTER_REPORT_INVALID')
        self.assertEqual(record['phase'], 'REPAIR EVALUATION #1')
        self.assertEqual(record['correction_attempt'], 1)
        self.assertEqual(record['failure_kind'], 'SEMANTIC_MODEL_CONFLICT')
        self.assertNotIn('Private detail', json.dumps(record))


class ModelConflictWorkflowTests(unittest.TestCase):
    def test_detected_conflict_runs_clean_replan_then_accepts_only_fresh_candidate(self):
        from test_task_runner_workflow import TaskRunnerWorkflowIntegrationTests
        fixture = TaskRunnerWorkflowIntegrationTests()
        code, run, output = fixture.run_case(
            benchmark=False, risk='medium', detected_model_conflict=True,
        )
        self.assertEqual(code, 0, output)
        self.assertIn('IMPLEMENTER_MODEL_CONFLICT_REPLAN', output)
        self.assertIn('=== REPLAN #1 ===', output)
        reset = json.loads((run / 'replan_01_reset.json').read_text())
        self.assertEqual(reset['status'], 'SEMANTIC_REPLAN_RESET_PASS')
        self.assertTrue((run / 'replan_01_rejected_candidate.patch').is_file())
        self.assertTrue((run / 'final_acceptance.json').is_file())
        state = json.loads((run / 'run_state.json').read_text())
        self.assertEqual(state['attempt_id'], 2)
        self.assertEqual(state['terminal']['result_code'], 'HARNESS_TASK_PASS')
        first = json.loads((run / 'implementation_report_01.json').read_text())
        self.assertEqual(first['status'], 'REPLAN_REQUIRED')
        self.assertTrue(first['post_patch_impact']['changed_contracts'])
        self.assertEqual(len(list(run.glob('task_contract_*.json'))), 1)

    def test_main_controlled_exit_persists_current_cause_instead_of_old_evaluator(self):
        from test_task_runner_workflow import TaskRunnerWorkflowIntegrationTests
        diagnostic = ArtifactContractError(code='SOURCE_REVISION_STALE', field='source_assessments',
            message='Private source values', expected='Current reference',
            failure_kind=ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT)
        stop = task_runner.HarnessControlledStop('IMPLEMENTER_REPORT_INVALID', diagnostic=diagnostic,
            phase='REPAIR EVALUATION #1', correction_attempt=1)
        code, run, output = TaskRunnerWorkflowIntegrationTests().run_case(
            benchmark=False, risk='medium', planner_exception=stop,
        )
        self.assertEqual(code, 2, output)
        record = json.loads((run / 'terminal_failure.json').read_text())
        self.assertEqual(record['reason_code'], 'SOURCE_REVISION_STALE')
        self.assertEqual(record['correction_attempt'], 1)
        self.assertTrue((run / 'controller_private/terminal_failure.json').is_file())
        (run / 'evaluator_01_PHASE_A_00.failure.json').write_text(json.dumps({
            'reason_code':'IMPACT_EVIDENCE_EMPTY', 'correctable':True,
        }))
        summary = case_failure_evidence(run=run, exit_code=code, independent_validation=None)
        self.assertEqual(summary['reason_code'], 'SOURCE_REVISION_STALE')
        self.assertEqual(summary['stop_reason_code'], 'IMPLEMENTER_REPORT_INVALID')
        self.assertEqual(summary['phase'], 'REPAIR EVALUATION #1')
        self.assertNotIn('Private source', json.dumps(summary))


if __name__ == '__main__':
    unittest.main()
