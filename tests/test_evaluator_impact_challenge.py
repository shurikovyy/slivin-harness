from __future__ import annotations

import copy
import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import task_runner

from slivin_harness.control_plane import ArtifactVisibility, ControllerPlane
from slivin_harness.evaluator import (
    BLIND_AUDIT_SCHEMA, EVALUATOR_SCHEMA, run_evaluator,
    validate_blind_audit, validate_evaluation_artifact,
)
from slivin_harness.implementer import build_implementation_contract, build_implementation_impact_closure
from slivin_harness.output_schema import validate_strict_output_schema
from slivin_harness.phase6 import BLIND_AUDIT_VERSION
from slivin_harness.protocol import ArtifactContractError
from slivin_harness.run_state import build_candidate_identity
from slivin_harness.verification import compile_verification_plan
from task_runner import collect_changed_paths
from test_implementer import git
from test_planner_impact_closure import synthetic_plan, synthetic_task_contract
from test_protocol import attach_post_patch_impact, evaluator_finding, proof, valid_pass


class ScriptedEvaluator:
    def __init__(self, audit, verdict, observer=None):
        self.responses = [audit, verdict]
        self.observer = observer
        self.prompts = []
        self.threads = []

    def start_thread(self, **kwargs):
        self.threads.append(kwargs)
        return "fresh-independent-evaluator"

    def run_turn(self, **kwargs):
        self.prompts.append(kwargs["prompt"])
        if self.observer:
            self.observer(len(self.prompts), kwargs)
        return json.dumps(self.responses[len(self.prompts) - 1])


class EvaluatorImpactChallengeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="slivin-evaluator-impact-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        files = {
            "state.py": "def is_current(entry):\n    return entry['active']\n",
            "reader_a.py": "def read_value(entry):\n    return entry['value'] if entry['active'] else None\n",
            "reader_b.py": "from state import is_current\n\ndef can_read(entry):\n    return bool(is_current(entry))\n",
            "writer.py": "def store(entries, key, entry):\n    entries[key] = entry\n",
            "sibling.py": "def title():\n    return 'Entries'\n",
            "metrics.py": "def ratio(count, total):\n    return count / total\n",
            "README.md": "# Ordinary prose\n", "obsolete.py": "LEGACY = True\n",
        }
        for path, content in files.items():
            (self.workspace / path).write_text(content, encoding="utf-8")
        git(self.workspace, "init")
        git(self.workspace, "config", "user.name", "Test")
        git(self.workspace, "config", "user.email", "test@example.invalid")
        git(self.workspace, "add", "--all")
        git(self.workspace, "commit", "-m", "synthetic baseline")
        self.patch()
        self.plan = synthetic_plan()
        self.contract = build_implementation_contract(self.plan, task_contract=synthetic_task_contract())
        self.changed_paths = collect_changed_paths(self.workspace)
        self.candidate_id = build_candidate_identity(self.workspace).candidate_id
        self.binding = {"candidate_id": self.candidate_id, "attempt_id": 1}
        self.impact = self.build_impact()
        model = self.plan["impact_closure"]
        self.audit = {
            "protocol_version": BLIND_AUDIT_VERSION, "candidate_id": self.candidate_id,
            "summary": "Independent entry lifecycle review.", "findings": [], "advisories": [],
            "impact_analysis": {
                "applicable": True,
                "changed_contracts": [{
                    "impact_id": "CONTRACT-1", "name": "Visible entry eligibility",
                    "before": "Active expired entries were visible.", "after": "Expired entries are excluded from visibility.",
                    "paths": ["state.py"], "symbols": ["is_current"],
                    "evidence": ["The actual predicate now compares expiration and current time."],
                }],
                "affected_consumers": [{
                    "impact_id": f"CONSUMER-{index}", "name": f"Independent entry reader {index}",
                    "paths": list(row["paths"]), "symbols": list(row["symbols"]),
                    "relation": "The reader depends on entry eligibility.", "required_behavior": "Expired entries are inaccessible.",
                    "evidence": ["The actual reader was traced through eligibility decisions."],
                } for index, row in enumerate(model["in_scope_consumers"], 1)],
                "not_affected_consumers": [dict(copy.deepcopy(row), impact_id=f"NOT-AFFECTED-{index}", name=f"Independent static sibling {index}") for index, row in enumerate(model["not_affected_consumers"], 1)],
                "related_out_of_scope": [dict(copy.deepcopy(row), impact_id=f"RELATED-{index}", name=f"Independent metrics issue {index}") for index, row in enumerate(model["related_out_of_scope"], 1)],
                "changed_path_review": [{"path": path, "observed_role": "Implementation", "impact": "Enforces the entry validity contract.", "evidence": [f"Actual diff of {path} reviewed."]} for path in self.changed_paths],
                "search_evidence": [{"target": "is_current and active field", "method": "Trace predicate calls and direct field reads through sibling decision modules.", "evidence_paths": ["state.py", "reader_a.py", "reader_b.py", "writer.py"], "conclusion": "Both readers and the writer materially relate to entry validity."}],
                "closure_summary": "The actual predicate and direct field readers were followed through sibling decisions, including consumers outside the diff.",
            },
        }
        self.verdict = self.pass_verdict()

    def patch(self):
        (self.workspace / "state.py").write_text("def is_current(entry):\n    return entry['active'] and entry['expires_at'] > entry['now']\n", encoding="utf-8")
        (self.workspace / "reader_a.py").write_text("from state import is_current\n\ndef read_value(entry):\n    return entry['value'] if is_current(entry) else None\n", encoding="utf-8")

    def build_impact(self):
        report = attach_post_patch_impact({
            "protocol_version": "implementer.v4", "status": "COMPLETE", "summary": "Entry reads corrected.",
            "reason": "", "evidence": [], "blockers": [], "additional_check_paths": [], "registered_checks": [],
            "discovered_obligations": [],
            "contract_evidence": [{"item_id": row["id"], "status": "VERIFIED", "evidence": ["The configured regression passed."]} for row in self.contract["items"]],
            "self_verification": {"status": "PASS", "command": "self", "evidence": ["SELF_VERIFY_PASS"], "receipt_id": ""},
        }, plan=self.plan, changed_paths=self.changed_paths)
        return build_implementation_impact_closure(
            report, workspace=self.workspace, changed_paths=self.changed_paths, candidate_id=self.candidate_id,
            plan=self.plan, contract=self.contract, self_verification_ok=True, revision_binding=self.binding,
        )

    def pass_verdict(self):
        return valid_pass(blind_audit=self.audit, planner_impact=self.plan["impact_closure"], implementation_impact=self.impact)

    def validate_a(self, **context):
        validate_blind_audit(self.audit, **dict(workspace=self.workspace, candidate_id=self.candidate_id, changed_paths=self.changed_paths, **context))

    def validate_b(self, **context):
        options = dict(workspace=self.workspace, candidate_id=self.candidate_id, changed_paths=self.changed_paths, planner_impact_closure=self.plan["impact_closure"], implementation_impact_closure=self.impact)
        options.update(context)
        validate_evaluation_artifact(self.verdict, blind_audit=self.audit, **options)

    def reject_a(self, message=None, **context):
        with self.assertRaisesRegex(RuntimeError, message or "."):
            self.validate_a(**context)

    def reject_b(self, message=None, **context):
        with self.assertRaisesRegex(RuntimeError, message or "."):
            self.validate_b(**context)

    def negative(self, group, value):
        row = self.verdict["impact_challenge"][group][0]
        row.update(disposition=value, finding_ids=["IMPACT-GAP-1"])
        finding = evaluator_finding("IMPACT-GAP-1")
        if value == "MODEL_CONFLICT":
            finding["category"] = "MODEL"
        self.verdict.update(status="REPLAN_REQUIRED" if value == "MODEL_CONFLICT" else "FINDINGS", findings=[finding], reason="The independently inspected candidate has a material impact gap.")

    def run_phases(self, *, observer=None, persist=None, impact=None):
        server = ScriptedEvaluator(self.audit, self.verdict, observer)
        plane = ControllerPlane(self.root / "run")

        def persist_blind(audit):
            plane.write_json_once("blind.json", audit, visibility=ArtifactVisibility.PRIVATE)

        def guard(_phase):
            if build_candidate_identity(self.workspace).candidate_id != self.candidate_id:
                raise RuntimeError("Evaluator mutated the candidate")

        result = run_evaluator(
            server, workspace=self.workspace, task_prompt="Expired entries must not be returned.",
            task_contract=synthetic_task_contract(), preflight={"head_sha": git(self.workspace, "rev-parse", "HEAD")},
            owner_allowed_paths=[], changed_paths=self.changed_paths, candidate_id=self.candidate_id,
            implementation_contract=self.contract, verification_plan=compile_verification_plan(self.contract, project_checks=[]),
            contract_closure={"controller_marker": "CONTRACT_CLOSURE_SECRET"},
            checks_evidence={"controller_marker": "CHECKS_SECRET"}, runtime_evidence={"controller_marker": "RUNTIME_SECRET"},
            plan=self.plan, implementation_impact_closure=impact or self.impact, revision_binding=self.binding,
            on_blind_audit=persist or persist_blind, on_phase_complete=guard,
        )
        return server, plane, result

    def test_good_candidate_all_independent_dispositions_pass(self):
        self.validate_a()
        self.validate_b()
        self.assertNotEqual(self.audit["impact_analysis"]["changed_contracts"][0]["name"], self.plan["impact_closure"]["changed_contracts"][0]["name"])

    def test_phase_a_is_blind_and_phase_b_follows_immutable_persistence(self):
        self.plan["diagnosis"]["high_level_approach"] = ["PLANNER_REASONING_SECRET"]
        self.impact = self.build_impact()
        expected_blind = copy.deepcopy(self.audit)

        def inspect(phase, kwargs):
            prompt = kwargs["prompt"]
            self.assertNotIn("PLANNER_REASONING_SECRET", prompt)
            if phase == 1:
                for marker in ("CONTRACT_CLOSURE_SECRET", "CHECKS_SECRET", "RUNTIME_SECRET", self.impact["fingerprint"], self.plan["impact_closure"]["in_scope_consumers"][0]["name"]):
                    self.assertNotIn(marker, prompt)
            else:
                saved = json.loads((self.root / "run" / "controller_private" / "blind.json").read_text(encoding="utf-8"))
                self.assertEqual(saved, expected_blind)
                for marker in ("CONTRACT_CLOSURE_SECRET", "CHECKS_SECRET", "RUNTIME_SECRET", self.impact["fingerprint"]):
                    self.assertIn(marker, prompt)

        server, plane, result = self.run_phases(observer=inspect)
        self.assertEqual(server.threads[0]["sandbox"], "read-only")
        self.assertEqual(result[0], expected_blind)
        with self.assertRaisesRegex(RuntimeError, "Immutable artifact"):
            plane.write_json_once("blind.json", self.audit, visibility=ArtifactVisibility.PRIVATE)

    def test_phase_b_is_not_run_when_blind_persistence_fails(self):
        phases = []
        def fail(_audit):
            raise RuntimeError("Persistence failed")
        with self.assertRaisesRegex(RuntimeError, "Persistence failed"):
            self.run_phases(observer=lambda phase, _kwargs: phases.append(phase), persist=fail)
        self.assertEqual(phases, [1])

    def test_blind_changed_contract_is_required(self):
        self.audit["impact_analysis"]["changed_contracts"] = []
        self.reject_a("requires actual changed contracts")

    def test_blind_before_after_must_differ(self):
        row = self.audit["impact_analysis"]["changed_contracts"][0]
        row["after"] = row["before"]
        self.reject_a("distinct before/after")

    def test_blind_missing_extra_duplicate_changed_paths_rejected(self):
        original = copy.deepcopy(self.audit)
        for mode in ("missing", "extra", "duplicate"):
            with self.subTest(mode=mode):
                self.audit = copy.deepcopy(original)
                rows = self.audit["impact_analysis"]["changed_path_review"]
                if mode == "missing":
                    rows.pop()
                else:
                    rows.append(dict(rows[0], path="README.md" if mode == "extra" else rows[0]["path"]))
                self.reject_a("exactly once")

    def test_deleted_changed_path_is_reviewable_but_not_other_evidence(self):
        (self.workspace / "obsolete.py").unlink()
        self.changed_paths = collect_changed_paths(self.workspace)
        self.candidate_id = build_candidate_identity(self.workspace).candidate_id
        self.binding["candidate_id"] = self.candidate_id
        self.impact = self.build_impact()
        self.audit["candidate_id"] = self.candidate_id
        self.audit["impact_analysis"]["changed_path_review"].append({"path": "obsolete.py", "observed_role": "Deleted legacy source", "impact": "Removes an unused constant.", "evidence": ["The baseline obsolete.py constant has no readers."]})
        self.validate_a()
        self.verdict = self.pass_verdict()
        self.validate_b()
        self.audit["impact_analysis"]["search_evidence"][0]["evidence_paths"] = ["obsolete.py"]
        self.reject_a("existing repository file")

    def test_blind_consumers_require_concrete_paths_symbols_relation_evidence(self):
        original = copy.deepcopy(self.audit)
        for key, value in (("paths", []), ("symbols", []), ("symbols", ["shared consumers"]), ("relation", ""), ("required_behavior", ""), ("evidence", [])):
            with self.subTest(key=key, value=value):
                self.audit = copy.deepcopy(original)
                self.audit["impact_analysis"]["affected_consumers"][0][key] = value
                self.reject_a()

    def test_blind_all_evidence_paths_are_safe_existing_and_canonical(self):
        for path in ("../outside.py", "C:/outside.py", "missing.py", "."):
            with self.subTest(path=path):
                self.audit["impact_analysis"]["affected_consumers"][0]["paths"] = [path]
                self.reject_a()

    def test_blind_canonical_junction_escape_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="slivin-evaluator-outside-") as outside:
            (Path(outside) / "notes.md").write_text("Outside evidence.\n", encoding="utf-8")
            link = self.workspace / "linked"
            if os.name == "nt":
                subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), outside], check=True, capture_output=True)
            else:
                link.symlink_to(Path(outside), target_is_directory=True)
            self.audit["impact_analysis"]["search_evidence"][0]["evidence_paths"] = ["linked/notes.md"]
            self.reject_a("escapes workspace")
            self.audit["impact_analysis"]["search_evidence"][0]["evidence_paths"] = ["state.py"]
            self.changed_paths = ["linked/notes.md"]
            self.audit["impact_analysis"]["changed_path_review"] = [{"path": "linked/notes.md", "observed_role": "Prose", "impact": "Claims to be a valid changed file.", "evidence": ["This existing path resolves outside the workspace."]}]
            self.reject_a("escapes workspace")

    def test_blind_search_and_summary_are_required(self):
        original = copy.deepcopy(self.audit)
        self.audit["impact_analysis"]["search_evidence"] = []
        self.reject_a("search evidence")
        self.audit = original
        self.audit["impact_analysis"]["closure_summary"] = " "
        self.reject_a()

    def test_blind_ids_and_classification_names_are_unique(self):
        original = copy.deepcopy(self.audit)
        for mode in ("unsafe", "duplicate_id", "duplicate_name", "cross_classification"):
            with self.subTest(mode=mode):
                self.audit = copy.deepcopy(original)
                rows = self.audit["impact_analysis"]["affected_consumers"]
                if mode == "unsafe":
                    rows[0]["impact_id"] = "../CONSUMER-1"
                elif mode == "duplicate_id":
                    rows[1]["impact_id"] = rows[0]["impact_id"]
                elif mode == "duplicate_name":
                    rows[1]["name"] = rows[0]["name"].upper()
                else:
                    self.audit["impact_analysis"]["not_affected_consumers"][0]["name"] = rows[0]["name"]
                self.reject_a()

    def test_owner_backed_prose_exception_uses_shared_policy(self):
        (self.workspace / "state.py").write_text("def is_current(entry):\n    return entry['active']\n", encoding="utf-8")
        (self.workspace / "reader_a.py").write_text("def read_value(entry):\n    return entry['value'] if entry['active'] else None\n", encoding="utf-8")
        (self.workspace / "README.md").write_text("# Corrected explanatory prose\n", encoding="utf-8")
        self.candidate_id = build_candidate_identity(self.workspace).candidate_id
        self.audit["candidate_id"] = self.candidate_id
        analysis = self.audit["impact_analysis"]
        for key in ("changed_contracts", "affected_consumers", "not_affected_consumers", "related_out_of_scope"):
            analysis[key] = []
        analysis["applicable"] = False
        self.changed_paths = collect_changed_paths(self.workspace)
        self.assertEqual(self.changed_paths, ["README.md"])
        analysis["changed_path_review"] = [{"path": "README.md", "observed_role": "Prose", "impact": "Corrects explanatory spelling.", "evidence": ["README.md prose reviewed."]}]
        analysis["search_evidence"][0]["evidence_paths"] = ["README.md"]
        analysis["closure_summary"] = "README.md contains only ordinary explanatory prose with no behavioral contracts or runtime obligations."
        from slivin_harness.impact import validate_owner_prose_boundary
        with mock.patch("slivin_harness.evaluator.validate_owner_prose_boundary", wraps=validate_owner_prose_boundary) as shared:
            self.validate_a(owner_allowed_paths=["README.md"])
            shared.assert_called_once()
        for boundary in ([], ["README.md", "state.py"], ["state.py"], ["."], ["missing.md"], ["../outside.md"]):
            with self.subTest(boundary=boundary):
                self.reject_a(owner_allowed_paths=boundary)

    def test_behavioral_candidate_cannot_self_authorize_prose(self):
        analysis = self.audit["impact_analysis"]
        for key in ("changed_contracts", "affected_consumers", "not_affected_consumers", "related_out_of_scope"):
            analysis[key] = []
        analysis["applicable"] = False
        analysis["search_evidence"][0]["evidence_paths"] = ["README.md"]
        analysis["closure_summary"] = "README.md is only prose and this lengthy declaration claims there is no behavioral impact. " * 20
        self.reject_a()
        self.reject_a(owner_allowed_paths=["README.md"])

    def test_every_phase_b_input_set_requires_exact_unique_dispositions(self):
        original = copy.deepcopy(self.verdict)
        for group in (key for key, value in self.verdict["impact_challenge"].items() if isinstance(value, list) and value):
            for mode in ("missing", "extra", "duplicate"):
                with self.subTest(group=group, mode=mode):
                    self.verdict = copy.deepcopy(original)
                    rows = self.verdict["impact_challenge"][group]
                    if mode == "missing":
                        rows.pop()
                    else:
                        row = copy.deepcopy(rows[0])
                        if mode == "extra":
                            key = next(key for key in ("impact_id", "reference", "path", "name") if key in row)
                            row[key] = "EXTRA-999" if key != "path" else "README.md"
                        rows.append(row)
                    self.reject_b()

    def test_all_negative_dispositions_require_findings_and_forbid_pass(self):
        variants = {
            "blind_contract_dispositions": ("MATERIAL_GAP", "MODEL_CONFLICT"),
            "blind_consumer_dispositions": ("MISCLASSIFIED_NOT_AFFECTED", "MISCLASSIFIED_OUT_OF_SCOPE", "MISSING"),
            "planner_consumer_dispositions": ("UNSUPPORTED", "IMPLEMENTATION_GAP"),
            "not_affected_dispositions": ("ACTUALLY_AFFECTED", "INSUFFICIENT_EVIDENCE"),
            "related_follow_up_dispositions": ("ACTUALLY_IN_SCOPE", "UNSUPPORTED"),
            "changed_path_dispositions": ("SUSPICIOUS", "UNJUSTIFIED"),
        }
        original = copy.deepcopy(self.verdict)
        for group, values in variants.items():
            for value in values:
                with self.subTest(group=group, disposition=value):
                    self.verdict = copy.deepcopy(original)
                    self.negative(group, value)
                    self.validate_b()
                    self.verdict["status"] = "PASS"
                    self.reject_b()
                    self.verdict["findings"] = []
                    self.verdict["impact_challenge"][group][0]["finding_ids"] = []
                    self.reject_b("corresponding final finding")

    def test_implementer_discoveries_require_independent_dispositions(self):
        row = copy.deepcopy(self.impact["post_patch_impact"]["in_scope_consumers"][0])
        row.update(source="DISCOVERED", name="New writer dependency", paths=["writer.py"], symbols=["store"])
        self.impact["post_patch_impact"]["in_scope_consumers"].append(row)
        self.reject_b("implementer_consumer_dispositions")
        self.verdict = self.pass_verdict()
        self.validate_b()
        for status in ("UNSUPPORTED", "INCOMPLETE"):
            self.negative("implementer_consumer_dispositions", status)
            self.validate_b()
            self.verdict["status"] = "PASS"
            self.reject_b()

    def test_planner_and_implementer_not_affected_are_separate_authorities(self):
        original = copy.deepcopy(self.verdict)
        for source in ("BLIND", "PLANNER", "IMPLEMENTER"):
            with self.subTest(source=source):
                self.verdict = copy.deepcopy(original)
                self.verdict["impact_challenge"]["not_affected_dispositions"] = [row for row in self.verdict["impact_challenge"]["not_affected_dispositions"] if row["source"] != source]
                self.reject_b("not_affected_dispositions")

    def test_all_related_follow_ups_including_blind_survive_phase_b(self):
        self.audit["impact_analysis"]["related_out_of_scope"].append({
            "impact_id": "RELATED-2", "name": "Duplicate entry replacement policy", "paths": ["writer.py"], "symbols": ["store"],
            "relation": "The same entry store also replaces existing keys.",
            "reason": "Replacement policy is independent of expiration handling.",
            "evidence": ["writer.py store unconditionally assigns entries[key]."],
            "suggested_follow_up": "Define the intended behavior when a key already exists.",
        })
        self.verdict = self.pass_verdict()
        original = copy.deepcopy(self.verdict)
        for source in ("BLIND", "PLANNER", "IMPLEMENTER"):
            with self.subTest(source=source):
                self.verdict = copy.deepcopy(original)
                self.verdict["impact_challenge"]["related_follow_up_dispositions"] = [row for row in self.verdict["impact_challenge"]["related_follow_up_dispositions"] if row["source"] != source]
                self.reject_b("related_follow_up_dispositions")
        self.verdict = original
        _, _, (audit, verdict) = self.run_phases()
        self.assertTrue(audit["impact_analysis"]["related_out_of_scope"][0]["suggested_follow_up"])
        self.assertTrue(any(row["source"] == "BLIND" and row["reference"] == "RELATED-1" for row in verdict["impact_challenge"]["related_follow_up_dispositions"]))
        self.assertTrue(any(row["source"] == "BLIND" and row["reference"] == "RELATED-2" for row in verdict["impact_challenge"]["related_follow_up_dispositions"]))
        self.assertNotIn("Duplicate entry replacement policy", json.dumps(self.plan["impact_closure"]))

    def test_material_finding_reference_cannot_be_invented(self):
        self.negative("blind_consumer_dispositions", "MISSING")
        self.verdict["impact_challenge"]["blind_consumer_dispositions"][0]["finding_ids"] = ["UNKNOWN"]
        self.reject_b("existing final findings")

    def test_positive_consumer_match_must_reference_actual_in_scope(self):
        row = self.verdict["impact_challenge"]["blind_consumer_dispositions"][0]
        row["matches"] = []
        self.reject_b("actual IN_SCOPE")
        row["matches"] = [{"source": "PLANNER", "classification": "IN_SCOPE", "name": "Invented consumer"}]
        self.reject_b("existing normalized ledger")

    def test_disposition_evidence_requires_existing_safe_files(self):
        self.verdict["impact_challenge"]["planner_consumer_dispositions"][0]["evidence_paths"] = ["../escape.py"]
        self.reject_b()

    def test_coverage_summary_cannot_replace_dispositions_or_be_empty(self):
        self.verdict["impact_challenge"]["coverage_summary"] = " "
        self.reject_b()

    def test_retained_blind_finding_cannot_disappear(self):
        finding = evaluator_finding()
        self.audit["findings"] = [finding]
        self.verdict = self.pass_verdict()
        self.verdict["blind_finding_dispositions"][0]["disposition"] = "RETAINED"
        self.reject_b("retained blind finding")

    def test_model_conflict_requires_replan_not_repair(self):
        self.negative("blind_contract_dispositions", "MODEL_CONFLICT")
        self.validate_b()
        self.verdict["status"] = "FINDINGS"
        self.reject_b("REPLAN_REQUIRED")

    def test_candidate_change_invalidates_blind_and_final_challenge(self):
        (self.workspace / "reader_a.py").write_text("def read_value(entry):\n    return None\n", encoding="utf-8")
        new_id = build_candidate_identity(self.workspace).candidate_id
        with self.assertRaisesRegex(RuntimeError, "stale"):
            validate_blind_audit(self.audit, workspace=self.workspace, changed_paths=self.changed_paths, candidate_id=new_id)
        self.reject_b("stale", candidate_id=new_id)

    def test_stale_implementation_artifact_never_enters_phase_b(self):
        original = copy.deepcopy(self.impact)
        for field, value in (("candidate_id", "old"), ("plan_fingerprint", "old"), ("implementation_contract_fingerprint", "old"), ("changed_paths", []), ("revision_binding", {"attempt_id": 0})):
            with self.subTest(field=field):
                from slivin_harness.protocol import stable_fingerprint
                stale = dict(copy.deepcopy(original), **{field: value})
                stale["fingerprint"] = stable_fingerprint({key: val for key, val in stale.items() if key != "fingerprint"}, length=64)
                phases = []
                with self.assertRaises(ArtifactContractError):
                    self.run_phases(impact=stale, persist=lambda _audit: None, observer=lambda phase, _kwargs: phases.append(phase))
                self.assertEqual(phases, [1])

    def test_evaluator_cannot_mutate_candidate_in_either_phase(self):
        for phase in (1, 2):
            with self.subTest(phase=phase):
                self.patch()
                def mutate(current, _kwargs):
                    if current == phase:
                        (self.workspace / "state.py").write_text("BROKEN = True\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "mutated the candidate"):
                    self.run_phases(observer=mutate, persist=lambda _audit: None)

    def test_strict_v2_v6_output_schemas(self):
        self.assertEqual(BLIND_AUDIT_SCHEMA["properties"]["protocol_version"]["enum"], ["blind-audit.v2"])
        self.assertEqual(EVALUATOR_SCHEMA["properties"]["protocol_version"]["enum"], ["evaluator.v6"])
        validate_strict_output_schema(BLIND_AUDIT_SCHEMA)
        validate_strict_output_schema(EVALUATOR_SCHEMA)

    def test_impact_wire_blocks_and_candidate_ids_are_mandatory(self):
        audit, verdict = copy.deepcopy(self.audit), copy.deepcopy(self.verdict)
        for key in ("impact_analysis", "candidate_id"):
            self.audit = copy.deepcopy(audit)
            del self.audit[key]
            self.reject_a()
        self.audit = audit
        for key in ("impact_challenge", "candidate_id"):
            self.verdict = copy.deepcopy(verdict)
            del self.verdict[key]
            self.reject_b()


class EvaluatorAutonomyWorkflowTests(unittest.TestCase):
    def run_sibling_case(self, *, repair=False, model_conflict=False):
        temporary = tempfile.TemporaryDirectory(prefix="slivin-independent-sibling-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "source"
        source.mkdir()
        files = {
            "state.py": "def is_current(entry):\n    return entry['active']\n",
            "reader_a.py": "def read_value(entry):\n    return entry['value'] if entry['active'] else None\n",
            "reader_b.py": "def can_read(entry):\n    return bool(entry['active'])\n",
            "writer.py": "def store(entries, key, entry):\n    entries[key] = entry\n",
            "sibling.py": "def title():\n    return 'Entries'\n",
            "metrics.py": "def ratio(count, total):\n    return count / total\n",
            "regression.py": "from reader_a import read_value\nentry = dict(active=True, expires_at=1, now=2, value='payload')\nassert read_value(entry) is None\nassert read_value(dict(entry, expires_at=3)) == 'payload'\nprint('READER_A_PASS')\n",
        }
        for path, content in files.items():
            (source / path).write_text(content, encoding="utf-8")
        git(source, "init")
        git(source, "config", "user.name", "Test")
        git(source, "config", "user.email", "test@example.invalid")
        git(source, "add", "--all")
        git(source, "commit", "-m", "synthetic baseline")
        task = synthetic_task_contract()
        manifest = root / "task.toml"
        manifest.write_text(
            'version = 2\ntask_id = "INDEPENDENT_SIBLING"\nproject = "demo"\n'
            'workspace_mode = "git_worktree"\nresult_mode = "keep_worktree"\nrisk = "medium"\n'
            'max_fix_cycles = 2\nmax_replan_cycles = 1\nturn_timeout_seconds = 60\nrequire_clean_git = true\n'
            f'prompt = {json.dumps(task["raw_user_request"])}\n'
            '[[checks]]\nname = "Reader A regression"\nfeedback = "repair"\ncommand = ["{python}", "-B", "regression.py"]\ntimeout_seconds = 30\n',
            encoding="utf-8",
        )
        run_root = root / "run"
        observed = {"planner_calls": 0, "implementer_threads": [], "phase_a": [], "green_before_gap": False, "finding": None}
        test = self

        class Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id):
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        def planner(*_args, **kwargs):
            observed["planner_calls"] += 1
            plan = synthetic_plan()
            if observed["planner_calls"] == 1:
                plan["summary"] = "The value reader needs expiration-aware eligibility."
                plan["diagnosis"]["high_level_approach"] = ["Update eligibility and the value reader."]
                plan["affected_consumers"] = plan["affected_consumers"][:1]
                closure = plan["impact_closure"]
                closure["in_scope_consumers"] = closure["in_scope_consumers"][:1]
                closure["search_evidence"][0]["evidence_paths"].remove("reader_b.py")
                closure["search_evidence"][0]["conclusion"] = "The value reader uses eligibility."
                closure["closure_summary"] = "The predicate and known value reader were inspected."
                plan["evidence_plan"]["consumers"] = plan["evidence_plan"]["consumers"][:1]
            else:
                test.assertEqual(collect_changed_paths(Path(kwargs["workspace"])), [])
            return plan

        def implementer(*_args, **kwargs):
            workspace = Path(kwargs["workspace"])
            observed["implementer_threads"].append(kwargs["thread_id"])
            continuing = len(observed["implementer_threads"]) > 1
            contract = kwargs["implementation_contract"]
            if continuing and not repair and not model_conflict:
                observed["expanded_items"] = copy.deepcopy(contract["items"])
                report = attach_post_patch_impact({
                    "protocol_version": "implementer.v4", "status": "BLOCKED", "summary": "The new reader obligation remains unresolved.",
                    "reason": "The material sibling defect has not been corrected.", "evidence": ["reader_b.py still reads active alone."],
                    "contract_evidence": [], "self_verification": {"status": "NOT_RUN", "command": "", "evidence": [], "receipt_id": ""},
                    "additional_check_paths": [], "registered_checks": [], "discovered_obligations": [], "blockers": [],
                }, plan=kwargs["plan"], changed_paths=collect_changed_paths(workspace))
                return report
            (workspace / "state.py").write_text("def is_current(entry):\n    return entry['active'] and entry['expires_at'] > entry['now']\n", encoding="utf-8")
            (workspace / "reader_a.py").write_text("from state import is_current\n\ndef read_value(entry):\n    return entry['value'] if is_current(entry) else None\n", encoding="utf-8")
            if continuing:
                test.assertFalse(Path(kwargs["stamp_path"]).exists())
                (workspace / "reader_b.py").write_text("from state import is_current\n\ndef can_read(entry):\n    return bool(is_current(entry))\n", encoding="utf-8")
                (workspace / "regression.py").write_text(files["regression.py"] + "from reader_b import can_read\nassert not can_read(entry)\nassert can_read(dict(entry, expires_at=3))\n", encoding="utf-8")
            subprocess.run(list(kwargs["self_verify_command"]), cwd=workspace, check=True, capture_output=True)
            test.assertTrue(task_runner.verify_self_verification_stamp(
                workspace=workspace, stamp_path=kwargs["stamp_path"], control_plane=kwargs["control_plane"],
                run_state=kwargs["run_state"], check_registry_digest=kwargs["check_registry_digest"],
            ))
            discoveries = []
            if continuing and not model_conflict:
                finding = observed["finding"]
                discoveries = [{"kind": "consumer", "name": finding["title"], "reason": finding["failure_mode"], "required_behavior": finding["required_action"], "required_proof": finding["required_proof"], "evidence": finding["evidence"]}]
            report = attach_post_patch_impact({
                "protocol_version": "implementer.v4", "status": "COMPLETE", "summary": "Configured entry regression passed.",
                "reason": "", "evidence": [], "blockers": [], "additional_check_paths": [], "registered_checks": [],
                "discovered_obligations": discoveries,
                "contract_evidence": [{"item_id": row["id"], "status": "VERIFIED", "evidence": ["Configured regression passed."]} for row in contract["items"]],
                "self_verification": {"status": "PASS", "command": "self", "evidence": ["SELF_VERIFY_PASS"], "receipt_id": ""},
            }, plan=kwargs["plan"], changed_paths=collect_changed_paths(workspace))
            for row in report["post_patch_impact"]["in_scope_consumers"]:
                if row["source"] == "DISCOVERED":
                    row.update(paths=["reader_b.py"], symbols=["can_read"])
            return report

        class RepositoryEvaluatorServer:
            """A repository-driven agent double; the Controller/validators are real."""
            def __init__(self, *_args, **_kwargs):
                self.thread_count = 0
                self.workspaces = {}
                self.audits = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def start_thread(self, **kwargs):
                self.thread_count += 1
                thread = f"thread-{self.thread_count}"
                self.workspaces[thread] = Path(kwargs["cwd"])
                return thread

            def run_turn(self, **kwargs):
                workspace = self.workspaces[kwargs["thread_id"]]
                prompt = kwargs["prompt"]
                if kwargs["output_schema"] is BLIND_AUDIT_SCHEMA:
                    test.assertNotIn("NORMALIZED PLANNER IMPACT", prompt)
                    test.assertNotIn("CONTROLLER-NORMALIZED IMPLEMENTATION IMPACT", prompt)
                    if not observed["phase_a"]:
                        test.assertNotIn("reader_b.py", prompt)
                    readers = []
                    # Discover reader functions from repository files, not from
                    # either prior impact artifact or a Phase A prompt hint.
                    for path in sorted(workspace.glob("reader_*.py")):
                        for node in ast.parse(path.read_text(encoding="utf-8")).body:
                            if isinstance(node, ast.FunctionDef):
                                readers.append((path.name, node.name))
                    probe = "import json, importlib; entry=dict(active=True, expires_at=1, now=2, value='payload'); print(json.dumps([bool(getattr(importlib.import_module(path[:-3]), name)(entry)) for path,name in " + repr(readers) + "]))"
                    result = subprocess.run([sys.executable, "-B", "-c", probe], cwd=workspace, check=True, capture_output=True, text=True, encoding="utf-8")
                    broken = json.loads(result.stdout)
                    audit = {
                        "protocol_version": "blind-audit.v2", "candidate_id": build_candidate_identity(workspace).candidate_id,
                        "summary": "Independently traced entry eligibility through repository readers.", "findings": [], "advisories": [],
                        "impact_analysis": {
                            "applicable": True,
                            "changed_contracts": [{"impact_id": "CONTRACT-1", "name": "Public entry visibility", "before": "An active entry was visible regardless of expiry.", "after": "Expiry must prevent visibility.", "paths": ["state.py"], "symbols": ["is_current"], "evidence": ["The predicate diff adds expiration handling."]}],
                            "affected_consumers": [{"impact_id": f"CONSUMER-{i}", "name": f"Independently observed {name}", "paths": [path], "symbols": [name], "relation": "Reads entry eligibility through predicate or direct active field.", "required_behavior": "Expired entries cannot be exposed.", "evidence": [f"Repository inspection and expired-entry probe reached {path} {name}."]} for i, (path, name) in enumerate(readers, 1)],
                            "not_affected_consumers": [], "related_out_of_scope": [],
                            "changed_path_review": [{"path": path, "observed_role": "Implementation or regression", "impact": "Changes entry eligibility or its assertions.", "evidence": [f"Inspected actual diff for {path}."]} for path in collect_changed_paths(workspace)],
                            "search_evidence": [{"target": "entry active field and eligibility predicate", "method": "Enumerate reader functions, inspect direct field decisions, and probe expired entries through each public reader.", "evidence_paths": ["state.py", *(path for path, _name in readers)], "conclusion": "The outward sweep found all public reader decisions, including a sibling outside the original diff."}],
                            "closure_summary": "The predicate and direct active-field readers were independently explored beyond the changed paths.",
                        },
                    }
                    for (path, name), wrong in zip(readers, broken):
                        if wrong:
                            finding = {"finding_id": "SIBLING-GAP", "severity": "HIGH", "category": "CONSUMER", "title": "Expired entry availability reader", "evidence": [f"{path} {name} returns true for an expired active entry; regression.py asserts only the value reader."], "failure_mode": "The availability reader exposes expired entries by reading active directly.", "required_action": "The availability reader must reject expired entries and preserve fresh availability.", "required_proof": proof("Exercise expired and fresh entries through the availability reader.")}
                            audit["findings"].append(finding)
                    self.audits[kwargs["thread_id"]] = audit
                    observed["phase_a"].append((kwargs["thread_id"], audit["candidate_id"]))
                    return json.dumps(audit)
                audit = self.audits[kwargs["thread_id"]]
                saved = run_root / "controller_private" / f"blind_audit_{len(observed['phase_a']):02d}.json"
                test.assertEqual(json.loads(saved.read_text(encoding="utf-8")), audit)
                def context(label):
                    return json.JSONDecoder().raw_decode(prompt.split(label + ":\n", 1)[1])[0]
                planned = context("NORMALIZED PLANNER IMPACT CLOSURE")
                implemented = context("CONTROLLER-NORMALIZED IMPLEMENTATION IMPACT CLOSURE")
                verdict = valid_pass(blind_audit=audit, planner_impact=planned, implementation_impact=implemented)
                for consumer, disposition in zip(audit["impact_analysis"]["affected_consumers"], verdict["impact_challenge"]["blind_consumer_dispositions"]):
                    disposition["matches"] = [{"source": src, "classification": "IN_SCOPE", "name": row["name"]} for src, ledger in (("PLANNER", planned), ("IMPLEMENTER", implemented["post_patch_impact"])) for row in ledger["in_scope_consumers"] if set(row["paths"]) & set(consumer["paths"])]
                    if not disposition["matches"]:
                        disposition.update(disposition="MISSING", finding_ids=["SIBLING-GAP"])
                if audit["findings"]:
                    deterministic = context("DETERMINISTIC CONTROLLER EVIDENCE")
                    test.assertTrue(all(row["returncode"] == 0 for row in deterministic["checks"]))
                    test.assertEqual(implemented["status"], "PASS")
                    test.assertNotIn("reader_b.py", json.dumps(planned))
                    test.assertNotIn("reader_b.py", json.dumps(implemented))
                    observed["green_before_gap"] = True
                    verdict.update(status="FINDINGS", findings=copy.deepcopy(audit["findings"]))
                    verdict["blind_finding_dispositions"][0]["disposition"] = "RETAINED"
                    if model_conflict:
                        verdict.update(status="REPLAN_REQUIRED", reason="The model omitted direct eligibility authority in a public decision point.")
                        verdict["findings"][0]["category"] = "MODEL"
                        verdict["impact_challenge"]["blind_contract_dispositions"][0].update(disposition="MODEL_CONFLICT", finding_ids=["SIBLING-GAP"])
                    observed["finding"] = copy.deepcopy(verdict["findings"][0])
                return json.dumps(verdict)

        config = {"projects": {"demo": {"repo": str(source), "base_ref": "HEAD", "result_mode": "keep_worktree", "toolchain": {}}}, "workspace": {"root": str(root / "workspaces")}}
        output = io.StringIO()
        with (
            mock.patch.object(task_runner, "RunRecorder", Recorder),
            mock.patch.object(task_runner, "load_local_config", return_value=(config, None)),
            mock.patch.object(task_runner, "CodexAppServer", RepositoryEvaluatorServer),
            mock.patch.object(task_runner, "resolve_codex_cmd", return_value=Path(sys.executable)),
            mock.patch.object(task_runner, "run_task_contract_normalizer", return_value=task),
            mock.patch.object(task_runner, "run_planner", side_effect=planner),
            mock.patch.object(task_runner, "run_implementer_report", side_effect=implementer),
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
        ):
            result = task_runner.main([str(manifest)])
        return result, observed, run_root, output.getvalue()

    def test_missed_sibling_prevents_pass_despite_green_contract_and_tests(self):
        result, seen, root, output = self.run_sibling_case()
        self.assertEqual(result, 2, output)
        self.assertTrue(seen["green_before_gap"])
        self.assertTrue(any(item["source"] == "DISCOVERED" for item in seen["expanded_items"]))
        self.assertEqual(seen["implementer_threads"][0], seen["implementer_threads"][1])
        self.assertFalse((root / "final_acceptance.json").exists())
        self.assertNotIn("HARNESS_TASK_PASS", output)

    def test_consumer_finding_expands_repairs_and_requires_fresh_blind_candidate(self):
        result, seen, root, output = self.run_sibling_case(repair=True)
        self.assertEqual(result, 0, output)
        self.assertTrue(seen["green_before_gap"])
        self.assertEqual(seen["implementer_threads"][0], seen["implementer_threads"][1])
        self.assertEqual(len(seen["phase_a"]), 2)
        self.assertNotEqual(seen["phase_a"][0][0], seen["phase_a"][1][0])
        self.assertNotEqual(seen["phase_a"][0][1], seen["phase_a"][1][1])
        self.assertTrue((root / "implementation_impact_closure_02.json").is_file())
        final = json.loads((root / "evaluation_02.json").read_text(encoding="utf-8"))
        self.assertEqual(final["status"], "PASS")
        self.assertEqual(len(final["impact_challenge"]["implementer_consumer_dispositions"]), 1)

    def test_independent_model_conflict_uses_fresh_semantic_replan(self):
        result, seen, root, output = self.run_sibling_case(model_conflict=True)
        self.assertEqual(result, 0, output)
        self.assertEqual(seen["planner_calls"], 2)
        self.assertNotEqual(seen["implementer_threads"][0], seen["implementer_threads"][1])
        self.assertEqual(len(seen["phase_a"]), 2)
        self.assertTrue((root / "replan_01_reset.json").is_file())


if __name__ == "__main__":
    unittest.main()
