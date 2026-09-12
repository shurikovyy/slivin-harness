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
    BLIND_AUDIT_SCHEMA, EVALUATOR_SCHEMA, admit_evaluation_artifact,
    build_evaluator_schema, build_phase_b_origin_catalog, run_evaluator,
    validate_blind_audit, validate_evaluation_artifact,
)
from slivin_harness.implementer import build_implementation_contract, build_implementation_impact_closure
from slivin_harness.output_schema import validate_strict_output_schema
from slivin_harness.phase6 import BLIND_AUDIT_VERSION
from slivin_harness.protocol import ArtifactContractError, ArtifactFailureKind
from slivin_harness.run_state import build_candidate_identity
from slivin_harness.verification import compile_verification_plan
from task_runner import collect_changed_paths
from test_implementer import git
from test_planner_impact_closure import synthetic_plan, synthetic_task_contract
from test_protocol import attach_post_patch_impact, evaluator_finding, phase_b_wire, proof, valid_pass


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
            "protocol_version": "implementer.v6", "status": "COMPLETE", "summary": "Entry reads corrected.",
            "reason": "", "evidence": [], "blockers": [], "additional_check_paths": [], "registered_checks": [],
            "discovered_obligations": [],
            "contract_evidence": [{"item_id": row["id"], "status": "VERIFIED", "evidence": ["The configured regression passed."]} for row in self.contract["items"]],
            "self_verification": {"status": "PASS", "command": "self", "evidence": ["SELF_VERIFY_PASS"], "receipt_id": ""},
        }, plan=self.plan, changed_paths=self.changed_paths, contract=self.contract)
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

    def admit_wire(self, wire, *, catalog=None):
        catalog = catalog or build_phase_b_origin_catalog(
            self.audit, self.plan["impact_closure"], self.impact,
        )
        return admit_evaluation_artifact(
            wire, origin_catalog=catalog, blind_audit=self.audit,
            workspace=self.workspace, candidate_id=self.candidate_id,
            changed_paths=self.changed_paths,
            planner_impact_closure=self.plan["impact_closure"],
            implementation_impact_closure=self.impact,
        )

    def fixture_wire(self, filename):
        fixture = json.loads((Path(__file__).parent / "fixtures/model_artifact_admission" / filename).read_text(encoding="utf-8"))
        catalog = build_phase_b_origin_catalog(
            self.audit, self.plan["impact_closure"], self.impact,
        )
        def bind(value):
            if isinstance(value, str):
                if value == "@CANDIDATE@":
                    return self.candidate_id
                if value.startswith("@ORIGIN-") and value.endswith("@"):
                    return catalog["origins"][int(value[8:-1])]["origin_ref"]
                return value
            if isinstance(value, list):
                return [bind(item) for item in value]
            if isinstance(value, dict):
                return {key: bind(item) for key, item in value.items()}
            return value
        return fixture, bind(fixture["wire_artifact"]), catalog

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
        if group == "blind_contract_dispositions":
            finding["category"] = "MODEL"
        self.verdict.update(status="REPLAN_REQUIRED" if group == "blind_contract_dispositions" else "FINDINGS", findings=[finding], reason="The independently inspected candidate has a material impact gap.")

    def run_phases(self, *, observer=None, persist=None, impact=None, responses=None, on_raw_report=None, run_name="run"):
        current_impact = impact or self.impact

        def as_wire(value):
            if not isinstance(value, dict) or "impact_challenge" not in value:
                return value
            rows = [row for group, group_rows in value["impact_challenge"].items()
                    if group != "coverage_summary" and group != "changed_path_dispositions"
                    for row in group_rows]
            if rows and "origin_ref" in rows[0]:
                return value
            return phase_b_wire(
                evaluation=value, blind_audit=self.audit,
                planner_impact=self.plan["impact_closure"], implementation_impact=current_impact,
            )

        server = ScriptedEvaluator(self.audit, as_wire(self.verdict), observer)
        if responses is not None:
            server.responses = [as_wire(value) for value in responses]
        plane = ControllerPlane(self.root / run_name)

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
            plan=self.plan, implementation_impact_closure=current_impact, revision_binding=self.binding,
            on_blind_audit=persist or persist_blind, on_phase_complete=guard,
            on_origin_catalog=lambda catalog: plane.write_json_once(
                "origin_catalog.json", catalog, visibility=ArtifactVisibility.PRIVATE,
            ),
            on_raw_report=on_raw_report,
        )
        return server, plane, result

    def test_phase_a_batch_correction_never_discloses_phase_b_early(self):
        for index in range(2, 26):
            self.audit["impact_analysis"]["related_out_of_scope"].append({
                "impact_id": f"RELATED-{index}", "name": f"Navigation finding {index}",
                "paths": ["README.md"], "symbols": ["README.md#notes"],
                "relation": "Repository prose navigation", "reason": "Navigation has no effect on entry reads.",
                "evidence": ["README.md heading inspected independently."],
                "suggested_follow_up": f"Review navigation entry {index}.",
            })
        self.verdict = self.pass_verdict()
        invalid = copy.deepcopy(self.audit)
        for row in invalid["impact_analysis"]["related_out_of_scope"]:
            row["symbols"] = []
        raw_records = []
        def observe(turn, options):
            if turn <= 2:
                for marker in ("CONTRACT_CLOSURE_SECRET", "CHECKS_SECRET", self.impact["fingerprint"]):
                    self.assertNotIn(marker, options["prompt"])
                self.assertFalse((self.root / "run/controller_private/blind.json").exists())
            if turn == 2:
                self.assertEqual(len(json.loads(options["prompt"].splitlines()[-1])["allowed_fields"]), 25)
            if turn == 3:
                self.assertTrue((self.root / "run/controller_private/blind.json").exists())
        server, _, (_, verdict) = self.run_phases(
            responses=[invalid, self.audit, self.verdict], observer=observe,
            on_raw_report=lambda phase, attempt, raw: raw_records.append((phase, attempt, raw)),
        )
        self.assertEqual(len(server.prompts), 3)
        self.assertEqual(verdict["status"], "PASS")
        self.assertEqual([(phase, attempt) for phase, attempt, _ in raw_records], [("PHASE_A", 0), ("PHASE_A", 1), ("PHASE_B", 0)])

    def test_phase_b_corrects_unknown_origin_ref_in_one_bounded_turn(self):
        corrected = phase_b_wire(
            evaluation=self.verdict, blind_audit=self.audit,
            planner_impact=self.plan["impact_closure"], implementation_impact=self.impact,
        )
        invalid = copy.deepcopy(corrected)
        invalid["impact_challenge"]["blind_contract_dispositions"][0]["matches"][0]["origin_ref"] = "ORIGIN-unknown"
        changed_field = "impact_challenge.blind_contract_dispositions[0].matches[0].origin_ref"
        server, _, (_, verdict) = self.run_phases(
            responses=[self.audit, invalid, corrected],
            run_name="origin-ref-correction",
        )
        self.assertEqual(verdict["status"], "PASS")
        self.assertEqual(len(server.prompts), 3)
        diagnostic = json.loads(server.prompts[-1].splitlines()[-1])
        self.assertEqual(diagnostic["allowed_fields"], [changed_field])
        self.assertEqual(diagnostic["code"], "ORIGIN_REF_UNKNOWN")

    def test_phase_b_origin_catalog_and_dynamic_schema_own_identity_metadata(self):
        catalog = build_phase_b_origin_catalog(
            self.audit, self.plan["impact_closure"], self.impact,
        )
        self.assertEqual(catalog["schema_version"], "phase-b-origin-catalog.v1")
        self.assertEqual(len({row["origin_ref"] for row in catalog["origins"]}), len(catalog["origins"]))
        schema = build_evaluator_schema(catalog)
        validate_strict_output_schema(schema)
        challenge = schema["properties"]["impact_challenge"]["properties"]
        contract_matches = challenge["blind_contract_dispositions"]["items"]["properties"]["matches"]["items"]["properties"]
        consumer_matches = challenge["blind_consumer_dispositions"]["items"]["properties"]["matches"]["items"]["properties"]
        self.assertEqual(set(contract_matches), {"origin_ref"})
        self.assertEqual(set(consumer_matches), {"origin_ref"})
        self.assertTrue(set(contract_matches["origin_ref"]["enum"]).isdisjoint(consumer_matches["origin_ref"]["enum"]))
        self.assertNotIn("source_revision", json.dumps(schema))
        self.assertNotIn('"classification"', json.dumps(schema))

    def test_dynamic_schema_exact_cardinality_and_zero_origin_groups(self):
        catalog = build_phase_b_origin_catalog(
            self.audit, self.plan["impact_closure"], self.impact,
        )
        challenge = build_evaluator_schema(catalog)["properties"]["impact_challenge"]["properties"]
        expected_wire = phase_b_wire(
            evaluation=self.verdict, blind_audit=self.audit,
            planner_impact=self.plan["impact_closure"], implementation_impact=self.impact,
        )["impact_challenge"]
        for group in (
            "blind_contract_dispositions", "blind_consumer_dispositions",
            "planner_consumer_dispositions", "implementer_consumer_dispositions",
            "not_affected_dispositions", "related_follow_up_dispositions",
        ):
            rows = challenge[group]
            actual = self.verdict["impact_challenge"][group]
            self.assertEqual((rows["minItems"], rows["maxItems"]), (len(actual), len(actual)))
            if actual:
                self.assertEqual(
                    set(rows["items"]["properties"]["origin_ref"]["enum"]),
                    {row["origin_ref"] for row in expected_wire[group]},
                )
        self.assertEqual(challenge["implementer_consumer_dispositions"]["maxItems"], 0)
        self.assertNotIn("enum", challenge["implementer_consumer_dispositions"]["items"]["properties"]["origin_ref"])

    def test_zero_compatible_matches_have_no_wire_slot(self):
        catalog = build_phase_b_origin_catalog(
            self.audit, self.plan["impact_closure"], self.impact,
        )
        blind_only = dict(catalog, origins=[row for row in catalog["origins"] if row["authority"] == "BLIND"])
        challenge = build_evaluator_schema(blind_only)["properties"]["impact_challenge"]["properties"]
        for group in ("blind_contract_dispositions", "blind_consumer_dispositions"):
            matches = challenge[group]["items"]["properties"]["matches"]
            self.assertEqual(matches["maxItems"], 0)
            self.assertNotIn("enum", matches["items"]["properties"]["origin_ref"])
        full = build_evaluator_schema(catalog)["properties"]["impact_challenge"]["properties"]
        self.assertEqual(
            set(full["blind_contract_dispositions"]["items"]["properties"]["matches"]["items"]["properties"]["origin_ref"]["enum"]),
            {row["origin_ref"] for row in catalog["origins"] if row["authority"] != "BLIND" and row["classification"] == "CHANGED_CONTRACT"},
        )

    def test_zero_origin_row_is_semantic_not_impossible_local_correction(self):
        wire = phase_b_wire(
            evaluation=self.verdict, blind_audit=self.audit,
            planner_impact=self.plan["impact_closure"], implementation_impact=self.impact,
        )
        wire["impact_challenge"]["implementer_consumer_dispositions"] = [{
            "origin_ref": "ORIGIN-unknown", "disposition": "CONFIRMED",
            "reason": "Invented row", "evidence_paths": ["reader_a.py"],
            "evidence": ["Invented evidence"], "finding_ids": [],
        }]
        with self.assertRaises(ArtifactContractError) as raised:
            self.admit_wire(wire)
        self.assertEqual(raised.exception.code, "ORIGIN_DISPOSITION_CARDINALITY")
        self.assertIs(raised.exception.failure_kind, ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT)
        turns = []
        with self.assertRaises(ArtifactContractError) as raised:
            self.run_phases(
                responses=[self.audit, wire], run_name="zero-origin-no-correction",
                observer=lambda turn, _options: turns.append(turn),
            )
        self.assertEqual(raised.exception.code, "ORIGIN_DISPOSITION_CARDINALITY")
        self.assertEqual(turns, [1, 2])

    def test_exact_origin_ref_resolution_derives_controller_metadata(self):
        catalog = build_phase_b_origin_catalog(
            self.audit, self.plan["impact_closure"], self.impact,
        )
        wire = phase_b_wire(
            evaluation=self.verdict, blind_audit=self.audit,
            planner_impact=self.plan["impact_closure"], implementation_impact=self.impact,
        )
        admitted = self.admit_wire(wire, catalog=catalog)
        wire_row = wire["impact_challenge"]["planner_consumer_dispositions"][0]
        origin = next(row for row in catalog["origins"] if row["origin_ref"] == wire_row["origin_ref"])
        row = admitted["impact_challenge"]["planner_consumer_dispositions"][0]
        self.assertEqual(row["reference"], origin["source_id"])
        self.assertEqual(row["source_revision"], origin["source_revision"])

    def test_incompatible_handle_is_not_canonicalized_into_positive_coverage(self):
        catalog = build_phase_b_origin_catalog(
            self.audit, self.plan["impact_closure"], self.impact,
        )
        wire = phase_b_wire(
            evaluation=self.verdict, blind_audit=self.audit,
            planner_impact=self.plan["impact_closure"], implementation_impact=self.impact,
        )
        contract_handle = next(row["origin_ref"] for row in catalog["origins"]
                               if row["authority"] != "BLIND" and row["classification"] == "CHANGED_CONTRACT")
        wire["impact_challenge"]["blind_consumer_dispositions"][0]["matches"][0] = {"origin_ref": contract_handle}
        with self.assertRaises(ArtifactContractError) as raised:
            self.admit_wire(wire, catalog=catalog)
        self.assertEqual(raised.exception.code, "ORIGIN_REF_INCOMPATIBLE")

    def test_tampered_controller_origin_catalog_is_hard_failure(self):
        catalog = build_phase_b_origin_catalog(
            self.audit, self.plan["impact_closure"], self.impact,
        )
        catalog["origins"][0]["source_revision"] = "0" * 64
        wire = phase_b_wire(
            evaluation=self.verdict, blind_audit=self.audit,
            planner_impact=self.plan["impact_closure"], implementation_impact=self.impact,
        )
        with self.assertRaises(ArtifactContractError) as raised:
            self.admit_wire(wire, catalog=catalog)
        self.assertEqual(raised.exception.code, "ORIGIN_CATALOG_TAMPERED")
        self.assertIs(raised.exception.failure_kind, ArtifactFailureKind.INTEGRITY_OR_INFRA_FAILURE)

    def test_origin_ref_correction_cannot_change_semantic_conclusion(self):
        corrected = phase_b_wire(
            evaluation=self.verdict, blind_audit=self.audit,
            planner_impact=self.plan["impact_closure"], implementation_impact=self.impact,
        )
        invalid = copy.deepcopy(corrected)
        invalid["impact_challenge"]["blind_contract_dispositions"][0]["origin_ref"] = "ORIGIN-unknown"
        mutated = copy.deepcopy(corrected)
        mutated["impact_challenge"]["blind_contract_dispositions"][0]["disposition"] = "MATERIAL_GAP"
        mutated["reason"] = "Changed semantic conclusion"
        mutated["findings"] = [evaluator_finding("MUTATED")]
        with self.assertRaisesRegex(RuntimeError, "CHANGED_CLAIMS"):
            self.run_phases(responses=[self.audit, invalid, mutated], run_name="origin-semantic-mutation")

    def test_captured_qe1_invalid_contract_handle_gets_exact_reference_correction(self):
        fixture, invalid, catalog = self.fixture_wire("qe1_expiry_1.json")
        observed = fixture["observed_legacy_shape"]
        self.assertEqual(observed["disposition"], "MISSING")
        self.assertEqual(observed["match_authority_classification"], [
            ["PLANNER", "CHANGED_CONTRACT"], ["IMPLEMENTER", "CHANGED_CONTRACT"],
        ])
        bad_row = invalid["impact_challenge"]["blind_consumer_dispositions"][1]
        self.assertEqual(bad_row["disposition"], observed["disposition"])
        allowed = build_evaluator_schema(catalog)["properties"]["impact_challenge"]["properties"]["blind_consumer_dispositions"]["items"]["properties"]["matches"]["items"]["properties"]["origin_ref"]["enum"]
        self.assertTrue(all(match["origin_ref"] not in allowed for match in bad_row["matches"]))
        partial = copy.deepcopy(invalid)
        valid_handles = [next(row["origin_ref"] for row in catalog["origins"]
                              if row["authority"] == authority and row["classification"] == "IN_SCOPE")
                         for authority in ("PLANNER", "IMPLEMENTER")]
        partial["impact_challenge"]["blind_consumer_dispositions"][1]["matches"][0]["origin_ref"] = valid_handles[0]
        corrected = copy.deepcopy(partial)
        corrected["impact_challenge"]["blind_consumer_dispositions"][1]["matches"][1]["origin_ref"] = valid_handles[1]
        server, _, (_, verdict) = self.run_phases(
            responses=[self.audit, invalid, partial, corrected], run_name="captured-qe1",
        )
        self.assertEqual(verdict["status"], "FINDINGS")
        diagnostics = [json.loads(prompt.splitlines()[-1]) for prompt in server.prompts[2:]]
        self.assertEqual([item["code"] for item in diagnostics], [fixture["expected_route"]] * 2)
        self.assertEqual([item["allowed_fields"] for item in diagnostics], [
            ["impact_challenge.blind_consumer_dispositions[1].matches[0].origin_ref"],
            ["impact_challenge.blind_consumer_dispositions[1].matches[0].origin_ref",
             "impact_challenge.blind_consumer_dispositions[1].matches[1].origin_ref"],
        ])

    def test_captured_qs1_unknown_name_gets_handle_correction_and_no_model_revision(self):
        fixture, invalid, catalog = self.fixture_wire("qs1_suspension_1.json")
        observed = fixture["observed_legacy_shape"]
        self.assertEqual(observed["match_fields"], ["reference", "source_revision", "source", "classification"])
        corrected = copy.deepcopy(invalid)
        corrected["impact_challenge"]["blind_contract_dispositions"][0]["matches"][0]["origin_ref"] = next(
            row["origin_ref"] for row in catalog["origins"]
            if row["authority"] == "PLANNER" and row["classification"] == "CHANGED_CONTRACT"
        )
        server, _, (_, verdict) = self.run_phases(
            responses=[self.audit, invalid, corrected], run_name="captured-qs1",
        )
        self.assertEqual(verdict["status"], "FINDINGS")
        diagnostic = json.loads(server.prompts[-1].splitlines()[-1])
        self.assertEqual(diagnostic["code"], fixture["expected_route"])
        self.assertNotIn("source_revision", json.dumps(corrected))
        admitted = self.admit_wire(corrected)
        self.assertRegex(
            admitted["impact_challenge"]["blind_contract_dispositions"][0]["matches"][0]["source_revision"],
            r"^[0-9a-f]{64}$",
        )

    def test_phase_b_batch_repair_no_progress_and_candidate_write_guards(self):
        invalid = copy.deepcopy(self.verdict)
        rows = invalid['impact_challenge']['related_follow_up_dispositions']
        self.assertGreaterEqual(len(rows), 3)
        for row in rows:
            row['evidence_paths'] = []
        server, _, (_, verdict) = self.run_phases(responses=[self.audit, invalid, self.verdict])
        self.assertEqual(verdict['status'], 'PASS')
        self.assertEqual(len(server.prompts), 3)
        with self.assertRaisesRegex(RuntimeError, 'NO_PROGRESS'):
            self.run_phases(responses=[self.audit, invalid, invalid], run_name='no-progress')
        def mutate(turn, _options):
            if turn == 3:
                (self.workspace / 'state.py').write_text('changed during B correction', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'mutated the candidate'):
            self.run_phases(responses=[self.audit, invalid, self.verdict], observer=mutate, run_name='mutated')

    def test_promoted_origins_require_current_target_and_independent_confirmation(self):
        from slivin_harness.implementer import report_discoveries
        from slivin_harness.phase5 import expand_contract_and_verification_plan
        from slivin_harness.source_records import source_ref, register_observations
        for author in ('PLANNER', 'IMPLEMENTER'):
            with self.subTest(author=author):
                contract = copy.deepcopy(self.contract)
                old = next(row for row in contract['source_inventory']['records'] if row['group'] == 'related_out_of_scope')
                if author == 'IMPLEMENTER':
                    observation = dict(copy.deepcopy(old['claim']), observation_id='independent-outside', name='Additional eligibility consumer')
                    contract['source_inventory'], _ = register_observations(contract['source_inventory'], {'related_out_of_scope':[observation]})
                    from slivin_harness.protocol import stable_fingerprint
                    contract['fingerprint'] = stable_fingerprint({key:value for key,value in contract.items() if key != 'fingerprint'})
                    old = contract['source_inventory']['records'][-1]
                wire = attach_post_patch_impact({'status':'COMPLETE'}, plan=self.plan, changed_paths=self.changed_paths, contract=contract)
                assessment = next(row for row in wire['post_patch_impact']['source_assessments'] if row['source_ref'] == source_ref(old))
                assessment.update(disposition='PROMOTE', promotion_id='promoted-metrics')
                wire['post_patch_impact']['in_scope_consumers'].append(dict(observation_id='promoted-metrics', name=old['claim']['name'], paths=['metrics.py'], symbols=['ratio'],
                    why_affected='Independent investigation finds an eligibility dependency.', required_behavior='Count eligible entries only.', evidence=['metrics.py ratio caller inspected.'],
                    required_proof=proof('Assert eligibility-aware ratio for expired and fresh entries.')))
                expanded = expand_contract_and_verification_plan(implementation_contract=contract,
                    previous_verification_plan=compile_verification_plan(contract, project_checks=[]),
                    discoveries=report_discoveries(wire, contract=contract, plan=self.plan), project_checks=[], task_checks=[], observations=wire['post_patch_impact'])
                self.contract = expanded.implementation_contract
                self.impact = self.build_impact()
                self.verdict = self.pass_verdict()
                self.validate_b()
                original_impact, original_verdict = copy.deepcopy(self.impact), copy.deepcopy(self.verdict)
                target = self.contract['source_inventory']['transitions'][-1]['target_ref']['source_id']
                for change in ('no_transition', 'no_target', 'no_confirmation', 'stale_confirmation'):
                    self.impact, self.verdict = copy.deepcopy(original_impact), copy.deepcopy(original_verdict)
                    if change == 'no_transition': self.impact['source_inventory']['transitions'] = []
                    elif change == 'no_target': self.impact['post_patch_impact']['in_scope_consumers'] = [row for row in self.impact['post_patch_impact']['in_scope_consumers'] if row['source_ref']['source_id'] != target]
                    elif change == 'no_confirmation': self.verdict['impact_challenge']['implementer_consumer_dispositions'] = []
                    else: next(row for row in self.verdict['impact_challenge']['implementer_consumer_dispositions'] if row['reference'] == target)['source_revision'] = '0' * 64
                    self.reject_b()
                self.contract = contract

    def test_good_candidate_all_independent_dispositions_pass(self):
        self.validate_a()
        self.validate_b()
        self.assertNotEqual(self.audit["impact_analysis"]["changed_contracts"][0]["name"], self.plan["impact_closure"]["changed_contracts"][0]["name"])

    def test_phase_a_is_blind_and_phase_b_follows_immutable_persistence(self):
        self.plan["diagnosis"]["high_level_approach"] = ["PLANNER_REASONING_SECRET"]
        self.contract = build_implementation_contract(self.plan, task_contract=synthetic_task_contract())
        self.impact = self.build_impact()
        self.verdict = self.pass_verdict()
        expected_blind = copy.deepcopy(self.audit)

        def inspect(phase, kwargs):
            prompt = kwargs["prompt"]
            self.assertNotIn("PLANNER_REASONING_SECRET", prompt)
            if phase == 1:
                for marker in ("CONTRACT_CLOSURE_SECRET", "CHECKS_SECRET", "RUNTIME_SECRET", self.impact["fingerprint"], self.plan["impact_closure"]["in_scope_consumers"][0]["name"]):
                    self.assertNotIn(marker, prompt)
            else:
                self.assertTrue((self.root / "run/controller_private/blind.json").is_file(), "PHASE_A_NOT_PERSISTED_BEFORE_B")
                saved = json.loads((self.root / "run" / "controller_private" / "blind.json").read_text(encoding="utf-8"))
                self.assertEqual(saved, expected_blind)
                for marker in ("CONTRACT_CLOSURE_SECRET", "CHECKS_SECRET", "RUNTIME_SECRET", self.impact["fingerprint"]):
                    self.assertIn(marker, prompt)

        server, plane, result = self.run_phases(observer=inspect)
        self.assertEqual(server.threads[0]["execution_role"].value, "evaluator")
        self.assertNotIn("sandbox", server.threads[0])
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
        row["matches"] = [{"source": "PLANNER", "classification": "IN_SCOPE", "reference": "Invented-consumer", "source_revision": "0" * 64}]
        self.reject_b("unknown or has a stale")

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

    def assert_blind_contract_status_matrix(self, disposition):
        self.negative("blind_contract_dispositions", disposition)
        for status in ("REPLAN_REQUIRED", "FINDINGS", "BLOCKED", "NEEDS_USER_DECISION", "PASS"):
            with self.subTest(disposition=disposition, status=status):
                self.verdict["status"] = status
                row = self.verdict["impact_challenge"]["blind_contract_dispositions"][0]
                self.assertEqual(row["finding_ids"], [self.verdict["findings"][0]["finding_id"]])
                if status == "REPLAN_REQUIRED":
                    self.validate_b()
                else:
                    self.reject_b("PASS requires no findings" if status == "PASS" else "REPLAN_REQUIRED")

    def test_model_conflict_status_matrix_requires_only_replan(self):
        self.assert_blind_contract_status_matrix("MODEL_CONFLICT")

    def test_material_gap_status_matrix_requires_only_replan(self):
        self.assert_blind_contract_status_matrix("MATERIAL_GAP")

    def test_blind_contract_replan_still_requires_reason_and_material_finding(self):
        for disposition in ("MATERIAL_GAP", "MODEL_CONFLICT"):
            for missing in ("reason", "finding_ids", "findings"):
                with self.subTest(disposition=disposition, missing=missing):
                    self.negative("blind_contract_dispositions", disposition)
                    if missing == "finding_ids":
                        self.verdict["impact_challenge"]["blind_contract_dispositions"][0][missing] = []
                    else:
                        self.verdict[missing] = "" if missing == "reason" else []
                    self.reject_b()

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

    def test_strict_v2_v8_output_schemas(self):
        self.assertEqual(BLIND_AUDIT_SCHEMA["properties"]["protocol_version"]["enum"], ["blind-audit.v2"])
        self.assertEqual(EVALUATOR_SCHEMA["properties"]["protocol_version"]["enum"], ["evaluator.v8"])
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
    def run_sibling_case(self, *, repair=False, contract_gap=None):
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
                closure = plan["impact_closure"]
                closure["in_scope_consumers"] = closure["in_scope_consumers"][:1]
                closure["search_evidence"][0]["evidence_paths"].remove("reader_b.py")
                closure["search_evidence"][0]["conclusion"] = "The value reader uses eligibility."
                closure["closure_summary"] = "The predicate and known value reader were inspected."
                plan["evidence_plan"]["consumers"] = plan["evidence_plan"]["consumers"][:1]
            else:
                test.assertEqual(collect_changed_paths(Path(kwargs["workspace"])), [])
                if contract_gap == "MATERIAL_GAP":
                    plan["impact_closure"]["changed_contracts"].append({
                        "name": "Availability decision authority", "before": "Availability trusts the active field alone.",
                        "after": "Availability uses the expiration-aware predicate.",
                        "evidence_paths": ["reader_b.py"], "evidence_symbols": ["can_read"],
                    })
            return plan

        def implementer(*_args, **kwargs):
            workspace = Path(kwargs["workspace"])
            observed["implementer_threads"].append(kwargs["thread_id"])
            continuing = len(observed["implementer_threads"]) > 1
            contract = kwargs["implementation_contract"]
            if continuing and not repair and not contract_gap:
                observed["expanded_items"] = copy.deepcopy(contract["items"])
                report = attach_post_patch_impact({
                    "protocol_version": "implementer.v6", "status": "BLOCKED", "summary": "The new reader obligation remains unresolved.",
                    "reason": "The material sibling defect has not been corrected.", "evidence": ["reader_b.py still reads active alone."],
                    "contract_evidence": [], "self_verification": {"status": "NOT_RUN", "command": "", "evidence": [], "receipt_id": ""},
                    "additional_check_paths": [], "registered_checks": [], "discovered_obligations": [], "blockers": [],
                }, plan=kwargs["plan"], changed_paths=collect_changed_paths(workspace), contract=contract)
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
            report = attach_post_patch_impact({
                "protocol_version": "implementer.v6", "status": "COMPLETE", "summary": "Configured entry regression passed.",
                "reason": "", "evidence": [], "blockers": [], "additional_check_paths": [], "registered_checks": [],
                "contract_evidence": [{"item_id": row["id"], "status": "VERIFIED", "evidence": ["Configured regression passed."]} for row in contract["items"]],
                "self_verification": {"status": "PASS", "command": "self", "evidence": ["SELF_VERIFY_PASS"], "receipt_id": ""},
            }, plan=kwargs["plan"], changed_paths=collect_changed_paths(workspace), contract=contract)
            evaluator_ids = {row["source_id"] for row in contract["source_inventory"]["records"] if row["author"] == "EVALUATOR"}
            for row in report["post_patch_impact"]["source_assessments"]:
                if row["source_ref"]["source_id"] in evaluator_ids:
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

            def retire_readonly_threads(self):
                pass

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
                        if contract_gap == "MATERIAL_GAP" and name == "can_read":
                            # The blind model identifies a separate decision authority
                            # from repository inspection, before seeing prior ledgers.
                            audit["impact_analysis"]["changed_contracts"].append({
                                "impact_id": "CONTRACT-2", "name": "Availability decision authority",
                                "before": "Availability trusts the active field alone.",
                                "after": "Availability uses the expiration-aware predicate.",
                                "paths": [path], "symbols": [name],
                                "evidence": [f"{path} {name} determines availability independently of the value reader; expiration must govern both decisions."],
                            })
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
                    disposition["matches"] = [{"source": src, "classification": "IN_SCOPE", "reference": row["source_ref"]["source_id"], "source_revision": row["source_ref"]["source_revision"]} for src, ledger in (("PLANNER", planned), ("IMPLEMENTER", implemented["post_patch_impact"])) for row in ledger["in_scope_consumers"] if set(row["paths"]) & set(consumer["paths"])]
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
                    if contract_gap:
                        verdict.update(status="REPLAN_REQUIRED", reason="The model omitted direct eligibility authority in a public decision point.")
                        verdict["findings"][0]["category"] = "MODEL"
                        row = verdict["impact_challenge"]["blind_contract_dispositions"][-1]
                        row.update(disposition=contract_gap, finding_ids=["SIBLING-GAP"])
                        if contract_gap == "MATERIAL_GAP":
                            row["matches"] = []
                            row["reason"] = "The prior model does not cover the separate availability decision authority found in the blind repository sweep."
                            test.assertEqual(row["impact_id"], "CONTRACT-2")
                            test.assertNotIn("Availability decision authority", json.dumps(planned))
                    observed["finding"] = copy.deepcopy(verdict["findings"][0])
                elif contract_gap == "MATERIAL_GAP":
                    verdict["impact_challenge"]["blind_contract_dispositions"][-1]["matches"] = [
                        {"source": "PLANNER", "classification": "CHANGED_CONTRACT", "reference": planned["changed_contracts"][-1]["source_ref"]["source_id"], "source_revision": planned["changed_contracts"][-1]["source_ref"]["source_revision"]},
                    ]
                planner_claims = copy.deepcopy(planned)
                for group in ("changed_contracts", "in_scope_consumers",
                              "not_affected_consumers", "related_out_of_scope"):
                    for row in planner_claims[group]:
                        row.pop("source_ref", None)
                return json.dumps(phase_b_wire(
                    evaluation=verdict, blind_audit=audit,
                    planner_impact=planner_claims, implementation_impact=implemented,
                ))

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

    def test_independent_contract_gaps_use_fresh_semantic_replan(self):
        for disposition in ("MODEL_CONFLICT", "MATERIAL_GAP"):
            with self.subTest(disposition=disposition):
                result, seen, root, output = self.run_sibling_case(contract_gap=disposition)
                self.assertEqual(result, 0, output)
                self.assertEqual(seen["planner_calls"], 2)
                self.assertNotEqual(seen["implementer_threads"][0], seen["implementer_threads"][1])
                self.assertEqual(len(seen["phase_a"]), 2)
                self.assertTrue((root / "replan_01_reset.json").is_file())
                rejected = json.loads((root / "evaluation_01.json").read_text(encoding="utf-8"))
                self.assertEqual(rejected["status"], "REPLAN_REQUIRED")
                self.assertEqual(rejected["impact_challenge"]["blind_contract_dispositions"][-1]["disposition"], disposition)
                if disposition == "MATERIAL_GAP":
                    blind = json.loads((root / "blind_audit_01.json").read_text(encoding="utf-8"))
                    self.assertEqual(blind["impact_analysis"]["changed_contracts"][-1]["impact_id"], "CONTRACT-2")


if __name__ == "__main__":
    unittest.main()
