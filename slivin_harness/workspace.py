from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath

from slivin_harness.git_integrity import (
    CandidateWorkspaceBaseline,
    candidate_baseline_for,
    clear_candidate_baseline,
)

WORKTREE_EXCLUDES = [
    ".harness_tmp/",
    ".venv/",
    ".harness_git_excludes",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".jest-cache*",
    "coverage/",
]
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.prod",
    ".env.production",
    "credentials.json",
    "service-account.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


@dataclass(frozen=True)
class RuntimeProjection:
    """Controller-authoritative record of an independently copied runtime root.

    This record exists only for a directory explicitly selected by the local
    project profile.  Its destination is not inferred from a coincidental
    workspace path: Phase 7 uses this provenance before it can rebind a
    source-owned toolchain path in a historical benchmark.
    """

    relative_path: str
    source_kind: str
    destination: Path
    is_directory: bool
    copy_mode: str
    runtime_only: bool


@dataclass
class WorkspaceSession:
    workspace: Path
    mode: str
    managed: bool
    project_name: str | None = None
    source_repo: Path | None = None
    source_head: str | None = None
    source_base_sha: str | None = None
    base_sha: str | None = None
    result_mode: str = "keep_worktree"
    exposed_paths: tuple[str, ...] = ()
    runtime_projections: tuple[RuntimeProjection, ...] = ()
    benchmark_isolated: bool = False


def _run_git(
    repo: Path,
    *args: str,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=input_bytes is None,
        encoding="utf-8" if input_bytes is None else None,
        errors="replace" if input_bytes is None else None,
    )
    if check and result.returncode != 0:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}:\n{str(stderr).strip()}"
        )
    return result


def _safe_path_segment(raw: str, *, fallback: str, max_length: int = 40) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(raw))
    safe = safe.strip("._") or fallback
    if len(safe) <= max_length:
        return safe
    digest = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:8]
    return f"{safe[: max(1, max_length - 9)]}-{digest}"


def _safe_relative_path(raw: str) -> Path:
    value = str(raw)
    normalized = value.replace("\\", "/")
    windows = PureWindowsPath(value)
    rel = Path(normalized)
    if (
        not value.strip()
        or "\x00" in value
        or normalized.startswith("/")
        or bool(windows.drive or windows.root)
        or rel.is_absolute()
        or ".." in rel.parts
        or str(rel) in {"", "."}
    ):
        raise RuntimeError(f"Workspace exposure path must be repo-relative: {raw}")
    return rel


def normalize_copy_untracked_paths(raw_paths: list[str]) -> tuple[str, ...]:
    """Validate projection roots and reject order-dependent overlaps.

    Windows workspaces are case-insensitive in the supported deployment model,
    so equality and parent/child comparisons deliberately use ``casefold`` on
    every normalized path component on every host.
    """

    normalized = tuple(_safe_relative_path(item).as_posix() for item in raw_paths)
    keyed = [
        (value, tuple(part.casefold() for part in Path(value).parts))
        for value in normalized
    ]
    conflicts: set[str] = set()
    for index, (left, left_parts) in enumerate(keyed):
        for right, right_parts in keyed[index + 1 :]:
            shortest = min(len(left_parts), len(right_parts))
            if left_parts[:shortest] == right_parts[:shortest]:
                conflicts.update((left, right))
    if conflicts:
        raise RuntimeError(
            "workspace.copy_untracked contains duplicate or overlapping paths: "
            + ", ".join(sorted(conflicts, key=str.casefold))
        )
    return normalized


def _format_path(
    raw: str | Path,
    *,
    harness_root: Path,
    project_root: Path | None = None,
) -> Path:
    value = os.path.expandvars(str(raw)).format(
        home=str(Path.home()),
        harness_root=str(harness_root),
        project_root=str(project_root) if project_root else "",
    )
    path = Path(value).expanduser()
    return (path if path.is_absolute() else harness_root / path).resolve()


def _assert_git_repo(path: Path) -> None:
    probe = _run_git(path, "rev-parse", "--is-inside-work-tree", check=False)
    if probe.returncode != 0 or str(probe.stdout).strip() != "true":
        raise RuntimeError(f"Project is not a Git repository: {path}")


def _status_porcelain(path: Path) -> str:
    return str(
        _run_git(
            path,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ).stdout
    )


def _status_excluding_allowed_untracked(
    status_text: str,
    allowed_paths: list[str] | tuple[str, ...],
) -> str:
    allowed = [_safe_relative_path(item).as_posix().rstrip("/") for item in allowed_paths]
    kept: list[str] = []
    for line in status_text.splitlines():
        if not line.startswith("?? "):
            kept.append(line)
            continue
        raw_path = line[3:].replace("\\", "/")
        if any(raw_path == item or raw_path.startswith(item + "/") for item in allowed):
            continue
        kept.append(line)
    return "\n".join(kept) + ("\n" if kept else "")


def _is_sensitive(rel: Path) -> bool:
    name = rel.name.lower()
    return (
        name in SENSITIVE_NAMES
        or (name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"})
        or rel.suffix.lower() in SENSITIVE_SUFFIXES
    )


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attrs = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attrs & flag)



def _canonical_is_within(root: Path, candidate: Path) -> bool:
    try:
        root_resolved = root.resolve(strict=False)
        candidate_resolved = candidate.resolve(strict=False)
        common = os.path.commonpath([str(root_resolved), str(candidate_resolved)])
        return os.path.normcase(common) == os.path.normcase(str(root_resolved))
    except (OSError, RuntimeError, ValueError):
        return False


def assert_safe_runtime_path(
    root: Path,
    path: Path,
    *,
    include_leaf: bool = True,
) -> None:
    """Reject path escape through a symlink/junction in any ancestor.

    Checking only the leaf is insufficient: ``ignored/link/.env`` can look like
    a regular file while ``link`` redirects outside the repository.  Exposure
    and restore operations must validate every existing component before they
    read or write through it.
    """

    raw_root = Path(root)
    raw_path = Path(path)
    root = raw_root.resolve(strict=False)
    try:
        # Preserve the caller's path components when it supplied a root/path
        # pair using the same spelling: this detects an alias that resolves
        # *inside* the root as well as one that escapes it.  Native Windows may
        # spell an equivalent root through its 8.3 alias, so fall back to a
        # canonical relative path only when lexical derivation is impossible.
        rel = raw_path.absolute().relative_to(raw_root.absolute())
        traversal_root = raw_root
    except ValueError:
        candidate = raw_path.resolve(strict=False)
        if not _canonical_is_within(root, candidate):
            raise RuntimeError(f"Runtime path is outside its root: {path}")
        try:
            rel = Path(os.path.relpath(str(candidate), str(root)))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Runtime path is outside its root: {path}") from exc
        traversal_root = root
    if ".." in rel.parts:
        raise RuntimeError(f"Runtime path is outside its root: {path}")
    parts = rel.parts if include_leaf else rel.parts[:-1]
    current = traversal_root
    for part in parts:
        current = current / part
        if current.is_symlink() or _is_reparse_point(current):
            raise RuntimeError(
                "Refusing runtime path through symlink/junction/reparse point: "
                + str(current)
            )
    candidate = raw_path.resolve(strict=False)
    probe = path if include_leaf else path.parent
    if not _canonical_is_within(root, probe):
        raise RuntimeError(f"Runtime path escaped its root after resolution: {path}")

def _assert_regular_tree(path: Path) -> None:
    candidates = [path]
    if path.is_dir():
        candidates.extend(path.rglob("*"))
    for item in candidates:
        if item.is_symlink() or _is_reparse_point(item):
            raise RuntimeError(
                "Refusing to expose symlink/junction/reparse point to the agent: "
                + str(item)
            )
        if item.exists() and not (item.is_file() or item.is_dir()):
            raise RuntimeError(f"Unsupported exposed filesystem object: {item}")


def _assert_independent_copy(source: Path, target: Path, *, relative: Path) -> None:
    """Verify the completed copy did not retain any source hardlink aliases."""

    try:
        if os.path.samefile(source, target):
            raise RuntimeError(
                "Refusing runtime projection that aliases the source path: "
                + relative.as_posix()
            )
        if not source.is_dir():
            return
        for source_item in source.rglob("*"):
            if not source_item.is_file():
                continue
            target_item = target / source_item.relative_to(source)
            if not target_item.is_file() or os.path.samefile(source_item, target_item):
                raise RuntimeError(
                    "Refusing runtime projection with source hardlink alias: "
                    + relative.as_posix()
                )
    except OSError as exc:
        raise RuntimeError(
            "Unable to verify independent runtime copy: " + relative.as_posix()
        ) from exc


def _make_worktree_excludes(workspace: Path, exposed_paths: list[str]) -> Path:
    path = workspace / ".harness_git_excludes"
    patterns = list(WORKTREE_EXCLUDES)
    for raw in exposed_paths:
        rel = _safe_relative_path(raw).as_posix()
        patterns.append(rel + "/" if (workspace / rel).is_dir() else rel)
    path.write_text("\n".join(dict.fromkeys(patterns)) + "\n", encoding="utf-8", newline="\n")
    return path


def add_worktree_excludes(workspace: Path, raw_paths: list[str]) -> Path:
    """Extend the managed worktree exclude file with runtime-owned paths."""

    path = workspace / ".harness_git_excludes"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    patterns = list(existing)
    for raw in raw_paths:
        rel = _safe_relative_path(raw).as_posix().rstrip("/")
        patterns.append(rel + "/")
    path.write_text(
        "\n".join(dict.fromkeys(item for item in patterns if item)) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _sensitive_paths_in_tree(source_repo: Path, source: Path) -> list[Path]:
    candidates = [source]
    if source.is_dir():
        candidates.extend(source.rglob("*"))
    sensitive: list[Path] = []
    for item in candidates:
        try:
            rel = item.relative_to(source_repo)
        except ValueError:
            continue
        if _is_sensitive(rel):
            sensitive.append(rel)
    return sensitive


def _copy_exposed_paths(
    source_repo: Path,
    workspace: Path,
    raw_paths: list[str],
    *,
    allow_sensitive_copy: bool,
    missing_is_error: bool = False,
) -> tuple[str, ...]:
    copied: list[str] = []
    for raw in raw_paths:
        rel = _safe_relative_path(raw)
        source = source_repo / rel
        target = workspace / rel
        if not source.exists() and not source.is_symlink():
            if missing_is_error:
                raise RuntimeError(
                    "Configured workspace runtime path does not exist: " + rel.as_posix()
                )
            print("WORKSPACE_EXPOSE_MISSING:", rel.as_posix())
            continue
        assert_safe_runtime_path(source_repo, source, include_leaf=True)
        assert_safe_runtime_path(workspace, target, include_leaf=False)
        if _run_git(
            source_repo,
            "ls-files",
            "--error-unmatch",
            "--",
            rel.as_posix(),
            check=False,
        ).returncode == 0:
            print("WORKSPACE_EXPOSE_TRACKED_SKIP:", rel.as_posix())
            continue
        _assert_regular_tree(source)
        sensitive_paths = _sensitive_paths_in_tree(source_repo, source)
        if sensitive_paths and not allow_sensitive_copy:
            examples = ", ".join(path.as_posix() for path in sensitive_paths[:5])
            suffix = " ..." if len(sensitive_paths) > 5 else ""
            raise RuntimeError(
                "Sensitive path requires allow_sensitive_copy=true: "
                + examples
                + suffix
            )
        if target.exists():
            print("WORKSPACE_EXPOSE_EXISTS_SKIP:", rel.as_posix())
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, symlinks=False)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
        _assert_regular_tree(target)
        assert_safe_runtime_path(workspace, target, include_leaf=True)
        _assert_independent_copy(source, target, relative=rel)
        copied.append(rel.as_posix())
        print("WORKSPACE_EXPOSE_COPIED:", rel.as_posix())
    return tuple(copied)



def _worktreeinclude_paths(source_repo: Path) -> tuple[str, ...]:
    """Enumerate ignored regular files selected by repository .worktreeinclude.

    The file is an owner-controlled repository policy.  It can authorize sensitive
    ignored files such as .env without a second task-local opt-in, but it can never
    expose tracked files, symlinks/junctions, or paths outside the repository.
    """

    include = source_repo / ".worktreeinclude"
    if not include.is_file():
        return ()
    result = _run_git(
        source_repo,
        "ls-files",
        "--others",
        "--ignored",
        "-z",
        f"--exclude-from={include}",
        "--",
    )
    values = [item for item in str(result.stdout).split("\0") if item]
    selected: list[str] = []
    for raw in values:
        rel = _safe_relative_path(raw)
        if rel.parts and rel.parts[0].casefold() == "node_modules":
            raise RuntimeError(
                ".worktreeinclude must not expose node_modules; use the local "
                "workspace.copy_untracked runtime projection instead"
            )
        source = source_repo / rel
        if not source.exists() and not source.is_symlink():
            continue
        assert_safe_runtime_path(source_repo, source, include_leaf=True)
        if _run_git(
            source_repo,
            "ls-files",
            "--error-unmatch",
            "--",
            rel.as_posix(),
            check=False,
        ).returncode == 0:
            continue
        if _run_git(
            source_repo,
            "check-ignore",
            "-q",
            "--",
            rel.as_posix(),
            check=False,
        ).returncode != 0:
            raise RuntimeError(
                ".worktreeinclude may expose only repository-ignored paths: "
                + rel.as_posix()
            )
        _assert_regular_tree(source)
        if source.is_dir():
            # ls-files normally returns files, but fail closed if Git behavior or
            # a future pattern yields a directory object.
            raise RuntimeError(
                ".worktreeinclude must resolve to regular files, not directories: "
                + rel.as_posix()
            )
        selected.append(rel.as_posix())
    return tuple(sorted(dict.fromkeys(selected)))

def _remove_worktree(source_repo: Path, workspace: Path) -> None:
    _run_git(source_repo, "worktree", "remove", "--force", str(workspace), check=False)
    _run_git(source_repo, "worktree", "prune", check=False)
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)


def _is_historical_manifest(manifest: dict) -> bool:
    if manifest.get("benchmark"):
        return True
    checks = manifest.get("checks", [])
    return any(
        isinstance(item, dict) and item.get("feedback") == "heldout"
        for item in checks
    )


def _tree_entries(repo: Path, treeish: str) -> tuple[tuple[str, str, str, str], ...]:
    raw = str(
        _run_git(
            repo,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            treeish,
        ).stdout
    )
    entries: list[tuple[str, str, str, str]] = []
    for item in raw.split("\0"):
        if not item:
            continue
        header, path = item.split("\t", 1)
        mode, object_type, object_id = header.split(" ", 2)
        entries.append((mode, object_type, object_id, path))
    return tuple(entries)


def _materialize_sanitized_benchmark_repo(
    *,
    source_repo: Path,
    source_base_sha: str,
    workspace: Path,
) -> str:
    """Create a standalone one-commit repository containing only the baseline tree.

    A linked worktree shares the source repository's object database and refs.  That
    is convenient for production work but invalid for a hidden historical exam: an
    agent could inspect unrelated refs or unreachable objects containing an earlier
    solution.  The sanitized repository copies only the blobs referenced by the
    selected baseline tree and creates a new detached single commit.
    """

    workspace.mkdir(parents=True, exist_ok=False)
    _run_git(workspace, "init")
    _run_git(workspace, "config", "core.autocrlf", "false")
    _run_git(workspace, "config", "core.longpaths", "true", check=False)
    _run_git(workspace, "config", "gc.auto", "0")

    source_entries = _tree_entries(source_repo, source_base_sha)
    for mode, object_type, object_id, rel in source_entries:
        safe = _safe_relative_path(rel)
        if safe.as_posix() != rel.replace("\\", "/"):
            raise RuntimeError(f"Unsafe benchmark tree path: {rel!r}")
        if object_type == "commit" or mode == "160000":
            raise RuntimeError(
                "Historical benchmark isolation does not expose submodules; "
                f"materialize the fixture first: {rel}"
            )
        if object_type != "blob":
            raise RuntimeError(f"Unsupported benchmark tree object {object_type}: {rel}")
        if mode == "120000" and os.name == "nt":
            raise RuntimeError(
                "Historical benchmark baseline contains a symlink that cannot be "
                "safely materialized on native Windows: " + rel
            )
        blob = _run_git(source_repo, "cat-file", "blob", object_id, input_bytes=b"").stdout
        if not isinstance(blob, bytes):
            raise RuntimeError("Expected binary blob output while sanitizing benchmark")
        written = _run_git(workspace, "hash-object", "-w", "--stdin", input_bytes=blob).stdout
        written_id = written.decode("ascii", errors="strict").strip()
        if written_id != object_id:
            raise RuntimeError(f"Benchmark blob identity changed while copying {rel}")
        _run_git(
            workspace,
            "update-index",
            "--add",
            "--cacheinfo",
            f"{mode},{object_id},{rel}",
        )

    tree_sha = str(_run_git(workspace, "write-tree").stdout).strip()
    commit_sha = str(
        _run_git(
            workspace,
            "-c",
            "user.name=Slivin Harness",
            "-c",
            "user.email=slivin-harness@example.invalid",
            "commit-tree",
            tree_sha,
            "-m",
            "Sanitized historical benchmark baseline",
        ).stdout
    ).strip()
    _run_git(workspace, "checkout", "--detach", "-f", commit_sha)
    _run_git(workspace, "update-ref", "-d", "refs/heads/master", check=False)
    _run_git(workspace, "update-ref", "-d", "refs/heads/main", check=False)
    _run_git(
        workspace,
        "update-ref",
        "-d",
        "refs/heads/slivin-benchmark-baseline",
        check=False,
    )

    sanitized_entries = _tree_entries(workspace, commit_sha)
    if sanitized_entries != source_entries:
        raise RuntimeError("Sanitized benchmark repository tree differs from source baseline")
    refs = str(_run_git(workspace, "for-each-ref", "--format=%(refname)").stdout).strip()
    if refs:
        raise RuntimeError("Sanitized benchmark repository unexpectedly retains refs: " + refs)
    return commit_sha


def prepare_workspace_session(
    *,
    manifest: dict,
    local_config: dict,
    harness_root: Path,
    task_id: str,
) -> WorkspaceSession:
    raw_workspace = manifest.get("workspace")
    project_name = manifest.get("project")
    if raw_workspace:
        workspace = _format_path(str(raw_workspace), harness_root=harness_root)
        return WorkspaceSession(
            workspace=workspace,
            mode="static",
            managed=False,
            result_mode="keep_worktree",
        )
    if not project_name:
        raise RuntimeError("Task manifest requires either workspace=... or project=...")
    if str(manifest.get("workspace_mode", "git_worktree")).lower() != "git_worktree":
        raise RuntimeError("Only workspace_mode=git_worktree is supported for project tasks")

    project_cfg = local_config.get("projects", {}).get(str(project_name))
    if not isinstance(project_cfg, dict):
        raise RuntimeError(
            f"Project profile '{project_name}' is missing from harness.local.toml"
        )
    raw_repo = project_cfg.get("repo")
    if not raw_repo:
        raise RuntimeError(f"[projects.{project_name}] requires repo=...")
    source_repo = _format_path(str(raw_repo), harness_root=harness_root)
    if not source_repo.exists():
        raise RuntimeError(f"Project repository does not exist: {source_repo}")
    _assert_git_repo(source_repo)

    workspace_cfg = project_cfg.get("workspace", {})
    if not isinstance(workspace_cfg, dict):
        raise RuntimeError(f"[projects.{project_name}.workspace] must be a table")
    raw_exposed = workspace_cfg.get("copy_untracked", [])
    if not isinstance(raw_exposed, list) or not all(isinstance(item, str) for item in raw_exposed):
        raise RuntimeError("copy_untracked must be an array of strings")
    configured_relative = list(normalize_copy_untracked_paths(raw_exposed))
    configured_exposed = list(configured_relative)
    allow_sensitive_copy = bool(workspace_cfg.get("allow_sensitive_copy", False))
    repository_included = list(_worktreeinclude_paths(source_repo))

    source_status = _status_porcelain(source_repo)
    effective_status = _status_excluding_allowed_untracked(
        source_status, [*configured_exposed, *repository_included]
    )
    if bool(project_cfg.get("require_clean_source", True)) and effective_status.strip():
        raise RuntimeError(
            "Source repository is not clean. Configured exposed untracked paths are "
            "ignored by this gate.\n\n" + effective_status
        )

    source_head = str(_run_git(source_repo, "rev-parse", "HEAD").stdout).strip()
    base_ref = str(manifest.get("base_ref", project_cfg.get("base_ref", "HEAD")))
    base_sha = str(_run_git(source_repo, "rev-parse", base_ref).stdout).strip()
    result_mode = str(
        manifest.get("result_mode", project_cfg.get("result_mode", "keep_worktree"))
    ).lower()
    if result_mode not in {"keep_worktree", "apply_to_source"}:
        raise RuntimeError(f"Unsupported result_mode: {result_mode}")
    historical = _is_historical_manifest(manifest)
    if historical and result_mode != "keep_worktree":
        raise RuntimeError("Historical benchmark requires result_mode=keep_worktree")
    if result_mode == "apply_to_source" and base_sha != source_head:
        raise RuntimeError("apply_to_source requires base_ref to resolve to source HEAD")

    raw_root = local_config.get("workspace", {}).get("root") or str(harness_root / ".workspaces")
    root = _format_path(str(raw_root), harness_root=harness_root, project_root=source_repo)
    workspace = (
        root
        / _safe_path_segment(str(project_name), fallback="project", max_length=32)
        / _safe_path_segment(task_id, fallback="task", max_length=40)
        / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    ).resolve()
    workspace.parent.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        _run_git(source_repo, "config", "core.longpaths", "true")
    benchmark_isolated = False
    workspace_base_sha = base_sha
    if historical:
        workspace_base_sha = _materialize_sanitized_benchmark_repo(
            source_repo=source_repo,
            source_base_sha=base_sha,
            workspace=workspace,
        )
        benchmark_isolated = True
    else:
        _run_git(source_repo, "config", "extensions.worktreeConfig", "true")
        _run_git(source_repo, "worktree", "add", "--detach", str(workspace), base_sha)
    try:
        canonical_copied = _copy_exposed_paths(
            source_repo,
            workspace,
            repository_included,
            allow_sensitive_copy=True,
        )
        manual_copied = _copy_exposed_paths(
            source_repo,
            workspace,
            [
                item
                for item, rel in zip(configured_exposed, configured_relative)
                if rel not in set(canonical_copied)
            ],
            allow_sensitive_copy=allow_sensitive_copy,
            missing_is_error=True,
        )
        copied = tuple(dict.fromkeys([*canonical_copied, *manual_copied]))
        runtime_projections = tuple(
            RuntimeProjection(
                relative_path=rel,
                source_kind="workspace.copy_untracked",
                destination=workspace / rel,
                is_directory=True,
                copy_mode="physical_copy",
                runtime_only=True,
            )
            for rel in manual_copied
            if (workspace / rel).is_dir()
        )
        excludes = _make_worktree_excludes(workspace, list(copied))
        _run_git(workspace, "config", "--worktree", "core.excludesFile", str(excludes))
        status_text = _status_porcelain(workspace)
        if status_text.strip():
            raise RuntimeError(
                "Managed worktree is unexpectedly dirty after preparation:\n" + status_text
            )
    except Exception:
        if benchmark_isolated:
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            _remove_worktree(source_repo, workspace)
        raise

    session = WorkspaceSession(
        workspace=workspace,
        mode="benchmark_isolated" if benchmark_isolated else "git_worktree",
        managed=True,
        project_name=str(project_name),
        source_repo=source_repo,
        source_head=source_head,
        source_base_sha=base_sha,
        base_sha=workspace_base_sha,
        result_mode=result_mode,
        exposed_paths=copied,
        runtime_projections=runtime_projections,
        benchmark_isolated=benchmark_isolated,
    )
    try:
        CandidateWorkspaceBaseline.capture(
            workspace,
            baseline_sha=workspace_base_sha,
            excluded_prefixes=(
                ".git",
                ".harness_tmp",
                ".venv",
                ".harness_git_excludes",
                "__pycache__",
                "**/__pycache__",
                "*.py[cod]",
                "**/*.py[cod]",
                ".pytest_cache",
                "**/.pytest_cache",
                *copied,
                *(item.relative_path for item in runtime_projections),
            ),
        )
    except Exception:
        if benchmark_isolated:
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            _remove_worktree(source_repo, workspace)
        raise
    return session


def remove_managed_workspace(session: WorkspaceSession) -> None:
    if not session.managed:
        return
    clear_candidate_baseline(session.workspace)
    if session.benchmark_isolated:
        shutil.rmtree(session.workspace, ignore_errors=True)
    elif session.source_repo is not None:
        _remove_worktree(session.source_repo, session.workspace)


def _collect_untracked(repo: Path) -> list[str]:
    output = str(
        _run_git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout
    )
    return [item for item in output.split("\0") if item]


def _build_patch(repo: Path, *, scratch_root: Path | None = None) -> bytes:
    baseline = candidate_baseline_for(repo)
    if baseline is None:
        head = str(_run_git(repo, "rev-parse", "HEAD").stdout).strip()
        baseline = CandidateWorkspaceBaseline.from_git_tree(
            repo,
            baseline_sha=head,
            excluded_prefixes=(
                ".git",
                ".harness_tmp",
                ".venv",
                ".harness_git_excludes",
                "__pycache__",
                "**/__pycache__",
                "*.py[cod]",
                "**/*.py[cod]",
                ".pytest_cache",
                "**/.pytest_cache",
            ),
        )

    parent = Path(scratch_root) if scratch_root is not None else None
    # Keep the Controller-private temporary components short.  The scratch root
    # can already be deeply nested (notably in a gitless release archive), and
    # verbose UUID-bearing components can otherwise cross the legacy Windows
    # MAX_PATH boundary before Git sees the isolated index.
    temp_name = f"i-{uuid.uuid4().hex[:12]}"
    default_parent = Path(tempfile.gettempdir()) / "sh-idx"
    temp_parent = parent or default_parent
    if os.name == "nt" and len(str(temp_parent / temp_name / "index.lock")) >= 220:
        temp_parent = default_parent
    temp_parent.mkdir(parents=True, exist_ok=True)
    temp_root = temp_parent / temp_name
    temp_root.mkdir()
    index_path = temp_root / "index"
    hooks_path = temp_root / "h"
    hooks_path.mkdir()
    environment = dict(os.environ)
    environment["GIT_INDEX_FILE"] = str(index_path)
    environment["GIT_OPTIONAL_LOCKS"] = "0"

    def run(*args: str) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(
            ["git", "-c", f"core.hooksPath={hooks_path}", *args],
            cwd=repo,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Failed to build candidate patch with isolated index:\n"
                + completed.stderr.decode("utf-8", errors="replace")
            )
        return completed

    try:
        run("read-tree", baseline.baseline_sha)
        for entry in baseline.changed_entries():
            rel = str(entry["path"])
            if entry["state"] == "deleted":
                run("update-index", "--force-remove", "--", rel)
            else:
                run("add", "-f", "--", rel)
        return run(
            "diff", "--cached", "--binary", "--full-index", baseline.baseline_sha, "--"
        ).stdout
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def build_repository_patch(repo: Path, *, scratch_root: Path | None = None) -> bytes:
    return _build_patch(repo, scratch_root=scratch_root)


def build_candidate_patch(
    session: WorkspaceSession, *, scratch_root: Path | None = None
) -> bytes:
    if candidate_baseline_for(session.workspace) is None:
        head = session.base_sha or str(
            _run_git(session.workspace, "rev-parse", "HEAD").stdout
        ).strip()
        CandidateWorkspaceBaseline.from_git_tree(
            session.workspace,
            baseline_sha=head,
            excluded_prefixes=(
                ".git",
                ".harness_tmp",
                ".venv",
                ".harness_git_excludes",
                "__pycache__",
                "**/__pycache__",
                "*.py[cod]",
                "**/*.py[cod]",
                ".pytest_cache",
                "**/.pytest_cache",
                *session.exposed_paths,
                *(item.relative_path for item in session.runtime_projections),
            ),
        )
    return build_repository_patch(session.workspace, scratch_root=scratch_root)


def _remove_new_candidate_files(repo: Path) -> None:
    baseline = candidate_baseline_for(repo)
    if baseline is None:
        raise RuntimeError("Source candidate baseline is missing during rollback")
    baseline_paths = {item.path for item in baseline.entries}
    added = {
        str(item["path"])
        for item in baseline.changed_entries()
        if item["state"] != "deleted" and str(item["path"]) not in baseline_paths
    }
    for raw in sorted(added, key=lambda item: len(Path(item).parts), reverse=True):
        path = repo / raw
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def apply_candidate_to_source(session: WorkspaceSession, *, patch: bytes) -> None:
    if session.result_mode != "apply_to_source":
        return
    if session.source_repo is None or session.source_head is None:
        raise RuntimeError("Managed source repository metadata is missing")
    source = session.source_repo
    current_head = str(_run_git(source, "rev-parse", "HEAD").stdout).strip()
    if current_head != session.source_head:
        raise RuntimeError(
            "Source HEAD changed while Harness was running; candidate was not applied"
        )
    status_text = _status_excluding_allowed_untracked(
        _status_porcelain(source), session.exposed_paths
    )
    if status_text.strip():
        raise RuntimeError(
            "Source became dirty while Harness was running; candidate was not applied\n\n"
            + status_text
        )
    if not patch:
        print("RESULT_APPLY: no candidate diff")
        return

    CandidateWorkspaceBaseline.capture(
        source,
        baseline_sha=session.source_head,
        excluded_prefixes=(
            ".git",
            ".harness_tmp",
            ".venv",
            ".harness_git_excludes",
            *session.exposed_paths,
        ),
    )

    check = subprocess.run(
        ["git", "apply", "--check", "--binary", "--whitespace=nowarn", "-"],
        cwd=source,
        input=patch,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check.returncode != 0:
        raise RuntimeError(
            "Candidate patch does not apply cleanly to source:\n"
            + check.stderr.decode("utf-8", errors="replace")
        )

    apply_result = subprocess.run(
        ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
        cwd=source,
        input=patch,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if apply_result.returncode != 0:
        raise RuntimeError(
            "Candidate passed checks but could not be applied to source:\n"
            + apply_result.stderr.decode("utf-8", errors="replace")
        )

    applied_patch = _build_patch(source)
    if applied_patch != patch:
        _run_git(source, "reset", "--hard", "HEAD")
        _remove_new_candidate_files(source)
        raise RuntimeError(
            "Applied source diff does not exactly match the accepted candidate. "
            "Source was rolled back."
        )
    print("RESULT_APPLIED_TO_SOURCE:", source)
