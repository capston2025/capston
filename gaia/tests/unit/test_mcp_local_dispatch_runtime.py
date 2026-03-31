from __future__ import annotations

from gaia.src.phase4 import mcp_local_dispatch_runtime as runtime


def test_current_browser_backend_always_returns_openclaw(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "gaia")

    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "openclaw"


def test_execute_mcp_action_routes_browser_wait_to_openclaw(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_dispatch_openclaw_action(raw_base_url, *, action, params, timeout=None):
        calls.append((str(action), dict(params or {})))
        return 200, {"success": True, "reason_code": "ok", "transport": "openclaw"}, ""

    monkeypatch.setattr(runtime, "dispatch_openclaw_action", fake_dispatch_openclaw_action)

    result = runtime.execute_mcp_action(
        "http://127.0.0.1:8000",
        action="browser_wait",
        params={"session_id": "s1", "text": "ready"},
    )

    assert result.status_code == 200
    assert result.payload["transport"] == "openclaw"
    assert calls == [("browser_wait", {"session_id": "s1", "text": "ready"})]


def test_execute_mcp_action_routes_capture_screenshot_to_openclaw(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_dispatch_openclaw_action(raw_base_url, *, action, params, timeout=None):
        calls.append((str(action), dict(params or {})))
        return (
            200,
            {
                "success": True,
                "reason_code": "ok",
                "screenshot": "ZmFrZQ==",
                "transport": "openclaw",
            },
            "",
        )

    monkeypatch.setattr(runtime, "dispatch_openclaw_action", fake_dispatch_openclaw_action)

    result = runtime.execute_mcp_action(
        "http://127.0.0.1:8000",
        action="capture_screenshot",
        params={"session_id": "shot-1"},
    )

    assert result.status_code == 200
    assert result.payload["transport"] == "openclaw"
    assert calls == [("capture_screenshot", {"session_id": "shot-1"})]


def test_execute_mcp_action_routes_console_logs_to_openclaw(monkeypatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def fake_dispatch_console(raw_base_url, *, session_id, level="", limit=100, timeout=None):
        del raw_base_url, timeout
        calls.append((str(session_id), str(level), int(limit)))
        return 200, {"success": True, "logs": ["a"], "items": ["a"]}, ""

    monkeypatch.setattr(runtime, "dispatch_openclaw_console_logs", fake_dispatch_console)

    result = runtime.execute_mcp_action(
        "http://127.0.0.1:8000",
        action="get_console_logs",
        params={"session_id": "s1", "type": "error", "limit": 12},
    )

    assert result.status_code == 200
    assert result.payload["items"] == ["a"]
    assert calls == [("s1", "error", 12)]


def test_execute_mcp_action_routes_current_url_to_openclaw_session_state(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "get_openclaw_session_url", lambda session_id: f"https://example.test/{session_id}")

    result = runtime.execute_mcp_action(
        "http://127.0.0.1:8000",
        action="get_current_url",
        params={"session_id": "s1"},
    )

    assert result.status_code == 200
    assert result.payload == {
        "success": True,
        "reason_code": "ok",
        "url": "https://example.test/s1",
    }


def test_close_mcp_session_routes_to_openclaw(monkeypatch) -> None:
    calls: list[str] = []

    def fake_dispatch_close(raw_base_url, *, session_id, timeout=None):
        del raw_base_url, timeout
        calls.append(str(session_id))
        return 200, {"success": True, "reason_code": "ok"}, ""

    monkeypatch.setattr(runtime, "dispatch_openclaw_close", fake_dispatch_close)

    result = runtime.close_mcp_session(
        "http://127.0.0.1:8000",
        session_id="s-close",
    )

    assert result.status_code == 200
    assert result.payload["reason_code"] == "ok"
    assert calls == ["s-close"]
