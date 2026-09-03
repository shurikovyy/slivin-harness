from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import task_runner
from slivin_harness.control_plane import ControllerPlane
from slivin_harness.execution import ExecutionBroker
from slivin_harness.preflight import (
    STATIC_CHECK_INPUT_NOT_FOUND,
    STATIC_COMMAND_TEMPLATE_INVALID,
    STATIC_EXECUTABLE_NOT_FOUND,
    STATIC_JEST_CONFIG_NOT_FOUND,
    STATIC_JEST_CONFIG_PROBE_FAILED,
    STATIC_RUNTIME_INTEGRITY_FAILED,
    STATIC_TOOLCHAIN_MISSING_ENTRY,
    STATIC_TOOLCHAIN_PATH_NOT_FOUND,
    STATIC_TOOLCHAIN_PROBE_FAILED,
    STATIC_TOOLCHAIN_UNKNOWN_PLACEHOLDER,
    CommandTemplateError,
    ToolProbeRegistry,
    expand_check_command,
    extract_command_placeholders,
    run_static_toolchain_preflight,
)
from slivin_harness.runtime_projection import RuntimeProjectionIntegrityManager
from slivin_harness.verification import Capability, available_capabilities
from slivin_harness.workspace import RuntimeProjection, WorkspaceSession


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


class StaticToolchainPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="slivin-static-preflight-"))
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.harness_root = self.root / "harness"
        self.harness_root.mkdir()
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.broker = ExecutionBroker(
            workspace=self.workspace,
            run_root=self.run_root,
            private_root=self.run_root / "controller_private",
        )

    def registry(
        self,
        toolchain: dict[str, str],
        *,
        source_repo: Path | None = None,
        manager: RuntimeProjectionIntegrityManager | None = None,
        historical: bool = False,
        rebound: dict[str, str] | None = None,
    ) -> ToolProbeRegistry:
        return ToolProbeRegistry(
            workspace=self.workspace,
            harness_root=self.harness_root,
            source_repo=source_repo,
            toolchain=toolchain,
            execution_broker=self.broker,
            runtime_integrity_manager=manager,
            historical=historical,
            rebound_to_workspace=rebound or {},
        )

    @staticmethod
    def check(command: list[str], *, name: str = "check", feedback: str = "repair") -> dict:
        return {
            "name": name,
            "feedback": feedback,
            "command": command,
            "timeout_seconds": 30,
        }

    def fake_jest(self, *, behavior: str = "pass", root: Path | None = None) -> Path:
        target_root = root or self.workspace
        script = target_root / "runtime" / "jest.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            """from pathlib import Path
import sys
args = sys.argv[1:]
if '--runTestsByPath' in args:
    Path(__file__).with_name('tests-ran.marker').write_text('ran', encoding='utf-8')
if '--version' in args:
    if __BEHAVIOR__ == 'broken':
        raise SystemExit(7)
    if __BEHAVIOR__ == 'mutate':
        Path(__file__).with_name('dep.js').write_text('mutated', encoding='utf-8')
    print('29.7.0')
    raise SystemExit(0)
if '--showConfig' in args:
    config = Path(args[args.index('--config') + 1])
    text = config.read_text(encoding='utf-8')
    if 'INVALID' in text or 'NO_JSDOM' in text:
        raise SystemExit(8)
    print('{"testEnvironment":"jsdom"}')
    raise SystemExit(0)
raise SystemExit(9)
""".replace("__BEHAVIOR__", repr(behavior)),
            encoding="utf-8",
            newline="\n",
        )
        (script.parent / "dep.js").write_text("pristine", encoding="utf-8")
        return script

    def jest_check(self) -> dict:
        config = self.workspace / "jest.config.cjs"
        config.write_text("module.exports = {};\n", encoding="utf-8")
        test_file = self.workspace / "tests" / "selection.test.cjs"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("test fixture\n", encoding="utf-8")
        return self.check(
            [
                "{node}",
                "{jest}",
                "--config",
                "{workspace}/jest.config.cjs",
                "--runTestsByPath",
                "{workspace}/tests/selection.test.cjs",
                "--runInBand",
            ],
            name="Jest selection",
        )

    def test_template_parser_is_strict_and_expansion_is_canonical(self) -> None:
        command = ["{node}", "{jest}", "{workspace}/test.cjs", "{{literal}}"]
        self.assertEqual(
            extract_command_placeholders(command),
            ("jest", "node", "workspace"),
        )
        expanded = expand_check_command(
            command,
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": "node-value", "jest": "jest-value"},
        )
        self.assertEqual(expanded[-1], "{literal}")
        self.assertEqual(
            expanded,
            task_runner.expand_command(
                command,
                workspace=self.workspace,
                toolchain={"node": "node-value", "jest": "jest-value"},
            ),
        )
        for invalid in (
            ["{tool.path}"],
            ["{tool[0]}"],
            ["{tool!r}"],
            ["{tool:>10}"],
            ["{}"],
            ["{"],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(CommandTemplateError) as caught:
                    extract_command_placeholders(invalid)
                self.assertEqual(caught.exception.reason_code, STATIC_COMMAND_TEMPLATE_INVALID)

    def test_unknown_and_missing_known_placeholders_are_distinct(self) -> None:
        unknown = run_static_toolchain_preflight(
            [self.check(["{mystery}"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={},
            probe_registry=self.registry({}),
        )
        self.assertIn(STATIC_TOOLCHAIN_UNKNOWN_PLACEHOLDER, unknown.reason_codes)
        missing = run_static_toolchain_preflight(
            [self.check(["{node}"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={},
            probe_registry=self.registry({}),
        )
        self.assertIn(STATIC_TOOLCHAIN_MISSING_ENTRY, missing.reason_codes)

    def test_unused_invalid_project_python_is_not_probed(self) -> None:
        result = run_static_toolchain_preflight(
            [self.check(["{python}", "-c", "print('not executed')"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"project_python": str(self.workspace / "missing-python.exe")},
            probe_registry=self.registry(
                {"project_python": str(self.workspace / "missing-python.exe")}
            ),
        )
        self.assertTrue(result.passed, result.public_dict())
        self.assertEqual(
            result.optional_toolchain_entries,
            ({"name": "project_python", "status": "UNUSED_NOT_PROBED"},),
        )
        self.assertEqual([item.probe_id for item in result.probes], ["python.version"])

    def test_post_plan_project_python_is_probed_on_demand(self) -> None:
        registry = self.registry(
            {"project_python": str(self.workspace / "missing-python.exe")}
        )
        evidence = registry.ensure_capabilities(
            [Capability.PROJECT_PYTHON.value],
            batch_id="post-plan",
        )
        self.assertFalse(evidence.passed)
        self.assertIn(STATIC_TOOLCHAIN_PATH_NOT_FOUND, evidence.reason_codes)
        available = available_capabilities(
            toolchain=registry.toolchain,
            configured=[Capability.PROJECT_PYTHON.value],
            verified_tool_capabilities=registry.verified_capabilities,
        )
        self.assertNotIn(Capability.PROJECT_PYTHON.value, available)

    def test_configured_tool_capabilities_require_probe_evidence(self) -> None:
        available = available_capabilities(
            toolchain={"node": "configured", "jest": "configured"},
            configured=[Capability.NODE.value, Capability.JEST.value],
        )
        self.assertNotIn(Capability.NODE.value, available)
        self.assertNotIn(Capability.JEST.value, available)

    def test_absolute_node_and_bare_git_are_probed(self) -> None:
        result = run_static_toolchain_preflight(
            [
                self.check(["{node}", "-c", "print('not executed')"], name="Node"),
                self.check(["git", "diff", "--check"], name="Git"),
            ],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": sys.executable},
            probe_registry=self.registry({"node": sys.executable}),
        )
        self.assertTrue(result.passed, result.public_dict())
        self.assertEqual(
            set(result.verified_capabilities),
            {Capability.GIT.value, Capability.NODE.value},
        )

    def test_missing_bare_executable_and_directory_executable_are_blocked(self) -> None:
        missing = run_static_toolchain_preflight(
            [self.check(["slivin-command-that-does-not-exist-12345"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={},
            probe_registry=self.registry({}),
        )
        self.assertIn(STATIC_EXECUTABLE_NOT_FOUND, missing.reason_codes)
        directory = run_static_toolchain_preflight(
            [self.check([str(self.workspace)])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={},
            probe_registry=self.registry({}),
        )
        self.assertIn(STATIC_EXECUTABLE_NOT_FOUND, directory.reason_codes)

    def test_historical_source_local_tool_is_rejected_without_rebind(self) -> None:
        source = self.root / "source"
        source.mkdir()
        source_node = source / "node.exe"
        source_node.write_text("not executable", encoding="utf-8")
        result = run_static_toolchain_preflight(
            [self.check(["{node}", "--version"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": str(source_node)},
            probe_registry=self.registry(
                {"node": str(source_node)}, source_repo=source, historical=True
            ),
        )
        self.assertIn(STATIC_TOOLCHAIN_PATH_NOT_FOUND, result.reason_codes)
        registry = self.registry({}, source_repo=source, historical=True)
        escaped = json.dumps({"path": str(source)})
        self.assertNotIn(str(source).casefold(), registry._safe_summary(escaped).casefold())
        self.assertIn("<source_repo>", registry._safe_summary(escaped))

    def projection_manager(
        self, *, behavior: str = "pass"
    ) -> tuple[RuntimeProjectionIntegrityManager, Path, Path, Path]:
        source = self.root / "source"
        source.mkdir(exist_ok=True)
        source_jest = self.fake_jest(behavior=behavior, root=source)
        destination = self.workspace / "runtime"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source / "runtime", destination)
        session = WorkspaceSession(
            workspace=self.workspace,
            mode="standalone_historical",
            managed=True,
            source_repo=source,
            runtime_projections=(
                RuntimeProjection(
                    relative_path="runtime",
                    source_kind="workspace.copy_untracked",
                    destination=destination,
                    is_directory=True,
                    copy_mode="physical_copy",
                    runtime_only=True,
                ),
            ),
            benchmark_isolated=True,
        )
        manager = RuntimeProjectionIntegrityManager(
            session=session,
            control_plane=ControllerPlane(self.run_root),
        )
        manager.establish_baseline()
        return manager, source, source_jest, destination / "jest.py"

    def test_rebound_jest_config_probe_passes_without_running_tests(self) -> None:
        manager, source, _source_jest, workspace_jest = self.projection_manager()
        spec = self.jest_check()
        registry = self.registry(
            {"node": sys.executable, "jest": str(workspace_jest)},
            source_repo=source,
            manager=manager,
            historical=True,
            rebound={"jest": "runtime/jest.py"},
        )
        result = run_static_toolchain_preflight(
            [spec],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
        )
        self.assertTrue(result.passed, result.public_dict())
        self.assertEqual(
            {item.probe_id for item in result.probes},
            {"node.version", "jest.version", "jest.config.1"},
        )
        config_probe = next(item for item in result.probes if item.probe_id == "jest.config.1")
        self.assertEqual(config_probe.safe_summary, "")
        self.assertFalse((workspace_jest.parent / "tests-ran.marker").exists())
        public = json.dumps(result.public_dict(), sort_keys=True)
        self.assertNotIn(str(source), public)

    def test_broken_jest_and_invalid_environment_fail_typed_probes(self) -> None:
        broken = self.fake_jest(behavior="broken")
        spec = self.jest_check()
        broken_result = run_static_toolchain_preflight(
            [spec],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": sys.executable, "jest": str(broken)},
            probe_registry=self.registry({"node": sys.executable, "jest": str(broken)}),
        )
        self.assertIn(STATIC_TOOLCHAIN_PROBE_FAILED, broken_result.reason_codes)

        self.fake_jest(behavior="pass")
        (self.workspace / "jest.config.cjs").write_text("NO_JSDOM\n", encoding="utf-8")
        invalid_result = run_static_toolchain_preflight(
            [spec],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={
                "node": sys.executable,
                "jest": str(self.workspace / "runtime" / "jest.py"),
            },
            probe_registry=self.registry(
                {
                    "node": sys.executable,
                    "jest": str(self.workspace / "runtime" / "jest.py"),
                }
            ),
        )
        self.assertIn(STATIC_JEST_CONFIG_PROBE_FAILED, invalid_result.reason_codes)

    def test_missing_jest_config_and_test_input_are_distinct(self) -> None:
        jest = self.fake_jest()
        spec = self.jest_check()
        (self.workspace / "jest.config.cjs").unlink()
        missing_config = run_static_toolchain_preflight(
            [spec],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": sys.executable, "jest": str(jest)},
            probe_registry=self.registry({"node": sys.executable, "jest": str(jest)}),
        )
        self.assertIn(STATIC_JEST_CONFIG_NOT_FOUND, missing_config.reason_codes)
        (self.workspace / "jest.config.cjs").write_text("module.exports = {};\n", encoding="utf-8")
        (self.workspace / "tests" / "selection.test.cjs").unlink()
        missing_test = run_static_toolchain_preflight(
            [spec],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": sys.executable, "jest": str(jest)},
            probe_registry=self.registry({"node": sys.executable, "jest": str(jest)}),
        )
        self.assertIn(STATIC_CHECK_INPUT_NOT_FOUND, missing_test.reason_codes)

    def test_known_script_inputs_are_checked_but_never_executed(self) -> None:
        hidden = self.harness_root / "hidden.cjs"
        marker = self.harness_root / "executed.marker"
        hidden.write_text(
            f"require('fs').writeFileSync({json.dumps(str(marker))}, 'ran');\n",
            encoding="utf-8",
        )
        python_script = self.workspace / "tools" / "check.py"
        python_script.parent.mkdir()
        python_script.write_text("raise SystemExit('must not run')\n", encoding="utf-8")
        result = run_static_toolchain_preflight(
            [
                self.check(["{node}", "--check", "missing.js"], name="Missing syntax"),
                self.check(["{node}", str(hidden)], name="Hidden"),
                self.check(["{python}", "tools/check.py"], name="Python input"),
            ],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": sys.executable},
            probe_registry=self.registry({"node": sys.executable}),
        )
        self.assertIn(STATIC_CHECK_INPUT_NOT_FOUND, result.reason_codes)
        self.assertFalse(marker.exists())

    def test_unknown_command_does_not_guess_argument_paths(self) -> None:
        result = run_static_toolchain_preflight(
            [self.check([sys.executable, "nonexistent-output-name.bin"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={},
            probe_registry=self.registry({}),
        )
        self.assertTrue(result.passed, result.public_dict())

    def test_preflight_restores_workspace_projection_before_probe(self) -> None:
        manager, source, source_jest, workspace_jest = self.projection_manager()
        source_text = source_jest.read_text(encoding="utf-8")
        workspace_jest.write_text("raise SystemExit(99)\n", encoding="utf-8")
        spec = self.jest_check()
        registry = self.registry(
            {"node": sys.executable, "jest": str(workspace_jest)},
            source_repo=source,
            manager=manager,
            historical=True,
            rebound={"jest": "runtime/jest.py"},
        )
        result = run_static_toolchain_preflight(
            [spec],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
        )
        self.assertTrue(result.passed, result.public_dict())
        self.assertEqual(workspace_jest.read_text(encoding="utf-8"), source_text)

    def test_source_change_and_probe_mutation_block_preflight(self) -> None:
        manager, source, source_jest, workspace_jest = self.projection_manager()
        source_jest.write_text("changed source\n", encoding="utf-8")
        registry = self.registry(
            {"node": sys.executable, "jest": str(workspace_jest)},
            source_repo=source,
            manager=manager,
            historical=True,
            rebound={"jest": "runtime/jest.py"},
        )
        source_changed = run_static_toolchain_preflight(
            [self.jest_check()],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
        )
        self.assertEqual(source_changed.reason_codes, (STATIC_RUNTIME_INTEGRITY_FAILED,))

        shutil.rmtree(source)
        manager, source, _source_jest, workspace_jest = self.projection_manager(
            behavior="mutate"
        )
        registry = self.registry(
            {"node": sys.executable, "jest": str(workspace_jest)},
            source_repo=source,
            manager=manager,
            historical=True,
            rebound={"jest": "runtime/jest.py"},
        )
        mutated = run_static_toolchain_preflight(
            [self.jest_check()],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
        )
        self.assertEqual(mutated.reason_codes, (STATIC_RUNTIME_INTEGRITY_FAILED,))
        self.assertEqual(
            (workspace_jest.parent / "dep.js").read_text(encoding="utf-8"),
            "pristine",
        )
        self.assertNotIn(Capability.JEST.value, registry.verified_capabilities)

    def test_static_evidence_is_reused_by_post_plan_gate(self) -> None:
        registry = self.registry({"node": sys.executable})
        static = run_static_toolchain_preflight(
            [self.check(["{node}", "-c", "print('not run')"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
        )
        self.assertTrue(static.passed)
        late = registry.ensure_capabilities(
            [Capability.NODE.value], batch_id="post-plan"
        )
        self.assertEqual(late.probes, ())
        self.assertEqual(late.reused_capabilities, (Capability.NODE.value,))

    def test_new_post_plan_jest_requirement_is_probed_on_demand(self) -> None:
        jest = self.fake_jest()
        registry = self.registry({"node": sys.executable, "jest": str(jest)})
        evidence = registry.ensure_capabilities(
            [Capability.JEST.value], batch_id="post-plan-jest"
        )
        self.assertTrue(evidence.passed, evidence.public_dict())
        self.assertEqual(
            set(registry.verified_capabilities),
            {Capability.NODE.value, Capability.JEST.value},
        )

    def test_matrix_manifest_statically_requires_node_and_jest(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "cases"
            / "matrix-all-matching"
            / "task.toml"
        )
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
        placeholders = {
            placeholder
            for spec in manifest["checks"]
            for placeholder in extract_command_placeholders(spec["command"])
        }
        feedback = {spec["feedback"] for spec in manifest["checks"]}
        self.assertTrue({"node", "jest"} <= placeholders)
        self.assertEqual(feedback, {"repair", "heldout"})


class StaticPreflightWorkflowOrderingTests(unittest.TestCase):
    def test_missing_jest_stops_before_baseline_or_agent_server(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-static-ordering-"))
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        (repo / "jest.config.cjs").write_text("module.exports = {};\n", encoding="utf-8")
        test_file = repo / "selection.test.cjs"
        test_file.write_text("test fixture\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "baseline")
        manifest = root / "task.toml"
        manifest.write_text(
            f'''version = 2
task_id = "STATIC_ORDERING"
project = "demo"
workspace_mode = "git_worktree"
base_ref = "HEAD"
result_mode = "keep_worktree"
risk = "medium"
max_fix_cycles = 0
max_replan_cycles = 0
turn_timeout_seconds = 60
require_clean_git = true
prompt = "Missing Jest must stop early."

[benchmark]
confirm_current_baseline_broken = true
baseline_failure_marker = "ORACLE_REACHED"

[[checks]]
name = "Jest"
feedback = "repair"
command = ["{{node}}", "{{jest}}", "--config", "{{workspace}}/jest.config.cjs", "--runTestsByPath", "{{workspace}}/selection.test.cjs"]
timeout_seconds = 30

[[checks]]
name = "Heldout"
feedback = "heldout"
command = ["{{python}}", "-c", "print('ORACLE_REACHED')"]
timeout_seconds = 30
''',
            encoding="utf-8",
            newline="\n",
        )
        run_root = root / "run"

        class Recorder(task_runner.RunRecorder):
            def __init__(self, _task_id: str) -> None:
                self.root = run_root
                self.root.mkdir(parents=True, exist_ok=True)

        local_config = {
            "workspace": {"root": str(root / "workspaces")},
            "projects": {
                "demo": {
                    "repo": str(repo),
                    "base_ref": "HEAD",
                    "result_mode": "keep_worktree",
                    "toolchain": {"node": sys.executable},
                }
            },
        }
        baseline = mock.Mock(side_effect=AssertionError("baseline must not run"))
        normalizer = mock.Mock(side_effect=AssertionError("normalizer must not run"))
        app_server = mock.Mock(side_effect=AssertionError("app server must not start"))
        output = io.StringIO()
        with (
            mock.patch.object(task_runner, "RunRecorder", Recorder),
            mock.patch.object(task_runner, "load_local_config", return_value=(local_config, None)),
            mock.patch.object(task_runner, "run_benchmark_baseline_gate", baseline),
            mock.patch.object(task_runner, "run_task_contract_normalizer", normalizer),
            mock.patch.object(task_runner, "CodexAppServer", app_server),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            exit_code = task_runner.main([str(manifest)])

        self.assertEqual(exit_code, 2, output.getvalue())
        self.assertIn("STATIC_TOOLCHAIN_PREFLIGHT_FAILED", output.getvalue())
        baseline.assert_not_called()
        normalizer.assert_not_called()
        app_server.assert_not_called()
        artifact = json.loads(
            (run_root / "static_toolchain_preflight.json").read_text(encoding="utf-8")
        )
        self.assertEqual(artifact["schema_version"], "static-toolchain-preflight.v1")
        self.assertEqual(artifact["status"], "FAIL")
        self.assertIn(STATIC_TOOLCHAIN_MISSING_ENTRY, artifact["reason_codes"])
        self.assertNotIn(str(repo), json.dumps(artifact, sort_keys=True))
        self.assertFalse((run_root / "task_contract_01.json").exists())
        self.assertFalse((run_root / "plan_01.json").exists())


if __name__ == "__main__":
    unittest.main()
