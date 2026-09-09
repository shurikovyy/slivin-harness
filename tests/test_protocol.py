from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from slivin_harness.evaluator import EVALUATOR_SCHEMA
from slivin_harness.phase6 import BLIND_AUDIT_VERSION
from slivin_harness.implementer import (
    IMPLEMENTER_PROTOCOL_VERSION,
    IMPLEMENTER_REPORT_SCHEMA,
    build_implementation_contract,
    materialize_post_patch_impact,
)
from slivin_harness.planner import PLANNER_SCHEMA
from slivin_harness.protocol import (
    ArtifactContractError,
    EVALUATOR_PROTOCOL_VERSION,
    PLANNER_PROTOCOL_VERSION,
    plan_fingerprint,
    safe_repo_relative,
)
from slivin_harness.task_contract import (
    TASK_CONTRACT_VERSION,
    build_task_contract,
    validate_task_contract,
)
from slivin_harness.verification import (
    VERIFICATION_PLAN_VERSION,
    compile_verification_plan,
)
from task_runner import (
    build_implementation_prompt,
    validate_evaluation_artifact,
    validate_plan_artifact,
)


def empty_post_patch_impact() -> dict:
    return {
        "applicable": False, "changed_contracts": [], "in_scope_consumers": [],
        "not_affected_consumers": [], "related_out_of_scope": [], "new_risks": [],
        "changed_path_review": [], "search_evidence": [], "closure_summary": "", "source_assessments": [],
    }


def attach_post_patch_impact(report: dict, *, plan: dict | None, changed_paths: list[str], contract=None) -> dict:
    """Test client: observations are independent prose; inherited text is not output."""
    from slivin_harness.source_records import source_ref
    from slivin_harness.protocol import stable_fingerprint
    contract = contract or build_implementation_contract(plan, task_contract=valid_task_contract())
    report.setdefault("terminal_reason_kind", {
        "COMPLETE": "NONE", "REPLAN_REQUIRED": "TECHNICAL_MODEL_DIVERGENCE",
        "BLOCKED": "INFRASTRUCTURE_BLOCKED", "NEEDS_USER_DECISION": "USER_DECISION_REQUIRED",
    }[report["status"]])
    report.setdefault("reason", "")
    report.setdefault("evidence", [])
    report.setdefault("registered_checks", [])
    if "self_verification" in report:
        report["self_verification"].setdefault("receipt_id", "")
    discoveries = report.pop("discovered_obligations", [])
    closure = empty_post_patch_impact()
    report["post_patch_impact"] = closure
    if report["status"] != "COMPLETE":
        return report
    model = (plan or valid_plan())["impact_closure"]
    closure["applicable"] = model["applicable"]
    for record in contract["source_inventory"]["records"]:
        claim = record["claim"]
        closure["source_assessments"].append({
            "source_ref": source_ref(record), "disposition": "CONFIRM", "promotion_id": "",
            "observation": "Current repository inspection supports the recorded classification and behavior.",
            "paths": list(claim.get("paths", claim.get("evidence_paths", []))),
            "symbols": list(claim.get("symbols", claim.get("evidence_symbols", []))),
            "evidence": ["Final repository definitions and their callers were inspected in this attempt."],
        })
    for assessment in closure["source_assessments"]:
        event = next((event for event in contract["source_inventory"]["transitions"] if event["source_ref"] == assessment["source_ref"]), None)
        if event is not None:
            assessment.update(disposition="PROMOTE", promotion_id=event["target_ref"]["source_id"].removeprefix("I-" + contract["source_inventory"]["namespace"] + "-"))
    known = {record["source_id"] for record in contract["source_inventory"]["records"]}
    def add(group, row):
        row = copy.deepcopy(row)
        row.pop("source", None)
        row.setdefault("observation_id", "OBS-" + stable_fingerprint([group, row]))
        identifier = "I-" + contract["source_inventory"]["namespace"] + "-" + row["observation_id"]
        if identifier not in known:
            closure[group].append(row)
    if plan is None and not contract["source_inventory"]["records"]:
        for group in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope"):
            for item in model[group]:
                row = copy.deepcopy(item)
                if group == "changed_contracts":
                    row["paths"] = row.pop("evidence_paths")
                    row["symbols"] = row.pop("evidence_symbols")
                    row["evidence"] = ["The requested behavior is implemented in the current definitions."]
                add(group, row)
    for item in discoveries:
        row = dict(name=item["name"], paths=["reader.py"], symbols=["read_target"],
                   evidence=item["evidence"], required_proof=item["required_proof"])
        if item["kind"] == "consumer":
            row.update(why_affected=item["reason"], required_behavior=item["required_behavior"])
            add("in_scope_consumers", row)
        else:
            row.update(reason=item["reason"], failure_mode=item["required_behavior"])
            add("new_risks", row)
    closure["changed_path_review"] = [{
        "path": path, "role": "DOCUMENTATION" if not closure["applicable"] else "IMPLEMENTATION",
        "reason": "This file implements or verifies the requested behavior.",
        "evidence": [f"The complete current diff of {path} has been inspected."],
    } for path in changed_paths]
    closure["search_evidence"] = [{
        "target": row["target"], "method": "Trace definitions, writers and all reachable consumers in the actual repository.",
        "evidence_paths": list(row["evidence_paths"]),
        "conclusion": "Each reachable consumer was inspected against the current candidate.",
    } for row in model["search_evidence"]]
    closure["closure_summary"] = "The actual patch and reachable consumers were examined; observations and changed paths are accounted for."
    if not closure["applicable"]:
        closure["closure_summary"] = "README.md remains explanatory prose with no executable state, runtime contract or behavioral obligations after the editorial correction."
    return report


def proof(claim: str, *, level: str = "LOCAL_DETERMINISTIC", capabilities=None) -> dict:
    return {
        "claim": claim,
        "level": level,
        "capabilities": list(capabilities or []),
    }


def valid_task_contract(raw: str = "Change target.txt from before to after. Do not change other files.") -> dict:
    normalized = {
        "protocol_version": TASK_CONTRACT_VERSION,
        "status": "READY",
        "summary": "Change the target value and preserve all other files.",
        "explicit_intent": [
            {"claim": "Change target.txt from before to after.", "source_text": "Change target.txt from before to after."}
        ],
        "explicit_acceptance": [
            {"claim": "target.txt contains after.", "source_text": "Change target.txt from before to after."}
        ],
        "explicit_preservation": [
            {"claim": "Do not change other files.", "source_text": "Do not change other files."}
        ],
        "explicit_forbidden": [],
        "owner_boundaries": [],
        "non_goals": [],
        "ambiguities": [],
        "reason": "",
    }
    return build_task_contract(raw_request=raw, normalized=normalized)


def valid_plan() -> dict:
    return {
        "protocol_version": PLANNER_PROTOCOL_VERSION,
        "status": "READY",
        "summary": "The old fixture value is the complete root cause.",
        "task_contract_alignment": {
            "status": "ALIGNED",
            "evidence": ["The raw request and explicit Task Contract both require after."],
            "reason": "",
        },
        "characterization": {
            "observed_behavior": ["target.txt contains before."],
            "existing_contract": ["The task requires target.txt to contain after."],
            "evidence": ["target.txt is tracked and contains before."],
        },
        "diagnosis": {
            "kind": "BUG",
            "root_cause": {
                "claim": "The tracked fixture contains the old value.",
                "evidence": ["target.txt contains before."],
                "confidence": "HIGH",
            },
            "extension_point": {"claim": "", "evidence": [], "confidence": "LOW"},
            "design_constraints": ["Preserve all other files."],
            "high_level_approach": ["Replace only the old fixture value."],
        },
        "assumptions": [],
        "technical_contract": {
            "technical_acceptance": ["target.txt contains exactly after."],
            "derived_preservation": ["No sibling file is changed."],
        },
        "impact_closure": {
            "applicable": True,
            "changed_contracts": [{
                "name": "Target read value",
                "before": "read_target returns before.",
                "after": "read_target returns after.",
                "evidence_paths": ["reader.py", "target.txt"],
                "evidence_symbols": ["read_target"],
            }],
            "in_scope_consumers": [{
                "name": "Target fixture consumer",
                "paths": ["reader.py"],
                "symbols": ["read_target"],
                "why_affected": "It reads target.txt.",
                "required_behavior": "It observes after.",
                "evidence": ["reader.py read_target reads target.txt directly."],
                "required_proof": proof("The configured target-content check passes."),
            }],
            "not_affected_consumers": [],
            "related_out_of_scope": [],
            "search_evidence": [{
                "target": "read_target",
                "method": "Inspect the target reader and its file dependency.",
                "evidence_paths": ["reader.py", "target.txt"],
                "conclusion": "read_target is the only repository reader of the target value.",
            }],
            "closure_summary": "The fixture writer and the only reader were examined; no other target dependencies exist.",
        },
        "state_model": {
            "applicable": False,
            "representations": [],
            "authority": [],
            "lifecycle": [],
            "boundaries": [],
            "required_proof": proof("No state model is required."),
        },
        "risks": [
            {
                "condition": "Another file is changed.",
                "failure_mode": "The explicit preservation contract is violated.",
                "required_proof": proof("Only target.txt changes."),
            }
        ],
        "evidence_plan": {
            "regression": [proof("target.txt contains after.")],
            "preservation": [proof("Only target.txt changes.")],
            "consumers": [proof("The target fixture consumer observes after.")],
            "boundaries": [],
        },
        "documentation": {
            "required": False,
            "reason": "No documented product contract changes.",
            "required_proof": proof("No documentation update is required."),
        },
        "owner_boundary_assessment": {
            "compatible": True,
            "reason": "The task can be completed inside target.txt.",
        },
        "unknowns": [],
    }


def write_plan_evidence(workspace: Path) -> None:
    (workspace / "target.txt").write_text("before\n", encoding="utf-8")
    (workspace / "reader.py").write_text(
        "from pathlib import Path\n\ndef read_target():\n"
        "    return Path('target.txt').read_text(encoding='utf-8').strip()\n",
        encoding="utf-8",
    )


def evaluator_finding(finding_id: str = "BLIND-1") -> dict:
    return {
        "finding_id": finding_id,
        "severity": "MEDIUM",
        "category": "EVIDENCE",
        "title": "Regression evidence is incomplete",
        "evidence": ["The configured test does not execute the production reader."],
        "failure_mode": "A false-green test could accept a broken candidate.",
        "required_action": "Add evidence through the real production path.",
        "required_proof": proof("The production reader observes the changed value."),
    }


def valid_blind_audit(*, findings=None, candidate_id="candidate-1", changed_paths=None) -> dict:
    paths = ["target.txt"] if changed_paths is None else changed_paths
    return {
        "protocol_version": BLIND_AUDIT_VERSION,
        "candidate_id": candidate_id,
        "summary": "Independent candidate audit completed.",
        "impact_analysis": {
            "applicable": True,
            "changed_contracts": [{
                "impact_id": "CONTRACT-1", "name": "Observable reader value",
                "before": "The public reader returns before.", "after": "The public reader returns after.",
                "paths": ["reader.py", "target.txt"], "symbols": ["read_target"],
                "evidence": ["reader.py reads the changed target.txt value."],
            }],
            "affected_consumers": [{
                "impact_id": "CONSUMER-1", "name": "Public value reader",
                "paths": ["reader.py"], "symbols": ["read_target"],
                "relation": "The reader observes the changed file value.", "required_behavior": "Return after.",
                "evidence": ["The public read_target path consumes target.txt."],
            }],
            "not_affected_consumers": [], "related_out_of_scope": [],
            "changed_path_review": [{"path": path, "observed_role": "Implementation", "impact": "Updates the observable value.", "evidence": [f"The diff for {path} was inspected."]} for path in paths],
            "search_evidence": [{"target": "read_target", "method": "Trace file reads through the public reader and inspect sibling callers.", "evidence_paths": ["reader.py", "target.txt"], "conclusion": "The public reader is material to the value change."}],
            "closure_summary": "The changed value was traced through actual readers and plausible siblings independently of implementation declarations.",
        },
        "findings": list(findings or []),
        "advisories": [],
    }


_DEFAULT_PLAN = object()


def implementation_impact_fixture(*, candidate_id="candidate-1", plan=_DEFAULT_PLAN, contract=None, changed_paths=None, revision_binding=None) -> dict:
    from slivin_harness.protocol import stable_fingerprint
    plan = valid_plan() if plan is _DEFAULT_PLAN else plan
    contract = contract or build_implementation_contract(plan, task_contract=valid_task_contract())
    paths = ["target.txt"] if changed_paths is None else changed_paths
    report = attach_post_patch_impact({"status": "COMPLETE"}, plan=plan, changed_paths=paths, contract=contract)
    artifact = {
        "schema_version": "implementation-impact-closure.v2", "status": "PASS",
        "candidate_id": candidate_id, "plan_fingerprint": plan_fingerprint(plan) if plan is not None else None,
        "implementation_contract_fingerprint": contract["fingerprint"], "changed_paths": sorted(paths),
        "post_patch_impact": materialize_post_patch_impact(report, contract=contract, plan=plan),
        "source_inventory": copy.deepcopy(contract["source_inventory"]),
        "source_assessments": copy.deepcopy(report["post_patch_impact"]["source_assessments"]),
        "revision_binding": revision_binding or {},
    }
    artifact["fingerprint"] = stable_fingerprint(artifact, length=64)
    return artifact


def valid_pass(*, blind_audit=None, planner_impact=None, implementation_impact=None) -> dict:
    audit = blind_audit or valid_blind_audit()
    planner_impact = valid_plan()["impact_closure"] if planner_impact is None else planner_impact
    implementation_impact = implementation_impact or implementation_impact_fixture(candidate_id=audit["candidate_id"])
    planner_impact = copy.deepcopy(planner_impact)
    from slivin_harness.source_records import source_ref
    for group in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope"):
        planner_impact[group] = [dict(copy.deepcopy(record["claim"]), source_ref=source_ref(record))
            for record in implementation_impact["source_inventory"]["records"]
            if record["author"] == "PLANNER" and record["group"] == group]
    sources = {"BLIND": audit["impact_analysis"], "PLANNER": planner_impact, "IMPLEMENTER": implementation_impact["post_patch_impact"]}
    sources["IMPLEMENTER"] = copy.deepcopy(sources["IMPLEMENTER"])
    promoted_ids = {row["source_ref"]["source_id"] for row in implementation_impact["source_inventory"]["transitions"]}
    for origin in implementation_impact["source_inventory"]["records"]:
        if origin["author"] != "PLANNER" and origin["source_id"] in promoted_ids:
            sources["IMPLEMENTER"][origin["group"]].append(dict(copy.deepcopy(origin["claim"]), source_ref=source_ref(origin)))

    def disposition(status, paths, **reference):
        return dict(reference, disposition=status, reason="Independent repository inspection confirms this classification.", evidence_paths=list(paths), evidence=["The actual consumer and candidate behavior were inspected."], finding_ids=[])

    challenge = {}
    for group, input_group, status in (
        ("blind_contract_dispositions", "changed_contracts", "COVERED"),
        ("blind_consumer_dispositions", "affected_consumers", "COVERED_IN_SCOPE"),
    ):
        targets = planner_impact.get("changed_contracts" if input_group == "changed_contracts" else "in_scope_consumers", [])
        source = "PLANNER"
        if not targets:
            targets = sources["IMPLEMENTER"]["changed_contracts" if input_group == "changed_contracts" else "in_scope_consumers"]
            source = "IMPLEMENTER"
        challenge[group] = [dict(
            disposition(status, row["paths"], impact_id=row["impact_id"]),
            matches=[{"source": source, "classification": "CHANGED_CONTRACT" if input_group == "changed_contracts" else "IN_SCOPE", "reference": targets[0]["source_ref"]["source_id"], "source_revision": targets[0]["source_ref"]["source_revision"]}] if targets else [],
        ) for row in sources["BLIND"][input_group]]
    challenge["planner_consumer_dispositions"] = [disposition("CONFIRMED", row["paths"], reference=row["source_ref"]["source_id"], source_revision=row["source_ref"]["source_revision"]) for row in planner_impact.get("in_scope_consumers", [])]
    challenge["implementer_consumer_dispositions"] = [disposition("CONFIRMED", row["paths"], reference=row["source_ref"]["source_id"], source_revision=row["source_ref"]["source_revision"]) for row in sources["IMPLEMENTER"]["in_scope_consumers"] if row["source"] == "DISCOVERED"]
    for group, input_group, status in (
        ("not_affected_dispositions", "not_affected_consumers", "CONFIRMED_NOT_AFFECTED"),
        ("related_follow_up_dispositions", "related_out_of_scope", "CONFIRMED_OUT_OF_SCOPE"),
    ):
        challenge[group] = [disposition(status, row["paths"], source=source, reference=row["impact_id"] if source == "BLIND" else row["source_ref"]["source_id"], source_revision="" if source == "BLIND" else row["source_ref"]["source_revision"]) for source, ledger in sources.items() for row in ledger.get(input_group, [])]
    evidence_paths = sources["BLIND"]["search_evidence"][0]["evidence_paths"]
    promotions = {row["source_ref"]["source_id"] for row in implementation_impact["source_inventory"]["transitions"]}
    for group in ("not_affected_dispositions", "related_follow_up_dispositions"):
        for row in challenge[group]:
            if row["source"] != "BLIND" and row["reference"] in promotions:
                row["disposition"] = "PROMOTED_IN_SCOPE"
    challenge["changed_path_dispositions"] = [disposition("UNDERSTOOD", evidence_paths, path=row["path"]) for row in sources["BLIND"]["changed_path_review"]]
    challenge["coverage_summary"] = "Independent contracts, readers, sibling classifications and all changed paths were compared with repository evidence."
    return {
        "protocol_version": EVALUATOR_PROTOCOL_VERSION,
        "candidate_id": audit["candidate_id"],
        "impact_challenge": challenge,
        "status": "PASS",
        "summary": "The task is satisfied and checks cover the changed contract.",
        "blind_finding_dispositions": [
            {
                "finding_id": item["finding_id"],
                "disposition": "DISMISSED_WITH_EVIDENCE",
                "evidence": ["Repository evidence disproves the original reachability claim."],
            }
            for item in audit["findings"]
        ],
        "findings": [],
        "reason": "",
    }


class ProtocolContractTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="slivin-protocol-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        write_plan_evidence(self.workspace)
        self.task_contract = valid_task_contract()
        self.evaluation_context = {
            "workspace": self.workspace, "candidate_id": "candidate-1", "changed_paths": ["target.txt"],
            "planner_impact_closure": valid_plan()["impact_closure"],
            "implementation_impact_closure": implementation_impact_fixture(),
        }

    def test_protocol_versions_are_explicit(self) -> None:
        self.assertEqual(PLANNER_SCHEMA["properties"]["protocol_version"]["enum"], [PLANNER_PROTOCOL_VERSION])
        self.assertEqual(EVALUATOR_SCHEMA["properties"]["protocol_version"]["enum"], [EVALUATOR_PROTOCOL_VERSION])
        self.assertEqual(IMPLEMENTER_REPORT_SCHEMA["properties"]["protocol_version"]["enum"], [IMPLEMENTER_PROTOCOL_VERSION])
        self.assertEqual(TASK_CONTRACT_VERSION, "task-contract.v1")
        self.assertEqual(VERIFICATION_PLAN_VERSION, "verification-plan.v1")

    def test_task_contract_requires_exact_source_text(self) -> None:
        validate_task_contract(self.task_contract)
        broken = valid_task_contract()
        broken["explicit_acceptance"][0]["source_text"] = "not in raw"
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_task_contract(broken)
        self.assertEqual(ctx.exception.code, "TASK_CONTRACT_SOURCE_MISMATCH")

    def test_compact_ready_plan_is_accepted(self) -> None:
        plan = valid_plan()
        validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(len(plan_fingerprint(plan)), 16)

    def test_ready_plan_can_keep_non_blocking_unknowns(self) -> None:
        plan = valid_plan()
        plan["unknowns"] = [{"kind": "NON_BLOCKING", "claim": "Browser unavailable", "reason": "Local proof is sufficient."}]
        validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)

    def test_ready_plan_requires_root_cause_evidence(self) -> None:
        plan = valid_plan()
        plan["diagnosis"]["root_cause"]["evidence"] = []
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(ctx.exception.code, "PLANNER_DIAGNOSIS_MISSING")

    def test_feature_uses_extension_point_instead_of_root_cause(self) -> None:
        plan = valid_plan()
        plan["diagnosis"].update({
            "kind": "FEATURE",
            "root_cause": {"claim": "", "evidence": [], "confidence": "LOW"},
            "extension_point": {"claim": "Existing target loader", "evidence": ["loader reads target.txt"], "confidence": "HIGH"},
        })
        validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)

    def test_compatibility_narrowing_assumption_requires_high_confidence(self) -> None:
        plan = valid_plan()
        plan["assumptions"] = [{
            "claim": "Legacy state never occurs",
            "evidence": ["Only the new writer was found"],
            "confidence": "MEDIUM",
            "narrows_compatibility": True,
            "compatibility_impact": "Legacy state would be rejected.",
        }]
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(ctx.exception.code, "UNSAFE_COMPATIBILITY_ASSUMPTION")

    def test_blocked_plan_requires_a_concrete_reason(self) -> None:
        plan = valid_plan()
        plan["status"] = "BLOCKED"
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(ctx.exception.code, "PLANNER_STOP_WITHOUT_REASON")

    def test_unknown_plan_fields_are_rejected(self) -> None:
        plan = valid_plan()
        plan["release_obligations"] = ["CC-1"]
        with self.assertRaises(ArtifactContractError) as ctx:
            validate_plan_artifact(plan, workspace=self.workspace, task_contract=self.task_contract)
        self.assertEqual(ctx.exception.code, "UNKNOWN_FIELDS")

    def test_pass_is_mechanically_strict(self) -> None:
        audit = valid_blind_audit()
        validate_evaluation_artifact(valid_pass(blind_audit=audit), blind_audit=audit, **self.evaluation_context)

        finding = evaluator_finding()
        audit = valid_blind_audit(findings=[finding])
        evaluation = valid_pass(blind_audit=audit)
        evaluation["blind_finding_dispositions"][0]["disposition"] = "RETAINED"
        evaluation["findings"] = [finding]
        with self.assertRaisesRegex(RuntimeError, "PASS requires no findings"):
            validate_evaluation_artifact(evaluation, blind_audit=audit, **self.evaluation_context)

    def test_findings_status_requires_a_finding(self) -> None:
        audit = valid_blind_audit()
        evaluation = valid_pass(blind_audit=audit)
        evaluation["status"] = "FINDINGS"
        with self.assertRaisesRegex(RuntimeError, "requires at least one finding"):
            validate_evaluation_artifact(evaluation, blind_audit=audit, **self.evaluation_context)

    def test_phase_b_must_disposition_every_blind_finding(self) -> None:
        audit = valid_blind_audit(findings=[evaluator_finding()])
        evaluation = valid_pass(blind_audit=audit)
        evaluation["blind_finding_dispositions"] = []
        with self.assertRaisesRegex(RuntimeError, "disposition every"):
            validate_evaluation_artifact(evaluation, blind_audit=audit, **self.evaluation_context)

    def test_implementation_handoff_contains_task_contract_and_verification_plan(self) -> None:
        plan = valid_plan()
        contract = build_implementation_contract(plan, task_contract=self.task_contract)
        verification = compile_verification_plan(contract, project_checks=[{"name": "Target"}])
        prompt = build_implementation_prompt(
            self.task_contract["raw_user_request"],
            plan,
            task_contract=self.task_contract,
            implementation_contract=contract,
            verification_plan=verification,
            self_verify_command=["python", ".harness_tmp/self_verify.py"],
            toolchain={"project_python": "python"},
            allowed_paths=["target.txt"],
        )
        self.assertIn("BEGIN USER TASK CONTRACT", prompt)
        self.assertIn("BEGIN COMPACT PLAN CONTEXT", prompt)
        self.assertIn("BEGIN IMPLEMENTATION CONTRACT", prompt)
        self.assertIn("BEGIN VERIFICATION PLAN", prompt)
        self.assertIn(plan_fingerprint(plan), prompt)
        self.assertIn('"target.txt"', prompt)
        self.assertNotIn("release_obligations", prompt)

    def test_safe_repo_relative_rejects_escape(self) -> None:
        self.assertEqual(safe_repo_relative("src/file.py"), "src/file.py")
        with self.assertRaises(ArtifactContractError):
            safe_repo_relative("../../secret")


if __name__ == "__main__":
    unittest.main()
