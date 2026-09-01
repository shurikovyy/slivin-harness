from __future__ import annotations

import unittest

from slivin_harness.protocol import ArtifactContractError, MANIFEST_VERSION
from task_runner import validate_manifest


def valid_manifest() -> dict:
    return {
        "version": MANIFEST_VERSION,
        "task_id": "DEMO_TASK",
        "workspace": "/tmp/demo",
        "risk": "medium",
        "max_fix_cycles": 2,
        "max_replan_cycles": 1,
        "turn_timeout_seconds": 900,
        "require_clean_git": True,
        "prompt": "Fix the demonstrated defect and preserve existing behavior.",
        "checks": [
            {
                "name": "unit",
                "feedback": "repair",
                "command": ["python", "-c", "print('ok')"],
                "timeout_seconds": 30,
            }
        ],
    }


class ManifestValidationTests(unittest.TestCase):
    def test_valid_version_two_manifest(self) -> None:
        validate_manifest(valid_manifest())

    def test_unknown_top_level_field_is_rejected(self) -> None:
        manifest = valid_manifest()
        manifest["max_impact_cycles"] = 2
        with self.assertRaisesRegex(RuntimeError, "Unknown task manifest fields"):
            validate_manifest(manifest)

    def test_unknown_check_field_is_rejected(self) -> None:
        manifest = valid_manifest()
        manifest["checks"][0]["shell"] = True
        with self.assertRaisesRegex(RuntimeError, "Unknown fields in checks"):
            validate_manifest(manifest)


    def test_benchmark_baseline_gate_requires_failure_marker(self) -> None:
        manifest = valid_manifest()
        manifest["benchmark"] = {"confirm_current_baseline_broken": True}
        with self.assertRaisesRegex(RuntimeError, "requires baseline_failure_marker"):
            validate_manifest(manifest)

    def test_heldout_requires_oracle_marker_and_keep_worktree(self) -> None:
        manifest = valid_manifest()
        manifest["checks"].append(
            {
                "name": "hidden",
                "feedback": "heldout",
                "command": ["python", "-c", "print('ORACLE_REACHED')"],
                "timeout_seconds": 30,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "baseline_failure_marker"):
            validate_manifest(manifest)

        manifest["benchmark"] = {"baseline_failure_marker": "ORACLE_REACHED"}
        manifest["result_mode"] = "apply_to_source"
        with self.assertRaisesRegex(RuntimeError, "result_mode=keep_worktree"):
            validate_manifest(manifest)

    def test_old_manifest_version_is_rejected(self) -> None:
        manifest = valid_manifest()
        manifest["version"] = 1
        with self.assertRaisesRegex(RuntimeError, "Unsupported manifest version"):
            validate_manifest(manifest)

    def test_requires_exactly_one_workspace_source(self) -> None:
        manifest = valid_manifest()
        manifest["project"] = "demo"
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            validate_manifest(manifest)

    def test_cycle_and_timeout_bounds_are_enforced(self) -> None:
        manifest = valid_manifest()
        manifest["max_fix_cycles"] = 99
        with self.assertRaisesRegex(RuntimeError, "max_fix_cycles"):
            validate_manifest(manifest)

        manifest = valid_manifest()
        manifest["turn_timeout_seconds"] = 10
        with self.assertRaisesRegex(RuntimeError, "turn_timeout_seconds"):
            validate_manifest(manifest)

    def test_owner_defined_allowed_paths_must_be_repo_relative(self) -> None:
        manifest = valid_manifest()
        manifest["allowed_paths"] = ["../outside"]
        with self.assertRaises(ArtifactContractError):
            validate_manifest(manifest)

    def test_low_risk_is_a_supported_small_pipeline(self) -> None:
        manifest = valid_manifest()
        manifest["risk"] = "low"
        manifest["max_replan_cycles"] = 0
        validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
