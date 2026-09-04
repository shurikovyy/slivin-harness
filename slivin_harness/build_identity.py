from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


HARNESS_BUILD_IDENTITY_VERSION = "harness-build-identity.v1"
_FULL_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


@dataclass(frozen=True)
class HarnessBuildIdentity:
    schema_version: str
    version: str
    git_commit: str | None
    git_dirty: bool | None
    source_kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _git(harness_root: Path, *args: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(harness_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout_seconds,
    )


def detect_harness_build_identity(
    *,
    harness_root: Path,
    version: str,
    timeout_seconds: int = 5,
) -> HarnessBuildIdentity:
    """Best-effort local build identity with no network access or path disclosure."""

    root = Path(harness_root).expanduser().resolve()
    timeout = max(1, int(timeout_seconds))
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree", timeout_seconds=timeout)
        if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
            raise RuntimeError("not a Git worktree")
        top_level = _git(root, "rev-parse", "--show-toplevel", timeout_seconds=timeout)
        if top_level.returncode != 0 or Path(top_level.stdout.strip()).resolve() != root:
            raise RuntimeError("harness root is not the Git worktree root")
        head = _git(root, "rev-parse", "HEAD", timeout_seconds=timeout)
        commit = head.stdout.strip().lower()
        if head.returncode != 0 or not _FULL_SHA.fullmatch(commit):
            raise RuntimeError("Git commit is unavailable")
        status = _git(
            root,
            "status",
            "--porcelain",
            "--untracked-files=no",
            timeout_seconds=timeout,
        )
        if status.returncode != 0:
            raise RuntimeError("Git dirty state is unavailable")
        return HarnessBuildIdentity(
            schema_version=HARNESS_BUILD_IDENTITY_VERSION,
            version=str(version),
            git_commit=commit,
            git_dirty=bool(status.stdout),
            source_kind="GIT_CHECKOUT",
        )
    except (FileNotFoundError, OSError, RuntimeError, subprocess.TimeoutExpired):
        return HarnessBuildIdentity(
            schema_version=HARNESS_BUILD_IDENTITY_VERSION,
            version=str(version),
            git_commit=None,
            git_dirty=None,
            source_kind="ARCHIVE_OR_UNKNOWN",
        )
