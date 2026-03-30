from __future__ import annotations

import asyncio

from gaia.src.phase4 import mcp_local_dispatch_runtime as runtime


def test_should_fallback_from_openclaw_detects_embedded_cdp_boot_failure(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_OPENCLAW_FALLBACK_BACKEND", "gaia")

    assert runtime._should_fallback_from_openclaw(
        {
            "reason_code": "action_timeout",
            "reason": 'DOM 분석 실패: Error: Failed to start Chrome CDP on port 18800 for profile "openclaw".',
        },
        "",
    )


def test_current_browser_backend_prefers_openclaw_when_selected(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "openclaw")

    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "openclaw"


def test_current_browser_backend_defaults_to_openclaw(monkeypatch) -> None:
    monkeypatch.delenv("GAIA_BROWSER_BACKEND", raising=False)
    monkeypatch.delenv("GAIA_OPENCLAW_BASE_URL", raising=False)

    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "openclaw"


def test_current_browser_backend_can_force_gaia(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "gaia")
    monkeypatch.delenv("GAIA_OPENCLAW_BASE_URL", raising=False)

    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "gaia"


def test_execute_mcp_action_falls_back_to_local_backend_on_openclaw_bootstrap_failure(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "openclaw")
    monkeypatch.setenv("GAIA_OPENCLAW_FALLBACK_BACKEND", "gaia")

    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_dispatch_openclaw_action(raw_base_url, *, action, params, timeout=None):
        calls.append(("openclaw", str(action), dict(params or {})))
        return (
            500,
            {
                "reason_code": "action_timeout",
                "reason": 'DOM 분석 실패: Error: Failed to start Chrome CDP on port 18800 for profile "openclaw".',
            },
            'DOM 분석 실패: Error: Failed to start Chrome CDP on port 18800 for profile "openclaw".',
        )

    async def fake_dispatch_local_execute_async(raw_base_url, *, action, params):
        calls.append(("gaia", str(action), dict(params or {})))
        return runtime.DispatchResult(
            status_code=200,
            payload={"success": True, "reason_code": "ok", "transport": "gaia"},
            text="",
        )

    monkeypatch.setattr(runtime, "dispatch_openclaw_action", fake_dispatch_openclaw_action)
    monkeypatch.setattr(runtime, "_dispatch_local_execute_async", fake_dispatch_local_execute_async)
    monkeypatch.setattr(runtime, "_run_sync", lambda awaitable: asyncio.run(awaitable))

    result = runtime.execute_mcp_action(
        "http://127.0.0.1:8000",
        action="browser_snapshot",
        params={"session_id": "s1"},
    )

    assert result.status_code == 200
    assert result.payload["transport"] == "gaia"
    assert calls == [
        ("openclaw", "browser_snapshot", {"session_id": "s1"}),
        ("gaia", "browser_snapshot", {"session_id": "s1"}),
    ]
    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "openclaw"


def test_execute_mcp_action_falls_back_to_local_backend_on_openclaw_bootstrap_exception(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "openclaw")
    monkeypatch.setenv("GAIA_OPENCLAW_FALLBACK_BACKEND", "gaia")

    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_dispatch_openclaw_action(raw_base_url, *, action, params, timeout=None):
        calls.append(("openclaw", str(action), dict(params or {})))
        raise RuntimeError('Failed to start Chrome CDP on port 18800 for profile "openclaw".')

    async def fake_dispatch_local_execute_async(raw_base_url, *, action, params):
        calls.append(("gaia", str(action), dict(params or {})))
        return runtime.DispatchResult(
            status_code=200,
            payload={"success": True, "reason_code": "ok", "transport": "gaia"},
            text="",
        )

    monkeypatch.setattr(runtime, "dispatch_openclaw_action", fake_dispatch_openclaw_action)
    monkeypatch.setattr(runtime, "_dispatch_local_execute_async", fake_dispatch_local_execute_async)
    monkeypatch.setattr(runtime, "_run_sync", lambda awaitable: asyncio.run(awaitable))

    result = runtime.execute_mcp_action(
        "http://127.0.0.1:8000",
        action="browser_snapshot",
        params={"session_id": "s1"},
    )

    assert result.status_code == 200
    assert result.payload["transport"] == "gaia"
    assert calls == [
        ("openclaw", "browser_snapshot", {"session_id": "s1"}),
        ("gaia", "browser_snapshot", {"session_id": "s1"}),
    ]
    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "openclaw"


def test_execute_mcp_action_falls_back_to_local_backend_on_openclaw_timeout_exception(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "openclaw")
    monkeypatch.setenv("GAIA_OPENCLAW_FALLBACK_BACKEND", "gaia")

    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_dispatch_openclaw_action(raw_base_url, *, action, params, timeout=None):
        calls.append(("openclaw", str(action), dict(params or {})))
        raise RuntimeError("HTTPConnectionPool(host='127.0.0.1', port=18791): Read timed out. (read timeout=12.0)")

    async def fake_dispatch_local_execute_async(raw_base_url, *, action, params):
        calls.append(("gaia", str(action), dict(params or {})))
        return runtime.DispatchResult(
            status_code=200,
            payload={"success": True, "reason_code": "ok", "transport": "gaia"},
            text="",
        )

    monkeypatch.setattr(runtime, "dispatch_openclaw_action", fake_dispatch_openclaw_action)
    monkeypatch.setattr(runtime, "_dispatch_local_execute_async", fake_dispatch_local_execute_async)
    monkeypatch.setattr(runtime, "_run_sync", lambda awaitable: asyncio.run(awaitable))

    result = runtime.execute_mcp_action(
        "http://127.0.0.1:8000",
        action="browser_snapshot",
        params={"session_id": "s1"},
    )

    assert result.status_code == 200
    assert result.payload["transport"] == "gaia"
    assert calls == [
        ("openclaw", "browser_snapshot", {"session_id": "s1"}),
        ("gaia", "browser_snapshot", {"session_id": "s1"}),
    ]
    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "openclaw"


def test_execute_mcp_action_keeps_openclaw_failure_when_fallback_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "openclaw")
    monkeypatch.setenv("GAIA_OPENCLAW_FALLBACK_BACKEND", "disabled")

    def fake_dispatch_openclaw_action(raw_base_url, *, action, params, timeout=None):
        return (
            500,
            {
                "reason_code": "action_timeout",
                "reason": 'DOM 분석 실패: Error: Failed to start Chrome CDP on port 18800 for profile "openclaw".',
            },
            'DOM 분석 실패: Error: Failed to start Chrome CDP on port 18800 for profile "openclaw".',
        )

    monkeypatch.setattr(runtime, "dispatch_openclaw_action", fake_dispatch_openclaw_action)

    result = runtime.execute_mcp_action(
        "http://127.0.0.1:8000",
        action="browser_snapshot",
        params={"session_id": "s1"},
    )

    assert result.status_code == 500
    assert result.payload["reason_code"] == "action_timeout"
    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "openclaw"


def test_openclaw_fallback_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GAIA_OPENCLAW_FALLBACK_BACKEND", raising=False)

    assert runtime._openclaw_fallback_enabled() is False


def test_execute_mcp_action_routes_capture_screenshot_to_openclaw(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "openclaw")

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
