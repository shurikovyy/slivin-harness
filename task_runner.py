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
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from slivin_harness.app_server import CodexAppServer
from slivin_harness.console import configure_utf8_stdio
from slivin_harness.evaluator import run_evaluator
from slivin_harness.impact import run_impact_auditor
from slivin_harness.planner import run_planner
from slivin_harness.protocol import (
    ArtifactContractError,
    EVALUATOR_PROTOCOL_VERSION,
    ID_PATTERNS,
    IMPACT_PROTOCOL_VERSION,
    PLANNER_PROTOCOL_VERSION,
    compact_plan_retry_context,
    impact_fingerprint,
    impact_obligation_ids,
    impact_required_candidate_paths,
    plan_fingerprint,
    required_obligation_ids,
)
from slivin_harness.workspace import (
    WorkspaceSession,
    apply_candidate_to_source,
    build_candidate_patch,
    prepare_workspace_session,
)


configure_utf8_stdio()

HARNESS_ROOT = Path(__file__).resolve().parent

DEFAULT_CODEX_NAMES = (
    "codex.cmd",
    "codex",
)



IMPLEMENTER_INSTRUCTIONS = """
Ты implementation agent внутри Slivin Harness.

Следуй инструкциям и AGENTS.md текущего repository.

Дополнительные правила Harness:

- изменяй только файлы внутри переданного workspace;
- не выполняй git add, git commit, git push, git pull, git merge,
  git rebase, git reset, git restore, git switch, git checkout или git clean;
- не меняй .git;
- не меняй unrelated scope; `candidate_paths` задают planned change surface;
  если material consumer требует дополнительный path, изменение допустимо только
  как часть исходного intent: Controller механически обнаружит расширение, откатит
  незапланированный path к baseline и проведёт replan/snapshot перед принятием;
- до production edits проверь ключевые факты planning artifact по реальному коду;
- planning artifact является инженерной гипотезой, а не приказом:
  если фактический код его опровергает, следуй доказательствам;
- current_contract/assumptions Planner нужно перепроверять по фактическому коду и тестам;
- не вводи новую eligibility/validity condition, сужающую compatibility behavior,
  без доказательства или явного требования пользователя;
- blocking verification obligations вычисляет Controller детерминированно из
  `release_critical` CC/INT и всех LIFE/REP/AUTH/CONS/PRES/TEST; реализация должна
  оставить достаточно evidence для независимого Evaluator;
- LIFE-* задают scope/lifecycle/authority state mechanisms; ACTION_LOCAL state
  не должен переопределять target другого нового action, а frozen in-flight target
  не должен молча ретаргетиться global intent;
- REP-* / AUTH-* требуют downstream local readers и единого authority/precedence
  across visibility/count/eligibility/payload/routing; backend compatibility
  сама по себе недостаточна;
- остальные CC-*/INT-* — characterization context; не превращай advisory observation
  в новую обязательную semantics без причины;
- используй предоставленный trusted toolchain для реальных project checks;
- любые cache/temp/test-runtime артефакты создавай только внутри `.harness_tmp`;
  для Jest используй `--no-cache` либо cache внутри `.harness_tmp`, не создавай
  `.jest-cache*` в корне repository; не оставляй `__pycache__` в source tree;
- не заменяй доступный behavioral test самодельным smoke-check;
- не считай собственное сообщение PASS доказательством завершения задачи;
- внешний Harness самостоятельно запускает acceptance checks и fresh evaluation;
- если Harness возвращает failure или finding, самостоятельно проверь его
  достижимость и исправляй только подтверждённую in-scope причину;
- не ослабляй тесты ради зелёного результата;
- после исправления закончи turn: Harness сам повторит проверки и evaluation.
""".strip()


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


def load_manifest(path: Path) -> dict:
    return tomllib.loads(
        path.read_text(encoding="utf-8")
    )


def resolve_runtime_path(
    raw_path: str | Path,
    *,
    base: Path = HARNESS_ROOT,
    project_root: Path | None = None,
) -> Path:
    value = os.path.expandvars(str(raw_path))
    value = value.format(
        home=str(Path.home()),
        harness_root=str(HARNESS_ROOT),
        project_root=str(project_root) if project_root else "",
    )
    path = Path(value).expanduser()

    if not path.is_absolute():
        path = base / path

    return path.resolve()


def resolve_harness_path(raw_path: str | Path) -> Path:
    return resolve_runtime_path(raw_path)


def load_local_config() -> tuple[dict, Path | None]:
    raw_override = os.environ.get("SLIVIN_HARNESS_CONFIG")
    config_path = (
        resolve_harness_path(raw_override)
        if raw_override
        else HARNESS_ROOT / "harness.local.toml"
    )

    if not config_path.exists():
        return {}, None

    return (
        tomllib.loads(
            config_path.read_text(encoding="utf-8")
        ),
        config_path,
    )


def _resolve_tool_path(
    raw: str | Path,
    *,
    project_root: Path | None = None,
) -> Path:
    value = os.path.expandvars(str(raw))
    formatted = value.format(
        home=str(Path.home()),
        harness_root=str(HARNESS_ROOT),
        project_root=str(project_root) if project_root else "",
    )

    # A bare executable name is intentionally PATH-resolved. This makes the
    # committed task/project configuration portable across machines.
    if not any(sep in formatted for sep in ("/", "\\")) and not formatted.startswith("~"):
        found = shutil.which(formatted)
        if found:
            return Path(found).resolve()
        raise RuntimeError(
            f"Configured executable '{formatted}' was not found on PATH. "
            "Use an absolute/user-local path in harness.local.toml if needed."
        )

    return resolve_runtime_path(
        formatted,
        project_root=project_root,
    )


def resolve_codex_cmd(local_config: dict) -> Path:
    raw = (
        os.environ.get("SLIVIN_CODEX_CMD")
        or local_config.get("codex", {}).get("command")
    )
    if raw:
        return _resolve_tool_path(str(raw))

    for name in DEFAULT_CODEX_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()

    raise RuntimeError(
        "Codex CLI is not configured and was not found on PATH. "
        "Set [codex].command in harness.local.toml or SLIVIN_CODEX_CMD."
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
    global_toolchain = local_config.get("toolchain", {})
    if isinstance(global_toolchain, dict):
        sources.append(global_toolchain)

    if project_name:
        project_cfg = (
            local_config.get("projects", {})
            .get(project_name, {})
        )
        project_toolchain = (
            project_cfg.get("toolchain", {})
            if isinstance(project_cfg, dict)
            else {}
        )
        if isinstance(project_toolchain, dict):
            sources.append(project_toolchain)

    manifest_toolchain = manifest.get("toolchain", {})
    if isinstance(manifest_toolchain, dict):
        sources.append(manifest_toolchain)

    for source in sources:
        for name, raw_path in source.items():
            merged[str(name)] = str(raw_path)

    return {
        name: str(
            _resolve_tool_path(
                raw_path,
                project_root=project_root,
            )
        )
        for name, raw_path in merged.items()
    }


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
        raise RuntimeError(
            f"Workspace is not a Git repository: {workspace}"
        )

    status = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout

    if status.strip():
        raise RuntimeError(
            "Workspace is not clean.\n\n"
            + status
            + "\nCommit/stash/remove unrelated changes before "
              "starting this Harness version."
        )



def _run_git(
    workspace: Path,
    *args: str,
) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def capture_preflight(
    workspace: Path,
) -> dict:
    """Capture immutable task-baseline evidence before the first agent edit."""
    head_sha = _run_git(
        workspace,
        "rev-parse",
        "HEAD",
    ).strip()

    status = _run_git(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )

    tracked_paths = [
        line
        for line in _run_git(
            workspace,
            "ls-files",
        ).splitlines()
        if line
    ]

    return {
        "head_sha": head_sha,
        "working_tree_clean": not bool(status.strip()),
        "status_porcelain": status,
        "tracked_paths": tracked_paths,
    }



def _safe_repo_path(
    workspace: Path,
    raw_path: str,
) -> tuple[str, Path]:
    rel = Path(raw_path.replace("\\", "/"))

    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(
            f"Planner candidate path must be repo-relative: {raw_path}"
        )

    target = (workspace / rel).resolve()
    root = workspace.resolve()

    if target != root and root not in target.parents:
        raise RuntimeError(
            f"Planner candidate path escapes workspace: {raw_path}"
        )

    return rel.as_posix(), target


def _git_optional(
    workspace: Path,
    *args: str,
) -> str | None:
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
        return None

    return result.stdout.strip()


def capture_baseline_snapshot(
    workspace: Path,
    *,
    preflight: dict,
    candidate_paths: list[str],
    existing_snapshot: dict | None = None,
    captured_before_first_edit: bool = True,
    captured_before_path_edit: bool | None = None,
    snapshot_role: str | None = None,
) -> dict:
    """Capture baseline/object evidence and path-local filesystem evidence."""
    head_sha = str(preflight["head_sha"])
    files: dict[str, dict] = dict(
        (existing_snapshot or {}).get(
            "files",
            {},
        )
    )

    for raw_path in candidate_paths:
        rel, target = _safe_repo_path(
            workspace,
            str(raw_path),
        )

        # Preserve the strongest already-captured per-path pre-edit evidence.
        if (
            rel in files
            and files[rel].get("captured_before_path_edit")
        ):
            continue

        before_path_edit = (
            captured_before_first_edit
            if captured_before_path_edit is None
            else captured_before_path_edit
        )

        entry: dict[str, object] = {
            "path": rel,
            "captured_before_first_edit": captured_before_first_edit,
            "captured_before_path_edit": before_path_edit,
            "exists": target.exists(),
            "is_file": target.is_file(),
        }

        if target.is_file():
            data = target.read_bytes()
            entry["worktree_size"] = len(data)
            entry["worktree_sha256"] = hashlib.sha256(data).hexdigest()
            entry["worktree_snapshot_role"] = (
                snapshot_role
                or (
                    "pre_edit"
                    if captured_before_first_edit
                    else "candidate_state_at_replan"
                )
            )

        entry["git_eol"] = _git_optional(
            workspace,
            "ls-files",
            "--eol",
            "--",
            rel,
        )

        entry["index_entry"] = _git_optional(
            workspace,
            "ls-files",
            "-s",
            "--",
            rel,
        )

        blob_sha = _git_optional(
            workspace,
            "rev-parse",
            f"{head_sha}:{rel}",
        )
        entry["baseline_blob_sha"] = blob_sha

        if blob_sha:
            size = _git_optional(
                workspace,
                "cat-file",
                "-s",
                blob_sha,
            )
            entry["baseline_blob_size"] = (
                int(size)
                if size and size.isdigit()
                else size
            )

        files[rel] = entry

    return {
        "head_sha": head_sha,
        "files": files,
    }



def planned_candidate_paths(
    workspace: Path,
    plan: dict,
) -> set[str]:
    result: set[str] = set()
    for raw_path in plan.get("candidate_paths", []):
        rel, _ = _safe_repo_path(workspace, str(raw_path))
        result.add(rel)
    return result


def collect_changed_paths(workspace: Path) -> set[str]:
    """Return tracked + untracked non-ignored candidate paths relative to repo."""
    tracked = _run_git(
        workspace,
        "diff",
        "--name-only",
        "--no-renames",
        "HEAD",
        "--",
    )
    untracked = _run_git(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
    )
    return {
        item.replace("\\", "/")
        for item in [*tracked.splitlines(), *untracked.splitlines()]
        if item.strip()
    }


def find_unplanned_changed_paths(
    workspace: Path,
    plan: dict,
) -> list[str]:
    planned = planned_candidate_paths(workspace, plan)
    return sorted(
        path
        for path in collect_changed_paths(workspace)
        if path not in planned
    )


def restore_unplanned_paths_to_baseline(
    workspace: Path,
    *,
    preflight: dict,
    paths: list[str],
) -> None:
    """Rollback only unplanned paths so they can be replanned and snapshotted.

    Controller-owned Git mutation is intentional here. Implementer remains forbidden
    from Git history/worktree control. Tracked paths are restored from the immutable
    task baseline HEAD; untracked files introduced by the agent are removed.
    """
    tracked_baseline = set(preflight.get("tracked_paths", []))
    head_sha = str(preflight["head_sha"])

    tracked = [path for path in paths if path in tracked_baseline]
    untracked = [path for path in paths if path not in tracked_baseline]

    if tracked:
        subprocess.run(
            [
                "git",
                "restore",
                f"--source={head_sha}",
                "--staged",
                "--worktree",
                "--",
                *tracked,
            ],
            cwd=workspace,
            check=True,
        )

    root = workspace.resolve()
    for raw_path in untracked:
        _, target = _safe_repo_path(workspace, raw_path)
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)

        parent = target.parent
        while parent != root and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def change_surface_revision_context(
    *,
    unexpected_paths: list[str],
    current_plan: dict,
) -> dict:
    return {
        "status": "REPLAN_REQUIRED",
        "reason": "UNPLANNED_CHANGE_SURFACE",
        "summary": (
            "Controller detected candidate changes outside Planner.candidate_paths. "
            "Those paths were restored to the trusted baseline before acceptance. "
            "Re-evaluate whether they are truly required and explicitly include every "
            "required changed path in candidate_paths."
        ),
        "unexpected_changed_paths": unexpected_paths,
        "previous_candidate_paths": [
            str(path)
            for path in current_plan.get("candidate_paths", [])
        ],
        "controller_action": (
            "Unexpected paths were rolled back to baseline. Existing in-plan changes "
            "were preserved. If an unexpected path is required, add it explicitly to "
            "candidate_paths so Harness can capture trusted pre-path-edit evidence."
        ),
    }


def validate_actual_surface_against_plan(
    workspace: Path,
    plan: dict,
) -> None:
    unexpected = find_unplanned_changed_paths(workspace, plan)
    if unexpected:
        raise RuntimeError(
            "Current candidate still contains changed paths outside revised "
            "candidate_paths:\n  - " + "\n  - ".join(unexpected)
        )


def validate_toolchain(toolchain: dict[str, str]) -> None:
    for name, raw_path in toolchain.items():
        path = Path(raw_path).expanduser()

        if not path.is_absolute():
            raise RuntimeError(
                f"Toolchain path must be absolute: {name}={raw_path}"
            )

        if not path.exists():
            raise RuntimeError(
                f"Toolchain executable/file does not exist: "
                f"{name}={path}"
            )


def format_toolchain(toolchain: dict[str, str]) -> str:
    if not toolchain:
        return "(trusted toolchain not declared)"

    return "\n".join(
        f"- {name}: {path}"
        for name, path in toolchain.items()
    )


def expand_command(
    command: list[str],
    *,
    workspace: Path,
    toolchain: dict[str, str],
) -> list[str]:
    tokens = {
        "workspace": str(workspace),
        "harness_root": str(HARNESS_ROOT),
        "python": sys.executable,
        **toolchain,
    }

    try:
        return [
            item.format(**tokens)
            for item in command
        ]
    except KeyError as exc:
        raise RuntimeError(
            f"Unknown command placeholder {{{exc.args[0]}}}. "
            "Declare it under [toolchain] in harness.local.toml or the task manifest."
        ) from exc


def run_check(
    spec: dict,
    *,
    workspace: Path,
    toolchain: dict[str, str],
    runtime_tmp: Path | None = None,
) -> CheckResult:
    name = spec["name"]

    command = expand_command(
        spec["command"],
        workspace=workspace,
        toolchain=toolchain,
    )

    timeout = int(
        spec.get("timeout_seconds", 600)
    )

    tmp_root = (
        runtime_tmp
        if runtime_tmp is not None
        else workspace / ".harness_tmp"
    )
    tmp_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    env = os.environ.copy()
    env["SLIVIN_HARNESS_WORKSPACE"] = str(workspace)
    env["TEMP"] = str(tmp_root)
    env["TMP"] = str(tmp_root)
    env["TMPDIR"] = str(tmp_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["XDG_CACHE_HOME"] = str(tmp_root / "cache")
    env["NPM_CONFIG_CACHE"] = str(tmp_root / "npm")

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
            timeout=timeout,
            env=env,
        )

        return CheckResult(
            name=name,
            command=command,
            returncode=result.returncode,
            output=result.stdout.strip(),
            duration_seconds=(
                time.monotonic() - started
            ),
        )

    except subprocess.TimeoutExpired as exc:
        output = ""

        if exc.stdout:
            if isinstance(exc.stdout, bytes):
                output = exc.stdout.decode(
                    "utf-8",
                    errors="replace",
                )
            else:
                output = exc.stdout

        return CheckResult(
            name=name,
            command=command,
            returncode=124,
            output=output.strip(),
            timed_out=True,
            duration_seconds=(
                time.monotonic() - started
            ),
        )


def run_checks(
    specs: list[dict],
    *,
    workspace: Path,
    toolchain: dict[str, str],
) -> list[CheckResult]:
    results: list[CheckResult] = []

    for index, spec in enumerate(specs, start=1):
        name = spec["name"]

        print(
            f"[{index}/{len(specs)}] {name}"
        )

        result = run_check(
            spec,
            workspace=workspace,
            toolchain=toolchain,
        )

        results.append(result)
        duration = format_duration(
            result.duration_seconds,
            compact=True,
        )

        if result.passed:
            print(f"  PASS ({duration})")
        elif result.timed_out:
            print(f"  TIMEOUT ({duration})")
        else:
            print(
                f"  FAIL (exit={result.returncode}, {duration})"
            )

        if result.output:
            print()
            print(result.output)
            print()

    return results


def split_checks(
    specs: list[dict],
) -> tuple[list[dict], list[dict]]:
    repair_checks: list[dict] = []
    heldout_checks: list[dict] = []

    for spec in specs:
        feedback = str(
            spec.get("feedback", "repair")
        ).lower()

        if feedback == "repair":
            repair_checks.append(spec)
        elif feedback == "heldout":
            heldout_checks.append(spec)
        else:
            raise RuntimeError(
                f"Unsupported check feedback mode: {feedback} "
                f"for {spec.get('name', '<unnamed>')}"
            )

    return repair_checks, heldout_checks


def collect_plan_ids(
    plan: dict,
    field: str,
) -> list[str]:
    return [
        str(item["id"])
        for item in plan.get(field, [])
    ]



def validate_plan_artifact(
    plan: dict,
) -> None:
    if "release_obligations" in plan:
        raise ArtifactContractError(
            code="PLANNER_FORBIDDEN_RELEASE_OBLIGATIONS_FIELD",
            field="release_obligations",
            message=(
                "Planner must not restate blocking obligation IDs. "
                "The Controller derives that ledger deterministically."
            ),
            expected=(
                "Omit release_obligations entirely. Mark only CC/INT items with "
                "release_critical=true/false; Controller owns the final ID ledger."
            ),
            actual=plan.get("release_obligations"),
        )

    if plan.get("protocol_version") != PLANNER_PROTOCOL_VERSION:
        raise ArtifactContractError(
            code="PLANNER_PROTOCOL_VERSION_MISMATCH",
            field="protocol_version",
            message=(
                "Planner artifact protocol_version does not match the Controller contract."
            ),
            expected=PLANNER_PROTOCOL_VERSION,
            actual=plan.get("protocol_version"),
        )

    id_fields = (
        "current_contract",
        "assumptions",
        "affected_consumers",
        "state_lifecycle_audit",
        "decision_escalations",
        "representation_consumer_audit",
        "authority_matrix",
        "preservation_contract",
        "interaction_matrix",
        "test_matrix",
    )

    all_ids: list[str] = []
    for field in id_fields:
        pattern = re.compile(ID_PATTERNS[field])
        for item in plan.get(field, []):
            item_id = str(item.get("id", ""))
            if pattern.fullmatch(item_id) is None:
                raise ArtifactContractError(
                    code="PLANNER_ID_FORMAT_INVALID",
                    field=f"{field}[].id",
                    message=(
                        f"Planner ID must be a bare identifier matching "
                        f"{ID_PATTERNS[field]}; prose and grouped IDs are forbidden."
                    ),
                    expected=(
                        f"One bare ID only, e.g. "
                        f"{ID_PATTERNS[field].replace('^', '').replace('$', '')}"
                    ),
                    actual=item_id,
                )
            all_ids.append(item_id)

    duplicates = sorted({
        item_id
        for item_id in all_ids
        if all_ids.count(item_id) > 1
    })
    if duplicates:
        raise ArtifactContractError(
            code="PLANNER_DUPLICATE_ID",
            field="*.id",
            message="Planner returned duplicate IDs across the artifact.",
            expected="Every artifact ID is unique.",
            actual=duplicates,
        )

    for contract in plan.get("current_contract", []):
        if not contract.get("evidence"):
            raise ArtifactContractError(
                code="PLANNER_CURRENT_CONTRACT_EVIDENCE_MISSING",
                field=f"current_contract[{contract.get('id')}].evidence",
                message="Current-contract item has no evidence.",
                expected="At least one concrete evidence string.",
                actual=[],
            )
        if not isinstance(contract.get("release_critical"), bool):
            raise ArtifactContractError(
                code="PLANNER_RELEASE_CRITICAL_INVALID",
                field=f"current_contract[{contract.get('id')}].release_critical",
                message="Current-contract release_critical must be a boolean.",
                expected="true or false",
                actual=contract.get("release_critical"),
            )

    for interaction in plan.get("interaction_matrix", []):
        if not isinstance(interaction.get("release_critical"), bool):
            raise ArtifactContractError(
                code="PLANNER_RELEASE_CRITICAL_INVALID",
                field=f"interaction_matrix[{interaction.get('id')}].release_critical",
                message="Interaction release_critical must be a boolean.",
                expected="true or false",
                actual=interaction.get("release_critical"),
            )

    for assumption in plan.get("assumptions", []):
        if assumption.get("narrows_existing_behavior"):
            if (
                assumption.get("confidence") != "HIGH"
                or not assumption.get("evidence")
            ):
                raise ArtifactContractError(
                    code="PLANNER_UNSAFE_NARROWING_ASSUMPTION",
                    field=f"assumptions[{assumption.get('id')}]",
                    message=(
                        "Behavior-narrowing assumption lacks HIGH-confidence evidence."
                    ),
                    expected=(
                        "confidence=HIGH and at least one concrete evidence item, "
                        "or do not narrow existing behavior."
                    ),
                    actual={
                        "confidence": assumption.get("confidence"),
                        "evidence": assumption.get("evidence"),
                    },
                )

    candidate_paths = [
        str(path)
        for path in plan.get("candidate_paths", [])
    ]
    if len(candidate_paths) != len(set(candidate_paths)):
        raise ArtifactContractError(
            code="PLANNER_DUPLICATE_CANDIDATE_PATH",
            field="candidate_paths",
            message="Planner returned duplicate candidate_paths.",
            expected="Unique repo-relative paths only.",
            actual=candidate_paths,
        )

    if plan.get("status") == "READY" and not candidate_paths:
        raise ArtifactContractError(
            code="PLANNER_CANDIDATE_PATHS_MISSING",
            field="candidate_paths",
            message="Planner READY artifact must declare candidate_paths.",
            expected="At least one exact repo-relative path.",
            actual=candidate_paths,
        )

    decision_escalations = plan.get("decision_escalations", [])
    if (
        plan.get("status") == "NEEDS_USER_DECISION"
        and not decision_escalations
    ):
        raise ArtifactContractError(
            code="PLANNER_DECISION_ESCALATION_MISSING",
            field="decision_escalations",
            message=(
                "NEEDS_USER_DECISION requires evidence that lifecycle/ownership "
                "cannot resolve the semantic conflict."
            ),
            expected="At least one DEC-* object.",
            actual=[],
        )

    if plan.get("status") == "READY" and decision_escalations:
        raise ArtifactContractError(
            code="PLANNER_READY_WITH_UNRESOLVED_DECISION",
            field="decision_escalations",
            message="READY artifact cannot contain unresolved decision escalations.",
            expected="[] for READY status.",
            actual=collect_plan_ids(plan, "decision_escalations"),
        )

    for item in plan.get("state_lifecycle_audit", []):
        if item.get("role") == "UNKNOWN" and item.get("confidence") == "HIGH":
            raise ArtifactContractError(
                code="PLANNER_UNKNOWN_LIFECYCLE_HIGH_CONFIDENCE",
                field=f"state_lifecycle_audit[{item.get('id')}].confidence",
                message="UNKNOWN lifecycle role cannot have HIGH confidence.",
                expected="MEDIUM/LOW confidence or a resolved lifecycle role.",
                actual="HIGH",
            )

    # Blocking obligations are Controller-owned and deterministically derived.
    # This is intentionally computed here so Planner cannot create an ambiguous
    # cross-reference list such as `CC-1 — description` or `CC-1, CC-2`.
    derived = required_obligation_ids(plan)
    if len(derived) != len(set(derived)):
        raise ArtifactContractError(
            code="CONTROLLER_DERIVED_OBLIGATION_DUPLICATE",
            field="derived_release_obligations",
            message="Controller-derived obligation ledger contains duplicate IDs.",
            expected="A unique deterministic set of exact IDs.",
            actual=derived,
        )



def validate_impact_artifact(
    impact: dict,
    *,
    plan: dict,
    workspace: Path,
) -> None:
    if impact.get("protocol_version") != IMPACT_PROTOCOL_VERSION:
        raise ArtifactContractError(
            code="IMPACT_PROTOCOL_VERSION_MISMATCH",
            field="protocol_version",
            message="Impact artifact protocol_version does not match Controller contract.",
            expected=IMPACT_PROTOCOL_VERSION,
            actual=impact.get("protocol_version"),
        )

    expected_plan_fingerprint = plan_fingerprint(plan)
    if impact.get("plan_fingerprint") != expected_plan_fingerprint:
        raise ArtifactContractError(
            code="IMPACT_PLAN_FINGERPRINT_MISMATCH",
            field="plan_fingerprint",
            message="Impact audit is not bound to the current approved plan.",
            expected=expected_plan_fingerprint,
            actual=impact.get("plan_fingerprint"),
        )

    items = impact.get("items", [])
    ids: list[str] = []
    pattern = re.compile(ID_PATTERNS["impact_items"])
    for item in items:
        item_id = str(item.get("id", ""))
        if pattern.fullmatch(item_id) is None:
            raise ArtifactContractError(
                code="IMPACT_ID_FORMAT_INVALID",
                field="items[].id",
                message="Impact item ID must be one bare IMP-* identifier.",
                expected="IMP-<integer>",
                actual=item_id,
            )
        ids.append(item_id)

        reader_paths = [str(path) for path in item.get("reader_paths", [])]
        if not reader_paths:
            raise ArtifactContractError(
                code="IMPACT_READER_PATHS_MISSING",
                field=f"items[{item_id}].reader_paths",
                message="Material impact item must identify concrete reader paths.",
                expected="At least one exact repo-relative reader path.",
                actual=reader_paths,
            )
        for raw in reader_paths:
            _safe_repo_path(workspace, raw)

        required_paths = [
            str(path) for path in item.get("required_candidate_paths", [])
        ]
        verification_paths = [
            str(path) for path in item.get("verification_paths", [])
        ]
        for raw in required_paths + verification_paths:
            _safe_repo_path(workspace, raw)

        if item.get("disposition") == "CHANGE_REQUIRED" and not required_paths:
            raise ArtifactContractError(
                code="IMPACT_CHANGE_PATHS_MISSING",
                field=f"items[{item_id}].required_candidate_paths",
                message="CHANGE_REQUIRED must name exact paths that need implementation/test changes.",
                expected="At least one exact repo-relative path.",
                actual=required_paths,
            )

        if not item.get("evidence"):
            raise ArtifactContractError(
                code="IMPACT_EVIDENCE_MISSING",
                field=f"items[{item_id}].evidence",
                message="Impact item has no concrete source evidence.",
                expected="At least one concrete code/search evidence string.",
                actual=[],
            )

    if len(ids) != len(set(ids)):
        raise ArtifactContractError(
            code="IMPACT_DUPLICATE_ID",
            field="items[].id",
            message="Impact audit returned duplicate IMP IDs.",
            expected="Unique IMP-* IDs.",
            actual=ids,
        )

    if impact.get("status") == "COMPLETE" and impact.get("shared_change_detected"):
        if not items:
            raise ArtifactContractError(
                code="IMPACT_SHARED_CHANGE_WITHOUT_ITEMS",
                field="items",
                message="Shared change was detected but no sibling-consumer inventory was returned.",
                expected="At least one IMP-* item.",
                actual=[],
            )

    if impact.get("status") == "COMPLETE" and not impact.get("completeness_evidence"):
        raise ArtifactContractError(
            code="IMPACT_COMPLETENESS_EVIDENCE_MISSING",
            field="completeness_evidence",
            message="Impact audit must show how repository-wide sibling consumers were searched.",
            expected="At least one repository-wide search/trace evidence string.",
            actual=[],
        )


def impact_change_items(impact: dict) -> list[dict]:
    return [
        item for item in impact.get("items", [])
        if item.get("disposition") == "CHANGE_REQUIRED"
    ]


def missing_impact_candidate_paths(plan: dict, impact: dict) -> list[str]:
    planned = {str(path) for path in plan.get("candidate_paths", [])}
    return [
        path for path in impact_required_candidate_paths(impact)
        if path not in planned
    ]


def impact_revision_context(impact: dict, missing_paths: list[str]) -> dict:
    return {
        "reason": "SHARED_IMPACT_CHANGE_REQUIRED",
        "impact_fingerprint": impact_fingerprint(impact),
        "mandatory_required_candidate_paths": missing_paths,
        "impact_audit": impact,
        "instruction": (
            "Fresh Shared Impact Audit found reachable sibling consumers that are "
            "incompatible with the current candidate. Treat CHANGE_REQUIRED items "
            "as machine-owned impact evidence. Add every mandatory required path to "
            "candidate_paths and update preservation/tests as needed; do not dismiss "
            "the consumer merely because it was absent from the initial Planner list."
        ),
    }


def build_impact_repair_prompt(
    impact: dict,
    *,
    plan: dict,
    baseline_snapshot: dict,
) -> str:
    return f"""
Fresh Shared Impact Auditor found material sibling-consumer work before release.

Task is NOT complete.

Current Controller-approved PLAN_FINGERPRINT: {plan_fingerprint(plan)}
IMPACT_FINGERPRINT: {impact_fingerprint(impact)}

--- BEGIN CURRENT PLAN ---
{json.dumps(plan, ensure_ascii=False, indent=2)}
--- END CURRENT PLAN ---

--- BEGIN SHARED IMPACT AUDIT ---
{json.dumps(impact, ensure_ascii=False, indent=2)}
--- END SHARED IMPACT AUDIT ---

--- BEGIN CURRENT BASELINE SNAPSHOT ---
{json.dumps(baseline_snapshot, ensure_ascii=False, indent=2)}
--- END CURRENT BASELINE SNAPSHOT ---

Independently verify each CHANGE_REQUIRED item and its real lifecycle reachability.
For confirmed items, fix the incompatible local reader/consumer and add targeted
regression evidence. Do not change unrelated scope and do not encode a known-answer
implementation. Every changed path must remain inside current candidate_paths;
D-032 will enforce this mechanically.

After repair finish the turn. Harness will rerun deterministic checks, a NEW fresh
Shared Impact Audit, and only then Fresh Evaluator.
""".strip()


def validate_evaluation_artifact(
    evaluation: dict,
    *,
    plan: dict,
    impact_audit: dict,
    risk: str,
) -> None:
    if evaluation.get("protocol_version") != EVALUATOR_PROTOCOL_VERSION:
        raise ArtifactContractError(
            code="EVALUATOR_PROTOCOL_VERSION_MISMATCH",
            field="protocol_version",
            message=(
                "Evaluator artifact protocol_version does not match the Controller contract."
            ),
            expected=EVALUATOR_PROTOCOL_VERSION,
            actual=evaluation.get("protocol_version"),
        )

    expected_fingerprint = plan_fingerprint(plan)
    if evaluation.get("plan_fingerprint") != expected_fingerprint:
        raise ArtifactContractError(
            code="EVALUATOR_PLAN_FINGERPRINT_MISMATCH",
            field="plan_fingerprint",
            message="Evaluator verdict is not bound to the current Controller-approved plan.",
            expected=expected_fingerprint,
            actual=evaluation.get("plan_fingerprint"),
        )

    expected_impact_fingerprint = impact_fingerprint(impact_audit)
    if evaluation.get("impact_fingerprint") != expected_impact_fingerprint:
        raise ArtifactContractError(
            code="EVALUATOR_IMPACT_FINGERPRINT_MISMATCH",
            field="impact_fingerprint",
            message="Evaluator verdict is not bound to the current Fresh Shared Impact Audit.",
            expected=expected_impact_fingerprint,
            actual=evaluation.get("impact_fingerprint"),
        )

    expected_obligations = set(
        required_obligation_ids(plan)
    )
    assessed = evaluation.get(
        "obligation_assessment",
        [],
    )
    assessed_ids = [
        str(item["id"])
        for item in assessed
    ]

    if len(assessed_ids) != len(set(assessed_ids)):
        raise RuntimeError(
            "Evaluator returned duplicate obligation IDs."
        )

    if set(assessed_ids) != expected_obligations:
        missing = sorted(
            expected_obligations - set(assessed_ids)
        )
        extra = sorted(
            set(assessed_ids) - expected_obligations
        )
        raise RuntimeError(
            "Evaluator obligation ledger does not match Planner obligations. "
            f"Missing={missing}; extra={extra}"
        )

    expected_impact_ids = set(impact_obligation_ids(impact_audit))
    impact_assessed = evaluation.get("impact_assessment", [])
    impact_assessed_ids = [str(item["id"]) for item in impact_assessed]

    if len(impact_assessed_ids) != len(set(impact_assessed_ids)):
        raise RuntimeError("Evaluator returned duplicate impact IDs.")

    if set(impact_assessed_ids) != expected_impact_ids:
        missing = sorted(expected_impact_ids - set(impact_assessed_ids))
        extra = sorted(set(impact_assessed_ids) - expected_impact_ids)
        raise RuntimeError(
            "Evaluator impact ledger does not match Fresh Shared Impact Audit. "
            f"Missing={missing}; extra={extra}"
        )

    expected_assumptions = set(
        collect_plan_ids(plan, "assumptions")
    )
    audits = evaluation.get(
        "planner_assumption_audit",
        [],
    )
    audit_ids = [
        str(item["id"])
        for item in audits
    ]

    if len(audit_ids) != len(set(audit_ids)):
        raise RuntimeError(
            "Evaluator returned duplicate assumption IDs."
        )

    if set(audit_ids) != expected_assumptions:
        missing = sorted(
            expected_assumptions - set(audit_ids)
        )
        extra = sorted(
            set(audit_ids) - expected_assumptions
        )
        raise RuntimeError(
            "Evaluator assumption audit does not match Planner assumptions. "
            f"Missing={missing}; extra={extra}"
        )

    if evaluation["status"] != "PASS":
        return

    not_passed = [
        item
        for item in assessed
        if item["status"] != "PASS"
    ]

    impact_not_passed = [
        item
        for item in impact_assessed
        if item["status"] != "PASS"
    ]

    blocking_findings = [
        finding
        for finding in evaluation["findings"]
        if finding["severity"] in {
            "BLOCKER",
            "HIGH",
            "MEDIUM",
        }
    ]


    blocking_plan_findings = [
        finding
        for finding in evaluation.get(
            "plan_findings",
            [],
        )
        if finding["severity"] in {
            "BLOCKER",
            "HIGH",
            "MEDIUM",
        }
    ]

    narrowing_ids = {
        str(item["id"])
        for item in plan.get("assumptions", [])
        if item.get("narrows_existing_behavior")
    }
    narrowing_audit_failures = [
        item
        for item in audits
        if (
            item["id"] in narrowing_ids
            and item["status"] != "CONFIRMED"
        )
    ]

    if (
        not_passed
        or impact_not_passed
        or blocking_findings
        or blocking_plan_findings
        or narrowing_audit_failures
    ):
        raise RuntimeError(
            "Evaluator returned contradictory PASS: unresolved release/impact obligation, "
            "blocking implementation/plan finding, or unconfirmed "
            "behavior-narrowing assumption."
        )

    if risk in {"medium", "high"} and evaluation.get(
        "unverified_risks"
    ):
        raise RuntimeError(
            "Evaluator returned PASS with unverified risks for "
            f"{risk}-risk task: {evaluation['unverified_risks']}"
        )

def truncate_output(
    output: str,
    *,
    limit: int = 12_000,
) -> str:
    if len(output) <= limit:
        return output

    half = limit // 2

    return (
        output[:half]
        + "\n\n... OUTPUT TRUNCATED ...\n\n"
        + output[-half:]
    )


def checks_summary(
    results: list[CheckResult],
) -> str:
    parts: list[str] = []

    for result in results:
        status = (
            "PASS"
            if result.passed
            else "TIMEOUT"
            if result.timed_out
            else f"FAIL({result.returncode})"
        )

        output = truncate_output(
            result.output,
            limit=6_000,
        )

        parts.append(
            "\n".join(
                [
                    f"CHECK: {result.name}",
                    f"STATUS: {status}",
                    f"COMMAND: {result.command}",
                    "OUTPUT:",
                    output or "<no output>",
                ]
            )
        )

    return "\n\n".join(parts)


def build_check_repair_prompt(
    failures: list[CheckResult],
) -> str:
    parts = [
        """
Внешний Slivin Harness отклонил текущий результат.

Задача НЕ завершена.

Исправь причины перечисленных deterministic failures.
Не меняй unrelated scope. Если repair объективно требует новый path,
Harness отдельно reconciles actual diff с planned candidate surface.
Не изменяй или не ослабляй тесты только ради зелёного результата,
если тест фиксирует требуемый контракт.

После исправления закончи turn.
Harness самостоятельно повторит все acceptance checks и fresh evaluation.
""".strip()
    ]

    for failure in failures:
        output = truncate_output(
            failure.output
        )

        parts.append(
            f"""
=== CHECK: {failure.name} ===
COMMAND:
{failure.command}

EXIT CODE:
{failure.returncode}

OUTPUT:
{output or "<no output>"}
""".strip()
        )

    return "\n\n".join(parts)


def build_evaluator_repair_prompt(
    evaluation: dict,
    *,
    plan: dict,
    impact_audit: dict,
    baseline_snapshot: dict,
) -> str:
    evaluation_json = json.dumps(
        evaluation,
        ensure_ascii=False,
        indent=2,
    )
    plan_json = json.dumps(
        plan,
        ensure_ascii=False,
        indent=2,
    )
    approved_plan_fingerprint = plan_fingerprint(plan)
    approved_impact_fingerprint = impact_fingerprint(impact_audit)
    impact_json = json.dumps(impact_audit, ensure_ascii=False, indent=2)
    impact_ids_json = json.dumps(
        impact_obligation_ids(impact_audit), ensure_ascii=False, indent=2
    )
    snapshot_json = json.dumps(
        baseline_snapshot,
        ensure_ascii=False,
        indent=2,
    )
    obligation_json = json.dumps(
        required_obligation_ids(plan),
        ensure_ascii=False,
        indent=2,
    )

    return f"""
Независимый Fresh Evaluator отклонил текущий candidate.

Задача НЕ завершена.

Current Controller-approved plan for this repair cycle.
PLAN_FINGERPRINT: {approved_plan_fingerprint}

--- BEGIN CURRENT PLAN ---
{plan_json}
--- END CURRENT PLAN ---

Current Fresh Shared Impact Audit for this repair cycle.
IMPACT_FINGERPRINT: {approved_impact_fingerprint}

--- BEGIN CURRENT IMPACT AUDIT ---
{impact_json}
--- END CURRENT IMPACT AUDIT ---

--- BEGIN CONTROLLER IMPACT IDS ---
{impact_ids_json}
--- END CONTROLLER IMPACT IDS ---

Controller-derived blocking obligation IDs:

--- BEGIN CONTROLLER OBLIGATION IDS ---
{obligation_json}
--- END CONTROLLER OBLIGATION IDS ---

Current path-local baseline evidence:

--- BEGIN CURRENT BASELINE SNAPSHOT ---
{snapshot_json}
--- END CURRENT BASELINE SNAPSHOT ---

Проверь каждое замечание самостоятельно по коду и исходному требованию.
Исправь только подтверждённые in-scope findings и напрямую связанные
регрессии. Не превращай review finding в новое product requirement.
Не меняй unrelated scope и не ослабляй тесты. Новый действительно required path
будет отдельно reconciled Controller с planned candidate surface.

Если конкретный finding неверен, не делай искусственный patch только
ради удовлетворения reviewer. Закончи turn после проверки/исправлений:
Harness заново запустит deterministic checks и НОВЫЙ fresh evaluation.

--- BEGIN EVALUATION ---
{evaluation_json}
--- END EVALUATION ---
""".strip()



def build_change_surface_repair_prompt(
    *,
    unexpected_paths: list[str],
    revised_plan: dict,
    baseline_snapshot: dict,
) -> str:
    plan_json = json.dumps(
        revised_plan,
        ensure_ascii=False,
        indent=2,
    )
    approved_plan_fingerprint = plan_fingerprint(revised_plan)
    snapshot_json = json.dumps(
        baseline_snapshot,
        ensure_ascii=False,
        indent=2,
    )
    obligation_json = json.dumps(
        required_obligation_ids(revised_plan),
        ensure_ascii=False,
        indent=2,
    )
    paths = "\n".join(f"- {path}" for path in unexpected_paths)

    return f"""
Slivin Harness обнаружил изменения вне planned `candidate_paths`:

{paths}

Controller уже откатил ТОЛЬКО эти незапланированные paths к trusted task baseline.
Изменения внутри прежнего planned surface сохранены.

Fresh read-only Planner пересобрал change surface.
Controller-approved PLAN_FINGERPRINT: {approved_plan_fingerprint}

--- BEGIN REVISED PLAN ---
{plan_json}
--- END REVISED PLAN ---

Harness также обновил path-local baseline evidence:

--- BEGIN UPDATED BASELINE SNAPSHOT ---
{snapshot_json}
--- END UPDATED BASELINE SNAPSHOT ---

Controller-derived blocking obligation IDs for this revised plan:

--- BEGIN CONTROLLER OBLIGATION IDS ---
{obligation_json}
--- END CONTROLLER OBLIGATION IDS ---

Продолжи implementation по revised plan.
Если откатанный path действительно нужен, внеси изменение заново только если он
теперь явно присутствует в `candidate_paths`. Не восстанавливай unrelated change.
После turn внешний Harness снова механически сравнит actual diff с planned surface.
""".strip()


def build_implementation_prompt(
    task_prompt: str,
    plan: dict,
    toolchain: dict[str, str],
    baseline_snapshot: dict,
) -> str:
    plan_json = json.dumps(
        plan,
        ensure_ascii=False,
        indent=2,
    )
    approved_plan_fingerprint = plan_fingerprint(plan)

    toolchain_text = format_toolchain(
        toolchain
    )

    baseline_snapshot_json = json.dumps(
        baseline_snapshot,
        ensure_ascii=False,
        indent=2,
    )

    obligation_json = json.dumps(
        required_obligation_ids(plan),
        ensure_ascii=False,
        indent=2,
    )

    return f"""
Исходная задача пользователя:

--- BEGIN TASK ---
{task_prompt}
--- END TASK ---

Независимый read-only Planner подготовил engineering artifact.
Controller-approved PLAN_FINGERPRINT: {approved_plan_fingerprint}

--- BEGIN PLAN ---
{plan_json}
--- END PLAN ---

Harness pre-edit baseline snapshot для planned candidate_paths:

--- BEGIN BASELINE SNAPSHOT ---
{baseline_snapshot_json}
--- END BASELINE SNAPSHOT ---

Planner artifact — гипотеза, подтверждённая его read-only исследованием,
но не готовый patch. Перед изменениями перепроверь ключевые факты по
реальному коду. Если код опровергает Planner, следуй фактическим
доказательствам и сохраняй исходный пользовательский intent.

Current contract и assumptions должны быть перепроверены до ввода новых
validity/eligibility условий. Blocking verification ledger вычислен Controller
из Planner artifact и является отдельным machine-owned handoff:

--- BEGIN CONTROLLER OBLIGATION IDS ---
{obligation_json}
--- END CONTROLLER OBLIGATION IDS ---

Независимый Evaluator потребует конкретное evidence ровно по этим IDs.

Trusted toolchain, доступный в этой среде:

--- BEGIN TOOLCHAIN ---
{toolchain_text}
--- END TOOLCHAIN ---

Выполни задачу полностью в текущем workspace.
Используй реальные project checks, если они применимы.
""".strip()




def _check_result_record(result: CheckResult) -> dict:
    return {
        "name": result.name,
        "command": result.command,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "duration_seconds": result.duration_seconds,
        "output": result.output,
    }


def run_oracle_calibration(
    specs: list[dict],
    *,
    broken_workspace: Path,
    good_reference_workspace: Path,
    toolchain: dict[str, str],
    recorder: "RunRecorder",
) -> None:
    if not specs:
        raise RuntimeError(
            "Oracle calibration requested but no heldout checks are configured."
        )

    if not good_reference_workspace.exists():
        raise RuntimeError(
            "Oracle good reference workspace does not exist: "
            f"{good_reference_workspace}"
        )

    if good_reference_workspace.resolve() == broken_workspace.resolve():
        raise RuntimeError(
            "Oracle good reference workspace must differ from broken workspace."
        )

    print("=== ORACLE CALIBRATION ===")
    records: list[dict] = []
    calibration_ok = True

    for index, spec in enumerate(specs, start=1):
        print(f"[{index}/{len(specs)}] {spec['name']}")

        broken_result = run_check(
            spec,
            workspace=broken_workspace,
            toolchain=toolchain,
            runtime_tmp=(
                recorder.root
                / "oracle_calibration_tmp"
                / f"check_{index:02d}"
                / "broken"
            ),
        )
        good_result = run_check(
            spec,
            workspace=good_reference_workspace,
            toolchain=toolchain,
            runtime_tmp=(
                recorder.root
                / "oracle_calibration_tmp"
                / f"check_{index:02d}"
                / "good"
            ),
        )

        broken_ok = not broken_result.passed
        good_ok = good_result.passed

        print(
            "  BROKEN_BASELINE:",
            "FAIL as expected" if broken_ok else "UNEXPECTED PASS",
            f"({format_duration(broken_result.duration_seconds, compact=True)})",
        )
        print(
            "  GOOD_REFERENCE:",
            "PASS" if good_ok else "UNEXPECTED FAIL",
            f"({format_duration(good_result.duration_seconds, compact=True)})",
        )

        records.append({
            "check": spec["name"],
            "broken_workspace": str(broken_workspace),
            "good_reference_workspace": str(good_reference_workspace),
            "broken_expected_fail": broken_ok,
            "good_expected_pass": good_ok,
            "broken": _check_result_record(broken_result),
            "good": _check_result_record(good_result),
        })

        calibration_ok = calibration_ok and broken_ok and good_ok

    artifact = recorder.write_json(
        "oracle_calibration.json",
        records,
    )
    print("ORACLE_CALIBRATION_ARTIFACT:", artifact)

    if not calibration_ok:
        raise RuntimeError(
            "Held-out oracle calibration failed: every held-out check must "
            "FAIL on broken baseline and PASS on known-good reference."
        )

    print("ORACLE_CALIBRATION_PASS")
    print()



def _stable_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_oracle_calibration_certificate(
    specs: list[dict],
    *,
    certificate_path: Path,
    recorder: "RunRecorder",
) -> None:
    if not certificate_path.exists():
        raise RuntimeError(
            "Oracle calibration certificate does not exist: "
            f"{certificate_path}"
        )

    certificate = json.loads(
        certificate_path.read_text(encoding="utf-8")
    )

    if certificate.get("schema_version") != 1:
        raise RuntimeError(
            "Unsupported oracle calibration certificate schema: "
            f"{certificate.get('schema_version')}"
        )

    expected = {
        item["name"]: item
        for item in certificate.get("heldout_checks", [])
    }

    if len(expected) != len(specs):
        raise RuntimeError(
            "Oracle calibration certificate does not cover the current "
            "held-out check set. Recalibrate the grader."
        )

    verified: list[dict] = []

    for spec in specs:
        name = spec["name"]
        item = expected.get(name)
        if item is None:
            raise RuntimeError(
                "Held-out check is missing from calibration certificate: "
                f"{name}"
            )

        actual_spec_sha = _stable_sha256(spec)
        if actual_spec_sha != item.get("spec_sha256"):
            raise RuntimeError(
                "Held-out check definition changed since calibration: "
                f"{name}. Recalibrate before running the benchmark."
            )

        file_results: list[dict] = []
        for file_entry in item.get("files", []):
            path = resolve_harness_path(file_entry["path"])
            if not path.is_file():
                raise RuntimeError(
                    "Calibrated held-out file is missing: "
                    f"{path}"
                )
            actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_sha != file_entry.get("sha256"):
                raise RuntimeError(
                    "Calibrated held-out file changed since calibration: "
                    f"{file_entry['path']}. Recalibrate the grader."
                )
            file_results.append({
                "path": file_entry["path"],
                "sha256": actual_sha,
            })

        if item.get("broken_result") != "FAIL" or item.get("good_result") != "PASS":
            raise RuntimeError(
                "Calibration certificate does not attest broken=FAIL/good=PASS "
                f"for: {name}"
            )

        verified.append({
            "name": name,
            "spec_sha256": actual_spec_sha,
            "files": file_results,
            "broken_result": item["broken_result"],
            "good_result": item["good_result"],
        })

    recorder.write_json(
        "oracle_calibration_certificate_verified.json",
        {
            "certificate": str(certificate_path),
            "verified_checks": verified,
        },
    )

    print("=== ORACLE CALIBRATION CERTIFICATE ===")
    print("CERTIFICATE:", certificate_path)
    for item in verified:
        print(
            f"  {item['name']}: "
            "broken=FAIL good=PASS hashes=verified"
        )
    print("ORACLE_CALIBRATION_CERTIFICATE_PASS")
    print()


def run_benchmark_baseline_gate(
    specs: list[dict],
    *,
    workspace: Path,
    toolchain: dict[str, str],
    runtime_root: Path,
) -> dict:
    """Prove that the *current* historical workspace is still broken.

    This is Controller-owned evidence. Check output is intentionally retained only
    in the run artifact; Planner receives a sanitized fact and never the hidden
    assertion text.
    """
    if not specs:
        raise RuntimeError(
            "benchmark.confirm_current_baseline_broken=true requires at least "
            "one held-out check."
        )

    print("=== BENCHMARK BASELINE GATE ===")
    records: list[dict] = []
    all_failed_as_expected = True

    for index, spec in enumerate(specs, start=1):
        result = run_check(
            spec,
            workspace=workspace,
            toolchain=toolchain,
            runtime_tmp=(runtime_root / f"check_{index:02d}"),
        )
        failed_as_expected = not result.passed
        all_failed_as_expected = (
            all_failed_as_expected and failed_as_expected
        )

        print(
            f"[{index}/{len(specs)}] {spec['name']}: ",
            "FAIL as expected" if failed_as_expected else "UNEXPECTED PASS",
            f"({format_duration(result.duration_seconds, compact=True)})",
        )

        records.append({
            "check": spec["name"],
            "expected_result": "FAIL",
            "observed_result": "FAIL" if failed_as_expected else "PASS",
            "passed_gate": failed_as_expected,
            "result": _check_result_record(result),
        })

    evidence = {
        "schema_version": 1,
        "baseline_status": (
            "CONFIRMED_BROKEN"
            if all_failed_as_expected
            else "NOT_CONFIRMED_BROKEN"
        ),
        "authority": "CONTROLLER_HELDOUT",
        "workspace": str(workspace),
        "check_names": [record["check"] for record in records],
        "failure_details_exposed_to_planner": False,
        "records": records,
    }

    if all_failed_as_expected:
        print("BENCHMARK_BASELINE_CONFIRMED_BROKEN")
    else:
        print("BENCHMARK_BASELINE_NOT_CONFIRMED_BROKEN")
    print()

    return evidence


def planner_benchmark_context(evidence: dict | None) -> dict:
    """Sanitized trusted fact for Planner; never expose hidden assertion text."""
    if not evidence:
        return {}
    return {
        "schema_version": evidence.get("schema_version", 1),
        "baseline_status": evidence.get("baseline_status"),
        "authority": evidence.get("authority"),
        "check_names": list(evidence.get("check_names", [])),
        "failure_details_exposed_to_planner": False,
        "policy": (
            "This exact workspace was independently proven broken by the "
            "Controller before planning. Do not use an ad-hoc or partial probe "
            "to negate defect existence. If your probe passes, treat the probe "
            "as lower-fidelity and identify the missing lifecycle/readers."
        ),
    }


def format_duration(
    seconds: float,
    *,
    compact: bool = False,
) -> str:
    seconds = max(0.0, float(seconds))

    if compact and seconds < 60:
        return f"{seconds:.2f}s"

    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"


def phase_done(
    label: str,
    started: float,
) -> float:
    duration = time.monotonic() - started
    print(
        f"{label}_DONE: "
        f"{format_duration(duration)}"
    )
    print()
    return duration


class RunRecorder:
    def __init__(
        self,
        *,
        task_id: str,
    ) -> None:
        safe_task = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "_",
            task_id,
        ).strip("_") or "task"

        stamp = datetime.now().astimezone().strftime(
            "%Y%m%d-%H%M%S-%f"
        )

        self.root = (
            HARNESS_ROOT
            / "runs"
            / safe_task
            / stamp
        )
        self.root.mkdir(
            parents=True,
            exist_ok=False,
        )

    def write_json(
        self,
        name: str,
        value: object,
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_text(
        self,
        name: str,
        value: str,
    ) -> Path:
        path = self.root / name
        path.write_text(
            value,
            encoding="utf-8",
        )
        return path

    def write_bytes(
        self,
        name: str,
        value: bytes,
    ) -> Path:
        path = self.root / name
        path.write_bytes(value)
        return path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def _skill_name_from_file(
    skill_file: Path,
) -> str:
    try:
        lines = skill_file.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()[:40]
    except OSError:
        return skill_file.parent.name

    in_frontmatter = False

    for line in lines:
        stripped = line.strip()

        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue

        if (
            in_frontmatter
            and stripped.startswith("name:")
        ):
            value = stripped.split(
                ":",
                1,
            )[1].strip().strip('"').strip("'")

            if value:
                return value

    return skill_file.parent.name


def collect_repo_context(
    codex: CodexAppServer,
    *,
    workspace: Path,
    explicit_skill_names: list[str],
) -> tuple[dict, list[dict[str, str]]]:
    agents: list[dict] = []

    for path in sorted(
        workspace.rglob("AGENTS.md")
    ):
        if ".git" in path.parts:
            continue

        agents.append(
            {
                "path": path.relative_to(
                    workspace
                ).as_posix(),
                "sha256": _sha256_file(path),
            }
        )

    repo_skills: list[dict] = []

    skills_root = workspace / ".agents" / "skills"

    if skills_root.exists():
        for skill_file in sorted(
            skills_root.rglob("SKILL.md")
        ):
            repo_skills.append(
                {
                    "name": _skill_name_from_file(
                        skill_file
                    ),
                    "path": str(
                        skill_file.resolve()
                    ),
                    "repo_relative_path": (
                        skill_file.relative_to(
                            workspace
                        ).as_posix()
                    ),
                    "sha256": _sha256_file(
                        skill_file
                    ),
                }
            )

    print("=== REPO CONTEXT ===")
    print("Discovering App Server skills...", flush=True)

    skill_result = codex.list_skills(
        workspace,
        force_reload=True,
    )

    data = skill_result.get(
        "data",
        [],
    )

    skill_entry = next(
        (
            item
            for item in data
            if Path(
                str(
                    item.get(
                        "cwd",
                        workspace,
                    )
                )
            ).resolve() == workspace.resolve()
        ),
        data[0] if data else {},
    )

    app_skills = skill_entry.get(
        "skills",
        [],
    )
    app_errors = skill_entry.get(
        "errors",
        [],
    )

    app_by_name: dict[str, list[dict]] = {}

    for skill in app_skills:
        app_by_name.setdefault(
            str(skill.get("name", "")),
            [],
        ).append(skill)

    print("AGENTS:")
    if agents:
        for item in agents:
            print(
                f"  - {item['path']} "
                f"sha256={item['sha256'][:12]}..."
            )
    else:
        print("  (none found)")

    print("REPO SKILLS:")
    if repo_skills:
        for item in repo_skills:
            matches = app_by_name.get(
                item["name"],
                [],
            )
            discovered = bool(matches)
            enabled = any(
                bool(match.get("enabled", True))
                for match in matches
            )

            print(
                f"  - {item['name']}: "
                f"app_server_discovered={str(discovered).lower()} "
                f"enabled={str(enabled).lower()} "
                f"path={item['repo_relative_path']}"
            )
    else:
        print("  (none found)")

    if app_errors:
        print("SKILL DISCOVERY ERRORS:")
        for error in app_errors:
            print(f"  - {error}")

    repo_by_name = {
        item["name"]: item
        for item in repo_skills
    }

    explicit_skills: list[dict[str, str]] = []

    for name in explicit_skill_names:
        repo_item = repo_by_name.get(name)
        app_matches = app_by_name.get(
            name,
            [],
        )

        enabled = any(
            bool(match.get("enabled", True))
            for match in app_matches
        )

        if (
            repo_item is None
            or not app_matches
            or not enabled
        ):
            raise RuntimeError(
                "Requested explicit skill is not an enabled "
                f"repo skill discovered by App Server: {name}"
            )

        explicit_skills.append(
            {
                "name": name,
                "path": repo_item["path"],
            }
        )

    print(
        "EXPLICIT_ACTIVE_SKILLS:",
        (
            ", ".join(
                item["name"]
                for item in explicit_skills
            )
            if explicit_skills
            else "(none)"
        ),
    )
    print(
        "AUTO_SKILL_USAGE:",
        "not asserted; only explicit skill input is auditable",
    )
    print()

    context = {
        "agents": agents,
        "repo_skills": repo_skills,
        "app_server_skills": app_skills,
        "app_server_errors": app_errors,
        "explicit_active_skills": explicit_skills,
    }

    return context, explicit_skills


def make_thread_started_callback(
    *,
    phase: str,
    recorder: RunRecorder,
):
    def callback(thread: dict) -> None:
        sources = thread.get(
            "instructionSources",
            [],
        )

        print(
            f"[{phase}] INSTRUCTION_SOURCES:"
        )

        if not sources:
            print("  (not reported by App Server)")
        else:
            for source in sources:
                if isinstance(source, dict):
                    display = (
                        source.get("path")
                        or source.get("name")
                        or json.dumps(
                            source,
                            ensure_ascii=False,
                        )
                    )
                else:
                    display = str(source)

                print(f"  - {display}")

        recorder.write_json(
            f"thread_{phase.lower()}_metadata.json",
            thread,
        )

    return callback


def make_heartbeat(
    phase: str,
):
    def callback(state: dict) -> None:
        alive = (
            "alive"
            if state.get("alive")
            else (
                "exited:"
                + str(
                    state.get("returncode")
                )
            )
        )

        last_activity = float(
            state.get(
                "last_activity_seconds",
                0.0,
            )
        )

        print(
            f"[{phase}] working... "
            f"elapsed={format_duration(state['turn_elapsed_seconds'])} "
            f"app-server={alive} "
            f"last-event={format_duration(last_activity)}",
            flush=True,
        )

    return callback


def stream_agent_delta(
    text: str,
) -> None:
    print(
        text,
        end="",
        flush=True,
    )


def stream_agent_message_end() -> None:
    # One visible separator per completed agent message. We intentionally
    # do not put a newline after every token/delta.
    print(
        "\n",
        flush=True,
    )


def assert_harness_tmp_ignored(
    workspace: Path,
) -> None:
    probe = subprocess.run(
        [
            "git",
            "check-ignore",
            "-q",
            ".harness_tmp/",
        ],
        cwd=workspace,
    )

    if probe.returncode != 0:
        raise RuntimeError(
            ".harness_tmp/ must be ignored before running Harness. "
            "Add it to .git/info/exclude or repository ignore policy."
        )


def _plan_validation_error(
    plan: dict,
    *,
    trusted_benchmark_evidence: dict | None = None,
) -> dict | None:
    try:
        validate_plan_artifact(plan)

        if (
            trusted_benchmark_evidence
            and trusted_benchmark_evidence.get("baseline_status")
            == "CONFIRMED_BROKEN"
            and plan.get("status") == "BLOCKED"
        ):
            raise ArtifactContractError(
                code="PLANNER_TRUSTED_BASELINE_CONFLICT",
                field="status",
                message=(
                    "Planner returned BLOCKED even though the Controller already "
                    "proved this exact historical workspace fails the calibrated "
                    "held-out contract. A partial/synthetic reproduction cannot "
                    "override stronger Controller evidence."
                ),
                expected=(
                    "Return READY and investigate why any local probe disagrees "
                    "with the trusted baseline evidence. BLOCKED is not valid for "
                    "'defect not reproduced' in this benchmark."
                ),
                actual={
                    "status": plan.get("status"),
                    "summary": plan.get("summary"),
                },
            )
    except ArtifactContractError as exc:
        return exc.feedback()
    except RuntimeError as exc:
        return {
            "protocol_error": "PLANNER_ARTIFACT_VALIDATION_FAILED",
            "field": "unknown",
            "message": str(exc),
            "expected": "A Planner artifact satisfying the declared schema and Controller invariants.",
            "actual": None,
            "repair_rule": (
                "Return a fresh artifact that satisfies the schema literally; "
                "do not encode prose in ID/reference fields."
            ),
        }

    return None


def run_validated_planner(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    toolchain: dict[str, str],
    preflight: dict,
    baseline_snapshot: dict | None,
    revision_context: dict | None,
    trusted_benchmark_evidence: dict | None,
    explicit_skills: list[dict[str, str]],
    max_validation_retries: int,
    phase_label: str,
    artifact_prefix: str,
    recorder: RunRecorder,
) -> dict | None:
    base_revision_context = revision_context
    current_revision = revision_context

    for attempt in range(
        max_validation_retries + 1
    ):
        started = time.monotonic()

        plan = run_planner(
            codex,
            workspace=workspace,
            task_prompt=task_prompt,
            toolchain=toolchain,
            preflight=preflight,
            baseline_snapshot=baseline_snapshot,
            revision_context=current_revision,
            benchmark_evidence=planner_benchmark_context(
                trusted_benchmark_evidence
            ),
            explicit_skills=explicit_skills,
            on_heartbeat=make_heartbeat(
                phase_label
            ),
            on_thread_started=(
                make_thread_started_callback(
                    phase=(
                        phase_label
                        + f"_A{attempt + 1}"
                    ),
                    recorder=recorder,
                )
            ),
        )

        recorder.write_json(
            f"{artifact_prefix}_attempt_{attempt + 1}.json",
            plan,
        )

        phase_done(
            phase_label,
            started,
        )

        error = _plan_validation_error(
            plan,
            trusted_benchmark_evidence=trusted_benchmark_evidence,
        )

        if error is None:
            plan_contract = {
                "protocol_version": PLANNER_PROTOCOL_VERSION,
                "plan_fingerprint": plan_fingerprint(plan),
                "blocking_obligation_ids": required_obligation_ids(plan),
                "candidate_paths": [
                    str(path) for path in plan.get("candidate_paths", [])
                ],
            }
            recorder.write_json(
                f"{artifact_prefix}_contract.json",
                plan_contract,
            )
            print_structured(
                "PLAN CONTRACT",
                plan_contract,
            )
            print_structured(
                (
                    "PLAN RESULT"
                    if artifact_prefix == "plan"
                    else "REVISED PLAN RESULT"
                ),
                plan,
            )
            return plan

        print_structured(
            "INVALID PLAN SUMMARY",
            compact_plan_retry_context(plan),
        )
        print(
            "PLAN_VALIDATION_FAIL:",
            error.get("protocol_error"),
            f"field={error.get('field')}",
        )
        print_structured(
            "PLAN VALIDATION ERROR",
            error,
        )

        recorder.write_json(
            f"{artifact_prefix}_attempt_{attempt + 1}_validation_error.json",
            error,
        )

        if attempt >= max_validation_retries:
            return None

        print(
            f"=== {phase_label} VALIDATION RETRY "
            f"{attempt + 1}/{max_validation_retries} ==="
        )

        current_revision = {
            "revision_context": base_revision_context,
            "protocol_validation": error,
            "invalid_plan_summary": compact_plan_retry_context(plan),
            "retry_instruction": (
                "Repair only the protocol/semantic validation failure described above. "
                "Return a complete fresh Planner artifact under planner.v2. "
                "Do not copy malformed field formatting from the previous attempt."
            ),
        }

    return None



def run_validated_impact_audit(
    codex: CodexAppServer,
    *,
    workspace: Path,
    task_prompt: str,
    plan: dict,
    changed_paths: list[str],
    checks_summary_text: str,
    preflight: dict,
    baseline_snapshot: dict,
    explicit_skills: list[dict[str, str]],
    phase_label: str,
    artifact_prefix: str,
    recorder: RunRecorder,
) -> dict | None:
    started = time.monotonic()
    impact = run_impact_auditor(
        codex,
        workspace=workspace,
        task_prompt=task_prompt,
        plan=plan,
        changed_paths=changed_paths,
        checks_summary=checks_summary_text,
        preflight=preflight,
        baseline_snapshot=baseline_snapshot,
        explicit_skills=explicit_skills,
        on_heartbeat=make_heartbeat(phase_label),
        on_thread_started=make_thread_started_callback(
            phase=phase_label,
            recorder=recorder,
        ),
    )
    phase_done(phase_label, started)
    recorder.write_json(f"{artifact_prefix}.json", impact)

    try:
        validate_impact_artifact(
            impact,
            plan=plan,
            workspace=workspace,
        )
    except (ArtifactContractError, RuntimeError) as exc:
        if isinstance(exc, ArtifactContractError):
            payload = exc.feedback()
        else:
            payload = {
                "protocol_error": "IMPACT_ARTIFACT_VALIDATION_FAILED",
                "field": "unknown",
                "message": str(exc),
            }
        recorder.write_json(f"{artifact_prefix}_validation_error.json", payload)
        print_structured("IMPACT VALIDATION ERROR", payload)
        return None

    contract = {
        "protocol_version": IMPACT_PROTOCOL_VERSION,
        "impact_fingerprint": impact_fingerprint(impact),
        "plan_fingerprint": plan_fingerprint(plan),
        "impact_obligation_ids": impact_obligation_ids(impact),
        "required_candidate_paths": impact_required_candidate_paths(impact),
    }
    recorder.write_json(f"{artifact_prefix}_contract.json", contract)
    print_structured("SHARED IMPACT CONTRACT", contract)
    print_structured("SHARED IMPACT RESULT", impact)
    return impact


def print_structured(
    title: str,
    value: dict,
) -> None:
    print(f"=== {title} ===")
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )
    )
    print()



def finalize_success(
    *,
    workspace_session: WorkspaceSession,
    recorder: RunRecorder,
) -> None:
    if not workspace_session.managed:
        return

    patch = build_candidate_patch(workspace_session)
    patch_path = recorder.write_bytes("candidate.patch", patch)

    recorder.write_json(
        "workspace_session.json",
        {
            "mode": workspace_session.mode,
            "workspace": str(workspace_session.workspace),
            "project_name": workspace_session.project_name,
            "source_repo": (
                str(workspace_session.source_repo)
                if workspace_session.source_repo
                else None
            ),
            "source_head": workspace_session.source_head,
            "base_sha": workspace_session.base_sha,
            "result_mode": workspace_session.result_mode,
            "exposed_paths": list(workspace_session.exposed_paths),
            "candidate_patch": str(patch_path),
        },
    )

    if workspace_session.result_mode == "apply_to_source":
        apply_candidate_to_source(
            workspace_session,
            patch=patch,
        )
    else:
        print(
            "RESULT_WORKTREE_RETAINED:",
            workspace_session.workspace,
        )
        print("RESULT_PATCH:", patch_path)



def finalize_success_guarded(
    *,
    workspace_session: WorkspaceSession,
    recorder: RunRecorder,
) -> bool:
    try:
        finalize_success(
            workspace_session=workspace_session,
            recorder=recorder,
        )
        return True
    except RuntimeError as exc:
        recorder.write_text(
            "result_publication_error.txt",
            str(exc) + "\n",
        )
        print("HARNESS_TASK_BLOCKED: result_publication")
        print("RESULT_PUBLICATION_FAIL:", exc)
        return False


def evaluator_machine_guard(
    evaluation: dict,
    *,
    plan: dict,
    impact_audit: dict,
    risk: str,
) -> None:
    validate_evaluation_artifact(
        evaluation,
        plan=plan,
        impact_audit=impact_audit,
        risk=risk,
    )


def main() -> int:
    task_started = time.monotonic()
    task_started_wall = datetime.now().astimezone()
    workspace_session: WorkspaceSession | None = None

    try:
        parser = argparse.ArgumentParser()

        parser.add_argument(
            "manifest",
            type=Path,
        )

        args = parser.parse_args()

        manifest_path = resolve_harness_path(
            args.manifest
        )
        manifest = load_manifest(
            manifest_path
        )

        local_config, local_config_path = load_local_config()

        task_id = str(
            manifest.get(
                "task_id",
                manifest_path.stem,
            )
        )

        prompt = manifest["prompt"]

        checks = manifest.get(
            "checks",
            [],
        )
        repair_checks, heldout_checks = (
            split_checks(checks)
        )

        max_fix_cycles = int(
            manifest.get(
                "max_fix_cycles",
                3,
            )
        )

        max_replan_cycles = int(
            manifest.get(
                "max_replan_cycles",
                2,
            )
        )

        max_change_surface_cycles = int(
            manifest.get(
                "max_change_surface_cycles",
                max_replan_cycles,
            )
        )

        max_impact_cycles = int(
            manifest.get(
                "max_impact_cycles",
                2,
            )
        )

        max_plan_validation_retries = int(
            manifest.get(
                "max_plan_validation_retries",
                2,
            )
        )

        require_clean_git = bool(
            manifest.get(
                "require_clean_git",
                True,
            )
        )

        risk = str(
            manifest.get(
                "risk",
                "medium",
            )
        ).lower()

        if risk not in {
            "low",
            "medium",
            "high",
        }:
            raise RuntimeError(
                f"Unsupported risk level: {risk}"
            )

        workspace_session = prepare_workspace_session(
            manifest=manifest,
            local_config=local_config,
            harness_root=HARNESS_ROOT,
            task_id=task_id,
        )
        workspace = workspace_session.workspace

        toolchain = resolve_toolchain(
            local_config,
            manifest,
            project_name=workspace_session.project_name,
            project_root=workspace_session.source_repo,
        )

        validate_toolchain(
            toolchain
        )

        codex_cmd = resolve_codex_cmd(
            local_config
        )

        if not codex_cmd.exists():
            raise RuntimeError(
                "Codex CLI not found: "
                f"{codex_cmd}. Set [codex].command in harness.local.toml "
                "or SLIVIN_CODEX_CMD."
            )

        explicit_skill_names = [
            str(name)
            for name in manifest.get(
                "skills",
                [],
            )
        ]

        benchmark = manifest.get("benchmark", {})
        calibrate_heldout = bool(
            benchmark.get("calibrate_heldout", False)
        )
        raw_certificate = benchmark.get(
            "calibration_certificate"
        )
        confirm_current_baseline_broken = bool(
            benchmark.get("confirm_current_baseline_broken", False)
        )
        calibration_certificate = (
            resolve_harness_path(raw_certificate)
            if raw_certificate
            else None
        )
        good_reference_workspace = None

        if calibrate_heldout and calibration_certificate is not None:
            raise RuntimeError(
                "Use either live oracle calibration or a calibration certificate, "
                "not both."
            )

        if calibrate_heldout:
            raw_good_reference = benchmark.get(
                "good_reference_workspace"
            )
            if not raw_good_reference:
                raise RuntimeError(
                    "benchmark.calibrate_heldout=true requires "
                    "benchmark.good_reference_workspace."
                )
            good_reference_workspace = resolve_harness_path(
                str(raw_good_reference)
            )

        if not workspace.exists():
            raise RuntimeError(
                f"Workspace does not exist: {workspace}"
            )

        if require_clean_git:
            assert_clean_git(
                workspace
            )

        assert_harness_tmp_ignored(
            workspace
        )

        recorder = RunRecorder(
            task_id=task_id,
        )

        recorder.write_json(
            "workspace_session.json",
            {
                "mode": workspace_session.mode,
                "workspace": str(workspace_session.workspace),
                "project_name": workspace_session.project_name,
                "source_repo": (
                    str(workspace_session.source_repo)
                    if workspace_session.source_repo
                    else None
                ),
                "source_head": workspace_session.source_head,
                "base_sha": workspace_session.base_sha,
                "result_mode": workspace_session.result_mode,
                "exposed_paths": list(workspace_session.exposed_paths),
                "candidate_patch": None,
            },
        )

        recorder.write_json(
            "manifest_snapshot.json",
            manifest,
        )

        print(
            "TASK_STARTED:",
            task_started_wall.isoformat(),
        )
        print(f"TASK: {task_id}")
        print(f"WORKSPACE: {workspace}")
        print(f"WORKSPACE_MODE: {workspace_session.mode}")
        if workspace_session.project_name:
            print(f"PROJECT: {workspace_session.project_name}")
        if workspace_session.source_repo:
            print(f"SOURCE_REPO: {workspace_session.source_repo}")
        if workspace_session.managed:
            print(f"RESULT_MODE: {workspace_session.result_mode}")
        if workspace_session.exposed_paths:
            print(
                "EXPOSED_LOCAL_PATHS:",
                ", ".join(workspace_session.exposed_paths),
            )
        print(f"RUN_DIR: {recorder.root}")
        print(f"RISK: {risk}")
        print(
            "LOCAL_CONFIG:",
            local_config_path or "(defaults)",
        )
        print("CODEX_CMD:", codex_cmd)
        print(
            "TOOLCHAIN:",
            ", ".join(
                f"{name}={path}"
                for name, path in sorted(toolchain.items())
            ),
        )
        print(
            f"CHECKS: {len(checks)} "
            f"(repair={len(repair_checks)}, "
            f"heldout={len(heldout_checks)})"
        )
        print(
            f"MAX_FIX_CYCLES: "
            f"{max_fix_cycles}"
        )
        print(
            f"MAX_REPLAN_CYCLES: "
            f"{max_replan_cycles}"
        )
        print(
            f"MAX_CHANGE_SURFACE_CYCLES: "
            f"{max_change_surface_cycles}"
        )
        print(
            f"MAX_IMPACT_CYCLES: "
            f"{max_impact_cycles}"
        )
        print(
            "MAX_PLAN_VALIDATION_RETRIES:",
            max_plan_validation_retries,
        )
        calibration_mode = (
            "live"
            if calibrate_heldout
            else (
                "certificate"
                if calibration_certificate is not None
                else "disabled"
            )
        )
        print("ORACLE_CALIBRATION:", calibration_mode)
        print(
            "BENCHMARK_BASELINE_GATE:",
            "enabled" if confirm_current_baseline_broken else "disabled",
        )
        if calibrate_heldout:
            print(
                "GOOD_REFERENCE_WORKSPACE:",
                good_reference_workspace,
            )
        if calibration_certificate is not None:
            print(
                "CALIBRATION_CERTIFICATE:",
                calibration_certificate,
            )
        print()

        if calibrate_heldout:
            calibration_started = time.monotonic()

            run_oracle_calibration(
                heldout_checks,
                broken_workspace=workspace,
                good_reference_workspace=good_reference_workspace,
                toolchain=toolchain,
                recorder=recorder,
            )

            phase_done(
                "ORACLE_CALIBRATION",
                calibration_started,
            )

            if require_clean_git:
                assert_clean_git(workspace)
        elif calibration_certificate is not None:
            calibration_started = time.monotonic()
            verify_oracle_calibration_certificate(
                heldout_checks,
                certificate_path=calibration_certificate,
                recorder=recorder,
            )
            phase_done(
                "ORACLE_CALIBRATION_CERTIFICATE",
                calibration_started,
            )

        trusted_benchmark_evidence: dict | None = None
        if confirm_current_baseline_broken:
            baseline_gate_started = time.monotonic()
            trusted_benchmark_evidence = run_benchmark_baseline_gate(
                heldout_checks,
                workspace=workspace,
                toolchain=toolchain,
                runtime_root=(recorder.root / "benchmark_baseline_gate_tmp"),
            )
            recorder.write_json(
                "benchmark_baseline_gate.json",
                trusted_benchmark_evidence,
            )
            phase_done(
                "BENCHMARK_BASELINE_GATE",
                baseline_gate_started,
            )
            if (
                trusted_benchmark_evidence.get("baseline_status")
                != "CONFIRMED_BROKEN"
            ):
                print(
                    "HARNESS_TASK_BLOCKED: benchmark_baseline_not_broken"
                )
                return 2
            if require_clean_git:
                assert_clean_git(workspace)

        preflight = capture_preflight(
            workspace
        )
        recorder.write_json(
            "preflight.json",
            preflight,
        )

        runtime_tmp = (
            workspace
            / ".harness_tmp"
            / "agent_runtime"
        )

        with CodexAppServer(
            codex_cmd,
            client_version="0.5.3",
            runtime_tmp=runtime_tmp,
        ) as codex:
            context_started = (
                time.monotonic()
            )

            repo_context, explicit_skills = (
                collect_repo_context(
                    codex,
                    workspace=workspace,
                    explicit_skill_names=(
                        explicit_skill_names
                    ),
                )
            )

            recorder.write_json(
                "repo_context.json",
                repo_context,
            )

            phase_done(
                "REPO_CONTEXT",
                context_started,
            )

            if risk == "low":
                plan = {
                    "protocol_version": PLANNER_PROTOCOL_VERSION,
                    "status": "READY",
                    "summary": (
                        "Planner skipped for "
                        "low-risk task."
                    ),
                    "current_contract": [],
                    "assumptions": [],
                    "affected_consumers": [],
                    "state_lifecycle_audit": [],
                    "decision_escalations": [],
                    "representation_consumer_audit": [],
                    "authority_matrix": [],
                    "preservation_contract": [],
                    "interaction_matrix": [],
                    "test_matrix": [],
                    "candidate_paths": [],
                }
            else:
                print(
                    "=== PLANNING ==="
                )

                plan = run_validated_planner(
                    codex,
                    workspace=workspace,
                    task_prompt=prompt,
                    toolchain=toolchain,
                    preflight=preflight,
                    baseline_snapshot=None,
                    revision_context=None,
                    trusted_benchmark_evidence=trusted_benchmark_evidence,
                    explicit_skills=explicit_skills,
                    max_validation_retries=(
                        max_plan_validation_retries
                    ),
                    phase_label="PLANNING",
                    artifact_prefix="plan",
                    recorder=recorder,
                )

                if plan is None:
                    print(
                        "HARNESS_TASK_BLOCKED: "
                        "planner_artifact_invalid"
                    )
                    return 2

                if plan["status"] == "BLOCKED":
                    print(
                        "HARNESS_TASK_BLOCKED: "
                        "planner"
                    )
                    return 2

                if (
                    plan["status"]
                    == "NEEDS_USER_DECISION"
                ):
                    print(
                        "HARNESS_TASK_NEEDS_USER_DECISION: "
                        "planner"
                    )
                    return 3

                if plan["status"] != "READY":
                    print(
                        "HARNESS_TASK_BLOCKED: "
                        "unexpected_planner_status="
                        + str(
                            plan["status"]
                        )
                    )
                    return 2

            snapshot_started = (
                time.monotonic()
            )

            baseline_snapshot = (
                capture_baseline_snapshot(
                    workspace,
                    preflight=preflight,
                    candidate_paths=[
                        str(path)
                        for path in plan.get(
                            "candidate_paths",
                            [],
                        )
                    ],
                    captured_before_first_edit=True,
                )
            )

            recorder.write_json(
                "baseline_snapshot.json",
                baseline_snapshot,
            )

            print_structured(
                "BASELINE SNAPSHOT",
                baseline_snapshot,
            )

            phase_done(
                "BASELINE_SNAPSHOT",
                snapshot_started,
            )

            thread_id = codex.start_thread(
                cwd=workspace,
                sandbox="workspace-write",
                developer_instructions=(
                    IMPLEMENTER_INSTRUCTIONS
                ),
                on_started=(
                    make_thread_started_callback(
                        phase="IMPLEMENTER",
                        recorder=recorder,
                    )
                ),
            )

            print(
                "IMPLEMENTER_THREAD:",
                thread_id,
            )
            print()
            print(
                "=== IMPLEMENTATION ==="
            )

            implementation_prompt = (
                build_implementation_prompt(
                    prompt,
                    plan,
                    toolchain,
                    baseline_snapshot,
                )
            )

            implementation_started = (
                time.monotonic()
            )

            codex.run_turn(
                thread_id=thread_id,
                prompt=implementation_prompt,
                on_delta=stream_agent_delta,
                on_message_end=(
                    stream_agent_message_end
                ),
                skills=explicit_skills,
            )

            phase_done(
                "IMPLEMENTATION",
                implementation_started,
            )

            repair_cycles = 0
            replan_cycles = 0
            change_surface_cycles = 0
            impact_cycles = 0
            impact_index = 0
            evaluation_index = 0

            while True:
                if risk != "low":
                    unexpected_paths = find_unplanned_changed_paths(
                        workspace,
                        plan,
                    )

                    if unexpected_paths:
                        if change_surface_cycles >= max_change_surface_cycles:
                            print(
                                "HARNESS_TASK_FAIL: maximum change-surface cycles "
                                "reached while reconciling actual changed paths"
                            )
                            return 1

                        change_surface_cycles += 1
                        print()
                        print(
                            "=== CHANGE SURFACE RECONCILIATION "
                            f"{change_surface_cycles} ==="
                        )
                        print(
                            "UNPLANNED_CHANGED_PATHS:\n  - "
                            + "\n  - ".join(unexpected_paths)
                        )

                        recorder.write_json(
                            f"change_surface_{change_surface_cycles:02d}_detected.json",
                            {
                                "unexpected_changed_paths": unexpected_paths,
                                "planned_candidate_paths": sorted(
                                    planned_candidate_paths(workspace, plan)
                                ),
                                "actual_changed_paths": sorted(
                                    collect_changed_paths(workspace)
                                ),
                            },
                        )

                        restore_unplanned_paths_to_baseline(
                            workspace,
                            preflight=preflight,
                            paths=unexpected_paths,
                        )

                        remaining_unplanned = find_unplanned_changed_paths(
                            workspace,
                            plan,
                        )
                        if remaining_unplanned:
                            raise RuntimeError(
                                "Controller failed to restore unplanned paths:\n  - "
                                + "\n  - ".join(remaining_unplanned)
                            )

                        revision_context = change_surface_revision_context(
                            unexpected_paths=unexpected_paths,
                            current_plan=plan,
                        )

                        revised_plan = run_validated_planner(
                            codex,
                            workspace=workspace,
                            task_prompt=prompt,
                            toolchain=toolchain,
                            preflight=preflight,
                            baseline_snapshot=baseline_snapshot,
                            revision_context=revision_context,
                            trusted_benchmark_evidence=trusted_benchmark_evidence,
                            explicit_skills=explicit_skills,
                            max_validation_retries=max_plan_validation_retries,
                            phase_label=(
                                "CHANGE_SURFACE_REPLAN_"
                                f"{change_surface_cycles}"
                            ),
                            artifact_prefix=(
                                "change_surface_replan_"
                                f"{change_surface_cycles:02d}"
                            ),
                            recorder=recorder,
                        )

                        if revised_plan is None:
                            print(
                                "HARNESS_TASK_BLOCKED: "
                                "change_surface_replanner_artifact_invalid"
                            )
                            return 2
                        if revised_plan["status"] == "BLOCKED":
                            print(
                                "HARNESS_TASK_BLOCKED: change_surface_replanner"
                            )
                            return 2
                        if revised_plan["status"] == "NEEDS_USER_DECISION":
                            print(
                                "HARNESS_TASK_NEEDS_USER_DECISION: "
                                "change_surface_replanner"
                            )
                            return 3
                        if revised_plan["status"] != "READY":
                            print(
                                "HARNESS_TASK_BLOCKED: "
                                "unexpected_change_surface_replanner_status="
                                + str(revised_plan["status"])
                            )
                            return 2

                        # Any old candidate change omitted by the revised plan is no
                        # longer accepted and is restored as well. This makes actual
                        # diff == revised planned surface a mechanical invariant.
                        revised_planned = planned_candidate_paths(
                            workspace,
                            revised_plan,
                        )
                        obsolete_changed = sorted(
                            collect_changed_paths(workspace) - revised_planned
                        )
                        if obsolete_changed:
                            restore_unplanned_paths_to_baseline(
                                workspace,
                                preflight=preflight,
                                paths=obsolete_changed,
                            )

                        plan = revised_plan
                        baseline_snapshot = capture_baseline_snapshot(
                            workspace,
                            preflight=preflight,
                            candidate_paths=[
                                str(path)
                                for path in plan.get("candidate_paths", [])
                            ],
                            existing_snapshot=baseline_snapshot,
                            captured_before_first_edit=False,
                            captured_before_path_edit=True,
                            snapshot_role=(
                                "pre_path_edit_after_surface_reconciliation"
                            ),
                        )

                        validate_actual_surface_against_plan(
                            workspace,
                            plan,
                        )

                        recorder.write_json(
                            f"baseline_snapshot_surface_{change_surface_cycles:02d}.json",
                            baseline_snapshot,
                        )
                        print_structured(
                            "UPDATED BASELINE SNAPSHOT",
                            baseline_snapshot,
                        )

                        surface_prompt = build_change_surface_repair_prompt(
                            unexpected_paths=unexpected_paths,
                            revised_plan=plan,
                            baseline_snapshot=baseline_snapshot,
                        )
                        surface_started = time.monotonic()
                        codex.run_turn(
                            thread_id=thread_id,
                            prompt=surface_prompt,
                            on_delta=stream_agent_delta,
                            on_message_end=stream_agent_message_end,
                            skills=explicit_skills,
                        )
                        phase_done(
                            f"CHANGE_SURFACE_REPAIR_{change_surface_cycles}",
                            surface_started,
                        )
                        continue

                print(
                    f"=== DETERMINISTIC CHECKS "
                    f"(repair_cycles={repair_cycles}) ==="
                )

                checks_started = (
                    time.monotonic()
                )

                results = run_checks(
                    repair_checks,
                    workspace=workspace,
                    toolchain=toolchain,
                )

                phase_done(
                    "DETERMINISTIC_CHECKS",
                    checks_started,
                )

                recorder.write_json(
                    (
                        "checks_"
                        f"{repair_cycles:02d}_"
                        f"{evaluation_index:02d}.json"
                    ),
                    [
                        {
                            "name": item.name,
                            "command": item.command,
                            "returncode": (
                                item.returncode
                            ),
                            "timed_out": (
                                item.timed_out
                            ),
                            "duration_seconds": (
                                item.duration_seconds
                            ),
                            "output": item.output,
                        }
                        for item in results
                    ],
                )

                failures = [
                    result
                    for result in results
                    if not result.passed
                ]

                if failures:
                    if (
                        repair_cycles
                        >= max_fix_cycles
                    ):
                        print()
                        print(
                            "HARNESS_TASK_FAIL: "
                            "maximum fix cycles "
                            "reached"
                        )
                        return 1

                    repair_cycles += 1

                    print()
                    print(
                        f"=== REPAIR CYCLE "
                        f"{repair_cycles}: "
                        "CHECK FAILURES ==="
                    )

                    repair_prompt = (
                        build_check_repair_prompt(
                            failures
                        )
                    )

                    repair_started = (
                        time.monotonic()
                    )

                    codex.run_turn(
                        thread_id=thread_id,
                        prompt=repair_prompt,
                        on_delta=(
                            stream_agent_delta
                        ),
                        on_message_end=(
                            stream_agent_message_end
                        ),
                        skills=explicit_skills,
                    )

                    phase_done(
                        (
                            "REPAIR_CHECKS_"
                            f"{repair_cycles}"
                        ),
                        repair_started,
                    )
                    continue

                if risk != "low" and find_unplanned_changed_paths(
                    workspace,
                    plan,
                ):
                    # A deterministic check is allowed to reveal/create candidate
                    # files, but they are subject to the same D-032 reconciliation
                    # before evaluation. Loop back to the machine-enforced gate.
                    print(
                        "CHANGE_SURFACE_CHANGED_DURING_CHECKS: "
                        "reconciling before evaluation"
                    )
                    continue

                if risk != "low":
                    impact_index += 1
                    print()
                    print("=== FRESH SHARED IMPACT AUDIT ===")
                    current_impact = run_validated_impact_audit(
                        codex,
                        workspace=workspace,
                        task_prompt=prompt,
                        plan=plan,
                        changed_paths=sorted(collect_changed_paths(workspace)),
                        checks_summary_text=checks_summary(results),
                        preflight=preflight,
                        baseline_snapshot=baseline_snapshot,
                        explicit_skills=explicit_skills,
                        phase_label=f"IMPACT_{impact_index}",
                        artifact_prefix=f"impact_{impact_index:02d}",
                        recorder=recorder,
                    )

                    if current_impact is None:
                        print("HARNESS_TASK_BLOCKED: impact_artifact_invalid")
                        return 2
                    if current_impact.get("status") == "BLOCKED":
                        print("HARNESS_TASK_BLOCKED: shared_impact_auditor")
                        return 2

                    required_impact_changes = impact_change_items(current_impact)
                    if required_impact_changes:
                        if impact_cycles >= max_impact_cycles:
                            print(
                                "HARNESS_TASK_FAIL: maximum shared-impact repair "
                                "cycles reached"
                            )
                            return 1

                        impact_cycles += 1
                        missing_paths = missing_impact_candidate_paths(
                            plan,
                            current_impact,
                        )

                        if missing_paths:
                            print()
                            print(
                                f"=== SHARED IMPACT REPLAN {impact_cycles}: "
                                "MISSING SIBLING PATHS ==="
                            )
                            print(
                                "IMPACT_REQUIRED_PATHS:\n  - "
                                + "\n  - ".join(missing_paths)
                            )

                            revised_plan = run_validated_planner(
                                codex,
                                workspace=workspace,
                                task_prompt=prompt,
                                toolchain=toolchain,
                                preflight=preflight,
                                baseline_snapshot=baseline_snapshot,
                                revision_context=impact_revision_context(
                                    current_impact,
                                    missing_paths,
                                ),
                                trusted_benchmark_evidence=trusted_benchmark_evidence,
                                explicit_skills=explicit_skills,
                                max_validation_retries=max_plan_validation_retries,
                                phase_label=f"IMPACT_REPLAN_{impact_cycles}",
                                artifact_prefix=f"impact_replan_{impact_cycles:02d}",
                                recorder=recorder,
                            )

                            if revised_plan is None:
                                print(
                                    "HARNESS_TASK_BLOCKED: "
                                    "impact_replanner_artifact_invalid"
                                )
                                return 2
                            if revised_plan.get("status") == "BLOCKED":
                                print("HARNESS_TASK_BLOCKED: impact_replanner")
                                return 2
                            if revised_plan.get("status") == "NEEDS_USER_DECISION":
                                print(
                                    "HARNESS_TASK_NEEDS_USER_DECISION: "
                                    "impact_replanner"
                                )
                                return 3
                            if revised_plan.get("status") != "READY":
                                print(
                                    "HARNESS_TASK_BLOCKED: "
                                    "unexpected_impact_replanner_status="
                                    + str(revised_plan.get("status"))
                                )
                                return 2

                            missing_after_replan = missing_impact_candidate_paths(
                                revised_plan,
                                current_impact,
                            )
                            if missing_after_replan:
                                recorder.write_json(
                                    f"impact_replan_{impact_cycles:02d}_missing_paths.json",
                                    {
                                        "missing_required_candidate_paths": (
                                            missing_after_replan
                                        )
                                    },
                                )
                                print(
                                    "HARNESS_TASK_BLOCKED: "
                                    "impact_replan_missing_required_paths"
                                )
                                print(
                                    "MISSING_REQUIRED_PATHS:\n  - "
                                    + "\n  - ".join(missing_after_replan)
                                )
                                return 2

                            revised_planned = planned_candidate_paths(
                                workspace,
                                revised_plan,
                            )
                            obsolete_changed = sorted(
                                collect_changed_paths(workspace) - revised_planned
                            )
                            if obsolete_changed:
                                restore_unplanned_paths_to_baseline(
                                    workspace,
                                    preflight=preflight,
                                    paths=obsolete_changed,
                                )

                            plan = revised_plan
                            baseline_snapshot = capture_baseline_snapshot(
                                workspace,
                                preflight=preflight,
                                candidate_paths=[
                                    str(path)
                                    for path in plan.get("candidate_paths", [])
                                ],
                                existing_snapshot=baseline_snapshot,
                                captured_before_first_edit=False,
                                captured_before_path_edit=True,
                                snapshot_role=(
                                    "pre_path_edit_after_shared_impact_replan"
                                ),
                            )
                            recorder.write_json(
                                f"baseline_snapshot_impact_{impact_cycles:02d}.json",
                                baseline_snapshot,
                            )
                            print_structured(
                                "UPDATED BASELINE SNAPSHOT",
                                baseline_snapshot,
                            )

                        impact_repair_prompt = build_impact_repair_prompt(
                            current_impact,
                            plan=plan,
                            baseline_snapshot=baseline_snapshot,
                        )
                        impact_repair_started = time.monotonic()
                        codex.run_turn(
                            thread_id=thread_id,
                            prompt=impact_repair_prompt,
                            on_delta=stream_agent_delta,
                            on_message_end=stream_agent_message_end,
                            skills=explicit_skills,
                        )
                        phase_done(
                            f"IMPACT_REPAIR_{impact_cycles}",
                            impact_repair_started,
                        )
                        continue

                if risk == "low":
                    if heldout_checks:
                        print()
                        print(
                            "=== HELD-OUT "
                            "EVALUATION ==="
                        )
                        heldout_started = (
                            time.monotonic()
                        )
                        heldout_results = (
                            run_checks(
                                heldout_checks,
                                workspace=workspace,
                                toolchain=toolchain,
                            )
                        )
                        phase_done(
                            "HELDOUT",
                            heldout_started,
                        )

                        if any(
                            not item.passed
                            for item
                            in heldout_results
                        ):
                            print()
                            print(
                                "HARNESS_HELDOUT_FAIL"
                            )
                            return 4

                    if not finalize_success_guarded(
                        workspace_session=workspace_session,
                        recorder=recorder,
                    ):
                        return 2
                    print()
                    print(
                        "HARNESS_TASK_PASS"
                    )
                    return 0

                print()
                print(
                    "=== FRESH EVALUATION ==="
                )

                evaluation_index += 1
                evaluation_started = (
                    time.monotonic()
                )

                evaluation = run_evaluator(
                    codex,
                    workspace=workspace,
                    task_prompt=prompt,
                    plan=plan,
                    impact_audit=current_impact,
                    checks_summary=(
                        checks_summary(
                            results
                        )
                    ),
                    required_obligation_ids=(
                        required_obligation_ids(
                            plan
                        )
                    ),
                    preflight=preflight,
                    baseline_snapshot=(
                        baseline_snapshot
                    ),
                    explicit_skills=(
                        explicit_skills
                    ),
                    on_heartbeat=make_heartbeat(
                        (
                            "EVALUATION_"
                            f"{evaluation_index}"
                        )
                    ),
                    on_thread_started=(
                        make_thread_started_callback(
                            phase=(
                                "EVALUATOR_"
                                f"{evaluation_index}"
                            ),
                            recorder=recorder,
                        )
                    ),
                )

                phase_done(
                    (
                        "EVALUATION_"
                        f"{evaluation_index}"
                    ),
                    evaluation_started,
                )

                recorder.write_json(
                    (
                        "evaluation_"
                        f"{evaluation_index:02d}.json"
                    ),
                    evaluation,
                )

                print_structured(
                    "EVALUATION RESULT",
                    evaluation,
                )

                try:
                    evaluator_machine_guard(
                        evaluation,
                        plan=plan,
                        impact_audit=current_impact,
                        risk=risk,
                    )
                except RuntimeError as exc:
                    recorder.write_text(
                        (
                            "evaluation_"
                            f"{evaluation_index:02d}_"
                            "validation_error.txt"
                        ),
                        str(exc) + "\n",
                    )
                    print(
                        "HARNESS_TASK_BLOCKED: "
                        "evaluator_artifact_invalid"
                    )
                    print(
                        "EVALUATOR_VALIDATION_FAIL:",
                        exc,
                    )
                    return 2

                status = evaluation[
                    "status"
                ]

                if status == "PASS":
                    if heldout_checks:
                        print(
                            "=== HELD-OUT "
                            "EVALUATION ==="
                        )
                        heldout_started = (
                            time.monotonic()
                        )
                        heldout_results = (
                            run_checks(
                                heldout_checks,
                                workspace=workspace,
                                toolchain=toolchain,
                            )
                        )
                        phase_done(
                            "HELDOUT",
                            heldout_started,
                        )

                        recorder.write_json(
                            "heldout_results.json",
                            [
                                {
                                    "name": item.name,
                                    "returncode": (
                                        item.returncode
                                    ),
                                    "timed_out": (
                                        item.timed_out
                                    ),
                                    "duration_seconds": (
                                        item.duration_seconds
                                    ),
                                }
                                for item
                                in heldout_results
                            ],
                        )

                        heldout_failures = [
                            item
                            for item
                            in heldout_results
                            if not item.passed
                        ]

                        if heldout_failures:
                            print()
                            print(
                                "HARNESS_HELDOUT_FAIL: "
                                "held-out evidence "
                                "failed; no repair "
                                "feedback was sent "
                                "to the agent"
                            )
                            return 4

                        print()
                        print(
                            "HELDOUT_PASS"
                        )

                    try:
                        validate_actual_surface_against_plan(
                            workspace,
                            plan,
                        )
                    except RuntimeError as exc:
                        recorder.write_text(
                            "final_change_surface_error.txt",
                            str(exc) + "\n",
                        )
                        print(
                            "HARNESS_TASK_BLOCKED: "
                            "final_change_surface_mismatch"
                        )
                        print("CHANGE_SURFACE_FAIL:", exc)
                        return 2

                    if not finalize_success_guarded(
                        workspace_session=workspace_session,
                        recorder=recorder,
                    ):
                        return 2
                    print(
                        "HARNESS_TASK_PASS"
                    )
                    return 0

                if (
                    status
                    == "REPLAN_REQUIRED"
                ):
                    if (
                        replan_cycles
                        >= max_replan_cycles
                    ):
                        print(
                            "HARNESS_TASK_FAIL: "
                            "maximum replan cycles "
                            "reached"
                        )
                        return 1

                    replan_cycles += 1

                    print()
                    print(
                        f"=== REPLAN CYCLE "
                        f"{replan_cycles}: "
                        "EVALUATOR REJECTED "
                        "PLAN ==="
                    )

                    revised_plan = (
                        run_validated_planner(
                            codex,
                            workspace=workspace,
                            task_prompt=prompt,
                            toolchain=toolchain,
                            preflight=preflight,
                            baseline_snapshot=(
                                baseline_snapshot
                            ),
                            revision_context=(
                                evaluation
                            ),
                            trusted_benchmark_evidence=trusted_benchmark_evidence,
                            explicit_skills=(
                                explicit_skills
                            ),
                            max_validation_retries=(
                                max_plan_validation_retries
                            ),
                            phase_label=(
                                "REPLAN_"
                                f"{replan_cycles}"
                            ),
                            artifact_prefix=(
                                "replan_"
                                f"{replan_cycles:02d}"
                            ),
                            recorder=recorder,
                        )
                    )

                    if revised_plan is None:
                        print(
                            "HARNESS_TASK_BLOCKED: "
                            "replanner_artifact_invalid"
                        )
                        return 2

                    plan = revised_plan

                    if (
                        plan["status"]
                        == "BLOCKED"
                    ):
                        print(
                            "HARNESS_TASK_BLOCKED: "
                            "replanner"
                        )
                        return 2

                    if (
                        plan["status"]
                        == "NEEDS_USER_DECISION"
                    ):
                        print(
                            "HARNESS_TASK_NEEDS_USER_DECISION: "
                            "replanner"
                        )
                        return 3

                    if (
                        plan["status"]
                        != "READY"
                    ):
                        print(
                            "HARNESS_TASK_BLOCKED: "
                            "unexpected_replanner_status="
                            + str(
                                plan["status"]
                            )
                        )
                        return 2

                    # D-032 guarantees the actual diff was within the previous
                    # planned surface before evaluation. If the revised plan removes
                    # an old changed path, restore it. Newly added plan paths are
                    # therefore still untouched and can receive trusted per-path
                    # pre-edit evidence even though the task already has other edits.
                    revised_planned = planned_candidate_paths(
                        workspace,
                        plan,
                    )
                    obsolete_changed = sorted(
                        collect_changed_paths(workspace) - revised_planned
                    )
                    if obsolete_changed:
                        restore_unplanned_paths_to_baseline(
                            workspace,
                            preflight=preflight,
                            paths=obsolete_changed,
                        )

                    baseline_snapshot = (
                        capture_baseline_snapshot(
                            workspace,
                            preflight=preflight,
                            candidate_paths=[
                                str(path)
                                for path
                                in plan.get(
                                    "candidate_paths",
                                    [],
                                )
                            ],
                            existing_snapshot=(
                                baseline_snapshot
                            ),
                            captured_before_first_edit=False,
                            captured_before_path_edit=True,
                            snapshot_role=(
                                "pre_path_edit_after_evaluator_replan"
                            ),
                        )
                    )

                    recorder.write_json(
                        (
                            "baseline_snapshot_"
                            f"replan_{replan_cycles:02d}.json"
                        ),
                        baseline_snapshot,
                    )

                    print_structured(
                        "UPDATED BASELINE SNAPSHOT",
                        baseline_snapshot,
                    )

                    # First ask a fresh evaluator whether the already-built
                    # candidate satisfies the corrected plan. If not, normal
                    # FINDINGS routing will return concrete candidate work
                    # to the implementer.
                    continue

                if status == "BLOCKED":
                    print(
                        "HARNESS_TASK_BLOCKED: "
                        "evaluator"
                    )
                    return 2

                if (
                    status
                    == "NEEDS_USER_DECISION"
                ):
                    print(
                        "HARNESS_TASK_NEEDS_USER_DECISION: "
                        "evaluator"
                    )
                    return 3

                if status != "FINDINGS":
                    print(
                        "HARNESS_TASK_BLOCKED: "
                        "unexpected_evaluator_status="
                        + str(status)
                    )
                    return 2

                if (
                    repair_cycles
                    >= max_fix_cycles
                ):
                    print(
                        "HARNESS_TASK_FAIL: "
                        "maximum fix cycles "
                        "reached"
                    )
                    return 1

                repair_cycles += 1

                print(
                    f"=== REPAIR CYCLE "
                    f"{repair_cycles}: "
                    "EVALUATOR FINDINGS ==="
                )

                repair_prompt = (
                    build_evaluator_repair_prompt(
                        evaluation,
                        plan=plan,
                        impact_audit=current_impact,
                        baseline_snapshot=baseline_snapshot,
                    )
                )

                repair_started = (
                    time.monotonic()
                )

                codex.run_turn(
                    thread_id=thread_id,
                    prompt=repair_prompt,
                    on_delta=(
                        stream_agent_delta
                    ),
                    on_message_end=(
                        stream_agent_message_end
                    ),
                    skills=explicit_skills,
                )

                phase_done(
                    (
                        "REPAIR_EVALUATOR_"
                        f"{repair_cycles}"
                    ),
                    repair_started,
                )

    finally:
        if (
            workspace_session is not None
            and workspace_session.managed
        ):
            if workspace_session.workspace.exists():
                print()
                print(
                    "MANAGED_WORKTREE_ON_EXIT:",
                    workspace_session.workspace,
                )
            else:
                print()
                print(
                    "MANAGED_WORKTREE_MISSING_ON_EXIT:",
                    workspace_session.workspace,
                )

        print()
        print(
            "TOTAL_ELAPSED:",
            format_duration(
                time.monotonic()
                - task_started
            ),
        )


if __name__ == "__main__":
    try:
        raise SystemExit(
            main()
        )
    except SystemExit:
        raise
    except Exception as exc:
        print()
        print(
            "HARNESS_INTERNAL_ERROR:",
            f"{type(exc).__name__}: {exc}",
        )

        if (
            os.environ.get(
                "SLIVIN_HARNESS_DEBUG"
            )
            == "1"
        ):
            raise

        raise SystemExit(99)
