"""
GAIA Orchestrator - AI Agent Orchestration
Coordinates multiple AI agents to perform end-to-end QA testing
"""

import json
from typing import Dict, List
from dataclasses import asdict

from src.phase1.agent_client import AgentServiceClient, AnalysisResult, TestCase
from src.tracker.checklist import ChecklistTracker
from src.utils.models import TestScenario, Assertion, TestStep
# from src.phase4.agent import ExplorerAgent  # Phase 4 구현 시
# from src.phase5.report import ReportGenerator  # Phase 5 구현 시


class GAIAOrchestrator:
    """Orchestrates the entire GAIA workflow"""

    def __init__(self):
        """Initialize orchestrator with all agents and services"""
        self.agent_client = AgentServiceClient()
        self.checklist_tracker = ChecklistTracker()

    def run(self, spec_text: str, target_url: str) -> Dict:
        """
        Run the complete GAIA workflow.

        Args:
            spec_text: The specification document text
            target_url: The target website URL to test

        Returns:
            Final report with coverage and findings
        """
        print("=" * 60)
        print("🚀 GAIA Orchestration Started")
        print("=" * 60)

        # ===== PHASE 1: Spec Analysis =====
        print("\n📋 Phase 1: Analyzing specification...")
        analysis_result = self._phase1_analyze(spec_text)

        print(f"✅ Generated {analysis_result.summary['total']} test cases")
        print(f"   - MUST: {analysis_result.summary['must']}")
        print(f"   - SHOULD: {analysis_result.summary['should']}")
        print(f"   - MAY: {analysis_result.summary['may']}")

        # Convert Agent Builder results to TestScenarios
        test_scenarios = self._convert_to_scenarios(analysis_result.checklist)

        # Initialize checklist tracker
        self.checklist_tracker.seed_from_scenarios(test_scenarios)

        # ===== PHASE 4: LLM Agent Exploration =====
        print("\n🔍 Phase 4: Exploring website with AI agent...")
        exploration_result = self._phase4_explore(
            target_url=target_url,
            checklist=analysis_result.checklist
        )

        # ===== PHASE 5: Report Generation =====
        print("\n📊 Phase 5: Generating report...")
        final_report = self._phase5_report()

        print("\n" + "=" * 60)
        print("✨ GAIA Orchestration Completed")
        print("=" * 60)

        return final_report

    def _phase1_analyze(self, spec_text: str) -> AnalysisResult:
        """
        Phase 1: Analyze specification using Agent Builder.

        This phase uses OpenAI Agent Builder to:
        1. Extract all features from the spec
        2. Generate structured test cases
        3. Categorize and prioritize tests
        """
        result = self.agent_client.analyze_document(spec_text)
        return result

    def _phase4_explore(self, target_url: str, checklist: List) -> Dict:
        """
        Phase 4: Explore website using LLM Agent with MCP.

        This phase:
        1. Provides the checklist to the LLM Agent
        2. Agent explores the website using Playwright MCP
        3. When features are found, marks them in the tracker
        4. Returns exploration statistics
        """
        # TODO: Implement Phase 4 Agent
        # For now, return mock result

        print(f"   Target URL: {target_url}")
        print(f"   Checklist items to find: {len(checklist)}")

        # Example of how Phase 4 will work:
        #
        # explorer = ExplorerAgent(
        #     checklist=checklist,
        #     tracker=self.checklist_tracker
        # )
        #
        # result = explorer.explore(target_url, instructions=f"""
        # You are a QA automation agent. Your goal is to explore {target_url}
        # and find the following features:
        #
        # {json.dumps([asdict(tc) for tc in checklist], indent=2, ensure_ascii=False)}
        #
        # For each feature you find:
        # 1. Navigate to the appropriate page
        # 2. Verify the feature exists and works
        # 3. Call checklist_tracker.mark_found(feature_id)
        #
        # Available tools:
        # - playwright.goto(url)
        # - playwright.click(selector)
        # - playwright.fill(selector, text)
        # - playwright.get_text(selector)
        # - checklist_tracker.mark_found(feature_id)
        # """)

        return {
            "explored": True,
            "pages_visited": 0,
            "features_found": 0
        }

    def _convert_to_scenarios(self, test_cases: List[TestCase]) -> List[TestScenario]:
        """
        Convert Agent Builder TestCases to GAIA TestScenarios.

        Args:
            test_cases: List of TestCase from Agent Builder

        Returns:
            List of TestScenario for GAIA system
        """
        scenarios = []
        for tc in test_cases:
            # Convert steps to TestStep objects
            steps = [
                TestStep(
                    description=step,
                    action="click",  # Default action, Phase 4 will determine actual action
                    selector="",     # Will be filled by Phase 4
                    params=[]
                )
                for step in tc.steps
            ]

            # Create assertion from expected_result
            assertion = Assertion(
                description=tc.expected_result,
                selector="",
                condition="exists",
                params=[]
            )

            # Create TestScenario
            scenario = TestScenario(
                id=tc.id,
                priority=tc.priority,
                scenario=tc.name,
                steps=steps,
                assertion=assertion
            )
            scenarios.append(scenario)

        return scenarios

    def _phase5_report(self) -> Dict:
        """
        Phase 5: Generate final report.

        This phase:
        1. Retrieves current checklist status
        2. Calculates coverage metrics
        3. Generates detailed report
        """
        coverage = self.checklist_tracker.coverage()
        checklist_dict = self.checklist_tracker.as_dict()

        checked_items = [item for item in checklist_dict.values() if item.checked]
        unchecked_items = [item for item in checklist_dict.values() if not item.checked]

        report = {
            "summary": {
                "total_features": len(checklist_dict),
                "found": len(checked_items),
                "missing": len(unchecked_items),
                "coverage_percentage": round(coverage * 100, 2)
            },
            "found_features": [
                {"id": item.feature_id, "description": item.description, "evidence": item.evidence}
                for item in checked_items
            ],
            "missing_features": [
                {"id": item.feature_id, "description": item.description}
                for item in unchecked_items
            ]
        }

        print(f"   Coverage: {coverage*100:.1f}% ({len(checked_items)}/{len(checklist_dict)})")
        print(f"   Found: {len(checked_items)} features")
        print(f"   Missing: {len(unchecked_items)} features")

        return report


# Example usage
if __name__ == "__main__":
    orchestrator = GAIAOrchestrator()

    # Sample spec
    spec = """
    온라인 쇼핑몰 웹사이트 기획서

    주요 기능:
    1. 회원가입 및 로그인
    2. 상품 검색
    3. 장바구니 담기
    4. 결제하기
    """

    # Run orchestration
    result = orchestrator.run(
        spec_text=spec,
        target_url="https://example-shop.com"
    )

    # Print final report
    print("\n" + "=" * 60)
    print("📄 FINAL REPORT")
    print("=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False))
