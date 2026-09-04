from __future__ import annotations

import os
import shutil
import subprocess
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from slivin_harness.git_integrity import CandidateWorkspaceBaseline
from slivin_harness.phase7 import ReconstructionPreparation, run_reconstructed_verification
from slivin_harness.run_state import build_candidate_identity
from slivin_harness.workspace import (
    RuntimeProjection,
    WorkspaceSession,
    build_candidate_patch,
    materialize_authoritative_runtime_copy,
)


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


class ReconstructedVerificationTests(unittest.TestCase):
    @contextmanager
    def temp_directory(self):
        root = Path.cwd() / ".harness_tmp" / "rv"
        root.mkdir(parents=True, exist_ok=True)
        path = root / uuid.uuid4().hex[:8]
        path.mkdir()
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def make_candidate(self, root: Path) -> tuple[Path, str, bytes, object]:
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Reconstruction Test")
        git(repo, "config", "user.email", "reconstruction@example.invalid")
        (repo / ".gitignore").write_text("ignored-helper.js\n", encoding="utf-8")
        (repo / "app.js").write_text("baseline\n", encoding="utf-8")
        git(repo, "add", ".gitignore", "app.js")
        git(repo, "commit", "-m", "baseline")
        baseline = git(repo, "rev-parse", "HEAD")
        CandidateWorkspaceBaseline.capture(
            repo,
            baseline_sha=baseline,
            excluded_prefixes=(".git", ".harness_tmp", ".harness_git_excludes"),
        )
        (repo / "app.js").write_text("candidate\n", encoding="utf-8")
        (repo / "ignored-helper.js").write_text("included\n", encoding="utf-8")
        candidate = build_candidate_identity(repo, baseline_sha=baseline)
        patch = build_candidate_patch(
            WorkspaceSession(repo, "test", False, base_sha=baseline),
            scratch_root=root / "scratch",
        )
        return repo, baseline, patch, candidate

    @staticmethod
    def preparation(_workspace: Path) -> ReconstructionPreparation:
        return ReconstructionPreparation(
            context=None,
            excluded_prefixes=(".git", ".harness_tmp", ".harness_git_excludes"),
        )

    @staticmethod
    def passed() -> dict[str, object]:
        return {
            "static_preflight_status": "PASS",
            "repair_checks_status": "PASS",
            "heldout_status": "NOT_APPLICABLE",
            "runtime_projection_status": "PASS",
            "git_control_status": "PASS",
            "reason_code": None,
        }

    def test_ignored_candidate_file_is_reconstructed_and_replayed(self) -> None:
        with self.temp_directory() as root:
            repo, baseline, patch, candidate = self.make_candidate(root)

            def verify(workspace, _candidate, _context):
                self.assertEqual(
                    (workspace / "ignored-helper.js").read_text(encoding="utf-8"),
                    "included\n",
                )
                return self.passed(), {"checks": "replayed"}

            result = run_reconstructed_verification(
                repository=repo,
                baseline_sha=baseline,
                patch=patch,
                expected_candidate=candidate,
                private_root=root / "private",
                prepare_workspace=self.preparation,
                verify=verify,
            )
            self.assertEqual(result.public["status"], "PASS", result.private)
            self.assertEqual(
                result.public["reconstructed_candidate_id"], candidate.candidate_id
            )

    def test_original_workspace_only_helper_cannot_satisfy_replay(self) -> None:
        with self.temp_directory() as root:
            repo, baseline, patch, candidate = self.make_candidate(root)
            (repo / ".harness_tmp").mkdir()
            (repo / ".harness_tmp" / "helper.js").write_text("secret\n", encoding="utf-8")

            def verify(workspace, _candidate, _context):
                status = self.passed()
                if not (workspace / ".harness_tmp" / "helper.js").exists():
                    status["repair_checks_status"] = "FAIL"
                    status["reason_code"] = "RECONSTRUCTED_REPAIR_CHECKS_FAILED"
                return status, {}

            result = run_reconstructed_verification(
                repository=repo,
                baseline_sha=baseline,
                patch=patch,
                expected_candidate=candidate,
                private_root=root / "private",
                prepare_workspace=self.preparation,
                verify=verify,
            )
            self.assertEqual(result.public["status"], "FAIL")
            self.assertEqual(
                result.public["reason_code"],
                "RECONSTRUCTED_REPAIR_CHECKS_FAILED",
                result.private,
            )

    def test_replayed_check_candidate_mutation_blocks_acceptance(self) -> None:
        with self.temp_directory() as root:
            repo, baseline, patch, candidate = self.make_candidate(root)

            def verify(workspace, _candidate, _context):
                (workspace / "mutated-by-check.txt").write_text("mutation\n", encoding="utf-8")
                return self.passed(), {}

            result = run_reconstructed_verification(
                repository=repo,
                baseline_sha=baseline,
                patch=patch,
                expected_candidate=candidate,
                private_root=root / "private",
                prepare_workspace=self.preparation,
                verify=verify,
            )
            self.assertEqual(result.public["status"], "FAIL")
            self.assertFalse(result.public["candidate_unchanged"])

    def test_loose_unreferenced_object_is_not_available_to_replay(self) -> None:
        with self.temp_directory() as root:
            repo, baseline, _patch, _candidate = self.make_candidate(root)
            created = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=repo,
                input=b"original-workspace-only\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout.decode("ascii").strip()
            self.assertEqual(
                subprocess.run(
                    ["git", "cat-file", "-e", created],
                    cwd=repo,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                ).returncode,
                0,
            )
            (repo / "app.js").write_text(created + "\n", encoding="ascii")
            candidate = build_candidate_identity(repo, baseline_sha=baseline)
            patch_bytes = build_candidate_patch(
                WorkspaceSession(repo, "test", False, base_sha=baseline),
                scratch_root=root / "scratch-loose",
            )

            def verify(workspace, _candidate, _context):
                status = self.passed()
                lookup = subprocess.run(
                    ["git", "cat-file", "-e", created],
                    cwd=workspace,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if lookup.returncode != 0:
                    status["repair_checks_status"] = "FAIL"
                    status["reason_code"] = "RECONSTRUCTED_REPAIR_CHECKS_FAILED"
                return status, {}

            result = run_reconstructed_verification(
                repository=repo,
                baseline_sha=baseline,
                patch=patch_bytes,
                expected_candidate=candidate,
                private_root=root / "private",
                prepare_workspace=self.preparation,
                verify=verify,
            )
            self.assertEqual(result.public["status"], "FAIL")
            self.assertEqual(
                result.public["reason_code"],
                "RECONSTRUCTED_REPAIR_CHECKS_FAILED",
                result.private,
            )

    def test_proof_runtime_and_exposed_files_come_from_source_not_candidate(self) -> None:
        with self.temp_directory() as root:
            source = root / "source"
            source.mkdir()
            git(source, "init")
            git(source, "config", "user.name", "Reconstruction Test")
            git(source, "config", "user.email", "reconstruction@example.invalid")
            (source / ".gitignore").write_text(".env\nnode_modules/\n", encoding="utf-8")
            (source / "tracked.txt").write_text("baseline\n", encoding="utf-8")
            git(source, "add", ".gitignore", "tracked.txt")
            git(source, "commit", "-m", "baseline")
            (source / ".env").write_text("source-value\n", encoding="utf-8")
            dependency = source / "node_modules" / "pkg" / "index.js"
            dependency.parent.mkdir(parents=True)
            dependency.write_text("source-runtime\n", encoding="utf-8")

            candidate_workspace = root / "candidate"
            candidate_workspace.mkdir()
            (candidate_workspace / ".env").write_text("mutated-value\n", encoding="utf-8")
            candidate_dependency = candidate_workspace / "node_modules" / "pkg" / "index.js"
            candidate_dependency.parent.mkdir(parents=True)
            candidate_dependency.write_text("mutated-runtime\n", encoding="utf-8")
            session = WorkspaceSession(
                workspace=candidate_workspace,
                mode="historical",
                managed=True,
                source_repo=source,
                exposed_paths=(".env", "node_modules"),
                runtime_projections=(
                    RuntimeProjection(
                        relative_path="node_modules",
                        source_kind="workspace.copy_untracked",
                        destination=candidate_workspace / "node_modules",
                        is_directory=True,
                        copy_mode="physical_copy",
                        runtime_only=True,
                    ),
                ),
                benchmark_isolated=True,
            )
            proof = root / "proof-runtime"
            proof.mkdir()
            proof_session = materialize_authoritative_runtime_copy(session, workspace=proof)

            self.assertEqual((proof / ".env").read_text(encoding="utf-8"), "source-value\n")
            self.assertEqual(
                (proof / "node_modules" / "pkg" / "index.js").read_text(encoding="utf-8"),
                "source-runtime\n",
            )
            self.assertEqual(
                tuple(item.relative_path for item in proof_session.runtime_projections),
                ("node_modules",),
            )


if __name__ == "__main__":
    unittest.main()
