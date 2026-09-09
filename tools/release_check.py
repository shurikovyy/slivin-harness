"""One release gate. Mandatory NOT_RUN/SKIP/failure can never qualify a build."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
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


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    output = args.output or Path(tempfile.gettempdir()) / ("shr-release-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if output.is_relative_to(ROOT):
        raise RuntimeError("Native scratch acceptance requires --output outside the Harness checkout")
    identity = detect_harness_build_identity(harness_root=ROOT, version=__version__).to_dict()
    initial = source_manifest(ROOT)
    names = ("self_check", "boundary", "stateful", "mutations", "mixed_runners", "native_roles", "real_models")
    report = dict(schema_version="release-qualification.v1", status="NOT_QUALIFIED", profile=args.profile,
        started_at=datetime.now(timezone.utc).isoformat(), harness_build=identity, harness_source=initial,
        workflow=workflow_snapshot(harness_version=__version__), diagnostic=args.diagnostic,
        stages={name: dict(status="NOT_RUN") for name in names})
    write(output / "qualification.json", report)
    print("RELEASE_EVIDENCE:", output, flush=True)
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
            "native_roles": ([sys.executable, "tools/smoke_readonly_scratch.py", "--node", str(node), "--codex", str(codex), "--runtime-source", str(runtime), "--output", str(output / "native_roles")], 3600),
            "real_models": ([sys.executable, "tools/release_real_models.py", "--node", str(node), "--codex", str(codex), "--runtime-source", str(runtime), "--output", str(output / "real_models")], 28000),
        }
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                   SLIVIN_SMOKE_NODE=str(node), SLIVIN_SMOKE_JEST=str(jest))
        for name in names:
            command, timeout = commands[name]
            started = time.monotonic()
            report["stages"][name] = dict(status="RUNNING", command=command, timeout_seconds=timeout)
            write(output / "qualification.json", report)
            print("RELEASE_STAGE_START:", name, flush=True)
            with (output / (name + ".log")).open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            stage = report["stages"][name]
            stage.update(status="PASS" if result.returncode == 0 else "FAIL", exit_code=result.returncode, elapsed_seconds=round(time.monotonic()-started, 3))
            summary = output / name / ("diagnostics/result.json" if name == "native_roles" else "summary.json")
            if summary.is_file():
                stage["summary"] = str(summary)
                stage["summary_sha256"] = hashlib.sha256(summary.read_bytes()).hexdigest()
                if json.loads(summary.read_text(encoding="utf-8")).get("status") != "PASS":
                    stage["status"] = "FAIL"
            elif name != "self_check":
                stage.update(status="FAIL", reason="Mandatory machine-readable evidence missing")
            write(output / "qualification.json", report)
            print("RELEASE_STAGE_END:", name, stage["status"], flush=True)
            if stage["status"] != "PASS":
                break
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
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    write(output / "qualification.json", report)
    print(report["status"], output / "qualification.json", flush=True)
    return 0 if report["status"] == "RELEASE_QUALIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
