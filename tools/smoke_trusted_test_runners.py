"""Opt-in real Node/Jest checks + generic agent-double workflow, without LLM turns."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        if path.is_file():
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--jest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    node, jest = args.node.resolve(strict=True), args.jest.resolve(strict=True)
    if not node.is_file() or not jest.is_file():
        parser.error("Node and Jest must be existing files")
    runtime = next((p for p in jest.parents if p.name == "node_modules"), jest.parent)
    output = args.output or Path(tempfile.mkdtemp(prefix="slivin-trusted-runners-smoke-"))
    output.mkdir(parents=True, exist_ok=True)
    before = {"node_sha256": hashlib.sha256(node.read_bytes()).hexdigest(), "runtime_sha256": tree_digest(runtime)}
    versions = {}
    for label, command in (("node", [str(node), "--version"]), ("jest", [str(node), str(jest), "--version"])):
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        versions[label] = result.stdout.strip()
    env = os.environ.copy()
    env.update(SLIVIN_SMOKE_NODE=str(node), SLIVIN_SMOKE_JEST=str(jest), PYTHONIOENCODING="utf-8")
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_native_trusted_runners.py", "-v"]
    with (output / "commands_full.txt").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    after = {"node_sha256": hashlib.sha256(node.read_bytes()).hexdigest(), "runtime_sha256": tree_digest(runtime)}
    record = {"status": "PASS" if result.returncode == 0 and before == after else "FAIL",
              "scope": "Controller compiler/generated self-verify/Controller/reconstruction; agent replies are doubles; no sandbox smoke",
              "versions": versions, "command": command, "cwd": str(ROOT), "exit_code": result.returncode,
              "source_runtime_unchanged": before == after, "before": before, "after": after}
    (output / "summary.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print("TRUSTED_RUNNER_SMOKE:", record["status"], "EVIDENCE:", output)
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
