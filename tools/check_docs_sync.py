from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

# Import after ROOT is known; running the script from another CWD is supported.
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from slivin_harness import __version__
from slivin_harness.implementer import IMPLEMENTER_PROTOCOL_VERSION, IMPLEMENTATION_CONTRACT_VERSION
from slivin_harness.control_plane import CONTROL_PLANE_VERSION
from slivin_harness.execution import EXECUTION_BROKER_VERSION
from slivin_harness.phase5 import (
    CONTRACT_EXPANSION_VERSION,
    PHASE5_VERSION,
    PROJECT_RUNTIME_VERSION,
)
from slivin_harness.phase6 import (
    BLIND_AUDIT_VERSION,
    CONTRACT_CLOSURE_VERSION,
    PHASE6_VERSION,
    RUNTIME_EVIDENCE_VERSION,
    RUNTIME_REQUEST_VERSION,
    RUNTIME_RESULT_VERSION,
    RUNTIME_SCENARIO_VERSION,
)
from slivin_harness.phase7 import (
    BENCHMARK_ISOLATION_VERSION,
    DELIVERY_RECORD_VERSION,
    FINAL_ACCEPTANCE_VERSION,
    HELDOUT_EVIDENCE_VERSION,
    PATCH_PROOF_VERSION,
    PHASE7_VERSION,
)
from slivin_harness.run_state import CANDIDATE_IDENTITY_VERSION, RUN_STATE_VERSION
from slivin_harness.task_contract import TASK_CONTRACT_VERSION
from slivin_harness.verification import VERIFICATION_PLAN_VERSION
from slivin_harness.workflow import (
    WORKFLOW_PHASE,
    WORKFLOW_VERSION,
    render_workflow_markdown,
    validate_workflow_definition,
    workflow_snapshot,
)
from slivin_harness.protocol import (
    EVALUATOR_PROTOCOL_VERSION,
    MANIFEST_VERSION,
    PLANNER_PROTOCOL_VERSION,
)
from task_runner import (
    load_manifest,
    split_checks,
    verify_oracle_calibration_certificate,
)

EXPECTED_MAIN_DOCS = {
    "ARCHITECTURE.md",
    "HISTORY.md",
    "PHASE4_EXECUTION.md",
    "PHASE5_CONTRACT_RUNTIME.md",
    "PHASE6_RUNTIME_EVALUATOR.md",
    "PHASE7_FINAL_GATE.md",
    "PRACTICAL_GUIDE.md",
    "QUALITY_MODEL.md",
    "README.md",
    "WINDOWS_SETUP.md",
    "WORKFLOW.md",
}
REMOVED_DOCS = {
    "CURRENT_STATE.md",
    "DECISIONS.md",
    "DECISION_TEMPLATE.md",
    "HANDOFF_PROTOCOL.md",
    "MAINTAINING_HARNESS.md",
    "WORKSPACE_MODEL.md",
}
REMOVED_MANIFEST_FIELDS = {
    "max_change_surface_cycles",
    "max_impact_cycles",
    "max_plan_validation_retries",
}
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


class _Recorder:
    def write_json(self, name: str, value: object) -> Path:
        return Path(tempfile.gettempdir()) / name


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _check_internal_links(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for raw in LINK_RE.findall(text):
        target = raw.strip().split("#", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        target = unquote(target)
        resolved = (path.parent / target).resolve()
        _assert(resolved.exists(), f"Broken Markdown link in {path.relative_to(ROOT)}: {raw}")


def _check_one_h1(path: Path) -> None:
    h1 = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("# ")]
    _assert(len(h1) == 1, f"Expected exactly one H1 in {path.relative_to(ROOT)}, got {len(h1)}")


def main() -> int:
    _assert(__version__ == "0.8.0a18", f"Unexpected Harness version: {__version__}")
    _assert(MANIFEST_VERSION == 2, f"Unexpected manifest version: {MANIFEST_VERSION}")
    _assert(PLANNER_PROTOCOL_VERSION == "planner.v4", PLANNER_PROTOCOL_VERSION)
    _assert(IMPLEMENTATION_CONTRACT_VERSION == "implementation-contract.v3", IMPLEMENTATION_CONTRACT_VERSION)
    _assert(EVALUATOR_PROTOCOL_VERSION == "evaluator.v5", EVALUATOR_PROTOCOL_VERSION)
    _assert(IMPLEMENTER_PROTOCOL_VERSION == "implementer.v3", IMPLEMENTER_PROTOCOL_VERSION)
    _assert(WORKFLOW_VERSION == "workflow.v6", WORKFLOW_VERSION)
    _assert(RUN_STATE_VERSION == "run-state.v1", RUN_STATE_VERSION)
    _assert(CANDIDATE_IDENTITY_VERSION == "candidate.v1", CANDIDATE_IDENTITY_VERSION)
    _assert(CONTROL_PLANE_VERSION == "controller-plane.v1", CONTROL_PLANE_VERSION)
    _assert(EXECUTION_BROKER_VERSION == "execution-broker.v1", EXECUTION_BROKER_VERSION)
    _assert(TASK_CONTRACT_VERSION == "task-contract.v1", TASK_CONTRACT_VERSION)
    _assert(VERIFICATION_PLAN_VERSION == "verification-plan.v1", VERIFICATION_PLAN_VERSION)
    _assert(PHASE5_VERSION == "phase5-contract-runtime.v1", PHASE5_VERSION)
    _assert(CONTRACT_EXPANSION_VERSION == "contract-expansion.v1", CONTRACT_EXPANSION_VERSION)
    _assert(PROJECT_RUNTIME_VERSION == "project-runtime.v1", PROJECT_RUNTIME_VERSION)
    _assert(PHASE6_VERSION == "phase6-runtime-evaluator.v1", PHASE6_VERSION)
    _assert(RUNTIME_SCENARIO_VERSION == "runtime-scenario.v1", RUNTIME_SCENARIO_VERSION)
    _assert(RUNTIME_REQUEST_VERSION == "runtime-request.v1", RUNTIME_REQUEST_VERSION)
    _assert(RUNTIME_RESULT_VERSION == "runtime-result.v1", RUNTIME_RESULT_VERSION)
    _assert(RUNTIME_EVIDENCE_VERSION == "runtime-evidence.v1", RUNTIME_EVIDENCE_VERSION)
    _assert(CONTRACT_CLOSURE_VERSION == "contract-closure.v1", CONTRACT_CLOSURE_VERSION)
    _assert(BLIND_AUDIT_VERSION == "blind-audit.v1", BLIND_AUDIT_VERSION)
    _assert(PHASE7_VERSION == "phase7-final-gate.v1", PHASE7_VERSION)
    _assert(PATCH_PROOF_VERSION == "patch-proof.v1", PATCH_PROOF_VERSION)
    _assert(FINAL_ACCEPTANCE_VERSION == "final-acceptance.v2", FINAL_ACCEPTANCE_VERSION)
    _assert(DELIVERY_RECORD_VERSION == "delivery-record.v2", DELIVERY_RECORD_VERSION)
    _assert(HELDOUT_EVIDENCE_VERSION == "heldout-evidence.v2", HELDOUT_EVIDENCE_VERSION)
    _assert(BENCHMARK_ISOLATION_VERSION == "benchmark-isolation.v1", BENCHMARK_ISOLATION_VERSION)
    validate_workflow_definition()

    docs_dir = ROOT / "docs"
    generated_markdown = render_workflow_markdown(harness_version=__version__)
    _assert(
        (docs_dir / "WORKFLOW.md").read_text(encoding="utf-8") == generated_markdown,
        "docs/WORKFLOW.md is stale; run ./py tools/render_workflow_docs.py",
    )
    generated_json = json.dumps(
        workflow_snapshot(harness_version=__version__),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    _assert(
        (docs_dir / "workflow.v6.json").read_text(encoding="utf-8") == generated_json,
        "docs/workflow.v6.json is stale; run ./py tools/render_workflow_docs.py",
    )

    actual_docs = {path.name for path in docs_dir.glob("*.md")}
    _assert(actual_docs == EXPECTED_MAIN_DOCS, f"Unexpected docs set: {sorted(actual_docs)}")
    _assert(not any((docs_dir / name).exists() for name in REMOVED_DOCS), "Removed docs returned")
    _assert(not (ROOT / "slivin_harness" / "impact.py").exists(), "impact.py must stay removed")

    markdown_files = [ROOT / "README.md", ROOT / "CHANGELOG.md"]
    markdown_files.extend(sorted(docs_dir.glob("*.md")))
    markdown_files.append(ROOT / "cases" / "matrix-all-matching" / "README.md")
    for path in markdown_files:
        _check_one_h1(path)
        _check_internal_links(path)

    all_active_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [ROOT / "README.md", docs_dir / "ARCHITECTURE.md", docs_dir / "QUALITY_MODEL.md"]
    )
    for marker in (
        "0.8.0a18",
        "version = 2",
        "task-contract.v1",
        "planner.v4",
        "implementer.v3",
        "implementation-contract.v3",
        "verification-plan.v1",
        "evaluator.v5",
        WORKFLOW_VERSION,
        RUN_STATE_VERSION,
        CANDIDATE_IDENTITY_VERSION,
        CONTROL_PLANE_VERSION,
        EXECUTION_BROKER_VERSION,
        PHASE5_VERSION,
        CONTRACT_EXPANSION_VERSION,
        PROJECT_RUNTIME_VERSION,
        PHASE6_VERSION,
        RUNTIME_SCENARIO_VERSION,
        RUNTIME_REQUEST_VERSION,
        RUNTIME_RESULT_VERSION,
        RUNTIME_EVIDENCE_VERSION,
        CONTRACT_CLOSURE_VERSION,
        BLIND_AUDIT_VERSION,
        PHASE7_VERSION,
        PATCH_PROOF_VERSION,
        FINAL_ACCEPTANCE_VERSION,
        DELIVERY_RECORD_VERSION,
        HELDOUT_EVIDENCE_VERSION,
        BENCHMARK_ISOLATION_VERSION,
        WORKFLOW_PHASE,
    ):
        _assert(marker in all_active_text, f"Active docs do not mention {marker}")

    manifests = [
        ROOT / "examples" / "project-task.example.toml",
        ROOT / "cases" / "matrix-all-matching" / "task.toml",
    ]
    loaded = [load_manifest(path) for path in manifests]
    for path in manifests:
        text = path.read_text(encoding="utf-8")
        for field in REMOVED_MANIFEST_FIELDS:
            _assert(field not in text, f"Removed field {field} remains in {path.relative_to(ROOT)}")
    _assert(loaded[0]["risk"] == "low", "Example task should demonstrate the low pipeline")
    _assert(loaded[1]["risk"] == "medium", "Matrix benchmark should use medium pipeline")
    _assert(
        loaded[1]["benchmark"].get("baseline_failure_marker"),
        "Matrix benchmark must define baseline_failure_marker",
    )

    _, heldout = split_checks(loaded[1]["checks"])
    _assert(len(heldout) == 1, "Matrix benchmark must have exactly one held-out check")
    _assert(heldout[0]["name"] == "Historical Matrix semantic held-out", "Unexpected Matrix held-out")
    _assert((ROOT / "hidden_checks" / "matrix_semantic_check.cjs").is_file(), "Semantic Matrix grader missing")
    _assert(not (ROOT / "hidden_checks" / "matrix_all_matching.test.cjs").exists(), "Legacy Matrix grader returned")
    certificate = __import__("json").loads(
        (ROOT / loaded[1]["benchmark"]["calibration_certificate"]).read_text(encoding="utf-8")
    )
    _assert(certificate.get("schema_version") == 2, "Matrix calibration must use schema v2")
    matrix_calibration = certificate.get("heldout_checks", [])[0]
    _assert(
        matrix_calibration.get("fixture_fingerprint_algorithm")
        == "sha256(canonical-json({repo_relative_path: sha256(file_bytes)}))",
        "Matrix calibration fingerprint algorithm drifted",
    )
    _assert(
        matrix_calibration.get("fixture_fingerprint_files") == [
            "static/js/components/datatable/selection/core.js",
            "static/js/components/datatable/selection/bulk_edit.js",
            "static/js/distribution/index.js",
            "static/js/config/tableConfigs/matrix.js",
        ],
        "Matrix calibration fingerprint surface drifted",
    )
    expected_calibration_cases = {
        "historical-_90": ("broken_baseline", "FAIL"),
        "historical-_92": ("known_incomplete", "FAIL"),
        "workspace_14": ("known_incomplete", "FAIL"),
        "candidate-0.6.2": ("known_incomplete", "FAIL"),
        "candidate-0.6.5": ("known_incomplete", "FAIL"),
        "semantic-good-a": ("positive_reference", "PASS"),
        "semantic-good-b": ("positive_reference", "PASS"),
    }
    actual_calibration_cases = {
        item.get("name"): (item.get("role"), item.get("expected_result"))
        for item in matrix_calibration.get("calibration_cases", [])
    }
    _assert(
        actual_calibration_cases == expected_calibration_cases,
        f"Matrix calibration controls drifted: {actual_calibration_cases}",
    )
    verify_oracle_calibration_certificate(
        heldout,
        certificate_path=ROOT / loaded[1]["benchmark"]["calibration_certificate"],
        recorder=_Recorder(),
    )

    print(
        "DOCS_SYNC_PASS "
        f"harness={__version__} manifest={MANIFEST_VERSION} "
        f"task_contract={TASK_CONTRACT_VERSION} planner={PLANNER_PROTOCOL_VERSION} "
        f"implementer={IMPLEMENTER_PROTOCOL_VERSION} implementation_contract={IMPLEMENTATION_CONTRACT_VERSION} "
        f"verification_plan={VERIFICATION_PLAN_VERSION} evaluator={EVALUATOR_PROTOCOL_VERSION} workflow={WORKFLOW_VERSION} "
        f"run_state={RUN_STATE_VERSION} candidate={CANDIDATE_IDENTITY_VERSION} "
        f"control_plane={CONTROL_PLANE_VERSION} execution_broker={EXECUTION_BROKER_VERSION}"
        f" phase5={PHASE5_VERSION} contract_expansion={CONTRACT_EXPANSION_VERSION}"
        f" project_runtime={PROJECT_RUNTIME_VERSION}"
        f" phase6={PHASE6_VERSION} runtime_scenario={RUNTIME_SCENARIO_VERSION}"
        f" runtime_request={RUNTIME_REQUEST_VERSION} runtime_result={RUNTIME_RESULT_VERSION}"
        f" runtime_evidence={RUNTIME_EVIDENCE_VERSION} contract_closure={CONTRACT_CLOSURE_VERSION}"
        f" blind_audit={BLIND_AUDIT_VERSION}"
        f" phase7={PHASE7_VERSION} patch_proof={PATCH_PROOF_VERSION}"
        f" final_acceptance={FINAL_ACCEPTANCE_VERSION} delivery={DELIVERY_RECORD_VERSION}"
        f" heldout_evidence={HELDOUT_EVIDENCE_VERSION}"
        f" benchmark_isolation={BENCHMARK_ISOLATION_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
