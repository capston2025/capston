"""Agent 워크플로 기반 명세 분석 유틸리티입니다."""
from __future__ import annotations

import json
from typing import List, Sequence

from gaia.src.phase1.adapters import checklist_to_scenarios
from gaia.src.phase1.agent_client import AgentServiceClient
from gaia.src.utils.config import CONFIG, LLMConfig
from gaia.src.utils.models import DomElement, TestScenario


class SpecAnalyzer:
    """Agent Builder 워크플로를 호출해 자동화 플랜을 생성합니다."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        agent_client: AgentServiceClient | None = None,
    ) -> None:
        self.config = config or CONFIG.llm
        self._agent_client = agent_client or AgentServiceClient()

    # ------------------------------------------------------------------
    def generate_from_spec(
        self,
        document_text: str,
        feature_query: str = "",
        base_url: str = "",
    ) -> List[TestScenario]:
        try:
            print(f"[SpecAnalyzer] Calling Agent Service with document length: {len(document_text)}")
            if feature_query:
                print(f"[SpecAnalyzer] Feature query: {feature_query}")
            if base_url:
                print(f"[SpecAnalyzer] Base URL: {base_url}")
            # AgentWorkflowRunner 대신 AgentServiceClient를 사용
            result = self._agent_client.analyze_document(
                document_text,
                feature_query=feature_query,
                base_url=base_url,
            )
            print(f"[SpecAnalyzer] Agent Service returned result: {type(result)}")

            # 🚨 NEW: RT JSON을 직접 받은 경우
            if isinstance(result, dict) and 'test_scenarios' in result:
                print(f"[SpecAnalyzer] Received RT JSON directly from Agent Service")
                scenarios = self._convert_rt_json_to_scenarios(result)
                print(f"[SpecAnalyzer] Converted {len(scenarios)} RT scenarios")
                if not scenarios:
                    print("[SpecAnalyzer] WARNING: No scenarios in RT JSON, using fallback")
                    return self._fallback_plan()
                return scenarios

            # OLD: AnalysisResult (TC) 형식인 경우 (하위 호환성)
            print(f"[SpecAnalyzer] Result content (first 500 chars): {str(result)[:500]}")
            scenarios = self._convert_analysis_result(result)
            print(f"[SpecAnalyzer] Converted to {len(scenarios)} scenarios")
            if not scenarios:
                print("[SpecAnalyzer] WARNING: No scenarios generated, using fallback")
                return self._fallback_plan()
            return scenarios
        except Exception as e:
            # 오류 발생 시 로그를 남기고 폴백을 사용
            print(f"[SpecAnalyzer] ERROR: Agent Builder failed, using fallback: {e}")
            import traceback
            traceback.print_exc()
            return self._fallback_plan()

    def generate_from_context(
        self,
        dom_elements: Sequence[DomElement],
        document_text: str | None = None,
    ) -> List[TestScenario]:
        enriched_text_parts = []
        if document_text:
            enriched_text_parts.append(document_text)
        if dom_elements:
            dom_dump = json.dumps(
                [element.model_dump() for element in dom_elements],
                ensure_ascii=False,
            )
            enriched_text_parts.append("[DOM_ELEMENTS]\n" + dom_dump)

        combined_text = "\n\n".join(enriched_text_parts) if enriched_text_parts else ""
        try:
            # AgentWorkflowRunner 대신 AgentServiceClient를 사용
            result = self._agent_client.analyze_document(combined_text)
            # 시나리오 형식으로 변환
            scenarios = self._convert_analysis_result(result)
            return scenarios or self._fallback_plan()
        except Exception as e:
            # 오류 발생 시 로그를 남기고 폴백을 사용
            print(f"Warning: Agent Builder failed, using fallback: {e}")
            return self._fallback_plan()

    # ------------------------------------------------------------------
    def _convert_rt_json_to_scenarios(self, rt_json: dict) -> List[TestScenario]:
        """RT JSON을 TestScenario 목록으로 직접 변환합니다."""
        from gaia.src.utils.models import Assertion, TestStep

        scenarios = []
        for rt_scenario in rt_json.get("test_scenarios", []):
            # RT steps를 TestStep 객체로 변환 (action, selector, params 유지!)
            steps = []
            for step in rt_scenario.get("steps", []):
                action = step.get("action", "")
                selector = step.get("selector", "")
                params = step.get("params", [])

                # 🚨 디버깅: action 확인
                if action == "note" or action == "":
                    print(f"[WARN] RT step has action='{action}' - this should not happen!")
                    print(f"       Step: {step}")

                test_step = TestStep(
                    description=step.get("description", ""),
                    action=action,
                    selector=selector,
                    params=params,
                )
                steps.append(test_step)

                # 첫 번째 step 로깅
                if len(steps) == 1:
                    print(f"[DEBUG] First TestStep created: action={test_step.action}, selector={test_step.selector}")

            # RT assertion을 Assertion 객체로 변환
            rt_assertion = rt_scenario.get("assertion", {})
            assertion = Assertion(
                description=rt_assertion.get("description", ""),
                selector=rt_assertion.get("selector", ""),
                condition=rt_assertion.get("condition", "expectVisible"),
                params=rt_assertion.get("params", []),
                expected_outcome=rt_assertion.get("expected_outcome"),
                success_indicators=rt_assertion.get("success_indicators", []),
            )

            scenario = TestScenario(
                id=rt_scenario.get("id", ""),
                priority=rt_scenario.get("priority", "SHOULD"),
                scenario=rt_scenario.get("scenario", ""),
                steps=steps,
                assertion=assertion,
            )
            scenarios.append(scenario)

        return scenarios

    def _convert_analysis_result(self, result) -> List[TestScenario]:
        """AgentServiceClient AnalysisResult를 TestScenario 목록으로 변환합니다."""
        from gaia.src.utils.models import Assertion, TestStep

        scenarios = []
        for test_case in result.checklist:
            # 문자열 단계를 TestStep 객체로 변환
            steps = [
                TestStep(
                    description=step,
                    action="",  # 이후 자동 매핑
                    selector="",  # 이후 자동 매핑
                    params=[],
                )
                for step in test_case.steps
            ]

            # expected_result로 Assertion 생성
            assertion = Assertion(
                description=test_case.expected_result,
                selector="",  # 이후 자동 매핑
                condition="is_visible",  # 기본 조건
                params=[],
            )

            scenario = TestScenario(
                id=test_case.id,
                priority=test_case.priority,
                scenario=test_case.name,
                steps=steps,
                assertion=assertion,
            )
            scenarios.append(scenario)

        return scenarios

    def _fallback_plan(self) -> List[TestScenario]:
        fallback = {
            "test_scenarios": [
                {
                    "id": "TC_001",
                    "priority": "High",
                    "scenario": "사용자는 이메일과 비밀번호로 정상 로그인할 수 있다.",
                    "steps": [
                        {
                            "description": "로그인 페이지로 이동한다.",
                            "action": "goto",
                            "selector": "",
                            "params": ["https://example.com/login"],
                        },
                        {
                            "description": "이메일 입력 필드에 계정을 입력한다.",
                            "action": "fill",
                            "selector": "input[type=email]",
                            "params": ["test@example.com"],
                        },
                        {
                            "description": "비밀번호 입력 필드에 암호를 입력한다.",
                            "action": "fill",
                            "selector": "input[type=password]",
                            "params": ["hunter2"],
                        },
                        {
                            "description": "로그인 버튼을 클릭한다.",
                            "action": "click",
                            "selector": "button[type=submit]",
                            "params": [],
                        },
                    ],
                    "assertion": {
                        "description": "대시보드로 리다이렉션되는지 확인한다.",
                        "selector": "body",
                        "condition": "url_contains",
                        "params": ["/dashboard"],
                    },
                }
            ]
        }
        return [TestScenario.model_validate(item) for item in fallback["test_scenarios"]]
