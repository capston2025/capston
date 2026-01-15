"""Qt workers for goal-driven and exploratory automation."""
from __future__ import annotations

import time
from typing import Sequence

from PySide6.QtCore import QObject, Signal

from gaia.src.phase4.goal_driven import (
    ExplorationConfig,
    ExploratoryAgent,
    GoalDrivenAgent,
    TestGoal,
    sort_goals_by_priority,
)
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.config import CONFIG


class GoalDrivenWorker(QObject):
    """Run goal-driven automation in a background thread."""

    progress = Signal(str)
    screenshot = Signal(str, object)  # (base64, click_position dict 또는 None)
    scenario_started = Signal(str)
    scenario_finished = Signal(str)
    finished = Signal()

    def __init__(
        self,
        url: str,
        goals: Sequence[TestGoal],
        *,
        tracker: ChecklistTracker | None = None,
        fallback_actions: int = 8,
        session_id: str | None = None,
        mcp_host_url: str | None = None,
    ) -> None:
        super().__init__()
        self._base_url = url
        self._goals = sort_goals_by_priority(list(goals))
        self._tracker = tracker
        self._fallback_actions = max(0, int(fallback_actions))
        self._cancel_requested = False
        self._session_id = session_id or f"goal_ui_{int(time.time())}"
        self._mcp_host_url = mcp_host_url or CONFIG.mcp.host_url

        self._goal_agent = GoalDrivenAgent(
            mcp_host_url=self._mcp_host_url,
            session_id=self._session_id,
            log_callback=self._on_progress,
            screenshot_callback=self._on_screenshot,
        )

    def start(self) -> None:
        if not self._goals:
            self.progress.emit("ℹ️ 실행할 목표가 없습니다.")
            self.finished.emit()
            return

        self.progress.emit(f"🎯 Goal-Driven 자동화를 시작합니다 ({len(self._goals)}개 목표)")

        for index, goal in enumerate(self._goals, start=1):
            if self._cancel_requested:
                self.progress.emit("⏹️ Goal-Driven 실행이 취소되었습니다.")
                break

            goal_to_run = goal
            if index == 1 and not goal.start_url:
                goal_to_run = goal.model_copy(update={"start_url": self._base_url})

            self.scenario_started.emit(goal_to_run.id)
            self.progress.emit(
                f"[{index}/{len(self._goals)}] {goal_to_run.priority} - {goal_to_run.name}"
            )

            result = self._goal_agent.execute_goal(goal_to_run)

            if not result.success and self._fallback_actions > 0 and not self._cancel_requested:
                self.progress.emit(
                    f"🔎 목표 실패 → 탐색 모드로 전환 ({self._fallback_actions} 액션)"
                )
                exploration_config = ExplorationConfig(
                    max_actions=self._fallback_actions,
                    max_depth=2,
                )
                exploratory_agent = ExploratoryAgent(
                    mcp_host_url=self._mcp_host_url,
                    session_id=self._session_id,
                    config=exploration_config,
                    log_callback=self._on_exploration_progress,
                    screenshot_callback=self._on_screenshot,
                )
                exploratory_agent.explore(self._base_url)
                self.progress.emit("🔁 목표를 다시 시도합니다.")
                result = self._goal_agent.execute_goal(goal_to_run)

            status = "success" if result.success else "failed"
            if self._tracker:
                self._tracker.set_status(goal_to_run.id, status, evidence=result.final_reason)

            if result.success:
                self.progress.emit(f"   ✅ 목표 달성: {goal_to_run.name}")
            else:
                self.progress.emit(f"   ❌ 목표 실패: {goal_to_run.name}")
                self.progress.emit(f"      이유: {result.final_reason}")

            self.scenario_finished.emit(goal_to_run.id)

        self.finished.emit()

    def _on_progress(self, message: str) -> None:
        self.progress.emit(message)

    def _on_exploration_progress(self, message: str) -> None:
        self.progress.emit(f"[탐색] {message}")

    def _on_screenshot(self, screenshot_base64: str, click_position: dict | None = None) -> None:
        self.screenshot.emit(screenshot_base64, click_position)

    def request_cancel(self) -> None:
        self._cancel_requested = True


class ExploratoryWorker(QObject):
    """Run exploratory automation in a background thread."""

    progress = Signal(str)
    screenshot = Signal(str, object)
    finished = Signal()

    def __init__(
        self,
        url: str,
        *,
        max_actions: int = 50,
        session_id: str | None = None,
        mcp_host_url: str | None = None,
    ) -> None:
        super().__init__()
        self._url = url
        self._max_actions = max(1, int(max_actions))
        self._cancel_requested = False
        self._session_id = session_id or f"explore_ui_{int(time.time())}"
        self._mcp_host_url = mcp_host_url or CONFIG.mcp.host_url

    def start(self) -> None:
        self.progress.emit(f"🔍 Exploratory 모드를 시작합니다 (최대 {self._max_actions} 액션)")
        agent = ExploratoryAgent(
            mcp_host_url=self._mcp_host_url,
            session_id=self._session_id,
            config=ExplorationConfig(max_actions=self._max_actions),
            log_callback=self._on_progress,
            screenshot_callback=self._on_screenshot,
        )
        result = agent.explore(self._url)

        self.progress.emit("✅ Exploratory 모드 종료")
        self.progress.emit(f"   - 총 액션: {result.total_actions}")
        self.progress.emit(f"   - 방문 페이지: {result.total_pages_visited}")
        self.progress.emit(f"   - 발견 이슈: {len(result.issues_found)}개")
        self.finished.emit()

    def _on_progress(self, message: str) -> None:
        self.progress.emit(message)

    def _on_screenshot(self, screenshot_base64: str, click_position: dict | None = None) -> None:
        self.screenshot.emit(screenshot_base64, click_position)

    def request_cancel(self) -> None:
        self._cancel_requested = True


__all__ = ["GoalDrivenWorker", "ExploratoryWorker"]
