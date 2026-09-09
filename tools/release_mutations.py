"""Required defect mutations on separate disposable source copies only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = (
    ("native_to_jest", "slivin_harness/test_runners.py", [("return \"NODE_TEST\"", "return \"JEST\"")],
     "test_runner_report_recovery.RunnerResolutionTests.test_native_without_jest_and_missing_node"),
    ("skip_stale_refresh", "task_runner.py", [("            planner_preparation_index += 1", "            planner_preparation_index += 1\n            if planner_preparation_index > 1:\n                return set(), [], 'MUTATION_SKIPPED_REFRESH'")],
     "test_planner_tool_evidence_refresh.PlannerToolEvidenceWorkflowTests.test_jest_evidence_refreshes_before_implementer_triggered_replan"),
    ("premature_phase_b", "slivin_harness/evaluator.py", [("    on_blind_audit(copy.deepcopy(blind_audit))", "    # MUTATION: premature disclosure"),
        ("    return blind_audit, verdict", "    on_blind_audit(copy.deepcopy(blind_audit))\n    return blind_audit, verdict")],
     "test_evaluator_impact_challenge.EvaluatorImpactChallengeTests.test_phase_a_is_blind_and_phase_b_follows_immutable_persistence"),
    ("drop_source", "slivin_harness/source_records.py", [("if complete and seen != expected.keys():", "if False:")],
     "test_systemic_reliability.SourceOwnershipTests.test_missing_duplicate_unknown_and_stale_origins_rejected"),
    ("stale_receipt", "slivin_harness/control_plane.py", [("and receipt.get(\"binding\") == binding.to_dict()", "and True")],
     "test_control_plane.ControlPlaneTests.test_private_receipt_is_bound_to_every_revision_dimension"),
    ("always_stop_valid", "task_runner.py", [("    workspace = kwargs[\"workspace\"]", "    raise RuntimeError('MUTATION_ALWAYS_STOP')\n    workspace = kwargs[\"workspace\"]")],
     "test_systemic_reliability.BatchProgressTests.test_independent_errors_recover_in_one_batch"),
    ("permit_candidate_write", "task_runner.py", [("elif identity != frozen_candidate:", "elif False:"),
        ("if attempt and candidate_content_fingerprint(workspace) != frozen_candidate:", "if False:")],
     "test_systemic_reliability.BatchProgressTests.test_same_path_candidate_mutation_is_rejected_during_report_repair"),
)


def run_test(root: Path, test: str, log: Path, target: str) -> tuple[int, dict]:
    root = root.resolve()
    env = dict(os.environ, PYTHONPATH=str(root / "tests"), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    report = log.with_suffix(".json")
    result = subprocess.run([sys.executable, str(root / "tools/release_mutations.py"), "--probe", test,
                            "--target", target, "--output", str(report.resolve())], cwd=root, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=300)
    text = result.stdout + result.stderr
    log.write_text(text, encoding="utf-8")
    return result.returncode, json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}


def probe(test: str, target: str, output: Path) -> int:
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    touched = set()
    target_path = os.path.normcase(str((ROOT / target).resolve()))
    def trace(frame, event, arg):
        if event == "call" and os.path.normcase(frame.f_code.co_filename) == target_path:
            touched.add(frame.f_code.co_name)
        return None
    class Result(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.defects = []
        def record(self, err):
            self.defects.append(dict(type=err[0].__name__, message=str(err[1]), code=getattr(err[1], "code", None)))
        def addFailure(self, test, err):
            self.record(err)
            super().addFailure(test, err)
        def addError(self, test, err):
            self.record(err)
            super().addError(test, err)
        def addSubTest(self, test, subtest, err):
            if err is not None:
                self.record(err)
            super().addSubTest(test, subtest, err)
    sys.setprofile(trace)
    try:
        result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromName(test))
    finally:
        sys.setprofile(None)
    output.write_text(json.dumps(dict(tests_run=result.testsRun, passed=result.wasSuccessful(),
        skipped=len(result.skipped), target_calls=sorted(touched), defects=result.defects), indent=2), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def defect_detected(label: str, record: dict) -> bool:
    expected = {
        "native_to_jest": ("resolve_javascript_runner", "TestRunnerResolutionError", "JEST requires configured node, jest"),
        "skip_stale_refresh": ("prepare_planner_capabilities", "AssertionError", "Each Planner needs refreshed Jest version/config evidence after reset"),
        "premature_phase_b": ("run_evaluator", "AssertionError", "PHASE_A_NOT_PERSISTED_BEFORE_B"),
        "drop_source": ("resolve_assessments", "AssertionError", "ArtifactContractError not raised"),
        "stale_receipt": ("verify_self_verify_receipt", "AssertionError", "True is not false"),
        "always_stop_valid": ("run_implementer_report", "RuntimeError", "MUTATION_ALWAYS_STOP"),
        "permit_candidate_write": ("run_implementer_report", "AssertionError", "HarnessControlledStop not raised"),
    }
    target, kind, message = expected[label]
    defects = record.get("defects", [])
    return (record.get("tests_run") == 1 and record.get("skipped") == 0 and record.get("passed") is False
        and target in record.get("target_calls", []) and bool(defects)
        and all(row["type"] == kind and message in row["message"] for row in defects))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe")
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.probe:
        return probe(args.probe, args.target, args.output)
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for label, relative, edits, test in MUTATIONS:
        copy = args.output / label
        copy.mkdir()
        for directory in ("slivin_harness", "tools", "tests", "docs"):
            shutil.copytree(ROOT / directory, copy / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("task_runner.py", "README.md"):
            shutil.copyfile(ROOT / name, copy / name)
        # The mutation target is a fixed inventory path inside this disposable copy.
        target = (copy / relative).resolve(strict=True)
        if not target.is_relative_to(copy.resolve()) or target.is_relative_to(ROOT / "slivin_harness"):
            raise RuntimeError("Mutation escaped disposable copy")
        baseline, baseline_record = run_test(copy, test, copy / "baseline.log", relative)
        if baseline != 0 or baseline_record.get("tests_run") != 1 or baseline_record.get("skipped") != 0:
            results.append(dict(mutation=label, status="FAIL", reason="Unmutated control did not pass", test=test))
            continue
        content = target.read_text(encoding="utf-8")
        for old, new in edits:
            if content.count(old) != 1:
                raise RuntimeError("Mutation anchor changed: " + label + " / " + old)
            content = content.replace(old, new)
        compile(content, str(target), "exec")
        target.write_text(content, encoding="utf-8")
        mutated, mutated_record = run_test(copy, test, copy / "mutated.log", relative)
        detected = mutated != 0 and defect_detected(label, mutated_record)
        results.append(dict(mutation=label, status="PASS" if detected else "FAIL", test=test,
                            baseline_exit=baseline, mutated_exit=mutated, defect_detected=detected,
                            baseline=baseline_record, mutated=mutated_record))
        print("MUTATION_CONTROL", label, results[-1]["status"], flush=True)
    passed = len(results) == len(MUTATIONS) and all(row["status"] == "PASS" for row in results)
    (args.output / "summary.json").write_text(json.dumps(dict(schema_version="mutation-controls.v1", status="PASS" if passed else "FAIL", controls=results), indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
