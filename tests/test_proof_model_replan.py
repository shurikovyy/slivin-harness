from __future__ import annotations

import contextlib
import copy
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
from slivin_harness.implementer import IMPLEMENTER_PROTOCOL_VERSION, build_implementation_contract, validate_implementation_report
from slivin_harness.planner import PLANNER_INSTRUCTIONS
from test_planner_impact_closure import synthetic_plan, synthetic_task_contract
from test_protocol import attach_post_patch_impact, proof, valid_blind_audit, valid_pass, valid_plan, valid_task_contract, write_plan_evidence


KINDS = ("NONE", "TECHNICAL_MODEL_DIVERGENCE", "PROOF_MODEL_DIVERGENCE", "INFRASTRUCTURE_BLOCKED", "USER_DECISION_REQUIRED")


def report_for(contract, plan, paths, *, status="COMPLETE", kind="NONE"):
    return attach_post_patch_impact({
        "protocol_version": IMPLEMENTER_PROTOCOL_VERSION, "status": status, "terminal_reason_kind": kind,
        "summary": "Entry validity checked against both public readers.",
        "reason": "" if status == "COMPLETE" else "The selected broad suite cannot establish preservation because independent legacy assertions already fail on baseline.",
        "evidence": [] if status == "COMPLETE" else ["legacy_unrelated_tests.py fails with the same assertion on baseline and candidate; it imports neither reader nor shared state."],
        "contract_evidence": [{"item_id": row["id"], "status": "VERIFIED", "evidence": ["Both public readers reject expired entries and retain fresh reads."]} for row in contract["items"]] if status == "COMPLETE" else [],
        "self_verification": {"status": "PASS", "command": "self", "evidence": ["SELF_VERIFY_PASS"], "receipt_id": ""},
        "additional_check_paths": [], "registered_checks": [], "discovered_obligations": [], "blockers": [],
    }, plan=plan, changed_paths=paths, contract=contract)


class TerminalReasonTests(unittest.TestCase):
    def test_every_status_reason_pair(self):
        allowed = {
            ("COMPLETE", "NONE"), ("REPLAN_REQUIRED", "TECHNICAL_MODEL_DIVERGENCE"),
            ("REPLAN_REQUIRED", "PROOF_MODEL_DIVERGENCE"), ("BLOCKED", "INFRASTRUCTURE_BLOCKED"),
            ("NEEDS_USER_DECISION", "USER_DECISION_REQUIRED"),
        }
        with tempfile.TemporaryDirectory(prefix="slivin-terminal-reason-") as root:
            workspace = Path(root)
            write_plan_evidence(workspace)
            plan = valid_plan()
            contract = build_implementation_contract(plan, task_contract=valid_task_contract())
            for status in ("COMPLETE", "REPLAN_REQUIRED", "BLOCKED", "NEEDS_USER_DECISION"):
                for kind in (*KINDS, None, "UNKNOWN", 0):
                    with self.subTest(status=status, kind=kind):
                        report = report_for(contract, plan, [], status=status, kind=kind)
                        if kind is None:
                            del report["terminal_reason_kind"]
                        def validate():
                            validate_implementation_report(report, contract=contract, changed_paths=[], workspace=workspace, plan=plan, self_verification_ok=True)
                        if (status, kind) in allowed:
                            validate()
                        else:
                            with self.assertRaisesRegex(RuntimeError, "terminal_reason_kind"):
                                validate()

    def test_proof_replan_still_requires_reason_and_evidence(self):
        with tempfile.TemporaryDirectory(prefix="slivin-proof-evidence-") as root:
            workspace = Path(root)
            write_plan_evidence(workspace)
            plan = valid_plan()
            contract = build_implementation_contract(plan, task_contract=valid_task_contract())
            for field, empty in (("reason", ""), ("evidence", [])):
                report = report_for(contract, plan, [], status="REPLAN_REQUIRED", kind="PROOF_MODEL_DIVERGENCE")
                report[field] = empty
                report["blockers"] = ["Generic blocked ledger does not identify the invalid proof route."]
                with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, f"concrete {field}"):
                    validate_implementation_report(report, contract=contract, changed_paths=[], workspace=workspace, plan=plan, self_verification_ok=False)

    def test_agent_policy_distinguishes_semantics_proof_and_registration(self):
        self.assertIn("evidence_plan.preservation не расширяет product scope", PLANNER_INSTRUCTIONS)
        self.assertIn("exact suite является green baseline", PLANNER_INSTRUCTIONS)
        self.assertIn("Targeted assertions должны проверять actual affected behavior", PLANNER_INSTRUCTIONS)
        instructions = task_runner.IMPLEMENTER_INSTRUCTIONS
        self.assertIn("terminal_reason_kind=PROOF_MODEL_DIVERGENCE", instructions)
        self.assertIn("не превращай в authoritative registered_checks", instructions)
        self.assertIn("Changed/new regression tests", instructions)
        self.assertIn("Product intent, PRESERVE-1 и owner-configured checks остаются обязательными", instructions)


class ProofModelWorkflowTests(unittest.TestCase):
    def run_case(self, *, owner_legacy_gate=False, broken_reader=False, technical_review=False):
        root = Path(tempfile.mkdtemp(prefix="slivin-proof-replan-"))
        repo = root / "repo"
        repo.mkdir()
        initial = "def is_current(entry):\n    return entry['active']\n"
        fixed = "def is_current(entry):\n    return entry['active'] and entry['expires_at'] > entry['now']\n"
        files = {
            "shared_state.py": initial,
            "reader_a.py": "from shared_state import is_current\n\ndef read_value(entry):\n    return entry['value'] if is_current(entry) else None\n",
            "reader_b.py": "from shared_state import is_current\n\ndef can_read(entry):\n    return bool(is_current(entry))\n",
            "target_tests.py": "from reader_a import read_value\nfrom reader_b import can_read\nentry = dict(active=True, expires_at=1, now=2, value='kept')\nassert read_value(entry) is None\nassert not can_read(entry)\nfresh = dict(entry, expires_at=3)\nassert read_value(fresh) == 'kept'\nassert can_read(fresh)\nprint('TARGET_CONSUMERS_PASS')\n",
            "legacy_unrelated_tests.py": "def legacy_label():\n    return 'old-label'\n\nassert legacy_label() == 'documented-label', 'LEGACY_LABEL_BASELINE_RED'\n",
        }
        for name, text in files.items():
            (repo / name).write_text(text, encoding="utf-8")
        for args in (("init",), ("config", "user.name", "Test"), ("config", "user.email", "test@example.invalid"), ("add", "--all"), ("commit", "-m", "synthetic baseline")):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

        def execute(workspace, *args):
            return subprocess.run([sys.executable, *args], cwd=workspace, capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

        baseline = execute(repo, "legacy_unrelated_tests.py")
        self.assertNotEqual(baseline.returncode, 0)
        self.assertIn("LEGACY_LABEL_BASELINE_RED", baseline.stderr)
        self.assertNotEqual(execute(repo, "target_tests.py").returncode, 0)
        task = synthetic_task_contract()
        broad_claim = "Absolute PASS of target_tests.py and legacy_unrelated_tests.py proves preservation."
        targeted_claim = "target_tests.py executes read_value and can_read for expired and fresh entries."

        def make_plan(attempt):
            plan = json.loads(json.dumps(synthetic_plan()).replace("state.py", "shared_state.py"))
            plan["impact_closure"]["not_affected_consumers"] = []
            plan["impact_closure"]["related_out_of_scope"] = []
            plan["impact_closure"]["search_evidence"][0]["evidence_paths"] = ["shared_state.py", "reader_a.py", "reader_b.py"]
            plan["evidence_plan"]["preservation"] = [proof(broad_claim if attempt == 1 else targeted_claim)]
            if attempt > 1:
                plan["impact_closure"]["related_out_of_scope"] = [{
                    "name": "Legacy label baseline debt", "paths": ["legacy_unrelated_tests.py"], "symbols": ["legacy_label"],
                    "relation": "The broad repository test route includes the independent legacy label assertion.",
                    "reason": "The label failure exists before the entry validity change and does not consume entry state or either reader.",
                    "evidence": ["LEGACY_LABEL_BASELINE_RED occurs on clean baseline and candidate; legacy_label returns a fixed literal with no shared-state imports."],
                    "suggested_follow_up": "Reconcile legacy_label with its documented label and repair its independent regression.",
                }]
            return plan

        manifest = root / "task.toml"
        extra = '\n[[checks]]\nname = "Owner legacy gate"\nfeedback = "repair"\ncommand = ["{python}", "legacy_unrelated_tests.py"]\ntimeout_seconds = 30\n' if owner_legacy_gate else ""
        manifest.write_text('''version = 2
task_id = "PROOF_MODEL_REPLAN"
project = "demo"
workspace_mode = "git_worktree"
result_mode = "keep_worktree"
risk = "medium"
max_fix_cycles = 0
max_replan_cycles = 1
turn_timeout_seconds = 60
require_clean_git = true
prompt = "Expired entries must not be returned. Preserve fresh reads."
[[checks]]
name = "Owner target regressions"
feedback = "repair"
command = ["{python}", "target_tests.py"]
timeout_seconds = 30
''' + extra, encoding="utf-8")
        run_root = root / "run"
        observed = {"planner": [], "proof_reviews": [], "implementer": [], "self_verify": [], "evaluator": 0, "contracts": []}
        test = self

        class Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id):
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        class AgentServer:
            def __init__(self, *_args, **_kwargs):
                self.threads = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def start_thread(self, **kwargs):
                thread = f"thread-{len(self.threads) + 1}"
                self.threads[thread] = kwargs
                return thread

            def retire_readonly_threads(self):
                pass

            def run_turn(self, **kwargs):
                thread = kwargs["thread_id"]
                workspace = Path(self.threads[thread]["cwd"])
                prompt = kwargs["prompt"]
                version = kwargs["output_schema"]["properties"]["protocol_version"]["enum"][0]
                if version == "proof-route-review.v1":
                    observed["proof_reviews"].append(thread)
                    test.assertEqual((workspace / "shared_state.py").read_text(encoding="utf-8"), fixed)
                    test.assertIn("PROOF_MODEL_DIVERGENCE", prompt)
                    test.assertIn("LEGACY_LABEL_BASELINE_RED", prompt)
                    contract = observed["contracts"][-1]
                    if technical_review:
                        return json.dumps(dict(protocol_version=version, status="TECHNICAL_REPLAN_REQUIRED",
                            candidate_id=task_runner.build_candidate_identity(workspace).candidate_id,
                            contract_fingerprint=contract["fingerprint"], reason="The prior technical dependency model omitted direct readers and must be rebuilt.",
                            evidence=["reader_b uses direct eligibility; inspect all callers from clean baseline."], changes=[]))
                    return json.dumps(dict(protocol_version=version, status="READY",
                        candidate_id=task_runner.build_candidate_identity(workspace).candidate_id,
                        contract_fingerprint=contract["fingerprint"],
                        reason="Public reader requirements are unchanged; independent legacy label assertions do not prove them.",
                        evidence=["Actual baseline and candidate legacy assertion match, label has no entry-state dependency; both reader assertions execute."],
                        changes=[dict(item_id="PRESERVE-1", reason="Use both actual reader assertions for preservation.",
                            evidence=["target_tests.py executes fresh and expired entry cases for both readers."], proofs=[proof(targeted_claim)])]))
                if version == "planner.v6":
                    observed["planner"].append((thread, prompt))
                    test.assertEqual(task_runner.collect_changed_paths(workspace), [])
                    test.assertEqual((workspace / "shared_state.py").read_text(encoding="utf-8"), initial)
                    test.assertEqual(execute(workspace, "legacy_unrelated_tests.py").returncode, baseline.returncode)
                    test.assertIn(json.dumps(task, ensure_ascii=False, indent=2), prompt)
                    if len(observed["planner"]) > 1:
                        test.assertIn("TECHNICAL_MODEL_DIVERGENCE" if technical_review else "PROOF_MODEL_DIVERGENCE", prompt)
                        test.assertIn("product intent are unchanged", prompt)
                        test.assertNotIn("REJECTED_SOLUTION_MARKER", prompt)
                        test.assertNotIn(fixed.strip(), prompt)
                        test.assertNotEqual(thread, observed["planner"][0][0])
                    return json.dumps(make_plan(len(observed["planner"])))
                raise AssertionError(f"Unexpected raw agent turn: {version}")

        original_implementer = task_runner.run_implementer_report

        def implementer(server, **kwargs):
            workspace = kwargs["workspace"]
            observed["implementer"].append(kwargs["thread_id"])
            observed["contracts"].append(copy.deepcopy(kwargs["implementation_contract"]))
            if len(observed["implementer"]) == 1 or (technical_review and len(observed["implementer"]) == 2):
                test.assertEqual(task_runner.collect_changed_paths(workspace), [])
            else:
                test.assertIn("shared_state.py", task_runner.collect_changed_paths(workspace))
                test.assertEqual((workspace / "shared_state.py").read_text(encoding="utf-8"), fixed)
            if len(observed["implementer"]) > 1:
                if technical_review:
                    test.assertNotEqual(observed["implementer"][0], kwargs["thread_id"])
                else:
                    test.assertEqual(observed["implementer"][0], kwargs["thread_id"])
                test.assertFalse(kwargs["stamp_path"].exists())
            (workspace / "shared_state.py").write_text(fixed, encoding="utf-8")
            if broken_reader:
                (workspace / "reader_b.py").write_text("def can_read(entry):\n    return bool(entry['active'])\n", encoding="utf-8")
            verification = subprocess.run(list(kwargs["self_verify_command"]), cwd=workspace, capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            observed["self_verify"].append(verification.returncode)
            if not owner_legacy_gate and not broken_reader:
                test.assertEqual(verification.returncode, 0, verification.stdout + verification.stderr)
            legacy = execute(workspace, "legacy_unrelated_tests.py")
            test.assertEqual(legacy.returncode, baseline.returncode)
            test.assertIn("LEGACY_LABEL_BASELINE_RED", legacy.stderr)
            test.assertEqual((workspace / "legacy_unrelated_tests.py").read_text(encoding="utf-8"), files["legacy_unrelated_tests.py"])
            first = len(observed["implementer"]) == 1
            report = report_for(kwargs["implementation_contract"], kwargs["plan"], task_runner.collect_changed_paths(workspace), status="REPLAN_REQUIRED" if first else "COMPLETE", kind="PROOF_MODEL_DIVERGENCE" if first else "NONE")
            if not first and not any(row["group"] == "related_out_of_scope" for row in kwargs["implementation_contract"]["source_inventory"]["records"]):
                report["post_patch_impact"]["related_out_of_scope"].append(dict(
                    observation_id="legacy-label", **make_plan(2)["impact_closure"]["related_out_of_scope"][0]))
            if first:
                report["summary"] = "REJECTED_SOLUTION_MARKER: implementation prose must not enter the next Planner."
                report["reason"] = "Invalid Planner-derived proof route: " + broad_claim
                report["evidence"].append("Baseline and candidate assertion: " + legacy.stderr.splitlines()[-1])
                report["evidence"].append(f"Target/owner self-verification exit={verification.returncode}; candidate changed paths={task_runner.collect_changed_paths(workspace)}; legacy assertion matches the independent baseline failure.")
            # Exercise the real report validator, receipt checks and terminal routing;
            # only the structured agent response is a double.
            with mock.patch.object(task_runner, "run_agent_turn", return_value=json.dumps(report)):
                return original_implementer(server, **kwargs)

        def evaluator(*_args, **kwargs):
            observed["evaluator"] += 1
            audit = valid_blind_audit(candidate_id=kwargs["candidate_id"], changed_paths=kwargs["changed_paths"])
            model = kwargs["plan"]["impact_closure"]
            impact = audit["impact_analysis"]
            impact["changed_contracts"] = [dict(impact_id="CONTRACT-1", name="Independent entry eligibility", before="Active expired entries were readable.", after="Expired entries are rejected.", paths=["shared_state.py"], symbols=["is_current"], evidence=["Actual expiration branch inspected."])]
            impact["affected_consumers"] = [dict(impact_id=f"CONSUMER-{index}", name=f"Independent reader {index}", paths=row["paths"], symbols=row["symbols"], relation=row["why_affected"], required_behavior=row["required_behavior"], evidence=row["evidence"]) for index, row in enumerate(model["in_scope_consumers"], 1)]
            impact["search_evidence"][0]["evidence_paths"] = ["shared_state.py", "reader_a.py", "reader_b.py"]
            kwargs["on_blind_audit"](audit)
            kwargs["on_phase_complete"]("PHASE_A")
            kwargs["on_phase_complete"]("PHASE_B")
            return audit, valid_pass(blind_audit=audit, planner_impact=model, implementation_impact=kwargs["implementation_impact_closure"])

        output = io.StringIO()
        config = {"projects": {"demo": {"repo": str(repo), "base_ref": "HEAD", "result_mode": "keep_worktree"}}, "workspace": {"root": str(root / "workspaces")}}
        with (
            mock.patch.object(task_runner, "RunRecorder", Recorder),
            mock.patch.object(task_runner, "load_local_config", return_value=(config, None)),
            mock.patch.object(task_runner, "CodexAppServer", AgentServer),
            mock.patch.object(task_runner, "resolve_codex_cmd", return_value=Path(sys.executable)),
            mock.patch.object(task_runner, "run_task_contract_normalizer", return_value=task),
            mock.patch.object(task_runner, "run_implementer_report", side_effect=implementer),
            mock.patch.object(task_runner, "run_evaluator", side_effect=evaluator),
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(output),
        ):
            result = task_runner.main([str(manifest)])
        return result, run_root, observed, output.getvalue()

    def test_baseline_red_proof_preserves_candidate_and_delivers_follow_up(self):
        result, root, observed, output = self.run_case()
        self.assertEqual(result, 0, output)
        self.assertEqual(len(observed["planner"]), 1)
        self.assertEqual(len(observed["proof_reviews"]), 1)
        self.assertEqual(len(observed["implementer"]), 3)
        self.assertEqual(observed["self_verify"], [0, 0, 0])
        self.assertEqual(observed["evaluator"], 1)
        self.assertFalse((root / "replan_01_reset.json").exists())
        self.assertTrue((root / "proof_route_review_01.json").is_file())
        preserves = [next(item for item in contract["items"] if item["id"] == "PRESERVE-1") for contract in observed["contracts"]]
        self.assertEqual(preserves[0]["requirement"], preserves[1]["requirement"])
        self.assertEqual(preserves[0]["required_proof"], preserves[1]["required_proof"])
        self.assertEqual(observed["contracts"][0]["proof_routes"], [])
        self.assertEqual(observed["contracts"][1]["proof_routes"][0]["item_id"], "PRESERVE-1")
        registries = list(root.rglob("check_registry*.json"))
        self.assertTrue(list(root.glob("verification_plan*.json")))
        for registry in registries:
            self.assertNotIn("legacy_unrelated_tests.py", registry.read_text(encoding="utf-8"))
        handoff = json.loads((root / "user_follow_up_report.json").read_text(encoding="utf-8"))
        self.assertEqual(handoff["count"], 1)
        self.assertEqual(handoff["follow_ups"][0]["review_status"], "CONFIRMED_OUT_OF_SCOPE")
        self.assertTrue((root / "final_acceptance.json").is_file())
        candidate = (root / "candidate.patch").read_text(encoding="utf-8")
        self.assertNotIn("legacy_unrelated_tests.py", candidate)
        self.assertIn("HARNESS_TASK_PASS", output)

    def test_owner_baseline_red_gate_remains_hard_after_proof_replan(self):
        result, root, observed, output = self.run_case(owner_legacy_gate=True)
        self.assertNotEqual(result, 0, output)
        self.assertEqual(len(observed["planner"]), 1)
        self.assertTrue(all(code != 0 for code in observed["self_verify"]))
        self.assertEqual(observed["evaluator"], 0)
        self.assertFalse((root / "final_acceptance.json").exists())
        self.assertIn("trusted self-verification PASS", output)

    def test_independent_technical_review_uses_semantic_reset_and_completes(self):
        result, root, observed, output = self.run_case(technical_review=True)
        self.assertEqual(result, 0, output)
        self.assertEqual(len(observed["planner"]), 2)
        self.assertEqual(len(observed["proof_reviews"]), 1)
        self.assertTrue((root / "replan_01_reset.json").is_file())
        self.assertFalse((root / "replan_02_reset.json").exists())
        self.assertTrue((root / "final_acceptance.json").is_file())

    def test_affected_reader_failure_is_not_excused_by_independent_baseline_debt(self):
        result, root, observed, output = self.run_case(broken_reader=True)
        self.assertNotEqual(result, 0, output)
        self.assertTrue(all(code != 0 for code in observed["self_verify"]))
        self.assertFalse((root / "final_acceptance.json").exists())
        self.assertIn("trusted self-verification PASS", output)
