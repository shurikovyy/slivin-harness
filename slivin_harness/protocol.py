from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

PLANNER_PROTOCOL_VERSION = "planner.v6"
EVALUATOR_PROTOCOL_VERSION = "evaluator.v8"
MANIFEST_VERSION = 2


class ArtifactFailureKind(str, Enum):
    """Controller routing class for failures at an agent-artifact boundary."""

    LOCAL_WIRE_ERROR = "LOCAL_WIRE_ERROR"
    SEMANTIC_MODEL_CONFLICT = "SEMANTIC_MODEL_CONFLICT"
    INTEGRITY_OR_INFRA_FAILURE = "INTEGRITY_OR_INFRA_FAILURE"


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
        failure_kind: ArtifactFailureKind = ArtifactFailureKind.LOCAL_WIRE_ERROR,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message
        self.expected = expected
        self.actual = actual
        self.failure_kind = failure_kind

    def feedback(self) -> dict[str, object | None]:
        return {
            "protocol_error": self.code,
            "field": self.field,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "failure_kind": self.failure_kind.value,
        }


class ArtifactDiagnosticBatch(ArtifactContractError):
    """Independent diagnostics; first-error attributes remain useful to callers."""

    def __init__(self, diagnostics: list[ArtifactContractError]) -> None:
        if not diagnostics:
            raise ValueError("A diagnostic batch cannot be empty")
        first = diagnostics[0]
        kinds = {item.failure_kind for item in diagnostics}
        if ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE in kinds:
            failure_kind = ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE
        elif ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT in kinds:
            failure_kind = ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT
        else:
            failure_kind = ArtifactFailureKind.LOCAL_WIRE_ERROR
        super().__init__(code=first.code, field=first.field, message=first.message,
                         expected=first.expected, actual=first.actual,
                         failure_kind=failure_kind)
        self.diagnostics = tuple(diagnostics)

    def feedback(self) -> dict[str, object | None]:
        return {**super().feedback(), "diagnostics": [item.feedback() for item in self.diagnostics]}


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
            failure_kind=ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE,
        )
    return path.as_posix()
