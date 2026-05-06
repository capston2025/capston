#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "benchmarks" / "comparisons"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_artifact_dir(path: Path) -> Path:
    candidate = path.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    if not (candidate / "summary.json").exists():
        raise FileNotFoundError(f"summary.json not found under {candidate}")
    if not (candidate / "results.json").exists():
        raise FileNotFoundError(f"results.json not found under {candidate}")
    return candidate


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _load_artifact(path: Path) -> Dict[str, Any]:
    artifact_dir = _resolve_artifact_dir(path)
    summary = _load_json(artifact_dir / "summary.json")
    results = _load_json(artifact_dir / "results.json")
    if not isinstance(summary, dict):
        raise ValueError(f"summary.json must contain an object: {artifact_dir}")
    if not isinstance(results, list):
        raise ValueError(f"results.json must contain an array: {artifact_dir}")
    return {
        "artifact_dir": str(artifact_dir),
        "summary": summary,
        "results": results,
    }


def _summary_metrics(summary: Dict[str, Any]) -> Dict[str, Any]:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    kpis = summary.get("kpi_metrics") if isinstance(summary.get("kpi_metrics"), dict) else {}
    return {
        "suite_id": str(summary.get("suite_id") or ""),
        "runs_total": _safe_int(metrics.get("runs_total")),
        "success_rate": _safe_float(metrics.get("success_rate")),
        "avg_time_seconds": _safe_float(metrics.get("avg_time_seconds")),
        "scenario_success_rate": _safe_float(kpis.get("scenario_success_rate")),
        "progress_stop_failure_rate": _safe_float(kpis.get("progress_stop_failure_rate")),
        "intervention_rate": _safe_float(kpis.get("intervention_rate")),
        "self_recovery_rate": kpis.get("self_recovery_rate"),
        "status_counts": summary.get("status_counts") if isinstance(summary.get("status_counts"), dict) else {},
        "failures": summary.get("failures") if isinstance(summary.get("failures"), list) else [],
    }


def _row_steps(row: Dict[str, Any]) -> int:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    return _safe_int(summary.get("steps"))


def _row_reason_codes(row: Dict[str, Any]) -> Dict[str, int]:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    raw = summary.get("reason_code_summary") if isinstance(summary.get("reason_code_summary"), dict) else {}
    return {str(key): _safe_int(value) for key, value in raw.items()}


def _aggregate_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    row_list = [row for row in rows if isinstance(row, dict)]
    total_duration = sum(_safe_float(row.get("duration_seconds")) for row in row_list)
    total_steps = sum(_row_steps(row) for row in row_list)
    reason_codes: Dict[str, int] = {}
    per_scenario: Dict[str, Dict[str, Any]] = {}
    for row in row_list:
        scenario_id = str(row.get("scenario_id") or "unknown")
        bucket = per_scenario.setdefault(
            scenario_id,
            {
                "runs": 0,
                "success": 0,
                "duration_seconds": 0.0,
                "steps": 0,
                "statuses": {},
            },
        )
        bucket["runs"] += 1
        status = str(row.get("status") or "FAIL").upper()
        if status == "SUCCESS":
            bucket["success"] += 1
        bucket["duration_seconds"] += _safe_float(row.get("duration_seconds"))
        bucket["steps"] += _row_steps(row)
        statuses = bucket["statuses"]
        statuses[status] = _safe_int(statuses.get(status)) + 1
        for code, count in _row_reason_codes(row).items():
            reason_codes[code] = _safe_int(reason_codes.get(code)) + count

    for bucket in per_scenario.values():
        runs = max(1, _safe_int(bucket.get("runs")))
        bucket["success_rate"] = round(_safe_int(bucket.get("success")) / runs, 4)
        bucket["avg_duration_seconds"] = round(_safe_float(bucket.get("duration_seconds")) / runs, 2)
        bucket["avg_steps"] = round(_safe_float(bucket.get("steps")) / runs, 2)
        bucket.pop("duration_seconds", None)
        bucket.pop("steps", None)

    count = max(1, len(row_list))
    return {
        "runs_total": len(row_list),
        "avg_duration_seconds": round(total_duration / count, 2) if row_list else 0.0,
        "avg_steps": round(total_steps / count, 2) if row_list else 0.0,
        "reason_code_summary": dict(sorted(reason_codes.items())),
        "per_scenario": dict(sorted(per_scenario.items())),
    }


def _delta(candidate: float, baseline: float) -> Dict[str, Any]:
    absolute = round(candidate - baseline, 4)
    if baseline == 0:
        relative = None
    else:
        relative = round((candidate - baseline) / baseline, 4)
    return {
        "baseline": baseline,
        "candidate": candidate,
        "absolute": absolute,
        "relative": relative,
    }


def _speedup_ratio(baseline_seconds: float, candidate_seconds: float) -> float | None:
    if candidate_seconds <= 0:
        return None
    return round(baseline_seconds / candidate_seconds, 4)


def _build_gate(summary_delta: Dict[str, Any], *, max_success_regression: float = 0.0) -> Dict[str, Any]:
    success_delta = _safe_float(summary_delta["success_rate"]["absolute"])
    stop_delta = _safe_float(summary_delta["progress_stop_failure_rate"]["absolute"])
    intervention_delta = _safe_float(summary_delta["intervention_rate"]["absolute"])
    avg_time_delta = _safe_float(summary_delta["avg_time_seconds"]["absolute"])
    passed = bool(
        success_delta >= -abs(max_success_regression)
        and stop_delta <= 0
        and intervention_delta <= 0
        and avg_time_delta <= 0
    )
    return {
        "passed": passed,
        "checks": {
            "success_rate_not_regressed": success_delta >= -abs(max_success_regression),
            "progress_stop_not_regressed": stop_delta <= 0,
            "intervention_not_regressed": intervention_delta <= 0,
            "avg_time_not_regressed": avg_time_delta <= 0,
        },
    }


def compare_artifacts(
    baseline_path: Path,
    candidate_path: Path,
    *,
    max_success_regression: float = 0.0,
) -> Dict[str, Any]:
    baseline = _load_artifact(baseline_path)
    candidate = _load_artifact(candidate_path)
    baseline_metrics = _summary_metrics(baseline["summary"])
    candidate_metrics = _summary_metrics(candidate["summary"])
    baseline_rows = _aggregate_rows(baseline["results"])
    candidate_rows = _aggregate_rows(candidate["results"])

    metric_delta = {
        "success_rate": _delta(candidate_metrics["success_rate"], baseline_metrics["success_rate"]),
        "scenario_success_rate": _delta(
            candidate_metrics["scenario_success_rate"],
            baseline_metrics["scenario_success_rate"],
        ),
        "avg_time_seconds": _delta(
            candidate_metrics["avg_time_seconds"],
            baseline_metrics["avg_time_seconds"],
        ),
        "progress_stop_failure_rate": _delta(
            candidate_metrics["progress_stop_failure_rate"],
            baseline_metrics["progress_stop_failure_rate"],
        ),
        "intervention_rate": _delta(
            candidate_metrics["intervention_rate"],
            baseline_metrics["intervention_rate"],
        ),
        "avg_steps": _delta(
            _safe_float(candidate_rows["avg_steps"]),
            _safe_float(baseline_rows["avg_steps"]),
        ),
    }
    metric_delta["speedup_ratio"] = _speedup_ratio(
        baseline_metrics["avg_time_seconds"],
        candidate_metrics["avg_time_seconds"],
    )
    return {
        "schema_version": "gaia.benchmark.compare.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "baseline": {
            "artifact_dir": baseline["artifact_dir"],
            "metrics": baseline_metrics,
            "rows": baseline_rows,
        },
        "candidate": {
            "artifact_dir": candidate["artifact_dir"],
            "metrics": candidate_metrics,
            "rows": candidate_rows,
        },
        "delta": metric_delta,
        "gate": _build_gate(metric_delta, max_success_regression=max_success_regression),
    }


def _format_relative(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{round(float(value) * 100, 2)}%"


def write_markdown(path: Path, report: Dict[str, Any]) -> None:
    delta = report["delta"]
    gate = report["gate"]
    lines: List[str] = [
        "# Benchmark comparison",
        "",
        f"- generated_at: {report['generated_at']}",
        f"- gate_passed: {gate['passed']}",
        f"- baseline: {report['baseline']['artifact_dir']}",
        f"- candidate: {report['candidate']['artifact_dir']}",
        "",
        "## Metric Delta",
        "",
        "| metric | baseline | candidate | absolute | relative |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key in (
        "success_rate",
        "scenario_success_rate",
        "avg_time_seconds",
        "progress_stop_failure_rate",
        "intervention_rate",
        "avg_steps",
    ):
        item = delta[key]
        lines.append(
            "| {key} | {baseline} | {candidate} | {absolute} | {relative} |".format(
                key=key,
                baseline=item["baseline"],
                candidate=item["candidate"],
                absolute=item["absolute"],
                relative=_format_relative(item["relative"]),
            )
        )
    lines.extend(
        [
            "",
            f"- speedup_ratio: {delta.get('speedup_ratio')}",
            "",
            "## Gate Checks",
            "",
        ]
    )
    for key, value in gate["checks"].items():
        lines.append(f"- {key}: {value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two GAIA benchmark artifact directories.")
    parser.add_argument("--baseline", required=True, help="Baseline artifact directory or summary.json path.")
    parser.add_argument("--candidate", required=True, help="Candidate artifact directory or summary.json path.")
    parser.add_argument("--output-dir", help="Directory to write comparison summary.json and summary.md.")
    parser.add_argument("--max-success-regression", type=float, default=0.0)
    parser.add_argument("--fail-on-regression", action="store_true", help="Exit 1 when the comparison gate fails.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = compare_artifacts(
        Path(args.baseline),
        Path(args.candidate),
        max_success_regression=float(args.max_success_regression),
    )
    out_dir = Path(args.output_dir).resolve() if args.output_dir else DEFAULT_OUTPUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(out_dir / "summary.md", report)
    print(json.dumps({"artifact_dir": str(out_dir), "gate": report["gate"], "delta": report["delta"]}, ensure_ascii=False))
    if bool(args.fail_on_regression) and not bool(report["gate"]["passed"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
