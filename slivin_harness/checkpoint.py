"""Private, reproducible candidate checkpoints; not acceptance or process resume."""
from __future__ import annotations

from slivin_harness.boundaries import boundary

import hashlib
import os
import json
import re
from pathlib import Path
from typing import Any

from . import __version__
from .control_plane import ArtifactVisibility, ControllerPlane, is_within
from .impact import safe_impact_path
from .run_state import build_candidate_identity
from .build_identity import source_manifest
from .execution import EXECUTION_BROKER_VERSION, ROLE_EXECUTION_CONTEXT_VERSION
from .control_plane import CONTROL_PLANE_VERSION, SELF_VERIFY_RECEIPT_VERSION, safe_artifact_name

CHECKPOINT_VERSION = "candidate-checkpoint.v1"

# Controller-owned artifacts only. Never enumerate/copy the receipt key, local
# configuration or environment. Role caches/runtime copies are reproducible
# inputs, while authored proof scripts/results are sealed before scratch reset.
_CONTROLLER_EVIDENCE = re.compile(r"(?:check_registry|verification_plan(?:_\d+)?|execution_policy|project_runtime(?:_replan)?_\d+|runtime_evidence_\d+|contract_closure_\d+|planner_tool_evidence_\d+|self_verify_receipt.*)\.json\Z")
_SCRATCH_EXCLUDES = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "cache", "npm", "npm-cache", "jest-cache"}
_JEST_CACHE_FILE = re.compile(r"(?:haste-map|perf-cache)-[0-9a-f-]+\Z")
_SENSITIVE_NAMES = {".env", ".receipt_key", "credentials", "credentials.json", "auth.json", "config.toml", "id_rsa", "id_ed25519"}


def seal_evidence(*, plane: ControllerPlane, workspace: Path) -> tuple[list[dict], list[dict]]:
    evidence, excluded = [], []
    def seal(path: Path, kind: str, locator: str):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise RuntimeError("CHECKPOINT_EVIDENCE_LINK")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        artifact = f"checkpoints/evidence/{digest}"
        plane.write_bytes(artifact, raw, visibility=ArtifactVisibility.PRIVATE)
        evidence.append(dict(kind=kind, original_locator=locator, artifact=artifact, sha256=digest, size=len(raw)))
    for path in sorted(plane.private_root.glob("*.json")):
        if _CONTROLLER_EVIDENCE.fullmatch(path.name):
            seal(path, "CONTROLLER_EVIDENCE", path.name)
    for role in ("planner", "implementer", "evaluator"):
        root = workspace / ".harness_tmp" / role
        if not root.exists():
            continue
        if not is_within(workspace / ".harness_tmp", root) or root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
            raise RuntimeError("CHECKPOINT_SCRATCH_BOUNDARY")
        for parent, directories, filenames in os.walk(root, followlinks=False):
            for name in list(directories):
                path = Path(parent) / name
                if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                    raise RuntimeError("CHECKPOINT_EVIDENCE_LINK")
                if name in _SCRATCH_EXCLUDES:
                    directories.remove(name)
                    excluded.append(dict(locator=path.relative_to(workspace).as_posix(), reason="REPRODUCIBLE_CACHE_OR_RUNTIME"))
            for name in sorted(filenames):
                path = Path(parent) / name
                locator = path.relative_to(workspace).as_posix()
                if Path(parent).name == "jest" and _JEST_CACHE_FILE.fullmatch(name):
                    excluded.append(dict(locator=locator, reason="REPRODUCIBLE_CACHE_OR_RUNTIME"))
                    continue
                if name in _SENSITIVE_NAMES or name.startswith(".env.") or path.suffix.lower() in {".key", ".pem", ".pfx", ".p12"}:
                    excluded.append(dict(locator=locator, reason="SENSITIVE_MATERIAL_NOT_READ"))
                    continue
                seal(path, "UNTRUSTED_ROLE_SCRATCH", locator)
    return evidence, excluded


def verify_checkpoint_evidence(plane: ControllerPlane, checkpoint: dict) -> None:
    """Forensic integrity verification only; this never issues a fresh receipt."""
    for entry in [*checkpoint["evidence"], *[row for row in checkpoint["entries"] if "blob" in row]]:
        name = safe_artifact_name(entry.get("artifact", entry.get("blob")))
        if not name.startswith("checkpoints/"):
            raise RuntimeError("CHECKPOINT_EVIDENCE_BOUNDARY")
        path = plane.path_for(name, ArtifactVisibility.PRIVATE)
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("CHECKPOINT_EVIDENCE_MISSING_OR_CHANGED")


@boundary("B17")
def save_report_checkpoint(*, plane: ControllerPlane, workspace: Path, name: str,
                           contract: dict, run_state=None, stamp_path: Path | None = None,
                           check_registry_digest: str | None = None) -> dict:
    """Seal changed physical bytes and known evidence before report validation.

    Baseline SHA plus file blobs/deletions reconstruct the observed candidate.
    Blobs use digest filenames, never agent-supplied output paths. Runtime and
    credentials excluded by the Controller candidate baseline stay excluded.
    A second observation detects concurrent writes. Failure is not PASS.
    """
    before = build_candidate_identity(workspace)
    entries = []
    for item in before.entries:
        entry = dict(item)
        relative = safe_impact_path(item["path"], field="checkpoint.path")
        path = workspace / relative
        if not is_within(workspace, path.parent):
            raise RuntimeError("CHECKPOINT_PATH_ESCAPE")
        if item["state"] in {"file", "symlink"}:
            raw = (os.readlink(path).encode("utf-8", errors="surrogateescape")
                   if item["state"] == "symlink" else path.read_bytes())
            digest = hashlib.sha256(raw).hexdigest()
            if digest != item["sha256"]:
                raise RuntimeError("CHECKPOINT_CANDIDATE_CHANGED")
            blob = f"checkpoints/blobs/{digest}"
            destination = plane.path_for(blob, ArtifactVisibility.PRIVATE)
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise RuntimeError("CHECKPOINT_BLOB_CONFLICT")
            else:
                plane.write_bytes(blob, raw, visibility=ArtifactVisibility.PRIVATE)
            entry["blob"] = blob
        elif item["state"] != "deleted":
            raise RuntimeError("CHECKPOINT_UNSUPPORTED_ENTRY")
        entries.append(entry)
    evidence, exclusions = seal_evidence(plane=plane, workspace=workspace)
    contract_raw = json.dumps(contract, ensure_ascii=False, sort_keys=True).encode("utf-8")
    contract_digest = hashlib.sha256(contract_raw).hexdigest()
    contract_artifact = f"checkpoints/evidence/{contract_digest}"
    plane.write_bytes(contract_artifact, contract_raw, visibility=ArtifactVisibility.PRIVATE)
    evidence.append(dict(kind="ACTIVE_IMPLEMENTATION_CONTRACT", artifact=contract_artifact, sha256=contract_digest))
    if stamp_path is not None and stamp_path.exists():
        # Only the Controller-generated stamp locator is accepted here. No
        # arbitrary report evidence string is interpreted as a filesystem path.
        if not is_within(workspace / ".harness_tmp", stamp_path) or not stamp_path.is_file():
            raise RuntimeError("CHECKPOINT_STAMP_BOUNDARY")
        raw = stamp_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        artifact = f"checkpoints/evidence/{digest}.json"
        plane.write_bytes(artifact, raw, visibility=ArtifactVisibility.PRIVATE)
        evidence.append(dict(kind="UNTRUSTED_SELF_VERIFY_STAMP", artifact=artifact, sha256=digest))
    after = build_candidate_identity(workspace)
    if before != after:
        raise RuntimeError("CHECKPOINT_CANDIDATE_CHANGED")
    checkpoint = dict(schema_version=CHECKPOINT_VERSION, status="SAVED_UNVERIFIED",
                      harness_version=__version__, candidate=before.to_dict(), entries=entries,
                      protocol_versions=dict(controller=CONTROL_PLANE_VERSION, receipt=SELF_VERIFY_RECEIPT_VERSION,
                          execution=EXECUTION_BROKER_VERSION, role_context=ROLE_EXECUTION_CONTEXT_VERSION),
                      harness_source=source_manifest(Path(__file__).resolve().parents[1]),
                      contract_fingerprint=contract["fingerprint"],
                      source_inventory=contract["source_inventory"],
                      revision_binding=run_state.verification_binding(candidate_id=before.candidate_id,
                          check_registry_digest=check_registry_digest) if run_state else None,
                      evidence=evidence, evidence_exclusions=exclusions, cross_process_resume=False)
    verify_checkpoint_evidence(plane, checkpoint)
    plane.write_private_json(f"checkpoints/{name}.json", checkpoint)
    return checkpoint
