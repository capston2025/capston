#!/usr/bin/env python3
"""
QA Agent Integration Test
Tests the full workflow: Agent Service Health Check → Document Analysis → Test Case Generation
"""

import json
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia.src.phase1.agent_client import AgentServiceClient


def test_health_check():
    """Test if the agent service is healthy"""
    print("🔍 Testing Agent Service Health Check...")
    client = AgentServiceClient()
    
    if client.health_check():
        print("✅ Agent service is healthy\n")
        return True
    else:
        print("❌ Agent service is not healthy")
        print("   Make sure the service is running:")
        print("   cd gaia/agent-service && npm run dev\n")
        return False


def test_document_analysis():
    """Test document analysis and test case generation"""
    print("🔍 Testing Document Analysis...")
    
    sample_spec = """
온라인 도서관 시스템 기획서

핵심 기능:
1. 사용자 회원가입 및 로그인
   - 이메일 인증
   - 비밀번호 찾기

2. 도서 검색 및 조회
   - 제목, 저자, ISBN으로 검색
   - 카테고리별 분류
   - 상세 정보 확인

3. 도서 대출 및 반납
   - 대출 신청
   - 대출 기간 확인
   - 온라인 반납

4. 예약 시스템
   - 대출 중인 도서 예약
   - 예약 취소
   - 예약 알림

5. 리뷰 및 평점
   - 도서 리뷰 작성
   - 별점 평가
   - 다른 사용자 리뷰 확인
"""
    
    client = AgentServiceClient()
    
    try:
        result = client.analyze_document(sample_spec, timeout=300)
        
        print(f"✅ Analysis completed successfully")
        print(f"\n📊 Summary:")
        print(f"   Total test cases: {result.summary['total']}")
        print(f"   MUST: {result.summary['must']}")
        print(f"   SHOULD: {result.summary['should']}")
        print(f"   MAY: {result.summary['may']}")
        
        print(f"\n📋 Generated Test Cases:")
        for i, tc in enumerate(result.checklist[:5], 1):  # Show first 5
            print(f"\n   {i}. [{tc.id}] {tc.name}")
            print(f"      Priority: {tc.priority}")
            print(f"      Category: {tc.category}")
            print(f"      Steps: {len(tc.steps)} steps")
            if tc.steps:
                print(f"      First step: {tc.steps[0]}")
        
        if len(result.checklist) > 5:
            print(f"\n   ... and {len(result.checklist) - 5} more test cases")
        
        return True
        
    except Exception as e:
        print(f"❌ Analysis failed: {e}")
        return False


def test_json_validation():
    """Test that generated test cases follow the expected format"""
    print("\n🔍 Testing JSON Structure Validation...")
    
    sample_spec = """
간단한 계산기 웹앱

기능:
1. 기본 사칙연산 (더하기, 빼기, 곱하기, 나누기)
2. 숫자 입력
3. 계산 결과 표시
4. 초기화 버튼
"""
    
    client = AgentServiceClient()
    
    try:
        result = client.analyze_document(sample_spec, timeout=120)
        
        # Validate structure
        assert hasattr(result, 'checklist'), "Missing checklist"
        assert hasattr(result, 'summary'), "Missing summary"
        assert len(result.checklist) > 0, "Empty checklist"
        
        # Validate first test case structure
        tc = result.checklist[0]
        assert hasattr(tc, 'id'), "Missing id"
        assert hasattr(tc, 'name'), "Missing name"
        assert hasattr(tc, 'category'), "Missing category"
        assert hasattr(tc, 'priority'), "Missing priority"
        assert hasattr(tc, 'precondition'), "Missing precondition"
        assert hasattr(tc, 'steps'), "Missing steps"
        assert hasattr(tc, 'expected_result'), "Missing expected_result"
        assert tc.priority in ['MUST', 'SHOULD', 'MAY'], f"Invalid priority: {tc.priority}"
        assert isinstance(tc.steps, list), "Steps should be a list"
        assert len(tc.steps) > 0, "Steps should not be empty"
        
        print("✅ JSON structure validation passed")
        print(f"   ✓ All required fields present")
        print(f"   ✓ Priority values valid")
        print(f"   ✓ Steps format correct")
        
        return True
        
    except AssertionError as e:
        print(f"❌ Validation failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 60)
    print("QA Agent Integration Test Suite")
    print("=" * 60)
    print()
    
    tests = [
        ("Health Check", test_health_check),
        ("Document Analysis", test_document_analysis),
        ("JSON Validation", test_json_validation),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            results.append(test_func())
        except Exception as e:
            print(f"❌ {test_name} threw an exception: {e}")
            results.append(False)
        print()
    
    print("=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    
    for (test_name, _), result in zip(tests, results):
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    passed = sum(results)
    total = len(results)
    print(f"\nTotal: {passed}/{total} tests passed")
    
    return all(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
