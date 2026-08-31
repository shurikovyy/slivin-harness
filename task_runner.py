from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from slivin_harness import __version__
from slivin_harness.app_server import CodexAppServer, TurnTimeoutError
from slivin_harness.console import configure_utf8_stdio
from slivin_harness.evaluator import run_evaluator
from slivin_harness.implementer import (
    IMPLEMENTER_REPORT_SCHEMA,
    build_implementation_contract,
    compact_plan_context,
    parse_implementation_report,
    validate_implementation_report,
)
from slivin_harness.planner import run_planner
from slivin_harness.protocol import (
    ArtifactContractError,
    EVALUATOR_PROTOCOL_VERSION,
    MANIFEST_VERSION,
    PLANNER_PROTOCOL_VERSION,
    ensure_exact_keys,
    plan_fingerprint,
    require_string_list,
    require_type,
    safe_repo_relative,
    stable_fingerprint,
)
from slivin_harness.run_state import RunState, build_candidate_identity
from slivin_harness.workflow import (
    EvaluatorStatus,
    InvalidationTrigger,
    ImplementerStatus,
    PipelineProfile,
    PlannerStatus,
    RevisionKind,
    StageId,
    StageResultCode,
    WORKFLOW_VERSION,
    WorkflowMode,
    WorkflowOutcome,
    enum_values,
    workflow_snapshot,
)
from slivin_harness.workspace import (
    WorkspaceSession,
    apply_candidate_to_source,
    build_candidate_patch,
    prepare_workspace_session,
)

configure_utf8_stdio()
HARNESS_ROOT = Path(__file__).resolve().parent
DEFAULT_CODEX_NAMES = ("codex.cmd", "codex")

IMPLEMENTER_INSTRUCTIONS = """
Ты Implementer внутри Slivin Harness.

Работай только в текущем workspace и следуй repository instructions/AGENTS.md.
Запрещены git add/commit/push/pull/merge/rebase/reset/restore/switch/checkout/clean.
Не меняй .git и не ищи готовые решения в других копиях проекта или архивах.

Правила работы:
- сначала проверь исходную задачу и compact plan по реальному коду;
- Implementation Contract — обязательный список результата, preservation и найденных Planner consumers; plan остаётся гипотезой, а не готовым patch;
- до завершения явно проверь каждый contract item; consumer можно отметить NOT_APPLICABLE только с конкретным evidence, что он недостижим/не затронут;
- делай минимальное целостное исправление, включая достижимых sibling consumers;
- сохрани явно требуемое старое поведение и target уже начатого stateful action;
- обнови документацию, если contract требует это или реально изменился пользовательский/API/архитектурный контракт;
- добавь contract-oriented regression tests, а не проверку конкретной формы patch;
- обязательно запусти перед завершением Harness-owned SELF_VERIFY_COMMAND: он использует тот же trusted toolchain и те же repair checks, что затем независимо повторит Controller;
- если при исследовании найдены дополнительные существующие test files для materially affected consumers, укажи их в additional_check_paths; Controller безопасно перезапустит поддерживаемые test paths;
- temp/cache размещай в .harness_tmp;
- если две разные попытки записи завершаются Permission denied/Access denied, не повторяй их: зафиксируй инфраструктурную блокировку;
- не ослабляй тесты ради PASS;
- финальный ответ — structured Implementation Report. COMPLETE допустим только после self-verification PASS и проверки всего Implementation Contract.
""".strip()

TOP_LEVEL_FIELDS = {
    "version",
    "task_id",
    "project",
    "workspace",
    "workspace_mode",
    "base_ref",
    "result_mode",
    "risk",
    "max_fix_cycles",
    "max_replan_cycles",
    "turn_timeout_seconds",
    "require_clean_git",
    "allowed_paths",
    "prompt",
    "toolchain",
    "benchmark",
    "checks",
}
CHECK_FIELDS = {"name", "feedback", "command", "timeout_seconds"}
BENCHMARK_FIELDS = {"calibration_certificate", "confirm_current_baseline_broken", "baseline_failure_marker"}


@dataclass
class CheckResult:
    name: str
    command: list[str]
    returncode: int
    output: str
    timed_out: bool = False
    duration_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class RunRecorder:
    def __init__(self, task_id: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = hashlib.sha256(os.urandom(16)).hexdigest()[:8]
        self.root = HARNESS_ROOT / "runs" / task_id / f"{stamp}-{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, value: object) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path

    def write_text(self, name: str, value: str) -> Path:
        path = self.root / name
        path.write_text(value, encoding="utf-8", newline="\n")
        return path

    def write_bytes(self, name: str, value: bytes) -> Path:
        path = self.root / name
        path.write_bytes(value)
        return path


def load_manifest(path: Path) -> dict:
    with path.open("rb") as handle:
        manifest = tomllib.load(handle)
    validate_manifest(manifest)
    return manifest


def _require_int_range(
    manifest: dict,
    field: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = manifest.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RuntimeError(f"{field} must be an integer in range {minimum}..{maximum}")
    return value


def validate_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict):
        raise RuntimeError("Task manifest must be a TOML table")
    unknown = sorted(set(manifest) - TOP_LEVEL_FIELDS)
    if unknown:
        raise RuntimeError("Unknown task manifest fields: " + ", ".join(unknown))
    if manifest.get("version") != MANIFEST_VERSION:
        raise RuntimeError(
            f"Unsupported manifest version {manifest.get('version')!r}; expected {MANIFEST_VERSION}"
        )
    for field in ("task_id", "prompt"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise RuntimeError(f"{field} must be a non-empty string")
    if bool(manifest.get("project")) == bool(manifest.get("workspace")):
        raise RuntimeError("Define exactly one of project=... or workspace=...")
    if "workspace_mode" in manifest and manifest["workspace_mode"] != "git_worktree":
        raise RuntimeError("workspace_mode must be 'git_worktree'")
    if manifest.get("result_mode", "keep_worktree") not in {"keep_worktree", "apply_to_source"}:
        raise RuntimeError("result_mode must be keep_worktree or apply_to_source")
    risk = manifest.get("risk", "medium")
    if risk not in {"low", "medium", "high"}:
        raise RuntimeError("risk must be low, medium or high")
    _require_int_range(manifest, "max_fix_cycles", default=2, minimum=0, maximum=5)
    _require_int_range(manifest, "max_replan_cycles", default=1, minimum=0, maximum=2)
    _require_int_range(
        manifest,
        "turn_timeout_seconds",
        default=900,
        minimum=60,
        maximum=3600,
    )
    if "require_clean_git" in manifest and not isinstance(manifest["require_clean_git"], bool):
        raise RuntimeError("require_clean_git must be boolean")

    allowed_paths = manifest.get("allowed_paths", [])
    if not isinstance(allowed_paths, list) or not all(isinstance(item, str) for item in allowed_paths):
        raise RuntimeError("allowed_paths must be an array of strings")
    for index, raw in enumerate(allowed_paths):
        safe_repo_relative(raw, field=f"allowed_paths[{index}]")

    toolchain = manifest.get("toolchain", {})
    if not isinstance(toolchain, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in toolchain.items()
    ):
        raise RuntimeError("toolchain must be a table of string paths")

    benchmark = manifest.get("benchmark", {})
    if not isinstance(benchmark, dict):
        raise RuntimeError("benchmark must be a table")
    benchmark_unknown = sorted(set(benchmark) - BENCHMARK_FIELDS)
    if benchmark_unknown:
        raise RuntimeError("Unknown benchmark fields: " + ", ".join(benchmark_unknown))
    if "calibration_certificate" in benchmark and not isinstance(
        benchmark["calibration_certificate"], str
    ):
        raise RuntimeError("benchmark.calibration_certificate must be a string")
    if "confirm_current_baseline_broken" in benchmark and not isinstance(
        benchmark["confirm_current_baseline_broken"], bool
    ):
        raise RuntimeError("benchmark.confirm_current_baseline_broken must be boolean")
    if "baseline_failure_marker" in benchmark and (
        not isinstance(benchmark["baseline_failure_marker"], str)
        or not benchmark["baseline_failure_marker"].strip()
    ):
        raise RuntimeError("benchmark.baseline_failure_marker must be a non-empty string")
    if benchmark.get("confirm_current_baseline_broken") and not benchmark.get(
        "baseline_failure_marker"
    ):
        raise RuntimeError(
            "benchmark.confirm_current_baseline_broken requires baseline_failure_marker"
        )

    checks = manifest.get("checks")
    if not isinstance(checks, list) or not checks:
        raise RuntimeError("Task manifest requires at least one [[checks]] entry")
    for index, spec in enumerate(checks):
        if not isinstance(spec, dict):
            raise RuntimeError(f"checks[{index}] must be a table")
        unknown_check = sorted(set(spec) - CHECK_FIELDS)
        missing = sorted({"name", "feedback", "command", "timeout_seconds"} - set(spec))
        if unknown_check:
            raise RuntimeError(f"Unknown fields in checks[{index}]: {', '.join(unknown_check)}")
        if missing:
            raise RuntimeError(f"Missing fields in checks[{index}]: {', '.join(missing)}")
        if not isinstance(spec["name"], str) or not spec["name"].strip():
            raise RuntimeError(f"checks[{index}].name must be non-empty")
        if spec["feedback"] not in {"repair", "heldout"}:
            raise RuntimeError(f"checks[{index}].feedback must be repair or heldout")
        if not isinstance(spec["command"], list) or not spec["command"] or not all(
            isinstance(item, str) for item in spec["command"]
        ):
            raise RuntimeError(f"checks[{index}].command must be a non-empty string array")
        timeout = spec["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise RuntimeError(f"checks[{index}].timeout_seconds must be 1..3600")


def resolve_runtime_path(
    raw_path: str | Path,
    *,
    base: Path = HARNESS_ROOT,
    project_root: Path | None = None,
) -> Path:
    value = os.path.expandvars(str(raw_path)).format(
        home=str(Path.home()),
        harness_root=str(HARNESS_ROOT),
        project_root=str(project_root) if project_root else "",
    )
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def resolve_harness_path(raw_path: str | Path) -> Path:
    return resolve_runtime_path(raw_path)


def load_local_config() -> tuple[dict, Path | None]:
    override = os.environ.get("SLIVIN_HARNESS_CONFIG")
    path = resolve_harness_path(override) if override else HARNESS_ROOT / "harness.local.toml"
    if not path.exists():
        return {}, None
    with path.open("rb") as handle:
        return tomllib.load(handle), path


def _resolve_tool_path(raw: str | Path, *, project_root: Path | None = None) -> Path:
    formatted = os.path.expandvars(str(raw)).format(
        home=str(Path.home()),
        harness_root=str(HARNESS_ROOT),
        project_root=str(project_root) if project_root else "",
    )
    if not any(sep in formatted for sep in ("/", "\\")) and not formatted.startswith("~"):
        found = shutil.which(formatted)
        if found:
            return Path(found).resolve()
        raise RuntimeError(
            f"Configured executable '{formatted}' was not found on PATH. "
            "Set an absolute path in harness.local.toml."
        )
    return resolve_runtime_path(formatted, project_root=project_root)


def resolve_codex_cmd(local_config: dict) -> Path:
    raw = os.environ.get("SLIVIN_CODEX_CMD") or local_config.get("codex", {}).get("command")
    if raw:
        return _resolve_tool_path(str(raw))
    for name in DEFAULT_CODEX_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    raise RuntimeError(
        "Codex CLI is not configured. Set [codex].command in harness.local.toml."
    )


def resolve_toolchain(
    local_config: dict,
    manifest: dict,
    *,
    project_name: str | None = None,
    project_root: Path | None = None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    sources: list[dict] = []
    if isinstance(local_config.get("toolchain"), dict):
        sources.append(local_config["toolchain"])
    if project_name:
        project_cfg = local_config.get("projects", {}).get(project_name, {})
        if isinstance(project_cfg, dict) and isinstance(project_cfg.get("toolchain"), dict):
            sources.append(project_cfg["toolchain"])
    if isinstance(manifest.get("toolchain"), dict):
        sources.append(manifest["toolchain"])
    for source in sources:
        merged.update({str(key): str(value) for key, value in source.items()})
    return {
        name: str(_resolve_tool_path(raw, project_root=project_root))
        for name, raw in merged.items()
    }


def validate_toolchain(toolchain: dict[str, str]) -> None:
    for name, raw in toolchain.items():
        path = Path(raw)
        if not path.exists():
            raise RuntimeError(f"Toolchain entry does not exist: {name}={path}")


def _run_git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def assert_clean_git(workspace: Path) -> None:
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if probe.returncode != 0:
        raise RuntimeError(f"Workspace is not a Git repository: {workspace}")
    status = _run_git(workspace, "status", "--porcelain=v1", "--untracked-files=all")
    if status.strip():
        raise RuntimeError("Workspace is not clean:\n" + status)


def capture_preflight(workspace: Path) -> dict:
    status = _run_git(workspace, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "head_sha": _run_git(workspace, "rev-parse", "HEAD").strip(),
        "working_tree_clean": not bool(status.strip()),
        "status_porcelain": status,
        "tracked_file_count": len(_run_git(workspace, "ls-files").splitlines()),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def collect_changed_paths(workspace: Path) -> list[str]:
    changed = set(
        line
        for line in _run_git(workspace, "diff", "--name-only", "HEAD", "--").splitlines()
        if line
    )
    changed.update(
        item
        for item in _run_git(
            workspace,
            "ls-files",
            "--others",
            "--exclude-standard",
        ).splitlines()
        if item
    )
    return sorted(path.replace("\\", "/") for path in changed)


def enforce_allowed_paths(changed_paths: list[str], allowed_paths: list[str]) -> None:
    if not allowed_paths:
        return
    normalized = [safe_repo_relative(item).rstrip("/") for item in allowed_paths]
    outside = [
        path
        for path in changed_paths
        if not any(path == allowed or path.startswith(allowed + "/") for allowed in normalized)
    ]
    if outside:
        raise RuntimeError(
            "Candidate changed paths outside owner-defined allowed_paths:\n  - "
            + "\n  - ".join(outside)
        )


def current_diff_text(workspace: Path, *, limit: int = 240_000) -> str:
    """Return the complete candidate diff, including new untracked files."""
    untracked = [
        item
        for item in _run_git(
            workspace, "ls-files", "--others", "--exclude-standard"
        ).splitlines()
        if item
    ]
    try:
        if untracked:
            _run_git(workspace, "add", "-N", "--", *untracked)
        text = _run_git(workspace, "diff", "--binary", "--full-index", "HEAD", "--")
    finally:
        if untracked:
            _run_git(workspace, "reset", "--", *untracked)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... DIFF TRUNCATED, total characters={len(text)} ...\n"


def expand_command(
    command: list[str],
    *,
    workspace: Path,
    toolchain: dict[str, str],
) -> list[str]:
    values = {
        "workspace": str(workspace.resolve()),
        "harness_root": str(HARNESS_ROOT),
        "python": toolchain.get("project_python", toolchain.get("python", sys.executable)),
        **toolchain,
    }
    expanded: list[str] = []
    for raw in command:
        try:
            expanded.append(raw.format(**values))
        except KeyError as exc:
            raise RuntimeError(f"Unknown command placeholder {exc} in {raw!r}") from exc
    return expanded


def candidate_content_fingerprint(workspace: Path) -> str:
    """Backward-compatible alias for the canonical candidate identity."""
    return build_candidate_identity(workspace).candidate_id


def _display_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    import shlex

    return shlex.join(command)


def prepare_self_verify_runner(
    *,
    workspace: Path,
    specs: list[dict],
    toolchain: dict[str, str],
) -> tuple[Path, Path, list[str]]:
    """Create a Harness-owned runner the Implementer can execute inside its sandbox."""
    runtime_dir = workspace / ".harness_tmp" / "agent_runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    script_path = runtime_dir / "self_verify.py"
    stamp_path = runtime_dir / "self_verify_stamp.json"
    stamp_path.unlink(missing_ok=True)
    checks = [
        {
            "name": spec["name"],
            "command": expand_command(spec["command"], workspace=workspace, toolchain=toolchain),
            "timeout_seconds": spec["timeout_seconds"],
        }
        for spec in specs
    ]
    template = '''from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__WORKSPACE__)
RUNTIME = Path(__RUNTIME__)
STAMP = Path(__STAMP__)
HARNESS_ROOT = Path(__HARNESS_ROOT__)
CHECKS = json.loads(__CHECKS__)
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

from slivin_harness.run_state import build_candidate_identity


STAMP.unlink(missing_ok=True)
all_passed = True
records = []
for index, check in enumerate(CHECKS, start=1):
    tmp = RUNTIME / f"check_{index:02d}"
    tmp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(tmp), "TMP": str(tmp), "TMPDIR": str(tmp),
        "XDG_CACHE_HOME": str(tmp / "cache"),
        "NPM_CONFIG_CACHE": str(tmp / "npm"),
        "SLIVIN_HARNESS_WORKSPACE": str(WORKSPACE),
    })
    print(f"[SELF-VERIFY {index}/{len(CHECKS)}] {check['name']}")
    try:
        result = subprocess.run(
            check["command"], cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=int(check["timeout_seconds"]), env=env
        )
        output = result.stdout or ""
        code = result.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        code = 124
    if output.strip():
        print(output.rstrip())
    passed = code == 0
    print("PASS" if passed else f"FAIL returncode={code}")
    records.append({"name": check["name"], "returncode": code})
    all_passed = all_passed and passed

if all_passed:
    candidate_id = build_candidate_identity(WORKSPACE).candidate_id
    STAMP.write_text(json.dumps({
        "passed": True,
        "candidate_id": candidate_id,
        "candidate_fingerprint": candidate_id,
        "checks": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SELF_VERIFY_PASS")
    raise SystemExit(0)
print("SELF_VERIFY_FAIL")
raise SystemExit(1)
'''
    script = (
        template.replace("__WORKSPACE__", repr(str(workspace.resolve())))
        .replace("__RUNTIME__", repr(str((runtime_dir / "self_verify").resolve())))
        .replace("__STAMP__", repr(str(stamp_path.resolve())))
        .replace("__HARNESS_ROOT__", repr(str(HARNESS_ROOT.resolve())))
        .replace("__CHECKS__", repr(json.dumps(checks, ensure_ascii=False)))
    )
    script_path.write_text(script, encoding="utf-8")
    return script_path, stamp_path, [sys.executable, str(script_path)]


def verify_self_verification_stamp(*, workspace: Path, stamp_path: Path) -> bool:
    if not stamp_path.is_file():
        return False
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        stamp.get("passed") is True
        and (stamp.get("candidate_id") or stamp.get("candidate_fingerprint"))
        == candidate_content_fingerprint(workspace)
    )


def build_dynamic_check_specs(
    paths: list[str],
    *,
    workspace: Path,
    toolchain: dict[str, str],
    base_specs: list[dict],
) -> tuple[list[dict], list[str]]:
    """Build Controller checks from test paths, never arbitrary agent commands."""
    specs: list[dict] = []
    notes: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        rel = safe_repo_relative(raw, field="additional_check_path")
        if rel in seen:
            continue
        seen.add(rel)
        path = workspace / rel
        if not path.is_file():
            notes.append(f"UNSUPPORTED_DYNAMIC_CHECK {rel}: file does not exist")
            continue
        if any(rel in " ".join(str(part).replace("\\", "/") for part in base.get("command", [])) for base in base_specs):
            notes.append(f"DYNAMIC_CHECK_ALREADY_COVERED {rel}")
            continue
        lower = rel.lower()
        test_like = (
            "/__tests__/" in "/" + lower
            or Path(lower).name.startswith("test_")
            or ".test." in lower
            or lower.endswith("_test.py")
        )
        if not test_like:
            notes.append(f"UNSUPPORTED_DYNAMIC_CHECK {rel}: path is not test-like")
            continue
        if lower.endswith((".js", ".cjs", ".mjs")) and toolchain.get("node") and toolchain.get("jest"):
            command = [toolchain["node"], toolchain["jest"]]
            config = workspace / "jest.config.cjs"
            if config.is_file():
                command += ["--config", str(config)]
            command += ["--runTestsByPath", str(path), "--runInBand", "--no-cache"]
            specs.append({
                "name": f"Discovered Jest: {rel}",
                "feedback": "repair",
                "command": command,
                "timeout_seconds": 180,
            })
            continue

        python_cmd = None
        for base in base_specs:
            cmd = list(base.get("command", []))
            if not cmd or "{python}" not in cmd:
                continue
            if "manage.py" in cmd and "test" in cmd:
                label = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel
                python_cmd = [
                    toolchain.get("project_python", toolchain.get("python", sys.executable)),
                    "manage.py", "test", label,
                ]
                break
            if "-m" in cmd and "pytest" in cmd:
                python_cmd = [
                    toolchain.get("project_python", toolchain.get("python", sys.executable)),
                    "-m", "pytest", rel, "-q",
                ]
                break
        if python_cmd:
            specs.append({
                "name": f"Discovered Python test: {rel}",
                "feedback": "repair",
                "command": python_cmd,
                "timeout_seconds": 300,
            })
        else:
            notes.append(f"UNSUPPORTED_DYNAMIC_CHECK {rel}: no trusted runner template/toolchain")
    return specs, notes


def run_check(
    spec: dict,
    *,
    workspace: Path,
    toolchain: dict[str, str],
    runtime_tmp: Path,
) -> CheckResult:
    command = expand_command(spec["command"], workspace=workspace, toolchain=toolchain)
    runtime_tmp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(runtime_tmp),
            "TMP": str(runtime_tmp),
            "TMPDIR": str(runtime_tmp),
            "XDG_CACHE_HOME": str(runtime_tmp / "cache"),
            "NPM_CONFIG_CACHE": str(runtime_tmp / "npm"),
            "SLIVIN_HARNESS_WORKSPACE": str(workspace.resolve()),
            "SLIVIN_HARNESS_ROOT": str(HARNESS_ROOT.resolve()),
        }
    )
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=spec["timeout_seconds"],
            env=env,
        )
        return CheckResult(
            name=spec["name"],
            command=command,
            returncode=result.returncode,
            output=result.stdout,
            duration_seconds=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return CheckResult(
            name=spec["name"],
            command=command,
            returncode=124,
            output=str(output),
            timed_out=True,
            duration_seconds=time.monotonic() - started,
        )


def run_checks(
    specs: list[dict],
    *,
    workspace: Path,
    toolchain: dict[str, str],
    runtime_root: Path,
    label: str,
) -> list[CheckResult]:
    print(f"=== {label} ===")
    results: list[CheckResult] = []
    for index, spec in enumerate(specs, start=1):
        result = run_check(
            spec,
            workspace=workspace,
            toolchain=toolchain,
            runtime_tmp=runtime_root / f"check_{index:02d}",
        )
        results.append(result)
        state = "PASS" if result.passed else "FAIL"
        print(f"[{index}/{len(specs)}] {spec['name']}: {state} ({result.duration_seconds:.2f}s)")
        if result.output.strip():
            print(result.output.rstrip())
    print()
    return results


def split_checks(checks: list[dict]) -> tuple[list[dict], list[dict]]:
    return (
        [spec for spec in checks if spec["feedback"] == "repair"],
        [spec for spec in checks if spec["feedback"] == "heldout"],
    )


def workflow_mode_for_manifest(manifest: dict) -> WorkflowMode:
    has_heldout = any(spec.get("feedback") == "heldout" for spec in manifest["checks"])
    if manifest.get("benchmark") or has_heldout:
        return WorkflowMode.HISTORICAL_BENCHMARK
    return WorkflowMode.PRODUCTION


def pipeline_profile_for_manifest(manifest: dict) -> PipelineProfile:
    # Phase 1 preserves manifest v2 compatibility. A later intake phase will
    # replace risk-driven routing with an explicit quality_mode contract.
    return (
        PipelineProfile.FAST
        if manifest.get("risk", "medium") == "low"
        else PipelineProfile.FULL
    )


def check_records(results: list[CheckResult]) -> list[dict]:
    return [
        {
            "name": item.name,
            "command": item.command,
            "returncode": item.returncode,
            "timed_out": item.timed_out,
            "duration_seconds": item.duration_seconds,
            "output": item.output,
        }
        for item in results
    ]


def checks_summary(results: list[CheckResult], *, output_limit: int = 8_000) -> str:
    blocks: list[str] = []
    for item in results:
        output = item.output
        if len(output) > output_limit:
            output = output[-output_limit:]
        blocks.append(
            f"{item.name}: {'PASS' if item.passed else 'FAIL'}\n"
            f"command={item.command!r}\n{output}"
        )
    return "\n\n".join(blocks)


def validate_plan_artifact(plan: dict, *, workspace: Path) -> None:
    allowed = {
        "protocol_version",
        "status",
        "summary",
        "observed_behavior",
        "expected_behavior",
        "root_cause",
        "change_plan",
        "preserve",
        "consumers_to_check",
        "risks",
        "test_plan",
        "documentation",
        "likely_paths",
        "unknowns",
    }
    ensure_exact_keys(plan, allowed=allowed, required=allowed, field="plan")
    if plan["protocol_version"] != PLANNER_PROTOCOL_VERSION:
        raise ArtifactContractError(
            code="PLANNER_VERSION",
            field="protocol_version",
            message="Planner protocol version mismatch",
            expected=PLANNER_PROTOCOL_VERSION,
            actual=plan["protocol_version"],
        )
    if plan["status"] not in set(enum_values(PlannerStatus)):
        raise ArtifactContractError(
            code="PLANNER_STATUS",
            field="status",
            message="Unknown Planner status",
            expected="/".join(enum_values(PlannerStatus)),
            actual=plan["status"],
        )
    for field in (
        "observed_behavior",
        "expected_behavior",
        "change_plan",
        "preserve",
        "consumers_to_check",
        "risks",
        "test_plan",
        "likely_paths",
        "unknowns",
    ):
        require_string_list(plan[field], field=field)
    require_type(plan["summary"], str, field="summary")
    root = plan["root_cause"]
    require_type(root, dict, field="root_cause")
    ensure_exact_keys(
        root,
        allowed={"claim", "evidence", "confidence"},
        required={"claim", "evidence", "confidence"},
        field="root_cause",
    )
    require_type(root["claim"], str, field="root_cause.claim")
    require_string_list(root["evidence"], field="root_cause.evidence")
    if root["confidence"] not in {"HIGH", "MEDIUM", "LOW"}:
        raise ArtifactContractError(
            code="PLANNER_CONFIDENCE",
            field="root_cause.confidence",
            message="Invalid root cause confidence",
            expected="HIGH/MEDIUM/LOW",
            actual=root["confidence"],
        )
    documentation = plan["documentation"]
    require_type(documentation, dict, field="documentation")
    ensure_exact_keys(
        documentation,
        allowed={"required", "paths", "reason"},
        required={"required", "paths", "reason"},
        field="documentation",
    )
    require_type(documentation["required"], bool, field="documentation.required")
    require_type(documentation["reason"], str, field="documentation.reason")
    require_string_list(documentation["paths"], field="documentation.paths")
    for index, raw in enumerate(plan["likely_paths"]):
        safe_repo_relative(raw, field=f"likely_paths[{index}]")
    for index, raw in enumerate(documentation["paths"]):
        safe_repo_relative(raw, field=f"documentation.paths[{index}]")

    if plan["status"] == PlannerStatus.READY.value:
        # READY means the remaining uncertainty is non-blocking. A Planner may
        # keep honest unknowns instead of pretending the repository is fully
        # understood. Blocking semantic/evidence gaps belong to
        # NEEDS_USER_DECISION or BLOCKED and require a concrete reason below.
        if not root["claim"].strip() or not root["evidence"]:
            raise ArtifactContractError(
                code="PLANNER_ROOT_CAUSE_MISSING",
                field="root_cause",
                message="READY requires a root cause with evidence",
                expected="Non-empty claim and evidence",
                actual=root,
            )
        if not plan["change_plan"] or not plan["test_plan"]:
            raise ArtifactContractError(
                code="PLANNER_PLAN_INCOMPLETE",
                field="change_plan/test_plan",
                message="READY requires change_plan and test_plan",
                expected="Non-empty arrays",
                actual={"change_plan": plan["change_plan"], "test_plan": plan["test_plan"]},
            )
    elif not plan["unknowns"]:
        raise ArtifactContractError(
            code="PLANNER_STOP_WITHOUT_REASON",
            field="unknowns",
            message="BLOCKED/NEEDS_USER_DECISION requires a concrete unresolved reason",
            expected="Non-empty unknowns",
            actual=[],
        )

    # Ensure paths actually resolve under repository, without requiring them to exist.
    root_resolved = workspace.resolve()
    for raw in plan["likely_paths"] + documentation["paths"]:
        candidate = (workspace / safe_repo_relative(raw)).resolve()
        if candidate != root_resolved and root_resolved not in candidate.parents:
            raise RuntimeError(f"Planner path escapes workspace: {raw}")


def validate_evaluation_artifact(evaluation: dict) -> None:
    allowed = {
        "protocol_version",
        "status",
        "summary",
        "task_satisfied",
        "changed_files_reviewed",
        "checks_assessment",
        "findings",
        "unverified",
        "replan_reason",
    }
    ensure_exact_keys(evaluation, allowed=allowed, required=allowed, field="evaluation")
    if evaluation["protocol_version"] != EVALUATOR_PROTOCOL_VERSION:
        raise ArtifactContractError(
            code="EVALUATOR_VERSION",
            field="protocol_version",
            message="Evaluator protocol version mismatch",
            expected=EVALUATOR_PROTOCOL_VERSION,
            actual=evaluation["protocol_version"],
        )
    status = evaluation["status"]
    if status not in set(enum_values(EvaluatorStatus)):
        raise ArtifactContractError(
            code="EVALUATOR_STATUS",
            field="status",
            message="Unknown Evaluator status",
            expected="/".join(enum_values(EvaluatorStatus)),
            actual=status,
        )
    require_type(evaluation["summary"], str, field="summary")
    require_type(evaluation["task_satisfied"], bool, field="task_satisfied")
    require_string_list(evaluation["changed_files_reviewed"], field="changed_files_reviewed")
    require_string_list(evaluation["checks_assessment"], field="checks_assessment")
    require_type(evaluation["findings"], list, field="findings")
    require_type(evaluation["unverified"], list, field="unverified")
    require_type(evaluation["replan_reason"], str, field="replan_reason")
    for index, finding in enumerate(evaluation["findings"]):
        require_type(finding, dict, field=f"findings[{index}]")
        ensure_exact_keys(
            finding,
            allowed={"severity", "title", "evidence", "required_action"},
            required={"severity", "title", "evidence", "required_action"},
            field=f"findings[{index}]",
        )
        if finding["severity"] not in {"HIGH", "MEDIUM", "LOW"}:
            raise ArtifactContractError(
                code="EVALUATOR_SEVERITY",
                field=f"findings[{index}].severity",
                message="Invalid finding severity",
                expected="HIGH/MEDIUM/LOW",
                actual=finding["severity"],
            )
        require_type(finding["title"], str, field=f"findings[{index}].title")
        require_string_list(finding["evidence"], field=f"findings[{index}].evidence")
        require_type(finding["required_action"], str, field=f"findings[{index}].required_action")
    for index, item in enumerate(evaluation["unverified"]):
        require_type(item, dict, field=f"unverified[{index}]")
        ensure_exact_keys(
            item,
            allowed={"claim", "reason", "required_evidence"},
            required={"claim", "reason", "required_evidence"},
            field=f"unverified[{index}]",
        )
        for key in ("claim", "reason", "required_evidence"):
            require_type(item[key], str, field=f"unverified[{index}].{key}")

    if status == EvaluatorStatus.PASS.value:
        if not evaluation["task_satisfied"] or evaluation["findings"] or evaluation["unverified"] or evaluation["replan_reason"]:
            raise RuntimeError(
                "Evaluator PASS is invalid: task_satisfied must be true and findings, "
                "unverified and replan_reason must be empty"
            )
    elif status == EvaluatorStatus.FINDINGS.value and not evaluation["findings"]:
        raise RuntimeError("Evaluator FINDINGS requires at least one finding")
    elif status == EvaluatorStatus.REPLAN_REQUIRED.value and not evaluation["replan_reason"].strip():
        raise RuntimeError("Evaluator REPLAN_REQUIRED requires replan_reason")
    elif status in {EvaluatorStatus.BLOCKED.value, EvaluatorStatus.NEEDS_USER_DECISION.value} and not (
        evaluation["unverified"] or evaluation["replan_reason"].strip()
    ):
        raise RuntimeError(f"Evaluator {status} requires an explicit reason")


def build_implementation_prompt(
    task_prompt: str,
    plan: dict | None,
    *,
    implementation_contract: dict,
    self_verify_command: list[str],
    toolchain: dict[str, str],
    allowed_paths: list[str],
) -> str:
    compact_plan = compact_plan_context(plan)
    plan_block = (
        "Для risk=low отдельный Planner не запускался. Самостоятельно исследуй задачу."
        if compact_plan is None
        else (
            f"PLAN_FINGERPRINT: {plan_fingerprint(plan)}\n"
            "--- BEGIN COMPACT PLAN CONTEXT ---\n"
            + json.dumps(compact_plan, ensure_ascii=False, indent=2)
            + "\n--- END COMPACT PLAN CONTEXT ---"
        )
    )
    boundary = (
        "Owner-defined hard path boundary:\n" + json.dumps(allowed_paths, ensure_ascii=False)
        if allowed_paths
        else "Owner did not set a hard path boundary. Planner likely_paths are hints only."
    )
    return f"""
Исходная задача:
--- BEGIN TASK ---
{task_prompt}
--- END TASK ---

{plan_block}

--- BEGIN IMPLEMENTATION CONTRACT ---
{json.dumps(implementation_contract, ensure_ascii=False, indent=2)}
--- END IMPLEMENTATION CONTRACT ---

{boundary}

TRUSTED_TOOLCHAIN:
{json.dumps(toolchain, ensure_ascii=False, indent=2)}

SELF_VERIFY_COMMAND:
{_display_command(self_verify_command)}

Сначала реализуй целостный fix. Затем обязательно запусти SELF_VERIFY_COMMAND и сам
исправляй найденные ошибки, пока он не выдаст SELF_VERIFY_PASS. После этого проверь каждый
Implementation Contract item и только затем верни structured Implementation Report.

Если по ходу исследования найдены дополнительные существующие/новые test files для
materially affected consumers, добавь их repo-relative paths в additional_check_paths.
Controller сам построит безопасную команду из trusted toolchain; произвольные команды
туда передавать не нужно.
""".strip()


def _repair_contract_block(
    *, implementation_contract: dict, self_verify_command: list[str]
) -> str:
    return f"""
Implementation Contract остаётся обязательным после repair:
{json.dumps(implementation_contract, ensure_ascii=False, indent=2)}

Перед COMPLETE снова запусти актуальный SELF_VERIFY_COMMAND:
{_display_command(self_verify_command)}

После изменений заново дай evidence по КАЖДОМУ contract item в structured report.
""".strip()


def build_implementation_continuation_prompt(
    *, implementation_contract: dict, self_verify_command: list[str]
) -> str:
    return f"""
Предыдущий Implementer turn был прерван Harness timeout. Уже внесённые изменения в workspace
сохранены, thread и исходный task остаются теми же. НЕ начинай исследование заново и не
откатывай подтверждённую работу.

1. Сначала посмотри текущий `git diff`/status и продолжи только незавершённые пункты.
2. Особое внимание удели ещё не доказанным risks/consumers из Implementation Contract.
3. Запусти актуальный SELF_VERIFY_COMMAND.
4. Верни COMPLETE только после evidence по каждому contract item; иначе BLOCKED с реальной причиной.

Implementation Contract:
{json.dumps(implementation_contract, ensure_ascii=False, indent=2)}

SELF_VERIFY_COMMAND:
{_display_command(self_verify_command)}
""".strip()


def build_check_repair_prompt(
    results: list[CheckResult],
    *,
    implementation_contract: dict,
    self_verify_command: list[str],
) -> str:
    failed = [item for item in results if not item.passed]
    return f"""
Deterministic checks нашли ошибки. Task ещё не принят.

--- BEGIN FAILED CHECKS ---
{checks_summary(failed)}
--- END FAILED CHECKS ---

Проверь причину по коду. Исправь только подтверждённую in-scope проблему, не ослабляй
checks. {_repair_contract_block(implementation_contract=implementation_contract, self_verify_command=self_verify_command)}
""".strip()


def build_evaluator_repair_prompt(
    evaluation: dict,
    *,
    implementation_contract: dict,
    self_verify_command: list[str],
) -> str:
    return f"""
Независимый Evaluator нашёл проблемы в текущем candidate. Task ещё не принят.

--- BEGIN EVALUATOR VERDICT ---
{json.dumps(evaluation, ensure_ascii=False, indent=2)}
--- END EVALUATOR VERDICT ---

Самостоятельно проверь достижимость findings и исправь подтверждённые причины.
Не подгоняй код под формулировку отчёта и не ослабляй tests.
{_repair_contract_block(implementation_contract=implementation_contract, self_verify_command=self_verify_command)}
""".strip()


def _stable_sha256(value: object) -> str:
    return stable_fingerprint(value, length=64)


def verify_oracle_calibration_certificate(
    specs: list[dict],
    *,
    certificate_path: Path,
    recorder: RunRecorder,
) -> None:
    if not certificate_path.is_file():
        raise RuntimeError(f"Calibration certificate does not exist: {certificate_path}")
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    schema_version = certificate.get("schema_version")
    if schema_version not in {1, 2}:
        raise RuntimeError("Unsupported calibration certificate schema")
    entries = {item["name"]: item for item in certificate.get("heldout_checks", [])}
    if set(entries) != {spec["name"] for spec in specs}:
        raise RuntimeError("Calibration certificate does not match held-out check set")
    verified: list[dict] = []
    for spec in specs:
        entry = entries[spec["name"]]
        actual_spec = _stable_sha256(spec)
        if actual_spec != entry.get("spec_sha256"):
            raise RuntimeError(f"Held-out definition changed: {spec['name']}")
        files: list[dict] = []
        for file_entry in entry.get("files", []):
            path = resolve_harness_path(file_entry["path"])
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if actual != file_entry.get("sha256"):
                raise RuntimeError(f"Calibrated held-out file changed: {file_entry['path']}")
            files.append({"path": file_entry["path"], "sha256": actual})
        if schema_version == 1:
            if entry.get("broken_result") != "FAIL" or entry.get("good_result") != "PASS":
                raise RuntimeError("Certificate must attest broken=FAIL and good=PASS")
            calibration_cases = [
                {"name": "legacy-broken", "role": "broken_baseline", "expected_result": "FAIL"},
                {"name": "legacy-good", "role": "positive_reference", "expected_result": "PASS"},
            ]
        else:
            calibration_cases = entry.get("calibration_cases", [])
            if not isinstance(calibration_cases, list) or len(calibration_cases) < 2:
                raise RuntimeError("Semantic calibration certificate needs multiple calibration_cases")
            seen_names: set[str] = set()
            observed_results: set[str] = set()
            roles: dict[str, int] = {}
            for case in calibration_cases:
                if not isinstance(case, dict):
                    raise RuntimeError("Invalid calibration case")
                name = str(case.get("name") or "").strip()
                role = str(case.get("role") or "").strip()
                result = str(case.get("expected_result") or "").strip()
                fingerprint = str(case.get("fixture_fingerprint") or "").strip()
                if not name or name in seen_names:
                    raise RuntimeError("Calibration case names must be unique and non-empty")
                if result not in {"PASS", "FAIL"}:
                    raise RuntimeError("Calibration case expected_result must be PASS or FAIL")
                if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                    raise RuntimeError("Calibration case fixture_fingerprint must be sha256")
                seen_names.add(name)
                observed_results.add(result)
                roles[role] = roles.get(role, 0) + 1
            if observed_results != {"PASS", "FAIL"}:
                raise RuntimeError("Semantic calibration must contain both PASS and FAIL cases")
            if roles.get("broken_baseline", 0) < 1 or roles.get("positive_reference", 0) < 1:
                raise RuntimeError("Semantic calibration needs broken_baseline and positive_reference cases")
        verified.append({
            "name": spec["name"],
            "spec_sha256": actual_spec,
            "files": files,
            "calibration_cases": calibration_cases,
        })
    recorder.write_json(
        "oracle_calibration_certificate_verified.json",
        {"certificate": str(certificate_path), "checks": verified},
    )
    print("ORACLE_CALIBRATION_CERTIFICATE_PASS")


def run_benchmark_baseline_gate(
    specs: list[dict],
    *,
    workspace: Path,
    toolchain: dict[str, str],
    runtime_root: Path,
    failure_marker: str,
) -> dict:
    if not specs:
        raise RuntimeError("Baseline gate requires at least one held-out check")
    results = run_checks(
        specs,
        workspace=workspace,
        toolchain=toolchain,
        runtime_root=runtime_root,
        label="BENCHMARK BASELINE GATE",
    )
    if any(item.timed_out for item in results):
        raise RuntimeError("Historical baseline held-out timed out; benchmark setup is invalid")
    if any(item.passed for item in results):
        print("BENCHMARK_BASELINE_NOT_CONFIRMED_BROKEN")
        raise RuntimeError("Historical baseline unexpectedly passes the held-out grader")
    if any(failure_marker not in item.output for item in results):
        raise RuntimeError(
            "Historical baseline held-out failed before the calibrated oracle result was "
            "observed; treat this as infrastructure/setup failure, not proof of a broken baseline"
        )

    confirmed = True
    evidence = {
        "baseline_status": "CONFIRMED_BROKEN" if confirmed else "NOT_CONFIRMED_BROKEN",
        "authority": "CONTROLLER_HELDOUT",
        "failure_details_exposed_to_planner": False,
        "records": check_records(results),
    }
    print("BENCHMARK_BASELINE_CONFIRMED_BROKEN")
    return evidence



def require_candidate_change_for_confirmed_benchmark(
    benchmark_evidence: dict | None,
    changed_paths: list[str],
) -> None:
    if (
        benchmark_evidence
        and benchmark_evidence.get("baseline_status") == "CONFIRMED_BROKEN"
        and not changed_paths
    ):
        raise RuntimeError(
            "Confirmed-broken benchmark produced no candidate changes after IMPLEMENT. "
            "Stop before checks/evaluation; inspect Implementer output and workspace-write "
            "sandbox diagnostics."
        )


def planner_benchmark_context(evidence: dict | None) -> str:
    if not evidence:
        return ""
    return (
        "Controller-owned benchmark fact: current baseline was independently "
        f"classified as {evidence.get('baseline_status')}. Hidden assertion text is not exposed."
    )


def collect_repo_context(workspace: Path) -> dict:
    agents = []
    for path in (workspace / "AGENTS.md",):
        if path.is_file():
            agents.append(
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    skills = []
    skills_root = workspace / ".agents" / "skills"
    if skills_root.is_dir():
        for path in sorted(skills_root.glob("*/SKILL.md")):
            skills.append(
                {
                    "name": path.parent.name,
                    "path": path.relative_to(workspace).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    return {"agents": agents, "skills": skills}


def make_heartbeat(label: str):
    def heartbeat(state: dict) -> None:
        print(
            f"[{label}] working... elapsed={state['turn_elapsed_seconds']:.0f}s "
            f"app-server={'alive' if state['alive'] else 'dead'} "
            f"last-event={state['last_activity_seconds']:.0f}s"
        )
    return heartbeat


def run_agent_turn(
    codex: CodexAppServer,
    *,
    thread_id: str,
    prompt: str,
    timeout: int,
    label: str,
    output_schema: dict | None = None,
) -> str:
    print(f"=== {label} ===")
    return codex.run_turn(
        thread_id=thread_id,
        prompt=prompt,
        timeout=timeout,
        on_delta=lambda delta: print(delta, end="", flush=True),
        on_message_end=lambda: print(),
        on_heartbeat=make_heartbeat(label),
        output_schema=output_schema,
    )


def run_implementer_report(
    codex: CodexAppServer,
    *,
    thread_id: str,
    prompt: str,
    timeout: int,
    label: str,
    implementation_contract: dict,
    self_verify_command: list[str],
    workspace: Path,
    stamp_path: Path,
    plan: dict | None,
) -> dict:
    current_prompt = prompt
    current_label = label
    timeout_continuations = 0
    while True:
        try:
            raw = run_agent_turn(
                codex,
                thread_id=thread_id,
                prompt=current_prompt,
                timeout=timeout if timeout_continuations == 0 else min(timeout, 300),
                label=current_label,
                output_schema=IMPLEMENTER_REPORT_SCHEMA,
            )
            break
        except TurnTimeoutError:
            if timeout_continuations >= 1:
                raise
            timeout_continuations += 1
            print(
                "IMPLEMENTER_TIMEOUT_CONTINUE: preserving current workspace and continuing "
                "the same Implementer thread"
            )
            current_prompt = build_implementation_continuation_prompt(
                implementation_contract=implementation_contract,
                self_verify_command=self_verify_command,
            )
            current_label = f"{label} CONTINUE"
    report = parse_implementation_report(raw)
    changed_paths = collect_changed_paths(workspace)
    documentation_paths = []
    if plan and plan["documentation"]["required"]:
        documentation_paths = list(plan["documentation"]["paths"])
    validate_implementation_report(
        report,
        contract=implementation_contract,
        changed_paths=changed_paths,
        self_verification_ok=verify_self_verification_stamp(
            workspace=workspace, stamp_path=stamp_path
        ),
        documentation_paths=documentation_paths,
    )
    print("=== IMPLEMENTATION REPORT ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def merge_dynamic_specs(existing: list[dict], discovered: list[dict]) -> list[dict]:
    result = list(existing)
    keys = {json.dumps(item["command"], ensure_ascii=False) for item in result}
    for spec in discovered:
        key = json.dumps(spec["command"], ensure_ascii=False)
        if key not in keys:
            keys.add(key)
            result.append(spec)
    return result


def _thread_recorder(recorder: RunRecorder, role: str):
    def callback(thread: dict) -> None:
        recorder.write_json(f"thread_{role}_metadata.json", thread)
    return callback


def package_candidate(
    *,
    session: WorkspaceSession,
    recorder: RunRecorder,
) -> tuple[bytes, dict[str, str]]:
    """Build the accepted patch without mutating the source checkout."""
    patch = build_candidate_patch(session)
    patch_path = recorder.write_bytes("candidate.patch", patch)
    metadata = {
        "path": str(patch_path),
        "sha256": hashlib.sha256(patch).hexdigest(),
        "result_mode": session.result_mode,
    }
    print("RESULT_PATCH:", patch_path)
    return patch, metadata


def deliver_candidate(
    *,
    session: WorkspaceSession,
    patch: bytes,
) -> dict[str, str]:
    """Deliver a packaged candidate using the owner-selected result mode."""
    apply_candidate_to_source(session, patch=patch)
    if session.result_mode == "keep_worktree":
        print("RESULT_WORKTREE_RETAINED:", session.workspace)
        destination = str(session.workspace)
    else:
        destination = str(session.source_repo or "")
    return {
        "status": StageResultCode.RESULT_DELIVERY_PASS.value,
        "result_mode": session.result_mode,
        "destination": destination,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a task through Slivin Harness")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    started = time.monotonic()
    session: WorkspaceSession | None = None
    recorder: RunRecorder | None = None
    run_state: RunState | None = None
    try:
        manifest_path = args.manifest.expanduser().resolve()
        manifest = load_manifest(manifest_path)
        if args.validate_only:
            print(
                f"MANIFEST_VALID version={MANIFEST_VERSION} task={manifest['task_id']} "
                f"risk={manifest.get('risk', 'medium')}"
            )
            return 0

        workflow_mode = workflow_mode_for_manifest(manifest)
        pipeline_profile = pipeline_profile_for_manifest(manifest)
        recorder = RunRecorder(manifest["task_id"])
        recorder.write_json("manifest_snapshot.json", manifest)
        recorder.write_json(
            "workflow_snapshot.json",
            workflow_snapshot(harness_version=__version__),
        )
        run_state = RunState.create(
            path=recorder.root / "run_state.json",
            task_id=manifest["task_id"],
            harness_version=__version__,
            workflow_version=WORKFLOW_VERSION,
            mode=workflow_mode,
            pipeline_profile=pipeline_profile,
        )
        run_state.begin_stage(StageId.INTAKE_PREFLIGHT)

        local_config, local_config_path = load_local_config()
        session = prepare_workspace_session(
            manifest=manifest,
            local_config=local_config,
            harness_root=HARNESS_ROOT,
            task_id=manifest["task_id"],
        )
        workspace = session.workspace
        if (
            workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK
            and session.result_mode != "keep_worktree"
        ):
            raise RuntimeError(
                "Historical benchmark requires result_mode=keep_worktree"
            )
        if not workspace.is_dir():
            raise RuntimeError(f"Workspace does not exist: {workspace}")
        if manifest.get("require_clean_git", True):
            assert_clean_git(workspace)

        preflight = capture_preflight(workspace)
        recorder.write_json("preflight.json", preflight)
        repo_context = collect_repo_context(workspace)
        recorder.write_json("repo_context.json", repo_context)

        project_root = session.source_repo or workspace
        toolchain = resolve_toolchain(
            local_config,
            manifest,
            project_name=session.project_name,
            project_root=project_root,
        )
        validate_toolchain(toolchain)
        run_state.bump_revision(
            RevisionKind.RUNTIME_ENVIRONMENT,
            artifact="preflight.json",
        )
        repair_specs, heldout_specs = split_checks(manifest["checks"])
        risk = manifest.get("risk", "medium")
        max_fix = manifest.get("max_fix_cycles", 2)
        max_replan = manifest.get("max_replan_cycles", 1)
        timeout = manifest.get("turn_timeout_seconds", 900)
        allowed_paths = list(manifest.get("allowed_paths", []))

        def observe_candidate(reason_code: str):
            expected_head = session.base_sha or preflight["head_sha"]
            identity = build_candidate_identity(
                workspace,
                baseline_sha=expected_head,
            )
            if identity.workspace_head != expected_head:
                raise RuntimeError(
                    "Workspace HEAD changed during the task; Git history is Controller-owned"
                )
            run_state.observe_candidate(identity, reason_code=reason_code)
            recorder.write_json("candidate_identity_current.json", identity.to_dict())
            return identity

        print("TASK_STARTED:", datetime.now().astimezone().isoformat())
        print("HARNESS_VERSION:", __version__)
        print("TASK:", manifest["task_id"])
        print("RISK:", risk)
        print("WORKFLOW_MODE:", workflow_mode.value)
        print("PIPELINE_PROFILE:", pipeline_profile.value)
        print("WORKSPACE:", workspace)
        print("RESULT_MODE:", session.result_mode)
        print("RUN_DIR:", recorder.root)
        print("LOCAL_CONFIG:", local_config_path or "(none)")
        print(
            "PIPELINE:",
            "0 PREFLIGHT → 1 PLANNER[SKIP] → 2 CONTRACT → 3 IMPLEMENT → "
            "4 CHECKS → 5 RUNTIME[SKIP] → 6 EVALUATOR[SKIP] → 7 FINAL"
            if pipeline_profile == PipelineProfile.FAST
            else "0 PREFLIGHT → 1 PLANNER → 2 CONTRACT → 3 IMPLEMENT → "
            "4 CHECKS → 5 RUNTIME[SKIP] → 6 EVALUATOR → 7 FINAL",
        )

        benchmark_evidence = None
        benchmark = manifest.get("benchmark", {})
        if benchmark.get("calibration_certificate"):
            verify_oracle_calibration_certificate(
                heldout_specs,
                certificate_path=resolve_harness_path(benchmark["calibration_certificate"]),
                recorder=recorder,
            )
        if benchmark.get("confirm_current_baseline_broken"):
            benchmark_evidence = run_benchmark_baseline_gate(
                heldout_specs,
                workspace=workspace,
                toolchain=toolchain,
                runtime_root=recorder.root / "benchmark_baseline_gate_tmp",
                failure_marker=str(benchmark["baseline_failure_marker"]),
            )
            recorder.write_json("benchmark_baseline_gate.json", benchmark_evidence)

        run_state.set_baseline(
            source_head=session.source_head,
            workspace_head=preflight["head_sha"],
            source_repo=str(session.source_repo) if session.source_repo else None,
            workspace=str(workspace),
        )
        run_state.pass_stage(
            StageId.INTAKE_PREFLIGHT,
            StageResultCode.PREFLIGHT_READY,
            artifacts=(
                "manifest_snapshot.json",
                "workflow_snapshot.json",
                "preflight.json",
                "repo_context.json",
            ),
        )

        runtime_tmp = workspace / ".harness_tmp" / "agent_runtime"
        codex_cmd = resolve_codex_cmd(local_config)
        with CodexAppServer(
            codex_cmd,
            client_version=__version__,
            runtime_tmp=runtime_tmp,
        ) as codex:
            plan: dict | None = None
            run_state.begin_stage(StageId.PLANNER)
            if pipeline_profile == PipelineProfile.FULL:
                print("=== PLAN ===")
                plan = run_planner(
                    codex,
                    workspace=workspace,
                    task_prompt=manifest["prompt"],
                    preflight=preflight,
                    replan_context=planner_benchmark_context(benchmark_evidence),
                    on_heartbeat=make_heartbeat("PLAN"),
                    on_thread_started=_thread_recorder(recorder, "planner_1"),
                    timeout=timeout,
                )
                validate_plan_artifact(plan, workspace=workspace)
                recorder.write_json("plan_01.json", plan)
                run_state.bump_revision(RevisionKind.PLAN, artifact="plan_01.json")
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                if plan["status"] != PlannerStatus.READY.value:
                    if plan["status"] == PlannerStatus.BLOCKED.value:
                        run_state.route_stage(
                            StageId.PLANNER,
                            outcome=WorkflowOutcome.BLOCKED,
                            result_code=StageResultCode.BLOCKED,
                            reason_code="PLANNER_BLOCKED",
                            artifacts=("plan_01.json",),
                        )
                    else:
                        run_state.route_stage(
                            StageId.PLANNER,
                            outcome=WorkflowOutcome.NEEDS_USER_DECISION,
                            result_code=StageResultCode.NEEDS_USER_DECISION,
                            reason_code="PLANNER_NEEDS_USER_DECISION",
                            artifacts=("plan_01.json",),
                        )
                    print("HARNESS_TASK_STOPPED:", plan["status"])
                    return 2
                run_state.pass_stage(
                    StageId.PLANNER,
                    StageResultCode.PLANNER_READY,
                    artifacts=("plan_01.json",),
                )
            else:
                run_state.skip_stage(
                    StageId.PLANNER,
                    StageResultCode.PLANNER_SKIPPED_FAST,
                    reason_code="FAST_PROFILE_COMPATIBILITY",
                )

            run_state.begin_stage(StageId.IMPLEMENTATION_CONTRACT)
            implementation_contract = build_implementation_contract(
                plan, task_prompt=manifest["prompt"]
            )
            recorder.write_json("implementation_contract_01.json", implementation_contract)
            run_state.bump_revision(
                RevisionKind.IMPLEMENTATION_CONTRACT,
                artifact="implementation_contract_01.json",
            )
            run_state.pass_stage(
                StageId.IMPLEMENTATION_CONTRACT,
                StageResultCode.IMPLEMENTATION_CONTRACT_READY,
                artifacts=("implementation_contract_01.json",),
            )
            print("=== IMPLEMENTATION CONTRACT ===")
            print(json.dumps(implementation_contract, ensure_ascii=False, indent=2))

            dynamic_specs: list[dict] = []
            dynamic_notes: list[str] = []
            implementation_report_index = 0

            def active_repair_specs() -> list[dict]:
                return list(repair_specs) + list(dynamic_specs)

            def register_report_checks(report: dict) -> None:
                nonlocal dynamic_specs
                discovered, notes = build_dynamic_check_specs(
                    list(report["additional_check_paths"]),
                    workspace=workspace,
                    toolchain=toolchain,
                    base_specs=repair_specs,
                )
                before = len(dynamic_specs)
                dynamic_specs = merge_dynamic_specs(dynamic_specs, discovered)
                for note in notes:
                    if note not in dynamic_notes:
                        dynamic_notes.append(note)
                        print(note)
                if len(dynamic_specs) > before:
                    print(
                        "DYNAMIC_CHECKS_ADDED:",
                        ", ".join(spec["name"] for spec in dynamic_specs[before:]),
                    )

            run_state.begin_stage(StageId.IMPLEMENTER)
            implementer_thread = codex.start_thread(
                cwd=workspace,
                sandbox="workspace-write",
                developer_instructions=IMPLEMENTER_INSTRUCTIONS,
                on_started=_thread_recorder(recorder, "implementer"),
            )
            _, stamp_path, self_verify_command = prepare_self_verify_runner(
                workspace=workspace,
                specs=active_repair_specs(),
                toolchain=toolchain,
            )
            implementation_report_index += 1
            report = run_implementer_report(
                codex,
                thread_id=implementer_thread,
                prompt=build_implementation_prompt(
                    manifest["prompt"],
                    plan,
                    implementation_contract=implementation_contract,
                    self_verify_command=self_verify_command,
                    toolchain=toolchain,
                    allowed_paths=allowed_paths,
                ),
                timeout=timeout,
                label="IMPLEMENT",
                implementation_contract=implementation_contract,
                self_verify_command=self_verify_command,
                workspace=workspace,
                stamp_path=stamp_path,
                plan=plan,
            )
            recorder.write_json(
                f"implementation_report_{implementation_report_index:02d}.json", report
            )
            candidate_identity = observe_candidate("IMPLEMENTER_REPORT")
            if report["status"] != ImplementerStatus.COMPLETE.value:
                run_state.route_stage(
                    StageId.IMPLEMENTER,
                    outcome=WorkflowOutcome.BLOCKED,
                    result_code=StageResultCode.BLOCKED,
                    reason_code="IMPLEMENTER_BLOCKED",
                    artifacts=(
                        f"implementation_report_{implementation_report_index:02d}.json",
                        "candidate_identity_current.json",
                    ),
                )
                print("HARNESS_TASK_STOPPED: IMPLEMENTER_BLOCKED")
                return 2
            run_state.pass_stage(
                StageId.IMPLEMENTER,
                StageResultCode.IMPLEMENTATION_COMPLETE,
                artifacts=(
                    f"implementation_report_{implementation_report_index:02d}.json",
                    "candidate_identity_current.json",
                ),
            )
            register_report_checks(report)

            initial_changed_paths = collect_changed_paths(workspace)
            require_candidate_change_for_confirmed_benchmark(
                benchmark_evidence,
                initial_changed_paths,
            )

            fix_cycles = 0
            replan_cycles = 0
            evaluation_index = 0
            check_index = 0
            first_evaluation_pass: bool | None = None
            while True:
                changed_paths = collect_changed_paths(workspace)
                enforce_allowed_paths(changed_paths, allowed_paths)
                current_specs = active_repair_specs()
                check_index += 1
                run_state.begin_stage(StageId.DETERMINISTIC_CHECKS)
                checks_candidate_before = observe_candidate("CONTROLLER_CHECKS_BEFORE")
                repair_results = run_checks(
                    current_specs,
                    workspace=workspace,
                    toolchain=toolchain,
                    runtime_root=recorder.root / f"checks_{check_index:02d}",
                    label=f"CHECKS #{check_index}",
                )
                checks_artifact = f"checks_{check_index:02d}.json"
                recorder.write_json(checks_artifact, check_records(repair_results))
                checks_candidate_after = observe_candidate("CONTROLLER_CHECKS_AFTER")
                if checks_candidate_after.candidate_id != checks_candidate_before.candidate_id:
                    run_state.route_stage(
                        StageId.DETERMINISTIC_CHECKS,
                        outcome=WorkflowOutcome.INVALID,
                        result_code=StageResultCode.INVALID,
                        reason_code="CHECK_MUTATED_CANDIDATE",
                        artifacts=(checks_artifact, "candidate_identity_current.json"),
                    )
                    raise RuntimeError("Controller checks changed the candidate")

                if any(not result.passed for result in repair_results):
                    run_state.route_stage(
                        StageId.DETERMINISTIC_CHECKS,
                        outcome=WorkflowOutcome.REPAIR,
                        result_code=StageResultCode.CHECK_REPAIR_REQUIRED,
                        reason_code="DETERMINISTIC_CHECK_FAILED",
                        artifacts=(checks_artifact, "candidate_identity_current.json"),
                    )
                    if fix_cycles >= max_fix:
                        raise RuntimeError("Deterministic checks still fail after max_fix_cycles")
                    fix_cycles += 1
                    _, stamp_path, self_verify_command = prepare_self_verify_runner(
                        workspace=workspace,
                        specs=current_specs,
                        toolchain=toolchain,
                    )
                    implementation_report_index += 1
                    run_state.begin_stage(StageId.IMPLEMENTER)
                    report = run_implementer_report(
                        codex,
                        thread_id=implementer_thread,
                        prompt=build_check_repair_prompt(
                            repair_results,
                            implementation_contract=implementation_contract,
                            self_verify_command=self_verify_command,
                        ),
                        timeout=timeout,
                        label=f"REPAIR CHECKS #{fix_cycles}",
                        implementation_contract=implementation_contract,
                        self_verify_command=self_verify_command,
                        workspace=workspace,
                        stamp_path=stamp_path,
                        plan=plan,
                    )
                    report_artifact = f"implementation_report_{implementation_report_index:02d}.json"
                    recorder.write_json(report_artifact, report)
                    observe_candidate("IMPLEMENTER_REPAIR_CHECKS")
                    if report["status"] != ImplementerStatus.COMPLETE.value:
                        run_state.route_stage(
                            StageId.IMPLEMENTER,
                            outcome=WorkflowOutcome.BLOCKED,
                            result_code=StageResultCode.BLOCKED,
                            reason_code="IMPLEMENTER_BLOCKED",
                            artifacts=(report_artifact, "candidate_identity_current.json"),
                        )
                        print("HARNESS_TASK_STOPPED: IMPLEMENTER_BLOCKED")
                        return 2
                    run_state.pass_stage(
                        StageId.IMPLEMENTER,
                        StageResultCode.IMPLEMENTATION_COMPLETE,
                        artifacts=(report_artifact, "candidate_identity_current.json"),
                    )
                    register_report_checks(report)
                    continue

                run_state.pass_stage(
                    StageId.DETERMINISTIC_CHECKS,
                    StageResultCode.DETERMINISTIC_VERIFICATION_PASS,
                    artifacts=(checks_artifact, "candidate_identity_current.json"),
                )
                run_state.begin_stage(StageId.RUNTIME_VERIFICATION)
                run_state.skip_stage(
                    StageId.RUNTIME_VERIFICATION,
                    StageResultCode.RUNTIME_VERIFICATION_SKIPPED,
                    reason_code="RUNTIME_LAYER_NOT_IMPLEMENTED_PHASE1",
                )

                if pipeline_profile == PipelineProfile.FAST:
                    run_state.begin_stage(StageId.EVALUATOR)
                    run_state.skip_stage(
                        StageId.EVALUATOR,
                        StageResultCode.EVALUATION_SKIPPED_FAST,
                        reason_code="FAST_PROFILE_COMPATIBILITY",
                    )
                    break

                evaluation_index += 1
                evaluator_check_summary = checks_summary(repair_results)
                if dynamic_notes:
                    evaluator_check_summary += "\n\nDynamic check notes:\n" + "\n".join(dynamic_notes)
                run_state.begin_stage(StageId.EVALUATOR)
                evaluation_candidate_before = observe_candidate("EVALUATOR_BEFORE")
                evaluation = run_evaluator(
                    codex,
                    workspace=workspace,
                    task_prompt=manifest["prompt"],
                    preflight=preflight,
                    changed_paths=changed_paths,
                    diff_text=current_diff_text(workspace),
                    checks_summary=evaluator_check_summary,
                    on_heartbeat=make_heartbeat(f"EVALUATE #{evaluation_index}"),
                    on_thread_started=_thread_recorder(recorder, f"evaluator_{evaluation_index}"),
                    timeout=timeout,
                )
                validate_evaluation_artifact(evaluation)
                evaluation_artifact = f"evaluation_{evaluation_index:02d}.json"
                recorder.write_json(evaluation_artifact, evaluation)
                evaluation_candidate_after = observe_candidate("EVALUATOR_AFTER")
                if evaluation_candidate_after.candidate_id != evaluation_candidate_before.candidate_id:
                    run_state.route_stage(
                        StageId.EVALUATOR,
                        outcome=WorkflowOutcome.INVALID,
                        result_code=StageResultCode.INVALID,
                        reason_code="EVALUATOR_MUTATED_CANDIDATE",
                        artifacts=(evaluation_artifact, "candidate_identity_current.json"),
                    )
                    raise RuntimeError("Evaluator changed the candidate")
                print("=== EVALUATION ===")
                print(json.dumps(evaluation, ensure_ascii=False, indent=2))
                if first_evaluation_pass is None:
                    first_evaluation_pass = evaluation["status"] == EvaluatorStatus.PASS.value

                if evaluation["status"] == EvaluatorStatus.PASS.value:
                    run_state.pass_stage(
                        StageId.EVALUATOR,
                        StageResultCode.EVALUATION_PASS,
                        artifacts=(evaluation_artifact, "candidate_identity_current.json"),
                    )
                    break
                if evaluation["status"] == EvaluatorStatus.FINDINGS.value:
                    run_state.route_stage(
                        StageId.EVALUATOR,
                        outcome=WorkflowOutcome.REPAIR,
                        result_code=StageResultCode.EVALUATOR_FINDINGS,
                        reason_code="EVALUATOR_FINDINGS",
                        artifacts=(evaluation_artifact, "candidate_identity_current.json"),
                    )
                    if fix_cycles >= max_fix:
                        if benchmark_evidence and heldout_specs:
                            diagnostic_results = run_checks(
                                heldout_specs,
                                workspace=workspace,
                                toolchain=toolchain,
                                runtime_root=recorder.root / "heldout_diagnostic_after_max_fix",
                                label="HELD-OUT DIAGNOSTIC (NO FEEDBACK)",
                            )
                            recorder.write_json(
                                "heldout_diagnostic_after_max_fix.json",
                                check_records(diagnostic_results),
                            )
                            print("HELDOUT_DIAGNOSTIC_ONLY: result is not returned to Implementer")
                        raise RuntimeError("Evaluator still finds defects after max_fix_cycles")
                    fix_cycles += 1
                    current_specs = active_repair_specs()
                    _, stamp_path, self_verify_command = prepare_self_verify_runner(
                        workspace=workspace,
                        specs=current_specs,
                        toolchain=toolchain,
                    )
                    implementation_report_index += 1
                    run_state.begin_stage(StageId.IMPLEMENTER)
                    report = run_implementer_report(
                        codex,
                        thread_id=implementer_thread,
                        prompt=build_evaluator_repair_prompt(
                            evaluation,
                            implementation_contract=implementation_contract,
                            self_verify_command=self_verify_command,
                        ),
                        timeout=timeout,
                        label=f"REPAIR EVALUATION #{fix_cycles}",
                        implementation_contract=implementation_contract,
                        self_verify_command=self_verify_command,
                        workspace=workspace,
                        stamp_path=stamp_path,
                        plan=plan,
                    )
                    report_artifact = f"implementation_report_{implementation_report_index:02d}.json"
                    recorder.write_json(report_artifact, report)
                    observe_candidate("IMPLEMENTER_REPAIR_EVALUATION")
                    if report["status"] != ImplementerStatus.COMPLETE.value:
                        run_state.route_stage(
                            StageId.IMPLEMENTER,
                            outcome=WorkflowOutcome.BLOCKED,
                            result_code=StageResultCode.BLOCKED,
                            reason_code="IMPLEMENTER_BLOCKED",
                            artifacts=(report_artifact, "candidate_identity_current.json"),
                        )
                        print("HARNESS_TASK_STOPPED: IMPLEMENTER_BLOCKED")
                        return 2
                    run_state.pass_stage(
                        StageId.IMPLEMENTER,
                        StageResultCode.IMPLEMENTATION_COMPLETE,
                        artifacts=(report_artifact, "candidate_identity_current.json"),
                    )
                    register_report_checks(report)
                    continue
                if evaluation["status"] == EvaluatorStatus.REPLAN_REQUIRED.value:
                    run_state.route_stage(
                        StageId.EVALUATOR,
                        outcome=WorkflowOutcome.REPLAN,
                        result_code=StageResultCode.REPLAN_REQUIRED,
                        reason_code="EVALUATOR_REPLAN_REQUIRED",
                        artifacts=(evaluation_artifact, "candidate_identity_current.json"),
                    )
                    if replan_cycles >= max_replan:
                        raise RuntimeError("Evaluator requested replan after max_replan_cycles")
                    replan_cycles += 1
                    run_state.invalidate(
                        InvalidationTrigger.REPLAN_REQUIRED,
                        detail=evaluation["replan_reason"],
                    )
                    print(f"=== REPLAN #{replan_cycles} ===")
                    run_state.begin_stage(StageId.PLANNER)
                    plan = run_planner(
                        codex,
                        workspace=workspace,
                        task_prompt=manifest["prompt"],
                        preflight=preflight,
                        replan_context=(
                            "Current candidate was rejected by a blind Evaluator. "
                            "Observed reason (not a reference implementation):\n"
                            + evaluation["replan_reason"]
                        ),
                        on_heartbeat=make_heartbeat(f"REPLAN #{replan_cycles}"),
                        on_thread_started=_thread_recorder(recorder, f"planner_replan_{replan_cycles}"),
                        timeout=timeout,
                    )
                    validate_plan_artifact(plan, workspace=workspace)
                    replan_artifact = f"replan_{replan_cycles:02d}.json"
                    recorder.write_json(replan_artifact, plan)
                    run_state.bump_revision(RevisionKind.PLAN, artifact=replan_artifact)
                    if plan["status"] != PlannerStatus.READY.value:
                        if plan["status"] == PlannerStatus.BLOCKED.value:
                            run_state.route_stage(
                                StageId.PLANNER,
                                outcome=WorkflowOutcome.BLOCKED,
                                result_code=StageResultCode.BLOCKED,
                                reason_code="PLANNER_BLOCKED_AFTER_REPLAN",
                                artifacts=(replan_artifact,),
                            )
                        else:
                            run_state.route_stage(
                                StageId.PLANNER,
                                outcome=WorkflowOutcome.NEEDS_USER_DECISION,
                                result_code=StageResultCode.NEEDS_USER_DECISION,
                                reason_code="PLANNER_NEEDS_USER_DECISION_AFTER_REPLAN",
                                artifacts=(replan_artifact,),
                            )
                        print("HARNESS_TASK_STOPPED:", plan["status"])
                        return 2
                    run_state.pass_stage(
                        StageId.PLANNER,
                        StageResultCode.PLANNER_READY,
                        artifacts=(replan_artifact,),
                    )
                    run_state.begin_stage(StageId.IMPLEMENTATION_CONTRACT)
                    implementation_contract = build_implementation_contract(
                        plan, task_prompt=manifest["prompt"]
                    )
                    contract_artifact = f"implementation_contract_replan_{replan_cycles:02d}.json"
                    recorder.write_json(contract_artifact, implementation_contract)
                    run_state.bump_revision(
                        RevisionKind.IMPLEMENTATION_CONTRACT,
                        artifact=contract_artifact,
                    )
                    run_state.pass_stage(
                        StageId.IMPLEMENTATION_CONTRACT,
                        StageResultCode.IMPLEMENTATION_CONTRACT_READY,
                        artifacts=(contract_artifact,),
                    )
                    current_specs = active_repair_specs()
                    _, stamp_path, self_verify_command = prepare_self_verify_runner(
                        workspace=workspace,
                        specs=current_specs,
                        toolchain=toolchain,
                    )
                    implementation_report_index += 1
                    run_state.begin_stage(StageId.IMPLEMENTER)
                    report = run_implementer_report(
                        codex,
                        thread_id=implementer_thread,
                        prompt=build_implementation_prompt(
                            manifest["prompt"],
                            plan,
                            implementation_contract=implementation_contract,
                            self_verify_command=self_verify_command,
                            toolchain=toolchain,
                            allowed_paths=allowed_paths,
                        ),
                        timeout=timeout,
                        label=f"IMPLEMENT REPLAN #{replan_cycles}",
                        implementation_contract=implementation_contract,
                        self_verify_command=self_verify_command,
                        workspace=workspace,
                        stamp_path=stamp_path,
                        plan=plan,
                    )
                    report_artifact = f"implementation_report_{implementation_report_index:02d}.json"
                    recorder.write_json(report_artifact, report)
                    observe_candidate("IMPLEMENTER_REPLAN")
                    if report["status"] != ImplementerStatus.COMPLETE.value:
                        run_state.route_stage(
                            StageId.IMPLEMENTER,
                            outcome=WorkflowOutcome.BLOCKED,
                            result_code=StageResultCode.BLOCKED,
                            reason_code="IMPLEMENTER_BLOCKED_AFTER_REPLAN",
                            artifacts=(report_artifact, "candidate_identity_current.json"),
                        )
                        print("HARNESS_TASK_STOPPED: IMPLEMENTER_BLOCKED")
                        return 2
                    run_state.pass_stage(
                        StageId.IMPLEMENTER,
                        StageResultCode.IMPLEMENTATION_COMPLETE,
                        artifacts=(report_artifact, "candidate_identity_current.json"),
                    )
                    register_report_checks(report)
                    continue

                if evaluation["status"] == EvaluatorStatus.BLOCKED.value:
                    run_state.route_stage(
                        StageId.EVALUATOR,
                        outcome=WorkflowOutcome.BLOCKED,
                        result_code=StageResultCode.BLOCKED,
                        reason_code="EVALUATOR_BLOCKED",
                        artifacts=(evaluation_artifact,),
                    )
                else:
                    run_state.route_stage(
                        StageId.EVALUATOR,
                        outcome=WorkflowOutcome.NEEDS_USER_DECISION,
                        result_code=StageResultCode.NEEDS_USER_DECISION,
                        reason_code="EVALUATOR_NEEDS_USER_DECISION",
                        artifacts=(evaluation_artifact,),
                    )
                print("HARNESS_TASK_STOPPED:", evaluation["status"])
                return 2

            execution_metrics = {
                "self_verify": "PASS",
                "dynamic_checks": len(dynamic_specs),
                "first_evaluation_pass": first_evaluation_pass,
                "repair_cycles": fix_cycles,
                "replan_cycles": replan_cycles,
            }
            recorder.write_json("execution_metrics.json", execution_metrics)
            recorder.write_json(
                "dynamic_checks.json",
                {"checks": dynamic_specs, "notes": dynamic_notes},
            )
            print(
                "EXECUTION_METRICS:",
                f"self_verify=PASS dynamic_checks={len(dynamic_specs)} "
                f"first_evaluation_pass={first_evaluation_pass} repair_cycles={fix_cycles}",
            )
        run_state.begin_stage(StageId.FINAL_GATE)
        final_candidate_before = observe_candidate("FINAL_GATE_BEFORE")
        changed_paths = list(final_candidate_before.changed_paths)
        enforce_allowed_paths(changed_paths, allowed_paths)

        heldout_artifacts: list[str] = []
        if heldout_specs:
            heldout_results = run_checks(
                heldout_specs,
                workspace=workspace,
                toolchain=toolchain,
                runtime_root=recorder.root / "heldout",
                label="HELD-OUT",
            )
            heldout_artifact = "heldout_results.json"
            recorder.write_json(heldout_artifact, check_records(heldout_results))
            heldout_artifacts.append(heldout_artifact)
            final_candidate_after_heldout = observe_candidate("FINAL_GATE_AFTER_HELDOUT")
            if final_candidate_after_heldout.candidate_id != final_candidate_before.candidate_id:
                raise RuntimeError("Held-out grader mutated the candidate")
            if any(not item.passed for item in heldout_results):
                raise RuntimeError(
                    "Held-out grader failed. Trial stops; assertion is not returned to Implementer."
                )
            print("HELDOUT_PASS")

        final_candidate = observe_candidate("FINAL_GATE_PACKAGE")
        if final_candidate.candidate_id != final_candidate_before.candidate_id:
            raise RuntimeError("Candidate changed during Final Gate")
        patch, patch_metadata = package_candidate(session=session, recorder=recorder)
        packaged_candidate = observe_candidate("FINAL_GATE_AFTER_PACKAGE")
        if packaged_candidate.candidate_id != final_candidate.candidate_id:
            raise RuntimeError("Candidate packaging mutated the candidate")
        success_code = (
            StageResultCode.HARNESS_BENCHMARK_PASS
            if workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK
            else StageResultCode.HARNESS_TASK_PASS
        )
        final_acceptance = {
            "schema_version": "final-acceptance.v1",
            "task_id": manifest["task_id"],
            "workflow_version": WORKFLOW_VERSION,
            "harness_version": __version__,
            "mode": workflow_mode.value,
            "pipeline_profile": pipeline_profile.value,
            "baseline_sha": final_candidate.baseline_sha,
            "workspace_head": final_candidate.workspace_head,
            "candidate_id": final_candidate.candidate_id,
            "changed_paths": list(final_candidate.changed_paths),
            "revision_snapshot": dict(run_state.data["revisions"]),
            "patch": patch_metadata,
            "quality_gate_status": StageResultCode.FINAL_ACCEPTANCE_PASS.value,
            "expected_terminal_result": success_code.value,
        }
        recorder.write_json("final_acceptance.json", final_acceptance)
        delivery_record = deliver_candidate(session=session, patch=patch)
        delivered_candidate = observe_candidate("FINAL_GATE_AFTER_DELIVERY")
        if delivered_candidate.candidate_id != final_candidate.candidate_id:
            raise RuntimeError("Result delivery mutated the managed candidate")
        recorder.write_json("delivery_record.json", delivery_record)
        final_artifacts = [
            "candidate_identity_current.json",
            "candidate.patch",
            "final_acceptance.json",
            "delivery_record.json",
            *heldout_artifacts,
        ]
        run_state.pass_stage(
            StageId.FINAL_GATE,
            success_code,
            artifacts=final_artifacts,
        )
        run_state.mark_terminal(
            outcome=WorkflowOutcome.PASS,
            result_code=success_code,
        )
        print(success_code.value)
        print(f"TOTAL_ELAPSED: {time.monotonic() - started:.2f}s")
        return 0
    except (RuntimeError, ArtifactContractError, OSError, ValueError) as exc:
        if run_state is not None:
            try:
                run_state.fail_active_stage(
                    reason_code="HARNESS_EXCEPTION",
                    detail=str(exc),
                )
            except Exception as state_exc:
                print(
                    "RUN_STATE_UPDATE_FAIL:",
                    state_exc,
                    file=sys.stderr,
                )
        print("HARNESS_TASK_FAIL:", exc, file=sys.stderr)
        if session is not None:
            print("MANAGED_WORKTREE_ON_EXIT:", session.workspace, file=sys.stderr)
        if recorder is not None:
            print("RUN_DIR:", recorder.root, file=sys.stderr)
        print(f"TOTAL_ELAPSED: {time.monotonic() - started:.2f}s", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
