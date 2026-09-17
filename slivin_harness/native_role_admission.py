"""Controller-owned native validation-command admission and bounded recovery."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .boundaries import boundary
from .codex_transport import CanonicalExecutedCommand, same_windows_path


NATIVE_ROLE_COMMAND_ADMISSION_SCHEMA = "native-role-command-admission.v1"
NATIVE_COMMAND_CORRECTION_BUDGET = 1


class NativeRoleEvidenceError(RuntimeError):
    """Typed native acceptance failure after canonical transport admission."""

    def __init__(self, category: str, *, context: str, detail: str,
                 item_id: str | None = None, observed_payload: str | None = None,
                 recovery_evidence: dict | None = None, correctable: bool = True,
                 correction_commands: tuple[str, ...] = (),
                 discard_item_ids: tuple[str, ...] = ()):
        self.category = category
        self.reason_code = category
        self.context = context
        self.detail = detail
        self.item_id = item_id
        self.observed_payload = observed_payload
        self.recovery_evidence = recovery_evidence
        self.correctable = correctable
        self.correction_commands = correction_commands
        self.discard_item_ids = discard_item_ids
        super().__init__(f"{category} context={context} detail={detail}")

    def to_dict(self) -> dict:
        value = {"category": self.category, "context": self.context, "detail": self.detail}
        if self.item_id:
            value["item_id"] = self.item_id
        if self.observed_payload is not None:
            value["observed_payload"] = self.observed_payload
        if self.recovery_evidence is not None:
            value["recovery_evidence"] = self.recovery_evidence
        value["correctable"] = self.correctable
        if self.correction_commands:
            value["correction_commands"] = list(self.correction_commands)
        if self.discard_item_ids:
            value["discard_item_ids"] = list(self.discard_item_ids)
        return value


@dataclass(frozen=True)
class NativeCorrectionTurn:
    thread_id: str
    commands: tuple[CanonicalExecutedCommand, ...]


@boundary("B21")
def admit_native_phase_with_recovery(
    commands: list[CanonicalExecutedCommand], *, thread_id: str, project: Path,
    validate: Callable[[list[CanonicalExecutedCommand]], dict],
    correction: Callable[[str, str], NativeCorrectionTurn],
) -> dict:
    """Admit a phase, allowing one same-thread validation-command retry only."""
    original_error: NativeRoleEvidenceError | None = None
    try:
        result = validate(commands)
        result["role_command_recovery"] = {
            "schema_version": NATIVE_ROLE_COMMAND_ADMISSION_SCHEMA,
            "status": "NOT_REQUIRED", "attempts": 0,
        }
        return result
    except NativeRoleEvidenceError as error:
        if (error.category != "ROLE_COMMAND_DRIFT" or not error.correctable or
                not error.correction_commands):
            raise
        original_error = error
    assert original_error is not None

    prompt = (
        "CONTROLLER ROLE-COMMAND CORRECTION. The previous validation command identity "
        "did not match the Controller request. Execute exactly the following failed "
        "validation command(s), separately and from the project cwd; execute no other "
        "command:\n" + "\n".join(original_error.correction_commands) + "\n"
        "Do not restate or alter any Controller-owned configuration. "
        "Return only a brief acknowledgement after the command."
    )
    turn = correction(thread_id, prompt)
    recovery = {
        "schema_version": NATIVE_ROLE_COMMAND_ADMISSION_SCHEMA,
        "status": "ATTEMPTED",
        "attempts": NATIVE_COMMAND_CORRECTION_BUDGET,
        "thread_id": thread_id,
        "original": original_error.to_dict(),
        "expected_commands": list(original_error.correction_commands),
        "correction_command_count": len(turn.commands),
    }
    if turn.thread_id != thread_id:
        raise NativeRoleEvidenceError("INTEGRITY_FAILURE", context="role-command-recovery",
            detail="Correction escaped the original role thread", recovery_evidence=recovery)
    if len(turn.commands) != len(original_error.correction_commands) or any(
        row.shell != "powershell" or row.payload != expected or
        not same_windows_path(row.cwd, str(project))
        for row, expected in zip(turn.commands, original_error.correction_commands)
    ):
        raise NativeRoleEvidenceError("ROLE_COMMAND_DRIFT", context=original_error.context,
            detail="Correction must execute only the exact failed validation command(s)",
            recovery_evidence={**recovery, "status": "EXHAUSTED"})
    admitted = [row for row in commands if row.item_id not in original_error.discard_item_ids]
    admitted.extend(turn.commands)
    try:
        result = validate(admitted)
    except NativeRoleEvidenceError as corrected:
        if corrected.category == "ROLE_COMMAND_DRIFT":
            raise NativeRoleEvidenceError("ROLE_COMMAND_DRIFT", context=corrected.context,
                detail="Repeated role command drift exhausted correction budget",
                item_id=corrected.item_id, observed_payload=corrected.observed_payload,
                recovery_evidence={**recovery, "status": "EXHAUSTED"}) from corrected
        raise
    result["role_command_recovery"] = {
        **recovery,
        "status": "RECOVERED",
        "correction_item_ids": [row.item_id for row in turn.commands],
    }
    return result
