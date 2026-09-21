from __future__ import annotations

import copy
import json
import unittest

from slivin_harness.protocol import (
    ArtifactContractError,
    ArtifactDiagnosticBatch,
    ArtifactFailureKind,
)
from slivin_harness.report_recovery import (
    ReportCorrectionState,
    ReportRecoveryStop,
    correction_fields,
    correction_prompt,
)


def missing(field: str, value: str) -> ArtifactContractError:
    return ArtifactContractError(
        code="IMPACT_PATH_MISSING",
        field=field,
        message="Impact evidence requires an existing repository file",
        expected="Concrete, consistent repository-backed impact closure",
        actual=value,
        failure_kind=ArtifactFailureKind.LOCAL_WIRE_ERROR,
    )


class MissingPathRecoveryTests(unittest.TestCase):
    evaluator_field = "impact_analysis.related_out_of_scope[1].paths"

    def evaluator_report(self) -> dict:
        return {
            "candidate_id": "candidate-fixed",
            "status": "FINDINGS",
            "impact_analysis": {
                "related_out_of_scope": [
                    {"name": "unrelated", "paths": ["other.md"]},
                    {
                        "name": "Dangling deployment documentation link",
                        "classification": "RELATED_OUT_OF_SCOPE",
                        "relation": "Repository documentation navigation.",
                        "reason": "The target is absent and unrelated to eligibility.",
                        "suggested_follow_up": "Repair the link separately.",
                        "paths": ["README.md", "src/access.cjs", "docs/deployment.md"],
                        "symbols": [],
                        "evidence": ["README.md contains the link."],
                    },
                ],
            },
        }

    def begin(self, report: dict, error: ArtifactContractError) -> ReportCorrectionState:
        state = ReportCorrectionState()
        state.observe(report)
        state.next_fields(error, attempt=0)
        return state

    def test_exact_diagnosed_missing_path_prune_is_eligible(self) -> None:
        original = self.evaluator_report()
        error = missing(self.evaluator_field + "[2]", "docs/deployment.md")
        state = self.begin(original, error)
        self.assertEqual(state.allowed_fields, [self.evaluator_field])
        corrected = copy.deepcopy(original)
        corrected["impact_analysis"]["related_out_of_scope"][1]["paths"] = [
            "README.md", "src/access.cjs",
        ]
        state.observe(corrected)

    def test_replacement_addition_valid_removal_and_reorder_are_rejected(self) -> None:
        original = self.evaluator_report()
        error = missing(self.evaluator_field + "[2]", "docs/deployment.md")
        variants = {
            "replacement": ["README.md", "src/access.cjs", "docs/other.md"],
            "addition": ["README.md", "src/access.cjs", "new.md"],
            "valid-removal": ["README.md"],
            "reorder": ["src/access.cjs", "README.md"],
        }
        for label, paths in variants.items():
            with self.subTest(label=label):
                state = self.begin(original, error)
                corrected = copy.deepcopy(original)
                corrected["impact_analysis"]["related_out_of_scope"][1]["paths"] = paths
                with self.assertRaisesRegex(ReportRecoveryStop, "CHANGED_CLAIMS"):
                    state.observe(corrected)

    def test_semantic_row_mutation_is_rejected_during_exact_prune(self) -> None:
        original = self.evaluator_report()
        error = missing(self.evaluator_field + "[2]", "docs/deployment.md")
        state = self.begin(original, error)
        corrected = copy.deepcopy(original)
        row = corrected["impact_analysis"]["related_out_of_scope"][1]
        row["paths"] = ["README.md", "src/access.cjs"]
        row["reason"] = "Changed semantic conclusion."
        with self.assertRaisesRegex(ReportRecoveryStop, "CHANGED_CLAIMS"):
            state.observe(corrected)

    def test_multiple_diagnosed_missing_entries_are_pruned_in_one_batch(self) -> None:
        original = self.evaluator_report()
        original["impact_analysis"]["related_out_of_scope"][1]["paths"] = [
            "README.md", "docs/first.md", "src/access.cjs", "docs/second.md",
        ]
        error = ArtifactDiagnosticBatch([
            missing(self.evaluator_field + "[1]", "docs/first.md"),
            missing(self.evaluator_field + "[3]", "docs/second.md"),
        ])
        state = self.begin(original, error)
        corrected = copy.deepcopy(original)
        corrected["impact_analysis"]["related_out_of_scope"][1]["paths"] = [
            "README.md", "src/access.cjs",
        ]
        state.observe(corrected)

    def test_all_missing_path_list_is_not_correction_eligible(self) -> None:
        original = self.evaluator_report()
        original["impact_analysis"]["related_out_of_scope"][1]["paths"] = [
            "docs/deployment.md",
        ]
        error = missing(self.evaluator_field + "[0]", "docs/deployment.md")
        self.assertIsNone(correction_fields(error, report=original))
        state = ReportCorrectionState()
        state.observe(original)
        with self.assertRaisesRegex(ReportRecoveryStop, "REPORT_INVALID"):
            state.next_fields(error, attempt=0)

    def test_unsafe_path_never_enters_prune_recovery(self) -> None:
        original = self.evaluator_report()
        error = ArtifactContractError(
            code="UNSAFE_PATH",
            field=self.evaluator_field + "[2]",
            message="Unsafe impact evidence path",
            expected="Safe repository path",
            actual="../outside.md",
            failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
        )
        self.assertIsNone(correction_fields(error, report=original))

    def test_planner_missing_path_uses_the_same_pruning_invariant(self) -> None:
        field = "impact_closure.in_scope_consumers[0].paths"
        original = {
            "status": "READY",
            "diagnosis": {"root_cause": "frozen"},
            "impact_closure": {"in_scope_consumers": [{
                "name": "Reader", "paths": ["reader.py", "missing.py"],
                "required_behavior": "Preserve reads.",
            }]},
        }
        error = missing(field + "[1]", "missing.py")
        state = self.begin(original, error)
        corrected = copy.deepcopy(original)
        corrected["impact_closure"]["in_scope_consumers"][0]["paths"] = ["other.py"]
        with self.assertRaisesRegex(ReportRecoveryStop, "CHANGED_CLAIMS"):
            state.observe(corrected)
        exact = copy.deepcopy(original)
        exact["impact_closure"]["in_scope_consumers"][0]["paths"] = ["reader.py"]
        self.begin(original, error).observe(exact)

    def test_prompt_names_only_exact_pruning_authority(self) -> None:
        error = missing(self.evaluator_field + "[2]", "docs/deployment.md")
        prompt = correction_prompt(error, fields=[self.evaluator_field], role="Evaluator PHASE_A")
        self.assertIn("remove only the exact diagnosed nonexistent path entries", prompt)
        diagnostic = json.loads(prompt.splitlines()[-1])
        self.assertEqual(diagnostic["missing_path_pruning"], [{
            "field": self.evaluator_field + "[2]",
            "remove_exact_value": "docs/deployment.md",
        }])

    def test_repeated_unchanged_path_report_stops_as_no_progress(self) -> None:
        original = self.evaluator_report()
        error = missing(self.evaluator_field + "[2]", "docs/deployment.md")
        state = self.begin(original, error)
        state.observe(copy.deepcopy(original))
        with self.assertRaisesRegex(ReportRecoveryStop, "NO_PROGRESS"):
            state.next_fields(error, attempt=1)


if __name__ == "__main__":
    unittest.main()
