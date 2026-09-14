"""Opt-in, LLM-backed native acceptance; never part of offline self-check.

Only disposable canaries are mutated/attempted. Installed dependencies are
physically copied and fingerprinted; no installs, sandbox overrides or ACL edits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from slivin_harness.codex_transport import (
    CAPTURED_FORM_ORIGINS, TRANSPORT_SCHEMA, CanonicalExecutedCommand, CodexTransportAdapter,
    CodexTransportError, require_output, same_windows_path,
)
from slivin_harness.evaluator import EVALUATOR_INSTRUCTIONS
from slivin_harness.execution import ExecutionBroker, ExecutionRole
from slivin_harness.git_integrity import CandidateWorkspaceBaseline, GitControlIntegrityManager, TrustedBatchIntegrityCoordinator
from slivin_harness.planner import PLANNER_INSTRUCTIONS
from slivin_harness.run_state import build_candidate_identity
from slivin_harness.runtime_projection import RuntimeProjectionIntegrityManager, fingerprint_runtime_tree
from slivin_harness.workspace import RuntimeProjection, WorkspaceSession


PROBE = r"""
const fs=require('fs'),path=require('path'),os=require('os'),cp=require('child_process');
const [project,scratch,sibling,priv,peer]=process.argv.slice(2);
const rows=[],errors=[];
function same(actual,expected){return typeof actual==='string'&&typeof expected==='string'&&
 path.isAbsolute(actual)&&path.isAbsolute(expected)&&path.resolve(actual).toLowerCase()===path.resolve(expected).toLowerCase();}
if(process.argv.length!==7)errors.push('argument-count');
if(!same(process.cwd(),project))errors.push('project-cwd');
for(const key of ['TEMP','TMP'])if(!same(process.env[key],scratch))errors.push(key);
if(!same(os.tmpdir(),scratch))errors.push('os.tmpdir');
for(const [name,target] of [['sibling',sibling],['private',priv],['peer',peer]]){
 if(!path.isAbsolute(target||''))errors.push(name+'-target');
 else try{if(!fs.statSync(name==='peer'?target:path.join(target,'canary.txt')).isFile())errors.push(name+'-target');}
 catch(e){errors.push(name+'-target:'+e.code);}
}
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
negatives('absolute',false);negatives('relative-after-chdir',true);
const child=cp.spawnSync(process.execPath,['-p','40+2'],{encoding:'utf8'});
const expected=['scratch:mkdir','scratch:write-read'];
for(const label of ['absolute','relative-after-chdir']){
 for(const file of ['src/arithmetic.cjs','tests/arithmetic.test.cjs','node_modules/jest/package.json'])expected.push(label+':write:'+file);
 for(const name of ['create','delete','rename','sibling','private','peer','git'])expected.push(label+':'+name);
}
if(rows.length!==22||rows.some((row,index)=>row.name!==expected[index]))errors.push('operation-count-or-order');
for(const row of rows){
 if(row.name.startsWith('scratch:')){if(row.result!=='ALLOWED')errors.push(row.name+':not-allowed');}
 else if(row.result!=='DENIED'||!['EPERM','EACCES'].includes(row.code))errors.push(row.name+':not-policy-denial');
}
console.log('PROBE_RESULT='+JSON.stringify({cwd:process.cwd(),tmpdir:os.tmpdir(),scratch,peer,rows,errors,
 child:{status:child.status,stdout:child.stdout,stderr:child.stderr,error:child.error?{code:child.error.code,syscall:child.error.syscall}:null}}));
if(errors.length)process.exitCode=7;
"""

INSTRUCTION_READ_SPECS = (
    ("root", "AGENTS.md", "ROOT_INSTRUCTIONS_READ",
     "[System.IO.File]::ReadAllText('AGENTS.md', [System.Text.Encoding]::UTF8)"),
    ("nested", "src/AGENTS.md", "NESTED_INSTRUCTIONS_READ",
     r"[System.IO.File]::ReadAllText('src\AGENTS.md', [System.Text.Encoding]::UTF8)"),
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
        self.transport = CodexTransportAdapter()
        self.commands = self.transport.commands

    def request(self, method, params, **kwargs):
        if method in {"thread/start", "turn/start", "thread/archive"}:
            with (self.log_root / "requests.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"method": method, "params": params}, ensure_ascii=False) + "\n")
        return super().request(method, params, **kwargs)

    def _receive_raw_optional(self, timeout):
        message = super()._receive_raw_optional(timeout)
        if message:
            if not isinstance(message, dict):
                raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
            method = message.get("method")
            if method not in {"item/completed", "item/commandExecution/outputDelta"}:
                return message
            params = message.get("params")
            if not isinstance(params, dict):
                raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
            item = params.get("item")
            if method == "item/completed" and not isinstance(item, dict):
                raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
            if method == "item/completed" and item.get("type") == "commandExecution":
                record = {"phase": self.phase, **{key: item.get(key) for key in (
                    "id", "command", "cwd", "exitCode", "durationMs", "aggregatedOutput")}}
                with (self.log_root / "commands.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.transport.observe_completed(item, phase=self.phase,
                    thread_id=params.get("threadId"), turn_id=params.get("turnId"))
                print("NATIVE_COMMAND:", self.phase, item.get("exitCode"), flush=True)
            elif method == "item/commandExecution/outputDelta":
                with (self.log_root / "output_deltas.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"phase": self.phase, "params": message["params"]}, ensure_ascii=False) + "\n")
                self.transport.observe_delta(params, phase=self.phase)
        return message


def _require_execution(commands: list[CanonicalExecutedCommand], *, expected: str,
                       project: Path, exit_code: int, label: str,
                       route: str | None = None) -> CanonicalExecutedCommand:
    candidates = [row for row in commands if row.payload == expected or (
        route is not None and row.payload.startswith("& '") and route in row.payload
    )]
    if len(candidates) > 1:
        raise CodexTransportError("DUPLICATE_EXECUTION", context=label, layer="consumer")
    if not candidates or candidates[0].shell != "powershell" or candidates[0].payload != expected or (
        not same_windows_path(candidates[0].cwd, str(project))
    ):
        raise CodexTransportError("COMMAND_IDENTITY_MISMATCH", context=label, layer="consumer")
    if candidates[0].exit_code != exit_code:
        raise CodexTransportError("COMMAND_EXECUTION_FAILED", context=label,
            item_id=candidates[0].item_id, layer="consumer")
    return candidates[0]


def validate_instruction_read_evidence(commands: list[CanonicalExecutedCommand], *, project: Path) -> dict:
    """Require the exact Controller-provided root and nested read commands."""
    evidence = {}
    for label, relative_path, marker, expected_script in INSTRUCTION_READ_SPECS:
        row = _require_execution(commands, expected=expected_script, project=project,
            exit_code=0, label=relative_path)
        evidence[label] = {
            "status": "PASS",
            "path": relative_path,
            "output_marker_observed": marker in (row.output or ""),
        }
    return evidence


def probe_command(*, node: Path, project: Path, scratch: Path, sibling: Path,
                  private: Path, peer: Path) -> str:
    """Exact Controller-selected PowerShell payload for the immutable probe."""
    def quote(value: Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"
    return (f"& {quote(node)} .\\sandbox_probe.cjs {quote(project)} {quote(scratch)} "
            f"{quote(sibling)} {quote(private)} {quote(peer)}")


def validate_probe_evidence(commands: list[CanonicalExecutedCommand], *, node: Path, project: Path,
                            scratch: Path, sibling: Path, private: Path, peer: Path) -> dict:
    expected = probe_command(node=node, project=project, scratch=scratch,
                             sibling=sibling, private=private, peer=peer)
    probe_row = _require_execution(commands, expected=expected, project=project,
        exit_code=0, label="sandbox_probe.cjs", route=" .\\sandbox_probe.cjs ")
    output = probe_row.output
    structured = None
    if isinstance(output, str):
        for line in output.splitlines():
            if line.startswith("PROBE_RESULT="):
                try:
                    parsed = json.loads(line[len("PROBE_RESULT="):])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    structured = parsed
                    break
    return {"probe_exit_evidence": "PASS", "structured_output_observed": structured is not None,
            "structured_output": structured}


def jest_command(node: Path, test_file: str) -> str:
    quote = "'" + str(node).replace("'", "''") + "'"
    return (f"& {quote} .\\node_modules\\jest\\bin\\jest.js --config .\\jest.config.cjs "
            f"--runInBand --runTestsByPath .\\tests\\{test_file} --watch=false")


def validate_phase(commands: list[CanonicalExecutedCommand], *, node: Path, project: Path, scratch: Path,
                   sibling: Path, private: Path, peer: Path, initial: bool) -> dict:
    probe_evidence = validate_probe_evidence(commands, node=node, project=project,
        scratch=scratch, sibling=sibling, private=private, peer=peer)
    probe = probe_evidence["structured_output"]
    pass_payload = jest_command(node, "arithmetic.test.cjs")
    fail_payload = jest_command(node, "failing.test.cjs")
    jest = [row for row in commands if row.payload.startswith("& '") and
        " .\\node_modules\\jest\\bin\\jest.js " in row.payload]
    if any(row.payload not in {pass_payload, fail_payload} or
        not same_windows_path(row.cwd, str(project)) for row in jest):
        raise CodexTransportError("COMMAND_IDENTITY_MISMATCH", context="jest-route", layer="consumer")
    passed = [row for row in jest if row.payload == pass_payload]
    failed = [row for row in jest if row.payload == fail_payload]
    if len(passed) != (2 if initial else 1) or len(failed) != (1 if initial else 0):
        raise CodexTransportError("COMMAND_IDENTITY_MISMATCH", context="jest-cardinality", layer="consumer")
    for row in passed:
        if row.exit_code != 0 or "Tests:       1 passed, 1 total" not in require_output(row):
            raise CodexTransportError("JEST_ASSERTION_EVIDENCE_FAILED", item_id=row.item_id,
                layer="consumer")
    for row in failed:
        output = require_output(row)
        if row.exit_code != 1 or any(marker not in output for marker in (
            "intentional failing assertion", "Expected: 999", "Received: 5"
        )):
            raise CodexTransportError("JEST_ASSERTION_EVIDENCE_FAILED", item_id=row.item_id,
                layer="consumer")
    # Preserve evidence for long haste-map names as well as the shorter perf cache.
    scan_root = Path("\\\\?\\" + str(scratch)) if os.name == "nt" else scratch
    cache_files = sorted(str(path.relative_to(scan_root)) for path in (scan_root / "jest").rglob("*") if path.is_file())
    if not cache_files:
        raise RuntimeError("No actual Jest cache files in role scratch")
    instruction_reads = validate_instruction_read_evidence(commands, project=project) if initial else None
    observed_rows = probe.get("rows") if probe else None
    negative = ([row for row in observed_rows if not row["name"].startswith("scratch:")]
        if isinstance(observed_rows, list) and all(
            isinstance(row, dict) and isinstance(row.get("name"), str) for row in observed_rows)
        else None)
    result = {"status": "PASS", "probe_exit_evidence": "PASS",
        "structured_output_observed": probe_evidence["structured_output_observed"],
        "negative_attempts_denied": (sum(row.get("result") == "DENIED" and
            row.get("code") in {"EPERM", "EACCES"} for row in negative)
            if negative is not None else None), "jest_passes": len(passed),
        "intentional_assertion_failures": len(failed), "cache_files": cache_files,
        "transport_schema": TRANSPORT_SCHEMA,
        "transport_forms": sorted({row.transport_form for row in commands}),
        "transport_form_corpus_match": {form: CAPTURED_FORM_ORIGINS.get(form)
            for form in sorted({row.transport_form for row in commands})},
        "output_sources": sorted({source for row in commands for source in row.output_sources}),
        "peer_canary": str(peer), "child_process_observation": probe.get("child") if probe else None}
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
        for name in ("slivin_harness/app_server.py", "slivin_harness/codex_transport.py",
            "slivin_harness/execution.py", "slivin_harness/planner.py",
            "slivin_harness/evaluator.py", "tools/smoke_readonly_scratch.py")}
    summary["versions"] = {
        "codex": subprocess.check_output([str(args.codex), "--version"], text=True, encoding="utf-8").strip(),
        "node": subprocess.check_output([str(args.node), "--version"], text=True, encoding="utf-8").strip(),
        "jest": json.loads((project / "node_modules/jest/package.json").read_text(encoding="utf-8"))["version"],
    }
    server: ObservedServer | None = None
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
                    command = jest_command(args.node, "arithmetic.test.cjs")
                    prompt = f"""VALIDATION ONLY on disposable synthetic canaries. No product patch. Use project cwd {project} for every command; session root is scratch {scratch}. Keep existing permissions; never request escalation or install packages."""
                    if initial:
                        prompt += " Execute these Controller-specified repository instruction read commands exactly as written, separately and from project cwd:\n"
                        prompt += "\n".join(spec[3] for spec in INSTRUCTION_READ_SPECS) + "\n"
                    selected_probe = probe_command(node=args.node, project=project, scratch=scratch,
                        sibling=sibling, private=private, peer=peer)
                    prompt += f"""Read src/arithmetic.cjs. Execute each validation command separately and capture the full result:
{selected_probe}
{command}
"""
                    if initial:
                        prompt += command + "\n" + jest_command(args.node, "failing.test.cjs") + "\n"
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
                    server.transport.assert_phase_complete(phase)
                    summary["phases"][phase] = validate_phase(server.commands[before:], node=args.node,
                        project=project, scratch=scratch, sibling=sibling, private=private,
                        peer=peer, initial=initial)
                    write_json(logs / "result.json", summary)
                role_canaries[role] = scratch / "positive/probe.txt"
        summary["status"] = "PASS"
    except Exception as exc:
        error = {"type": type(exc).__name__, "reason": str(exc)}
        if isinstance(exc, CodexTransportError):
            observed_forms = sorted({row.transport_form for row in server.commands}) if server else []
            error.update(reason_code=exc.reason_code,
                canonicalization_status="PASS" if exc.layer == "consumer" else "FAIL",
                transport_schema=TRANSPORT_SCHEMA,
                raw_transport_form_category=exc.transport_form or "not_available",
                transport_form_corpus_match=CAPTURED_FORM_ORIGINS.get(exc.transport_form),
                observed_transport_forms=observed_forms,
                canonicalized_command_count=len(server.commands) if server else 0)
        summary.update(status="FAIL", error=error)
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
            (path.name in {'app_server_command.json', 'requests.jsonl', 'commands.jsonl',
                           'output_deltas.jsonl', 'invariance.json'}
             or path.name.endswith('_thread.json'))}
        write_json(logs / "result.json", summary)
    print("NATIVE_SCOPED_SCRATCH_" + summary["status"], logs, flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
