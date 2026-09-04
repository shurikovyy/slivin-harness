from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class StrictOutputSchemaError(ValueError):
    """Raised before App Server turn/start for a non-strict output schema."""


def validate_strict_output_schema(schema: Mapping[str, Any]) -> None:
    """Validate the recursive object contract required by Structured Outputs.

    Every schema object that declares ``properties`` must prohibit additional
    properties and require every declared property exactly once. Traversing the
    full schema value covers nested properties, array items, composition
    branches, and shared/reused schema constants.
    """

    if not isinstance(schema, Mapping):
        raise StrictOutputSchemaError("output schema must be an object")

    visited: set[int] = set()

    def walk(value: Any, *, path: str) -> None:
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)

            if "properties" in value:
                properties = value["properties"]
                if not isinstance(properties, Mapping):
                    raise StrictOutputSchemaError(f"{path}.properties must be an object")
                if value.get("additionalProperties") is not False:
                    raise StrictOutputSchemaError(
                        f"{path}.additionalProperties must be false"
                    )
                required = value.get("required")
                if not isinstance(required, list) or not all(
                    isinstance(item, str) for item in required
                ):
                    raise StrictOutputSchemaError(f"{path}.required must be a string array")
                property_keys = list(properties)
                if len(required) != len(set(required)) or set(required) != set(property_keys):
                    missing = sorted(set(property_keys) - set(required))
                    extra = sorted(set(required) - set(property_keys))
                    raise StrictOutputSchemaError(
                        f"{path}.required must contain exactly every property key; "
                        f"missing={missing}, extra={extra}"
                    )

            for key, child in value.items():
                walk(child, path=f"{path}.{key}")
            return

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, child in enumerate(value):
                walk(child, path=f"{path}[{index}]")

    walk(schema, path="$")


def production_output_schemas() -> dict[str, Mapping[str, Any]]:
    """Return every schema supplied to CodexAppServer.run_turn in production."""

    from slivin_harness.evaluator import BLIND_AUDIT_SCHEMA, EVALUATOR_SCHEMA
    from slivin_harness.implementer import IMPLEMENTER_REPORT_SCHEMA
    from slivin_harness.planner import PLANNER_SCHEMA
    from slivin_harness.task_contract import TASK_CONTRACT_NORMALIZER_SCHEMA

    return {
        "TASK_CONTRACT_NORMALIZER_SCHEMA": TASK_CONTRACT_NORMALIZER_SCHEMA,
        "PLANNER_SCHEMA": PLANNER_SCHEMA,
        "IMPLEMENTER_REPORT_SCHEMA": IMPLEMENTER_REPORT_SCHEMA,
        "BLIND_AUDIT_SCHEMA": BLIND_AUDIT_SCHEMA,
        "EVALUATOR_SCHEMA": EVALUATOR_SCHEMA,
    }
