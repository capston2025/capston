from __future__ import annotations

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
