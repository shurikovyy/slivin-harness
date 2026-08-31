from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import slivin_harness
from slivin_harness.phase4 import CheckRegistry


ROOT = Path(__file__).resolve().parents[1]


class Phase4ExecutableIntegrationTests(unittest.TestCase):
    def test_release_version_and_protocol_are_phase4(self) -> None:
        self.assertEqual(slivin_harness.__version__, "0.8.0a8")
        implementer = (ROOT / "slivin_harness" / "implementer.py").read_text(encoding="utf-8")
        self.assertIn("implementer.v3", implementer)
        self.assertIn("REPLAN_REQUIRED", implementer)
        self.assertIn("NEEDS_USER_DECISION", implementer)

    def test_app_server_uses_inactivity_not_short_total_timeout(self) -> None:
        source = (ROOT / "slivin_harness" / "app_server.py").read_text(encoding="utf-8")
        self.assertIn("_phase4_inactivity_expired", source)
        self.assertIn("phase4 emergency ceiling", source)
        self.assertIn("last_activity_seconds", source)
        self.assertIn("active_tools", source)

    def test_task_runner_connects_private_registry_freeze_and_progress_guard(self) -> None:
        source = (ROOT / "task_runner.py").read_text(encoding="utf-8")
        self.assertIn("recorder.private_root", source)
        self.assertIn("CheckRegistry(", source)
        self.assertIn("check_registry.json", source)
        self.assertIn("verify_self_verification_stamp", source)
        self.assertIn("Controller checks changed the candidate", source)
        self.assertIn("_phase4_loop_stalled", source)
        self.assertNotIn("still fail after max_fix_cycles", source)

    def test_windows_style_registered_path_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            private = root / "private"
            (workspace / "tests").mkdir(parents=True)
            (workspace / "tests" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
            registry = CheckRegistry(private / "registry.json", workspace=workspace)
            reference = registry.register_path(r"tests\test_a.py")
            self.assertEqual(reference.value, "tests/test_a.py")

    def test_phase4_documentation_exists(self) -> None:
        doc = ROOT / "docs" / "PHASE4_EXECUTION.md"
        self.assertTrue(doc.is_file())
        text = doc.read_text(encoding="utf-8")
        self.assertIn("IMPLEMENTER v2", text)
        self.assertIn("CHECK_MUTATED_CANDIDATE", text)
        self.assertIn("inactivity", text.lower())


if __name__ == "__main__":
    unittest.main()
