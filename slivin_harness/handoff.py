"""Controller-owned delivery of current, repository-backed related findings."""
from __future__ import annotations

from slivin_harness.boundaries import boundary

from pathlib import Path
from typing import Any, Mapping, Sequence

from slivin_harness.evaluator import validate_evaluation_artifact
from slivin_harness.impact import impact_paths, impact_text
from slivin_harness.implementer import validate_implementation_impact_closure
from slivin_harness.protocol import plan_fingerprint, require_string_list, stable_fingerprint


USER_FOLLOW_UP_VERSION = "user-follow-up.v2"
USER_FOLLOW_UP_ARTIFACT = "user_follow_up_report.json"


class UserFollowUpError(RuntimeError):
    pass


def _text(value: object) -> str:
    return impact_text(value, field="user_follow_up")


def _name(value: str) -> str:
    return _text(value).casefold()


def _strings(value: object, field: str, *, preserve_spacing: bool = False) -> list[str]:
    rows = require_string_list(value, field=field)
    if not rows:
        raise UserFollowUpError(f"Follow-up requires concrete {field}")
    normalized = [_text(row) for row in rows]
    return sorted(set(rows if preserve_spacing else normalized))


@boundary("B12")
def build_user_follow_up_report(
    *, task_id: str, mode: str, pipeline_profile: str,
    candidate_id: str, attempt_id: int, revision_snapshot: Mapping[str, Any],
    workspace: Path, changed_paths: Sequence[str],
    plan: dict[str, Any] | None, implementation_contract: Mapping[str, Any],
    implementation_impact_closure: Mapping[str, Any],
    revision_binding: Mapping[str, Any],
    blind_audit: Mapping[str, Any] | None, evaluation: Mapping[str, Any] | None,
    owner_allowed_paths: Sequence[str] = (),
) -> dict[str, Any]:
    """Use only current Controller inputs, never held-out output or old attempts."""
    if pipeline_profile not in {"FULL", "FAST"}:
        raise UserFollowUpError("Unknown follow-up pipeline profile")
    if type(attempt_id) is not int or attempt_id < 1:
        raise UserFollowUpError("Follow-up requires a current attempt")
    validate_implementation_impact_closure(
        implementation_impact_closure, candidate_id=candidate_id, plan=plan,
        contract=implementation_contract, changed_paths=changed_paths,
        revision_binding=revision_binding,
    )
    implementation = implementation_impact_closure["post_patch_impact"]
    planner = plan["impact_closure"] if plan is not None else {}
    ledgers = [("IMPLEMENTER", implementation)]
    dispositions: dict[tuple[str, str], str] = {}
    if pipeline_profile == "FULL":
        if plan is None or blind_audit is None or evaluation is None or evaluation.get("status") != "PASS":
            raise UserFollowUpError("FULL follow-up requires current Planner, blind audit and Evaluator PASS")
        validate_evaluation_artifact(
            evaluation, blind_audit=blind_audit, workspace=workspace,
            candidate_id=candidate_id, changed_paths=changed_paths,
            planner_impact_closure=planner,
            implementation_impact_closure=implementation_impact_closure,
            owner_allowed_paths=owner_allowed_paths,
        )
        ledgers = [*ledgers, ("BLIND_EVALUATOR", blind_audit["impact_analysis"])]
        for row in evaluation["impact_challenge"]["related_follow_up_dispositions"]:
            source = "BLIND_EVALUATOR" if row["source"] == "BLIND" else row["source"]
            reference = row["reference"]
            dispositions[source, reference] = row["disposition"]
    elif blind_audit is not None or evaluation is not None:
        raise UserFollowUpError("FAST follow-up cannot claim independent Evaluator review")

    # Names are authoritative classification identifiers. Also retain exact
    # relation/behavior signatures to detect renamed copies of active obligations.
    in_scope_names: set[str] = set()
    in_scope_signatures: set[tuple[str, str]] = set()
    risk_signatures = {
        (_name(row["condition"]), _name(row["failure_mode"]))
        for row in (plan or {}).get("risks", [])
    }
    for ledger in (planner, implementation):
        for group in ("in_scope_consumers", "new_risks"):
            for row in ledger.get(group, []):
                in_scope_names.add(_name(row["name"]))
                in_scope_signatures.add((
                    _name(row.get("why_affected", row.get("reason", ""))),
                    _name(row.get("required_behavior", row.get("failure_mode", ""))),
                ))
                if group == "new_risks":
                    risk_signatures.add((_name(row["reason"]), _name(row["failure_mode"])))
    for item in implementation_contract["items"]:
        if item["source"] == "DISCOVERED" and item["type"] in {"consumer", "risk"}:
            prefix = f"Discovered {item['type']}: "
            requirement = item["requirement"]
            if requirement.startswith(prefix):
                in_scope_names.add(_name(requirement[len(prefix):].splitlines()[0]))

    merged: dict[str, dict[str, Any]] = {}
    review_status = "CONFIRMED_OUT_OF_SCOPE" if pipeline_profile == "FULL" else "DECLARED_OUT_OF_SCOPE_FAST"
    origins = {row["source_id"]: row for row in implementation_impact_closure["source_inventory"]["records"]}
    for source, ledger in ledgers:
        for row in sorted(ledger["related_out_of_scope"], key=lambda item: (_name(item["name"]), stable_fingerprint(item, length=64))):
            title = _text(row["name"])
            relation, reason, next_task = (_text(row[key]) for key in ("relation", "reason", "suggested_follow_up"))
            if next_task.casefold().strip(". !?") in {"later", "todo", "investigate", "fix it"}:
                raise UserFollowUpError("Follow-up requires a concrete suggested next task")
            if (
                _name(title) in in_scope_names
                or (_name(relation), _name(next_task)) in in_scope_signatures
                or (_name(relation), _name(reason)) in risk_signatures
            ):
                raise UserFollowUpError("Current IN_SCOPE obligation cannot become an out-of-scope follow-up")
            paths = sorted(set(impact_paths(
                _strings(row["paths"], "paths", preserve_spacing=True), field="follow_up.paths", workspace=workspace,
            )))
            symbols = _strings(row["symbols"], "symbols")
            evidence = _strings(row["evidence"], "evidence")
            reference = row["impact_id"] if source == "BLIND_EVALUATOR" else row["source_ref"]["source_id"]
            origin = origins.get(reference) if source != "BLIND_EVALUATOR" else None
            if pipeline_profile == "FULL" and dispositions.get((source, reference)) != "CONFIRMED_OUT_OF_SCOPE":
                raise UserFollowUpError("Every FULL related finding requires independent CONFIRMED_OUT_OF_SCOPE")
            if origin is not None and origin["author"] == "PLANNER" and pipeline_profile == "FULL" and dispositions.get(("PLANNER", reference)) != "CONFIRMED_OUT_OF_SCOPE":
                raise UserFollowUpError("The prior source classification also requires independent confirmation")
            identity = dict(relation=relation, reason=reason, suggested_follow_up=next_task, paths=paths, symbols=symbols)
            identity_fingerprint = stable_fingerprint(identity, length=64)
            provenance = dict(source=source, reference=reference, disposition=review_status,
                              source_revision=origin["source_revision"] if origin else "")
            if identity_fingerprint not in merged:
                merged[identity_fingerprint] = dict(
                    follow_up_id=f"FOLLOWUP-{identity_fingerprint[:16]}", title=title,
                    relation=relation, reason_out_of_scope=reason, suggested_next_task=next_task,
                    paths=paths, symbols=symbols, evidence=[], source_evidence=[], review_status=review_status, provenance=[],
                )
            entry = merged[identity_fingerprint]
            entry["evidence"] = sorted(set(entry["evidence"]) | set(evidence))
            if origin is not None:
                entry["source_evidence"] = sorted(set(entry["source_evidence"]) | set(origin["claim"].get("evidence", [])))
                original_provenance = dict(source=origin["author"], reference=reference,
                                           disposition="SOURCE_CLAIM", source_revision=origin["source_revision"])
                if original_provenance not in entry["provenance"]:
                    entry["provenance"].append(original_provenance)
            if provenance not in entry["provenance"]:
                entry["provenance"].append(provenance)
    follow_ups = [merged[key] for key in sorted(merged)]
    if len({row["follow_up_id"] for row in follow_ups}) != len(follow_ups):
        raise UserFollowUpError("Follow-up identifier collision")
    report = dict(
        schema_version=USER_FOLLOW_UP_VERSION,
        status="FOLLOW_UPS_PRESENT" if follow_ups else "NO_FOLLOW_UPS",
        task_id=_text(task_id), mode=_text(mode), pipeline_profile=pipeline_profile,
        candidate_id=_text(candidate_id), attempt_id=attempt_id,
        revision_snapshot=dict(revision_snapshot),
        source_bindings=dict(
            plan_fingerprint=plan_fingerprint(plan) if plan is not None else None,
            implementation_impact_fingerprint=implementation_impact_closure["fingerprint"],
            blind_audit_fingerprint=stable_fingerprint(blind_audit, length=64) if blind_audit is not None else None,
            evaluation_fingerprint=stable_fingerprint(evaluation, length=64) if evaluation is not None else None,
        ),
        follow_ups=follow_ups, count=len(follow_ups),
        summary=(
            f"{len(follow_ups)} related follow-up(s) from the current candidate; "
            + ("classification independently confirmed by Evaluator."
               if pipeline_profile == "FULL" else "Implementer declarations only; independent Evaluator did not run in FAST.")
        ),
    )
    report["fingerprint"] = stable_fingerprint(report, length=64)
    return report


def validate_user_follow_up_report(report: Mapping[str, Any], **current_context: Any) -> None:
    expected = build_user_follow_up_report(**current_context)
    # Canonical serialization distinguishes booleans from integer counts and
    # catches unknown fields as well as a correctly rehashed, incomplete ledger.
    if stable_fingerprint(report, length=64) != stable_fingerprint(expected, length=64):
        raise UserFollowUpError("User follow-up report is stale, tampered, or incomplete")


def user_follow_up_console_lines(report: Mapping[str, Any], public_path: Path) -> list[str]:
    def bounded(value: str, limit: int) -> str:
        text = " ".join("".join(char if char.isprintable() else " " for char in value).split())
        return text if len(text) <= limit else text[:limit - 3] + "..."

    return [
        f"USER_FOLLOW_UP_REPORT: {public_path}",
        f"USER_FOLLOW_UP_COUNT: {report['count']}",
        *[f"USER_FOLLOW_UP: {row['follow_up_id']} | {bounded(row['title'], 120)} | NEXT: {bounded(row['suggested_next_task'], 240)}"
          for row in report["follow_ups"]],
    ]
