from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path


class TurnTimeoutError(RuntimeError):
    """A Codex turn exceeded the Harness deadline and was interrupted."""

from typing import Callable

from slivin_harness.output_schema import validate_strict_output_schema


def _phase4_inactivity_expired(
    *,
    now: float,
    last_real_activity_at: float,
    inactivity_timeout_seconds: float,
    active_tools: int,
) -> bool:
    """Return true only after real inactivity with no active tool.

    Total elapsed time is intentionally not used. Controller-generated heartbeats
    do not call this function with a newer activity timestamp.
    """

    if active_tools > 0:
        return False
    return max(0.0, now - last_real_activity_at) >= max(
        0.0, inactivity_timeout_seconds
    )


class CodexAppServer:
    """Small synchronous JSON-RPC client for Codex App Server.

    The Controller owns approvals, timeout/cancellation and process health. Agent
    messages are streamed for observability, while the final structured response
    is returned to the caller.
    """

    def __init__(
        self,
        codex_cmd: Path,
        *,
        client_name: str = "slivin-harness",
        client_title: str = "Slivin Harness",
        client_version: str = "0.6.5",
        runtime_tmp: Path | None = None,
        process_env: dict[str, str] | None = None,
        execution_policy: dict | None = None,
    ) -> None:
        self.codex_cmd = codex_cmd
        self.client_name = client_name
        self.client_title = client_title
        self.client_version = client_version
        self.runtime_tmp = runtime_tmp
        self.process_env = dict(process_env) if process_env is not None else None
        self.execution_policy = execution_policy

        self.process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict] = queue.Queue()
        self._backlog: deque[dict] = deque()
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._next_request_id = 1
        self._last_message_at = time.monotonic()
        self._active_tools = 0
        self._stdout_closed = threading.Event()
        self._stderr_closed = threading.Event()
        self._thread_metadata: dict[str, dict] = {}
        self.stderr_log_path: Path | None = None

    def __enter__(self) -> "CodexAppServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _command(self) -> list[str]:
        args = [
            str(self.codex_cmd),
            "app-server",
            "--strict-config",
            "-c",
            "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c",
            "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "--stdio",
        ]
        if os.name != "nt":
            return args

        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/d", "/s", "/c", subprocess.list2cmdline(args)]

    def start(self) -> None:
        if not self.codex_cmd.exists():
            raise RuntimeError(f"Codex CLI not found: {self.codex_cmd}")

        env = (self.process_env or os.environ).copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if self.runtime_tmp is not None:
            self.runtime_tmp.mkdir(parents=True, exist_ok=True)
            runtime_tmp = str(self.runtime_tmp.resolve())
            env.update(
                {
                    "TEMP": runtime_tmp,
                    "TMP": runtime_tmp,
                    "TMPDIR": runtime_tmp,
                    "XDG_CACHE_HOME": str((self.runtime_tmp / "cache").resolve()),
                    "NPM_CONFIG_CACHE": str((self.runtime_tmp / "npm").resolve()),
                }
            )
            self.stderr_log_path = self.runtime_tmp / "app_server.stderr.log"

        self.process = subprocess.Popen(
            self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": self.client_name,
                    "title": self.client_title,
                    "version": self.client_version,
                },
                "capabilities": {"experimentalApi": False},
            },
        )
        self.notify("initialized", {})
        print("APP_SERVER:", result.get("userAgent", "initialized"))

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        self.process = None

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self._last_message_at = time.monotonic()
                line = line.strip()
                if not line:
                    continue
                try:
                    self._messages.put(json.loads(line))
                except json.JSONDecodeError:
                    print("APP_SERVER_NON_JSON:", line)
        finally:
            self._stdout_closed.set()

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        log_handle = None
        try:
            if self.stderr_log_path is not None:
                self.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
                log_handle = self.stderr_log_path.open("a", encoding="utf-8", newline="\n")
            for line in self.process.stderr:
                text = line.rstrip("\r\n")
                if text:
                    self._stderr_tail.append(text)
                if log_handle is not None:
                    log_handle.write(line)
                    log_handle.flush()
        finally:
            if log_handle is not None:
                log_handle.close()
            self._stderr_closed.set()

    def health(self) -> dict:
        process = self.process
        returncode = None if process is None else process.poll()
        return {
            "alive": process is not None and returncode is None,
            "returncode": returncode,
            "stdout_closed": self._stdout_closed.is_set(),
            "stderr_closed": self._stderr_closed.is_set(),
            "last_activity_seconds": max(0.0, time.monotonic() - self._last_message_at),
            "active_tools": self._active_tools,
        }

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    def _ensure_alive(self, *, operation: str) -> None:
        health = self.health()
        if not health["alive"]:
            tail = self.stderr_tail()
            raise RuntimeError(
                f"App Server exited while waiting for {operation}: "
                f"returncode={health['returncode']}, stdout_closed={health['stdout_closed']}"
                + (f"\n--- stderr tail ---\n{tail}" if tail else "")
            )

    def _send(self, message: dict) -> None:
        if self.process is None:
            raise RuntimeError("App Server is not running")
        self._ensure_alive(operation="send")
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError("Failed to write to App Server stdin") from exc

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _answer_server_request(self, message: dict) -> None:
        request_id = message.get("id")
        method = str(message.get("method", ""))

        # No interactive approval is allowed in an autonomous Harness run.
        # Different App Server requests use different response schemas, so they
        # must be denied explicitly rather than with one generic payload.
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            result = {"decision": "decline"}
        elif method == "item/permissions/requestApproval":
            result = {"permissions": {}, "scope": "turn"}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "decline", "content": None}
        else:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unsupported App Server request: {method}",
                    },
                }
            )
            print("APP_SERVER_REQUEST_UNSUPPORTED:", method)
            return

        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        print("APP_SERVER_REQUEST_DENIED:", method)

    def request(self, method: str, params: dict, *, timeout: float = 60) -> dict:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        # JSON-RPC request/response calls are bounded deterministic operations.
        # The long-running agent-turn inactivity policy below must not change
        # their ordinary timeout semantics.
        deadline = time.monotonic() + float(timeout)
        deferred: list[dict] = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"Timeout waiting for {method}")
                try:
                    message = self._messages.get(timeout=min(1.0, remaining))
                except queue.Empty:
                    self._ensure_alive(operation=method)
                    continue
                if "method" in message and "id" in message:
                    self._answer_server_request(message)
                    continue
                if message.get("id") != request_id:
                    deferred.append(message)
                    continue
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result", {})
        finally:
            self._backlog.extend(deferred)

    def _receive_raw_optional(self, timeout: float) -> dict | None:
        if self._backlog:
            return self._backlog.popleft()
        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def list_skills(self, cwd: Path, *, force_reload: bool = True) -> dict:
        return self.request(
            "skills/list",
            {"cwds": [str(cwd.resolve())], "forceReload": force_reload},
            timeout=120,
        )

    def start_thread(
        self,
        *,
        cwd: Path,
        sandbox: str,
        developer_instructions: str | None = None,
        on_started: Callable[[dict], None] | None = None,
    ) -> str:
        sandbox_modes = {"read-only", "workspace-write"}
        if sandbox not in sandbox_modes:
            raise RuntimeError(f"Unsupported sandbox mode: {sandbox}")
        params: dict = {
            "cwd": str(cwd.resolve()),
            # thread/start expects SandboxMode values, not camelCase SandboxPolicy types.
            "sandbox": sandbox,
            "approvalPolicy": "never",
            "ephemeral": True,
        }
        if developer_instructions:
            params["developerInstructions"] = developer_instructions
        thread = self.request("thread/start", params)["thread"]
        thread_id = str(thread["id"])
        self._thread_metadata[thread_id] = thread
        if on_started:
            on_started(thread)
        return thread_id

    def get_thread_metadata(self, thread_id: str) -> dict:
        return dict(self._thread_metadata.get(thread_id, {}))

    def _interrupt_turn(self, *, thread_id: str, turn_id: str) -> None:
        try:
            self.request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
                timeout=10,
            )
        except Exception as exc:  # cancellation is best-effort before hard failure
            print("APP_SERVER_INTERRUPT_FAILED:", exc)

    def run_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        timeout: float = 900,
        on_delta: Callable[[str], None] | None = None,
        on_message_end: Callable[[], None] | None = None,
        on_heartbeat: Callable[[dict], None] | None = None,
        heartbeat_interval: float = 20.0,
        output_schema: dict | None = None,
        skills: list[dict[str, str]] | None = None,
    ) -> str:
        """Run one App Server turn with an inactivity watchdog.

        ``timeout`` is the maximum period without real model/App Server activity,
        not a short total wall-clock budget. A running tool suppresses inactivity
        interruption. A seven-day phase4 emergency ceiling remains only as a final
        owner-safety bound.
        """
        skill_items = list(skills or [])
        visible_prompt = prompt
        if skill_items:
            visible_prompt = " ".join(f"${item['name']}" for item in skill_items) + "\n\n" + prompt

        turn_input: list[dict] = [{"type": "text", "text": visible_prompt}]
        turn_input.extend(
            {"type": "skill", "name": item["name"], "path": item["path"]}
            for item in skill_items
        )
        turn_params: dict = {"threadId": thread_id, "input": turn_input}
        if output_schema is not None:
            validate_strict_output_schema(output_schema)
            turn_params["outputSchema"] = output_schema

        turn_id = str(self.request("turn/start", turn_params)["turn"]["id"])
        started = time.monotonic()
        last_real_activity = started
        inactivity_timeout = max(0.0, float(timeout))
        emergency_deadline = started + 7 * 24 * 60 * 60  # phase4 emergency ceiling
        next_heartbeat = started + heartbeat_interval
        final_messages: list[str] = []
        fallback_messages: list[str] = []
        interrupted = False
        interrupt_deadline: float | None = None
        active_tool_ids: set[str] = set()

        def tool_key(item: dict) -> str:
            value = item.get("id") or item.get("itemId") or item.get("callId")
            return str(value) if value is not None else f"anonymous:{id(item)}"

        while True:
            now = time.monotonic()
            inactive = _phase4_inactivity_expired(
                now=now,
                last_real_activity_at=last_real_activity,
                inactivity_timeout_seconds=inactivity_timeout,
                active_tools=len(active_tool_ids),
            )
            emergency_expired = now >= emergency_deadline
            if not interrupted and (inactive or emergency_expired):
                interrupted = True
                self._interrupt_turn(thread_id=thread_id, turn_id=turn_id)
                interrupt_deadline = now + 15
            elif interrupted and interrupt_deadline is not None and now >= interrupt_deadline:
                raise TurnTimeoutError(f"Turn timeout after interrupt: {turn_id}")

            self._ensure_alive(operation=f"turn {turn_id}")
            if interrupted and interrupt_deadline is not None:
                wait_budget = max(0.01, interrupt_deadline - now)
            elif active_tool_ids:
                wait_budget = 1.0
            else:
                wait_budget = max(0.01, inactivity_timeout - (now - last_real_activity))
            message = self._receive_raw_optional(min(1.0, wait_budget))
            now = time.monotonic()
            if on_heartbeat and heartbeat_interval > 0 and now >= next_heartbeat:
                on_heartbeat(
                    {
                        **self.health(),
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "turn_elapsed_seconds": now - started,
                        "active_tools": len(active_tool_ids),
                        "inactivity_seconds": max(0.0, now - last_real_activity),
                    }
                )
                next_heartbeat = now + heartbeat_interval
            if message is None:
                continue

            # Any received App Server message is real activity. Controller heartbeat
            # output is produced above and deliberately does not update this timestamp.
            last_real_activity = now
            if "id" in message and "method" in message:
                self._answer_server_request(message)
                continue

            method = message.get("method")
            params = message.get("params", {})
            if method == "item/started":
                item = params.get("item", {})
                if isinstance(item, dict) and item.get("type") != "agentMessage":
                    active_tool_ids.add(tool_key(item))
                continue
            if method == "item/agentMessage/delta":
                delta = str(params.get("delta", ""))
                if delta and on_delta:
                    on_delta(delta)
                continue
            if method == "item/completed":
                item = params.get("item", {})
                if isinstance(item, dict):
                    active_tool_ids.discard(tool_key(item))
                    if item.get("type") == "agentMessage":
                        text = item.get("text")
                        if text:
                            target = final_messages if item.get("phase") == "final_answer" else fallback_messages
                            target.append(str(text))
                        if on_message_end:
                            on_message_end()
                continue
            if method == "error":
                error_thread_id = str(params.get("threadId") or "")
                error_turn_id = str(params.get("turnId") or "")
                if error_thread_id and error_thread_id != thread_id:
                    continue
                if error_turn_id and error_turn_id != turn_id:
                    continue
                if params.get("willRetry") is True:
                    error = params.get("error", {})
                    if isinstance(error, dict):
                        error_message = str(error.get("message") or "transient turn error")
                    else:
                        error_message = str(error)
                    print(f"APP_SERVER_TURN_RETRY: {error_message}")
                    continue
                raise RuntimeError(f"App Server turn error: {params}")
            if method != "turn/completed":
                continue

            turn = params.get("turn", {})
            if str(turn.get("id")) != turn_id:
                continue
            status = str(turn.get("status"))
            if interrupted or status == "interrupted":
                raise TurnTimeoutError(f"Turn interrupted after inactivity timeout: {turn_id}")
            if status != "completed":
                raise RuntimeError(f"Turn finished with status {status}: {turn}")
            if final_messages:
                return final_messages[-1]
            if fallback_messages:
                return fallback_messages[-1]
            raise RuntimeError(f"Turn completed without a final response: {turn_id}")
