from __future__ import annotations

import asyncio

from gaia.src.phase4 import mcp_local_dispatch_runtime as runtime


def setup_function(_func) -> None:
    runtime._clear_openclaw_runtime_unavailable()


def test_should_fallback_from_openclaw_detects_embedded_cdp_boot_failure(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_OPENCLAW_FALLBACK_BACKEND", "gaia")

    assert runtime._should_fallback_from_openclaw(
        {
            "reason_code": "action_timeout",
            "reason": 'DOM 분석 실패: Error: Failed to start Chrome CDP on port 18800 for profile "openclaw".',
        },
        "",
    )


def test_current_browser_backend_falls_back_to_gaia_after_openclaw_failure(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "openclaw")

    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "openclaw"

    runtime._mark_openclaw_runtime_unavailable()

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
    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "gaia"


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
    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "gaia"


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
    assert runtime.current_browser_backend("http://127.0.0.1:8000") == "gaia"


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
