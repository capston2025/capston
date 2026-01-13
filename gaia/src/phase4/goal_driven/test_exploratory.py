#!/usr/bin/env python3
"""
Exploratory Agent 테스트 스크립트

완전 자율 탐색 모드 테스트

사용법:
    1. MCP Host 실행: python -m gaia.src.phase4.mcp_host
    2. 테스트 실행: python -m gaia.src.phase4.goal_driven.test_exploratory
"""

import sys
import os
import json
from datetime import datetime

# 프로젝트 루트 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

from gaia.src.phase4.goal_driven.exploratory_agent import ExploratoryAgent
from gaia.src.phase4.goal_driven.exploratory_models import ExplorationConfig


def test_exploratory_basic():
    """기본 탐색 테스트"""

    print("=" * 80)
    print("🔍 Exploratory Agent - 완전 자율 탐색 모드")
    print("=" * 80)
    print()

    # 설정
    config = ExplorationConfig(
        max_actions=50,  # 최대 50개 액션
        max_depth=3,
        prioritize_untested=True,
        avoid_destructive=True,
        test_forms=True,
        test_navigation=True,
    )

    # Agent 생성
    agent = ExploratoryAgent(
        mcp_host_url="http://localhost:8001",
        session_id="exploratory_test",
        config=config,
    )

    # 탐색 실행
    start_url = "https://test-sitev2.vercel.app/"
    result = agent.explore(start_url)

    # 결과 저장
    save_result(result)

    # 이슈 리포트 출력
    print_issue_report(result)

    return result


def test_exploratory_with_checklist():
    """체크리스트와 함께 탐색 테스트"""

    print("=" * 80)
    print("🔍 Exploratory Agent - 체크리스트 포함")
    print("=" * 80)
    print()

    # TODO: 향후 체크리스트 기능 추가 시 구현
    print("아직 구현되지 않음 - 순수 탐색 모드만 지원")


def save_result(result):
    """결과를 JSON 파일로 저장"""

    # artifacts 디렉토리 확인
    artifacts_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
        "artifacts",
        "exploration_results"
    )
    os.makedirs(artifacts_dir, exist_ok=True)

    # 파일명 생성
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"exploration_{timestamp}.json"
    filepath = os.path.join(artifacts_dir, filename)

    # JSON 직렬화 가능한 형태로 변환
    result_dict = result.model_dump(mode='json')

    # 파일 저장
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(result_dict, f, indent=2, ensure_ascii=False)

    print(f"\n💾 결과 저장: {filepath}")


def print_issue_report(result):
    """발견된 이슈 리포트 출력"""

    print("\n" + "=" * 80)
    print("🐛 발견된 이슈 리포트")
    print("=" * 80)

    if not result.issues_found:
        print("✅ 이슈가 발견되지 않았습니다!")
        return

    # 심각도별 그룹화
    by_severity = {}
    for issue in result.issues_found:
        if issue.severity not in by_severity:
            by_severity[issue.severity] = []
        by_severity[issue.severity].append(issue)

    # 심각도별 출력
    for severity in ["critical", "high", "medium", "low"]:
        if severity not in by_severity:
            continue

        issues = by_severity[severity]
        print(f"\n## {severity.upper()} ({len(issues)}개)")
        print("-" * 80)

        for i, issue in enumerate(issues, 1):
            print(f"\n### {i}. {issue.title}")
            print(f"   타입: {issue.issue_type.value}")
            print(f"   URL: {issue.url}")
            print(f"   설명: {issue.description[:200]}...")

            if issue.steps_to_reproduce:
                print(f"   재현 단계:")
                for step in issue.steps_to_reproduce:
                    print(f"      {step}")

            if issue.error_message:
                print(f"   에러: {issue.error_message[:100]}...")

    print("\n" + "=" * 80)


def print_coverage_report(result):
    """커버리지 리포트 출력"""

    print("\n" + "=" * 80)
    print("📊 테스트 커버리지")
    print("=" * 80)

    coverage = result.coverage
    print(f"전체 요소: {coverage.get('total_interactive_elements', 0)}개")
    print(f"테스트 완료: {coverage.get('tested_elements', 0)}개")
    print(f"커버리지: {coverage.get('coverage_percentage', 0):.1f}%")
    print(f"방문 페이지: {coverage.get('total_pages', 0)}개")

    print("\n" + "=" * 80)


def print_step_summary(result):
    """스텝 요약 출력"""

    print("\n" + "=" * 80)
    print("📋 실행 스텝 요약")
    print("=" * 80)

    for step in result.steps:
        status = "✅" if step.success else "❌"
        action_desc = "종료"

        if step.decision.selected_action:
            action = step.decision.selected_action
            action_desc = f"{action.action_type}: {action.description[:50]}"

        print(f"{status} Step {step.step_number}: {action_desc}")

        if step.issues_found:
            print(f"   🚨 이슈 {len(step.issues_found)}개 발견")

        if step.error_message:
            print(f"   ⚠️  에러: {step.error_message[:80]}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Exploratory Agent 테스트")
    parser.add_argument(
        "--mode",
        choices=["basic", "checklist"],
        default="basic",
        help="실행 모드",
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://test-sitev2.vercel.app/",
        help="탐색할 시작 URL",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=50,
        help="최대 액션 수",
    )

    args = parser.parse_args()

    if args.mode == "basic":
        # 커스텀 설정 적용
        config = ExplorationConfig(
            max_actions=args.max_actions,
            max_depth=3,
            prioritize_untested=True,
            avoid_destructive=True,
            test_forms=True,
            test_navigation=True,
        )

        agent = ExploratoryAgent(
            mcp_host_url="http://localhost:8001",
            session_id="exploratory_custom",
            config=config,
        )

        result = agent.explore(args.url)

        # 상세 리포트 출력
        save_result(result)
        print_issue_report(result)
        print_coverage_report(result)
        print_step_summary(result)

    elif args.mode == "checklist":
        test_exploratory_with_checklist()
