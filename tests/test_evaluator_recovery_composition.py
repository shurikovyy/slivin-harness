"""Admission regressions: fixed claims, one closure, two total correction turns.

Router tests compile the actual production nested admission function, with a
scripted model and deliberately ordered validator diagnostics. They are not LLM
or product-correctness tests. Closure semantics use production state/validators.
"""
from __future__ import annotations

import ast
import copy
import inspect
import json
import textwrap
import unittest

from slivin_harness import evaluator
from slivin_harness.protocol import ArtifactContractError, ArtifactDiagnosticBatch, ArtifactFailureKind
from slivin_harness.report_recovery import (
    EvaluatorClosureCorrectionState, ReportRecoveryStop,
    correction_fields, preserves_evaluator_closure,
)

GROUP = 'planner_consumer_dispositions'
ID_FIELD = f'impact_challenge.{GROUP}[0].finding_ids'
EVIDENCE_FIELD = f'impact_challenge.{GROUP}[0].evidence'


def report(*, status='FINDINGS', finding_ids=(), evidence=('reader.js',), findings=()):
    return {
        'protocol_version': 'evaluator.v8', 'candidate_id': 'candidate',
        'status': status, 'reason': 'The declared reader contract needs correction.',
        'summary': 'The reader must preserve the declared behavior.',
        'findings': copy.deepcopy(list(findings)),
        'impact_challenge': {
            'blind_contract_dispositions': [],
            GROUP: [{'disposition': 'IMPLEMENTATION_GAP',
                     'origin_ref': 'origin-reader', 'reason': 'Existing reader mismatch.',
                     'evidence': list(evidence), 'finding_ids': list(finding_ids)}],
        },
    }


def finding(identifier='F1'):
    # The shape is sufficient for closure bookkeeping. Full finding validation
    # remains covered by the existing evaluator admission/integration suites.
    return {'finding_id': identifier, 'claim': 'Existing reader mismatch.',
            'evidence': ['reader.js']}


def closure_error(value):
    try:
        evaluator.detect_evaluator_claim_closure(value)
    except ArtifactContractError as error:
        return error
    raise AssertionError('Fixture must require closure')


class EvaluatorClosureConservationTests(unittest.TestCase):
    def test_status_only_does_not_require_an_invented_finding(self):
        original = report(status='PASS', finding_ids=('F1',), findings=(finding(),))
        state = EvaluatorClosureCorrectionState()
        state.begin(original, closure_error(original))
        corrected = copy.deepcopy(original)
        corrected['status'] = 'FINDINGS'
        state.observe_corrected(corrected)
        self.assertEqual(corrected['findings'], original['findings'])

    def test_status_only_contract_conflict_still_requires_replan(self):
        original = report(status='PASS', finding_ids=('F1',), findings=(finding(),))
        original['impact_challenge']['blind_contract_dispositions'] = [{
            'disposition': 'MODEL_CONFLICT', 'finding_ids': ['F1'], 'reason': 'Contract mismatch.'}]
        state = EvaluatorClosureCorrectionState()
        state.begin(original, closure_error(original))
        corrected = copy.deepcopy(original)
        corrected['status'] = 'REPLAN_REQUIRED'
        state.observe_corrected(corrected)
        self.assertEqual(state.expected_status, 'REPLAN_REQUIRED')

    def test_missing_binding_can_use_an_existing_finding_without_duplication(self):
        original = report(findings=(finding(),))
        state = EvaluatorClosureCorrectionState()
        state.begin(original, closure_error(original))
        corrected = copy.deepcopy(original)
        corrected['impact_challenge'][GROUP][0]['finding_ids'] = ['F1']
        state.observe_corrected(corrected)
        self.assertEqual(len(corrected['findings']), 1)

    def test_negative_claim_cannot_be_changed_to_positive(self):
        original = report(status='PASS', finding_ids=('F1',), findings=(finding(),))
        state = EvaluatorClosureCorrectionState()
        state.begin(original, closure_error(original))
        corrected = copy.deepcopy(original)
        corrected['status'] = 'FINDINGS'
        corrected['impact_challenge'][GROUP][0]['disposition'] = 'CONFIRMED'
        with self.assertRaises(ReportRecoveryStop):
            state.observe_corrected(corrected)

    def test_wrong_derived_status_is_rejected(self):
        original = report(status='PASS', finding_ids=('F1',), findings=(finding(),))
        state = EvaluatorClosureCorrectionState()
        state.begin(original, closure_error(original))
        corrected = copy.deepcopy(original)
        corrected['status'] = 'REPLAN_REQUIRED'
        with self.assertRaises(ReportRecoveryStop):
            state.observe_corrected(corrected)

    def test_existing_finding_and_candidate_are_immutable(self):
        original = report(findings=(finding(),))
        for mutation in ('candidate_id', 'finding'):
            with self.subTest(mutation=mutation):
                state = EvaluatorClosureCorrectionState()
                state.begin(original, closure_error(original))
                corrected = copy.deepcopy(original)
                corrected['impact_challenge'][GROUP][0]['finding_ids'] = ['F1']
                if mutation == 'finding':
                    corrected['findings'][0]['claim'] = 'Unrelated weaker claim.'
                else:
                    corrected['candidate_id'] = 'another-candidate'
                with self.assertRaises(ReportRecoveryStop):
                    state.observe_corrected(corrected)

    def test_unreferenced_appended_finding_is_rejected(self):
        original = report(status='PASS', finding_ids=('F1',), findings=(finding(),))
        corrected = copy.deepcopy(original)
        corrected['status'] = 'FINDINGS'
        corrected['findings'].append(finding('F2'))
        self.assertFalse(preserves_evaluator_closure(
            original, corrected, fields=['status', 'findings']))

    def test_invalid_or_duplicate_final_ids_do_not_raise_or_pass(self):
        original = report()
        for bad_id in ([], {}, None, '', 'F1'):
            with self.subTest(bad_id=bad_id):
                corrected = copy.deepcopy(original)
                corrected['findings'] = [finding(), finding(bad_id)]
                corrected['impact_challenge'][GROUP][0]['finding_ids'] = ['F1']
                self.assertFalse(preserves_evaluator_closure(
                    original, corrected, fields=[ID_FIELD, 'findings']))

    def test_nonlocal_diagnostic_cannot_be_reclassified_by_missing_field_split(self):
        for kind in (ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
                     ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT):
            with self.subTest(kind=kind):
                error = ArtifactContractError(
                    code='MISSING_FIELDS', field='impact_analysis.related_out_of_scope[0]',
                    message='Nonlocal diagnostic', expected='Validated current state',
                    actual=['symbols', 'evidence'], failure_kind=kind)
                self.assertIsNone(correction_fields(error))


class EvaluatorCorrectionCompositionTests(unittest.TestCase):
    def run_admission(self, reports, validator, phase='PHASE_B'):
        """Execute the production local function, not a copied state machine."""
        source = textwrap.dedent(inspect.getsource(inspect.unwrap(evaluator.run_evaluator)))
        tree = ast.parse(source)
        node = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == 'admit_phase')
        module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        received = []
        raw_records = []
        guards = []
        failures = []
        iterator = iter(reports)

        class Model:
            def run_turn(self, **kwargs):
                received.append(kwargs)
                return json.dumps(next(iterator))

        namespace = dict(vars(evaluator), codex=Model(), thread_id='same-evaluator-thread',
                         explicit_skills=None, on_heartbeat=None, timeout=900,
                         on_raw_report=lambda *args: raw_records.append(args),
                         on_phase_complete=lambda name: guards.append(name),
                         on_artifact_failure=lambda *args: failures.append(args))
        exec(compile(module, evaluator.__file__, 'exec'), namespace)
        self.observed_calls = received
        self.observed_guards = guards
        self.observed_failures = failures
        self.observed_raw = raw_records
        return namespace['admit_phase'](phase, 'initial', {}, validator)

    @staticmethod
    def require_evidence(value):
        row = value['impact_challenge'][GROUP][0]
        if not row['evidence']:
            raise ArtifactContractError(
                code='IMPACT_EVIDENCE_EMPTY', field=EVIDENCE_FIELD,
                message='Existing reader evidence is required', expected='Nonempty evidence', actual=[])

    def test_local_then_closure_within_the_original_total_budget(self):
        initial = report(evidence=())
        local = copy.deepcopy(initial)
        local['impact_challenge'][GROUP][0]['evidence'] = ['reader.js']
        closed = copy.deepcopy(local)
        closed['findings'] = [finding()]
        closed['impact_challenge'][GROUP][0]['finding_ids'] = ['F1']

        def validate(value):
            self.require_evidence(value)
            evaluator.detect_evaluator_claim_closure(value)

        result = self.run_admission([initial, local, closed], validate)
        self.assertEqual(result, closed)
        self.assertEqual(len(self.observed_calls), 3)
        self.assertEqual(len(self.observed_guards), 3)
        self.assertEqual(len(self.observed_raw), 3)
        self.assertEqual({call['thread_id'] for call in self.observed_calls}, {'same-evaluator-thread'})

    def test_closure_then_an_existing_local_correction_route_is_composable(self):
        initial = report(evidence=())
        closed = copy.deepcopy(initial)
        closed['findings'] = [finding()]
        closed['impact_challenge'][GROUP][0]['finding_ids'] = ['F1']
        local = copy.deepcopy(closed)
        local['impact_challenge'][GROUP][0]['evidence'] = ['reader.js']

        def validate(value):
            # Exercise the router independently of which validator first exposes
            # an error: a later check may expose a previously masked local error.
            evaluator.detect_evaluator_claim_closure(value)
            self.require_evidence(value)

        self.assertEqual(self.run_admission([initial, closed, local], validate), local)
        self.assertEqual(len(self.observed_calls), 3)

    def test_corrected_local_fields_cannot_be_reverted_during_closure(self):
        initial = report(evidence=())
        local = copy.deepcopy(initial)
        local['impact_challenge'][GROUP][0]['evidence'] = ['reader.js']
        malicious = copy.deepcopy(local)
        malicious['findings'] = [finding()]
        malicious['impact_challenge'][GROUP][0].update(finding_ids=['F1'], evidence=[])

        def validate(value):
            self.require_evidence(value)
            evaluator.detect_evaluator_claim_closure(value)

        with self.assertRaises(ReportRecoveryStop):
            self.run_admission([initial, local, malicious], validate)
        self.assertEqual(len(self.observed_calls), 3)

    def test_integrity_failure_is_never_a_report_retry(self):
        def validate(value):
            raise ArtifactContractError(
                code='UNSAFE_PATH', field=EVIDENCE_FIELD, message='Escapes workspace',
                expected='Current workspace', failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE)
        with self.assertRaises(ArtifactContractError):
            self.run_admission([report()], validate)
        self.assertEqual(len(self.observed_calls), 1)

    def test_no_progress_does_not_consume_unbounded_model_calls(self):
        initial = report(evidence=())
        with self.assertRaises(ReportRecoveryStop):
            self.run_admission([initial, initial, initial, initial], self.require_evidence)
        self.assertEqual(len(self.observed_calls), 2)

    def test_phase_a_must_not_use_phase_b_claim_closure(self):
        with self.assertRaises(ReportRecoveryStop):
            self.run_admission([report()], evaluator.detect_evaluator_claim_closure, phase='PHASE_A')
        self.assertEqual(len(self.observed_calls), 1)

    def test_global_budget_does_not_grow_when_routes_change(self):
        initial = report(evidence=())
        local = copy.deepcopy(initial)
        local['impact_challenge'][GROUP][0]['evidence'] = ['reader.js']
        closed = copy.deepcopy(local)
        closed['findings'] = [finding()]
        closed['impact_challenge'][GROUP][0]['finding_ids'] = ['F1']

        def validate(value):
            self.require_evidence(value)
            evaluator.detect_evaluator_claim_closure(value)
            raise ArtifactContractError(
                code='IMPACT_SYMBOL_GENERIC', field=f'impact_challenge.{GROUP}[0].evidence',
                message='A further local error', expected='Concrete evidence')

        with self.assertRaises(ReportRecoveryStop):
            self.run_admission([initial, local, closed, closed], validate)
        self.assertEqual(len(self.observed_calls), 3)


class EvaluatorContractReasonTests(unittest.TestCase):
    def value(self):
        value = report(status='PASS', finding_ids=('F1',), findings=(finding(),))
        value['reason'] = ''
        value['impact_challenge']['blind_contract_dispositions'] = [{
            'disposition': 'MODEL_CONFLICT', 'finding_ids': ['F1'],
            'reason': 'The original technical contract excludes an affected reader.'}]
        return value

    def test_derived_replan_can_fill_empty_top_level_reason(self):
        original = self.value()
        state = EvaluatorClosureCorrectionState()
        fields = state.begin(original, closure_error(original))
        self.assertIn('reason', fields)
        corrected = copy.deepcopy(original)
        corrected['status'] = 'REPLAN_REQUIRED'
        corrected['reason'] = original['impact_challenge']['blind_contract_dispositions'][0]['reason']
        state.observe_corrected(corrected)

    def test_replan_reason_cannot_stay_empty_or_change_row_claims(self):
        original = self.value()
        for change in ('empty', 'row'):
            with self.subTest(change=change):
                state = EvaluatorClosureCorrectionState()
                state.begin(original, closure_error(original))
                corrected = copy.deepcopy(original)
                corrected['status'] = 'REPLAN_REQUIRED'
                if change == 'row':
                    corrected['reason'] = 'An explanation.'
                    corrected['impact_challenge']['blind_contract_dispositions'][0]['reason'] = 'Changed claim.'
                with self.assertRaises(ReportRecoveryStop):
                    state.observe_corrected(corrected)


if __name__ == '__main__':
    unittest.main()
