#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from gaia.harness.benchmark_policy import apply_benchmark_success_policy

def _load_suite(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("suite must be a JSON object")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("suite.scenarios must be a non-empty array")
    return data


def _normalize_status(summary: Dict[str, Any], exit_code: int) -> str:
    final_status = str(summary.get("final_status") or "").strip() or str(summary.get("status") or "").strip()
    if final_status:
        return final_status
    return "SUCCESS" if int(exit_code) == 0 else "FAIL"


def _build_child_code(scenario: Dict[str, Any], session_id: str) -> str:
    payload = json.dumps({"scenario": scenario, "session_id": session_id}, ensure_ascii=False)
    return f"""
import contextlib, io, json, sys
import os
from gaia.terminal import _build_test_goal, run_chat_terminal_once
payload = json.loads({payload!r})
scenario = payload['scenario']
session_id = payload['session_id']
prepared_goal = _build_test_goal(url=scenario['url'], query=scenario['goal'])
constraints = scenario.get('constraints') if isinstance(scenario.get('constraints'), dict) else {{}}
expected_signals = scenario.get('expected_signals') if isinstance(scenario.get('expected_signals'), list) else []
goal_test_data = dict(getattr(prepared_goal, 'test_data', {{}}) or {{}})
scenario_test_data = scenario.get('test_data') if isinstance(scenario.get('test_data'), dict) else {{}}
if scenario_test_data:
    goal_test_data.update(scenario_test_data)
prepared_goal.expected_signals = [str(item) for item in expected_signals if str(item).strip()]
if prepared_goal.expected_signals:
    goal_test_data['harness_expected_signals'] = list(prepared_goal.expected_signals)
if constraints.get('requires_test_credentials'):
    username = (os.getenv('GAIA_TEST_USERNAME') or os.getenv('GAIA_AUTH_USERNAME') or '').strip()
    password = (os.getenv('GAIA_TEST_PASSWORD') or os.getenv('GAIA_AUTH_PASSWORD') or '').strip()
    email = (os.getenv('GAIA_TEST_EMAIL') or os.getenv('GAIA_AUTH_EMAIL') or '').strip()
    if username and password:
        goal_test_data['username'] = username
        goal_test_data['password'] = password
        goal_test_data.setdefault('auth_mode', 'provided_credentials')
        goal_test_data.setdefault('return_credentials', True)
        if email:
            goal_test_data['email'] = email
prepared_goal.test_data = goal_test_data
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    code, summary = run_chat_terminal_once(
        url=scenario['url'],
        query=scenario['goal'],
        session_id=session_id,
        prepared_goal=prepared_goal,
    )
result = {{
    'exit_code': int(code),
    'summary': summary,
    'captured_log': buf.getvalue(),
}}
print(json.dumps(result, ensure_ascii=False))
"""


def _run_scenario_once(
    scenario: Dict[str, Any],
    *,
    python_executable: str,
    session_id: str,
    timeout_sec: int,
    env: Dict[str, str],
) -> Dict[str, Any]:
    code = _build_child_code(scenario, session_id)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [python_executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
            cwd=str(WORKSPACE_ROOT),
            check=False,
        )
        duration = round(time.monotonic() - started, 2)
    except subprocess.TimeoutExpired as exc:
        return {
            "scenario_id": scenario.get("id"),
            "goal": scenario.get("goal"),
            "status": "FAIL",
            "reason": f"benchmark_timeout({timeout_sec}s)",
            "exit_code": 124,
            "duration_seconds": round(time.monotonic() - started, 2),
            "summary": {},
            "captured_log": str(exc.stdout or "") + str(exc.stderr or ""),
        }

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    payload: Dict[str, Any] = {}
    if stdout:
        last_line = stdout.splitlines()[-1]
        try:
            parsed = json.loads(last_line)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    exit_code = int(payload.get("exit_code") if isinstance(payload.get("exit_code"), int) else proc.returncode)
    status = _normalize_status(summary, exit_code)
    reason = str(summary.get("reason") or stderr or "")
    status, reason, benchmark_policy = apply_benchmark_success_policy(
        status=status,
        reason=reason,
        summary=summary,
    )
    return {
        "scenario_id": scenario.get("id"),
        "goal": scenario.get("goal"),
        "status": status,
        "reason": reason,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "summary": summary,
        "captured_log": payload.get("captured_log") if isinstance(payload.get("captured_log"), str) else stderr,
        "benchmark_policy": benchmark_policy,
    }


def _compute_metrics(rows: List[Dict[str, Any]], repeats: int) -> Dict[str, Any]:
    if not rows:
        return {
            "runs_total": 0,
            "success_rate": 0.0,
            "avg_time_seconds": 0.0,
            "reproducibility": 0.0,
            "flaky_rate": 0.0,
        }
    success_rows = [r for r in rows if str(r.get("status") or "") == "SUCCESS"]
    success_rate = round(len(success_rows) / len(rows), 4)
    avg_time = round(statistics.mean(float(r.get("duration_seconds") or 0.0) for r in rows), 2)

    per_case: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        per_case[str(row.get("scenario_id") or "unknown")].append(str(row.get("status") or "FAIL"))

    observed = 0
    reproducible = 0
    flaky = 0
    for statuses in per_case.values():
        if not statuses:
            continue
        observed += 1
        uniq = set(statuses)
        if repeats > 1 and len(statuses) == repeats and uniq == {"SUCCESS"}:
            reproducible += 1
        if repeats > 1 and "SUCCESS" in uniq and len(uniq) > 1:
            flaky += 1
    return {
        "runs_total": len(rows),
        "success_rate": success_rate,
        "avg_time_seconds": avg_time,
        "reproducibility": round((reproducible / observed), 4) if observed and repeats > 1 else None,
        "flaky_rate": round((flaky / observed), 4) if observed and repeats > 1 else None,
    }


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
        "dom 요소를 반복적으로 읽지 못해",
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


def _compute_kpi_metrics(rows: List[Dict[str, Any]], repeats: int) -> Dict[str, Any]:
    total = max(1, len(rows))
    success_count = sum(1 for row in rows if str(row.get("status") or "").strip().upper() == "SUCCESS")
    blocked_count = sum(1 for row in rows if _is_blocked_user_action(row))
    stop_failure_count = sum(1 for row in rows if _is_progress_stop_failure(row))
    recovery_rows = [row for row in rows if _has_recovery_event(row)]
    recovery_success = sum(
        1
        for row in recovery_rows
        if str(row.get("status") or "").strip().upper() == "SUCCESS"
    )

    per_case: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        per_case[str(row.get("scenario_id") or "unknown")].append(str(row.get("status") or "FAIL").upper())
    reproducible = 0
    observed = 0
    if repeats > 1:
        for statuses in per_case.values():
            if len(statuses) != repeats:
                continue
            observed += 1
            if set(statuses) == {"SUCCESS"}:
                reproducible += 1

    return {
        "scenario_success_rate": round(success_count / total, 4),
        "reproducibility_rate": round((reproducible / observed), 4) if observed else None,
        "progress_stop_failure_rate": round(stop_failure_count / total, 4),
        "self_recovery_rate": round((recovery_success / len(recovery_rows)), 4) if recovery_rows else None,
        "intervention_rate": round(blocked_count / total, 4),
        "counts": {
            "success": success_count,
            "blocked": blocked_count,
            "progress_stop_failures": stop_failure_count,
            "recovery_runs": len(recovery_rows),
            "recovery_success": recovery_success,
        },
        "targets": {
            "reproducibility_rate": 0.80,
            "progress_stop_failure_rate": 0.10,
            "self_recovery_rate": 0.60,
            "scenario_success_rate": 0.70,
            "intervention_rate": 0.20,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GAIA benchmark suite from scenario JSON.")
    parser.add_argument("--suite", required=True, help="Path to suite JSON")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--timeout-cap", type=int, default=600)
    parser.add_argument("--session-prefix", default="benchmark")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    suite_path = Path(args.suite).expanduser().resolve()
    suite = _load_suite(suite_path)
    scenarios = list(suite.get("scenarios") or [])
    if args.limit and int(args.limit) > 0:
        scenarios = scenarios[: int(args.limit)]
    repeats = max(1, int(args.repeats))
    timeout_cap = max(600, int(args.timeout_cap))

    started_at = datetime.now().astimezone()
    run_id = f"{Path(args.suite).stem}_{started_at.strftime('%Y%m%d_%H%M%S')}"
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else (Path("artifacts") / "benchmarks" / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    provider = str(args.provider or "").strip().lower()
    if not provider:
        model_name = str(args.model or "").strip().lower()
        if model_name.startswith("gpt-") or "codex" in model_name:
            provider = "openai"
        elif model_name.startswith("gemini"):
            provider = "gemini"
    if provider:
        env.setdefault("GAIA_LLM_PROVIDER", provider)
    env.setdefault("GAIA_LLM_MODEL", str(args.model))
    env.setdefault("GAIA_RAIL_ENABLED", "0")

    rows: List[Dict[str, Any]] = []
    for repeat_idx in range(1, repeats + 1):
        for idx, scenario in enumerate(scenarios, start=1):
            sid = f"{args.session_prefix}_{Path(args.suite).stem}_{repeat_idx}_{idx}"
            scenario_budget = int(scenario.get("time_budget_sec") or 600)
            budget = max(600, min(scenario_budget, timeout_cap))
            print(f"[{repeat_idx}/{repeats}] {idx}/{len(scenarios)} {scenario.get('id')} ...", flush=True)
            row = _run_scenario_once(
                scenario,
                python_executable=sys.executable,
                session_id=sid,
                timeout_sec=budget,
                env=env,
            )
            row["repeat"] = repeat_idx
            row["constraints"] = scenario.get("constraints") if isinstance(scenario.get("constraints"), dict) else {}
            row["expected_signals"] = scenario.get("expected_signals") if isinstance(scenario.get("expected_signals"), list) else []
            rows.append(row)

    metrics = _compute_metrics(rows, repeats)
    kpi_metrics = _compute_kpi_metrics(rows, repeats)
    status_counts = Counter(str(r.get("status") or "UNKNOWN") for r in rows)
    summary = {
        "schema_version": "gaia.benchmark.v1",
        "suite_id": suite.get("suite_id") or suite_path.stem,
        "site": suite.get("site") or {},
        "started_at": started_at.isoformat(),
        "repeats": repeats,
        "scenario_count": len(scenarios),
        "model": args.model,
        "metrics": metrics,
        "kpi_metrics": kpi_metrics,
        "status_counts": dict(status_counts),
        "failures": [
            {
                "scenario_id": r.get("scenario_id"),
                "status": r.get("status"),
                "reason": r.get("reason"),
            }
            for r in rows
            if str(r.get("status") or "") != "SUCCESS"
        ][:20],
    }

    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    md = io.StringIO()
    md.write(f"# Benchmark Summary\n\n")
    md.write(f"- suite: {summary['suite_id']}\n")
    md.write(f"- scenarios: {summary['scenario_count']}\n")
    md.write(f"- repeats: {repeats}\n")
    md.write(f"- model: {args.model}\n")
    md.write(f"- success_rate: {metrics['success_rate']}\n")
    md.write(f"- avg_time_seconds: {metrics['avg_time_seconds']}\n")
    md.write(f"- KPI scenario_success_rate: {kpi_metrics['scenario_success_rate']}\n")
    md.write(f"- KPI reproducibility_rate: {kpi_metrics['reproducibility_rate']}\n")
    md.write(f"- KPI progress_stop_failure_rate: {kpi_metrics['progress_stop_failure_rate']}\n")
    md.write(f"- KPI self_recovery_rate: {kpi_metrics['self_recovery_rate']}\n")
    md.write(f"- KPI intervention_rate: {kpi_metrics['intervention_rate']}\n")
    md.write(f"- status_counts: {dict(status_counts)}\n\n")
    if summary["failures"]:
        md.write("## Failures\n\n")
        for fail in summary["failures"]:
            md.write(f"- {fail['scenario_id']}: {fail['status']} / {fail['reason']}\n")
    (output_dir / "summary.md").write_text(md.getvalue(), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
