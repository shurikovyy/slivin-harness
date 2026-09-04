from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

from slivin_harness.verification import validate_proof_target

IMPLEMENTER_PROTOCOL_VERSION = "implementer.v3"
CHECK_REGISTRY_VERSION = "check-registry.v1"
CONTROLLER_CHECKS_VERSION = "controller-checks.v1"
WATCHDOG_VERSION = "activity-watchdog.v1"


class Phase4ContractError(RuntimeError):
    """Raised when an Implementer/check artifact violates the Phase 4 contract."""


class ImplementerStatus(str, enum.Enum):
    COMPLETE = "COMPLETE"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    BLOCKED = "BLOCKED"
    NEEDS_USER_DECISION = "NEEDS_USER_DECISION"


class ObligationStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"
    NOT_AFFECTED = "NOT_AFFECTED"
    BLOCKED = "BLOCKED"


class CheckClassification(str, enum.Enum):
    PASS = "CHECK_PASS"
    FAIL = "CHECK_FAIL"
    TIMEOUT = "CHECK_TIMEOUT"
    INFRA_ERROR = "CHECK_INFRA_ERROR"
    MUTATED_CANDIDATE = "CHECK_MUTATED_CANDIDATE"


@dataclasses.dataclass(frozen=True)
class RevisionBinding:
    candidate_id: str
    task_contract_rev: int
    plan_rev: int
    implementation_contract_rev: int
    verification_plan_rev: int
    runtime_env_id: str
    attempt_id: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def digest(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class SelfVerifyReceipt:
    protocol_version: str
    binding: RevisionBinding
    registry_digest: str
    completed_at_unix: float
    checks: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        binding: RevisionBinding,
        registry_digest: str,
        checks: Iterable[str],
        now: float | None = None,
    ) -> "SelfVerifyReceipt":
        return cls(
            protocol_version="self-verify-receipt.v2",
            binding=binding,
            registry_digest=registry_digest,
            completed_at_unix=time.time() if now is None else float(now),
            checks=tuple(checks),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "binding": self.binding.as_dict(),
            "registry_digest": self.registry_digest,
            "completed_at_unix": self.completed_at_unix,
            "checks": list(self.checks),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SelfVerifyReceipt":
        binding = RevisionBinding(**dict(value["binding"]))
        return cls(
            protocol_version=str(value["protocol_version"]),
            binding=binding,
            registry_digest=str(value["registry_digest"]),
            completed_at_unix=float(value["completed_at_unix"]),
            checks=tuple(str(item) for item in value.get("checks", ())),
        )

    def matches(self, *, binding: RevisionBinding, registry_digest: str) -> bool:
        return self.binding == binding and self.registry_digest == registry_digest


@dataclasses.dataclass(frozen=True)
class CheckReference:
    kind: str
    value: str
    source: str = "IMPLEMENTER"

    def canonical(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value, "source": self.source}


def _safe_repo_relative(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Phase4ContractError("Check path must be a non-empty string")
    raw = value.replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise Phase4ContractError(f"Unsafe repository-relative path: {value!r}")
    if ":" in pure.parts[0]:
        raise Phase4ContractError(f"Drive-qualified path is not allowed: {value!r}")
    return pure.as_posix()


class CheckRegistry:
    """Controller-owned typed task-check registry.

    The registry is stored in the private control plane. The agent may request
    registration, but cannot author arbitrary authoritative shell commands.
    """

    def __init__(
        self,
        path: Path,
        *,
        workspace: Path,
        trusted_check_ids: Iterable[str] = (),
    ) -> None:
        self.path = Path(path)
        self.workspace = Path(workspace).resolve()
        self.trusted_check_ids = frozenset(str(value) for value in trusted_check_ids)

    def _empty(self) -> dict[str, Any]:
        return {"protocol_version": CHECK_REGISTRY_VERSION, "revision": 0, "checks": []}

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("protocol_version") != CHECK_REGISTRY_VERSION:
            raise Phase4ContractError("Unsupported check registry protocol")
        if not isinstance(data.get("checks"), list):
            raise Phase4ContractError("Check registry checks must be a list")
        return data

    def save(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(dict(data), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def register_path(self, value: str, *, source: str = "IMPLEMENTER") -> CheckReference:
        rel = _safe_repo_relative(value)
        target = (self.workspace / Path(rel)).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise Phase4ContractError(f"Check path escapes workspace: {value!r}") from exc
        if not target.is_file():
            raise Phase4ContractError(f"Registered check path does not exist: {rel}")
        lower = rel.lower()
        supported = (
            lower.endswith(".py")
            or lower.endswith(".cjs")
            or lower.endswith(".mjs")
            or lower.endswith(".js")
            or lower.endswith(".ts")
            or lower.endswith(".tsx")
        )
        if not supported:
            raise Phase4ContractError(f"Unsupported typed check path: {rel}")
        return self._register(CheckReference("path", rel, source))

    def register_id(self, value: str, *, source: str = "IMPLEMENTER") -> CheckReference:
        if not isinstance(value, str) or not value.strip():
            raise Phase4ContractError("Check id must be a non-empty string")
        if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-" for ch in value):
            raise Phase4ContractError(f"Unsafe check id: {value!r}")
        if value not in self.trusted_check_ids:
            raise Phase4ContractError(f"Unknown trusted check id: {value!r}")
        return self._register(CheckReference("check_id", value, source))

    def _register(self, reference: CheckReference) -> CheckReference:
        data = self.load()
        canonical = reference.canonical()
        if canonical not in data["checks"]:
            data["checks"].append(canonical)
            data["checks"].sort(key=lambda item: (item["kind"], item["value"], item["source"]))
            data["revision"] = int(data.get("revision", 0)) + 1
            self.save(data)
        return reference

    def references(self) -> tuple[CheckReference, ...]:
        data = self.load()
        return tuple(CheckReference(**item) for item in data["checks"])

    def digest(self) -> str:
        data = self.load()
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def reset(self) -> None:
        """Start a new attempt with an empty typed task-check registry.

        A semantic replan invalidates task-specific checks discovered under the
        rejected technical model. Project gates are supplied separately by the
        Controller and therefore are not lost.
        """
        current = self.load()
        revision = int(current.get("revision", 0)) + 1
        data = self._empty()
        data["revision"] = revision
        self.save(data)


@dataclasses.dataclass
class ActivityWatchdog:
    inactivity_timeout_seconds: float
    started_at: float = dataclasses.field(default_factory=time.monotonic)
    last_real_activity_at: float | None = None
    active_tools: int = 0
    recovery_attempts: int = 0
    max_recovery_attempts: int = 1
    emergency_wall_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.inactivity_timeout_seconds <= 0:
            raise ValueError("inactivity_timeout_seconds must be positive")
        if self.last_real_activity_at is None:
            self.last_real_activity_at = self.started_at

    def note_activity(self, *, now: float | None = None) -> None:
        self.last_real_activity_at = time.monotonic() if now is None else float(now)

    def tool_started(self, *, now: float | None = None) -> None:
        self.active_tools += 1
        self.note_activity(now=now)

    def tool_completed(self, *, now: float | None = None) -> None:
        self.active_tools = max(0, self.active_tools - 1)
        self.note_activity(now=now)

    def inactivity_seconds(self, *, now: float | None = None) -> float:
        current = time.monotonic() if now is None else float(now)
        assert self.last_real_activity_at is not None
        return max(0.0, current - self.last_real_activity_at)

    def wall_seconds(self, *, now: float | None = None) -> float:
        current = time.monotonic() if now is None else float(now)
        return max(0.0, current - self.started_at)

    def should_interrupt(self, *, process_alive: bool, now: float | None = None) -> bool:
        if not process_alive:
            return True
        if self.active_tools > 0:
            return False
        if self.emergency_wall_timeout_seconds is not None:
            if self.wall_seconds(now=now) >= self.emergency_wall_timeout_seconds:
                return True
        return self.inactivity_seconds(now=now) >= self.inactivity_timeout_seconds

    def may_recover(self) -> bool:
        return self.recovery_attempts < self.max_recovery_attempts

    def register_recovery(self, *, now: float | None = None) -> None:
        self.recovery_attempts += 1
        self.note_activity(now=now)


@dataclasses.dataclass(frozen=True)
class ControllerCheckResult:
    protocol_version: str
    name: str
    classification: CheckClassification
    returncode: int | None
    duration_seconds: float
    stdout: str
    stderr: str
    candidate_before: str
    candidate_after: str
    command_identity: tuple[str, ...]
    execution_enforcement: str

    def as_dict(self) -> dict[str, Any]:
        value = dataclasses.asdict(self)
        value["classification"] = self.classification.value
        value["command_identity"] = list(self.command_identity)
        return value


def classify_check_result(
    *,
    returncode: int | None,
    timed_out: bool,
    infra_error: bool,
    candidate_changed: bool,
) -> CheckClassification:
    if candidate_changed:
        return CheckClassification.MUTATED_CANDIDATE
    if infra_error:
        return CheckClassification.INFRA_ERROR
    if timed_out:
        return CheckClassification.TIMEOUT
    if returncode == 0:
        return CheckClassification.PASS
    return CheckClassification.FAIL


class ControllerCheckRunner:
    """Broker-aware deterministic check runner with candidate freeze guards."""

    def __init__(
        self,
        *,
        cwd: Path,
        base_env: Mapping[str, str],
        fingerprint: Callable[[], str],
        execution_enforcement: str = "ADVISORY",
    ) -> None:
        self.cwd = Path(cwd)
        self.base_env = dict(base_env)
        self.fingerprint = fingerprint
        self.execution_enforcement = execution_enforcement

    def run(
        self,
        *,
        name: str,
        command: Sequence[str],
        timeout_seconds: float,
        temp_dir: Path,
    ) -> ControllerCheckResult:
        before = self.fingerprint()
        started = time.monotonic()
        temp_dir = Path(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        env = dict(self.base_env)
        temp_value = str(temp_dir)
        env.update(
            {
                "TEMP": temp_value,
                "TMP": temp_value,
                "TMPDIR": temp_value,
                "XDG_CACHE_HOME": str(temp_dir / "cache"),
                "NPM_CONFIG_CACHE": str(temp_dir / "npm-cache"),
            }
        )
        timed_out = False
        infra_error = False
        returncode: int | None = None
        stdout = ""
        stderr = ""
        try:
            completed = subprocess.run(
                list(command),
                cwd=self.cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _to_text(exc.stdout)
            stderr = _to_text(exc.stderr)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            infra_error = True
            stderr = f"{type(exc).__name__}: {exc}"
        after = self.fingerprint()
        classification = classify_check_result(
            returncode=returncode,
            timed_out=timed_out,
            infra_error=infra_error,
            candidate_changed=before != after,
        )
        return ControllerCheckResult(
            protocol_version=CONTROLLER_CHECKS_VERSION,
            name=name,
            classification=classification,
            returncode=returncode,
            duration_seconds=max(0.0, time.monotonic() - started),
            stdout=stdout,
            stderr=stderr,
            candidate_before=before,
            candidate_after=after,
            command_identity=tuple(str(item) for item in command),
            execution_enforcement=self.execution_enforcement,
        )


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def validate_implementer_report(
    report: Mapping[str, Any],
    *,
    active_contract_items: Sequence[Mapping[str, Any]],
    require_receipt: bool = True,
) -> dict[str, Any]:
    if report.get("protocol_version") != IMPLEMENTER_PROTOCOL_VERSION:
        raise Phase4ContractError(
            f"protocol_version must be {IMPLEMENTER_PROTOCOL_VERSION!r}"
        )
    try:
        status = ImplementerStatus(str(report.get("status")))
    except ValueError as exc:
        raise Phase4ContractError("Unsupported Implementer status") from exc

    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise Phase4ContractError("Implementer summary must be non-empty")

    if status is not ImplementerStatus.COMPLETE:
        reason = report.get("reason")
        evidence = report.get("evidence")
        if not isinstance(reason, str) or not reason.strip():
            raise Phase4ContractError(f"{status.value} requires a concrete reason")
        if not isinstance(evidence, list) or not any(str(item).strip() for item in evidence):
            raise Phase4ContractError(f"{status.value} requires concrete evidence")
        return dict(report)

    evidence_rows = report.get("contract_evidence")
    if not isinstance(evidence_rows, list):
        raise Phase4ContractError("COMPLETE requires contract_evidence list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in evidence_rows:
        if not isinstance(row, Mapping):
            raise Phase4ContractError("contract_evidence rows must be objects")
        item_id = str(row.get("item_id", ""))
        if not item_id or item_id in by_id:
            raise Phase4ContractError("contract_evidence item_id must be unique and non-empty")
        by_id[item_id] = row

    expected = {str(item.get("id")): item for item in active_contract_items}
    if set(by_id) != set(expected):
        missing = sorted(set(expected) - set(by_id))
        extra = sorted(set(by_id) - set(expected))
        raise Phase4ContractError(
            "Implementer must account for every Implementation Contract item exactly once; "
            f"missing={missing}, extra={extra}"
        )
    for item_id, item in expected.items():
        row = by_id[item_id]
        raw_status = str(row.get("status"))
        if raw_status == "NOT_APPLICABLE":
            raw_status = ObligationStatus.NOT_AFFECTED.value
        try:
            obligation_status = ObligationStatus(raw_status)
        except ValueError as exc:
            raise Phase4ContractError(f"Invalid status for {item_id}") from exc
        if obligation_status is ObligationStatus.BLOCKED:
            raise Phase4ContractError(f"COMPLETE cannot leave {item_id} BLOCKED")
        allow_not_affected = bool(
            item.get("allow_not_affected", item.get("allow_not_applicable", False))
        )
        if obligation_status is ObligationStatus.NOT_AFFECTED and not allow_not_affected:
            raise Phase4ContractError(f"{item_id} cannot be NOT_AFFECTED")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not any(str(value).strip() for value in evidence):
            raise Phase4ContractError(f"{item_id} requires concrete evidence")

    self_verification = report.get("self_verification")
    if not isinstance(self_verification, Mapping):
        raise Phase4ContractError("COMPLETE requires self_verification object")
    if self_verification.get("status") != "PASS":
        raise Phase4ContractError("COMPLETE requires self_verification PASS")
    receipt_id = str(self_verification.get("receipt_id", "")).strip()
    if require_receipt and not receipt_id:
        raise Phase4ContractError("COMPLETE requires Controller-owned self-verification receipt_id")
    if not require_receipt:
        legacy_evidence = self_verification.get("evidence")
        if not isinstance(legacy_evidence, list) or not any(str(item).strip() for item in legacy_evidence):
            raise Phase4ContractError("COMPLETE requires self-verification evidence before receipt issuance")

    discoveries = report.get("discovered_obligations", [])
    if not isinstance(discoveries, list):
        raise Phase4ContractError("discovered_obligations must be a list")
    for discovery in discoveries:
        if not isinstance(discovery, Mapping):
            raise Phase4ContractError("discovered obligation must be an object")
        if discovery.get("kind") not in {"consumer", "risk"}:
            raise Phase4ContractError("discovered obligation kind must be consumer or risk")
        for field in ("name", "reason", "required_behavior", "evidence"):
            value = discovery.get(field)
            if field == "evidence":
                if not isinstance(value, list) or not any(str(item).strip() for item in value):
                    raise Phase4ContractError("discovered obligation evidence is required")
            elif not isinstance(value, str) or not value.strip():
                raise Phase4ContractError(f"discovered obligation {field} is required")
        try:
            validate_proof_target(
                discovery.get("required_proof"),
                field="discovered_obligation.required_proof",
            )
        except Exception as exc:
            raise Phase4ContractError(
                "discovered obligation requires a valid typed required_proof"
            ) from exc

    registered = report.get("registered_checks", [])
    if not isinstance(registered, list):
        raise Phase4ContractError("registered_checks must be a list")
    return dict(report)


def fingerprint_workspace_candidate(workspace: Path) -> str:
    """Stable content fingerprint for a disposable candidate worktree.

    It deliberately ignores Git metadata and Harness/runtime scratch. The canonical
    candidate.v1 identity remains authoritative where available; this helper is the
    Phase 4 freeze guard used by the deterministic runner integration.
    """

    workspace = Path(workspace)
    digest = hashlib.sha256()
    excluded_roots = {".git", ".venv", ".harness_tmp"}
    for path in sorted(workspace.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(workspace)
        if any(part in excluded_roots for part in rel.parts):
            continue
        if path.is_dir():
            continue
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"SYMLINK\0")
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        else:
            digest.update(b"FILE\0")
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return "phase4-candidate.v1:" + digest.hexdigest()


class BaselinePolicy(str, enum.Enum):
    REQUIRED_GREEN = "REQUIRED_GREEN"
    NO_REGRESSION = "NO_REGRESSION"
    EXPECTED_BROKEN = "EXPECTED_BROKEN"


@dataclasses.dataclass(frozen=True)
class BaselineEvaluation:
    policy: BaselinePolicy
    passed: bool
    reason: str
    baseline_failures: tuple[str, ...] = ()
    candidate_failures: tuple[str, ...] = ()


def evaluate_baseline_policy(
    *,
    policy: BaselinePolicy | str,
    baseline_failures: Iterable[str],
    candidate_failures: Iterable[str],
    expected_failure_marker: str | None = None,
    candidate_oracle_passed: bool | None = None,
) -> BaselineEvaluation:
    policy = BaselinePolicy(policy)
    baseline = tuple(sorted({str(value) for value in baseline_failures}))
    candidate = tuple(sorted({str(value) for value in candidate_failures}))
    if policy is BaselinePolicy.REQUIRED_GREEN:
        passed = not baseline and not candidate
        reason = "baseline and candidate are green" if passed else "required-green gate failed"
    elif policy is BaselinePolicy.NO_REGRESSION:
        added = sorted(set(candidate) - set(baseline))
        passed = not added
        reason = "candidate introduced no new typed failures" if passed else f"new failures: {added}"
    else:
        if not expected_failure_marker:
            raise Phase4ContractError("EXPECTED_BROKEN requires expected_failure_marker")
        baseline_reached = expected_failure_marker in baseline
        passed = baseline_reached and candidate_oracle_passed is True
        reason = (
            "expected broken baseline became green"
            if passed
            else "expected-broken discrimination did not complete"
        )
    return BaselineEvaluation(
        policy=policy,
        passed=passed,
        reason=reason,
        baseline_failures=baseline,
        candidate_failures=candidate,
    )


def git_changed_paths(workspace: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=Path(workspace),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise Phase4ContractError(f"Unable to inspect changed paths: {completed.stderr.strip()}")
    result: list[str] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        value = line[3:] if len(line) >= 4 else line
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        result.append(value.replace("\\", "/"))
    return tuple(sorted(set(result)))


def is_test_path(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    name = PurePosixPath(normalized).name
    return (
        "/tests/" in f"/{normalized}"
        or "/__tests__/" in f"/{normalized}"
        or name.startswith("test_") and name.endswith(".py")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def ensure_changed_tests_are_covered(
    *,
    changed_paths: Iterable[str],
    registered_references: Iterable[CheckReference],
    project_check_text: str = "",
) -> tuple[str, ...]:
    registered_paths = {
        reference.value.replace("\\", "/")
        for reference in registered_references
        if reference.kind == "path"
    }
    project_text = project_check_text.replace("\\", "/")
    missing: list[str] = []
    for path in changed_paths:
        normalized = path.replace("\\", "/")
        if not is_test_path(normalized):
            continue
        covered = normalized in registered_paths or normalized in project_text
        if not covered:
            # A project gate may intentionally cover the entire containing test directory.
            parent = str(PurePosixPath(normalized).parent)
            covered = bool(parent and parent != "." and parent in project_text)
        if not covered:
            missing.append(normalized)
    if missing:
        raise Phase4ContractError(f"UNREGISTERED_TEST_CHANGE: {sorted(missing)}")
    return tuple(sorted(set(changed_paths)))
