from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import slivin_harness
import task_runner
from slivin_harness.implementer import IMPLEMENTER_PROTOCOL_VERSION
from slivin_harness.phase5 import PHASE5_VERSION, PROJECT_RUNTIME_VERSION
from slivin_harness.workflow import WORKFLOW_PHASE, WORKFLOW_VERSION


ROOT = Path(__file__).resolve().parents[1]


class Phase5ExecutableIntegrationTests(unittest.TestCase):
    def test_release_versions_are_phase5(self) -> None:
        self.assertEqual(slivin_harness.__version__, "0.8.0a8")
        self.assertEqual(WORKFLOW_VERSION, "workflow.v4")
        self.assertEqual(WORKFLOW_PHASE, "phase5-contract-runtime-reproducibility")
        self.assertEqual(IMPLEMENTER_PROTOCOL_VERSION, "implementer.v3")
        self.assertEqual(PHASE5_VERSION, "phase5-contract-runtime.v1")
        self.assertEqual(PROJECT_RUNTIME_VERSION, "project-runtime.v1")

    def test_task_runner_connects_expansion_runtime_and_local_file_guards(self) -> None:
        source = (ROOT / "task_runner.py").read_text(encoding="utf-8")
        for marker in (
            "recompile_active_definition",
            "expand_contract_and_verification_plan",
            "reconcile_project_runtime",
            "ProjectRuntimeManager",
            "restore_exposed_runtime_files",
            "ACTIVE_DEFINITION_EXPANDED",
        ):
            self.assertIn(marker, source)

    def test_runtime_profile_resolves_bootstrap_python_and_worktree_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap = root / "python.exe"
            bootstrap.write_text("placeholder", encoding="utf-8")
            config = {
                "projects": {
                    "demo": {
                        "runtime": {
                            "bootstrap_python": str(bootstrap),
                            "expected_python": f"{sys.version_info.major}.{sys.version_info.minor}",
                            "venv": ".venv",
                            "dependency_files": ["requirements.txt"],
                            "pip_install_args": ["--disable-pip-version-check"],
                        }
                    }
                }
            }
            resolved = task_runner.resolve_project_runtime_config(
                config,
                project_name="demo",
                source_repo=root,
            )
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.bootstrap_python, bootstrap.resolve())
            self.assertEqual(resolved.venv_relative, ".venv")
            self.assertEqual(resolved.dependency_files, ("requirements.txt",))

    def test_phase5_documentation_exists(self) -> None:
        doc = ROOT / "docs" / "PHASE5_CONTRACT_RUNTIME.md"
        self.assertTrue(doc.is_file())
        text = doc.read_text(encoding="utf-8")
        self.assertIn("CONSUMER-DISCOVERED", text)
        self.assertIn(".worktreeinclude", text)
        self.assertIn("worktree-local", text)
        self.assertIn("Verification Plan", text)


if __name__ == "__main__":
    unittest.main()
