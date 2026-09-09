"""Shared repository evidence and owner-backed impact applicability checks."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from slivin_harness.protocol import (
    ArtifactContractError, ArtifactDiagnosticBatch, ensure_exact_keys,
    require_string_list, require_type, safe_repo_relative,
)


def impact_error(code: str, *, field: str, message: str, actual: object) -> None:
    raise ArtifactContractError(
        code=code, field=field, message=message,
        expected="Concrete, consistent repository-backed impact closure", actual=actual,
    )


def impact_text(value: object, *, field: str) -> str:
    require_type(value, str, field=field)
    if not value.strip():
        impact_error("IMPACT_EVIDENCE_EMPTY", field=field, message=f"{field} must be non-empty", actual=value)
    return " ".join(value.split())


def safe_impact_path(raw: str, *, field: str) -> str:
    relative = safe_repo_relative(raw, field=field)
    if relative == "." or raw.replace("\\", "/").startswith("/") or any(
        char in raw for char in ':*?"<>|'
    ) or any(ord(char) < 32 for char in raw):
        impact_error("UNSAFE_PATH", field=field, message="Unsafe impact evidence path", actual=raw)
    return relative


def impact_paths(values: list[str], *, field: str, workspace: Path, allow_missing: bool = False) -> list[str]:
    """Validate file evidence; allow_missing is only for Controller-known deletions."""
    root = workspace.resolve()
    normalized: list[str] = []
    for index, raw in enumerate(values):
        path_field = f"{field}[{index}]"
        relative = safe_impact_path(raw, field=path_field)
        try:
            resolved = (root / relative).resolve()
            inside = resolved.is_relative_to(root)
            exists = inside and resolved.is_file()
            missing = inside and not (root / relative).exists() and not (root / relative).is_symlink()
        except (OSError, RuntimeError, ValueError):
            inside = exists = missing = False
        if not inside:
            impact_error("UNSAFE_PATH", field=path_field, message="Impact evidence path escapes workspace", actual=raw)
        if not exists and not (allow_missing and missing):
            impact_error("IMPACT_PATH_MISSING", field=path_field, message="Impact evidence requires an existing repository file", actual=raw)
        normalized.append(relative)
    return normalized


def validate_impact_structure(value: object, *, schema: Mapping[str, Any],
                              workspace: Path, field: str) -> None:
    """Collect independent leaf diagnostics without traversing malformed parents.

    A bounded package is never silently truncated. More than 1024 diagnostics
    rejects the envelope; correction does not manufacture missing source claims.
    Shared scalar rules also serve read-only role report admission.
    """
    from slivin_harness.verification import validate_proof_target
    errors: list[ArtifactContractError] = []

    def capture(operation) -> bool:
        try:
            operation()
            return True
        except ArtifactContractError as exc:
            errors.append(exc)
            if len(errors) > 1024:
                impact_error("REPORT_DIAGNOSTIC_LIMIT", field=field,
                             message="Report exceeds 1024 diagnostics; no fields were discarded", actual=len(errors))
            return False

    def visit(node, shape, location, key=""):
        kind = shape["type"]
        if key == "required_proof":
            capture(lambda: validate_proof_target(node, field=location))
        elif kind == "object":
            if not capture(lambda: require_type(node, dict, field=location)):
                return
            fields = shape["properties"]
            # Unknown keys and missing keys are parent errors. Known siblings
            # can still be checked; absent children are never dereferenced.
            capture(lambda: ensure_exact_keys(node, allowed=fields, required=fields, field=location))
            for child, child_shape in fields.items():
                if child in node:
                    visit(node[child], child_shape, f"{location}.{child}", child)
        elif kind == "array" and shape["items"]["type"] == "object":
            if capture(lambda: require_type(node, list, field=location)):
                for index, row in enumerate(node):
                    visit(row, shape["items"], f"{location}[{index}]")
        elif kind == "array":
            if not capture(lambda: require_string_list(node, field=location)):
                return
            if not node and key != "finding_ids":
                capture(lambda: impact_error("IMPACT_EVIDENCE_EMPTY", field=location,
                    message=f"{location} requires concrete evidence", actual=node))
            for entry in node:
                capture(lambda entry=entry: impact_text(entry, field=location))
            if key in {"paths", "evidence_paths"}:
                capture(lambda: impact_paths(node, field=location, workspace=workspace))
            if key == "symbols" and any(any(char.isspace() for char in entry)
                    or not any(char.isalnum() for char in entry) for entry in node):
                capture(lambda: impact_error("IMPACT_SYMBOL_GENERIC", field=location,
                    message="Symbols require concrete identifiers or documentation anchors", actual=node))
        elif kind == "string":
            # An empty summary is valid on a non-COMPLETE envelope. Completeness
            # and status-dependent semantics are checked by the consumer.
            if key in {"closure_summary", "promotion_id", "source_revision"}:
                capture(lambda: require_type(node, str, field=location))
            else:
                capture(lambda: impact_text(node, field=location))
            if "enum" in shape and node not in shape["enum"]:
                capture(lambda: impact_error("POST_PATCH_ENUM", field=location,
                    message="Invalid enum value", actual=node))
        elif kind == "boolean":
            capture(lambda: require_type(node, bool, field=location))

    visit(value, schema, field)
    if errors:
        raise ArtifactDiagnosticBatch(errors)


def validate_owner_prose_boundary(
    owner_allowed_paths: Sequence[str], *, workspace: Path, search_paths: Sequence[str],
    field: str = "impact_closure",
) -> list[str]:
    """Only Controller/manifest paths can authorize the prose-only exception."""
    if not owner_allowed_paths:
        impact_error("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY", field="owner_allowed_paths", message="Non-applicable READY/COMPLETE requires a non-empty owner-defined prose-file boundary", actual=owner_allowed_paths)
    owner_paths = require_string_list(list(owner_allowed_paths), field="owner_allowed_paths")
    if any(char in path for path in owner_paths for char in "[]"):
        impact_error("UNSAFE_PATH", field="owner_allowed_paths", message="Non-applicable owner boundary cannot contain glob patterns", actual=owner_paths)
    owner_paths = impact_paths(owner_paths, field="owner_allowed_paths", workspace=workspace)
    prose_extensions = {".md", ".rst", ".txt", ".adoc"}
    if any(
        Path(path).suffix.lower() not in prose_extensions
        or (workspace / path).resolve().suffix.lower() not in prose_extensions
        for path in owner_paths
    ):
        impact_error("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY", field="owner_allowed_paths", message="Non-applicable READY/COMPLETE requires only ordinary prose files in the owner boundary, including resolved targets", actual=owner_paths)
    if not set(search_paths).issubset(owner_paths):
        impact_error("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY", field=f"{field}.search_evidence", message="Non-applicable evidence must stay within the exact owner-defined prose-file boundary", actual=search_paths)
    return owner_paths
