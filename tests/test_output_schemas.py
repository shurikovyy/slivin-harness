from __future__ import annotations

import unittest
from pathlib import Path

from slivin_harness.app_server import CodexAppServer
from slivin_harness.planner import PLANNER_SCHEMA
from slivin_harness.implementer import IMPLEMENTER_REPORT_SCHEMA
from slivin_harness.output_schema import (
    StrictOutputSchemaError,
    production_output_schemas,
    validate_strict_output_schema,
)


def strict_object(**properties: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


class StrictOutputSchemaTests(unittest.TestCase):
    def test_implementer_v5_requires_strict_post_patch_impact(self) -> None:
        self.assertEqual(IMPLEMENTER_REPORT_SCHEMA["properties"]["protocol_version"]["enum"], ["implementer.v6"])
        self.assertIn("post_patch_impact", IMPLEMENTER_REPORT_SCHEMA["required"])
        self.assertIn("terminal_reason_kind", IMPLEMENTER_REPORT_SCHEMA["required"])
        self.assertEqual(set(IMPLEMENTER_REPORT_SCHEMA["properties"]["terminal_reason_kind"]["enum"]), {
            "NONE", "TECHNICAL_MODEL_DIVERGENCE", "PROOF_MODEL_DIVERGENCE",
            "INFRASTRUCTURE_BLOCKED", "USER_DECISION_REQUIRED",
        })
        closure = IMPLEMENTER_REPORT_SCHEMA["properties"]["post_patch_impact"]
        self.assertEqual(set(closure["required"]), {
            "applicable", "changed_contracts", "in_scope_consumers", "not_affected_consumers",
            "related_out_of_scope", "new_risks", "changed_path_review", "search_evidence", "closure_summary", "source_assessments",
        })
        validate_strict_output_schema(IMPLEMENTER_REPORT_SCHEMA)
        consumers = closure["properties"]["in_scope_consumers"]["items"]
        self.assertNotIn("source", consumers["properties"])
        self.assertIn("observation_id", consumers["required"])
        self.assertIn("required_proof", closure["properties"]["new_risks"]["items"]["required"])

    def test_planner_v5_requires_strict_typed_impact_closure(self) -> None:
        self.assertEqual(PLANNER_SCHEMA["properties"]["protocol_version"]["enum"], ["planner.v6"])
        self.assertIn("impact_closure", PLANNER_SCHEMA["required"])
        closure = PLANNER_SCHEMA["properties"]["impact_closure"]
        self.assertEqual(set(closure["required"]), {
            "applicable", "changed_contracts", "in_scope_consumers", "not_affected_consumers",
            "related_out_of_scope", "search_evidence", "closure_summary",
        })
        validate_strict_output_schema(PLANNER_SCHEMA)
        consumer = closure["properties"]["in_scope_consumers"]["items"]
        self.assertIn("required_proof", consumer["required"])
        self.assertNotIn("affected_consumers", PLANNER_SCHEMA["properties"])

    def test_every_production_app_server_output_schema_is_strict(self) -> None:
        schemas = production_output_schemas()
        self.assertEqual(
            set(schemas),
            {
                "TASK_CONTRACT_NORMALIZER_SCHEMA",
                "PLANNER_SCHEMA",
                "IMPLEMENTER_REPORT_SCHEMA",
                "BLIND_AUDIT_SCHEMA",
                "EVALUATOR_SCHEMA",
            },
        )
        for name, schema in schemas.items():
            with self.subTest(schema=name):
                validate_strict_output_schema(schema)

    def test_old_implementer_nested_required_shape_is_rejected(self) -> None:
        schema = strict_object(
            self_verification={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "string"},
                    "command": {"type": "string"},
                },
                "required": ["status"],
            }
        )
        with self.assertRaisesRegex(StrictOutputSchemaError, r"missing=\['command'\]"):
            validate_strict_output_schema(schema)

    def test_missing_top_level_required_property_is_rejected(self) -> None:
        schema = strict_object(status={"type": "string"}, summary={"type": "string"})
        schema["required"] = ["status"]
        with self.assertRaisesRegex(StrictOutputSchemaError, r"missing=\['summary'\]"):
            validate_strict_output_schema(schema)

    def test_nested_object_violation_is_rejected(self) -> None:
        schema = strict_object(
            nested={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            }
        )
        with self.assertRaisesRegex(StrictOutputSchemaError, "additionalProperties"):
            validate_strict_output_schema(schema)

    def test_array_item_object_violation_is_rejected(self) -> None:
        schema = strict_object(
            rows={
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"value": {"type": "string"}},
                    "required": [],
                },
            }
        )
        with self.assertRaisesRegex(StrictOutputSchemaError, r"missing=\['value'\]"):
            validate_strict_output_schema(schema)

    def test_composition_branch_violation_is_rejected(self) -> None:
        schema = strict_object(
            result={
                "anyOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"value": {"type": "string"}},
                        "required": [],
                    },
                ]
            }
        )
        with self.assertRaisesRegex(StrictOutputSchemaError, r"missing=\['value'\]"):
            validate_strict_output_schema(schema)

    def test_app_server_rejects_invalid_schema_before_turn_start(self) -> None:
        server = CodexAppServer(Path("codex"))
        calls: list[tuple[str, dict]] = []

        def request(method: str, params: dict, *, timeout: float = 60) -> dict:
            calls.append((method, params))
            raise AssertionError("turn/start must not be called")

        server.request = request  # type: ignore[method-assign]
        invalid = strict_object(status={"type": "string"})
        invalid["required"] = []
        with self.assertRaises(StrictOutputSchemaError):
            server.run_turn(
                thread_id="thread-1",
                prompt="test",
                output_schema=invalid,
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
