"""Replay retained QE1/QS1/QE2 liveness failures without model execution."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slivin_harness.checkpoint import save_report_checkpoint
from slivin_harness.control_plane import ControllerPlane
from slivin_harness.evaluator import detect_evaluator_claim_closure
from slivin_harness.protocol import ArtifactContractError, ArtifactFailureKind
from slivin_harness.report_recovery import EvaluatorClosureCorrectionState
from tools.release_real_models import fixtures, verify_fixture_authorities

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "real_model_liveness"
ADAPTER_VERSION = "real-model-failure-replay.v1"


def _fixture(name: str) -> tuple[dict, str]:
    raw = (FIXTURE_ROOT / name).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _result(fixture: dict, digest: str, *, actual: str, passed: bool) -> dict:
    return {
        "fixture_id": fixture["fixture_id"],
        "fixture_origin": fixture["origin"],
        "source_qualification_run_id": fixture["source_qualification_run_id"],
        "source_run_id": fixture["source_run_id"],
        "expected_outcome": fixture["expected_outcome"],
        "actual_outcome": actual,
        "status": "PASS" if passed else "FAIL",
        "replay_schema": ADAPTER_VERSION,
        "fixture_sha256": digest,
        "source_artifact_sha256": fixture["source_artifact_sha256"],
    }


def replay_qe1() -> dict:
    fixture, digest = _fixture("qe1_negative_closure.json")
    captured = fixture["captured_wire"]
    challenge = {captured["impact_group"]: [captured["row"]]}
    report = {
        "protocol_version": captured["protocol_version"],
        "status": captured["status"],
        "candidate_id": captured["candidate_id"],
        "impact_challenge": challenge,
        "findings": captured["findings"],
    }
    try:
        detect_evaluator_claim_closure(report)
    except ArtifactContractError as error:
        actual = "HARD_FAIL_PASS_WITH_NEGATIVE_DISPOSITION" if (
            error.code == "PASS_WITH_NEGATIVE_DISPOSITION"
            and error.failure_kind is ArtifactFailureKind.SEMANTIC_MODEL_CONFLICT
        ) else f"{error.failure_kind.value}:{error.code}"
    else:
        actual = "UNEXPECTED_ACCEPT"
    compatible = dict(report, status="FINDINGS")
    compatible_kind = None
    closure_recovery = "NOT_ATTEMPTED"
    fields: list[str] = []
    try:
        detect_evaluator_claim_closure(compatible)
    except ArtifactContractError as error:
        compatible_kind = error.failure_kind.value
        state = EvaluatorClosureCorrectionState()
        fields = state.begin(compatible, error)
        corrected = copy.deepcopy(compatible)
        corrected["findings"] = [{
            "finding_id": "QE1-CLOSURE-1",
            "severity": "MEDIUM",
            "category": "CONSUMER",
            "title": "Captured negative consumer claim",
            "evidence": ["The frozen disposition already declares the material gap."],
            "failure_mode": "The independently identified consumer is not covered.",
            "required_action": "Address the already-declared consumer gap.",
            "required_proof": {
                "claim": "The declared consumer gap is addressed.",
                "level": "LOCAL_DETERMINISTIC",
                "capabilities": [],
            },
        }]
        corrected["impact_challenge"][captured["impact_group"]][0]["finding_ids"] = [
            "QE1-CLOSURE-1",
        ]
        state.observe_corrected(corrected)
        try:
            detect_evaluator_claim_closure(corrected)
        except ArtifactContractError as corrected_error:
            closure_recovery = f"{corrected_error.failure_kind.value}:{corrected_error.code}"
        else:
            closure_recovery = "CLAIM_CLOSURE_PASS"
    passed = (
        actual == fixture["expected_outcome"]
        and compatible_kind == ArtifactFailureKind.CLAIM_CLOSURE_INCOMPLETE.value
        and fields == [
            f"impact_challenge.{captured['impact_group']}[0].finding_ids", "findings",
        ]
        and closure_recovery == "CLAIM_CLOSURE_PASS"
    )
    result = _result(fixture, digest, actual=actual, passed=passed)
    result["compatible_status_classification"] = compatible_kind
    result["compatible_status_recovery"] = closure_recovery
    return result


def replay_qs1() -> dict:
    fixture, digest = _fixture("qs1_volatile_checkpoint.json")
    with tempfile.TemporaryDirectory(prefix="slivin-qs1-replay-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Replay"], cwd=workspace, check=True)
        subprocess.run(["git", "config", "user.email", "replay@example.invalid"], cwd=workspace, check=True)
        (workspace / "candidate.txt").write_text("stable\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=workspace, check=True, capture_output=True)
        volatile = workspace / fixture["volatile_locator"]
        volatile.parent.mkdir(parents=True)
        physical = "\\\\?\\" + str(volatile.absolute()) if os.name == "nt" else str(volatile)
        with open(physical, "wb") as handle:
            handle.write(b"captured volatile cache class")
        plane = ControllerPlane(root / "run")
        from slivin_harness import checkpoint as checkpoint_module
        observe = checkpoint_module.build_candidate_identity
        observations = 0

        def disappear_during_checkpoint(candidate_workspace: Path):
            nonlocal observations
            observations += 1
            if observations == 1:
                os.unlink(physical)
            return observe(candidate_workspace)

        with mock.patch(
            "slivin_harness.checkpoint.build_candidate_identity",
            side_effect=disappear_during_checkpoint,
        ):
            checkpoint = save_report_checkpoint(
                plane=plane,
                workspace=workspace,
                name="qs1-replay",
                contract={"fingerprint": "captured-qs1", "source_inventory": {"records": [], "transitions": []}},
            )
        excluded = checkpoint["evidence_exclusions"]
        expected_root = ".harness_tmp/planner"
        passed = any(
            row.get("locator") == expected_root
            and row.get("reason") == "UNREGISTERED_VOLATILE_ROLE_SCRATCH"
            for row in excluded
        )
    actual = "UNREGISTERED_VOLATILE_ROLE_SCRATCH_EXCLUDED" if passed else "VOLATILE_SCRATCH_NOT_EXCLUDED"
    return _result(fixture, digest, actual=actual, passed=passed)


def replay_qe2() -> dict:
    fixture, digest = _fixture("qe2_mixed_readme.json")
    _, files = fixtures("expiry")
    files["README.md"] = fixture["baseline_readme"]
    with tempfile.TemporaryDirectory(prefix="slivin-qe2-replay-") as temporary:
        destination = Path(temporary)
        for relative, content in files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        (destination / "README.md").write_text(
            fixture["delivered_readme"], encoding="utf-8", newline="\n",
        )
        for relative, content in fixture["delivered_product_files"].items():
            (destination / relative).write_text(content, encoding="utf-8", newline="\n")
        authorities = verify_fixture_authorities(destination=destination, files=files)
    passed = all(authorities.values())
    actual = "MIXED_DOCUMENT_AUTHORITY_PASS" if passed else "MIXED_DOCUMENT_AUTHORITY_FAIL"
    result = _result(fixture, digest, actual=actual, passed=passed)
    result["authorities"] = authorities
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = [replay_qe1(), replay_qs1(), replay_qe2()]
    passed = all(row["status"] == "PASS" for row in results)
    summary = {
        "schema_version": ADAPTER_VERSION,
        "status": "PASS" if passed else "FAIL",
        "model_execution": "NOT_RUN",
        "fixtures": results,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print("REAL_MODEL_FAILURE_REPLAY_" + summary["status"])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
