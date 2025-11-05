#!/usr/bin/env python3
"""Hacker News 실제 사이트 테스트 실행기"""
import sys
import json
from pathlib import Path

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator

def main():
    print("=" * 60)
    print("GAIA REAL-WORLD TEST: HACKER NEWS")
    print("=" * 60)

    # 대상 URL과 테스트 플랜
    url = "https://news.ycombinator.com"
    test_plan_path = Path("/Users/coldmans/Documents/GitHub/capston/gaia/artifacts/plans/hackernews_test.json")

    print(f"Target URL: {url}")
    print(f"Test Plan: {test_plan_path}")
    print()

    # 테스트 플랜 불러오기
    with open(test_plan_path, 'r', encoding='utf-8') as f:
        test_plan = json.load(f)

    print(f"Loaded {len(test_plan['test_scenarios'])} test scenarios")
    print()

    # 테스트 플랜을 TestScenario 객체로 변환
    from gaia.src.utils.models import TestScenario, TestStep, Assertion
    scenarios = []
    for scenario_dict in test_plan['test_scenarios']:
        # 단계 정보가 있으면 파싱
        steps = []
        for step_dict in scenario_dict.get('steps', []):
            # GAIA 형식에서는 description 대신 step_description을 사용
            step = TestStep(
                description=step_dict.get('step_description', ''),
                action=step_dict.get('action', 'wait'),
                selector='',  # 자동으로 탐지됨
                params=step_dict.get('params', [])
            )
            steps.append(step)

        # assertion 정보 파싱
        assertion_dict = scenario_dict.get('assertion', {})
        assertion = Assertion(
            description=assertion_dict.get('description', ''),
            selector=assertion_dict.get('selector', ''),
            condition=assertion_dict.get('condition', ''),
            params=assertion_dict.get('params', [])
        )

        scenario = TestScenario(
            id=scenario_dict['id'],
            priority=scenario_dict['priority'],
            scenario=scenario_dict['scenario'],
            steps=steps,
            assertion=assertion
        )
        scenarios.append(scenario)

    # 오케스트레이터 생성
    orchestrator = IntelligentOrchestrator()

    try:
        # 테스트 실행
        print("🚀 Starting tests on REAL WEBSITE: Hacker News")
        print("=" * 60)
        print()

        results = orchestrator.execute_scenarios(url, scenarios)

        # 결과 출력
        print()
        print("=" * 60)
        print("TEST RESULTS")
        print("=" * 60)

        print(f"Total:   {results['total']}")
        print(f"Success: {results['success']}")
        print(f"Partial: {results['partial']}")
        print(f"Failed:  {results['failed']}")
        print(f"Skipped: {results['skipped']}")
        print()

        # 상세 결과 표시
        if 'scenarios' in results:
            for scenario in results['scenarios']:
                status = scenario.get('status', 'unknown')
                symbol = '✓' if status == 'success' else '✗' if status == 'failed' else '~' if status == 'partial' else '-'
                print(f"{symbol} {scenario.get('id')}: {scenario.get('scenario', 'Unknown'[:60])}")

        print("=" * 60)

        # 성공률 계산
        if results['total'] > 0:
            success_rate = (results['success'] / results['total']) * 100
            print(f"\n✨ Success Rate: {success_rate:.1f}%")

    finally:
        # close 메서드가 있으면 오케스트레이터 종료
        if hasattr(orchestrator, 'close'):
            orchestrator.close()

if __name__ == "__main__":
    main()
