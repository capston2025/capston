#!/usr/bin/env python3
"""Test runner for Hacker News real-world testing"""
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator

def main():
    print("=" * 60)
    print("GAIA REAL-WORLD TEST: HACKER NEWS")
    print("=" * 60)

    # Target URL and test plan
    url = "https://news.ycombinator.com"
    test_plan_path = Path("/Users/coldmans/Documents/GitHub/capston/gaia/artifacts/plans/hackernews_test.json")

    print(f"Target URL: {url}")
    print(f"Test Plan: {test_plan_path}")
    print()

    # Load test plan
    with open(test_plan_path, 'r', encoding='utf-8') as f:
        test_plan = json.load(f)

    print(f"Loaded {len(test_plan['test_scenarios'])} test scenarios")
    print()

    # Convert test plan to TestScenario objects
    from gaia.src.utils.models import TestScenario, TestStep, Assertion
    scenarios = []
    for scenario_dict in test_plan['test_scenarios']:
        # Parse steps if they exist
        steps = []
        for step_dict in scenario_dict.get('steps', []):
            # GAIA format uses step_description, not description
            step = TestStep(
                description=step_dict.get('step_description', ''),
                action=step_dict.get('action', 'wait'),
                selector='',  # Will be found automatically
                params=step_dict.get('params', [])
            )
            steps.append(step)

        # Parse assertion
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

    # Create orchestrator
    orchestrator = IntelligentOrchestrator()

    try:
        # Run tests
        print("🚀 Starting tests on REAL WEBSITE: Hacker News")
        print("=" * 60)
        print()

        results = orchestrator.execute_scenarios(url, scenarios)

        # Print results
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

        # Show detailed results
        if 'scenarios' in results:
            for scenario in results['scenarios']:
                status = scenario.get('status', 'unknown')
                symbol = '✓' if status == 'success' else '✗' if status == 'failed' else '~' if status == 'partial' else '-'
                print(f"{symbol} {scenario.get('id')}: {scenario.get('scenario', 'Unknown'[:60])}")

        print("=" * 60)

        # Calculate success rate
        if results['total'] > 0:
            success_rate = (results['success'] / results['total']) * 100
            print(f"\n✨ Success Rate: {success_rate:.1f}%")

    finally:
        # Close orchestrator if method exists
        if hasattr(orchestrator, 'close'):
            orchestrator.close()

if __name__ == "__main__":
    main()
