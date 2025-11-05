#!/usr/bin/env python3
"""
GAIA 시스템을 위한 자동 수정 테스트 루프입니다.
테스트를 실행하고 실패를 분석해 코드를 자동으로 수정한 뒤 반복합니다.
"""
import json
import os
import sys
from pathlib import Path
import subprocess

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from gaia.src.phase4.master_orchestrator import MasterOrchestrator
from gaia.src.utils.models import TestScenario

# 검증을 위해 Playwright MCP 임포트
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("경고: 검증용 Playwright를 사용할 수 없습니다")


def load_test_plan(plan_path: str):
    """JSON 파일에서 테스트 플랜을 불러옵니다."""
    with open(plan_path, 'r') as f:
        data = json.load(f)

    scenarios = []
    for scenario_data in data['test_scenarios']:
        try:
            scenario = TestScenario(**scenario_data)
            scenarios.append(scenario)
        except Exception as e:
            print(f"Warning: Failed to load scenario {scenario_data.get('id', 'unknown')}: {e}")

    return scenarios


def verify_test_feasibility(url: str, test_description: str, test_id: str):
    """
    Use Playwright to verify if a test is actually feasible on the site.
    반환: (실행 가능 여부: bool, 이유: str)
    """
    if not HAS_PLAYWRIGHT:
        return True, "Cannot verify - Playwright not available"

    print(f"  🔍 Verifying test {test_id} feasibility...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until='networkidle')

            # 페이지 HTML 가져오기
            html = page.content()

            # 테스트 설명을 기준으로 특정 패턴 확인
            if "submit" in test_description.lower() or "send button" in test_description.lower():
                # 제출 버튼 탐색
                submit_buttons = page.locator('button[type="submit"], input[type="submit"], button:has-text("제출"), button:has-text("전송"), button:has-text("보내기")').count()

                if submit_buttons == 0:
                    browser.close()
                    return False, "No submit button found on the page"

            browser.close()
            return True, "Test appears feasible"

    except Exception as e:
        return True, f"Verification error (assuming feasible): {e}"


def run_single_iteration(url: str, plan_path: str, iteration: int):
    """단일 테스트 반복을 실행하고 결과를 반환합니다."""
    print("\n" + "=" * 60)
    print(f"ITERATION {iteration}")
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
    orchestrator = MasterOrchestrator(session_id=f"iteration_{iteration}")

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
    print(f"ITERATION {iteration} RESULTS")
    print("=" * 60)
    print(f"SUCCESS: {results['success']}")
    print(f"FAILED: {results['failed']}")
    print(f"PARTIAL: {results['partial']}")
    print(f"SKIPPED: {results['skipped']}")
    print("=" * 60)

    return results


def analyze_and_fix_failures(results, url: str):
    """
    실패한 테스트를 분석하고 자동 수정을 시도합니다.

    반환:
        (수정된 개수: int, 불가능한 테스트 목록: list)
    """
    print("\n🔧 Analyzing failures...")
    fixed_count = 0
    impossible_tests = []

    for scenario_result in results['scenarios']:
        if scenario_result['status'] != 'failed':
            continue

        test_id = scenario_result['id']
        scenario_desc = scenario_result.get('scenario', '')
        error_msg = scenario_result.get('error', '')

        print(f"\n  ❌ Analyzing {test_id}: {scenario_desc}")
        print(f"     Error: {error_msg[:150]}...")

        # 테스트가 실제로 가능한지 확인
        feasible, reason = verify_test_feasibility(url, scenario_desc, test_id)

        if not feasible:
            print(f"     ⚠️ Test marked as IMPOSSIBLE: {reason}")
            impossible_tests.append({
                'id': test_id,
                'reason': reason,
                'description': scenario_desc
            })
            continue

        # TODO: 오류 패턴을 기반으로 자동 수정 로직 추가
        # 현재는 비활성 요소 수정 로직에 의존

    return fixed_count, impossible_tests


def run_infinite_loop(url: str, plan_path: str, max_iterations: int = 10):
    """모든 테스트가 통과하거나 최대 반복 횟수에 도달할 때까지 테스트-수정 루프를 실행합니다."""
    print("🚀 Starting infinite test loop...")
    print(f"   Max iterations: {max_iterations}")
    print(f"   Target: {url}")

    for iteration in range(1, max_iterations + 1):
        # 테스트 실행
        results = run_single_iteration(url, plan_path, iteration)

        # 모든 테스트 통과 여부 확인
        if results['failed'] == 0:
            print(f"\n🎉 ALL TESTS PASSED in iteration {iteration}!")
            return results

        # 분석 및 수정
        fixed_count, impossible_tests = analyze_and_fix_failures(results, url)

        if impossible_tests:
            print(f"\n⚠️ Found {len(impossible_tests)} impossible tests:")
            for test in impossible_tests:
                print(f"   - {test['id']}: {test['reason']}")

        if fixed_count > 0:
            print(f"\n✅ Applied {fixed_count} automatic fixes")
        else:
            print(f"\n⚠️ No automatic fixes available for this iteration")

        # 다음 반복으로 진행
        print(f"\n🔄 Moving to iteration {iteration + 1}...")

    print(f"\n❌ Max iterations ({max_iterations}) reached without passing all tests")
    return results


if __name__ == "__main__":
    # 구성
    TARGET_URL = "https://final-blog-25638597.figma.site"
    TEST_PLAN = "/Users/coldmans/Documents/GitHub/capston/gaia/artifacts/plans/realistic_test_no_selectors.json"
    MAX_ITERATIONS = 10

    # 무한 루프 실행
    try:
        final_results = run_infinite_loop(TARGET_URL, TEST_PLAN, MAX_ITERATIONS)

        # 적절한 종료 코드 반환
        if final_results['failed'] > 0:
            sys.exit(1)
        else:
            sys.exit(0)

    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n💥 FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(3)
