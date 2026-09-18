from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import task_runner
from slivin_harness.protocol import ArtifactContractError, ArtifactFailureKind
from slivin_harness.report_recovery import ReportRecoveryStop
from tools.release_real_models import case_failure_evidence
from tools.replay_real_model_failures import (
    FIXTURE_ROOT,
    replay_qe1,
    replay_qs1,
    replay_qe2,
)


class RealModelFailureReplayTests(unittest.TestCase):
    def test_captured_qe1_qs1_qe2_replays_pass(self):
        results = [replay_qe1(), replay_qs1(), replay_qe2()]
        self.assertEqual([row['status'] for row in results], ['PASS', 'PASS', 'PASS'])
        self.assertEqual([row['fixture_origin'] for row in results], ['captured_real'] * 3)
        self.assertEqual({row['source_qualification_run_id'] for row in results}, {'shr-q-fb852e56ab'})
        self.assertEqual(results[0]['compatible_status_classification'], 'CLAIM_CLOSURE_INCOMPLETE')
        self.assertEqual(results[0]['compatible_status_recovery'], 'CLAIM_CLOSURE_PASS')
        self.assertTrue(all(results[2]['authorities'].values()))

    def test_fixture_bytes_are_bound_to_replay_provenance(self):
        for path in sorted(FIXTURE_ROOT.glob('*.json')):
            value = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(value['origin'], 'captured_real')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            replay = {
                'qe1_negative_closure.json': replay_qe1,
                'qs1_volatile_checkpoint.json': replay_qs1,
                'qe2_mixed_readme.json': replay_qe2,
            }[path.name]()
            self.assertEqual(replay['fixture_sha256'], digest)
            self.assertEqual(replay['source_artifact_sha256'], value['source_artifact_sha256'])

    def test_terminal_failure_taxonomy_is_bounded_and_machine_readable(self):
        closure = ArtifactContractError(
            code='NEGATIVE_WITHOUT_FINDING',
            field='impact_challenge.blind_consumer_dispositions[4].finding_ids',
            message='private model prose is not public authority',
            expected='material finding',
            failure_kind=ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE,
        )
        record = task_runner.terminal_failure_record(closure)
        self.assertEqual(record['failure_class'], 'RECOVERABLE_REPORT_ARTIFACT_FAILURE')
        self.assertEqual(record['reason_code'], 'NEGATIVE_WITHOUT_FINDING')
        self.assertNotIn('message', record)

        exhausted = task_runner.terminal_failure_record(ReportRecoveryStop(
            'EVALUATOR_CLOSURE_NO_PROGRESS',
            failure_kind=ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE,
            field='impact_challenge.blind_consumer_dispositions[4].finding_ids',
            correction_attempt=1,
        ))
        self.assertEqual(exhausted['failure_kind'], 'CLAIM_CLOSURE_INCOMPLETE')
        self.assertEqual(exhausted['correction_attempt'], 1)
        self.assertEqual(exhausted['field'],
                         'impact_challenge.blind_consumer_dispositions[4].finding_ids')

        with tempfile.TemporaryDirectory(prefix='slivin-failure-summary-') as temporary:
            run = Path(temporary)
            (run / 'terminal_failure.json').write_text(json.dumps(record), encoding='utf-8')
            copied = case_failure_evidence(run=run, exit_code=1, independent_validation=None)
            self.assertEqual(copied, record)
            evaluator_failure = {
                'schema_version': 'evaluator-artifact-failure.v1',
                'status': 'INVALID', 'phase': 'PHASE_B', 'correction_attempt': 1,
                'reason_code': 'NEGATIVE_WITHOUT_FINDING',
                'failure_kind': 'CLAIM_CLOSURE_INCOMPLETE',
                'field': 'impact_challenge.blind_consumer_dispositions[4].finding_ids',
                'correctable': True, 'raw_artifact': 'evaluator_01_PHASE_B_01.raw.json',
                'sanitized_artifact': {'status': 'FINDINGS', 'finding_ids': []},
                'private_message': 'must not escape',
            }
            (run / 'evaluator_01_PHASE_B_01.failure.json').write_text(
                json.dumps(evaluator_failure), encoding='utf-8',
            )
            enriched = case_failure_evidence(run=run, exit_code=1, independent_validation=None)
            self.assertEqual(enriched['artifact_failure']['correction_attempt'], 1)
            self.assertNotIn('private_message', enriched['artifact_failure'])
            outer = case_failure_evidence(
                run=run, exit_code=0,
                independent_validation={'status': 'FAIL', 'checks': {'mixed_document': False, 'replay': True}},
            )
            self.assertEqual(outer['failure_class'], 'OUTER_QUALIFICATION_VALIDATOR_FAILURE')
            self.assertEqual(outer['failed_checks'], ['mixed_document'])


if __name__ == '__main__':
    unittest.main()
