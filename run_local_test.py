#!/usr/bin/env python3
"""
Test runner for local UI test site.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from gaia.src.phase4.master_orchestrator import MasterOrchestrator
from gaia.src.utils.models import TestScenario

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

def run_tests(url: str, plan_path: str):
    """Run tests and return results."""
    print("=" * 60)
    print("GAIA LOCAL SITE TEST RUNNER")
    print("=" * 60)
    print(f"Target URL: {url}")
    print(f"Test Plan: {plan_path}")
    print("=" * 60)

    print("\nLoading test plan...")
    scenarios = load_test_plan(plan_path)
    print(f"Loaded {len(scenarios)} test scenarios")

    print("\nInitializing MasterOrchestrator...")
    orchestrator = MasterOrchestrator(session_id="local_test_session")

    print("\nExecuting tests...")
    print("-" * 60)

    results = orchestrator.execute_scenarios(
        url=url,
        scenarios=scenarios,
        progress_callback=lambda msg: print(msg)
    )

    # Print results
    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)
    print(f"Total Scenarios: {results['total']}")
    print(f"SUCCESS: {results['success']} ({results['success']/results['total']*100:.1f}%)")
    print(f"PARTIAL: {results['partial']} ({results['partial']/results['total']*100:.1f}%)")
    print(f"FAILED: {results['failed']} ({results['failed']/results['total']*100:.1f}%)")
    print(f"SKIPPED: {results['skipped']} ({results['skipped']/results['total']*100:.1f}%)")
    print("=" * 60)

    # Detailed results
    print("\nDetailed Results:")
    print("-" * 60)
    for scenario_result in results['scenarios']:
        status_emoji = {
            'success': '✅',
            'partial': '⚠️',
            'failed': '❌',
            'skipped': '⏭️'
        }.get(scenario_result['status'], '?')

        print(f"{status_emoji} {scenario_result['id']}: {scenario_result.get('scenario', 'N/A')}")
        if 'error' in scenario_result:
            print(f"   Error: {scenario_result['error'][:100]}...")
        print()

    return results

if __name__ == "__main__":
    # Configuration
    TARGET_URL = "http://localhost:3000"
    TEST_PLAN = "/Users/coldmans/Documents/GitHub/capston/gaia/artifacts/plans/comprehensive_ui_test_no_selectors.json"

    # Run tests
    try:
        results = run_tests(TARGET_URL, TEST_PLAN)

        # Exit with appropriate code
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
