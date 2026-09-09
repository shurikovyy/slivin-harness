"""Narrow report-only correction policy, independent of product repair/replan."""
from __future__ import annotations

import copy
import re
from typing import Any

from .protocol import ArtifactContractError

MAX_REPORT_CORRECTIONS = 2
_LOCAL_FIELD = re.compile(
    r"post_patch_impact\.(?:changed_contracts|in_scope_consumers|not_affected_consumers|"
    r"related_out_of_scope|new_risks|changed_path_review|search_evidence)\[\d+\]\."
    r"(?:symbols|evidence|evidence_paths)(?:\[\d+\])?$"
)


def correctable_report_field(error: ArtifactContractError) -> str | None:
    # No model, set coverage, classification, proof, capability or integrity errors.
    if error.code == "MISSING_FIELDS" and isinstance(error.actual, list) and len(error.actual) == 1:
        candidate = error.field + "." + str(error.actual[0])
        return candidate if _LOCAL_FIELD.fullmatch(candidate) else None
    if error.code not in {"IMPACT_EVIDENCE_EMPTY", "IMPACT_SYMBOL_GENERIC", "TYPE_MISMATCH", "IMPACT_PATH_MISSING"}:
        return None
    if not _LOCAL_FIELD.fullmatch(error.field):
        return None
    # Permit replacing this evidence array only. Identity, semantics, other rows
    # and all Contract/discovery/check claims stay byte-for-byte JSON-equivalent.
    return re.sub(r"\[\d+\]$", "", error.field)


def _without_fields(report: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    value = copy.deepcopy(report)
    for field in fields:
        parts = re.findall(r"[^.\[\]]+", field)
        node: Any = value
        try:
            for part in parts[:-1]:
                node = node[int(part)] if isinstance(node, list) else node[part]
            node[parts[-1]] = "<CONTROLLER_ALLOWED_LOCAL_CORRECTION>"
        except (KeyError, IndexError, TypeError, ValueError):
            # Removing/rearranging a row cannot be normalized away.
            continue
    return value


def preserves_report_claims(original: dict, corrected: dict, *, fields: list[str]) -> bool:
    return _without_fields(original, fields) == _without_fields(corrected, fields)


def correction_prompt(error: ArtifactContractError, *, fields: list[str]) -> str:
    import json
    diagnostic = {
        "code": error.code, "field": error.field,
        "expected": "Non-empty concrete repository evidence identifiers/locators at this field",
        "allowed_fields": fields,
    }
    return (
        "REPORT-ONLY CORRECTION. Return the complete corrected Implementer report in this same thread.\n"
        "Do not modify project files, tests, runtime, Git, permissions, Plan, Contract or Verification Plan. "
        "Do not remove/reorder findings, consumers, obligations or checks; preserve every other field. "
        "Only clarify the identified evidence fields using the existing candidate. For documentation, "
        "real link targets or heading anchors such as README.md#usage are concrete identifiers; "
        "do not invent code symbols or drop evidence. Existing assertions and trusted verification remain mandatory.\n"
        + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
    )
