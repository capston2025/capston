#!/usr/bin/env python3
"""simple_test.json을 실행하는 테스트 러너"""
import sys
import json
from pathlib import Path

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator

def main():
    print("=" * 60)
    print("GAIA SIMPLE TEST RUNNER")
    print("=" * 60)

    # 대상 URL과 테스트 플랜
    url = "https://test-sitev2.vercel.app"
    test_plan_path = Path("/Users/coldmans/Documents/GitHub/capston/artifacts/plans/simple_test.json")

    print(f"Target URL: {url}")
    print(f"Test Plan: {test_plan_path}")
    print()

    # 테스트 플랜 불러오기
    with open(test_plan_path, 'r', encoding='utf-8') as f:
        test_plan = json.load(f)

    print(f"Loaded {len(test_plan['test_scenarios'])} test scenarios")
    print()

    # 테스트 플랜을 TestScenario 객체로 변환
    from gaia.src.utils.models import TestScenario, TestStep
    scenarios = []
    for scenario_dict in test_plan['test_scenarios']:
        steps = [TestStep(**step) for step in scenario_dict.get('steps', [])]
        scenario = TestScenario(
            id=scenario_dict['id'],
            priority=scenario_dict['priority'],
            scenario=scenario_dict['scenario'],
            steps=steps,
            assertion=scenario_dict.get('assertion', {})
        )
        scenarios.append(scenario)

    # 오케스트레이터 생성
    orchestrator = IntelligentOrchestrator()

    try:
        # 테스트 실행
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
                print(f"{symbol} {scenario.get('id')}: {scenario.get('scenario', 'Unknown')}")

        print("=" * 60)

    finally:
        orchestrator.close()

if __name__ == "__main__":
    main()
