from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

PLANNER_PROTOCOL_VERSION = "planner.v4"
EVALUATOR_PROTOCOL_VERSION = "evaluator.v5"
MANIFEST_VERSION = 2


class ArtifactContractError(RuntimeError):
    """A structured agent artifact violated the Controller contract."""

    def __init__(
        self,
        *,
        code: str,
        field: str,
        message: str,
        expected: str,
        actual: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message
        self.expected = expected
        self.actual = actual

    def feedback(self) -> dict[str, object | None]:
        return {
            "protocol_error": self.code,
            "field": self.field,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
        }


def stable_fingerprint(value: object, *, length: int = 16) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def plan_fingerprint(plan: dict[str, Any]) -> str:
    return stable_fingerprint(plan)


def ensure_exact_keys(
    value: dict[str, Any],
    *,
    allowed: Iterable[str],
    required: Iterable[str],
    field: str,
) -> None:
    allowed_set = set(allowed)
    required_set = set(required)
    unknown = sorted(set(value) - allowed_set)
    missing = sorted(required_set - set(value))
    if unknown:
        raise ArtifactContractError(
            code="UNKNOWN_FIELDS",
            field=field,
            message=f"Unknown fields in {field}: {', '.join(unknown)}",
            expected=f"Only: {', '.join(sorted(allowed_set))}",
            actual=unknown,
        )
    if missing:
        raise ArtifactContractError(
            code="MISSING_FIELDS",
            field=field,
            message=f"Missing required fields in {field}: {', '.join(missing)}",
            expected=f"Required: {', '.join(sorted(required_set))}",
            actual=missing,
        )


def require_type(value: object, expected: type, *, field: str) -> None:
    if not isinstance(value, expected):
        raise ArtifactContractError(
            code="TYPE_MISMATCH",
            field=field,
            message=f"{field} has wrong type",
            expected=expected.__name__,
            actual=type(value).__name__,
        )


def require_string_list(value: object, *, field: str) -> list[str]:
    require_type(value, list, field=field)
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ArtifactContractError(
                code="TYPE_MISMATCH",
                field=f"{field}[{index}]",
                message=f"{field} must contain strings only",
                expected="string",
                actual=type(item).__name__,
            )
        result.append(item)
    return result


def safe_repo_relative(raw: str, *, field: str = "path") -> str:
    normalized = raw.replace("\\", "/").strip()
    path = Path(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise ArtifactContractError(
            code="UNSAFE_PATH",
            field=field,
            message=f"Unsafe repository-relative path: {raw}",
            expected="A non-empty path inside the task repository",
            actual=raw,
        )
    return path.as_posix()
