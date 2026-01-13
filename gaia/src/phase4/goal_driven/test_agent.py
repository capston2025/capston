#!/usr/bin/env python3
"""
Goal-Driven Agent 테스트 스크립트

사용법:
    1. MCP Host 실행: python -m gaia.src.phase4.mcp_host
    2. 테스트 실행: python -m gaia.src.phase4.goal_driven.test_agent
"""

import sys
import os

# 프로젝트 루트 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

from gaia.src.phase4.goal_driven.models import TestGoal, GoalTestPlan
from gaia.src.phase4.goal_driven.agent import GoalDrivenAgent


def test_login_goal():
    """로그인 테스트 - Goal만 정의하고 AI가 알아서 수행"""

    # 1. Goal 정의 (세부 스텝 없음!)
    login_goal = TestGoal(
        id="TC001",
        name="로그인 성공",
        description="유효한 자격 증명으로 로그인한다",
        start_url="https://test-sitev2.vercel.app/#basics",
        preconditions=["로그아웃 상태"],
        test_data={
            "email": "test.user@example.com",
            "password": "P@ssw0rd!",
        },
        success_criteria=[
            "성공 토스트 메시지",
            "로그아웃 버튼 표시",
            "사용자 아바타 표시",
            "환영 메시지",
        ],
        failure_criteria=[
            "오류 메시지",
            "로그인 폼이 그대로 남아있음",
        ],
        max_steps=15,
    )

    # 2. Agent 생성
    agent = GoalDrivenAgent(
        mcp_host_url="http://localhost:8000",
        session_id="test_login",
    )

    # 3. 목표 실행 - AI가 알아서 중간 단계 파악
    print("=" * 60)
    print("Goal-Driven Agent 테스트")
    print("=" * 60)
    print(f"목표: {login_goal.name}")
    print(f"설명: {login_goal.description}")
    print(f"성공 조건: {login_goal.success_criteria}")
    print("=" * 60)
    print()

    result = agent.execute_goal(login_goal)

    # 4. 결과 출력
    print()
    print("=" * 60)
    print("테스트 결과")
    print("=" * 60)
    print(f"성공 여부: {'✅ 성공' if result.success else '❌ 실패'}")
    print(f"총 스텝 수: {result.total_steps}")
    print(f"소요 시간: {result.duration_seconds:.2f}초")
    print(f"최종 이유: {result.final_reason}")
    print()

    if result.steps_taken:
        print("수행된 스텝:")
        for step in result.steps_taken:
            status = "✅" if step.success else "❌"
            print(f"  {status} Step {step.step_number}: {step.action.action.value} - {step.action.reasoning}")
            if step.error_message:
                print(f"      오류: {step.error_message}")

    return result


def test_simple_navigation():
    """간단한 네비게이션 테스트"""

    goal = TestGoal(
        id="TC_NAV",
        name="페이지 네비게이션",
        description="기본 기능 페이지로 이동하고 로그인 섹션을 확인한다",
        start_url="https://test-sitev2.vercel.app/",
        test_data={},
        success_criteria=[
            "로그인 탭이 보임",
            "회원가입 탭이 보임",
        ],
        max_steps=5,
    )

    agent = GoalDrivenAgent(
        mcp_host_url="http://localhost:8000",
        session_id="test_nav",
    )

    result = agent.execute_goal(goal)

    print(f"\n네비게이션 테스트: {'✅ 성공' if result.success else '❌ 실패'}")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Goal-Driven Agent 테스트")
    parser.add_argument(
        "--test",
        choices=["login", "nav", "all"],
        default="login",
        help="실행할 테스트",
    )

    args = parser.parse_args()

    if args.test == "login":
        test_login_goal()
    elif args.test == "nav":
        test_simple_navigation()
    else:
        test_simple_navigation()
        print("\n" + "=" * 60 + "\n")
        test_login_goal()
