#!/usr/bin/env python3
"""
Auto-test runner for GAIA system.
Loads test plan with no selectors and executes using MasterOrchestrator.
"""
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv()

from gaia.src.phase4.master_orchestrator import MasterOrchestrator
from gaia.src.utils.models import TestScenario

def load_test_plan(plan_path: str):
    """Load test plan from JSON file."""
    with open(plan_path, 'r') as f:
        data = json.load(f)

    # Convert to TestScenario objects
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
    print("GAIA AUTO-TEST RUNNER")
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
    orchestrator = MasterOrchestrator(session_id="auto_test_session")

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
    print("TEST RESULTS")
    print("=" * 60)
    print(f"Total Scenarios: {results['total']}")
    print(f"SUCCESS: {results['success']} ({results['success']/results['total']*100:.1f}%)")
    print(f"PARTIAL: {results['partial']} ({results['partial']/results['total']*100:.1f}%)")
    print(f"FAILED: {results['failed']} ({results['failed']/results['total']*100:.1f}%)")
    print(f"SKIPPED: {results['skipped']} ({results['skipped']/results['total']*100:.1f}%)")
    print(f"Pages Explored: {results.get('pages_explored', 'N/A')}")
    print("=" * 60)

    # Detailed results
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
    # Configuration
    TARGET_URL = "https://final-blog-25638597.figma.site"
    TEST_PLAN = "/Users/coldmans/Documents/GitHub/capston/gaia/artifacts/plans/realistic_test_no_selectors.json"

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
