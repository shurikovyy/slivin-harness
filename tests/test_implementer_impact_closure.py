from __future__ import annotations

import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from slivin_harness.implementer import (
    IMPLEMENTER_PROTOCOL_VERSION, build_implementation_contract,
    build_implementation_impact_closure, validate_implementation_impact_closure,
    validate_implementation_report, report_discoveries,
)
from slivin_harness.phase5 import expand_contract_and_verification_plan
from slivin_harness.impact import validate_owner_prose_boundary
from slivin_harness.protocol import ArtifactContractError, ArtifactFailureKind, plan_fingerprint
from slivin_harness.run_state import build_candidate_identity
from slivin_harness.task_contract import build_task_contract
from slivin_harness.verification import compile_verification_plan
from task_runner import collect_changed_paths
from test_implementer import git
from test_planner_impact_closure import synthetic_plan, synthetic_task_contract
from test_protocol import attach_post_patch_impact, empty_post_patch_impact, proof


class ImplementerImpactClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="slivin-post-impact-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        files = {
            "state.py": "def is_current(entry):\n    return entry['active']\n",
            "reader_a.py": "from state import is_current\n\ndef read_value(entry):\n    return entry['value'] if is_current(entry) else None\n",
            "reader_b.py": "from state import is_current\n\ndef can_read(entry):\n    return bool(is_current(entry))\n",
            "writer.py": "def store(entries, key, entry):\n    entries[key] = entry\n",
            "sibling.py": "def title():\n    return 'Entries'\n",
            "metrics.py": "def ratio(count, total):\n    return count / total\n",
            "README.md": "# Notes\n\nOrdinary explanatory prose.\n",
            "obsolete.py": "LEGACY = True\n",
        }
        for path, contents in files.items():
            (self.workspace / path).write_text(contents, encoding="utf-8")
        git(self.workspace, "init")
        git(self.workspace, "config", "user.name", "Test")
        git(self.workspace, "config", "user.email", "test@example.invalid")
        git(self.workspace, "add", "--all")
        git(self.workspace, "commit", "-m", "baseline")
        (self.workspace / "state.py").write_text("def is_current(entry):\n    return entry['active'] and entry['expires_at'] > entry['now']\n", encoding="utf-8")
        self.task = synthetic_task_contract()
        self.plan = synthetic_plan()
        self.contract = build_implementation_contract(self.plan, task_contract=self.task)
        self.report = self.make_report()

    def make_report(self) -> dict:
        report = {
            "protocol_version": IMPLEMENTER_PROTOCOL_VERSION, "status": "COMPLETE", "summary": "Expired entry readers are corrected.",
            "reason": "", "evidence": [], "blockers": [], "additional_check_paths": [], "registered_checks": [],
            "discovered_obligations": [],
            "contract_evidence": [{"item_id": item["id"], "status": "VERIFIED", "evidence": ["Final fresh/expired behavior checked."]} for item in self.contract["items"]],
            "self_verification": {"status": "PASS", "command": "self", "evidence": ["SELF_VERIFY_PASS"], "receipt_id": ""},
        }
        return attach_post_patch_impact(report, plan=self.plan, changed_paths=collect_changed_paths(self.workspace), contract=self.contract)

    def assessment(self, group, author="PLANNER"):
        identifier = next(row["source_id"] for row in self.contract["source_inventory"]["records"] if row["group"] == group and row["author"] == author)
        return next(row for row in self.report["post_patch_impact"]["source_assessments"] if row["source_ref"]["source_id"] == identifier)

    def drop_assessment(self, group, author="PLANNER"):
        self.report["post_patch_impact"]["source_assessments"].remove(self.assessment(group, author))

    def validate(self, *, self_verify: bool = True, owner_paths=(), expanded: bool = False) -> None:
        validate_implementation_report(
            self.report, contract=self.contract, changed_paths=collect_changed_paths(self.workspace),
            workspace=self.workspace, plan=self.plan, owner_allowed_paths=owner_paths,
            self_verification_ok=self_verify, require_expanded=expanded,
        )

    def reject(self, code: str | None = None, **kwargs) -> None:
        with self.assertRaises(ArtifactContractError) as raised:
            self.validate(**kwargs)
        if code:
            self.assertEqual(raised.exception.code, code)

    def add_discovery(self, *, kind="consumer", name="Entry writer") -> None:
        row = {
            "observation_id": "new-" + kind, "name": name, "paths": ["writer.py"], "symbols": ["store"],
            "evidence": ["writer.py store supplies entries to the corrected validity predicate."],
            "required_proof": proof("Fresh entries are stored and expired entries cannot become readable."),
        }
        reason = "The writer supplies the entry validity fields read by the predicate."
        behavior = "Expired entries cannot become readable through the writer."
        if kind == "consumer":
            row.update(why_affected=reason, required_behavior=behavior)
            self.report["post_patch_impact"]["in_scope_consumers"].append(row)
        else:
            row.update(reason=reason, failure_mode=behavior)
            self.report["post_patch_impact"]["new_risks"].append(row)


    def expand(self):
        return expand_contract_and_verification_plan(
            implementation_contract=self.contract,
            previous_verification_plan=compile_verification_plan(self.contract, project_checks=[]),
            discoveries=report_discoveries(self.report, contract=self.contract, plan=self.plan),
            observations=self.report["post_patch_impact"], project_checks=[], task_checks=[],
        )

    def artifact(self):
        candidate = build_candidate_identity(self.workspace)
        context = {
            "candidate_id": candidate.candidate_id, "plan": self.plan, "contract": self.contract,
            "changed_paths": collect_changed_paths(self.workspace), "revision_binding": {"attempt_id": 1, "implementation_contract_rev": 1},
        }
        artifact = build_implementation_impact_closure(self.report, workspace=self.workspace, self_verification_ok=True, **context)
        return artifact, context

    def test_full_complete_reconciles_model_consumers_and_actual_diff(self):
        self.validate(expanded=True)
        artifact, context = self.artifact()
        validate_implementation_impact_closure(artifact, **context)

    def test_missing_planner_consumer_is_rejected(self):
        self.drop_assessment("in_scope_consumers")
        self.reject("SOURCE_ASSESSMENT_MISSING")

    def test_changed_before_or_after_requires_replan(self):
        row = self.assessment("changed_contracts")
        row["disposition"] = "CHALLENGE"
        row["observation"] = "The actual entry ownership contradicts the assumed lifecycle."
        self.reject("SOURCE_MODEL_CHALLENGE")
        self.report.update(status="REPLAN_REQUIRED", terminal_reason_kind="TECHNICAL_MODEL_DIVERGENCE", reason="The validity contract differs from the Planner model.", evidence=["state.py actual entry fields contradict the assumed lifecycle."])
        self.validate(self_verify=False)

    def test_unplanned_contract_cannot_be_added_under_complete(self):
        self.report["post_patch_impact"]["changed_contracts"].append({"observation_id": "other-contract", "name": "Payload ownership", "before": "One owner", "after": "Another owner", "paths": ["state.py"], "symbols": ["is_current"], "evidence": ["The owner changed in state.py."]})
        self.reject("POST_PATCH_MODEL_DIVERGENCE")

    def test_planned_contract_cannot_disappear(self):
        self.drop_assessment("changed_contracts")
        self.reject("SOURCE_ASSESSMENT_MISSING")

    def test_planner_consumer_behavior_and_proof_cannot_change(self):
        original = copy.deepcopy(self.report)
        for key in ("why_affected", "required_behavior", "required_proof"):
            self.report = copy.deepcopy(original)
            self.assessment("in_scope_consumers")[key] = proof("Different proof") if key == "required_proof" else "Different requirement."
            self.reject("UNKNOWN_FIELDS")

    def test_not_affected_must_be_reconsidered(self):
        self.drop_assessment("not_affected_consumers")
        self.reject("SOURCE_ASSESSMENT_MISSING")

    def test_not_affected_can_be_promoted_and_requires_expansion(self):
        self.add_discovery(name="Static sibling")
        self.assessment("not_affected_consumers").update(disposition="PROMOTE", promotion_id="new-consumer")
        self.validate()
        self.reject("POST_PATCH_DISCOVERY_CONTRACT", expanded=True)
        self.contract = self.expand().implementation_contract
        with self.assertRaisesRegex(RuntimeError, "every Implementation Contract item"):
            self.validate()
        self.report = self.make_report()
        with self.assertRaisesRegex(RuntimeError, "trusted self-verification"):
            self.validate(self_verify=False)
        self.validate(expanded=True)
        self.assertFalse(self.expand().added_item_ids)

    def test_discovered_consumer_without_obligation_is_rejected(self):
        self.add_discovery()
        self.validate()
        self.reject("POST_PATCH_DISCOVERY_CONTRACT", expanded=True)

    def test_consumer_obligation_without_impact_row_is_rejected(self):
        self.add_discovery()
        self.contract = self.expand().implementation_contract
        self.report = self.make_report()
        self.drop_assessment("in_scope_consumers", author="IMPLEMENTER")
        self.reject("SOURCE_ASSESSMENT_MISSING")

    def test_discovered_behavior_proof_and_evidence_must_match(self):
        self.add_discovery()
        expanded = self.expand().implementation_contract
        for key in ("why_affected", "required_behavior", "required_proof", "evidence"):
            report = copy.deepcopy(self.report)
            report["post_patch_impact"]["in_scope_consumers"][0][key] = {"required_proof": proof("Different proof."), "evidence": ["Different evidence."]}.get(key, "Different assertion.")
            from slivin_harness.source_records import register_observations
            with self.assertRaises(ArtifactContractError) as failure:
                register_observations(expanded["source_inventory"], report["post_patch_impact"])
            self.assertEqual(failure.exception.code, "OBSERVATION_ID_CONFLICT")

    def test_new_risk_mapping_expands_and_is_idempotent(self):
        self.add_discovery(kind="risk", name="Stale write")
        self.validate()
        expansion = self.expand()
        self.assertEqual(expansion.added_item_ids, ("RISK-DISCOVERED-1",))
        self.contract = expansion.implementation_contract
        self.report = self.make_report()
        self.validate(expanded=True)
        self.assertFalse(self.expand().added_item_ids)
        self.assessment("new_risks", author="IMPLEMENTER")["failure_mode"] = "Different failure mode."
        self.reject("UNKNOWN_FIELDS")

    def test_risk_mapping_is_bidirectional(self):
        self.add_discovery(kind="risk")
        self.reject("POST_PATCH_DISCOVERY_CONTRACT", expanded=True)
        self.contract = self.expand().implementation_contract
        self.report = self.make_report()
        self.drop_assessment("new_risks", author="IMPLEMENTER")
        self.reject("SOURCE_ASSESSMENT_MISSING")

    def test_missing_extra_and_duplicate_changed_paths_are_rejected(self):
        original = copy.deepcopy(self.report)
        for mode in ("missing", "extra", "duplicate"):
            self.report = copy.deepcopy(original)
            reviews = self.report["post_patch_impact"]["changed_path_review"]
            if mode == "missing":
                reviews.clear()
            else:
                row = copy.deepcopy(reviews[0])
                if mode == "extra":
                    row["path"] = "README.md"
                reviews.append(row)
            self.reject("POST_PATCH_PATH_COVERAGE")

    def test_deleted_changed_path_does_not_require_final_file(self):
        (self.workspace / "obsolete.py").unlink()
        self.report = self.make_report()
        self.validate()
        artifact, _ = self.artifact()
        self.assertIn("obsolete.py", artifact["changed_paths"])

    def test_deleted_file_cannot_be_used_as_other_repository_evidence(self):
        (self.workspace / "obsolete.py").unlink()
        self.report = self.make_report()
        self.report["post_patch_impact"]["search_evidence"][0]["evidence_paths"] = ["obsolete.py"]
        self.reject("IMPACT_PATH_MISSING")

    def test_escaping_and_generic_evidence_are_rejected(self):
        original = copy.deepcopy(self.report)
        for key, value in (("paths", ["../outside.py"]), ("symbols", ["shared consumers"]), ("evidence", [])):
            self.report = copy.deepcopy(original)
            self.assessment("in_scope_consumers")[key] = value
            self.reject()

    def test_search_and_summary_are_required(self):
        original = copy.deepcopy(self.report)
        self.report["post_patch_impact"]["search_evidence"] = []
        self.reject("POST_PATCH_SEARCH_MISSING")
        self.report = original
        self.report["post_patch_impact"]["closure_summary"] = " "
        self.reject("IMPACT_EVIDENCE_EMPTY")

    def test_other_justified_is_not_a_generic_bucket(self):
        row = self.report["post_patch_impact"]["changed_path_review"][0]
        row.update(role="OTHER_JUSTIFIED", reason="Needed")
        self.reject("POST_PATCH_OTHER_UNJUSTIFIED")

    def test_planner_follow_up_cannot_be_dropped_or_rewritten(self):
        original = copy.deepcopy(self.report)
        self.drop_assessment("related_out_of_scope")
        self.reject("SOURCE_ASSESSMENT_MISSING")
        self.report = original
        self.assessment("related_out_of_scope")["suggested_follow_up"] = "Ignore it."
        self.reject("UNKNOWN_FIELDS")

    def test_extra_follow_up_is_preserved_without_expansion(self):
        row = {"observation_id": "writer-capacity", "name": "Independent writer capacity limit", "paths": ["writer.py"], "symbols": ["store"], "relation": "Entry storage", "reason": "Storage limits do not change predicate validity.", "evidence": ["writer.py has no capacity limit."], "suggested_follow_up": "Investigate bounded storage capacity for entries."}
        self.report["post_patch_impact"]["related_out_of_scope"].append(row)
        self.validate()
        expansion = self.expand()
        self.assertFalse(expansion.added_item_ids)
        self.contract = expansion.implementation_contract
        self.report = self.make_report()
        artifact, _ = self.artifact()
        self.assertIn("Independent writer capacity limit", [item["name"] for item in artifact["post_patch_impact"]["related_out_of_scope"]])

    def test_artifact_binds_candidate_plan_contract_paths_and_revisions(self):
        artifact, context = self.artifact()
        self.assertEqual(artifact, self.artifact()[0])
        self.assertEqual(artifact["plan_fingerprint"], plan_fingerprint(self.plan))
        for key, value in (("candidate_id", "other"), ("plan", None), ("changed_paths", []), ("revision_binding", {"attempt_id": 2})):
            with self.subTest(key=key), self.assertRaisesRegex(ArtifactContractError, "current candidate"):
                validate_implementation_impact_closure(artifact, **dict(context, **{key: value}))
        altered = dict(self.contract, fingerprint="other")
        with self.assertRaises(ArtifactContractError):
            validate_implementation_impact_closure(artifact, **dict(context, contract=altered))

    def test_tampered_artifact_fingerprint_is_rejected(self):
        artifact, context = self.artifact()
        artifact["post_patch_impact"]["closure_summary"] = "Forged."
        with self.assertRaisesRegex(ArtifactContractError, "fingerprint mismatch") as raised:
            validate_implementation_impact_closure(artifact, **context)
        self.assertIs(raised.exception.failure_kind, ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE)

    def test_repair_changes_candidate_and_invalidates_old_closure(self):
        old, context = self.artifact()
        (self.workspace / "reader_a.py").write_text("from state import is_current\n\ndef read_value(entry):\n    return entry.get('value') if is_current(entry) else None\n", encoding="utf-8")
        context.update(candidate_id=build_candidate_identity(self.workspace).candidate_id, changed_paths=collect_changed_paths(self.workspace))
        with self.assertRaises(ArtifactContractError):
            validate_implementation_impact_closure(old, **context)
        self.reject("POST_PATCH_PATH_COVERAGE")
        self.report = self.make_report()
        with self.assertRaisesRegex(RuntimeError, "trusted self-verification"):
            self.validate(self_verify=False)
        fresh, _ = self.artifact()
        self.assertNotEqual(fresh["fingerprint"], old["fingerprint"])

    def test_fast_code_task_requires_applicable_closure_and_expansion(self):
        model = copy.deepcopy(self.plan["impact_closure"])
        self.plan = None
        self.contract = build_implementation_contract(None, task_contract=self.task)
        self.report = self.make_report()
        closure = self.report["post_patch_impact"]
        closure["source_assessments"] = []
        for group in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope"):
            closure[group] = []
            for index, original in enumerate(model[group]):
                row = dict(copy.deepcopy(original), observation_id=f"{group}-{index}")
                if group == "changed_contracts":
                    row["paths"] = row.pop("evidence_paths")
                    row["symbols"] = row.pop("evidence_symbols")
                    row["evidence"] = ["Actual validity predicate inspected."]
                closure[group].append(row)
        closure["search_evidence"][0]["evidence_paths"] = ["state.py", "reader_a.py", "reader_b.py"]
        self.validate()
        self.reject("POST_PATCH_DISCOVERY_CONTRACT", expanded=True)
        self.assertEqual(len(self.expand().added_item_ids), 2)
        closure["applicable"] = False
        self.reject()

    def test_fast_sanitized_prose_claim_without_owner_boundary_is_rejected(self):
        self.plan = None
        self.contract = build_implementation_contract(None, task_contract=self.task)
        self.report["contract_evidence"] = [{"item_id": row["id"], "status": "VERIFIED", "evidence": ["Checked."]} for row in self.contract["items"]]
        closure = self.report["post_patch_impact"]
        for key in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope", "new_risks"):
            closure[key] = []
        closure["applicable"] = False
        closure["source_assessments"] = []
        closure["search_evidence"][0]["evidence_paths"] = ["README.md"]
        closure["closure_summary"] = "README.md contains only explanatory prose; this task has no executable behavior and affects no state or shared runtime consumers."
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY")
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY", owner_paths=["README.md"])

    def test_fast_owner_backed_prose_can_avoid_fake_ledger(self):
        # Restore only the known fixture file, then create a genuine prose diff.
        (self.workspace / "state.py").write_text("def is_current(entry):\n    return entry['active']\n", encoding="utf-8")
        (self.workspace / "README.md").write_text("# Notes\n\nCorrected explanatory prose.\n", encoding="utf-8")
        self.plan = None
        intent = "Correct explanatory prose spelling."
        normalized = {key: copy.deepcopy(value) for key, value in self.task.items() if key not in {"raw_user_request", "raw_request_sha256", "fingerprint"}}
        normalized["summary"] = intent
        for key in ("explicit_intent", "explicit_acceptance"):
            normalized[key] = [{"claim": intent, "source_text": intent}]
        normalized["explicit_preservation"] = []
        self.task = build_task_contract(raw_request=intent, normalized=normalized)
        self.contract = build_implementation_contract(None, task_contract=self.task)
        self.report["contract_evidence"] = [{"item_id": row["id"], "status": "VERIFIED", "evidence": ["Spelling checked."]} for row in self.contract["items"]]
        closure = empty_post_patch_impact()
        closure.update(
            changed_path_review=[{"path": "README.md", "role": "DOCUMENTATION", "reason": "Correct spelling.", "evidence": ["README.md prose diff inspected."]}],
            search_evidence=[{"target": "README.md", "method": "Review corrected paragraph and references.", "evidence_paths": ["README.md"], "conclusion": "The paragraph is only explanatory prose."}],
            closure_summary="README.md only changes explanatory prose and has no executable consumers, behavioral contracts or runtime obligations.",
        )
        self.report["post_patch_impact"] = closure
        self.validate(owner_paths=["README.md"], expanded=True)
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY")

    def test_owner_prose_helper_rejects_unsafe_or_non_prose_boundaries(self):
        (self.workspace / "notes").mkdir()
        (self.workspace / "[ab].md").write_text("Ordinary prose.\n", encoding="utf-8")
        for paths in (
            [], ["README.md", "state.py"], ["state.py"], ["notes"], ["missing.md"],
            ["../outside.md"], ["..\\outside.md"], ["C:outside.md"], ["C:/outside.md"],
            ["/outside.md"], ["\\\\server\\share\\notes.md"], ["*.md"], ["[ab].md"],
            ["README.md:stream"], ["."], [""],
        ):
            with self.subTest(paths=paths), self.assertRaises(ArtifactContractError):
                validate_owner_prose_boundary(paths, workspace=self.workspace, search_paths=["README.md"])
        with self.assertRaises(ArtifactContractError):
            validate_owner_prose_boundary(["README.md"], workspace=self.workspace, search_paths=["state.py"])
        for extension in (".md", ".rst", ".txt", ".adoc"):
            path = f"notes/guide{extension}"
            (self.workspace / path).write_text("Ordinary explanatory prose.\n", encoding="utf-8")
            self.assertEqual(
                validate_owner_prose_boundary([path.replace("/", "\\")], workspace=self.workspace, search_paths=[path]),
                [path],
            )

    def test_owner_prose_helper_rejects_canonical_escape(self):
        with tempfile.TemporaryDirectory(prefix="slivin-impact-outside-") as outside:
            (Path(outside) / "notes.md").write_text("Ordinary prose.\n", encoding="utf-8")
            link = self.workspace / "linked"
            if os.name == "nt":
                subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), outside], check=True, capture_output=True)
            else:
                link.symlink_to(Path(outside), target_is_directory=True)
            with self.assertRaises(ArtifactContractError) as raised:
                validate_owner_prose_boundary(["README.md", "linked/notes.md"], workspace=self.workspace, search_paths=["README.md"])
            self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_full_applicability_cannot_be_changed_by_implementer(self):
        self.report["post_patch_impact"]["applicable"] = False
        self.reject("POST_PATCH_MODEL_DIVERGENCE")

    def test_non_complete_can_use_empty_partial_ledger(self):
        for status in ("REPLAN_REQUIRED", "BLOCKED", "NEEDS_USER_DECISION"):
            self.report.update(status=status, terminal_reason_kind={"REPLAN_REQUIRED": "TECHNICAL_MODEL_DIVERGENCE", "BLOCKED": "INFRASTRUCTURE_BLOCKED", "NEEDS_USER_DECISION": "USER_DECISION_REQUIRED"}[status], reason="Actual validity ownership requires further investigation.", evidence=["writer.py stores fields outside the assumed lifecycle."], post_patch_impact=empty_post_patch_impact(), contract_evidence=[])
            self.validate(self_verify=False)

    def test_post_patch_wire_block_is_mandatory(self):
        del self.report["post_patch_impact"]
        self.reject()


if __name__ == "__main__":
    unittest.main()
