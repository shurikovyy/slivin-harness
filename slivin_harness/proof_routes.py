"""Planner assessment of proof routes; immutable product claims stay in Contract.

Only Controller applies these typed revisions. They cannot edit owner gates,
source claims, candidate files, checks or runtime configuration.
"""
from __future__ import annotations

from slivin_harness.boundaries import boundary

import copy
import json
from pathlib import Path

from .execution import ExecutionRole
from .protocol import ensure_exact_keys, require_string_list, stable_fingerprint
from .verification import PROOF_TARGET_SCHEMA, merged_required_proof, validate_proof_target, validate_merged_required_proof

PROOF_ROUTE_VERSION = "proof-route-review.v1"
_CHANGE = {"type": "object", "additionalProperties": False, "properties": {
    "item_id": {"type": "string"}, "reason": {"type": "string"},
    "evidence": {"type": "array", "items": {"type": "string"}},
    "proofs": {"type": "array", "items": PROOF_TARGET_SCHEMA},
}, "required": ["item_id", "reason", "evidence", "proofs"]}
PROOF_ROUTE_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "protocol_version": {"type": "string", "enum": [PROOF_ROUTE_VERSION]},
    "candidate_id": {"type": "string"}, "contract_fingerprint": {"type": "string"},
    "status": {"type": "string", "enum": ["READY", "BLOCKED", "TECHNICAL_REPLAN_REQUIRED"]},
    "reason": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}},
    "changes": {"type": "array", "items": _CHANGE},
}, "required": ["protocol_version", "candidate_id", "contract_fingerprint", "status", "reason", "evidence", "changes"]}


def effective_proofs(contract: dict) -> dict:
    current = {item["id"]: copy.deepcopy(item["required_proof"]) for item in contract["items"]}
    history = contract["proof_routes"]
    if not isinstance(history, list):
        raise RuntimeError("PROOF_ROUTE_HISTORY_INVALID")
    for route in history:
        fields = {"item_id", "previous_proof_fingerprint", "required_proof", "reason", "evidence", "review_fingerprint"}
        ensure_exact_keys(route, allowed=fields, required=fields, field="proof_routes")
        item_id = route["item_id"]
        if item_id not in current or route["previous_proof_fingerprint"] != stable_fingerprint(current[item_id], length=64):
            raise RuntimeError("PROOF_ROUTE_HISTORY_STALE")
        validate_merged_required_proof(route["required_proof"], field="proof_routes.required_proof")
        if not isinstance(route["reason"], str) or not route["reason"].strip() or not require_string_list(route["evidence"], field="proof_routes.evidence"):
            raise RuntimeError("PROOF_ROUTE_EVIDENCE_MISSING")
        current[item_id] = copy.deepcopy(route["required_proof"])
    return current


def validate_proof_review(contract: dict, review: dict, *, candidate_id: str) -> None:
    fields = PROOF_ROUTE_SCHEMA["properties"]
    ensure_exact_keys(review, allowed=fields, required=fields, field="proof_route_review")
    if review["protocol_version"] != PROOF_ROUTE_VERSION or review["candidate_id"] != candidate_id or review["contract_fingerprint"] != contract["fingerprint"]:
        raise RuntimeError("PROOF_ROUTE_REVIEW_STALE")
    if review["status"] not in PROOF_ROUTE_SCHEMA["properties"]["status"]["enum"]:
        raise RuntimeError("PROOF_ROUTE_STATUS_INVALID")
    if not isinstance(review["reason"], str) or not review["reason"].strip() or not require_string_list(review["evidence"], field="proof_route_review.evidence"):
        raise RuntimeError("PROOF_ROUTE_EVIDENCE_MISSING")
    if not isinstance(review["changes"], list):
        raise RuntimeError("PROOF_ROUTE_CHANGES_INVALID")
    if review["status"] != "READY":
        if review["changes"]:
            raise RuntimeError("PROOF_ROUTE_NON_READY_CHANGES")
        return
    if not review["changes"]:
        raise RuntimeError("PROOF_ROUTE_NO_PROGRESS")


@boundary("B18")
def apply_proof_review(contract: dict, review: dict, *, candidate_id: str) -> dict:
    validate_proof_review(contract, review, candidate_id=candidate_id)
    if review["status"] != "READY":
        raise RuntimeError("PROOF_ROUTE_REVIEW_NOT_READY")
    result = copy.deepcopy(contract)
    current = effective_proofs(contract)
    seen = set()
    for change in review["changes"]:
        fields = _CHANGE["properties"]
        ensure_exact_keys(change, allowed=fields, required=fields, field="proof_route_review.changes")
        item_id = change["item_id"]
        if item_id not in current or item_id in seen:
            raise RuntimeError("PROOF_ROUTE_REFERENCE_INVALID")
        seen.add(item_id)
        if not isinstance(change["proofs"], list) or not change["proofs"]:
            raise RuntimeError("PROOF_ROUTE_EMPTY")
        for proof in change["proofs"]:
            validate_proof_target(proof, field="proof_route_review.proofs")
        proof = merged_required_proof(change["proofs"], fallback_claim="")
        if proof == current[item_id]:
            raise RuntimeError("PROOF_ROUTE_NO_PROGRESS")
        result["proof_routes"].append(dict(item_id=item_id, previous_proof_fingerprint=stable_fingerprint(current[item_id], length=64),
            required_proof=proof, reason=change["reason"], evidence=copy.deepcopy(change["evidence"]),
            review_fingerprint=stable_fingerprint(review, length=64)))
    effective_proofs(result)
    result["fingerprint"] = stable_fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def run_proof_route_review(codex, *, workspace: Path, contract: dict, candidate_id: str,
                           reason: str, task_contract: dict, owner_checks: list, available_capabilities: list,
                           timeout: float, on_thread_started=None, on_heartbeat=None) -> dict:
    thread = codex.start_thread(cwd=workspace, execution_role=ExecutionRole.PLANNER,
        developer_instructions="Independently review a proof route on the preserved candidate. Read-only project, scoped writable scratch. Return only the required proof-route-review artifact.",
        on_started=on_thread_started)
    prompt = """Independently investigate the reported proof-route failure using the repository and actual assertions.
READY explicitly confirms unchanged product requirements/model; propose only replacement proof routes by item_id.
Prove the same required behavior. A baseline failure is not automatically unrelated: investigate its semantic scope.
Owner checks remain mandatory, including red checks. Do not drop obligations, weaken assertions, edit project files,
reclassify unresolved in-scope defects, or invent capabilities. Return BLOCKED if honest proof is unavailable.
Return TECHNICAL_REPLAN_REQUIRED only for an independently established change in the technical product model.
Source records are prior claims, not current proof. Do not repeat inherited product prose in changes.
""" + json.dumps(dict(candidate_id=candidate_id, contract=contract, task_contract=task_contract,
                        owner_checks=owner_checks, available_capabilities=available_capabilities, reported_reason=reason),
                      ensure_ascii=False, indent=2)
    raw = codex.run_turn(thread_id=thread, prompt=prompt, output_schema=PROOF_ROUTE_SCHEMA,
                         timeout=timeout, on_heartbeat=on_heartbeat)
    return json.loads(raw)
