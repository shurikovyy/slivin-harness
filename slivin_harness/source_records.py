"""Immutable Controller-carried claims and explicit current-role assessments.

An origin is a prior claim, never proof. References bind the source record's
revision; adding another record does not make existing references stale.
"""
from __future__ import annotations

from slivin_harness.boundaries import boundary

import copy
import re
from typing import Any, Mapping

from .protocol import ArtifactContractError, ensure_exact_keys, stable_fingerprint

SOURCE_INVENTORY_VERSION = "impact-sources.v1"
SOURCE_GROUPS = ("changed_contracts", "in_scope_consumers", "not_affected_consumers",
                 "related_out_of_scope", "new_risks")
SOURCE_REF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"source_id": {"type": "string"}, "source_revision": {"type": "string"}},
    "required": ["source_id", "source_revision"],
}


def source_error(code: str, message: str, actual: object = None) -> None:
    raise ArtifactContractError(code=code, field="source_assessments", message=message,
                                expected="Every current origin explicitly assessed by exact reference", actual=actual)


def _record(*, source_id: str, author: str, group: str, claim: Mapping[str, Any],
            source_artifact: str) -> dict:
    value = dict(source_id=source_id, author=author, group=group,
                 source_artifact=source_artifact, claim=copy.deepcopy(dict(claim)))
    value["source_revision"] = stable_fingerprint(value, length=64)
    return value


def build_source_inventory(plan: dict | None, *, task_fingerprint: str) -> dict:
    plan_revision = stable_fingerprint(plan, length=64) if plan is not None else "FAST"
    namespace = stable_fingerprint([task_fingerprint, plan_revision], length=24)
    records = []
    for group in SOURCE_GROUPS:
        for index, claim in enumerate((plan or {}).get("impact_closure", {}).get(group, []), 1):
            records.append(_record(source_id=f"P-{namespace}-{group}-{index}",
                           author="PLANNER", group=group, claim=claim, source_artifact=plan_revision))
    return dict(schema_version=SOURCE_INVENTORY_VERSION, namespace=namespace, records=records, transitions=[])


def source_ref(record: Mapping[str, Any]) -> dict[str, str]:
    return {key: record[key] for key in ("source_id", "source_revision")}


def validate_source_inventory(inventory: object) -> None:
    if not isinstance(inventory, dict):
        source_error("SOURCE_INVENTORY_INVALID", "Source inventory must be a Controller object")
    ensure_exact_keys(inventory, allowed={"schema_version", "namespace", "records", "transitions"},
                      required={"schema_version", "namespace", "records", "transitions"}, field="source_inventory")
    if inventory["schema_version"] != SOURCE_INVENTORY_VERSION or not isinstance(inventory["records"], list):
        source_error("SOURCE_INVENTORY_INVALID", "Unsupported inventory schema or malformed records")
    ids = set()
    for record in inventory["records"]:
        if not isinstance(record, dict):
            source_error("SOURCE_INVENTORY_INVALID", "Malformed source record")
        keys = {"source_id", "source_revision", "author", "group", "source_artifact", "claim"}
        ensure_exact_keys(record, allowed=keys, required=keys, field="source_inventory.records")
        if record["group"] not in SOURCE_GROUPS or record["author"] not in {"PLANNER", "IMPLEMENTER", "EVALUATOR"}:
            source_error("SOURCE_INVENTORY_INVALID", "Invalid source ownership")
        if not isinstance(record["source_id"], str) or record["source_id"] in ids:
            source_error("SOURCE_ID_DUPLICATE", "Source IDs must be unique")
        ids.add(record["source_id"])
        if record["source_revision"] != stable_fingerprint(
                {key: value for key, value in record.items() if key != "source_revision"}, length=64):
            source_error("SOURCE_REVISION_STALE", "Source record has changed")
    if not isinstance(inventory["namespace"], str) or not re.fullmatch(r"[0-9a-f]{24}", inventory["namespace"]):
        source_error("SOURCE_INVENTORY_INVALID", "Invalid Controller source namespace")
    if not isinstance(inventory["transitions"], list):
        source_error("SOURCE_INVENTORY_INVALID", "Invalid source transitions")
    records = {record["source_id"]: record for record in inventory["records"]}
    promoted = set()
    for event in inventory["transitions"]:
        if not isinstance(event, dict) or set(event) != {"source_ref", "target_ref", "disposition", "observation", "evidence"}:
            source_error("SOURCE_TRANSITION_INVALID", "Malformed source transition")
        source, target = event["source_ref"], event["target_ref"]
        if not isinstance(source, dict) or not isinstance(target, dict):
            source_error("SOURCE_TRANSITION_INVALID", "Malformed transition references")
        left, right = records.get(source.get("source_id")), records.get(target.get("source_id"))
        if (left is None or right is None or source != source_ref(left) or target != source_ref(right)
                or event["disposition"] != "PROMOTE" or left["group"] not in {"not_affected_consumers", "related_out_of_scope"}
                or right["group"] != "in_scope_consumers" or left["source_id"] in promoted):
            source_error("SOURCE_TRANSITION_INVALID", "Promotion must retain valid unique source and target references")
        promoted.add(left["source_id"])


@boundary("B19")
def resolve_assessments(inventory: dict, assessments: object, *, complete: bool) -> list[tuple[dict, dict]]:
    validate_source_inventory(inventory)
    if not isinstance(assessments, list):
        source_error("SOURCE_ASSESSMENTS_TYPE", "Assessments must be an array")
    expected = {row["source_id"]: row for row in inventory["records"]}
    seen: set[str] = set()
    result = []
    for assessment in assessments:
        if not isinstance(assessment, dict) or not isinstance(assessment.get("source_ref"), dict):
            source_error("SOURCE_REFERENCE_INVALID", "An assessment requires a source_ref object")
        reference = assessment["source_ref"]
        ensure_exact_keys(reference, allowed=SOURCE_REF_SCHEMA["properties"],
                          required=SOURCE_REF_SCHEMA["properties"], field="source_ref")
        identifier = reference["source_id"]
        if not isinstance(identifier, str) or identifier not in expected:
            source_error("SOURCE_REFERENCE_UNKNOWN", "Unknown source reference", identifier)
        if identifier in seen:
            source_error("SOURCE_ASSESSMENT_DUPLICATE", "An origin must be assessed exactly once", identifier)
        seen.add(identifier)
        origin = expected[identifier]
        if reference != source_ref(origin):
            source_error("SOURCE_REVISION_STALE", "Assessment refers to another source revision", identifier)
        disposition = assessment.get("disposition")
        if disposition not in {"CONFIRM", "CHALLENGE", "PROMOTE", "INSUFFICIENT_EVIDENCE"}:
            source_error("SOURCE_DISPOSITION_INVALID", "Explicit assessment is required", disposition)
        if complete and disposition in {"CHALLENGE", "INSUFFICIENT_EVIDENCE"}:
            source_error("SOURCE_MODEL_CHALLENGE", "A challenged or unverified claim cannot become COMPLETE", identifier)
        if disposition == "PROMOTE" and origin["group"] not in {"not_affected_consumers", "related_out_of_scope"}:
            source_error("SOURCE_PROMOTION_INVALID", "Only an outside consumer can be promoted into scope", identifier)
        prior = next((event for event in inventory["transitions"] if event["source_ref"]["source_id"] == identifier), None)
        if complete and prior is not None and (disposition != "PROMOTE" or
                prior["target_ref"]["source_id"] != f"I-{inventory['namespace']}-{assessment.get('promotion_id')}"):
            source_error("SOURCE_PROMOTION_LOST", "An admitted promotion cannot silently revert to an outside claim", identifier)
        result.append((origin, assessment))
    if complete and seen != expected.keys():
        source_error("SOURCE_ASSESSMENT_MISSING", "Every current source needs an explicit assessment", sorted(expected.keys() - seen))
    return result


@boundary("B19")
def register_observations(inventory: dict, observations: Mapping[str, list[dict]]) -> tuple[dict, list[str]]:
    """Idempotent discovery admission. Same local ID with changed payload conflicts."""
    validate_source_inventory(inventory)
    result = copy.deepcopy(inventory)
    existing = {row["source_id"]: row for row in result["records"]}
    added = []
    for group in SOURCE_GROUPS:
        for observation in observations.get(group, []):
            local_id = observation.get("observation_id")
            if not isinstance(local_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", local_id):
                source_error("OBSERVATION_ID_INVALID", "New observations require a stable local ID", local_id)
            identifier = f"I-{inventory['namespace']}-{local_id}"
            record = _record(source_id=identifier, author="IMPLEMENTER", group=group,
                             claim=observation, source_artifact=inventory["namespace"])
            if identifier in existing:
                if record != existing[identifier]:
                    source_error("OBSERVATION_ID_CONFLICT", "Repeated ID carries different claims", identifier)
                continue
            existing[identifier] = record
            result["records"].append(record)
            added.append(identifier)
    for assessment in observations.get("source_assessments", []):
        if assessment["disposition"] != "PROMOTE":
            continue
        reference = assessment["source_ref"]
        target_id = f"I-{inventory['namespace']}-{assessment['promotion_id']}"
        target = existing.get(target_id)
        if target is None or target["group"] != "in_scope_consumers":
            source_error("SOURCE_PROMOTION_INVALID", "Promotion requires a current in-scope target", target_id)
        prior = next((event for event in result["transitions"] if event["source_ref"] == reference), None)
        if prior is not None:
            if prior["target_ref"] != source_ref(target):
                source_error("SOURCE_PROMOTION_CONFLICT", "An admitted promotion cannot change its target silently")
            continue
        result["transitions"].append(dict(source_ref=copy.deepcopy(reference), target_ref=source_ref(target),
                                         disposition="PROMOTE", observation=assessment["observation"],
                                         evidence=copy.deepcopy(assessment["evidence"])))
    validate_source_inventory(result)
    return result, added


@boundary("B19")
def register_evaluator_findings(inventory: dict, evaluation: Mapping[str, Any]) -> dict:
    """Admit validated findings once, before asking Implementer to assess them.

    The exact validated evaluation is the origin revision. A later independent
    audit may produce another claim; a repeated delivery of this one is a no-op.
    Paths and symbols come from the next role's own inspection, not invented by
    this adapter from free-form evidence text.
    """
    validate_source_inventory(inventory)
    result = copy.deepcopy(inventory)
    revision = stable_fingerprint(evaluation, length=64)
    existing = {row["source_id"]: row for row in result["records"]}
    for finding in evaluation["findings"]:
        if finding["category"] not in {"CONSUMER", "RISK"}:
            continue
        consumer = finding["category"] == "CONSUMER"
        claim = dict(name=finding["title"], required_proof=copy.deepcopy(finding["required_proof"]),
                     evidence=list(finding["evidence"]))
        claim.update({"why_affected" if consumer else "reason": finding["failure_mode"],
                      "required_behavior" if consumer else "failure_mode": finding["required_action"]})
        identifier = f"E-{inventory['namespace']}-{stable_fingerprint([revision, finding['finding_id']], length=24)}"
        record = _record(source_id=identifier, author="EVALUATOR",
                         group="in_scope_consumers" if consumer else "new_risks",
                         claim=claim, source_artifact=revision)
        if identifier in existing:
            if existing[identifier] != record:
                source_error("SOURCE_ORIGIN_CONFLICT", "An admitted Evaluator origin cannot change")
        else:
            result["records"].append(record)
            existing[identifier] = record
    validate_source_inventory(result)
    return result
