#!/usr/bin/env python3
"""
[팀원용] GAIA 모니터링 서버에 연결하는 스크립트.

팀장이 공유한 명령어를 그대로 붙여넣기만 하면 됩니다:

  python scripts/gaia_monitor_connect.py http://<서버IP>:9091 --token <토큰>

이후 벤치마크 실행 시 자동으로 팀 서버에 결과가 업로드됩니다.
설정은 ~/.gaia/monitoring.json 에 저장됩니다.
"""

import argparse
import json
import sys
from pathlib import Path

import requests

CONFIG_PATH = Path.home() / ".gaia" / "monitoring.json"


def save_config(server: str, token: str):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = {"server": server, "token": token}
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    print(f"  설정 저장: {CONFIG_PATH}")


def test_connection(server: str, token: str) -> bool:
    """Pushgateway에 테스트 메트릭을 보내서 연결 확인."""
    test_payload = "# HELP gaia_connect_test Connection test\n# TYPE gaia_connect_test gauge\ngaia_connect_test 1\n"
    url = f"{server.rstrip('/')}/metrics/job/gaia_connect_test"
    try:
        resp = requests.post(
            url,
            data=test_payload.encode(),
            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
            auth=("gaia", token),
            timeout=8,
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.ConnectionError:
        print(f"\n  [오류] 서버에 연결할 수 없습니다: {server}")
        print("  - 서버 주소가 맞는지 확인하세요.")
        print("  - 서버가 실행 중인지 팀장에게 확인하세요.")
        return False
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            print(f"\n  [오류] 토큰이 올바르지 않습니다 (401 Unauthorized)")
            print("  - 팀장에게 올바른 토큰을 다시 받으세요.")
        else:
            print(f"\n  [오류] HTTP {e.response.status_code}: {e.response.text}")
        return False


def load_existing_config() -> dict | None:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return None
    return None


def main():
    parser = argparse.ArgumentParser(
        description="GAIA 모니터링 서버 연결 설정 (팀원용)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python scripts/gaia_monitor_connect.py http://1.2.3.4:9091 --token abc123xyz

연결 해제:
  python scripts/gaia_monitor_connect.py --disconnect
        """,
    )
    parser.add_argument("server", nargs="?", help="팀장이 공유한 서버 주소 (예: http://1.2.3.4:9091)")
    parser.add_argument("--token", help="팀장이 공유한 토큰")
    parser.add_argument("--disconnect", action="store_true", help="모니터링 서버 연결 해제")
    parser.add_argument("--status", action="store_true", help="현재 연결 상태 확인")
    args = parser.parse_args()

    # 상태 확인
    if args.status:
        cfg = load_existing_config()
        if cfg:
            print(f"  연결된 서버: {cfg['server']}")
            print(f"  토큰: {'*' * 8}{cfg['token'][-4:]}")
        else:
            print("  연결된 서버 없음.")
        return

    # 연결 해제
    if args.disconnect:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
            print("  모니터링 서버 연결이 해제되었습니다.")
        else:
            print("  연결된 서버가 없습니다.")
        return

    # 연결 설정
    if not args.server or not args.token:
        parser.print_help()
        print("\n[오류] server 주소와 --token 이 모두 필요합니다.")
        sys.exit(1)

    server = args.server.rstrip("/")

    print(f"\n  서버: {server}")
    print(f"  토큰: {'*' * 8}{args.token[-4:]}")
    print("\n  연결 테스트 중...")

    if not test_connection(server, args.token):
        sys.exit(1)

    print("  연결 성공! ✅")
    save_config(server, args.token)

    print()
    print("=" * 50)
    print("  설정 완료!")
    print("=" * 50)
    print()
    print("  이제 벤치마크를 실행하면 결과가 자동으로")
    print("  팀 모니터링 서버에 업로드됩니다.")
    print()
    print("  상태 확인: python scripts/gaia_monitor_connect.py --status")
    print("  연결 해제: python scripts/gaia_monitor_connect.py --disconnect")
    print()


if __name__ == "__main__":
    main()
