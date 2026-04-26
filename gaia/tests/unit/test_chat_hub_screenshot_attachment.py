from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from gaia import chat_hub


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
