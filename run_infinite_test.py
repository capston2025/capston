#!/usr/bin/env python3
"""
Infinite test loop with auto-fixing for GAIA system.
Runs tests, analyzes failures, automatically fixes code, and repeats.
"""
import json
import os
import sys
from pathlib import Path
import subprocess

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from gaia.src.phase4.master_orchestrator import MasterOrchestrator
from gaia.src.utils.models import TestScenario

# Import Playwright MCP for verification
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("Warning: Playwright not available for verification")


def load_test_plan(plan_path: str):
    """Load test plan from JSON file."""
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
    Returns: (feasible: bool, reason: str)
    """
    if not HAS_PLAYWRIGHT:
        return True, "Cannot verify - Playwright not available"

    print(f"  🔍 Verifying test {test_id} feasibility...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until='networkidle')

            # Get page HTML
            html = page.content()

            # Check for specific patterns based on test description
            if "submit" in test_description.lower() or "send button" in test_description.lower():
                # Look for submit buttons
                submit_buttons = page.locator('button[type="submit"], input[type="submit"], button:has-text("제출"), button:has-text("전송"), button:has-text("보내기")').count()

                if submit_buttons == 0:
                    browser.close()
                    return False, "No submit button found on the page"

            browser.close()
            return True, "Test appears feasible"

    except Exception as e:
        return True, f"Verification error (assuming feasible): {e}"


def run_single_iteration(url: str, plan_path: str, iteration: int):
    """Run a single test iteration and return results."""
    print("\n" + "=" * 60)
    print(f"ITERATION {iteration}")
    print("=" * 60)
    print(f"Target URL: {url}")
    print(f"Test Plan: {plan_path}")
    print("=" * 60)

    # Load test plan
    print("\nLoading test plan...")
    scenarios = load_test_plan(plan_path)
    print(f"Loaded {len(scenarios)} test scenarios")

    # Initialize orchestrator
    print("\nInitializing MasterOrchestrator...")
    orchestrator = MasterOrchestrator(session_id=f"iteration_{iteration}")

    # Execute tests
    print("\nExecuting tests...")
    print("-" * 60)

    results = orchestrator.execute_scenarios(
        url=url,
        scenarios=scenarios,
        progress_callback=lambda msg: print(msg)
    )

    # Print results
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
    Analyze failed tests and attempt automatic fixes.
    Returns: (fixed_count: int, impossible_tests: list)
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

        # Check if test is actually feasible
        feasible, reason = verify_test_feasibility(url, scenario_desc, test_id)

        if not feasible:
            print(f"     ⚠️ Test marked as IMPOSSIBLE: {reason}")
            impossible_tests.append({
                'id': test_id,
                'reason': reason,
                'description': scenario_desc
            })
            continue

        # TODO: Add more automatic fixes here based on error patterns
        # For now, we rely on the disabled element fix already applied

    return fixed_count, impossible_tests


def run_infinite_loop(url: str, plan_path: str, max_iterations: int = 10):
    """Run test-fix loop until all tests pass or max iterations reached."""
    print("🚀 Starting infinite test loop...")
    print(f"   Max iterations: {max_iterations}")
    print(f"   Target: {url}")

    for iteration in range(1, max_iterations + 1):
        # Run tests
        results = run_single_iteration(url, plan_path, iteration)

        # Check if all tests passed
        if results['failed'] == 0:
            print(f"\n🎉 ALL TESTS PASSED in iteration {iteration}!")
            return results

        # Analyze and fix
        fixed_count, impossible_tests = analyze_and_fix_failures(results, url)

        if impossible_tests:
            print(f"\n⚠️ Found {len(impossible_tests)} impossible tests:")
            for test in impossible_tests:
                print(f"   - {test['id']}: {test['reason']}")

        if fixed_count > 0:
            print(f"\n✅ Applied {fixed_count} automatic fixes")
        else:
            print(f"\n⚠️ No automatic fixes available for this iteration")

        # Continue to next iteration
        print(f"\n🔄 Moving to iteration {iteration + 1}...")

    print(f"\n❌ Max iterations ({max_iterations}) reached without passing all tests")
    return results


if __name__ == "__main__":
    # Configuration
    TARGET_URL = "https://final-blog-25638597.figma.site"
    TEST_PLAN = "/Users/coldmans/Documents/GitHub/capston/gaia/artifacts/plans/realistic_test_no_selectors.json"
    MAX_ITERATIONS = 10

    # Run infinite loop
    try:
        final_results = run_infinite_loop(TARGET_URL, TEST_PLAN, MAX_ITERATIONS)

        # Exit with appropriate code
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
