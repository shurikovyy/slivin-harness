from __future__ import annotations

import dataclasses
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from slivin_harness.control_plane import canonical_path, is_within
from slivin_harness.execution import ExecutionBroker, ExecutionRole
from slivin_harness.git_integrity import GitControlIntegrityError, GitControlIntegrityManager
from slivin_harness.preflight import (
    CommandTemplateError,
    expand_command_template,
    resolve_python_command,
)
from slivin_harness.protocol import safe_repo_relative, stable_fingerprint
from slivin_harness.run_state import CandidateIdentity, build_candidate_identity
from slivin_harness.runtime_projection import (
    RuntimeProjectionIntegrityError,
    RuntimeProjectionIntegrityManager,
)
from slivin_harness.verification import Capability, ProofLevel, validate_verification_plan
from slivin_harness.workflow import RuntimeStatus

PHASE6_VERSION = "phase6-runtime-evaluator.v1"
RUNTIME_SCENARIO_VERSION = "runtime-scenario.v1"
RUNTIME_REQUEST_VERSION = "runtime-request.v1"
RUNTIME_RESULT_VERSION = "runtime-result.v1"
RUNTIME_EVIDENCE_VERSION = "runtime-evidence.v1"
CONTRACT_CLOSURE_VERSION = "contract-closure.v1"
BLIND_AUDIT_VERSION = "blind-audit.v1"
MAX_RUNTIME_RESULT_BYTES = 2_000_000
MAX_RUNTIME_LOG_CHARS = 1_000_000

_RUNTIME_PROFILES = {
    ProofLevel.LIVE_LOCAL.value,
    ProofLevel.TEST_EXTERNAL.value,
    ProofLevel.PROD_OBSERVE.value,
}
_RUNTIME_PROFILE_CAPABILITY = {
    ProofLevel.LIVE_LOCAL.value: Capability.LIVE_LOCAL_RUNTIME.value,
    ProofLevel.TEST_EXTERNAL.value: Capability.TEST_EXTERNAL_RUNTIME.value,
    ProofLevel.PROD_OBSERVE.value: Capability.PROD_OBSERVE_RUNTIME.value,
}
_RUNTIME_FAILURES = {
    RuntimeStatus.BEHAVIOR_FAIL.value,
    RuntimeStatus.START_FAIL.value,
    RuntimeStatus.TIMEOUT.value,
    RuntimeStatus.INFRA_ERROR.value,
    RuntimeStatus.INVALID_RESULT.value,
    RuntimeStatus.READBACK_FAIL.value,
    RuntimeStatus.CLEANUP_FAIL.value,
    RuntimeStatus.MUTATED_CANDIDATE.value,
}


class Phase6ContractError(RuntimeError):
    """A Phase 6 runtime/evaluator artifact violates the Controller contract."""


@dataclasses.dataclass(frozen=True)
class RuntimeScenarioConfig:
    scenario_id: str
    profile: str
    capabilities: tuple[str, ...]
    command: tuple[str, ...]
    timeout_seconds: int = 300
    startup_command: tuple[str, ...] = ()
    health_command: tuple[str, ...] = ()
    startup_timeout_seconds: int = 60
    health_interval_seconds: float = 1.0
    cleanup_command: tuple[str, ...] = ()
    cleanup_timeout_seconds: int = 60
    disposable: bool = False
    read_only_enforced: bool = False
    preserve_env: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise Phase6ContractError("Runtime scenario id must be non-empty")
        if self.profile not in _RUNTIME_PROFILES:
            raise Phase6ContractError(f"Unsupported runtime profile: {self.profile}")
        allowed = {item.value for item in Capability}
        unknown = sorted(set(self.capabilities) - allowed)
        if unknown:
            raise Phase6ContractError(
                "Unknown runtime scenario capabilities: " + ", ".join(unknown)
            )
        if len(self.capabilities) != len(set(self.capabilities)):
            raise Phase6ContractError(
                f"Runtime scenario {self.scenario_id} contains duplicate capabilities"
            )
        if not self.command or not all(str(item).strip() for item in self.command):
            raise Phase6ContractError(
                f"Runtime scenario {self.scenario_id} requires a command"
            )
        for field, value in (
            ("timeout_seconds", self.timeout_seconds),
            ("startup_timeout_seconds", self.startup_timeout_seconds),
            ("cleanup_timeout_seconds", self.cleanup_timeout_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
                raise Phase6ContractError(
                    f"Runtime scenario {self.scenario_id} {field} must be 1..3600"
                )
        if self.health_interval_seconds <= 0:
            raise Phase6ContractError("health_interval_seconds must be positive")
        if self.startup_command and not self.health_command:
            raise Phase6ContractError(
                f"LIVE_LOCAL startup scenario {self.scenario_id} requires health_command"
            )
        if self.health_command and not self.startup_command:
            raise Phase6ContractError(
                f"Runtime scenario {self.scenario_id} health_command requires startup_command"
            )
        if self.profile == ProofLevel.TEST_EXTERNAL.value:
            if not self.disposable and not self.cleanup_command:
                raise Phase6ContractError(
                    f"TEST_EXTERNAL scenario {self.scenario_id} requires cleanup_command or disposable=true"
                )
        if self.profile == ProofLevel.PROD_OBSERVE.value:
            if not self.read_only_enforced:
                raise Phase6ContractError(
                    f"PROD_OBSERVE scenario {self.scenario_id} requires read_only_enforced=true"
                )
            if self.startup_command or self.cleanup_command:
                raise Phase6ContractError(
                    f"PROD_OBSERVE scenario {self.scenario_id} cannot define startup/cleanup mutation lifecycle"
                )

    @property
    def advertised_capabilities(self) -> frozenset[str]:
        return frozenset(
            {
                *self.capabilities,
                _RUNTIME_PROFILE_CAPABILITY[self.profile],
            }
        )

    def public_summary(self) -> dict[str, Any]:
        return {
            "protocol_version": RUNTIME_SCENARIO_VERSION,
            "scenario_id": self.scenario_id,
            "profile": self.profile,
            "capabilities": sorted(self.advertised_capabilities),
            "has_startup": bool(self.startup_command),
            "has_cleanup": bool(self.cleanup_command),
            "disposable": self.disposable,
            "read_only_enforced": self.read_only_enforced,
        }


@dataclasses.dataclass(frozen=True)
class RuntimeRequirement:
    item_id: str
    profile: str
    capabilities: tuple[str, ...]
    claims: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class RuntimeScenarioExecution:
    scenario_id: str
    profile: str
    status: str
    candidate_before: str
    candidate_after: str
    request: dict[str, Any]
    result: dict[str, Any] | None
    stdout: str
    stderr: str
    startup_stdout: str
    startup_stderr: str
    cleanup_stdout: str
    cleanup_stderr: str
    duration_seconds: float
    reason_code: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == RuntimeStatus.PASS.value

    def public_record(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "profile": self.profile,
            "status": self.status,
            "candidate_before": self.candidate_before,
            "candidate_after": self.candidate_after,
            "request": self.request,
            "result": self.result,
            "duration_seconds": self.duration_seconds,
            "reason_code": self.reason_code,
        }

    def private_record(self) -> dict[str, Any]:
        return {
            **self.public_record(),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "startup_stdout": self.startup_stdout,
            "startup_stderr": self.startup_stderr,
            "cleanup_stdout": self.cleanup_stdout,
            "cleanup_stderr": self.cleanup_stderr,
        }


@dataclasses.dataclass(frozen=True)
class RuntimeVerificationRecord:
    protocol_version: str
    status: str
    candidate_id: str
    verification_plan_fingerprint: str
    scenarios: tuple[RuntimeScenarioExecution, ...]
    reason_code: str | None = None

    def public_record(self) -> dict[str, Any]:
        value = {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "candidate_id": self.candidate_id,
            "verification_plan_fingerprint": self.verification_plan_fingerprint,
            "scenarios": [item.public_record() for item in self.scenarios],
            "reason_code": self.reason_code,
        }
        value["fingerprint"] = stable_fingerprint(value)
        return value

    def private_record(self) -> dict[str, Any]:
        value = {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "candidate_id": self.candidate_id,
            "verification_plan_fingerprint": self.verification_plan_fingerprint,
            "scenarios": [item.private_record() for item in self.scenarios],
            "reason_code": self.reason_code,
        }
        value["fingerprint"] = stable_fingerprint(value)
        return value


@dataclasses.dataclass(frozen=True)
class _Completed:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    infra_error: bool = False


def _require_string_array(value: object, *, field: str, allow_empty: bool = True) -> tuple[str, ...]:
    if value is None and allow_empty:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Phase6ContractError(f"{field} must be an array of strings")
    rows = tuple(item for item in value if item.strip())
    if not allow_empty and not rows:
        raise Phase6ContractError(f"{field} must be non-empty")
    return rows


def _optional_bool(value: object, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise Phase6ContractError(f"{field} must be boolean")
    return value


def _optional_int(value: object, *, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise Phase6ContractError(f"{field} must be an integer")
    return value


def _optional_number(value: object, *, field: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Phase6ContractError(f"{field} must be numeric")
    return float(value)


def runtime_scenarios_from_config(
    local_config: Mapping[str, Any], *, project_name: str | None
) -> tuple[RuntimeScenarioConfig, ...]:
    if not project_name:
        return ()
    project = local_config.get("projects", {}).get(project_name, {})
    if not isinstance(project, Mapping):
        return ()
    table = project.get("runtime_verification")
    if table is None:
        return ()
    if not isinstance(table, Mapping):
        raise Phase6ContractError(
            f"[projects.{project_name}.runtime_verification] must be a table"
        )
    unknown_table = sorted(set(table) - {"enabled", "scenarios"})
    if unknown_table:
        raise Phase6ContractError(
            "Unknown runtime_verification fields: " + ", ".join(unknown_table)
        )
    enabled = _optional_bool(
        table.get("enabled"),
        field=f"projects.{project_name}.runtime_verification.enabled",
        default=True,
    )
    if not enabled:
        return ()
    rows = table.get("scenarios", [])
    if not isinstance(rows, list):
        raise Phase6ContractError("runtime_verification.scenarios must be an array of tables")
    scenarios: list[RuntimeScenarioConfig] = []
    seen: set[str] = set()
    allowed = {
        "id",
        "profile",
        "capabilities",
        "command",
        "timeout_seconds",
        "startup_command",
        "health_command",
        "startup_timeout_seconds",
        "health_interval_seconds",
        "cleanup_command",
        "cleanup_timeout_seconds",
        "disposable",
        "read_only_enforced",
        "preserve_env",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise Phase6ContractError(
                f"runtime_verification.scenarios[{index}] must be a table"
            )
        unknown = sorted(set(row) - allowed)
        if unknown:
            raise Phase6ContractError(
                f"Unknown runtime scenario fields at index {index}: {', '.join(unknown)}"
            )
        scenario_id = str(row.get("id", "")).strip()
        if scenario_id in seen:
            raise Phase6ContractError(f"Duplicate runtime scenario id: {scenario_id}")
        seen.add(scenario_id)
        profile = str(row.get("profile", "")).strip()
        scenario = RuntimeScenarioConfig(
            scenario_id=scenario_id,
            profile=profile,
            capabilities=_require_string_array(
                row.get("capabilities", []),
                field=f"runtime_verification.scenarios[{index}].capabilities",
            ),
            command=_require_string_array(
                row.get("command"),
                field=f"runtime_verification.scenarios[{index}].command",
                allow_empty=False,
            ),
            timeout_seconds=_optional_int(
                row.get("timeout_seconds"),
                field=f"runtime_verification.scenarios[{index}].timeout_seconds",
                default=300,
            ),
            startup_command=_require_string_array(
                row.get("startup_command", []),
                field=f"runtime_verification.scenarios[{index}].startup_command",
            ),
            health_command=_require_string_array(
                row.get("health_command", []),
                field=f"runtime_verification.scenarios[{index}].health_command",
            ),
            startup_timeout_seconds=_optional_int(
                row.get("startup_timeout_seconds"),
                field=f"runtime_verification.scenarios[{index}].startup_timeout_seconds",
                default=60,
            ),
            health_interval_seconds=_optional_number(
                row.get("health_interval_seconds"),
                field=f"runtime_verification.scenarios[{index}].health_interval_seconds",
                default=1.0,
            ),
            cleanup_command=_require_string_array(
                row.get("cleanup_command", []),
                field=f"runtime_verification.scenarios[{index}].cleanup_command",
            ),
            cleanup_timeout_seconds=_optional_int(
                row.get("cleanup_timeout_seconds"),
                field=f"runtime_verification.scenarios[{index}].cleanup_timeout_seconds",
                default=60,
            ),
            disposable=_optional_bool(
                row.get("disposable"),
                field=f"runtime_verification.scenarios[{index}].disposable",
                default=False,
            ),
            read_only_enforced=_optional_bool(
                row.get("read_only_enforced"),
                field=f"runtime_verification.scenarios[{index}].read_only_enforced",
                default=False,
            ),
            preserve_env=_require_string_array(
                row.get("preserve_env", []),
                field=f"runtime_verification.scenarios[{index}].preserve_env",
            ),
        )
        scenarios.append(scenario)
    return tuple(sorted(scenarios, key=lambda item: item.scenario_id))


def runtime_requirements(verification_plan: Mapping[str, Any]) -> tuple[RuntimeRequirement, ...]:
    validate_verification_plan(verification_plan)
    rows: list[RuntimeRequirement] = []
    for item in verification_plan["requirements"]:
        claims = tuple(str(value) for value in item["claims"])
        for profile in item["profiles"]:
            level = str(profile["level"])
            if level == ProofLevel.LOCAL_DETERMINISTIC.value:
                continue
            rows.append(
                RuntimeRequirement(
                    item_id=str(item["item_id"]),
                    profile=level,
                    capabilities=tuple(sorted(str(value) for value in profile["capabilities"])),
                    claims=claims,
                )
            )
    return tuple(rows)


def runtime_available_capabilities(
    scenarios: Iterable[RuntimeScenarioConfig],
) -> set[str]:
    result: set[str] = set()
    for scenario in scenarios:
        result.update(scenario.advertised_capabilities)
    return result


def runtime_requirement_gaps(
    verification_plan: Mapping[str, Any],
    scenarios: Iterable[RuntimeScenarioConfig],
) -> list[str]:
    available = tuple(scenarios)
    gaps: list[str] = []
    for requirement in runtime_requirements(verification_plan):
        required = set(requirement.capabilities)
        matching = [
            item
            for item in available
            if item.profile == requirement.profile
            and required.issubset(item.advertised_capabilities)
        ]
        if not matching:
            gaps.append(
                f"{requirement.item_id}:{requirement.profile}:"
                + ",".join(sorted(required))
            )
    return sorted(gaps)


def assign_runtime_scenarios(
    verification_plan: Mapping[str, Any],
    scenarios: Iterable[RuntimeScenarioConfig],
) -> tuple[tuple[RuntimeScenarioConfig, tuple[RuntimeRequirement, ...]], ...]:
    scenario_rows = tuple(sorted(scenarios, key=lambda item: item.scenario_id))
    grouped: dict[str, list[RuntimeRequirement]] = {}
    by_id = {item.scenario_id: item for item in scenario_rows}
    for requirement in runtime_requirements(verification_plan):
        required = set(requirement.capabilities)
        candidates = [
            item
            for item in scenario_rows
            if item.profile == requirement.profile
            and required.issubset(item.advertised_capabilities)
        ]
        if not candidates:
            raise Phase6ContractError(
                "No configured runtime scenario covers "
                f"{requirement.item_id}/{requirement.profile}/{sorted(required)}"
            )
        selected = candidates[0]
        grouped.setdefault(selected.scenario_id, []).append(requirement)
    return tuple(
        (by_id[scenario_id], tuple(grouped[scenario_id]))
        for scenario_id in sorted(grouped)
    )


def _expand_command(
    command: Sequence[str],
    *,
    workspace: Path,
    toolchain: Mapping[str, str],
    runtime_port: int,
    runtime_scratch: Path,
    request_path: Path,
    result_path: Path,
) -> list[str]:
    values = {
        **{str(key): str(value) for key, value in toolchain.items()},
        "workspace": str(workspace),
        "runtime_port": str(runtime_port),
        "runtime_scratch": str(runtime_scratch),
        "runtime_request": str(request_path),
        "runtime_result": str(result_path),
        "python": resolve_python_command(toolchain).value,
        "harness_python": resolve_python_command(
            toolchain, placeholder="harness_python"
        ).value,
    }
    try:
        return expand_command_template(command, values=values)
    except CommandTemplateError as exc:
        raise Phase6ContractError(str(exc)) from exc


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_completed(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
) -> _Completed:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return _Completed(result.returncode, result.stdout, result.stderr)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return _Completed(None, stdout, stderr, timed_out=True)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return _Completed(None, "", str(exc), infra_error=True)


def _redact_text(value: str, secrets: Iterable[str]) -> str:
    result = str(value)
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        result = result.replace(secret, "<redacted>")
    if len(result) > MAX_RUNTIME_LOG_CHARS:
        result = (
            result[:MAX_RUNTIME_LOG_CHARS]
            + f"\n... RUNTIME LOG TRUNCATED, total characters={len(result)} ...\n"
        )
    return result


def _redact_runtime_result(value: Mapping[str, Any], secrets: Iterable[str]) -> dict[str, Any]:
    result = dict(value)
    result["summary"] = _redact_text(str(result.get("summary", "")), secrets)
    requirement_rows: list[dict[str, Any]] = []
    for raw in result.get("requirement_results", []):
        row = dict(raw)
        row["evidence"] = [
            _redact_text(str(item), secrets) for item in row.get("evidence", [])
        ]
        requirement_rows.append(row)
    result["requirement_results"] = requirement_rows
    cleanup = dict(result.get("cleanup", {}))
    cleanup["evidence"] = [
        _redact_text(str(item), secrets) for item in cleanup.get("evidence", [])
    ]
    result["cleanup"] = cleanup
    return result


def _terminate_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _validate_runtime_result(
    value: Mapping[str, Any],
    *,
    scenario: RuntimeScenarioConfig,
    requirements: Sequence[RuntimeRequirement],
    candidate_id: str,
) -> dict[str, Any]:
    allowed = {
        "protocol_version",
        "scenario_id",
        "profile",
        "status",
        "summary",
        "oracle_reached",
        "candidate_id",
        "requirement_results",
        "initial_state_confirmed",
        "fresh_readback_confirmed",
        "cleanup",
        "read_only_confirmed",
    }
    required = allowed
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown or missing:
        raise Phase6ContractError(
            f"Runtime result fields invalid; missing={missing}, unknown={unknown}"
        )
    if value["protocol_version"] != RUNTIME_RESULT_VERSION:
        raise Phase6ContractError("Runtime result protocol mismatch")
    if value["scenario_id"] != scenario.scenario_id:
        raise Phase6ContractError("Runtime result scenario_id mismatch")
    if value["profile"] != scenario.profile:
        raise Phase6ContractError("Runtime result profile mismatch")
    if value["status"] not in {RuntimeStatus.PASS.value, *_RUNTIME_FAILURES}:
        raise Phase6ContractError(f"Unknown runtime result status: {value['status']}")
    if value["candidate_id"] != candidate_id:
        raise Phase6ContractError("Runtime result candidate_id mismatch")
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise Phase6ContractError("Runtime result summary must be non-empty")
    if not isinstance(value["oracle_reached"], bool):
        raise Phase6ContractError("Runtime result oracle_reached must be boolean")
    rows = value["requirement_results"]
    if not isinstance(rows, list):
        raise Phase6ContractError("Runtime requirement_results must be a list")
    expected = {item.item_id for item in requirements}
    observed: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise Phase6ContractError(
                f"Runtime requirement_results[{index}] must be an object"
            )
        if set(row) != {"item_id", "status", "evidence"}:
            raise Phase6ContractError(
                f"Runtime requirement_results[{index}] fields invalid"
            )
        item_id = str(row["item_id"])
        if item_id in observed:
            raise Phase6ContractError("Runtime result contains duplicate item_id")
        observed.add(item_id)
        if row["status"] not in {"PASS", "FAIL"}:
            raise Phase6ContractError("Runtime requirement result status must be PASS/FAIL")
        evidence = row["evidence"]
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise Phase6ContractError(
                "Runtime requirement result evidence must be non-empty strings"
            )
    if observed != expected:
        raise Phase6ContractError(
            f"Runtime result item coverage mismatch; expected={sorted(expected)}, actual={sorted(observed)}"
        )
    for field in (
        "initial_state_confirmed",
        "fresh_readback_confirmed",
        "read_only_confirmed",
    ):
        if not isinstance(value[field], bool):
            raise Phase6ContractError(f"Runtime result {field} must be boolean")
    cleanup = value["cleanup"]
    if not isinstance(cleanup, Mapping) or set(cleanup) != {
        "required",
        "confirmed",
        "evidence",
    }:
        raise Phase6ContractError("Runtime result cleanup fields invalid")
    if not isinstance(cleanup["required"], bool) or not isinstance(
        cleanup["confirmed"], bool
    ):
        raise Phase6ContractError("Runtime result cleanup flags must be boolean")
    if not isinstance(cleanup["evidence"], list) or not all(
        isinstance(item, str) for item in cleanup["evidence"]
    ):
        raise Phase6ContractError("Runtime cleanup evidence must be strings")

    if value["status"] == RuntimeStatus.PASS.value:
        if not value["oracle_reached"]:
            raise Phase6ContractError("Runtime PASS requires oracle_reached=true")
        if any(row["status"] != "PASS" for row in rows):
            raise Phase6ContractError("Runtime PASS requires every requirement PASS")
        if scenario.profile in {
            ProofLevel.LIVE_LOCAL.value,
            ProofLevel.TEST_EXTERNAL.value,
        } and not value["initial_state_confirmed"]:
            raise Phase6ContractError(
                f"{scenario.profile} PASS requires initial_state_confirmed=true"
            )
        if scenario.profile == ProofLevel.TEST_EXTERNAL.value:
            if not value["fresh_readback_confirmed"]:
                raise Phase6ContractError(
                    "TEST_EXTERNAL PASS requires fresh_readback_confirmed=true"
                )
            if not scenario.disposable and not cleanup["required"]:
                raise Phase6ContractError(
                    "TEST_EXTERNAL PASS must declare cleanup required for non-disposable scenario"
                )
        if scenario.profile == ProofLevel.PROD_OBSERVE.value:
            if not value["read_only_confirmed"]:
                raise Phase6ContractError(
                    "PROD_OBSERVE PASS requires read_only_confirmed=true"
                )
    return dict(value)


class RuntimeExecutor:
    """Execute Controller-owned runtime scenarios required by Verification Plan."""

    def __init__(
        self,
        *,
        workspace: Path,
        source_repo: Path | None,
        toolchain: Mapping[str, str],
        execution_broker: ExecutionBroker,
        runtime_integrity_manager: RuntimeProjectionIntegrityManager | None = None,
        git_integrity_manager: GitControlIntegrityManager | None = None,
    ) -> None:
        self.workspace = canonical_path(workspace)
        self.source_repo = canonical_path(source_repo) if source_repo else None
        self.toolchain = {str(key): str(value) for key, value in toolchain.items()}
        self.execution_broker = execution_broker
        self.runtime_integrity_manager = runtime_integrity_manager
        self.git_integrity_manager = git_integrity_manager

    def _source_head(self) -> str | None:
        if self.source_repo is None:
            return None
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.source_repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise Phase6ContractError(
                "Cannot read source HEAD during runtime verification: "
                + result.stderr.strip()
            )
        return result.stdout.strip()

    def _source_status(self) -> str | None:
        if self.source_repo is None:
            return None
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.source_repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise Phase6ContractError(
                "Cannot read source status during runtime verification: "
                + result.stderr.strip()
            )
        return result.stdout

    def _run_scenario(
        self,
        scenario: RuntimeScenarioConfig,
        requirements: Sequence[RuntimeRequirement],
        *,
        verification_plan_fingerprint: str,
        index: int,
    ) -> RuntimeScenarioExecution:
        started = time.monotonic()
        candidate_before = build_candidate_identity(self.workspace)
        source_head_before = self._source_head()
        source_status_before = self._source_status()
        scratch = self.execution_broker.scratch_root(ExecutionRole.RUNTIME) / (
            f"scenario_{index:02d}_{scenario.scenario_id}"
        )
        if scratch.exists():
            import shutil

            shutil.rmtree(scratch)
        scratch.mkdir(parents=True, exist_ok=True)
        request_path = scratch / "request.json"
        result_path = scratch / "result.json"
        runtime_port = _free_local_port()
        request = {
            "protocol_version": RUNTIME_REQUEST_VERSION,
            "scenario_id": scenario.scenario_id,
            "profile": scenario.profile,
            "candidate_id": candidate_before.candidate_id,
            "verification_plan_fingerprint": verification_plan_fingerprint,
            "requirements": [item.to_dict() for item in requirements],
            "runtime_port": runtime_port,
        }
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        result_path.unlink(missing_ok=True)
        extra = {
            "SLIVIN_RUNTIME_REQUEST": str(request_path),
            "SLIVIN_RUNTIME_RESULT": str(result_path),
            "SLIVIN_RUNTIME_SCENARIO_ID": scenario.scenario_id,
            "SLIVIN_RUNTIME_PROFILE": scenario.profile,
            "SLIVIN_RUNTIME_PORT": str(runtime_port),
        }
        env = self.execution_broker.environment_for(
            ExecutionRole.RUNTIME,
            extra=extra,
            preserve_sensitive=scenario.preserve_env,
        )
        env.update(
            {
                "TEMP": str(scratch),
                "TMP": str(scratch),
                "TMPDIR": str(scratch),
                "XDG_CACHE_HOME": str(scratch / "cache"),
                "NPM_CONFIG_CACHE": str(scratch / "npm"),
            }
        )
        secret_values = tuple(
            str(env.get(name, ""))
            for name in scenario.preserve_env
            if str(env.get(name, ""))
        )
        values = {
            "workspace": self.workspace,
            "toolchain": self.toolchain,
            "runtime_port": runtime_port,
            "runtime_scratch": scratch,
            "request_path": request_path,
            "result_path": result_path,
        }
        startup_process: subprocess.Popen[Any] | None = None
        startup_stdout = ""
        startup_stderr = ""
        startup_stdout_path = scratch / "startup.stdout.log"
        startup_stderr_path = scratch / "startup.stderr.log"
        startup_stdout_handle = None
        startup_stderr_handle = None
        cleanup_stdout = ""
        cleanup_stderr = ""
        scenario_completed = _Completed(None, "", "")
        status = RuntimeStatus.INFRA_ERROR.value
        reason_code: str | None = None
        result_value: dict[str, Any] | None = None
        scenario_attempted = False
        startup_ready = not scenario.startup_command

        try:
            if scenario.startup_command:
                startup_command = _expand_command(
                    scenario.startup_command,
                    workspace=values["workspace"],
                    toolchain=values["toolchain"],
                    runtime_port=values["runtime_port"],
                    runtime_scratch=values["runtime_scratch"],
                    request_path=values["request_path"],
                    result_path=values["result_path"],
                )
                try:
                    startup_stdout_handle = startup_stdout_path.open(
                        "w", encoding="utf-8", newline="\n"
                    )
                    startup_stderr_handle = startup_stderr_path.open(
                        "w", encoding="utf-8", newline="\n"
                    )
                    startup_process = subprocess.Popen(
                        startup_command,
                        cwd=self.workspace,
                        env=env,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        stdout=startup_stdout_handle,
                        stderr=startup_stderr_handle,
                    )
                except (FileNotFoundError, PermissionError, OSError) as exc:
                    if startup_stdout_handle is not None:
                        startup_stdout_handle.close()
                    if startup_stderr_handle is not None:
                        startup_stderr_handle.close()
                    status = RuntimeStatus.INFRA_ERROR.value
                    reason_code = "RUNTIME_START_INFRA_ERROR"
                    scenario_completed = _Completed(
                        None, "", str(exc), infra_error=True
                    )
                else:
                    health_command = _expand_command(
                        scenario.health_command,
                        workspace=values["workspace"],
                        toolchain=values["toolchain"],
                        runtime_port=values["runtime_port"],
                        runtime_scratch=values["runtime_scratch"],
                        request_path=values["request_path"],
                        result_path=values["result_path"],
                    )
                    deadline = time.monotonic() + scenario.startup_timeout_seconds
                    health_tail = ""
                    while time.monotonic() < deadline:
                        if startup_process.poll() is not None:
                            status = RuntimeStatus.START_FAIL.value
                            reason_code = "RUNTIME_PROCESS_EXITED_BEFORE_HEALTH"
                            break
                        remaining = max(1, int(deadline - time.monotonic()))
                        health = _run_completed(
                            health_command,
                            cwd=self.workspace,
                            env=env,
                            timeout=min(30, remaining),
                        )
                        health_tail = (health.stdout + "\n" + health.stderr)[-4000:]
                        if health.infra_error:
                            status = RuntimeStatus.INFRA_ERROR.value
                            reason_code = "RUNTIME_HEALTH_INFRA_ERROR"
                            scenario_completed = health
                            break
                        if (
                            health.returncode == 0
                            and not health.timed_out
                        ):
                            startup_ready = True
                            break
                        time.sleep(scenario.health_interval_seconds)
                    if not startup_ready and reason_code is None:
                        status = RuntimeStatus.START_FAIL.value
                        reason_code = "RUNTIME_HEALTH_TIMEOUT"
                        scenario_completed = _Completed(
                            None, "", health_tail, timed_out=True
                        )

            if startup_ready:
                command = _expand_command(
                    scenario.command,
                    workspace=values["workspace"],
                    toolchain=values["toolchain"],
                    runtime_port=values["runtime_port"],
                    runtime_scratch=values["runtime_scratch"],
                    request_path=values["request_path"],
                    result_path=values["result_path"],
                )
                scenario_attempted = True
                scenario_completed = _run_completed(
                    command,
                    cwd=self.workspace,
                    env=env,
                    timeout=scenario.timeout_seconds,
                )
                if scenario_completed.infra_error:
                    status = RuntimeStatus.INFRA_ERROR.value
                    reason_code = "RUNTIME_COMMAND_INFRA_ERROR"
                elif scenario_completed.timed_out:
                    status = RuntimeStatus.TIMEOUT.value
                    reason_code = "RUNTIME_COMMAND_TIMEOUT"
                elif scenario_completed.returncode != 0:
                    status = RuntimeStatus.INFRA_ERROR.value
                    reason_code = "RUNTIME_COMMAND_NONZERO"
                elif not result_path.is_file():
                    status = RuntimeStatus.INFRA_ERROR.value
                    reason_code = "RUNTIME_RESULT_MISSING"
                elif result_path.stat().st_size > MAX_RUNTIME_RESULT_BYTES:
                    status = RuntimeStatus.INVALID_RESULT.value
                    reason_code = "RUNTIME_RESULT_TOO_LARGE"
                    result_value = {
                        "error": (
                            "Runtime result exceeds "
                            f"{MAX_RUNTIME_RESULT_BYTES} bytes"
                        )
                    }
                else:
                    try:
                        raw_result = json.loads(result_path.read_text(encoding="utf-8"))
                        if not isinstance(raw_result, Mapping):
                            raise Phase6ContractError("Runtime result must be an object")
                        validated_result = _validate_runtime_result(
                            raw_result,
                            scenario=scenario,
                            requirements=requirements,
                            candidate_id=candidate_before.candidate_id,
                        )
                        status = str(validated_result["status"])
                        result_value = _redact_runtime_result(
                            validated_result, secret_values
                        )
                        reason_code = (
                            None if status == RuntimeStatus.PASS.value else status
                        )
                    except (json.JSONDecodeError, Phase6ContractError, OSError) as exc:
                        status = RuntimeStatus.INVALID_RESULT.value
                        reason_code = "RUNTIME_RESULT_INVALID"
                        result_value = {"error": _redact_text(str(exc), secret_values)}
                        scenario_completed = dataclasses.replace(
                            scenario_completed,
                            stderr=(
                                scenario_completed.stderr + "\n" + str(exc)
                            ).strip(),
                            infra_error=True,
                        )

            # A timed-out or failed external action may already have mutated the
            # test system.  Therefore cleanup is attempted whenever the scenario
            # command was invoked, regardless of its result.
            if scenario_attempted and scenario.cleanup_command:
                cleanup_command = _expand_command(
                    scenario.cleanup_command,
                    workspace=values["workspace"],
                    toolchain=values["toolchain"],
                    runtime_port=values["runtime_port"],
                    runtime_scratch=values["runtime_scratch"],
                    request_path=values["request_path"],
                    result_path=values["result_path"],
                )
                cleanup_completed = _run_completed(
                    cleanup_command,
                    cwd=self.workspace,
                    env=env,
                    timeout=scenario.cleanup_timeout_seconds,
                )
                cleanup_stdout = cleanup_completed.stdout
                cleanup_stderr = cleanup_completed.stderr
                if (
                    cleanup_completed.infra_error
                    or cleanup_completed.timed_out
                    or cleanup_completed.returncode != 0
                ):
                    status = RuntimeStatus.CLEANUP_FAIL.value
                    reason_code = "RUNTIME_CLEANUP_COMMAND_FAILED"
                elif result_value is not None:
                    cleanup = dict(result_value["cleanup"])
                    cleanup["confirmed"] = True
                    cleanup["evidence"] = [
                        *list(cleanup.get("evidence", [])),
                        "Controller-owned cleanup command completed successfully.",
                    ]
                    result_value = {**result_value, "cleanup": cleanup}
        finally:
            _terminate_process(startup_process)
            if startup_stdout_handle is not None and not startup_stdout_handle.closed:
                startup_stdout_handle.close()
            if startup_stderr_handle is not None and not startup_stderr_handle.closed:
                startup_stderr_handle.close()
            if startup_stdout_path.is_file():
                startup_stdout = startup_stdout_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            if startup_stderr_path.is_file():
                startup_stderr = startup_stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )

        candidate_after = build_candidate_identity(self.workspace)
        source_head_after = self._source_head()
        source_status_after = self._source_status()
        if candidate_after.candidate_id != candidate_before.candidate_id:
            status = RuntimeStatus.MUTATED_CANDIDATE.value
            reason_code = "RUNTIME_MUTATED_CANDIDATE"
        elif (
            source_head_after != source_head_before
            or source_status_after != source_status_before
        ):
            status = RuntimeStatus.MUTATED_CANDIDATE.value
            reason_code = "RUNTIME_MUTATED_SOURCE"
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)
        return RuntimeScenarioExecution(
            scenario.scenario_id,
            scenario.profile,
            status,
            candidate_before.candidate_id,
            candidate_after.candidate_id,
            request,
            result_value,
            _redact_text(scenario_completed.stdout, secret_values),
            _redact_text(scenario_completed.stderr, secret_values),
            _redact_text(startup_stdout, secret_values),
            _redact_text(startup_stderr, secret_values),
            _redact_text(cleanup_stdout, secret_values),
            _redact_text(cleanup_stderr, secret_values),
            time.monotonic() - started,
            reason_code,
        )

    def execute(
        self,
        verification_plan: Mapping[str, Any],
        scenarios: Iterable[RuntimeScenarioConfig],
    ) -> RuntimeVerificationRecord:
        validate_verification_plan(verification_plan)
        candidate = build_candidate_identity(self.workspace)
        assignments = assign_runtime_scenarios(verification_plan, scenarios)
        if not assignments:
            return RuntimeVerificationRecord(
                protocol_version=RUNTIME_EVIDENCE_VERSION,
                status=RuntimeStatus.SKIPPED.value,
                candidate_id=candidate.candidate_id,
                verification_plan_fingerprint=str(verification_plan["fingerprint"]),
                scenarios=(),
                reason_code="NO_RUNTIME_PROOF_REQUIRED",
            )
        results_list: list[RuntimeScenarioExecution] = []
        for index, (scenario, requirements) in enumerate(assignments, start=1):
            def execute_scenario() -> RuntimeScenarioExecution:
                return self._run_scenario(
                    scenario,
                    requirements,
                    verification_plan_fingerprint=str(verification_plan["fingerprint"]),
                    index=index,
                )

            try:
                scenario_batch_id = f"RUNTIME_VERIFICATION:{scenario.scenario_id}"
                runtime_operation = (
                    (
                        lambda: self.runtime_integrity_manager.run_batch(
                            scenario_batch_id, execute_scenario
                        )
                    )
                    if self.runtime_integrity_manager is not None
                    else execute_scenario
                )
                result = (
                    self.git_integrity_manager.run_batch(
                        scenario_batch_id, runtime_operation
                    )
                    if self.git_integrity_manager is not None
                    else runtime_operation()
                )
            except (RuntimeProjectionIntegrityError, GitControlIntegrityError) as exc:
                candidate_now = build_candidate_identity(self.workspace).candidate_id
                result = RuntimeScenarioExecution(
                    scenario.scenario_id,
                    scenario.profile,
                    RuntimeStatus.INFRA_ERROR.value,
                    candidate_now,
                    candidate_now,
                    {
                        "protocol_version": RUNTIME_REQUEST_VERSION,
                        "scenario_id": scenario.scenario_id,
                        "profile": scenario.profile,
                    },
                    None,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    0.0,
                    exc.reason_code,
                )
            results_list.append(result)
        results = tuple(results_list)
        final_candidate = build_candidate_identity(self.workspace)
        if final_candidate.candidate_id != candidate.candidate_id:
            status = RuntimeStatus.MUTATED_CANDIDATE.value
            reason = "RUNTIME_MUTATED_CANDIDATE"
        else:
            first_failure = next((item for item in results if not item.passed), None)
            status = (
                RuntimeStatus.PASS.value
                if first_failure is None
                else first_failure.status
            )
            reason = None if first_failure is None else first_failure.reason_code
        return RuntimeVerificationRecord(
            protocol_version=RUNTIME_EVIDENCE_VERSION,
            status=status,
            candidate_id=candidate.candidate_id,
            verification_plan_fingerprint=str(verification_plan["fingerprint"]),
            scenarios=results,
            reason_code=reason,
        )


def build_contract_closure_record(
    *,
    implementation_contract: Mapping[str, Any],
    verification_plan: Mapping[str, Any],
    implementation_report: Mapping[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    rows = implementation_report.get("contract_evidence", [])
    evidence_by_id = {
        str(item["item_id"]): {
            "item_id": str(item["item_id"]),
            "status": (
                "NOT_AFFECTED"
                if item.get("status") == "NOT_APPLICABLE"
                else str(item.get("status"))
            ),
            "accepted_evidence": [str(value) for value in item.get("evidence", [])],
        }
        for item in rows
        if isinstance(item, Mapping) and item.get("item_id")
    }
    expected_ids = [str(item["id"]) for item in implementation_contract["items"]]
    if set(evidence_by_id) != set(expected_ids):
        raise Phase6ContractError(
            "Contract closure record does not cover the active Implementation Contract"
        )
    ordered = [evidence_by_id[item_id] for item_id in expected_ids]
    value: dict[str, Any] = {
        "protocol_version": CONTRACT_CLOSURE_VERSION,
        "candidate_id": candidate_id,
        "implementation_contract_fingerprint": implementation_contract["fingerprint"],
        "verification_plan_fingerprint": verification_plan["fingerprint"],
        "items": ordered,
    }
    value["fingerprint"] = stable_fingerprint(value)
    return value


def validate_contract_closure_record(
    value: Mapping[str, Any],
    *,
    implementation_contract: Mapping[str, Any],
    verification_plan: Mapping[str, Any],
    candidate_id: str,
) -> None:
    expected_keys = {
        "protocol_version",
        "candidate_id",
        "implementation_contract_fingerprint",
        "verification_plan_fingerprint",
        "items",
        "fingerprint",
    }
    if set(value) != expected_keys:
        raise Phase6ContractError("Contract closure fields invalid")
    if value["protocol_version"] != CONTRACT_CLOSURE_VERSION:
        raise Phase6ContractError("Contract closure protocol mismatch")
    if value["candidate_id"] != candidate_id:
        raise Phase6ContractError("Contract closure candidate mismatch")
    if value["implementation_contract_fingerprint"] != implementation_contract["fingerprint"]:
        raise Phase6ContractError("Contract closure contract mismatch")
    if value["verification_plan_fingerprint"] != verification_plan["fingerprint"]:
        raise Phase6ContractError("Contract closure Verification Plan mismatch")
    rows = value["items"]
    if not isinstance(rows, list):
        raise Phase6ContractError("Contract closure items must be a list")
    expected_items = {str(item["id"]): item for item in implementation_contract["items"]}
    observed: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "item_id",
            "status",
            "accepted_evidence",
        }:
            raise Phase6ContractError("Contract closure item fields invalid")
        item_id = str(row["item_id"])
        if item_id in observed or item_id not in expected_items:
            raise Phase6ContractError("Contract closure item id invalid")
        observed.add(item_id)
        status = str(row["status"])
        if status == "NOT_AFFECTED" and not expected_items[item_id]["allow_not_affected"]:
            raise Phase6ContractError("Only consumer closure may be NOT_AFFECTED")
        if status not in {"VERIFIED", "NOT_AFFECTED"}:
            raise Phase6ContractError("Contract closure item must be terminal")
        evidence = row["accepted_evidence"]
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise Phase6ContractError("Contract closure evidence must be non-empty")
    if observed != set(expected_items):
        raise Phase6ContractError("Contract closure does not cover all items")
    expected = stable_fingerprint({key: val for key, val in value.items() if key != "fingerprint"})
    if value["fingerprint"] != expected:
        raise Phase6ContractError("Contract closure fingerprint mismatch")


def validate_runtime_scenario_commands(
    scenarios: Iterable[RuntimeScenarioConfig],
    *,
    workspace: Path,
    toolchain: Mapping[str, str],
) -> None:
    """Resolve every configured Controller-owned command before Implementer starts."""
    import shutil

    scratch = workspace / ".harness_tmp" / "runtime" / "capability_probe"
    scratch.mkdir(parents=True, exist_ok=True)
    request_path = scratch / "request.json"
    result_path = scratch / "result.json"
    for scenario in scenarios:
        for label, command in (
            ("command", scenario.command),
            ("startup_command", scenario.startup_command),
            ("health_command", scenario.health_command),
            ("cleanup_command", scenario.cleanup_command),
        ):
            if not command:
                continue
            expanded = _expand_command(
                command,
                workspace=workspace,
                toolchain=toolchain,
                runtime_port=1,
                runtime_scratch=scratch,
                request_path=request_path,
                result_path=result_path,
            )
            executable = expanded[0]
            path = Path(executable).expanduser()
            if path.is_absolute() or any(sep in executable for sep in ("/", "\\")):
                if not path.is_file():
                    raise Phase6ContractError(
                        f"Runtime scenario {scenario.scenario_id} {label} executable does not exist: {path}"
                    )
            elif shutil.which(executable) is None:
                raise Phase6ContractError(
                    f"Runtime scenario {scenario.scenario_id} {label} executable not found on PATH: {executable}"
                )


def runtime_environment_gaps(
    scenarios: Iterable[RuntimeScenarioConfig],
    *,
    verification_plan: Mapping[str, Any],
    execution_broker: ExecutionBroker,
) -> list[str]:
    """Return missing owner-declared runtime environment inputs.

    A runtime scenario must not survive the pre-Implementer capability gate and
    only then discover that its scoped credential/environment input is absent.
    Values are never serialized; only missing variable names are reported.
    """
    gaps: list[str] = []
    if runtime_requirement_gaps(verification_plan, scenarios):
        return gaps
    selected = {
        scenario.scenario_id: scenario
        for scenario, _requirements in assign_runtime_scenarios(
            verification_plan, scenarios
        )
    }
    for scenario in selected.values():
        try:
            env = execution_broker.environment_for(
                ExecutionRole.RUNTIME,
                preserve_sensitive=scenario.preserve_env,
            )
        except RuntimeError as exc:
            gaps.append(f"{scenario.scenario_id}:environment_invalid:{exc}")
            continue
        for name in scenario.preserve_env:
            if not str(env.get(name, "")):
                gaps.append(f"{scenario.scenario_id}:missing_env:{name}")
    return sorted(set(gaps))


def runtime_selected_scenarios(
    verification_plan: Mapping[str, Any],
    scenarios: Iterable[RuntimeScenarioConfig],
) -> tuple[RuntimeScenarioConfig, ...]:
    """Return only scenarios selected to satisfy the active Verification Plan."""
    return tuple(
        scenario
        for scenario, _requirements in assign_runtime_scenarios(
            verification_plan, scenarios
        )
    )


def runtime_command_gaps(
    verification_plan: Mapping[str, Any],
    scenarios: Iterable[RuntimeScenarioConfig],
    *,
    workspace: Path,
    toolchain: Mapping[str, str],
) -> list[str]:
    """Validate only runtime scenarios selected by the active proof plan."""
    if runtime_requirement_gaps(verification_plan, scenarios):
        return []
    try:
        selected = runtime_selected_scenarios(verification_plan, scenarios)
        validate_runtime_scenario_commands(
            selected, workspace=workspace, toolchain=toolchain
        )
    except Phase6ContractError as exc:
        return [str(exc)]
    return []
