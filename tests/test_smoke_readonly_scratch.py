"""Regression tests for native repository-instruction read evidence."""
from __future__ import annotations

from pathlib import Path
import unittest

from tools.smoke_readonly_scratch import (
    INSTRUCTION_READ_SPECS,
    validate_instruction_read_evidence,
)


class InstructionReadEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = (Path.cwd() / ".synthetic-instruction-project").resolve()

    def command(self, script: str, *, cwd: Path | None = None,
                exit_code: int = 0, output: str | None = None) -> dict:
        escaped_script = script.replace("\\", "\\\\")
        rendered = (
            r'"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
            f'-NoProfile -Command "{escaped_script}"'
        )
        return {
            "command": rendered,
            "cwd": str(cwd or self.project),
            "exitCode": exit_code,
            "aggregatedOutput": output,
        }

    def valid_commands(self, *, output: str | None = None) -> list[dict]:
        return [self.command(script, output=output) for _, _, _, script in INSTRUCTION_READ_SPECS]

    def test_successful_exact_reads_do_not_require_aggregated_output(self) -> None:
        evidence = validate_instruction_read_evidence(
            self.valid_commands(output=None), project=self.project
        )

        self.assertEqual(set(evidence), {"root", "nested"})
        self.assertTrue(all(row["status"] == "PASS" for row in evidence.values()))
        self.assertTrue(all(row["output_marker_observed"] is False for row in evidence.values()))

    def test_marker_is_additional_evidence_only(self) -> None:
        commands = self.valid_commands()
        commands[0]["aggregatedOutput"] = "ROOT_INSTRUCTIONS_READ"
        commands[1]["aggregatedOutput"] = "NESTED_INSTRUCTIONS_READ"
        evidence = validate_instruction_read_evidence(commands, project=self.project)
        self.assertTrue(all(row["output_marker_observed"] for row in evidence.values()))

        echoed = self.command(
            "Write-Output 'ROOT_INSTRUCTIONS_READ NESTED_INSTRUCTIONS_READ AGENTS.md'",
            output="ROOT_INSTRUCTIONS_READ NESTED_INSTRUCTIONS_READ",
        )
        with self.assertRaisesRegex(RuntimeError, "AGENTS.md"):
            validate_instruction_read_evidence([echoed], project=self.project)

    def test_missing_wrong_path_wrong_cwd_and_nonzero_reads_fail(self) -> None:
        cases = {}
        valid = self.valid_commands()
        cases["missing_root"] = (valid[1:], "AGENTS.md")
        cases["missing_nested"] = (valid[:1], "src/AGENTS.md")

        wrong_path = self.valid_commands()
        wrong_path[1] = self.command(
            r"[System.IO.File]::ReadAllText('src\OTHER.md', [System.Text.Encoding]::UTF8)"
        )
        cases["wrong_path"] = (wrong_path, "src/AGENTS.md")

        wrong_cwd = self.valid_commands()
        wrong_cwd[1]["cwd"] = str(self.project.parent)
        cases["wrong_cwd"] = (wrong_cwd, "src/AGENTS.md")

        nonzero = self.valid_commands()
        nonzero[1]["exitCode"] = 1
        cases["nonzero"] = (nonzero, "src/AGENTS.md")

        for name, (commands, expected_path) in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, expected_path):
                validate_instruction_read_evidence(commands, project=self.project)


if __name__ == "__main__":
    unittest.main()
