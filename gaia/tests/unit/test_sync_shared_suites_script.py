from __future__ import annotations

import json
from pathlib import Path

from scripts import sync_shared_suites


def test_infer_suite_key_prefers_suite_id() -> None:
    assert sync_shared_suites.infer_suite_key(Path("custom_story_docs_suite.json"), {"suite_id": "story_docs_public_v1"}) == "story_docs"


def test_pull_merges_remote_suite(tmp_path: Path, monkeypatch) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps({"suite_id": "demo_public_v1", "scenarios": [{"id": "LOCAL", "goal": "local"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_shared_suites, "load_monitoring_config", lambda: {"server": "http://monitor:9091", "token": "t"})
    monkeypatch.setattr(
        sync_shared_suites,
        "download_shared_suite",
        lambda **kwargs: {"suite_id": "demo_public_v1", "scenarios": [{"id": "REMOTE", "goal": "remote"}]},
    )

    result = sync_shared_suites.main(["pull", "--suite", str(suite_path)])

    assert result == 0
    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    assert [row["id"] for row in payload["scenarios"]] == ["REMOTE", "LOCAL"]


def test_push_rejects_empty_suite(tmp_path: Path, monkeypatch) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps({"suite_id": "demo_public_v1", "scenarios": []}), encoding="utf-8")
    monkeypatch.setattr(sync_shared_suites, "load_monitoring_config", lambda: {"server": "http://monitor:9091", "token": "t"})

    result = sync_shared_suites.main(["push", "--suite", str(suite_path)])

    assert result == 1
