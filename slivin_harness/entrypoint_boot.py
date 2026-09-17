"""Side-effect-free machine-readable boot contract for release executables."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .entrypoint_identity import (
    HARNESS_SOURCE_ROOT,
    canonical_module_from_source,
    canonical_source_path,
    runtime_boundary_entrypoint,
)


ENTRYPOINT_BOOT_SCHEMA = "release-entrypoint-boot.v1"


def emit_entrypoint_boot_if_requested(
    argv: Sequence[str],
    *,
    source_file: str | Path,
    boundary_functions: Iterable[Callable] = (),
) -> bool:
    """Emit one boot record only for the exact explicit ``--boot-check`` mode."""
    if tuple(argv) != ("--boot-check",):
        return False
    source = canonical_source_path(source_file)
    boundaries = []
    for function in boundary_functions:
        recorded = getattr(function, "__boundary_entrypoint__", None)
        actual = runtime_boundary_entrypoint(inspect.unwrap(function))
        if not isinstance(recorded, str) or recorded != actual:
            raise RuntimeError("ENTRYPOINT_BOOT_BOUNDARY_IDENTITY_MISMATCH")
        boundaries.append(recorded)
    record = {
        "schema_version": ENTRYPOINT_BOOT_SCHEMA,
        "status": "PASS",
        "source": source.relative_to(HARNESS_SOURCE_ROOT).as_posix(),
        "module": canonical_module_from_source(source),
        "boundary_entrypoints": sorted(boundaries),
        "model_execution": "NOT_RUN",
    }
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
    return True
