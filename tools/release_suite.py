"""Machine-readable qualification tests with observed production boundary coverage."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from slivin_harness.boundaries import BOUNDARIES, attach_boundary_observer, detach_boundary_observer, boundary_inventory

# Fixed suites contain independent expected outcomes. Entire modules retain
# positive, negative, freshness and recovery siblings when a fixture evolves.
BOUNDARY_MODULES = (
    "test_control_plane", "test_phase3_contracts", "test_protocol", "test_planner",
    "test_implementer_impact_closure", "test_runner_report_recovery", "test_systemic_reliability",
    "test_phase4_execution", "test_evaluator_impact_challenge", "test_proof_model_replan",
    "test_task_runner_workflow", "test_reconstructed_verification", "test_phase7_final_gate",
    "test_user_follow_up_handoff", "test_planner_tool_evidence_refresh",
)


def check_inventory() -> dict:
    observed = {}
    for path in [ROOT / "task_runner.py", *sorted((ROOT / "slivin_harness").glob("*.py"))]:
        module = path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")
        def walk(node, prefix=""):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.ClassDef)):
                    name = prefix + child.name
                    for decorator in child.decorator_list:
                        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name) and decorator.func.id == "boundary":
                            observed[module + "." + name] = ast.literal_eval(decorator.args[0])
                    walk(child, name + ".")
                else:
                    walk(child, prefix)
        walk(ast.parse(path.read_text(encoding="utf-8")))
    expected = {entry: identifier for identifier, (_, entries) in BOUNDARIES.items() for entry in entries}
    if expected != observed:
        raise RuntimeError("Boundary inventory differs from production hooks: " + repr((expected.keys() - observed.keys(), observed.keys() - expected.keys())))
    return boundary_inventory()


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = {}
        self.current = None
        self.boundary_events = []

    def startTest(self, test):
        self.current = test.id()
        super().startTest(test)

    def addSuccess(self, test):
        self.outcomes[test.id()] = "PASS"
        super().addSuccess(test)

    def addError(self, test, err):
        self.outcomes[test.id()] = "ERROR"
        super().addError(test, err)

    def addFailure(self, test, err):
        self.outcomes[test.id()] = "FAIL"
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        self.outcomes[test.id()] = "SKIP: " + reason
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.outcomes[test.id()] = "FAIL"
        super().addSubTest(test, subtest, err)

    def boundary(self, event):
        self.boundary_events.append(dict(test=self.current, **event))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("boundary", "stateful"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    inventory = check_inventory()
    modules = sorted(set(BOUNDARY_MODULES) | {test.split('.')[0] for boundary in inventory['boundaries'] for case in boundary['families'].values() for test in case['unittest_ids']}) if args.stage == "boundary" else ("test_reliability_stateful", "test_systemic_reliability")
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    runner = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult)
    result = runner._makeResult()
    token = attach_boundary_observer(result.boundary)
    try:
        suite.run(result)
    finally:
        detach_boundary_observer(token)
    result.printErrors()
    covered = {event["entrypoint"] for event in result.boundary_events if event["event"] == "RETURN" and result.outcomes.get(event["test"]) == "PASS"}
    required = {entry for _, entries in BOUNDARIES.values() for entry in entries} if args.stage == "boundary" else set()
    missing = sorted(required - covered)
    families = []
    if args.stage == 'boundary':
        for boundary in inventory['boundaries']:
            for family, case in boundary['families'].items():
                outcomes = {test:result.outcomes.get(test, 'NOT_RUN') for test in case['unittest_ids']}
                families.append(dict(boundary_id=boundary['boundary_id'], family=family,
                    expectation=case['expectation'], cases=outcomes,
                    status='PASS' if all(status == 'PASS' for status in outcomes.values()) else 'FAIL'))
    # Cross-platform chmod/symlink cases remain visible; all mandatory native
    # assertions execute separately. No other skip can qualify this suite.
    allowed_skips = {
        'test_runtime_projection_integrity.RuntimeProjectionIntegrityTests.test_tree_fingerprint_rejects_symlink_without_following_it': 'Creating symlinks is not reliably permitted on Windows',
        'test_run_state.CandidateIdentityTests.test_candidate_id_distinguishes_executable_mode_changes_when_supported': 'Git/filesystem does not expose chmod-only executable-bit changes (expected on native Windows/NTFS)',
        'test_app_server.AppServerTests.test_non_windows_command_is_direct': 'POSIX-specific command shape',
    }
    mandatory_skips = [test for test, reason in result.skipped if allowed_skips.get(test.id()) != reason]
    passed = result.wasSuccessful() and not missing and not mandatory_skips and result.testsRun > 0 and all(row['status']=='PASS' for row in families)
    record = dict(schema_version="release-suite.v1", status="PASS" if passed else "FAIL", stage=args.stage,
                  tests_run=result.testsRun, outcomes=result.outcomes, missing_entrypoints=missing,
                  skipped=[dict(test=test.id(), reason=reason) for test, reason in result.skipped],
                  boundary_inventory=inventory, boundary_events=result.boundary_events,
                  boundary_families=families,
                  levels=["production functions", "model doubles", "disposable Git", "real subprocess assertions where fixture requires"])
    (args.output / "summary.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("RELEASE_SUITE_" + record["status"], args.stage, "tests=", result.testsRun, "missing=", missing)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
