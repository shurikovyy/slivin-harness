"""Boot mandatory model-stage Python executables in their exact script mode."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from slivin_harness.entrypoint_boot import ENTRYPOINT_BOOT_SCHEMA


BOOT_TARGETS = (
    ("task_runner", "task_runner.py", "task_runner"),
    ("native_roles", "tools/smoke_readonly_scratch.py", "tools.smoke_readonly_scratch"),
    ("real_models", "tools/release_real_models.py", "tools.release_real_models"),
)


def boot_target(label: str, relative: str, expected_module: str) -> dict:
    path = ROOT / relative
    command = [sys.executable, relative, "--boot-check"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                               text=True, encoding="utf-8", timeout=60)
    record = {
        "label": label,
        "source": relative,
        "command": command,
        "exit_code": completed.returncode,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0:
        record.update(status="FAIL", reason="BOOT_PROCESS_FAILED")
        return record
    if len(lines) != 1:
        record.update(status="FAIL", reason="BOOT_OUTPUT_CARDINALITY")
        return record
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError:
        record.update(status="FAIL", reason="BOOT_OUTPUT_INVALID_JSON")
        return record
    expected = {
        "schema_version": ENTRYPOINT_BOOT_SCHEMA,
        "status": "PASS",
        "source": relative,
        "module": expected_module,
        "model_execution": "NOT_RUN",
    }
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        record.update(status="FAIL", reason="BOOT_CONTRACT_MISMATCH")
        return record
    boundaries = payload.get("boundary_entrypoints")
    if not isinstance(boundaries, list) or any(not isinstance(value, str) for value in boundaries):
        record.update(status="FAIL", reason="BOOT_BOUNDARY_EVIDENCE_INVALID")
        return record
    record.update(status="PASS", module=payload["module"],
                  boundary_entrypoints=boundaries,
                  model_execution=payload["model_execution"])
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    targets = [boot_target(*target) for target in BOOT_TARGETS]
    passed = all(target["status"] == "PASS" for target in targets)
    summary = {
        "schema_version": "release-entrypoint-boot-gate.v1",
        "status": "PASS" if passed else "FAIL",
        "boot_contract_schema": ENTRYPOINT_BOOT_SCHEMA,
        "targets": targets,
        "model_execution": "NOT_RUN",
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RELEASE_ENTRYPOINT_BOOT_" + summary["status"],
          "targets=", len(targets), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
