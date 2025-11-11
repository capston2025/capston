"""GAIA 서비스와 GUI 이벤트를 연결하는 애플리케이션 컨트롤러입니다."""
from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase1.agent_client import AgentServiceClient
from gaia.src.phase4.agent import AgentOrchestrator
from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator
from gaia.src.phase4.master_orchestrator import MasterOrchestrator
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.models import Assertion, TestScenario, TestStep
from gaia.src.utils.plan_repository import PlanRepository

from gaia.src.gui.worker import AutomationWorker
from gaia.src.gui.analysis_worker import AnalysisWorker


@dataclass(slots=True)
class ControllerConfig:
    pdf_loader: PDFLoader | None = None
    analyzer: SpecAnalyzer | None = None
    orchestrator: AgentOrchestrator | None = None


class AppController(QObject):
    """파일 입력, 플랜 생성, 자동화 실행을 조정합니다."""

    def __init__(self, window, config: ControllerConfig | None = None) -> None:
        super().__init__(window)
        self._window = window
        self._config = config or ControllerConfig()

        self._pdf_loader = self._config.pdf_loader or PDFLoader()
        self._analyzer = self._config.analyzer or SpecAnalyzer()
        self._agent_client = AgentServiceClient()
        self._tracker = ChecklistTracker()
        self._orchestrator = self._config.orchestrator or AgentOrchestrator(
            analyzer=self._analyzer,
            tracker=self._tracker,
        )
        self._intelligent_orchestrator = IntelligentOrchestrator(tracker=self._tracker)
        self._master_orchestrator = MasterOrchestrator(tracker=self._tracker)
        self._plan_repository = PlanRepository()

        self._current_pdf_text: str | None = None
        self._current_pdf_hash: str | None = None
        self._current_url: str | None = None
        self._plan: Sequence[TestScenario] = ()
        self._analysis_plan: Sequence[TestScenario] = ()
        self._worker_thread: QThread | None = None
        self._worker: AutomationWorker | None = None
        self._analysis_thread: QThread | None = None
        self._analysis_worker: AnalysisWorker | None = None

        self._connect_signals()

    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self._window.fileDropped.connect(self._on_file_dropped)
        self._window.planFileSelected.connect(self._on_plan_file_selected)
        self._window.startRequested.connect(self._on_start_requested)
        self._window.cancelRequested.connect(self._on_cancel_requested)
        self._window.urlSubmitted.connect(self._on_url_submitted)

    # ------------------------------------------------------------------
    @Slot(str)
    def _on_file_dropped(self, file_path: str) -> None:
        path = Path(file_path)
        if not path.exists():
            self._window.append_log(f"⚠️ File not found: {path}")
            return

        if path.suffix.lower() != ".pdf":
            self._window.append_log("⚠️ Only PDF files are supported at this time.")
            return

        self._window.append_log(f"📄 Loading PDF: {path.name}")

        # PDF 텍스트 추출
        try:
            result = self._pdf_loader.extract(path)
        except Exception as exc:  # pragma: no cover - 방어적 로깅
            self._window.append_log(f"❌ Failed to parse PDF: {exc}")
            return

        self._current_pdf_text = result.text

        # 캐싱을 위한 PDF 해시 생성
        import hashlib
        self._current_pdf_hash = hashlib.md5(result.text.encode()).hexdigest()[:12]

        # 즉각적인 피드백을 위해 휴리스틱 체크리스트를 먼저 표시
        self._window.show_checklist(result.checklist_items)
        self._window.append_log("📄 PDF loaded, starting AI analysis...")

        # 추천 URL이 있는지 확인
        if result.suggested_url:
            self._current_url = result.suggested_url
            self._window.set_url_field(result.suggested_url)
            self._window.append_log(f"🌐 Suggested test URL: {result.suggested_url}")

        # 백그라운드 스레드에서 Agent Builder 분석 시작
        self._start_analysis_worker(result.text)

    def _start_analysis_worker(self, pdf_text: str) -> None:
        """Agent Builder 분석을 워커 스레드에서 시작합니다."""
        if self._analysis_thread and self._analysis_thread.isRunning():
            self._window.append_log("⚠️ Analysis already in progress, please wait...")
            return

        thread = QThread(self)
        worker = AnalysisWorker(pdf_text, analyzer=self._analyzer)
        worker.moveToThread(thread)

        # 시그널 연결
        thread.started.connect(worker.run)
        worker.progress.connect(self._handle_worker_progress)
        worker.finished.connect(self._on_analysis_finished)
        worker.error.connect(self._on_analysis_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._analysis_thread = thread
        self._analysis_worker = worker
        self._window.show_loading_overlay("AI가 체크리스트를 정리하고 있어요…")
        thread.start()

    @Slot(str)
    def _on_plan_file_selected(self, file_path: str) -> None:
        path = Path(file_path)
        if not path.exists():
            self._window.append_log(f"⚠️ 저장된 플랜을 찾을 수 없습니다: {path}")
            return

        if self._analysis_thread and self._analysis_thread.isRunning():
            self._window.append_log("⚠️ 현재 분석이 진행 중입니다. 잠시 후 다시 시도해주세요.")
            return

        try:
            scenarios, metadata = self._plan_repository.load_plan_file(path)
        except Exception as exc:
            self._window.append_log(f"❌ 플랜을 불러오지 못했습니다: {exc}")
            return

        if not scenarios:
            self._window.append_log("⚠️ 선택한 플랜에 실행 가능한 시나리오가 없습니다.")
            return

        plan_list = list(scenarios)
        self._analysis_plan = plan_list
        self._plan = ()
        self._current_pdf_text = None
        self._current_pdf_hash = metadata.get("pdf_hash") if metadata else None
        loaded_url = (metadata.get("url") if metadata else "") or ""

        if loaded_url:
            self._current_url = loaded_url
            self._window.set_url_field(loaded_url)
            self._window.append_log(f"🌐 플랜에 저장된 URL을 불러왔습니다: {loaded_url}")
        else:
            self._window.append_log("ℹ️ 플랜에 URL 정보가 없어 직접 입력이 필요합니다.")

        self._window.show_scenarios(plan_list)
        summary = self._summarize_scenarios(plan_list)
        self._window.append_log(
            f"📂 '{path.name}' 플랜 불러오기 완료 — 총 {summary['total']}개 "
            f"(MUST {summary['must']}, SHOULD {summary['should']}, MAY {summary['may']})"
        )
        self._reset_tracker_with_plan(plan_list)

    @Slot(object)
    def _on_analysis_finished(self, analysis_result) -> None:
        """Agent Builder 분석 완료를 처리합니다."""
        self._window.hide_loading_overlay()
        summary = analysis_result.summary
        self._window.append_log(
            f"✅ Generated {summary['total']} test cases "
            f"(MUST: {summary['must']}, SHOULD: {summary['should']}, MAY: {summary['may']})"
        )

        # 글래스 카드 형태로 테스트 케이스 표시
        self._window.show_scenarios(analysis_result.checklist)

        # 🚨 FIX: Agent Service에서 이미 RT JSON을 받았으므로 재사용
        # analysis_result에 _rt_scenarios 속성이 있으면 사용, 없으면 변환
        if hasattr(analysis_result, '_rt_scenarios') and analysis_result._rt_scenarios:
            self._analysis_plan = analysis_result._rt_scenarios
            self._window.append_log(f"📋 Using {len(self._analysis_plan)} RT scenarios with selectors")
        else:
            # Fallback: TC checklist를 변환 (하위 호환성)
            self._analysis_plan = self._convert_testcases_to_scenarios(
                analysis_result.checklist
            )
        self._reset_tracker_with_plan(self._analysis_plan)

        # 재분석을 피하기 위해 플랜을 디스크에 저장
        # URL이 있으면 해당 URL로, 없으면 PDF 해시로 저장
        if self._analysis_plan:
            try:
                saved_path = self._plan_repository.save_plan_for_url(
                    self._current_url or "",
                    self._analysis_plan,
                    pdf_hash=self._current_pdf_hash
                )
                self._window.append_log(f"💾 Plan cached: {saved_path.name}")
            except Exception as e:
                self._window.append_log(f"⚠️ Failed to cache plan: {e}")

        # 각 테스트 케이스 로그
        for tc in analysis_result.checklist:
            self._window.append_log(f"  • {tc.id}: {tc.name}")

        # 챗봇 대화처럼 브라우저 뷰에 결과 표시
        self._show_analysis_results_in_browser(analysis_result)

        self._analysis_thread = None
        self._analysis_worker = None

    def _summarize_scenarios(self, scenarios: Sequence[TestScenario]) -> dict[str, int]:
        summary = {"total": 0, "must": 0, "should": 0, "may": 0}
        for scenario in scenarios:
            summary["total"] += 1
            priority = (scenario.priority or "").lower()
            if priority in {"must", "high"}:
                summary["must"] += 1
            elif priority in {"should", "medium"}:
                summary["should"] += 1
            else:
                summary["may"] += 1
        return summary

    def _show_analysis_results_in_browser(self, analysis_result) -> None:
        """Agent Builder 결과를 글래스 스타일로 브라우저 뷰에 표시합니다."""
        summary = analysis_result.summary

        must_cases = [tc for tc in analysis_result.checklist if tc.priority == 'MUST']
        should_cases = [tc for tc in analysis_result.checklist if tc.priority == 'SHOULD']
        may_cases = [tc for tc in analysis_result.checklist if tc.priority == 'MAY']

        sections_html = ""
        priority_groups = [
            ("must", "MUST PRIORITY", "제품 신뢰도를 지키는 필수 흐름", must_cases),
            ("should", "SHOULD PRIORITY", "경험을 강화하는 권장 흐름", should_cases),
            ("may", "MAY PRIORITY", "여유가 있을 때 확인할 선택 흐름", may_cases),
        ]

        for css_class, badge_text, description, cases in priority_groups:
            if not cases:
                continue

            card_html = []
            for tc in cases:
                steps_html = "".join(
                    f"<li>{html.escape(step)}</li>" for step in tc.steps
                )
                description = html.escape(getattr(tc, "scenario", tc.name))
                precondition_html = (
                    f"<div class='case-pre'>{html.escape(tc.precondition)}</div>"
                    if getattr(tc, "precondition", "")
                    else ""
                )
                expected_html = (
                    f"<div class='case-assertion'>✅ {html.escape(tc.expected_result)}</div>"
                    if getattr(tc, "expected_result", "")
                    else ""
                )
                card_html.append(
                    f"""
                    <div class="case-card">
                        <div class="case-id">{html.escape(tc.id)}</div>
                        <div class="case-title">{html.escape(tc.name)}</div>
                        <div class="case-desc">{description}</div>
                        {precondition_html}
                        <ul class="step-list">{steps_html}</ul>
                        {expected_html}
                    </div>
                    """
                )

            sections_html += f"""
            <div class="priority-group {css_class}">
                <div class="group-header">
                    <span class="group-badge">{badge_text}</span>
                    <span class="group-label">{description}</span>
                </div>
                <div class="group-cards">
                    {''.join(card_html)}
                </div>
            </div>
            """

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{
                    font-family: 'Pretendard', 'Noto Sans KR', 'Apple SD Gothic Neo', sans-serif;
                    background: linear-gradient(135deg, #f6f7ff 0%, #e8f1ff 100%);
                    color: #141731;
                    min-height: 100vh;
                }}
                .wrapper {{
                    max-width: 980px;
                    margin: 0 auto;
                    padding: 56px 24px 80px;
                }}
                .glass {{
                    background: rgba(255, 255, 255, 0.64);
                    border-radius: 32px;
                    border: 1px solid rgba(255, 255, 255, 0.45);
                    box-shadow: 0 32px 64px rgba(91, 95, 247, 0.18);
                    padding: 48px;
                }}
                .summary {{
                    display: flex;
                    flex-direction: column;
                    gap: 24px;
                    margin-bottom: 32px;
                }}
                .summary-pill {{
                    display: inline-flex;
                    align-items: center;
                    gap: 10px;
                    padding: 9px 20px;
                    border-radius: 999px;
                    font-size: 12px;
                    letter-spacing: 0.7px;
                    text-transform: uppercase;
                    font-weight: 600;
                    background: rgba(99, 102, 241, 0.18);
                    color: #5b5ff7;
                }}
                .summary h1 {{
                    font-size: 30px;
                    font-weight: 700;
                    color: #151833;
                }}
                .metrics {{
                    display: flex;
                    flex-wrap: wrap;
                    gap: 20px;
                }}
                .metric {{
                    flex: 1 1 140px;
                    background: rgba(255, 255, 255, 0.52);
                    border-radius: 22px;
                    border: 1px solid rgba(255, 255, 255, 0.35);
                    padding: 18px;
                    text-align: center;
                    box-shadow: 0 18px 30px rgba(91, 95, 247, 0.08);
                }}
                .metric .value {{
                    font-size: 28px;
                    font-weight: 700;
                    margin-bottom: 6px;
                }}
                .metric.must .value {{ color: #e11d48; }}
                .metric.should .value {{ color: #c2410c; }}
                .metric.may .value {{ color: #047857; }}
                .metric.total .value {{ color: #4338ca; }}
                .metric .label {{
                    font-size: 12px;
                    letter-spacing: 0.6px;
                    text-transform: uppercase;
                    color: #5d6183;
                }}
                .priority-group {{ margin-top: 34px; }}
                .group-header {{
                    display: flex;
                    gap: 14px;
                    align-items: center;
                    margin-bottom: 18px;
                }}
                .group-badge {{
                    font-size: 12px;
                    letter-spacing: 0.6px;
                    text-transform: uppercase;
                    font-weight: 600;
                    padding: 6px 14px;
                    border-radius: 999px;
                    background: rgba(99, 102, 241, 0.18);
                    color: #5b5ff7;
                }}
                .priority-group.must .group-badge {{ background: rgba(244, 63, 94, 0.18); color: #e11d48; }}
                .priority-group.should .group-badge {{ background: rgba(250, 204, 21, 0.2); color: #c2410c; }}
                .priority-group.may .group-badge {{ background: rgba(16, 185, 129, 0.2); color: #047857; }}
                .group-label {{
                    font-size: 15px;
                    font-weight: 600;
                    color: #1b1f3f;
                }}
                .group-cards {{
                    display: grid;
                    gap: 18px;
                    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
                }}
                .case-card {{
                    background: rgba(255, 255, 255, 0.72);
                    border-radius: 22px;
                    border: 1px solid rgba(255, 255, 255, 0.38);
                    padding: 20px 22px;
                    box-shadow: 0 18px 36px rgba(91, 95, 247, 0.12);
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                }}
                .case-id {{
                    font-size: 12px;
                    text-transform: uppercase;
                    letter-spacing: 0.6px;
                    color: #6367a5;
                }}
                .case-title {{
                    font-size: 16px;
                    font-weight: 600;
                    color: #161a3a;
                }}
                .case-pre {{
                    font-size: 12px;
                    color: #5d6183;
                    background: rgba(99, 102, 241, 0.08);
                    border-radius: 14px;
                    padding: 6px 12px;
                    display: inline-block;
                }}
                .step-list {{
                    margin-left: 16px;
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                    color: #2c3055;
                    font-size: 13px;
                }}
                .case-assertion {{
                    font-weight: 600;
                    color: #2563eb;
                    font-size: 13px;
                }}
                .footer-message {{
                    margin-top: 44px;
                    text-align: center;
                    font-size: 13px;
                    color: #5d6183;
                }}
            </style>
        </head>
        <body>
            <div class="wrapper">
                <div class="glass">
                    <div class="summary">
                        <span class="summary-pill">AI GENERATED TEST BLUEPRINT</span>
                        <h1>총 {summary['total']}개의 자동화 시나리오가 준비됐어요</h1>
                        <div class="metrics">
                            <div class="metric total">
                                <span class="value">{summary['total']}</span>
                                <span class="label">Total</span>
                            </div>
                            <div class="metric must">
                                <span class="value">{summary['must']}</span>
                                <span class="label">Must</span>
                            </div>
                            <div class="metric should">
                                <span class="value">{summary['should']}</span>
                                <span class="label">Should</span>
                            </div>
                            <div class="metric may">
                                <span class="value">{summary['may']}</span>
                                <span class="label">May</span>
                            </div>
                        </div>
                    </div>
                    {sections_html}
                    <div class="footer-message">URL을 설정한 뒤 “자동화 시작”을 눌러 실제 브라우저 실행을 확인해 보세요.</div>
                </div>
            </div>
        </body>
        </html>
        """

        self._window.show_html_in_browser(html_content)

    @Slot(str)
    def _on_analysis_error(self, error_message: str) -> None:
        """Agent Builder 분석 오류를 처리합니다."""
        self._window.hide_loading_overlay()
        self._window.append_log(f"❌ Agent Builder failed: {error_message}")
        self._window.append_log("📝 Using heuristic checklist instead")

        self._analysis_thread = None
        self._analysis_worker = None

    # ------------------------------------------------------------------
    @Slot()
    def _on_start_requested(self) -> None:
        if not self._current_url:
            self._window.append_log("⚠️ 테스트할 URL을 입력하거나 PDF에서 URL을 추출해주세요.")
            return

        if self._worker_thread:
            self._window.append_log("⚠️ Automation already in progress.")
            return

        # Agent Builder가 만든 추상 시나리오인 analysis_plan 사용
        candidate_plan: List[TestScenario] = list(self._analysis_plan) if self._analysis_plan else []

        if not candidate_plan:
            self._window.append_log("⚠️ 생성된 테스트 시나리오가 없습니다. PDF를 먼저 분석해주세요.")
            return

        # 1단계: MCP로 DOM 분석 및 스크린샷 캡처
        self._window.append_log("📸 MCP로 DOM 분석 및 스크린샷 캡처 중...")
        self._reset_tracker_with_plan(candidate_plan)

        # 2단계: LLM이 실행 가능한 테스트를 선택하고 우선순위 큐 생성
        # 3단계: 사이트 탐색과 함께 테스트 실행
        self._plan = candidate_plan
        self._window.append_log(f"🤖 Master Orchestrator 자동화를 시작합니다 ({len(candidate_plan)}개 시나리오)")
        self._window.append_log("   🗺️  1️⃣ 사이트 구조 탐색 (네비게이션 링크 발견)")
        self._window.append_log("   📄 2️⃣ 각 페이지별 DOM + 스크린샷 분석")
        self._window.append_log("   🚀 3️⃣ 페이지별로 실행 가능한 테스트 자동 실행")
        self._window.set_busy(True, message="AI가 사이트를 탐색하는 중이에요…")

        self._start_intelligent_worker(self._current_url, candidate_plan)

    def _start_intelligent_worker(self, url: str, plan: Sequence[TestScenario]) -> None:
        """사이트 탐색을 포함한 MasterOrchestrator를 백그라운드에서 시작합니다."""
        from gaia.src.gui.intelligent_worker import IntelligentWorker

        thread = QThread(self)
        # IntelligentOrchestrator 대신 MasterOrchestrator 사용
        worker = IntelligentWorker(url, plan, orchestrator=self._master_orchestrator)
        worker.moveToThread(thread)

        thread.started.connect(worker.start)
        worker.progress.connect(self._handle_worker_progress)
        worker.screenshot.connect(self._window.update_live_preview)
        worker.finished.connect(self._on_intelligent_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._worker_thread = thread
        self._worker = worker
        thread.start()

    def _start_worker(self, url: str, plan: Sequence[TestScenario]) -> None:
        thread = QThread(self)
        worker = AutomationWorker(url, plan, orchestrator=self._orchestrator)
        worker.moveToThread(thread)

        thread.started.connect(worker.start)
        worker.progress.connect(self._handle_worker_progress)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._worker_thread = thread
        self._worker = worker
        thread.start()

    # ------------------------------------------------------------------
    @Slot()
    def _on_intelligent_worker_finished(self) -> None:
        """IntelligentOrchestrator 완료를 처리합니다."""
        summary = self._tracker.coverage() * 100
        self._window.append_log(f"✅ LLM-powered automation completed. Coverage: {summary:.1f}%")
        self._window.set_busy(False)
        self._update_overall_progress_display()
        self._worker_thread = None
        self._worker = None

    @Slot()
    def _on_worker_finished(self) -> None:
        summary = self._tracker.coverage() * 100
        self._window.append_log(f"✅ Automation completed. Coverage: {summary:.1f}%")
        self._window.set_busy(False)
        self._update_overall_progress_display()
        self._worker_thread = None
        self._worker = None

    # ------------------------------------------------------------------
    @Slot()
    def _on_cancel_requested(self) -> None:
        if self._worker:
            self._worker.request_cancel()
            self._window.append_log("⏹️ Cancel requested.")
        else:
            self._window.append_log("ℹ️ No automation in progress.")

    # ------------------------------------------------------------------
    @Slot(str)
    def _on_url_submitted(self, url: str) -> None:
        self._current_url = url
        self._window.append_log(f"🌐 Loading URL: {url}")
        self._window.load_url(url)

        # 이전에 URL 없이 분석했다면 이제 URL과 함께 저장
        if self._analysis_plan and not self._plan:
            try:
                saved_path = self._plan_repository.save_plan_for_url(
                    url,
                    self._analysis_plan,
                    pdf_hash=self._current_pdf_hash
                )
                self._window.append_log(f"💾 Plan cached with URL: {saved_path.name}")
            except Exception as e:
                self._window.append_log(f"⚠️ Failed to cache plan: {e}")

    # ------------------------------------------------------------------
    @Slot(str)
    def _handle_worker_progress(self, message: str) -> None:
        self._window.append_log(message)
        self._update_overall_progress_display()

    def _reset_tracker_with_plan(self, scenarios: Sequence[TestScenario]) -> None:
        plan_list = list(scenarios)
        self._tracker.items.clear()
        if plan_list:
            self._tracker.seed_from_scenarios(plan_list)
        self._update_overall_progress_display()

    def _update_overall_progress_display(self) -> None:
        items = getattr(self._tracker, "items", {})
        total = len(items)
        completed = sum(1 for item in items.values() if getattr(item, "checked", False))
        percent = (completed / total * 100) if total else 0.0
        self._window.update_overall_progress(percent, completed, total)
        if total:
            progress_items = []
            for item in items.values():
                title = item.feature_id or item.description or ""
                status = getattr(item, "status", "pending")
                percent_value = 100.0 if item.checked else 0.0
                if status == "failed":
                    percent_value = 0.0
                progress_items.append((title, percent_value, status))
        else:
            progress_items = []
        self._window.update_test_progress(progress_items)

    # ------------------------------------------------------------------
    def _plan_has_selectors(self, scenarios: Sequence[TestScenario]) -> bool:
        for scenario in scenarios:
            for step in scenario.steps:
                if step.selector and step.selector.strip():
                    return True
        return False

    def _convert_testcases_to_scenarios(
        self, checklist: Sequence[object]
    ) -> List[TestScenario]:
        scenarios: List[TestScenario] = []
        for index, tc in enumerate(checklist, start=1):
            tc_id = getattr(tc, "id", f"TC_{index:03d}")
            name = getattr(tc, "name", getattr(tc, "scenario", "Unnamed scenario"))
            priority = getattr(tc, "priority", "MAY")
            expected = getattr(tc, "expected_result", "")

            steps_raw = list(getattr(tc, "steps", []) or [])
            steps: List[TestStep] = []
            for raw_step in steps_raw:
                if isinstance(raw_step, dict):
                    description = raw_step.get("description", "")
                    selector = raw_step.get("selector", "")
                    action = raw_step.get("action", "noop")
                    params = list(raw_step.get("params", []))
                else:
                    description = str(raw_step)
                    selector = ""
                    action = "note"
                    params = []

                steps.append(
                    TestStep(
                        description=description,
                        action=action,
                        selector=selector,
                        params=params,
                    )
                )

            assertion = Assertion(
                description=expected,
                selector="",
                condition="note",
                params=[],
            )

            scenarios.append(
                TestScenario(
                    id=str(tc_id),
                    priority=str(priority),
                    scenario=str(name),
                    steps=steps,
                    assertion=assertion,
                )
            )
        return scenarios
