#!/usr/bin/env python3
"""
GAIA 시연회 데모 라운드 결과 기록 스크립트.

체험자 결과를 입력하면 GAIA 벤치마크 결과와 합산해
Prometheus Pushgateway 로 전송하고 Grafana 대시보드에 반영합니다.

사용법:
  # 라운드 결과 기록 (GAIA 결과 artifacts에서 자동 탐색)
  python scripts/push_demo_result.py \\
      --round 1 --scenario MELON_002_CHART_LIST \\
      --human-time 53 --human-success

  # 체험자 실패한 경우
  python scripts/push_demo_result.py \\
      --round 2 --scenario MUSINSA_001 \\
      --human-time 90 --human-fail

  # 현재 스코어 보기
  python scripts/push_demo_result.py --status

  # 데모 초기화 (전체 리셋)
  python scripts/push_demo_result.py --reset
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = WORKSPACE_ROOT / "artifacts" / "benchmarks"
MONITORING_CONFIG = Path.home() / ".gaia" / "monitoring.json"
STATE_FILE = Path.home() / ".gaia" / "demo_state.json"
PUSH_USER = "gaia"

_SUCCESS_STATUSES = {"SUCCESS"}
_FAIL_STATUSES    = {"FAIL", "BLOCKED_USER_ACTION", "ERROR", "TIMEOUT"}


# ── 설정·상태 ───────────────────────────────────────────────────────────────

def load_config() -> dict | None:
    if MONITORING_CONFIG.exists():
        try:
            return json.loads(MONITORING_CONFIG.read_text())
        except Exception:
            return None
    return None


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        "rounds": [],
        "gaia_wins": 0,
        "human_wins": 0,
        "draws": 0,
        "gaia_success": 0,
        "human_success": 0,
        "latest_speed_ratio": None,
    }


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── GAIA 결과 자동 탐색 ──────────────────────────────────────────────────────

def find_latest_gaia_result(scenario_id: str | None = None) -> dict | None:
    """artifacts/benchmarks 에서 가장 최근 결과 반환.
    scenario_id 가 주어지면 해당 시나리오만, 없으면 전체 중 가장 최근 결과."""
    if not ARTIFACTS_DIR.exists():
        return None
    best: dict | None = None
    best_mtime = 0.0
    for d in sorted(ARTIFACTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        results_file = d / "results.json"
        if not results_file.exists():
            continue
        try:
            rows = json.loads(results_file.read_text(encoding="utf-8"))
            for row in rows:
                sid = str(row.get("scenario_id") or "")
                if scenario_id and sid != scenario_id:
                    continue
                mtime = results_file.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best = row
        except Exception:
            continue
        if best and not scenario_id:
            break  # 시나리오 ID 미지정 시 가장 최근 디렉토리 첫 결과로 확정
    return best


# ── Prometheus 텍스트 빌더 ────────────────────────────────────────────────────

def _gauge(name: str, help_text: str, value: float, labels: dict,
           declared: set | None = None) -> list[str]:
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    lines: list[str] = []
    if declared is None or name not in declared:
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        if declared is not None:
            declared.add(name)
    lines.append(f"{name}{{{label_str}}} {float(value)}")
    return lines


def build_cumulative_metrics(state: dict) -> str:
    declared: set = set()
    lines: list[str] = []
    total = len(state["rounds"])

    lines.extend(_gauge("gaia_demo_score", "데모 누적 승수",
                        state["gaia_wins"], {"player": "gaia"}, declared))
    lines.extend(_gauge("gaia_demo_score", "데모 누적 승수",
                        state["human_wins"], {"player": "human"}, declared))
    lines.extend(_gauge("gaia_demo_rounds_total", "총 라운드 수", total, {}, declared))
    lines.extend(_gauge("gaia_demo_draws", "무승부 수", state["draws"], {}, declared))

    if total > 0:
        gaia_rounds  = [r for r in state["rounds"] if r.get("gaia_time") is not None]
        human_rounds = [r for r in state["rounds"] if r.get("human_time") is not None]

        if gaia_rounds:
            gaia_avg = sum(r["gaia_time"] for r in gaia_rounds) / len(gaia_rounds)
            lines.extend(_gauge("gaia_demo_avg_duration_seconds", "플레이어별 평균 소요 시간 (초)",
                                gaia_avg, {"player": "gaia"}, declared))
        if human_rounds:
            human_avg = sum(r["human_time"] for r in human_rounds) / len(human_rounds)
            lines.extend(_gauge("gaia_demo_avg_duration_seconds", "플레이어별 평균 소요 시간 (초)",
                                human_avg, {"player": "human"}, declared))

        lines.extend(_gauge("gaia_demo_success_rate", "플레이어별 성공률 (0-1)",
                            state["gaia_success"] / total, {"player": "gaia"}, declared))
        lines.extend(_gauge("gaia_demo_success_rate", "플레이어별 성공률 (0-1)",
                            state["human_success"] / total, {"player": "human"}, declared))

    if state.get("latest_speed_ratio") is not None:
        lines.extend(_gauge("gaia_demo_latest_speed_ratio",
                            "최신 라운드 체험자/GAIA 소요시간 비율 (클수록 GAIA 빠름)",
                            state["latest_speed_ratio"], {}, declared))

    return "\n".join(lines) + "\n"


def build_round_metrics(round_num: int, scenario_id: str,
                        human_time: float, human_ok: bool,
                        gaia_time: float | None, gaia_ok: bool | None,
                        winner: str, speed_ratio: float | None) -> str:
    declared: set = set()
    lines: list[str] = []
    base = {"round": str(round_num), "scenario_id": scenario_id}

    lines.extend(_gauge("gaia_demo_round_human_duration_seconds",
                        "체험자 소요 시간 (초)", human_time, base, declared))
    lines.extend(_gauge("gaia_demo_round_human_status",
                        "체험자 성공 여부 (1=성공 0=실패)",
                        1.0 if human_ok else 0.0, base, declared))

    if gaia_time is not None:
        lines.extend(_gauge("gaia_demo_round_gaia_duration_seconds",
                            "GAIA 소요 시간 (초)", gaia_time, base, declared))
    if gaia_ok is not None:
        lines.extend(_gauge("gaia_demo_round_gaia_status",
                            "GAIA 성공 여부 (1=성공 0=실패)",
                            1.0 if gaia_ok else 0.0, base, declared))
    if speed_ratio is not None:
        lines.extend(_gauge("gaia_demo_round_speed_ratio",
                            "체험자/GAIA 소요시간 비율 (클수록 GAIA 빠름)",
                            speed_ratio, base, declared))

    lines.extend(_gauge("gaia_demo_round_winner",
                        "라운드 승자 (winner 라벨 참조)",
                        1.0, {**base, "winner": winner}, declared))

    return "\n".join(lines) + "\n"


# ── 승자 결정 ─────────────────────────────────────────────────────────────────

def determine_winner(human_time: float, human_ok: bool,
                     gaia_time: float | None, gaia_ok: bool | None) -> str:
    if gaia_ok is None:
        return "unknown"
    if human_ok and gaia_ok:
        return "gaia" if gaia_time is not None and gaia_time < human_time else "human"
    if human_ok and not gaia_ok:
        return "human"
    if not human_ok and gaia_ok:
        return "gaia"
    return "draw"


# ── Pushgateway 전송 ─────────────────────────────────────────────────────────

def _push(metrics_text: str, instance: str, gateway_url: str,
          token: str | None) -> bool:
    safe_instance = quote(str(instance), safe="")
    url = urljoin(gateway_url.rstrip("/") + "/",
                  f"metrics/job/gaia_benchmark/instance/{safe_instance}")
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
        print(f"  [오류] 서버 연결 실패: {gateway_url}", file=sys.stderr)
        return False
    except requests.exceptions.HTTPError as e:
        print(f"  [오류] HTTP {e.response.status_code}: {e.response.text}",
              file=sys.stderr)
        return False


def _delete_instance(instance: str, gateway_url: str, token: str | None) -> None:
    safe_instance = quote(str(instance), safe="")
    url = urljoin(gateway_url.rstrip("/") + "/",
                  f"metrics/job/gaia_benchmark/instance/{safe_instance}")
    kwargs: dict = {"timeout": 10}
    if token:
        kwargs["auth"] = (PUSH_USER, token)
    try:
        requests.delete(url, **kwargs)
    except Exception:
        pass


# ── 콘솔 출력 ────────────────────────────────────────────────────────────────

def print_status(state: dict) -> None:
    total = len(state["rounds"])
    print()
    print("══════════════════════════════════════")
    print("  🏆  GAIA 시연회 현황")
    print("══════════════════════════════════════")
    print(f"  진행 라운드: {total}회")
    print(f"  🤖 GAIA:    {state['gaia_wins']}승")
    print(f"  👤 체험자:  {state['human_wins']}승")
    print(f"  🤝 무승부:  {state['draws']}회")
    if total > 0:
        print()
        for r in state["rounds"]:
            winner_icon = {"gaia": "🤖", "human": "👤",
                           "draw": "🤝", "unknown": "❓"}.get(r["winner"], "?")
            gaia_t  = f"{r['gaia_time']:.1f}초" if r.get("gaia_time") is not None else "  ?"
            gaia_s  = "✅" if r.get("gaia_ok") else "❌"
            human_s = "✅" if r["human_ok"] else "❌"
            ratio   = f" ({r['gaia_time'] and r['human_time']/r['gaia_time']:.1f}×)" \
                      if r.get("gaia_time") else ""
            print(f"  R{r['round']:2d}  {r['scenario_id']:<25s}"
                  f"  👤{r['human_time']:.0f}초{human_s}"
                  f"  🤖{gaia_t}{gaia_s}{ratio}"
                  f"  {winner_icon}")
    print("══════════════════════════════════════")
    print()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="GAIA 시연회 라운드 결과 기록",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--round", type=int, help="라운드 번호 (1부터)")
    parser.add_argument("--scenario", help="시나리오 ID (예: MELON_002_CHART_LIST)")
    parser.add_argument("--human-time", type=float, help="체험자 소요 시간 (초)")

    human_result = parser.add_mutually_exclusive_group()
    human_result.add_argument("--human-success", dest="human_ok",
                              action="store_true", default=None,
                              help="체험자 성공")
    human_result.add_argument("--human-fail", dest="human_ok",
                              action="store_false",
                              help="체험자 실패")

    parser.add_argument("--status", action="store_true", help="현재 스코어 확인")
    parser.add_argument("--reset",  action="store_true", help="데모 전체 초기화")
    parser.add_argument("--gateway", help="Pushgateway URL 직접 지정")
    parser.add_argument("--token",   help="토큰 직접 지정")
    args = parser.parse_args()

    # ── 설정 로드 ──────────────────────────────────────────────────────────
    if args.gateway:
        gateway_url, token = args.gateway, args.token
    else:
        cfg = load_config()
        if not cfg:
            print("[오류] 모니터링 서버 미설정. "
                  "python scripts/gaia_monitor_connect.py 먼저 실행하세요.",
                  file=sys.stderr)
            sys.exit(1)
        gateway_url, token = cfg["server"], cfg["token"]

    state = load_state()

    # ── 상태 조회 ─────────────────────────────────────────────────────────
    if args.status:
        print_status(state)
        return

    # ── 초기화 ────────────────────────────────────────────────────────────
    if args.reset:
        print("데모 초기화 중...")
        _delete_instance("gaia_demo", gateway_url, token)
        for r in state["rounds"]:
            _delete_instance(f"gaia_demo_r{r['round']}", gateway_url, token)
        empty = {"rounds": [], "gaia_wins": 0, "human_wins": 0, "draws": 0,
                 "gaia_success": 0, "human_success": 0, "latest_speed_ratio": None}
        save_state(empty)
        print("✅ 초기화 완료")
        return

    # ── 입력 검증 ─────────────────────────────────────────────────────────
    errors = []
    if not args.round:
        errors.append("--round 필요")
    if args.human_time is None:
        errors.append("--human-time 필요")
    if args.human_ok is None:
        errors.append("--human-success 또는 --human-fail 필요")
    if errors:
        parser.error(" / ".join(errors))

    human_ok   = bool(args.human_ok)
    human_time = args.human_time

    # ── GAIA 결과 자동 탐색 ────────────────────────────────────────────────
    label = args.scenario if args.scenario else "최근 실행"
    print(f"\n  GAIA 결과 탐색 중... ({label})")
    gaia_row = find_latest_gaia_result(args.scenario)
    if gaia_row:
        gaia_time: float | None = gaia_row.get("duration_seconds")
        raw_status = str(gaia_row.get("status") or "").upper()
        gaia_ok: bool | None = raw_status in _SUCCESS_STATUSES
        # --scenario 미입력 시 자동 탐지된 시나리오 ID 사용
        if not args.scenario:
            args.scenario = str(gaia_row.get("scenario_id") or "unknown")
            print(f"  🔍 시나리오 자동 감지: {args.scenario}")
        print(f"  ✅ GAIA 결과: {gaia_time:.1f}초, "
              f"{'성공' if gaia_ok else '실패'} ({raw_status})")
    else:
        gaia_time = None
        gaia_ok   = None
        print(f"  ⚠️  GAIA 결과를 찾을 수 없음 (벤치마크가 실행됐는지 확인하세요)")

    # ── 승자 결정 ─────────────────────────────────────────────────────────
    winner = determine_winner(human_time, human_ok, gaia_time, gaia_ok)
    speed_ratio: float | None = (
        round(human_time / gaia_time, 2)
        if gaia_time and gaia_time > 0 else None
    )

    # ── 상태 업데이트 ─────────────────────────────────────────────────────
    if winner == "gaia":
        state["gaia_wins"] += 1
    elif winner == "human":
        state["human_wins"] += 1
    elif winner == "draw":
        state["draws"] += 1

    state["gaia_success"]  += 1 if gaia_ok  else 0
    state["human_success"] += 1 if human_ok else 0
    state["latest_speed_ratio"] = speed_ratio
    state["rounds"].append({
        "round":       args.round,
        "scenario_id": args.scenario,
        "human_time":  human_time,
        "human_ok":    human_ok,
        "gaia_time":   gaia_time,
        "gaia_ok":     gaia_ok,
        "winner":      winner,
    })
    save_state(state)

    # ── 메트릭 빌드 & 전송 ────────────────────────────────────────────────
    cumulative_text = build_cumulative_metrics(state)
    round_text      = build_round_metrics(
        args.round, args.scenario, human_time, human_ok,
        gaia_time, gaia_ok, winner, speed_ratio,
    )

    ok1 = _push(cumulative_text, "gaia_demo", gateway_url, token)
    ok2 = _push(round_text, f"gaia_demo_r{args.round}", gateway_url, token)

    # ── 결과 출력 ─────────────────────────────────────────────────────────
    if ok1 and ok2:
        winner_label = {
            "gaia":    "🤖 GAIA 승!",
            "human":   "👤 체험자 승!",
            "draw":    "🤝 무승부",
            "unknown": "❓ 미정 (GAIA 결과 없음)",
        }.get(winner, winner)

        print()
        print(f"  ┌─ {args.round}라운드 결과 ({args.scenario}) ─────────────")
        print(f"  │  👤 체험자: {human_time:.0f}초  {'✅ 성공' if human_ok else '❌ 실패'}")
        if gaia_time is not None:
            print(f"  │  🤖 GAIA:   {gaia_time:.1f}초  {'✅ 성공' if gaia_ok else '❌ 실패'}")
        if speed_ratio:
            print(f"  │  ⚡ GAIA가 {speed_ratio:.1f}배 빠름")
        print(f"  │  → {winner_label}")
        print(f"  └─────────────────────────────────────────")
        print(f"  누적: 🤖 {state['gaia_wins']}승 / "
              f"👤 {state['human_wins']}승 / "
              f"🤝 {state['draws']}무")
        print(f"  ✅ Grafana 전송 완료\n")
    else:
        print("  ❌ 전송 실패", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
