"""GAIA 서비스와 GUI 이벤트를 연결하는 애플리케이션 컨트롤러입니다."""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Sequence

from gaia.common import RunContext

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase1.prd_ingest import ingest_prd_bundle
from gaia.src.phase1.prd_bundle_repository import PRDBundleRepository
from gaia.src.phase1.agent_client import AgentServiceClient
from gaia.src.phase4.agent import AgentOrchestrator, MCPClient
from gaia.src.phase4.goal_driven import goals_from_scenarios, sort_goals_by_priority, TestGoal
from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator
from gaia.src.phase4.master_orchestrator import MasterOrchestrator
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.config import MCPConfig
from gaia.src.utils.models import Assertion, TestScenario, TestStep
from gaia.src.utils.plan_repository import PlanRepository

from gaia.src.gui.worker import AutomationWorker
from gaia.src.gui.analysis_worker import AnalysisWorker
from gaia.src.gui.goal_worker import GoalDrivenWorker, ExploratoryWorker
from gaia.chat_hub import HubContext, _compile_steering_policy, _format_steering_status, _looks_like_steering_text

TELEGRAM_BRIDGE_STATUS_FILE = Path.home() / ".gaia" / "telegram_bridge.status.json"


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
        self._session_key = (os.getenv("GAIA_SESSION_KEY") or "").strip() or None
        self._session_id = (
            (os.getenv("GAIA_MCP_SESSION_ID") or "").strip()
            or self._session_key
        )
        self._mcp_host_url = (
            (os.getenv("MCP_HOST_URL") or os.getenv("GAIA_MCP_HOST_URL") or "").strip()
            or None
        )
        self._max_actions = 50

        mcp_config: MCPConfig | None = None
        if self._mcp_host_url:
            mcp_config = MCPConfig(host_url=self._mcp_host_url)

        if self._config.orchestrator is not None:
            self._orchestrator = self._config.orchestrator
        else:
            mcp_client = MCPClient(config=mcp_config) if mcp_config else None
            self._orchestrator = AgentOrchestrator(
                analyzer=self._analyzer,
                tracker=self._tracker,
                mcp_client=mcp_client,
            )

        self._intelligent_orchestrator = IntelligentOrchestrator(
            tracker=self._tracker,
            mcp_config=mcp_config,
            session_id=self._session_id or "default",
        )
        self._master_orchestrator = MasterOrchestrator(
            tracker=self._tracker,
            mcp_config=mcp_config,
            session_id=self._session_id or "default",
        )
        self._plan_repository = PlanRepository()
        self._bundle_repository = PRDBundleRepository()

        self._current_pdf_text: str | None = None
        self._current_pdf_hash: str | None = None
        self._current_url: str | None = None
        self._current_feature_query: str | None = None  # ICR 측정용
        self._current_plan_file: str | None = None  # ICR 측정용
        self._current_bug_json: str | None = None  # ER 측정용 (이전 테스트 불러오기 시 bug.json)
        self._control_channel: str = "local"
        self._last_result_summary: dict[str, Any] | None = None
        self._last_progress_message: str = ""
        self._current_execution_goal: str = ""
        self._current_execution_step: str = ""
        self._blocked_reason: str = ""
        self._active_steering_policy: dict[str, Any] = {}
        self._pending_intervention: dict[str, Any] | None = None
        self._pending_intervention_event: threading.Event | None = None
        self._plan: Sequence[TestScenario] = ()
        self._analysis_plan: Sequence[TestScenario] = ()
        self._analysis_goals: Sequence[TestGoal] = ()
        self._startup_mode: str | None = None
        self._worker_thread: QThread | None = None
        self._worker: AutomationWorker | None = None
        self._analysis_thread: QThread | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._bridge_status_timer = QTimer(self)
        self._bridge_status_timer.setInterval(3000)
        self._bridge_status_timer.timeout.connect(self._refresh_bridge_status)

        self._connect_signals()

    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self._window.fileDropped.connect(self._on_file_dropped)
        self._window.planFileSelected.connect(self._on_plan_file_selected)
        self._window.bugJsonSelected.connect(self._on_bug_json_selected)
        self._window.inputSourceCleared.connect(self._on_input_source_cleared)
        self._window.startRequested.connect(self._on_start_requested)
        self._window.cancelRequested.connect(self._on_cancel_requested)
        self._window.chatMessageSubmitted.connect(self._on_chat_message_submitted)
        self._window.urlSubmitted.connect(self._on_url_submitted)

    def apply_run_context(
        self,
        context: RunContext | Mapping[str, Any] | None = None,
        *,
        url: str | None = None,
        plan_path: str | Path | None = None,
        bundle_path: str | Path | None = None,
        spec_path: str | Path | None = None,
        mode: str | None = None,
        feature_query: str | None = None,
        max_actions: int | None = None,
    ) -> None:
        """Load pre-populated state from CLI run context."""
        resolved_url = url or (
            context.url if isinstance(context, RunContext) else (
                context.get("url") if isinstance(context, Mapping) else None
            )
        )
        resolved_plan_path = plan_path or (
            context.plan_path if isinstance(context, RunContext) else (
                context.get("plan_path") if isinstance(context, Mapping) else None
            )
        )
        resolved_bundle_path = bundle_path or (
            context.get("bundle_path") if isinstance(context, Mapping) else None
        )
        resolved_spec_path = spec_path or (
            context.spec_path if isinstance(context, RunContext) else (
                context.get("spec_path") if isinstance(context, Mapping) else None
            )
        )
        resolved_max_actions: Any = max_actions
        if resolved_max_actions is None and isinstance(context, RunContext):
            summary = context.summary if isinstance(context.summary, Mapping) else {}
            resolved_max_actions = summary.get("max_actions")
        elif resolved_max_actions is None and isinstance(context, Mapping):
            summary = context.get("summary")
            if isinstance(summary, Mapping):
                resolved_max_actions = summary.get("max_actions")
            if resolved_max_actions is None:
                resolved_max_actions = context.get("max_actions")

        if resolved_url:
            self._current_url = str(resolved_url)
            self._window.set_url_field(self._current_url)

        if resolved_bundle_path:
            self._on_plan_file_selected(str(resolved_bundle_path))
        elif resolved_plan_path:
            self._on_plan_file_selected(str(resolved_plan_path))
        elif resolved_spec_path and str(resolved_spec_path).lower().endswith(".pdf"):
            self._on_file_dropped(str(resolved_spec_path))

        self.set_start_mode(mode)
        if feature_query:
            self._current_feature_query = feature_query
            self._window.set_feature_query(feature_query)
        if resolved_max_actions is not None:
            try:
                self._max_actions = max(1, int(resolved_max_actions))
            except (TypeError, ValueError):
                pass

    def set_start_mode(self, mode: str | None) -> None:
        normalized = None
        if mode == "plan":
            normalized = "bundle"
        elif mode == "ai":
            normalized = "ai"
        elif mode == "chat":
            normalized = "quick"
        elif mode in {"bundle", "ai", "quick"}:
            normalized = mode
        self._startup_mode = normalized
        if normalized:
            self._window.set_selected_run_mode(normalized)

    def set_control_channel(self, channel: str | None) -> None:
        normalized = "telegram" if str(channel or "").strip().lower() == "telegram" else "local"
        self._control_channel = normalized
        self._window.set_control_channel(normalized)
        if normalized == "telegram":
            self._refresh_bridge_status()
            self._bridge_status_timer.start()
        else:
            self._bridge_status_timer.stop()

    def _build_quick_goal(self, url: str, query: str) -> TestGoal:
        query_text = str(query or "").strip()
        words = [word for word in query_text.replace("/", " ").split() if word.strip()]
        return TestGoal(
            id=f"GUI_{int(time.time())}",
            name=(query_text[:40] or "gui quick test").strip(),
            description=query_text,
            priority="MUST",
            keywords=words[:5],
            success_criteria=[query_text],
            max_steps=20,
            start_url=url,
            test_data=({"steering_policy": dict(self._active_steering_policy)} if self._active_steering_policy else {}),
        )

    def _hub_context(self) -> HubContext:
        return HubContext(
            provider=str(os.getenv("GAIA_LLM_PROVIDER") or "openai"),
            model=str(os.getenv("GAIA_LLM_MODEL") or "gpt-5.4"),
            auth_strategy=str(os.getenv("GAIA_AUTH_STRATEGY") or "reuse"),
            url=str(self._current_url or ""),
            runtime="gui",
            control_channel=self._control_channel,
            session_key=self._session_key or "gui",
            session_id=self._session_id or (self._session_key or "gui"),
        )

    def _sync_execution_status(self) -> None:
        self._window.set_execution_status(
            goal=self._current_execution_goal,
            step=self._current_execution_step,
            blocked_reason=self._blocked_reason,
        )

    def _update_execution_from_progress(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        step_match = re.search(r"Step\s+(\d+)(?:/(\d+))?", text)
        if step_match:
            current = step_match.group(1)
            total = step_match.group(2)
            self._current_execution_step = f"{current}/{total}" if total else current
        if "개입 필요" in text:
            blocked_text = text.split("개입 필요", 1)[-1].strip(" :")
            if blocked_text:
                self._blocked_reason = blocked_text
        self._sync_execution_status()

    def _goal_intervention_callback(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        event = threading.Event()
        self._pending_intervention = dict(payload or {})
        self._pending_intervention_event = event
        reason = str(self._pending_intervention.get("reason") or self._pending_intervention.get("question") or "사용자 입력이 필요합니다.").strip()
        self._blocked_reason = reason
        self._sync_execution_status()
        self._window.append_chat_message("GAIA", f"개입 필요: {reason}")
        self._window.append_chat_message("GAIA", "입력을 마친 뒤 /resume 을 입력하세요.")
        if not event.wait(timeout=600):
            self._pending_intervention = None
            self._pending_intervention_event = None
            self._blocked_reason = "사용자 입력이 10분 내 제공되지 않았습니다."
            self._sync_execution_status()
            return {"action": "cancel", "proceed": False, "reason_code": "user_intervention_missing"}
        response = dict(self._pending_intervention.get("_response") or {})
        self._pending_intervention = None
        self._pending_intervention_event = None
        if bool(response.get("proceed")):
            self._blocked_reason = ""
        self._sync_execution_status()
        return response

    def _exploratory_intervention_callback(self, reason: str, current_url: str) -> bool:
        event = threading.Event()
        self._pending_intervention = {"kind": "exploratory", "reason": reason, "current_url": current_url}
        self._pending_intervention_event = event
        self._blocked_reason = reason
        self._sync_execution_status()
        self._window.append_chat_message("GAIA", f"개입 필요: {reason}")
        self._window.append_chat_message("GAIA", "계속하려면 /resume, 중단하려면 중단해 라고 입력하세요.")
        if not event.wait(timeout=600):
            self._pending_intervention = None
            self._pending_intervention_event = None
            self._blocked_reason = "사용자 입력이 10분 내 제공되지 않았습니다."
            self._sync_execution_status()
            return False
        response = dict(self._pending_intervention.get("_response") or {})
        self._pending_intervention = None
        self._pending_intervention_event = None
        if bool(response.get("proceed")):
            self._blocked_reason = ""
        self._sync_execution_status()
        return bool(response.get("proceed"))

    def _refresh_bridge_status(self) -> None:
        if self._control_channel != "telegram":
            return
        status_text = "제어 채널: 텔레그램 선택됨. bridge 상태 확인 중"
        try:
            if TELEGRAM_BRIDGE_STATUS_FILE.exists():
                import json
                payload = json.loads(TELEGRAM_BRIDGE_STATUS_FILE.read_text(encoding="utf-8"))
                state = str(payload.get("state") or "unknown").strip()
                if state == "running":
                    status_text = "제어 채널: 텔레그램 연결됨 (bridge 실행 중)"
                elif state == "stopped":
                    status_text = "제어 채널: 텔레그램 대기 (bridge 중지됨)"
                else:
                    status_text = f"제어 채널: 텔레그램 ({state})"
        except Exception:
            status_text = "제어 채널: 텔레그램 상태 확인 실패"
        self._window.set_bridge_status(status_text)

    # ------------------------------------------------------------------
    @Slot(str)
    def _on_file_dropped(self, file_path: str) -> None:
        path = Path(file_path)
        if not path.exists():
            self._window.append_log(f"⚠️ File not found: {path}")
            return

        suffix = path.suffix.lower()
        if suffix == ".json":
            self._on_plan_file_selected(str(path))
            return

        if suffix not in {".pdf", ".docx", ".md", ".txt"}:
            self._window.append_log("⚠️ 지원 형식: PDF, DOCX, MD, TXT, JSON 번들")
            return

        if suffix != ".pdf":
            self._window.append_log(f"📄 기획서 로딩: {path.name}")
            try:
                bundle = ingest_prd_bundle(
                    input_path=path,
                    base_url=self._current_url,
                )
                bundle_path = self._bundle_repository.save_bundle(bundle)
            except Exception as exc:
                self._window.append_log(f"❌ 기획서를 번들로 변환하지 못했습니다: {exc}")
                return

            self._analysis_plan = ()
            self._analysis_goals = sort_goals_by_priority(
                [
                    goal.to_test_goal(bundle.execution_profile.base_url)
                    for goal in bundle.generated_goals
                    if goal.enabled
                ]
            )
            self._plan = ()
            self._current_pdf_text = None
            self._current_pdf_hash = bundle.source.content_hash
            self._current_plan_file = str(bundle_path)

            if bundle.execution_profile.base_url:
                self._current_url = str(bundle.execution_profile.base_url)
                self._window.set_url_field(self._current_url)

            self._window.show_scenarios(self._analysis_goals)
            summary = self._summarize_goals(self._analysis_goals)
            self._window.append_log(
                f"📦 번들 생성 완료 — 총 {summary['total']}개 "
                f"(MUST {summary['must']}, SHOULD {summary['should']}, MAY {summary['may']})"
            )
            self._window.append_log(f"💾 저장 위치: {bundle_path}")
            self._reset_tracker_with_goals(self._analysis_goals)
            return

        self._window.append_log(f"📄 Loading PDF: {path.name}")

        # PDF 텍스트 추출
        try:
            result = self._pdf_loader.extract(path)
        except Exception as exc:  # pragma: no cover - 방어적 로깅
            self._window.append_log(f"❌ Failed to parse PDF: {exc}")
            return

        self._current_pdf_text = result.text
        self._analysis_plan = ()
        self._analysis_goals = ()

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

        # GUI에서 feature_query 가져오기
        feature_query = self._window.get_feature_query()
        self._current_feature_query = feature_query  # ICR 측정용 저장

        thread = QThread(self)
        worker = AnalysisWorker(pdf_text, analyzer=self._analyzer, feature_query=feature_query)
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
            if path.suffix.lower() == ".json":
                raw = path.read_text(encoding="utf-8")
                if "\"gaia.prd_bundle." in raw:
                    bundle = self._bundle_repository.load_bundle(path)
                    goals = [
                        goal.to_test_goal(bundle.execution_profile.base_url)
                        for goal in bundle.generated_goals
                        if goal.enabled
                    ]
                    if not goals:
                        self._window.append_log("⚠️ 선택한 번들에 실행 가능한 목표가 없습니다.")
                        return
                    self._analysis_plan = ()
                    self._analysis_goals = sort_goals_by_priority(goals)
                    self._plan = ()
                    self._current_pdf_text = None
                    self._current_pdf_hash = bundle.source.content_hash
                    self._current_plan_file = str(path)
                    loaded_url = (bundle.execution_profile.base_url or "").strip()
                    if loaded_url:
                        self._current_url = loaded_url
                        self._window.set_url_field(loaded_url)
                        self._window.append_log(f"🌐 번들에 저장된 URL을 불러왔습니다: {loaded_url}")
                    else:
                        self._window.append_log("ℹ️ 번들에 URL 정보가 없어 직접 입력이 필요합니다.")
                    self._window.show_scenarios(self._analysis_goals)
                    self._window.set_selected_input_source("bundle")
                    summary = self._summarize_goals(self._analysis_goals)
                    self._window.append_log(
                        f"📦 '{path.name}' 번들 불러오기 완료 — 총 {summary['total']}개 "
                        f"(MUST {summary['must']}, SHOULD {summary['should']}, MAY {summary['may']})"
                    )
                    self._reset_tracker_with_goals(self._analysis_goals)
                    return
            scenarios, metadata = self._plan_repository.load_plan_file(path)
        except Exception as exc:
            self._window.append_log(f"❌ 플랜을 불러오지 못했습니다: {exc}")
            return

        if not scenarios:
            self._window.append_log("⚠️ 선택한 플랜에 실행 가능한 시나리오가 없습니다.")
            return

        plan_list = list(scenarios)
        self._analysis_plan = plan_list
        self._analysis_goals = sort_goals_by_priority(goals_from_scenarios(plan_list))
        self._plan = ()
        self._current_pdf_text = None
        self._current_pdf_hash = metadata.get("pdf_hash") if metadata else None
        self._current_plan_file = str(path)  # ICR 측정을 위해 플랜 파일 경로 저장
        loaded_url = (metadata.get("url") if metadata else "") or ""

        if loaded_url:
            self._current_url = loaded_url
            self._window.set_url_field(loaded_url)
            self._window.append_log(f"🌐 플랜에 저장된 URL을 불러왔습니다: {loaded_url}")
        else:
            self._window.append_log("ℹ️ 플랜에 URL 정보가 없어 직접 입력이 필요합니다.")

        self._window.show_scenarios(self._analysis_goals)
        self._window.set_selected_input_source("bundle")
        summary = self._summarize_scenarios(plan_list)
        self._window.append_log(
            f"📂 '{path.name}' 플랜 불러오기 완료 — 총 {summary['total']}개 "
            f"(MUST {summary['must']}, SHOULD {summary['should']}, MAY {summary['may']})"
        )
        self._reset_tracker_with_goals(self._analysis_goals)

        # 플랜 불러오기 후 bug.json 선택 여부 묻기
        self._window.ask_for_bug_json()

    @Slot()
    def _on_input_source_cleared(self) -> None:
        if self._analysis_thread and self._analysis_thread.isRunning():
            self._window.append_log("⚠️ 현재 분석이 진행 중입니다. 완료 후 입력 소스를 비워주세요.")
            return
        self._analysis_plan = ()
        self._analysis_goals = ()
        self._plan = ()
        self._current_pdf_text = None
        self._current_pdf_hash = None
        self._current_plan_file = None
        self._current_bug_json = None
        self._window.set_selected_input_source("none")
        self._window.show_scenarios(())
        self._window.show_checklist(())
        self._reset_tracker_with_goals(())
        self._window.append_log("ℹ️ 입력 소스 선택을 해제했습니다. 빠른 목표 실행 또는 완전 자율 모드로 바로 진행할 수 있습니다.")

    @Slot(str)
    def _on_bug_json_selected(self, file_path: str) -> None:
        """Bug JSON 파일이 선택되었을 때 처리합니다."""
        if file_path and Path(file_path).exists():
            self._current_bug_json = file_path
            self._window.append_log(f"🐛 Bug JSON 파일 선택됨: {Path(file_path).name}")
            self._window.append_log("ℹ️ 테스트 완료 후 ER (Error Rate)이 자동으로 측정됩니다.")

            # "로그인" 관련 테스트인 경우 ICR도 측정
            if self._plan and any("로그인" in s.get("scenario", "") for s in self._plan):
                self._window.append_log("ℹ️ 로그인 기능 테스트 감지: ICR (Intent Coverage Rate)도 측정됩니다.")
        else:
            self._current_bug_json = None

    @Slot(object)
    def _on_analysis_finished(self, analysis_result) -> None:
        """Agent Builder 분석 완료를 처리합니다."""
        self._window.hide_loading_overlay()
        summary = analysis_result.summary
        self._window.append_log(
            f"✅ Generated {summary['total']} test cases "
            f"(MUST: {summary['must']}, SHOULD: {summary['should']}, MAY: {summary['may']})"
        )

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

        extra_keywords = [self._current_feature_query] if self._current_feature_query else []
        if hasattr(analysis_result, "_goals") and analysis_result._goals:
            self._analysis_goals = analysis_result._goals
        else:
            self._analysis_goals = goals_from_scenarios(
                self._analysis_plan,
                extra_keywords=extra_keywords,
            )

        self._analysis_goals = sort_goals_by_priority(list(self._analysis_goals))

        # 글래스 카드 형태로 목표(Goal) 표시
        self._window.show_scenarios(self._analysis_goals)
        self._reset_tracker_with_goals(self._analysis_goals)

        # 재분석을 피하기 위해 플랜을 디스크에 저장
        # URL이 있으면 해당 URL로, 없으면 PDF 해시로 저장
        if self._analysis_plan:
            try:
                saved_path = self._plan_repository.save_plan_for_url(
                    self._current_url or "",
                    self._analysis_plan,
                    pdf_hash=self._current_pdf_hash
                )
                self._current_plan_file = str(saved_path)  # ICR 측정용 저장
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
        self._analysis_plan = ()
        self._analysis_goals = ()

    # ------------------------------------------------------------------
    @Slot()
    def _on_start_requested(self) -> None:
        if not self._current_url:
            self._window.append_log("⚠️ 테스트할 URL을 입력하거나 PDF에서 URL을 추출해주세요.")
            return

        if self._worker_thread:
            self._window.append_log("⚠️ Automation already in progress.")
            return

        startup_mode = self._startup_mode
        if startup_mode:
            self._startup_mode = None

        self._last_result_summary = None
        self._current_execution_goal = ""
        self._current_execution_step = ""
        self._blocked_reason = ""
        self._window.reset_result_summary()
        self._sync_execution_status()
        candidate_goals = list(self._analysis_goals) if self._analysis_goals else []
        selected_mode = startup_mode or self._window.get_selected_run_mode()
        feature_query = self._window.get_feature_query().strip()

        if selected_mode == "ai":
            self._current_execution_goal = "완전 자율 탐색"
            self._current_execution_step = "0"
            self._sync_execution_status()
            self._window.append_log("🧭 AI 모드로 즉시 탐색 실행합니다.")
            self._window.set_busy(True, message="AI가 웹 사이트를 탐색하는 중이에요…")
            self._start_exploratory_worker(self._current_url, max_actions=self._max_actions)
            return

        if selected_mode == "quick":
            if feature_query:
                quick_goal = self._build_quick_goal(self._current_url, feature_query)
                candidate_goals = [quick_goal]
                self._window.show_scenarios(candidate_goals)
                self._current_execution_goal = quick_goal.name
            elif not candidate_goals:
                self._window.append_log("⚠️ 빠른 목표 실행을 위해 테스트할 기능을 입력해주세요.")
                return

        if selected_mode == "bundle" and not candidate_goals:
            self._window.append_log("⚠️ 기획서/번들 실행 모드입니다. 먼저 번들 또는 기획서를 불러와 주세요.")
            return

        if candidate_goals:
            if not self._current_execution_goal:
                if len(candidate_goals) == 1:
                    self._current_execution_goal = str(candidate_goals[0].name or "")
                else:
                    self._current_execution_goal = f"{len(candidate_goals)}개 목표 실행"
            self._current_execution_step = "0"
            self._sync_execution_status()
            self._reset_tracker_with_goals(candidate_goals)
            self._plan = list(self._analysis_plan)
            self._window.append_log(
                f"🎯 Goal-Driven 자동화를 시작합니다 ({len(candidate_goals)}개 목표)"
            )
            self._window.append_log("   ✅ 우선순위 기반 목표 실행")
            self._window.append_log("   🔎 실패 시 탐색 모드로 보완")
            self._window.set_busy(True, message="AI가 목표를 수행하는 중이에요…")
            self._start_goal_worker(self._current_url, candidate_goals)
            return

        self._window.append_log("ℹ️ 목표가 없어 Exploratory 모드로 실행합니다.")
        self._window.set_busy(True, message="AI가 자율 탐색을 수행하는 중이에요…")
        self._start_exploratory_worker(self._current_url)

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
        worker.scenario_started.connect(self._window.highlight_current_scenario)
        worker.scenario_finished.connect(lambda _: None)  # Could add completion logic here
        worker.finished.connect(self._on_intelligent_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._worker_thread = thread
        self._worker = worker
        thread.start()

    def _start_goal_worker(self, url: str, goals: Sequence[TestGoal]) -> None:
        """Goal-Driven 에이전트를 백그라운드에서 시작합니다."""
        thread = QThread(self)
        worker = GoalDrivenWorker(
            url,
            goals,
            tracker=self._tracker,
            session_id=self._session_id,
            mcp_host_url=self._mcp_host_url,
            intervention_callback=self._goal_intervention_callback,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.start)
        worker.progress.connect(self._handle_worker_progress)
        worker.screenshot.connect(self._window.update_live_preview)
        worker.scenario_started.connect(self._window.highlight_current_scenario)
        worker.scenario_finished.connect(lambda _: None)
        worker.result_ready.connect(self._on_worker_result_ready)
        worker.finished.connect(self._on_intelligent_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._worker_thread = thread
        self._worker = worker
        thread.start()

    def _start_exploratory_worker(self, url: str, *, max_actions: int | None = None) -> None:
        """Exploratory 에이전트를 백그라운드에서 시작합니다."""
        resolved_actions = self._max_actions
        if max_actions is not None:
            try:
                resolved_actions = max(1, int(max_actions))
            except (TypeError, ValueError):
                resolved_actions = self._max_actions

        thread = QThread(self)
        worker = ExploratoryWorker(
            url,
            max_actions=resolved_actions,
            session_id=self._session_id,
            mcp_host_url=self._mcp_host_url,
            user_intervention_callback=self._exploratory_intervention_callback,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.start)
        worker.progress.connect(self._handle_worker_progress)
        worker.screenshot.connect(self._window.update_live_preview)
        worker.result_ready.connect(self._on_worker_result_ready)
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
        self._window.append_log(f"✅ 자동화 실행 완료. Coverage: {summary:.1f}%")
        if self._last_result_summary is None:
            self._window.show_result_summary(
                {
                    "mode": "unknown",
                    "status": "success",
                    "reason": f"자동화 실행이 완료되었습니다. Coverage {summary:.1f}%",
                }
            )

        # 모든 시나리오 하이라이트 초기화
        self._window.reset_scenario_highlights()

        # 이전 테스트를 불러온 경우 ICR 측정 수행
        if self._current_plan_file and self._analysis_plan:
            # 로그인 관련 플랜인지 확인
            is_login_related = any(
                "로그인" in str(getattr(s, "scenario", "")).lower() or
                "login" in str(getattr(s, "scenario", "")).lower()
                for s in self._analysis_plan
            )

            if is_login_related:
                self._window.append_log("\n" + "="*60)
                self._window.append_log("📊 로그인 기능 테스트 감지 - ICR 지표를 측정합니다")
                self._window.append_log("="*60)

                try:
                    from measure_metrics import calculate_icr
                    import json
                    from pathlib import Path

                    # 성공한 시나리오만 필터링하여 임시 플랜 파일 생성
                    with open(self._current_plan_file, 'r', encoding='utf-8') as f:
                        plan_data = json.load(f)

                    # tracker에서 성공한 시나리오 ID 가져오기
                    successful_ids = set()
                    for scenario in self._analysis_plan:
                        item = self._tracker.items.get(scenario.id)
                        if item and item.status == 'success':
                            successful_ids.add(scenario.id)

                    # 성공한 시나리오만 포함한 필터링된 플랜 데이터
                    filtered_scenarios = [
                        s for s in plan_data.get('test_scenarios', [])
                        if s.get('id') in successful_ids
                    ]

                    # 임시 플랜 파일 생성
                    filtered_plan_data = plan_data.copy()
                    filtered_plan_data['test_scenarios'] = filtered_scenarios

                    temp_plan_path = Path(self._current_plan_file).parent / "temp_filtered_plan.json"
                    with open(temp_plan_path, 'w', encoding='utf-8') as f:
                        json.dump(filtered_plan_data, f, ensure_ascii=False, indent=2)

                    self._window.append_log(f"   🔍 성공한 시나리오만 포함: {len(filtered_scenarios)}/{len(plan_data.get('test_scenarios', []))}개")

                    icr_result = calculate_icr(
                        plan_file=str(temp_plan_path),
                        ground_truth_file="ground_truth.json",
                        feature_query="로그인"
                    )

                    # 임시 파일 삭제
                    temp_plan_path.unlink(missing_ok=True)

                    # ICR 결과를 GUI에 표시
                    icr_pct = icr_result['icr_percentage']
                    covered = icr_result['covered_test_cases_count']
                    total = icr_result['total_ground_truth_test_cases']
                    target_passed = "✅ PASS" if icr_result['target_80_passed'] else "❌ FAIL"

                    self._window.append_log(f"\n{'='*60}")
                    self._window.append_log(f"📈 정량지표: ICR (Intent Coverage Rate)")
                    self._window.append_log(f"{'='*60}")
                    self._window.append_log(f"🎯 측정 기능: 로그인")
                    self._window.append_log(f"📊 Ground Truth Test Cases: {total}개")
                    self._window.append_log(f"✅ 커버된 Test Cases: {covered}개")
                    self._window.append_log(f"📈 ICR: {icr_pct:.2f}%")
                    self._window.append_log(f"🎯 목표 달성 (≥80%): {target_passed}")
                    self._window.append_log(f"{'='*60}\n")

                except Exception as e:
                    self._window.append_log(f"⚠️ ICR 측정 실패: {e}")

        self._window.set_busy(False)
        self._update_overall_progress_display()
        self._worker_thread = None
        self._worker = None

    @Slot()
    def _on_worker_finished(self) -> None:
        summary = self._tracker.coverage() * 100
        self._window.append_log(f"✅ Automation completed. Coverage: {summary:.1f}%")

        # ICR 측정 (특정 기능 테스트인 경우에만)
        if self._current_feature_query and self._current_plan_file:
            self._window.append_log(f"\n📊 정량지표 측정 중... (Feature: {self._current_feature_query})")

            # 1. ICR 측정
            try:
                from measure_metrics import calculate_icr
                icr_result = calculate_icr(
                    plan_file=self._current_plan_file,
                    ground_truth_file="ground_truth.json",
                    feature_query=self._current_feature_query
                )

                # ICR 결과를 GUI에 표시
                icr_pct = icr_result['icr_percentage']
                covered = icr_result['covered_test_cases_count']
                total = icr_result['total_ground_truth_test_cases']
                target_passed = "✅ PASS" if icr_result['target_80_passed'] else "❌ FAIL"

                self._window.append_log(f"\n{'='*60}")
                self._window.append_log(f"📈 정량지표 1: ICR (Intent Coverage Rate)")
                self._window.append_log(f"{'='*60}")
                self._window.append_log(f"🎯 측정 기능: {self._current_feature_query}")
                self._window.append_log(f"📊 Ground Truth Test Cases: {total}개")
                self._window.append_log(f"✅ 커버된 Test Cases: {covered}개")
                self._window.append_log(f"📈 ICR: {icr_pct:.2f}%")
                self._window.append_log(f"🎯 목표 달성 (≥80%): {target_passed}")
                self._window.append_log(f"{'='*60}\n")

            except Exception as e:
                self._window.append_log(f"⚠️ ICR 측정 실패: {e}")

            # 1.5. ICR 측정 (이전 테스트 불러오기 + 로그인 기능인 경우)
            if self._current_bug_json and self._plan and any("로그인" in s.get("scenario", "") for s in self._plan):
                try:
                    from measure_metrics import calculate_icr

                    icr_result = calculate_icr(
                        plan_file=self._current_plan_file,
                        ground_truth_file="ground_truth.json",
                        feature_query="로그인"
                    )

                    # ICR 결과를 GUI에 표시
                    icr_pct = icr_result['icr_percentage']
                    covered = icr_result['covered_test_cases_count']
                    total = icr_result['total_ground_truth_test_cases']
                    target_passed = "✅ PASS" if icr_result['target_80_passed'] else "❌ FAIL"

                    self._window.append_log(f"\n{'='*60}")
                    self._window.append_log(f"📈 정량지표 1: ICR (Intent Coverage Rate)")
                    self._window.append_log(f"{'='*60}")
                    self._window.append_log(f"🎯 측정 기능: 로그인")
                    self._window.append_log(f"📊 Ground Truth Test Cases: {total}개")
                    self._window.append_log(f"✅ 커버된 Test Cases: {covered}개")
                    self._window.append_log(f"📈 ICR: {icr_pct:.2f}%")
                    self._window.append_log(f"🎯 목표 달성 (≥80%): {target_passed}")
                    self._window.append_log(f"{'='*60}\n")

                except Exception as e:
                    self._window.append_log(f"⚠️ ICR 측정 실패: {e}")

            # 2. ER 측정 (이전 테스트 불러오기 시 bug.json을 선택한 경우)
            if self._current_bug_json:
                try:
                    from measure_metrics import extract_bugs_from_logs
                    import os

                    # 로그 파일 경로 찾기
                    log_file = "/tmp/agent-service-metrics-test.log"

                    # 로그 파일이 없으면 다른 경로 시도
                    if not os.path.exists(log_file):
                        # GUI에서 실행한 경우 워커 로그 확인
                        # (현재는 간단히 파일이 없으면 스킵)
                        self._window.append_log(f"⚠️ ER 측정 스킵: 로그 파일을 찾을 수 없습니다 ({log_file})")
                    else:
                        er_result = extract_bugs_from_logs(
                            log_file=log_file,
                            audit_file=self._current_bug_json
                        )

                        # ER 결과를 GUI에 표시
                        er_pct = er_result['er_percentage']
                        total_seeded = er_result['total_seeded']
                        detected = er_result['detected_bugs']
                        missed = er_result['missed_seeded']
                        false_pos = er_result['bad_test_fails']
                        target_passed = "✅ PASS" if er_result['target_20_passed'] else "❌ FAIL"

                        self._window.append_log(f"\n{'='*60}")
                        self._window.append_log(f"📈 정량지표 2: ER (Error Rate)")
                        self._window.append_log(f"{'='*60}")
                        self._window.append_log(f"🐛 시드 버그 총 개수: {total_seeded}개")
                        self._window.append_log(f"✅ 탐지된 버그: {detected}개")
                        self._window.append_log(f"❌ 미탐지된 버그: {missed}개")
                        self._window.append_log(f"⚠️  False Positive: {false_pos}개")
                        self._window.append_log(f"📈 ER: {er_pct:.2f}%")
                        self._window.append_log(f"🎯 목표 달성 (≤20%): {target_passed}")
                        self._window.append_log(f"{'='*60}\n")

                except Exception as e:
                    self._window.append_log(f"⚠️ ER 측정 실패: {e}")

        self._window.set_busy(False)
        self._update_overall_progress_display()
        self._worker_thread = None
        self._worker = None

    # ------------------------------------------------------------------
    @Slot()
    def _on_cancel_requested(self) -> None:
        if self._pending_intervention is not None and self._pending_intervention_event is not None:
            self._pending_intervention["_response"] = {
                "action": "cancel",
                "proceed": False,
                "reason_code": "user_cancelled",
            }
            self._pending_intervention_event.set()
        self._blocked_reason = "사용자 요청으로 실행을 중단했습니다."
        self._sync_execution_status()
        if self._worker:
            self._worker.request_cancel()
            self._window.append_log("⏹️ Cancel requested.")
            self._window.append_chat_message("GAIA", "실행 중단을 요청했습니다.")
        else:
            self._window.append_log("ℹ️ No automation in progress.")
            self._window.append_chat_message("GAIA", "현재 진행 중인 실행이 없습니다.")

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

    @Slot(object)
    def _on_worker_result_ready(self, summary: object) -> None:
        if isinstance(summary, dict):
            normalized_summary = dict(summary)
            if self._current_execution_goal and not normalized_summary.get("current_goal"):
                normalized_summary["current_goal"] = self._current_execution_goal
            if self._current_execution_step and not normalized_summary.get("current_step"):
                normalized_summary["current_step"] = self._current_execution_step
            if self._blocked_reason and not normalized_summary.get("blocked_reason"):
                normalized_summary["blocked_reason"] = self._blocked_reason
            self._last_result_summary = normalized_summary
            self._current_execution_goal = str(
                normalized_summary.get("current_goal")
                or self._current_execution_goal
                or ""
            )
            self._current_execution_step = str(
                normalized_summary.get("current_step")
                or self._current_execution_step
                or ""
            )
            self._blocked_reason = str(
                normalized_summary.get("blocked_reason")
                or self._blocked_reason
                or ""
            )
            self._persist_execution_history(normalized_summary)
            self._sync_execution_status()
            self._window.show_result_summary(normalized_summary)

    def _persist_execution_history(self, summary: dict[str, Any]) -> None:
        try:
            repo_root = Path(__file__).resolve().parents[3]
            results_root = repo_root / "artifacts" / "exploration_results"
            results_root.mkdir(parents=True, exist_ok=True)

            step_timeline = list(summary.get("step_timeline") or [])
            total_steps = len(step_timeline)
            success_count = sum(1 for row in step_timeline if isinstance(row, dict) and bool(row.get("success")))
            failed_count = max(0, total_steps - success_count)
            validation_summary = dict(summary.get("validation_summary") or {})
            issues_count = int(summary.get("issues") or validation_summary.get("failed_checks") or 0)
            duration_seconds = 0.0
            raw_duration = summary.get("duration") or summary.get("duration_seconds") or 0
            try:
                duration_seconds = float(raw_duration or 0)
            except Exception:
                duration_seconds = 0.0

            payload = {
                "schema_version": "gaia.execution_history.v1",
                "timestamp": time.strftime("%Y-%m-%d %H:%M"),
                "start_url": str(getattr(self, "_current_url", "") or self._window._url_input.text() or "Unknown"),
                "mode": str(summary.get("mode") or "goal"),
                "status": str(summary.get("status") or "unknown"),
                "reason": str(summary.get("reason") or ""),
                "current_goal": str(summary.get("current_goal") or ""),
                "current_step": str(summary.get("current_step") or ""),
                "blocked_reason": str(summary.get("blocked_reason") or ""),
                "total_steps": total_steps,
                "success_count": success_count,
                "failed_count": failed_count,
                "issues_count": issues_count,
                "duration_seconds": duration_seconds,
                "proof_lines": list(summary.get("proof_lines") or []),
                "validation_summary": validation_summary,
                "step_timeline": step_timeline,
            }

            out_path = results_root / f"execution_{int(time.time())}.json"
            with open(out_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._window.append_log(f"⚠️ 실행 이력 저장 실패: {exc}")

    # ------------------------------------------------------------------
    @Slot(str)
    def _handle_worker_progress(self, message: str) -> None:
        self._last_progress_message = str(message or "")
        self._update_execution_from_progress(self._last_progress_message)
        self._window.append_log(message)
        self._update_overall_progress_display()

    @Slot(str)
    def _on_chat_message_submitted(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self._window.append_chat_message("사용자", text)

        lowered = text.lower()
        if lowered.startswith("/resume") or lowered == "resume":
            if not self._pending_intervention or not self._pending_intervention_event:
                self._window.append_chat_message("GAIA", "대기 중인 개입 요청이 없습니다.")
                return
            response: dict[str, Any] = {"action": "resume", "proceed": True}
            if "otp=" in lowered:
                otp = text.split("otp=", 1)[1].strip()
                if otp:
                    response["otp"] = otp
            self._pending_intervention["_response"] = response
            self._pending_intervention_event.set()
            self._blocked_reason = ""
            self._sync_execution_status()
            self._window.append_chat_message("GAIA", "개입 요청을 재개합니다.")
            return

        if lowered.startswith("/steer") or _looks_like_steering_text(text):
            raw = text.split(" ", 1)[1].strip() if lowered.startswith("/steer ") else text
            if lowered in {"/steer", "/steer status"}:
                if self._active_steering_policy:
                    self._window.append_chat_message("GAIA", _format_steering_status(self._active_steering_policy))
                else:
                    self._window.append_chat_message("GAIA", "현재 활성 스티어링 정책이 없습니다.")
                return
            if lowered == "/steer clear":
                self._active_steering_policy = {}
                self._window.append_chat_message("GAIA", "스티어링 정책을 해제했습니다.")
                return
            policy = _compile_steering_policy(raw, self._hub_context())
            self._active_steering_policy = dict(policy)
            if self._worker is not None and hasattr(self._worker, "apply_steering_policy"):
                self._worker.apply_steering_policy(self._active_steering_policy)
            self._window.append_chat_message("GAIA", _format_steering_status(self._active_steering_policy))
            return

        if any(token in lowered for token in ("취소", "중단", "멈춰", "그만")):
            self._on_cancel_requested()
            return

        if (
            "지금" in text and ("뭐" in text or "상태" in text or "어디" in text)
        ) or "진행 상황" in text:
            if self._worker or self._current_execution_goal or self._current_execution_step or self._blocked_reason:
                parts = []
                if self._current_execution_goal:
                    parts.append(f"현재 목표: {self._current_execution_goal}")
                if self._current_execution_step:
                    parts.append(f"현재 단계: {self._current_execution_step}")
                if self._blocked_reason:
                    parts.append(f"대기 사유: {self._blocked_reason}")
                if self._last_progress_message:
                    parts.append(f"최근 진행: {self._last_progress_message}")
                reply = "\n".join(parts) if parts else "현재 실행 중이지만 아직 상태 메시지가 없습니다."
            elif isinstance(self._last_result_summary, dict):
                reply = str(self._last_result_summary.get("reason") or "직전 실행 결과가 없습니다.")
            else:
                reply = "아직 실행 전입니다."
            self._window.append_chat_message("GAIA", reply)
            return

        if self._worker:
            self._window.append_chat_message(
                "GAIA",
                "현재 실행 중입니다. 취소를 원하면 '중단해'라고 입력하거나, 진행 상황을 물어보세요.",
            )
            return

        if not self._current_url:
            self._window.append_chat_message("GAIA", "먼저 테스트할 URL을 입력해 주세요.")
            return

        self._window.set_selected_run_mode("quick")
        self._window.set_feature_query(text)
        self._window.append_chat_message("GAIA", "빠른 목표 실행으로 처리합니다.")
        self._on_start_requested()

    def _reset_tracker_with_plan(self, scenarios: Sequence[TestScenario]) -> None:
        plan_list = list(scenarios)
        self._tracker.items.clear()
        if plan_list:
            self._tracker.seed_from_scenarios(plan_list)
        self._update_overall_progress_display()

    def _reset_tracker_with_goals(self, goals: Sequence[TestGoal]) -> None:
        goal_list = list(goals)
        self._tracker.items.clear()
        if goal_list:
            self._tracker.seed_from_goals(goal_list)
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
