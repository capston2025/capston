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
from gaia.terminal import run_chat_terminal_once
payload = json.loads({payload!r})
scenario = payload['scenario']
session_id = payload['session_id']
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    code, summary = run_chat_terminal_once(
        url=scenario['url'],
        query=scenario['goal'],
        session_id=session_id,
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
            cwd=str(Path(__file__).resolve().parents[1]),
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
    return {
        "scenario_id": scenario.get("id"),
        "goal": scenario.get("goal"),
        "status": status,
        "reason": reason,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "summary": summary,
        "captured_log": payload.get("captured_log") if isinstance(payload.get("captured_log"), str) else stderr,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GAIA benchmark suite from scenario JSON.")
    parser.add_argument("--suite", required=True, help="Path to suite JSON")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--timeout-cap", type=int, default=90)
    parser.add_argument("--session-prefix", default="benchmark")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    suite_path = Path(args.suite).expanduser().resolve()
    suite = _load_suite(suite_path)
    scenarios = list(suite.get("scenarios") or [])
    if args.limit and int(args.limit) > 0:
        scenarios = scenarios[: int(args.limit)]
    repeats = max(1, int(args.repeats))
    timeout_cap = max(15, int(args.timeout_cap))

    started_at = datetime.now().astimezone()
    run_id = f"{Path(args.suite).stem}_{started_at.strftime('%Y%m%d_%H%M%S')}"
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else (Path("artifacts") / "benchmarks" / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("GAIA_LLM_MODEL", str(args.model))
    env.setdefault("GAIA_RAIL_ENABLED", "0")

    rows: List[Dict[str, Any]] = []
    for repeat_idx in range(1, repeats + 1):
        for idx, scenario in enumerate(scenarios, start=1):
            sid = f"{args.session_prefix}_{Path(args.suite).stem}_{repeat_idx}_{idx}"
            budget = max(15, min(int(scenario.get("time_budget_sec") or 60), timeout_cap))
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
