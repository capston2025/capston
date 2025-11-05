#!/usr/bin/env python3
"""
GAIA 시스템 자동 테스트 실행기.
선택자가 없는 테스트 플랜을 불러와 MasterOrchestrator로 실행합니다.
"""
import json
import os
import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

# .env에서 환경 변수를 불러오기
from dotenv import load_dotenv
load_dotenv()

from gaia.src.phase4.master_orchestrator import MasterOrchestrator
from gaia.src.utils.models import TestScenario

def load_test_plan(plan_path: str):
    """JSON 파일에서 테스트 플랜을 불러옵니다."""
    with open(plan_path, 'r') as f:
        data = json.load(f)

    # TestScenario 객체로 변환
    scenarios = []
    for scenario_data in data['test_scenarios']:
        try:
            scenario = TestScenario(**scenario_data)
            scenarios.append(scenario)
        except Exception as e:
            print(f"Warning: Failed to load scenario {scenario_data.get('id', 'unknown')}: {e}")

    return scenarios

def run_tests(url: str, plan_path: str):
    """테스트를 실행하고 결과를 반환합니다."""
    print("=" * 60)
    print("GAIA AUTO-TEST RUNNER")
    print("=" * 60)
    print(f"Target URL: {url}")
    print(f"Test Plan: {plan_path}")
    print("=" * 60)

    # 테스트 플랜 불러오기
    print("\nLoading test plan...")
    scenarios = load_test_plan(plan_path)
    print(f"Loaded {len(scenarios)} test scenarios")

    # 오케스트레이터 초기화
    print("\nInitializing MasterOrchestrator...")
    orchestrator = MasterOrchestrator(session_id="auto_test_session")

    # 테스트 실행
    print("\nExecuting tests...")
    print("-" * 60)

    results = orchestrator.execute_scenarios(
        url=url,
        scenarios=scenarios,
        progress_callback=lambda msg: print(msg)
    )

    # 결과 출력
    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)
    print(f"Total Scenarios: {results['total']}")
    print(f"SUCCESS: {results['success']} ({results['success']/results['total']*100:.1f}%)")
    print(f"PARTIAL: {results['partial']} ({results['partial']/results['total']*100:.1f}%)")
    print(f"FAILED: {results['failed']} ({results['failed']/results['total']*100:.1f}%)")
    print(f"SKIPPED: {results['skipped']} ({results['skipped']/results['total']*100:.1f}%)")
    print(f"Pages Explored: {results.get('pages_explored', 'N/A')}")
    print("=" * 60)

    # 상세 결과
    print("\nDetailed Results:")
    print("-" * 60)
    for scenario_result in results['scenarios']:
        status_emoji = {
            'success': '✓',
            'partial': '⚠',
            'failed': '✗',
            'skipped': '○'
        }.get(scenario_result['status'], '?')

        print(f"{status_emoji} {scenario_result['id']}: {scenario_result.get('scenario', 'N/A')}")
        print(f"   Status: {scenario_result['status'].upper()}")

        if 'error' in scenario_result:
            print(f"   Error: {scenario_result['error']}")

        if scenario_result.get('logs'):
            print(f"   Logs: {scenario_result['logs'][:200]}...")

        print()

    return results

if __name__ == "__main__":
    # 구성
    TARGET_URL = "https://final-blog-25638597.figma.site"
    TEST_PLAN = "/Users/coldmans/Documents/GitHub/capston/gaia/artifacts/plans/realistic_test_no_selectors.json"

    # 테스트 실행
    try:
        results = run_tests(TARGET_URL, TEST_PLAN)

        # 적절한 종료 코드 반환
        if results['failed'] > 0:
            sys.exit(1)
        elif results['partial'] > 0:
            sys.exit(2)
        else:
            sys.exit(0)
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(3)
