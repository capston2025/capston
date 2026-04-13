#!/usr/bin/env python3
"""
GAIA 벤치마크 메트릭을 Prometheus Pushgateway로 전송하는 스크립트.

사용법:
  # 가장 최근 벤치마크 1개 push
  python scripts/push_metrics.py

  # 특정 디렉토리 지정
  python scripts/push_metrics.py --suite-dir artifacts/benchmarks/my_suite_123

  # 모든 기존 결과 한꺼번에 push (초기 마이그레이션)
  python scripts/push_metrics.py --all

  # Pushgateway 주소 변경 (클라우드 VM IP 사용 시)
  python scripts/push_metrics.py --gateway http://1.2.3.4:9091
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

ARTIFACTS_DIR = Path(__file__).parent.parent / "artifacts" / "benchmarks"
DEFAULT_GATEWAY = os.environ.get("PUSHGATEWAY_URL", "http://localhost:9091")


def load_summary(suite_dir: Path) -> dict | None:
    summary_path = suite_dir / "summary.json"
    if not summary_path.exists():
        return None
    with open(summary_path) as f:
        return json.load(f)


def build_metrics_text(summary: dict) -> str:
    """Prometheus exposition 형식 텍스트 생성."""
    lines = []
    suite_id = summary.get("suite_id", "unknown")
    site_name = summary.get("site", {}).get("name", "unknown")
    model = summary.get("model", "unknown")
    provider = summary.get("provider", "unknown")
    started_at = summary.get("started_at", "")

    labels = f'suite_id="{suite_id}",site="{site_name}",model="{model}",provider="{provider}"'

    metrics = summary.get("metrics", {})
    kpi = summary.get("kpi_metrics", {})
    targets = kpi.get("targets", {})
    counts = kpi.get("counts", {})

    def add_gauge(name: str, help_text: str, value, extra_labels: str = ""):
        if value is None:
            return
        full_labels = f"{labels},{extra_labels}" if extra_labels else labels
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f'{name}{{{full_labels}}} {float(value)}')

    # 기본 메트릭
    add_gauge("gaia_runs_total", "Total number of runs", metrics.get("runs_total"))
    add_gauge("gaia_success_rate", "Overall success rate (0-1)", metrics.get("success_rate"))
    add_gauge("gaia_avg_time_seconds", "Average execution time in seconds", metrics.get("avg_time_seconds"))

    # KPI 메트릭
    add_gauge("gaia_scenario_success_rate", "Scenario success rate (0-1)", kpi.get("scenario_success_rate"))
    add_gauge("gaia_reproducibility_rate", "Reproducibility rate across repeat runs (0-1)", kpi.get("reproducibility_rate"))
    add_gauge("gaia_progress_stop_failure_rate", "Rate of timeout/stuck/DOM-not-found failures (0-1)", kpi.get("progress_stop_failure_rate"))
    add_gauge("gaia_self_recovery_rate", "Recovery success rate after failure (0-1)", kpi.get("self_recovery_rate"))
    add_gauge("gaia_intervention_rate", "Rate requiring human intervention (0-1)", kpi.get("intervention_rate"))

    # KPI 목표값 (참조선으로 사용)
    add_gauge("gaia_target_scenario_success_rate", "Target scenario success rate", targets.get("scenario_success_rate"))
    add_gauge("gaia_target_reproducibility_rate", "Target reproducibility rate", targets.get("reproducibility_rate"))
    add_gauge("gaia_target_progress_stop_failure_rate", "Target max progress stop failure rate", targets.get("progress_stop_failure_rate"))
    add_gauge("gaia_target_self_recovery_rate", "Target self recovery rate", targets.get("self_recovery_rate"))
    add_gauge("gaia_target_intervention_rate", "Target max intervention rate", targets.get("intervention_rate"))

    # 카운트 메트릭
    add_gauge("gaia_count_success", "Number of successful scenarios", counts.get("success"))
    add_gauge("gaia_count_blocked", "Number of blocked scenarios", counts.get("blocked"))
    add_gauge("gaia_count_progress_stop_failures", "Number of progress stop failures", counts.get("progress_stop_failures"))
    add_gauge("gaia_count_recovery_runs", "Number of recovery runs", counts.get("recovery_runs"))
    add_gauge("gaia_count_recovery_success", "Number of successful recoveries", counts.get("recovery_success"))

    # 상태별 카운트
    status_counts = summary.get("status_counts", {})
    lines.append("# HELP gaia_status_count Count of runs by final status")
    lines.append("# TYPE gaia_status_count gauge")
    for status, count in status_counts.items():
        lines.append(f'gaia_status_count{{{labels},status="{status}"}} {count}')

    return "\n".join(lines) + "\n"


def push_to_gateway(metrics_text: str, suite_id: str, gateway_url: str) -> bool:
    """Pushgateway에 메트릭 전송."""
    # job 이름은 영숫자/언더스코어만 사용
    job_name = "gaia_benchmark"
    instance = suite_id.replace("/", "_").replace(" ", "_")

    url = urljoin(gateway_url.rstrip("/") + "/", f"metrics/job/{job_name}/instance/{instance}")

    try:
        resp = requests.post(
            url,
            data=metrics_text.encode("utf-8"),
            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.ConnectionError:
        print(f"  [오류] Pushgateway에 연결할 수 없습니다: {gateway_url}", file=sys.stderr)
        print("  Docker가 실행 중인지 확인하세요: cd monitoring && docker compose up -d", file=sys.stderr)
        return False
    except requests.exceptions.HTTPError as e:
        print(f"  [오류] HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        return False


def find_latest_suite_dir() -> Path | None:
    if not ARTIFACTS_DIR.exists():
        return None
    suite_dirs = sorted(
        [d for d in ARTIFACTS_DIR.iterdir() if d.is_dir() and (d / "summary.json").exists()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return suite_dirs[0] if suite_dirs else None


def main():
    parser = argparse.ArgumentParser(description="GAIA 벤치마크 메트릭을 Pushgateway로 전송")
    parser.add_argument("--suite-dir", type=Path, help="특정 벤치마크 디렉토리 경로")
    parser.add_argument("--all", action="store_true", help="모든 벤치마크 결과 전송")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help=f"Pushgateway URL (기본: {DEFAULT_GATEWAY})")
    args = parser.parse_args()

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

    success_count = 0
    for suite_dir in suite_dirs:
        summary = load_summary(suite_dir)
        if not summary:
            print(f"  [건너뜀] summary.json 없음: {suite_dir.name}")
            continue

        suite_id = summary.get("suite_id", suite_dir.name)
        print(f"  push: {suite_dir.name} (suite_id={suite_id})")

        metrics_text = build_metrics_text(summary)
        if push_to_gateway(metrics_text, suite_dir.name, args.gateway):
            print(f"  [완료] {suite_id}")
            success_count += 1
        else:
            print(f"  [실패] {suite_id}")

    print(f"\n{success_count}/{len(suite_dirs)}개 전송 완료 → Grafana: http://localhost:3000")


if __name__ == "__main__":
    main()
