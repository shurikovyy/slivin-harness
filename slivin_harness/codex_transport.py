"""Controller-owned admission of Codex App Server commandExecution transport.

Only observed PowerShell envelopes are supported. Payload bytes are decoded once
from the App Server's doubled-backslash rendering, never searched fuzzily.
"""
from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
import re
from pathlib import Path

from .boundaries import boundary


TRANSPORT_SCHEMA = "codex-executed-command.v1"
CAPTURED_FORM_ORIGINS = {
    "powershell-command": "shr-q-5a12a511f1",
    "powershell-noprofile-command": "shr-q-5654234e36",
    "pwsh-command": "shr-q-42026aa1d8",
}
_ENVELOPE = re.compile(
    r'^"(?P<executable>[^"\r\n]+)" (?P<profile>-NoProfile )?-Command "(?P<payload>[^"\r\n]*)"$'
)


class CodexTransportError(RuntimeError):
    def __init__(self, reason_code: str, *, item_id: str | None = None,
                 context: str | None = None, transport_form: str | None = None,
                 layer: str = "adapter"):
        self.reason_code = reason_code
        self.item_id = item_id
        self.context = context
        self.transport_form = transport_form
        self.layer = layer
        super().__init__(reason_code + (f" context={context}" if context else "") +
                         (f" item_id={item_id}" if item_id else ""))


def _strip_long_path_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def windows_path_key(value: str) -> str:
    """Compare transport paths, not containment or authorization boundaries."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
    spelling = _strip_long_path_prefix(value.replace("/", "\\"))
    if not ntpath.isabs(spelling):
        raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
    # Existing aliases are accepted only after the host filesystem resolves
    # them. Nonexistent paths receive lexical normalization only.
    try:
        if os.name == "nt" and os.path.exists(spelling):
            spelling = _strip_long_path_prefix(os.path.realpath(spelling))
        return ntpath.normcase(ntpath.normpath(spelling))
    except (OSError, ValueError) as error:
        raise CodexTransportError("MALFORMED_TRANSPORT_EVENT") from error


def same_windows_path(left: str, right: str) -> bool:
    return windows_path_key(left) == windows_path_key(right)


def _decode_rendered_backslashes(value: str) -> str:
    if "\\" in value.replace("\\\\", ""):
        raise CodexTransportError("UNSUPPORTED_ENVELOPE", transport_form="unsupported-backslash-rendering")
    return value.replace("\\\\", "\\")


def _parse_envelope(raw: str, *, expected_shells: tuple[str, ...]) -> tuple[str, str, str]:
    if not isinstance(raw, str):
        raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
    match = _ENVELOPE.fullmatch(raw)
    if match is None:
        raise CodexTransportError("UNSUPPORTED_ENVELOPE", transport_form="unrecognized-envelope")
    executable = _decode_rendered_backslashes(match.group("executable"))
    payload = _decode_rendered_backslashes(match.group("payload"))
    if not any(same_windows_path(executable, selected) for selected in expected_shells):
        raise CodexTransportError("UNSUPPORTED_ENVELOPE", transport_form="wrong-shell-executable")
    family = "pwsh" if ntpath.basename(executable).casefold() == "pwsh.exe" else "powershell"
    form = family + ("-noprofile-command" if match.group("profile") else "-command")
    return executable, payload, form


@dataclass(frozen=True)
class CanonicalExecutedCommand:
    item_id: str
    phase: str
    shell: str
    shell_executable: str
    payload: str
    cwd: str
    exit_code: int
    output: str | None
    output_observed: bool
    output_sources: tuple[str, ...]
    completion_state: str
    transport_form: str
    aggregated_output_field: str


class CodexTransportAdapter:
    """Bind ordered deltas by App Server itemId before publishing completion."""

    def __init__(self, *, expected_shells: tuple[str, ...] | None = None):
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        system_shell = ntpath.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if expected_shells is None:
            selected = [system_shell]
            profile = os.environ.get("USERPROFILE")
            if profile:
                bundled = Path(profile) / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe"
                if bundled.is_file():
                    selected.append(str(bundled))
            expected_shells = tuple(selected)
        self.expected_shells = expected_shells
        self.commands: list[CanonicalExecutedCommand] = []
        self._deltas: dict[str, tuple[str, str, str, list[str]]] = {}
        self._completed_ids: set[str] = set()

    @boundary("B20")
    def observe_delta(self, params: dict, *, phase: str) -> None:
        if not isinstance(params, dict):
            raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
        item_id, thread_id, turn_id, delta = (
            params.get("itemId"), params.get("threadId"), params.get("turnId"), params.get("delta")
        )
        if not all(isinstance(value, str) and value for value in (item_id, thread_id, turn_id)) or not isinstance(delta, str):
            raise CodexTransportError("MALFORMED_TRANSPORT_EVENT", item_id=item_id if isinstance(item_id, str) else None)
        if item_id in self._completed_ids:
            raise CodexTransportError("TRANSPORT_EVIDENCE_INTEGRITY_FAILURE", item_id=item_id)
        prior = self._deltas.get(item_id)
        if prior is None:
            self._deltas[item_id] = (phase, thread_id, turn_id, [delta])
        elif prior[:3] != (phase, thread_id, turn_id):
            raise CodexTransportError("COMMAND_IDENTITY_MISMATCH", item_id=item_id)
        else:
            prior[3].append(delta)

    @boundary("B20")
    def observe_completed(self, item: dict, *, phase: str,
                          thread_id: str | None = None, turn_id: str | None = None) -> CanonicalExecutedCommand:
        if not isinstance(item, dict) or item.get("type") != "commandExecution":
            raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
        item_id, raw, cwd, exit_code = (
            item.get("id"), item.get("command"), item.get("cwd"), item.get("exitCode")
        )
        if not isinstance(item_id, str) or not item_id or not isinstance(cwd, str) or (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)
        ):
            raise CodexTransportError("MALFORMED_TRANSPORT_EVENT")
        if any(value is not None and (not isinstance(value, str) or not value)
               for value in (thread_id, turn_id)):
            raise CodexTransportError("MALFORMED_TRANSPORT_EVENT", item_id=item_id)
        if item_id in self._completed_ids:
            raise CodexTransportError("DUPLICATE_EXECUTION", item_id=item_id)
        shell_executable, payload, form = _parse_envelope(raw, expected_shells=self.expected_shells)
        canonical_cwd = windows_path_key(cwd)
        aggregated = item.get("aggregatedOutput")
        if aggregated is not None and not isinstance(aggregated, str):
            raise CodexTransportError("MALFORMED_TRANSPORT_EVENT", item_id=item_id)
        prior = self._deltas.pop(item_id, None)
        if prior is not None and (prior[0] != phase or
            (thread_id is not None and prior[1] != thread_id) or
            (turn_id is not None and prior[2] != turn_id)):
            raise CodexTransportError("COMMAND_IDENTITY_MISMATCH", item_id=item_id)
        delta_output = "".join(prior[3]) if prior is not None else None
        if aggregated is not None and delta_output is not None and aggregated != delta_output:
            raise CodexTransportError("TRANSPORT_EVIDENCE_INTEGRITY_FAILURE", item_id=item_id)
        output = aggregated if aggregated is not None else delta_output
        sources = tuple(source for source, present in (
            ("aggregated", aggregated is not None), ("delta", delta_output is not None)
        ) if present)
        record = CanonicalExecutedCommand(
            item_id=item_id, phase=phase, shell="powershell", shell_executable=shell_executable,
            payload=payload, cwd=canonical_cwd, exit_code=exit_code, output=output,
            output_observed=output is not None, output_sources=sources,
            completion_state="completed", transport_form=form,
            aggregated_output_field=("missing" if "aggregatedOutput" not in item else
                                     "null" if aggregated is None else "present"),
        )
        self._completed_ids.add(item_id)
        self.commands.append(record)
        return record

    def assert_phase_complete(self, phase: str) -> None:
        if any(value[0] == phase for value in self._deltas.values()):
            raise CodexTransportError("TRANSPORT_EVIDENCE_INTEGRITY_FAILURE")


def require_output(record: CanonicalExecutedCommand) -> str:
    if not record.output_observed or record.output is None:
        raise CodexTransportError("OUTPUT_UNAVAILABLE", item_id=record.item_id,
            layer="consumer")
    return record.output
