"""Narrow report-only correction policy, independent of product repair/replan."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from .protocol import ArtifactContractError, ArtifactDiagnosticBatch

MAX_REPORT_CORRECTIONS = 2
_LOCAL_FIELD = re.compile(
    r"(?:post_patch_impact|impact_analysis|impact_challenge)\.(?:changed_contracts|in_scope_consumers|affected_consumers|not_affected_consumers|"
    r"related_out_of_scope|new_risks|changed_path_review|search_evidence|source_assessments|"
    r"blind_contract_dispositions|blind_consumer_dispositions|planner_consumer_dispositions|"
    r"implementer_consumer_dispositions|not_affected_dispositions|related_follow_up_dispositions|"
    r"changed_path_dispositions)\[\d+\]\."
    r"(?:symbols|evidence|evidence_paths)(?:\[\d+\])?$"
)


class ReportRecoveryStop(RuntimeError):
    """A typed terminal decision, never a catch-all retry."""


@dataclass
class ReportCorrectionState:
    allowed_fields: list[str] = field(default_factory=list)
    original: dict | None = None
    previous_diagnostics: tuple | None = None

    def observe(self, report: dict) -> None:
        if self.original is None:
            self.original = copy.deepcopy(report)
        elif not preserves_report_claims(self.original, report, fields=self.allowed_fields):
            raise ReportRecoveryStop("REPORT_CORRECTION_CHANGED_CLAIMS")

    def next_fields(self, error: ArtifactContractError, *, attempt: int) -> list[str]:
        fields = correction_fields(error)
        if fields is None:
            raise ReportRecoveryStop("REPORT_INVALID")
        diagnostics = getattr(error, "diagnostics", (error,))
        signature = tuple(sorted((item.code, item.field) for item in diagnostics))
        if attempt and signature == self.previous_diagnostics:
            raise ReportRecoveryStop("REPORT_CORRECTION_NO_PROGRESS")
        self.previous_diagnostics = signature
        if attempt >= MAX_REPORT_CORRECTIONS:
            raise ReportRecoveryStop("REPORT_CORRECTION_EXHAUSTED")
        for name in fields:
            if name not in self.allowed_fields:
                self.allowed_fields.append(name)
        return self.allowed_fields


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


def correction_fields(error: ArtifactContractError) -> list[str] | None:
    """One ownership policy for the whole package; unsafe siblings forbid retry."""
    diagnostics = error.diagnostics if isinstance(error, ArtifactDiagnosticBatch) else (error,)
    fields = []
    for diagnostic in diagnostics:
        missing = diagnostic.actual if diagnostic.code == "MISSING_FIELDS" else None
        if isinstance(missing, list) and len(missing) > 1:
            leaves = [ArtifactContractError(code="MISSING_FIELDS", field=diagnostic.field,
                       message=diagnostic.message, expected=diagnostic.expected, actual=[key]) for key in missing]
        else:
            leaves = [diagnostic]
        for leaf in leaves:
            field = correctable_report_field(leaf)
            if field is None:
                return None
            if field not in fields:
                fields.append(field)
    return fields


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


def correction_prompt(error: ArtifactContractError, *, fields: list[str], role: str = "Implementer") -> str:
    import json
    diagnostic = {
        "code": error.code, "field": error.field,
        "expected": "Non-empty concrete repository evidence identifiers/locators at this field",
        "allowed_fields": fields,
        "diagnostics": [{"code": item.code, "field": item.field} for item in
                        (error.diagnostics if isinstance(error, ArtifactDiagnosticBatch) else (error,))],
    }
    return (
        f"REPORT-ONLY CORRECTION. Return the complete corrected {role} report in this same thread.\n"
        "Do not modify project files, tests, runtime, Git, permissions, Plan, Contract or Verification Plan. "
        "Do not remove/reorder findings, consumers, obligations or checks; preserve every other field. "
        "Only clarify the identified evidence fields using the existing candidate. For documentation, "
        "real link targets or heading anchors such as README.md#usage are concrete identifiers; "
        "do not invent code symbols or drop evidence. Existing assertions and trusted verification remain mandatory.\n"
        + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
    )
