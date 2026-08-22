from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable


class CodexAppServer:
    def __init__(
        self,
        codex_cmd: Path,
        *,
        client_name: str = "slivin-harness",
        client_title: str = "Slivin Harness",
        client_version: str = "0.1.0",
        runtime_tmp: Path | None = None,
    ) -> None:
        self.codex_cmd = codex_cmd
        self.client_name = client_name
        self.client_title = client_title
        self.client_version = client_version
        self.runtime_tmp = runtime_tmp

        self.process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict] = queue.Queue()
        self._backlog: deque[dict] = deque()
        self._next_request_id = 1
        self._last_message_at = time.monotonic()
        self._stdout_closed = threading.Event()
        self._thread_metadata: dict[str, dict] = {}

    def __enter__(self) -> "CodexAppServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if not self.codex_cmd.exists():
            raise RuntimeError(
                f"Codex CLI not found: {self.codex_cmd}"
            )

        comspec = os.environ.get("COMSPEC", "cmd.exe")

        command = subprocess.list2cmdline(
            [
                str(self.codex_cmd),
                "app-server",
                "--strict-config",
                "-c",
                "sandbox_workspace_write.exclude_slash_tmp=true",
                "-c",
                "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                "--stdio",
            ]
        )

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        if self.runtime_tmp is not None:
            self.runtime_tmp.mkdir(parents=True, exist_ok=True)
            runtime_tmp = str(self.runtime_tmp.resolve())

            # Child tools inherit these values. For workspace-write threads
            # the directory is already below the writable workspace root.
            env["TEMP"] = runtime_tmp
            env["TMP"] = runtime_tmp
            env["TMPDIR"] = runtime_tmp
            env["XDG_CACHE_HOME"] = str(
                (self.runtime_tmp / "cache").resolve()
            )
            env["NPM_CONFIG_CACHE"] = str(
                (self.runtime_tmp / "npm").resolve()
            )

        self.process = subprocess.Popen(
            [
                comspec,
                "/d",
                "/s",
                "/c",
                command,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )

        assert self.process.stdout is not None

        threading.Thread(
            target=self._read_stdout,
            daemon=True,
        ).start()

        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": self.client_name,
                    "title": self.client_title,
                    "version": self.client_version,
                },
                "capabilities": {
                    "experimentalApi": False,
                },
            },
        )

        self.notify(
            "initialized",
            {},
        )

        print(
            "APP_SERVER:",
            result["userAgent"],
        )

    def close(self) -> None:
        if self.process is None:
            return

        if self.process.poll() is None:
            self.process.terminate()

            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

        self.process = None

    def _read_stdout(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None

        try:
            for line in self.process.stdout:
                self._last_message_at = time.monotonic()

                line = line.strip()

                if not line:
                    continue

                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    print("APP_SERVER_NON_JSON:", line)
                    continue

                self._messages.put(message)
        finally:
            self._stdout_closed.set()

    def health(self) -> dict:
        process = self.process
        returncode = (
            None
            if process is None
            else process.poll()
        )

        return {
            "alive": (
                process is not None
                and returncode is None
            ),
            "returncode": returncode,
            "stdout_closed": self._stdout_closed.is_set(),
            "last_activity_seconds": max(
                0.0,
                time.monotonic() - self._last_message_at,
            ),
        }

    def _ensure_alive(
        self,
        *,
        operation: str,
    ) -> None:
        health = self.health()

        if not health["alive"]:
            raise RuntimeError(
                "App Server exited while waiting for "
                f"{operation}: returncode={health['returncode']}, "
                f"stdout_closed={health['stdout_closed']}"
            )

    def _send(self, message: dict) -> None:
        if self.process is None:
            raise RuntimeError("App Server is not running")

        self._ensure_alive(operation="send")

        assert self.process.stdin is not None

        try:
            self.process.stdin.write(
                json.dumps(
                    message,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(
                "Failed to write to App Server stdin"
            ) from exc

    def notify(
        self,
        method: str,
        params: dict,
    ) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    def request(
        self,
        method: str,
        params: dict,
        *,
        timeout: float = 60,
    ) -> dict:
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

        deadline = time.monotonic() + timeout
        deferred: list[dict] = []

        try:
            while True:
                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    raise RuntimeError(
                        f"Timeout waiting for {method}"
                    )

                try:
                    # Poll instead of blocking for the full RPC timeout so a
                    # dead app-server process is detected quickly.
                    message = self._messages.get(
                        timeout=min(1.0, remaining)
                    )
                except queue.Empty:
                    self._ensure_alive(
                        operation=method
                    )
                    continue

                if (
                    "method" in message
                    and "id" in message
                ):
                    raise RuntimeError(
                        f"Unexpected App Server request: {message}"
                    )

                if message.get("id") != request_id:
                    deferred.append(message)
                    continue

                if "error" in message:
                    raise RuntimeError(
                        f"{method} failed: {message['error']}"
                    )

                return message["result"]

        finally:
            self._backlog.extend(deferred)

    def _receive_raw_optional(
        self,
        timeout: float,
    ) -> dict | None:
        if self._backlog:
            return self._backlog.popleft()

        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def list_skills(
        self,
        cwd: Path,
        *,
        force_reload: bool = True,
    ) -> dict:
        return self.request(
            "skills/list",
            {
                "cwds": [str(cwd.resolve())],
                "forceReload": force_reload,
            },
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
        params: dict = {
            "cwd": str(cwd.resolve()),
            "sandbox": sandbox,
            "approvalPolicy": "never",
            "ephemeral": True,
        }

        if developer_instructions:
            params["developerInstructions"] = developer_instructions

        result = self.request(
            "thread/start",
            params,
        )

        thread = result["thread"]
        thread_id = thread["id"]
        self._thread_metadata[thread_id] = thread

        if on_started:
            on_started(thread)

        return thread_id

    def get_thread_metadata(
        self,
        thread_id: str,
    ) -> dict:
        return dict(
            self._thread_metadata.get(
                thread_id,
                {},
            )
        )

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
        skill_items = list(skills or [])
        visible_prompt = prompt

        if skill_items:
            markers = " ".join(
                f"${item['name']}"
                for item in skill_items
            )
            visible_prompt = (
                markers
                + "\n\n"
                + prompt
            )

        turn_input: list[dict] = [
            {
                "type": "text",
                "text": visible_prompt,
            }
        ]

        for skill in skill_items:
            turn_input.append(
                {
                    "type": "skill",
                    "name": skill["name"],
                    "path": skill["path"],
                }
            )

        turn_params = {
            "threadId": thread_id,
            "input": turn_input,
        }

        if output_schema is not None:
            turn_params["outputSchema"] = output_schema

        result = self.request(
            "turn/start",
            turn_params,
        )

        turn_id = result["turn"]["id"]
        turn_started = time.monotonic()
        deadline = turn_started + timeout
        next_heartbeat = (
            turn_started + heartbeat_interval
        )

        final_messages: list[str] = []
        fallback_messages: list[str] = []

        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                raise RuntimeError(
                    f"Turn timeout: {turn_id}"
                )

            self._ensure_alive(
                operation=f"turn {turn_id}"
            )

            message = self._receive_raw_optional(
                min(1.0, remaining)
            )

            now = time.monotonic()

            # Heartbeat is wall-clock based, not "queue-idle" based.
            # Structured Planner/Evaluator turns may emit tool/lifecycle events
            # continuously while producing no user-visible text.
            if (
                on_heartbeat
                and heartbeat_interval > 0
                and now >= next_heartbeat
            ):
                health = self.health()
                on_heartbeat(
                    {
                        **health,
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "turn_elapsed_seconds": (
                            now - turn_started
                        ),
                    }
                )
                next_heartbeat = (
                    now + heartbeat_interval
                )

            if message is None:
                continue

            if "id" in message and "method" in message:
                raise RuntimeError(
                    f"Unexpected App Server request: {message}"
                )

            method = message.get("method")
            params = message.get("params", {})

            if method == "item/agentMessage/delta":
                delta = params.get("delta", "")

                if delta and on_delta:
                    on_delta(delta)

                continue

            if method == "item/completed":
                item = params.get("item", {})

                if item.get("type") == "agentMessage":
                    msg_text = item.get("text")
                    phase = item.get("phase")

                    if msg_text:
                        if phase == "final_answer":
                            final_messages.append(msg_text)
                        else:
                            fallback_messages.append(msg_text)

                    if on_message_end:
                        on_message_end()

                continue

            if method == "error":
                print(
                    "APP_SERVER_ERROR:",
                    params,
                )
                continue

            if method != "turn/completed":
                continue

            turn = params.get("turn", {})

            if turn.get("id") != turn_id:
                continue

            status = turn.get("status")

            if status != "completed":
                raise RuntimeError(
                    f"Turn finished with status {status}: {turn}"
                )

            if final_messages:
                return final_messages[-1]

            if fallback_messages:
                return fallback_messages[-1]

            raise RuntimeError(
                f"Turn completed without an agent final response: {turn_id}"
            )
