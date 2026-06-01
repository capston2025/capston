from __future__ import annotations

import json

from gaia.src.gui import battle_web_admin


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_reset_battle_timer_calls_timer_scope(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def fake_urlopen(request, timeout=0):  # noqa: ANN001
        calls.append((request.full_url, request.get_method(), dict(request.header_items())))
        return _FakeResponse({"reset": {"sessionId": "battle-live"}})

    monkeypatch.setattr(battle_web_admin.urllib.request, "urlopen", fake_urlopen)

    result = battle_web_admin.reset_battle_timer(site_url="https://battle.example/", session_id="battle-live", token="tok")

    assert result["reset"]["sessionId"] == "battle-live"
    assert calls[0][0] == "https://battle.example/api/session?sessionId=battle-live&scope=timer"
    assert calls[0][1] == "DELETE"
    assert calls[0][2]["Authorization"] == "Bearer tok"


def test_delete_battle_record_targets_single_record(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_urlopen(request, timeout=0):  # noqa: ANN001
        calls.append((request.full_url, request.get_method()))
        return _FakeResponse({"deleted": {"recordId": "rec-1"}, "records": []})

    monkeypatch.setattr(battle_web_admin.urllib.request, "urlopen", fake_urlopen)

    result = battle_web_admin.delete_battle_record(
        site_url="https://battle.example",
        session_id="battle-live",
        record_id="rec-1",
    )

    assert result["deleted"]["recordId"] == "rec-1"
    assert calls == [("https://battle.example/api/records?sessionId=battle-live&recordId=rec-1", "DELETE")]
