from __future__ import annotations

import json
from pathlib import Path

from gaia.src.gui.benchmark_mode import (
    BENCHMARK_PRESETS,
    build_benchmark_catalog,
    load_benchmark_registry,
    override_suite_urls,
    render_benchmark_reports_html,
    save_benchmark_registry,
    scan_benchmark_reports,
    upsert_benchmark_site_url,
)


def test_load_and_save_benchmark_registry_round_trip(tmp_path: Path) -> None:
    registry_path = tmp_path / "benchmark_mode_targets.json"

    initial = load_benchmark_registry(registry_path)
    assert initial == {"sites": {}}

    payload = upsert_benchmark_site_url(initial, "inu_timetable", "https://example.com/app")
    saved_path = save_benchmark_registry(payload, registry_path)

    assert saved_path == registry_path
    assert load_benchmark_registry(registry_path) == payload


def test_upsert_benchmark_site_url_prioritizes_latest_url() -> None:
    payload = {"sites": {"inu_timetable": {"default_url": "https://old.example", "urls": ["https://old.example"]}}}

    updated = upsert_benchmark_site_url(payload, "inu_timetable", "https://new.example")

    site = updated["sites"]["inu_timetable"]
    assert site["default_url"] == "https://new.example"
    assert site["urls"][0] == "https://new.example"
    assert "https://old.example" in site["urls"]


def test_build_benchmark_catalog_includes_requested_presets_and_saved_urls() -> None:
    payload = {
        "sites": {
            "inu_timetable": {
                "default_url": "https://bench.example",
                "urls": ["https://bench.example", "https://backup.example"],
            }
        }
    }

    catalog = build_benchmark_catalog(payload)

    assert len(catalog) == len(BENCHMARK_PRESETS)
    inu = next(item for item in catalog if item["key"] == "inu_timetable")
    wiki = next(item for item in catalog if item["key"] == "wikipedia")
    assert inu["default_url"] == "https://bench.example"
    assert inu["urls"][:2] == ["https://bench.example", "https://backup.example"]
    assert inu["suite_available"] is True
    assert wiki["suite_available"] is True


def test_benchmark_presets_include_moneytoring_and_exclude_mdn() -> None:
    keys = {preset.key for preset in BENCHMARK_PRESETS}

    assert "moneytoring" in keys
    assert "mdn" not in keys


def test_override_suite_urls_updates_site_and_preserves_relative_paths() -> None:
    suite_payload = {
        "site": {"base_url": "https://old.example"},
        "scenarios": [
            {"id": "A", "url": "https://old.example/page-a"},
            {"id": "B", "url": "https://old.example/page-b"},
        ],
    }

    overridden = override_suite_urls(suite_payload, "https://new.example/base")

    assert overridden["site"]["base_url"] == "https://new.example/base"
    assert [row["url"] for row in overridden["scenarios"]] == [
        "https://new.example/base/page-a",
        "https://new.example/base/page-b",
    ]


def test_scan_benchmark_reports_filters_by_selected_url_host(tmp_path: Path) -> None:
    bench_root = tmp_path / "artifacts" / "benchmarks"
    target_dir = bench_root / "run_1"
    other_dir = bench_root / "run_2"
    target_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)

    (target_dir / "summary.json").write_text(
        json.dumps(
            {
                "started_at": "2026-04-11 12:00:00",
                "site": {"base_url": "https://inuu-timetable.vercel.app/"},
                "status_counts": {"SUCCESS": 1, "FAIL": 0},
                "metrics": {"success_rate": 1.0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (target_dir / "results.json").write_text(
        json.dumps([{"scenario_id": "INUU_001", "status": "SUCCESS", "reason": "ok"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (other_dir / "summary.json").write_text(
        json.dumps(
            {
                "started_at": "2026-04-11 12:05:00",
                "site": {"base_url": "https://ko.wikipedia.org/"},
                "status_counts": {"SUCCESS": 1, "FAIL": 0},
                "metrics": {"success_rate": 1.0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    reports = scan_benchmark_reports(
        workspace_root=tmp_path,
        site_key="inu_timetable",
        selected_url="https://inuu-timetable.vercel.app/custom",
    )

    assert len(reports) == 1
    assert reports[0]["summary"]["site"]["base_url"] == "https://inuu-timetable.vercel.app/"


def test_render_benchmark_reports_html_groups_by_scenario_and_surfaces_quant_metrics() -> None:
    html_doc = render_benchmark_reports_html(
        site_label="INU TIMETABLE",
        selected_url="https://inuu-timetable.vercel.app/",
        reports=[
            {
                "artifact_dir": "/tmp/run_1",
                "summary": {
                    "started_at": "2026-04-11 12:00:00",
                    "provider": "openai",
                    "model": "gpt-5.4",
                },
                "results": [
                    {
                        "scenario_id": "INUU_001",
                        "goal": "홈 화면 확인",
                        "status": "SUCCESS",
                        "reason": "ok",
                        "duration_seconds": 12.5,
                        "summary": {"goal_completion_source": "judge"},
                    },
                    {
                        "scenario_id": "INUU_002",
                        "goal": "검색 결과 변화",
                        "status": "FAIL",
                        "reason": "timeout",
                        "duration_seconds": 20.0,
                        "summary": {"goal_completion_source": "judge"},
                    },
                ],
            },
            {
                "artifact_dir": "/tmp/run_2",
                "summary": {
                    "started_at": "2026-04-12 08:00:00",
                    "provider": "gemini",
                    "model": "gemini-2.5-pro",
                },
                "results": [
                    {
                        "scenario_id": "INUU_001",
                        "goal": "홈 화면 확인",
                        "status": "SUCCESS",
                        "reason": "ok-again",
                        "duration_seconds": 10.0,
                        "summary": {"goal_completion_source": "expected_signals"},
                    },
                ],
            }
        ],
    )

    assert "INU TIMETABLE" in html_doc
    assert "INUU_001" in html_doc
    assert "timeout" in html_doc
    assert "Latest Sec" in html_doc
    assert "Median Sec" in html_doc
    assert "10.00s" in html_doc
    assert "12.50s" in html_doc
    assert "11.25s" in html_doc
    assert "expected_signals" in html_doc
    assert "/tmp/run_2" in html_doc
    assert "openai / gpt-5.4" in html_doc
    assert "gemini / gemini-2.5-pro" in html_doc

    empty_doc = render_benchmark_reports_html(
        site_label="MDN",
        selected_url="https://developer.mozilla.org/",
        reports=[],
    )
    assert "아직 실행 이력이 없습니다" in empty_doc
