#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "benchmarks"
RUN_SINGLE = ROOT / "scripts" / "run_goal_benchmark.py"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_artifact_dir(after_ts: float) -> Path:
    candidates = [
        p for p in ARTIFACT_ROOT.iterdir()
        if p.is_dir() and p.stat().st_mtime >= after_ts and (p / "summary.json").exists()
    ]
    if not candidates:
        raise FileNotFoundError("benchmark artifact directory not found")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _summary_reason_code_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    summary = row.get("summary")
    if not isinstance(summary, dict):
        return {}
    data = summary.get("reason_code_summary")
    return data if isinstance(data, dict) else {}


def _is_blocked_user_action(row: Dict[str, Any]) -> bool:
    summary = row.get("summary")
    if isinstance(summary, dict) and str(summary.get("final_status") or "").strip().upper() == "BLOCKED_USER_ACTION":
        return True
    reason = str(row.get("reason") or "")
    return "사용자 개입" in reason or "captcha" in reason.lower() or "login required" in reason.lower()


def _is_progress_stop_failure(row: Dict[str, Any]) -> bool:
    if str(row.get("status") or "").strip().upper() == "SUCCESS":
        return False
    if _is_blocked_user_action(row):
        return False
    reason = str(row.get("reason") or "").lower()
    stop_markers = (
        "benchmark_timeout",
        "timeout",
        "중단",
        "반복",
        "stuck",
        "no progress",
        "observe_no_dom",
        "화면 상태가 반복되어",
    )
    if any(marker in reason for marker in stop_markers):
        return True
    rc_summary = _summary_reason_code_summary(row)
    return any(
        str(code or "").strip().lower()
        in {
            "blocked_timeout",
            "clarification_timeout",
            "user_intervention_missing",
            "dom_snapshot_retry_exhausted",
            "observe_no_dom",
        }
        for code in rc_summary.keys()
    )


def _has_recovery_event(row: Dict[str, Any]) -> bool:
    rc_summary = _summary_reason_code_summary(row)
    recovery_prefixes = (
        "stale_",
        "resnapshot",
        "fallback_",
        "request_exception",
        "auth_submit_timeout_recovered",
        "dom_snapshot_retry",
    )
    for code in rc_summary.keys():
        normalized = str(code or "").strip().lower()
        if any(normalized.startswith(prefix) for prefix in recovery_prefixes):
            return True
    return False


def _compute_pack_kpis(rows: List[Dict[str, Any]], repeats: int) -> Dict[str, Any]:
    total = max(1, len(rows))
    success_count = sum(1 for row in rows if str(row.get("status") or "").strip().upper() == "SUCCESS")
    blocked_count = sum(1 for row in rows if _is_blocked_user_action(row))
    stop_failure_count = sum(1 for row in rows if _is_progress_stop_failure(row))
    recovery_rows = [row for row in rows if _has_recovery_event(row)]
    recovery_success = sum(
        1 for row in recovery_rows if str(row.get("status") or "").strip().upper() == "SUCCESS"
    )

    per_case: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        scenario_key = f"{row.get('suite_id')}::{row.get('scenario_id')}"
        per_case[scenario_key].append(str(row.get("status") or "FAIL").upper())

    reproducible = 0
    observed = 0
    flaky = 0
    if repeats > 1:
        for statuses in per_case.values():
            if len(statuses) != repeats:
                continue
            observed += 1
            uniq = set(statuses)
            if uniq == {"SUCCESS"}:
                reproducible += 1
            if "SUCCESS" in uniq and len(uniq) > 1:
                flaky += 1

    avg_time = round(statistics.mean(float(row.get("duration_seconds") or 0.0) for row in rows), 2)
    return {
        "scenario_success_rate": round(success_count / total, 4),
        "reproducibility_rate": round((reproducible / observed), 4) if observed else None,
        "progress_stop_failure_rate": round(stop_failure_count / total, 4),
        "self_recovery_rate": round((recovery_success / len(recovery_rows)), 4) if recovery_rows else None,
        "intervention_rate": round(blocked_count / total, 4),
        "avg_time_seconds": avg_time,
        "flaky_rate": round((flaky / observed), 4) if observed else None,
        "counts": {
          "runs_total": len(rows),
          "success": success_count,
          "blocked": blocked_count,
          "progress_stop_failures": stop_failure_count,
          "recovery_runs": len(recovery_rows),
          "recovery_success": recovery_success
        }
    }


def _run_suite(
    suite_path: Path,
    *,
    repeats: int,
    timeout_cap: int,
    session_prefix: str,
    env: Dict[str, str],
) -> Dict[str, Any]:
    started = time.time()
    before = time.time()
    cmd = [
        sys.executable,
        str(RUN_SINGLE),
        "--suite",
        str(suite_path),
        "--repeats",
        str(repeats),
        "--timeout-cap",
        str(timeout_cap),
        "--session-prefix",
        session_prefix,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    artifact_dir = _latest_artifact_dir(before)
    summary = _load_json(artifact_dir / "summary.json")
    rows = json.loads((artifact_dir / "results.json").read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        rows = []
    suite_id = str(summary.get("suite_id") or suite_path.stem)
    for row in rows:
        row["suite_id"] = suite_id
    return {
        "suite_id": suite_id,
        "suite_path": str(suite_path),
        "artifact_dir": str(artifact_dir),
        "duration_seconds": round(time.time() - started, 2),
        "exit_code": int(proc.returncode),
        "summary": summary,
        "rows": rows,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _run_harness(
    *,
    task_ids: List[str],
    suite_ids: List[str],
    tags: List[str],
    contains: List[str],
    repeats: int,
    timeout_sec: int,
    env: Dict[str, str],
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "gaia.cli",
        "harness",
        "run",
        "--json",
        "--repeats",
        str(repeats),
        "--timeout-sec",
        str(timeout_sec),
    ]
    for task_id in task_ids:
        cmd.extend(["--task-id", task_id])
    for suite_id in suite_ids:
        cmd.extend(["--suite-id", suite_id])
    for tag in tags:
        cmd.extend(["--tag", tag])
    for term in contains:
        cmd.extend(["--contains", term])
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "harness run failed")
    payload = json.loads(proc.stdout)
    if not isinstance(payload, dict):
        raise ValueError("harness run returned non-object payload")
    return payload


def _write_markdown(path: Path, report: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append(f"# KPI benchmark pack: {report['pack_id']}")
    lines.append("")
    lines.append(f"- generated_at: {report['generated_at']}")
    lines.append(f"- repeats: {report['repeats']}")
    lines.append(f"- timeout_cap: {report['timeout_cap']}")
    lines.append("")
    lines.append("## Overall KPI")
    lines.append("")
    overall = report["overall_kpis"]
    lines.append(f"- scenario_success_rate: {overall['scenario_success_rate']}")
    lines.append(f"- reproducibility_rate: {overall['reproducibility_rate']}")
    lines.append(f"- progress_stop_failure_rate: {overall['progress_stop_failure_rate']}")
    lines.append(f"- self_recovery_rate: {overall['self_recovery_rate']}")
    lines.append(f"- intervention_rate: {overall['intervention_rate']}")
    lines.append(f"- avg_time_seconds: {overall['avg_time_seconds']}")
    lines.append(f"- flaky_rate: {overall['flaky_rate']}")
    lines.append("")
    lines.append("## Suite breakdown")
    lines.append("")
    for suite in report["suites"]:
        summary = suite["summary"]
        kpis = summary.get("kpi_metrics") or {}
        lines.append(f"### {suite['suite_id']}")
        lines.append(f"- suite_path: {suite['suite_path']}")
        lines.append(f"- artifact_dir: {suite['artifact_dir']}")
        lines.append(f"- scenario_success_rate: {kpis.get('scenario_success_rate')}")
        lines.append(f"- reproducibility_rate: {kpis.get('reproducibility_rate')}")
        lines.append(f"- progress_stop_failure_rate: {kpis.get('progress_stop_failure_rate')}")
        lines.append(f"- self_recovery_rate: {kpis.get('self_recovery_rate')}")
        lines.append(f"- intervention_rate: {kpis.get('intervention_rate')}")
        lines.append("")
    harness = report.get("harness")
    if isinstance(harness, dict):
        lines.append("## Harness")
        lines.append("")
        lines.append(f"- artifact_dir: {harness.get('artifact_dir')}")
        summary = harness.get("summary") if isinstance(harness.get("summary"), dict) else {}
        for key in (
            "task_count",
            "repeats",
            "pass_at_1",
            "pass_at_k",
            "pass_all_k",
            "reason_code_total",
        ):
            if key in summary:
                lines.append(f"- {key}: {summary.get(key)}")
        top_reason_codes = harness.get("top_reason_codes")
        if isinstance(top_reason_codes, list) and top_reason_codes:
            lines.append("")
            lines.append("### Harness top reason codes")
            for item in top_reason_codes[:10]:
                if not isinstance(item, dict):
                    continue
                lines.append(f"- {item.get('reason_code')}: {item.get('count')}")
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run multiple GAIA benchmark suites and aggregate KPI metrics.")
    parser.add_argument("--suite", action="append", required=True, help="Path to a suite JSON. Repeatable.")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout-cap", type=int, default=180)
    parser.add_argument("--session-prefix", default="kpi-pack")
    parser.add_argument("--harness-task-id", action="append", default=[], dest="harness_task_ids")
    parser.add_argument("--harness-suite-id", action="append", default=[], dest="harness_suite_ids")
    parser.add_argument("--harness-tag", action="append", default=[], dest="harness_tags")
    parser.add_argument("--harness-contains", action="append", default=[], dest="harness_contains")
    parser.add_argument("--harness-repeats", type=int)
    parser.add_argument("--harness-timeout-sec", type=int)
    args = parser.parse_args()

    env = os.environ.copy()
    suite_paths = [Path(p).resolve() for p in args.suite]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pack_id = f"kpi_pack_{timestamp}"
    out_dir = ARTIFACT_ROOT / pack_id
    out_dir.mkdir(parents=True, exist_ok=True)

    suite_reports: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    for idx, suite_path in enumerate(suite_paths, start=1):
        suite_report = _run_suite(
            suite_path,
            repeats=max(1, int(args.repeats)),
            timeout_cap=max(10, int(args.timeout_cap)),
            session_prefix=f"{args.session_prefix}-{idx}",
            env=env,
        )
        suite_reports.append(suite_report)
        all_rows.extend(suite_report["rows"])

    overall_kpis = _compute_pack_kpis(all_rows, max(1, int(args.repeats)))
    harness_report: Dict[str, Any] | None = None
    if args.harness_task_ids or args.harness_suite_ids or args.harness_tags or args.harness_contains:
        harness_payload = _run_harness(
            task_ids=[str(v) for v in args.harness_task_ids],
            suite_ids=[str(v) for v in args.harness_suite_ids],
            tags=[str(v) for v in args.harness_tags],
            contains=[str(v) for v in args.harness_contains],
            repeats=max(1, int(args.harness_repeats or args.repeats)),
            timeout_sec=max(10, int(args.harness_timeout_sec or args.timeout_cap)),
            env=env,
        )
        harness_report = {
            "run_id": harness_payload.get("run_id"),
            "artifact_dir": harness_payload.get("artifact_dir"),
            "selection": harness_payload.get("selection"),
            "summary": harness_payload.get("summary"),
            "grade_summary": harness_payload.get("grade_summary"),
            "reason_code_summary": harness_payload.get("reason_code_summary"),
            "top_reason_codes": harness_payload.get("top_reason_codes"),
        }
    report = {
        "pack_id": pack_id,
        "generated_at": timestamp,
        "repeats": max(1, int(args.repeats)),
        "timeout_cap": max(10, int(args.timeout_cap)),
        "suites": [
            {
                "suite_id": suite["suite_id"],
                "suite_path": suite["suite_path"],
                "artifact_dir": suite["artifact_dir"],
                "summary": suite["summary"],
            }
            for suite in suite_reports
        ],
        "overall_kpis": overall_kpis,
    }
    if harness_report is not None:
        report["harness"] = harness_report
    (out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(out_dir / "summary.md", report)
    print(json.dumps({"artifact_dir": str(out_dir), "overall_kpis": overall_kpis}, ensure_ascii=False))


if __name__ == "__main__":
    main()
