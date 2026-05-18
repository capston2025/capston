#!/usr/bin/env python3
"""
GAIA 벤치마크 메트릭을 Prometheus Pushgateway로 전송하는 스크립트.

summary.json  → suite 전체 KPI 메트릭
results.json  → 시나리오별 상세 메트릭 (runs/success/fail/duration 등)

팀원이 gaia_monitor_connect.py 로 한 번 연결 설정을 하고 나면,
이 스크립트가 ~/.gaia/monitoring.json 을 읽어 서버/토큰을 사용합니다.

사용법:
  python scripts/push_metrics.py            # 가장 최근 결과 push
  python scripts/push_metrics.py --all      # 모든 기존 결과 push
  python scripts/push_metrics.py --gateway http://localhost:9091  # 직접 지정
  python scripts/run_goal_benchmark.py --suite ... --push-metrics  # 실행 후 명시적 push
"""

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from gaia.src.benchmark_suite_sharing import SharedSuiteError, upload_shared_suite
from scripts.benchmark_blocking import is_blocked_user_action, summary_reason_code_summary

ARTIFACTS_DIR = Path(__file__).parent.parent / "artifacts" / "benchmarks"
MONITORING_CONFIG = Path.home() / ".gaia" / "monitoring.json"
HISTORY_DIR = Path.home() / ".gaia" / "metrics_history"
EXTERNAL_PUBLIC_MANIFEST = WORKSPACE_ROOT / "gaia" / "tests" / "scenarios" / "external_public_manifest.json"
PUSH_USER = "gaia"


# ── 설정 로드 ──────────────────────────────────────────────────────────────

def load_monitoring_config() -> dict | None:
    if MONITORING_CONFIG.exists():
        try:
            return json.loads(MONITORING_CONFIG.read_text())
        except Exception:
            return None
    return None


def load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ── 메트릭 텍스트 빌더 ────────────────────────────────────────────────────


def _escape_label_value(value) -> str:
    """Escape a Prometheus label value according to the text exposition format."""
    return (
        str(value if value is not None else "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _labels_to_text(labels: dict) -> str:
    return ",".join(f'{key}="{_escape_label_value(value)}"' for key, value in sorted(labels.items()))


def _timestamp_seconds(raw: str | None) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        return float(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def _gauge(name: str, help_text: str, value, labels: dict,
           declared: set | None = None) -> list[str]:
    """Prometheus exposition 형식 게이지 라인 생성.
    declared: 이미 HELP/TYPE을 선언한 메트릭 이름 집합 (중복 방지).
    """
    if value is None:
        return []
    label_str = _labels_to_text(labels)
    lines = []
    if declared is not None and name not in declared:
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        declared.add(name)
    elif declared is None:
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    lines.append(f"{name}{{{label_str}}} {float(value)}")
    return lines


def _suite_key_from_suite_id(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"_public_v\d+$", "", text)
    text = re.sub(r"_v\d+$", "", text)
    return text.removesuffix("_suite")


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


@lru_cache(maxsize=4)
def _load_external_public_manifest(path: Path = EXTERNAL_PUBLIC_MANIFEST) -> dict[str, dict]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("suites")
    if not isinstance(entries, list):
        return {}
    mapping: dict[str, dict] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        metadata = {
            "site_key": str(item.get("site_key") or "").strip() or "unknown",
            "site": str(item.get("label") or item.get("site_key") or "").strip() or "unknown",
            "category": str(item.get("category") or "unknown").strip() or "unknown",
            "volatility": str(item.get("volatility") or "unknown").strip() or "unknown",
        }
        suite_path = str(item.get("suite_path") or "").strip()
        if suite_path:
            suite_file = (WORKSPACE_ROOT / suite_path).resolve()
            mapping[str(suite_file)] = metadata
            mapping[suite_file.name] = metadata
            suite_payload = load_json(suite_file)
            if isinstance(suite_payload, dict) and str(suite_payload.get("suite_id") or "").strip():
                mapping[str(suite_payload["suite_id"]).strip()] = metadata
                mapping[_suite_key_from_suite_id(str(suite_payload["suite_id"])).strip()] = metadata
        if metadata["site_key"]:
            mapping[metadata["site_key"]] = metadata
    return mapping


def _site_metadata(
    summary: dict,
    *,
    suite_json_path: Path | None = None,
    suite_id: str | None = None,
) -> dict[str, str]:
    manifest = _load_external_public_manifest()
    candidates: list[str] = []
    if suite_json_path is not None:
        resolved = suite_json_path.expanduser().resolve()
        candidates.extend([str(resolved), resolved.name])
        suite_payload = load_json(resolved)
        if isinstance(suite_payload, dict):
            candidates.append(str(suite_payload.get("suite_id") or "").strip())
    effective_suite_id = str(suite_id or summary.get("suite_id") or "").strip()
    if effective_suite_id:
        candidates.extend([effective_suite_id, _suite_key_from_suite_id(effective_suite_id)])

    for candidate in candidates:
        if candidate and candidate in manifest:
            return dict(manifest[candidate])

    site = summary.get("site") if isinstance(summary.get("site"), dict) else {}
    site_name = str(site.get("name") or summary.get("site") or "unknown").strip() or "unknown"
    site_key = _suite_key_from_suite_id(effective_suite_id) if effective_suite_id else "unknown"
    return {
        "site_key": site_key or "unknown",
        "site": site_name,
        "category": str(site.get("category") or "unknown").strip() or "unknown",
        "volatility": str(site.get("volatility") or "unknown").strip() or "unknown",
    }


def _with_site_labels(base: dict, metadata: dict[str, str]) -> dict:
    return {
        **base,
        "site_key": metadata.get("site_key", "unknown"),
        "category": metadata.get("category", "unknown"),
        "volatility": metadata.get("volatility", "unknown"),
    }


def _status_is_success(row: dict) -> bool:
    return str(row.get("status") or "").strip().upper() == "SUCCESS"


def _numeric_values(rows: list[dict], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _mean_or_none(values: list[float]) -> float | None:
    return round(statistics.mean(values), 4) if values else None


def _single_or_mixed(values: set[str], default: str = "unknown") -> str:
    cleaned = {value for value in values if value}
    if not cleaned:
        return default
    if len(cleaned) == 1:
        return next(iter(cleaned))
    return "mixed"


def _runner_id_from_rows(summary: dict, rows: list[dict] | None = None) -> str:
    values = {str(summary.get("runner_id") or "").strip()}
    for row in rows or []:
        values.add(str(row.get("runner_id") or "").strip())
    return _single_or_mixed(values)


def build_suite_metrics(
    summary: dict,
    declared: set | None = None,
    *,
    suite_json_path: Path | None = None,
) -> str:
    """suite 전체 KPI 메트릭 (summary.json 기반)."""
    if declared is None:
        declared = set()
    lines = []
    metadata = _site_metadata(summary, suite_json_path=suite_json_path)
    base = {
        "suite_id": summary.get("suite_id", "unknown"),
        "model":    summary.get("model", "unknown"),
        "provider": summary.get("provider", "unknown"),
        "runner_id": summary.get("runner_id", "unknown"),
    }
    base = _with_site_labels({**base, "site": metadata["site"]}, metadata)
    m   = summary.get("metrics", {})
    kpi = summary.get("kpi_metrics", {})
    tgt = kpi.get("targets", {})
    cnt = kpi.get("counts", {})
    started_ts = _timestamp_seconds(summary.get("started_at"))

    for args in [
        ("gaia_runs_total",                       "Total runs",                          m.get("runs_total")),
        ("gaia_success_rate",                     "Overall success rate",                m.get("success_rate")),
        ("gaia_avg_time_seconds",                 "Avg execution time (s)",              m.get("avg_time_seconds")),
        ("gaia_suite_success_rate",               "Suite-level scenario success rate",   kpi.get("scenario_success_rate")),
        ("gaia_suite_primary_success_rate",       "Suite success rate excluding blocked user-action gates", kpi.get("primary_success_rate")),
        ("gaia_reproducibility_rate",             "Reproducibility rate",                kpi.get("reproducibility_rate")),
        ("gaia_progress_stop_failure_rate",       "Progress stop failure rate",          kpi.get("progress_stop_failure_rate")),
        ("gaia_self_recovery_rate",               "Self recovery rate",                  kpi.get("self_recovery_rate")),
        ("gaia_intervention_rate",                "Intervention rate",                   kpi.get("intervention_rate")),
        ("gaia_target_scenario_success_rate",     "Target scenario success rate",        tgt.get("scenario_success_rate")),
        ("gaia_target_primary_success_rate",      "Target primary success rate",         tgt.get("primary_success_rate")),
        ("gaia_target_reproducibility_rate",      "Target reproducibility rate",         tgt.get("reproducibility_rate")),
        ("gaia_target_progress_stop_failure_rate","Target progress stop failure rate",   tgt.get("progress_stop_failure_rate")),
        ("gaia_target_self_recovery_rate",        "Target self recovery rate",           tgt.get("self_recovery_rate")),
        ("gaia_target_intervention_rate",         "Target intervention rate",            tgt.get("intervention_rate")),
        ("gaia_count_success",                    "Success count",                       cnt.get("success")),
        ("gaia_count_blocked",                    "Blocked count",                       cnt.get("blocked")),
        ("gaia_count_primary_runs",               "Primary non-blocked run count",       cnt.get("primary_runs")),
        ("gaia_count_progress_stop_failures",     "Progress stop failures",              cnt.get("progress_stop_failures")),
        ("gaia_count_recovery_runs",              "Recovery runs",                       cnt.get("recovery_runs")),
        ("gaia_count_recovery_success",           "Recovery success",                    cnt.get("recovery_success")),
    ]:
        lines.extend(_gauge(args[0], args[1], args[2], base, declared))

    # 상태별 카운트
    for status, count in summary.get("status_counts", {}).items():
        lines.extend(_gauge("gaia_status_count", "Count by final status",
                            count, {**base, "status": status}, declared))

    lines.extend(_gauge(
        "gaia_suite_started_timestamp_seconds",
        "Suite run start timestamp as Unix seconds",
        started_ts,
        base,
        declared,
    ))

    return "\n".join(lines) + "\n"


def build_scenario_metrics(
    summary: dict,
    results: list,
    declared: set | None = None,
    *,
    suite_json_path: Path | None = None,
) -> str:
    """시나리오별 상세 메트릭 (results.json 기반)."""
    if declared is None:
        declared = set()
    lines = []

    default_suite_id = str(summary.get("suite_id") or "unknown")

    # (suite_id, scenario_id) → 해당 실행 목록. pack 결과에서는 서로 다른 suite가
    # 같은 scenario_id를 가질 수 있으므로 suite_id까지 묶는다.
    scenario_runs: dict[tuple[str, str], list] = defaultdict(list)
    for row in results:
        suite_id = str(row.get("suite_id") or default_suite_id or "unknown")
        sid = str(row.get("scenario_id") or "unknown")
        scenario_runs[(suite_id, sid)].append(row)

    for (suite_id, scenario_id), runs in scenario_runs.items():
        durations   = [r["duration_seconds"] for r in runs if r.get("duration_seconds") is not None]
        statuses    = [str(r.get("status", "")).upper() for r in runs]
        success_cnt = statuses.count("SUCCESS")
        fail_cnt    = len(statuses) - success_cnt
        total_cnt   = len(runs)
        success_rate = success_cnt / total_cnt if total_cnt else 0.0

        avg_dur    = statistics.mean(durations)    if durations else None
        median_dur = statistics.median(durations)  if durations else None
        min_dur    = min(durations)                if durations else None
        max_dur    = max(durations)                if durations else None
        latest_dur = durations[-1]                 if durations else None

        last_run   = runs[-1]
        completion = last_run.get("summary", {}).get("goal_completion_source", "")
        model      = last_run.get("model", summary.get("model", "unknown"))
        provider   = last_run.get("provider", summary.get("provider", "unknown"))
        runner_id  = last_run.get("runner_id", summary.get("runner_id", "unknown"))
        started_ts = _timestamp_seconds(last_run.get("started_at") or summary.get("started_at"))
        metadata = _site_metadata(summary, suite_json_path=suite_json_path, suite_id=suite_id)

        base = {
            "suite_id":    suite_id,
            "scenario_id": scenario_id,
            "site":        metadata["site"],
            "model":       model,
            "provider":    provider,
            "runner_id":   runner_id,
        }
        base = _with_site_labels(base, metadata)

        for args in [
            ("gaia_scenario_runs_total",         "Total runs for this scenario",          total_cnt),
            ("gaia_scenario_success_count",      "Success count for this scenario",       success_cnt),
            ("gaia_scenario_fail_count",         "Fail count for this scenario",          fail_cnt),
            ("gaia_scenario_success_rate",       "Success rate for this scenario (0-1)",  success_rate),
            ("gaia_scenario_avg_duration_sec",   "Avg duration (s)",                      avg_dur),
            ("gaia_scenario_median_duration_sec","Median duration (s)",                   median_dur),
            ("gaia_scenario_min_duration_sec",   "Min duration (s)",                      min_dur),
            ("gaia_scenario_max_duration_sec",   "Max duration (s)",                      max_dur),
            ("gaia_scenario_latest_duration_sec","Latest run duration (s)",               latest_dur),
        ]:
            lines.extend(_gauge(args[0], args[1], args[2], base, declared))

        last_status_ok = 1.0 if statuses and statuses[-1] == "SUCCESS" else 0.0
        # 실패 사유: reason_code_summary 상위 코드 사용 (자유 형식 텍스트는 민감 정보 포함 가능성으로 제외)
        fail_reason = ""
        if last_status_ok == 0.0:
            code_summary = last_run.get("summary", {}).get("reason_code_summary", {})
            if code_summary:
                top_code = max(code_summary, key=code_summary.get)
                top_count = code_summary[top_code]
                all_codes = ",".join(
                    f"{k}:{v}" for k, v in
                    sorted(code_summary.items(), key=lambda x: -x[1])[:3]
                )
                fail_reason = all_codes[:120]
        lines.extend(_gauge(
            "gaia_scenario_last_status",
            "Last run result (1=SUCCESS 0=FAIL)",
            last_status_ok,
            {**base, "completion": completion, "fail_reason": fail_reason},
            declared,
        ))
        lines.extend(_gauge(
            "gaia_scenario_info",
            "Scenario presence marker with description",
            1,
            _with_site_labels(
                {
                    "suite_id": suite_id,
                    "scenario_id": scenario_id,
                    "site": metadata["site"],
                    "runner_id": runner_id,
                },
                metadata,
            ),
            declared,
        ))
        lines.extend(_gauge(
            "gaia_scenario_last_run_timestamp_seconds",
            "Scenario run start timestamp as Unix seconds",
            started_ts,
            base,
            declared,
        ))

    return "\n".join(lines) + "\n"


def _reason_code_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if _status_is_success(row):
            continue
        rc_summary = summary_reason_code_summary(row)
        if rc_summary:
            for raw_code, raw_count in rc_summary.items():
                code = str(raw_code or "").strip()
                if not code:
                    continue
                try:
                    count = int(raw_count)
                except (TypeError, ValueError):
                    count = 1
                counts[code] += max(1, count)
            continue
        counts["unknown_failure"] += 1
    return dict(counts)


def build_external_pack_metrics(summary: dict, results: list, declared: set | None = None) -> str:
    """30-site external public pack 전체를 한 화면에서 보기 위한 rollup metrics."""
    if declared is None:
        declared = set()
    if not isinstance(summary.get("overall_kpis"), dict):
        return ""

    rows = [row for row in results if isinstance(row, dict)]
    overall = summary.get("overall_kpis") or {}
    counts = overall.get("counts") if isinstance(overall.get("counts"), dict) else {}
    pack_id = str(summary.get("pack_id") or summary.get("suite_id") or "unknown")
    model = _single_or_mixed(
        {str(row.get("model") or "").strip() for row in rows}
        | {str(summary.get("model") or "").strip()},
    )
    provider = _single_or_mixed(
        {str(row.get("provider") or "").strip() for row in rows}
        | {str(summary.get("provider") or "").strip()},
    )
    runner_id = _runner_id_from_rows(summary, rows)
    base = {"pack_id": pack_id, "model": model, "provider": provider, "runner_id": runner_id}

    suite_ids = {
        str(item.get("suite_id") or "").strip()
        for item in (summary.get("suites") if isinstance(summary.get("suites"), list) else [])
        if isinstance(item, dict)
    }
    suite_ids.update(str(row.get("suite_id") or "").strip() for row in rows if row.get("suite_id"))
    suite_ids.discard("")
    scenario_keys = {
        (str(row.get("suite_id") or "unknown"), str(row.get("scenario_id") or "unknown"))
        for row in rows
    }
    success_count = int(counts.get("success") or sum(1 for row in rows if _status_is_success(row)))
    runs_total = int(counts.get("runs_total") or len(rows))
    avg_duration = overall.get("avg_time_seconds")
    if avg_duration is None:
        avg_duration = _mean_or_none(_numeric_values(rows, "duration_seconds"))

    lines: list[str] = []
    for args in [
        ("gaia_external_pack_runs_total", "External public pack total runs", runs_total),
        ("gaia_external_pack_success_count", "External public pack success count", success_count),
        ("gaia_external_pack_site_count", "External public pack site count", len(suite_ids)),
        ("gaia_external_pack_scenario_count", "External public pack scenario count", len(scenario_keys)),
        ("gaia_external_pack_success_rate", "External public pack scenario success rate", overall.get("scenario_success_rate")),
        ("gaia_external_pack_primary_success_rate", "External public pack primary success rate", overall.get("primary_success_rate")),
        ("gaia_external_pack_reproducibility_rate", "External public pack reproducibility rate", overall.get("reproducibility_rate")),
        ("gaia_external_pack_progress_stop_failure_rate", "External public pack progress stop failure rate", overall.get("progress_stop_failure_rate")),
        ("gaia_external_pack_self_recovery_rate", "External public pack self recovery rate", overall.get("self_recovery_rate")),
        ("gaia_external_pack_intervention_rate", "External public pack intervention rate", overall.get("intervention_rate")),
        ("gaia_external_pack_flaky_rate", "External public pack flaky rate", overall.get("flaky_rate")),
        ("gaia_external_pack_avg_duration_seconds", "External public pack average duration seconds", avg_duration),
    ]:
        lines.extend(_gauge(args[0], args[1], args[2], base, declared))

    rows_by_suite: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rows_by_suite[str(row.get("suite_id") or "unknown")].append(row)

    rows_by_category: dict[str, list[dict]] = defaultdict(list)
    for suite_id, suite_rows in sorted(rows_by_suite.items()):
        metadata = _site_metadata({}, suite_id=suite_id)
        site_base = _with_site_labels(
            {
                "pack_id": pack_id,
                "suite_id": suite_id,
                "site": metadata["site"],
                "model": model,
                "provider": provider,
                "runner_id": runner_id,
            },
            metadata,
        )
        total = len(suite_rows)
        site_success = sum(1 for row in suite_rows if _status_is_success(row))
        durations = _numeric_values(suite_rows, "duration_seconds")
        latest_ok = 1.0 if suite_rows and _status_is_success(suite_rows[-1]) else 0.0
        blocked = sum(1 for row in suite_rows if is_blocked_user_action(row))
        rows_by_category[metadata["category"]].extend(suite_rows)

        for args in [
            ("gaia_external_site_runs_total", "External public site total runs", total),
            ("gaia_external_site_success_count", "External public site success count", site_success),
            ("gaia_external_site_success_rate", "External public site success rate", _safe_ratio(site_success, total)),
            ("gaia_external_site_avg_duration_seconds", "External public site average duration seconds", _mean_or_none(durations)),
            ("gaia_external_site_latest_status", "External public site latest status (1=SUCCESS 0=not success)", latest_ok),
            ("gaia_external_site_blocked_count", "External public site blocked user-action count", blocked),
        ]:
            lines.extend(_gauge(args[0], args[1], args[2], site_base, declared))

        for code, count in _reason_code_counts(suite_rows).items():
            lines.extend(_gauge(
                "gaia_external_site_reason_code_count",
                "External public site reason code count",
                count,
                {**site_base, "reason_code": code},
                declared,
            ))

    for category, category_rows in sorted(rows_by_category.items()):
        total = len(category_rows)
        success = sum(1 for row in category_rows if _status_is_success(row))
        category_base = {
            "pack_id": pack_id,
            "category": category,
            "model": model,
            "provider": provider,
            "runner_id": runner_id,
        }
        for args in [
            ("gaia_external_category_runs_total", "External public category total runs", total),
            ("gaia_external_category_success_count", "External public category success count", success),
            ("gaia_external_category_success_rate", "External public category success rate", _safe_ratio(success, total)),
            ("gaia_external_category_avg_duration_seconds", "External public category average duration seconds", _mean_or_none(_numeric_values(category_rows, "duration_seconds"))),
        ]:
            lines.extend(_gauge(args[0], args[1], args[2], category_base, declared))

        for code, count in _reason_code_counts(category_rows).items():
            lines.extend(_gauge(
                "gaia_external_reason_code_count",
                "External public reason code count by category",
                count,
                {**category_base, "reason_code": code},
                declared,
            ))

    return "\n".join(lines) + ("\n" if lines else "")


# ── 실행 히스토리 관리 ────────────────────────────────────────────────────


def load_history(suite_id: str) -> list[dict]:
    """suite_id 에 해당하는 누적 실행 히스토리를 로드.
    파일이 없거나 읽기 실패 시 빈 리스트 반환.
    """
    path = HISTORY_DIR / f"{suite_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_history(suite_id: str, rows: list[dict]) -> None:
    """누적 실행 히스토리를 로컬 파일에 저장."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{suite_id}.json"
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def merge_into_history(
    history: list[dict],
    new_rows: list[dict],
    suite_dir_name: str,
) -> list[dict]:
    """새 실행 결과를 히스토리에 병합 (scenario_id + started_at 기준 중복 제거).
    병합 후 started_at 기준 오름차순 정렬 → runs[-1] 이 항상 최신 실행.
    """
    existing_keys: set[tuple[str, str]] = {
        (str(r.get("scenario_id") or ""), str(r.get("started_at") or ""))
        for r in history
    }
    for row in new_rows:
        key = (str(row.get("scenario_id") or ""), str(row.get("started_at") or ""))
        if key not in existing_keys:
            history.append({**row, "_suite_dir": suite_dir_name})
            existing_keys.add(key)

    # started_at 기준 정렬 (없으면 빈 문자열로 처리 → 맨 앞)
    history.sort(key=lambda r: str(r.get("started_at") or ""))
    return history


# ── Pushgateway 전송 ───────────────────────────────────────────────────────

def push_to_gateway(metrics_text: str, instance: str, gateway_url: str, token: str | None) -> bool:
    safe_instance = quote(str(instance or "unknown"), safe="")
    url = urljoin(gateway_url.rstrip("/") + "/", f"metrics/job/gaia_benchmark/instance/{safe_instance}")
    kwargs: dict = {
        "data": metrics_text.encode("utf-8"),
        "headers": {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
        "timeout": 10,
    }
    if token:
        kwargs["auth"] = (PUSH_USER, token)
    try:
        resp = requests.post(url, **kwargs)
        resp.raise_for_status()
        return True
    except requests.exceptions.ConnectionError:
        print(f"  [오류] 서버에 연결할 수 없습니다: {gateway_url}", file=sys.stderr)
        print("  팀장에게 서버 상태를 확인하세요.", file=sys.stderr)
        return False
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            print("  [오류] 인증 실패(401). 재연결: python scripts/gaia_monitor_connect.py <주소> --token <토큰>", file=sys.stderr)
        else:
            print(f"  [오류] HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        return False


# ── 메인 ──────────────────────────────────────────────────────────────────

def find_latest_suite_dir() -> Path | None:
    if not ARTIFACTS_DIR.exists():
        return None
    dirs = sorted(
        [d for d in ARTIFACTS_DIR.iterdir() if d.is_dir() and (d / "summary.json").exists()],
        key=lambda d: d.stat().st_mtime, reverse=True,
    )
    return dirs[0] if dirs else None


def infer_shared_suite_key(summary: dict, suite_json_path: Path | None = None) -> str:
    suite_id = str(summary.get("suite_id") or "").strip()
    if suite_id:
        return re.sub(r"_public_v\d+$", "", suite_id)
    if suite_json_path is not None:
        return suite_json_path.stem.removeprefix("custom_").removesuffix("_suite")
    return "unknown"


def push_shared_suite_json(
    suite_json_path: Path,
    *,
    summary: dict,
    gateway_url: str,
    token: str | None,
    suite_key: str | None = None,
) -> bool:
    suite_payload = load_json(suite_json_path)
    if not isinstance(suite_payload, dict):
        print(f"  [suite 공유 건너뜀] suite JSON을 읽지 못했습니다: {suite_json_path}", file=sys.stderr)
        return False
    if not suite_payload.get("scenarios"):
        print("  [suite 공유 건너뜀] 공유할 테스트가 없습니다.", file=sys.stderr)
        return False
    key = str(suite_key or infer_shared_suite_key(summary, suite_json_path)).strip()
    try:
        upload_shared_suite(server=gateway_url, token=token, suite_key=key, suite_payload=suite_payload)
    except SharedSuiteError as exc:
        print(f"  [suite 공유 실패] {exc}", file=sys.stderr)
        return False
    print(f"  [suite 공유 완료] {key}")
    return True


def push_suite_dir(
    suite_dir: Path,
    gateway_url: str,
    token: str | None,
    *,
    suite_json_path: Path | None = None,
    suite_key: str | None = None,
    share_suite: bool = False,
) -> bool:
    summary = load_json(suite_dir / "summary.json")
    results = load_json(suite_dir / "results.json")
    if not isinstance(results, list):
        results = []

    if not summary:
        print(f"  [건너뜀] summary.json 없음: {suite_dir.name}")
        return False

    suite_id = summary.get("suite_id") or summary.get("pack_id") or suite_dir.name
    print(f"  push → {suite_dir.name}")

    # ── 히스토리 병합: 현재 실행 결과를 누적 히스토리에 추가 후 저장 ──
    history = load_history(suite_id)
    before_count = len(history)
    history = merge_into_history(history, results or [], suite_dir.name)
    added = len(history) - before_count
    save_history(suite_id, history)
    print(f"  [히스토리] 누적 {len(history)}건 (+{added}건 신규)")

    # declared 집합을 공유해서 HELP/TYPE 중복 방지
    declared: set = set()
    suite_metrics    = build_suite_metrics(summary, declared, suite_json_path=suite_json_path)
    # 시나리오 메트릭은 히스토리 전체 기반으로 계산 → 누적 통계 반영
    scenario_metrics = (
        build_scenario_metrics(summary, history, declared, suite_json_path=suite_json_path)
        if history
        else ""
    )
    pack_metrics = build_external_pack_metrics(summary, results or [], declared)

    full_metrics = suite_metrics + scenario_metrics + pack_metrics
    instance = suite_id  # suite_id(or pack_id)는 실행 간 안정적인 값 → Pushgateway에서 덮어쓰기로 최신 상태 유지

    if push_to_gateway(full_metrics, instance, gateway_url, token):
        print(f"  [완료] {suite_id} ({len(results or [])}개 시나리오)")
        if share_suite and suite_json_path is not None:
            push_shared_suite_json(
                suite_json_path,
                summary=summary,
                gateway_url=gateway_url,
                token=token,
                suite_key=suite_key,
            )
        return True
    else:
        print(f"  [실패] {suite_id}")
        return False


def push_all_suite_info(gateway_url: str, token: str | None) -> None:
    """gaia/tests/scenarios/ 의 suite JSON 파일을 읽어 gaia_scenario_info 메트릭을 push.
    팀원 결과가 없어도 suite 정의만으로 시나리오 설명을 Grafana에 표시할 수 있음."""
    suites_dir = WORKSPACE_ROOT / "gaia" / "tests" / "scenarios"
    if not suites_dir.exists():
        print(f"[오류] suite 디렉토리를 찾을 수 없습니다: {suites_dir}", file=sys.stderr)
        return

    suite_files = list(suites_dir.glob("*.json"))
    ok = 0
    for suite_file in sorted(suite_files):
        suite_data = load_json(suite_file)
        if not isinstance(suite_data, dict):
            continue
        suite_id = suite_data.get("suite_id")
        site_name = suite_data.get("site", {}).get("name", "unknown")
        scenarios = suite_data.get("scenarios", [])
        if not suite_id or not scenarios:
            continue

        declared: set = set()
        lines = []
        for sc in scenarios:
            scenario_id = sc.get("id", "")
            goal = str(sc.get("goal") or "")[:200].replace("\n", " ")
            if not scenario_id:
                continue
            lines.extend(_gauge(
                "gaia_scenario_info",
                "Scenario presence marker with description",
                1,
                {"suite_id": suite_id, "scenario_id": scenario_id, "site": site_name, "goal": goal},
                declared,
            ))

        if not lines:
            continue

        metrics_text = "\n".join(lines) + "\n"
        if push_to_gateway(metrics_text, f"suite_info_{suite_id}", gateway_url, token):
            print(f"  [완료] {suite_id} ({len(scenarios)}개 시나리오)")
            ok += 1
        else:
            print(f"  [실패] {suite_id}")

    print(f"\n{ok}/{len(suite_files)}개 suite 정보 전송 완료")


def main():
    parser = argparse.ArgumentParser(description="GAIA 벤치마크 메트릭을 팀 모니터링 서버로 전송")
    parser.add_argument("--suite-dir", type=Path, help="특정 벤치마크 디렉토리 경로")
    parser.add_argument("--all", action="store_true", help="모든 벤치마크 결과 전송")
    parser.add_argument("--push-suite-info", action="store_true", help="suite JSON 파일에서 시나리오 설명을 직접 push")
    parser.add_argument("--gateway", help="Pushgateway URL 직접 지정")
    parser.add_argument("--token",   help="토큰 직접 지정")
    parser.add_argument("--suite-json", type=Path, help="metrics와 함께 공유할 원본 suite JSON")
    parser.add_argument("--suite-key", help="공유 suite key. 기본값은 suite_id에서 추론")
    parser.add_argument("--no-share-suite", action="store_true", help="--suite-json이 있어도 suite 정의를 공유하지 않음")
    parser.add_argument("--reset-history", metavar="SUITE_ID",
                        help="지정한 suite_id 의 로컬 누적 히스토리를 초기화 (파일 삭제)")
    args = parser.parse_args()

    if args.gateway:
        gateway_url, token = args.gateway, args.token
    else:
        cfg = load_monitoring_config()
        if cfg:
            gateway_url, token = cfg["server"], cfg["token"]
            print(f"  서버: {gateway_url}")
        else:
            print("[오류] 연결된 모니터링 서버가 없습니다.")
            print("팀장에게 연결 명령어를 받아 실행하세요:")
            print("  python scripts/gaia_monitor_connect.py <서버주소> --token <토큰>")
            sys.exit(1)

    if args.reset_history:
        path = HISTORY_DIR / f"{args.reset_history}.json"
        if path.exists():
            path.unlink()
            print(f"[완료] {args.reset_history} 히스토리 초기화됨: {path}")
        else:
            print(f"[정보] 히스토리 파일 없음 (이미 비어있음): {path}")
        return

    if args.push_suite_info:
        push_all_suite_info(gateway_url, token)
        return

    if args.suite_dir:
        suite_dirs = [args.suite_dir]
    elif args.all:
        if not ARTIFACTS_DIR.exists():
            print(f"벤치마크 디렉토리가 없습니다: {ARTIFACTS_DIR}")
            sys.exit(1)
        suite_dirs = sorted(
            [d for d in ARTIFACTS_DIR.iterdir() if d.is_dir() and (d / "summary.json").exists()],
            key=lambda d: d.stat().st_mtime,
        )
    else:
        latest = find_latest_suite_dir()
        if not latest:
            print("전송할 벤치마크 결과가 없습니다.")
            sys.exit(1)
        suite_dirs = [latest]

    share_suite = bool(args.suite_json) and not bool(args.no_share_suite)
    ok = sum(
        push_suite_dir(
            d,
            gateway_url,
            token,
            suite_json_path=args.suite_json,
            suite_key=args.suite_key,
            share_suite=share_suite,
        )
        for d in suite_dirs
    )
    print(f"\n{ok}/{len(suite_dirs)}개 전송 완료")


if __name__ == "__main__":
    main()
