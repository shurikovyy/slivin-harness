from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from slivin_harness.app_server import TurnTimeoutError
from slivin_harness.control_plane import ControllerPlane
from slivin_harness.implementer import (
    IMPLEMENTER_PROTOCOL_VERSION,
    IMPLEMENTATION_CONTRACT_VERSION,
    build_implementation_contract,
    validate_implementation_report,
)
from slivin_harness.git_integrity import (
    GIT_CONTROL_STATE_MUTATED_DURING_BATCH,
    CandidateWorkspaceBaseline,
    GitControlIntegrityManager,
)
from slivin_harness.run_state import build_candidate_identity
from task_runner import (
    HarnessControlledStop,
    build_dynamic_check_specs,
    build_trusted_check_id_specs,
    candidate_content_fingerprint,
    prepare_self_verify_runner,
    run_implementer_report,
    verify_self_verification_stamp,
)
from slivin_harness.runtime_projection import (
    RUNTIME_PROJECTION_MUTATED_DURING_CHECK,
    RuntimeProjectionIntegrityManager,
)
from slivin_harness.workspace import RuntimeProjection, WorkspaceSession
from test_protocol import proof, valid_plan, valid_task_contract


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", check=True
    )
    return result.stdout.strip()


class ImplementerContractTests(unittest.TestCase):
    def make_repo(self) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="slivin-implementer-"))
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        (repo / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "baseline")
        return repo

    def contract(self, plan=None):
        return build_implementation_contract(
            valid_plan() if plan is None else plan,
            task_contract=valid_task_contract(),
        )

    def test_contract_keeps_user_requirements_and_consumers_explicit(self) -> None:
        contract = self.contract()
        ids = [item["id"] for item in contract["items"]]
        self.assertEqual(ids, ["ACCEPTANCE-1", "PRESERVE-1", "CONSUMER-1", "RISK-1"])
        self.assertEqual(contract["protocol_version"], IMPLEMENTATION_CONTRACT_VERSION)
        self.assertIn("target.txt contains after", contract["items"][0]["requirement"])
        self.assertIn("Do not change other files", contract["items"][1]["requirement"])
        self.assertIn("Target fixture consumer", contract["items"][2]["requirement"])
        self.assertNotIn("EVIDENCE-1", ids)
        self.assertTrue(all("required_proof" in item for item in contract["items"]))

    def test_complete_report_requires_every_contract_item_and_current_self_verify(self) -> None:
        repo = self.make_repo()
        contract = self.contract()
        (repo / "src" / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
        report = {
            "protocol_version": IMPLEMENTER_PROTOCOL_VERSION,
            "status": "COMPLETE",
            "summary": "done",
            "contract_evidence": [
                {"item_id": item["id"], "status": "VERIFIED", "evidence": ["checked"]}
                for item in contract["items"]
            ],
            "self_verification": {"status": "PASS", "command": "self", "evidence": ["PASS"]},
            "additional_check_paths": [],
            "blockers": [],
        }
        validate_implementation_report(
            report, contract=contract, changed_paths=["src/a.py"],
            self_verification_ok=True, documentation_paths=[]
        )
        report["contract_evidence"].pop()
        with self.assertRaisesRegex(RuntimeError, "every Implementation Contract item"):
            validate_implementation_report(
                report, contract=contract, changed_paths=["src/a.py"],
                self_verification_ok=True, documentation_paths=[]
            )

    def test_not_affected_is_only_allowed_for_consumers(self) -> None:
        contract = self.contract()
        report = {
            "protocol_version": IMPLEMENTER_PROTOCOL_VERSION,
            "status": "COMPLETE",
            "summary": "done",
            "contract_evidence": [
                {"item_id": item["id"], "status": "VERIFIED", "evidence": ["checked"]}
                for item in contract["items"]
            ],
            "self_verification": {"status": "PASS", "command": "self", "evidence": ["PASS"]},
            "additional_check_paths": [],
            "blockers": [],
        }
        report["contract_evidence"][0]["status"] = "NOT_AFFECTED"
        with self.assertRaisesRegex(RuntimeError, "cannot be NOT_AFFECTED"):
            validate_implementation_report(
                report, contract=contract, changed_paths=[], self_verification_ok=True,
                documentation_paths=[]
            )
        consumer_index = next(
            i for i, item in enumerate(contract["items"]) if item["type"] == "consumer"
        )
        report["contract_evidence"][0]["status"] = "VERIFIED"
        report["contract_evidence"][consumer_index]["status"] = "NOT_AFFECTED"
        validate_implementation_report(
            report, contract=contract, changed_paths=[], self_verification_ok=True,
            documentation_paths=[]
        )

    def test_planner_risks_are_required_contract_items(self) -> None:
        plan = valid_plan()
        plan["risks"] = [
            {"condition": "A", "failure_mode": "Risk A", "required_proof": proof("Proof A")},
            {"condition": "B", "failure_mode": "Risk B", "required_proof": proof("Proof B")},
        ]
        contract = self.contract(plan)
        risk_items = [item for item in contract["items"] if item["type"] == "risk"]
        self.assertEqual([item["id"] for item in risk_items], ["RISK-1", "RISK-2"])
        self.assertTrue(all(item["allow_not_affected"] is False for item in risk_items))

    def test_contract_soft_threshold_does_not_drop_material_items(self) -> None:
        plan = valid_plan()
        plan["affected_consumers"] = [
            {"name": f"Consumer {i}", "why_affected": "shared", "must_verify": "preserved", "required_proof": proof(f"consumer {i} proof")}
            for i in range(15)
        ]
        contract = self.contract(plan)
        self.assertGreater(len(contract["items"]), 14)
        self.assertTrue(contract["warnings"])
        self.assertEqual(len([x for x in contract["items"] if x["type"] == "consumer"]), 15)

    def test_implementer_timeout_continues_same_thread_once(self) -> None:
        repo = self.make_repo()
        plan = valid_plan()
        contract = self.contract(plan)
        report = {
            "protocol_version": IMPLEMENTER_PROTOCOL_VERSION,
            "status": "BLOCKED",
            "summary": "blocked after continuation",
            "contract_evidence": [
                {"item_id": item["id"], "status": "BLOCKED", "evidence": ["still pending"]}
                for item in contract["items"]
            ],
            "self_verification": {"status": "NOT_RUN", "command": "self", "evidence": ["not run"]},
            "additional_check_paths": [],
            "blockers": ["real blocker"],
        }

        class FakeCodex:
            def __init__(self): self.calls = []
            def run_turn(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise TurnTimeoutError("timeout")
                return json.dumps(report)

        codex = FakeCodex()
        result = run_implementer_report(
            codex, thread_id="thread-1", prompt="initial", timeout=900,
            label="IMPLEMENT", implementation_contract=contract,
            self_verify_command=[sys.executable, "self_verify.py"], workspace=repo,
            stamp_path=repo / "missing-stamp.json", plan=plan,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(codex.calls), 2)
        self.assertEqual(codex.calls[0]["thread_id"], codex.calls[1]["thread_id"])
        self.assertEqual(codex.calls[1]["timeout"], 300)
        self.assertIn("НЕ начинай исследование заново", codex.calls[1]["prompt"])

    def test_self_verify_stamp_is_bound_to_current_candidate(self) -> None:
        repo = self.make_repo()
        (repo / "src" / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
        specs = [{
            "name": "simple", "feedback": "repair",
            "command": ["{python}", "-c", "from pathlib import Path; assert 'VALUE = 2' in Path('src/a.py').read_text()"],
            "timeout_seconds": 30,
        }]
        _, stamp, command = prepare_self_verify_runner(
            workspace=repo, specs=specs, toolchain={"project_python": sys.executable}
        )
        result = subprocess.run(command, cwd=repo, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(verify_self_verification_stamp(workspace=repo, stamp_path=stamp))
        before = candidate_content_fingerprint(repo)
        (repo / "src" / "a.py").write_text("VALUE = 3\n", encoding="utf-8")
        self.assertNotEqual(before, candidate_content_fingerprint(repo))
        self.assertFalse(verify_self_verification_stamp(workspace=repo, stamp_path=stamp))

    def test_mutating_self_verify_cannot_issue_controller_receipt(self) -> None:
        repo = self.make_repo()
        (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-m", "ignore runtime")
        source = repo.parent / (repo.name + "-source")
        source_dep = source / "node_modules" / "dep" / "index.js"
        workspace_dep = repo / "node_modules" / "dep" / "index.js"
        source_dep.parent.mkdir(parents=True)
        workspace_dep.parent.mkdir(parents=True)
        source_dep.write_text("baseline\n", encoding="utf-8")
        workspace_dep.write_text("baseline\n", encoding="utf-8")
        control_plane = ControllerPlane(repo.parent / (repo.name + "-run"))
        manager = RuntimeProjectionIntegrityManager(
            session=WorkspaceSession(
                workspace=repo,
                mode="benchmark_isolated",
                managed=True,
                source_repo=source,
                runtime_projections=(
                    RuntimeProjection(
                        relative_path="node_modules",
                        source_kind="workspace.copy_untracked",
                        destination=repo / "node_modules",
                        is_directory=True,
                        copy_mode="physical_copy",
                        runtime_only=True,
                    ),
                ),
            ),
            control_plane=control_plane,
            retry_delay_seconds=0,
        )
        manager.establish_baseline()
        (repo / "src" / "a.py").write_text("VALUE = 2\n", encoding="utf-8")
        specs = [
            {
                "name": "mutating self verify",
                "feedback": "repair",
                "command": [
                    "{python}",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('node_modules/dep/index.js').write_text('mutated')"
                    ),
                ],
                "timeout_seconds": 30,
            }
        ]
        _, stamp, command = prepare_self_verify_runner(
            workspace=repo,
            specs=specs,
            toolchain={"project_python": sys.executable},
        )
        contract = self.contract()
        report = {
            "protocol_version": IMPLEMENTER_PROTOCOL_VERSION,
            "status": "COMPLETE",
            "summary": "done",
            "contract_evidence": [
                {"item_id": item["id"], "status": "VERIFIED", "evidence": ["checked"]}
                for item in contract["items"]
            ],
            "self_verification": {
                "status": "PASS",
                "command": "self",
                "evidence": ["SELF_VERIFY_PASS"],
            },
            "additional_check_paths": [],
            "blockers": [],
        }

        class FakeCodex:
            def run_turn(self, **_kwargs):
                completed = subprocess.run(command, cwd=repo, check=False)
                if completed.returncode != 0:
                    raise AssertionError("agent self verify fixture did not report PASS")
                return json.dumps(report)

        with self.assertRaises(HarnessControlledStop) as raised:
            run_implementer_report(
                FakeCodex(),
                thread_id="thread",
                prompt="implement",
                timeout=60,
                label="IMPLEMENT",
                implementation_contract=contract,
                self_verify_command=command,
                workspace=repo,
                stamp_path=stamp,
                plan=valid_plan(),
                control_plane=control_plane,
                runtime_integrity_manager=manager,
            )
        self.assertEqual(str(raised.exception), RUNTIME_PROJECTION_MUTATED_DURING_CHECK)
        self.assertEqual(workspace_dep.read_text(encoding="utf-8"), "baseline\n")
        self.assertFalse(
            (control_plane.private_root / "self_verify_receipt_current.json").exists()
        )

    def test_dynamic_checks_accept_test_paths_not_arbitrary_commands(self) -> None:
        repo = self.make_repo()
        js = repo / "tests" / "thing.test.cjs"
        js.write_text("test('x', () => expect(1).toBe(1));\n", encoding="utf-8")
        specs, notes = build_dynamic_check_specs(
            ["tests/thing.test.cjs", "src/a.py"], workspace=repo,
            toolchain={"node": "C:/node.exe", "jest": "C:/jest.js"}, base_specs=[]
        )
        self.assertEqual(len(specs), 1)
        self.assertIn("thing.test.cjs", specs[0]["name"])
        self.assertTrue(any("not test-like" in note for note in notes))

    def test_git_control_mutation_blocks_implementer_receipt_and_hidden_file_is_visible(self) -> None:
        repo = self.make_repo()
        baseline_sha = git(repo, "rev-parse", "HEAD")
        control_plane = ControllerPlane(repo.parent / f"{repo.name}-run")
        CandidateWorkspaceBaseline.capture(
            repo,
            baseline_sha=baseline_sha,
            excluded_prefixes=(".git", ".harness_tmp", ".venv", ".harness_git_excludes"),
            control_plane=control_plane,
        )
        git_manager = GitControlIntegrityManager(
            workspace=repo,
            control_plane=control_plane,
        )
        git_manager.establish_baseline()

        class FakeCodex:
            def run_turn(self, **_kwargs):
                with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as stream:
                    stream.write("stealth.py\n")
                (repo / "stealth.py").write_text("hidden candidate\n", encoding="utf-8")
                return "{}"

        with self.assertRaises(HarnessControlledStop) as raised:
            run_implementer_report(
                FakeCodex(),
                thread_id="thread",
                prompt="implement",
                timeout=60,
                label="IMPLEMENT",
                implementation_contract=self.contract(),
                self_verify_command=[],
                workspace=repo,
                stamp_path=repo / ".harness_tmp" / "missing.json",
                plan=valid_plan(),
                control_plane=control_plane,
                git_integrity_manager=git_manager,
            )

        self.assertEqual(
            str(raised.exception), GIT_CONTROL_STATE_MUTATED_DURING_BATCH
        )
        self.assertIn(
            "stealth.py",
            build_candidate_identity(repo, baseline_sha=baseline_sha).changed_paths,
        )
        self.assertFalse(
            (control_plane.private_root / "self_verify_receipt_current.json").exists()
        )

    def test_trusted_check_id_must_resolve_to_controller_owned_spec(self) -> None:
        specs, notes = build_trusted_check_id_specs(
            ["git.diff-check"],
            base_specs=[],
        )
        self.assertEqual(specs[0]["command"], ["git", "diff", "--check"])
        self.assertEqual(notes, [])
        with self.assertRaisesRegex(RuntimeError, "Unknown trusted check id"):
            build_trusted_check_id_specs(["looks.safe"], base_specs=[])


if __name__ == "__main__":
    unittest.main()
