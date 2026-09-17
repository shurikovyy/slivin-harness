"""Negative fixture: __main__ must not alias to task_runner."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from slivin_harness.boundaries import boundary


@boundary("B21")
def admit_native_phase_with_recovery():
    raise AssertionError("decorator admission must fail before execution")
