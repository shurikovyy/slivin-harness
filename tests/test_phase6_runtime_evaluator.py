from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from slivin_harness.control_plane import ControllerPlane
from slivin_harness.evaluator import (
    run_evaluator,
    validate_blind_audit,
    validate_evaluation_artifact,
)
from slivin_harness.execution import ExecutionBroker, ExecutionRole
from slivin_harness.implementer import build_implementation_contract
from slivin_harness.phase6 import (
    BLIND_AUDIT_VERSION,
    CONTRACT_CLOSURE_VERSION,
    MAX_RUNTIME_RESULT_BYTES,
    Phase6ContractError,
    RuntimeExecutor,
    RuntimeScenarioConfig,
    build_contract_closure_record,
    runtime_available_capabilities,
    runtime_command_gaps,
    runtime_environment_gaps,
    runtime_requirement_gaps,
    runtime_scenarios_from_config,
    validate_contract_closure_record,
)
from slivin_harness.protocol import EVALUATOR_PROTOCOL_VERSION
from slivin_harness.verification import compile_verification_plan
from slivin_harness.workflow import RuntimeStatus
from test_protocol import proof, valid_plan, valid_task_contract


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


PROBE_SOURCE = r'''from pathlib import Path
import json
import os

request = json.loads(Path(os.environ["SLIVIN_RUNTIME_REQUEST"]).read_text(encoding="utf-8"))
profile = request["profile"]
rows = [
    {"item_id": item["item_id"], "status": "PASS", "evidence": ["runtime probe observed the required claim"]}
    for item in request["requirements"]
]
result = {
    "protocol_version": "runtime-result.v1",
    "scenario_id": request["scenario_id"],
    "profile": profile,
    "status": "RUNTIME_VERIFICATION_PASS",
    "summary": "runtime scenario passed",
    "oracle_reached": True,
    "candidate_id": request["candidate_id"],
    "requirement_results": rows,
    "initial_state_confirmed": profile != "PROD_OBSERVE",
    "fresh_readback_confirmed": profile == "TEST_EXTERNAL",
    "cleanup": {
        "required": profile == "TEST_EXTERNAL",
        "confirmed": profile != "TEST_EXTERNAL",
        "evidence": ["cleanup is performed by the Controller-owned cleanup command"],
    },
    "read_only_confirmed": profile == "PROD_OBSERVE",
}
Path(os.environ["SLIVIN_RUNTIME_RESULT"]).write_text(json.dumps(result), encoding="utf-8")
'''

MUTATING_PROBE_SOURCE = r'''from pathlib import Path
exec(Path("runtime_probe.py").read_text(encoding="utf-8"))
Path("target.txt").write_text("mutated by runtime\n", encoding="utf-8")
'''

HEALTH_SOURCE = r'''import socket
import sys
with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1):
    pass
'''


class RuntimePhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="slivin-phase6-runtime-"))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "target.txt").write_text("baseline\n", encoding="utf-8")
        (self.repo / "runtime_probe.py").write_text(PROBE_SOURCE, encoding="utf-8")
        (self.repo / "mutating_probe.py").write_text(
            MUTATING_PROBE_SOURCE, encoding="utf-8"
        )
        (self.repo / "health.py").write_text(HEALTH_SOURCE, encoding="utf-8")
        (self.repo / "nonzero_probe.py").write_text(
            'from pathlib import Path\nexec(Path("runtime_probe.py").read_text(encoding="utf-8"))\nraise SystemExit(3)\n',
            encoding="utf-8",
        )
        (self.repo / "cleanup.py").write_text(
            'from pathlib import Path\nPath(".harness_tmp/runtime/cleanup.txt").parent.mkdir(parents=True, exist_ok=True)\nPath(".harness_tmp/runtime/cleanup.txt").write_text("done")\n',
            encoding="utf-8",
        )
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "baseline")
        self.run_root = self.root / "run"
        self.private_root = self.run_root / "controller_private"
        self.private_root.mkdir(parents=True)
        self.broker = ExecutionBroker(
            workspace=self.repo,
            run_root=self.run_root,
            private_root=self.private_root,
            base_env={},
        )
        self.toolchain = {
            "python": sys.executable,
            "project_python": sys.executable,
        }

    def runtime_plan(self, *, level: str, capabilities: list[str]) -> tuple[dict, dict]:
        plan = valid_plan()
        plan["evidence_plan"]["regression"] = [
            proof("Runtime acceptance is observed.", level=level, capabilities=capabilities)
        ]
        contract = build_implementation_contract(
            plan, task_contract=valid_task_contract()
        )
        return contract, compile_verification_plan(contract, project_checks=[])

    def executor(self) -> RuntimeExecutor:
        return RuntimeExecutor(
            workspace=self.repo,
            source_repo=None,
            toolchain=self.toolchain,
            execution_broker=self.broker,
        )

    def test_runtime_environment_gap_is_detected_before_execution(self) -> None:
        scenario = RuntimeScenarioConfig(
            scenario_id="external-with-secret",
            profile="TEST_EXTERNAL",
            capabilities=("TEST_EXTERNAL_WRITE", "TEST_EXTERNAL_FRESH_READ"),
            command=(sys.executable, "runtime_probe.py"),
            cleanup_command=(sys.executable, "cleanup.py"),
            preserve_env=("ONEC_TEST_TOKEN",),
        )
        self.assertEqual(
            runtime_environment_gaps(
                (scenario,),
                verification_plan=self.runtime_plan(
                    level="TEST_EXTERNAL",
                    capabilities=["TEST_EXTERNAL_WRITE", "TEST_EXTERNAL_FRESH_READ"],
                )[1],
                execution_broker=self.broker,
            ),
            ["external-with-secret:missing_env:ONEC_TEST_TOKEN"],
        )

    def test_unused_runtime_scenario_missing_secret_does_not_block_task(self) -> None:
        live = RuntimeScenarioConfig(
            scenario_id="live",
            profile="LIVE_LOCAL",
            capabilities=("LOCAL_APP",),
            command=(sys.executable, "runtime_probe.py"),
        )
        unused_external = RuntimeScenarioConfig(
            scenario_id="unused-external",
            profile="TEST_EXTERNAL",
            capabilities=("TEST_EXTERNAL_WRITE", "TEST_EXTERNAL_FRESH_READ"),
            command=(sys.executable, "runtime_probe.py"),
            cleanup_command=(sys.executable, "cleanup.py"),
            preserve_env=("ONEC_TEST_TOKEN",),
        )
        verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP"]
        )[1]
        self.assertEqual(
            runtime_environment_gaps(
                (live, unused_external),
                verification_plan=verification,
                execution_broker=self.broker,
            ),
            [],
        )

    def test_runtime_command_gap_checks_only_selected_scenario(self) -> None:
        live = RuntimeScenarioConfig(
            scenario_id="live",
            profile="LIVE_LOCAL",
            capabilities=("LOCAL_APP",),
            command=(sys.executable, "runtime_probe.py"),
        )
        unused = RuntimeScenarioConfig(
            scenario_id="unused",
            profile="PROD_OBSERVE",
            capabilities=("PROD_READ_ONLY",),
            command=("definitely-missing-phase6-executable",),
            read_only_enforced=True,
        )
        verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP"]
        )[1]
        self.assertEqual(
            runtime_command_gaps(
                verification,
                (live, unused),
                workspace=self.repo,
                toolchain=self.toolchain,
            ),
            [],
        )

        selected_plan = self.runtime_plan(
            level="PROD_OBSERVE", capabilities=["PROD_READ_ONLY"]
        )[1]
        gaps = runtime_command_gaps(
            selected_plan,
            (live, unused),
            workspace=self.repo,
            toolchain=self.toolchain,
        )
        self.assertEqual(len(gaps), 1)
        self.assertIn("definitely-missing-phase6-executable", gaps[0])

    def test_runtime_config_is_typed_and_prod_observe_requires_read_only(self) -> None:
        config = {
            "projects": {
                "demo": {
                    "runtime_verification": {
                        "enabled": True,
                        "scenarios": [
                            {
                                "id": "prod-read",
                                "profile": "PROD_OBSERVE",
                                "capabilities": ["PROD_READ_ONLY"],
                                "command": [sys.executable, "runtime_probe.py"],
                                "read_only_enforced": True,
                            }
                        ],
                    }
                }
            }
        }
        scenarios = runtime_scenarios_from_config(config, project_name="demo")
        self.assertEqual(scenarios[0].profile, "PROD_OBSERVE")
        self.assertIn("PROD_OBSERVE_RUNTIME", scenarios[0].advertised_capabilities)

        config["projects"]["demo"]["runtime_verification"]["scenarios"][0][
            "read_only_enforced"
        ] = False
        with self.assertRaisesRegex(Phase6ContractError, "read_only_enforced"):
            runtime_scenarios_from_config(config, project_name="demo")

        config["projects"]["demo"]["runtime_verification"]["scenarios"][0][
            "read_only_enforced"
        ] = "true"
        with self.assertRaisesRegex(Phase6ContractError, "must be boolean"):
            runtime_scenarios_from_config(config, project_name="demo")

    def test_requirement_gap_is_per_scenario_not_union_of_capabilities(self) -> None:
        _contract, verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP", "BROWSER_DOM"]
        )
        scenarios = (
            RuntimeScenarioConfig(
                scenario_id="local-app",
                profile="LIVE_LOCAL",
                capabilities=("LOCAL_APP",),
                command=(sys.executable, "runtime_probe.py"),
            ),
            RuntimeScenarioConfig(
                scenario_id="unrelated-browser",
                profile="PROD_OBSERVE",
                capabilities=("BROWSER_DOM", "PROD_READ_ONLY"),
                command=(sys.executable, "runtime_probe.py"),
                read_only_enforced=True,
            ),
        )
        self.assertIn("LOCAL_APP", runtime_available_capabilities(scenarios))
        gaps = runtime_requirement_gaps(verification, scenarios)
        self.assertEqual(len(gaps), 1)
        self.assertIn("ACCEPTANCE-1:LIVE_LOCAL", gaps[0])

    def test_live_local_start_health_and_runtime_pass(self) -> None:
        _contract, verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP"]
        )
        scenario = RuntimeScenarioConfig(
            scenario_id="live-local",
            profile="LIVE_LOCAL",
            capabilities=("LOCAL_APP",),
            startup_command=(
                sys.executable,
                "-m",
                "http.server",
                "{runtime_port}",
                "--bind",
                "127.0.0.1",
            ),
            health_command=(sys.executable, "health.py", "{runtime_port}"),
            command=(sys.executable, "runtime_probe.py"),
            timeout_seconds=30,
            startup_timeout_seconds=15,
            health_interval_seconds=0.1,
        )
        record = self.executor().execute(verification, (scenario,))
        self.assertEqual(record.status, RuntimeStatus.PASS.value)
        self.assertEqual(record.scenarios[0].status, RuntimeStatus.PASS.value)
        self.assertEqual(
            record.scenarios[0].candidate_before,
            record.scenarios[0].candidate_after,
        )

    def test_test_external_requires_readback_and_cleanup_boundary(self) -> None:
        _contract, verification = self.runtime_plan(
            level="TEST_EXTERNAL",
            capabilities=["TEST_EXTERNAL_WRITE", "TEST_EXTERNAL_FRESH_READ"],
        )
        with self.assertRaisesRegex(Phase6ContractError, "cleanup_command"):
            RuntimeScenarioConfig(
                scenario_id="external-no-cleanup",
                profile="TEST_EXTERNAL",
                capabilities=("TEST_EXTERNAL_WRITE", "TEST_EXTERNAL_FRESH_READ"),
                command=(sys.executable, "runtime_probe.py"),
            )
        scenario = RuntimeScenarioConfig(
            scenario_id="external",
            profile="TEST_EXTERNAL",
            capabilities=("TEST_EXTERNAL_WRITE", "TEST_EXTERNAL_FRESH_READ"),
            command=(sys.executable, "runtime_probe.py"),
            cleanup_command=(sys.executable, "cleanup.py"),
        )
        record = self.executor().execute(verification, (scenario,))
        self.assertEqual(record.status, RuntimeStatus.PASS.value)
        self.assertTrue(record.scenarios[0].result["cleanup"]["confirmed"])
        self.assertIn("done", (self.repo / ".harness_tmp/runtime/cleanup.txt").read_text())

    def test_nonzero_command_cannot_smuggle_a_green_result(self) -> None:
        _contract, verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP"]
        )
        scenario = RuntimeScenarioConfig(
            scenario_id="nonzero",
            profile="LIVE_LOCAL",
            capabilities=("LOCAL_APP",),
            command=(sys.executable, "nonzero_probe.py"),
        )
        record = self.executor().execute(verification, (scenario,))
        self.assertEqual(record.status, RuntimeStatus.INFRA_ERROR.value)
        self.assertEqual(record.reason_code, "RUNTIME_COMMAND_NONZERO")

    def test_runtime_mutation_overrides_green_scenario_result(self) -> None:
        _contract, verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP"]
        )
        scenario = RuntimeScenarioConfig(
            scenario_id="mutating",
            profile="LIVE_LOCAL",
            capabilities=("LOCAL_APP",),
            command=(sys.executable, "mutating_probe.py"),
        )
        record = self.executor().execute(verification, (scenario,))
        self.assertEqual(record.status, RuntimeStatus.MUTATED_CANDIDATE.value)
        self.assertEqual(record.reason_code, "RUNTIME_MUTATED_CANDIDATE")

    def test_runtime_rejects_oversized_structured_result(self) -> None:
        script = self.repo / "oversized_result.py"
        script.write_text(
            """from __future__ import annotations
import os
from pathlib import Path
Path(os.environ['SLIVIN_RUNTIME_RESULT']).write_text('x' * (2_000_000 + 1), encoding='utf-8')
""",
            encoding="utf-8",
            newline="\n",
        )
        scenario = RuntimeScenarioConfig(
            scenario_id="oversized",
            profile="LIVE_LOCAL",
            capabilities=("LOCAL_APP",),
            command=(sys.executable, str(script)),
        )
        _, verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP"]
        )
        record = self.executor().execute(verification, [scenario])
        self.assertEqual(record.status, "RUNTIME_INVALID_RESULT")
        self.assertIn(str(MAX_RUNTIME_RESULT_BYTES), record.scenarios[0].result["error"])

    def test_runtime_redacts_preserved_secret_from_logs_and_result(self) -> None:
        secret = "phase6-super-secret"
        script = self.repo / "secret_probe.py"
        script.write_text(
            f"""from __future__ import annotations
import json
import os
from pathlib import Path
request = json.loads(Path(os.environ['SLIVIN_RUNTIME_REQUEST']).read_text(encoding='utf-8'))
secret = os.environ['ONEC_TOKEN']
print(secret)
result = {{
    'protocol_version': 'runtime-result.v1',
    'scenario_id': request['scenario_id'],
    'profile': request['profile'],
    'status': 'RUNTIME_VERIFICATION_PASS',
    'summary': secret,
    'oracle_reached': True,
    'candidate_id': request['candidate_id'],
    'requirement_results': [
        {{'item_id': row['item_id'], 'status': 'PASS', 'evidence': [secret]}}
        for row in request['requirements']
    ],
    'initial_state_confirmed': True,
    'fresh_readback_confirmed': False,
    'cleanup': {{'required': False, 'confirmed': True, 'evidence': []}},
    'read_only_confirmed': False,
}}
Path(os.environ['SLIVIN_RUNTIME_RESULT']).write_text(json.dumps(result), encoding='utf-8')
""",
            encoding="utf-8",
            newline="\n",
        )
        scenario = RuntimeScenarioConfig(
            scenario_id="secret-runtime",
            profile="LIVE_LOCAL",
            capabilities=("LOCAL_APP",),
            command=(sys.executable, str(script)),
            preserve_env=("ONEC_TOKEN",),
        )
        _, verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP"]
        )
        broker = ExecutionBroker(
            workspace=self.repo,
            run_root=self.root / "secret-run",
            private_root=self.root / "secret-run" / "controller_private",
            base_env={"PATH": os.environ.get("PATH", ""), "ONEC_TOKEN": secret},
        )
        executor = RuntimeExecutor(
            workspace=self.repo,
            source_repo=None,
            toolchain={"project_python": sys.executable},
            execution_broker=broker,
        )
        record = executor.execute(verification, [scenario])
        public_text = json.dumps(record.public_record(), ensure_ascii=False)
        private_text = json.dumps(record.private_record(), ensure_ascii=False)
        self.assertNotIn(secret, public_text)
        self.assertNotIn(secret, private_text)
        self.assertIn("<redacted>", private_text)
        runtime_scratch = broker.scratch_root(ExecutionRole.RUNTIME)
        self.assertFalse(any(runtime_scratch.glob("scenario_*_secret-runtime")))

    def test_runtime_detects_mutation_of_the_source_checkout(self) -> None:
        source = self.root / "source"
        source.mkdir()
        git(source, "init")
        git(source, "config", "user.name", "Test")
        git(source, "config", "user.email", "test@example.invalid")
        source_target = source / "source.txt"
        source_target.write_text("source baseline\n", encoding="utf-8")
        git(source, "add", "-A")
        git(source, "commit", "-m", "source baseline")
        (self.repo / "source_mutator.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(source_target)!r}).write_text('changed\\n', encoding='utf-8')\n"
            "exec(Path('runtime_probe.py').read_text(encoding='utf-8'))\n",
            encoding="utf-8",
        )
        git(self.repo, "add", "source_mutator.py")
        git(self.repo, "commit", "-m", "source mutation probe")
        _contract, verification = self.runtime_plan(
            level="LIVE_LOCAL", capabilities=["LOCAL_APP"]
        )
        scenario = RuntimeScenarioConfig(
            scenario_id="source-mutator",
            profile="LIVE_LOCAL",
            capabilities=("LOCAL_APP",),
            command=(sys.executable, "source_mutator.py"),
        )
        executor = RuntimeExecutor(
            workspace=self.repo,
            source_repo=source,
            toolchain=self.toolchain,
            execution_broker=self.broker,
        )
        record = executor.execute(verification, (scenario,))
        self.assertEqual(record.status, RuntimeStatus.MUTATED_CANDIDATE.value)
        self.assertEqual(record.reason_code, "RUNTIME_MUTATED_SOURCE")

    def test_contract_closure_is_controller_normalized_and_fingerprinted(self) -> None:
        contract, verification = self.runtime_plan(
            level="LOCAL_DETERMINISTIC", capabilities=[]
        )
        report = {
            "contract_evidence": [
                {
                    "item_id": item["id"],
                    "status": "VERIFIED",
                    "evidence": [f"evidence for {item['id']}"],
                }
                for item in contract["items"]
            ]
        }
        from slivin_harness.run_state import build_candidate_identity

        candidate_id = build_candidate_identity(self.repo).candidate_id
        closure = build_contract_closure_record(
            implementation_contract=contract,
            verification_plan=verification,
            implementation_report=report,
            candidate_id=candidate_id,
        )
        self.assertEqual(closure["protocol_version"], CONTRACT_CLOSURE_VERSION)
        validate_contract_closure_record(
            closure,
            implementation_contract=contract,
            verification_plan=verification,
            candidate_id=candidate_id,
        )
        closure["items"][0]["accepted_evidence"] = ["tampered"]
        with self.assertRaisesRegex(Phase6ContractError, "fingerprint"):
            validate_contract_closure_record(
                closure,
                implementation_contract=contract,
                verification_plan=verification,
                candidate_id=candidate_id,
            )


class _FakeEvaluatorServer:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = [json.dumps(value) for value in responses]
        self.prompts: list[str] = []
        self.thread_ids: list[str] = []
        self.sandboxes: list[str] = []

    def start_thread(self, **kwargs) -> str:
        self.sandboxes.append(kwargs["sandbox"])
        return "fresh-evaluator-thread"

    def run_turn(self, **kwargs) -> str:
        self.thread_ids.append(kwargs["thread_id"])
        self.prompts.append(kwargs["prompt"])
        return self.responses.pop(0)


class TwoPhaseEvaluatorTests(unittest.TestCase):
    def test_two_phase_prompt_is_blind_then_contract_aware(self) -> None:
        audit = {
            "protocol_version": BLIND_AUDIT_VERSION,
            "summary": "Blind repository audit found no material defect.",
            "findings": [],
            "advisories": [],
        }
        verdict = {
            "protocol_version": EVALUATOR_PROTOCOL_VERSION,
            "status": "PASS",
            "summary": "Known obligations and blind audit are satisfied.",
            "blind_finding_dispositions": [],
            "findings": [],
            "reason": "",
        }
        server = _FakeEvaluatorServer([audit, verdict])
        phases: list[str] = []
        persisted: list[dict] = []
        contract = {"protocol_version": "implementation-contract.v3", "items": []}
        verification = {"protocol_version": "verification-plan.v1"}
        closure = {"protocol_version": CONTRACT_CLOSURE_VERSION}
        observed_audit, observed_verdict = run_evaluator(
            server,  # type: ignore[arg-type]
            workspace=Path(tempfile.mkdtemp(prefix="slivin-evaluator-phase6-")),
            task_prompt="Fix the target behavior.",
            task_contract={"explicit_acceptance": []},
            preflight={"head_sha": "abc"},
            owner_allowed_paths=[],
            changed_paths=["target.txt"],
            candidate_id="candidate-1",
            implementation_contract=contract,
            verification_plan=verification,
            contract_closure=closure,
            checks_evidence={"checks": []},
            runtime_evidence={"status": "RUNTIME_VERIFICATION_SKIPPED"},
            on_blind_audit=persisted.append,
            on_phase_complete=phases.append,
            timeout=30,
        )
        self.assertEqual(observed_audit, audit)
        self.assertEqual(observed_verdict, verdict)
        self.assertEqual(server.sandboxes, ["read-only"])
        self.assertEqual(server.thread_ids, ["fresh-evaluator-thread"] * 2)
        self.assertNotIn("IMPLEMENTATION CONTRACT", server.prompts[0])
        self.assertNotIn("CONTROLLER EVIDENCE", server.prompts[0])
        self.assertIn("IMPLEMENTATION CONTRACT", server.prompts[1])
        self.assertIn("CONTRACT CLOSURE RECORD", server.prompts[1])
        self.assertEqual(persisted, [audit])
        self.assertEqual(phases, ["PHASE_A", "PHASE_B"])

    def test_retained_blind_finding_must_survive_final_verdict(self) -> None:
        finding = {
            "finding_id": "BLIND-1",
            "severity": "MEDIUM",
            "category": "CONSUMER",
            "title": "Sibling consumer is broken",
            "evidence": ["The sibling calls the changed helper."],
            "failure_mode": "The sibling receives incompatible behavior.",
            "required_action": "Preserve the sibling contract.",
            "required_proof": proof("The sibling behavior remains unchanged."),
        }
        audit = {
            "protocol_version": BLIND_AUDIT_VERSION,
            "summary": "One material consumer finding.",
            "findings": [finding],
            "advisories": [],
        }
        validate_blind_audit(audit)
        verdict = {
            "protocol_version": EVALUATOR_PROTOCOL_VERSION,
            "status": "PASS",
            "summary": "Incorrectly claims pass.",
            "blind_finding_dispositions": [
                {
                    "finding_id": "BLIND-1",
                    "disposition": "RETAINED",
                    "evidence": ["The finding remains reachable."],
                }
            ],
            "findings": [finding],
            "reason": "",
        }
        with self.assertRaisesRegex(RuntimeError, "PASS requires no findings"):
            validate_evaluation_artifact(verdict, blind_audit=audit)


if __name__ == "__main__":
    unittest.main()
