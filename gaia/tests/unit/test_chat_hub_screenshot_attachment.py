from __future__ import annotations

import base64
import threading
import time
from io import BytesIO

from PIL import Image

from gaia import chat_hub
from gaia.chat_hub import CommandResult, HubContext


def test_parse_kv_tokens_keeps_dynamic_human_answer_fields() -> None:
    parsed = chat_hub._parse_kv_tokens('student_id=20201234 otp=123456 answer="cold mans"')

    assert parsed == {
        "student_id": "20201234",
        "otp": "123456",
        "answer": "cold mans",
    }


def test_dispatch_command_preserves_inline_credentials_when_router_rewrites_goal(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_interpret(_context, _text, *, pending_kind=""):
        return {
            "intent": "run_test",
            "confidence": 0.9,
            "goal_text": "로그인 후 메인 화면을 확인해줘",
            "steering_text": "",
            "handoff": {},
        }

    def fake_run_test(context, query, sink, intervention_callback=None):
        del context, sink, intervention_callback
        captured["query"] = query
        return 0, {
            "goal": query,
            "status": "success",
            "final_status": "SUCCESS",
            "steps": 0,
            "duration_seconds": 0,
        }

    monkeypatch.setattr(chat_hub, "_interpret_user_message_with_llm", fake_interpret)
    monkeypatch.setattr(chat_hub, "_run_test", fake_run_test)
    context = HubContext(
        provider="openai",
        model="gpt-5.5",
        auth_strategy="reuse",
        url="https://example.com/login",
        runtime="terminal",
        control_channel="telegram",
    )

    result = chat_hub.dispatch_command(
        context,
        "로그인 후 메인 화면 확인 username=student01 password=secret",
        chat_hub.TerminalSink(),
    )

    assert isinstance(result, CommandResult)
    assert result.code == 0
    assert captured["query"].startswith("로그인 후 메인 화면을 확인해줘")
    assert "username=student01" in captured["query"]
    assert "password=secret" in captured["query"]


def test_telegram_intervention_callback_waits_for_pending_response(monkeypatch) -> None:
    class _Sink:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def info(self, text: str) -> None:
            self.lines.append(text)

        def error(self, text: str) -> None:
            self.lines.append(text)

    context = HubContext(
        provider="openai",
        model="gpt-5.5",
        auth_strategy="reuse",
        url="https://example.com/login",
        runtime="terminal",
        control_channel="telegram",
    )
    sink = _Sink()
    callback = chat_hub._build_telegram_intervention_callback(context, sink)
    monkeypatch.setenv("GAIA_TELEGRAM_INTERVENTION_TIMEOUT_SEC", "2")

    def provide_response() -> None:
        time.sleep(0.1)
        context.pending_user_response = {
            "action": "continue",
            "proceed": "true",
            "username": "student01",
            "password": "secret",
        }

    thread = threading.Thread(target=provide_response)
    thread.start()
    response = callback(
        {
            "kind": "auth",
            "question": "로그인 정보가 필요합니다.",
            "fields": ["username", "password"],
        }
    )
    thread.join(timeout=1)

    assert response["action"] == "continue"
    assert response["username"] == "student01"
    assert response["password"] == "secret"
    assert context.pending_user_input == {}
    assert context.pending_user_response == {}


def test_help_text_advertises_generic_resume_key_values() -> None:
    help_text = chat_hub._help_text()

    assert "/resume [key=value ...]" in help_text
    assert 'otp=123456 answer="..." student_id=20201234' in help_text


def test_capture_session_screenshot_attachment_uses_dispatch_runtime(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object], object]] = []

    class _Response:
        status_code = 200
        payload = {
            "screenshot": "ZmFrZQ==",
            "saved_path": "/tmp/openclaw-shot.png",
        }

    def fake_execute_mcp_action(raw_base_url, *, action, params, timeout=None):
        calls.append((str(raw_base_url), str(action), dict(params or {}), timeout))
        return _Response()

    monkeypatch.setattr(chat_hub, "execute_mcp_action", fake_execute_mcp_action)
    monkeypatch.delenv("GAIA_MCP_HOST_URL", raising=False)
    monkeypatch.delenv("MCP_HOST_URL", raising=False)

    payload = chat_hub._capture_session_screenshot_attachment("session-1")

    assert payload == {
        "kind": "image_base64",
        "mime": "image/png",
        "data": "ZmFrZQ==",
        "path": "/tmp/openclaw-shot.png",
    }
    assert calls == [
        (
            "http://127.0.0.1:8001",
            "browser_screenshot",
            {
                "session_id": "session-1",
                "full_page": False,
                "type": "png",
            },
            90,
        )
    ]


def test_capture_session_screenshot_attachment_retries_blank_capture(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _png_base64(color: tuple[int, int, int]) -> str:
        image = Image.new("RGB", (32, 32), color)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    responses = [
        {
            "screenshot": _png_base64((255, 255, 255)),
            "current_url": "https://example.com/result",
        },
        {
            "screenshot": _png_base64((49, 130, 246)),
            "current_url": "https://example.com/result",
            "saved_path": "/tmp/openclaw-good.png",
        },
    ]

    class _Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

    def fake_execute_mcp_action(raw_base_url, *, action, params, timeout=None):
        calls.append(dict(params or {}))
        return _Response(responses[len(calls) - 1])

    monkeypatch.setattr(chat_hub, "execute_mcp_action", fake_execute_mcp_action)
    monkeypatch.setattr(chat_hub.time, "sleep", lambda *_args, **_kwargs: None)

    payload = chat_hub._capture_session_screenshot_attachment("session-1")

    assert payload == {
        "kind": "image_base64",
        "mime": "image/png",
        "data": responses[1]["screenshot"],
        "path": "/tmp/openclaw-good.png",
    }
    assert calls == [
        {
            "session_id": "session-1",
            "full_page": False,
            "type": "png",
        },
        {
            "session_id": "session-1",
            "full_page": False,
            "type": "png",
        },
    ]
