from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping

from slivin_harness.control_plane import canonical_path, is_within

EXECUTION_BROKER_VERSION = "execution-broker.v1"
ROLE_EXECUTION_CONTEXT_VERSION = "role-execution-context.v1"


class ScopedExecutionPolicyError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {message}")


class ExecutionRole(str, Enum):
    APP_SERVER = "app_server"
    INTAKE = "intake"
    PLANNER = "planner"
    IMPLEMENTER = "implementer"
    CONTROLLER_CHECK = "controller_check"
    RUNTIME = "runtime"
    EVALUATOR = "evaluator"
    HELDOUT = "heldout"


class EnforcementLevel(str, Enum):
    ENFORCED = "ENFORCED"
    ADVISORY = "ADVISORY"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class RoleExecutionContext:
    """Controller-owned project/read + session scratch/write context.

    Codex 0.153.4's native unelevated sandbox requires its session root to
    coincide with the writable root. Command cwd may still be the project.
    """

    role: ExecutionRole
    project_root: Path
    scratch_root: Path
    profile_id: str

    def cache_environment(self) -> dict[str, str]:
        return {
            "TEMP": str(self.scratch_root),
            "TMP": str(self.scratch_root),
            "TMPDIR": str(self.scratch_root),
            "XDG_CACHE_HOME": str(self.scratch_root / "cache"),
            "NPM_CONFIG_CACHE": str(self.scratch_root / "npm"),
            "SLIVIN_HARNESS_WORKSPACE": str(self.project_root),
            "SLIVIN_HARNESS_EXECUTION_ROLE": self.role.value,
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }

    def thread_config(self) -> dict:
        # Select a fresh named profile, never extend legacy workspace grants.
        # Native unelevated Windows cannot enforce deny-read/split-root rules.
        # Preserve legacy read access; the sole write grant is this scratch.
        return {
            f"permissions.{self.profile_id}.filesystem": {
                ":root": "read", str(self.scratch_root): "write",
            },
            f"permissions.{self.profile_id}.network.enabled": False,
            "shell_environment_policy.set": self.cache_environment(),
        }

    def thread_settings(self) -> dict:
        return {
            "cwd": str(self.scratch_root),
            "permissions": self.profile_id,
            "approvalPolicy": "never",
        }

    def instructions(self) -> str:
        return (
            "CONTROLLER EXECUTION CONTEXT:\n"
            f"Project root: {self.project_root}\n"
            f"Session root and sole writable scratch: {self.scratch_root}\n"
            "The project, its tests, dependencies and Git controls are read-only. "
            "Resolve repository-relative paths against the project root above; "
            "set command workdir/cwd to that project root for tests, searches and Git diff. "
            "Changing command cwd does not grant project writes. Follow the project's "
            "AGENTS.md and applicable nested repository instructions. The session root "
            "is inside the project so ancestor repository instruction discovery is retained. "
            "Use the provided role-local TEMP/TMP/TMPDIR and cache paths for diagnostics; "
            "do not use other roles' scratch or previous attempts. No permission escalation."
        )

    def requested_metadata(self) -> dict:
        return {
            "schema_version": ROLE_EXECUTION_CONTEXT_VERSION,
            "role": self.role.value,
            "project_root": str(self.project_root),
            "session_root": str(self.scratch_root),
            "scratch_root": str(self.scratch_root),
            "profile_id": self.profile_id,
            "filesystem": {":root": "read", str(self.scratch_root): "write"},
            "network_access": False,
            "approval_policy": "never",
            "environment": self.cache_environment(),
            "turn_policy": "INHERIT_THREAD_CONTEXT",
        }

    def validate_response(self, response: Mapping) -> dict:
        policy = response.get("sandbox", {})
        profile = response.get("activePermissionProfile", {}) or {}
        if not isinstance(policy, Mapping) or not isinstance(profile, Mapping):
            raise ScopedExecutionPolicyError("ROLE_EXECUTION_POLICY_MISMATCH", "Malformed App Server permission metadata")
        roots = policy.get("writableRoots", [])
        expected_root = str(self.scratch_root)
        if (
            response.get("cwd") != expected_root
            or response.get("runtimeWorkspaceRoots") != [expected_root]
            or response.get("approvalPolicy") != "never"
            or profile.get("id") != self.profile_id
            or profile.get("extends") is not None
            or policy.get("type") != "workspaceWrite"
            or not isinstance(roots, list)
            or any(root != expected_root for root in roots)
            or policy.get("networkAccess") is not False
            or policy.get("excludeTmpdirEnvVar") is not True
            or policy.get("excludeSlashTmp") is not True
        ):
            raise ScopedExecutionPolicyError(
                "ROLE_EXECUTION_POLICY_MISMATCH", "App Server did not report the requested scratch-only policy"
            )
        sources = response.get("instructionSources")
        if not isinstance(sources, list) or any(
            not isinstance(path, str) or is_within(self.project_root / ".harness_tmp", Path(path))
            for path in sources
        ):
            raise ScopedExecutionPolicyError(
                "ROLE_EXECUTION_INSTRUCTION_CONTAMINATION",
                "Repository instructions must not be inherited from temporary role artifacts",
            )
        return {
            "requested": self.requested_metadata(),
            "reported": {key: response[key] for key in (
                "cwd", "runtimeWorkspaceRoots", "approvalPolicy", "sandbox",
                "activePermissionProfile", "instructionSources",
            )},
            "reported_policy_validation": "PASS",
            # Metadata is not a filesystem probe; native acceptance records its
            # own actual operations without claiming universal enforcement.
            "filesystem_probe_result": "NOT_RUN_BY_THREAD_START",
        }


@dataclass(frozen=True)
class ExecutionPolicy:
    schema_version: str
    role: str
    cwd: str
    scratch_root: str
    readable_roots: tuple[str, ...]
    writable_roots: tuple[str, ...]
    filesystem_enforcement: str
    network_enforcement: str
    network_allowed: bool
    external_mutation_allowed: bool
    notes: tuple[str, ...]

    def to_dict(self) -> dict:
        value = asdict(self)
        value["readable_roots"] = list(self.readable_roots)
        value["writable_roots"] = list(self.writable_roots)
        value["notes"] = list(self.notes)
        return value


_SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:TOKEN|PASSWORD|PASSWD|SECRET|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET|CREDENTIAL)(?:_|$)",
    re.IGNORECASE,
)


class ExecutionBroker:
    """Build role-specific execution declarations and sanitized environments.

    Phase 2 deliberately distinguishes declared policy from OS enforcement.
    Native Controller subprocesses are therefore reported as ADVISORY until a
    later restricted runner proves and enforces the boundary.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        run_root: Path,
        private_root: Path,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        workspace_input = workspace.expanduser()
        run_root_input = run_root.expanduser()
        private_root_input = private_root.expanduser()
        self.workspace = canonical_path(workspace_input)
        self.run_root = canonical_path(run_root_input)
        self.private_root = canonical_path(private_root_input)
        self._private_root_aliases = self._path_aliases(private_root_input)
        self.base_env = dict(base_env or os.environ)
        if is_within(self.workspace, self.private_root):
            raise RuntimeError("Controller private root must be outside the agent workspace")

    @staticmethod
    def _normalize_path_text(value: str) -> str:
        """Normalize path-like text for lexical alias checks.

        This is only a fast first line.  Full path-valued environment entries
        are also canonicalized with :func:`is_within` below.
        """
        text = str(value).strip().strip('"').replace("\\", os.sep).replace("/", os.sep)
        normalized = os.path.normcase(os.path.normpath(text))
        # Windows APIs may return extended-length spellings for the same path.
        if normalized.startswith("\\\\?\\unc\\"):
            normalized = "\\\\" + normalized[8:]
        elif normalized.startswith("\\\\?\\"):
            normalized = normalized[4:]
        return normalized.rstrip("\\/")

    @classmethod
    def _path_aliases(cls, path: Path) -> tuple[str, ...]:
        aliases: set[str] = set()
        for candidate in (path.absolute(), canonical_path(path)):
            normalized = cls._normalize_path_text(str(candidate))
            if normalized:
                aliases.add(normalized)
        return tuple(sorted(aliases))

    @staticmethod
    def _contains_path_alias(value: str, alias: str) -> bool:
        """Match a path alias only at path/token boundaries.

        A private root named ``controller_private`` must not reject the
        unrelated sibling ``controller_private_backup``.
        """
        start = 0
        before_boundaries = {'"', "'", "=", " ", "\t", "(", "[", "{", os.pathsep}
        after_boundaries = {
            '"', "'", " ", "\t", ")", "]", "}", os.pathsep, os.sep, "/", "\\"
        }
        while True:
            index = value.find(alias, start)
            if index < 0:
                return False
            end = index + len(alias)
            before_ok = index == 0 or value[index - 1] in before_boundaries
            after_ok = end == len(value) or value[end] in after_boundaries
            if before_ok and after_ok:
                return True
            start = index + 1

    def _value_references_private_root(self, raw_value: str) -> bool:
        """Reject private-plane paths despite Windows lexical aliases.

        Environment values that are complete paths (or path-list entries) are
        canonicalized.  A lexical alias check is retained for values that
        embed a path in a larger string.
        """
        value = str(raw_value)
        normalized_value = self._normalize_path_text(value)
        if any(
            alias and self._contains_path_alias(normalized_value, alias)
            for alias in self._private_root_aliases
        ):
            return True

        candidates = [value]
        if os.pathsep in value:
            candidates.extend(value.split(os.pathsep))
        for candidate_text in candidates:
            candidate_text = candidate_text.strip().strip('"')
            if not candidate_text:
                continue
            try:
                candidate = Path(candidate_text).expanduser()
            except (OSError, TypeError, ValueError):
                continue
            if candidate.is_absolute() and is_within(self.private_root, candidate):
                return True
        return False

    def scratch_root(self, role: ExecutionRole) -> Path:
        root = self.workspace / ".harness_tmp" / role.value
        root.mkdir(parents=True, exist_ok=True)
        return canonical_path(root)

    def prepare_readonly_role(self, role: ExecutionRole) -> RoleExecutionContext:
        if role not in {ExecutionRole.PLANNER, ExecutionRole.EVALUATOR}:
            raise ScopedExecutionPolicyError("ROLE_EXECUTION_ROLE_INVALID", "Only Planner/Evaluator use scoped scratch")
        parent = self._checked_role_scratch_root(role)
        # mkdtemp uses mode 0700, which creates a protected owner-only DACL on
        # Windows/Python 3.13+. That prevents the restricted-token runner from
        # reading its own scratch. Use normal inherited directory permissions;
        # the selected Codex profile supplies the scoped write boundary.
        scratch = parent / ("session-" + uuid.uuid4().hex)
        scratch.mkdir(exist_ok=False)
        scratch = canonical_path(scratch)
        return RoleExecutionContext(role, self.workspace, scratch, "harness_" + role.value + "_" + scratch.name.replace("-", "_"))

    def _checked_role_scratch_root(self, role: ExecutionRole) -> Path:
        parent = self.workspace / ".harness_tmp" / role.value
        for path in (parent.parent, parent):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ScopedExecutionPolicyError("ROLE_EXECUTION_SCRATCH_UNSAFE", "Role scratch cannot be a link/junction")
        parent.mkdir(parents=True, exist_ok=True)
        if not is_within(self.workspace / ".harness_tmp", parent):
            raise ScopedExecutionPolicyError("ROLE_EXECUTION_SCRATCH_UNSAFE", "Role scratch escaped workspace")
        return parent

    def clear_role_scratch(self, role: ExecutionRole) -> None:
        if role not in {ExecutionRole.PLANNER, ExecutionRole.IMPLEMENTER, ExecutionRole.EVALUATOR}:
            raise ScopedExecutionPolicyError("ROLE_EXECUTION_ROLE_INVALID", "Only attempt role scratch may be reset")
        root = canonical_path(self._checked_role_scratch_root(role))
        # Containment/link checks above precede deletion. Jest cache names can
        # exceed MAX_PATH; normal rmtree can leave such children behind on Windows.
        target = str(root)
        if os.name == "nt" and not target.startswith("\\\\?\\"):
            target = "\\\\?\\UNC\\" + target[2:] if target.startswith("\\\\") else "\\\\?\\" + target
        try:
            shutil.rmtree(target)
            root.mkdir()
        except OSError as exc:
            raise ScopedExecutionPolicyError("ROLE_EXECUTION_SCRATCH_CLEANUP_FAILED", str(exc)) from exc

    def policy_for(self, role: ExecutionRole) -> ExecutionPolicy:
        scratch = self.scratch_root(role)
        if role == ExecutionRole.IMPLEMENTER:
            writable = (str(self.workspace),)
            fs_level = EnforcementLevel.ENFORCED
            notes = ("Codex workspace-write sandbox; Controller private plane is outside cwd.",)
        elif role in {ExecutionRole.PLANNER, ExecutionRole.EVALUATOR}:
            writable = (str(scratch),)
            fs_level = EnforcementLevel.ADVISORY
            notes = (
                "Each fresh thread selects a named scratch-only permission profile and unique session/cache root; project remains read-only. Requested/reported policy and native probe results are separate evidence.",
            )
        elif role == ExecutionRole.INTAKE:
            writable = (str(scratch),)
            fs_level = EnforcementLevel.ADVISORY
            notes = (
                "Target contract is project read-only plus scratch-write; native Windows capability must be probed before claiming full enforcement.",
            )
        elif role in {ExecutionRole.CONTROLLER_CHECK, ExecutionRole.HELDOUT}:
            writable = (str(scratch),)
            fs_level = EnforcementLevel.ADVISORY
            notes = (
                "Phase 2 records the boundary honestly; the restricted OS check runner is a later phase.",
            )
        else:
            writable = (str(scratch),)
            fs_level = EnforcementLevel.ADVISORY
            notes = ("Execution is brokered, but OS-level filesystem isolation is not yet universal.",)
        network_allowed = role in {ExecutionRole.APP_SERVER, ExecutionRole.IMPLEMENTER, ExecutionRole.RUNTIME}
        network_level = (
            EnforcementLevel.ADVISORY if network_allowed else EnforcementLevel.UNAVAILABLE
        )
        return ExecutionPolicy(
            schema_version=EXECUTION_BROKER_VERSION,
            role=role.value,
            cwd=str(self.workspace),
            scratch_root=str(scratch),
            readable_roots=(str(self.workspace),),
            writable_roots=writable,
            filesystem_enforcement=fs_level.value,
            network_enforcement=network_level.value,
            network_allowed=network_allowed,
            external_mutation_allowed=role == ExecutionRole.RUNTIME,
            notes=notes,
        )

    def environment_for(
        self,
        role: ExecutionRole,
        *,
        extra: Mapping[str, str] | None = None,
        preserve_sensitive: Iterable[str] = (),
    ) -> dict[str, str]:
        preserve = {item.upper() for item in preserve_sensitive}
        env: dict[str, str] = {}
        for key, value in self.base_env.items():
            upper = key.upper()
            if upper.startswith("SLIVIN_HARNESS_PRIVATE"):
                continue
            if _SECRET_NAME_RE.search(upper) and upper not in preserve:
                continue
            env[key] = value
        scratch = self.scratch_root(role)
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "TEMP": str(scratch),
                "TMP": str(scratch),
                "TMPDIR": str(scratch),
                "XDG_CACHE_HOME": str(scratch / "cache"),
                "NPM_CONFIG_CACHE": str(scratch / "npm"),
                # Read-only Git commands must not refresh/write the real index.
                "GIT_OPTIONAL_LOCKS": "0",
                "SLIVIN_HARNESS_WORKSPACE": str(self.workspace),
                "SLIVIN_HARNESS_EXECUTION_ROLE": role.value,
            }
        )
        if extra:
            for key, value in extra.items():
                upper = str(key).upper()
                if _SECRET_NAME_RE.search(upper) and upper not in preserve:
                    raise RuntimeError(
                        f"Sensitive environment key requires explicit preserve_sensitive: {key}"
                    )
                if self._value_references_private_root(str(value)):
                    raise RuntimeError("Controller private path cannot be exposed via environment")
                env[str(key)] = str(value)
        for value in env.values():
            if self._value_references_private_root(value):
                raise RuntimeError("Controller private path leaked into execution environment")
        return env
