#!/usr/bin/env python3
"""
Realistic Test Runner - Tests the type fallback implementation
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator
from gaia.src.utils.models import TestScenario, TestStep

def main():
    print("=" * 60)
    print("GAIA REALISTIC TEST RUNNER")
    print("=" * 60)

    # Target URL and test plan
    url = "https://test-sitev2.vercel.app/"
    test_plan_path = Path("/Users/coldmans/Documents/GitHub/capston/gaia/artifacts/plans/realistic_test_no_selectors.json")

    print(f"Target URL: {url}")
    print(f"Test Plan: {test_plan_path}")
    print()

    # Load test plan
    with open(test_plan_path, 'r', encoding='utf-8') as f:
        test_plan = json.load(f)

    print(f"Loaded {len(test_plan['test_scenarios'])} test scenarios")
    print()

    # Convert test plan to TestScenario objects
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

    # Create orchestrator
    orchestrator = IntelligentOrchestrator()

    try:
        # Run tests
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
                print(f"{symbol} {scenario.get('id')}: {scenario.get('scenario', 'Unknown')}")

        print("=" * 60)

    finally:
        orchestrator.close()

if __name__ == "__main__":
    main()
