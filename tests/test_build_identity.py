from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import slivin_harness
from slivin_harness.build_identity import (
    HARNESS_BUILD_IDENTITY_VERSION,
    detect_harness_build_identity,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


class HarnessBuildIdentityTests(unittest.TestCase):
    def test_package_version_is_0_8_0a19(self) -> None:
        self.assertEqual(slivin_harness.__version__, "0.8.0a19")

    def test_git_checkout_reports_exact_commit_and_tracked_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="slivin-build-identity-") as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.name", "Build Identity Test")
            git(repo, "config", "user.email", "build-identity@example.invalid")
            tracked = repo / "tracked.txt"
            tracked.write_text("clean\n", encoding="utf-8")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "-m", "baseline")
            expected = git(repo, "rev-parse", "HEAD")

            clean = detect_harness_build_identity(
                harness_root=repo,
                version=slivin_harness.__version__,
            )
            self.assertEqual(clean.schema_version, HARNESS_BUILD_IDENTITY_VERSION)
            self.assertEqual(clean.git_commit, expected)
            self.assertFalse(clean.git_dirty)
            self.assertEqual(clean.source_kind, "GIT_CHECKOUT")

            (repo / "ignored-untracked.txt").write_text("untracked\n", encoding="utf-8")
            still_clean = detect_harness_build_identity(
                harness_root=repo,
                version=slivin_harness.__version__,
            )
            self.assertFalse(still_clean.git_dirty)
            tracked.write_text("dirty\n", encoding="utf-8")
            dirty = detect_harness_build_identity(
                harness_root=repo,
                version=slivin_harness.__version__,
            )
            self.assertTrue(dirty.git_dirty)
            self.assertNotIn(str(repo), json.dumps(dirty.to_dict(), sort_keys=True))

    def test_gitless_archive_is_nonfatal_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="slivin-build-archive-") as tmp:
            archive = Path(tmp) / "archive"
            archive.mkdir()
            identity = detect_harness_build_identity(
                harness_root=archive,
                version=slivin_harness.__version__,
            )
            self.assertIsNone(identity.git_commit)
            self.assertIsNone(identity.git_dirty)
            self.assertEqual(identity.source_kind, "ARCHIVE_OR_UNKNOWN")
            self.assertNotIn(str(archive), json.dumps(identity.to_dict(), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
