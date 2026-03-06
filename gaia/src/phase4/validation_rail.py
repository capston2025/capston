from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

DEFAULT_RAIL_BENCHMARK_PROFILE = {
    "success_rate_min": 0.70,
    "reproducibility_min": 0.50,
    "avg_time_max_sec": 60.0,
    "flaky_rate_max": 0.05,
    "min_runs_for_gate": 3,
}


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _default_workdir() -> Path:
    return (Path(__file__).resolve().parents[2] / "playwright-rail").resolve()


def _default_artifacts_root() -> Path:
    return (Path(__file__).resolve().parents[2] / "artifacts" / "validation-rail").resolve()


def _default_history_root() -> Path:
    return (_default_artifacts_root() / "_history").resolve()


def _safe_name(value: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in (value or ""))
    out = out.strip("-_")
    return out or "unknown"


def _host_supported(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    supported = {
        "inuu-timetable.vercel.app",
        "www.inuu-timetable.vercel.app",
    }
    return host in supported


def _skip_result(scope: str, mode: str, reason_code: str, reason: str) -> Dict[str, Any]:
    return {
        "summary": {
            "status": "skipped",
            "scope": scope,
            "mode": mode,
            "reason_code": reason_code,
            "reason": reason,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "duration_seconds": 0.0,
            "benchmark_metrics": {},
        },
        "cases": [],
        "artifacts": {},
    }


def _normalize_case_status(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"passed", "pass", "ok"}:
        return "passed"
    if token in {"failed", "timedout", "timeout", "error"}:
        return "failed"
    if token in {"skipped", "interrupted"}:
        return "skipped"
    return "unknown"


def _history_path(target_url: str, scope: str) -> Path:
    host = _safe_name(urlparse(target_url).netloc or "unknown-host")
    return (_default_history_root() / f"{host}_{_safe_name(scope)}.jsonl").resolve()


def _append_history_record(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_history_records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
    except Exception:
        return []
    return rows


def _compute_benchmark_metrics(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not history:
        return {
            "runs_total": 0,
            "success_rate": 0.0,
            "reproducibility": 0.0,
            "flaky_rate": 0.0,
            "avg_time_seconds": 0.0,
            "go_no_go_ready": False,
            "go_no_go_reason": "no_history",
        }

    evaluated_runs = [row for row in history if str((row.get("summary") or {}).get("status") or "").strip().lower() not in {"skipped"}]
    successful_runs = [
        row for row in evaluated_runs
        if str((row.get("summary") or {}).get("status") or "").strip().lower() == "passed"
    ]
    run_total = len(evaluated_runs)
    success_rate = round((len(successful_runs) / run_total) if run_total else 0.0, 4)

    passed_durations: List[float] = []
    case_status_map: Dict[str, List[str]] = {}
    for row in evaluated_runs:
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        try:
            duration = float(summary.get("duration_seconds") or 0.0)
        except Exception:
            duration = 0.0
        if str(summary.get("status") or "").strip().lower() == "passed" and duration > 0:
            passed_durations.append(duration)
        for case in row.get("cases") or []:
            if not isinstance(case, dict):
                continue
            case_id = str(case.get("id") or "").strip()
            if not case_id:
                continue
            case_status_map.setdefault(case_id, []).append(_normalize_case_status(case.get("status")))

    reproducible = 0
    flaky = 0
    observed_cases = 0
    for statuses in case_status_map.values():
        normalized = [s for s in statuses if s in {"passed", "failed", "skipped"}]
        if not normalized:
            continue
        observed_cases += 1
        uniq = set(normalized)
        if uniq == {"passed"}:
            reproducible += 1
        elif "passed" in uniq and "failed" in uniq:
            flaky += 1

    reproducibility = round((reproducible / observed_cases) if observed_cases else 0.0, 4)
    flaky_rate = round((flaky / observed_cases) if observed_cases else 0.0, 4)
    avg_time_seconds = round(sum(passed_durations) / len(passed_durations), 2) if passed_durations else 0.0

    enough_runs = run_total >= int(DEFAULT_RAIL_BENCHMARK_PROFILE["min_runs_for_gate"])
    ready = bool(
        enough_runs
        and success_rate >= float(DEFAULT_RAIL_BENCHMARK_PROFILE["success_rate_min"])
        and reproducibility >= float(DEFAULT_RAIL_BENCHMARK_PROFILE["reproducibility_min"])
        and avg_time_seconds <= float(DEFAULT_RAIL_BENCHMARK_PROFILE["avg_time_max_sec"])
        and flaky_rate <= float(DEFAULT_RAIL_BENCHMARK_PROFILE["flaky_rate_max"])
    )
    if not enough_runs:
        reason = "insufficient_history"
    elif ready:
        reason = "ready"
    else:
        reason = "threshold_not_met"

    return {
        "runs_total": run_total,
        "success_rate": success_rate,
        "reproducibility": reproducibility,
        "flaky_rate": flaky_rate,
        "avg_time_seconds": avg_time_seconds,
        "go_no_go_ready": ready,
        "go_no_go_reason": reason,
        "thresholds": dict(DEFAULT_RAIL_BENCHMARK_PROFILE),
    }


def run_validation_rail(
    *,
    target_url: str,
    run_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    enabled = _env_bool("GAIA_RAIL_ENABLED", True)
    mode = str(os.getenv("GAIA_RAIL_MODE", "soft") or "soft").strip().lower()
    if mode not in {"soft", "hard"}:
        mode = "soft"
    selected_scope = str(scope or os.getenv("GAIA_RAIL_SCOPE_DEFAULT", "smoke") or "smoke").strip().lower()
    if selected_scope not in {"smoke", "full"}:
        selected_scope = "smoke"

    if not enabled:
        return _skip_result(selected_scope, mode, "rail_skipped_disabled", "validation rail disabled")

    if not _host_supported(target_url):
        return _skip_result(selected_scope, mode, "rail_skipped_no_suite", "unsupported host for validation rail")

    workdir = Path(os.getenv("GAIA_RAIL_NODE_WORKDIR") or _default_workdir()).resolve()
    if not workdir.exists():
        return _skip_result(selected_scope, mode, "rail_skipped_env_missing", "playwright rail workdir not found")
    if shutil.which("npx") is None:
        return _skip_result(selected_scope, mode, "rail_skipped_env_missing", "npx not found")

    timeout_sec = max(30, _env_int("GAIA_RAIL_TIMEOUT_SEC", 300))
    ts = int(time.time())
    rid = _safe_name(str(run_id or f"rail-{ts}"))
    artifact_dir = (_default_artifacts_root() / rid / selected_scope).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    suite_path = f"tests/{selected_scope}"
    cmd = [
        "npx",
        "playwright",
        "test",
        suite_path,
        "--config=playwright.config.ts",
    ]
    env = os.environ.copy()
    env["GAIA_RAIL_BASE_URL"] = str(target_url or "").strip()
    env["GAIA_RAIL_ARTIFACT_DIR"] = str(artifact_dir)

    if os.getenv("GAIA_RAIL_USERNAME"):
        env["GAIA_RAIL_USERNAME"] = str(os.getenv("GAIA_RAIL_USERNAME") or "")
    if os.getenv("GAIA_RAIL_PASSWORD"):
        env["GAIA_RAIL_PASSWORD"] = str(os.getenv("GAIA_RAIL_PASSWORD") or "")
    if os.getenv("GAIA_TEST_USERNAME"):
        env["GAIA_TEST_USERNAME"] = str(os.getenv("GAIA_TEST_USERNAME") or "")
    if os.getenv("GAIA_TEST_PASSWORD"):
        env["GAIA_TEST_PASSWORD"] = str(os.getenv("GAIA_TEST_PASSWORD") or "")

    t0 = time.time()
    stdout_tail = ""
    stderr_tail = ""
    status = "passed"
    reason_code = "rail_passed"
    reason = "validation rail passed"

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            env=env,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout_tail = "\n".join((proc.stdout or "").splitlines()[-80:])
        stderr_tail = "\n".join((proc.stderr or "").splitlines()[-80:])
        if int(proc.returncode) != 0:
            status = "failed"
            reason_code = "rail_failed"
            reason = f"playwright exit_code={proc.returncode}"
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        reason_code = "rail_timeout"
        reason = f"playwright timed out ({timeout_sec}s)"
        out = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stdout_tail = "\n".join(out.splitlines()[-80:])
        stderr_tail = "\n".join(err.splitlines()[-80:])
    except Exception as exc:
        status = "error"
        reason_code = "rail_failed"
        reason = str(exc)

    summary_path = artifact_dir / "summary.json"
    cases_path = artifact_dir / "cases.json"
    summary_obj: Dict[str, Any] = {}
    cases_obj: List[Dict[str, Any]] = []
    if summary_path.exists():
        try:
            summary_obj = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary_obj = {}
    if cases_path.exists():
        try:
            parsed = json.loads(cases_path.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                cases_obj = parsed
        except Exception:
            cases_obj = []

    total = 0
    passed = 0
    failed = 0
    skipped = 0
    if isinstance(summary_obj, dict):
        try:
            total = int(summary_obj.get("total") or 0)
            passed = int(summary_obj.get("passed") or 0)
            failed = int(summary_obj.get("failed") or 0)
            skipped = int(summary_obj.get("skipped") or 0)
        except Exception:
            total = 0
            passed = 0
            failed = 0
            skipped = 0

    duration = round(float(time.time() - t0), 2)
    if status == "passed" and failed > 0:
        status = "failed"
        reason_code = "rail_failed"
        reason = "summary indicates failed cases"

    summary_payload = {
        "status": status,
        "scope": selected_scope,
        "mode": mode,
        "reason_code": reason_code,
        "reason": reason,
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_seconds": duration,
    }

    history_path = _history_path(target_url, selected_scope)
    history_record = {
        "generated_at": int(time.time()),
        "target_url": str(target_url or "").strip(),
        "run_id": rid,
        "summary": summary_payload,
        "cases": cases_obj,
    }
    benchmark_metrics: Dict[str, Any] = {}
    try:
        _append_history_record(history_path, history_record)
        benchmark_metrics = _compute_benchmark_metrics(_load_history_records(history_path))
    except Exception:
        benchmark_metrics = {}
    summary_payload["benchmark_metrics"] = benchmark_metrics

    return {
        "summary": summary_payload,
        "cases": cases_obj,
        "artifacts": {
            "artifact_dir": str(artifact_dir),
            "summary_path": str(summary_path),
            "cases_path": str(cases_path),
            "history_path": str(history_path),
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        },
    }
