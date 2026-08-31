from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from slivin_harness.implementer import build_implementation_contract
from slivin_harness.phase5 import (
    Phase5ContractError,
    ProjectRuntimeConfig,
    ProjectRuntimeManager,
    expand_contract_and_verification_plan,
)
from slivin_harness.task_contract import TASK_CONTRACT_VERSION, build_task_contract
from slivin_harness.verification import Capability, ProofLevel, compile_verification_plan


def task_contract() -> dict:
    raw = "Исправь кнопку. Остальное не ломай."
    return build_task_contract(
        raw_request=raw,
        normalized={
            "protocol_version": TASK_CONTRACT_VERSION,
            "status": "READY",
            "summary": "Исправить кнопку.",
            "explicit_intent": [{"claim": "Исправить кнопку.", "source_text": "Исправь кнопку."}],
            "explicit_acceptance": [{"claim": "Кнопка исправлена.", "source_text": "Исправь кнопку."}],
            "explicit_preservation": [{"claim": "Остальное не ломать.", "source_text": "Остальное не ломай."}],
            "explicit_forbidden": [],
            "owner_boundaries": [],
            "non_goals": [],
            "ambiguities": [],
            "reason": "",
        },
    )


def discovery(*, name: str = "Distribution", level: str = "LOCAL_DETERMINISTIC") -> dict:
    capabilities = []
    if level == ProofLevel.LIVE_LOCAL.value:
        capabilities = [Capability.BROWSER_DOM.value]
    return {
        "kind": "consumer",
        "name": name,
        "reason": "Uses the changed shared selection authority.",
        "required_behavior": "Existing stage guard remains fail-closed.",
        "required_proof": {
            "claim": "The stage guard remains fail-closed.",
            "level": level,
            "capabilities": capabilities,
        },
        "evidence": ["static/js/distribution/index.js uses the helper"],
    }


class ContractExpansionTests(unittest.TestCase):
    def test_discovery_expands_contract_and_recompiles_verification_plan(self) -> None:
        contract = build_implementation_contract(None, task_contract=task_contract())
        plan = compile_verification_plan(contract, project_checks=[{"name": "Unit"}])

        result = expand_contract_and_verification_plan(
            implementation_contract=contract,
            previous_verification_plan=plan,
            discoveries=[discovery()],
            project_checks=[{"name": "Unit"}],
            task_checks=["path:tests/test_selection.py"],
        )

        self.assertEqual(result.added_item_ids, ("CONSUMER-DISCOVERED-1",))
        self.assertIn("CONSUMER-DISCOVERED-1", [item["id"] for item in result.implementation_contract["items"]])
        self.assertIn(
            "CONSUMER-DISCOVERED-1",
            [item["item_id"] for item in result.verification_plan["requirements"]],
        )
        self.assertEqual(result.verification_plan["task_checks"], ["path:tests/test_selection.py"])
        self.assertNotEqual(contract["fingerprint"], result.implementation_contract["fingerprint"])
        self.assertNotEqual(plan["fingerprint"], result.verification_plan["fingerprint"])

    def test_duplicate_discovery_is_idempotent(self) -> None:
        contract = build_implementation_contract(None, task_contract=task_contract())
        plan = compile_verification_plan(contract, project_checks=[])
        first = expand_contract_and_verification_plan(
            implementation_contract=contract,
            previous_verification_plan=plan,
            discoveries=[discovery()],
            project_checks=[],
            task_checks=[],
        )
        second = expand_contract_and_verification_plan(
            implementation_contract=first.implementation_contract,
            previous_verification_plan=first.verification_plan,
            discoveries=[discovery()],
            project_checks=[],
            task_checks=[],
        )
        self.assertEqual(second.added_item_ids, ())
        self.assertEqual(second.duplicate_discoveries, ("Distribution",))
        self.assertEqual(
            first.implementation_contract["fingerprint"],
            second.implementation_contract["fingerprint"],
        )

    def test_new_runtime_proof_is_not_silently_collapsed(self) -> None:
        contract = build_implementation_contract(None, task_contract=task_contract())
        plan = compile_verification_plan(contract, project_checks=[])
        result = expand_contract_and_verification_plan(
            implementation_contract=contract,
            previous_verification_plan=plan,
            discoveries=[discovery(level=ProofLevel.LIVE_LOCAL.value)],
            project_checks=[],
            task_checks=[],
        )
        self.assertTrue(result.runtime_profiles_changed)
        self.assertEqual(result.verification_plan["runtime_profiles"], ["LIVE_LOCAL"])
        self.assertIn(Capability.LIVE_LOCAL_RUNTIME.value, result.verification_plan["required_capabilities"])
        self.assertIn(Capability.BROWSER_DOM.value, result.verification_plan["required_capabilities"])

    def test_discovery_requires_typed_proof(self) -> None:
        contract = build_implementation_contract(None, task_contract=task_contract())
        plan = compile_verification_plan(contract, project_checks=[])
        invalid = discovery()
        invalid.pop("required_proof")
        with self.assertRaises(Phase5ContractError):
            expand_contract_and_verification_plan(
                implementation_contract=contract,
                previous_verification_plan=plan,
                discoveries=[invalid],
                project_checks=[],
                task_checks=[],
            )


class _FakeRuntimeRunner:
    def __init__(self, workspace: Path, venv: Path) -> None:
        self.workspace = workspace
        self.venv = venv
        self.extra_package = False

    def __call__(self, command, cwd, env):
        args = list(command)
        stdout = ""
        stderr = ""
        code = 0
        if "-m" in args and "venv" in args:
            python = self.venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("fake", encoding="utf-8")
        elif "-c" in args:
            stdout = "3.12.9\n"
        elif args[-3:] == ["-m", "pip", "check"]:
            stdout = "No broken requirements found.\n"
        elif args[-4:] == ["-m", "pip", "freeze", "--all"]:
            requirement = (self.workspace / "requirements.txt").read_text(encoding="utf-8").strip()
            stdout = requirement + ("\nhidden==1" if self.extra_package else "") + "\n"
        elif "install" in args:
            self.extra_package = False
        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


class ProjectRuntimeTests(unittest.TestCase):
    def _manager(self, root: Path):
        workspace = root / "workspace"
        workspace.mkdir()
        bootstrap = root / "python312.exe"
        bootstrap.write_text("fake", encoding="utf-8")
        (workspace / "requirements.txt").write_text("demo==1\n", encoding="utf-8")
        config = ProjectRuntimeConfig(
            bootstrap_python=bootstrap,
            expected_python="3.12",
            dependency_files=("requirements.txt",),
        )
        manager = ProjectRuntimeManager(workspace=workspace, config=config)
        fake = _FakeRuntimeRunner(workspace, manager.venv)
        manager.runner = fake
        return manager, fake, workspace

    def test_build_creates_worktree_runtime_and_stable_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, _fake, _workspace = self._manager(Path(tmp))
            state = manager.build(clean=True)
            self.assertEqual(state.mode, "WORKTREE_VENV")
            project_python = Path(state.project_python)
            self.assertTrue(project_python.is_file())
            self.assertEqual(state.project_version, "3.12.9")
            self.assertTrue(state.runtime_id.startswith("project-runtime.v1:"))
            # Native Windows may expose the same temporary directory through
            # different lexical spellings.  String-prefix comparison is not a
            # filesystem ownership check.  The runtime contract is that the
            # executable entry point belongs to the exact Controller-owned
            # worktree venv.
            self.assertEqual(project_python.parent.parent, manager.venv)
            self.assertEqual(manager.venv.relative_to(manager.workspace), Path(".venv"))

    def test_runtime_state_accepts_equivalent_workspace_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            bootstrap = root / "python312.exe"
            bootstrap.write_text("fake", encoding="utf-8")
            (workspace / "requirements.txt").write_text("demo==1\n", encoding="utf-8")

            # Deliberately use a different lexical spelling that resolves to
            # the same worktree.  Native Windows tempfile/NTFS aliases create
            # the same class of mismatch as the reported failure.
            workspace_alias = workspace / "not-created" / ".."
            manager = ProjectRuntimeManager(
                workspace=workspace_alias,
                config=ProjectRuntimeConfig(
                    bootstrap_python=bootstrap,
                    expected_python="3.12",
                    dependency_files=("requirements.txt",),
                ),
            )
            manager.runner = _FakeRuntimeRunner(manager.workspace, manager.venv)

            state = manager.build(clean=True)
            project_python = Path(state.project_python)

            self.assertNotEqual(str(workspace_alias), str(manager.workspace))
            self.assertEqual(project_python.parent.parent, manager.venv)
            self.assertEqual(manager.venv.relative_to(manager.workspace), Path(".venv"))

    def test_runtime_entrypoint_outside_worktree_venv_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager, _fake, _workspace = self._manager(root)
            outside_python = root / "outside" / "Scripts" / "python.exe"

            with self.assertRaisesRegex(Phase5ContractError, "escapes worktree venv"):
                manager._assert_worktree_local_entrypoint(outside_python)

    def test_dependency_change_rebuilds_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, _fake, workspace = self._manager(Path(tmp))
            before = manager.build(clean=True)
            (workspace / "requirements.txt").write_text("demo==2\n", encoding="utf-8")
            result = manager.reconcile(before)
            self.assertTrue(result.changed)
            self.assertIn("DEPENDENCY_MANIFEST_CHANGED", result.reasons)
            self.assertNotEqual(before.runtime_id, result.state.runtime_id)

    def test_hidden_package_drift_rebuilds_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, fake, _workspace = self._manager(Path(tmp))
            before = manager.build(clean=True)
            fake.extra_package = True
            result = manager.reconcile(before)
            self.assertTrue(result.changed)
            self.assertIn("RUNTIME_ENV_DRIFT", result.reasons)
            self.assertEqual(before.package_snapshot_sha256, result.state.package_snapshot_sha256)

    def test_wrong_bootstrap_version_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, _fake, _workspace = self._manager(Path(tmp))
            manager.config = ProjectRuntimeConfig(
                bootstrap_python=manager.config.bootstrap_python,
                expected_python="3.11",
                dependency_files=("requirements.txt",),
            )
            with self.assertRaisesRegex(Phase5ContractError, "does not satisfy"):
                manager.build(clean=True)


if __name__ == "__main__":
    unittest.main()
