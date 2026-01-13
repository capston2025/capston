#!/usr/bin/env python3
"""
부모 hover 로직 테스트 스크립트
"전체 문제" 링크를 클릭해서 부모 hover가 작동하는지 확인
"""

import requests
import json
import time

MCP_HOST_URL = "http://localhost:8001"
SESSION_ID = "hover_test"

def execute_action(action, selector="", value="", url=""):
    """MCP Host에 액션 실행 요청"""
    response = requests.post(
        f"{MCP_HOST_URL}/execute",
        json={
            "action": "execute_action",
            "params": {
                "session_id": SESSION_ID,
                "action": action,
                "selector": selector,
                "value": value,
                "url": url,
            }
        },
        timeout=60
    )
    return response.json()

def main():
    print("=" * 80)
    print("🧪 부모 Hover 로직 테스트")
    print("=" * 80)
    print()

    # 1. 백준 사이트로 이동
    print("1️⃣  백준 사이트로 이동...")
    result = execute_action("goto", value="https://www.acmicpc.net/")
    print(f"   결과: {result.get('success')}")
    time.sleep(3)

    # 2. "전체 문제" 링크 클릭 시도 (부모 hover 필요)
    print("\n2️⃣  '전체 문제' 링크 클릭 시도...")
    print("   (이 링크는 숨겨져 있어서 부모 '문제' 메뉴를 hover해야 합니다)")

    result = execute_action("click", selector='a:has-text("전체 문제")')
    print(f"   결과: {result.get('success')}")

    if result.get('success'):
        print("   ✅ 성공! 부모 hover 로직이 작동했습니다!")
    else:
        print(f"   ❌ 실패: {result.get('message', result.get('error', 'Unknown error'))[:200]}")

    print()
    print("=" * 80)
    print("테스트 완료!")
    print("MCP Host 로그(/tmp/mcp_host_v2.log)를 확인하세요.")
    print("=" * 80)

if __name__ == "__main__":
    main()
