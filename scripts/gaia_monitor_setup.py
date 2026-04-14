#!/usr/bin/env python3
"""
[팀장용] GAIA 모니터링 서버 최초 세팅 스크립트.

이 스크립트 하나로:
  1. 팀 토큰(비밀번호) 생성
  2. nginx htpasswd 파일 생성
  3. Docker 스택 실행
  4. 팀원에게 공유할 연결 명령어 출력

사용법:
  python scripts/gaia_monitor_setup.py
  python scripts/gaia_monitor_setup.py --token myteampassword
  python scripts/gaia_monitor_setup.py --grafana-password mygrafanapass
"""

import argparse
import hashlib
import os
import secrets
import subprocess
import sys
from pathlib import Path

MONITORING_DIR = Path(__file__).parent.parent / "monitoring"
TOKENS_DIR = MONITORING_DIR / "nginx" / "tokens"
HTPASSWD_FILE = TOKENS_DIR / ".htpasswd"
TEAM_USER = "gaia"   # Basic Auth 사용자명 (고정)


def generate_token(length: int = 32) -> str:
    """URL-safe 랜덤 토큰 생성."""
    return secrets.token_urlsafe(length)


def make_htpasswd_line(username: str, password: str) -> str:
    """nginx가 읽을 수 있는 apr1(md5) htpasswd 라인 생성."""
    # Python 내장으로 apr1 md5 구현
    import crypt  # unix only
    try:
        hashed = crypt.crypt(password, crypt.mksalt(crypt.METHOD_MD5))
        return f"{username}:{hashed}"
    except Exception:
        # crypt 없을 때 (Windows 등) openssl 사용
        result = subprocess.run(
            ["openssl", "passwd", "-apr1", password],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return f"{username}:{result.stdout.strip()}"
        # fallback: SHA1
        import base64
        sha = base64.b64encode(hashlib.sha1(password.encode()).digest()).decode()
        return f"{username}:{'{SHA}'}{sha}"


def write_htpasswd(token: str):
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    line = make_htpasswd_line(TEAM_USER, token)
    HTPASSWD_FILE.write_text(line + "\n")
    # Docker 컨테이너가 읽을 수 있도록 권한 설정
    HTPASSWD_FILE.chmod(0o644)
    print(f"  htpasswd 생성: {HTPASSWD_FILE}")


def start_docker(grafana_user: str, grafana_password: str):
    print("\n  Docker 스택 시작 중...")
    env = {
        **os.environ,
        "GRAFANA_USER": grafana_user,
        "GRAFANA_PASSWORD": grafana_password,
    }
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=MONITORING_DIR,
        env=env,
    )
    if result.returncode != 0:
        print("\n[오류] Docker Compose 실행 실패. Docker가 설치/실행 중인지 확인하세요.")
        sys.exit(1)
    print("  Docker 스택 시작 완료!")


def get_public_ip() -> str:
    """현재 서버의 공인 IP 조회."""
    try:
        import urllib.request
        return urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode()
    except Exception:
        return "<서버_IP>"


def main():
    parser = argparse.ArgumentParser(description="GAIA 모니터링 서버 세팅 (팀장용)")
    parser.add_argument("--token", help="팀 공유 토큰 (미지정 시 자동 생성)")
    parser.add_argument("--grafana-user", default="admin", help="Grafana 관리자 계정 (기본: admin)")
    parser.add_argument("--grafana-password", default=None, help="Grafana 관리자 비밀번호 (미지정 시 자동 생성)")
    args = parser.parse_args()

    token = args.token or generate_token()
    grafana_password = args.grafana_password or generate_token(16)

    print("=" * 55)
    print("  GAIA 모니터링 서버 세팅")
    print("=" * 55)

    # 1. htpasswd 생성
    print("\n[1/3] 팀 토큰 생성")
    write_htpasswd(token)

    # 2. Docker 시작
    print("\n[2/3] Docker 스택 시작")
    start_docker(args.grafana_user, grafana_password)

    # 3. 공인 IP 조회
    print("\n[3/3] 공인 IP 확인")
    ip = get_public_ip()
    print(f"  서버 IP: {ip}")

    # 결과 출력
    print("\n" + "=" * 55)
    print("  ✅ 세팅 완료! 팀원들에게 아래 명령어를 공유하세요.")
    print("=" * 55)
    print()
    print("  ┌─ 팀원 연결 명령어 (이것만 공유하면 됩니다) ─┐")
    print()
    print(f"  python scripts/gaia_monitor_connect.py \\")
    print(f"      http://{ip}:9091 \\")
    print(f"      --token {token}")
    print()
    print("  └──────────────────────────────────────────────┘")
    print()
    print(f"  Grafana 대시보드: http://{ip}:3000")
    print(f"  Grafana 계정:     {args.grafana_user} / {grafana_password}")
    print()
    print("  ※ 클라우드 VM이라면 포트 9091, 3000을 팀원 IP에만 허용하세요.")
    print()


if __name__ == "__main__":
    main()
