"""Narrow report-only correction policy, independent of product repair/replan."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from .protocol import ArtifactContractError, ArtifactDiagnosticBatch, ArtifactFailureKind

MAX_REPORT_CORRECTIONS = 2
MAX_EVALUATOR_CLOSURE_CORRECTIONS = 1
_LOCAL_FIELD = re.compile(
    r"(?:post_patch_impact|impact_analysis|impact_challenge)\.(?:changed_contracts|in_scope_consumers|affected_consumers|not_affected_consumers|"
    r"related_out_of_scope|new_risks|changed_path_review|search_evidence|source_assessments|"
    r"blind_contract_dispositions|blind_consumer_dispositions|planner_consumer_dispositions|"
    r"implementer_consumer_dispositions|not_affected_dispositions|related_follow_up_dispositions|"
    r"changed_path_dispositions)\[\d+\]\."
    r"(?:symbols|evidence|evidence_paths)(?:\[\d+\])?$"
)
_PLANNER_LOCAL_FIELD = re.compile(
    r"impact_closure\.(?:changed_contracts|in_scope_consumers|not_affected_consumers|"
    r"related_out_of_scope|search_evidence)\[\d+\]\."
    r"(?:paths|symbols|evidence|evidence_paths|evidence_symbols)(?:\[\d+\])?$"
)
_ORIGIN_REF_FIELD = re.compile(
    r"impact_challenge\.(?:blind_contract_dispositions|blind_consumer_dispositions|"
    r"planner_consumer_dispositions|implementer_consumer_dispositions|"
    r"not_affected_dispositions|related_follow_up_dispositions)\[\d+\]"
    r"(?:\.matches\[\d+\])?\.origin_ref$"
)
_EVALUATOR_CLOSURE_FIELD = re.compile(
    r"impact_challenge\.(?:blind_contract_dispositions|blind_consumer_dispositions|"
    r"planner_consumer_dispositions|implementer_consumer_dispositions|"
    r"not_affected_dispositions|related_follow_up_dispositions|"
    r"changed_path_dispositions)\[\d+\]\.finding_ids$"
)


class ReportRecoveryStop(RuntimeError):
    """A typed terminal decision, never a catch-all retry."""

    def __init__(
        self, reason_code: str, *,
        failure_kind: ArtifactFailureKind = ArtifactFailureKind.LOCAL_WIRE_ERROR,
        field: str | None = None,
        correction_attempt: int | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.failure_kind = failure_kind
        self.field = field
        self.correction_attempt = correction_attempt


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


@dataclass
class EvaluatorClosureCorrectionState:
    """One-turn materialization of an already-declared negative Phase-B claim."""

    original: dict | None = None
    allowed_fields: list[str] = field(default_factory=list)
    correction_observed: bool = False

    def begin(self, report: dict, error: ArtifactContractError) -> list[str]:
        diagnostics = error.diagnostics if isinstance(error, ArtifactDiagnosticBatch) else (error,)
        if error.failure_kind is not ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE:
            raise ReportRecoveryStop("EVALUATOR_CLOSURE_NOT_CORRECTABLE")
        fields: list[str] = []
        for diagnostic in diagnostics:
            if (diagnostic.code not in {"NEGATIVE_WITHOUT_FINDING", "NEGATIVE_FINDING_MISSING"}
                    or not _EVALUATOR_CLOSURE_FIELD.fullmatch(diagnostic.field)):
                raise ReportRecoveryStop("EVALUATOR_CLOSURE_NOT_CORRECTABLE")
            if diagnostic.field not in fields:
                fields.append(diagnostic.field)
        if not fields or report.get("status") == "PASS":
            raise ReportRecoveryStop("EVALUATOR_CLOSURE_STATUS_CONFLICT")
        self.original = copy.deepcopy(report)
        self.allowed_fields = [*fields, "findings"]
        return list(self.allowed_fields)

    def observe_corrected(self, report: dict) -> None:
        closure_field = self.allowed_fields[0] if self.allowed_fields else None
        if self.original is None:
            raise ReportRecoveryStop(
                "EVALUATOR_CLOSURE_NOT_STARTED",
                failure_kind=ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE,
                field=closure_field, correction_attempt=1,
            )
        if self.correction_observed:
            raise ReportRecoveryStop(
                "EVALUATOR_CLOSURE_EXHAUSTED",
                failure_kind=ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE,
                field=closure_field, correction_attempt=1,
            )
        self.correction_observed = True
        if report == self.original:
            raise ReportRecoveryStop(
                "EVALUATOR_CLOSURE_NO_PROGRESS",
                failure_kind=ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE,
                field=closure_field, correction_attempt=1,
            )
        if not preserves_evaluator_closure(self.original, report, fields=self.allowed_fields):
            raise ReportRecoveryStop(
                "EVALUATOR_CLOSURE_CHANGED_CLAIMS",
                failure_kind=ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE,
                field=closure_field, correction_attempt=1,
            )


def correctable_report_field(error: ArtifactContractError) -> str | None:
    # No model, set coverage, classification, proof, capability or integrity errors.
    if error.failure_kind is not ArtifactFailureKind.LOCAL_WIRE_ERROR:
        return None
    if error.code in {"ORIGIN_REF_UNKNOWN", "ORIGIN_REF_INCOMPATIBLE"} and _ORIGIN_REF_FIELD.fullmatch(error.field):
        return error.field
    if error.code == "MISSING_FIELDS" and isinstance(error.actual, list) and len(error.actual) == 1:
        candidate = error.field + "." + str(error.actual[0])
        return candidate if (_LOCAL_FIELD.fullmatch(candidate) or _PLANNER_LOCAL_FIELD.fullmatch(candidate)) else None
    if error.code not in {"IMPACT_EVIDENCE_EMPTY", "IMPACT_SYMBOL_GENERIC", "TYPE_MISMATCH", "IMPACT_PATH_MISSING"}:
        return None
    if not (_LOCAL_FIELD.fullmatch(error.field) or _PLANNER_LOCAL_FIELD.fullmatch(error.field)):
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


def _field_value(report: dict[str, Any], field_name: str) -> Any:
    node: Any = report
    for part in re.findall(r"[^.\[\]]+", field_name):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def preserves_evaluator_closure(original: dict, corrected: dict, *, fields: list[str]) -> bool:
    """Freeze verdict semantics; permit only new findings and their exact bindings."""
    if _without_fields(original, fields) != _without_fields(corrected, fields):
        return False
    before = original.get("findings")
    after = corrected.get("findings")
    if not isinstance(before, list) or not isinstance(after, list) or after[:len(before)] != before:
        return False
    appended = after[len(before):]
    if not appended:
        return False
    appended_ids = {
        finding.get("finding_id") for finding in appended
        if isinstance(finding, dict) and isinstance(finding.get("finding_id"), str)
    }
    if len(appended_ids) != len(appended) or not appended_ids:
        return False
    referenced: set[str] = set()
    for field_name in fields:
        if field_name == "findings":
            continue
        try:
            old_ids = _field_value(original, field_name)
            new_ids = _field_value(corrected, field_name)
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        if not isinstance(old_ids, list) or not isinstance(new_ids, list) or not new_ids:
            return False
        if len(new_ids) != len(set(new_ids)):
            return False
        if old_ids:
            if new_ids != old_ids:
                return False
        elif not set(new_ids) <= appended_ids:
            return False
        referenced.update(set(new_ids) & appended_ids)
    return referenced == appended_ids


def correction_prompt(error: ArtifactContractError, *, fields: list[str], role: str = "Implementer") -> str:
    import json
    diagnostic = {
        "code": error.code, "field": error.field,
        "expected": error.expected,
        "allowed_fields": fields,
        "diagnostics": [{"code": item.code, "field": item.field} for item in
                        (error.diagnostics if isinstance(error, ArtifactDiagnosticBatch) else (error,))],
    }
    role_policy = (
        "Preserve diagnosis, root cause, technical contract, task alignment, impact classifications, "
        "required behavior, proof levels/capabilities, status and product intent. "
        if role.startswith("Planner") else
        "Do not modify project files, tests, runtime, Git, permissions, Plan, Contract or Verification Plan. "
    )
    return (
        f"REPORT-ONLY CORRECTION. Return the complete corrected {role} report in this same thread.\n"
        + role_policy +
        "Do not remove/reorder findings, consumers, obligations or checks; preserve every other field. "
        "Only clarify the identified evidence fields using the existing candidate. For documentation, "
        "real link targets or heading anchors such as README.md#usage are concrete identifiers; "
        "do not invent code symbols or drop evidence. Existing assertions and trusted verification remain mandatory.\n"
        + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
    )


def evaluator_closure_prompt(error: ArtifactContractError, *, fields: list[str]) -> str:
    import json
    diagnostic = {
        "code": error.code,
        "field": error.field,
        "failure_kind": error.failure_kind.value,
        "allowed_fields": fields,
        "correction_budget": MAX_EVALUATOR_CLOSURE_CORRECTIONS,
    }
    return (
        "CLAIM-PRESERVING PHASE-B CLOSURE. Return the complete corrected Evaluator Phase B report "
        "in this same thread. The negative disposition is already a frozen semantic claim. "
        "Do not change status, dispositions, reasons, origin_ref values, matches, blind finding "
        "dispositions, coverage conclusions, existing findings, candidate or any other field. "
        "Only append the minimum material final finding(s) required by the already-declared negative "
        "claim and bind the listed finding_ids fields to those new IDs. Do not turn a negative "
        "disposition positive.\n"
        + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
    )
