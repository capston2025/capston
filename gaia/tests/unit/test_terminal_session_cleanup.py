from __future__ import annotations

from types import SimpleNamespace

from gaia.terminal import _capture_final_evidence_attachment, run_chat_terminal_once
from gaia.src.phase4.goal_driven import TestGoal


def test_run_chat_terminal_once_closes_browser_session(monkeypatch) -> None:
    events: list[str] = []

    class FakeAgent:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._reason_code_counts = {}
            self._last_goal_completion_source = "judge"
            self._last_container_source_summary = {}
            self._active_scoped_container_ref = ""
            self._last_exec_result = SimpleNamespace(state_change={})

        def execute_goal(self, goal: TestGoal) -> SimpleNamespace:
            return SimpleNamespace(
                goal_name=goal.name,
                success=True,
                final_reason="ok",
                total_steps=1,
                duration_seconds=0.1,
                steps_taken=[],
            )

        def _analyze_dom(self) -> list[object]:
            return []

    monkeypatch.setattr("gaia.terminal.GoalDrivenAgent", FakeAgent)
    monkeypatch.setattr(
        "gaia.terminal.run_validation_rail",
        lambda target_url, run_id: {"summary": {}, "cases": [], "artifacts": {}},
    )
    monkeypatch.setattr("gaia.terminal.derive_achieved_signals", lambda *args, **kwargs: [])
    monkeypatch.setattr("gaia.terminal.is_low_information_screenshot", lambda *_args, **_kwargs: False)

    class _ScreenshotResponse:
        status_code = 200
        payload = {
            "screenshot": "proof-image",
            "mime_type": "image/png",
            "saved_path": "/tmp/final-proof.png",
            "current_url": "https://example.com/done",
        }

    def fake_execute_mcp_action(*args, **kwargs):
        del args, kwargs
        events.append("screenshot")
        return _ScreenshotResponse()

    monkeypatch.setattr("gaia.terminal.execute_mcp_action", fake_execute_mcp_action)
    monkeypatch.setattr(
        "gaia.terminal.close_mcp_session",
        lambda raw_base_url, *, session_id, timeout=None: events.append(f"close:{session_id}"),
    )

    goal = TestGoal(
        id="TC001",
        name="홈 확인",
        description="홈 화면이 보이는지 확인",
        success_criteria=["홈 화면"],
        start_url="https://example.com/",
    )

    code, summary = run_chat_terminal_once(
        url="https://example.com/",
        query="홈 화면이 보이는지 확인",
        session_id="terminal-cleanup-test",
        prepared_goal=goal,
    )

    assert code == 0
    assert summary["final_status"] == "SUCCESS"
    assert events == ["screenshot", "close:terminal-cleanup-test"]
    assert summary["attachments"][0]["label"] == "최종 증거 화면"
    assert summary["attachments"][0]["path"] == "/tmp/final-proof.png"


def test_capture_final_evidence_targets_active_ref(monkeypatch) -> None:
    captured_params: list[dict[str, object]] = []

    class _ScreenshotResponse:
        status_code = 200
        payload = {
            "screenshot": "targeted-proof-image",
            "mime_type": "image/png",
            "saved_path": "/tmp/targeted-proof.png",
            "current_url": "https://example.com/done",
        }

    def fake_execute_mcp_action(*_args, **kwargs):
        captured_params.append(dict(kwargs.get("params") or {}))
        return _ScreenshotResponse()

    monkeypatch.setattr("gaia.terminal.execute_mcp_action", fake_execute_mcp_action)
    monkeypatch.setattr("gaia.terminal.is_low_information_screenshot", lambda *_args, **_kwargs: False)

    attachment = _capture_final_evidence_attachment("session-1", target_ref="e42")

    assert captured_params == [
        {
            "session_id": "session-1",
            "full_page": False,
            "type": "png",
            "ref": "e42",
        }
    ]
    assert attachment is not None
    assert attachment["label"] == "최종 증거 영역"
    assert attachment["targeted"] is True
    assert attachment["targetRef"] == "e42"
    assert attachment["path"] == "/tmp/targeted-proof.png"


def test_capture_final_evidence_falls_back_when_target_ref_fails(monkeypatch) -> None:
    captured_params: list[dict[str, object]] = []

    class _FailedResponse:
        status_code = 400
        payload = {"error": "ref not found"}

    class _ScreenshotResponse:
        status_code = 200
        payload = {
            "screenshot": "fallback-proof-image",
            "mime_type": "image/png",
            "saved_path": "/tmp/fallback-proof.png",
            "current_url": "https://example.com/done",
        }

    responses = [_FailedResponse(), _ScreenshotResponse()]

    def fake_execute_mcp_action(*_args, **kwargs):
        captured_params.append(dict(kwargs.get("params") or {}))
        return responses.pop(0)

    monkeypatch.setattr("gaia.terminal.execute_mcp_action", fake_execute_mcp_action)
    monkeypatch.setattr("gaia.terminal.is_low_information_screenshot", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("gaia.terminal.time.sleep", lambda *_args, **_kwargs: None)

    attachment = _capture_final_evidence_attachment("session-1", target_ref="stale-ref")

    assert captured_params == [
        {
            "session_id": "session-1",
            "full_page": False,
            "type": "png",
            "ref": "stale-ref",
        },
        {
            "session_id": "session-1",
            "full_page": False,
            "type": "png",
        },
    ]
    assert attachment is not None
    assert attachment["label"] == "최종 증거 화면"
    assert "targeted" not in attachment
    assert attachment["path"] == "/tmp/fallback-proof.png"
