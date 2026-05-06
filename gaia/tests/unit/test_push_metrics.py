from __future__ import annotations

import json
from pathlib import Path

from scripts import push_metrics


def test_gauge_escapes_prometheus_label_values() -> None:
    lines = push_metrics._gauge(
        "gaia_test_metric",
        "test metric",
        1,
        {
            "suite_id": 'suite"bad',
            "scenario_id": "line\nbreak",
            "site": r"path\\value",
        },
    )

    metric = lines[-1]
    assert 'suite_id="suite\\"bad"' in metric
    assert 'scenario_id="line\\nbreak"' in metric
    assert 'site="path\\\\\\\\value"' in metric


def test_scenario_metrics_drop_sensitive_high_cardinality_labels() -> None:
    summary = {
        "suite_id": "auth_suite",
        "site": {
            "name": "Sensitive Site",
            "base_url": "https://example.test/private/path",
        },
        "started_at": "2026-05-04T12:34:56+09:00",
        "model": "gpt-5.5",
        "provider": "openai",
    }
    results = [
        {
            "scenario_id": "AUTH_001",
            "goal": "로그인해서 private 내용을 확인해줘",
            "reason": "user@example.test 비밀번호가 필요함",
            "status": "FAIL",
            "duration_seconds": 3.5,
            "summary": {"goal_completion_source": "auth_gate"},
            "model": "gpt-5.5",
            "provider": "openai",
        }
    ]

    metrics = push_metrics.build_scenario_metrics(summary, results)

    assert "last_reason" not in metrics
    assert "started_at=" not in metrics
    assert "site_url" not in metrics
    assert "goal=" not in metrics
    assert "user@example.test" not in metrics
    assert "private/path" not in metrics
    assert "gaia_scenario_last_run_timestamp_seconds" in metrics
    assert 'completion="auth_gate"' in metrics


def test_push_suite_dir_uses_stable_suite_instance(tmp_path, monkeypatch) -> None:
    suite_dir = tmp_path / "auth_suite_20260504_123456"
    suite_dir.mkdir()
    (suite_dir / "summary.json").write_text(
        json.dumps(
            {
                "suite_id": "auth_suite",
                "site": {"name": "Sensitive Site"},
                "model": "gpt-5.5",
                "provider": "openai",
                "metrics": {"runs_total": 1},
                "kpi_metrics": {"targets": {}, "counts": {}},
                "status_counts": {"SUCCESS": 1},
            }
        ),
        encoding="utf-8",
    )
    (suite_dir / "results.json").write_text("[]", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_push(metrics_text: str, instance: str, gateway_url: str, token: str | None) -> bool:
        captured["metrics_text"] = metrics_text
        captured["instance"] = instance
        captured["gateway_url"] = gateway_url
        captured["token"] = token
        return True

    monkeypatch.setattr(push_metrics, "push_to_gateway", fake_push)

    assert push_metrics.push_suite_dir(suite_dir, "http://monitor.example", "secret") is True
    assert captured["instance"] == "auth_suite"


def test_push_suite_dir_can_share_suite_definition(tmp_path, monkeypatch) -> None:
    suite_dir = tmp_path / "auth_suite_20260504_123456"
    suite_dir.mkdir()
    suite_json = tmp_path / "auth_suite.json"
    suite_json.write_text(
        json.dumps({"suite_id": "auth_suite_public_v1", "scenarios": [{"id": "AUTH_001", "goal": "로그인"}]}),
        encoding="utf-8",
    )
    (suite_dir / "summary.json").write_text(
        json.dumps(
            {
                "suite_id": "auth_suite_public_v1",
                "site": {"name": "Sensitive Site"},
                "model": "gpt-5.5",
                "provider": "openai",
                "metrics": {"runs_total": 1},
                "kpi_metrics": {"targets": {}, "counts": {}},
                "status_counts": {"SUCCESS": 1},
            }
        ),
        encoding="utf-8",
    )
    (suite_dir / "results.json").write_text("[]", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(push_metrics, "push_to_gateway", lambda *args, **kwargs: True)

    def fake_upload(**kwargs):
        captured.update(kwargs)
        return "http://monitor.example/shared/suites/auth_suite.json"

    monkeypatch.setattr(push_metrics, "upload_shared_suite", fake_upload)

    assert (
        push_metrics.push_suite_dir(
            suite_dir,
            "http://monitor.example",
            "secret",
            suite_json_path=suite_json,
            share_suite=True,
        )
        is True
    )
    assert captured["server"] == "http://monitor.example"
    assert captured["token"] == "secret"
    assert captured["suite_key"] == "auth_suite"
    assert captured["suite_payload"]["scenarios"] == [{"id": "AUTH_001", "goal": "로그인"}]
