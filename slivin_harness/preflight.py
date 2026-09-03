from __future__ import annotations

import os
import re
import shutil
import string
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence

from slivin_harness.control_plane import canonical_path, is_within
from slivin_harness.execution import ExecutionBroker, ExecutionRole
from slivin_harness.runtime_projection import (
    RuntimeProjectionIntegrityError,
    RuntimeProjectionIntegrityManager,
)
from slivin_harness.verification import Capability
from slivin_harness.workspace import assert_safe_runtime_path


STATIC_TOOLCHAIN_PREFLIGHT_VERSION = "static-toolchain-preflight.v1"
STATIC_TOOLCHAIN_PREFLIGHT_PRIVATE_VERSION = "static-toolchain-preflight-private.v1"

STATIC_TOOLCHAIN_UNKNOWN_PLACEHOLDER = "STATIC_TOOLCHAIN_UNKNOWN_PLACEHOLDER"
STATIC_TOOLCHAIN_MISSING_ENTRY = "STATIC_TOOLCHAIN_MISSING_ENTRY"
STATIC_TOOLCHAIN_PATH_NOT_FOUND = "STATIC_TOOLCHAIN_PATH_NOT_FOUND"
STATIC_EXECUTABLE_NOT_FOUND = "STATIC_EXECUTABLE_NOT_FOUND"
STATIC_TOOLCHAIN_PROBE_FAILED = "STATIC_TOOLCHAIN_PROBE_FAILED"
STATIC_JEST_CONFIG_NOT_FOUND = "STATIC_JEST_CONFIG_NOT_FOUND"
STATIC_JEST_CONFIG_PROBE_FAILED = "STATIC_JEST_CONFIG_PROBE_FAILED"
STATIC_CHECK_INPUT_NOT_FOUND = "STATIC_CHECK_INPUT_NOT_FOUND"
STATIC_COMMAND_TEMPLATE_INVALID = "STATIC_COMMAND_TEMPLATE_INVALID"
STATIC_RUNTIME_INTEGRITY_FAILED = "STATIC_RUNTIME_INTEGRITY_FAILED"

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_FORMATTER = string.Formatter()
_BUILTIN_PLACEHOLDERS = frozenset(
    {"workspace", "harness_root", "project_root", "python"}
)
_KNOWN_TOOLCHAIN_KEYS = frozenset({"node", "jest", "project_python"})
TOOL_BACKED_CAPABILITIES = frozenset(
    {
        Capability.GIT.value,
        Capability.PROJECT_PYTHON.value,
        Capability.NODE.value,
        Capability.JEST.value,
    }
)


class CommandTemplateError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class StaticPreflightError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def extract_command_placeholders(command: Sequence[str]) -> tuple[str, ...]:
    """Parse strict simple placeholders while preserving literal braces."""

    placeholders: set[str] = set()
    for raw in command:
        try:
            parsed = tuple(_FORMATTER.parse(str(raw)))
        except ValueError as exc:
            raise CommandTemplateError(
                STATIC_COMMAND_TEMPLATE_INVALID,
                "Malformed braces in command template",
            ) from exc
        for _literal, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if (
                not field_name
                or not _IDENTIFIER.fullmatch(field_name)
                or conversion is not None
                or bool(format_spec)
            ):
                raise CommandTemplateError(
                    STATIC_COMMAND_TEMPLATE_INVALID,
                    "Command placeholders must be simple identifiers without conversion or format spec",
                )
            placeholders.add(field_name)
    return tuple(sorted(placeholders))


def expand_command_template(
    command: Sequence[str],
    *,
    values: Mapping[str, str],
    missing_reason_code: str = STATIC_TOOLCHAIN_UNKNOWN_PLACEHOLDER,
) -> list[str]:
    placeholders = extract_command_placeholders(command)
    missing = sorted(set(placeholders) - set(values))
    if missing:
        raise CommandTemplateError(
            missing_reason_code,
            "Command placeholder is not available: " + ", ".join(missing),
        )
    return [str(raw).format_map(dict(values)) for raw in command]


def expand_check_command(
    command: Sequence[str],
    *,
    workspace: Path,
    harness_root: Path,
    toolchain: Mapping[str, str],
    project_root: Path | None = None,
) -> list[str]:
    """Canonical expansion used by both preflight and actual manifest checks."""

    values = {str(key): str(value) for key, value in toolchain.items()}
    values.update(
        {
            "workspace": str(workspace.resolve()),
            "harness_root": str(harness_root.resolve()),
            # Manifest checks always operate on the managed candidate.  This
            # also prevents a historical command from receiving the source
            # checkout through a similarly named placeholder.
            "project_root": str((project_root or workspace).resolve()),
            "python": str(Path(sys.executable).resolve()),
        }
    )
    return expand_command_template(command, values=values)


def _looks_absolute(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _looks_path_like(value: str) -> bool:
    return _looks_absolute(value) or "/" in value or "\\" in value or value.startswith(".")


def _safe_relative_path(value: str) -> Path:
    normalized = value.replace("\\", "/")
    windows = PureWindowsPath(value)
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or windows.is_absolute()
        or windows.drive
        or windows.root
        or any(part == ".." for part in pure.parts)
    ):
        raise StaticPreflightError(
            STATIC_TOOLCHAIN_PATH_NOT_FOUND,
            "Relative command path escapes the managed workspace",
        )
    return Path(*pure.parts)


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


@dataclass(frozen=True)
class ToolProbeRecord:
    probe_id: str
    capability: str | None
    status: str
    reason_code: str | None = None
    safe_summary: str = ""

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.probe_id,
            "capability": self.capability,
            "status": self.status,
        }
        if self.reason_code:
            value["reason_code"] = self.reason_code
        if self.safe_summary:
            value["summary"] = self.safe_summary
        return value


@dataclass
class ToolProbeBatchResult:
    requested_capabilities: tuple[str, ...]
    verified_capabilities: tuple[str, ...]
    reused_capabilities: tuple[str, ...]
    probes: tuple[ToolProbeRecord, ...]
    reason_codes: tuple[str, ...]
    integrity_reason_code: str | None = None

    @property
    def passed(self) -> bool:
        return not self.reason_codes

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "requested_capabilities": list(self.requested_capabilities),
            "verified_capabilities": list(self.verified_capabilities),
            "reused_capabilities": list(self.reused_capabilities),
            "probes": [item.public_dict() for item in self.probes],
            "reason_codes": list(self.reason_codes),
        }
        if self.integrity_reason_code:
            value["integrity_reason_code"] = self.integrity_reason_code
        return value


@dataclass
class StaticToolchainPreflightResult:
    status: str
    required_placeholders: tuple[str, ...]
    required_toolchain_entries: tuple[str, ...]
    verified_capabilities: tuple[str, ...]
    checks: tuple[dict[str, Any], ...]
    probes: tuple[ToolProbeRecord, ...]
    resolved_executables: tuple[dict[str, Any], ...]
    optional_toolchain_entries: tuple[dict[str, str], ...]
    reason_codes: tuple[str, ...]
    private_details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STATIC_TOOLCHAIN_PREFLIGHT_VERSION,
            "status": self.status,
            "required_placeholders": list(self.required_placeholders),
            "required_toolchain_entries": list(self.required_toolchain_entries),
            "verified_capabilities": list(self.verified_capabilities),
            "checks": list(self.checks),
            "probes": [item.public_dict() for item in self.probes],
            "resolved_executables": list(self.resolved_executables),
            "optional_toolchain_entries": list(self.optional_toolchain_entries),
            "reason_codes": list(self.reason_codes),
            "source_paths_exposed": False,
            "hidden_commands_executed": False,
            "tests_executed": False,
            "fresh_dependency_install_performed": False,
        }

    def private_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STATIC_TOOLCHAIN_PREFLIGHT_PRIVATE_VERSION,
            "public_result": self.public_dict(),
            **self.private_details,
        }


class ToolProbeRegistry:
    """Controller-owned cache of executable probe evidence for one run."""

    def __init__(
        self,
        *,
        workspace: Path,
        harness_root: Path,
        source_repo: Path | None,
        toolchain: Mapping[str, str],
        execution_broker: ExecutionBroker,
        runtime_integrity_manager: RuntimeProjectionIntegrityManager | None = None,
        historical: bool = False,
        rebound_to_workspace: Mapping[str, str] | None = None,
        probe_timeout_seconds: int = 30,
        output_limit: int = 2_000,
    ) -> None:
        workspace_input = Path(workspace).expanduser()
        harness_input = Path(harness_root).expanduser()
        source_input = Path(source_repo).expanduser() if source_repo is not None else None
        self.workspace = canonical_path(workspace_input)
        self.harness_root = canonical_path(harness_input)
        self.source_repo = canonical_path(source_input) if source_input is not None else None
        redaction_roots: list[tuple[Path, str]] = [
            (workspace_input.absolute(), "<workspace>"),
            (self.workspace, "<workspace>"),
            (harness_input.absolute(), "<harness_root>"),
            (self.harness_root, "<harness_root>"),
        ]
        if source_input is not None and self.source_repo is not None:
            redaction_roots.extend(
                [
                    (source_input.absolute(), "<source_repo>"),
                    (self.source_repo, "<source_repo>"),
                ]
            )
        self._redaction_roots = tuple(redaction_roots)
        self.toolchain = {str(key): str(value) for key, value in toolchain.items()}
        self.execution_broker = execution_broker
        self.runtime_integrity_manager = runtime_integrity_manager
        self.historical = historical
        self.rebound_to_workspace = {
            str(key): str(value).replace("\\", "/")
            for key, value in (rebound_to_workspace or {}).items()
        }
        self.probe_timeout_seconds = max(1, int(probe_timeout_seconds))
        self.output_limit = max(128, int(output_limit))
        self._verified_capabilities: set[str] = set()
        self._python_verified = False
        self._verified_jest_configs: set[str] = set()
        self._resolved_public: dict[str, dict[str, Any]] = {}
        self._private_probe_commands: list[dict[str, Any]] = []

    @property
    def verified_capabilities(self) -> frozenset[str]:
        return frozenset(self._verified_capabilities)

    @property
    def resolved_executables(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._resolved_public[key] for key in sorted(self._resolved_public))

    @property
    def private_probe_commands(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._private_probe_commands)

    def invalidate(self, *capabilities: str) -> None:
        for capability in capabilities:
            self._verified_capabilities.discard(str(capability))

    def _safe_summary(self, raw: str) -> str:
        value = str(raw).replace("\r", " ").replace("\n", " ").strip()
        for root, marker in self._redaction_roots:
            text = str(root)
            spellings = {
                text,
                text.replace("\\", "/"),
                text.replace("\\", "\\\\"),
            }
            for spelling in sorted(spellings, key=len, reverse=True):
                value = re.sub(
                    re.escape(spelling),
                    marker,
                    value,
                    flags=re.IGNORECASE,
                )
        return value[: self.output_limit]

    def _public_location(self, path: Path) -> dict[str, Any]:
        canonical = canonical_path(path)
        if is_within(self.workspace, canonical):
            return {
                "location_kind": "WORKSPACE",
                "relative_path": canonical.relative_to(self.workspace).as_posix(),
            }
        if is_within(self.harness_root, canonical):
            return {
                "location_kind": "HARNESS",
                "relative_path": canonical.relative_to(self.harness_root).as_posix(),
            }
        return {"location_kind": "EXTERNAL"}

    def _resolve_path(self, value: str) -> Path:
        if _looks_absolute(value):
            return canonical_path(Path(value))
        relative = _safe_relative_path(value)
        return canonical_path(self.workspace / relative)

    def _reject_historical_source_path(self, path: Path) -> None:
        if self.historical and self.source_repo is not None and is_within(self.source_repo, path):
            raise StaticPreflightError(
                STATIC_TOOLCHAIN_PATH_NOT_FOUND,
                "Historical command retained a source-local path",
            )

    def resolve_tool_entry(self, key: str, *, require_file: bool = True) -> Path:
        raw = self.toolchain.get(key)
        if not raw:
            raise StaticPreflightError(
                STATIC_TOOLCHAIN_MISSING_ENTRY,
                f"Required toolchain entry is missing: {key}",
            )
        if _looks_path_like(raw):
            path = self._resolve_path(raw)
        else:
            found = shutil.which(raw, path=self._environment().get("PATH"))
            if not found:
                raise StaticPreflightError(
                    STATIC_EXECUTABLE_NOT_FOUND,
                    f"Executable for toolchain entry was not found: {key}",
                )
            path = canonical_path(Path(found))
        self._reject_historical_source_path(path)
        if require_file and not path.is_file():
            raise StaticPreflightError(
                STATIC_TOOLCHAIN_PATH_NOT_FOUND,
                f"Required toolchain file does not exist: {key}",
            )
        if not require_file and not path.exists():
            raise StaticPreflightError(
                STATIC_TOOLCHAIN_PATH_NOT_FOUND,
                f"Required toolchain path does not exist: {key}",
            )
        if key in self.rebound_to_workspace:
            manager = self.runtime_integrity_manager
            authority = (
                manager.authorized_projection_for_path(path, require_file=require_file)
                if manager is not None
                else None
            )
            if authority is None or not is_within(self.workspace, path):
                raise StaticPreflightError(
                    STATIC_RUNTIME_INTEGRITY_FAILED,
                    f"Rebound toolchain entry lacks runtime projection authority: {key}",
                )
        self._resolved_public[key] = {
            "name": key,
            "status": "READY",
            **self._public_location(path),
        }
        return path

    def resolve_executable(self, value: str, *, record_name: str) -> Path:
        if _looks_path_like(value):
            path = self._resolve_path(value)
        else:
            found = shutil.which(value, path=self._environment().get("PATH"))
            if not found:
                raise StaticPreflightError(
                    STATIC_EXECUTABLE_NOT_FOUND,
                    f"Required executable was not found: {record_name}",
                )
            path = canonical_path(Path(found))
        self._reject_historical_source_path(path)
        if not path.is_file():
            raise StaticPreflightError(
                STATIC_EXECUTABLE_NOT_FOUND,
                f"Required executable is not a regular file: {record_name}",
            )
        self._resolved_public.setdefault(
            record_name,
            {"name": record_name, "status": "READY", **self._public_location(path)},
        )
        return path

    def _environment(self) -> dict[str, str]:
        return self.execution_broker.environment_for(
            ExecutionRole.CONTROLLER_CHECK,
            extra={
                "SLIVIN_HARNESS_PREFLIGHT": "1",
            },
        )

    def _run_probe(
        self,
        *,
        probe_id: str,
        capability: str | None,
        command: Sequence[str],
        failure_reason: str,
    ) -> ToolProbeRecord:
        scratch = self.execution_broker.scratch_root(ExecutionRole.CONTROLLER_CHECK) / "preflight"
        scratch.mkdir(parents=True, exist_ok=True)
        log_path = scratch / (re.sub(r"[^A-Za-z0-9_.-]", "_", probe_id) + ".log")
        returncode: int | None = None
        timed_out = False
        error_text = ""
        try:
            with log_path.open("wb") as output:
                completed = subprocess.run(
                    [str(part) for part in command],
                    cwd=self.workspace,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    timeout=self.probe_timeout_seconds,
                    env=self._environment(),
                )
                returncode = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        except (FileNotFoundError, PermissionError, OSError) as exc:
            error_text = f"{type(exc).__name__}: {exc}"
        try:
            output_text = log_path.read_bytes()[: self.output_limit].decode(
                "utf-8", errors="replace"
            )
        except OSError:
            output_text = ""
        passed = returncode == 0 and not timed_out and not error_text
        raw_summary = error_text or output_text
        if passed and probe_id.endswith(".version"):
            lines = raw_summary.splitlines()
            raw_summary = lines[0] if lines else ""
        elif passed:
            raw_summary = ""
        summary = self._safe_summary(raw_summary)
        self._private_probe_commands.append(
            {
                "id": probe_id,
                "capability": capability,
                "argv": [str(part) for part in command],
                "returncode": returncode,
                "timed_out": timed_out,
            }
        )
        return ToolProbeRecord(
            probe_id=probe_id,
            capability=capability,
            status="PASS" if passed else "FAIL",
            reason_code=None if passed else failure_reason,
            safe_summary=summary,
        )

    def ensure_capabilities(
        self,
        capabilities: Iterable[str],
        *,
        batch_id: str,
        include_python: bool = False,
        jest_configs: Iterable[Path] = (),
    ) -> ToolProbeBatchResult:
        requested = set(str(item) for item in capabilities) & set(TOOL_BACKED_CAPABILITIES)
        if Capability.JEST.value in requested:
            requested.add(Capability.NODE.value)
        configs = tuple(sorted({str(canonical_path(path)) for path in jest_configs}))
        reused = requested & self._verified_capabilities
        needs_work = bool(requested - self._verified_capabilities)
        needs_work = needs_work or (include_python and not self._python_verified)
        needs_work = needs_work or bool(set(configs) - self._verified_jest_configs)
        if not needs_work:
            return ToolProbeBatchResult(
                requested_capabilities=tuple(sorted(requested)),
                verified_capabilities=tuple(sorted(requested & self._verified_capabilities)),
                reused_capabilities=tuple(sorted(reused)),
                probes=(),
                reason_codes=(),
            )

        new_verified = set(self._verified_capabilities)
        new_python_verified = self._python_verified
        new_jest_configs = set(self._verified_jest_configs)
        records: list[ToolProbeRecord] = []
        reasons: list[str] = []

        def record(item: ToolProbeRecord) -> bool:
            records.append(item)
            if item.reason_code:
                reasons.append(item.reason_code)
            return item.status == "PASS"

        def operation() -> None:
            nonlocal new_python_verified
            if include_python and not new_python_verified:
                python_path = canonical_path(Path(sys.executable))
                self._resolved_public["python"] = {
                    "name": "python",
                    "status": "READY",
                    "location_kind": "HARNESS_BUILTIN",
                }
                if record(
                    self._run_probe(
                        probe_id="python.version",
                        capability=None,
                        command=[str(python_path), "--version"],
                        failure_reason=STATIC_TOOLCHAIN_PROBE_FAILED,
                    )
                ):
                    new_python_verified = True

            if Capability.GIT.value in requested and Capability.GIT.value not in new_verified:
                try:
                    git_path = self.resolve_executable("git", record_name="git")
                except StaticPreflightError as exc:
                    reasons.append(exc.reason_code)
                else:
                    if record(
                        self._run_probe(
                            probe_id="git.version",
                            capability=Capability.GIT.value,
                            command=[str(git_path), "--version"],
                            failure_reason=STATIC_TOOLCHAIN_PROBE_FAILED,
                        )
                    ):
                        new_verified.add(Capability.GIT.value)

            node_path: Path | None = None
            if Capability.NODE.value in requested:
                try:
                    node_path = self.resolve_tool_entry("node")
                except StaticPreflightError as exc:
                    reasons.append(exc.reason_code)
                else:
                    if Capability.NODE.value in new_verified or record(
                        self._run_probe(
                            probe_id="node.version",
                            capability=Capability.NODE.value,
                            command=[str(node_path), "--version"],
                            failure_reason=STATIC_TOOLCHAIN_PROBE_FAILED,
                        )
                    ):
                        new_verified.add(Capability.NODE.value)

            if (
                Capability.PROJECT_PYTHON.value in requested
                and Capability.PROJECT_PYTHON.value not in new_verified
            ):
                try:
                    project_python = self.resolve_tool_entry("project_python")
                except StaticPreflightError as exc:
                    reasons.append(exc.reason_code)
                else:
                    if record(
                        self._run_probe(
                            probe_id="project-python.version",
                            capability=Capability.PROJECT_PYTHON.value,
                            command=[str(project_python), "--version"],
                            failure_reason=STATIC_TOOLCHAIN_PROBE_FAILED,
                        )
                    ):
                        new_verified.add(Capability.PROJECT_PYTHON.value)

            if Capability.JEST.value in requested:
                jest_version_ok = Capability.JEST.value in new_verified
                jest_path: Path | None = None
                try:
                    jest_path = self.resolve_tool_entry("jest")
                except StaticPreflightError as exc:
                    reasons.append(exc.reason_code)
                if node_path is None and Capability.NODE.value in new_verified:
                    try:
                        node_path = self.resolve_tool_entry("node")
                    except StaticPreflightError as exc:
                        reasons.append(exc.reason_code)
                if (
                    not jest_version_ok
                    and node_path is not None
                    and jest_path is not None
                    and Capability.NODE.value in new_verified
                ):
                    jest_version_ok = record(
                        self._run_probe(
                            probe_id="jest.version",
                            capability=Capability.JEST.value,
                            command=[str(node_path), str(jest_path), "--version"],
                            failure_reason=STATIC_TOOLCHAIN_PROBE_FAILED,
                        )
                    )
                config_ok = True
                if jest_version_ok and node_path is not None and jest_path is not None:
                    for config_text in configs:
                        if config_text in new_jest_configs:
                            continue
                        config_path = Path(config_text)
                        item = self._run_probe(
                            probe_id="jest.config." + str(len(new_jest_configs) + 1),
                            capability=Capability.JEST.value,
                            command=[
                                str(node_path),
                                str(jest_path),
                                "--showConfig",
                                "--config",
                                str(config_path),
                            ],
                            failure_reason=STATIC_JEST_CONFIG_PROBE_FAILED,
                        )
                        if record(item):
                            new_jest_configs.add(config_text)
                        else:
                            config_ok = False
                elif configs:
                    config_ok = False
                if jest_version_ok and config_ok and set(configs) <= new_jest_configs:
                    new_verified.add(Capability.JEST.value)
                else:
                    new_verified.discard(Capability.JEST.value)

        try:
            manager = self.runtime_integrity_manager
            if manager is not None:
                manager.run_batch(batch_id, operation)
            else:
                operation()
        except RuntimeProjectionIntegrityError as exc:
            return ToolProbeBatchResult(
                requested_capabilities=tuple(sorted(requested)),
                verified_capabilities=tuple(sorted(requested & self._verified_capabilities)),
                reused_capabilities=tuple(sorted(reused)),
                probes=(),
                reason_codes=(STATIC_RUNTIME_INTEGRITY_FAILED,),
                integrity_reason_code=exc.reason_code,
            )

        if not reasons:
            self._verified_capabilities = new_verified
            self._python_verified = new_python_verified
            self._verified_jest_configs = new_jest_configs
        return ToolProbeBatchResult(
            requested_capabilities=tuple(sorted(requested)),
            verified_capabilities=tuple(sorted(requested & self._verified_capabilities)),
            reused_capabilities=tuple(sorted(reused)),
            probes=tuple(records),
            reason_codes=tuple(_dedupe(reasons)),
        )


def _resolve_required_input(
    raw: str,
    *,
    workspace: Path,
    harness_root: Path,
    reason_code: str,
) -> Path:
    if _looks_absolute(raw):
        path = canonical_path(Path(raw))
    else:
        try:
            path = canonical_path(workspace / _safe_relative_path(raw))
        except StaticPreflightError as exc:
            raise StaticPreflightError(reason_code, str(exc)) from exc
    if not path.is_file():
        raise StaticPreflightError(reason_code, "Required static input is not a regular file")
    if is_within(workspace, path):
        assert_safe_runtime_path(workspace, path, include_leaf=True)
    elif is_within(harness_root, path):
        assert_safe_runtime_path(harness_root, path, include_leaf=True)
    return path


def _argument_after(command: Sequence[str], option: str) -> str | None:
    for index, value in enumerate(command):
        if value == option:
            return command[index + 1] if index + 1 < len(command) else None
        if value.startswith(option + "="):
            return value.split("=", 1)[1]
    return None


def _validate_known_inputs(
    command: Sequence[str],
    *,
    family: str,
    workspace: Path,
    harness_root: Path,
    jest_path: Path | None,
) -> tuple[Path, ...]:
    configs: list[Path] = []
    if family == "jest":
        config_raw = _argument_after(command, "--config")
        if config_raw is None:
            if "--runTestsByPath" in command:
                raise StaticPreflightError(
                    STATIC_JEST_CONFIG_NOT_FOUND,
                    "Manifest Jest test command must identify its config with --config",
                )
            return ()
        config = _resolve_required_input(
            config_raw,
            workspace=workspace,
            harness_root=harness_root,
            reason_code=STATIC_JEST_CONFIG_NOT_FOUND,
        )
        configs.append(config)
        if "--runTestsByPath" in command:
            start = command.index("--runTestsByPath") + 1
            for raw in command[start:]:
                if raw.startswith("-"):
                    break
                _resolve_required_input(
                    raw,
                    workspace=workspace,
                    harness_root=harness_root,
                    reason_code=STATIC_CHECK_INPUT_NOT_FOUND,
                )
        return tuple(configs)

    if family == "node" and len(command) >= 2:
        if command[1] == "--check":
            if len(command) < 3:
                raise StaticPreflightError(
                    STATIC_CHECK_INPUT_NOT_FOUND,
                    "node --check requires a script",
                )
            _resolve_required_input(
                command[2],
                workspace=workspace,
                harness_root=harness_root,
                reason_code=STATIC_CHECK_INPUT_NOT_FOUND,
            )
        elif not command[1].startswith("-"):
            candidate = command[1]
            if jest_path is None or canonical_path(Path(candidate)) != canonical_path(jest_path):
                if _looks_path_like(candidate) or candidate.endswith((".js", ".cjs", ".mjs")):
                    _resolve_required_input(
                        candidate,
                        workspace=workspace,
                        harness_root=harness_root,
                        reason_code=STATIC_CHECK_INPUT_NOT_FOUND,
                    )
    elif family in {"python", "project_python"} and len(command) >= 2:
        script = command[1]
        if not script.startswith("-") and script.endswith(".py"):
            _resolve_required_input(
                script,
                workspace=workspace,
                harness_root=harness_root,
                reason_code=STATIC_CHECK_INPUT_NOT_FOUND,
            )
    return tuple(configs)


def run_static_toolchain_preflight(
    checks: Sequence[Mapping[str, Any]],
    *,
    workspace: Path,
    harness_root: Path,
    toolchain: Mapping[str, str],
    probe_registry: ToolProbeRegistry,
) -> StaticToolchainPreflightResult:
    """Validate manifest-known commands without executing tests or scripts."""

    required_placeholders: set[str] = set()
    required_entries: set[str] = set()
    requested_capabilities: set[str] = set()
    include_python = False
    check_records: list[dict[str, Any]] = []
    private_checks: list[dict[str, Any]] = []
    reasons: list[str] = []
    jest_configs: set[Path] = set()
    allowed = set(_BUILTIN_PLACEHOLDERS) | set(toolchain) | set(_KNOWN_TOOLCHAIN_KEYS)

    for spec in checks:
        name = str(spec.get("name") or "<unnamed>")
        raw_command = [str(item) for item in spec.get("command", [])]
        check_reasons: list[str] = []
        expanded: list[str] | None = None
        placeholders: tuple[str, ...] = ()
        try:
            placeholders = extract_command_placeholders(raw_command)
            unknown = sorted(set(placeholders) - allowed)
            if unknown:
                raise CommandTemplateError(
                    STATIC_TOOLCHAIN_UNKNOWN_PLACEHOLDER,
                    "Unknown command placeholder: " + ", ".join(unknown),
                )
            missing_entries = sorted(
                placeholder
                for placeholder in placeholders
                if placeholder not in _BUILTIN_PLACEHOLDERS and placeholder not in toolchain
            )
            if missing_entries:
                raise CommandTemplateError(
                    STATIC_TOOLCHAIN_MISSING_ENTRY,
                    "Required toolchain entry is missing: " + ", ".join(missing_entries),
                )
            expanded = expand_check_command(
                raw_command,
                workspace=workspace,
                harness_root=harness_root,
                toolchain=toolchain,
            )
        except CommandTemplateError as exc:
            check_reasons.append(exc.reason_code)

        required_placeholders.update(placeholders)
        required_entries.update(set(placeholders) - set(_BUILTIN_PLACEHOLDERS))
        if "python" in placeholders:
            include_python = True
        if "node" in placeholders:
            requested_capabilities.add(Capability.NODE.value)
        if "jest" in placeholders:
            requested_capabilities.update({Capability.NODE.value, Capability.JEST.value})
        if "project_python" in placeholders:
            requested_capabilities.add(Capability.PROJECT_PYTHON.value)

        family = "unknown"
        if expanded:
            try:
                probe_registry.resolve_executable(expanded[0], record_name=f"check:{name}")
                if raw_command[0] == "git" or Path(expanded[0]).name.lower() in {
                    "git",
                    "git.exe",
                    "git.cmd",
                }:
                    family = "git"
                    requested_capabilities.add(Capability.GIT.value)
                elif "project_python" in extract_command_placeholders(raw_command[:1]):
                    family = "project_python"
                elif "python" in extract_command_placeholders(raw_command[:1]):
                    family = "python"
                elif "node" in extract_command_placeholders(raw_command[:1]):
                    family = "node"
                jest_path: Path | None = None
                if "jest" in placeholders:
                    jest_path = probe_registry.resolve_tool_entry("jest")
                    if len(expanded) > 1 and canonical_path(Path(expanded[1])) == jest_path:
                        family = "jest"
                for key in sorted(required_entries & set(placeholders)):
                    probe_registry.resolve_tool_entry(
                        key,
                        require_file=key in _KNOWN_TOOLCHAIN_KEYS,
                    )
                jest_configs.update(
                    _validate_known_inputs(
                        expanded,
                        family=family,
                        workspace=canonical_path(workspace),
                        harness_root=canonical_path(harness_root),
                        jest_path=jest_path,
                    )
                )
            except (StaticPreflightError, RuntimeError, OSError) as exc:
                reason = getattr(exc, "reason_code", STATIC_TOOLCHAIN_PATH_NOT_FOUND)
                check_reasons.append(str(reason))

        check_reasons = _dedupe(check_reasons)
        reasons.extend(check_reasons)
        check_records.append(
            {
                "name": name,
                "feedback": str(spec.get("feedback") or ""),
                "status": "READY" if not check_reasons else "BLOCKED",
                "command_family": family,
                "reason_codes": check_reasons,
            }
        )
        private_checks.append(
            {
                "name": name,
                "placeholders": list(placeholders),
                "expanded_command": expanded,
                "command_family": family,
                "reason_codes": check_reasons,
            }
        )

    probe_result = ToolProbeBatchResult((), (), (), (), ())
    if not reasons:
        probe_result = probe_registry.ensure_capabilities(
            requested_capabilities,
            batch_id="static-toolchain-preflight",
            include_python=include_python,
            jest_configs=jest_configs,
        )
        reasons.extend(probe_result.reason_codes)

    optional_entries = tuple(
        {"name": key, "status": "UNUSED_NOT_PROBED"}
        for key in sorted(set(toolchain) - required_entries)
    )
    reasons = _dedupe(reasons)
    return StaticToolchainPreflightResult(
        status="PASS" if not reasons else "FAIL",
        required_placeholders=tuple(sorted(required_placeholders)),
        required_toolchain_entries=tuple(sorted(required_entries)),
        verified_capabilities=tuple(sorted(probe_registry.verified_capabilities)),
        checks=tuple(check_records),
        probes=probe_result.probes,
        resolved_executables=probe_registry.resolved_executables,
        optional_toolchain_entries=optional_entries,
        reason_codes=tuple(reasons),
        private_details={
            "checks": private_checks,
            "probe_commands": list(probe_registry.private_probe_commands),
            "integrity_reason_code": probe_result.integrity_reason_code,
        },
    )
