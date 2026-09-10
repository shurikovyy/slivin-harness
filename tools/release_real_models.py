"""Fixed, generic FULL qualification tasks using actual configured Codex turns.

Fixture source and assertions are public to the roles. No benchmark corpus,
reference fix, hidden grader or model double is involved. Two tasks, then repeat
the first; budgets and fixtures are part of the hashed release source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from slivin_harness.runtime_projection import fingerprint_runtime_tree
from slivin_harness.control_plane import safe_artifact_name
from slivin_harness.run_state import build_candidate_identity
from dataclasses import asdict


def fixtures(kind: str) -> tuple[str, dict[str, str]]:
    if kind == "expiry":
        prompt = "Expired entries must not be available for reading. Preserve access to active, unexpired entries."
        state = "module.exports.isReadable = entry => Boolean(entry.active);\n"
        sample = "{active:true, expiresAt:10, now:20, value:'payload'}"
        fresh = "{...entry, expiresAt:30}"
        direct = "Boolean(entry.active)"
    else:
        prompt = "Suspended accounts must not be allowed to use resources. Preserve access for enabled accounts that are not suspended."
        state = "module.exports.isReadable = entry => Boolean(entry.enabled);\n"
        sample = "{enabled:true, suspended:true, value:'payload'}"
        fresh = "{...entry, suspended:false}"
        direct = "Boolean(entry.enabled)"
    files = {
        ".gitignore": "node_modules/\n.harness_tmp/\n",
        "package.json": json.dumps({"name": "public-qualification-fixture", "version": "1.0.0", "private": True,
                                   "scripts": {"test": "jest --runInBand"}}) + "\n",
        "jest.config.cjs": "module.exports={testEnvironment:'node',testRegex:'.*\\\\.jest\\\\.cjs$'};\n",
        "src/state.cjs": state,
        "src/content.cjs": "const {isReadable}=require('./state.cjs');\nexports.read=entry=>isReadable(entry)?entry.value:null;\n",
        "src/access.cjs": f"exports.available=entry=>{direct};\n",
        "src/summary.cjs": "const {available}=require('./access.cjs');\nexports.count=entries=>entries.filter(available).length;\n",
        "src/legacy-label.cjs": "exports.label=()=> 'outdated';\n",
        "tests/content.native.cjs": "const {test}=require('node:test'); const assert=require('node:assert/strict'); const {read}=require('../src/content.cjs');\n"
            + f"const entry={sample}; test('actual content assertion',()=>{{assert.equal(read(entry),null); assert.equal(read({fresh}),'payload');}});\n",
        "tests/access.jest.cjs": "const {available}=require('../src/access.cjs'); const {count}=require('../src/summary.cjs');\n"
            + f"const entry={sample}; test('actual availability assertions',()=>{{expect(available(entry)).toBe(false); expect(count([entry,{fresh}])).toBe(1); expect(available({fresh})).toBe(true);}});\n",
        "tests/legacy.jest.cjs": "const {label}=require('../src/legacy-label.cjs'); test('independent legacy label',()=>{expect(label()).toBe('current');});\n",
        "README.md": "# Resource reads\n\nContent, availability and counts use the same eligibility policy.\n\n"
            "Run `npm test` to execute the Jest suite. Native assertions also exist.\n\n"
            "The legacy label is documented as `current`; it is independent of resource eligibility.\n\n"
            "Documentation navigation refers to [deployment instructions](docs/deployment.md).\n",
    }
    return prompt, files


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def run_assertions(workspace: Path, *, node: Path, runtime: Path, output: Path, phase: str) -> dict:
    """Execute the public frozen assertions with fixed real runners and selectors."""
    workspace = workspace.resolve()
    commands = {
        "native": [str(node), "--test", str(workspace / "tests/content.native.cjs")],
        "jest": [str(node), str(runtime / "jest/bin/jest.js"), "--config", str(workspace / "jest.config.cjs"),
                 "--runInBand", "--runTestsByPath", str(workspace / "tests/access.jest.cjs"), "--watch=false", "--verbose"],
        "legacy": [str(node), str(runtime / "jest/bin/jest.js"), "--config", str(workspace / "jest.config.cjs"),
                   "--runInBand", "--runTestsByPath", str(workspace / "tests/legacy.jest.cjs"), "--watch=false", "--verbose"],
    }
    results = {}
    for name, command in commands.items():
        result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, encoding="utf-8", timeout=120)
        text = result.stdout + result.stderr
        (output / f"{phase}-{name}.log").write_text(text, encoding="utf-8")
        results[name] = dict(exit_code=result.returncode, assertion_executed=(
            "actual content assertion" in text if name == "native" else
            "actual availability assertions" in text if name == "jest" else
            "independent legacy label" in text))
    return results


def verify_delivery(*, run: Path, folder: Path, repo: Path, baseline: str, task_id: str,
                    files: dict, acceptance: dict, handoff: dict, delivery: dict, node: Path, runtime: Path) -> dict:
    checks = {}
    checks["full_acceptance"] = (acceptance.get("quality_gate_status") == "FINAL_ACCEPTANCE_PASS"
        and acceptance.get("pipeline_profile") == "FULL" and acceptance.get("mode") == "PRODUCTION"
        and acceptance.get("expected_terminal_result") == "HARNESS_TASK_PASS"
        and acceptance.get("task_id") == task_id and acceptance.get("source_baseline_sha") == baseline)
    checks["delivery_status"] = (delivery.get("status") == "RESULT_DELIVERY_PASS"
        and delivery.get("result_mode") == "keep_worktree" and delivery.get("exact_patch_match") is True)
    destination = Path(delivery.get("destination", "")).resolve()
    checks["contained_destination"] = destination.is_relative_to((folder / "workspaces").resolve()) and destination != repo.resolve()
    if not all(checks.values()):
        return dict(status="FAIL", checks=checks)
    candidate = build_candidate_identity(destination)
    candidate_id = candidate.candidate_id
    checks["actual_candidate"] = candidate_id == acceptance.get("candidate_id") and candidate.baseline_sha == baseline
    reconstructed = acceptance.get("reconstructed_verification", {})
    patch = acceptance.get("patch_proof", {})
    checks["reconstruction"] = (reconstructed.get("status") == "PASS"
        and reconstructed.get("expected_candidate_id") == candidate_id == reconstructed.get("reconstructed_candidate_id")
        and patch.get("status") == "PATCH_RECONSTRUCTION_PASS"
        and patch.get("expected_candidate_id") == candidate_id == patch.get("reconstructed_candidate_id"))
    checks["handoff_binding"] = all(handoff.get(key) == acceptance.get(key)
        for key in ("task_id", "mode", "pipeline_profile", "candidate_id", "attempt_id", "revision_snapshot"))
    bindings = acceptance.get("artifact_bindings", [])
    stages = acceptance.get("stage_bindings", [])
    required_stages = {'intake_preflight', 'planner', 'implementation_contract', 'implementer',
                       'deterministic_checks', 'runtime_verification', 'evaluator'}
    checks['full_stage_inventory'] = len(stages) == len(required_stages) and {row.get('stage') for row in stages} == required_stages
    checks['current_stage_evidence'] = bool(stages)
    for stage in stages:
        checks['current_stage_evidence'] &= (stage.get('state') == 'PASSED' or
            (stage.get('stage') == 'runtime_verification' and stage.get('state') == 'SKIPPED' and stage.get('result_code') == 'RUNTIME_VERIFICATION_SKIPPED'))
        if stage.get('stage') in {'implementer', 'deterministic_checks', 'runtime_verification', 'evaluator'}:
            checks['current_stage_evidence'] &= stage.get('candidate_id') == candidate_id and stage.get('revision_snapshot') == acceptance.get('revision_snapshot')
        checks['current_stage_evidence'] &= bool(stage.get('artifacts'))
    required_artifacts = {'quality_gate_reconciliation.json', 'user_follow_up_report.json', 'patch_proof.json', 'reconstructed_verification.json'}
    required_artifacts.update(name for stage in stages for name in stage.get('artifacts', []) if name != 'candidate.patch')
    checks['complete_artifact_inventory'] = (len(bindings) == len({row.get('artifact') for row in bindings})
        and {row.get('artifact') for row in bindings} == required_artifacts)
    checks["artifact_digests"] = bool(bindings)
    for binding in bindings:
        relative = safe_artifact_name(binding["artifact"])
        artifact = run / ("controller_private" if binding["authoritative"] else "") / relative
        raw = artifact.read_bytes()
        checks["artifact_digests"] &= hashlib.sha256(raw).hexdigest() == binding["sha256"] and len(raw) == binding["size"]
    checks['payload_validation'] = validate_stage_payloads(run=run, acceptance=acceptance,
        handoff=handoff, candidate_id=candidate_id, workspace=destination, node=node)
    frozen = [name for name in files if name.startswith("tests/") or name in
              {"src/legacy-label.cjs", "jest.config.cjs", "package.json", "README.md"}]
    checks["frozen_assertions_and_unrelated_files"] = all((destination / name).is_file()
        and (destination / name).read_text(encoding="utf-8") == files[name] for name in frozen)
    checks["unrelated_documentation_preserved"] = not (destination / "docs/deployment.md").exists()
    follow_ups = handoff.get("follow_ups", [])
    eligible = [row for row in follow_ups if row.get('review_status') == 'CONFIRMED_OUT_OF_SCOPE'
                and row.get('evidence') and row.get('suggested_next_task')]
    legacy = {row.get('follow_up_id') for row in eligible if set(row.get('paths', [])) & {'src/legacy-label.cjs', 'tests/legacy.jest.cjs'}}
    documentation = {row.get('follow_up_id') for row in eligible if set(row.get('paths', [])) & {'README.md', 'docs/deployment.md'}}
    checks["expected_follow_ups"] = (handoff.get('count') == len(follow_ups)
        and len({row.get('follow_up_id') for row in follow_ups}) == len(follow_ups)
        and any(left and right and left != right for left in legacy for right in documentation))
    replay = run_assertions(destination, node=node, runtime=runtime, output=folder, phase="delivered")
    checks["frozen_assertion_replay"] = all(row["assertion_executed"] for row in replay.values()) and (
        replay["native"]["exit_code"] == 0 and replay["jest"]["exit_code"] == 0 and replay["legacy"]["exit_code"] != 0)
    checks["candidate_stable_after_replay"] = build_candidate_identity(destination) == candidate
    fault_controls = stage_payload_fault_controls(run=run, acceptance=acceptance, handoff=handoff,
        candidate_id=candidate_id, workspace=destination, node=node)
    checks['artifact_fault_controls'] = fault_controls['status'] == 'PASS'
    return dict(status="PASS" if all(checks.values()) else "FAIL", checks=checks, replay=replay,
                actual_candidate=candidate.to_dict(), artifact_fault_controls=fault_controls)


def validate_stage_payloads(*, run: Path, acceptance: dict, handoff: dict, candidate_id: str, workspace: Path, node: Path) -> bool:
    """Validate required actual artifacts, not a producer's list of marker files."""
    from slivin_harness.implementer import validate_implementation_contract, validate_implementation_impact_closure
    from slivin_harness.verification import validate_verification_plan
    from slivin_harness.phase6 import validate_contract_closure_record
    from slivin_harness.evaluator import validate_blind_audit, validate_evaluation_artifact
    from slivin_harness.control_plane import ControllerPlane, SelfVerifyBinding
    from slivin_harness.phase4 import CheckRegistry
    try:
        payloads = {}
        for binding in acceptance['artifact_bindings']:
            name = safe_artifact_name(binding['artifact'])
            if name.endswith('.json'):
                path = run / ('controller_private' if binding['authoritative'] else '') / name
                payloads[name] = json.loads(path.read_text(encoding='utf-8'))
        stages = {row['stage']:row for row in acceptance['stage_bindings']}
        def one(stage, version):
            values = [payloads.get(name) for name in stages[stage]['artifacts']]
            matching = [value for value in values if isinstance(value,dict) and value.get('protocol_version',value.get('schema_version')) == version]
            if len(matching) != 1: raise ValueError('Required stage schema missing/duplicate: '+version)
            return matching[0]
        task = one('intake_preflight','task-contract.v1')
        plan = one('planner','planner.v6')
        tool_evidence = one('planner','planner-tool-evidence.v1')
        contract = one('implementation_contract','implementation-contract.v4')
        verification = one('implementation_contract','verification-plan.v1')
        capability = one('implementation_contract','capability-gate.v1')
        report = one('implementer','implementer.v6')
        impact = one('implementer','implementation-impact-closure.v2')
        audit = one('evaluator','blind-audit.v2')
        evaluation = one('evaluator','evaluator.v7')
        closure = one('evaluator','contract-closure.v1')
        runtime = one('runtime_verification','runtime-evidence.v1')
        if (task['status'] != 'READY' or plan['status'] != 'READY' or tool_evidence['status'] != 'PASS'
            or report['status'] != 'COMPLETE' or evaluation['status'] != 'PASS' or impact['status'] != 'PASS'):
            return False
        validate_implementation_contract(contract)
        validate_verification_plan(verification)
        if any(capability.get(key) != [] for key in ('missing','runtime_requirement_gaps','runtime_environment_gaps','runtime_command_gaps')):
            return False
        if verification['implementation_contract_fingerprint'] != contract['fingerprint']:
            return False
        validate_contract_closure_record(closure, implementation_contract=contract, verification_plan=verification, candidate_id=candidate_id)
        if any(item['status'] not in {'VERIFIED','NOT_AFFECTED'} for item in closure['items']):
            return False
        if impact['candidate_id'] != candidate_id or runtime['candidate_id'] != candidate_id or runtime['verification_plan_fingerprint'] != verification['fingerprint']:
            return False
        if runtime['status'] != ('RUNTIME_VERIFICATION_PASS' if verification['runtime_required'] else 'RUNTIME_VERIFICATION_SKIPPED'):
            return False
        validate_blind_audit(audit, workspace=workspace, candidate_id=candidate_id, changed_paths=acceptance['changed_paths'])
        validate_evaluation_artifact(evaluation, blind_audit=audit, workspace=workspace,
            candidate_id=candidate_id, changed_paths=acceptance['changed_paths'],
            planner_impact_closure=plan['impact_closure'], implementation_impact_closure=impact)
        check_lists = [payloads[name] for name in stages['deterministic_checks']['artifacts']
                       if re.fullmatch(r'checks_\d+\.json',name) and isinstance(payloads.get(name),list)]
        if len(check_lists) != 1 or not {'Owner native content assertions','Owner Jest availability assertions','Git diff hygiene'} <= {row.get('name') for row in check_lists[0]}:
            return False
        for check in check_lists[0]:
            if (check.get('protocol_version') != 'controller-checks.v1' or check.get('classification') != 'CHECK_PASS'
                or check.get('returncode') != 0 or check.get('timed_out') is not False or check.get('infra_error') is not False
                or check.get('candidate_before') != candidate_id or check.get('candidate_after') != candidate_id):
                return False
        # Fixed owner commands are part of the fixture, not agent-authored evidence.
        expected = {
            'Owner native content assertions': ['{node}', str(workspace / 'tests/content.native.cjs')],
            'Owner Jest availability assertions': ['{node}', str(workspace / 'node_modules/jest/bin/jest.js'), '--config', str(workspace / 'jest.config.cjs'), '--runInBand', '--runTestsByPath', str(workspace / 'tests/access.jest.cjs'), '--watch=false'],
            'Git diff hygiene': ['git','diff','--check'],
        }
        actual_owner = {row['name']:row['command'] for row in check_lists[0] if row['name'] in expected}
        node_commands = [actual_owner[name][0] for name in ('Owner native content assertions','Owner Jest availability assertions')]
        if any(Path(command).resolve() != node.resolve() for command in node_commands):
            return False
        def normalized(argv):
            return [os.path.normcase(str(Path(arg).resolve())) if Path(arg).is_absolute() else arg for arg in argv]
        for name, argv in expected.items():
            argv = [node_commands[0] if arg == '{node}' else arg for arg in argv]
            if normalized(actual_owner[name]) != normalized(argv):
                return False
        reconciliation = payloads['quality_gate_reconciliation.json']
        if (reconciliation.get('status') != 'QUALITY_GATE_RECONCILIATION_PASS' or reconciliation.get('candidate_id') != candidate_id
            or reconciliation.get('stage_bindings') != acceptance['stage_bindings'] or reconciliation.get('revision_snapshot') != acceptance['revision_snapshot']):
            return False
        if payloads['user_follow_up_report.json'] != handoff or payloads['patch_proof.json'] != acceptance['patch_proof'] or payloads['reconstructed_verification.json'] != acceptance['reconstructed_verification']:
            return False
        reconstructed = payloads['reconstructed_verification.json']
        if reconstructed.get('candidate_unchanged') is not True or any(reconstructed.get(key) != 'PASS' for key in
                ('static_preflight_status','repair_checks_status','runtime_projection_status','git_control_status')):
            return False
        revisions = acceptance['revision_snapshot']
        registry = CheckRegistry(run / 'controller_private/check_registry.json', workspace=workspace)
        binding = SelfVerifyBinding(candidate_id=candidate_id, task_contract_rev=revisions['task_contract'],
            plan_rev=revisions['plan'], implementation_contract_rev=revisions['implementation_contract'],
            verification_plan_rev=revisions['verification_plan'], runtime_env_id=revisions['runtime_environment'],
            attempt_id=acceptance['attempt_id'], check_registry_digest=registry.digest())
        validate_implementation_impact_closure(impact, candidate_id=candidate_id, plan=plan, contract=contract,
            changed_paths=acceptance['changed_paths'], revision_binding=binding.to_dict())
        # Use the Controller receipt verifier without initializing or writing state.
        plane = object.__new__(ControllerPlane)
        plane.run_root, plane.private_root = run.resolve(), (run / 'controller_private').resolve()
        plane._secret_path = plane.private_root / '.receipt_key'
        return plane.verify_self_verify_receipt(binding=binding)
    except (KeyError, ValueError, TypeError, RuntimeError, OSError):
        return False


def stage_payload_fault_controls(*, run: Path, acceptance: dict, handoff: dict,
                                 candidate_id: str, workspace: Path, node: Path) -> dict:
    """Reject independent counterfeit mutations of one actual successful FULL run."""
    base_valid = validate_stage_payloads(run=run, acceptance=acceptance, handoff=handoff,
        candidate_id=candidate_id, workspace=workspace, node=node)
    payloads = {}
    bindings = {row.get('artifact'): row for row in acceptance.get('artifact_bindings', [])}
    for name, binding in bindings.items():
        name = safe_artifact_name(name)
        if name.endswith('.json'):
            path = (run / ('controller_private' if binding['authoritative'] else '') / name).resolve()
            payloads[path] = json.loads(path.read_text(encoding='utf-8'))
    receipt_path = (run / 'controller_private/self_verify_receipt_current.json').resolve()
    payloads[receipt_path] = json.loads(receipt_path.read_text(encoding='utf-8'))
    stages = {row['stage']: row for row in acceptance.get('stage_bindings', [])}

    def artifact_path(name):
        binding = bindings[name]
        return (run / ('controller_private' if binding['authoritative'] else '')
                / safe_artifact_name(name)).resolve()

    def one_path(stage, version):
        matches = []
        for name in stages[stage]['artifacts']:
            path = artifact_path(name)
            value = payloads.get(path)
            if isinstance(value, dict) and value.get('protocol_version', value.get('schema_version')) == version:
                matches.append(path)
        if len(matches) != 1:
            raise RuntimeError('Fault-control stage schema missing/duplicate: ' + version)
        return matches[0]

    checks_paths = [artifact_path(name) for name in stages['deterministic_checks']['artifacts']
                    if re.fullmatch(r'checks_\d+\.json', name)]
    if len(checks_paths) != 1:
        raise RuntimeError('Fault-control checks artifact missing/duplicate')
    checks_path = checks_paths[0]
    runtime_path = one_path('runtime_verification', 'runtime-evidence.v1')
    capability_path = one_path('implementation_contract', 'capability-gate.v1')
    evaluation_path = one_path('evaluator', 'evaluator.v7')
    audit_path = one_path('evaluator', 'blind-audit.v2')
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in payloads}
    original_read = Path.read_text

    mutations = {
        'owner_commands_replaced_with_noop': (checks_path,
            lambda rows: [dict(row, command=[sys.executable, '-c', 'pass']) for row in rows]),
        'owner_node_replaced_with_python': (checks_path,
            lambda rows: [dict(row, command=[sys.executable, *row['command'][1:]])
                if row.get('name') != 'Git diff hygiene' else row for row in rows]),
        'runtime_reported_failure': (runtime_path, lambda row: dict(row, status='RUNTIME_BEHAVIOR_FAIL')),
        'capability_missing_required': (capability_path, lambda row: dict(row, missing=['JEST'])),
        'stale_evaluator_candidate': (evaluation_path, lambda row: dict(row, candidate_id='STALE')),
        'missing_blind_schema': (audit_path, lambda row: dict(row, protocol_version='unknown')),
        'failed_receipt': (receipt_path, lambda row: dict(row, passed=False)),
    }
    outcomes = {}
    for name, (target, mutate) in mutations.items():
        selected = dict(payloads)
        selected[target] = mutate(json.loads(json.dumps(payloads[target])))

        def read_text(path, *args, **kwargs):
            value = selected.get(path.resolve())
            return json.dumps(value) if value is not None else original_read(path, *args, **kwargs)

        with mock.patch.object(Path, 'read_text', read_text):
            outcomes[name] = not validate_stage_payloads(run=run, acceptance=acceptance, handoff=handoff,
                candidate_id=candidate_id, workspace=workspace, node=node)
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in payloads}
    candidate_unchanged = build_candidate_identity(workspace).candidate_id == candidate_id
    passed = base_valid and all(outcomes.values()) and before == after and candidate_unchanged
    return dict(status='PASS' if passed else 'FAIL', base_payload_valid=base_valid,
                rejected_mutations=outcomes, originals_unchanged=before == after,
                candidate_unchanged=candidate_unchanged)


def execute_case(*, output: Path, label: str, kind: str, node: Path, codex: Path, runtime: Path) -> dict:
    folder = output / label
    folder.mkdir()
    repo = folder / "source"
    repo.mkdir()
    prompt, files = fixtures(kind)
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    git(repo, "init")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Qualification Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "Public generic qualification fixture")
    baseline = git(repo, "rev-parse", "HEAD")
    shutil.copytree(runtime, repo / "node_modules", symlinks=False)
    initial_assertions = run_assertions(repo, node=node, runtime=runtime, output=folder, phase="baseline")
    if not all(row["exit_code"] != 0 and row["assertion_executed"] for row in initial_assertions.values()):
        raise RuntimeError("Public qualification fixture did not execute all expected failing baseline assertions: " + repr(initial_assertions))
    checks = [
        dict(name="Owner native content assertions", feedback="repair", command=["{node}", "{workspace}/tests/content.native.cjs"], timeout_seconds=90),
        dict(name="Owner Jest availability assertions", feedback="repair", command=["{node}", "{jest}", "--config", "{workspace}/jest.config.cjs", "--runInBand", "--runTestsByPath", "{workspace}/tests/access.jest.cjs", "--watch=false"], timeout_seconds=90),
        dict(name="Git diff hygiene", feedback="repair", command=["git", "diff", "--check"], timeout_seconds=30),
    ]
    manifest = folder / "task.toml"
    task_id = "QUALIFY_" + label.upper().replace("-", "_")
    manifest.write_text('\n'.join([
        'version = 2', 'task_id = ' + json.dumps(task_id), 'project = "fixture"', 'workspace_mode = "git_worktree"',
        'result_mode = "keep_worktree"', 'risk = "medium"', 'max_fix_cycles = 2', 'max_replan_cycles = 2',
        'turn_timeout_seconds = 900', 'require_clean_git = true', 'prompt = ' + json.dumps(prompt),
        *['\n[[checks]]\n' + '\n'.join(key + ' = ' + json.dumps(value) for key, value in check.items()) for check in checks],
    ]) + '\n', encoding="utf-8")
    config = folder / "fixture.local.toml"
    config.write_text(f'''[codex]
command = {json.dumps(str(codex))}
[workspace]
root = {json.dumps(str(folder / 'workspaces'))}
[projects.fixture]
repo = {json.dumps(str(repo))}
base_ref = "HEAD"
result_mode = "keep_worktree"
require_clean_source = true
[projects.fixture.toolchain]
node = {json.dumps(str(node))}
jest = "{{project_root}}/node_modules/jest/bin/jest.js"
[projects.fixture.workspace]
copy_untracked = ["node_modules"]
allow_sensitive_copy = false
''', encoding="utf-8")
    env = dict(os.environ, SLIVIN_HARNESS_CONFIG=str(config), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    with (folder / "pipeline.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run([sys.executable, str(ROOT / "task_runner.py"), str(manifest)], cwd=ROOT,
                                   env=env, stdout=log, stderr=subprocess.STDOUT, timeout=9000)
    log_text = (folder / "pipeline.log").read_text(encoding="utf-8")
    matches = re.findall(r"^RUN_DIR:\s*(.+)$", log_text, flags=re.MULTILINE)
    record = dict(label=label, kind=kind, task_id=task_id, model_turns="REAL", exit_code=completed.returncode,
                  baseline_sha=baseline, status="FAIL", prompt=prompt, fixture_sha256=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest())
    if matches:
        run = Path(matches[-1].strip())
        record["run_root"] = str(run)
        acceptance = run / "final_acceptance.json"
        handoff = run / "user_follow_up_report.json"
        delivery = run / "delivery_record.json"
        record["final_acceptance_exists"] = acceptance.is_file()
        record["handoff_exists"] = handoff.is_file()
        record["delivery_exists"] = delivery.is_file()
        if acceptance.is_file() and handoff.is_file() and delivery.is_file():
            record["acceptance"] = json.loads(acceptance.read_text(encoding="utf-8"))
            record["handoff"] = json.loads(handoff.read_text(encoding="utf-8"))
            record["delivery"] = json.loads(delivery.read_text(encoding="utf-8"))
            record["independent_validation"] = verify_delivery(run=run, folder=folder, repo=repo, baseline=baseline,
                task_id=task_id, files=files, acceptance=record["acceptance"], handoff=record["handoff"], delivery=record["delivery"], node=node, runtime=runtime)
            record["status"] = "PASS" if completed.returncode == 0 and record["independent_validation"]["status"] == "PASS" else "FAIL"
    record["baseline_assertions"] = initial_assertions
    record["source_unchanged"] = git(repo, "status", "--short") == "" and git(repo, "rev-parse", "HEAD") == baseline
    if not record["source_unchanged"]:
        record["status"] = "FAIL"
    (folder / "summary.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("REAL_MODEL_CASE", label, record["status"], flush=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("node", "codex", "runtime-source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = asdict(fingerprint_runtime_tree(args.runtime_source))
    cases = []
    # Failures stay in the report; no change to task count, order, prompt or budget.
    for label, kind in (("expiry-1", "expiry"), ("suspension-1", "suspension"), ("expiry-2", "expiry")):
        try:
            cases.append(execute_case(output=args.output, label=label, kind=kind, node=args.node,
                                      codex=args.codex, runtime=args.runtime_source))
        except Exception as error:
            cases.append(dict(label=label, kind=kind, status="FAIL", error_type=type(error).__name__, reason=str(error)))
        (args.output / "summary.json").write_text(json.dumps(dict(status="RUNNING", cases=cases), ensure_ascii=False, indent=2), encoding="utf-8")
    unchanged = before == asdict(fingerprint_runtime_tree(args.runtime_source))
    passed = len(cases) == 3 and all(case["status"] == "PASS" for case in cases) and unchanged
    record = dict(schema_version="real-model-qualification.v1", status="PASS" if passed else "FAIL", cases=cases,
                  runtime_source_unchanged=unchanged, runtime_before=before, doubles=False)
    (args.output / "summary.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
