from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from scripts import run_kpi_benchmark_pack as kpi_pack
from scripts.run_kpi_benchmark_pack import (
    MIN_BENCHMARK_TIMEOUT_SEC,
    _build_run_suite_command,
    _compute_pack_kpis,
    _effective_timeout_cap,
    _is_blocked_user_action,
    _load_suite_manifest,
    _resolve_suite_paths,
    _try_push_pack_metrics,
)


def test_effective_timeout_cap_enforces_minimum_budget() -> None:
    assert _effective_timeout_cap(180) == MIN_BENCHMARK_TIMEOUT_SEC
    assert _effective_timeout_cap(600) == MIN_BENCHMARK_TIMEOUT_SEC
    assert _effective_timeout_cap(900) == 900


def test_load_suite_manifest_resolves_suite_paths_from_manifest_dir(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text("{}", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"suites": [{"suite_path": "suite.json"}]}),
        encoding="utf-8",
    )

    assert _load_suite_manifest(manifest_path) == [suite_path.resolve()]


def test_resolve_suite_paths_accepts_explicit_suites_and_manifest(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.json"
    manifest_suite = tmp_path / "manifest_suite.json"
    explicit.write_text("{}", encoding="utf-8")
    manifest_suite.write_text("{}", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"suites": [{"suite_path": "manifest_suite.json"}]}),
        encoding="utf-8",
    )

    assert _resolve_suite_paths(
        suite_args=[str(explicit)],
        suite_manifest=str(manifest_path),
    ) == [explicit.resolve(), manifest_suite.resolve()]


def test_build_run_suite_command_forwards_push_metrics(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"

    without_push = _build_run_suite_command(
        suite_path,
        repeats=1,
        timeout_cap=600,
        session_prefix="external-public",
        push_metrics=False,
    )
    with_push = _build_run_suite_command(
        suite_path,
        repeats=1,
        timeout_cap=600,
        session_prefix="external-public",
        push_metrics=True,
    )

    assert "--push-metrics" not in without_push
    assert with_push[-1] == "--push-metrics"


def test_try_push_pack_metrics_uploads_final_pack_artifact(tmp_path: Path, monkeypatch) -> None:
    monitoring_config = tmp_path / "monitoring.json"
    monitoring_config.write_text("{}", encoding="utf-8")
    push_script = tmp_path / "push_metrics.py"
    push_script.write_text("# test", encoding="utf-8")
    pack_dir = tmp_path / "kpi_pack_test"
    pack_dir.mkdir()
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append([str(part) for part in cmd])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(kpi_pack, "MONITORING_CONFIG", monitoring_config)
    monkeypatch.setattr(kpi_pack, "PUSH_METRICS", push_script)
    monkeypatch.setattr(kpi_pack.subprocess, "run", fake_run)

    _try_push_pack_metrics(pack_dir)

    assert calls == [
        [
            str(kpi_pack.sys.executable),
            str(push_script),
            "--suite-dir",
            str(pack_dir),
            "--no-share-suite",
        ]
    ]


def test_pack_kpis_isolate_korean_captcha_gate_from_primary_success_rate() -> None:
    rows = [
        {"suite_id": "ok_suite", "scenario_id": "OK_001", "status": "SUCCESS", "duration_seconds": 4.0},
        {
            "suite_id": "naver_shopping_public_v2",
            "scenario_id": "NAVERSHOP_002_SEARCH_PRODUCT",
            "status": "FAIL",
            "reason": "NAVER 보안 확인 캡차 화면입니다. 보안문자 정답이 필요합니다.",
            "duration_seconds": 9.0,
            "summary": {"reason_code_summary": {"wait_repeated": 2}},
        },
    ]

    assert _is_blocked_user_action(rows[1])
    kpis = _compute_pack_kpis(rows, repeats=1)

    assert kpis["scenario_success_rate"] == 0.5
    assert kpis["primary_success_rate"] == 1.0
    assert kpis["counts"]["blocked"] == 1
    assert kpis["counts"]["primary_runs"] == 1
