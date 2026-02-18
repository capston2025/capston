"""
Goal-Driven Agent

목표만 주면 AI가 알아서 DOM을 분석하고 다음 액션을 결정하여 실행
사전 정의된 스텝 없이 동적으로 테스트 수행
"""

from __future__ import annotations
import time
import json
import os
from dataclasses import dataclass
import requests
from typing import Any, Dict, List, Optional, Callable
from urllib.parse import urlparse

from .models import (
    TestGoal,
    ActionDecision,
    ActionType,
    GoalResult,
    StepResult,
    DOMElement,
)
from gaia.src.phase4.memory.models import (
    MemoryActionRecord,
    MemorySummaryRecord,
)
from gaia.src.phase4.memory.retriever import MemoryRetriever
from gaia.src.phase4.memory.store import MemoryStore


@dataclass
class MasterDirective:
    kind: str
    reason: str = ""
    close_element_id: Optional[int] = None


class FlowMasterOrchestrator:
    """
    마스터 오케스트레이터:
    - 실행 루프 예산 관리
    - 로그인 모달 복구/중단 판단
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

        if login_gate_visible and not requires_login_interaction:
            if close_element_id is not None:
                if self.consecutive_auto_recovery >= self._auto_recovery_limit:
                    self.stop_reason = (
                        "로그인 모달 닫기 복구가 반복되어 중단했습니다. "
                        "직접 로그인하거나 목표를 로그인 제외 동선으로 바꿔주세요."
                    )
                    return MasterDirective(kind="stop", reason=self.stop_reason)
                return MasterDirective(
                    kind="recover_login",
                    close_element_id=close_element_id,
                    reason="로그인 모달 자동 복구",
                )

            if not has_login_test_data:
                self.login_gate_llm_loop_count += 1
                if self.login_gate_llm_loop_count >= self._login_gate_loop_limit:
                    self.stop_reason = (
                        "로그인 화면이 반복되지만 닫기 요소를 찾지 못했습니다. "
                        "직접 로그인 후 다시 실행하거나 test_data에 계정을 넣어주세요."
                    )
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


class StepSubAgent:
    """
    스텝 서브에이전트:
    - 마스터가 내린 액션 1건 실행
    - StepResult 생성
    """

    def __init__(self, owner: "GoalDrivenAgent"):
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
    snapshot_id_used: str = ""
    ref_id_used: str = ""

    def as_error_message(self) -> Optional[str]:
        if self.success and self.effective:
            return None
        return f"[{self.reason_code}] {self.reason or 'Unknown error'}"


class GoalDrivenAgent:
    """
    Goal-Driven 테스트 에이전트

    사용법:
        agent = GoalDrivenAgent(mcp_host_url="http://localhost:8000")
        result = agent.execute_goal(goal)
    """

    def __init__(
        self,
        mcp_host_url: str = "http://localhost:8000",
        gemini_api_key: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        session_id: str = "goal_driven",
        log_callback: Optional[Callable[[str], None]] = None,
        screenshot_callback: Optional[Callable[[str], None]] = None,
    ):
        self.mcp_host_url = mcp_host_url
        self.session_id = session_id
        self._log_callback = log_callback
        self._screenshot_callback = screenshot_callback

        # Vision LLM 클라이언트 초기화 (CLI에서 선택한 provider/model 우선)
        provider = (
            os.getenv("GAIA_LLM_PROVIDER")
            or os.getenv("VISION_PROVIDER")
            or "openai"
        ).strip().lower()
        if llm_api_key:
            if provider == "gemini":
                os.environ.setdefault("GEMINI_API_KEY", llm_api_key)
            else:
                os.environ.setdefault("OPENAI_API_KEY", llm_api_key)
        elif gemini_api_key and provider == "gemini":
            os.environ.setdefault("GEMINI_API_KEY", gemini_api_key)

        from gaia.src.phase4.llm_vision_client import get_vision_client
        self.llm = get_vision_client()

        # 실행 기록
        self._action_history: List[str] = []
        self._action_feedback: List[str] = []

        # DOM 요소의 셀렉터 저장 (element_id -> selector)
        self._element_selectors: Dict[int, str] = {}
        self._element_full_selectors: Dict[int, str] = {}
        self._element_ref_ids: Dict[int, str] = {}
        self._element_scopes: Dict[int, Dict[str, Any]] = {}
        self._active_snapshot_id: str = ""
        self._active_dom_hash: str = ""
        self._active_snapshot_epoch: int = 0
        self._last_exec_result: Optional[ActionExecResult] = None

        # 실행 기억(KB)
        self._memory_store = MemoryStore(enabled=True)
        self._memory_retriever = MemoryRetriever(self._memory_store)
        self._memory_episode_id: Optional[int] = None
        self._memory_domain: str = ""

    def _log(self, message: str):
        """로그 출력"""
        print(f"[GoalAgent] {message}")
        if self._log_callback:
            self._log_callback(message)

    @staticmethod
    def _normalize_text(value: Optional[str]) -> str:
        return (value or "").strip().lower()

    @classmethod
    def _contains_login_hint(cls, value: Optional[str]) -> bool:
        text = cls._normalize_text(value)
        if not text:
            return False
        hints = (
            "로그인",
            "sign in",
            "log in",
            "login",
            "이메일",
            "email",
            "비밀번호",
            "password",
            "아이디",
            "username",
            "인증",
            "auth",
        )
        return any(h in text for h in hints)

    @classmethod
    def _contains_close_hint(cls, value: Optional[str]) -> bool:
        text = cls._normalize_text(value)
        if not text:
            return False
        hints = (
            "닫",
            "close",
            "취소",
            "cancel",
            "x",
            "×",
        )
        return any(h in text for h in hints)

    @classmethod
    def _is_login_gate(cls, dom_elements: List[DOMElement]) -> bool:
        score = 0
        for el in dom_elements:
            if cls._contains_login_hint(el.text):
                score += 1
            if cls._contains_login_hint(el.placeholder):
                score += 1
            if cls._contains_login_hint(el.aria_label):
                score += 1
            if cls._contains_login_hint(el.role):
                score += 1
            if cls._normalize_text(el.type) in {"password", "email"}:
                score += 1
            if score >= 3:
                return True
        return False

    @classmethod
    def _goal_requires_login_interaction(cls, goal: TestGoal) -> bool:
        if cls._contains_login_hint(goal.name) or cls._contains_login_hint(goal.description):
            return True
        for criterion in goal.success_criteria:
            if cls._contains_login_hint(str(criterion)):
                return True
        return False

    @classmethod
    def _pick_login_modal_close_element(
        cls,
        dom_elements: List[DOMElement],
        selector_map: Dict[int, str],
    ) -> Optional[int]:
        candidates: List[tuple[int, int]] = []
        for el in dom_elements:
            selector = selector_map.get(el.id, "")
            score = 0

            text_fields = [
                el.text,
                el.aria_label,
                el.placeholder,
                getattr(el, "title", None),
                selector,
            ]
            if any(cls._contains_close_hint(field) for field in text_fields):
                score += 3
            if cls._normalize_text(el.text) in {"x", "×", "닫기", "close"}:
                score += 3
            if cls._normalize_text(el.tag) in {"button", "a"}:
                score += 1
            if cls._normalize_text(el.role) in {"button", "dialogclose"}:
                score += 1

            normalized_selector = cls._normalize_text(selector)
            if any(h in normalized_selector for h in ("close", "cancel", "modal", "dialog", "dismiss")):
                score += 2

            if any(cls._contains_login_hint(field) for field in text_fields):
                score -= 2
            if cls._normalize_text(el.type) == "submit":
                score -= 2

            if score > 0 and el.id in selector_map:
                candidates.append((score, el.id))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _has_login_test_data(goal: TestGoal) -> bool:
        data = goal.test_data or {}
        if not isinstance(data, dict):
            return False
        keys = {str(k).strip().lower() for k in data.keys()}
        has_id = any(k in keys for k in {"email", "id", "username", "login_id", "user"})
        has_pw = any(k in keys for k in {"password", "pw", "passwd"})
        return has_id and has_pw

    @staticmethod
    def _decision_signature(decision: ActionDecision) -> str:
        element = decision.element_id if decision.element_id is not None else -1
        value = (decision.value or "").strip().lower()
        return f"{decision.action.value}:{element}:{value}"

    @classmethod
    def _looks_like_modal_close_loop(cls, decision: ActionDecision) -> bool:
        reason = cls._normalize_text(decision.reasoning)
        close_hints = ("닫", "close", "x 버튼", "모달", "popup", "팝업")
        return decision.action.value in {"click", "wait"} and any(h in reason for h in close_hints)

    @staticmethod
    def _fatal_llm_reason(raw_reason: str) -> Optional[str]:
        text = (raw_reason or "").lower()
        if not text:
            return None
        if "insufficient_quota" in text:
            return (
                "LLM 호출이 중단되었습니다: OpenAI API quota/billing 부족 "
                "(429 insufficient_quota)."
            )
        if "invalid_api_key" in text or "incorrect api key" in text:
            return "LLM 호출이 중단되었습니다: OpenAI API 키가 유효하지 않습니다."
        if "authentication" in text or "unauthorized" in text or "401" in text:
            return "LLM 호출이 중단되었습니다: 인증 오류(401/Unauthorized)."
        if "forbidden" in text or "403" in text:
            return "LLM 호출이 중단되었습니다: 권한 오류(403 Forbidden)."
        if "codex exec failed" in text or "unexpected argument" in text:
            return (
                "LLM 호출이 중단되었습니다: Codex CLI 실행 인자/버전 오류입니다. "
                "`codex exec --help`로 옵션 호환성을 확인하세요."
            )
        return None

    @staticmethod
    def _dom_progress_signature(dom_elements: List[DOMElement]) -> str:
        chunks: List[str] = []
        for el in dom_elements[:25]:
            chunks.append(
                f"{el.tag}|{(el.text or '')[:40]}|{el.role or ''}|{el.type or ''}|{el.aria_label or ''}"
            )
        return f"{len(dom_elements)}#" + "||".join(chunks)

    def _record_action_feedback(
        self,
        *,
        step_number: int,
        decision: ActionDecision,
        success: bool,
        changed: bool,
        error: Optional[str],
        reason_code: Optional[str] = None,
        state_change: Optional[Dict[str, Any]] = None,
    ):
        code = reason_code or (self._last_exec_result.reason_code if self._last_exec_result else "unknown")
        state_info = ""
        if isinstance(state_change, dict) and state_change:
            effective = bool(state_change.get("effective", False))
            state_info = f", effective={effective}"
        feedback = (
            f"Step {step_number}: action={decision.action.value}, "
            f"element_id={decision.element_id}, changed={changed}, success={success}, "
            f"reason_code={code}{state_info}, error={error or 'none'}"
        )
        self._action_feedback.append(feedback)
        if len(self._action_feedback) > 10:
            self._action_feedback = self._action_feedback[-10:]

    @staticmethod
    def _extract_domain(url: Optional[str]) -> str:
        parsed = urlparse(url or "")
        return (parsed.netloc or "").lower()

    def _build_memory_context(self, goal: TestGoal) -> str:
        if not self._memory_store.enabled or not self._memory_domain:
            return ""
        hints = self._memory_retriever.retrieve_lightweight(
            domain=self._memory_domain,
            goal_text=f"{goal.name} {goal.description}",
            action_history=self._action_history[-6:],
        )
        return self._memory_retriever.format_for_prompt(hints)

    def _record_recovery_hints(self, goal: TestGoal, reason_code: str) -> None:
        if not self._memory_store.enabled or not self._memory_domain:
            return
        hints = self._memory_retriever.retrieve_recovery(
            domain=self._memory_domain,
            goal_text=f"{goal.name} {goal.description}",
            reason_code=reason_code,
            limit=3,
        )
        text = self._memory_retriever.format_for_prompt(hints, max_items=3)
        if not text:
            return
        self._action_feedback.append(f"Recovery hints ({reason_code}): {text}")
        if len(self._action_feedback) > 10:
            self._action_feedback = self._action_feedback[-10:]

    def _record_action_memory(
        self,
        *,
        goal: TestGoal,
        step_number: int,
        decision: ActionDecision,
        success: bool,
        changed: bool,
        error: Optional[str],
    ) -> None:
        if not self._memory_store.enabled:
            return
        if self._memory_episode_id is None:
            return
        exec_result = self._last_exec_result or ActionExecResult(
            success=success,
            effective=success,
            reason_code="unknown",
            reason=error or "",
        )
        selector = ""
        full_selector = ""
        ref_id = ""
        frame_index: Optional[int] = None
        tab_index: Optional[int] = None
        if decision.element_id is not None:
            selector = self._element_selectors.get(decision.element_id, "")
            full_selector = self._element_full_selectors.get(decision.element_id, "")
            ref_id = self._element_ref_ids.get(decision.element_id, "")
            scope = self._element_scopes.get(decision.element_id, {})
            if isinstance(scope, dict):
                frame_index = scope.get("frame_index")
                tab_index = scope.get("tab_index")

        try:
            self._memory_store.record_action(
                MemoryActionRecord(
                    episode_id=self._memory_episode_id,
                    domain=self._memory_domain,
                    url=goal.start_url or "",
                    step_number=step_number,
                    action=decision.action.value,
                    selector=selector,
                    full_selector=full_selector,
                    ref_id=ref_id,
                    success=bool(exec_result.success and exec_result.effective),
                    effective=bool(exec_result.effective),
                    changed=bool(changed),
                    reason_code=exec_result.reason_code,
                    reason=exec_result.reason or (error or ""),
                    snapshot_id=exec_result.snapshot_id_used or self._active_snapshot_id,
                    dom_hash=self._active_dom_hash,
                    epoch=self._active_snapshot_epoch,
                    frame_index=frame_index if isinstance(frame_index, int) else None,
                    tab_index=tab_index if isinstance(tab_index, int) else None,
                    state_change=exec_result.state_change or {},
                    attempt_logs=exec_result.attempt_logs or [],
                )
            )
        except Exception:
            return

    def _record_goal_summary(
        self,
        *,
        goal: TestGoal,
        status: str,
        reason: str,
        step_count: int,
        duration_seconds: float,
    ) -> None:
        if not self._memory_store.enabled:
            return
        try:
            self._memory_store.add_dialog_summary(
                MemorySummaryRecord(
                    episode_id=self._memory_episode_id,
                    domain=self._memory_domain,
                    command="/test",
                    summary=(
                        f"goal={goal.name}, status={status}, steps={step_count}, "
                        f"reason={reason}, duration={duration_seconds:.2f}s"
                    ),
                    status=status,
                    metadata={
                        "goal_id": goal.id,
                        "goal_name": goal.name,
                        "steps": step_count,
                        "reason": reason,
                        "duration_seconds": duration_seconds,
                    },
                )
            )
        except Exception:
            return

    @classmethod
    def _goal_text_blob(cls, goal: TestGoal) -> str:
        fields = [goal.name, goal.description]
        fields.extend(str(x) for x in (goal.success_criteria or []))
        return " ".join(cls._normalize_text(x) for x in fields if x)

    @classmethod
    def _goal_mentions_signup(cls, goal: TestGoal) -> bool:
        blob = cls._goal_text_blob(goal)
        signup_keywords = (
            "회원가입",
            "가입",
            "sign up",
            "signup",
            "register",
            "registration",
            "계정 생성",
        )
        return any(k in blob for k in signup_keywords)

    @classmethod
    def _dom_contains_any_hint(cls, dom_elements: List[DOMElement], keywords: tuple[str, ...]) -> bool:
        for el in dom_elements:
            fields = [
                el.text,
                el.placeholder,
                el.aria_label,
                getattr(el, "title", None),
            ]
            for field in fields:
                normalized = cls._normalize_text(field)
                if not normalized:
                    continue
                if any(k in normalized for k in keywords):
                    return True
        return False

    @classmethod
    def _has_signup_completion_evidence(cls, dom_elements: List[DOMElement]) -> bool:
        completion_hints = (
            "회원가입 완료",
            "가입 완료",
            "가입되었습니다",
            "가입이 완료",
            "환영합니다",
            "welcome",
            "로그아웃",
            "마이페이지",
            "프로필",
        )
        if cls._dom_contains_any_hint(dom_elements, completion_hints):
            return True
        return False

    def _validate_goal_achievement_claim(
        self,
        goal: TestGoal,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> tuple[bool, Optional[str]]:
        if not decision.is_goal_achieved:
            return True, None

        if self._goal_mentions_signup(goal):
            if not self._has_signup_completion_evidence(dom_elements):
                return (
                    False,
                    "회원가입 목표는 화면 진입만으로 성공으로 보지 않습니다. "
                    "회원가입 제출 및 완료 신호가 필요합니다.",
                )

        return True, None

    def _build_failure_result(
        self,
        *,
        goal: TestGoal,
        steps: List[StepResult],
        step_count: int,
        start_time: float,
        reason: str,
    ) -> GoalResult:
        self._log(f"❌ {reason}")
        result = GoalResult(
            goal_id=goal.id,
            goal_name=goal.name,
            success=False,
            steps_taken=steps,
            total_steps=step_count,
            final_reason=reason,
            duration_seconds=time.time() - start_time,
        )
        self._record_goal_summary(
            goal=goal,
            status="failed",
            reason=reason,
            step_count=step_count,
            duration_seconds=result.duration_seconds,
        )
        return result

    def execute_goal(self, goal: TestGoal) -> GoalResult:
        """
        목표를 달성할 때까지 실행

        1. DOM 분석
        2. LLM에게 다음 액션 결정 요청
        3. 액션 실행
        4. 목표 달성 여부 확인
        5. 반복
        """
        start_time = time.time()
        self._action_history = []
        self._action_feedback = []
        steps: List[StepResult] = []

        self._log(f"🎯 목표 시작: {goal.name}")
        self._log(f"   설명: {goal.description}")
        self._log(f"   성공 조건: {goal.success_criteria}")

        self._memory_domain = self._extract_domain(goal.start_url)
        self._memory_episode_id = None
        try:
            self._memory_store.garbage_collect(retention_days=30)
            self._memory_episode_id = self._memory_store.start_episode(
                provider=(os.getenv("GAIA_LLM_PROVIDER") or "openai"),
                model=(os.getenv("GAIA_LLM_MODEL") or os.getenv("VISION_MODEL") or "unknown"),
                runtime="terminal",
                domain=self._memory_domain,
                goal_text=f"{goal.name} {goal.description}",
                url=goal.start_url or "",
            )
        except Exception:
            self._memory_episode_id = None

        # 시작 URL로 이동
        current_url = goal.start_url
        if goal.start_url:
            self._log(f"📍 시작 URL로 이동: {goal.start_url}")
            self._execute_action("goto", url=goal.start_url)
            time.sleep(2)  # 페이지 로드 대기

        requires_login_interaction = self._goal_requires_login_interaction(goal)
        has_login_test_data = self._has_login_test_data(goal)
        orchestrator = FlowMasterOrchestrator(goal=goal, max_steps=goal.max_steps)
        sub_agent = StepSubAgent(self)
        ineffective_action_streak = 0

        while orchestrator.can_continue():
            step_count = orchestrator.begin_step()
            step_start = time.time()

            self._log(f"\n--- Step {step_count}/{orchestrator.max_steps} ---")

            # 1. 현재 페이지 DOM 분석
            dom_elements = self._analyze_dom(url=current_url)
            if not dom_elements:
                self._log("⚠️ DOM 요소를 찾을 수 없음, 잠시 대기 후 재시도")
                time.sleep(1)
                dom_elements = self._analyze_dom()
                if not dom_elements:
                    orchestrator.observe_no_dom()
                    if orchestrator.stop_reason:
                        return self._build_failure_result(
                            goal=goal,
                            steps=steps,
                            step_count=step_count,
                            start_time=start_time,
                            reason=orchestrator.stop_reason,
                        )
                    continue

            orchestrator.observe_dom(dom_elements)
            if orchestrator.stop_reason:
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=orchestrator.stop_reason,
                )

            self._log(f"📊 DOM 요소 {len(dom_elements)}개 발견")
            before_signature = self._dom_progress_signature(dom_elements)
            login_gate_visible = self._is_login_gate(dom_elements)
            if login_gate_visible:
                self._log("🔐 로그인/인증 화면이 감지되었습니다.")

            # 2. 스크린샷 캡처
            screenshot = self._capture_screenshot()

            close_element_id: Optional[int] = None
            if login_gate_visible and not requires_login_interaction:
                close_element_id = self._pick_login_modal_close_element(
                    dom_elements,
                    self._element_selectors,
                )

            directive = orchestrator.next_directive(
                login_gate_visible=login_gate_visible,
                requires_login_interaction=requires_login_interaction,
                has_login_test_data=has_login_test_data,
                close_element_id=close_element_id,
            )

            if directive.kind == "stop":
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=directive.reason or "마스터 오케스트레이터가 실행을 중단했습니다.",
                )

            if directive.kind == "recover_login" and directive.close_element_id is not None:
                auto_decision = ActionDecision(
                    action=ActionType.CLICK,
                    element_id=directive.close_element_id,
                    reasoning="로그인 모달 닫기 버튼 자동 감지",
                    confidence=0.95,
                )
                self._log("🧭 자동 복구: 로그인 모달 닫기 버튼을 먼저 클릭합니다.")
                step_result, success, error = sub_agent.run_step(
                    step_number=step_count,
                    step_start=step_start,
                    decision=auto_decision,
                    dom_elements=dom_elements,
                )
                steps.append(step_result)
                if success:
                    self._action_history.append(
                        f"Step {step_count}: {auto_decision.action.value} - {auto_decision.reasoning}"
                    )
                else:
                    self._log(f"⚠️ 자동 복구 실패: {error}")
                post_dom = self._analyze_dom()
                changed = bool(post_dom) and self._dom_progress_signature(post_dom) != before_signature
                self._record_action_feedback(
                    step_number=step_count,
                    decision=auto_decision,
                    success=success,
                    changed=changed,
                    error=error,
                    reason_code=self._last_exec_result.reason_code if self._last_exec_result else None,
                    state_change=self._last_exec_result.state_change if self._last_exec_result else None,
                )
                self._record_action_memory(
                    goal=goal,
                    step_number=step_count,
                    decision=auto_decision,
                    success=success,
                    changed=changed,
                    error=error,
                )
                if not success or not changed:
                    reason_code = self._last_exec_result.reason_code if self._last_exec_result else "unknown"
                    self._record_recovery_hints(goal, reason_code)
                if auto_decision.action in {ActionType.CLICK, ActionType.PRESS} and success and not changed:
                    ineffective_action_streak += 1
                else:
                    ineffective_action_streak = 0
                orchestrator.record_auto_recovery(success=success)
                if orchestrator.stop_reason:
                    return self._build_failure_result(
                        goal=goal,
                        steps=steps,
                        step_count=step_count,
                        start_time=start_time,
                        reason=orchestrator.stop_reason,
                    )
                if ineffective_action_streak >= 4:
                    return self._build_failure_result(
                        goal=goal,
                        steps=steps,
                        step_count=step_count,
                        start_time=start_time,
                        reason=(
                            "명령은 성공으로 반환되지만 화면 변화가 반복적으로 없어 중단했습니다. "
                            "선택자 품질 또는 모달 구조를 확인하세요."
                        ),
                    )
                time.sleep(0.4)
                continue

            # 3. LLM에게 다음 액션 결정 요청
            memory_context = self._build_memory_context(goal)
            decision = self._decide_next_action(
                dom_elements=dom_elements,
                goal=goal,
                screenshot=screenshot,
                memory_context=memory_context,
            )

            self._log(f"🤖 LLM 결정: {decision.action.value} - {decision.reasoning}")

            fatal_reason = self._fatal_llm_reason(decision.reasoning)
            if fatal_reason:
                steps.append(
                    StepResult(
                        step_number=step_count,
                        action=decision,
                        success=False,
                        error_message=fatal_reason,
                        duration_ms=int((time.time() - step_start) * 1000),
                    )
                )
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=fatal_reason,
                )

            # 4. 목표 달성 확인
            if decision.is_goal_achieved:
                is_valid, invalid_reason = self._validate_goal_achievement_claim(
                    goal=goal,
                    decision=decision,
                    dom_elements=dom_elements,
                )
                if not is_valid:
                    self._log(f"⚠️ 목표 달성 판정 보류: {invalid_reason}")
                    decision = ActionDecision(
                        action=decision.action,
                        element_id=decision.element_id,
                        value=decision.value,
                        reasoning=f"{decision.reasoning} | 보류 사유: {invalid_reason}",
                        confidence=max(float(decision.confidence or 0.0) - 0.2, 0.0),
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
                else:
                    self._log(f"✅ 목표 달성! 이유: {decision.goal_achievement_reason}")
                    result = GoalResult(
                        goal_id=goal.id,
                        goal_name=goal.name,
                        success=True,
                        steps_taken=steps,
                        total_steps=step_count,
                        final_reason=decision.goal_achievement_reason or "목표 달성됨",
                        duration_seconds=time.time() - start_time,
                    )
                    self._record_goal_summary(
                        goal=goal,
                        status="success",
                        reason=result.final_reason,
                        step_count=step_count,
                        duration_seconds=result.duration_seconds,
                    )
                    return result

            signature = self._decision_signature(decision)
            orchestrator.record_llm_decision(
                decision_signature=signature,
                looks_like_modal_close_loop=self._looks_like_modal_close_loop(decision),
                login_gate_visible=login_gate_visible,
                has_login_test_data=has_login_test_data,
            )
            if orchestrator.stop_reason:
                steps.append(
                    StepResult(
                        step_number=step_count,
                        action=decision,
                        success=False,
                        error_message=orchestrator.stop_reason,
                        duration_ms=int((time.time() - step_start) * 1000),
                    )
                )
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=orchestrator.stop_reason,
                )

            # 5. 액션 실행
            step_result, success, error = sub_agent.run_step(
                step_number=step_count,
                step_start=step_start,
                decision=decision,
                dom_elements=dom_elements,
            )
            steps.append(step_result)

            if success:
                self._action_history.append(
                    f"Step {step_count}: {decision.action.value} - {decision.reasoning}"
                )
            else:
                self._log(f"⚠️ 액션 실패: {error}")

            post_dom = self._analyze_dom()
            changed = bool(post_dom) and self._dom_progress_signature(post_dom) != before_signature
            self._record_action_feedback(
                step_number=step_count,
                decision=decision,
                success=success,
                changed=changed,
                error=error,
                reason_code=self._last_exec_result.reason_code if self._last_exec_result else None,
                state_change=self._last_exec_result.state_change if self._last_exec_result else None,
            )
            self._record_action_memory(
                goal=goal,
                step_number=step_count,
                decision=decision,
                success=success,
                changed=changed,
                error=error,
            )
            if not success or not changed:
                reason_code = self._last_exec_result.reason_code if self._last_exec_result else "unknown"
                self._record_recovery_hints(goal, reason_code)

            if decision.action in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS, ActionType.NAVIGATE}:
                if success and not changed:
                    ineffective_action_streak += 1
                else:
                    ineffective_action_streak = 0
            else:
                ineffective_action_streak = 0

            if ineffective_action_streak >= 4:
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=(
                        "같은 유형의 무효 액션이 반복되어 중단했습니다. "
                        "LLM 판단은 내려지고 있으나 실제 UI 상태 변화가 없습니다."
                    ),
                )

            # 다음 스텝 전 잠시 대기
            time.sleep(0.5)

        final_reason = (
            orchestrator.stop_reason
            or f"마스터 오케스트레이터 실행 한도 초과 ({orchestrator.max_steps})"
        )
        return self._build_failure_result(
            goal=goal,
            steps=steps,
            step_count=orchestrator.step_count,
            start_time=start_time,
            reason=final_reason,
        )

    def _analyze_dom(self, url: Optional[str] = None) -> List[DOMElement]:
        """MCP Host를 통해 DOM 분석"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "snapshot_page",
                    "params": {
                        "session_id": self.session_id,
                        "url": url or "",
                    },
                },
                timeout=30,
            )
            try:
                data = response.json()
            except Exception:
                data = {"error": response.text or "invalid_json_response"}

            if response.status_code >= 400:
                detail = data.get("detail") or data.get("error") or response.reason
                self._log(f"DOM 분석 오류: HTTP {response.status_code} - {detail}")
                return []

            # analyze_page는 success 필드 없이 elements를 직접 반환
            if "error" in data:
                self._log(f"DOM 분석 오류: {data['error']}")
                return []

            raw_elements = data.get("elements", []) or data.get("dom_elements", [])

            # 셀렉터 맵 초기화
            self._element_selectors = {}
            self._element_full_selectors = {}
            self._element_ref_ids = {}
            self._element_scopes = {}
            self._active_snapshot_id = str(data.get("snapshot_id") or "")
            self._active_dom_hash = str(data.get("dom_hash") or "")
            self._active_snapshot_epoch = int(data.get("epoch") or 0)

            # DOMElement로 변환 (ID 부여)
            elements = []
            for idx, el in enumerate(raw_elements):
                attrs = el.get("attributes", {})

                # 셀렉터 저장
                selector = el.get("selector", "")
                full_selector = el.get("full_selector") or selector
                ref_id = el.get("ref_id", "")
                scope = el.get("scope")
                if selector:
                    self._element_selectors[idx] = selector
                if full_selector:
                    self._element_full_selectors[idx] = full_selector
                if isinstance(ref_id, str) and ref_id:
                    self._element_ref_ids[idx] = ref_id
                if isinstance(scope, dict):
                    self._element_scopes[idx] = scope

                elements.append(
                    DOMElement(
                        id=idx,
                        tag=el.get("tag", ""),
                        text=el.get("text", "")[:100],  # 텍스트 길이 제한
                        role=attrs.get("role"),
                        type=attrs.get("type"),
                        placeholder=attrs.get("placeholder"),
                        aria_label=attrs.get("aria-label"),
                        title=attrs.get("title"),
                        href=attrs.get("href"),
                        bounding_box=el.get("bounding_box"),
                    )
                )

            return elements

        except Exception as e:
            self._log(f"DOM 분석 실패: {e}")
            return []

    def _capture_screenshot(self) -> Optional[str]:
        """스크린샷 캡처"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "capture_screenshot",
                    "params": {
                        "session_id": self.session_id,
                    },
                },
                timeout=30,
            )
            try:
                data = response.json()
            except Exception:
                data = {"error": response.text or "invalid_json_response"}
            if response.status_code >= 400:
                detail = data.get("detail") or data.get("error") or response.reason
                self._log(f"스크린샷 캡처 오류: HTTP {response.status_code} - {detail}")
                return None
            screenshot = data.get("screenshot")

            if screenshot and self._screenshot_callback:
                self._screenshot_callback(screenshot)

            return screenshot

        except Exception as e:
            self._log(f"스크린샷 캡처 실패: {e}")
            return None

    def _decide_next_action(
        self,
        dom_elements: List[DOMElement],
        goal: TestGoal,
        screenshot: Optional[str] = None,
        memory_context: str = "",
    ) -> ActionDecision:
        """LLM에게 다음 액션 결정 요청"""

        # DOM 요소를 LLM이 이해하기 쉬운 형태로 변환
        elements_text = self._format_dom_for_llm(dom_elements)
        signup_rule = ""
        if self._goal_mentions_signup(goal):
            signup_rule = """
5. **회원가입 목표 특별 규칙(강제)**
   - 회원가입 화면/모달 진입만으로는 절대 성공이 아닙니다.
   - 입력값 채움 + 제출 버튼 클릭 + 완료 신호(완료 문구/로그인 상태 변화) 확인 전까지 is_goal_achieved=false를 유지하세요.
"""

        # 프롬프트 구성
        prompt = f"""당신은 웹 테스트 자동화 에이전트입니다.
현재 화면의 DOM 요소와 목표를 분석하고, 다음에 수행할 액션을 결정하세요.

## 목표
- 이름: {goal.name}
- 설명: {goal.description}
- 우선순위: {getattr(goal, "priority", "MAY")}
- 성공 조건: {', '.join(goal.success_criteria)}
- 실패 조건: {', '.join(goal.failure_criteria) if goal.failure_criteria else '없음'}
 - 키워드: {', '.join(getattr(goal, "keywords", []) or []) if getattr(goal, "keywords", None) else '없음'}

## 사용 가능한 테스트 데이터
{json.dumps(goal.test_data, ensure_ascii=False, indent=2)}

## 지금까지 수행한 액션
{chr(10).join(self._action_history[-5:]) if self._action_history else '없음 (첫 번째 스텝)'}

## 최근 액션 실행 피드백
{chr(10).join(self._action_feedback[-5:]) if self._action_feedback else '없음'}

## 도메인 실행 기억(KB)
{memory_context or '없음'}

## 현재 화면의 DOM 요소 (클릭/입력 가능한 요소들)
{elements_text}

## 중요 지시사항
0. **키워드 우선 탐색**: 키워드와 관련된 요소를 먼저 찾아서 목표 달성에 활용하세요.
1. **탭/섹션 UI 확인**: role="tab"인 요소가 있으면 먼저 해당 탭을 클릭해야 합니다!
   - 예: 로그인 탭, 회원가입 탭이 있으면 → 먼저 로그인 탭 클릭 → 그 다음 폼 입력

2. **입력 전 활성화 확인**: 입력 필드가 비활성 상태일 수 있으므로 탭/버튼을 먼저 클릭

3. **목표 달성 여부 확인**
   - 성공 조건에 해당하는 요소가 보이면 is_goal_achieved: true

4. **중간 단계 파악**: 기획서에 없는 단계도 스스로 파악하세요
   - 예: "로그인" 목표 → (1)로그인 탭 클릭 → (2)이메일 입력 → (3)비밀번호 입력 → (4)제출 버튼 클릭
{signup_rule}
6. **무효 액션 반복 금지**
   - 최근 실행 피드백에서 changed=false 또는 success=false인 액션/요소 조합은 반복하지 마세요.
   - 같은 요소를 2회 연속 클릭했는데 changed=false라면 다른 요소/전략을 선택하세요.

## 응답 형식 (JSON만, 마크다운 없이)
{{
    "action": "click" | "fill" | "press" | "scroll" | "wait",
    "element_id": 요소ID (숫자),
    "value": "입력값 (fill인 경우) 또는 키 이름 (press인 경우, 예: Enter)",
    "reasoning": "이 액션을 선택한 이유",
    "confidence": 0.0~1.0,
    "is_goal_achieved": true | false,
    "goal_achievement_reason": "목표 달성 판단 이유 (is_goal_achieved가 true인 경우)"
}}

JSON 응답:"""

        try:
            # Gemini API 호출
            if screenshot:
                response_text = self.llm.analyze_with_vision(prompt, screenshot)
            else:
                # 스크린샷 없이 텍스트만으로 분석 (fallback)
                response_text = self._call_llm_text_only(prompt)

            # JSON 파싱
            return self._parse_decision(response_text)

        except Exception as e:
            self._log(f"LLM 결정 실패: {e}")
            # 기본 액션 반환 (대기)
            return ActionDecision(
                action=ActionType.WAIT,
                reasoning=f"LLM 오류: {e}",
                confidence=0.0,
            )

    def _format_dom_for_llm(self, elements: List[DOMElement]) -> str:
        """DOM 요소를 LLM이 이해하기 쉬운 텍스트로 변환"""
        lines = []
        for el in elements[:50]:  # 최대 50개로 제한
            parts = [f"[{el.id}] <{el.tag}>"]

            if el.text:
                parts.append(f'"{el.text}"')
            if el.role:
                parts.append(f"role={el.role}")
            if el.type and el.type != "button":
                parts.append(f"type={el.type}")
            if el.placeholder:
                parts.append(f'placeholder="{el.placeholder}"')
            if el.aria_label:
                parts.append(f'aria-label="{el.aria_label}"')

            lines.append(" ".join(parts))

        return "\n".join(lines)

    def _parse_decision(self, response_text: str) -> ActionDecision:
        """LLM 응답을 ActionDecision으로 파싱"""
        # 마크다운 코드 블록 제거
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)

            return ActionDecision(
                action=ActionType(data.get("action", "wait")),
                element_id=data.get("element_id"),
                value=data.get("value"),
                reasoning=data.get("reasoning", ""),
                confidence=data.get("confidence", 0.5),
                is_goal_achieved=data.get("is_goal_achieved", False),
                goal_achievement_reason=data.get("goal_achievement_reason"),
            )

        except (json.JSONDecodeError, ValueError) as e:
            self._log(f"JSON 파싱 실패: {e}, 응답: {text[:200]}")
            return ActionDecision(
                action=ActionType.WAIT,
                reasoning=f"파싱 오류: {e}",
                confidence=0.0,
            )

    def _execute_decision(
        self,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> tuple[bool, Optional[str]]:
        """결정된 액션 실행"""

        self._last_exec_result = None

        # 요소 ID로 셀렉터 찾기
        selector = None
        full_selector = None
        ref_id = None
        if decision.element_id is not None:
            selector = self._element_selectors.get(decision.element_id)
            full_selector = self._element_full_selectors.get(decision.element_id)
            ref_id = self._element_ref_ids.get(decision.element_id)
            if not selector and not full_selector and not ref_id:
                self._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="not_found",
                    reason=f"요소 ID {decision.element_id}에 대한 ref/selector를 찾을 수 없음",
                )
                return False, f"요소 ID {decision.element_id}에 대한 ref/selector를 찾을 수 없음"

        try:
            if decision.action in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS, ActionType.HOVER} and decision.element_id is None:
                self._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="missing_element_id",
                    reason=f"{decision.action.value} 액션에는 element_id가 필요함",
                )
                return False, f"{decision.action.value} 액션에는 element_id가 필요함"

            if decision.action == ActionType.CLICK:
                self._last_exec_result = self._execute_action(
                    "click",
                    selector=selector,
                    full_selector=full_selector,
                    ref_id=ref_id,
                )
                return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

            elif decision.action == ActionType.FILL:
                if not decision.value:
                    self._last_exec_result = ActionExecResult(
                        success=False,
                        effective=False,
                        reason_code="invalid_input",
                        reason="fill 액션에 value가 필요함",
                    )
                    return False, "fill 액션에 value가 필요함"
                self._last_exec_result = self._execute_action(
                    "fill",
                    selector=selector,
                    full_selector=full_selector,
                    ref_id=ref_id,
                    value=decision.value,
                )
                return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

            elif decision.action == ActionType.PRESS:
                # press 액션은 키보드 입력 (Enter, Tab 등)
                key = decision.value or "Enter"
                self._last_exec_result = self._execute_action(
                    "press",
                    selector=selector or "",
                    full_selector=full_selector,
                    ref_id=ref_id,
                    value=key,
                )
                return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

            elif decision.action == ActionType.SCROLL:
                self._last_exec_result = self._execute_action("scroll", value="down")
                return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

            elif decision.action == ActionType.WAIT:
                time.sleep(1)
                self._last_exec_result = ActionExecResult(
                    success=True,
                    effective=True,
                    reason_code="wait",
                    reason="wait",
                )
                return True, None

            elif decision.action == ActionType.NAVIGATE:
                self._last_exec_result = self._execute_action("goto", url=decision.value)
                return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

            elif decision.action == ActionType.HOVER:
                self._last_exec_result = self._execute_action(
                    "hover",
                    selector=selector,
                    full_selector=full_selector,
                    ref_id=ref_id,
                )
                return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

            else:
                self._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="unsupported_action",
                    reason=f"지원하지 않는 액션: {decision.action}",
                )
                return False, f"지원하지 않는 액션: {decision.action}"

        except Exception as e:
            self._last_exec_result = ActionExecResult(
                success=False,
                effective=False,
                reason_code="exception",
                reason=str(e),
            )
            return False, str(e)

    def _execute_action(
        self,
        action: str,
        selector: Optional[str] = None,
        full_selector: Optional[str] = None,
        ref_id: Optional[str] = None,
        value: Optional[str] = None,
        url: Optional[str] = None,
    ) -> ActionExecResult:
        """MCP Host를 통해 액션 실행"""

        use_ref_protocol = bool(
            ref_id
            and self._active_snapshot_id
            and action in {"click", "fill", "press", "hover"}
        )
        if use_ref_protocol:
            params = {
                "session_id": self.session_id,
                "snapshot_id": self._active_snapshot_id,
                "ref_id": ref_id,
                "action": action,
                "url": url or "",
                "verify": True,
                "selector_hint": full_selector or selector or "",
            }
            if value is not None:
                params["value"] = value
            request_action = "execute_ref_action"
        else:
            params = {
                "session_id": self.session_id,
                "action": action,
                "url": url or "",
                "selector": full_selector or selector or "",
            }
            if value is not None:
                params["value"] = value
            request_action = "execute_action"

        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": request_action,
                    "params": params,
                },
                timeout=60,
            )
            try:
                data = response.json()
            except Exception:
                data = {"error": response.text or "invalid_json_response"}

            if response.status_code >= 400:
                detail = str(data.get("detail") or data.get("error") or response.reason or "HTTP error")
                return ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code=f"http_{response.status_code}",
                    reason=detail,
                    state_change={},
                    attempt_logs=[],
                    snapshot_id_used=str(data.get("snapshot_id_used") or ""),
                    ref_id_used=str(data.get("ref_id_used") or ""),
                )

            is_success = bool(data.get("success"))
            is_effective = bool(data.get("effective", True))
            if is_success and is_effective:
                return ActionExecResult(
                    success=True,
                    effective=True,
                    reason_code="ok",
                    reason="ok",
                    state_change=data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
                    attempt_logs=data.get("attempt_logs") if isinstance(data.get("attempt_logs"), list) else [],
                    snapshot_id_used=str(data.get("snapshot_id_used") or ""),
                    ref_id_used=str(data.get("ref_id_used") or ""),
                )

            reason_code = str(data.get("reason_code") or data.get("error") or "unknown_error")
            reason = str(data.get("reason") or data.get("message") or data.get("detail") or "Unknown error")
            attempt_logs = data.get("attempt_logs")
            if isinstance(attempt_logs, list) and attempt_logs:
                reason = f"{reason} (attempts={len(attempt_logs)})"
            return ActionExecResult(
                success=is_success,
                effective=is_effective,
                reason_code=reason_code,
                reason=reason,
                state_change=data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
                attempt_logs=attempt_logs if isinstance(attempt_logs, list) else [],
                snapshot_id_used=str(data.get("snapshot_id_used") or ""),
                ref_id_used=str(data.get("ref_id_used") or ""),
            )

        except Exception as e:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="request_exception",
                reason=str(e),
            )

    def _call_llm_text_only(self, prompt: str) -> str:
        """스크린샷 없이 텍스트만으로 LLM 호출 (provider 자동 선택)"""
        if hasattr(self.llm, "analyze_text"):
            return str(self.llm.analyze_text(prompt, max_completion_tokens=4096, temperature=0.1))

        # Gemini-style client
        if hasattr(self.llm, "client") and hasattr(getattr(self.llm, "client"), "models"):
            try:
                from google.genai import types

                response = self.llm.client.models.generate_content(
                    model=self.llm.model,
                    contents=[types.Content(parts=[types.Part(text=prompt)])],
                    config=types.GenerateContentConfig(
                        max_output_tokens=4096,
                        temperature=0.1,
                    ),
                )
                text = getattr(response, "text", None)
                if isinstance(text, str):
                    return text
            except Exception:
                pass

        # OpenAI-style client
        response = self.llm.client.chat.completions.create(
            model=self.llm.model,
            max_completion_tokens=4096,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content if response.choices else ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
                    continue
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
            return "\n".join(chunks).strip()
        return str(content or "")
