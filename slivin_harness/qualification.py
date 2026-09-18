"""Controller-owned model qualification profiles and identity."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence


REAL_MODEL_CASES = ("expiry-1", "suspension-1", "expiry-2")
MODEL_REASONING_EFFORTS = frozenset({
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
})
_MODEL_NAME = re.compile(r"[a-z0-9][a-z0-9.-]*\Z")


@dataclass(frozen=True)
class QualificationProfile:
    mode: str
    model: str
    model_reasoning_effort: str
    real_model_cases: tuple[str, ...]
    fail_fast: bool
    release_qualifying: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "qualification_mode": self.mode,
            "selected_model": self.model,
            "selected_reasoning_effort": self.model_reasoning_effort,
            "real_model_cases_requested": list(self.real_model_cases),
            "fail_fast": self.fail_fast,
            "release_qualifying": self.release_qualifying,
        }


QUALIFICATION_PROFILES = {
    "dev": QualificationProfile(
        mode="dev",
        model="gpt-5.6-terra",
        model_reasoning_effort="medium",
        real_model_cases=("expiry-1",),
        fail_fast=True,
        release_qualifying=False,
    ),
    "release": QualificationProfile(
        mode="release",
        model="gpt-5.6-sol",
        model_reasoning_effort="high",
        real_model_cases=REAL_MODEL_CASES,
        fail_fast=False,
        release_qualifying=True,
    ),
}


def qualification_profile(mode: str) -> QualificationProfile:
    try:
        return QUALIFICATION_PROFILES[mode]
    except KeyError as exc:
        raise ValueError(f"Unknown qualification mode: {mode}") from exc


def validate_model_identity(model: str, effort: str) -> None:
    if not isinstance(model, str) or not _MODEL_NAME.fullmatch(model):
        raise ValueError("Qualification model must be a canonical model identifier")
    if effort not in MODEL_REASONING_EFFORTS:
        raise ValueError("Qualification reasoning effort is unsupported")


def validate_real_model_selection(
    *, mode: str, model: str, effort: str, cases: Sequence[str], fail_fast: bool,
) -> bool:
    """Validate direct-tool selection and return whether it is release-qualifying."""
    validate_model_identity(model, effort)
    selected = tuple(cases)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Real-model cases must be a non-empty unique ordered selection")
    if any(case not in REAL_MODEL_CASES for case in selected):
        raise ValueError("Unknown real-model qualification case")
    if mode in QUALIFICATION_PROFILES:
        profile = qualification_profile(mode)
        if (
            selected != profile.real_model_cases
            or model != profile.model
            or effort != profile.model_reasoning_effort
            or bool(fail_fast) is not profile.fail_fast
        ):
            raise ValueError(f"{mode} qualification selection does not match its Controller profile")
        return profile.release_qualifying
    if mode != "focused":
        raise ValueError(f"Unknown real-model qualification mode: {mode}")
    return False


def codex_config_overrides(model: str, effort: str) -> tuple[str, ...]:
    """Exact TOML CLI overrides; ambient user config cannot replace them."""
    validate_model_identity(model, effort)
    return (
        f'model="{model}"',
        f'model_reasoning_effort="{effort}"',
    )
