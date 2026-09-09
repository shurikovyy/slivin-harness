"""Executable boundary inventory and observational hooks; no second state machine.

RETURN means a function returned, never product PASS. Release assertions live in
independently authored tests; metadata cannot synthesize expected outcomes.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
from pathlib import Path

BOUNDARY_VERSION = "boundary-contracts.v1"
# Fully qualified lexical names include Controller closures, so continuation
# and recompilation cannot disappear behind an inaccurate module-level symbol.
BOUNDARIES = {
    "B01": ("Fresh tools and role context", ("task_runner.main.prepare_planner_capabilities", "slivin_harness.preflight.run_static_toolchain_preflight")),
    "B02": ("Owner task envelope", ("slivin_harness.task_contract.run_task_contract_normalizer",)),
    "B03": ("Independent technical model", ("slivin_harness.planner.run_planner", "slivin_harness.planner.validate_plan_artifact")),
    "B04": ("Contract and proof compilation", ("slivin_harness.implementer.build_implementation_contract", "slivin_harness.verification.compile_verification_plan")),
    "B05": ("Implementer report admission and correction", ("task_runner.run_implementer_report",)),
    "B06": ("Trusted test compilation", ("task_runner.build_dynamic_check_specs", "slivin_harness.phase4.CheckRegistry.bind_compiled_specs")),
    "B07": ("Atomic active definition expansion", ("task_runner.main.recompile_active_definition", "slivin_harness.phase5.expand_contract_and_verification_plan")),
    "B08": ("Current assertions and receipt", ("task_runner.run_checks", "task_runner.verify_self_verification_stamp")),
    "B09": ("Technical model reset", ("task_runner.main.replan_implementation", "slivin_harness.phase7.reset_workspace_for_semantic_replan")),
    "B10": ("Independent blind audit and ordered disclosure", ("slivin_harness.evaluator.run_evaluator", "slivin_harness.evaluator.validate_blind_audit")),
    "B11": ("Evaluator source dispositions", ("slivin_harness.evaluator.validate_evaluation_artifact",)),
    "B12": ("Complete current follow-up handoff", ("slivin_harness.handoff.build_user_follow_up_report",)),
    "B13": ("Reconstructed assertions and hidden classification", ("slivin_harness.reconstructed_verification.run_authoritative_reconstructed_verification", "slivin_harness.phase7.classify_heldout_results")),
    "B14": ("Final acceptance and transactional delivery", ("slivin_harness.phase7.build_final_acceptance", "slivin_harness.phase7.deliver_candidate_transaction")),
    "B15": ("Same-thread continuation", ("task_runner.main.continue_implementer",)),
    "B16": ("Report/check/runtime stabilization", ("task_runner.main.stabilize_implementer_report", "task_runner.main.reconcile_project_runtime", "slivin_harness.phase6.RuntimeExecutor.execute", "slivin_harness.phase6.build_contract_closure_record", "slivin_harness.phase6.validate_contract_closure_record")),
    "B17": ("Durable checkpoint and terminal observation", ("slivin_harness.checkpoint.save_report_checkpoint", "task_runner.observe_terminal_report_candidate")),
    "B18": ("Proof-only revision without product reset", ("task_runner.main.revise_proof_route", "slivin_harness.proof_routes.apply_proof_review")),
    "B19": ("Immutable source admission", ("slivin_harness.source_records.register_observations", "slivin_harness.source_records.register_evaluator_findings", "slivin_harness.source_records.resolve_assessments")),
}
_observers = contextvars.ContextVar("harness_boundary_observers", default=())


def attach_boundary_observer(callback):
    return _observers.set((*_observers.get(), callback))


def detach_boundary_observer(token):
    _observers.reset(token)


def boundary_inventory() -> dict:
    inventory = json.loads(Path(__file__).with_name('boundary_contracts.json').read_text(encoding='utf-8'))
    if inventory.get('schema_version') != BOUNDARY_VERSION or {row['boundary_id']:tuple(row['entrypoints']) for row in inventory['boundaries']} != {key:value[1] for key,value in BOUNDARIES.items()}:
        raise RuntimeError('BOUNDARY_CONTRACT_INVENTORY_DRIFT')
    for row in inventory['boundaries']:
        if set(row['families']) != {'input','output','freshness','recovery'} or any(not case['unittest_ids'] or case.get('required') is not True for case in row['families'].values()):
            raise RuntimeError('BOUNDARY_REQUIRED_FAMILY_MISSING')
    return inventory


def boundary(identifier: str):
    if identifier not in BOUNDARIES:
        raise ValueError("Unknown production boundary")
    def decorate(function):
        lexical = function.__module__ + "." + function.__qualname__.replace(".<locals>", "")
        # task_runner executed as a script retains the same public identity.
        if lexical.startswith("__main__."):
            lexical = "task_runner." + lexical[len("__main__."):]
        if lexical not in BOUNDARIES[identifier][1]:
            raise ValueError("Boundary entrypoint is not in the executable inventory: " + lexical)
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            callbacks = _observers.get()
            caller = inspect.currentframe().f_back.f_code.co_qualname.replace(".<locals>", "") if callbacks else ""
            def emit(event, exception=None):
                record = dict(boundary_id=identifier, entrypoint=lexical, caller=caller, event=event)
                if exception is not None:
                    record["exception_type"] = type(exception).__name__
                for callback in callbacks:
                    callback(record)
            emit("ENTER")
            try:
                value = function(*args, **kwargs)
            except BaseException as error:
                emit("RAISE", error)
                raise
            emit("RETURN")
            return value
        wrapped.__boundary_id__ = identifier
        return wrapped
    return decorate
