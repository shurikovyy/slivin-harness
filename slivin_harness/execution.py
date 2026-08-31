from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping

from slivin_harness.control_plane import canonical_path, is_within

EXECUTION_BROKER_VERSION = "execution-broker.v1"


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

    def policy_for(self, role: ExecutionRole) -> ExecutionPolicy:
        scratch = self.scratch_root(role)
        if role == ExecutionRole.IMPLEMENTER:
            writable = (str(self.workspace),)
            fs_level = EnforcementLevel.ENFORCED
            notes = ("Codex workspace-write sandbox; Controller private plane is outside cwd.",)
        elif role in {ExecutionRole.INTAKE, ExecutionRole.PLANNER, ExecutionRole.EVALUATOR}:
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
