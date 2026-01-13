"""
백준(BOJ) 웹사이트 테스트
문제 상세 정보 확인 시나리오
"""
import os
import sys
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

# GAIA 모듈 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'gaia'))

from gaia.src.phase4.goal_driven.agent import GoalDrivenAgent
from gaia.src.phase4.goal_driven.models import TestGoal


def test_baekjoon_problem_detail():
    """
    백준 문제 상세 페이지 테스트
    - 문제 설명 읽기
    - 입력/출력 예제 확인
    """

    # Gemini API 키
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        print("❌ GEMINI_API_KEY 환경 변수가 설정되지 않았습니다")
        return

    # MCP Host 확인
    print("=" * 60)
    print("📋 MCP Host 확인 중...")
    print("   포트: 8001")
    print("=" * 60)

    # Agent 초기화
    agent = GoalDrivenAgent(
        mcp_host_url="http://localhost:8001",
        gemini_api_key=gemini_api_key,
        session_id="baekjoon_test"
    )

    # 테스트 목표 설정
    # 백준 1000번 문제 (A+B)
    goal = TestGoal(
        id="baekjoon_1000",
        name="백준 1000번 문제 정보 읽기",
        description="""
        백준 온라인 저지의 1000번 문제(A+B) 페이지로 이동하여
        문제 설명, 입력, 출력, 예제를 읽어옵니다.
        """,
        start_url="https://www.acmicpc.net/problem/1000",
        success_criteria=[
            "문제 제목이 보임",
            "문제 설명 텍스트가 표시됨",
            "입력 설명이 있음",
            "출력 설명이 있음",
            "예제 입력과 예제 출력이 표시됨"
        ],
        failure_criteria=[
            "페이지 로드 오류",
            "404 에러"
        ],
        max_steps=10,
        test_data={}
    )

    print("\n🚀 테스트 시작: 백준 1000번 문제 페이지")
    print(f"   URL: {goal.start_url}")

    # 목표 실행
    result = agent.execute_goal(goal)

    # 결과 출력
    print("\n" + "=" * 60)
    print("📊 테스트 결과")
    print("=" * 60)
    print(f"성공 여부: {'✅ 성공' if result.success else '❌ 실패'}")
    print(f"총 스텝 수: {result.total_steps}")
    print(f"소요 시간: {result.duration_seconds:.2f}초")
    print(f"종료 이유: {result.final_reason}")

    print("\n📝 실행한 액션들:")
    for step in result.steps_taken:
        status = "✅" if step.success else "❌"
        print(f"  {status} Step {step.step_number}: {step.action.action.value}")
        print(f"      이유: {step.action.reasoning}")
        if step.error_message:
            print(f"      오류: {step.error_message}")

    print("=" * 60)

    return result


if __name__ == "__main__":
    test_baekjoon_problem_detail()
