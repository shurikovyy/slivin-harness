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
_PATH_ARRAY_FIELD = re.compile(
    r"(?:"
    r"(?:post_patch_impact|impact_analysis)\.(?:changed_contracts|in_scope_consumers|affected_consumers|"
    r"not_affected_consumers|related_out_of_scope|new_risks|search_evidence|source_assessments)\[\d+\]\."
    r"(?:paths|evidence_paths)"
    r"|impact_closure\.(?:changed_contracts|in_scope_consumers|not_affected_consumers|"
    r"related_out_of_scope|search_evidence)\[\d+\]\.(?:paths|evidence_paths)"
    r")$"
)
_MISSING_PATH_LEAF = re.compile(_PATH_ARRAY_FIELD.pattern[:-1] + r"\[\d+\]$")
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
    path_prune_expectations: dict[str, list[Any]] = field(default_factory=dict)

    def observe(self, report: dict) -> None:
        if self.original is None:
            self.original = copy.deepcopy(report)
        elif not preserves_report_claims(self.original, report, fields=self.allowed_fields):
            raise ReportRecoveryStop("REPORT_CORRECTION_CHANGED_CLAIMS")
        elif report != self.original and not preserves_missing_path_prunes(
            report, expectations=self.path_prune_expectations,
        ):
            raise ReportRecoveryStop("REPORT_CORRECTION_CHANGED_CLAIMS")

    def next_fields(self, error: ArtifactContractError, *, attempt: int) -> list[str]:
        fields = correction_fields(error, report=self.original)
        if fields is None:
            raise ReportRecoveryStop("REPORT_INVALID")
        diagnostics = getattr(error, "diagnostics", (error,))
        signature = tuple(sorted((item.code, item.field) for item in diagnostics))
        if attempt and signature == self.previous_diagnostics:
            raise ReportRecoveryStop("REPORT_CORRECTION_NO_PROGRESS")
        self.previous_diagnostics = signature
        if attempt >= MAX_REPORT_CORRECTIONS:
            raise ReportRecoveryStop("REPORT_CORRECTION_EXHAUSTED")
        expectations = missing_path_prune_expectations(self.original, error)
        if expectations is None:
            raise ReportRecoveryStop("REPORT_INVALID")
        for name, expected in expectations.items():
            previous = self.path_prune_expectations.get(name)
            if previous is not None and previous != expected:
                raise ReportRecoveryStop("REPORT_INVALID")
            self.path_prune_expectations[name] = expected
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
    expected_status: str | None = None

    def begin(self, report: dict, error: ArtifactContractError) -> list[str]:
        diagnostics = error.diagnostics if isinstance(error, ArtifactDiagnosticBatch) else (error,)
        if error.failure_kind is not ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE:
            raise ReportRecoveryStop("EVALUATOR_CLOSURE_NOT_CORRECTABLE")
        fields: list[str] = []
        for diagnostic in diagnostics:
            if diagnostic.code == "PASS_WITH_NEGATIVE_DISPOSITION" and diagnostic.field == "status":
                if "status" not in fields:
                    fields.append("status")
                continue
            if (diagnostic.code not in {"NEGATIVE_WITHOUT_FINDING", "NEGATIVE_FINDING_MISSING"}
                    or not _EVALUATOR_CLOSURE_FIELD.fullmatch(diagnostic.field)):
                raise ReportRecoveryStop("EVALUATOR_CLOSURE_NOT_CORRECTABLE")
            if diagnostic.field not in fields:
                fields.append(diagnostic.field)
        if not fields:
            raise ReportRecoveryStop("EVALUATOR_CLOSURE_STATUS_CONFLICT")
        self.original = copy.deepcopy(report)
        if report.get("status") == "PASS":
            self.expected_status = "REPLAN_REQUIRED" if any(
                row.get("disposition") in {"MATERIAL_GAP", "MODEL_CONFLICT"}
                for row in report.get("impact_challenge", {}).get("blind_contract_dispositions", [])
            ) else "FINDINGS"
            if "status" not in fields:
                fields.append("status")
        self.allowed_fields = [*fields, "findings"]
        if (self.expected_status == "REPLAN_REQUIRED"
                and isinstance(report.get("reason"), str) and not report["reason"].strip()):
            # The newly derived stop status requires a top-level explanation.
            # Row reasons and all material claims remain frozen.
            self.allowed_fields.append("reason")
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
        if self.expected_status is not None and report.get("status") != self.expected_status:
            raise ReportRecoveryStop(
                "EVALUATOR_CLOSURE_CHANGED_CLAIMS",
                failure_kind=ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE,
                field="status", correction_attempt=1,
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
    if error.code == "IMPACT_PATH_MISSING":
        if not _MISSING_PATH_LEAF.fullmatch(error.field):
            return None
        return re.sub(r"\[\d+\]$", "", error.field)
    if error.code not in {"IMPACT_EVIDENCE_EMPTY", "IMPACT_SYMBOL_GENERIC", "TYPE_MISMATCH"}:
        return None
    if not (_LOCAL_FIELD.fullmatch(error.field) or _PLANNER_LOCAL_FIELD.fullmatch(error.field)):
        return None
    # Permit replacing this evidence array only. Identity, semantics, other rows
    # and all Contract/discovery/check claims stay byte-for-byte JSON-equivalent.
    return re.sub(r"\[\d+\]$", "", error.field)


def correction_fields(error: ArtifactContractError, *, report: dict | None = None) -> list[str] | None:
    """One ownership policy for the whole package; unsafe siblings forbid retry."""
    diagnostics = error.diagnostics if isinstance(error, ArtifactDiagnosticBatch) else (error,)
    fields = []
    for diagnostic in diagnostics:
        missing = diagnostic.actual if diagnostic.code == "MISSING_FIELDS" else None
        if isinstance(missing, list) and len(missing) > 1:
            leaves = [ArtifactContractError(code="MISSING_FIELDS", field=diagnostic.field,
                       message=diagnostic.message, expected=diagnostic.expected, actual=[key],
                       failure_kind=diagnostic.failure_kind) for key in missing]
        else:
            leaves = [diagnostic]
        for leaf in leaves:
            field = correctable_report_field(leaf)
            if field is None:
                return None
            if field not in fields:
                fields.append(field)
    if any(item.code == "IMPACT_PATH_MISSING" for item in diagnostics):
        if report is None or missing_path_prune_expectations(report, error) is None:
            return None
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


def missing_path_prune_expectations(
    report: dict | None, error: ArtifactContractError,
) -> dict[str, list[Any]] | None:
    """Derive the only admissible path arrays from Controller-proven missing leaves."""
    if report is None:
        return None
    diagnostics = error.diagnostics if isinstance(error, ArtifactDiagnosticBatch) else (error,)
    missing = [item for item in diagnostics if item.code == "IMPACT_PATH_MISSING"]
    if not missing:
        return {}
    indexes: dict[str, set[int]] = {}
    for diagnostic in missing:
        match = re.fullmatch(r"(.+)\[(\d+)\]", diagnostic.field)
        if (diagnostic.failure_kind is not ArtifactFailureKind.LOCAL_WIRE_ERROR
                or match is None or not _PATH_ARRAY_FIELD.fullmatch(match.group(1))):
            return None
        parent, raw_index = match.groups()
        try:
            values = _field_value(report, parent)
            index = int(raw_index)
            if (not isinstance(values, list) or index >= len(values)
                    or values[index] != diagnostic.actual):
                return None
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        indexes.setdefault(parent, set()).add(index)
    expected: dict[str, list[Any]] = {}
    for parent, removed in indexes.items():
        values = _field_value(report, parent)
        survivors = [copy.deepcopy(value) for index, value in enumerate(values) if index not in removed]
        if not survivors:
            return None
        expected[parent] = survivors
    return expected


def preserves_missing_path_prunes(
    corrected: dict, *, expectations: dict[str, list[Any]],
) -> bool:
    for name, expected in expectations.items():
        try:
            if _field_value(corrected, name) != expected:
                return False
        except (KeyError, IndexError, TypeError, ValueError):
            return False
    return True


def preserves_evaluator_closure(original: dict, corrected: dict, *, fields: list[str]) -> bool:
    """Keep claims fixed; materialize missing findings or bind existing ones.

    A complete finding must not be duplicated merely to correct a derived status.
    Existing findings are immutable; every appended finding must be referenced by
    an explicitly authorized closure field. Final semantic validation is mandatory.
    """
    if "reason" in fields and (not isinstance(corrected.get("reason"), str)
                               or not corrected["reason"].strip()):
        return False
    if _without_fields(original, fields) != _without_fields(corrected, fields):
        return False
    before = original.get("findings")
    after = corrected.get("findings")
    if not isinstance(before, list) or not isinstance(after, list) or after[:len(before)] != before:
        return False

    def identifiers(rows: list) -> set[str] | None:
        if any(not isinstance(row, dict) or not isinstance(row.get("finding_id"), str)
               or not row["finding_id"].strip() for row in rows):
            return None
        values = {row["finding_id"] for row in rows}
        return values if len(values) == len(rows) else None

    existing_ids = identifiers(before)
    appended_ids = identifiers(after[len(before):])
    if existing_ids is None or appended_ids is None or existing_ids & appended_ids:
        return False
    final_ids = existing_ids | appended_ids
    referenced: set[str] = set()
    for field_name in fields:
        if field_name in {"findings", "status", "reason"}:
            continue
        try:
            old_ids = _field_value(original, field_name)
            new_ids = _field_value(corrected, field_name)
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        if (not isinstance(old_ids, list) or not isinstance(new_ids, list)
                or not new_ids or not all(isinstance(value, str) and value for value in new_ids)
                or len(new_ids) != len(set(new_ids)) or not set(new_ids) <= final_ids):
            return False
        if old_ids and new_ids != old_ids:
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
        "missing_path_pruning": [
            {"field": item.field, "remove_exact_value": item.actual}
            for item in (error.diagnostics if isinstance(error, ArtifactDiagnosticBatch) else (error,))
            if item.code == "IMPACT_PATH_MISSING"
        ],
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
        "For IMPACT_PATH_MISSING, remove only the exact diagnosed nonexistent path entries. "
        "Do not add or replace paths, remove or reorder surviving paths, or change semantic row fields. "
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
        "required_status": error.expected if error.code == "PASS_WITH_NEGATIVE_DISPOSITION" else None,
        "allowed_fields": fields,
        "correction_budget": MAX_EVALUATOR_CLOSURE_CORRECTIONS,
    }
    return (
        "CLAIM-PRESERVING PHASE-B CLOSURE. Return the complete corrected Evaluator Phase B report "
        "in this same thread. The negative disposition is already a frozen semantic claim. "
        "When status is allowed, change PASS only to the Controller-prescribed direction implied by the negative claims: "
        "REPLAN_REQUIRED for blind contract MATERIAL_GAP/MODEL_CONFLICT, otherwise FINDINGS. "
        "Only when top-level reason is explicitly allowlisted, fill its previously empty "
        "value with an explanation of the frozen negative contract claims. "
        "Do not change dispositions, row reasons, origin_ref values, matches, blind finding "
        "dispositions, coverage conclusions, existing findings, candidate or any other field. "
        "Only append the minimum material final finding(s) required by the already-declared negative "
        "claim and bind the listed finding_ids fields to those new IDs. Each new finding needs "
        "existing candidate repository evidence, a concrete failure mode, required action and typed proof. "
        "Do not invent a finding, treat green tests as rebuttal, or turn a negative disposition positive. "
        "If repository evidence cannot support a material finding, report-only correction cannot close it.\n"
        + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
    )
