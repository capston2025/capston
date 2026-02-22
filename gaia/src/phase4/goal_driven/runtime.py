"""
Goal-driven runtime orchestration primitives.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Protocol, Tuple

from .models import TestGoal, ActionDecision, StepResult, DOMElement


@dataclass
class MasterDirective:
    kind: str
    reason: str = ""
    close_element_id: Optional[int] = None


class FlowMasterOrchestrator:
    """
    마스터 오케스트레이터:
    - 실행 루프 예산 관리
    - 반복 액션/반복 화면 중단 판단
    - 반복 액션/반복 화면 감지
    """

    def __init__(self, goal: TestGoal, max_steps: int):
        self.goal = goal
        try:
            parsed_max_steps = int(max_steps or 0)
        except Exception:
            parsed_max_steps = 0

        # 기존 20 고정 체감 완화를 위해 최소 예산을 상향
        self.max_steps = max(parsed_max_steps, 40)
        self.step_count = 0
        self.stop_reason: Optional[str] = None

        self.last_decision_signature: Optional[str] = None
        self.same_decision_count = 0
        self.last_dom_signature: Optional[str] = None
        self.same_dom_count = 0
        self.no_dom_count = 0

        self.login_gate_llm_loop_count = 0
        self.consecutive_auto_recovery = 0
        self.auto_recovery_fail_count = 0

        self._same_decision_limit = 5
        self._same_dom_limit = 10
        self._no_dom_limit = 3
        self._login_gate_loop_limit = 3
        self._auto_recovery_limit = 4
        self._auto_recovery_fail_limit = 2

    def can_continue(self) -> bool:
        return self.stop_reason is None and self.step_count < self.max_steps

    def begin_step(self) -> int:
        self.step_count += 1
        return self.step_count

    def observe_no_dom(self):
        self.no_dom_count += 1
        if self.no_dom_count >= self._no_dom_limit and not self.stop_reason:
            self.stop_reason = (
                "DOM 요소를 반복적으로 읽지 못해 실행을 중단했습니다. "
                "페이지 로딩 상태나 MCP host 연결을 확인하세요."
            )

    def observe_dom(self, dom_elements: List[DOMElement]):
        self.no_dom_count = 0

        signature_parts: List[str] = []
        for el in dom_elements[:15]:
            signature_parts.append(
                f"{el.tag}:{(el.text or '')[:24]}:{el.role or ''}:{el.type or ''}"
            )
        dom_signature = "|".join(signature_parts)

        if dom_signature == self.last_dom_signature:
            self.same_dom_count += 1
        else:
            self.last_dom_signature = dom_signature
            self.same_dom_count = 1

        if self.same_dom_count >= self._same_dom_limit and not self.stop_reason:
            self.stop_reason = (
                "화면 상태가 반복되어 더 이상 진행이 어렵습니다. "
                "현재 페이지에서 수동 전환 후 다시 시도하세요."
            )

    def next_directive(
        self,
        *,
        login_gate_visible: bool,
        requires_login_interaction: bool,
        has_login_test_data: bool,
        close_element_id: Optional[int],
    ) -> MasterDirective:
        if self.stop_reason:
            return MasterDirective(kind="stop", reason=self.stop_reason)

        return MasterDirective(kind="run_llm")

    def record_auto_recovery(self, success: bool):
        self.consecutive_auto_recovery += 1
        if success:
            self.auto_recovery_fail_count = 0
        else:
            self.auto_recovery_fail_count += 1

        if (
            self.auto_recovery_fail_count >= self._auto_recovery_fail_limit
            and not self.stop_reason
        ):
            self.stop_reason = (
                "로그인 모달 자동 복구가 연속 실패하여 중단했습니다. "
                "모달 구조를 확인하거나 수동으로 화면을 정리해 주세요."
            )

    def record_llm_decision(
        self,
        *,
        decision_signature: str,
        looks_like_modal_close_loop: bool,
        login_gate_visible: bool,
        has_login_test_data: bool,
    ):
        if decision_signature == self.last_decision_signature:
            self.same_decision_count += 1
        else:
            self.last_decision_signature = decision_signature
            self.same_decision_count = 1

        if self.same_decision_count >= self._same_decision_limit and not self.stop_reason:
            self.stop_reason = (
                "동일 액션이 반복되어 실행을 중단했습니다. "
                "목표를 더 구체적으로 입력하거나 /url 후 다시 시도하세요."
            )

        if login_gate_visible and not has_login_test_data and looks_like_modal_close_loop:
            self.login_gate_llm_loop_count += 1
        else:
            self.login_gate_llm_loop_count = 0

        if self.login_gate_llm_loop_count >= self._login_gate_loop_limit and not self.stop_reason:
            self.stop_reason = (
                "로그인 모달 반복으로 목표를 진행할 수 없어 중단했습니다. "
                "먼저 로그인 후 다시 실행하거나, test_data에 로그인 계정을 넣어주세요."
            )

        if not login_gate_visible:
            self.consecutive_auto_recovery = 0
            self.auto_recovery_fail_count = 0


class StepRunner(Protocol):
    def _execute_decision(
        self,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> Tuple[bool, Optional[str]]:
        ...


class StepSubAgent:
    """
    스텝 서브에이전트:
    - 마스터가 내린 액션 1건 실행
    - StepResult 생성
    """

    def __init__(self, owner: StepRunner):
        self.owner = owner

    def run_step(
        self,
        *,
        step_number: int,
        step_start: float,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> tuple[StepResult, bool, Optional[str]]:
        success, error = self.owner._execute_decision(decision, dom_elements)
        step_result = StepResult(
            step_number=step_number,
            action=decision,
            success=success,
            error_message=error,
            duration_ms=int((time.time() - step_start) * 1000),
        )
        return step_result, success, error


@dataclass(slots=True)
class ActionExecResult:
    success: bool
    effective: bool = True
    reason_code: str = "ok"
    reason: str = ""
    state_change: Dict[str, Any] | None = None
    attempt_logs: List[Dict[str, Any]] | None = None
    retry_path: List[str] | None = None
    attempt_count: int = 0
    snapshot_id_used: str = ""
    ref_id_used: str = ""

    def as_error_message(self) -> Optional[str]:
        if self.success and self.effective:
            return None
        return f"[{self.reason_code}] {self.reason or 'Unknown error'}"

