"""Console diagnostics for failed release qualification stages."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import unittest
import uuid

from tools.release_check import RELEASE_LOG_TAIL_LINES, emit_stage_failure_diagnostics


class ReleaseFailureDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".harness_tmp" / ("release-diagnostics-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_failure_prints_paths_typed_summary_and_bounded_redacted_tail(self) -> None:
        secret = "release-super-secret-value"
        log = self.root / "boundary.log"
        lines = [f"line-{index}" for index in range(RELEASE_LOG_TAIL_LINES + 15)]
        lines[-2] = "bare inherited value " + secret
        lines[-1] = "Authorization: Bearer independent-token-value"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary = self.root / "boundary" / "summary.json"
        summary.parent.mkdir()
        summary.write_text(json.dumps({
            "status": "FAIL",
            "error": {"type": "BoundaryFailure", "reason": "typed boundary failure"},
        }), encoding="utf-8")

        output = io.StringIO()
        with redirect_stdout(output):
            emit_stage_failure_diagnostics(
                "boundary",
                log_path=log,
                summary_path=summary,
                environ={"OPENAI_API_KEY": secret},
            )
        rendered = output.getvalue()
        tail = [line for line in rendered.splitlines() if line.startswith("| ")]

        self.assertIn("RELEASE_STAGE_FAILURE: boundary", rendered)
        self.assertIn("RELEASE_STAGE_LOG: " + str(log.resolve()), rendered)
        self.assertIn("RELEASE_STAGE_SUMMARY: " + str(summary.resolve()), rendered)
        self.assertIn("RELEASE_STAGE_SUMMARY_STATUS: FAIL", rendered)
        self.assertIn("RELEASE_STAGE_SUMMARY_REASON_TYPE: BoundaryFailure", rendered)
        self.assertIn("RELEASE_STAGE_SUMMARY_REASON: typed boundary failure", rendered)
        self.assertEqual(len(tail), RELEASE_LOG_TAIL_LINES)
        self.assertEqual(tail[0], "| line-15")
        self.assertIn("<redacted>", tail[-1])
        self.assertNotIn(secret, rendered)
        self.assertNotIn("independent-token-value", rendered)

    def test_unreadable_or_missing_summary_does_not_hide_log_tail(self) -> None:
        log = self.root / "self_check.log"
        log.write_text("first\nlast failure\n", encoding="utf-8")
        invalid = self.root / "self_check" / "summary.json"
        invalid.parent.mkdir()
        invalid.write_text("not-json", encoding="utf-8")

        invalid_output = io.StringIO()
        with redirect_stdout(invalid_output):
            emit_stage_failure_diagnostics(
                "self_check", log_path=log, summary_path=invalid, environ={}
            )
        self.assertIn("RELEASE_STAGE_SUMMARY_READ_STATUS: SUMMARY_UNREADABLE",
                      invalid_output.getvalue())
        self.assertIn("| last failure", invalid_output.getvalue())

        missing_output = io.StringIO()
        with redirect_stdout(missing_output):
            emit_stage_failure_diagnostics(
                "self_check", log_path=log, summary_path=self.root / "missing.json", environ={}
            )
        self.assertNotIn("RELEASE_STAGE_SUMMARY:", missing_output.getvalue())
        self.assertIn("| last failure", missing_output.getvalue())


if __name__ == "__main__":
    unittest.main()
