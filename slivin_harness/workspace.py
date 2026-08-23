from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


WORKTREE_EXCLUDES = [
    ".harness_tmp/",
    ".harness_git_excludes",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".jest-cache*",
    "coverage/",
]


@dataclass
class WorkspaceSession:
    workspace: Path
    mode: str
    managed: bool
    project_name: str | None = None
    source_repo: Path | None = None
    source_head: str | None = None
    base_sha: str | None = None
    result_mode: str = "keep_worktree"
    exposed_paths: tuple[str, ...] = ()


def _run_git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}:\n{result.stderr.strip()}"
        )
    return result


def _safe_relative_path(raw: str) -> Path:
    rel = Path(raw.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(
            f"Workspace exposure path must be repo-relative: {raw}"
        )
    return rel


def _format_path(
    raw: str | Path,
    *,
    harness_root: Path,
    project_root: Path | None = None,
) -> Path:
    value = os.path.expandvars(str(raw))
    value = value.format(
        home=str(Path.home()),
        harness_root=str(harness_root),
        project_root=str(project_root) if project_root else "",
    )
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = harness_root / path
    return path.resolve()


def _assert_git_repo(path: Path) -> None:
    probe = _run_git(path, "rev-parse", "--is-inside-work-tree", check=False)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise RuntimeError(f"Project is not a Git repository: {path}")


def _status_porcelain(path: Path) -> str:
    return _run_git(
        path,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).stdout


def _status_excluding_allowed_untracked(
    status: str,
    allowed_paths: list[str] | tuple[str, ...],
) -> str:
    allowed = [
        _safe_relative_path(str(item)).as_posix().rstrip("/")
        for item in allowed_paths
    ]
    kept: list[str] = []

    for line in status.splitlines():
        if not line.startswith("?? "):
            kept.append(line)
            continue

        raw_path = line[3:].replace("\\", "/")
        if any(
            raw_path == item or raw_path.startswith(item + "/")
            for item in allowed
        ):
            continue
        kept.append(line)

    return "\n".join(kept) + ("\n" if kept else "")


def _make_worktree_excludes(
    workspace: Path,
    exposed_paths: list[str],
) -> Path:
    excludes_path = workspace / ".harness_git_excludes"
    patterns = list(WORKTREE_EXCLUDES)

    for raw in exposed_paths:
        rel = _safe_relative_path(raw).as_posix()
        source = workspace / rel
        patterns.append(rel + "/" if source.is_dir() else rel)

    deduped: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        if pattern not in seen:
            seen.add(pattern)
            deduped.append(pattern)

    excludes_path.write_text(
        "\n".join(deduped) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return excludes_path


def _copy_exposed_paths(
    source_repo: Path,
    workspace: Path,
    raw_paths: list[str],
) -> tuple[str, ...]:
    copied: list[str] = []

    for raw in raw_paths:
        rel = _safe_relative_path(raw)
        source = source_repo / rel
        target = workspace / rel

        if not source.exists():
            print(f"WORKSPACE_EXPOSE_MISSING: {rel.as_posix()}")
            continue

        if _run_git(
            source_repo,
            "ls-files",
            "--error-unmatch",
            "--",
            rel.as_posix(),
            check=False,
        ).returncode == 0:
            # Tracked content already exists in the worktree.
            print(f"WORKSPACE_EXPOSE_TRACKED_SKIP: {rel.as_posix()}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)

        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target)

        copied.append(rel.as_posix())
        print(f"WORKSPACE_EXPOSE_COPIED: {rel.as_posix()}")

    return tuple(copied)


def prepare_workspace_session(
    *,
    manifest: dict,
    local_config: dict,
    harness_root: Path,
    task_id: str,
) -> WorkspaceSession:
    """Resolve a static workspace or create an isolated Git worktree.

    Backward-compatible static mode:
        workspace = "cases/.../workspace"

    Project mode:
        project = "name"
        workspace_mode = "git_worktree"   # optional, default for project mode

    Machine-specific source paths live under [projects.<name>] in
    harness.local.toml and are not committed to task manifests.
    """
    raw_workspace = manifest.get("workspace")
    project_name = manifest.get("project")

    if raw_workspace:
        workspace = _format_path(
            str(raw_workspace),
            harness_root=harness_root,
        )
        return WorkspaceSession(
            workspace=workspace,
            mode="static",
            managed=False,
            result_mode="keep_worktree",
        )

    if not project_name:
        raise RuntimeError(
            "Task manifest must define either 'workspace' (static mode) "
            "or 'project' (managed Git worktree mode)."
        )

    mode = str(manifest.get("workspace_mode", "git_worktree")).lower()
    if mode != "git_worktree":
        raise RuntimeError(
            f"Unsupported workspace_mode for project task: {mode}. "
            "Supported: git_worktree"
        )

    projects = local_config.get("projects", {})
    project_cfg = projects.get(str(project_name))
    if not isinstance(project_cfg, dict):
        raise RuntimeError(
            f"Project profile '{project_name}' is missing from harness.local.toml. "
            f"Add [projects.{project_name}] with repo=..."
        )

    raw_repo = project_cfg.get("repo")
    if not raw_repo:
        raise RuntimeError(
            f"[projects.{project_name}] requires repo=... in harness.local.toml"
        )

    source_repo = _format_path(
        str(raw_repo),
        harness_root=harness_root,
    )
    if not source_repo.exists():
        raise RuntimeError(f"Project repository does not exist: {source_repo}")
    _assert_git_repo(source_repo)

    workspace_cfg = project_cfg.get("workspace", {})
    raw_exposed = workspace_cfg.get("copy_untracked", [])
    if not isinstance(raw_exposed, list):
        raise RuntimeError(
            f"[projects.{project_name}.workspace].copy_untracked must be an array"
        )
    configured_exposed = [str(item) for item in raw_exposed]

    require_clean_source = bool(project_cfg.get("require_clean_source", True))
    source_status = _status_porcelain(source_repo)
    effective_source_status = _status_excluding_allowed_untracked(
        source_status,
        configured_exposed,
    )
    if require_clean_source and effective_source_status.strip():
        raise RuntimeError(
            "Source project repository is not clean. Harness worktree mode "
            "requires a known committed source baseline by default. Configured "
            "copy_untracked paths are allowed and ignored by this gate.\n\n"
            + effective_source_status
        )

    source_head = _run_git(source_repo, "rev-parse", "HEAD").stdout.strip()
    base_ref = str(manifest.get("base_ref", project_cfg.get("base_ref", "HEAD")))
    base_sha = _run_git(source_repo, "rev-parse", base_ref).stdout.strip()

    result_mode = str(
        manifest.get(
            "result_mode",
            project_cfg.get("result_mode", "keep_worktree"),
        )
    ).lower()
    if result_mode not in {"keep_worktree", "apply_to_source"}:
        raise RuntimeError(
            f"Unsupported result_mode: {result_mode}. "
            "Supported: keep_worktree, apply_to_source"
        )

    if result_mode == "apply_to_source" and base_sha != source_head:
        raise RuntimeError(
            "result_mode=apply_to_source requires base_ref to resolve to the "
            "source working tree HEAD. Use base_ref=HEAD or keep_worktree."
        )

    raw_root = (
        local_config.get("workspace", {}).get("root")
        or str(harness_root / ".workspaces")
    )
    workspace_root = _format_path(
        str(raw_root),
        harness_root=harness_root,
        project_root=source_repo,
    )

    safe_project = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in str(project_name)
    ) or "project"
    safe_task = "".join(
        ch if ch.isalnum() or ch in "-_" else "_"
        for ch in task_id
    ) or "task"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    workspace = (
        workspace_root
        / safe_project
        / safe_task
        / f"{stamp}-{suffix}"
    ).resolve()
    workspace.parent.mkdir(parents=True, exist_ok=True)

    # Required by Git for worktree-specific config such as core.excludesFile.
    _run_git(source_repo, "config", "extensions.worktreeConfig", "true")
    _run_git(
        source_repo,
        "worktree",
        "add",
        "--detach",
        str(workspace),
        base_sha,
    )

    copied = _copy_exposed_paths(
        source_repo,
        workspace,
        configured_exposed,
    )

    excludes_path = _make_worktree_excludes(
        workspace,
        list(copied),
    )
    _run_git(
        workspace,
        "config",
        "--worktree",
        "core.excludesFile",
        str(excludes_path),
    )

    status = _status_porcelain(workspace)
    if status.strip():
        raise RuntimeError(
            "Managed worktree is unexpectedly dirty after preparation:\n" + status
        )

    return WorkspaceSession(
        workspace=workspace,
        mode="git_worktree",
        managed=True,
        project_name=str(project_name),
        source_repo=source_repo,
        source_head=source_head,
        base_sha=base_sha,
        result_mode=result_mode,
        exposed_paths=copied,
    )


def _collect_untracked(repo: Path) -> list[str]:
    output = _run_git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    ).stdout
    return [item for item in output.split("\0") if item]


def build_candidate_patch(session: WorkspaceSession) -> bytes:
    """Build a binary Git patch including untracked, non-ignored files."""
    repo = session.workspace
    untracked = _collect_untracked(repo)

    try:
        if untracked:
            _run_git(repo, "add", "-N", "--", *untracked)

        result = subprocess.run(
            ["git", "diff", "--binary", "--full-index", "HEAD", "--"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Failed to build candidate patch:\n"
                + result.stderr.decode("utf-8", errors="replace")
            )
        return result.stdout
    finally:
        if untracked:
            _run_git(repo, "reset", "--", *untracked)


def apply_candidate_to_source(
    session: WorkspaceSession,
    *,
    patch: bytes,
) -> None:
    if session.result_mode != "apply_to_source":
        return
    if session.source_repo is None or session.source_head is None:
        raise RuntimeError("Managed source repository metadata is missing")

    source_repo = session.source_repo
    current_head = _run_git(source_repo, "rev-parse", "HEAD").stdout.strip()
    if current_head != session.source_head:
        raise RuntimeError(
            "Source repository HEAD changed while Harness was running. "
            "Candidate was NOT applied.\n"
            f"start={session.source_head}\ncurrent={current_head}"
        )

    source_status = _status_porcelain(source_repo)
    effective_source_status = _status_excluding_allowed_untracked(
        source_status,
        session.exposed_paths,
    )
    if effective_source_status.strip():
        raise RuntimeError(
            "Source repository became dirty while Harness was running. "
            "Candidate was NOT applied. Configured exposed untracked paths are "
            "ignored by this gate.\n\n" + effective_source_status
        )

    if not patch:
        print("RESULT_APPLY: no candidate diff")
        return

    result = subprocess.run(
        ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
        cwd=source_repo,
        input=patch,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Candidate passed Harness but could not be applied to source repository.\n"
            + result.stderr.decode("utf-8", errors="replace")
        )

    print(f"RESULT_APPLIED_TO_SOURCE: {source_repo}")
