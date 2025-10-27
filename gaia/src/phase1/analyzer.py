"""Agent workflow-backed spec analysis utilities."""
from __future__ import annotations

import json
from typing import List, Sequence

from gaia.src.phase1.adapters import checklist_to_scenarios
from gaia.src.phase1.agent_client import AgentServiceClient
from gaia.src.utils.config import CONFIG, LLMConfig
from gaia.src.utils.models import DomElement, TestScenario


class SpecAnalyzer:
    """Generates automation plans by invoking an Agent Builder workflow."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        agent_client: AgentServiceClient | None = None,
    ) -> None:
        self.config = config or CONFIG.llm
        self._agent_client = agent_client or AgentServiceClient()

    # ------------------------------------------------------------------
    def generate_from_spec(self, document_text: str) -> List[TestScenario]:
        try:
            # Use AgentServiceClient instead of AgentWorkflowRunner
            result = self._agent_client.analyze_document(document_text)
            # Convert to scenarios format
            scenarios = self._convert_analysis_result(result)
            return scenarios or self._fallback_plan()
        except Exception as e:
            # For errors, log and use fallback
            print(f"Warning: Agent Builder failed, using fallback: {e}")
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
            # Use AgentServiceClient instead of AgentWorkflowRunner
            result = self._agent_client.analyze_document(combined_text)
            # Convert to scenarios format
            scenarios = self._convert_analysis_result(result)
            return scenarios or self._fallback_plan()
        except Exception as e:
            # For errors, log and use fallback
            print(f"Warning: Agent Builder failed, using fallback: {e}")
            return self._fallback_plan()

    # ------------------------------------------------------------------
    def _convert_analysis_result(self, result) -> List[TestScenario]:
        """Convert AgentServiceClient AnalysisResult to TestScenario list."""
        from gaia.src.utils.models import Assertion, TestStep

        scenarios = []
        for test_case in result.checklist:
            # Convert steps from strings to TestStep objects
            steps = [
                TestStep(
                    description=step,
                    action="",  # Will be auto-matched later
                    selector="",  # Will be auto-matched later
                    params=[],
                )
                for step in test_case.steps
            ]

            # Create assertion from expected_result
            assertion = Assertion(
                description=test_case.expected_result,
                selector="",  # Will be auto-matched later
                condition="is_visible",  # Default condition
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
