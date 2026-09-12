"""One release gate. Mandatory NOT_RUN/SKIP/failure can never qualify a build."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from slivin_harness import __version__
from slivin_harness.build_identity import detect_harness_build_identity, source_manifest
from slivin_harness.workflow import workflow_snapshot
from tools.release_identity import codex_launch_identity


RELEASE_LOG_TAIL_LINES = 60
RELEASE_LOG_TAIL_BYTES = 262_144
RELEASE_LOG_LINE_CHARS = 2_000
RELEASE_SUMMARY_DIAGNOSTIC_BYTES = 1_048_576
RELEASE_STAGES = (
    "self_check", "boundary", "stateful", "mutations", "mixed_runners",
    "artifact_replay", "native_roles", "real_models",
)
MODEL_BACKED_STAGES = frozenset({"native_roles", "real_models"})
_SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:TOKEN|PASSWORD|PASSWD|SECRET|API_KEY|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET|CREDENTIAL)(?:_|$)",
    re.IGNORECASE,
)
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(authorization|api[_-]?key|token|password|passwd|secret|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret|credential)(\s*[:=]\s*)((?:bearer\s+)?[^\s,;]+)"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_output_root() -> Path:
    """Keep enough Windows path headroom for nested worktree runtime copies."""
    return Path(tempfile.gettempdir()) / ("shr-q-" + uuid.uuid4().hex[:10])


def _sanitize_console_text(value: object, *, environ: dict[str, str]) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    secrets = {
        secret for key, secret in environ.items()
        if secret and len(secret) >= 4 and _SECRET_NAME_RE.search(key)
    }
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    text = _INLINE_SECRET_RE.sub(r"\1\2<redacted>", text)
    if len(text) > RELEASE_LOG_LINE_CHARS:
        text = text[:RELEASE_LOG_LINE_CHARS] + "...<truncated>"
    return text


def _safe_summary_diagnostic(path: Path) -> dict[str, str]:
    try:
        if path.is_symlink():
            return {"read_status": "SUMMARY_UNSAFE_LINK"}
        if path.stat().st_size > RELEASE_SUMMARY_DIAGNOSTIC_BYTES:
            return {"read_status": "SUMMARY_TOO_LARGE"}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"read_status": "SUMMARY_UNREADABLE"}
    if not isinstance(payload, dict):
        return {"read_status": "SUMMARY_NOT_OBJECT"}
    diagnostic = {"read_status": "OK"}
    for key in ("status", "reason_code", "result_code"):
        if isinstance(payload.get(key), (str, int, float, bool)):
            diagnostic[key] = str(payload[key])
    if isinstance(payload.get("reason"), (str, int, float, bool)):
        diagnostic["reason"] = str(payload["reason"])
    error = payload.get("error")
    if isinstance(error, dict):
        for source, target in (("type", "reason_type"), ("status", "error_status"),
                               ("reason_code", "error_reason_code"), ("reason", "reason")):
            if target not in diagnostic and isinstance(error.get(source), (str, int, float, bool)):
                diagnostic[target] = str(error[source])
    elif "reason" not in diagnostic and isinstance(error, (str, int, float, bool)):
        diagnostic["reason"] = str(error)
    return diagnostic


def _bounded_log_tail(path: Path) -> list[str]:
    try:
        if path.is_symlink():
            return []
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - RELEASE_LOG_TAIL_BYTES)
            handle.seek(start)
            raw = handle.read(RELEASE_LOG_TAIL_BYTES)
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start and lines:
        lines = lines[1:]
    return lines[-RELEASE_LOG_TAIL_LINES:]


def emit_stage_failure_diagnostics(
    stage_name: str,
    *,
    log_path: Path,
    summary_path: Path | None,
    environ: dict[str, str] | None = None,
) -> None:
    safe_env = dict(os.environ if environ is None else environ)
    print("RELEASE_STAGE_FAILURE:", stage_name, flush=True)
    print("RELEASE_STAGE_LOG:", log_path.resolve(), flush=True)
    if summary_path is not None and summary_path.is_file():
        print("RELEASE_STAGE_SUMMARY:", summary_path.resolve(), flush=True)
        for key, value in _safe_summary_diagnostic(summary_path).items():
            print(
                "RELEASE_STAGE_SUMMARY_" + key.upper() + ":",
                _sanitize_console_text(value, environ=safe_env),
                flush=True,
            )
    tail = _bounded_log_tail(log_path)
    print("RELEASE_STAGE_LOG_TAIL_BEGIN:", stage_name, "lines=" + str(len(tail)), flush=True)
    for line in tail:
        print("| " + _sanitize_console_text(line, environ=safe_env), flush=True)
    print("RELEASE_STAGE_LOG_TAIL_END:", stage_name, flush=True)


def execute_mandatory_stage_sequence(stage_names, execute) -> tuple[str, ...]:
    """Stop before every later stage when one mandatory predecessor fails."""
    visited = []
    for name in stage_names:
        visited.append(name)
        if not execute(name):
            break
    return tuple(visited)


def validate_release_stage_order(stage_names) -> None:
    """Known transcript replay must gate every model-backed qualification stage."""
    names = tuple(stage_names)
    if len(names) != len(set(names)) or names.count("artifact_replay") != 1:
        raise RuntimeError("Release stage order requires one unique artifact_replay stage")
    replay_index = names.index("artifact_replay")
    if not MODEL_BACKED_STAGES.issubset(names) or any(
        names.index(name) <= replay_index for name in MODEL_BACKED_STAGES
    ):
        raise RuntimeError("Artifact replay must precede every model-backed release stage")


def tools_from_profile(args):
    # Only explicit tool paths and runtime directories are read from the local
    # profile. It is never edited, copied into fixtures or emitted in evidence.
    import task_runner
    config, _ = task_runner.load_local_config()
    codex = args.codex or task_runner.resolve_codex_cmd(config)
    node, jest = args.node, args.jest
    if node is None or jest is None:
        for name, project in config.get("projects", {}).items():
            if "jest" not in project.get("toolchain", {}):
                continue
            toolchain = task_runner.resolve_toolchain(config, {}, project_name=name, project_root=Path(project["repo"]))
            candidate_node, candidate_jest = Path(toolchain.get("node", "")), Path(toolchain.get("jest", ""))
            if candidate_node.is_file() and candidate_jest.is_file():
                node, jest = node or candidate_node, jest or candidate_jest
                break
    if node is None or jest is None:
        raise RuntimeError("Installed Node/Jest are required; pass --node and --jest or configure existing local tool paths")
    node, jest, codex = (path.resolve(strict=True) for path in (node, jest, codex))
    runtime = next((parent for parent in jest.parents if parent.name == "node_modules"), None)
    if runtime is None or not (runtime / "jest/package.json").is_file():
        raise RuntimeError("Jest must belong to a physical installed node_modules runtime")
    record = {}
    for name, executable, argv in (("node", node, [str(node), "--version"]), ("jest", jest, [str(node), str(jest), "--version"]), ("codex", codex, [str(codex), "--version"])):
        version = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", check=True, timeout=30).stdout.strip()
        record[name] = dict(path=str(executable), sha256=hashlib.sha256(executable.read_bytes()).hexdigest(), version=version)
    record['codex']['launch_chain'] = codex_launch_identity(codex, cwd=ROOT)
    return node, jest, codex, runtime, record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("windows-local",), required=True)
    for name in ("node", "jest", "codex", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--diagnostic", action="store_true", help="Allow a dirty development tree; outcome is always NOT_QUALIFIED")
    args = parser.parse_args()
    output = args.output or default_output_root()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if output.is_relative_to(ROOT):
        raise RuntimeError("Native scratch acceptance requires --output outside the Harness checkout")
    identity = detect_harness_build_identity(harness_root=ROOT, version=__version__).to_dict()
    initial = source_manifest(ROOT)
    names = RELEASE_STAGES
    validate_release_stage_order(names)
    report = dict(schema_version="release-qualification.v1", status="NOT_QUALIFIED", profile=args.profile,
        started_at=datetime.now(timezone.utc).isoformat(), harness_build=identity, harness_source=initial,
        workflow=workflow_snapshot(harness_version=__version__), diagnostic=args.diagnostic,
        stages={name: dict(status="NOT_RUN") for name in names})
    write(output / "qualification.json", report)
    print("RELEASE_EVIDENCE:", output, flush=True)
    active_stage: tuple[str, Path, Path] | None = None
    failure_diagnostics_emitted = False
    try:
        if os.name != "nt":
            raise RuntimeError("windows-local requires native Windows")
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        if not args.diagnostic and (status or identity["git_dirty"] is not False or not identity["git_commit"]):
            raise RuntimeError("Qualification requires a frozen, clean Git commit")
        node, jest, codex, runtime, versions = tools_from_profile(args)
        report["executables"] = versions
        commands = {
            "self_check": ([sys.executable, "tools/self_check.py"], 2400),
            "boundary": ([sys.executable, "tools/release_suite.py", "--stage", "boundary", "--output", str(output / "boundary")], 2400),
            "stateful": ([sys.executable, "tools/release_suite.py", "--stage", "stateful", "--output", str(output / "stateful")], 900),
            "mutations": ([sys.executable, "tools/release_mutations.py", "--output", str(output / "mutations")], 1800),
            "mixed_runners": ([sys.executable, "tools/smoke_trusted_test_runners.py", "--node", str(node), "--jest", str(jest), "--output", str(output / "mixed_runners")], 900),
            "artifact_replay": ([sys.executable, "tools/replay_model_artifact_transcripts.py", "--output", str(output / "artifact_replay")], 300),
            "native_roles": ([sys.executable, "tools/smoke_readonly_scratch.py", "--node", str(node), "--codex", str(codex), "--runtime-source", str(runtime), "--output", str(output / "native_roles")], 3600),
            "real_models": ([sys.executable, "tools/release_real_models.py", "--node", str(node), "--codex", str(codex), "--runtime-source", str(runtime), "--output", str(output / "real_models")], 28000),
        }
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                   SLIVIN_SMOKE_NODE=str(node), SLIVIN_SMOKE_JEST=str(jest))
        def execute_stage(name: str) -> bool:
            nonlocal active_stage, failure_diagnostics_emitted
            command, timeout = commands[name]
            started = time.monotonic()
            log_path = output / (name + ".log")
            summary = output / name / ("diagnostics/result.json" if name == "native_roles" else "summary.json")
            active_stage = (name, log_path, summary)
            report["stages"][name] = dict(status="RUNNING", command=command, timeout_seconds=timeout,
                                           log=str(log_path))
            write(output / "qualification.json", report)
            print("RELEASE_STAGE_START:", name, flush=True)
            with log_path.open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            stage = report["stages"][name]
            stage.update(status="PASS" if result.returncode == 0 else "FAIL", exit_code=result.returncode, elapsed_seconds=round(time.monotonic()-started, 3))
            if summary.is_file():
                stage["summary"] = str(summary)
                try:
                    raw_summary = summary.read_bytes()
                    summary_payload = json.loads(raw_summary.decode("utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    stage.update(status="FAIL", reason="Mandatory machine-readable evidence unreadable",
                                 summary_error_type=type(error).__name__)
                else:
                    stage["summary_sha256"] = hashlib.sha256(raw_summary).hexdigest()
                    if not isinstance(summary_payload, dict) or summary_payload.get("status") != "PASS":
                        stage["status"] = "FAIL"
            elif name != "self_check":
                stage.update(status="FAIL", reason="Mandatory machine-readable evidence missing")
            write(output / "qualification.json", report)
            print("RELEASE_STAGE_END:", name, stage["status"], flush=True)
            if stage["status"] != "PASS":
                emit_stage_failure_diagnostics(name, log_path=log_path, summary_path=summary, environ=env)
                failure_diagnostics_emitted = True
                return False
            active_stage = None
            return True
        execute_mandatory_stage_sequence(names, execute_stage)
        report["source_unchanged"] = initial == source_manifest(ROOT)
        # Resolve again as well as hashing: an unchanged shim can select another
        # PATH/local Node or platform package/native payload after the run.
        report["executables_after"] = tools_from_profile(args)[4]
        report["executables_unchanged"] = report["executables_after"] == versions
        qualified = not args.diagnostic and report["source_unchanged"] and report["executables_unchanged"] and all(stage["status"] == "PASS" for stage in report["stages"].values())
        report["status"] = "RELEASE_QUALIFIED" if qualified else "NOT_QUALIFIED"
    except Exception as error:
        report["error"] = dict(type=type(error).__name__, reason=str(error))
        for stage in report["stages"].values():
            if stage["status"] == "RUNNING":
                stage.update(status="FAIL", reason="Execution interrupted; see release error")
        if active_stage is not None and not failure_diagnostics_emitted:
            name, log_path, summary = active_stage
            emit_stage_failure_diagnostics(name, log_path=log_path, summary_path=summary,
                                           environ=locals().get("env", dict(os.environ)))
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    write(output / "qualification.json", report)
    print(report["status"], output / "qualification.json", flush=True)
    return 0 if report["status"] == "RELEASE_QUALIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
