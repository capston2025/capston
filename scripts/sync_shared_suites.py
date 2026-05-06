#!/usr/bin/env python3
"""Push and pull benchmark suite definitions through the monitoring server."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from gaia.src.benchmark_suite_sharing import (
    SharedSuiteError,
    SharedSuiteNotFound,
    download_shared_suite,
    list_shared_suites,
    merge_shared_suite_payload,
    upload_shared_suite,
)

MONITORING_CONFIG = Path.home() / ".gaia" / "monitoring.json"


def load_monitoring_config(path: Path = MONITORING_CONFIG) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    server = str(payload.get("server") or "").strip()
    token = str(payload.get("token") or "").strip()
    if not server:
        return None
    return {"server": server, "token": token}


def load_suite(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("suite must be a JSON object")
    payload.setdefault("scenarios", [])
    if not isinstance(payload.get("scenarios"), list):
        raise ValueError("suite.scenarios must be a list")
    return payload


def infer_suite_key(path: Path, payload: dict[str, Any] | None = None) -> str:
    suite_id = str((payload or {}).get("suite_id") or "").strip()
    if suite_id:
        return re.sub(r"_public_v\d+$", "", suite_id)
    name = path.stem
    name = name.removeprefix("custom_").removesuffix("_suite")
    return name


def save_suite(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _connection(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.server:
        return str(args.server).rstrip("/"), args.token
    cfg = load_monitoring_config()
    if not cfg:
        print("[오류] 연결된 모니터링 서버가 없습니다.", file=sys.stderr)
        print("  python scripts/gaia_monitor_connect.py <서버주소> --token <토큰>", file=sys.stderr)
        raise SystemExit(1)
    return cfg["server"], cfg.get("token")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GAIA benchmark suite 공유/가져오기")
    parser.add_argument("--server", help="모니터링 서버 URL 직접 지정")
    parser.add_argument("--token", help="팀 공유 토큰 직접 지정")
    subparsers = parser.add_subparsers(dest="command", required=True)

    push_parser = subparsers.add_parser("push", help="로컬 suite JSON을 팀 서버에 공유")
    push_parser.add_argument("--suite", type=Path, required=True, help="공유할 suite JSON")
    push_parser.add_argument("--key", help="공유 key. 기본값은 suite_id 또는 파일명에서 추론")

    pull_parser = subparsers.add_parser("pull", help="팀 서버의 suite JSON을 로컬로 가져오기")
    pull_parser.add_argument("--suite", type=Path, required=True, help="저장/병합할 로컬 suite JSON")
    pull_parser.add_argument("--key", help="공유 key. 기본값은 suite_id 또는 파일명에서 추론")
    pull_parser.add_argument("--replace", action="store_true", help="병합하지 않고 팀 공유본으로 덮어쓰기")

    subparsers.add_parser("list", help="팀 서버에 공유된 suite key 목록")

    args = parser.parse_args(argv)
    server, token = _connection(args)

    try:
        if args.command == "list":
            names = list_shared_suites(server=server, token=token)
            if not names:
                print("공유된 suite가 없습니다.")
                return 0
            for name in names:
                print(name)
            return 0

        suite_path = args.suite.expanduser().resolve()
        local_payload = load_suite(suite_path) if suite_path.exists() else None
        suite_key = str(args.key or infer_suite_key(suite_path, local_payload)).strip()

        if args.command == "push":
            if local_payload is None:
                print(f"[오류] suite 파일을 찾지 못했습니다: {suite_path}", file=sys.stderr)
                return 1
            if not local_payload.get("scenarios"):
                print("[오류] 공유할 테스트가 없습니다.", file=sys.stderr)
                return 1
            url = upload_shared_suite(server=server, token=token, suite_key=suite_key, suite_payload=local_payload)
            print(f"공유 완료: {suite_key}")
            print(f"  {url}")
            return 0

        remote_payload = download_shared_suite(server=server, token=token, suite_key=suite_key)
        if args.replace or local_payload is None:
            save_suite(suite_path, remote_payload)
            print(f"가져오기 완료: {suite_key} -> {suite_path}")
            return 0

        merged, stats = merge_shared_suite_payload(local_payload, remote_payload)
        save_suite(suite_path, merged)
        print(
            "가져오기 완료: "
            f"{suite_key} (추가 {stats.added}, 업데이트 {stats.updated}, 로컬 유지 {stats.local_only})"
        )
        return 0
    except SharedSuiteNotFound:
        print(f"[오류] 팀 서버에 공유된 suite가 없습니다: {args.key or '<inferred>'}", file=sys.stderr)
        return 1
    except (OSError, ValueError, SharedSuiteError) as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
