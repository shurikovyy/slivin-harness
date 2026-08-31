from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from slivin_harness.control_plane import canonical_path, is_within
from slivin_harness.implementer import validate_implementation_contract
from slivin_harness.protocol import ArtifactContractError, safe_repo_relative, stable_fingerprint
from slivin_harness.verification import (
    compile_verification_plan,
    merged_required_proof,
    validate_proof_target,
    validate_verification_plan,
)

PHASE5_VERSION = "phase5-contract-runtime.v1"
CONTRACT_EXPANSION_VERSION = "contract-expansion.v1"
PROJECT_RUNTIME_VERSION = "project-runtime.v1"


class Phase5ContractError(RuntimeError):
    """A discovery/runtime artifact violates the Phase 5 contract."""


@dataclasses.dataclass(frozen=True)
class ContractExpansionResult:
    protocol_version: str
    implementation_contract: dict[str, Any]
    verification_plan: dict[str, Any]
    added_item_ids: tuple[str, ...]
    duplicate_discoveries: tuple[str, ...]
    runtime_profiles_changed: bool

    def summary(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "added_item_ids": list(self.added_item_ids),
            "duplicate_discoveries": list(self.duplicate_discoveries),
            "implementation_contract_fingerprint": self.implementation_contract["fingerprint"],
            "verification_plan_fingerprint": self.verification_plan["fingerprint"],
            "runtime_profiles_changed": self.runtime_profiles_changed,
        }


def _canonical_discovery(value: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    required = {
        "kind",
        "name",
        "reason",
        "required_behavior",
        "required_proof",
        "evidence",
    }
    unknown = sorted(set(value) - required)
    missing = sorted(required - set(value))
    if unknown or missing:
        raise Phase5ContractError(
            f"discovered_obligations[{index}] fields invalid; missing={missing}, unknown={unknown}"
        )
    kind = str(value["kind"])
    if kind not in {"consumer", "risk"}:
        raise Phase5ContractError("Discovered obligation kind must be consumer or risk")
    text: dict[str, str] = {}
    for field in ("name", "reason", "required_behavior"):
        raw = value[field]
        if not isinstance(raw, str) or not raw.strip():
            raise Phase5ContractError(f"Discovered obligation {field} must be non-empty")
        text[field] = raw.strip()
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        raise Phase5ContractError("Discovered obligation evidence must be a non-empty string list")
    proof = dict(validate_proof_target(value["required_proof"], field=f"discovered_obligations[{index}].required_proof"))
    return {
        "kind": kind,
        **text,
        "required_proof": proof,
        "evidence": [item.strip() for item in evidence],
    }


def _discovery_requirement(value: Mapping[str, Any]) -> str:
    if value["kind"] == "consumer":
        return (
            f"{value['name']}\n"
            f"Why affected: {value['reason']}\n"
            f"Must verify: {value['required_behavior']}"
        )
    return (
        f"Discovered risk: {value['name']}\n"
        f"Condition/reason: {value['reason']}\n"
        f"Failure mode to exclude: {value['required_behavior']}"
    )


def _next_discovered_index(items: Iterable[Mapping[str, Any]], prefix: str) -> int:
    highest = 0
    marker = f"{prefix}-DISCOVERED-"
    for item in items:
        raw = str(item.get("id", ""))
        if not raw.startswith(marker):
            continue
        try:
            highest = max(highest, int(raw[len(marker) :]))
        except ValueError:
            continue
    return highest + 1


def _contract_warnings(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if len(items) <= 14:
        return []
    return [
        "Implementation Contract exceeds the soft review threshold of 14 items; "
        "material obligations were retained and should be semantically grouped if possible."
    ]


def expand_contract_and_verification_plan(
    *,
    implementation_contract: Mapping[str, Any],
    previous_verification_plan: Mapping[str, Any],
    discoveries: Iterable[Mapping[str, Any]],
    project_checks: Iterable[Mapping[str, Any]],
    task_checks: Iterable[str],
) -> ContractExpansionResult:
    """Atomically expand the active Definition of Done and recompile its proof plan.

    The Implementer may discover facts, but the Controller owns the active Contract.
    Existing items remain byte-for-byte intact. Duplicate discoveries are recorded but
    do not create obligation explosion.
    """

    validate_implementation_contract(implementation_contract)
    validate_verification_plan(previous_verification_plan)
    items = [dict(item) for item in implementation_contract["items"]]
    existing = {(str(item["type"]), str(item["requirement"]).strip()) for item in items}
    counters = {
        "consumer": _next_discovered_index(items, "CONSUMER"),
        "risk": _next_discovered_index(items, "RISK"),
    }
    added: list[str] = []
    duplicates: list[str] = []

    for index, raw in enumerate(discoveries):
        if not isinstance(raw, Mapping):
            raise Phase5ContractError(f"discovered_obligations[{index}] must be an object")
        value = _canonical_discovery(raw, index=index)
        requirement = _discovery_requirement(value)
        key = (value["kind"], requirement)
        if key in existing:
            duplicates.append(value["name"])
            continue
        prefix = "CONSUMER" if value["kind"] == "consumer" else "RISK"
        item_id = f"{prefix}-DISCOVERED-{counters[value['kind']]}"
        counters[value["kind"]] += 1
        item = {
            "id": item_id,
            "type": value["kind"],
            "source": "DISCOVERED",
            "requirement": requirement,
            "required_proof": merged_required_proof(
                [value["required_proof"]],
                fallback_claim=value["required_behavior"],
            ),
            "allow_not_affected": value["kind"] == "consumer",
        }
        items.append(item)
        existing.add(key)
        added.append(item_id)

    contract = {
        "protocol_version": implementation_contract["protocol_version"],
        "task_contract_fingerprint": implementation_contract["task_contract_fingerprint"],
        "items": items,
        "warnings": _contract_warnings(items),
    }
    contract["fingerprint"] = stable_fingerprint(contract)
    validate_implementation_contract(contract)

    plan = compile_verification_plan(
        contract,
        project_checks=project_checks,
        task_checks=task_checks,
    )
    validate_verification_plan(plan)
    previous_profiles = tuple(previous_verification_plan.get("runtime_profiles", ()))
    return ContractExpansionResult(
        protocol_version=CONTRACT_EXPANSION_VERSION,
        implementation_contract=contract,
        verification_plan=plan,
        added_item_ids=tuple(added),
        duplicate_discoveries=tuple(duplicates),
        runtime_profiles_changed=tuple(plan["runtime_profiles"]) != previous_profiles,
    )


@dataclasses.dataclass(frozen=True)
class ProjectRuntimeConfig:
    bootstrap_python: Path
    expected_python: str
    venv_relative: str = ".venv"
    dependency_files: tuple[str, ...] = ("requirements.txt",)
    pip_install_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        safe_repo_relative(self.venv_relative, field="runtime.venv")
        for index, value in enumerate(self.dependency_files):
            safe_repo_relative(value, field=f"runtime.dependency_files[{index}]")
        if not self.expected_python.strip():
            raise Phase5ContractError("runtime.expected_python must be non-empty")


@dataclasses.dataclass(frozen=True)
class ProjectRuntimeState:
    protocol_version: str
    mode: str
    runtime_id: str
    bootstrap_python: str
    bootstrap_version: str
    project_python: str
    project_version: str
    venv_relative: str
    dependency_files: tuple[dict[str, str], ...]
    dependency_digest: str
    package_snapshot_sha256: str
    pip_check: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "mode": self.mode,
            "runtime_id": self.runtime_id,
            "bootstrap_python": self.bootstrap_python,
            "bootstrap_version": self.bootstrap_version,
            "project_python": self.project_python,
            "project_version": self.project_version,
            "venv_relative": self.venv_relative,
            "dependency_files": [dict(item) for item in self.dependency_files],
            "dependency_digest": self.dependency_digest,
            "package_snapshot_sha256": self.package_snapshot_sha256,
            "pip_check": self.pip_check,
        }


@dataclasses.dataclass(frozen=True)
class RuntimeReconciliation:
    changed: bool
    reasons: tuple[str, ...]
    before_runtime_id: str
    state: ProjectRuntimeState


CommandRunner = Callable[[Sequence[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]]


def _default_runner(
    command: Sequence[str], cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class ProjectRuntimeManager:
    """Build and reconcile a task-local authoritative Python runtime."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: ProjectRuntimeConfig,
        environment: Mapping[str, str] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.workspace = canonical_path(Path(workspace))
        self.config = config
        self.environment = dict(os.environ if environment is None else environment)
        self.runner = runner or _default_runner
        self.venv = canonical_path(self.workspace / config.venv_relative)
        if not is_within(self.workspace, self.venv):
            raise Phase5ContractError("Project venv escapes workspace")

    @property
    def project_python(self) -> Path:
        windows = self.venv / "Scripts" / "python.exe"
        posix = self.venv / "bin" / "python"
        if windows.exists():
            return windows
        if posix.exists():
            return posix
        return windows if os.name == "nt" else posix

    def _assert_worktree_local_entrypoint(self, python: Path) -> None:
        """Require the venv entry-point path itself to live in this worktree.

        POSIX venvs commonly expose ``bin/python`` as a symlink to the
        bootstrap executable. Resolving the leaf would therefore point outside
        the worktree even though checks correctly execute the worktree-local
        entry point and its isolated site-packages. Compare the canonical
        parent directory instead. This also tolerates native Windows
        tempfile/NTFS aliases and casing.
        """

        if not is_within(self.venv, python.parent):
            raise Phase5ContractError(
                f"Project runtime Python entry point escapes worktree venv: {python}"
            )

    def _run(self, command: Sequence[str]) -> str:
        completed = self.runner(command, self.workspace, self.environment)
        if completed.returncode != 0:
            rendered = subprocess.list2cmdline(list(command))
            raise Phase5ContractError(
                f"Project runtime command failed ({completed.returncode}): {rendered}\n"
                f"{(completed.stderr or completed.stdout or '').strip()}"
            )
        return (completed.stdout or "").strip()

    def _python_version(self, python: Path) -> str:
        return self._run(
            [
                str(python),
                "-c",
                "import sys; print('.'.join(map(str, sys.version_info[:3])))",
            ]
        ).splitlines()[-1].strip()

    def _validate_expected_version(self, version: str) -> None:
        expected = tuple(int(item) for item in self.config.expected_python.split("."))
        actual = tuple(int(item) for item in version.split(".")[: len(expected)])
        if actual != expected:
            raise Phase5ContractError(
                f"Configured bootstrap Python {version} does not satisfy {self.config.expected_python}"
            )

    def _dependency_rows(self) -> tuple[dict[str, str], ...]:
        rows: list[dict[str, str]] = []
        for raw in self.config.dependency_files:
            path = self.workspace / raw
            if not path.is_file():
                raise Phase5ContractError(f"Dependency declaration does not exist: {raw}")
            rows.append(
                {
                    "path": raw.replace("\\", "/"),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        return tuple(rows)

    @staticmethod
    def _dependency_digest(rows: Iterable[Mapping[str, str]]) -> str:
        payload = json.dumps(list(rows), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _snapshot(self, *, bootstrap_version: str) -> ProjectRuntimeState:
        python = self.project_python
        if not python.is_file():
            raise Phase5ContractError(f"Project runtime Python is missing: {python}")
        self._assert_worktree_local_entrypoint(python)
        project_version = self._python_version(python)
        pip_check_output = self._run([str(python), "-m", "pip", "check"])
        freeze = self._run([str(python), "-m", "pip", "freeze", "--all"])
        rows = self._dependency_rows()
        body = {
            "protocol_version": PROJECT_RUNTIME_VERSION,
            "mode": "WORKTREE_VENV",
            "bootstrap_python": str(self.config.bootstrap_python.resolve()),
            "bootstrap_version": bootstrap_version,
            # POSIX venv Python is commonly a symlink to the bootstrap
            # executable.  Preserve the worktree-local path instead of
            # resolving it back to /usr/bin/python; checks must execute the
            # venv entry point and its isolated site-packages.
            "project_python": str(python.absolute()),
            "project_version": project_version,
            "venv_relative": self.config.venv_relative.replace("\\", "/"),
            "dependency_files": [dict(item) for item in rows],
            "dependency_digest": self._dependency_digest(rows),
            "package_snapshot_sha256": hashlib.sha256(freeze.encode("utf-8")).hexdigest(),
            "pip_check": pip_check_output or "No broken requirements found.",
        }
        runtime_id = "project-runtime.v1:" + stable_fingerprint(body)
        return ProjectRuntimeState(runtime_id=runtime_id, **body)

    def build(self, *, clean: bool = True) -> ProjectRuntimeState:
        bootstrap = self.config.bootstrap_python
        if not bootstrap.is_file():
            raise Phase5ContractError(f"Bootstrap Python does not exist: {bootstrap}")
        bootstrap_version = self._python_version(bootstrap)
        self._validate_expected_version(bootstrap_version)
        if clean and self.venv.exists():
            shutil.rmtree(self.venv)
        if not self.venv.exists():
            self._run([str(bootstrap), "-m", "venv", str(self.venv)])
        python = self.project_python
        if not python.is_file():
            raise Phase5ContractError(f"venv creation did not produce project Python: {python}")
        for raw in self.config.dependency_files:
            self._run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    *self.config.pip_install_args,
                    "-r",
                    str(self.workspace / raw),
                ]
            )
        return self._snapshot(bootstrap_version=bootstrap_version)

    def inspect(self) -> ProjectRuntimeState:
        bootstrap_version = self._python_version(self.config.bootstrap_python)
        self._validate_expected_version(bootstrap_version)
        return self._snapshot(bootstrap_version=bootstrap_version)

    def reconcile(self, baseline: ProjectRuntimeState) -> RuntimeReconciliation:
        reasons: list[str] = []
        try:
            current = self.inspect()
        except Phase5ContractError:
            reasons.append("PROJECT_RUNTIME_MISSING_OR_INVALID")
            current = None
        if current is not None:
            if current.dependency_digest != baseline.dependency_digest:
                reasons.append("DEPENDENCY_MANIFEST_CHANGED")
            if current.package_snapshot_sha256 != baseline.package_snapshot_sha256:
                reasons.append("RUNTIME_ENV_DRIFT")
            if current.project_version != baseline.project_version:
                reasons.append("PROJECT_PYTHON_VERSION_CHANGED")
        if not reasons:
            assert current is not None
            return RuntimeReconciliation(False, (), baseline.runtime_id, current)
        rebuilt = self.build(clean=True)
        return RuntimeReconciliation(True, tuple(dict.fromkeys(reasons)), baseline.runtime_id, rebuilt)


def runtime_state_from_dict(value: Mapping[str, Any]) -> ProjectRuntimeState:
    if value.get("protocol_version") != PROJECT_RUNTIME_VERSION:
        raise Phase5ContractError("Unsupported project runtime state")
    rows = value.get("dependency_files")
    if not isinstance(rows, list):
        raise Phase5ContractError("project runtime dependency_files must be a list")
    return ProjectRuntimeState(
        protocol_version=str(value["protocol_version"]),
        mode=str(value["mode"]),
        runtime_id=str(value["runtime_id"]),
        bootstrap_python=str(value["bootstrap_python"]),
        bootstrap_version=str(value["bootstrap_version"]),
        project_python=str(value["project_python"]),
        project_version=str(value["project_version"]),
        venv_relative=str(value["venv_relative"]),
        dependency_files=tuple(dict(item) for item in rows),
        dependency_digest=str(value["dependency_digest"]),
        package_snapshot_sha256=str(value["package_snapshot_sha256"]),
        pip_check=str(value["pip_check"]),
    )
