from __future__ import annotations

import json

import pytest

from gaia.src import benchmark_suite_sharing as sharing


def test_build_shared_suite_url_uses_monitoring_server_host() -> None:
    assert sharing.build_shared_suite_url("http://monitor.example:9091", "story_docs") == (
        "http://monitor.example:9091/shared/suites/story_docs.json"
    )


def test_sanitize_suite_for_sharing_removes_sensitive_values() -> None:
    payload = {
        "suite_id": "demo",
        "scenarios": [
            {
                "id": "A",
                "test_data": {
                    "username": "demo",
                    "password": "secret",
                    "nested": {"api_key": "abc", "safe": "ok"},
                },
            }
        ],
        "token": "top-secret",
    }

    sanitized = sharing.sanitize_suite_for_sharing(payload)

    assert "token" not in sanitized
    test_data = sanitized["scenarios"][0]["test_data"]
    assert test_data == {"username": "demo", "nested": {"safe": "ok"}}


def test_merge_shared_suite_payload_remote_updates_by_id_and_keeps_local_unique() -> None:
    local = {
        "suite_id": "demo",
        "site": {"name": "Local"},
        "scenarios": [
            {"id": "A", "goal": "old"},
            {"id": "LOCAL", "goal": "local only"},
        ],
    }
    remote = {
        "suite_id": "demo",
        "site": {"name": "Remote", "mode": "public_browse"},
        "scenarios": [
            {"id": "A", "goal": "new"},
            {"id": "B", "goal": "added"},
        ],
    }

    merged, stats = sharing.merge_shared_suite_payload(local, remote)

    assert [row["id"] for row in merged["scenarios"]] == ["A", "B", "LOCAL"]
    assert merged["scenarios"][0]["goal"] == "new"
    assert merged["site"] == {"name": "Local", "mode": "public_browse"}
    assert stats.added == 1
    assert stats.updated == 1
    assert stats.local_only == 1


def test_upload_shared_suite_puts_sanitized_json(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

    def fake_put(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Response()

    monkeypatch.setattr(sharing.requests, "put", fake_put)

    url = sharing.upload_shared_suite(
        server="http://monitor.example:9091",
        token="team-token",
        suite_key="demo",
        suite_payload={"suite_id": "demo", "token": "secret", "scenarios": [{"id": "A"}]},
    )

    assert url == "http://monitor.example:9091/shared/suites/demo.json"
    assert captured["url"] == url
    kwargs = captured["kwargs"]
    assert kwargs["auth"] == ("gaia", "team-token")
    body = json.loads(kwargs["data"].decode("utf-8"))
    assert "token" not in body


def test_download_shared_suite_returns_json_object(monkeypatch) -> None:
    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"suite_id": "demo", "scenarios": [{"id": "A"}]}

    monkeypatch.setattr(sharing.requests, "get", lambda *args, **kwargs: _Response())

    payload = sharing.download_shared_suite(server="http://monitor.example:9091", token="token", suite_key="demo")

    assert payload["suite_id"] == "demo"
    assert payload["scenarios"] == [{"id": "A"}]


def test_download_shared_suite_raises_not_found(monkeypatch) -> None:
    class _Response:
        status_code = 404

    monkeypatch.setattr(sharing.requests, "get", lambda *args, **kwargs: _Response())

    with pytest.raises(sharing.SharedSuiteNotFound):
        sharing.download_shared_suite(server="http://monitor.example:9091", token="token", suite_key="missing")
