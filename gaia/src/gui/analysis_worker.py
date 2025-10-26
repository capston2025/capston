"""Worker thread for Agent Builder analysis"""
from __future__ import annotations

from typing import Dict, Iterable

from PySide6.QtCore import QObject, Signal

from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.phase1.agent_client import AnalysisResult, TestCase
from gaia.src.utils.models import TestScenario


class AnalysisWorker(QObject):
    """Worker to analyze PDF with Agent Builder in background thread."""

    # Signals
    progress = Signal(str)  # Log messages
    finished = Signal(object)  # AnalysisResult
    error = Signal(str)  # Error message

    def __init__(self, pdf_text: str, analyzer: SpecAnalyzer | None = None):
        super().__init__()
        self.pdf_text = pdf_text
        self._analyzer = analyzer or SpecAnalyzer()

    def run(self) -> None:
        """Run the analysis (executed in worker thread)."""
        try:
            self.progress.emit("🤖 OpenAI Agent Builder에 분석을 요청하는 중입니다…")
            self.progress.emit("⏱️  문서 길이에 따라 2-5분 가량 소요될 수 있어요.")

            scenarios = self._analyzer.generate_from_spec(self.pdf_text)
            if not scenarios:
                raise RuntimeError("Agent Builder가 테스트 시나리오를 생성하지 못했습니다.")

            analysis_result = self._convert_to_analysis_result(scenarios)
            self.finished.emit(analysis_result)

        except Exception as exc:
            self.error.emit(str(exc))

    # ------------------------------------------------------------------
    def _convert_to_analysis_result(
        self,
        scenarios: Iterable[TestScenario],
    ) -> AnalysisResult:
        """Convert planner scenarios into the GUI-friendly AnalysisResult."""
        summary: Dict[str, int] = {"total": 0, "must": 0, "should": 0, "may": 0}
        test_cases: list[TestCase] = []

        for scenario in scenarios:
            summary["total"] += 1
            priority_label, summary_key = self._priority_mapping(scenario.priority)
            summary[summary_key] += 1

            steps = [step.description for step in scenario.steps]
            test_cases.append(
                TestCase(
                    id=scenario.id,
                    name=scenario.scenario,
                    category="",  # Category not provided by workflow
                    priority=priority_label,
                    precondition="",  # Placeholder; can be enriched later
                    steps=steps,
                    expected_result=scenario.assertion.description,
                )
            )

        return AnalysisResult(checklist=test_cases, summary=summary)

    @staticmethod
    def _priority_mapping(priority: str) -> tuple[str, str]:
        priority_normalized = (priority or "").strip().lower()
        if priority_normalized in {"must", "high"}:
            return "MUST", "must"
        if priority_normalized in {"should", "medium"}:
            return "SHOULD", "should"
        return "MAY", "may"
