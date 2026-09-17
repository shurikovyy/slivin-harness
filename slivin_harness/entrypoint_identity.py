"""Canonical executable identities derived from Harness-owned source files."""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Callable


HARNESS_SOURCE_ROOT = Path(__file__).resolve().parents[1]


class EntrypointIdentityError(ValueError):
    """A source file or qualname cannot form a trusted Harness entrypoint."""


def canonical_source_path(source_file: str | Path, *, root: Path = HARNESS_SOURCE_ROOT) -> Path:
    """Return an existing Python source path proven to be inside ``root``."""
    try:
        resolved_root = root.resolve(strict=True)
        resolved = Path(source_file).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise EntrypointIdentityError("Boundary source file cannot be resolved") from error
    if not resolved.is_file() or resolved.suffix.lower() != ".py":
        raise EntrypointIdentityError("Boundary source is not a Python file")
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise EntrypointIdentityError("Boundary source file escapes the Harness source tree") from error
    return resolved


def canonical_module_from_source(source_file: str | Path, *, root: Path = HARNESS_SOURCE_ROOT) -> str:
    """Map one trusted source file to its import-independent dotted module name."""
    resolved_root = root.resolve(strict=True)
    resolved = canonical_source_path(source_file, root=resolved_root)
    relative = resolved.relative_to(resolved_root)
    parts = list(relative.parts)
    if parts[-1].lower() == "__init__.py":
        parts.pop()
    else:
        parts[-1] = Path(parts[-1]).stem
    if not parts or any(not part.isidentifier() for part in parts):
        raise EntrypointIdentityError("Boundary source path is not a canonical Python module")
    return ".".join(parts)


def normalize_entrypoint_qualname(qualname: str) -> str:
    """Normalize lexical closures without accepting arbitrary pseudo-components."""
    if not isinstance(qualname, str):
        raise EntrypointIdentityError("Boundary qualname is not text")
    parts = [part for part in qualname.split(".") if part != "<locals>"]
    if not parts or any(not part.isidentifier() for part in parts):
        raise EntrypointIdentityError("Boundary qualname is not canonical")
    return ".".join(parts)


def canonical_boundary_entrypoint(
    source_file: str | Path,
    qualname: str,
    *,
    root: Path = HARNESS_SOURCE_ROOT,
) -> str:
    """Build the sole Controller-owned source-file + qualname identity."""
    return canonical_module_from_source(source_file, root=root) + "." + normalize_entrypoint_qualname(qualname)


def runtime_boundary_entrypoint(function: Callable) -> str:
    """Resolve a runtime function without trusting ``function.__module__``."""
    original = inspect.unwrap(function)
    try:
        source_file = inspect.getsourcefile(original)
    except (OSError, TypeError) as error:
        raise EntrypointIdentityError("Boundary function source is unavailable") from error
    if not source_file:
        raise EntrypointIdentityError("Boundary function source is unavailable")
    return canonical_boundary_entrypoint(source_file, original.__qualname__)
