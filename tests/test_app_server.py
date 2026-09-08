from __future__ import annotations

import os
import copy
import tempfile
import unittest
from pathlib import Path

from slivin_harness.app_server import CodexAppServer, TurnTimeoutError
from slivin_harness.execution import ExecutionBroker, ExecutionRole, ScopedExecutionPolicyError


class ScopedRoleAppServerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="slivin-scoped-wire-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.project = root / "project"
        self.project.mkdir()
        self.broker = ExecutionBroker(workspace=self.project, run_root=root / "run", private_root=root / "private")
        self.server = CodexAppServer(Path("codex"), execution_broker=self.broker,
            process_env={"TEMP": str(root / "old-app-server"), "TMP": str(root / "old-app-server")})
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[dict] = []
        self.override_response = lambda response: response

        def request(method, params, **kwargs):
            self.calls.append((method, copy.deepcopy(params)))
            if method == "thread/start":
                response = {"thread": {"id": f"thread-{len(self.responses)}"},
                    "cwd": params["cwd"], "runtimeWorkspaceRoots": [params["cwd"]],
                    "approvalPolicy": "never", "activePermissionProfile": {"id": params["permissions"], "extends": None},
                    "sandbox": {"type": "workspaceWrite", "writableRoots": [], "networkAccess": False,
                        "excludeTmpdirEnvVar": True, "excludeSlashTmp": True},
                    "instructionSources": [str(self.project / "AGENTS.md")]}
                response = self.override_response(response)
                self.responses.append(response)
                return response
            return {"turn": {"id": "turn-1"}}

        self.server.request = request
        self.server._ensure_alive = lambda **kwargs: None

    def complete_turn(self, thread):
        messages = iter([
            {"method": "item/completed", "params": {"item": {"type": "agentMessage", "phase": "final_answer", "text": "done"}}},
            {"method": "turn/completed", "params": {"turn": {"id": "turn-1", "status": "completed"}}},
        ])
        self.server._receive_raw_optional = lambda timeout: next(messages)
        return self.server.run_turn(thread_id=thread, prompt="diagnose", heartbeat_interval=0)

    def test_initial_corrective_replan_and_evaluator_turns_use_scoped_context(self) -> None:
        process_environment = dict(self.server.process_env)
        contexts = []
        for role in (ExecutionRole.PLANNER, ExecutionRole.PLANNER, ExecutionRole.EVALUATOR, ExecutionRole.EVALUATOR):
            thread = self.server.start_thread(cwd=self.project, execution_role=role, developer_instructions="role instructions")
            start = self.calls[-1][1]
            self.assertNotIn("sandbox", start)
            self.assertFalse(start["ephemeral"])
            self.assertEqual(start["approvalPolicy"], "never")
            self.assertIn(str(self.project.resolve()), start["developerInstructions"])
            self.assertIn("nested repository instructions", start["developerInstructions"])
            config = start["config"]
            self.assertEqual(config[f"permissions.{start['permissions']}.filesystem"], {":root": "read", start["cwd"]: "write"})
            self.assertEqual(config["shell_environment_policy.set"]["TEMP"], start["cwd"])
            for _ in range(2):  # Planner corrective / Evaluator B retain the same thread.
                self.assertEqual(self.complete_turn(thread), "done")
                turn = self.calls[-1][1]
                self.assertEqual(turn["threadId"], thread)
                self.assertNotIn("cwd", turn)
                self.assertNotIn("permissions", turn)
                self.assertNotIn("approvalPolicy", turn)
                self.assertNotIn("sandboxPolicy", turn)
            contexts.append(start["cwd"])
            metadata = self.server.get_thread_metadata(thread)["harness_execution_context"]
            self.assertEqual(metadata["reported_policy_validation"], "PASS")
            self.assertEqual(metadata["filesystem_probe_result"], "NOT_RUN_BY_THREAD_START")
        self.assertEqual(len(set(contexts)), 4)
        self.assertEqual(self.server.process_env, process_environment)

    def test_legacy_override_and_missing_controller_context_fail_closed(self) -> None:
        with self.assertRaisesRegex(ScopedExecutionPolicyError, "legacy sandbox"):
            self.server.start_thread(cwd=self.project, execution_role=ExecutionRole.PLANNER, sandbox="workspace-write")
        with self.assertRaises(ScopedExecutionPolicyError):
            CodexAppServer(Path("codex")).start_thread(cwd=self.project, execution_role=ExecutionRole.EVALUATOR)
        self.assertFalse(self.calls)

    def test_replan_retires_scoped_threads_before_scratch_cleanup(self) -> None:
        threads = [self.server.start_thread(cwd=self.project, execution_role=role)
            for role in (ExecutionRole.PLANNER, ExecutionRole.EVALUATOR)]
        self.server.retire_readonly_threads()
        self.assertEqual([params["threadId"] for method, params in self.calls if method == "thread/archive"], threads)
        self.assertFalse(self.server._role_contexts)
        self.server.retire_readonly_threads()  # Idempotent; no legacy thread is touched.
        self.assertEqual(sum(method == "thread/archive" for method, _ in self.calls), 2)
        with self.assertRaisesRegex(ScopedExecutionPolicyError, "THREAD_RETIRED"):
            self.complete_turn(threads[0])
        self.broker.clear_role_scratch(ExecutionRole.PLANNER)
        fresh = self.server.start_thread(cwd=self.project, execution_role=ExecutionRole.PLANNER)
        self.assertNotIn(fresh, threads)

    def test_failed_retirement_keeps_context_and_rejects_cleanup_route(self) -> None:
        thread = self.server.start_thread(cwd=self.project, execution_role=ExecutionRole.PLANNER)
        def fail(method, params, **kwargs):
            raise RuntimeError("cannot archive session")
        self.server.request = fail
        with self.assertRaisesRegex(ScopedExecutionPolicyError, "ROLE_EXECUTION_RETIRE_FAILED"):
            self.server.retire_readonly_threads()
        self.assertIn(thread, self.server._role_contexts)

    def test_reported_project_write_grant_or_wrong_profile_is_rejected(self) -> None:
        mutations = (
            lambda r: r["sandbox"].update(writableRoots=[str(self.project)]),
            lambda r: r["activePermissionProfile"].update(id=":workspace"),
            lambda r: r["sandbox"].update(excludeTmpdirEnvVar=False),
            lambda r: r["sandbox"].update(networkAccess=True),
            lambda r: r.update(cwd=str(self.project)),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                def override(response):
                    mutate(response)
                    return response
                self.override_response = override
                with self.assertRaisesRegex(ScopedExecutionPolicyError, "POLICY_MISMATCH"):
                    self.server.start_thread(cwd=self.project, execution_role=ExecutionRole.PLANNER)
        self.assertFalse(self.server._role_contexts)

    def test_temporary_instruction_inheritance_is_rejected(self) -> None:
        def override(response):
            response["instructionSources"].append(str(self.project / ".harness_tmp" / "AGENTS.md"))
            return response
        self.override_response = override
        with self.assertRaisesRegex(ScopedExecutionPolicyError, "INSTRUCTION_CONTAMINATION"):
            self.server.start_thread(cwd=self.project, execution_role=ExecutionRole.EVALUATOR)

    def test_unsupported_profile_has_typed_error_without_fallback(self) -> None:
        def unsupported(method, params, **kwargs):
            self.calls.append((method, params))
            raise RuntimeError("named permissions unsupported by installed runtime")
        self.server.request = unsupported
        with self.assertRaisesRegex(ScopedExecutionPolicyError, "ROLE_EXECUTION_POLICY_UNAVAILABLE"):
            self.server.start_thread(cwd=self.project, execution_role=ExecutionRole.PLANNER)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("sandbox", self.calls[0][1])


class AppServerTests(unittest.TestCase):
    def test_approval_requests_are_declined(self) -> None:
        server = CodexAppServer(Path("codex"))
        sent: list[dict] = []
        server._send = sent.append  # type: ignore[method-assign]
        server._answer_server_request(
            {"id": 7, "method": "item/commandExecution/requestApproval", "params": {}}
        )
        self.assertEqual(sent[0]["id"], 7)
        self.assertEqual(sent[0]["result"], {"decision": "decline"})

    def test_permission_requests_grant_nothing(self) -> None:
        server = CodexAppServer(Path("codex"))
        sent: list[dict] = []
        server._send = sent.append  # type: ignore[method-assign]
        server._answer_server_request(
            {"id": 8, "method": "item/permissions/requestApproval", "params": {}}
        )
        self.assertEqual(
            sent[0]["result"], {"permissions": {}, "scope": "turn"}
        )

    def test_mcp_elicitation_is_declined_with_its_own_schema(self) -> None:
        server = CodexAppServer(Path("codex"))
        sent: list[dict] = []
        server._send = sent.append  # type: ignore[method-assign]
        server._answer_server_request(
            {"id": 10, "method": "mcpServer/elicitation/request", "params": {}}
        )
        self.assertEqual(
            sent[0]["result"], {"action": "decline", "content": None}
        )

    def test_unknown_server_requests_receive_protocol_error(self) -> None:
        server = CodexAppServer(Path("codex"))
        sent: list[dict] = []
        server._send = sent.append  # type: ignore[method-assign]
        server._answer_server_request({"id": 9, "method": "unknown/request"})
        self.assertEqual(sent[0]["error"]["code"], -32601)

    def test_thread_start_uses_app_server_sandbox_mode_values(self) -> None:
        server = CodexAppServer(Path("codex"))
        captured: list[tuple[str, dict]] = []

        def fake_request(method: str, params: dict, *, timeout: float = 60) -> dict:
            captured.append((method, params))
            return {"thread": {"id": "thread-1"}}

        server.request = fake_request  # type: ignore[method-assign]
        for sandbox in ("read-only", "workspace-write"):
            captured.clear()
            thread_id = server.start_thread(cwd=Path.cwd(), sandbox=sandbox)
            self.assertEqual(thread_id, "thread-1")
            self.assertEqual(captured[0][1]["sandbox"], sandbox)
            self.assertEqual(captured[0][1]["approvalPolicy"], "never")
            self.assertTrue(captured[0][1]["ephemeral"])

    def test_thread_start_rejects_policy_type_name_as_sandbox_mode(self) -> None:
        server = CodexAppServer(Path("codex"))
        with self.assertRaisesRegex(RuntimeError, "Unsupported sandbox mode"):
            server.start_thread(cwd=Path.cwd(), sandbox="workspaceWrite")


    def test_retryable_turn_error_does_not_abort_turn(self) -> None:
        server = CodexAppServer(Path("codex"))

        def fake_request(method: str, params: dict, *, timeout: float = 60) -> dict:
            self.assertEqual(method, "turn/start")
            return {"turn": {"id": "turn-1"}}

        messages = iter(
            [
                {
                    "method": "error",
                    "params": {
                        "error": {
                            "message": "Reconnecting... 2/5",
                            "codexErrorInfo": {
                                "responseStreamDisconnected": {"httpStatusCode": None}
                            },
                        },
                        "willRetry": True,
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": "done",
                        }
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {"turn": {"id": "turn-1", "status": "completed"}},
                },
            ]
        )
        server.request = fake_request  # type: ignore[method-assign]
        server._ensure_alive = lambda **kwargs: None  # type: ignore[method-assign]
        server._receive_raw_optional = lambda timeout: next(messages)  # type: ignore[method-assign]

        result = server.run_turn(
            thread_id="thread-1",
            prompt="test",
            timeout=30,
            heartbeat_interval=0,
        )
        self.assertEqual(result, "done")

    def test_non_retryable_turn_error_is_fatal(self) -> None:
        server = CodexAppServer(Path("codex"))

        def fake_request(method: str, params: dict, *, timeout: float = 60) -> dict:
            return {"turn": {"id": "turn-1"}}

        messages = iter(
            [
                {
                    "method": "error",
                    "params": {
                        "error": {"message": "fatal"},
                        "willRetry": False,
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                    },
                }
            ]
        )
        server.request = fake_request  # type: ignore[method-assign]
        server._ensure_alive = lambda **kwargs: None  # type: ignore[method-assign]
        server._receive_raw_optional = lambda timeout: next(messages)  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "App Server turn error"):
            server.run_turn(
                thread_id="thread-1",
                prompt="test",
                timeout=30,
                heartbeat_interval=0,
            )

    def test_turn_timeout_uses_specific_exception(self) -> None:
        server = CodexAppServer(Path("codex"))

        def fake_request(method: str, params: dict, *, timeout: float = 60) -> dict:
            return {"turn": {"id": "turn-timeout"}}

        messages = iter([
            {
                "method": "turn/completed",
                "params": {"turn": {"id": "turn-timeout", "status": "interrupted"}},
            }
        ])
        server.request = fake_request  # type: ignore[method-assign]
        server._ensure_alive = lambda **kwargs: None  # type: ignore[method-assign]
        server._interrupt_turn = lambda **kwargs: None  # type: ignore[method-assign]
        server._receive_raw_optional = lambda timeout: next(messages)  # type: ignore[method-assign]

        with self.assertRaises(TurnTimeoutError):
            server.run_turn(
                thread_id="thread-1", prompt="test", timeout=0, heartbeat_interval=0
            )

    def test_runtime_tmp_is_task_local(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="slivin-app-server-"))
        server = CodexAppServer(Path("codex"), runtime_tmp=root)
        self.assertEqual(server.runtime_tmp, root)
        self.assertIsNone(server.stderr_log_path)


    def test_process_environment_can_be_broker_supplied(self) -> None:
        server = CodexAppServer(
            Path("codex"),
            process_env={"PATH": "brokered"},
            execution_policy={"role": "app_server"},
        )
        self.assertEqual(server.process_env, {"PATH": "brokered"})
        self.assertEqual(server.execution_policy, {"role": "app_server"})

    def test_non_windows_command_is_direct(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX-specific command shape")
        server = CodexAppServer(Path("/tmp/codex"))
        command = server._command()
        self.assertEqual(command[0], "/tmp/codex")
        self.assertIn("app-server", command)
        self.assertIn("--stdio", command)


if __name__ == "__main__":
    unittest.main()
