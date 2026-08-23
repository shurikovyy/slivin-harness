from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "prepare_workspace.py"


class PrepareWorkspaceTests(unittest.TestCase):
    def test_allow_env_keeps_env_venv_and_node_modules_but_excludes_them_from_baseline(self) -> None:
        workspace = Path(tempfile.mkdtemp(prefix="slivin-static-workspace-"))
        (workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (workspace / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (workspace / ".venv").mkdir()
        (workspace / ".venv" / "marker.txt").write_text("venv", encoding="utf-8")
        (workspace / "node_modules").mkdir()
        (workspace / "node_modules" / "marker.txt").write_text("node", encoding="utf-8")
        (workspace / "__pycache__").mkdir()
        (workspace / "__pycache__" / "junk.pyc").write_bytes(b"junk")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(workspace), "--allow-env"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            self.fail(result.stdout + "\n" + result.stderr)

        self.assertTrue((workspace / ".env").is_file())
        self.assertTrue((workspace / ".venv" / "marker.txt").is_file())
        self.assertTrue((workspace / "node_modules" / "marker.txt").is_file())
        self.assertFalse((workspace / "__pycache__").exists())

        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=workspace,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.splitlines()
        self.assertIn("app.py", tracked)
        self.assertNotIn(".env", tracked)
        self.assertFalse(any(path.startswith(".venv/") for path in tracked))
        self.assertFalse(any(path.startswith("node_modules/") for path in tracked))
        self.assertIn("ENV_VISIBLE_TO_AGENT: .env", result.stdout)


if __name__ == "__main__":
    unittest.main()
