#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Windows cp949 환경에서 emoji/한글 출력이 가능하도록 stdout/stderr를 UTF-8로 강제.
# GUI BenchmarkWorker가 이미 PYTHONIOENCODING=utf-8을 넘기지만, 사용자가 직접
# 스크립트를 실행하거나 다른 경로로 호출할 때도 안전하도록 보장.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.benchmark_blocking import (
    is_blocked_user_action,
    normalize_blocked_user_action_row,
    summary_reason_code_summary,
)
from scripts.runner_identity import resolve_runner_id
from gaia.harness.benchmark_policy import apply_benchmark_success_policy

_MIN_BENCHMARK_TIMEOUT_SEC = 600
_MIN_CODEX_EXEC_TIMEOUT_SEC = 180
_MAX_CODEX_EXEC_TIMEOUT_SEC = 300
_BENCHMARK_CODEX_REASONING_EFFORT = "low"
_LIVE_TRACE_MARKERS = (
    "🎯 목표 시작",
    "--- Step ",
    "LLM 결정:",
    "✅ 목표 달성!",
    "⚠️ 액션 실패:",
    "🔁 phase 전환:",
    "📍 시작 URL로 이동:",
    # GUI live preview thread 진단용 — GUI 로그에 노출
    "📷 live preview",
)


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

def _resolve_scenario_timeout_budget(
    *,
    scenario_budget: int | None,
    timeout_cap: int,
    timeout_floor: int = _MIN_BENCHMARK_TIMEOUT_SEC,
) -> int:
    cap = max(int(timeout_floor), int(timeout_cap))
    floor = max(15, int(timeout_floor))
    budget = int(scenario_budget or floor)
    return max(floor, min(budget, cap))


def _resolve_codex_exec_timeout(timeout_sec: int) -> int:
    budget = max(_MIN_BENCHMARK_TIMEOUT_SEC, int(timeout_sec))
    return max(
        _MIN_CODEX_EXEC_TIMEOUT_SEC,
        min(_MAX_CODEX_EXEC_TIMEOUT_SEC, budget // 2),
    )


def _prepare_scenario_env(env: Dict[str, str], timeout_sec: int) -> Dict[str, str]:
    scenario_env = dict(env)
    scenario_env["GAIA_CODEX_EXEC_TIMEOUT_SEC"] = str(_resolve_codex_exec_timeout(timeout_sec))
    scenario_env["GAIA_CODEX_REASONING_EFFORT"] = _BENCHMARK_CODEX_REASONING_EFFORT
    scenario_env.setdefault("PYTHONUNBUFFERED", "1")
    # Windows에서 자식 Python의 print()가 emoji를 cp949로 인코딩하려다 실패하는
    # UnicodeEncodeError를 방지하기 위해 UTF-8로 강제 설정.
    scenario_env.setdefault("PYTHONIOENCODING", "utf-8")
    return scenario_env


def _should_emit_live_trace_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    if any(text.startswith(marker) for marker in _LIVE_TRACE_MARKERS):
        return True
    if text.startswith("🧩 목표 제약 감지:"):
        return True
    return False


def _tail_text(text: str, *, max_lines: int = 20, max_chars: int = 4000) -> str:
    lines = str(text or "").splitlines()
    tail = "\n".join(lines[-max(1, int(max_lines)):]).strip()
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
    return tail


def _build_child_code(scenario: Dict[str, Any], session_id: str) -> str:
    payload = json.dumps({"scenario": scenario, "session_id": session_id}, ensure_ascii=False)
    return f"""
import contextlib, io, json, sys
import os
# Windows cp949 환경에서도 emoji/한글 출력이 가능하도록 stdout/stderr를 UTF-8로 강제 재설정.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
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
class _TeeWriter:
    def __init__(self, *writers):
        self._writers = writers

    def write(self, text):
        for writer in self._writers:
            writer.write(text)
        return len(text)

    def flush(self):
        for writer in self._writers:
            flush = getattr(writer, "flush", None)
            if callable(flush):
                flush()

tee = _TeeWriter(sys.__stdout__, buf)
with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
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
    scenario_env = _prepare_scenario_env(env, timeout_sec)
    code = _build_child_code(scenario, session_id)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            [python_executable, "-u", "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=scenario_env,
            cwd=str(WORKSPACE_ROOT),
            # 자식 프로세스가 UTF-8로 출력하므로 부모도 UTF-8로 디코드해야 함
            # (Windows의 기본 cp949 코덱으로 디코드하면 한글/emoji 바이트가 깨짐)
            encoding="utf-8",
            errors="replace",
        )
        stdout_lines: list[str] = []
        assert proc.stdout is not None
        while True:
            raw_line = proc.stdout.readline()
            if raw_line == "" and proc.poll() is not None:
                break
            if raw_line == "":
                continue
            line = str(raw_line or "").rstrip("\n")
            stdout_lines.append(line)
            if _should_emit_live_trace_line(line):
                print(line, flush=True)
        return_code = proc.wait(timeout=max(1, timeout_sec - int(time.monotonic() - started)))
        duration = round(time.monotonic() - started, 2)
    except subprocess.TimeoutExpired as exc:
        try:
            proc.kill()
        except Exception:
            pass
        return normalize_blocked_user_action_row({
            "scenario_id": scenario.get("id"),
            "goal": scenario.get("goal"),
            "status": "FAIL",
            "reason": f"benchmark_timeout({timeout_sec}s)",
            "exit_code": 124,
            "duration_seconds": round(time.monotonic() - started, 2),
            "summary": {},
            "captured_log": str(exc.stdout or "") + str(exc.stderr or ""),
        })

    stdout = "\n".join(stdout_lines).strip()
    stderr = ""
    payload: Dict[str, Any] = {}
    parse_error = ""
    if stdout:
        last_line = stdout.splitlines()[-1]
        try:
            parsed = json.loads(last_line)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception as exc:
            parse_error = str(exc)
            payload = {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    exit_code = int(payload.get("exit_code") if isinstance(payload.get("exit_code"), int) else return_code)
    status = _normalize_status(summary, exit_code)
    child_log = payload.get("captured_log") if isinstance(payload.get("captured_log"), str) else stdout
    reason = str(summary.get("reason") or stderr or "")
    if not reason and exit_code != 0:
        tail = _tail_text(child_log)
        if tail:
            reason = f"child_process_failed(exit_code={exit_code}): {tail}"
        else:
            reason = f"child_process_failed(exit_code={exit_code})"
        if parse_error:
            reason = f"{reason} [json_parse_error={parse_error}]"
    status, reason, benchmark_policy = apply_benchmark_success_policy(
        status=status,
        reason=reason,
        summary=summary,
    )
    return normalize_blocked_user_action_row({
        "scenario_id": scenario.get("id"),
        "goal": scenario.get("goal"),
        "status": status,
        "reason": reason,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "summary": summary,
        "captured_log": child_log,
        "benchmark_policy": benchmark_policy,
    })


def _compute_metrics(rows: List[Dict[str, Any]], repeats: int) -> Dict[str, Any]:
    if not rows:
        return {
            "runs_total": 0,
            "success_rate": 0.0,
            "primary_success_rate": 0.0,
            "blocked_runs_total": 0,
            "primary_runs_total": 0,
            "avg_time_seconds": 0.0,
            "reproducibility": 0.0,
            "flaky_rate": 0.0,
        }
    success_rows = [r for r in rows if str(r.get("status") or "") == "SUCCESS"]
    blocked_rows = [r for r in rows if _is_blocked_user_action(r)]
    primary_rows = [r for r in rows if not _is_blocked_user_action(r)]
    success_rate = round(len(success_rows) / len(rows), 4)
    primary_success_rate = round(len(success_rows) / len(primary_rows), 4) if primary_rows else None
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
        "primary_success_rate": primary_success_rate,
        "blocked_runs_total": len(blocked_rows),
        "primary_runs_total": len(primary_rows),
        "avg_time_seconds": avg_time,
        "reproducibility": round((reproducible / observed), 4) if observed and repeats > 1 else None,
        "flaky_rate": round((flaky / observed), 4) if observed and repeats > 1 else None,
    }


def _summary_reason_code_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return summary_reason_code_summary(row)


def _is_blocked_user_action(row: Dict[str, Any]) -> bool:
    return is_blocked_user_action(row)


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
    primary_total = max(0, len(rows) - blocked_count)
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
        "primary_success_rate": round(success_count / primary_total, 4) if primary_total else None,
        "reproducibility_rate": round((reproducible / observed), 4) if observed else None,
        "progress_stop_failure_rate": round(stop_failure_count / total, 4),
        "self_recovery_rate": round((recovery_success / len(recovery_rows)), 4) if recovery_rows else None,
        "intervention_rate": round(blocked_count / total, 4),
        "counts": {
            "success": success_count,
            "blocked": blocked_count,
            "primary_runs": primary_total,
            "progress_stop_failures": stop_failure_count,
            "recovery_runs": len(recovery_rows),
            "recovery_success": recovery_success,
        },
        "targets": {
            "reproducibility_rate": 0.80,
            "progress_stop_failure_rate": 0.10,
            "self_recovery_rate": 0.60,
            "scenario_success_rate": 0.70,
            "primary_success_rate": 0.70,
            "intervention_rate": 0.20,
        },
    }


def _infer_provider_from_model(model_name: str) -> str:
    normalized = str(model_name or "").strip().lower()
    if normalized.startswith("gpt-") or "codex" in normalized:
        return "openai"
    if normalized.startswith("gemini"):
        return "gemini"
    if normalized.startswith("gemma") or normalized.startswith("ollama:"):
        return "ollama"
    return ""


def _read_workspace_env_file_assignments() -> Dict[str, str]:
    env_path = WORKSPACE_ROOT / ".env"
    if not env_path.exists():
        return {}
    assignments: Dict[str, str] = {}
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            assignments[key.strip()] = value
    except Exception:
        return {}
    return assignments


def _load_gaia_profile_token(provider: str) -> str:
    profile_path = Path.home() / ".gaia" / "auth" / "profiles.json"
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    profile = raw.get(provider, {}) if isinstance(raw, dict) else {}
    token = profile.get("token") if isinstance(profile, dict) else ""
    return str(token or "").strip()


def _has_codex_cli_auth() -> bool:
    if shutil.which("codex") is None:
        return False
    auth_path = Path.home() / ".codex" / "auth.json"
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    return any(str(raw.get(key) or "").strip() for key in ("OPENAI_API_KEY", "auth_mode")) or bool(raw.get("tokens"))


def _populate_provider_credentials(env: Dict[str, str], provider: str) -> None:
    normalized = str(provider or "").strip().lower()
    dotenv = _read_workspace_env_file_assignments()
    if normalized == "openai":
        for key in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY"):
            if not str(env.get(key) or "").strip() and str(dotenv.get(key) or "").strip():
                env[key] = str(dotenv[key]).strip()
        if not str(env.get("OPENAI_API_KEY") or env.get("OPENAI_ADMIN_KEY") or "").strip():
            token = _load_gaia_profile_token("openai")
            if token:
                env["OPENAI_API_KEY"] = token
    elif normalized == "gemini":
        if not str(env.get("GEMINI_API_KEY") or "").strip() and str(dotenv.get("GEMINI_API_KEY") or "").strip():
            env["GEMINI_API_KEY"] = str(dotenv["GEMINI_API_KEY"]).strip()


def _provider_credential_error(provider: str, env: Dict[str, str]) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized == "openai" and not str(env.get("OPENAI_API_KEY") or env.get("OPENAI_ADMIN_KEY") or "").strip():
        if _has_codex_cli_auth():
            return ""
        return (
            "missing_provider_credentials: provider=openai requires OPENAI_API_KEY or OPENAI_ADMIN_KEY. "
            "Set it in the shell environment, repo .env, ~/.gaia/auth/profiles.json, or run `codex login` "
            "on this machine before running benchmarks."
        )
    if normalized == "gemini" and not str(env.get("GEMINI_API_KEY") or "").strip():
        return "missing_provider_credentials: provider=gemini requires GEMINI_API_KEY."
    return ""


def _should_push_metrics(args: Any) -> bool:
    """Benchmark metrics leave the machine only when explicitly requested."""
    return bool(getattr(args, "push_metrics", False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GAIA benchmark suite from scenario JSON.")
    parser.add_argument("--suite", required=True, help="Path to suite JSON")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument(
        "--runner-id",
        default="",
        help="Human/team runner identifier recorded in artifacts and metrics. Defaults to GAIA_RUNNER_ID or user@host.",
    )
    parser.add_argument("--timeout-cap", type=int, default=600)
    parser.add_argument("--session-prefix", default="benchmark")
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--push-metrics",
        action="store_true",
        help="Upload benchmark metrics to the configured monitoring server after the run.",
    )
    args = parser.parse_args()

    suite_path = Path(args.suite).expanduser().resolve()
    suite = _load_suite(suite_path)
    scenarios = list(suite.get("scenarios") or [])
    if args.limit and int(args.limit) > 0:
        scenarios = scenarios[: int(args.limit)]
    repeats = max(1, int(args.repeats))
    timeout_cap = max(_MIN_BENCHMARK_TIMEOUT_SEC, int(args.timeout_cap))

    started_at = datetime.now().astimezone()
    run_id = f"{Path(args.suite).stem}_{started_at.strftime('%Y%m%d_%H%M%S')}"
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else (Path("artifacts") / "benchmarks" / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    runner_id = resolve_runner_id(args.runner_id, env)
    env["GAIA_RUNNER_ID"] = runner_id

    provider = str(args.provider or "").strip().lower()
    if not provider:
        provider = _infer_provider_from_model(str(args.model or ""))
    if provider:
        env.setdefault("GAIA_LLM_PROVIDER", provider)
    env.setdefault("GAIA_LLM_MODEL", str(args.model))
    env.setdefault("GAIA_RAIL_ENABLED", "0")
    _populate_provider_credentials(env, provider)
    credential_error = _provider_credential_error(provider, env)
    if credential_error:
        empty_metrics = _compute_metrics([], repeats)
        empty_kpis = _compute_kpi_metrics([], repeats)
        summary = {
            "schema_version": "gaia.benchmark.v1",
            "suite_id": suite.get("suite_id") or suite_path.stem,
            "site": suite.get("site") or {},
            "started_at": started_at.isoformat(),
            "repeats": repeats,
            "scenario_count": len(scenarios),
            "provider": provider,
            "model": args.model,
            "runner_id": runner_id,
            "metrics": empty_metrics,
            "kpi_metrics": empty_kpis,
            "status_counts": {},
            "failures": [],
            "blocked": [],
            "fatal_error": credential_error,
        }
        (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "results.json").write_text("[]", encoding="utf-8")
        (output_dir / "summary.md").write_text(
            "# Benchmark Summary\n\n"
            f"- suite: {summary['suite_id']}\n"
            f"- scenarios: {summary['scenario_count']}\n"
            f"- provider: {provider or '-'}\n"
            f"- model: {args.model}\n"
            f"- runner_id: {runner_id}\n"
            f"- fatal_error: {credential_error}\n",
            encoding="utf-8",
        )
        print(credential_error, file=sys.stderr, flush=True)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2

    rows: List[Dict[str, Any]] = []
    for repeat_idx in range(1, repeats + 1):
        for idx, scenario in enumerate(scenarios, start=1):
            sid = f"{args.session_prefix}_{Path(args.suite).stem}_{repeat_idx}_{idx}"
            scenario_budget = int(scenario.get("time_budget_sec") or 600)
            budget = _resolve_scenario_timeout_budget(
                scenario_budget=scenario_budget,
                timeout_cap=timeout_cap,
                timeout_floor=_MIN_BENCHMARK_TIMEOUT_SEC,
            )
            print(f"[{repeat_idx}/{repeats}] {idx}/{len(scenarios)} {scenario.get('id')} ...", flush=True)
            row = _run_scenario_once(
                scenario,
                python_executable=sys.executable,
                session_id=sid,
                timeout_sec=budget,
                env=env,
            )
            row["repeat"] = repeat_idx
            row["provider"] = provider
            row["model"] = str(args.model)
            row["runner_id"] = runner_id
            row["constraints"] = scenario.get("constraints") if isinstance(scenario.get("constraints"), dict) else {}
            row["expected_signals"] = scenario.get("expected_signals") if isinstance(scenario.get("expected_signals"), list) else []
            rows.append(row)

    metrics = _compute_metrics(rows, repeats)
    kpi_metrics = _compute_kpi_metrics(rows, repeats)
    status_counts = Counter(str(r.get("status") or "UNKNOWN") for r in rows)
    blocked_rows = [r for r in rows if _is_blocked_user_action(r)]
    summary = {
        "schema_version": "gaia.benchmark.v1",
        "suite_id": suite.get("suite_id") or suite_path.stem,
        "site": suite.get("site") or {},
        "started_at": started_at.isoformat(),
        "repeats": repeats,
        "scenario_count": len(scenarios),
        "provider": provider,
        "model": args.model,
        "runner_id": runner_id,
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
            if str(r.get("status") or "") != "SUCCESS" and not _is_blocked_user_action(r)
        ][:20],
        "blocked": [
            {
                "scenario_id": r.get("scenario_id"),
                "status": r.get("status"),
                "reason": r.get("reason"),
                "blocked_reason_code": r.get("blocked_reason_code")
                or (r.get("summary") if isinstance(r.get("summary"), dict) else {}).get("blocked_reason_code"),
            }
            for r in blocked_rows
        ][:20],
    }

    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    md = io.StringIO()
    md.write(f"# Benchmark Summary\n\n")
    md.write(f"- suite: {summary['suite_id']}\n")
    md.write(f"- scenarios: {summary['scenario_count']}\n")
    md.write(f"- repeats: {repeats}\n")
    md.write(f"- provider: {provider or '-'}\n")
    md.write(f"- model: {args.model}\n")
    md.write(f"- runner_id: {runner_id}\n")
    md.write(f"- success_rate: {metrics['success_rate']}\n")
    md.write(f"- primary_success_rate: {metrics['primary_success_rate']}\n")
    md.write(f"- avg_time_seconds: {metrics['avg_time_seconds']}\n")
    md.write(f"- KPI scenario_success_rate: {kpi_metrics['scenario_success_rate']}\n")
    md.write(f"- KPI primary_success_rate: {kpi_metrics['primary_success_rate']}\n")
    md.write(f"- KPI reproducibility_rate: {kpi_metrics['reproducibility_rate']}\n")
    md.write(f"- KPI progress_stop_failure_rate: {kpi_metrics['progress_stop_failure_rate']}\n")
    md.write(f"- KPI self_recovery_rate: {kpi_metrics['self_recovery_rate']}\n")
    md.write(f"- KPI intervention_rate: {kpi_metrics['intervention_rate']}\n")
    md.write(f"- status_counts: {dict(status_counts)}\n\n")
    if summary["failures"]:
        md.write("## Failures\n\n")
        for fail in summary["failures"]:
            md.write(f"- {fail['scenario_id']}: {fail['status']} / {fail['reason']}\n")
    if summary["blocked"]:
        md.write("\n## Blocked User Action\n\n")
        for item in summary["blocked"]:
            md.write(
                f"- {item['scenario_id']}: {item['status']} / {item.get('blocked_reason_code')} / {item['reason']}\n"
            )
    (output_dir / "summary.md").write_text(md.getvalue(), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if _should_push_metrics(args):
        _try_push_metrics(output_dir, suite_path)
    return 0


def _try_push_metrics(output_dir: Path, suite_path: Path | None = None) -> None:
    """Push benchmark metrics when the caller explicitly opted in."""
    monitoring_config = Path.home() / ".gaia" / "monitoring.json"
    if not monitoring_config.exists():
        print("\n  모니터링 서버 설정이 없어 업로드를 건너뜁니다.")
        print("  연결: python scripts/gaia_monitor_connect.py <서버주소> --token <토큰>")
        return

    push_script = Path(__file__).parent / "push_metrics.py"
    if not push_script.exists():
        return

    print("\n  📡 모니터링 서버로 결과 업로드 중...")
    result = subprocess.run(
        [
            sys.executable,
            str(push_script),
            "--suite-dir",
            str(output_dir),
            *(["--suite-json", str(suite_path)] if suite_path is not None else []),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("  업로드 완료 ✅")
        if result.stdout.strip():
            print(f"  {result.stdout.strip()}")
    else:
        print("  업로드 실패 (벤치마크 결과는 정상 저장됨)")
        if result.stderr.strip():
            print(f"  오류: {result.stderr.strip()}")
        if result.stdout.strip():
            print(f"  출력: {result.stdout.strip()}")


if __name__ == "__main__":
    raise SystemExit(main())
