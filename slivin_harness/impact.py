"""Shared repository evidence and owner-backed impact applicability checks."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from slivin_harness.protocol import (
    ArtifactContractError, require_string_list, require_type, safe_repo_relative,
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


def impact_paths(values: list[str], *, field: str, workspace: Path) -> list[str]:
    root = workspace.resolve()
    normalized: list[str] = []
    for index, raw in enumerate(values):
        path_field = f"{field}[{index}]"
        relative = safe_impact_path(raw, field=path_field)
        try:
            resolved = (root / relative).resolve()
            inside = resolved.is_relative_to(root)
            exists = inside and resolved.is_file()
        except (OSError, RuntimeError, ValueError):
            inside = exists = False
        if not inside:
            impact_error("UNSAFE_PATH", field=path_field, message="Impact evidence path escapes workspace", actual=raw)
        if not exists:
            impact_error("IMPACT_PATH_MISSING", field=path_field, message="Impact evidence requires an existing repository file", actual=raw)
        normalized.append(relative)
    return normalized


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
