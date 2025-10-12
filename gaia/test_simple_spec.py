#!/usr/bin/env python3
"""Test Agent Builder with a simple spec"""
import sys
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase1.agent_client import AgentServiceClient

print("=" * 80)
print("🧪 Testing Agent Builder with Simple Spec")
print("=" * 80)

client = AgentServiceClient()

# Simple spec
simple_spec = """
UI 컴포넌트 테스트 사이트 기획서

## 주요 기능

### 1. 검색 기능
- 실시간 검색
- 필터링
- 정렬 (관련도순, 제목순)

### 2. 사용자 인증
- 로그인
- 회원가입
- 로그아웃

### 3. 장바구니
- 상품 추가
- 수량 조절
- 총액 계산
- 주문하기

### 4. 페이지네이션
- 페이지 번호 표시
- 이전/다음 버튼
- 항목 범위 표시

### 5. 폼 요소
- 라디오 버튼
- 토글 스위치
- 드롭다운 셀렉트
- 날짜 피커
"""

print("\n📄 Test Document:")
print(f"   Length: {len(simple_spec)} characters")
print(f"   Features: Search, Auth, Cart, Pagination, Forms")

print("\n🤖 Calling Agent Builder...")

try:
    analysis = client.analyze_document(simple_spec)

    print("\n✅ SUCCESS!")
    print(f"\n📊 Summary:")
    print(f"   Total: {analysis.summary['total']}")
    print(f"   MUST: {analysis.summary['must']}")
    print(f"   SHOULD: {analysis.summary['should']}")
    print(f"   MAY: {analysis.summary['may']}")

    print(f"\n📋 Test Cases:")
    for tc in analysis.checklist:
        print(f"   • [{tc.priority}] {tc.name}")

    print("\n" + "=" * 80)
    print("✅ Agent Builder works with simple specs!")
    print("=" * 80)

except Exception as e:
    print(f"\n❌ Failed: {e}")
    import traceback
    traceback.print_exc()
