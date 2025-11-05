#!/usr/bin/env python3
"""
QA Agent 사용 예시

이 스크립트는 QA Agent를 사용하여 기획서에서 테스트 케이스를 생성하는 방법을 보여줍니다.
"""

import sys
import os
import json

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia.src.phase1.agent_client import AgentServiceClient


def example_simple():
    """간단한 예제"""
    print("=" * 60)
    print("예제 1: 간단한 계산기 앱 테스트 케이스 생성")
    print("=" * 60)
    print()
    
    spec = """
계산기 웹 애플리케이션 기획서

주요 기능:
1. 숫자 입력 (0-9)
2. 사칙연산 (더하기, 빼기, 곱하기, 나누기)
3. 계산 결과 표시
4. 초기화 버튼 (AC)
5. 백스페이스 기능
"""
    
    print("📄 분석할 기획서:")
    print(spec)
    print()
    
    client = AgentServiceClient()
    
    print("🤖 QA Agent 분석 시작... (약 30초-2분 소요)")
    print()
    
    try:
        result = client.analyze_document(spec, timeout=180)
        
        print("✅ 분석 완료!")
        print()
        print(f"📊 요약:")
        print(f"  - 총 테스트 케이스: {result.summary['total']}개")
        print(f"  - MUST (필수): {result.summary['must']}개")
        print(f"  - SHOULD (권장): {result.summary['should']}개")
        print(f"  - MAY (선택): {result.summary['may']}개")
        print()
        
        print("📋 생성된 테스트 케이스:")
        print()
        for tc in result.checklist:
            print(f"[{tc.priority}] {tc.id}: {tc.name}")
            print(f"  카테고리: {tc.category}")
            print(f"  사전조건: {tc.precondition}")
            print(f"  단계:")
            for i, step in enumerate(tc.steps, 1):
                print(f"    {i}. {step}")
            print(f"  예상 결과: {tc.expected_result}")
            print()
        
        return True
        
    except Exception as e:
        print(f"❌ 에러 발생: {e}")
        return False


def example_detailed():
    """상세한 예제"""
    print("=" * 60)
    print("예제 2: 온라인 쇼핑몰 테스트 케이스 생성")
    print("=" * 60)
    print()
    
    spec = """
온라인 쇼핑몰 시스템 기획서

1. 회원 관리
   - 회원가입 (이메일 인증)
   - 로그인/로그아웃
   - 프로필 수정
   - 비밀번호 찾기

2. 상품 관리
   - 상품 검색 (키워드, 카테고리)
   - 상품 상세 조회
   - 상품 필터링 (가격, 브랜드)
   - 상품 정렬 (인기순, 가격순)

3. 장바구니
   - 장바구니 담기
   - 수량 변경
   - 장바구니 삭제
   - 장바구니 목록 확인

4. 주문 및 결제
   - 주문서 작성
   - 배송지 입력
   - 결제 수단 선택
   - 주문 완료 확인

5. 고객 지원
   - 공지사항 확인
   - 1:1 문의
   - 리뷰 작성
"""
    
    print("📄 분석할 기획서:")
    print(spec)
    print()
    
    client = AgentServiceClient()
    
    print("🤖 QA Agent 분석 시작... (약 1-3분 소요)")
    print()
    
    try:
        result = client.analyze_document(spec, timeout=300)
        
        print("✅ 분석 완료!")
        print()
        print(f"📊 요약:")
        print(f"  - 총 테스트 케이스: {result.summary['total']}개")
        print(f"  - MUST (필수): {result.summary['must']}개")
        print(f"  - SHOULD (권장): {result.summary['should']}개")
        print(f"  - MAY (선택): {result.summary['may']}개")
        print()
        
        # MUST 우선순위만 출력
        must_cases = [tc for tc in result.checklist if tc.priority == "MUST"]
        print(f"📋 MUST 우선순위 테스트 케이스 ({len(must_cases)}개):")
        print()
        for tc in must_cases:
            print(f"✅ {tc.id}: {tc.name}")
            print(f"   단계: {' → '.join(tc.steps[:3])}{'...' if len(tc.steps) > 3 else ''}")
            print()
        
        # JSON 파일로 저장
        output_file = "/tmp/qa_agent_test_cases.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'summary': result.summary,
                'checklist': [
                    {
                        'id': tc.id,
                        'name': tc.name,
                        'category': tc.category,
                        'priority': tc.priority,
                        'precondition': tc.precondition,
                        'steps': tc.steps,
                        'expected_result': tc.expected_result
                    }
                    for tc in result.checklist
                ]
            }, f, ensure_ascii=False, indent=2)
        
        print(f"💾 테스트 케이스가 저장되었습니다: {output_file}")
        return True
        
    except Exception as e:
        print(f"❌ 에러 발생: {e}")
        return False


def main():
    """메인 함수"""
    print()
    print("=" * 60)
    print("      QA Agent 사용 예시")
    print("=" * 60)
    print()
    print("⚠️  주의: Agent Service가 실행 중이어야 합니다.")
    print("   실행 방법: ./start_qa_agent.sh 또는 cd gaia/agent-service && npm run dev")
    print()
    input("Enter 키를 눌러 계속...")
    print()
    
    # 서비스 상태 확인
    client = AgentServiceClient()
    if not client.health_check():
        print("❌ Agent Service에 연결할 수 없습니다.")
        print("   Agent Service를 먼저 시작해주세요:")
        print("   ./start_qa_agent.sh")
        return False
    
    print("✅ Agent Service 연결 확인")
    print()
    
    # 예제 실행
    examples = [
        ("간단한 예제", example_simple),
        ("상세한 예제", example_detailed),
    ]
    
    for i, (name, func) in enumerate(examples, 1):
        print()
        print(f"실행할 예제를 선택하세요:")
        for j, (ex_name, _) in enumerate(examples, 1):
            print(f"  {j}. {ex_name}")
        print(f"  0. 종료")
        print()
        
        choice = input("선택 (1-2 또는 0): ").strip()
        
        if choice == "0":
            print("종료합니다.")
            break
        elif choice == "1":
            example_simple()
            break
        elif choice == "2":
            example_detailed()
            break
        else:
            print("잘못된 선택입니다.")
            continue


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다.")
        sys.exit(0)
