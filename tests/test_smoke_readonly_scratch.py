"""Offline regressions for exact native instruction and sandbox probe evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid

from tools.smoke_readonly_scratch import (
    INSTRUCTION_READ_SPECS,
    PROBE,
    validate_instruction_read_evidence,
    probe_command,
    validate_phase,
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


class SandboxProbeEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path.cwd() / (".native-probe-test-" + uuid.uuid4().hex)
        root.mkdir()
        self.addCleanup(shutil.rmtree, root)
        self.project = root / "project"
        self.scratch = root / "scratch"
        self.sibling = root / "sibling"
        self.private = root / "private"
        self.peer = root / "peer" / "canary.txt"
        self.node = Path(shutil.which("node") or "C:/synthetic/node.exe")
        for directory in (self.project, self.scratch, self.sibling, self.private, self.peer.parent):
            directory.mkdir(parents=True)
        for target in (self.sibling / "canary.txt", self.private / "canary.txt", self.peer):
            target.write_text("canary\n", encoding="utf-8")
        (self.scratch / "jest").mkdir()
        (self.scratch / "jest" / "cache").write_text("cached", encoding="utf-8")

    @staticmethod
    def wrapper(script: str) -> str:
        return (r'"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
                f'-NoProfile -Command "{script.replace(chr(92), chr(92) * 2)}"')

    def probe_row(self, *, script: str | None = None, cwd: Path | None = None,
                  exit_code: int = 0, output: str | None = None) -> dict:
        script = script if script is not None else probe_command(node=self.node,
            project=self.project, scratch=self.scratch, sibling=self.sibling,
            private=self.private, peer=self.peer)
        return {"command": self.wrapper(script), "cwd": str(cwd or self.project),
                "exitCode": exit_code, "aggregatedOutput": output}

    def jest_row(self) -> dict:
        script = (f"& '{self.node}' .\\node_modules\\jest\\bin\\jest.js --config "
                  ".\\jest.config.cjs --runInBand --runTestsByPath "
                  ".\\tests\\arithmetic.test.cjs --watch=false")
        return {"command": self.wrapper(script), "cwd": str(self.project),
                "exitCode": 0, "aggregatedOutput": "Tests:       1 passed, 1 total"}

    def phase(self, probe: dict, *additional: dict) -> dict:
        return validate_phase([probe, *additional, self.jest_row()], node=self.node,
            project=self.project, scratch=self.scratch, sibling=self.sibling,
            private=self.private, peer=self.peer, initial=False)

    def test_exact_probe_with_structured_output_passes(self) -> None:
        rows = [{"name": name, "result": "ALLOWED"}
                for name in ("scratch:mkdir", "scratch:write-read")]
        for label in ("absolute", "relative-after-chdir"):
            names = ["write:" + name for name in ("src/arithmetic.cjs",
                "tests/arithmetic.test.cjs", "node_modules/jest/package.json")]
            names.extend(("create", "delete", "rename", "sibling", "private", "peer", "git"))
            rows.extend({"name": label + ":" + name, "result": "DENIED", "code": "EACCES"}
                        for name in names)
        output = "PROBE_RESULT=" + json.dumps({"cwd": str(self.project),
            "tmpdir": str(self.scratch), "scratch": str(self.scratch),
            "peer": str(self.peer), "rows": rows, "errors": [], "child": {"status": None}})
        result = self.phase(self.probe_row(output=output))
        self.assertEqual(result["probe_exit_evidence"], "PASS")
        self.assertTrue(result["structured_output_observed"])
        self.assertEqual(result["negative_attempts_denied"], 20)

    def test_exact_probe_without_aggregated_output_passes_without_inventing_details(self) -> None:
        result = self.phase(self.probe_row(output=None))
        self.assertEqual(result["probe_exit_evidence"], "PASS")
        self.assertFalse(result["structured_output_observed"])
        self.assertIsNone(result["negative_attempts_denied"])
        self.assertIsNone(result["child_process_observation"])

    def test_nonzero_and_wrong_cwd_fail(self) -> None:
        for name, row in (("nonzero", self.probe_row(exit_code=7)),
                          ("wrong_cwd", self.probe_row(cwd=self.scratch))):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "probe|cwd"):
                self.phase(row)

    def test_altered_controller_arguments_fail(self) -> None:
        for field in ("project", "scratch", "sibling", "private", "peer"):
            args = {name: getattr(self, name) for name in
                ("project", "scratch", "sibling", "private", "peer")}
            args[field] = args[field].parent / (args[field].name + "-wrong")
            script = probe_command(node=self.node, **args)
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "probe"):
                self.phase(self.probe_row(script=script))

    def test_echo_and_other_node_command_do_not_count(self) -> None:
        exact = probe_command(node=self.node, project=self.project, scratch=self.scratch,
            sibling=self.sibling, private=self.private, peer=self.peer)
        for script in (f"Write-Output '{exact}'",
                       f"& '{self.node}' .\\other_probe.cjs '{self.project}'"):
            with self.subTest(script=script), self.assertRaisesRegex(RuntimeError, "probe"):
                self.phase(self.probe_row(script=script, output="PROBE_RESULT={}"))

    def test_duplicate_qualifying_probe_commands_fail(self) -> None:
        first = self.probe_row()
        with self.assertRaisesRegex(RuntimeError, "probe"):
            self.phase(first, self.probe_row())
        altered = probe_command(node=self.node, project=self.project, scratch=self.scratch,
            sibling=self.sibling, private=self.private, peer=self.peer.parent / "wrong.txt")
        with self.assertRaisesRegex(RuntimeError, "probe"):
            self.phase(first, self.probe_row(script=altered))


@unittest.skipUnless(shutil.which("node"), "Node is required to execute the immutable probe")
class SandboxProbeProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path.cwd() / (".native-probe-test-" + uuid.uuid4().hex)
        root.mkdir()
        self.addCleanup(shutil.rmtree, root)
        self.project = root / "project"
        self.scratch = root / "scratch"
        self.sibling = root / "sibling"
        self.private = root / "private"
        self.peer = root / "peer" / "canary.txt"
        for directory in (self.project, self.scratch, self.sibling, self.private, self.peer.parent,
                          self.project / "src", self.project / "tests",
                          self.project / "node_modules" / "jest"):
            directory.mkdir(parents=True)
        for target in (self.sibling / "canary.txt", self.private / "canary.txt", self.peer,
                       self.project / "src" / "arithmetic.cjs",
                       self.project / "tests" / "arithmetic.test.cjs",
                       self.project / "node_modules" / "jest" / "package.json",
                       self.project / "delete-canary.txt", self.project / "rename-canary.txt"):
            target.write_text("canary\n", encoding="utf-8")
        self.script = self.project / "sandbox_probe.cjs"
        self.script.write_text(PROBE, encoding="utf-8")

    def run_probe(self, special: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        # Exercise the production probe body with deterministic filesystem failures.
        # One test simulates an unrelated ENOENT; the other allows one write.
        runner = r"""
const fs=require('fs'),path=require('path');
let allowed=false;
for(const name of ['appendFileSync','writeFileSync','unlinkSync','renameSync']){
 const original=fs[name];
 fs[name]=function(...args){
  const target=path.resolve(String(args[0]));
  if(target.startsWith(path.resolve(process.env.PROBE_SCRATCH)+path.sep))return original.apply(this,args);
  if(process.env.PROBE_SPECIAL==='allowed'&&!allowed&&name==='appendFileSync'){
   allowed=true;return;
  }
  const error=new Error('simulated filesystem result');
  error.code=process.env.PROBE_SPECIAL==='enoent'&&target.endsWith('arithmetic.cjs')?'ENOENT':'EACCES';
  throw error;
 };
}
process.argv=[process.execPath,process.env.PROBE_PATH,...JSON.parse(process.env.PROBE_ARGS)];
require(process.env.PROBE_PATH);
"""
        env = {**os.environ, "TEMP": str(self.scratch), "TMP": str(self.scratch),
               "PROBE_SCRATCH": str(self.scratch), "PROBE_PATH": str(self.script),
               "PROBE_ARGS": json.dumps([str(p) for p in (self.project, self.scratch,
                    self.sibling, self.private, self.peer)]), "PROBE_SPECIAL": special}
        result = subprocess.run([shutil.which("node"), "-e", runner], cwd=self.project,
                                env=env, text=True, encoding="utf-8", capture_output=True, check=False)
        output = next(line for line in result.stdout.splitlines() if line.startswith("PROBE_RESULT="))
        return result, json.loads(output[len("PROBE_RESULT="):])

    def test_unrelated_enoent_is_not_a_sandbox_denial(self) -> None:
        process, result = self.run_probe("enoent")
        self.assertNotEqual(process.returncode, 0)
        self.assertTrue(any(row.get("code") == "ENOENT" for row in result["rows"]))
        self.assertTrue(any("not-policy-denial" in error for error in result["errors"]))

    def test_all_policy_denials_make_probe_exit_zero(self) -> None:
        process, result = self.run_probe("denied")
        self.assertEqual(process.returncode, 0, result["errors"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["rows"]), 22)

    def test_allowed_protected_write_fails_probe(self) -> None:
        process, result = self.run_probe("allowed")
        self.assertNotEqual(process.returncode, 0)
        self.assertTrue(any(row["result"] == "ALLOWED" for row in result["rows"]
                            if row["name"].endswith("write:src/arithmetic.cjs")))
        self.assertTrue(any("not-policy-denial" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
