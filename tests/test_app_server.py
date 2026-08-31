from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from slivin_harness.app_server import CodexAppServer, TurnTimeoutError


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
