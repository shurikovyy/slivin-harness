from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from slivin_harness.implementer import build_implementation_contract
from slivin_harness.planner import PLANNER_SCHEMA, run_planner, validate_plan_artifact
from slivin_harness.protocol import ArtifactContractError
from slivin_harness.task_contract import TASK_CONTRACT_VERSION, build_task_contract
from slivin_harness.verification import compile_verification_plan, merged_required_proof
from test_protocol import proof, valid_plan


def synthetic_task_contract() -> dict:
    intent = "Expired entries must not be returned."
    preservation = "Preserve fresh reads."
    return build_task_contract(
        raw_request=f"{intent} {preservation}",
        normalized={
            "protocol_version": TASK_CONTRACT_VERSION, "status": "READY",
            "summary": intent,
            "explicit_intent": [{"claim": intent, "source_text": intent}],
            "explicit_acceptance": [{"claim": intent, "source_text": intent}],
            "explicit_preservation": [{"claim": preservation, "source_text": preservation}],
            "explicit_forbidden": [], "owner_boundaries": [], "non_goals": [],
            "ambiguities": [], "reason": "",
        },
    )


def synthetic_plan() -> dict:
    plan = valid_plan()
    plan["summary"] = "Validity must include expiration before either reader uses an entry."
    plan["task_contract_alignment"]["evidence"] = ["The user requires expiration handling and fresh-read preservation."]
    plan["characterization"] = {
        "observed_behavior": ["Expired active entries remain readable."],
        "existing_contract": ["Only current entries may be returned."],
        "evidence": ["state.py is_current checks active alone."],
    }
    plan["diagnosis"]["root_cause"].update(
        claim="is_current ignores expires_at.", evidence=["state.py checks only active."]
    )
    plan["diagnosis"]["design_constraints"] = ["Keep fresh entries readable."]
    plan["diagnosis"]["high_level_approach"] = ["Enforce expiration in the shared validity predicate and verify both readers."]
    plan["technical_contract"] = {
        "technical_acceptance": ["Expired entries cannot be returned or reported readable."],
        "derived_preservation": ["Active fresh entries remain readable."],
    }
    consumers = [{
        "name": name, "paths": [path], "symbols": [symbol],
        "why_affected": f"{symbol} branches on is_current before {action}.",
        "required_behavior": behavior,
        "evidence": [f"{path} imports is_current and uses it in {symbol}."],
        "required_proof": proof(behavior),
    } for name, path, symbol, action, behavior in (
        ("Value reader", "reader_a.py", "read_value", "returning a value", "Expired values are not returned; fresh values are returned."),
        ("Availability reader", "reader_b.py", "can_read", "reporting availability", "Expired entries are unavailable; fresh entries are available."),
    )]
    plan["affected_consumers"] = [{
        "name": row["name"], "why_affected": row["why_affected"],
        "must_verify": row["required_behavior"], "required_proof": copy.deepcopy(row["required_proof"]),
    } for row in consumers]
    plan["impact_closure"] = {
        "applicable": True,
        "changed_contracts": [{
            "name": "Entry validity", "before": "active alone determines validity.",
            "after": "active and expires_at determine validity.",
            "evidence_paths": ["state.py"], "evidence_symbols": ["is_current", "expires_at"],
        }],
        "in_scope_consumers": consumers,
        "not_affected_consumers": [{
            "name": "Display title", "paths": ["sibling.py"], "symbols": ["title"],
            "why_considered": "A sibling presents entry metadata.",
            "reason": "title returns static text without reading validity or entries.",
            "evidence": ["sibling.py title has no parameters and returns a literal."],
        }],
        "related_out_of_scope": [{
            "name": "Empty count ratio", "paths": ["metrics.py"], "symbols": ["ratio"],
            "relation": "The reporting sibling also processes entry counts.",
            "reason": "Zero division is independent of expiration and does not affect either reader.",
            "evidence": ["metrics.py ratio divides by total without a zero guard."],
            "suggested_follow_up": "Define and test ratio behavior for an empty count.",
        }],
        "search_evidence": [{
            "target": "is_current and entry validity readers/writers",
            "method": "Trace predicate imports, entry fields and sibling entry consumers.",
            "evidence_paths": ["state.py", "reader_a.py", "reader_b.py", "sibling.py", "metrics.py"],
            "conclusion": "Both decision points use the predicate; static title and count ratio are independent.",
        }],
        "closure_summary": "The shared predicate, both decision points and plausible siblings were inspected and classified; entries are supplied by callers.",
    }
    plan["risks"] = []
    plan["evidence_plan"] = {
        "regression": [proof("Both readers reject expired entries.")],
        "preservation": [proof("Both readers accept active fresh entries.")],
        "consumers": [copy.deepcopy(row["required_proof"]) for row in consumers],
        "boundaries": [],
    }
    plan["owner_boundary_assessment"]["reason"] = "No owner file boundary was specified."
    return plan


class PlannerImpactClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="slivin-impact-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        files = {
            "state.py": "def is_current(entry):\n    return entry['active']  # expires_at is ignored\n",
            "reader_a.py": "from state import is_current\n\ndef read_value(entry):\n    return entry['value'] if is_current(entry) else None\n",
            "reader_b.py": "from state import is_current\n\ndef can_read(entry):\n    return bool(is_current(entry))\n",
            "sibling.py": "def title():\n    return 'Entries'\n",
            "metrics.py": "def ratio(count, total):\n    return count / total\n",
            "README.md": "# Notes\n\nA short explanatory paragraph with a typo.\n",
        }
        for path, content in files.items():
            (self.workspace / path).write_text(content, encoding="utf-8")
        self.task_contract = synthetic_task_contract()
        self.plan = synthetic_plan()

    def validate(self, plan: dict | None = None, *, owner_allowed_paths: list[str] | None = None) -> None:
        context = {} if owner_allowed_paths is None else {"owner_allowed_paths": owner_allowed_paths}
        validate_plan_artifact(plan or self.plan, workspace=self.workspace, task_contract=self.task_contract, **context)

    def reject(self, code: str | None = None, *, owner_allowed_paths: list[str] | None = None) -> None:
        with self.assertRaises(ArtifactContractError) as raised:
            self.validate(owner_allowed_paths=owner_allowed_paths)
        if code:
            self.assertEqual(raised.exception.code, code)

    def test_valid_ready_covers_multiple_concrete_consumers(self) -> None:
        self.validate()

    def test_ready_requires_changed_contracts(self) -> None:
        self.plan["impact_closure"]["changed_contracts"] = []
        self.reject("IMPACT_CONTRACTS_MISSING")

    def test_ready_requires_search_evidence(self) -> None:
        self.plan["impact_closure"]["search_evidence"] = []
        self.reject("IMPACT_SEARCH_MISSING")

    def test_all_impact_path_groups_reject_missing_and_escaping_paths(self) -> None:
        for group, key in (("changed_contracts", "evidence_paths"), ("in_scope_consumers", "paths"),
                           ("not_affected_consumers", "paths"), ("related_out_of_scope", "paths"),
                           ("search_evidence", "evidence_paths")):
            for path in ("missing.py", "../outside.py", "..\\outside.py", "C:outside.py", "C:/outside.py",
                         "/outside.py", "\\\\server\\share\\file.py", ".", "", "reader_a.py:stream"):
                with self.subTest(group=group, path=path):
                    self.plan = synthetic_plan()
                    self.plan["impact_closure"][group][0][key] = [path]
                    self.reject()

    def test_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="slivin-impact-outside-") as outside:
            target = Path(outside) / "external.py"
            target.write_text("external = True\n", encoding="utf-8")
            link = self.workspace / "linked"
            if os.name == "nt":
                # Directory junctions exercise real Windows resolution without
                # requiring the symlink privilege or developer mode.
                subprocess.run(
                    ["cmd", "/d", "/c", "mklink", "/J", str(link), outside],
                    check=True, capture_output=True,
                )
            else:
                link.symlink_to(Path(outside), target_is_directory=True)
            self.plan["impact_closure"]["changed_contracts"][0]["evidence_paths"] = ["linked/external.py"]
            self.reject("UNSAFE_PATH")

    def test_in_scope_consumer_cannot_disappear_from_affected_consumers(self) -> None:
        self.plan["affected_consumers"].pop()
        self.reject("IMPACT_CONSUMER_MISMATCH")

    def test_affected_consumer_cannot_be_missing_from_closure(self) -> None:
        self.plan["impact_closure"]["in_scope_consumers"].pop()
        self.reject("IMPACT_CONSUMER_MISMATCH")

    def test_not_affected_requires_concrete_evidence_and_reason(self) -> None:
        for key in ("name", "paths", "symbols", "why_considered", "reason", "evidence"):
            with self.subTest(key=key):
                self.plan = synthetic_plan()
                row = self.plan["impact_closure"]["not_affected_consumers"][0]
                row[key] = [] if isinstance(row[key], list) else " "
                self.reject("IMPACT_EVIDENCE_EMPTY")

    def test_related_follow_up_is_required_and_preserved_in_artifact(self) -> None:
        original = copy.deepcopy(self.plan)
        self.validate()
        build_implementation_contract(self.plan, task_contract=self.task_contract)
        self.assertEqual(json.loads(json.dumps(self.plan)), original)
        for key in ("relation", "reason", "evidence", "suggested_follow_up"):
            for missing in (False, True):
                with self.subTest(key=key, missing=missing):
                    self.plan = synthetic_plan()
                    row = self.plan["impact_closure"]["related_out_of_scope"][0]
                    if missing:
                        del row[key]
                    else:
                        row[key] = [" "] if key == "evidence" else " "
                    self.reject()

    def test_broad_consumer_without_paths_or_symbols_is_rejected(self) -> None:
        for key, value in (("paths", []), ("symbols", []), ("symbols", ["all shared consumers"]), ("symbols", ["..."])):
            with self.subTest(key=key, value=value):
                self.plan = synthetic_plan()
                row = self.plan["impact_closure"]["in_scope_consumers"][0]
                row.update(name="all shared consumers", **{key: value})
                self.reject()

    def test_changed_contract_requires_nonempty_semantics_and_symbols(self) -> None:
        for key, value in (("name", " "), ("before", ""), ("after", ""), ("evidence_symbols", [])):
            with self.subTest(key=key):
                self.plan = synthetic_plan()
                self.plan["impact_closure"]["changed_contracts"][0][key] = value
                self.reject("IMPACT_EVIDENCE_EMPTY")

    def test_duplicate_or_conflicting_classifications_are_rejected(self) -> None:
        self.plan["impact_closure"]["not_affected_consumers"][0]["name"] = "Value reader"
        self.reject("IMPACT_DUPLICATE_CONSUMER")
        self.plan = synthetic_plan()
        self.plan["affected_consumers"].append(copy.deepcopy(self.plan["affected_consumers"][0]))
        self.reject("IMPACT_DUPLICATE_CONSUMER")

    def test_proof_cannot_be_weakened_or_replaced(self) -> None:
        for key, value in (("claim", "A different assertion."), ("level", "LIVE_LOCAL"), ("capabilities", ["JEST"])):
            with self.subTest(key=key):
                self.plan = synthetic_plan()
                self.plan["affected_consumers"][0]["required_proof"][key] = value
                self.reject("IMPACT_PROOF_MISMATCH")

    def test_behavior_cannot_be_dropped_from_compiler_input(self) -> None:
        for key in ("why_affected", "must_verify"):
            with self.subTest(key=key):
                self.plan = synthetic_plan()
                self.plan["affected_consumers"][0][key] = "Different obligation."
                self.reject("IMPACT_BEHAVIOR_MISMATCH")

    def non_applicable_plan(self) -> dict:
        plan = synthetic_plan()
        plan["summary"] = "Correct an editorial typo in README.md."
        plan["diagnosis"]["root_cause"].update(claim="README.md contains a typo.", evidence=["README.md explanatory paragraph has an editorial typo."])
        plan["diagnosis"]["high_level_approach"] = ["Correct the prose spelling."]
        plan["technical_contract"] = {"technical_acceptance": ["README.md spelling is corrected."], "derived_preservation": []}
        plan["characterization"] = {
            "observed_behavior": ["README.md contains a prose typo."],
            "existing_contract": ["The document explains usage."], "evidence": ["README.md explanatory paragraph."],
        }
        plan["affected_consumers"] = []
        plan["evidence_plan"] = {"regression": [proof("README.md spelling is corrected.")], "preservation": [], "consumers": [], "boundaries": []}
        closure = plan["impact_closure"]
        closure.update(applicable=False, changed_contracts=[], in_scope_consumers=[], not_affected_consumers=[], related_out_of_scope=[])
        closure["search_evidence"] = [{
            "target": "README.md prose", "method": "Inspect the explanatory paragraph and repository references.",
            "evidence_paths": ["README.md"], "conclusion": "The prose is not executed or consumed as config or state.",
        }]
        closure["closure_summary"] = "README.md contains only explanatory prose; correcting spelling changes no behavioral contract or reachable program consumer."
        return plan

    def use_prose_task(self) -> None:
        intent = "Correct the prose spelling."
        task = synthetic_task_contract()
        normalized = {key: value for key, value in task.items() if key not in {"raw_user_request", "raw_request_sha256", "fingerprint"}}
        normalized["summary"] = intent
        for key in ("explicit_intent", "explicit_acceptance"):
            normalized[key] = [{"claim": intent, "source_text": intent}]
        normalized["explicit_preservation"] = []
        self.task_contract = build_task_contract(raw_request=intent, normalized=normalized)
        self.plan = self.non_applicable_plan()

    def test_trivial_task_needs_no_fake_consumers_but_requires_specific_explanation(self) -> None:
        self.use_prose_task()
        self.validate(owner_allowed_paths=["README.md"])
        contract = build_implementation_contract(self.plan, task_contract=self.task_contract)
        self.assertFalse(any(row["type"] == "consumer" for row in contract["items"]))
        for summary in ("", "N/A", "README.md", "README.md not applicable"):
            with self.subTest(summary=summary):
                self.plan["impact_closure"]["closure_summary"] = summary
                self.reject(owner_allowed_paths=["README.md"])

    def test_behavioral_change_cannot_bypass_with_false_and_empty_arrays(self) -> None:
        self.plan["impact_closure"] = self.non_applicable_plan()["impact_closure"]
        self.reject("IMPACT_NOT_APPLICABLE_UNJUSTIFIED", owner_allowed_paths=["README.md"])

    def test_fully_sanitized_behavioral_ledger_cannot_self_authorize_non_applicability(self) -> None:
        self.assertIn("Expired entries must not be returned.", self.task_contract["raw_user_request"])
        self.plan = self.non_applicable_plan()
        # Every Planner-owned behavioral declaration is consistently erased;
        # only the Controller retains the real behavioral user Task Contract.
        for group in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope"):
            self.assertEqual(self.plan["impact_closure"][group], [])
        self.assertEqual(self.plan["affected_consumers"], [])
        self.assertEqual(self.plan["risks"], [])
        self.assertFalse(self.plan["state_model"]["applicable"])
        for group in ("representations", "authority", "lifecycle", "boundaries"):
            self.assertEqual(self.plan["state_model"][group], [])
        for group in ("consumers", "boundaries"):
            self.assertEqual(self.plan["evidence_plan"][group], [])
        self.assertEqual(self.plan["evidence_plan"]["regression"][0]["level"], "LOCAL_DETERMINISTIC")
        self.assertEqual(self.plan["evidence_plan"]["regression"][0]["capabilities"], [])
        self.assertGreater(len(self.plan["impact_closure"]["closure_summary"].split()), 12)
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY")

    def test_sanitized_behavioral_ledger_with_explicit_empty_owner_boundary_is_rejected(self) -> None:
        self.plan = self.non_applicable_plan()
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY", owner_allowed_paths=[])

    def test_genuine_prose_task_without_owner_boundary_is_conservatively_rejected(self) -> None:
        self.use_prose_task()
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY")

    def test_long_planner_explanation_does_not_authorize_behavioral_task(self) -> None:
        self.plan = self.non_applicable_plan()
        self.plan["impact_closure"]["closure_summary"] *= 30
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY")

    def test_non_applicable_rejects_mixed_code_directory_glob_and_unsafe_owner_paths(self) -> None:
        self.use_prose_task()
        (self.workspace / "notes").mkdir()
        (self.workspace / "[ab].md").write_text("Literal filename still has glob syntax.\n", encoding="utf-8")
        for paths in (
            ["README.md", "state.py"], ["state.py"], ["notes"], ["missing.md"],
            ["../outside.md"], ["..\\outside.md"], ["C:outside.md"], ["C:/outside.md"],
            ["/outside.md"], ["\\\\server\\share\\notes.md"], ["*.md"], ["notes/**"],
            ["[ab].md"], ["README.md:stream"], ["."], [""], ["README.md", "missing.md"],
        ):
            with self.subTest(paths=paths):
                self.reject(owner_allowed_paths=paths)

    def test_non_applicable_rejects_owner_junction_or_symlink_escape(self) -> None:
        self.use_prose_task()
        with tempfile.TemporaryDirectory(prefix="slivin-owner-outside-") as outside:
            (Path(outside) / "notes.md").write_text("Ordinary prose.\n", encoding="utf-8")
            link = self.workspace / "linked"
            if os.name == "nt":
                subprocess.run(
                    ["cmd", "/d", "/c", "mklink", "/J", str(link), outside],
                    check=True, capture_output=True,
                )
            else:
                link.symlink_to(Path(outside), target_is_directory=True)
            # Search evidence stays valid inside the workspace; it is the owner
            # boundary itself that must reject the escaping canonical target.
            self.reject("UNSAFE_PATH", owner_allowed_paths=["README.md", "linked/notes.md"])

    def test_non_applicable_search_evidence_must_be_subset_of_owner_paths(self) -> None:
        self.use_prose_task()
        (self.workspace / "notes.md").write_text("Ordinary independent prose.\n", encoding="utf-8")
        self.plan["impact_closure"]["search_evidence"][0]["evidence_paths"].append("notes.md")
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY", owner_allowed_paths=["README.md"])
        self.validate(owner_allowed_paths=["README.md", "notes.md"])

    def test_prose_extensions_and_normalized_exact_owner_paths_are_supported(self) -> None:
        self.use_prose_task()
        (self.workspace / "notes").mkdir()
        for extension in (".md", ".rst", ".txt", ".adoc"):
            with self.subTest(extension=extension):
                path = f"notes/guide{extension}"
                (self.workspace / path).write_text("Ordinary explanatory prose.\n", encoding="utf-8")
                self.plan["impact_closure"]["search_evidence"][0]["evidence_paths"] = [path]
                self.plan["impact_closure"]["closure_summary"] = f"{path} contains ordinary explanatory prose with no runtime consumers or behavioral obligations."
                self.validate(owner_allowed_paths=[path.replace("/", "\\")])

    def test_non_applicable_rejects_code_evidence_and_runtime_proofs(self) -> None:
        self.plan = self.non_applicable_plan()
        self.plan["impact_closure"]["search_evidence"][0]["evidence_paths"] = ["state.py"]
        self.reject("IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY", owner_allowed_paths=["README.md"])
        self.plan = self.non_applicable_plan()
        self.plan["evidence_plan"]["regression"][0]["capabilities"] = ["NODE"]
        self.reject("IMPACT_NOT_APPLICABLE_UNJUSTIFIED", owner_allowed_paths=["README.md"])

    def test_owner_boundary_survives_initial_and_corrective_planner_validation(self) -> None:
        from test_planner import _FakeCodex

        self.use_prose_task()
        first = copy.deepcopy(self.plan)
        first["evidence_plan"]["regression"][0]["capabilities"] = ["DOCS_SYNC"]
        codex = _FakeCodex([first, self.plan])
        result = run_planner(
            codex, workspace=self.workspace, task_prompt=self.task_contract["raw_user_request"],
            task_contract=self.task_contract, preflight={"status": "READY"},
            owner_allowed_paths=["README.md"], available_verification_capabilities=[],
            manifest_repair_evidence=[],
        )
        self.assertEqual(result, self.plan)
        self.assertEqual(len(codex.turns), 2)
        contract = build_implementation_contract(result, task_contract=self.task_contract)
        self.assertFalse(any(row["type"] == "consumer" for row in contract["items"]))

    def test_corrective_turn_cannot_replace_owner_authority_with_sanitized_ledger(self) -> None:
        from test_planner import _FakeCodex

        first = synthetic_plan()
        first["evidence_plan"]["regression"][0]["capabilities"] = ["PROJECT_PYTHON"]
        codex = _FakeCodex([first, self.non_applicable_plan()])
        with self.assertRaises(ArtifactContractError) as raised:
            run_planner(
                codex, workspace=self.workspace, task_prompt=self.task_contract["raw_user_request"],
                task_contract=self.task_contract, preflight={"status": "READY"},
                owner_allowed_paths=[], available_verification_capabilities=[], manifest_repair_evidence=[],
            )
        self.assertEqual(raised.exception.code, "IMPACT_NOT_APPLICABLE_OWNER_BOUNDARY")
        self.assertEqual(len(codex.turns), 2)

    def test_blocked_can_preserve_unfinished_impact_without_fake_entries(self) -> None:
        self.plan["status"] = "BLOCKED"
        self.plan["unknowns"] = [{"kind": "BLOCKING", "claim": "Reader reachability unresolved.", "reason": "Additional repository investigation is required."}]
        self.plan["impact_closure"] = {key: [] for key in ("changed_contracts", "in_scope_consumers", "not_affected_consumers", "related_out_of_scope", "search_evidence")}
        self.plan["impact_closure"].update(applicable=True, closure_summary="")
        self.validate()

    def test_contract_compiler_retains_every_in_scope_and_excludes_other_classes(self) -> None:
        self.validate()
        contract = build_implementation_contract(self.plan, task_contract=self.task_contract)
        consumers = [row for row in contract["items"] if row["type"] == "consumer"]
        self.assertEqual(len(consumers), len(self.plan["impact_closure"]["in_scope_consumers"]))
        for source, item in zip(self.plan["impact_closure"]["in_scope_consumers"], consumers):
            self.assertIn(source["name"], item["requirement"])
            self.assertIn(source["required_behavior"], item["requirement"])
            self.assertEqual(item["required_proof"], merged_required_proof([source["required_proof"]], fallback_claim="unused"))
        serialized = json.dumps(contract)
        for group in ("not_affected_consumers", "related_out_of_scope"):
            for source in self.plan["impact_closure"][group]:
                self.assertNotIn(source["name"], serialized)

    def test_large_concrete_ledger_is_not_truncated_by_compactness_limits(self) -> None:
        for index in range(12):
            path, symbol = f"reader_extra_{index}.py", f"read_extra_{index}"
            (self.workspace / path).write_text(
                f"from state import is_current\n\ndef {symbol}(entry):\n"
                f"    return entry['field_{index}'] if is_current(entry) else None\n",
                encoding="utf-8",
            )
            row = {
                "name": symbol, "paths": [path], "symbols": [symbol],
                "why_affected": f"{symbol} reads field_{index} only when is_current permits it.",
                "required_behavior": f"Expired field_{index} values are not returned.",
                "evidence": [f"{path} delegates validity to is_current."],
                "required_proof": proof(f"{symbol} preserves fresh and excludes expired values."),
            }
            self.plan["impact_closure"]["in_scope_consumers"].append(row)
            self.plan["affected_consumers"].append({
                "name": row["name"], "why_affected": row["why_affected"],
                "must_verify": row["required_behavior"], "required_proof": copy.deepcopy(row["required_proof"]),
            })
            self.plan["impact_closure"]["search_evidence"][0]["evidence_paths"].append(path)
        self.validate()
        contract = build_implementation_contract(self.plan, task_contract=self.task_contract)
        self.assertEqual(sum(row["type"] == "consumer" for row in contract["items"]), 14)
        self.assertTrue(contract["warnings"])

    def test_task_contract_planner_contract_workflow_uses_v5_and_read_only(self) -> None:
        outer = self

        class FakeCodex:
            def start_thread(self, **kwargs) -> str:
                outer.assertEqual(kwargs["sandbox"], "read-only")
                return "planner-thread"

            def run_turn(self, **kwargs) -> str:
                outer.assertEqual(kwargs["output_schema"], PLANNER_SCHEMA)
                outer.assertIn("planner.v5", kwargs["prompt"])
                return json.dumps(outer.plan)

        result = run_planner(
            FakeCodex(), workspace=self.workspace, task_prompt=self.task_contract["raw_user_request"],
            task_contract=self.task_contract, preflight={"status": "READY"}, owner_allowed_paths=[],
            available_verification_capabilities=[], manifest_repair_evidence=[],
        )
        contract = build_implementation_contract(result, task_contract=self.task_contract)
        self.assertEqual(contract["task_contract_fingerprint"], self.task_contract["fingerprint"])
        verification = compile_verification_plan(contract, project_checks=[])
        self.assertEqual(verification["required_capabilities"], [])
        self.assertEqual(sum(row["type"] == "consumer" for row in contract["items"]), 2)


if __name__ == "__main__":
    unittest.main()
