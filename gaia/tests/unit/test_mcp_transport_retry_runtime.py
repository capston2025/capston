from __future__ import annotations

from gaia.src.phase4 import mcp_transport_retry_runtime as runtime
from gaia.src.phase4.mcp_local_dispatch_runtime import DispatchResult


def test_execute_mcp_action_with_recovery_retries_transport_error_without_host_recovery(monkeypatch) -> None:
    calls: list[int] = []

    def fake_execute(raw_base_url, *, action, params, timeout=None):
        del raw_base_url, action, params, timeout
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("connection refused")
        return DispatchResult(status_code=200, payload={"success": True}, text="")

    monkeypatch.setattr(runtime, "execute_mcp_action", fake_execute)

    result = runtime.execute_mcp_action_with_recovery(
        raw_base_url="http://127.0.0.1:8000",
        action="browser_snapshot",
        params={"session_id": "s1"},
        timeout=10,
        attempts=2,
        is_transport_error=lambda text: "connection refused" in text,
        recover_host=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("recover_host should not be called")),
        context="test",
    )

    assert result.status_code == 200
    assert calls == [1, 1]


def test_execute_mcp_action_with_recovery_does_not_retry_non_transport_error(monkeypatch) -> None:
    def fake_execute(raw_base_url, *, action, params, timeout=None):
        del raw_base_url, action, params, timeout
        raise RuntimeError("invalid action")

    monkeypatch.setattr(runtime, "execute_mcp_action", fake_execute)

    try:
        runtime.execute_mcp_action_with_recovery(
            raw_base_url="http://127.0.0.1:8000",
            action="browser_snapshot",
            params={"session_id": "s1"},
            timeout=10,
            attempts=2,
            is_transport_error=lambda text: "connection refused" in text,
            recover_host=lambda **_kwargs: True,
            context="test",
        )
    except RuntimeError as exc:
        assert "invalid action" in str(exc)
    else:
        raise AssertionError("expected execute_mcp_action_with_recovery to re-raise")
