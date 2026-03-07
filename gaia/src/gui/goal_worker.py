"""Qt workers for goal-driven and exploratory automation."""
from __future__ import annotations

import time
from typing import Any, Callable, Optional, Sequence

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


def _goal_step_timeline(result: Any, goal_name: str) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for step in list(getattr(result, "steps_taken", []) or []):
        action = getattr(getattr(step, "action", None), "action", None)
        reasoning = getattr(getattr(step, "action", None), "reasoning", "") or ""
        action_value = getattr(action, "value", None) if action is not None else None
        try:
            duration_seconds = round(float(getattr(step, "duration_ms", 0) or 0) / 1000.0, 2)
        except Exception:
            duration_seconds = 0.0
        timeline.append(
            {
                "goal": goal_name,
                "step": getattr(step, "step_number", None),
                "action": action_value or str(action or "-"),
                "duration_seconds": duration_seconds,
                "reasoning": str(reasoning).strip(),
                "success": bool(getattr(step, "success", False)),
                "error": str(getattr(step, "error_message", "") or "").strip(),
            }
        )
    return timeline


def _exploration_step_timeline(result: Any) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for step in list(getattr(result, "steps", []) or []):
        decision = getattr(step, "decision", None)
        selected = getattr(decision, "selected_action", None)
        try:
            duration_seconds = round(float(getattr(step, "duration_ms", 0) or 0) / 1000.0, 2)
        except Exception:
            duration_seconds = 0.0
        timeline.append(
            {
                "step": getattr(step, "step_number", None),
                "action": str(getattr(selected, "action_type", "") or "-").strip() or "-",
                "duration_seconds": duration_seconds,
                "reasoning": str(
                    getattr(decision, "reasoning", "")
                    or getattr(step, "feature_description", "")
                    or getattr(step, "test_scenario", "")
                    or ""
                ).strip(),
                "success": bool(getattr(step, "success", False)),
                "error": str(getattr(step, "error_message", "") or "").strip(),
            }
        )
    return timeline


class GoalDrivenWorker(QObject):
    """Run goal-driven automation in a background thread."""

    progress = Signal(str)
    screenshot = Signal(str, object)  # (base64, click_position dict 또는 None)
    scenario_started = Signal(str)
    scenario_finished = Signal(str)
    result_ready = Signal(object)
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
        intervention_callback: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None,
    ) -> None:
        super().__init__()
        self._base_url = url
        self._goals = sort_goals_by_priority(list(goals))
        self._tracker = tracker
        self._fallback_actions = max(0, int(fallback_actions))
        self._cancel_requested = False
        self._session_id = session_id or f"goal_ui_{int(time.time())}"
        self._mcp_host_url = mcp_host_url or CONFIG.mcp.host_url
        self._intervention_callback = intervention_callback

        self._goal_agent = GoalDrivenAgent(
            mcp_host_url=self._mcp_host_url,
            session_id=self._session_id,
            log_callback=self._on_progress,
            screenshot_callback=self._on_screenshot,
            intervention_callback=self._intervention_callback,
        )

    def start(self) -> None:
        successful_goals = 0
        failed_goals = 0
        last_reason = ""
        goal_summaries: list[dict[str, Any]] = []
        timeline_rows: list[dict[str, Any]] = []
        try:
            if not self._goals:
                self.progress.emit("ℹ️ 실행할 목표가 없습니다.")
                self.result_ready.emit(
                    {
                        "mode": "goal",
                        "status": "skipped",
                        "reason": "실행할 목표가 없습니다.",
                        "total_goals": 0,
                        "successful_goals": 0,
                        "failed_goals": 0,
                        "goals": [],
                    }
                )
                return

            self.progress.emit(f"🎯 Goal-Driven 자동화를 시작합니다 ({len(self._goals)}개 목표)")

            for index, goal in enumerate(self._goals, start=1):
                if self._cancel_requested:
                    self.progress.emit("⏹️ Goal-Driven 실행이 취소되었습니다.")
                    last_reason = "사용자 요청으로 실행이 취소되었습니다."
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
                        non_stop_mode=True,
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

                last_reason = result.final_reason or last_reason
                status = "success" if result.success else "failed"
                if self._tracker:
                    self._tracker.set_status(goal_to_run.id, status, evidence=result.final_reason)

                if result.success:
                    successful_goals += 1
                    self.progress.emit(f"   ✅ 목표 달성: {goal_to_run.name}")
                else:
                    failed_goals += 1
                    self.progress.emit(f"   ❌ 목표 실패: {goal_to_run.name}")
                    self.progress.emit(f"      이유: {result.final_reason}")

                goal_timeline = _goal_step_timeline(result, goal_to_run.name)
                goal_summaries.append(
                    {
                        "id": goal_to_run.id,
                        "name": goal_to_run.name,
                        "status": status,
                        "reason": result.final_reason,
                        "steps": int(result.total_steps or 0),
                        "step_timeline": goal_timeline,
                    }
                )
                timeline_rows.extend(goal_timeline)
                self.scenario_finished.emit(goal_to_run.id)

            summary_status = "success"
            if self._cancel_requested:
                summary_status = "cancelled"
            elif failed_goals > 0:
                summary_status = "failed"

            self.result_ready.emit(
                {
                    "mode": "goal",
                    "status": summary_status,
                    "reason": last_reason or ("모든 목표가 완료되었습니다." if summary_status == "success" else "일부 목표가 실패했습니다."),
                    "total_goals": len(self._goals),
                    "successful_goals": successful_goals,
                    "failed_goals": failed_goals,
                    "goals": goal_summaries,
                    "current_goal": goal_summaries[-1]["name"] if goal_summaries else "",
                    "current_step": f"{sum(int(row.get('steps') or 0) for row in goal_summaries)}단계 완료" if goal_summaries else "-",
                    "blocked_reason": "",
                    "step_timeline": timeline_rows[:20],
                    "proof_lines": [
                        f"{row.get('name')}: {row.get('reason')}"
                        for row in goal_summaries[-5:]
                        if isinstance(row, dict)
                    ],
                }
            )
        except Exception as exc:
            self.progress.emit(f"❌ Goal-Driven 실행 중 오류: {exc}")
            self.result_ready.emit(
                {
                    "mode": "goal",
                    "status": "failed",
                    "reason": str(exc),
                    "total_goals": len(self._goals),
                    "successful_goals": successful_goals,
                    "failed_goals": max(1, failed_goals),
                    "goals": goal_summaries,
                    "current_goal": goal_summaries[-1]["name"] if goal_summaries else "",
                    "current_step": "-",
                    "blocked_reason": "",
                    "step_timeline": timeline_rows[:20],
                    "proof_lines": [
                        f"{row.get('name')}: {row.get('reason')}"
                        for row in goal_summaries[-5:]
                        if isinstance(row, dict)
                    ],
                }
            )
        finally:
            self.finished.emit()

    def _on_progress(self, message: str) -> None:
        self.progress.emit(message)

    def _on_exploration_progress(self, message: str) -> None:
        self.progress.emit(f"[탐색] {message}")

    def _on_screenshot(self, screenshot_base64: str, click_position: dict | None = None) -> None:
        self.screenshot.emit(screenshot_base64, click_position)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def apply_steering_policy(self, policy: dict[str, Any]) -> None:
        if not isinstance(policy, dict) or not policy:
            return
        try:
            ttl = int(policy.get("ttl_remaining") or policy.get("ttl_steps") or 0)
        except Exception:
            ttl = 0
        self._goal_agent._steering_policy = dict(policy)
        self._goal_agent._steering_remaining_steps = max(0, ttl)
        self._goal_agent._steering_infeasible_block = False


class ExploratoryWorker(QObject):
    """Run exploratory automation in a background thread."""

    progress = Signal(str)
    screenshot = Signal(str, object)
    result_ready = Signal(object)
    finished = Signal()

    def __init__(
        self,
        url: str,
        *,
        max_actions: int = 50,
        session_id: str | None = None,
        mcp_host_url: str | None = None,
        user_intervention_callback: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        super().__init__()
        self._url = url
        self._max_actions = max(1, int(max_actions))
        self._cancel_requested = False
        self._session_id = session_id or f"explore_ui_{int(time.time())}"
        self._mcp_host_url = mcp_host_url or CONFIG.mcp.host_url
        self._user_intervention_callback = user_intervention_callback

    def start(self) -> None:
        try:
            if self._cancel_requested:
                self.progress.emit("⏹️ Exploratory 실행이 취소되었습니다.")
                self.result_ready.emit(
                    {
                        "mode": "exploratory",
                        "status": "cancelled",
                        "reason": "사용자 요청으로 실행이 취소되었습니다.",
                        "total_actions": 0,
                        "pages": 0,
                        "issues": 0,
                        "current_goal": "완전 자율 탐색",
                        "current_step": "-",
                        "blocked_reason": "",
                        "step_timeline": [],
                        "proof_lines": [],
                    }
                )
                return
            self.progress.emit(f"🔍 Exploratory 모드를 시작합니다 (최대 {self._max_actions} 액션)")
            agent = ExploratoryAgent(
                mcp_host_url=self._mcp_host_url,
                session_id=self._session_id,
                config=ExplorationConfig(
                    max_actions=self._max_actions,
                    non_stop_mode=True,
                ),
                log_callback=self._on_progress,
                screenshot_callback=self._on_screenshot,
                user_intervention_callback=self._user_intervention_callback,
            )
            result = agent.explore(self._url)

            self.progress.emit("✅ Exploratory 모드 종료")
            self.progress.emit(f"   - 총 액션: {result.total_actions}")
            self.progress.emit(f"   - 방문 페이지: {result.total_pages_visited}")
            self.progress.emit(f"   - 발견 이슈: {len(result.issues_found)}개")
            self.result_ready.emit(
                {
                    "mode": "exploratory",
                    "status": "success",
                    "reason": "자율 탐색이 완료되었습니다.",
                    "total_actions": int(result.total_actions or 0),
                    "pages": int(result.total_pages_visited or 0),
                    "issues": len(result.issues_found),
                    "current_goal": "완전 자율 탐색",
                    "current_step": f"{int(result.total_actions or 0)}액션 완료",
                    "blocked_reason": "",
                    "step_timeline": _exploration_step_timeline(result)[:20],
                    "proof_lines": [
                        f"방문 페이지 {int(result.total_pages_visited or 0)}개",
                        f"테스트 요소 {int(result.total_elements_tested or 0)}개",
                        f"발견 이슈 {len(result.issues_found)}개",
                    ],
                    "validation_summary": dict(getattr(result, "validation_summary", {}) or {}),
                }
            )
        except Exception as exc:
            self.progress.emit(f"❌ Exploratory 실행 중 오류: {exc}")
            self.result_ready.emit(
                {
                    "mode": "exploratory",
                    "status": "failed",
                    "reason": str(exc),
                    "total_actions": 0,
                    "pages": 0,
                    "issues": 0,
                    "current_goal": "완전 자율 탐색",
                    "current_step": "-",
                    "blocked_reason": "",
                    "step_timeline": [],
                    "proof_lines": [],
                }
            )
        finally:
            self.finished.emit()

    def _on_progress(self, message: str) -> None:
        self.progress.emit(message)

    def _on_screenshot(self, screenshot_base64: str, click_position: dict | None = None) -> None:
        self.screenshot.emit(screenshot_base64, click_position)

    def request_cancel(self) -> None:
        self._cancel_requested = True


__all__ = ["GoalDrivenWorker", "ExploratoryWorker"]
