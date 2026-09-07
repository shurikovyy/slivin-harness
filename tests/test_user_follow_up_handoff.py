from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from slivin_harness.handoff import (
    USER_FOLLOW_UP_ARTIFACT, UserFollowUpError, build_user_follow_up_report,
    user_follow_up_console_lines, validate_user_follow_up_report,
)
from slivin_harness.implementer import build_implementation_contract
from slivin_harness.phase7 import Phase7Error, artifact_digest, build_final_acceptance
from slivin_harness.protocol import stable_fingerprint
from slivin_harness.workflow import WorkflowMode
from test_protocol import implementation_impact_fixture, valid_blind_audit, valid_pass, valid_plan, valid_task_contract, write_plan_evidence


def related_finding():
    return dict(
        name="Independent metrics issue", paths=["metrics.py"], symbols=["reader_metric"],
        relation="Metric labeling is applied to the shared reader value.",
        reason="The existing metric label is independent of the requested reader value change.",
        suggested_follow_up="Correct reader_metric label to reader_value and verify emitted measurements.",
        evidence=["metrics.py reader_metric returns legacy_value although the telemetry contract names reader_value."],
    )


def write_related_evidence(workspace):
    (workspace / "metrics.py").write_text(
        "from reader import read_target\n\n# Telemetry consumers expect the label reader_value.\n"
        "def reader_metric():\n    return {'label': 'legacy_value', 'value': read_target()}\n", encoding="utf-8",
    )


def handoff_context(workspace, *, fast=False, related=False, blind_related=False):
    write_plan_evidence(workspace)
    write_related_evidence(workspace)
    plan = valid_plan()
    if related:
        plan["impact_closure"]["related_out_of_scope"] = [related_finding()]
    contract = build_implementation_contract(plan, task_contract=valid_task_contract())
    impact = implementation_impact_fixture(plan=plan, contract=contract)
    audit = valid_blind_audit()
    if blind_related:
        row = related_finding()
        row.update(impact_id="RELATED-1", name="Independent writer issue", relation="The writer records a separate diagnostic value.")
        audit["impact_analysis"]["related_out_of_scope"] = [row]
    evaluation = valid_pass(blind_audit=audit, planner_impact=plan["impact_closure"], implementation_impact=impact)
    if fast:
        plan = audit = evaluation = None
        impact["plan_fingerprint"] = None
        rehash(impact)
    return dict(
        task_id="FOLLOW_UP_TEST", mode="PRODUCTION", pipeline_profile="FAST" if fast else "FULL",
        candidate_id="candidate-1", attempt_id=1, revision_snapshot={},
        workspace=workspace, changed_paths=["target.txt"], plan=plan,
        implementation_contract=contract, implementation_impact_closure=impact,
        revision_binding={}, blind_audit=audit, evaluation=evaluation,
    )


def rehash(value):
    value["fingerprint"] = stable_fingerprint({key: val for key, val in value.items() if key != "fingerprint"}, length=64)


def refresh_evaluation(context):
    rehash(context["implementation_impact_closure"])
    if context["plan"] is not None:
        from slivin_harness.protocol import plan_fingerprint
        context["implementation_impact_closure"]["plan_fingerprint"] = plan_fingerprint(context["plan"])
        rehash(context["implementation_impact_closure"])
        context["evaluation"] = valid_pass(
            blind_audit=context["blind_audit"], planner_impact=context["plan"]["impact_closure"],
            implementation_impact=context["implementation_impact_closure"],
        )


def acceptance_context(context, run_root):
    report = build_user_follow_up_report(**context)
    private = run_root / "controller_private"
    private.mkdir(parents=True, exist_ok=True)
    for root in (run_root, private):
        (root / USER_FOLLOW_UP_ARTIFACT).write_text(json.dumps(report), encoding="utf-8")
    candidate_id = context["candidate_id"]
    return dict(
        task_id=context["task_id"], harness_version="0.8.0a26", workflow_version="workflow.v6",
        mode=WorkflowMode.PRODUCTION, pipeline_profile=context["pipeline_profile"],
        result_mode="keep_worktree", source_baseline_sha="baseline",
        final_candidate=SimpleNamespace(candidate_id=candidate_id, baseline_sha="baseline", workspace_head="baseline", changed_paths=tuple(context["changed_paths"])),
        quality_reconciliation=dict(status="QUALITY_GATE_RECONCILIATION_PASS", candidate_id=candidate_id, attempt_id=1, revision_snapshot={}, stage_bindings=[]),
        patch_metadata=dict(sha256="patch", path="candidate.patch"),
        patch_proof=dict(status="PATCH_RECONSTRUCTION_PASS", patch_sha256="patch", expected_candidate_id=candidate_id, reconstructed_candidate_id=candidate_id),
        reconstructed_verification=dict(status="PASS", expected_candidate_id=candidate_id, reconstructed_candidate_id=candidate_id),
        artifact_bindings=[artifact_digest(run_root, private, USER_FOLLOW_UP_ARTIFACT)], heldout_evidence=None,
        user_follow_up_report=report, user_follow_up_context=context, run_root=run_root, private_root=private,
    )


class UserFollowUpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="slivin-follow-up-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.context = handoff_context(self.workspace, related=True)

    def test_full_exact_duplicate_merges_provenance_and_evidence(self):
        impact = self.context["implementation_impact_closure"]
        impact["post_patch_impact"]["related_out_of_scope"][0]["evidence"].append("Post-patch metric emission was reviewed.")
        refresh_evaluation(self.context)
        report = build_user_follow_up_report(**self.context)
        self.assertEqual(report["count"], 1)
        row = report["follow_ups"][0]
        self.assertEqual([r["source"] for r in row["provenance"]], ["PLANNER", "IMPLEMENTER"])
        self.assertEqual(row["review_status"], "CONFIRMED_OUT_OF_SCOPE")
        self.assertEqual(len(row["evidence"]), 2)
        self.assertRegex(row["follow_up_id"], r"^FOLLOWUP-[0-9a-f]{16}$")

    def test_blind_only_finding_survives(self):
        context = handoff_context(self.workspace, blind_related=True)
        row = build_user_follow_up_report(**context)["follow_ups"][0]
        self.assertEqual(row["provenance"], [dict(source="BLIND_EVALUATOR", reference="RELATED-1", disposition="CONFIRMED_OUT_OF_SCOPE")])

    def test_distinct_findings_and_same_title_do_not_merge(self):
        context = handoff_context(self.workspace, related=True, blind_related=True)
        context["blind_audit"]["impact_analysis"]["related_out_of_scope"][0]["name"] = related_finding()["name"]
        refresh_evaluation(context)
        report = build_user_follow_up_report(**context)
        self.assertEqual(report["count"], 2)
        self.assertEqual(len({row["title"] for row in report["follow_ups"]}), 1)

    def test_identity_is_independent_of_name_evidence_and_order(self):
        before = build_user_follow_up_report(**self.context)
        row = self.context["implementation_impact_closure"]["post_patch_impact"]["related_out_of_scope"][0]
        row.update(name="Metric label follow-up", evidence=["Additional repository observation."])
        row["relation"] = "  " + row["relation"].replace(" ", "  ") + "\n"
        refresh_evaluation(self.context)
        after = build_user_follow_up_report(**self.context)
        self.assertEqual(before["follow_ups"][0]["follow_up_id"], after["follow_ups"][0]["follow_up_id"])
        self.assertEqual(after["count"], 1)
        self.assertEqual(after["follow_ups"][0]["title"], related_finding()["name"])

    def test_full_missing_or_negative_disposition_rejected(self):
        for status in (None, "ACTUALLY_IN_SCOPE", "UNSUPPORTED"):
            with self.subTest(status=status):
                context = copy.deepcopy(self.context)
                rows = context["evaluation"]["impact_challenge"]["related_follow_up_dispositions"]
                if status is None:
                    rows.pop()
                else:
                    rows[0]["disposition"] = status
                with self.assertRaises(RuntimeError):
                    build_user_follow_up_report(**context)

    def test_fast_declares_without_independent_confirmation(self):
        context = handoff_context(self.workspace, fast=True, related=True)
        report = build_user_follow_up_report(**context)
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["follow_ups"][0]["review_status"], "DECLARED_OUT_OF_SCOPE_FAST")
        self.assertEqual(report["follow_ups"][0]["provenance"][0]["source"], "IMPLEMENTER")
        self.assertIsNone(report["source_bindings"]["evaluation_fingerprint"])
        self.assertIn("independent Evaluator did not run", report["summary"])

    def test_fast_replan_can_bind_a_plan_without_claiming_evaluator_review(self):
        context = copy.deepcopy(self.context)
        context.update(pipeline_profile="FAST", blind_audit=None, evaluation=None)
        report = build_user_follow_up_report(**context)
        self.assertIsNotNone(report["source_bindings"]["plan_fingerprint"])
        self.assertEqual(report["follow_ups"][0]["review_status"], "DECLARED_OUT_OF_SCOPE_FAST")
        self.assertEqual([row["source"] for row in report["follow_ups"][0]["provenance"]], ["IMPLEMENTER"])

    def test_zero_full_and_fast_reports_are_mandatory_and_valid(self):
        for fast in (False, True):
            with self.subTest(fast=fast):
                context = handoff_context(self.workspace, fast=fast)
                report = build_user_follow_up_report(**context)
                validate_user_follow_up_report(report, **context)
                self.assertEqual(report["count"], 0)
                self.assertEqual(report["status"], "NO_FOLLOW_UPS")
                self.assertEqual(report["follow_ups"], [])

    def test_current_model_does_not_accumulate_rejected_attempt_findings(self):
        previous = build_user_follow_up_report(**self.context)
        current = handoff_context(self.workspace)
        current["attempt_id"] = 2
        report = build_user_follow_up_report(**current)
        self.assertEqual(report["count"], 0)
        with self.assertRaises(UserFollowUpError):
            validate_user_follow_up_report(previous, **current)

    def test_candidate_attempt_revision_and_sources_are_bound(self):
        report = build_user_follow_up_report(**self.context)
        for field, value in (("candidate_id", "other"), ("attempt_id", 2), ("revision_snapshot", {"implementation_contract": 2})):
            with self.subTest(field=field):
                context = copy.deepcopy(self.context)
                context[field] = value
                with self.assertRaises(RuntimeError):
                    validate_user_follow_up_report(report, **context)
        for source in ("plan", "implementation_impact_closure", "blind_audit", "evaluation", "implementation_contract"):
            with self.subTest(source=source):
                context = copy.deepcopy(self.context)
                if source == "implementation_impact_closure":
                    context[source]["post_patch_impact"]["closure_summary"] += " Additional current evidence."
                    rehash(context[source])
                elif source == "plan":
                    context[source]["diagnosis"]["high_level_approach"] += " Further inspection."
                    refresh_evaluation(context)
                elif source == "implementation_contract":
                    context[source]["fingerprint"] = "new-contract"
                else:
                    context[source]["summary"] += " Further inspection."
                with self.assertRaises(RuntimeError):
                    validate_user_follow_up_report(report, **context)

    def test_tampered_fingerprint_counts_ids_and_rehashed_omission_rejected(self):
        for mutation in ("fingerprint", "count", "boolean_count", "status", "duplicate", "omission"):
            with self.subTest(mutation=mutation):
                report = build_user_follow_up_report(**self.context)
                if mutation == "fingerprint":
                    report["fingerprint"] = "bad"
                elif mutation in {"count", "boolean_count"}:
                    report["count"] = 2 if mutation == "count" else True
                elif mutation == "status":
                    report["status"] = "NO_FOLLOW_UPS"
                elif mutation == "duplicate":
                    report["follow_ups"] *= 2
                    report["count"] = 2
                else:
                    report.update(follow_ups=[], count=0, status="NO_FOLLOW_UPS")
                if mutation != "fingerprint":
                    rehash(report)
                with self.assertRaises(UserFollowUpError):
                    validate_user_follow_up_report(report, **self.context)

    def test_source_paths_must_be_safe_existing_regular_files(self):
        for path in ("../reader.py", "/reader.py", "C:/reader.py", "missing.py", "."):
            with self.subTest(path=path):
                context = handoff_context(self.workspace, fast=True, related=True)
                context["implementation_impact_closure"]["post_patch_impact"]["related_out_of_scope"][0]["paths"] = [path]
                refresh_evaluation(context)
                with self.assertRaises(RuntimeError):
                    build_user_follow_up_report(**context)

    def test_distinct_repository_paths_do_not_collapse_internal_spaces(self):
        context = handoff_context(self.workspace, fast=True)
        rows = []
        for index, path in enumerate(("metric report.py", "metric  report.py")):
            (self.workspace / path).write_text("def reader_metric(): return 'legacy_value'\n", encoding="utf-8")
            row = related_finding()
            row.update(name=f"Metric report {index}", paths=[path])
            rows.append(row)
        context["implementation_impact_closure"]["post_patch_impact"]["related_out_of_scope"] = rows
        refresh_evaluation(context)
        report = build_user_follow_up_report(**context)
        self.assertEqual(report["count"], 2)
        self.assertEqual({row["paths"][0] for row in report["follow_ups"]}, {"metric report.py", "metric  report.py"})

    def test_concrete_suggested_next_task_required(self):
        for text in ("", "later", "todo", "investigate", "fix it"):
            with self.subTest(text=text):
                context = handoff_context(self.workspace, fast=True, related=True)
                context["implementation_impact_closure"]["post_patch_impact"]["related_out_of_scope"][0]["suggested_follow_up"] = text
                refresh_evaluation(context)
                with self.assertRaises(RuntimeError):
                    build_user_follow_up_report(**context)

    def test_in_scope_name_or_exact_signature_cannot_be_follow_up(self):
        for match in ("name", "signature", "contract"):
            with self.subTest(match=match):
                context = handoff_context(self.workspace, fast=True, related=True)
                impact = context["implementation_impact_closure"]["post_patch_impact"]
                row = impact["related_out_of_scope"][0]
                consumer = impact["in_scope_consumers"][0]
                if match == "name":
                    row["name"] = consumer["name"]
                elif match == "signature":
                    row.update(relation=consumer["why_affected"], suggested_follow_up=consumer["required_behavior"])
                else:
                    context["implementation_contract"]["items"].append(dict(source="DISCOVERED", type="consumer", requirement=f"Discovered consumer: {row['name']}\nCondition/reason: dependency"))
                refresh_evaluation(context)
                with self.assertRaisesRegex(UserFollowUpError, "IN_SCOPE"):
                    build_user_follow_up_report(**context)

    def test_console_is_bounded_deterministic_and_does_not_print_evidence(self):
        report = build_user_follow_up_report(**self.context)
        row = report["follow_ups"][0]
        row.update(title="Metric\n" * 100, suggested_next_task="Review\r\n" * 200)
        lines = user_follow_up_console_lines(report, self.root / USER_FOLLOW_UP_ARTIFACT)
        self.assertEqual(lines, user_follow_up_console_lines(report, self.root / USER_FOLLOW_UP_ARTIFACT))
        self.assertIn("USER_FOLLOW_UP_COUNT: 1", lines)
        self.assertLess(len(lines[2]), 450)
        self.assertNotIn("\n", lines[2])
        self.assertNotIn(row["evidence"][0], lines[2])

    def test_current_risk_signature_cannot_be_renamed_as_follow_up(self):
        context = handoff_context(self.workspace, related=True)
        risk = context["plan"]["risks"][0]
        for ledger in (context["plan"]["impact_closure"], context["implementation_impact_closure"]["post_patch_impact"]):
            ledger["related_out_of_scope"][0].update(relation=risk["condition"], reason=risk["failure_mode"])
        refresh_evaluation(context)
        with self.assertRaisesRegex(UserFollowUpError, "IN_SCOPE"):
            build_user_follow_up_report(**context)

    def test_final_acceptance_v3_binds_valid_zero_and_nonzero_report_digest(self):
        for related in (False, True):
            with self.subTest(related=related):
                args = acceptance_context(handoff_context(self.workspace, related=related), self.root / "run")
                result = build_final_acceptance(**args)
                self.assertEqual(result["schema_version"], "final-acceptance.v3")
                self.assertEqual(result["user_follow_up"]["count"], int(related))
                self.assertEqual(result["user_follow_up"]["sha256"], result["artifact_bindings"][0]["sha256"])

    def test_final_acceptance_requires_report_current_context_mirrors_and_digest(self):
        for mutation in ("absent", "public_missing", "mirror", "digest", "context", "candidate", "stale", "lost_finding", "boolean_artifact"):
            with self.subTest(mutation=mutation):
                args = acceptance_context(copy.deepcopy(self.context), self.root / "run")
                if mutation == "absent":
                    args["user_follow_up_report"] = None
                elif mutation == "public_missing":
                    (args["run_root"] / USER_FOLLOW_UP_ARTIFACT).unlink()
                elif mutation == "mirror":
                    (args["run_root"] / USER_FOLLOW_UP_ARTIFACT).write_text("{}", encoding="utf-8")
                elif mutation == "digest":
                    args["artifact_bindings"][0]["sha256"] = "wrong"
                elif mutation == "context":
                    args["user_follow_up_context"]["attempt_id"] = 2
                elif mutation == "candidate":
                    args["final_candidate"].candidate_id = "other"
                elif mutation == "stale":
                    args["user_follow_up_context"]["evaluation"]["summary"] += " Additional evidence."
                elif mutation == "boolean_artifact":
                    stored = copy.deepcopy(args["user_follow_up_report"])
                    stored["count"] = True
                    for root in (args["run_root"], args["private_root"]):
                        (root / USER_FOLLOW_UP_ARTIFACT).write_text(json.dumps(stored), encoding="utf-8")
                    args["artifact_bindings"] = [artifact_digest(args["run_root"], args["private_root"], USER_FOLLOW_UP_ARTIFACT)]
                else:
                    args["user_follow_up_report"].update(follow_ups=[], count=0, status="NO_FOLLOW_UPS")
                    rehash(args["user_follow_up_report"])
                with self.assertRaises(RuntimeError):
                    build_final_acceptance(**args)


if __name__ == "__main__":
    unittest.main()
