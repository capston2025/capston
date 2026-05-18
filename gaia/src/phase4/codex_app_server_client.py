from __future__ import annotations

import atexit
import base64
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable


class CodexAppServerError(RuntimeError):
    """Raised when the persistent Codex app-server transport cannot complete a turn."""


class CodexAppServerClient:
    """Small JSON-RPC client for `codex app-server --listen stdio://`.

    The goal is narrower than OpenClaw's full native Codex harness: keep one
    app-server process alive and ask it for bounded text/vision decisions,
    while keeping GAIA's existing browser dispatch and verifier loop intact.
    """

    _BASE_INSTRUCTIONS = (
        "You are GAIA's bounded decision engine. Treat every user message as a standalone request. "
        "Return only the final answer requested by the user message. Do not call tools, do not inspect "
        "files, and do not execute commands."
    )

    def __init__(
        self,
        *,
        codex_bin: str | None = None,
        model: str = "gpt-5.5",
        timeout_sec: int = 120,
        reasoning_effort: str | None = None,
        reuse_thread: bool = True,
    ) -> None:
        self.codex_bin = codex_bin or shutil.which("codex") or "codex"
        self.model = model
        self.timeout_sec = max(15, min(int(timeout_sec), 600))
        self.reasoning_effort = (reasoning_effort or os.getenv("GAIA_CODEX_REASONING_EFFORT") or "low").strip() or "low"
        self.reuse_thread = reuse_thread
        self._lock = threading.RLock()
        self._next_id = 1
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_lines: queue.Queue[str] = queue.Queue()
        self._thread_id: str | None = None
        self._tmpdir = tempfile.TemporaryDirectory(prefix="gaia-codex-appserver-")
        self._cwd = Path(self._tmpdir.name) / "workspace"
        self._cwd.mkdir(parents=True, exist_ok=True)
        atexit.register(self.close)

    @property
    def is_started(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def close(self) -> None:
        with self._lock:
            proc = self._process
            self._process = None
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
            try:
                self._tmpdir.cleanup()
            except Exception:
                pass

    def analyze_text(self, prompt: str) -> str:
        return self._run_turn([self._text_input(prompt)])

    def analyze_with_images(self, prompt: str, images: Iterable[str]) -> str:
        inputs: list[dict[str, Any]] = [self._text_input(prompt)]
        image_paths = self._write_images(images)
        inputs.extend({"type": "localImage", "path": str(path)} for path in image_paths)
        return self._run_turn(inputs)

    @staticmethod
    def _text_input(text: str) -> dict[str, Any]:
        return {"type": "text", "text": text, "text_elements": []}

    def _write_images(self, images: Iterable[str]) -> list[Path]:
        paths: list[Path] = []
        image_dir = Path(self._tmpdir.name) / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        for idx, image_b64 in enumerate(images):
            raw = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
            path = image_dir / f"input-{int(time.time() * 1000)}-{idx}.png"
            path.write_bytes(base64.b64decode(raw))
            paths.append(path)
        return paths

    def _start(self) -> None:
        if self.is_started:
            return
        cmd = [
            self.codex_bin,
            "app-server",
            "--listen",
            "stdio://",
            "--disable",
            "codex_hooks",
            "-c",
            'model_reasoning_effort="low"',
            "-c",
            "suppress_unstable_features_warning=true",
        ]
        if self.reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
        extra_args = os.getenv("GAIA_CODEX_APP_SERVER_ARGS", "").strip()
        if extra_args:
            cmd.extend(extra_args.split())
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self._cwd),
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self._request(
            "initialize",
            {
                "clientInfo": {"name": "gaia", "title": "GAIA", "version": "0"},
                "capabilities": {"experimentalApi": True},
            },
            timeout=10,
        )

    def _read_stdout(self) -> None:
        proc = self._process
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._messages.put(json.loads(line))
            except Exception:
                self._stderr_lines.put(f"invalid_json_stdout:{line[:500]}")

    def _read_stderr(self) -> None:
        proc = self._process
        if not proc or not proc.stderr:
            return
        for line in proc.stderr:
            line = line.strip()
            if line:
                self._stderr_lines.put(line[:1000])

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._process
        if not proc or proc.poll() is not None or not proc.stdin:
            raise CodexAppServerError("codex app-server is not running")
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def _request(self, method: str, params: Any, *, timeout: int | None = None) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return self._wait_for_response(request_id, timeout=timeout or self.timeout_sec)

    def _wait_for_response(self, request_id: int, *, timeout: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process and self._process.poll() is not None:
                raise CodexAppServerError(f"codex app-server exited with code {self._process.returncode}: {self._stderr_tail()}")
            try:
                message = self._messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if "id" in message and "method" in message:
                self._handle_server_request(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CodexAppServerError(str(message.get("error")))
            result = message.get("result")
            return result if isinstance(result, dict) else {"result": result}
        raise CodexAppServerError(f"codex app-server request timed out: {request_id}; stderr={self._stderr_tail()}")

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if request_id is None:
            return
        if method == "item/tool/call":
            result = {
                "success": False,
                "contentItems": [{"type": "inputText", "text": "Tool calls are disabled in GAIA decision transport."}],
            }
        elif method == "item/tool/requestUserInput":
            result = {"answers": {}}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "decline", "content": None, "_meta": None}
        elif method == "item/commandExecution/requestApproval":
            result = {"decision": "decline"}
        elif method == "item/fileChange/requestApproval":
            result = {"decision": "decline"}
        elif method in {"applyPatchApproval", "execCommandApproval"}:
            result = {"decision": "denied"}
        elif method == "item/permissions/requestApproval":
            result = {
                "permissions": {"type": "none"},
                "scope": "turn",
                "strictAutoReview": True,
            }
        else:
            result = {}
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception:
            pass

    def _ensure_thread(self) -> str:
        if self.reuse_thread and self._thread_id:
            return self._thread_id
        response = self._request(
            "thread/start",
            {
                "model": self.model,
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "cwd": str(self._cwd),
                "ephemeral": True,
                "baseInstructions": self._BASE_INSTRUCTIONS,
                "developerInstructions": self._BASE_INSTRUCTIONS,
                "sessionStartSource": "startup",
            },
            timeout=30,
        )
        thread = response.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexAppServerError(f"thread/start did not return a thread id: {response}")
        if self.reuse_thread:
            self._thread_id = thread_id
        return thread_id

    def _run_turn(self, inputs: list[dict[str, Any]]) -> str:
        with self._lock:
            self._start()
            thread_id = self._ensure_thread()
            response = self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": inputs,
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    "model": self.model,
                    "effort": self.reasoning_effort or "low",
                },
                timeout=15,
            )
            turn = response.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise CodexAppServerError(f"turn/start did not return a turn id: {response}")
            return self._wait_for_turn(thread_id=thread_id, turn_id=turn_id)

    def _wait_for_turn(self, *, thread_id: str, turn_id: str) -> str:
        deadline = time.monotonic() + self.timeout_sec
        deltas: list[str] = []
        completed_text = ""
        while time.monotonic() < deadline:
            if self._process and self._process.poll() is not None:
                raise CodexAppServerError(f"codex app-server exited with code {self._process.returncode}: {self._stderr_tail()}")
            try:
                message = self._messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if "id" in message and "method" in message:
                self._handle_server_request(message)
                continue
            method = str(message.get("method") or "")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if params.get("threadId") != thread_id:
                continue
            if method == "item/agentMessage/delta" and params.get("turnId") == turn_id:
                delta = params.get("delta")
                if isinstance(delta, str):
                    deltas.append(delta)
            elif method == "item/completed" and params.get("turnId") == turn_id:
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        completed_text = text
            elif method == "turn/completed":
                turn = params.get("turn")
                if isinstance(turn, dict) and turn.get("id") == turn_id:
                    if turn.get("status") == "failed":
                        raise CodexAppServerError(str(turn.get("error") or "turn failed"))
                    text = completed_text or "".join(deltas).strip()
                    if text:
                        return text
                    raise CodexAppServerError("turn completed without assistant text")
        raise CodexAppServerError(f"codex app-server turn timed out: {turn_id}; stderr={self._stderr_tail()}")

    def _stderr_tail(self, limit: int = 3) -> str:
        lines: list[str] = []
        try:
            while True:
                lines.append(self._stderr_lines.get_nowait())
        except queue.Empty:
            pass
        return " | ".join(lines[-limit:])


__all__ = ["CodexAppServerClient", "CodexAppServerError"]
