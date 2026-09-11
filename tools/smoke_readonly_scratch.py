"""Opt-in, LLM-backed native acceptance; never part of offline self-check.

Only disposable canaries are mutated/attempted. Installed dependencies are
physically copied and fingerprinted; no installs, sandbox overrides or ACL edits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from slivin_harness import __version__
from slivin_harness.app_server import CodexAppServer
from slivin_harness.control_plane import ControllerPlane, is_within
from slivin_harness.evaluator import EVALUATOR_INSTRUCTIONS
from slivin_harness.execution import ExecutionBroker, ExecutionRole
from slivin_harness.git_integrity import CandidateWorkspaceBaseline, GitControlIntegrityManager, TrustedBatchIntegrityCoordinator
from slivin_harness.planner import PLANNER_INSTRUCTIONS
from slivin_harness.run_state import build_candidate_identity
from slivin_harness.runtime_projection import RuntimeProjectionIntegrityManager, fingerprint_runtime_tree
from slivin_harness.workspace import RuntimeProjection, WorkspaceSession


PROBE = r"""
const fs=require('fs'),path=require('path'),cp=require('child_process');
const project=process.cwd(),scratch=process.env.TEMP,sibling=process.argv[2],priv=process.argv[3],peer=process.argv[4];
const rows=[];
function op(name,fn){try{fn();rows.push({name,result:'ALLOWED'});}catch(e){rows.push({name,result:'DENIED',code:e.code,syscall:e.syscall,path:e.path});}}
function negatives(label,relative){
 const p=s=>relative?path.relative(process.cwd(),path.resolve(project,s)):path.resolve(project,s);
 for(const file of ['src/arithmetic.cjs','tests/arithmetic.test.cjs','node_modules/jest/package.json'])
  op(label+':write:'+file,()=>fs.appendFileSync(p(file),'unexpected'));
 op(label+':create',()=>fs.writeFileSync(p('unauthorized-new.txt'),'unexpected'));
 op(label+':delete',()=>fs.unlinkSync(p('delete-canary.txt')));
 op(label+':rename',()=>fs.renameSync(p('rename-canary.txt'),p('renamed.txt')));
 op(label+':sibling',()=>fs.appendFileSync(p(path.join(sibling,'canary.txt')),'unexpected'));
 op(label+':private',()=>fs.appendFileSync(p(path.join(priv,'canary.txt')),'unexpected'));
 op(label+':peer',()=>fs.appendFileSync(p(peer),'unexpected'));
 op(label+':git',()=>fs.writeFileSync(p('.git/unauthorized-canary'),'unexpected'));
}
op('scratch:mkdir',()=>fs.mkdirSync(path.join(scratch,'positive'),{recursive:true}));
op('scratch:write-read',()=>{const p=path.join(scratch,'positive/probe.txt');fs.writeFileSync(p,'scratch-ok');if(fs.readFileSync(p,'utf8')!=='scratch-ok')throw Error('mismatch');});
negatives('absolute',false);process.chdir(project);negatives('relative-after-chdir',true);
const child=cp.spawnSync(process.execPath,['-p','40+2'],{encoding:'utf8'});
console.log('PROBE_RESULT='+JSON.stringify({cwd:process.cwd(),tmpdir:require('os').tmpdir(),scratch,peer,rows,
 child:{status:child.status,stdout:child.stdout,stderr:child.stderr,error:child.error?{code:child.error.code,syscall:child.error.syscall}:null}}));
if(rows.some(r=>r.name.startsWith('scratch:')?r.result!=='ALLOWED':r.result!=='DENIED'))process.exitCode=7;
"""

INSTRUCTION_READ_SPECS = (
    ("root", "AGENTS.md", "ROOT_INSTRUCTIONS_READ",
     "[System.IO.File]::ReadAllText('AGENTS.md', [System.Text.Encoding]::UTF8)"),
    ("nested", "src/AGENTS.md", "NESTED_INSTRUCTIONS_READ",
     r"[System.IO.File]::ReadAllText('src\AGENTS.md', [System.Text.Encoding]::UTF8)"),
)
_POWERSHELL_COMMAND = re.compile(
    r'^"[^"\r\n]*[\\/]powershell\.exe" -NoProfile -Command "(?P<script>[^"\r\n]*)"$',
    re.IGNORECASE,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(project: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=project, check=True, capture_output=True,
        text=True, encoding="utf-8", env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout.strip()


class ObservedServer(CodexAppServer):
    """Observe actual command results without replacing execution/permissions."""

    def __init__(self, *args, log_root: Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.log_root = log_root
        self.phase = "startup"
        self.commands: list[dict] = []

    def request(self, method, params, **kwargs):
        if method in {"thread/start", "turn/start", "thread/archive"}:
            with (self.log_root / "requests.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"method": method, "params": params}, ensure_ascii=False) + "\n")
        return super().request(method, params, **kwargs)

    def _receive_raw_optional(self, timeout):
        message = super()._receive_raw_optional(timeout)
        if message:
            item = message.get("params", {}).get("item", {})
            method = message.get("method")
            if method == "item/completed" and item.get("type") == "commandExecution":
                record = {"phase": self.phase, **{key: item.get(key) for key in (
                    "id", "command", "cwd", "exitCode", "durationMs", "aggregatedOutput")}}
                self.commands.append(record)
                with (self.log_root / "commands.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print("NATIVE_COMMAND:", self.phase, item.get("exitCode"), flush=True)
            elif method == "item/commandExecution/outputDelta":
                with (self.log_root / "output_deltas.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"phase": self.phase, "params": message["params"]}, ensure_ascii=False) + "\n")
        return message


def validate_instruction_read_evidence(commands: list[dict], *, project: Path) -> dict:
    """Require the exact Controller-provided root and nested read commands."""
    project = project.resolve()
    evidence = {}
    for label, relative_path, marker, expected_script in INSTRUCTION_READ_SPECS:
        successful = []
        for row in commands:
            command = row.get("command")
            cwd = row.get("cwd")
            if not isinstance(command, str) or not isinstance(cwd, str):
                continue
            # App Server commandExecution escapes Windows separators in its
            # rendered command. Collapse only that transport spelling before
            # comparing the complete PowerShell payload.
            rendered = command.replace("\\\\", "\\")
            match = _POWERSHELL_COMMAND.fullmatch(rendered)
            if match is None or match.group("script") != expected_script:
                continue
            try:
                exact_cwd = Path(cwd).resolve() == project
            except (OSError, RuntimeError, ValueError):
                exact_cwd = False
            if exact_cwd and row.get("exitCode") == 0:
                successful.append(row)
        if not successful:
            raise RuntimeError(
                f"Missing/failed exact repository instruction read: {relative_path}"
            )
        evidence[label] = {
            "status": "PASS",
            "path": relative_path,
            "output_marker_observed": any(
                marker in (row.get("aggregatedOutput") or "") for row in successful
            ),
        }
    return evidence


def validate_phase(commands: list[dict], *, project: Path, scratch: Path, peer: Path, initial: bool) -> dict:
    probes = [row for row in commands if (row["aggregatedOutput"] or "").startswith("PROBE_RESULT=")]
    if len(probes) != 1 or probes[0]["exitCode"] != 0 or Path(probes[0]["cwd"]) != project:
        raise RuntimeError("Missing/failed actual sandbox canary probe")
    probe = json.loads(probes[0]["aggregatedOutput"].split("PROBE_RESULT=", 1)[1])
    if Path(probe["scratch"]) != scratch or Path(probe["tmpdir"]) != scratch:
        raise RuntimeError("Agent cache environment escaped its role scratch")
    if Path(probe["peer"]) != peer:
        raise RuntimeError("Peer probe did not target the Controller-selected canary")
    positive = [row for row in probe["rows"] if row["name"].startswith("scratch:")]
    negative = [row for row in probe["rows"] if not row["name"].startswith("scratch:")]
    if len(positive) != 2 or any(row["result"] != "ALLOWED" for row in positive):
        raise RuntimeError("Scratch operations were not allowed")
    if len(negative) != 20 or any(row["result"] != "DENIED" or row.get("code") not in {"EPERM", "EACCES"} for row in negative):
        raise RuntimeError("A protected write operation was allowed or failed for an unrelated reason")
    # Match the executed Node/Jest invocation, not an echoed result or a JSON
    # write whose text happens to contain command/output strings.
    jest_command = re.compile(r"-Command [\"']& ['\"][^'\"\r\n]+['\"] \.\\node_modules\\jest\\bin\\jest\.js --config ")
    jest = [row for row in commands if jest_command.search(row["command"].replace("\\\\", "\\"))
        and "--runTestsByPath" in row["command"] and "--showConfig" not in row["command"]]
    passed = [row for row in jest if row["exitCode"] == 0 and "Tests:       1 passed, 1 total" in (row["aggregatedOutput"] or "")]
    failed = [row for row in jest if row["exitCode"] == 1 and "intentional failing assertion" in (row["aggregatedOutput"] or "")
        and "Expected: 999" in row["aggregatedOutput"] and "Received: 5" in row["aggregatedOutput"]]
    if len(passed) != (2 if initial else 1) or len(failed) != (1 if initial else 0):
        raise RuntimeError("Missing cold/warm PASS or intentional assertion FAIL")
    if any(Path(row["cwd"]) != project or any(flag in row["command"] for flag in ("--no-cache", "--cache=false", "--cacheDirectory")) for row in jest):
        raise RuntimeError("Jest did not use the required project cwd/default cached route")
    # Preserve evidence for long haste-map names as well as the shorter perf cache.
    scan_root = Path("\\\\?\\" + str(scratch)) if os.name == "nt" else scratch
    cache_files = sorted(str(path.relative_to(scan_root)) for path in (scan_root / "jest").rglob("*") if path.is_file())
    if not cache_files:
        raise RuntimeError("No actual Jest cache files in role scratch")
    instruction_reads = validate_instruction_read_evidence(commands, project=project) if initial else None
    result = {"status": "PASS", "negative_attempts_denied": len(negative), "jest_passes": len(passed),
        "intentional_assertion_failures": len(failed), "cache_files": cache_files,
        "peer_canary": str(peer), "child_process_observation": probe["child"]}
    if instruction_reads is not None:
        result["instruction_reads"] = instruction_reads
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--runtime-source", type=Path, required=True, help="Existing node_modules; read/copy only")
    parser.add_argument("--output", type=Path, help="New disposable output directory; must not exist")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This acceptance targets native Windows")
    for name in ("codex", "node", "runtime_source"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    if not args.codex.is_file() or not args.node.is_file() or not (args.runtime_source / "jest/bin/jest.js").is_file():
        parser.error("Expected installed Codex, Node and a complete Jest runtime")
    if args.runtime_source.name != "node_modules":
        parser.error("Runtime projection requires an existing node_modules directory")
    output = (args.output or Path(tempfile.gettempdir()) / ("slivin-native-scratch-" + uuid.uuid4().hex[:10])).resolve()
    if is_within(ROOT, output) or is_within(args.runtime_source.parent, output):
        parser.error("Diagnostic output must be outside Harness and the source project/runtime")
    output.mkdir(parents=True, exist_ok=False)
    logs = output / "diagnostics"
    logs.mkdir()
    project = output / "project"
    project.mkdir()
    print("NATIVE_OUTPUT:", output, flush=True)
    source_before = asdict(fingerprint_runtime_tree(args.runtime_source))
    shutil.copytree(args.runtime_source, project / "node_modules")
    files = {
        "AGENTS.md": "Repository marker: ROOT_INSTRUCTIONS_READ. Read src/AGENTS.md before src/arithmetic.cjs.\n",
        "src/AGENTS.md": "Nested repository marker: NESTED_INSTRUCTIONS_READ. Preserve source semantics.\n",
        "src/arithmetic.cjs": "exports.add=(a,b)=>a+b;\n",
        "tests/arithmetic.test.cjs": "const {add}=require('../src/arithmetic.cjs');test('real cached assertion',()=>expect(add(2,3)).toBe(5));\n",
        "tests/failing.test.cjs": "test('intentional failing assertion',()=>expect(2+3).toBe(999));\n",
        "jest.config.cjs": "module.exports={testEnvironment:'node',testMatch:['**/*.test.cjs'],transform:{}};\n",
        "package.json": '{"name":"scoped-scratch-smoke","version":"1.0.0","private":true}\n',
        ".gitignore": "node_modules/\n.harness_tmp/\n",
        "delete-canary.txt": "unchanged\n", "rename-canary.txt": "unchanged\n", "sandbox_probe.cjs": PROBE,
    }
    for name, content in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    run_root = output / "run"
    private = run_root / "controller_private"
    sibling = output / "sibling"
    canaries = [project / ".harness_tmp/peer/canary.txt",
        sibling / "canary.txt", private / "canary.txt"]
    for path in canaries:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unchanged-canary\n", encoding="utf-8")
    git(project, "init")
    git(project, "add", *files)
    git(project, "-c", "user.name=Disposable Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "synthetic baseline")
    baseline = git(project, "rev-parse", "HEAD")
    hashes_before = {name: hashlib.sha256((project / name).read_bytes()).hexdigest() for name in files}
    plane = ControllerPlane(run_root)
    broker = ExecutionBroker(workspace=project, run_root=run_root, private_root=private)
    session = WorkspaceSession(workspace=project, mode="managed", managed=True, base_sha=baseline,
        source_repo=args.runtime_source.parent,
        runtime_projections=(RuntimeProjection("node_modules", "workspace.copy_untracked", project / "node_modules", True, "physical_copy", True),))
    excluded = (".git", ".harness_tmp", "node_modules", ".harness_git_excludes")
    CandidateWorkspaceBaseline.capture(project, baseline_sha=baseline, excluded_prefixes=excluded, control_plane=plane)
    git_manager = GitControlIntegrityManager(workspace=project, control_plane=plane)
    git_manager.establish_baseline()
    runtime_manager = RuntimeProjectionIntegrityManager(session=session, control_plane=plane)
    runtime_manager.establish_baseline()
    identity = lambda: build_candidate_identity(project, baseline_sha=baseline, excluded_prefixes=excluded)
    guard = TrustedBatchIntegrityCoordinator(git_manager=git_manager, runtime_manager=runtime_manager, candidate_identity=identity)
    candidate_before = identity().candidate_id
    summary = {"schema_version": "native-scoped-scratch-smoke.v1", "harness_version": __version__, "status": "RUNNING", "phases": {}}
    summary["execution_source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("slivin_harness/app_server.py", "slivin_harness/execution.py", "slivin_harness/planner.py",
            "slivin_harness/evaluator.py", "tools/smoke_readonly_scratch.py")}
    summary["versions"] = {
        "codex": subprocess.check_output([str(args.codex), "--version"], text=True, encoding="utf-8").strip(),
        "node": subprocess.check_output([str(args.node), "--version"], text=True, encoding="utf-8").strip(),
        "jest": json.loads((project / "node_modules/jest/package.json").read_text(encoding="utf-8"))["version"],
    }
    try:
        with ObservedServer(args.codex, log_root=logs, client_version=__version__, execution_broker=broker,
            runtime_tmp=broker.scratch_root(ExecutionRole.APP_SERVER),
            process_env=broker.environment_for(ExecutionRole.APP_SERVER, preserve_sensitive=("OPENAI_API_KEY",))) as server:
            write_json(logs / "app_server_command.json", server._command())
            role_canaries: dict[ExecutionRole, Path] = {}
            for label, role in (("planner_initial", ExecutionRole.PLANNER), ("planner_fresh_after_cleanup", ExecutionRole.PLANNER),
                ("evaluator", ExecutionRole.EVALUATOR), ("planner_after_evaluator", ExecutionRole.PLANNER)):
                if label == "planner_fresh_after_cleanup":
                    server.retire_readonly_threads()
                    broker.clear_role_scratch(ExecutionRole.PLANNER)
                thread = server.start_thread(cwd=project, execution_role=role,
                    developer_instructions=PLANNER_INSTRUCTIONS if role == ExecutionRole.PLANNER else EVALUATOR_INSTRUCTIONS,
                    on_started=lambda metadata, label=label: write_json(logs / f"{label}_thread.json", metadata))
                context = server.get_thread_metadata(thread)["harness_execution_context"]
                scratch = Path(context["requested"]["scratch_root"])
                if list(scratch.iterdir()):
                    raise RuntimeError("Fresh role inherited scratch artifacts")
                # Check a genuinely granted, still-active peer root, not merely
                # an ungranted directory named "peer". Exercise both directions.
                peer = next((path for other, path in role_canaries.items() if other != role), canaries[0])
                if not peer.is_file():
                    raise RuntimeError("Missing peer canary")
                for turn_index in range(2 if label in {"planner_initial", "evaluator"} else 1):
                    phase = label + ("_initial" if turn_index == 0 else "_continuation")
                    server.phase = phase
                    initial = turn_index == 0
                    node = "'" + str(args.node).replace("'", "''") + "'"
                    command = f"& {node} .\\node_modules\\jest\\bin\\jest.js --config .\\jest.config.cjs --runInBand --runTestsByPath .\\tests\\arithmetic.test.cjs --watch=false"
                    prompt = f"""VALIDATION ONLY on disposable synthetic canaries. No product patch. Use project cwd {project} for every command; session root is scratch {scratch}. Keep existing permissions; never request escalation or install packages."""
                    if initial:
                        prompt += " Execute these Controller-specified repository instruction read commands exactly as written, separately and from project cwd:\n"
                        prompt += "\n".join(spec[3] for spec in INSTRUCTION_READ_SPECS) + "\n"
                    prompt += f"""Read src/arithmetic.cjs. Execute each validation command separately and capture the full result:
& {node} .\\sandbox_probe.cjs '{sibling}' '{private}' '{peer}'
{command}
"""
                    if initial:
                        prompt += command + "\n" + command.replace("arithmetic.test.cjs", "failing.test.cjs") + "\n"
                    prompt += "The negative operations in sandbox_probe.cjs are explicitly authorized attempts on disposable files and must be denied. Do not omit them. Do not change cache flags or config. "
                    if initial:
                        prompt += "The listed failing test is intentional and must execute its assertion. "
                    else:
                        prompt += "This continuation requires only the two listed commands; do not repeat the preceding turn's failing test or add another warm run. "
                    prompt += "Do not read secrets, external projects or other role artifacts. Controller captures command results; no additional JSON/files/verification commands are required. Return a brief diagnostic summary, not a product implementation/evaluation verdict."
                    before = len(server.commands)
                    result = guard.run_read_only(phase, lambda: server.run_turn(thread_id=thread, prompt=prompt, timeout=300,
                        on_heartbeat=lambda health: print("NATIVE_HEARTBEAT:", phase, round(health["turn_elapsed_seconds"]), flush=True)))
                    (logs / f"{phase}_response.txt").write_text(result, encoding="utf-8")
                    summary["phases"][phase] = validate_phase(server.commands[before:], project=project, scratch=scratch, peer=peer, initial=initial)
                    write_json(logs / "result.json", summary)
                role_canaries[role] = scratch / "positive/probe.txt"
        summary["status"] = "PASS"
    except Exception as exc:
        summary.update(status="FAIL", error={"type": type(exc).__name__, "reason": str(exc)})
    finally:
        after = {name: hashlib.sha256((project / name).read_bytes()).hexdigest() if (project / name).is_file() else None for name in files}
        source_after = asdict(fingerprint_runtime_tree(args.runtime_source))
        invariance = {"project_files_unchanged": hashes_before == after, "candidate_unchanged": candidate_before == identity().candidate_id,
            "source_runtime_unchanged": source_before == source_after, "source_runtime_before": source_before,
            "source_runtime_after": source_after, "project_before": hashes_before, "project_after": after,
            "canaries_unchanged": all(path.is_file() and path.read_text(encoding="utf-8") == "unchanged-canary\n" for path in canaries),
            "git_status": git(project, "status", "--short")}
        if not all(invariance[key] for key in ("project_files_unchanged", "candidate_unchanged", "source_runtime_unchanged", "canaries_unchanged")) or invariance["git_status"]:
            summary["status"] = "FAIL"
        write_json(logs / "invariance.json", invariance)
        summary['policy_evidence_sha256'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(logs.iterdir()) if path.is_file() and
            (path.name in {'app_server_command.json', 'requests.jsonl', 'commands.jsonl', 'invariance.json'}
             or path.name.endswith('_thread.json'))}
        write_json(logs / "result.json", summary)
    print("NATIVE_SCOPED_SCRATCH_" + summary["status"], logs, flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
