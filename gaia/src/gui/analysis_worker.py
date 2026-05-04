"""Agent Builder 분석을 수행하는 워커 스레드"""
from __future__ import annotations

from typing import Dict, Iterable

from PySide6.QtCore import QObject, Signal

from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.phase1.agent_client import AnalysisResult, TestCase
from gaia.src.phase4.goal_driven.goal_builder import goals_from_scenarios
from gaia.src.utils.models import TestScenario


class AnalysisWorker(QObject):
    """백그라운드 스레드에서 Agent Builder로 PDF를 분석하는 워커입니다."""

    # 시그널
    progress = Signal(str)  # 로그 메시지
    finished = Signal(object)  # AnalysisResult 객체
    error = Signal(str)  # 오류 메시지

    def __init__(
        self,
        pdf_text: str,
        analyzer: SpecAnalyzer | None = None,
        feature_query: str = "",
        base_url: str = "",
    ):
        super().__init__()
        self.pdf_text = pdf_text
        self._analyzer = analyzer or SpecAnalyzer()
        self.feature_query = feature_query
        self.base_url = str(base_url or "").strip()

    def run(self) -> None:
        """워크 스레드에서 분석을 실행합니다."""
        try:
            self.progress.emit("🤖 OpenAI Agent Builder에 분석을 요청하는 중입니다…")
            if self.feature_query:
                self.progress.emit(f"🎯 특정 기능 필터링: {self.feature_query}")
            if self.base_url:
                self.progress.emit(f"🌐 참조 사이트 링크: {self.base_url}")
            self.progress.emit("⏱️  문서 길이에 따라 2-5분 가량 소요될 수 있어요.")

            scenarios = self._analyzer.generate_from_spec(
                self.pdf_text,
                feature_query=self.feature_query,
                base_url=self.base_url,
            )
            if not scenarios:
                raise RuntimeError("Agent Builder가 테스트 시나리오를 생성하지 못했습니다.")

            analysis_result = self._convert_to_analysis_result(scenarios)
            extra_keywords = [self.feature_query] if self.feature_query else []
            analysis_result._goals = goals_from_scenarios(
                list(scenarios),
                extra_keywords=extra_keywords,
            )
            self.finished.emit(analysis_result)

        except Exception as exc:
            self.error.emit(str(exc))

    # ------------------------------------------------------------------
    def _convert_to_analysis_result(
        self,
        scenarios: Iterable[TestScenario],
    ) -> AnalysisResult:
        """플래너 시나리오를 GUI에서 사용하기 좋은 AnalysisResult로 변환합니다."""
        summary: Dict[str, int] = {"total": 0, "must": 0, "should": 0, "may": 0}
        test_cases: list[TestCase] = []
        scenarios_list = list(scenarios)  # Iterable을 list로 변환

        for scenario in scenarios_list:
            summary["total"] += 1
            priority_label, summary_key = self._priority_mapping(scenario.priority)
            summary[summary_key] += 1

            steps = [step.description for step in scenario.steps]
            test_cases.append(
                TestCase(
                    id=scenario.id,
                    name=scenario.scenario,
                    category="",  # 워크플로에서 카테고리를 제공하지 않음
                    priority=priority_label,
                    precondition="",  # 임시 자리표시자이며 후속 보강 가능
                    steps=steps,
                    expected_result=scenario.assertion.description,
                )
            )

        result = AnalysisResult(checklist=test_cases, summary=summary)
        # 🚨 FIX: RT scenarios를 AnalysisResult에 추가하여 action/selector 보존
        result._rt_scenarios = scenarios_list
        return result

    @staticmethod
    def _priority_mapping(priority: str) -> tuple[str, str]:
        priority_normalized = (priority or "").strip().lower()
        if priority_normalized in {"must", "high"}:
            return "MUST", "must"
        if priority_normalized in {"should", "medium"}:
            return "SHOULD", "should"
        return "MAY", "may"
