"""
GAIA 오케스트레이터 - AI 에이전트 오케스트레이션
엔드 투 엔드 QA 테스트를 수행하도록 여러 AI 에이전트를 조율합니다.
"""

import json
from typing import Dict, List
from dataclasses import asdict

from src.phase1.agent_client import AgentServiceClient, AnalysisResult, TestCase
from src.tracker.checklist import ChecklistTracker
from src.utils.models import TestScenario, Assertion, TestStep
# Phase 4 구현 시 사용할 import: from src.phase4.agent import ExplorerAgent
# Phase 5 구현 시 사용할 import: from src.phase5.report import ReportGenerator


class GAIAOrchestrator:
    """전체 GAIA 워크플로를 조율합니다"""

    def __init__(self):
        """모든 에이전트와 서비스를 초기화합니다"""
        self.agent_client = AgentServiceClient()
        self.checklist_tracker = ChecklistTracker()

    def run(self, spec_text: str, target_url: str) -> Dict:
        """
        GAIA 전체 워크플로를 실행합니다.

        매개변수:
            spec_text: 명세 문서 텍스트
            target_url: 테스트할 대상 웹사이트 URL

        반환:
            커버리지와 발견 사항을 포함한 최종 보고서
        """
        print("=" * 60)
        print("🚀 GAIA Orchestration Started")
        print("=" * 60)

        # ===== 1단계: 명세 분석 =====
        print("\n📋 Phase 1: Analyzing specification...")
        analysis_result = self._phase1_analyze(spec_text)

        print(f"✅ Generated {analysis_result.summary['total']} test cases")
        print(f"   - MUST: {analysis_result.summary['must']}")
        print(f"   - SHOULD: {analysis_result.summary['should']}")
        print(f"   - MAY: {analysis_result.summary['may']}")

        # Agent Builder 결과를 TestScenario로 변환
        test_scenarios = self._convert_to_scenarios(analysis_result.checklist)

        # 체크리스트 추적기를 초기화
        self.checklist_tracker.seed_from_scenarios(test_scenarios)

        # ===== 4단계: LLM 에이전트 탐색 =====
        print("\n🔍 Phase 4: Exploring website with AI agent...")
        exploration_result = self._phase4_explore(
            target_url=target_url,
            checklist=analysis_result.checklist
        )

        # ===== 5단계: 보고서 생성 =====
        print("\n📊 Phase 5: Generating report...")
        final_report = self._phase5_report()

        print("\n" + "=" * 60)
        print("✨ GAIA Orchestration Completed")
        print("=" * 60)

        return final_report

    def _phase1_analyze(self, spec_text: str) -> AnalysisResult:
        """
        1단계: Agent Builder를 사용해 명세를 분석합니다.

        이 단계에서는 OpenAI Agent Builder를 활용해 다음을 수행합니다.
        1. 명세에서 모든 기능을 추출
        2. 구조화된 테스트 케이스 생성
        3. 테스트를 범주화하고 우선순위를 지정
        """
        result = self.agent_client.analyze_document(spec_text)
        return result

    def _phase4_explore(self, target_url: str, checklist: List) -> Dict:
        """
        4단계: MCP를 사용하는 LLM 에이전트로 웹사이트를 탐색합니다.

        이 단계에서는 다음을 수행합니다.
        1. 체크리스트를 LLM 에이전트에 전달
        2. Playwright MCP로 웹사이트 탐색
        3. 기능을 찾으면 트래커에 표시
        4. 탐색 통계를 반환
        """
        # TODO: Phase 4 에이전트 구현
        # 현재는 목업 결과를 반환

        print(f"   Target URL: {target_url}")
        print(f"   Checklist items to find: {len(checklist)}")

        # Phase 4 동작 예시:
        #
        # explorer = ExplorerAgent(
        #     checklist=checklist,
        #     tracker=self.checklist_tracker
        # )
        #
        # result = explorer.explore(target_url, instructions=f"""
        # 당신은 QA 자동화 에이전트입니다. 목표는 {target_url}를 탐색하는 것입니다
        # 그리고 다음 기능을 찾아야 합니다:
        #
        # {json.dumps([asdict(tc) for tc in checklist], indent=2, ensure_ascii=False)}
        #
        # 각 기능을 찾을 때마다:
        # 1. 해당 페이지로 이동
        # 2. 기능이 존재하고 동작하는지 확인
        # 3. checklist_tracker.mark_found(feature_id)를 호출
        #
        # 사용 가능한 도구:
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
        Agent Builder의 TestCase를 GAIA용 TestScenario로 변환합니다.

        매개변수:
            test_cases: Agent Builder가 생성한 TestCase 목록

        반환:
            GAIA 시스템에서 사용할 TestScenario 목록
        """
        scenarios = []
        for tc in test_cases:
            # 단계 문자열을 TestStep 객체로 변환
            steps = [
                TestStep(
                    description=step,
                    action="click",  # 기본 동작이며 실제 동작은 Phase 4에서 결정
                    selector="",     # Phase 4에서 채워짐
                    params=[]
                )
                for step in tc.steps
            ]

            # expected_result로 Assertion 생성
            assertion = Assertion(
                description=tc.expected_result,
                selector="",
                condition="exists",
                params=[]
            )

            # TestScenario 생성
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
        5단계: 최종 보고서를 생성합니다.

        이 단계에서는 다음을 수행합니다.
        1. 현재 체크리스트 상태를 가져옴
        2. 커버리지 지표를 계산
        3. 상세 보고서를 생성
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


# 사용 예시
if __name__ == "__main__":
    orchestrator = GAIAOrchestrator()

    # 샘플 명세
    spec = """
    온라인 쇼핑몰 웹사이트 기획서

    주요 기능:
    1. 회원가입 및 로그인
    2. 상품 검색
    3. 장바구니 담기
    4. 결제하기
    """

    # 오케스트레이션 실행
    result = orchestrator.run(
        spec_text=spec,
        target_url="https://example-shop.com"
    )

    # 최종 보고서 출력
    print("\n" + "=" * 60)
    print("📄 FINAL REPORT")
    print("=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False))
