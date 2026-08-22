from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

HARNESS_EXCLUDES = [
    ".harness_tmp/",
    ".env",
    ".env.*",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "node_modules/",
    ".jest-cache*",
    "coverage/",
    "*.zip",
    "*.7z",
    "*.rar",
]

GENERATED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".harness_tmp",
    "node_modules",
    "coverage",
}


def run_git(workspace: Path, *args: str) -> str:
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
        raise RuntimeError(
            f"git {' '.join(args)} failed:\n{result.stderr.strip()}"
        )
    return result.stdout.strip()


def remove_generated(workspace: Path) -> None:
    for path in sorted(workspace.rglob("*"), reverse=True):
        if ".git" in path.parts:
            continue
        if path.is_dir() and (
            path.name in GENERATED_DIR_NAMES
            or path.name.startswith(".jest-cache")
        ):
            print(f"REMOVE_GENERATED: {path.relative_to(workspace)}")
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
            print(f"REMOVE_GENERATED: {path.relative_to(workspace)}")
            path.unlink(missing_ok=True)


def find_sensitive_env_files(workspace: Path) -> list[Path]:
    result: list[Path] = []
    allowed_templates = {".env.example", ".env.sample", ".env.template"}
    for path in workspace.rglob(".env*"):
        if ".git" in path.parts:
            continue
        if path.name in allowed_templates:
            continue
        if path.is_file():
            result.append(path)
    return result


def ensure_info_exclude(workspace: Path) -> None:
    exclude = workspace / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = {line.strip() for line in current.splitlines()}
    additions = [pattern for pattern in HARNESS_EXCLUDES if pattern not in lines]
    if additions:
        with exclude.open("a", encoding="utf-8", newline="\n") as handle:
            if current and not current.endswith("\n"):
                handle.write("\n")
            for pattern in additions:
                handle.write(pattern + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a disposable project workspace for Slivin Harness."
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--commit-message",
        default="harness benchmark baseline",
    )
    args = parser.parse_args()

    workspace = args.workspace.expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise RuntimeError(f"Workspace directory does not exist: {workspace}")

    remove_generated(workspace)

    sensitive = find_sensitive_env_files(workspace)
    if sensitive:
        joined = "\n".join(
            f"  - {path.relative_to(workspace)}" for path in sensitive
        )
        raise RuntimeError(
            "Refusing to prepare workspace while .env files are present. "
            "Remove them from the benchmark copy first:\n" + joined
        )

    git_dir = workspace / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=workspace, check=True)
        run_git(workspace, "config", "user.name", "Slivin Harness Baseline")
        run_git(
            workspace,
            "config",
            "user.email",
            "slivin-harness@example.invalid",
        )

    ensure_info_exclude(workspace)

    # Commit only when the repository has no HEAD yet.
    head_probe = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=workspace,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if head_probe.returncode != 0:
        run_git(workspace, "add", "-A")
        run_git(workspace, "commit", "-m", args.commit_message)
        print("BASELINE_COMMIT_CREATED:", run_git(workspace, "rev-parse", "HEAD"))
    else:
        status = run_git(workspace, "status", "--porcelain=v1", "--untracked-files=all")
        if status:
            raise RuntimeError(
                "Existing workspace repository is dirty. Reset/clean it before Harness:\n"
                + status
            )
        print("BASELINE_ALREADY_PRESENT:", run_git(workspace, "rev-parse", "HEAD"))

    print("HARNESS_TMP_IGNORE:", run_git(workspace, "check-ignore", "-v", ".harness_tmp/"))
    print("WORKSPACE_READY:", workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
