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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from slivin_harness import __version__
from slivin_harness.app_server import CodexAppServer, TurnTimeoutError
from slivin_harness.build_identity import detect_harness_build_identity
from slivin_harness.console import configure_utf8_stdio
from slivin_harness.control_plane import (
    ArtifactVisibility,
    ControllerPlane,
    SelfVerifyBinding,
)
from slivin_harness.execution import ExecutionBroker, ExecutionRole
from slivin_harness.evaluator import (
    run_evaluator,
    validate_blind_audit,
    validate_evaluation_artifact as validate_evaluator_artifact,
)
from slivin_harness.implementer import (
    IMPLEMENTER_REPORT_SCHEMA,
    build_implementation_contract,
    compact_plan_context,
    parse_implementation_report,
    validate_implementation_contract,
    validate_implementation_report,
)
from slivin_harness.planner import run_planner, validate_plan_artifact as validate_planner_artifact
from slivin_harness.phase4 import (
    CheckClassification,
    CheckRegistry,
    Phase4ContractError,
    ensure_changed_tests_are_covered,
    fingerprint_workspace_candidate,
    git_changed_paths,
)
from slivin_harness.phase5 import (
    Phase5ContractError,
    ProjectRuntimeConfig,
    ProjectRuntimeManager,
    ProjectRuntimeState,
    expand_contract_and_verification_plan,
)
from slivin_harness.phase6 import (
    Phase6ContractError,
    RuntimeExecutor,
    build_contract_closure_record,
    runtime_available_capabilities,
    runtime_command_gaps,
    runtime_environment_gaps,
    runtime_requirement_gaps,
    runtime_scenarios_from_config,
    runtime_selected_scenarios,
    validate_contract_closure_record,
    validate_runtime_scenario_commands,
)
from slivin_harness.phase7 import (
    Phase7Error,
    artifact_digest,
    build_final_acceptance,
    build_patch_reconstruction_proof,
    classify_heldout_results,
    deliver_candidate_transaction,
    reconcile_quality_gate,
    reset_workspace_for_semantic_replan,
    sanitize_benchmark_toolchain,
)
from slivin_harness.preflight import (
    ToolProbeRegistry,
    expand_check_command,
    resolve_python_command,
    run_static_toolchain_preflight,
)
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
from slivin_harness.runtime_projection import (
    RuntimeProjectionIntegrityError,
    RuntimeProjectionIntegrityManager,
)
from slivin_harness.task_contract import (
    TASK_CONTRACT_VERSION,
    run_task_contract_normalizer,
    validate_task_contract,
)
from slivin_harness.verification import (
    VERIFICATION_PLAN_VERSION,
    available_capabilities,
    compile_verification_plan,
    configured_capabilities,
    required_capability_gaps,
    validate_verification_plan,
)
from slivin_harness.workflow import (
    EvaluatorStatus,
    InvalidationTrigger,
    ImplementerStatus,
    PipelineProfile,
    PlannerStatus,
    RevisionKind,
    RuntimeStatus,
    StageId,
    StageResultCode,
    TaskContractStatus,
    WORKFLOW_VERSION,
    WorkflowMode,
    WorkflowOutcome,
    enum_values,
    workflow_snapshot,
)
from slivin_harness.workspace import (
    WorkspaceSession,
    add_worktree_excludes,
    assert_safe_runtime_path,
    build_candidate_patch,
    prepare_workspace_session,
    remove_managed_workspace,
)

configure_utf8_stdio()
HARNESS_ROOT = Path(__file__).resolve().parent
DEFAULT_CODEX_NAMES = ("codex.cmd", "codex")
TRUSTED_CHECK_IDS = ("git.diff-check",)

IMPLEMENTER_INSTRUCTIONS = """
Ты Implementer внутри Slivin Harness.

Работай только в текущем workspace и следуй repository instructions/AGENTS.md.
Запрещены git add/commit/push/pull/merge/rebase/reset/restore/switch/checkout/clean.
Не меняй .git и не ищи готовые решения в других копиях проекта или архивах.

Правила работы:
- сначала проверь исходную задачу и compact plan по реальному коду;
- USER TASK CONTRACT задаёт неизменяемый product intent; Implementation Contract — обязательный минимум результата, preservation, state, consumers и risks; Planner context остаётся гипотезой, а не готовым patch;
- до завершения явно проверь каждый contract item; consumer можно отметить NOT_AFFECTED только с конкретным evidence, что он недостижим/не затронут;
- делай минимальное целостное исправление, включая достижимых sibling consumers;
- сохрани явно требуемое старое поведение и target уже начатого stateful action;
- обнови документацию, если contract требует это или реально изменился пользовательский/API/архитектурный контракт;
- добавь contract-oriented regression tests, а не проверку конкретной формы patch;
- обязательно запусти перед завершением Harness-owned SELF_VERIFY_COMMAND: он использует тот же trusted toolchain и те же repair checks, что затем независимо повторит Controller;
- typed Verification Plan задаёт обязательный уровень доказательства; не подменяй runtime proof локальным тестом;
- если при исследовании найдены дополнительные существующие test files, зарегистрируй их как typed `registered_checks`/`additional_check_paths`; trusted check ID сейчас только `git.diff-check`, произвольные/неизвестные Controller команды и IDs запрещены;
- если найден material consumer/risk вне active Contract, верни его в `discovered_obligations`; не ослабляй и не редактируй Contract самостоятельно;
- temp/cache размещай в .harness_tmp;
- если две разные попытки записи завершаются Permission denied/Access denied, не повторяй их: зафиксируй инфраструктурную блокировку;
- не ослабляй тесты ради PASS;
- финальный ответ — structured Implementation Report. COMPLETE допустим только после self-verification PASS и проверки всего Implementation Contract. REPLAN_REQUIRED/BLOCKED/NEEDS_USER_DECISION требуют одну конкретную reason + evidence, а не искусственный ledger по каждому item.
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
    returncode: int | None
    output: str
    timed_out: bool = False
    infra_error: bool = False
    duration_seconds: float = 0.0
    candidate_before: str | None = None
    candidate_after: str | None = None
    execution_enforcement: str = "ADVISORY"
    runtime_integrity_reason_code: str | None = None

    @property
    def classification(self) -> CheckClassification:
        if (
            self.candidate_before is not None
            and self.candidate_after is not None
            and self.candidate_before != self.candidate_after
        ):
            return CheckClassification.MUTATED_CANDIDATE
        if self.infra_error:
            return CheckClassification.INFRA_ERROR
        if self.timed_out:
            return CheckClassification.TIMEOUT
        if self.returncode == 0:
            return CheckClassification.PASS
        return CheckClassification.FAIL

    @property
    def passed(self) -> bool:
        return self.classification is CheckClassification.PASS


class HarnessControlledStop(RuntimeError):
    """A correctly routed BLOCKED/decision outcome, not an internal Harness crash."""


class RunRecorder:
    def __init__(self, task_id: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = hashlib.sha256(os.urandom(16)).hexdigest()[:8]
        self.root = HARNESS_ROOT / "runs" / task_id / f"{stamp}-{suffix}"
        self.root.mkdir(parents=True, exist_ok=True)
        self._control_plane: ControllerPlane | None = None

    @property
    def control_plane(self) -> ControllerPlane:
        # Lazy initialization keeps test recorders that override only ``root``
        # compatible while making the authoritative plane unambiguous.
        plane = getattr(self, "_control_plane", None)
        if plane is None:
            plane = ControllerPlane(self.root)
            self._control_plane = plane
        return plane

    @property
    def private_root(self) -> Path:
        return self.control_plane.private_root

    def write_json(self, name: str, value: object) -> Path:
        return self.control_plane.write_public_json(name, value)

    def write_private_json(self, name: str, value: object) -> Path:
        return self.control_plane.write_private_json(name, value)

    def write_authoritative_json(self, name: str, value: object) -> Path:
        """Write the private authority plus a public diagnostic mirror."""
        self.control_plane.write_private_json(name, value)
        return self.control_plane.write_public_json(name, value)

    def write_once_authoritative_json(self, name: str, value: object) -> Path:
        """Create an immutable Controller artifact and its diagnostic mirror once."""
        self.control_plane.write_json_once(
            name,
            value,
            visibility=ArtifactVisibility.PRIVATE,
        )
        return self.control_plane.write_json_once(
            name,
            value,
            visibility=ArtifactVisibility.PUBLIC,
        )

    def write_text(self, name: str, value: str) -> Path:
        return self.control_plane.write_text(
            name, value, visibility=ArtifactVisibility.PUBLIC
        )

    def write_bytes(self, name: str, value: bytes) -> Path:
        return self.control_plane.write_bytes(
            name, value, visibility=ArtifactVisibility.PUBLIC
        )


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

    has_heldout = any(spec["feedback"] == "heldout" for spec in checks)
    if has_heldout and not benchmark.get("baseline_failure_marker"):
        raise RuntimeError(
            "Historical held-out checks require benchmark.baseline_failure_marker "
            "so semantic failures can be distinguished from infrastructure errors"
        )
    if has_heldout and manifest.get("result_mode", "keep_worktree") != "keep_worktree":
        raise RuntimeError("Historical benchmark requires result_mode=keep_worktree")


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
    resolved: dict[str, str] = {}
    for name, raw in merged.items():
        formatted = os.path.expandvars(str(raw)).format(
            home=str(Path.home()),
            harness_root=str(HARNESS_ROOT),
            project_root=str(project_root) if project_root else "",
        )
        if not any(sep in formatted for sep in ("/", "\\")) and not formatted.startswith("~"):
            found = shutil.which(formatted)
            resolved[name] = str(Path(found).resolve()) if found else formatted
        else:
            resolved[name] = str(
                resolve_runtime_path(formatted, project_root=project_root)
            )
    return resolved


def validate_toolchain(toolchain: dict[str, str]) -> None:
    for name, raw in toolchain.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
            raise RuntimeError(f"Invalid toolchain entry name: {name!r}")
        if not isinstance(raw, str) or not raw.strip():
            raise RuntimeError(f"Toolchain entry must be a non-empty string: {name}")


def resolve_project_runtime_config(
    local_config: dict,
    *,
    project_name: str | None,
    source_repo: Path | None,
) -> ProjectRuntimeConfig | None:
    if not project_name:
        return None
    project = local_config.get("projects", {}).get(project_name, {})
    if not isinstance(project, dict):
        return None
    raw = project.get("runtime")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeError(f"[projects.{project_name}.runtime] must be a table")
    allowed = {
        "enabled",
        "bootstrap_python",
        "expected_python",
        "venv",
        "dependency_files",
        "pip_install_args",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise RuntimeError(
            f"Unknown [projects.{project_name}.runtime] fields: {', '.join(unknown)}"
        )
    if raw.get("enabled", True) is False:
        return None
    bootstrap = raw.get("bootstrap_python")
    if not isinstance(bootstrap, str) or not bootstrap.strip():
        raise RuntimeError(
            f"[projects.{project_name}.runtime] requires bootstrap_python"
        )
    expected = raw.get("expected_python")
    if not isinstance(expected, str) or not expected.strip():
        raise RuntimeError(
            f"[projects.{project_name}.runtime] requires expected_python, e.g. '3.12'"
        )
    dependency_files = raw.get("dependency_files", ["requirements.txt"])
    if not isinstance(dependency_files, list) or not all(
        isinstance(item, str) for item in dependency_files
    ):
        raise RuntimeError("runtime.dependency_files must be an array of strings")
    pip_args = raw.get("pip_install_args", [])
    if not isinstance(pip_args, list) or not all(isinstance(item, str) for item in pip_args):
        raise RuntimeError("runtime.pip_install_args must be an array of strings")
    venv = raw.get("venv", ".venv")
    if not isinstance(venv, str):
        raise RuntimeError("runtime.venv must be a string")
    bootstrap_path = _resolve_tool_path(str(bootstrap), project_root=source_repo)
    return ProjectRuntimeConfig(
        bootstrap_python=bootstrap_path,
        expected_python=expected,
        venv_relative=venv,
        dependency_files=tuple(dependency_files),
        pip_install_args=tuple(pip_args),
    )


def exposed_runtime_file_snapshot(
    session: WorkspaceSession,
    *,
    control_plane: ControllerPlane,
) -> dict[str, str]:
    """Fingerprint small runtime-only files with a Controller-private keyed HMAC.

    A projected dependency directory is already checked during its Controller
    physical copy and must not trigger a full recursive hash on every workflow
    boundary.  It remains Git-excluded and is never candidate material.
    """

    result: dict[str, str] = {}
    projected = {item.relative_path.replace("\\", "/") for item in session.runtime_projections}
    for raw in session.exposed_paths:
        if raw.replace("\\", "/") in projected:
            continue
        root = session.workspace / raw
        if root.is_file():
            rel = raw.replace("\\", "/")
            result[rel] = control_plane.keyed_fingerprint(
                root.read_bytes(), context=f"runtime-file:{rel}"
            )
        elif root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(session.workspace).as_posix()
                    result[rel] = control_plane.keyed_fingerprint(
                        path.read_bytes(), context=f"runtime-file:{rel}"
                    )
    return result


def restore_exposed_runtime_files(session: WorkspaceSession) -> None:
    """Restore runtime-only files from the unchanged source checkout.

    Files exposed through `.worktreeinclude` or the explicit local override are
    runtime inputs, not candidate output.  Restoring them is Controller-owned:
    the agent never receives their previous contents through a prompt or log.
    """

    if session.source_repo is None:
        return
    projected = {item.relative_path.replace("\\", "/") for item in session.runtime_projections}
    for raw in session.exposed_paths:
        if raw.replace("\\", "/") in projected:
            continue
        source = session.source_repo / raw
        target = session.workspace / raw
        assert_safe_runtime_path(session.source_repo, source, include_leaf=True)
        assert_safe_runtime_path(session.workspace, target, include_leaf=False)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            raise RuntimeError(f"Runtime-only source path disappeared: {raw}")


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
    return expand_check_command(
        command,
        workspace=workspace,
        harness_root=HARNESS_ROOT,
        toolchain=toolchain,
    )


def candidate_content_fingerprint(workspace: Path) -> str:
    """Backward-compatible alias for the canonical candidate identity."""
    return build_candidate_identity(workspace).candidate_id


def controller_check_fingerprint(workspace: Path) -> str:
    """Freeze a candidate for Controller checks, including non-Git test fixtures.

    Real task workspaces are Git worktrees and use candidate.v1. Some isolated
    unit tests exercise the check runner with a plain temporary directory; those
    fixtures still need mutation detection, but they do not have a Git baseline.
    """
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if probe.returncode == 0 and probe.stdout.strip().lower() == "true":
        return candidate_content_fingerprint(workspace)
    return fingerprint_workspace_candidate(workspace)


def _display_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    import shlex

    return shlex.join(command)


class SelfVerifyCommand(list[str]):
    """Agent-visible argv carrying a private Controller check definition in memory."""

    def __init__(self, argv: list[str], *, trusted_specs: list[dict]) -> None:
        super().__init__(argv)
        self.trusted_specs = tuple(
            {
                "name": str(item["name"]),
                "command": [str(part) for part in item["command"]],
                "timeout_seconds": int(item["timeout_seconds"]),
            }
            for item in trusted_specs
        )


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
    template = r'''from __future__ import annotations
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

WORKSPACE = Path(__WORKSPACE__)
RUNTIME = Path(__RUNTIME__)
STAMP = Path(__STAMP__)
CHECKS = json.loads(__CHECKS__)


def _git(*args):
    result = subprocess.run(
        ["git", *args],
        cwd=WORKSPACE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def _excluded(path):
    normalized = path.replace("\\", "/").strip("/")
    return any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in (".harness_tmp", ".venv")
    )


def _candidate_paths():
    paths = {
        item.replace("\\", "/")
        for item in _git(
            "diff", "--name-only", "--no-renames", "-z", "HEAD", "--"
        ).split("\0")
        if item
    }
    paths.update(
        item.replace("\\", "/")
        for item in _git(
            "ls-files", "--others", "--exclude-standard", "-z"
        ).split("\0")
        if item
    )
    return sorted(item for item in paths if not _excluded(item))


def _mode(rel, path):
    if path.is_symlink():
        return "120000"
    if path.is_file():
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", rel],
            cwd=WORKSPACE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).returncode == 0
        if tracked:
            raw = _git("diff", "--raw", "--no-renames", "HEAD", "--", rel).strip()
            if raw.startswith(":"):
                fields = raw.split(None, 5)
                if len(fields) >= 2 and fields[1] != "000000":
                    return fields[1]
        return "100755" if bool(path.stat().st_mode & stat.S_IXUSR) else "100644"
    if not path.exists():
        raw = _git("ls-tree", "HEAD", "--", rel).strip()
        return raw.split(None, 1)[0] if raw else None
    return None


def current_candidate_id():
    head = _git("rev-parse", "HEAD").strip()
    entries = []
    for rel in _candidate_paths():
        path = WORKSPACE / rel
        if path.is_symlink():
            raw = os.readlink(path).encode("utf-8", errors="surrogateescape")
            entries.append({
                "path": rel,
                "state": "symlink",
                "mode": _mode(rel, path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            })
        elif path.is_file():
            raw = path.read_bytes()
            entries.append({
                "path": rel,
                "state": "file",
                "mode": _mode(rel, path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            })
        elif not path.exists():
            entries.append({
                "path": rel,
                "state": "deleted",
                "mode": _mode(rel, path),
            })
        else:
            entries.append({"path": rel, "state": "non_file", "mode": None})
    payload = {
        "schema_version": "candidate.v1",
        "baseline_sha": head,
        "workspace_head": head,
        "entries": entries,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


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
    candidate_id = current_candidate_id()
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
        .replace("__CHECKS__", repr(json.dumps(checks, ensure_ascii=False)))
    )
    script_path.write_text(script, encoding="utf-8")
    command = SelfVerifyCommand(
        [sys.executable, str(script_path)],
        trusted_specs=checks,
    )
    return script_path, stamp_path, command


def verify_self_verification_stamp(
    *,
    workspace: Path,
    stamp_path: Path,
    control_plane: ControllerPlane | None = None,
    run_state: RunState | None = None,
    check_registry_digest: str | None = None,
    issue_receipt: bool = True,
) -> bool:
    """Validate the agent-writable claim and promote it to a private receipt.

    The claim inside the workspace is deliberately non-authoritative. Only the
    Controller recomputes candidate identity, binds the current revision vector,
    and writes the HMAC-protected private receipt.
    """
    if not stamp_path.is_file():
        return False
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    candidate_id = candidate_content_fingerprint(workspace)
    if not bool(
        stamp.get("passed") is True
        and (stamp.get("candidate_id") or stamp.get("candidate_fingerprint"))
        == candidate_id
    ):
        return False
    if issue_receipt and control_plane is not None and run_state is not None:
        raw_binding = run_state.verification_binding(
            candidate_id=candidate_id,
            check_registry_digest=check_registry_digest,
        )
        binding = SelfVerifyBinding(**raw_binding)
        control_plane.issue_self_verify_receipt(binding=binding, claim=stamp)
        if not control_plane.verify_self_verify_receipt(binding=binding):
            return False
    return True


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
                    resolve_python_command(toolchain).value,
                    "manage.py", "test", label,
                ]
                break
            if "-m" in cmd and "pytest" in cmd:
                python_cmd = [
                    resolve_python_command(toolchain).value,
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



def build_trusted_check_id_specs(
    ids: list[str],
    *,
    base_specs: list[dict],
) -> tuple[list[dict], list[str]]:
    """Resolve Controller-owned check IDs to concrete trusted commands.

    A syntactically safe ID is not enough: every accepted ID must map to a
    concrete Harness-owned specification.  Otherwise an agent could add a
    label to the Verification Plan without adding an executable proof.
    """

    specs: list[dict] = []
    notes: list[str] = []
    seen: set[str] = set()
    for raw in ids:
        value = str(raw)
        if value in seen:
            continue
        seen.add(value)
        if value == "git.diff-check":
            spec = {
                "name": "Trusted check: git.diff-check",
                "feedback": "repair",
                "command": ["git", "diff", "--check"],
                "timeout_seconds": 30,
            }
        else:
            raise RuntimeError(f"Unknown trusted check id: {value!r}")
        command_key = tuple(str(part) for part in spec["command"])
        already = any(
            tuple(str(part) for part in base.get("command", [])) == command_key
            for base in base_specs
        )
        if already:
            notes.append(f"TRUSTED_CHECK_ALREADY_COVERED {value}")
        else:
            specs.append(spec)
    return specs, notes

def run_check(
    spec: dict,
    *,
    workspace: Path,
    toolchain: dict[str, str],
    runtime_tmp: Path,
    execution_broker: ExecutionBroker | None = None,
    execution_role: ExecutionRole = ExecutionRole.CONTROLLER_CHECK,
) -> CheckResult:
    command = expand_command(spec["command"], workspace=workspace, toolchain=toolchain)
    runtime_tmp.mkdir(parents=True, exist_ok=True)
    extra_env = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(runtime_tmp),
        "TMP": str(runtime_tmp),
        "TMPDIR": str(runtime_tmp),
        "XDG_CACHE_HOME": str(runtime_tmp / "cache"),
        "NPM_CONFIG_CACHE": str(runtime_tmp / "npm"),
        "SLIVIN_HARNESS_WORKSPACE": str(workspace.resolve()),
        "SLIVIN_HARNESS_ROOT": str(HARNESS_ROOT.resolve()),
    }
    env = (
        execution_broker.environment_for(execution_role, extra=extra_env)
        if execution_broker is not None
        else {**os.environ, **extra_env}
    )
    execution_enforcement = (
        execution_broker.policy_for(execution_role).filesystem_enforcement
        if execution_broker is not None
        else "ADVISORY"
    )
    candidate_before = controller_check_fingerprint(workspace)
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
            output=result.stdout or "",
            duration_seconds=time.monotonic() - started,
            candidate_before=candidate_before,
            candidate_after=controller_check_fingerprint(workspace),
            execution_enforcement=execution_enforcement,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return CheckResult(
            name=spec["name"],
            command=command,
            returncode=None,
            output=str(output),
            timed_out=True,
            duration_seconds=time.monotonic() - started,
            candidate_before=candidate_before,
            candidate_after=controller_check_fingerprint(workspace),
            execution_enforcement=execution_enforcement,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return CheckResult(
            name=spec["name"],
            command=command,
            returncode=None,
            output=f"{type(exc).__name__}: {exc}",
            infra_error=True,
            duration_seconds=time.monotonic() - started,
            candidate_before=candidate_before,
            candidate_after=controller_check_fingerprint(workspace),
            execution_enforcement=execution_enforcement,
        )



def run_checks(
    specs: list[dict],
    *,
    workspace: Path,
    toolchain: dict[str, str],
    runtime_root: Path,
    label: str,
    execution_broker: ExecutionBroker | None = None,
    execution_role: ExecutionRole = ExecutionRole.CONTROLLER_CHECK,
    runtime_integrity_manager: RuntimeProjectionIntegrityManager | None = None,
    batch_id: str | None = None,
) -> list[CheckResult]:
    print(f"=== {label} ===")
    started = time.monotonic()

    def execute() -> list[CheckResult]:
        results: list[CheckResult] = []
        for index, spec in enumerate(specs, start=1):
            result = run_check(
                spec,
                workspace=workspace,
                toolchain=toolchain,
                runtime_tmp=runtime_root / f"check_{index:02d}",
                execution_broker=execution_broker,
                execution_role=execution_role,
            )
            results.append(result)
        return results

    try:
        results = (
            runtime_integrity_manager.run_batch(batch_id or label, execute)
            if runtime_integrity_manager is not None
            else execute()
        )
    except RuntimeProjectionIntegrityError as exc:
        # Discard command output from an invalidated batch. In particular, a
        # held-out command that mutated its runtime must not publish hidden
        # output while being reclassified as infrastructure/integrity failure.
        print("RUNTIME_PROJECTION_INTEGRITY_FAILURE:", exc.reason_code)
        fingerprint = controller_check_fingerprint(workspace)
        results = [
            CheckResult(
                name=f"{label} runtime projection integrity",
                command=[],
                returncode=None,
                output="",
                infra_error=True,
                duration_seconds=time.monotonic() - started,
                candidate_before=fingerprint,
                candidate_after=fingerprint,
                runtime_integrity_reason_code=exc.reason_code,
            )
        ]
    else:
        for index, (spec, result) in enumerate(zip(specs, results), start=1):
            state = result.classification.value
            print(
                f"[{index}/{len(specs)}] {spec['name']}: "
                f"{state} ({result.duration_seconds:.2f}s)"
            )
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
            "protocol_version": "controller-checks.v1",
            "name": item.name,
            "command": item.command,
            "returncode": item.returncode,
            "timed_out": item.timed_out,
            "infra_error": item.infra_error,
            "classification": item.classification.value,
            "duration_seconds": item.duration_seconds,
            "output": item.output,
            "candidate_before": item.candidate_before,
            "candidate_after": item.candidate_after,
            "execution_enforcement": item.execution_enforcement,
            "runtime_integrity_reason_code": item.runtime_integrity_reason_code,
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


def validate_plan_artifact(
    plan: dict, *, workspace: Path, task_contract: dict
) -> None:
    """Compatibility entry point backed by the planner.v4 validator."""
    validate_planner_artifact(
        plan,
        workspace=workspace,
        task_contract=task_contract,
    )

def validate_evaluation_artifact(
    evaluation: dict, *, blind_audit: dict
) -> None:
    """Compatibility entry point backed by evaluator.v5 validation."""
    validate_blind_audit(blind_audit)
    validate_evaluator_artifact(evaluation, blind_audit=blind_audit)


def build_implementation_prompt(
    task_prompt: str,
    plan: dict | None,
    *,
    task_contract: dict,
    implementation_contract: dict,
    verification_plan: dict,
    self_verify_command: list[str],
    toolchain: dict[str, str],
    allowed_paths: list[str],
) -> str:
    compact_plan = compact_plan_context(plan)
    plan_block = (
        "FAST profile: no separate Planner artifact. Investigate implementation details yourself."
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
        else "Owner did not set a hard path boundary. Discover the smallest complete technical surface."
    )
    return f"""
RAW USER REQUEST:
--- BEGIN RAW USER REQUEST ---
{task_prompt}
--- END RAW USER REQUEST ---

--- BEGIN USER TASK CONTRACT ---
{json.dumps(task_contract, ensure_ascii=False, indent=2)}
--- END USER TASK CONTRACT ---

{plan_block}

--- BEGIN IMPLEMENTATION CONTRACT ---
{json.dumps(implementation_contract, ensure_ascii=False, indent=2)}
--- END IMPLEMENTATION CONTRACT ---

--- BEGIN VERIFICATION PLAN ---
{json.dumps(verification_plan, ensure_ascii=False, indent=2)}
--- END VERIFICATION PLAN ---

{boundary}

TRUSTED_TOOLCHAIN:
{json.dumps(toolchain, ensure_ascii=False, indent=2)}

SELF_VERIFY_COMMAND:
{_display_command(self_verify_command)}

Сделай самое небольшое полное решение. USER TASK CONTRACT и Implementation Contract
обязательны; compact Planner context остаётся проверяемой гипотезой. Не заменяй required
runtime/external proof локальным тестом. Затем запусти SELF_VERIFY_COMMAND, исправляй ошибки
до SELF_VERIFY_PASS и дай конкретное evidence по каждому active Contract item.

Дополнительные test paths для materially affected consumers можно передать только через
typed registered_checks/additional_check_paths; Controller сам выберет trusted runner.
Новые material consumers/risks передавай через discovered_obligations. Для
REPLAN_REQUIRED/BLOCKED/NEEDS_USER_DECISION дай reason и evidence; полный Contract ledger
нужен только для COMPLETE.
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
    *,
    implementation_contract: dict,
    self_verify_command: list[str],
    verification_plan: dict | None = None,
    reason: str = "Previous Implementer turn was interrupted by inactivity watchdog.",
) -> str:
    return f"""
{reason} Уже внесённые изменения в workspace сохранены, thread и исходный task остаются теми же.
НЕ начинай исследование заново и не откатывай подтверждённую работу.

1. Сначала посмотри текущий `git diff`/status и продолжи только незавершённые пункты.
2. Особое внимание удели ещё не доказанным risks/consumers из Implementation Contract.
3. Запусти актуальный SELF_VERIFY_COMMAND.
4. Верни COMPLETE только после evidence по каждому contract item; иначе BLOCKED с реальной причиной.

Implementation Contract:
{json.dumps(implementation_contract, ensure_ascii=False, indent=2)}

Verification Plan:
{json.dumps(verification_plan, ensure_ascii=False, indent=2) if verification_plan is not None else "(unchanged)"}

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


def build_runtime_repair_prompt(
    runtime_evidence: dict,
    *,
    implementation_contract: dict,
    self_verify_command: list[str],
) -> str:
    return f"""
Controller Runtime Verification обнаружил material failure текущего candidate.

--- BEGIN RUNTIME EVIDENCE ---
{json.dumps(runtime_evidence, ensure_ascii=False, indent=2)}
--- END RUNTIME EVIDENCE ---

Определи подтверждённую причину. Исправь candidate, но не подменяй required runtime
proof локальным тестом и не отключай readback/cleanup.
{_repair_contract_block(implementation_contract=implementation_contract, self_verify_command=self_verify_command)}
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
    execution_broker: ExecutionBroker | None = None,
    runtime_integrity_manager: RuntimeProjectionIntegrityManager | None = None,
) -> dict:
    if not specs:
        raise RuntimeError("Baseline gate requires at least one held-out check")
    results = run_checks(
        specs,
        workspace=workspace,
        toolchain=toolchain,
        runtime_root=runtime_root,
        label="BENCHMARK BASELINE GATE",
        execution_broker=execution_broker,
        execution_role=ExecutionRole.HELDOUT,
        runtime_integrity_manager=runtime_integrity_manager,
        batch_id="HISTORICAL_BENCHMARK_BASELINE_GATE",
    )
    integrity_failure = next(
        (item.runtime_integrity_reason_code for item in results if item.runtime_integrity_reason_code),
        None,
    )
    if integrity_failure is not None:
        raise HarnessControlledStop(integrity_failure)
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
    control_plane: ControllerPlane | None = None,
    run_state: RunState | None = None,
    check_registry_digest: str | None = None,
    runtime_integrity_manager: RuntimeProjectionIntegrityManager | None = None,
    execution_broker: ExecutionBroker | None = None,
) -> dict:
    def stop_for_integrity(
        reason_code: str,
        *,
        cause: BaseException | None = None,
    ) -> None:
        if run_state is not None:
            run_state.route_stage(
                StageId.IMPLEMENTER,
                outcome=WorkflowOutcome.BLOCKED,
                result_code=StageResultCode.BLOCKED,
                reason_code=reason_code,
                artifacts=("runtime_projection_integrity.json",),
            )
        if cause is not None:
            raise HarnessControlledStop(reason_code) from cause
        raise HarnessControlledStop(reason_code)

    current_prompt = prompt
    current_label = label
    timeout_continuations = 0
    if runtime_integrity_manager is not None and runtime_integrity_manager.active:
        try:
            runtime_integrity_manager.prepare_before_batch(
                f"IMPLEMENTER_DEVELOPMENT_START:{label}"
            )
        except RuntimeProjectionIntegrityError as exc:
            stop_for_integrity(exc.reason_code, cause=exc)
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
    stamp_matches_candidate = verify_self_verification_stamp(
        workspace=workspace,
        stamp_path=stamp_path,
        control_plane=control_plane,
        run_state=run_state,
        check_registry_digest=check_registry_digest,
        issue_receipt=False,
    )
    controller_self_verify_ok = True
    projection_confirmation_required = bool(
        runtime_integrity_manager is not None and runtime_integrity_manager.active
    )
    if (
        report.get("status") == ImplementerStatus.COMPLETE.value
        and projection_confirmation_required
    ):
        trusted_specs = getattr(self_verify_command, "trusted_specs", None)
        if trusted_specs is None:
            controller_self_verify_ok = False
        else:
            confirmation = run_checks(
                list(trusted_specs),
                workspace=workspace,
                toolchain={},
                runtime_root=(
                    control_plane.run_root / "self_verify_confirmation"
                    if control_plane is not None
                    else workspace / ".harness_tmp" / "self_verify_confirmation"
                ),
                label=f"CONTROLLER SELF-VERIFY CONFIRMATION {label}",
                execution_broker=execution_broker,
                execution_role=ExecutionRole.CONTROLLER_CHECK,
                runtime_integrity_manager=runtime_integrity_manager,
                batch_id=f"IMPLEMENTER_SELF_VERIFY:{label}",
            )
            integrity_failure = next(
                (
                    item.runtime_integrity_reason_code
                    for item in confirmation
                    if item.runtime_integrity_reason_code
                ),
                None,
            )
            if integrity_failure is not None:
                stop_for_integrity(integrity_failure)
            controller_self_verify_ok = bool(confirmation) and all(
                item.passed for item in confirmation
            )
    elif projection_confirmation_required:
        controller_self_verify_ok = False
    self_verification_ok = bool(stamp_matches_candidate and controller_self_verify_ok)
    if self_verification_ok:
        self_verification_ok = verify_self_verification_stamp(
            workspace=workspace,
            stamp_path=stamp_path,
            control_plane=control_plane,
            run_state=run_state,
            check_registry_digest=check_registry_digest,
            issue_receipt=True,
        )
    validate_implementation_report(
        report,
        contract=implementation_contract,
        changed_paths=changed_paths,
        self_verification_ok=self_verification_ok,
        documentation_paths=[],
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


def _phase4_loop_stalled(
    history: list[tuple[str, str]],
    *,
    candidate_id: str,
    failure_signature: str,
) -> bool:
    """Detect a repeated no-progress repair/replan state.

    A loop is stalled only when the same candidate/plan identity produces the
    same normalized failure twice in succession. A changed candidate or changed
    failure set is progress and resets the comparison naturally.
    """

    normalized = hashlib.sha256(failure_signature.encode("utf-8")).hexdigest()
    key = (str(candidate_id), normalized)
    stalled = bool(history and history[-1] == key)
    history.append(key)
    if len(history) > 32:
        del history[:-32]
    return stalled


def _phase4_route_implementer_terminal(
    *,
    run_state: RunState,
    report: dict,
    artifacts: tuple[str, ...],
    reason_suffix: str = "",
) -> None:
    """Record a non-COMPLETE Implementer result without misclassifying it."""

    status = str(report.get("status", ""))
    suffix = f"_{reason_suffix}" if reason_suffix else ""
    if status == ImplementerStatus.REPLAN_REQUIRED.value:
        run_state.route_stage(
            StageId.IMPLEMENTER,
            outcome=WorkflowOutcome.REPLAN,
            result_code=StageResultCode.REPLAN_REQUIRED,
            reason_code=f"IMPLEMENTER_REPLAN_REQUIRED{suffix}",
            artifacts=artifacts,
        )
    elif status == ImplementerStatus.NEEDS_USER_DECISION.value:
        run_state.route_stage(
            StageId.IMPLEMENTER,
            outcome=WorkflowOutcome.NEEDS_USER_DECISION,
            result_code=StageResultCode.NEEDS_USER_DECISION,
            reason_code=f"IMPLEMENTER_NEEDS_USER_DECISION{suffix}",
            artifacts=artifacts,
        )
    else:
        run_state.route_stage(
            StageId.IMPLEMENTER,
            outcome=WorkflowOutcome.BLOCKED,
            result_code=StageResultCode.BLOCKED,
            reason_code=f"IMPLEMENTER_BLOCKED{suffix}",
            artifacts=artifacts,
        )


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
        harness_build_identity = detect_harness_build_identity(
            harness_root=HARNESS_ROOT,
            version=__version__,
        )
        recorder.write_authoritative_json(
            "harness_build_identity.json",
            harness_build_identity.to_dict(),
        )
        print("HARNESS_VERSION:", harness_build_identity.version)
        print(
            "HARNESS_GIT_COMMIT:",
            harness_build_identity.git_commit or "UNAVAILABLE",
        )
        print(
            "HARNESS_GIT_DIRTY:",
            (
                str(harness_build_identity.git_dirty).lower()
                if harness_build_identity.git_dirty is not None
                else "unknown"
            ),
        )
        recorder.write_authoritative_json("manifest_snapshot.json", manifest)
        recorder.write_authoritative_json(
            "workflow_snapshot.json",
            workflow_snapshot(harness_version=__version__),
        )
        run_state = RunState.create(
            path=recorder.private_root / "run_state.json",
            public_mirror_path=recorder.root / "run_state.json",
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

        execution_broker = ExecutionBroker(
            workspace=workspace,
            run_root=recorder.root,
            private_root=recorder.private_root,
        )
        execution_policies = {
            role.value: execution_broker.policy_for(role).to_dict()
            for role in ExecutionRole
        }
        recorder.write_json("execution_policies.json", execution_policies)
        recorder.write_private_json(
            "execution_policy_bindings.json",
            {
                "schema_version": "execution-policy-bindings.v1",
                "roles": sorted(execution_policies),
                "authoritative": True,
            },
        )

        runtime_integrity_manager = RuntimeProjectionIntegrityManager(
            session=session,
            control_plane=recorder.control_plane,
        )
        try:
            runtime_integrity_manager.establish_baseline()
        except RuntimeProjectionIntegrityError as exc:
            # A copy that cannot be proven identical is a preparation failure,
            # not a managed workspace that may be retained for agent use.
            remove_managed_workspace(session)
            session = None
            run_state.route_stage(
                StageId.INTAKE_PREFLIGHT,
                outcome=WorkflowOutcome.BLOCKED,
                result_code=StageResultCode.BLOCKED,
                reason_code=exc.reason_code,
                artifacts=("runtime_projection_integrity.json",),
            )
            print("HARNESS_TASK_STOPPED:", exc.reason_code)
            return 2
        except (RuntimeError, OSError):
            remove_managed_workspace(session)
            session = None
            raise

        runtime_manager: ProjectRuntimeManager | None = None
        runtime_state: ProjectRuntimeState | None = None
        try:
            runtime_config = resolve_project_runtime_config(
                local_config,
                project_name=session.project_name,
                source_repo=session.source_repo,
            )
            if runtime_config is not None:
                add_worktree_excludes(workspace, [runtime_config.venv_relative])
                runtime_manager = ProjectRuntimeManager(
                    workspace=workspace,
                    config=runtime_config,
                    environment=execution_broker.environment_for(ExecutionRole.RUNTIME),
                )
                print("=== PROJECT RUNTIME BOOTSTRAP ===")
                runtime_state = runtime_manager.build(clean=True)
                recorder.write_authoritative_json(
                    "project_runtime_01.json", runtime_state.to_dict()
                )
                run_state.bump_revision(
                    RevisionKind.RUNTIME_ENVIRONMENT,
                    artifact="project_runtime_01.json",
                )
            else:
                run_state.bump_revision(
                    RevisionKind.RUNTIME_ENVIRONMENT,
                    artifact="preflight.json",
                )
        except (Phase5ContractError, RuntimeError) as exc:
            recorder.write_authoritative_json(
                "project_runtime_error.json",
                {
                    "schema_version": "project-runtime-error.v1",
                    "reason_code": "PROJECT_RUNTIME_BOOTSTRAP_FAILED",
                    "message": str(exc),
                },
            )
            run_state.route_stage(
                StageId.INTAKE_PREFLIGHT,
                outcome=WorkflowOutcome.BLOCKED,
                result_code=StageResultCode.BLOCKED,
                reason_code="PROJECT_RUNTIME_BOOTSTRAP_FAILED",
                artifacts=("project_runtime_error.json",),
            )
            print("HARNESS_TASK_STOPPED: PROJECT_RUNTIME_BOOTSTRAP_FAILED", exc)
            return 2

        preflight = capture_preflight(workspace)
        recorder.write_authoritative_json("preflight.json", preflight)
        repo_context = collect_repo_context(workspace)
        recorder.write_json("repo_context.json", repo_context)
        local_runtime_files_baseline = exposed_runtime_file_snapshot(
            session, control_plane=recorder.control_plane
        )
        recorder.write_private_json(
            "local_runtime_files_snapshot.json",
            {
                "schema_version": "local-runtime-files.v1",
                "keyed_hmac_sha256": local_runtime_files_baseline,
            },
        )

        project_root = session.source_repo or workspace
        toolchain = resolve_toolchain(
            local_config,
            manifest,
            project_name=session.project_name,
            project_root=project_root,
        )
        if runtime_state is not None:
            toolchain["project_python"] = runtime_state.project_python
        benchmark_toolchain_removed: dict[str, str] = {}
        benchmark_toolchain_rebound: dict[str, str] = {}
        if workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK:
            benchmark_toolchain_sanitization = sanitize_benchmark_toolchain(
                toolchain=toolchain,
                source_repo=session.source_repo,
                workspace=workspace,
                runtime_projections=session.runtime_projections,
            )
            toolchain = benchmark_toolchain_sanitization.toolchain
            benchmark_toolchain_removed = benchmark_toolchain_sanitization.removed
            benchmark_toolchain_rebound = dict(
                benchmark_toolchain_sanitization.rebound_to_workspace
            )
            recorder.write_authoritative_json(
                "benchmark_toolchain_sanitization.json",
                {
                    "schema_version": "benchmark-toolchain-sanitization.v2",
                    "removed": benchmark_toolchain_removed,
                    "retained_keys": sorted(
                        set(toolchain)
                        - set(benchmark_toolchain_sanitization.rebound_to_workspace)
                    ),
                    "rebound_to_workspace": dict(
                        benchmark_toolchain_sanitization.rebound_to_workspace
                    ),
                    "source_paths_exposed_to_agents": False,
                    "fresh_dependency_install_performed": False,
                },
            )
        validate_toolchain(toolchain)
        repair_specs, heldout_specs = split_checks(manifest["checks"])
        tool_probe_registry = ToolProbeRegistry(
            workspace=workspace,
            harness_root=HARNESS_ROOT,
            source_repo=session.source_repo,
            toolchain=toolchain,
            execution_broker=execution_broker,
            control_plane=recorder.control_plane,
            runtime_integrity_manager=runtime_integrity_manager,
            historical=workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK,
            rebound_to_workspace=benchmark_toolchain_rebound,
        )
        static_preflight = run_static_toolchain_preflight(
            manifest["checks"],
            workspace=workspace,
            harness_root=HARNESS_ROOT,
            toolchain=toolchain,
            probe_registry=tool_probe_registry,
            candidate_baseline_sha=session.base_sha or preflight["head_sha"],
        )
        static_preflight_artifact = "static_toolchain_preflight.json"
        recorder.write_json(static_preflight_artifact, static_preflight.public_dict())
        recorder.write_private_json(
            "static_toolchain_preflight_private.json",
            static_preflight.private_dict(),
        )
        if not static_preflight.passed:
            static_artifacts = [
                "harness_build_identity.json",
                static_preflight_artifact,
            ]
            if runtime_integrity_manager.active:
                static_artifacts.append("runtime_projection_integrity.json")
            run_state.route_stage(
                StageId.INTAKE_PREFLIGHT,
                outcome=WorkflowOutcome.BLOCKED,
                result_code=StageResultCode.BLOCKED,
                reason_code="STATIC_TOOLCHAIN_PREFLIGHT_FAILED",
                artifacts=tuple(static_artifacts),
            )
            print(
                "HARNESS_TASK_STOPPED: STATIC_TOOLCHAIN_PREFLIGHT_FAILED",
                ", ".join(static_preflight.reason_codes),
            )
            return 2
        print("STATIC_TOOLCHAIN_PREFLIGHT_PASS")
        runtime_scenarios = runtime_scenarios_from_config(
            local_config, project_name=session.project_name
        )
        runtime_executor = RuntimeExecutor(
            workspace=workspace,
            source_repo=session.source_repo,
            toolchain=toolchain,
            execution_broker=execution_broker,
            runtime_integrity_manager=runtime_integrity_manager,
        )
        recorder.write_authoritative_json(
            "runtime_scenarios.json",
            {
                "schema_version": "runtime-scenarios.v1",
                "scenarios": [item.public_summary() for item in runtime_scenarios],
            },
        )
        risk = manifest.get("risk", "medium")
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
            tool_probe_registry.bind_candidate_identity(identity.candidate_id)
            run_state.observe_candidate(identity, reason_code=reason_code)
            recorder.write_authoritative_json(
                "candidate_identity_current.json", identity.to_dict()
            )
            return identity

        print("TASK_STARTED:", datetime.now().astimezone().isoformat())
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
            "4 CHECKS → 5 RUNTIME[CONDITIONAL] → 6 EVALUATOR[SKIP] → 7 FINAL"
            if pipeline_profile == PipelineProfile.FAST
            else "0 PREFLIGHT → 1 PLANNER → 2 CONTRACT → 3 IMPLEMENT → "
            "4 CHECKS → 5 RUNTIME[CONDITIONAL] → 6 EVALUATOR → 7 FINAL",
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
            try:
                benchmark_evidence = run_benchmark_baseline_gate(
                    heldout_specs,
                    workspace=workspace,
                    toolchain=toolchain,
                    runtime_root=recorder.root / "benchmark_baseline_gate_tmp",
                    failure_marker=str(benchmark["baseline_failure_marker"]),
                    execution_broker=execution_broker,
                    runtime_integrity_manager=runtime_integrity_manager,
                )
            except HarnessControlledStop as exc:
                run_state.route_stage(
                    StageId.INTAKE_PREFLIGHT,
                    outcome=WorkflowOutcome.BLOCKED,
                    result_code=StageResultCode.BLOCKED,
                    reason_code=str(exc),
                    artifacts=("runtime_projection_integrity.json",),
                )
                print("HARNESS_TASK_STOPPED:", exc)
                return 2
            recorder.write_json("benchmark_baseline_gate.json", benchmark_evidence)

        run_state.set_baseline(
            source_head=session.source_head,
            workspace_head=preflight["head_sha"],
            source_repo=str(session.source_repo) if session.source_repo else None,
            workspace=str(workspace),
        )
        benchmark_isolation_artifact: str | None = None
        if workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK:
            if not session.benchmark_isolated:
                raise RuntimeError(
                    "Historical benchmark did not receive a sanitized standalone repository"
                )
            refs = str(
                subprocess.run(
                    ["git", "for-each-ref", "--format=%(refname)"],
                    cwd=workspace,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=True,
                ).stdout
            ).splitlines()
            benchmark_isolation_artifact = "benchmark_isolation.json"
            recorder.write_authoritative_json(
                benchmark_isolation_artifact,
                {
                    "schema_version": "benchmark-isolation.v1",
                    "source_base_sha": session.source_base_sha,
                    "sanitized_workspace_sha": session.base_sha,
                    "standalone_git_directory": (workspace / ".git").is_dir(),
                    "shared_git_metadata": False,
                    "visible_refs": refs,
                    "previous_attempt_artifacts_exposed": False,
                    "hidden_grader_exposed_to_agents": False,
                    "result_mode": session.result_mode,
                    "status": "BENCHMARK_ISOLATION_PASS",
                },
            )
        runtime_tmp = execution_broker.scratch_root(ExecutionRole.APP_SERVER)
        codex_cmd = resolve_codex_cmd(local_config)
        app_server_policy = execution_broker.policy_for(ExecutionRole.APP_SERVER)
        app_server_env = execution_broker.environment_for(
            ExecutionRole.APP_SERVER,
            # Some installations authenticate Codex through this variable. The
            # value is inherited by the brokered process but never serialized.
            preserve_sensitive=("OPENAI_API_KEY",),
        )
        with CodexAppServer(
            codex_cmd,
            client_version=__version__,
            runtime_tmp=runtime_tmp,
            process_env=app_server_env,
            execution_policy=app_server_policy.to_dict(),
        ) as codex:
            print("=== USER TASK CONTRACT ===")
            task_contract = run_task_contract_normalizer(
                codex,
                cwd=execution_broker.scratch_root(ExecutionRole.INTAKE),
                raw_request=manifest["prompt"],
                on_heartbeat=make_heartbeat("INTAKE"),
                on_thread_started=_thread_recorder(recorder, "task_contract_1"),
                timeout=min(timeout, 300),
            )
            validate_task_contract(task_contract)
            recorder.write_authoritative_json("task_contract_01.json", task_contract)
            run_state.bump_revision(
                RevisionKind.TASK_CONTRACT, artifact="task_contract_01.json"
            )
            print(json.dumps(task_contract, ensure_ascii=False, indent=2))
            if task_contract["status"] != TaskContractStatus.READY.value:
                run_state.route_stage(
                    StageId.INTAKE_PREFLIGHT,
                    outcome=WorkflowOutcome.NEEDS_USER_DECISION,
                    result_code=StageResultCode.NEEDS_USER_DECISION,
                    reason_code="TASK_CONTRACT_NEEDS_USER_DECISION",
                    artifacts=("task_contract_01.json",),
                )
                print("HARNESS_TASK_STOPPED: NEEDS_USER_DECISION")
                return 2
            intake_artifacts = [
                "harness_build_identity.json",
                "manifest_snapshot.json",
                "workflow_snapshot.json",
                "preflight.json",
                "repo_context.json",
                "execution_policies.json",
                "static_toolchain_preflight.json",
                "task_contract_01.json",
            ]
            if benchmark_isolation_artifact:
                intake_artifacts.extend(
                    [benchmark_isolation_artifact, "benchmark_toolchain_sanitization.json"]
                )
            run_state.pass_stage(
                StageId.INTAKE_PREFLIGHT,
                StageResultCode.PREFLIGHT_READY,
                artifacts=tuple(intake_artifacts),
            )

            plan: dict | None = None
            run_state.begin_stage(StageId.PLANNER)
            if pipeline_profile == PipelineProfile.FULL:
                print("=== PLAN ===")
                plan = run_planner(
                    codex,
                    workspace=workspace,
                    task_prompt=manifest["prompt"],
                    task_contract=task_contract,
                    preflight=preflight,
                    owner_allowed_paths=allowed_paths,
                    replan_context=planner_benchmark_context(benchmark_evidence),
                    on_heartbeat=make_heartbeat("PLAN"),
                    on_thread_started=_thread_recorder(recorder, "planner_1"),
                    timeout=timeout,
                )
                validate_plan_artifact(plan, workspace=workspace, task_contract=task_contract)
                recorder.write_authoritative_json("plan_01.json", plan)
                run_state.bump_revision(RevisionKind.PLAN, artifact="plan_01.json")
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                if plan["status"] != PlannerStatus.READY.value:
                    if plan["status"] == PlannerStatus.BLOCKED.value:
                        outcome = WorkflowOutcome.BLOCKED
                        result_code = StageResultCode.BLOCKED
                        reason_code = "PLANNER_BLOCKED"
                    elif plan["status"] == PlannerStatus.TASK_CONTRACT_INVALID.value:
                        outcome = WorkflowOutcome.INVALID
                        result_code = StageResultCode.INVALID
                        reason_code = "TASK_CONTRACT_INVALID"
                    else:
                        outcome = WorkflowOutcome.NEEDS_USER_DECISION
                        result_code = StageResultCode.NEEDS_USER_DECISION
                        reason_code = "PLANNER_NEEDS_USER_DECISION"
                    run_state.route_stage(
                        StageId.PLANNER,
                        outcome=outcome,
                        result_code=result_code,
                        reason_code=reason_code,
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
                plan, task_contract=task_contract
            )
            validate_implementation_contract(implementation_contract)
            recorder.write_authoritative_json(
                "implementation_contract_01.json", implementation_contract
            )
            run_state.bump_revision(
                RevisionKind.IMPLEMENTATION_CONTRACT,
                artifact="implementation_contract_01.json",
            )
            verification_plan = compile_verification_plan(
                implementation_contract,
                project_checks=repair_specs,
            )
            validate_verification_plan(verification_plan)
            recorder.write_authoritative_json(
                "verification_plan_01.json", verification_plan
            )
            run_state.bump_revision(
                RevisionKind.VERIFICATION_PLAN,
                artifact="verification_plan_01.json",
            )
            declared_capabilities = configured_capabilities(
                local_config, project_name=session.project_name
            )
            runtime_capabilities = runtime_available_capabilities(runtime_scenarios)
            post_plan_tool_probes = tool_probe_registry.ensure_capabilities(
                verification_plan["required_capabilities"],
                batch_id="post-plan-capability-gate-01",
            )
            resolved_capabilities = available_capabilities(
                toolchain=toolchain,
                configured=declared_capabilities,
                runtime=runtime_capabilities,
                verified_tool_capabilities=tool_probe_registry.verified_capabilities,
            )
            capability_record = {
                "schema_version": "capability-gate.v1",
                "declared": declared_capabilities,
                "available": sorted(resolved_capabilities),
                "required": list(verification_plan["required_capabilities"]),
                "tool_probe_evidence": post_plan_tool_probes.public_dict(),
            }
            capability_record["missing"] = required_capability_gaps(
                verification_plan, available=resolved_capabilities
            )
            capability_record["runtime_requirement_gaps"] = runtime_requirement_gaps(
                verification_plan, runtime_scenarios
            )
            capability_record["runtime_environment_gaps"] = runtime_environment_gaps(
                runtime_scenarios,
                verification_plan=verification_plan,
                execution_broker=execution_broker,
            )
            capability_record["runtime_command_gaps"] = runtime_command_gaps(
                verification_plan,
                runtime_scenarios,
                workspace=workspace,
                toolchain=toolchain,
            )
            recorder.write_authoritative_json(
                "capability_gate_01.json", capability_record
            )
            if plan is not None and not plan["owner_boundary_assessment"]["compatible"]:
                run_state.route_stage(
                    StageId.IMPLEMENTATION_CONTRACT,
                    outcome=WorkflowOutcome.BLOCKED,
                    result_code=StageResultCode.BLOCKED,
                    reason_code="OWNER_BOUNDARY_CONFLICT",
                    artifacts=(
                        "implementation_contract_01.json",
                        "verification_plan_01.json",
                        "capability_gate_01.json",
                    ),
                )
                print("HARNESS_TASK_STOPPED: OWNER_BOUNDARY_CONFLICT")
                return 2
            if (
                capability_record["missing"]
                or capability_record["runtime_requirement_gaps"]
                or capability_record["runtime_environment_gaps"]
                or capability_record["runtime_command_gaps"]
            ):
                run_state.route_stage(
                    StageId.IMPLEMENTATION_CONTRACT,
                    outcome=WorkflowOutcome.BLOCKED,
                    result_code=StageResultCode.BLOCKED,
                    reason_code="REQUIRED_CAPABILITY_MISSING",
                    artifacts=(
                        "implementation_contract_01.json",
                        "verification_plan_01.json",
                        "capability_gate_01.json",
                    ),
                )
                print(
                    "HARNESS_TASK_STOPPED: REQUIRED_CAPABILITY_MISSING",
                    ", ".join([
                        *capability_record["missing"],
                        *capability_record["runtime_requirement_gaps"],
                        *capability_record["runtime_environment_gaps"],
                        *capability_record["runtime_command_gaps"],
                    ]),
                )
                return 2
            run_state.pass_stage(
                StageId.IMPLEMENTATION_CONTRACT,
                StageResultCode.IMPLEMENTATION_CONTRACT_READY,
                artifacts=(
                    "implementation_contract_01.json",
                    "verification_plan_01.json",
                    "capability_gate_01.json",
                ),
            )
            print("=== IMPLEMENTATION CONTRACT ===")
            print(json.dumps(implementation_contract, ensure_ascii=False, indent=2))
            print("=== VERIFICATION PLAN ===")
            print(json.dumps(verification_plan, ensure_ascii=False, indent=2))

            dynamic_specs: list[dict] = []
            dynamic_notes: list[str] = []
            implementation_report_index = 0
            implementation_contract_index = 1
            verification_plan_index = 1
            capability_gate_index = 1
            project_runtime_index = 1 if runtime_state is not None else 0
            contract_closure_index = 0
            runtime_verification_index = 0
            active_contract_closure: dict | None = None
            active_contract_closure_artifact: str | None = None
            active_runtime_evidence: dict = {
                "protocol_version": "runtime-evidence.v1",
                "status": StageResultCode.RUNTIME_VERIFICATION_SKIPPED.value,
                "candidate_id": None,
                "verification_plan_fingerprint": verification_plan["fingerprint"],
                "scenarios": [],
                "reason_code": "NOT_EVALUATED_YET",
            }
            active_runtime_artifact: str | None = None
            active_contract_artifact = "implementation_contract_01.json"
            active_verification_artifact = "verification_plan_01.json"
            active_capability_artifact = "capability_gate_01.json"
            # Authoritative typed registry lives in the Controller-private plane,
            # never in the agent-writable workspace.
            check_registry = CheckRegistry(
                recorder.private_root / "check_registry.json",
                workspace=workspace,
                trusted_check_ids=TRUSTED_CHECK_IDS,
            )

            def active_repair_specs() -> list[dict]:
                return list(repair_specs) + list(dynamic_specs)

            def active_task_check_keys() -> list[str]:
                return [
                    f"{item.kind}:{item.value}"
                    for item in check_registry.references()
                ]

            def register_report_checks(report: dict) -> bool:
                nonlocal dynamic_specs
                digest_before = check_registry.digest()
                requested_paths = [str(value) for value in report.get("additional_check_paths", [])]
                requested_ids: list[str] = []
                for item in report.get("registered_checks", []):
                    if not isinstance(item, dict):
                        continue
                    if item.get("kind") == "path":
                        requested_paths.append(str(item.get("value", "")))
                    elif item.get("kind") == "check_id":
                        requested_ids.append(str(item.get("value", "")))

                # Resolve every request before making it authoritative.  A
                # registry entry that has no executable trusted spec would be
                # false verification evidence.
                path_specs, path_notes = build_dynamic_check_specs(
                    requested_paths,
                    workspace=workspace,
                    toolchain=toolchain,
                    base_specs=repair_specs,
                )
                unsupported = [
                    note for note in path_notes if note.startswith("UNSUPPORTED_DYNAMIC_CHECK")
                ]
                if unsupported:
                    raise Phase5ContractError("; ".join(unsupported))
                id_specs, id_notes = build_trusted_check_id_specs(
                    requested_ids,
                    base_specs=repair_specs,
                )

                for value in requested_paths:
                    check_registry.register_path(value)
                for value in requested_ids:
                    check_registry.register_id(value)

                before = len(dynamic_specs)
                dynamic_specs = merge_dynamic_specs(
                    dynamic_specs,
                    [*path_specs, *id_specs],
                )
                for note in [*path_notes, *id_notes]:
                    if note not in dynamic_notes:
                        dynamic_notes.append(note)
                        print(note)
                if len(dynamic_specs) > before:
                    print(
                        "DYNAMIC_CHECKS_ADDED:",
                        ", ".join(spec["name"] for spec in dynamic_specs[before:]),
                    )
                return check_registry.digest() != digest_before

            run_state.begin_stage(StageId.IMPLEMENTER)
            implementer_thread = codex.start_thread(
                cwd=workspace,
                sandbox="workspace-write",
                developer_instructions=IMPLEMENTER_INSTRUCTIONS,
                on_started=_thread_recorder(recorder, "implementer"),
            )


            def recompile_active_definition(
                *,
                discoveries: list[dict],
                registry_changed: bool,
                detail: str,
            ) -> bool:
                """Atomically expand Contract + Verification Plan and re-run Step 2 gates."""

                nonlocal implementation_contract
                nonlocal verification_plan
                nonlocal implementation_contract_index
                nonlocal verification_plan_index
                nonlocal capability_gate_index
                nonlocal active_contract_artifact
                nonlocal active_verification_artifact
                nonlocal active_capability_artifact

                expansion = expand_contract_and_verification_plan(
                    implementation_contract=implementation_contract,
                    previous_verification_plan=verification_plan,
                    discoveries=discoveries,
                    project_checks=repair_specs,
                    task_checks=active_task_check_keys(),
                )
                contract_changed = (
                    expansion.implementation_contract["fingerprint"]
                    != implementation_contract["fingerprint"]
                )
                plan_changed = (
                    expansion.verification_plan["fingerprint"]
                    != verification_plan["fingerprint"]
                )
                if not contract_changed and not plan_changed:
                    return False

                trigger = (
                    InvalidationTrigger.CONTRACT_EXPANDED
                    if contract_changed
                    else InvalidationTrigger.CHECK_REGISTERED
                )
                run_state.invalidate(trigger, detail=detail)
                run_state.begin_stage(StageId.IMPLEMENTATION_CONTRACT)

                if contract_changed:
                    implementation_contract = expansion.implementation_contract
                    implementation_contract_index += 1
                    contract_artifact = (
                        f"implementation_contract_{implementation_contract_index:02d}.json"
                    )
                    recorder.write_authoritative_json(
                        contract_artifact, implementation_contract
                    )
                    run_state.bump_revision(
                        RevisionKind.IMPLEMENTATION_CONTRACT,
                        artifact=contract_artifact,
                    )
                    active_contract_artifact = contract_artifact
                else:
                    contract_artifact = active_contract_artifact

                verification_plan = expansion.verification_plan
                verification_plan_index += 1
                plan_artifact = f"verification_plan_{verification_plan_index:02d}.json"
                recorder.write_authoritative_json(plan_artifact, verification_plan)
                run_state.bump_revision(
                    RevisionKind.VERIFICATION_PLAN,
                    artifact=plan_artifact,
                )
                active_verification_artifact = plan_artifact

                # Re-run owner and capability gates against the newly active
                # Definition of Done.  A new proof requirement is not allowed to
                # bypass the pre-Implementer capability boundary.
                enforce_allowed_paths(collect_changed_paths(workspace), allowed_paths)
                if plan is not None and not plan["owner_boundary_assessment"]["compatible"]:
                    run_state.route_stage(
                        StageId.IMPLEMENTATION_CONTRACT,
                        outcome=WorkflowOutcome.BLOCKED,
                        result_code=StageResultCode.BLOCKED,
                        reason_code="OWNER_BOUNDARY_CONFLICT",
                        artifacts=(contract_artifact, plan_artifact),
                    )
                    raise HarnessControlledStop("OWNER_BOUNDARY_CONFLICT")

                dynamic_tool_probes = tool_probe_registry.ensure_capabilities(
                    verification_plan["required_capabilities"],
                    batch_id=f"post-plan-capability-gate-{capability_gate_index + 1:02d}",
                )
                current_capabilities = available_capabilities(
                    toolchain=toolchain,
                    configured=declared_capabilities,
                    runtime=runtime_available_capabilities(runtime_scenarios),
                    verified_tool_capabilities=tool_probe_registry.verified_capabilities,
                )
                capability_gate_index += 1
                capability_artifact = f"capability_gate_{capability_gate_index:02d}.json"
                capability_record = {
                    "schema_version": "capability-gate.v1",
                    "declared": declared_capabilities,
                    "available": sorted(current_capabilities),
                    "required": list(verification_plan["required_capabilities"]),
                    "tool_probe_evidence": dynamic_tool_probes.public_dict(),
                }
                capability_record["missing"] = required_capability_gaps(
                    verification_plan,
                    available=current_capabilities,
                )
                capability_record["runtime_requirement_gaps"] = runtime_requirement_gaps(
                    verification_plan, runtime_scenarios
                )
                capability_record["runtime_environment_gaps"] = runtime_environment_gaps(
                    runtime_scenarios,
                    verification_plan=verification_plan,
                    execution_broker=execution_broker,
                )
                capability_record["runtime_command_gaps"] = runtime_command_gaps(
                    verification_plan,
                    runtime_scenarios,
                    workspace=workspace,
                    toolchain=toolchain,
                )
                recorder.write_authoritative_json(
                    capability_artifact, capability_record
                )
                active_capability_artifact = capability_artifact
                if (
                    capability_record["missing"]
                    or capability_record["runtime_requirement_gaps"]
                    or capability_record["runtime_environment_gaps"]
                    or capability_record["runtime_command_gaps"]
                ):
                    run_state.route_stage(
                        StageId.IMPLEMENTATION_CONTRACT,
                        outcome=WorkflowOutcome.BLOCKED,
                        result_code=StageResultCode.BLOCKED,
                        reason_code="REQUIRED_CAPABILITY_MISSING",
                        artifacts=(
                            contract_artifact,
                            plan_artifact,
                            capability_artifact,
                        ),
                    )
                    raise HarnessControlledStop(
                        "REQUIRED_CAPABILITY_MISSING: "
                        + ", ".join([
                            *capability_record["missing"],
                            *capability_record["runtime_requirement_gaps"],
                            *capability_record["runtime_environment_gaps"],
                            *capability_record["runtime_command_gaps"],
                        ])
                    )

                expansion_artifact = (
                    f"contract_expansion_{verification_plan_index:02d}.json"
                )
                recorder.write_authoritative_json(
                    expansion_artifact,
                    {
                        **expansion.summary(),
                        "detail": detail,
                        "check_registry_digest": check_registry.digest(),
                        "task_checks": active_task_check_keys(),
                    },
                )
                run_state.pass_stage(
                    StageId.IMPLEMENTATION_CONTRACT,
                    StageResultCode.IMPLEMENTATION_CONTRACT_READY,
                    artifacts=(
                        contract_artifact,
                        plan_artifact,
                        capability_artifact,
                        expansion_artifact,
                    ),
                )
                print(
                    "ACTIVE_DEFINITION_EXPANDED:",
                    f"contract_items={len(implementation_contract['items'])}",
                    f"task_checks={len(active_task_check_keys())}",
                )
                return True

            def continue_implementer(
                *,
                reason: str,
                label: str,
            ) -> tuple[dict, str]:
                nonlocal implementation_report_index
                run_state.begin_stage(StageId.IMPLEMENTER)
                _, next_stamp, next_command = prepare_self_verify_runner(
                    workspace=workspace,
                    specs=active_repair_specs(),
                    toolchain=toolchain,
                )
                implementation_report_index += 1
                next_report = run_implementer_report(
                    codex,
                    thread_id=implementer_thread,
                    prompt=build_implementation_continuation_prompt(
                        implementation_contract=implementation_contract,
                        verification_plan=verification_plan,
                        self_verify_command=next_command,
                        reason=reason,
                    ),
                    timeout=timeout,
                    label=label,
                    implementation_contract=implementation_contract,
                    self_verify_command=next_command,
                    workspace=workspace,
                    stamp_path=next_stamp,
                    plan=plan,
                    control_plane=recorder.control_plane,
                    run_state=run_state,
                    check_registry_digest=check_registry.digest(),
                    runtime_integrity_manager=runtime_integrity_manager,
                    execution_broker=execution_broker,
                )
                next_artifact = (
                    f"implementation_report_{implementation_report_index:02d}.json"
                )
                recorder.write_json(next_artifact, next_report)
                observe_candidate(label.replace(" ", "_"))
                return next_report, next_artifact

            def reconcile_project_runtime() -> tuple[bool, str]:
                nonlocal runtime_state
                nonlocal project_runtime_index
                if runtime_manager is None or runtime_state is None:
                    return False, ""
                reconciliation = runtime_manager.reconcile(runtime_state)
                runtime_state = reconciliation.state
                toolchain["project_python"] = runtime_state.project_python
                tool_probe_registry.toolchain["project_python"] = (
                    runtime_state.project_python
                )
                if not reconciliation.changed:
                    return False, ""

                tool_probe_registry.invalidate_runtime_environment_evidence()

                trigger = (
                    InvalidationTrigger.DEPENDENCY_MANIFEST_CHANGED
                    if "DEPENDENCY_MANIFEST_CHANGED" in reconciliation.reasons
                    else InvalidationTrigger.RUNTIME_ENV_CHANGED
                )
                run_state.invalidate(
                    trigger,
                    detail=", ".join(reconciliation.reasons),
                )
                project_runtime_index += 1
                runtime_artifact = f"project_runtime_{project_runtime_index:02d}.json"
                recorder.write_authoritative_json(
                    runtime_artifact, runtime_state.to_dict()
                )
                run_state.bump_revision(
                    RevisionKind.RUNTIME_ENVIRONMENT,
                    artifact=runtime_artifact,
                )
                return True, (
                    "Project runtime was rebuilt from the authoritative dependency "
                    "declarations because: "
                    + ", ".join(reconciliation.reasons)
                    + ". Previous self-verification is stale."
                )

            def stabilize_implementer_report(report: dict, *, label: str) -> tuple[dict, str]:
                """Close discoveries, checks and runtime drift before accepting COMPLETE."""

                nonlocal implementation_report_index
                artifact = f"implementation_report_{implementation_report_index:02d}.json"
                while report.get("status") == ImplementerStatus.COMPLETE.value:
                    registry_changed = register_report_checks(report)
                    discoveries = list(report.get("discovered_obligations", []))
                    definition_changed = recompile_active_definition(
                        discoveries=discoveries,
                        registry_changed=registry_changed,
                        detail=(
                            "Implementer discovered material obligations and/or "
                            "registered typed Controller checks"
                        ),
                    )
                    if definition_changed:
                        report, artifact = continue_implementer(
                            reason=(
                                "Controller expanded the active Implementation Contract "
                                "and Verification Plan. Close every newly active item and "
                                "run the current self-verification before COMPLETE."
                            ),
                            label=f"{label} CONTRACT EXPANSION",
                        )
                        continue

                    runtime_changed, runtime_reason = reconcile_project_runtime()
                    if runtime_changed:
                        report, artifact = continue_implementer(
                            reason=runtime_reason,
                            label=f"{label} RUNTIME REBUILD",
                        )
                        continue

                    current_local_snapshot = exposed_runtime_file_snapshot(
                        session, control_plane=recorder.control_plane
                    )
                    if current_local_snapshot != local_runtime_files_baseline:
                        restore_exposed_runtime_files(session)
                        restored = exposed_runtime_file_snapshot(
                            session, control_plane=recorder.control_plane
                        )
                        if restored != local_runtime_files_baseline:
                            raise Phase5ContractError(
                                "LOCAL_RUNTIME_FILE_RESTORE_FAILED"
                            )
                        run_state.invalidate(
                            InvalidationTrigger.RUNTIME_ENV_CHANGED,
                            detail=(
                                "Runtime-only files from .worktreeinclude/copy_untracked "
                                "were changed and restored by Controller"
                            ),
                        )
                        report, artifact = continue_implementer(
                            reason=(
                                "Controller restored runtime-only local files to their "
                                "pre-implementation state. They are not part of the "
                                "candidate, so self-verification must run again."
                            ),
                            label=f"{label} LOCAL RUNTIME RESTORE",
                        )
                        continue

                    return report, artifact
                return report, artifact
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
                    task_contract=task_contract,
                    implementation_contract=implementation_contract,
                    verification_plan=verification_plan,
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
                control_plane=recorder.control_plane,
                run_state=run_state,
                check_registry_digest=check_registry.digest(),
                runtime_integrity_manager=runtime_integrity_manager,
                execution_broker=execution_broker,
            )
            report_artifact = f"implementation_report_{implementation_report_index:02d}.json"
            recorder.write_json(report_artifact, report)
            observe_candidate("IMPLEMENTER_REPORT")
            report, report_artifact = stabilize_implementer_report(report, label="IMPLEMENT")
            if report["status"] != ImplementerStatus.COMPLETE.value:
                _phase4_route_implementer_terminal(
                    run_state=run_state,
                    report=report,
                    artifacts=(report_artifact, "candidate_identity_current.json"),
                )
                print("HARNESS_TASK_STOPPED:", report["status"])
                return 2
            run_state.pass_stage(
                StageId.IMPLEMENTER,
                StageResultCode.IMPLEMENTATION_COMPLETE,
                artifacts=(report_artifact, "candidate_identity_current.json"),
            )

            initial_changed_paths = collect_changed_paths(workspace)
            require_candidate_change_for_confirmed_benchmark(
                benchmark_evidence,
                initial_changed_paths,
            )

            fix_cycles = 0
            replan_cycles = 0
            repair_progress_history: list[tuple[str, str]] = []
            replan_progress_history: list[tuple[str, str]] = []
            evaluation_index = 0
            check_index = 0
            first_evaluation_pass: bool | None = None
            while True:
                changed_paths = collect_changed_paths(workspace)
                enforce_allowed_paths(changed_paths, allowed_paths)
                closure_candidate = observe_candidate("CONTRACT_CLOSURE")
                contract_closure_index += 1
                active_contract_closure = build_contract_closure_record(
                    implementation_contract=implementation_contract,
                    verification_plan=verification_plan,
                    implementation_report=report,
                    candidate_id=closure_candidate.candidate_id,
                )
                validate_contract_closure_record(
                    active_contract_closure,
                    implementation_contract=implementation_contract,
                    verification_plan=verification_plan,
                    candidate_id=closure_candidate.candidate_id,
                )
                active_contract_closure_artifact = (
                    f"contract_closure_{contract_closure_index:02d}.json"
                )
                recorder.write_authoritative_json(
                    active_contract_closure_artifact, active_contract_closure
                )
                current_specs = active_repair_specs()
                ensure_changed_tests_are_covered(
                    changed_paths=git_changed_paths(workspace),
                    registered_references=check_registry.references(),
                    project_check_text=json.dumps(current_specs, ensure_ascii=False),
                )
                check_index += 1
                run_state.begin_stage(StageId.DETERMINISTIC_CHECKS)
                checks_candidate_before = observe_candidate("CONTROLLER_CHECKS_BEFORE")
                repair_results = run_checks(
                    current_specs,
                    workspace=workspace,
                    toolchain=toolchain,
                    runtime_root=recorder.root / f"checks_{check_index:02d}",
                    label=f"CHECKS #{check_index}",
                    execution_broker=execution_broker,
                    execution_role=ExecutionRole.CONTROLLER_CHECK,
                    runtime_integrity_manager=runtime_integrity_manager,
                    batch_id=f"DETERMINISTIC_CHECKS:{check_index:02d}",
                )
                checks_artifact = f"checks_{check_index:02d}.json"
                recorder.write_authoritative_json(checks_artifact, check_records(repair_results))
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

                infrastructure_failures = [
                    result
                    for result in repair_results
                    if result.classification is CheckClassification.INFRA_ERROR
                ]
                if infrastructure_failures:
                    integrity_reason = next(
                        (
                            result.runtime_integrity_reason_code
                            for result in infrastructure_failures
                            if result.runtime_integrity_reason_code
                        ),
                        None,
                    )
                    run_state.route_stage(
                        StageId.DETERMINISTIC_CHECKS,
                        outcome=WorkflowOutcome.BLOCKED,
                        result_code=StageResultCode.BLOCKED,
                        reason_code=integrity_reason or "CHECK_INFRA_ERROR",
                        artifacts=(checks_artifact, "candidate_identity_current.json"),
                    )
                    print("HARNESS_TASK_STOPPED:", integrity_reason or "CHECK_INFRA_ERROR")
                    return 2

                if any(not result.passed for result in repair_results):
                    run_state.route_stage(
                        StageId.DETERMINISTIC_CHECKS,
                        outcome=WorkflowOutcome.REPAIR,
                        result_code=StageResultCode.CHECK_REPAIR_REQUIRED,
                        reason_code="DETERMINISTIC_CHECK_FAILED",
                        artifacts=(checks_artifact, "candidate_identity_current.json"),
                    )
                    failed_signature = json.dumps(
                        [
                            {
                                "name": result.name,
                                "returncode": result.returncode,
                                "timed_out": result.timed_out,
                                "output": result.output[-4000:],
                            }
                            for result in repair_results
                            if not result.passed
                        ],
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if _phase4_loop_stalled(
                        repair_progress_history,
                        candidate_id=checks_candidate_after.candidate_id,
                        failure_signature=failed_signature,
                    ):
                        raise RuntimeError(
                            "REPAIR_STALLED: deterministic checks repeated with the "
                            "same candidate and failure set"
                        )
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
                        control_plane=recorder.control_plane,
                        run_state=run_state,
                        check_registry_digest=check_registry.digest(),
                        runtime_integrity_manager=runtime_integrity_manager,
                        execution_broker=execution_broker,
                    )
                    report_artifact = f"implementation_report_{implementation_report_index:02d}.json"
                    recorder.write_json(report_artifact, report)
                    observe_candidate("IMPLEMENTER_REPAIR_CHECKS")
                    report, report_artifact = stabilize_implementer_report(
                        report, label=f"REPAIR CHECKS #{fix_cycles}"
                    )
                    if report["status"] != ImplementerStatus.COMPLETE.value:
                        _phase4_route_implementer_terminal(
                            run_state=run_state,
                            report=report,
                            artifacts=(report_artifact, "candidate_identity_current.json"),
                            reason_suffix="AFTER_CHECK_REPAIR",
                        )
                        print("HARNESS_TASK_STOPPED:", report["status"])
                        return 2
                    run_state.pass_stage(
                        StageId.IMPLEMENTER,
                        StageResultCode.IMPLEMENTATION_COMPLETE,
                        artifacts=(report_artifact, "candidate_identity_current.json"),
                    )
                    continue

                run_state.pass_stage(
                    StageId.DETERMINISTIC_CHECKS,
                    StageResultCode.DETERMINISTIC_VERIFICATION_PASS,
                    artifacts=(checks_artifact, "candidate_identity_current.json"),
                )
                run_state.begin_stage(StageId.RUNTIME_VERIFICATION)
                runtime_verification_index += 1
                runtime_record = runtime_executor.execute(
                    verification_plan, runtime_scenarios
                )
                runtime_files_after = exposed_runtime_file_snapshot(
                    session, control_plane=recorder.control_plane
                )
                if runtime_files_after != local_runtime_files_baseline:
                    restore_exposed_runtime_files(session)
                    runtime_record = replace(
                        runtime_record,
                        status=RuntimeStatus.MUTATED_CANDIDATE.value,
                        reason_code="RUNTIME_MUTATED_LOCAL_RUNTIME_FILES",
                    )
                active_runtime_evidence = runtime_record.public_record()
                active_runtime_artifact = (
                    f"runtime_evidence_{runtime_verification_index:02d}.json"
                )
                recorder.write_private_json(
                    active_runtime_artifact, runtime_record.private_record()
                )
                recorder.write_json(
                    active_runtime_artifact, active_runtime_evidence
                )
                if runtime_record.status == RuntimeStatus.SKIPPED.value:
                    run_state.skip_stage(
                        StageId.RUNTIME_VERIFICATION,
                        StageResultCode.RUNTIME_VERIFICATION_SKIPPED,
                        reason_code=runtime_record.reason_code or "NO_RUNTIME_PROOF_REQUIRED",
                        artifacts=(
                            active_verification_artifact,
                            active_runtime_artifact,
                            "candidate_identity_current.json",
                        ),
                    )
                elif runtime_record.status == RuntimeStatus.PASS.value:
                    run_state.pass_stage(
                        StageId.RUNTIME_VERIFICATION,
                        StageResultCode.RUNTIME_VERIFICATION_PASS,
                        artifacts=(
                            active_verification_artifact,
                            active_runtime_artifact,
                            "candidate_identity_current.json",
                        ),
                    )
                    print("RUNTIME_VERIFICATION_PASS")
                elif runtime_record.status in {
                    RuntimeStatus.BEHAVIOR_FAIL.value,
                    RuntimeStatus.START_FAIL.value,
                    RuntimeStatus.READBACK_FAIL.value,
                    RuntimeStatus.MUTATED_CANDIDATE.value,
                }:
                    run_state.route_stage(
                        StageId.RUNTIME_VERIFICATION,
                        outcome=WorkflowOutcome.REPAIR,
                        result_code=StageResultCode.RUNTIME_REPAIR_REQUIRED,
                        reason_code=runtime_record.reason_code or runtime_record.status,
                        artifacts=(
                            active_runtime_artifact,
                            "candidate_identity_current.json",
                        ),
                    )
                    runtime_signature = json.dumps(
                        active_runtime_evidence, ensure_ascii=False, sort_keys=True
                    )
                    runtime_candidate = observe_candidate("RUNTIME_REPAIR_REQUIRED")
                    if _phase4_loop_stalled(
                        repair_progress_history,
                        candidate_id=runtime_candidate.candidate_id,
                        failure_signature=runtime_signature,
                    ):
                        raise RuntimeError(
                            "REPAIR_STALLED: Runtime Verification repeated the same "
                            "failure on the same candidate"
                        )
                    fix_cycles += 1
                    _, stamp_path, self_verify_command = prepare_self_verify_runner(
                        workspace=workspace,
                        specs=active_repair_specs(),
                        toolchain=toolchain,
                    )
                    implementation_report_index += 1
                    run_state.begin_stage(StageId.IMPLEMENTER)
                    report = run_implementer_report(
                        codex,
                        thread_id=implementer_thread,
                        prompt=build_runtime_repair_prompt(
                            active_runtime_evidence,
                            implementation_contract=implementation_contract,
                            self_verify_command=self_verify_command,
                        ),
                        timeout=timeout,
                        label=f"REPAIR RUNTIME #{fix_cycles}",
                        implementation_contract=implementation_contract,
                        self_verify_command=self_verify_command,
                        workspace=workspace,
                        stamp_path=stamp_path,
                        plan=plan,
                        control_plane=recorder.control_plane,
                        run_state=run_state,
                        check_registry_digest=check_registry.digest(),
                        runtime_integrity_manager=runtime_integrity_manager,
                        execution_broker=execution_broker,
                    )
                    report_artifact = (
                        f"implementation_report_{implementation_report_index:02d}.json"
                    )
                    recorder.write_json(report_artifact, report)
                    observe_candidate("IMPLEMENTER_REPAIR_RUNTIME")
                    report, report_artifact = stabilize_implementer_report(
                        report, label=f"REPAIR RUNTIME #{fix_cycles}"
                    )
                    if report["status"] != ImplementerStatus.COMPLETE.value:
                        _phase4_route_implementer_terminal(
                            run_state=run_state,
                            report=report,
                            artifacts=(
                                report_artifact,
                                active_runtime_artifact,
                                "candidate_identity_current.json",
                            ),
                            reason_suffix="AFTER_RUNTIME_REPAIR",
                        )
                        print("HARNESS_TASK_STOPPED:", report["status"])
                        return 2
                    run_state.pass_stage(
                        StageId.IMPLEMENTER,
                        StageResultCode.IMPLEMENTATION_COMPLETE,
                        artifacts=(
                            report_artifact,
                            "candidate_identity_current.json",
                        ),
                    )
                    continue
                else:
                    run_state.route_stage(
                        StageId.RUNTIME_VERIFICATION,
                        outcome=WorkflowOutcome.BLOCKED,
                        result_code=StageResultCode.BLOCKED,
                        reason_code=runtime_record.reason_code or runtime_record.status,
                        artifacts=(
                            active_runtime_artifact,
                            "candidate_identity_current.json",
                        ),
                    )
                    print(
                        "HARNESS_TASK_STOPPED:",
                        runtime_record.reason_code or runtime_record.status,
                    )
                    return 2

                if pipeline_profile == PipelineProfile.FAST:
                    run_state.begin_stage(StageId.EVALUATOR)
                    run_state.skip_stage(
                        StageId.EVALUATOR,
                        StageResultCode.EVALUATION_SKIPPED_FAST,
                        reason_code="FAST_PROFILE_COMPATIBILITY",
                    )
                    break

                evaluation_index += 1
                if active_contract_closure is None or active_contract_closure_artifact is None:
                    raise RuntimeError("Evaluator requires an active Contract Closure Record")
                deterministic_evidence = {
                    "protocol_version": "deterministic-evidence.v1",
                    "candidate_id": closure_candidate.candidate_id,
                    "checks_artifact": checks_artifact,
                    "checks": check_records(repair_results),
                    "dynamic_notes": list(dynamic_notes),
                }
                run_state.begin_stage(StageId.EVALUATOR)
                evaluation_candidate_before = observe_candidate("EVALUATOR_BEFORE")
                blind_artifact = f"blind_audit_{evaluation_index:02d}.json"
                persisted_blind_audit: dict | None = None

                def evaluator_blind_audit_recorder(audit: dict) -> None:
                    nonlocal persisted_blind_audit
                    current = observe_candidate("EVALUATOR_PHASE_A_AFTER")
                    if current.candidate_id != evaluation_candidate_before.candidate_id:
                        raise RuntimeError(
                            "Evaluator changed the candidate during PHASE_A"
                        )
                    persisted_blind_audit = dict(audit)
                    recorder.write_authoritative_json(blind_artifact, audit)

                def evaluator_phase_guard(phase: str) -> None:
                    current = observe_candidate(f"EVALUATOR_{phase}_AFTER")
                    if current.candidate_id != evaluation_candidate_before.candidate_id:
                        raise RuntimeError(
                            f"Evaluator changed the candidate during {phase}"
                        )

                blind_audit, evaluation = run_evaluator(
                    codex,
                    workspace=workspace,
                    task_prompt=manifest["prompt"],
                    task_contract=task_contract,
                    preflight=preflight,
                    owner_allowed_paths=allowed_paths,
                    changed_paths=changed_paths,
                    candidate_id=evaluation_candidate_before.candidate_id,
                    implementation_contract=implementation_contract,
                    verification_plan=verification_plan,
                    contract_closure=active_contract_closure,
                    checks_evidence=deterministic_evidence,
                    runtime_evidence=active_runtime_evidence,
                    runtime_probe_guidance=[],
                    on_heartbeat=make_heartbeat(f"EVALUATE #{evaluation_index}"),
                    on_thread_started=_thread_recorder(recorder, f"evaluator_{evaluation_index}"),
                    on_blind_audit=evaluator_blind_audit_recorder,
                    on_phase_complete=evaluator_phase_guard,
                    timeout=timeout,
                )
                validate_evaluation_artifact(
                    evaluation, blind_audit=blind_audit
                )
                if persisted_blind_audit != blind_audit:
                    raise RuntimeError(
                        "Evaluator Phase A artifact was not persisted before Phase B"
                    )
                evaluation_artifact = f"evaluation_{evaluation_index:02d}.json"
                recorder.write_authoritative_json(evaluation_artifact, evaluation)
                evaluation_candidate_after = observe_candidate("EVALUATOR_AFTER")
                if evaluation_candidate_after.candidate_id != evaluation_candidate_before.candidate_id:
                    run_state.route_stage(
                        StageId.EVALUATOR,
                        outcome=WorkflowOutcome.INVALID,
                        result_code=StageResultCode.INVALID,
                        reason_code="EVALUATOR_MUTATED_CANDIDATE",
                        artifacts=(
                            blind_artifact,
                            evaluation_artifact,
                            "candidate_identity_current.json",
                        ),
                    )
                    raise RuntimeError("Evaluator changed the candidate")
                print("=== BLIND AUDIT ===")
                print(json.dumps(blind_audit, ensure_ascii=False, indent=2))
                print("=== EVALUATION ===")
                print(json.dumps(evaluation, ensure_ascii=False, indent=2))
                if first_evaluation_pass is None:
                    first_evaluation_pass = evaluation["status"] == EvaluatorStatus.PASS.value

                if evaluation["status"] == EvaluatorStatus.PASS.value:
                    run_state.pass_stage(
                        StageId.EVALUATOR,
                        StageResultCode.EVALUATION_PASS,
                        artifacts=(
                            blind_artifact,
                            evaluation_artifact,
                            active_contract_closure_artifact,
                            checks_artifact,
                            active_runtime_artifact or active_verification_artifact,
                            "candidate_identity_current.json",
                        ),
                    )
                    break
                if evaluation["status"] == EvaluatorStatus.FINDINGS.value:
                    run_state.route_stage(
                        StageId.EVALUATOR,
                        outcome=WorkflowOutcome.REPAIR,
                        result_code=StageResultCode.EVALUATOR_FINDINGS,
                        reason_code="EVALUATOR_FINDINGS",
                        artifacts=(
                            blind_artifact,
                            evaluation_artifact,
                            "candidate_identity_current.json",
                        ),
                    )
                    evaluator_discoveries = [
                        {
                            "kind": finding["category"].lower(),
                            "name": finding["title"],
                            "reason": finding["failure_mode"],
                            "required_behavior": finding["required_action"],
                            "required_proof": finding["required_proof"],
                            "evidence": finding["evidence"],
                        }
                        for finding in evaluation.get("findings", [])
                        if finding.get("category") in {"CONSUMER", "RISK"}
                    ]
                    if evaluator_discoveries:
                        recompile_active_definition(
                            discoveries=evaluator_discoveries,
                            registry_changed=False,
                            detail=(
                                "Blind Evaluator discovered material consumers/risks"
                            ),
                        )
                    finding_signature = json.dumps(
                        evaluation.get("findings", []),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if _phase4_loop_stalled(
                        repair_progress_history,
                        candidate_id=evaluation_candidate_after.candidate_id,
                        failure_signature=finding_signature,
                    ):
                        raise RuntimeError(
                            "REPAIR_STALLED: Evaluator repeated the same findings on "
                            "the same candidate"
                        )
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
                        control_plane=recorder.control_plane,
                        run_state=run_state,
                        check_registry_digest=check_registry.digest(),
                        runtime_integrity_manager=runtime_integrity_manager,
                        execution_broker=execution_broker,
                    )
                    report_artifact = f"implementation_report_{implementation_report_index:02d}.json"
                    recorder.write_json(report_artifact, report)
                    observe_candidate("IMPLEMENTER_REPAIR_EVALUATION")
                    report, report_artifact = stabilize_implementer_report(
                        report, label=f"REPAIR EVALUATION #{fix_cycles}"
                    )
                    if report["status"] != ImplementerStatus.COMPLETE.value:
                        _phase4_route_implementer_terminal(
                            run_state=run_state,
                            report=report,
                            artifacts=(report_artifact, "candidate_identity_current.json"),
                            reason_suffix="AFTER_EVALUATOR_REPAIR",
                        )
                        print("HARNESS_TASK_STOPPED:", report["status"])
                        return 2
                    run_state.pass_stage(
                        StageId.IMPLEMENTER,
                        StageResultCode.IMPLEMENTATION_COMPLETE,
                        artifacts=(report_artifact, "candidate_identity_current.json"),
                    )
                    continue
                if evaluation["status"] == EvaluatorStatus.REPLAN_REQUIRED.value:
                    run_state.route_stage(
                        StageId.EVALUATOR,
                        outcome=WorkflowOutcome.REPLAN,
                        result_code=StageResultCode.REPLAN_REQUIRED,
                        reason_code="EVALUATOR_REPLAN_REQUIRED",
                        artifacts=(evaluation_artifact, "candidate_identity_current.json"),
                    )
                    if _phase4_loop_stalled(
                        replan_progress_history,
                        candidate_id=plan_fingerprint(plan) if plan is not None else "NO_PLAN",
                        failure_signature=evaluation["reason"],
                    ):
                        raise RuntimeError(
                            "REPLAN_STALLED: the same plan was rejected for the same reason"
                        )
                    replan_cycles += 1
                    run_state.invalidate(
                        InvalidationTrigger.REPLAN_REQUIRED,
                        detail=evaluation["reason"],
                    )
                    print(f"=== REPLAN #{replan_cycles} ===")

                    # Preserve the rejected implementation for audit, then remove
                    # it from the repository view seen by the fresh Planner. A
                    # semantic replan acknowledges that the previous technical
                    # model was wrong; keeping its diff visible would anchor the
                    # replacement agents to the rejected solution.
                    rejected_candidate = observe_candidate(
                        f"REPLAN_{replan_cycles}_REJECTED_CANDIDATE"
                    )
                    rejected_patch_artifact = (
                        f"replan_{replan_cycles:02d}_rejected_candidate.patch"
                    )
                    recorder.write_bytes(
                        rejected_patch_artifact,
                        build_candidate_patch(session),
                    )
                    replan_reset = reset_workspace_for_semantic_replan(
                        workspace=workspace,
                        baseline_sha=session.base_sha or preflight["head_sha"],
                    )
                    replan_reset_artifact = f"replan_{replan_cycles:02d}_reset.json"
                    recorder.write_authoritative_json(
                        replan_reset_artifact, replan_reset
                    )
                    clean_candidate = observe_candidate(
                        f"REPLAN_{replan_cycles}_CLEAN_BASELINE"
                    )
                    if clean_candidate.changed_paths:
                        raise RuntimeError(
                            "Semantic replan did not start from a clean candidate"
                        )

                    # Task-specific checks and evidence belonged to the rejected
                    # attempt. Project gates remain in repair_specs and are
                    # recompiled into the new Verification Plan.
                    check_registry.reset()
                    dynamic_specs = []
                    dynamic_notes = []
                    active_contract_closure = None
                    active_contract_closure_artifact = None
                    active_runtime_evidence = {
                        "protocol_version": "runtime-evidence.v1",
                        "status": StageResultCode.RUNTIME_VERIFICATION_SKIPPED.value,
                        "candidate_id": None,
                        "verification_plan_fingerprint": None,
                        "scenarios": [],
                        "reason_code": "INVALIDATED_BY_SEMANTIC_REPLAN",
                    }
                    active_runtime_artifact = None

                    # Clear role scratch so the new agents see repository facts,
                    # not temporary probes from the rejected attempt.
                    for role in (
                        ExecutionRole.PLANNER,
                        ExecutionRole.IMPLEMENTER,
                        ExecutionRole.EVALUATOR,
                    ):
                        scratch = execution_broker.scratch_root(role)
                        shutil.rmtree(scratch, ignore_errors=True)
                        scratch.mkdir(parents=True, exist_ok=True)

                    # Restore the authoritative project environment to the clean
                    # baseline as well. Replan is intentionally expensive and
                    # rare; reproducibility is more important than reusing a venv
                    # potentially mutated under the rejected implementation.
                    if runtime_manager is not None:
                        runtime_state = runtime_manager.build(clean=True)
                        project_runtime_index += 1
                        runtime_artifact = (
                            f"project_runtime_replan_{replan_cycles:02d}.json"
                        )
                        recorder.write_authoritative_json(
                            runtime_artifact, runtime_state.to_dict()
                        )
                        run_state.bump_revision(
                            RevisionKind.RUNTIME_ENVIRONMENT,
                            artifact=runtime_artifact,
                        )
                        toolchain["project_python"] = runtime_state.project_python
                        validate_toolchain(toolchain)
                        tool_probe_registry.toolchain["project_python"] = (
                            runtime_state.project_python
                        )
                        tool_probe_registry.invalidate_runtime_environment_evidence()

                    run_state.begin_stage(StageId.PLANNER)
                    plan = run_planner(
                        codex,
                        workspace=workspace,
                        task_prompt=manifest["prompt"],
                        task_contract=task_contract,
                        preflight=preflight,
                        owner_allowed_paths=allowed_paths,
                        replan_context=(
                            "Current candidate was rejected by a blind Evaluator. "
                            "Observed reason (not a reference implementation):\n"
                            + evaluation["reason"]
                        ),
                        on_heartbeat=make_heartbeat(f"REPLAN #{replan_cycles}"),
                        on_thread_started=_thread_recorder(recorder, f"planner_replan_{replan_cycles}"),
                        timeout=timeout,
                    )
                    validate_plan_artifact(plan, workspace=workspace, task_contract=task_contract)
                    replan_artifact = f"replan_{replan_cycles:02d}.json"
                    recorder.write_authoritative_json(replan_artifact, plan)
                    run_state.bump_revision(RevisionKind.PLAN, artifact=replan_artifact)
                    if plan["status"] != PlannerStatus.READY.value:
                        if plan["status"] == PlannerStatus.BLOCKED.value:
                            outcome = WorkflowOutcome.BLOCKED
                            result_code = StageResultCode.BLOCKED
                            reason_code = "PLANNER_BLOCKED_AFTER_REPLAN"
                        elif plan["status"] == PlannerStatus.TASK_CONTRACT_INVALID.value:
                            outcome = WorkflowOutcome.INVALID
                            result_code = StageResultCode.INVALID
                            reason_code = "TASK_CONTRACT_INVALID_AFTER_REPLAN"
                        else:
                            outcome = WorkflowOutcome.NEEDS_USER_DECISION
                            result_code = StageResultCode.NEEDS_USER_DECISION
                            reason_code = "PLANNER_NEEDS_USER_DECISION_AFTER_REPLAN"
                        run_state.route_stage(
                            StageId.PLANNER,
                            outcome=outcome,
                            result_code=result_code,
                            reason_code=reason_code,
                            artifacts=(replan_artifact,),
                        )
                        print("HARNESS_TASK_STOPPED:", plan["status"])
                        return 2
                    run_state.pass_stage(
                        StageId.PLANNER,
                        StageResultCode.PLANNER_READY,
                        artifacts=(
                            replan_artifact,
                            replan_reset_artifact,
                            rejected_patch_artifact,
                        ),
                    )
                    run_state.begin_stage(StageId.IMPLEMENTATION_CONTRACT)
                    implementation_contract = build_implementation_contract(
                        plan, task_contract=task_contract
                    )
                    validate_implementation_contract(implementation_contract)
                    implementation_contract_index += 1
                    contract_artifact = (
                        f"implementation_contract_{implementation_contract_index:02d}_"
                        f"replan_{replan_cycles:02d}.json"
                    )
                    recorder.write_authoritative_json(
                        contract_artifact, implementation_contract
                    )
                    run_state.bump_revision(
                        RevisionKind.IMPLEMENTATION_CONTRACT,
                        artifact=contract_artifact,
                    )
                    verification_plan = compile_verification_plan(
                        implementation_contract,
                        project_checks=repair_specs,
                        task_checks=active_task_check_keys(),
                    )
                    validate_verification_plan(verification_plan)
                    verification_plan_index += 1
                    verification_artifact = (
                        f"verification_plan_{verification_plan_index:02d}_"
                        f"replan_{replan_cycles:02d}.json"
                    )
                    recorder.write_authoritative_json(
                        verification_artifact, verification_plan
                    )
                    run_state.bump_revision(
                        RevisionKind.VERIFICATION_PLAN,
                        artifact=verification_artifact,
                    )
                    active_contract_artifact = contract_artifact
                    active_verification_artifact = verification_artifact
                    replan_tool_probes = tool_probe_registry.ensure_capabilities(
                        verification_plan["required_capabilities"],
                        batch_id=f"post-replan-capability-gate-{replan_cycles:02d}",
                    )
                    current_capabilities = available_capabilities(
                        toolchain=toolchain,
                        configured=declared_capabilities,
                        runtime=runtime_available_capabilities(runtime_scenarios),
                        verified_tool_capabilities=tool_probe_registry.verified_capabilities,
                    )
                    missing = required_capability_gaps(
                        verification_plan,
                        available=current_capabilities,
                    )
                    runtime_gaps = runtime_requirement_gaps(
                        verification_plan, runtime_scenarios
                    )
                    runtime_env_gaps = runtime_environment_gaps(
                        runtime_scenarios,
                        verification_plan=verification_plan,
                        execution_broker=execution_broker,
                    )
                    runtime_cmd_gaps = runtime_command_gaps(
                        verification_plan,
                        runtime_scenarios,
                        workspace=workspace,
                        toolchain=toolchain,
                    )
                    capability_gate_index += 1
                    capability_artifact = (
                        f"capability_gate_{capability_gate_index:02d}_"
                        f"replan_{replan_cycles:02d}.json"
                    )
                    capability_record = {
                        "schema_version": "capability-gate.v1",
                        "declared": declared_capabilities,
                        "available": sorted(current_capabilities),
                        "required": list(verification_plan["required_capabilities"]),
                        "tool_probe_evidence": replan_tool_probes.public_dict(),
                        "missing": missing,
                        "runtime_requirement_gaps": runtime_gaps,
                        "runtime_environment_gaps": runtime_env_gaps,
                        "runtime_command_gaps": runtime_cmd_gaps,
                    }
                    recorder.write_authoritative_json(
                        capability_artifact, capability_record
                    )
                    active_capability_artifact = capability_artifact
                    if not plan["owner_boundary_assessment"]["compatible"]:
                        run_state.route_stage(
                            StageId.IMPLEMENTATION_CONTRACT,
                            outcome=WorkflowOutcome.BLOCKED,
                            result_code=StageResultCode.BLOCKED,
                            reason_code="OWNER_BOUNDARY_CONFLICT_AFTER_REPLAN",
                            artifacts=(
                                contract_artifact,
                                verification_artifact,
                                capability_artifact,
                            ),
                        )
                        print("HARNESS_TASK_STOPPED: OWNER_BOUNDARY_CONFLICT")
                        return 2
                    if missing or runtime_gaps or runtime_env_gaps or runtime_cmd_gaps:
                        run_state.route_stage(
                            StageId.IMPLEMENTATION_CONTRACT,
                            outcome=WorkflowOutcome.BLOCKED,
                            result_code=StageResultCode.BLOCKED,
                            reason_code="REQUIRED_CAPABILITY_MISSING_AFTER_REPLAN",
                            artifacts=(
                                contract_artifact,
                                verification_artifact,
                                capability_artifact,
                            ),
                        )
                        print(
                            "HARNESS_TASK_STOPPED: REQUIRED_CAPABILITY_MISSING",
                            ", ".join([
                                *missing, *runtime_gaps, *runtime_env_gaps, *runtime_cmd_gaps
                            ]),
                        )
                        return 2
                    run_state.pass_stage(
                        StageId.IMPLEMENTATION_CONTRACT,
                        StageResultCode.IMPLEMENTATION_CONTRACT_READY,
                        artifacts=(
                            contract_artifact,
                            verification_artifact,
                            capability_artifact,
                        ),
                    )
                    active_runtime_evidence["verification_plan_fingerprint"] = (
                        verification_plan["fingerprint"]
                    )
                    current_specs = active_repair_specs()
                    _, stamp_path, self_verify_command = prepare_self_verify_runner(
                        workspace=workspace,
                        specs=current_specs,
                        toolchain=toolchain,
                    )
                    implementation_report_index += 1
                    implementer_thread = codex.start_thread(
                        cwd=workspace,
                        sandbox="workspace-write",
                        developer_instructions=IMPLEMENTER_INSTRUCTIONS,
                        on_started=_thread_recorder(
                            recorder, f"implementer_replan_{replan_cycles}"
                        ),
                    )
                    run_state.begin_stage(StageId.IMPLEMENTER)
                    report = run_implementer_report(
                        codex,
                        thread_id=implementer_thread,
                        prompt=build_implementation_prompt(
                            manifest["prompt"],
                            plan,
                            task_contract=task_contract,
                            implementation_contract=implementation_contract,
                            verification_plan=verification_plan,
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
                        control_plane=recorder.control_plane,
                        run_state=run_state,
                        check_registry_digest=check_registry.digest(),
                        runtime_integrity_manager=runtime_integrity_manager,
                        execution_broker=execution_broker,
                    )
                    report_artifact = f"implementation_report_{implementation_report_index:02d}.json"
                    recorder.write_json(report_artifact, report)
                    observe_candidate("IMPLEMENTER_REPLAN")
                    report, report_artifact = stabilize_implementer_report(
                        report, label=f"IMPLEMENT REPLAN #{replan_cycles}"
                    )
                    if report["status"] != ImplementerStatus.COMPLETE.value:
                        _phase4_route_implementer_terminal(
                            run_state=run_state,
                            report=report,
                            artifacts=(report_artifact, "candidate_identity_current.json"),
                            reason_suffix="AFTER_REPLAN",
                        )
                        print("HARNESS_TASK_STOPPED:", report["status"])
                        return 2
                    run_state.pass_stage(
                        StageId.IMPLEMENTER,
                        StageResultCode.IMPLEMENTATION_COMPLETE,
                        artifacts=(report_artifact, "candidate_identity_current.json"),
                    )
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

        quality_reconciliation = reconcile_quality_gate(
            run_state_data=run_state.data,
            final_candidate=final_candidate_before,
            mode=workflow_mode,
        )
        quality_reconciliation_artifact = "quality_gate_reconciliation.json"
        recorder.write_authoritative_json(
            quality_reconciliation_artifact,
            quality_reconciliation,
        )

        heldout_artifacts: list[str] = []
        heldout_evidence: dict | None = None
        if workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK:
            if not heldout_specs:
                run_state.route_stage(
                    StageId.FINAL_GATE,
                    outcome=WorkflowOutcome.INVALID,
                    result_code=StageResultCode.BENCHMARK_INVALID,
                    reason_code="HELDOUT_CHECKS_MISSING",
                    artifacts=(quality_reconciliation_artifact,),
                )
                print(StageResultCode.BENCHMARK_INVALID.value)
                return 2
            oracle_marker = str(benchmark.get("baseline_failure_marker") or "")
            heldout_results = run_checks(
                heldout_specs,
                workspace=workspace,
                toolchain=toolchain,
                runtime_root=recorder.root / "heldout",
                label="HELD-OUT",
                execution_broker=execution_broker,
                execution_role=ExecutionRole.HELDOUT,
                runtime_integrity_manager=runtime_integrity_manager,
                batch_id="FINAL_HELDOUT_CHECKS",
            )
            heldout_results_artifact = "heldout_results.json"
            recorder.write_authoritative_json(
                heldout_results_artifact,
                check_records(heldout_results),
            )
            final_candidate_after_heldout = observe_candidate("FINAL_GATE_AFTER_HELDOUT")
            heldout_evidence = classify_heldout_results(
                results=heldout_results,
                oracle_marker=oracle_marker,
                candidate_before=final_candidate_before.candidate_id,
                candidate_after=final_candidate_after_heldout.candidate_id,
            )
            heldout_evidence_artifact = "heldout_evidence.json"
            recorder.write_authoritative_json(
                heldout_evidence_artifact,
                heldout_evidence,
            )
            heldout_artifacts.extend(
                [heldout_results_artifact, heldout_evidence_artifact]
            )
            heldout_status = heldout_evidence["status"]
            if heldout_status != "HELDOUT_PASS":
                if heldout_status == "HELDOUT_SEMANTIC_FAIL":
                    outcome = WorkflowOutcome.FAIL
                    result_code = StageResultCode.HARNESS_BENCHMARK_FAIL
                    exit_code = 1
                else:
                    outcome = WorkflowOutcome.INVALID
                    result_code = StageResultCode.BENCHMARK_INVALID
                    exit_code = 2
                reason_code = str(
                    heldout_evidence.get("reason_code") or heldout_status
                )
                run_state.route_stage(
                    StageId.FINAL_GATE,
                    outcome=outcome,
                    result_code=result_code,
                    reason_code=reason_code,
                    artifacts=(
                        quality_reconciliation_artifact,
                        *heldout_artifacts,
                        "candidate_identity_current.json",
                    ),
                )
                print(result_code.value)
                print("HELDOUT_STATUS:", heldout_status)
                print(f"TOTAL_ELAPSED: {time.monotonic() - started:.2f}s")
                return exit_code
            print("HELDOUT_PASS")

        final_candidate = observe_candidate("FINAL_GATE_PACKAGE")
        if final_candidate.candidate_id != final_candidate_before.candidate_id:
            raise RuntimeError("Candidate changed during Final Gate")
        patch, patch_metadata = package_candidate(session=session, recorder=recorder)
        packaged_candidate = observe_candidate("FINAL_GATE_AFTER_PACKAGE")
        if packaged_candidate.candidate_id != final_candidate.candidate_id:
            raise RuntimeError("Candidate packaging mutated the candidate")

        patch_proof = build_patch_reconstruction_proof(
            repository=(
                session.workspace
                if session.benchmark_isolated or session.source_repo is None
                else session.source_repo
            ),
            baseline_sha=final_candidate.baseline_sha,
            patch=patch,
            expected_candidate=final_candidate,
            private_root=recorder.private_root,
        )
        patch_proof_artifact = "patch_proof.json"
        recorder.write_authoritative_json(patch_proof_artifact, patch_proof)

        evidence_names: list[str] = []
        for binding in quality_reconciliation["stage_bindings"]:
            evidence_names.extend(binding.get("artifacts", []))
        evidence_names.extend(
            [quality_reconciliation_artifact, patch_proof_artifact, *heldout_artifacts]
        )
        artifact_bindings = [
            artifact_digest(recorder.root, recorder.private_root, name)
            for name in dict.fromkeys(evidence_names)
            if name != "candidate.patch"
        ]

        success_code = (
            StageResultCode.HARNESS_BENCHMARK_PASS
            if workflow_mode == WorkflowMode.HISTORICAL_BENCHMARK
            else StageResultCode.HARNESS_TASK_PASS
        )
        final_acceptance = build_final_acceptance(
            task_id=manifest["task_id"],
            harness_version=__version__,
            workflow_version=WORKFLOW_VERSION,
            mode=workflow_mode,
            pipeline_profile=pipeline_profile.value,
            result_mode=session.result_mode,
            source_baseline_sha=(
                session.source_base_sha or session.source_head or final_candidate.baseline_sha
            ),
            final_candidate=final_candidate,
            quality_reconciliation=quality_reconciliation,
            patch_metadata=patch_metadata,
            patch_proof=patch_proof,
            artifact_bindings=artifact_bindings,
            heldout_evidence=heldout_evidence,
        )
        final_acceptance_artifact = "final_acceptance.json"
        recorder.write_once_authoritative_json(
            final_acceptance_artifact,
            final_acceptance,
        )
        print(StageResultCode.FINAL_ACCEPTANCE_PASS.value)

        delivery_result = deliver_candidate_transaction(
            session=session,
            patch=patch,
            final_candidate=final_candidate,
        )
        delivery_record = delivery_result.to_dict()
        delivery_record_artifact = "delivery_record.json"
        recorder.write_once_authoritative_json(
            delivery_record_artifact,
            delivery_record,
        )
        delivered_candidate = observe_candidate("FINAL_GATE_AFTER_DELIVERY")
        if delivered_candidate.candidate_id != final_candidate.candidate_id:
            raise RuntimeError("Result delivery mutated the managed candidate")

        final_artifacts = [
            "candidate_identity_current.json",
            "candidate.patch",
            quality_reconciliation_artifact,
            patch_proof_artifact,
            final_acceptance_artifact,
            delivery_record_artifact,
            *heldout_artifacts,
        ]
        if delivery_result.status != StageResultCode.RESULT_DELIVERY_PASS.value:
            if delivery_result.status == StageResultCode.RESULT_DELIVERY_BLOCKED.value:
                outcome = WorkflowOutcome.BLOCKED
                result_code = StageResultCode.RESULT_DELIVERY_BLOCKED
            else:
                outcome = WorkflowOutcome.INVALID
                result_code = StageResultCode.RESULT_DELIVERY_FAIL
            reason_code = delivery_result.reason_code or delivery_result.status
            run_state.route_stage(
                StageId.FINAL_GATE,
                outcome=outcome,
                result_code=result_code,
                reason_code=reason_code,
                artifacts=final_artifacts,
            )
            print(delivery_result.status)
            print("ACCEPTED_PATCH_RETAINED:", patch_metadata["path"])
            print("RESULT_WORKTREE_RETAINED:", session.workspace)
            print(f"TOTAL_ELAPSED: {time.monotonic() - started:.2f}s")
            return 2

        if session.result_mode == "keep_worktree":
            print("RESULT_WORKTREE_RETAINED:", session.workspace)
        else:
            print("RESULT_APPLIED_TO_SOURCE:", delivery_result.destination)
        print(StageResultCode.RESULT_DELIVERY_PASS.value)
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
    except HarnessControlledStop as exc:
        print("HARNESS_TASK_STOPPED:", exc, file=sys.stderr)
        if session is not None:
            print("MANAGED_WORKTREE_ON_EXIT:", session.workspace, file=sys.stderr)
        if recorder is not None:
            print("RUN_DIR:", recorder.root, file=sys.stderr)
        print(f"TOTAL_ELAPSED: {time.monotonic() - started:.2f}s", file=sys.stderr)
        return 2
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
