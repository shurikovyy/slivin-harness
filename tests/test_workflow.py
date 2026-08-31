from __future__ import annotations

import json
import unittest

from slivin_harness.evaluator import EVALUATOR_SCHEMA
from slivin_harness.implementer import IMPLEMENTER_REPORT_SCHEMA
from slivin_harness.planner import PLANNER_SCHEMA
from slivin_harness.workflow import (
    ALLOWED_STAGE_TRANSITIONS,
    INVALIDATION_RULES,
    STAGES,
    SUCCESS_NEXT,
    EvaluatorStatus,
    ImplementerStatus,
    InvalidationTrigger,
    PlannerStatus,
    StageId,
    enum_values,
    render_workflow_markdown,
    validate_workflow_definition,
    workflow_snapshot,
)


class WorkflowDefinitionTests(unittest.TestCase):
    def test_canonical_stage_order_is_exactly_zero_through_seven(self) -> None:
        validate_workflow_definition()
        self.assertEqual([stage.number for stage in STAGES], list(range(8)))
        self.assertEqual(
            [stage.stage_id for stage in STAGES],
            [
                StageId.INTAKE_PREFLIGHT,
                StageId.PLANNER,
                StageId.IMPLEMENTATION_CONTRACT,
                StageId.IMPLEMENTER,
                StageId.DETERMINISTIC_CHECKS,
                StageId.RUNTIME_VERIFICATION,
                StageId.EVALUATOR,
                StageId.FINAL_GATE,
            ],
        )

    def test_success_path_and_repair_replan_edges_are_declared(self) -> None:
        self.assertEqual(set(SUCCESS_NEXT), {stage.stage_id for stage in STAGES})
        self.assertEqual(
            set(ALLOWED_STAGE_TRANSITIONS),
            {None, *(stage.stage_id for stage in STAGES)},
        )
        for stage, next_stage in SUCCESS_NEXT.items():
            if next_stage is not None:
                self.assertIn(next_stage, ALLOWED_STAGE_TRANSITIONS[stage])
        self.assertIn(StageId.IMPLEMENTER, ALLOWED_STAGE_TRANSITIONS[StageId.DETERMINISTIC_CHECKS])
        self.assertIn(StageId.IMPLEMENTER, ALLOWED_STAGE_TRANSITIONS[StageId.RUNTIME_VERIFICATION])
        self.assertIn(StageId.IMPLEMENTER, ALLOWED_STAGE_TRANSITIONS[StageId.EVALUATOR])
        self.assertIn(StageId.PLANNER, ALLOWED_STAGE_TRANSITIONS[StageId.EVALUATOR])

    def test_every_invalidation_trigger_has_one_machine_rule(self) -> None:
        self.assertEqual(set(INVALIDATION_RULES), set(InvalidationTrigger))
        self.assertTrue(INVALIDATION_RULES[InvalidationTrigger.SOURCE_CHANGED].delivery_only)
        self.assertTrue(
            INVALIDATION_RULES[InvalidationTrigger.REPLAN_REQUIRED].new_attempt_required
        )
        self.assertEqual(
            INVALIDATION_RULES[InvalidationTrigger.CONTRACT_EXPANDED].invalidate_from,
            StageId.IMPLEMENTER,
        )

    def test_only_optional_compatibility_stages_define_skip_codes(self) -> None:
        skip_by_stage = {
            stage.stage_id: [code.value for code in stage.skip_codes]
            for stage in STAGES
            if stage.skip_codes
        }
        self.assertEqual(
            skip_by_stage,
            {
                StageId.PLANNER: ["PLANNER_SKIPPED_FAST"],
                StageId.RUNTIME_VERIFICATION: ["RUNTIME_VERIFICATION_SKIPPED"],
                StageId.EVALUATOR: ["EVALUATION_SKIPPED_FAST"],
            },
        )

    def test_agent_schemas_use_canonical_status_enums(self) -> None:
        self.assertEqual(
            PLANNER_SCHEMA["properties"]["status"]["enum"],
            enum_values(PlannerStatus),
        )
        self.assertEqual(
            IMPLEMENTER_REPORT_SCHEMA["properties"]["status"]["enum"],
            enum_values(ImplementerStatus),
        )
        self.assertEqual(
            EVALUATOR_SCHEMA["properties"]["status"]["enum"],
            enum_values(EvaluatorStatus),
        )

    def test_workflow_snapshot_is_json_serializable_and_complete(self) -> None:
        snapshot = workflow_snapshot(harness_version="test")
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        self.assertIn("runtime_verification", encoded)
        self.assertIn("CANDIDATE_CHANGED_AFTER_EVALUATION", encoded)
        self.assertEqual(len(snapshot["stages"]), 8)

    def test_rendered_workflow_is_understandable_and_generated(self) -> None:
        rendered = render_workflow_markdown(harness_version="test")
        self.assertIn("0. Intake / Preflight", rendered)
        self.assertIn("5. Runtime / external verification (условно)", rendered)
        self.assertIn("7. Final Gate / result handoff", rendered)
        self.assertIn("не меняет model prompts", rendered)


if __name__ == "__main__":
    unittest.main()
