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
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from gaia.src.benchmark_suite_sharing import SharedSuiteError, upload_shared_suite

ARTIFACTS_DIR = Path(__file__).parent.parent / "artifacts" / "benchmarks"
MONITORING_CONFIG = Path.home() / ".gaia" / "monitoring.json"
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


def build_suite_metrics(summary: dict, declared: set | None = None) -> str:
    """suite 전체 KPI 메트릭 (summary.json 기반)."""
    if declared is None:
        declared = set()
    lines = []
    base = {
        "suite_id": summary.get("suite_id", "unknown"),
        "site":     summary.get("site", {}).get("name", "unknown"),
        "model":    summary.get("model", "unknown"),
        "provider": summary.get("provider", "unknown"),
    }
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


def build_scenario_metrics(summary: dict, results: list, declared: set | None = None) -> str:
    """시나리오별 상세 메트릭 (results.json 기반)."""
    if declared is None:
        declared = set()
    lines = []

    suite_id  = summary.get("suite_id", "unknown")
    site_name = summary.get("site", {}).get("name", "unknown")
    started_ts = _timestamp_seconds(summary.get("started_at"))

    # scenario_id → 해당 실행 목록
    from collections import defaultdict
    scenario_runs: dict[str, list] = defaultdict(list)
    for row in results:
        sid = row.get("scenario_id", "unknown")
        scenario_runs[sid].append(row)

    for scenario_id, runs in scenario_runs.items():
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

        base = {
            "suite_id":    suite_id,
            "scenario_id": scenario_id,
            "site":        site_name,
            "model":       model,
            "provider":    provider,
        }

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
        lines.extend(_gauge(
            "gaia_scenario_last_status",
            "Last run result (1=SUCCESS 0=FAIL)",
            last_status_ok,
            {**base, "completion": completion},
            declared,
        ))
        goal = str(runs[0].get("goal") or "")[:200].replace("\n", " ")
        lines.extend(_gauge(
            "gaia_scenario_info",
            "Scenario presence marker with description",
            1,
            {"suite_id": suite_id, "scenario_id": scenario_id, "site": site_name, "goal": goal},
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

    if not summary:
        print(f"  [건너뜀] summary.json 없음: {suite_dir.name}")
        return False

    suite_id = summary.get("suite_id", suite_dir.name)
    print(f"  push → {suite_dir.name}")

    # declared 집합을 공유해서 HELP/TYPE 중복 방지
    declared: set = set()
    suite_metrics    = build_suite_metrics(summary, declared)
    scenario_metrics = build_scenario_metrics(summary, results or [], declared) if results else ""

    full_metrics = suite_metrics + scenario_metrics
    instance = str(suite_id or suite_dir.name)

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
