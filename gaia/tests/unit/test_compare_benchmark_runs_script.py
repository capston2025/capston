from __future__ import annotations

import json
from pathlib import Path

from scripts.compare_benchmark_runs import compare_artifacts, main, write_markdown


def _write_artifact(
    root: Path,
    *,
    success_rate: float,
    avg_time_seconds: float,
    progress_stop_failure_rate: float,
    rows: list[dict[str, object]],
) -> Path:
    root.mkdir(parents=True)
    summary = {
        "suite_id": "demo_suite",
        "metrics": {
            "runs_total": len(rows),
            "success_rate": success_rate,
            "avg_time_seconds": avg_time_seconds,
        },
        "kpi_metrics": {
            "scenario_success_rate": success_rate,
            "progress_stop_failure_rate": progress_stop_failure_rate,
            "intervention_rate": 0.0,
            "self_recovery_rate": None,
        },
        "status_counts": {"SUCCESS": int(len(rows) * success_rate)},
        "failures": [],
    }
    (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (root / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    return root


def test_compare_artifacts_reports_speedup_and_passed_gate(tmp_path: Path) -> None:
    baseline = _write_artifact(
        tmp_path / "baseline",
        success_rate=1.0,
        avg_time_seconds=35.22,
        progress_stop_failure_rate=0.0,
        rows=[
            {"scenario_id": "A", "status": "SUCCESS", "duration_seconds": 30, "summary": {"steps": 2}},
            {"scenario_id": "B", "status": "SUCCESS", "duration_seconds": 40, "summary": {"steps": 2}},
        ],
    )
    candidate = _write_artifact(
        tmp_path / "candidate",
        success_rate=1.0,
        avg_time_seconds=26.74,
        progress_stop_failure_rate=0.0,
        rows=[
            {
                "scenario_id": "A",
                "status": "SUCCESS",
                "duration_seconds": 25,
                "summary": {"steps": 1, "reason_code_summary": {"post_action_judge_completion": 1}},
            },
            {"scenario_id": "B", "status": "SUCCESS", "duration_seconds": 28, "summary": {"steps": 1}},
        ],
    )

    report = compare_artifacts(baseline, candidate)

    assert report["gate"]["passed"] is True
    assert report["delta"]["avg_time_seconds"]["absolute"] == -8.48
    assert report["delta"]["avg_steps"]["absolute"] == -1.0
    assert report["delta"]["speedup_ratio"] == 1.3171
    assert report["candidate"]["rows"]["reason_code_summary"]["post_action_judge_completion"] == 1


def test_compare_artifacts_fails_gate_on_success_regression(tmp_path: Path) -> None:
    baseline = _write_artifact(
        tmp_path / "baseline",
        success_rate=1.0,
        avg_time_seconds=30.0,
        progress_stop_failure_rate=0.0,
        rows=[{"scenario_id": "A", "status": "SUCCESS", "duration_seconds": 30, "summary": {"steps": 1}}],
    )
    candidate = _write_artifact(
        tmp_path / "candidate",
        success_rate=0.5,
        avg_time_seconds=20.0,
        progress_stop_failure_rate=0.0,
        rows=[{"scenario_id": "A", "status": "FAIL", "duration_seconds": 20, "summary": {"steps": 1}}],
    )

    report = compare_artifacts(baseline, candidate)

    assert report["gate"]["passed"] is False
    assert report["gate"]["checks"]["success_rate_not_regressed"] is False


def test_write_markdown_includes_gate_and_metric_delta(tmp_path: Path) -> None:
    baseline = _write_artifact(
        tmp_path / "baseline",
        success_rate=1.0,
        avg_time_seconds=10.0,
        progress_stop_failure_rate=0.0,
        rows=[{"scenario_id": "A", "status": "SUCCESS", "duration_seconds": 10, "summary": {"steps": 2}}],
    )
    candidate = _write_artifact(
        tmp_path / "candidate",
        success_rate=1.0,
        avg_time_seconds=8.0,
        progress_stop_failure_rate=0.0,
        rows=[{"scenario_id": "A", "status": "SUCCESS", "duration_seconds": 8, "summary": {"steps": 1}}],
    )
    report = compare_artifacts(baseline, candidate)
    output = tmp_path / "summary.md"

    write_markdown(output, report)

    text = output.read_text(encoding="utf-8")
    assert "gate_passed: True" in text
    assert "| avg_time_seconds | 10.0 | 8.0 | -2.0 | -20.0% |" in text


def test_main_can_fail_on_regression_for_release_gate(tmp_path: Path) -> None:
    baseline = _write_artifact(
        tmp_path / "baseline",
        success_rate=1.0,
        avg_time_seconds=10.0,
        progress_stop_failure_rate=0.0,
        rows=[{"scenario_id": "A", "status": "SUCCESS", "duration_seconds": 10, "summary": {"steps": 1}}],
    )
    candidate = _write_artifact(
        tmp_path / "candidate",
        success_rate=0.0,
        avg_time_seconds=8.0,
        progress_stop_failure_rate=0.0,
        rows=[{"scenario_id": "A", "status": "FAIL", "duration_seconds": 8, "summary": {"steps": 1}}],
    )
    output = tmp_path / "comparison"

    code = main(
        [
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--output-dir",
            str(output),
            "--fail-on-regression",
        ]
    )

    assert code == 1
    assert (output / "summary.json").exists()
