from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import task_runner
import slivin_harness.preflight as preflight_module
from slivin_harness.control_plane import ArtifactVisibility, ControllerPlane
from slivin_harness.execution import ExecutionBroker
from slivin_harness.git_integrity import (
    CandidateWorkspaceBaseline,
    GitControlIntegrityManager,
)
from slivin_harness.preflight import (
    STATIC_CHECK_INPUT_NOT_FOUND,
    STATIC_COMMAND_TEMPLATE_INVALID,
    STATIC_EXECUTABLE_NOT_FOUND,
    STATIC_GIT_CONTROL_INTEGRITY_FAILED,
    STATIC_JEST_CONFIG_NOT_FOUND,
    STATIC_JEST_CONFIG_PROBE_FAILED,
    STATIC_PREFLIGHT_MUTATED_CANDIDATE,
    STATIC_RUNTIME_INTEGRITY_FAILED,
    STATIC_TOOLCHAIN_MISSING_ENTRY,
    STATIC_TOOLCHAIN_PATH_NOT_FOUND,
    STATIC_TOOLCHAIN_PROBE_FAILED,
    STATIC_TOOLCHAIN_PROBE_OUTPUT_LIMIT,
    STATIC_TOOLCHAIN_UNKNOWN_PLACEHOLDER,
    CommandTemplateError,
    ToolProbeRegistry,
    expand_check_command,
    extract_command_placeholders,
    resolve_python_command,
    run_static_toolchain_preflight,
)
from slivin_harness.run_state import build_candidate_identity
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
        self.control_plane = ControllerPlane(self.run_root)
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
        git_manager: GitControlIntegrityManager | None = None,
        historical: bool = False,
        rebound: dict[str, str] | None = None,
        probe_timeout_seconds: int = 30,
    ) -> ToolProbeRegistry:
        return ToolProbeRegistry(
            workspace=self.workspace,
            harness_root=self.harness_root,
            source_repo=source_repo,
            toolchain=toolchain,
            execution_broker=self.broker,
            control_plane=self.control_plane,
            runtime_integrity_manager=manager,
            git_integrity_manager=git_manager,
            historical=historical,
            rebound_to_workspace=rebound or {},
            probe_timeout_seconds=probe_timeout_seconds,
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
    if __BEHAVIOR__ in {'mutate', 'mutate_both'}:
        Path(__file__).with_name('dep.js').write_text('mutated', encoding='utf-8')
    if __BEHAVIOR__ == 'mutate_both':
        (Path.cwd() / 'candidate.txt').write_text('mutated', encoding='utf-8')
    print('29.7.0')
    raise SystemExit(0)
if '--showConfig' in args:
    config = (Path(args[args.index('--config') + 1])
              if '--config' in args else Path.cwd() / 'jest.config.cjs')
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

    def commit_candidate(self) -> str:
        git(self.workspace, "init")
        git(self.workspace, "config", "user.name", "Static Preflight Test")
        git(self.workspace, "config", "user.email", "static-preflight@example.invalid")
        git(self.workspace, "add", ".")
        git(self.workspace, "commit", "-m", "candidate baseline")
        baseline = git(self.workspace, "rev-parse", "HEAD")
        CandidateWorkspaceBaseline.capture(
            self.workspace,
            baseline_sha=baseline,
            excluded_prefixes=(".git", ".harness_tmp", ".venv", ".harness_git_excludes"),
        )
        return baseline

    def candidate_mutating_jest(
        self,
        action: str,
        *,
        sentinel: str = "SECRET_LIKE_PREFLIGHT_SENTINEL",
        delay_seconds: int = 0,
    ) -> Path:
        script = self.fake_jest()
        actions = {
            "tracked": "Path('candidate.txt').write_text('mutated', encoding='utf-8')",
            "untracked": "Path('created-by-config.txt').write_text('created', encoding='utf-8')",
            "deleted": "Path('candidate.txt').unlink()",
            "scratch": "(Path('.harness_tmp') / 'config.marker').write_text('runtime', encoding='utf-8')",
            "git-exclude": "Path('.git/info/exclude').open('a', encoding='utf-8').write('stealth.js\\n'); Path('stealth.js').write_text('hidden candidate', encoding='utf-8')",
        }
        script.write_text(
            f"""from pathlib import Path
import sys
import time
args = sys.argv[1:]
if '--version' in args:
    print('29.7.0')
    raise SystemExit(0)
if '--showConfig' in args:
    print({sentinel!r})
    {actions[action]}
    time.sleep({delay_seconds})
    raise SystemExit(0)
raise SystemExit(9)
""",
            encoding="utf-8",
            newline="\n",
        )
        return script

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
                self.assertEqual(
                    caught.exception.reason_code,
                    STATIC_COMMAND_TEMPLATE_INVALID,
                )

    def test_python_placeholders_preserve_project_first_contract(self) -> None:
        project_python = str(self.root / "project-python.exe")
        configured_python = str(self.root / "configured-python.exe")
        toolchain = {
            "project_python": project_python,
            "python": configured_python,
        }
        self.assertEqual(resolve_python_command(toolchain).value, project_python)
        self.assertEqual(
            resolve_python_command({"python": configured_python}).value,
            configured_python,
        )
        self.assertEqual(resolve_python_command({}).value, str(Path(sys.executable).resolve()))
        self.assertEqual(
            resolve_python_command(
                {"project_python": "missing"}, placeholder="harness_python"
            ).value,
            str(Path(sys.executable).resolve()),
        )
        expanded = expand_check_command(
            ["{python}", "{harness_python}"],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=toolchain,
        )
        self.assertEqual(expanded, [project_python, str(Path(sys.executable).resolve())])
        self.assertEqual(
            expanded,
            task_runner.expand_command(
                ["{python}", "{harness_python}"],
                workspace=self.workspace,
                toolchain=toolchain,
            ),
        )

    def test_python_project_binding_requires_probe_evidence(self) -> None:
        venv = self.workspace / ".venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        project_python = (
            venv / "Scripts" / "python.exe"
            if os.name == "nt"
            else venv / "bin" / "python"
        )
        registry = self.registry({"project_python": str(project_python)})
        result = run_static_toolchain_preflight(
            [self.check(["{python}", "-c", "print('not executed')"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
        )
        self.assertTrue(result.passed, result.public_dict())
        self.assertIn(Capability.PROJECT_PYTHON.value, result.verified_capabilities)
        self.assertIn("project_python", result.required_toolchain_entries)
        self.assertIn("project-python.version", [item.probe_id for item in result.probes])

    def test_project_python_resolver_returns_lexical_entrypoint_not_leaf_canonical_target(self) -> None:
        entrypoint = self.workspace / ".venv" / "Scripts" / "python.exe"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_bytes(b"test executable entrypoint")
        registry = self.registry({"project_python": str(entrypoint)})
        real_canonical = preflight_module.canonical_path
        external_target = self.root / "bootstrap-python"

        def canonical_without_leaf(value):
            candidate = Path(value)
            if candidate == entrypoint:
                return external_target
            return real_canonical(candidate)

        with mock.patch.object(
            preflight_module,
            "canonical_path",
            side_effect=canonical_without_leaf,
        ):
            resolved = registry._resolve_project_python_execution_path(str(entrypoint))

        self.assertEqual(resolved, entrypoint.absolute())
        self.assertNotEqual(resolved, external_target)

    def test_configured_python_binding_is_probed_without_project_capability(self) -> None:
        registry = self.registry({"python": sys.executable})
        result = run_static_toolchain_preflight(
            [self.check(["{python}", "-c", "print('not executed')"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
        )
        self.assertTrue(result.passed, result.public_dict())
        self.assertIn("python", result.required_toolchain_entries)
        self.assertEqual([item.probe_id for item in result.probes], ["python.version"])
        self.assertNotIn(Capability.PROJECT_PYTHON.value, result.verified_capabilities)

    def test_explicit_project_python_is_missing_without_entry(self) -> None:
        result = run_static_toolchain_preflight(
            [self.check(["{project_python}", "--version"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={},
            probe_registry=self.registry({}),
        )
        self.assertIn(STATIC_TOOLCHAIN_MISSING_ENTRY, result.reason_codes)

    def test_dynamic_python_check_uses_shared_project_first_resolver(self) -> None:
        test_path = self.workspace / "tests" / "test_dynamic.py"
        test_path.parent.mkdir(parents=True)
        test_path.write_text("pass\n", encoding="utf-8")
        specs, notes = task_runner.build_dynamic_check_specs(
            ["tests/test_dynamic.py"],
            workspace=self.workspace,
            toolchain={"project_python": "project-python", "python": "configured-python"},
            base_specs=[
                {
                    "command": ["{python}", "-m", "pytest", "tests/test_base.py"],
                }
            ],
        )
        self.assertFalse(notes)
        self.assertEqual(specs[0]["command"][0], "project-python")

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
            [self.check(["{harness_python}", "-c", "print('not executed')"])],
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

    def test_jest_config_auto_discovery_is_probed_once_without_running_tests(self) -> None:
        config = self.workspace / "jest.config.cjs"
        config.write_text("module.exports = {};\n", encoding="utf-8")
        test_file = self.workspace / "tests" / "selection.test.cjs"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("fixture\n", encoding="utf-8")
        jest = self.fake_jest()
        command = [
            "{node}",
            "{jest}",
            "--runTestsByPath",
            "{workspace}/tests/selection.test.cjs",
            "--runInBand",
        ]
        registry = self.registry({"node": sys.executable, "jest": str(jest)})
        result = run_static_toolchain_preflight(
            [self.check(command, name="auto one"), self.check(command, name="auto two")],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
        )
        self.assertTrue(result.passed, result.public_dict())
        config_probes = [
            item for item in registry.private_probe_commands
            if item["id"].startswith("jest.config.")
        ]
        self.assertEqual(len(config_probes), 1)
        self.assertNotIn("--config", config_probes[0]["argv"])
        self.assertFalse((jest.parent / "tests-ran.marker").exists())

    def test_jest_explicit_equals_config_and_auto_failure_are_typed(self) -> None:
        config = self.workspace / "jest.config.cjs"
        config.write_text("module.exports = {};\n", encoding="utf-8")
        jest = self.fake_jest()
        explicit = run_static_toolchain_preflight(
            [
                self.check(
                    ["{node}", "{jest}", "--config={workspace}/jest.config.cjs"]
                )
            ],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": sys.executable, "jest": str(jest)},
            probe_registry=self.registry({"node": sys.executable, "jest": str(jest)}),
        )
        self.assertTrue(explicit.passed, explicit.public_dict())

        config.unlink()
        auto_failed = run_static_toolchain_preflight(
            [self.check(["{node}", "{jest}"])],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain={"node": sys.executable, "jest": str(jest)},
            probe_registry=self.registry({"node": sys.executable, "jest": str(jest)}),
        )
        self.assertIn(STATIC_JEST_CONFIG_PROBE_FAILED, auto_failed.reason_codes)
        self.assertNotIn(STATIC_JEST_CONFIG_NOT_FOUND, auto_failed.reason_codes)

    def _run_candidate_mutation(self, action: str, *, delay_seconds: int = 0):
        sentinel = "SECRET_LIKE_PREFLIGHT_SENTINEL"
        (self.workspace / "candidate.txt").write_text("baseline\n", encoding="utf-8")
        spec = self.jest_check()
        jest = self.candidate_mutating_jest(
            action,
            sentinel=sentinel,
            delay_seconds=delay_seconds,
        )
        baseline = self.commit_candidate()
        registry = self.registry(
            {"node": sys.executable, "jest": str(jest)},
            probe_timeout_seconds=1 if delay_seconds else 30,
        )
        result = run_static_toolchain_preflight(
            [spec],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
            candidate_baseline_sha=baseline,
        )
        return result, registry, sentinel

    def test_candidate_tracked_mutation_invalidates_evidence_and_keeps_raw_log_private(self) -> None:
        result, registry, sentinel = self._run_candidate_mutation("tracked")
        self.assertFalse(result.passed)
        self.assertFalse(result.candidate_unchanged)
        self.assertIn(STATIC_PREFLIGHT_MUTATED_CANDIDATE, result.reason_codes)
        self.assertNotIn(Capability.JEST.value, registry.verified_capabilities)
        public = json.dumps(result.public_dict(), sort_keys=True)
        self.assertNotIn(sentinel, public)
        self.assertNotIn(str(self.control_plane.private_root), public)
        config_probe = next(
            item for item in registry.private_probe_commands
            if item["id"].startswith("jest.config.")
        )
        private_log = self.control_plane.path_for(
            config_probe["log_artifact"], ArtifactVisibility.PRIVATE
        )
        self.assertIn(sentinel, private_log.read_text(encoding="utf-8"))
        self.assertFalse(list(self.workspace.rglob("preflight/*.log")))

    def test_candidate_untracked_creation_is_detected(self) -> None:
        result, registry, _sentinel = self._run_candidate_mutation("untracked")
        self.assertIn(STATIC_PREFLIGHT_MUTATED_CANDIDATE, result.reason_codes)
        self.assertFalse(result.candidate_unchanged)
        self.assertFalse(registry.verified_capabilities)

    def test_candidate_tracked_deletion_is_detected(self) -> None:
        result, registry, _sentinel = self._run_candidate_mutation("deleted")
        self.assertIn(STATIC_PREFLIGHT_MUTATED_CANDIDATE, result.reason_codes)
        self.assertFalse(result.candidate_unchanged)
        self.assertFalse(registry.verified_capabilities)

    def test_candidate_guard_ignores_harness_scratch(self) -> None:
        result, registry, _sentinel = self._run_candidate_mutation("scratch")
        self.assertTrue(result.passed, result.public_dict())
        self.assertTrue(result.candidate_unchanged)
        self.assertIn(Capability.JEST.value, registry.verified_capabilities)

    def test_candidate_comparison_runs_after_probe_timeout(self) -> None:
        result, registry, _sentinel = self._run_candidate_mutation(
            "tracked", delay_seconds=3
        )
        self.assertIn(STATIC_JEST_CONFIG_PROBE_FAILED, result.reason_codes)
        self.assertIn(STATIC_PREFLIGHT_MUTATED_CANDIDATE, result.reason_codes)
        self.assertFalse(registry.verified_capabilities)
        private_logs = [
            self.control_plane.path_for(item["log_artifact"], ArtifactVisibility.PRIVATE)
            for item in registry.private_probe_commands
        ]
        self.assertTrue(private_logs)
        for path in private_logs:
            with path.open("ab"):
                pass

    def test_candidate_comparison_runs_after_probe_launcher_oserror(self) -> None:
        (self.workspace / "candidate.txt").write_text("baseline\n", encoding="utf-8")
        baseline = self.commit_candidate()
        registry = self.registry({"node": sys.executable})

        def fail_launch(*_args, **_kwargs):
            (self.workspace / "candidate.txt").write_text("mutated", encoding="utf-8")
            raise OSError("synthetic launcher failure")

        with mock.patch.object(preflight_module, "_POPEN", side_effect=fail_launch):
            result = run_static_toolchain_preflight(
                [self.check(["{node}", "--version"])],
                workspace=self.workspace,
                harness_root=self.harness_root,
                toolchain=registry.toolchain,
                probe_registry=registry,
                candidate_baseline_sha=baseline,
            )
        self.assertIn(STATIC_TOOLCHAIN_PROBE_FAILED, result.reason_codes)
        self.assertIn(STATIC_PREFLIGHT_MUTATED_CANDIDATE, result.reason_codes)
        self.assertFalse(registry.verified_capabilities)

    def test_git_exclude_bypass_invalidates_probe_and_physical_candidate(self) -> None:
        sentinel = "GIT_CONTROL_SECRET_SENTINEL"
        (self.workspace / "candidate.txt").write_text("baseline\n", encoding="utf-8")
        spec = self.jest_check()
        jest = self.candidate_mutating_jest("git-exclude", sentinel=sentinel)
        baseline = self.commit_candidate()
        git_manager = GitControlIntegrityManager(
            workspace=self.workspace,
            control_plane=self.control_plane,
        )
        git_manager.establish_baseline()
        registry = self.registry(
            {"node": sys.executable, "jest": str(jest)},
            git_manager=git_manager,
        )

        result = run_static_toolchain_preflight(
            [spec],
            workspace=self.workspace,
            harness_root=self.harness_root,
            toolchain=registry.toolchain,
            probe_registry=registry,
            candidate_baseline_sha=baseline,
        )

        self.assertFalse(result.passed)
        self.assertIn(STATIC_GIT_CONTROL_INTEGRITY_FAILED, result.reason_codes)
        self.assertIn(STATIC_PREFLIGHT_MUTATED_CANDIDATE, result.reason_codes)
        self.assertIn("stealth.js", build_candidate_identity(
            self.workspace, baseline_sha=baseline
        ).changed_paths)
        self.assertNotIn(Capability.JEST.value, registry.verified_capabilities)
        public = json.dumps(result.public_dict(), sort_keys=True)
        self.assertNotIn(sentinel, public)
        private_logs = [
            self.control_plane.path_for(item["log_artifact"], ArtifactVisibility.PRIVATE)
            for item in registry.private_probe_commands
        ]
        self.assertTrue(any(sentinel in path.read_text(encoding="utf-8") for path in private_logs))

    def test_private_probe_log_names_do_not_collide_across_batches(self) -> None:
        registry = self.registry({"node": sys.executable})
        first = registry.ensure_capabilities([Capability.NODE.value], batch_id="first")
        self.assertTrue(first.passed)
        registry.invalidate(Capability.NODE.value)
        second = registry.ensure_capabilities([Capability.NODE.value], batch_id="second")
        self.assertTrue(second.passed)
        names = [item["log_artifact"] for item in registry.private_probe_commands]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all("preflight_logs/" in name for name in names))

    def test_private_probe_output_is_hard_capped(self) -> None:
        registry = ToolProbeRegistry(
            workspace=self.workspace,
            harness_root=self.harness_root,
            source_repo=None,
            toolchain={},
            execution_broker=self.broker,
            control_plane=self.control_plane,
            private_log_limit=1024,
        )
        record = registry._run_probe(
            batch_id="output-limit",
            probe_id="synthetic.output-limit",
            capability=None,
            command=[
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('PUBLIC_SENTINEL' * 1000); sys.stderr.write('ERR' * 1000)",
            ],
            failure_reason=STATIC_TOOLCHAIN_PROBE_FAILED,
        )
        self.assertEqual(record.reason_code, STATIC_TOOLCHAIN_PROBE_OUTPUT_LIMIT)
        self.assertTrue(record.output_truncated)
        private = registry.private_probe_commands[-1]["log_artifact"]
        log = self.control_plane.path_for(private, ArtifactVisibility.PRIVATE)
        self.assertLessEqual(log.stat().st_size, 1024)
        self.assertNotIn("PUBLIC_SENTINEL", json.dumps(record.public_dict()))

    def test_output_limit_terminates_probe_child_process_tree(self) -> None:
        marker = self.workspace / ".harness_tmp" / "probe-child.pid"
        marker.parent.mkdir(parents=True)
        registry = ToolProbeRegistry(
            workspace=self.workspace,
            harness_root=self.harness_root,
            source_repo=None,
            toolchain={},
            execution_broker=self.broker,
            control_plane=self.control_plane,
            private_log_limit=4096,
        )
        script = (
            "import pathlib,subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
            "chunk='X'*65536; "
            "[(sys.stdout.write(chunk),sys.stdout.flush()) for _ in range(1000)]; "
            "time.sleep(60)"
        )
        record = registry._run_probe(
            batch_id="process-tree",
            probe_id="synthetic.process-tree",
            capability=None,
            command=[sys.executable, "-c", script, str(marker)],
            failure_reason=STATIC_TOOLCHAIN_PROBE_FAILED,
        )
        self.assertEqual(record.reason_code, STATIC_TOOLCHAIN_PROBE_OUTPUT_LIMIT)
        child_pid = int(marker.read_text(encoding="utf-8"))

        def process_exists(pid: int) -> bool:
            if os.name == "nt":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                return str(pid) in result.stdout and "No tasks" not in result.stdout
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return True

        deadline = time.monotonic() + 5
        while process_exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(process_exists(child_pid))

    def test_failed_probe_output_is_private_and_public_summary_is_fixed(self) -> None:
        sentinel = "FAILED_PROBE_SECRET_SENTINEL"
        registry = self.registry({"node": sys.executable})

        real_popen = subprocess.Popen

        def failed_probe(_command, **kwargs):
            return real_popen(
                [sys.executable, "-c", f"print({sentinel!r}); raise SystemExit(7)"],
                **kwargs,
            )

        with mock.patch.object(preflight_module, "_POPEN", side_effect=failed_probe):
            result = run_static_toolchain_preflight(
                [self.check(["{node}", "--version"])],
                workspace=self.workspace,
                harness_root=self.harness_root,
                toolchain=registry.toolchain,
                probe_registry=registry,
            )
        public = json.dumps(result.public_dict(), sort_keys=True)
        self.assertNotIn(sentinel, public)
        failed = next(item for item in result.probes if item.status == "FAIL")
        self.assertEqual(
            failed.safe_summary,
            "Probe failed; see Controller-private diagnostics",
        )
        probe = next(item for item in registry.private_probe_commands if item["id"] == "node.version")
        private_log = self.control_plane.path_for(
            probe["log_artifact"], ArtifactVisibility.PRIVATE
        )
        self.assertIn(sentinel, private_log.read_text(encoding="utf-8"))
        self.assertFalse(list(self.workspace.rglob("*.log")))

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

    def test_candidate_and_runtime_mutation_are_both_reported(self) -> None:
        manager, source, _source_jest, workspace_jest = self.projection_manager(
            behavior="mutate_both"
        )
        (self.workspace / "candidate.txt").write_text("baseline\n", encoding="utf-8")
        spec = self.jest_check()
        baseline = self.commit_candidate()
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
            candidate_baseline_sha=baseline,
        )
        self.assertIn(STATIC_RUNTIME_INTEGRITY_FAILED, result.reason_codes)
        self.assertIn(STATIC_PREFLIGHT_MUTATED_CANDIDATE, result.reason_codes)
        self.assertFalse(registry.verified_capabilities)
        self.assertEqual(
            (workspace_jest.parent / "dep.js").read_text(encoding="utf-8"),
            "pristine",
        )

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
        eol = next(spec for spec in manifest["checks"] if spec["name"] == "EOL contract")
        self.assertEqual(eol["command"][0], "{harness_python}")


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
        build_identity = json.loads(
            (run_root / "harness_build_identity.json").read_text(encoding="utf-8")
        )
        self.assertEqual(build_identity["schema_version"], "harness-build-identity.v1")
        self.assertIn("HARNESS_GIT_COMMIT:", output.getvalue())
        self.assertFalse((run_root / "task_contract_01.json").exists())
        self.assertFalse((run_root / "plan_01.json").exists())

    def test_jest_config_candidate_mutation_stops_before_baseline_and_agents(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-static-candidate-mutation-"))
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        sentinel = "CONFIG_EXECUTION_PRIVATE_SENTINEL"
        (repo / "jest.config.cjs").write_text(
            "from pathlib import Path\n"
            f"print({sentinel!r})\n"
            "Path('tracked.txt').write_text('mutated', encoding='utf-8')\n",
            encoding="utf-8",
        )
        (repo / "selection.test.cjs").write_text("fixture\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "baseline")
        jest = root / "external-jest.py"
        jest.write_text(
            f"""from pathlib import Path
import sys
if '--version' in sys.argv:
    print('29.7.0')
    raise SystemExit(0)
if '--showConfig' in sys.argv:
    args = sys.argv[1:]
    config = Path(args[args.index('--config') + 1])
    exec(compile(config.read_text(encoding='utf-8'), str(config), 'exec'), {{}})
    raise SystemExit(0)
raise SystemExit(9)
""",
            encoding="utf-8",
        )
        manifest = root / "task.toml"
        manifest.write_text(
            '''version = 2
task_id = "STATIC_CANDIDATE_MUTATION"
project = "demo"
workspace_mode = "git_worktree"
base_ref = "HEAD"
result_mode = "keep_worktree"
risk = "medium"
max_fix_cycles = 0
max_replan_cycles = 0
turn_timeout_seconds = 60
require_clean_git = true
prompt = "Candidate mutation must stop early."

[benchmark]
confirm_current_baseline_broken = true
baseline_failure_marker = "ORACLE_REACHED"

[[checks]]
name = "Jest"
feedback = "repair"
command = ["{node}", "{jest}", "--config", "{workspace}/jest.config.cjs", "--runTestsByPath", "{workspace}/selection.test.cjs"]
timeout_seconds = 30

[[checks]]
name = "Heldout"
feedback = "heldout"
command = ["{harness_python}", "-c", "print('ORACLE_REACHED')"]
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
                    "toolchain": {"node": sys.executable, "jest": str(jest)},
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
        self.assertIn(STATIC_PREFLIGHT_MUTATED_CANDIDATE, output.getvalue())
        baseline.assert_not_called()
        normalizer.assert_not_called()
        app_server.assert_not_called()
        artifact = json.loads(
            (run_root / "static_toolchain_preflight.json").read_text(encoding="utf-8")
        )
        self.assertFalse(artifact["candidate_unchanged"])
        self.assertNotIn(sentinel, json.dumps(artifact, sort_keys=True))
        private_logs = list((run_root / "controller_private" / "preflight_logs").rglob("*.log"))
        self.assertTrue(private_logs)
        self.assertTrue(
            any(sentinel in path.read_text(encoding="utf-8") for path in private_logs)
        )
        self.assertFalse((run_root / "task_contract_01.json").exists())
        self.assertFalse((run_root / "plan_01.json").exists())


if __name__ == "__main__":
    unittest.main()
