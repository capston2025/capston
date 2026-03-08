"""
Goal-Driven Agent

목표만 주면 AI가 알아서 DOM을 분석하고 다음 액션을 결정하여 실행
사전 정의된 스텝 없이 동적으로 테스트 수행
"""

from __future__ import annotations
import time
import json
import os
import re
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
from .constraints import (
    derive_goal_constraints as derive_goal_constraints_impl,
    extract_metric_values_from_text as extract_metric_values_from_text_impl,
    estimate_goal_metric_from_dom as estimate_goal_metric_from_dom_impl,
)
from .auth_hints import (
    contains_close_hint as contains_close_hint_impl,
    contains_login_hint as contains_login_hint_impl,
    contains_next_pagination_hint as contains_next_pagination_hint_impl,
    goal_requires_login_interaction as goal_requires_login_interaction_impl,
    infer_runtime_phase as infer_runtime_phase_impl,
    is_compact_auth_page as is_compact_auth_page_impl,
    is_login_gate as is_login_gate_impl,
    is_navigational_href as is_navigational_href_impl,
    is_numeric_page_label as is_numeric_page_label_impl,
    recover_dom_after_empty as recover_dom_after_empty_impl,
)
from .account_signals import (
    contains_duplicate_account_hint as contains_duplicate_account_hint_impl,
    contains_logout_hint as contains_logout_hint_impl,
    goal_allows_logout as goal_allows_logout_impl,
    has_duplicate_account_signal as has_duplicate_account_signal_impl,
    next_username as next_username_impl,
    rotate_signup_identity as rotate_signup_identity_impl,
)
from .phase_constraints import (
    apply_phase_constraints as apply_phase_constraints_impl,
    build_constraint_failure_reason as build_constraint_failure_reason_impl,
    is_collect_constraint_unmet as is_collect_constraint_unmet_impl,
)
from .execute_goal_context_shift import handle_forced_context_shift
from .execute_goal_handoff import handle_master_handoff
from .execute_goal_intervention import handle_login_intervention
from .execute_goal_progress import evaluate_post_action_progress
from .execute_goal_recovery import handle_action_recovery
from .execute_goal_streaks import update_action_streaks_and_loops
from .parsing import parse_multi_values, parse_wait_payload
from .runtime import (
    ActionExecResult,
    FlowMasterOrchestrator,
    StepSubAgent,
)
from gaia.src.phase4.captcha_solver import CaptchaSolver
from gaia.src.phase4.memory.models import (
    MemoryActionRecord,
    MemorySummaryRecord,
)
from gaia.src.phase4.memory.retriever import MemoryRetriever
from gaia.src.phase4.memory.store import MemoryStore
from gaia.src.phase4.orchestrator import MasterOrchestrator
from gaia.src.phase4.browser_error_utils import add_no_retry_hint, extract_reason_fields


class _GoalFilterValidationAdapter:
    """Filter validation adapter for GoalDrivenAgent."""

    def __init__(self, agent: "GoalDrivenAgent"):
        self.agent = agent

    def analyze_dom(self) -> List[DOMElement]:
        return self.agent._analyze_dom()

    def apply_select(self, element_id: int, value: str) -> Dict[str, Any]:
        selector = self.agent._element_selectors.get(element_id)
        full_selector = self.agent._element_full_selectors.get(element_id) or selector
        ref_id = self.agent._element_ref_ids.get(element_id)
        exec_result = self.agent._execute_action(
            "select",
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value=value,
        )
        self.agent._last_exec_result = exec_result
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
        }

    def click_element(self, element_id: int) -> Dict[str, Any]:
        selector = self.agent._element_selectors.get(element_id)
        full_selector = self.agent._element_full_selectors.get(element_id) or selector
        ref_id = self.agent._element_ref_ids.get(element_id)
        before_url = str(self.agent._active_url or "")
        exec_result = self.agent._execute_action(
            "click",
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value=None,
        )
        self.agent._last_exec_result = exec_result
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
            "before_url": before_url,
            "after_url": str(self.agent._active_url or ""),
        }

    def scroll_for_pagination(self, anchor_element_id: int) -> Dict[str, Any]:
        selector = self.agent._element_selectors.get(anchor_element_id)
        full_selector = self.agent._element_full_selectors.get(anchor_element_id) or selector
        ref_id = self.agent._element_ref_ids.get(anchor_element_id)
        exec_result = self.agent._execute_action(
            "scroll",
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value="bottom",
        )
        self.agent._last_exec_result = exec_result
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
        }

    def wait_for_pagination_probe(self, wait_ms: int = 900) -> Dict[str, Any]:
        exec_result = self.agent._execute_action("wait", value={"timeMs": int(max(100, wait_ms))})
        self.agent._last_exec_result = exec_result
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
        }

    def reload_page(self, wait_ms: int = 900) -> Dict[str, Any]:
        current_url = str(self.agent._active_url or "")
        exec_result = self.agent._execute_action("goto", url=current_url)
        self.agent._last_exec_result = exec_result
        if wait_ms > 0:
            try:
                self.agent._execute_action("wait", value={"timeMs": int(max(100, wait_ms))})
            except Exception:
                pass
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
        }

    def resolve_ref(self, element_id: int) -> str:
        return str(self.agent._element_ref_ids.get(element_id) or "")

    def current_url(self) -> str:
        return str(self.agent._active_url or "")

    def record_reason(self, code: str) -> None:
        self.agent._record_reason_code(code)

    def log(self, message: str) -> None:
        self.agent._log(message)

    def capture_case_attachment(self, label: str) -> Optional[Dict[str, Any]]:
        shot = self.agent._capture_screenshot()
        if not isinstance(shot, str) or not shot.strip():
            return None
        return {
            "kind": "image_base64",
            "mime": "image/png",
            "data": shot,
            "label": str(label or "").strip(),
        }


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
        intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    ):
        self.mcp_host_url = mcp_host_url
        self.session_id = session_id
        self._log_callback = log_callback
        self._screenshot_callback = screenshot_callback
        self._intervention_callback = intervention_callback

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
        self._active_url: str = ""
        self._last_snapshot_evidence: Dict[str, Any] = {}
        self._last_exec_result: Optional[ActionExecResult] = None
        self._active_goal_text: str = ""
        self._ineffective_ref_counts: Dict[str, int] = {}
        self._last_success_click_intent: str = ""
        self._success_click_intent_streak: int = 0
        self._intent_stats: Dict[str, Dict[str, int]] = {}
        self._context_shift_round: int = 0
        self._last_context_shift_intent: str = ""
        self._runtime_phase: str = "COLLECT"
        self._progress_counter: int = 0
        self._no_progress_counter: int = 0
        self._weak_progress_streak: int = 0
        self._handoff_state: Dict[str, Any] = {}
        self._memory_selector_bias: Dict[str, float] = {}
        self._recent_click_element_ids: List[int] = []
        self._last_dom_top_ids: List[int] = []
        self._goal_constraints: Dict[str, Any] = {}
        self._goal_metric_value: Optional[float] = None
        self._goal_tokens: set[str] = set()
        self._steering_policy: Dict[str, Any] = {}
        self._steering_remaining_steps: int = 0
        self._steering_infeasible_block: bool = False
        self._reason_code_counts: Dict[str, int] = {}
        self._recovery_retry_streaks: Dict[str, int] = {}
        self._overlay_intercept_pending: bool = False
        self._loop_policy: Dict[str, int] = {
            "ref_soft_fail_limit": self._env_int("GAIA_LOOP_REF_SOFT_FAIL_LIMIT", 2, low=1, high=20),
            "scroll_streak_limit": self._env_int("GAIA_LOOP_SCROLL_STREAK_LIMIT", 3, low=1, high=20),
            "same_intent_soft_fail_limit": self._env_int("GAIA_LOOP_SAME_INTENT_SOFT_FAIL_LIMIT", 3, low=1, high=20),
            "no_progress_context_shift_min": self._env_int("GAIA_LOOP_NO_PROGRESS_CONTEXT_SHIFT_MIN", 2, low=0, high=50),
            "ineffective_action_shift_limit": self._env_int("GAIA_LOOP_INEFFECTIVE_ACTION_SHIFT_LIMIT", 3, low=1, high=30),
            "ineffective_action_stop_limit": self._env_int("GAIA_LOOP_INEFFECTIVE_ACTION_STOP_LIMIT", 8, low=2, high=80),
            "weak_progress_streak_limit": self._env_int("GAIA_LOOP_WEAK_PROGRESS_STREAK_LIMIT", 3, low=1, high=20),
            "oscillation_window": self._env_int("GAIA_LOOP_OSCILLATION_WINDOW", 6, low=4, high=20),
            "oscillation_block_steps": self._env_int("GAIA_LOOP_OSCILLATION_BLOCK_STEPS", 2, low=1, high=10),
            "close_phase_budget_steps": self._env_int("GAIA_LOOP_CLOSE_PHASE_BUDGET_STEPS", 6, low=1, high=40),
            "context_shift_fail_limit": self._env_int("GAIA_LOOP_CONTEXT_SHIFT_FAIL_LIMIT", 3, low=1, high=20),
            "context_shift_cooldown_steps": self._env_int("GAIA_LOOP_CONTEXT_SHIFT_COOLDOWN_STEPS", 4, low=0, high=60),
            "transient_retry_limit": self._env_int("GAIA_LOOP_TRANSIENT_RETRY_LIMIT", 2, low=1, high=20),
            "action_timeout_retry_limit": self._env_int("GAIA_LOOP_ACTION_TIMEOUT_RETRY_LIMIT", 2, low=1, high=20),
            "collect_gate_override_after": self._env_int("GAIA_LOOP_COLLECT_GATE_OVERRIDE_AFTER", 2, low=1, high=20),
            "captcha_solver_attempt_limit": self._env_int("GAIA_CAPTCHA_SOLVER_ATTEMPT_LIMIT", 2, low=1, high=10),
            "captcha_solver_cooldown_steps": self._env_int("GAIA_CAPTCHA_SOLVER_COOLDOWN_STEPS", 4, low=1, high=40),
        }

        # 실행 기억(KB)
        self._memory_store = MemoryStore(enabled=True)
        self._memory_retriever = MemoryRetriever(self._memory_store)
        self._memory_episode_id: Optional[int] = None
        self._memory_domain: str = ""

    def _log(self, message: str):
        """로그 출력"""
        print(message)
        if self._log_callback:
            self._log_callback(message)

    @staticmethod
    def _env_int(name: str, default: int, *, low: int = 0, high: int = 100) -> int:
        try:
            value = int(str(os.getenv(name, str(default))).strip())
        except Exception:
            value = int(default)
        if value < low:
            return low
        if value > high:
            return high
        return value

    def _loop_policy_value(self, key: str, default: int) -> int:
        cfg = self._loop_policy if isinstance(self._loop_policy, dict) else {}
        try:
            value = int(cfg.get(key, default))
        except Exception:
            value = int(default)
        return max(0, value)

    def _record_reason_code(self, code: Optional[str]) -> None:
        key = str(code or "").strip()
        if not key:
            return
        counts = self._reason_code_counts if isinstance(self._reason_code_counts, dict) else {}
        counts[key] = int(counts.get(key, 0)) + 1
        self._reason_code_counts = counts

    @staticmethod
    def _normalize_text(value: Optional[str]) -> str:
        return (value or "").strip().lower()

    @staticmethod
    def _tokenize_text(value: Optional[str]) -> List[str]:
        text = (value or "").lower()
        return [t for t in re.findall(r"[0-9a-zA-Z가-힣_]+", text) if len(t) >= 2]

    def _derive_goal_tokens(self, goal: TestGoal) -> set[str]:
        blob = self._goal_text_blob(goal)
        tokens = set(self._tokenize_text(blob))
        stop_tokens = {
            "그리고",
            "그다음",
            "다음",
            "먼저",
            "이후",
            "진행",
            "테스트",
            "the",
            "and",
            "then",
            "with",
            "from",
            "that",
            "this",
        }
        return {t for t in tokens if t not in stop_tokens}

    def _goal_overlap_score(self, *values: Optional[str]) -> float:
        if not self._goal_tokens:
            return 0.0
        value_tokens: set[str] = set()
        for value in values:
            if value:
                value_tokens.update(self._tokenize_text(str(value)))
        if not value_tokens:
            return 0.0
        return float(min(len(value_tokens.intersection(self._goal_tokens)), 6))

    @classmethod
    def _contains_login_hint(cls, value: Optional[str]) -> bool:
        return contains_login_hint_impl(value, cls._normalize_text)

    @classmethod
    def _contains_close_hint(cls, value: Optional[str]) -> bool:
        return contains_close_hint_impl(value, cls._normalize_text)

    @classmethod
    def _contains_progress_cta_hint(cls, value: Optional[str]) -> bool:
        return False

    @classmethod
    def _contains_context_shift_hint(cls, value: Optional[str]) -> bool:
        return False

    @classmethod
    def _contains_expand_hint(cls, value: Optional[str]) -> bool:
        return False

    @staticmethod
    def _is_numeric_page_label(value: Optional[str]) -> bool:
        return is_numeric_page_label_impl(value)

    @staticmethod
    def _is_navigational_href(value: Optional[str]) -> bool:
        return is_navigational_href_impl(value)

    @classmethod
    def _contains_wishlist_like_hint(cls, value: Optional[str]) -> bool:
        return False

    @classmethod
    def _contains_add_like_hint(cls, value: Optional[str]) -> bool:
        return False

    @classmethod
    def _contains_execute_hint(cls, value: Optional[str]) -> bool:
        return False

    def _recover_dom_after_empty(self, goal: "TestGoal") -> List["DOMElement"]:
        return recover_dom_after_empty_impl(
            runtime_phase=self._runtime_phase,
            no_progress_counter=self._no_progress_counter,
            goal_start_url=str(getattr(goal, "start_url", "") or ""),
            analyze_dom_fn=self._analyze_dom,
            log_fn=self._log,
            execute_action_fn=lambda start_url: self._execute_action("goto", url=start_url),
        )

    @classmethod
    def _contains_apply_hint(cls, value: Optional[str]) -> bool:
        return False

    @classmethod
    def _contains_completion_hint(cls, value: Optional[str]) -> bool:
        return False

    @classmethod
    def _contains_configure_hint(cls, value: Optional[str]) -> bool:
        return False

    @classmethod
    def _contains_next_pagination_hint(cls, value: Optional[str]) -> bool:
        return contains_next_pagination_hint_impl(value, cls._normalize_text)

    @classmethod
    def _derive_goal_constraints(cls, goal: TestGoal) -> Dict[str, Any]:
        return derive_goal_constraints_impl(
            cls._goal_text_blob(goal),
            cls._normalize_text,
        )

    @classmethod
    def _extract_metric_values_from_text(cls, value: str, metric_terms: List[str]) -> List[int]:
        return extract_metric_values_from_text_impl(
            value,
            metric_terms,
            cls._normalize_text,
        )

    def _estimate_goal_metric_from_dom(self, dom_elements: List[DOMElement]) -> Optional[float]:
        return estimate_goal_metric_from_dom_impl(
            dom_elements,
            self._goal_constraints,
            self._normalize_text,
        )

    def _evaluate_goal_mutation_contract(
        self,
        *,
        before_dom: List[DOMElement],
        after_dom: List[DOMElement],
    ) -> Optional[str]:
        try:
            from .constraints import evaluate_mutation_contract

            return evaluate_mutation_contract(
                before_dom=before_dom,
                after_dom=after_dom,
                goal_constraints=self._goal_constraints,
                normalize_text=self._normalize_text,
            )
        except Exception:
            return None

    def _is_collect_constraint_unmet(self) -> bool:
        return is_collect_constraint_unmet_impl(
            self._goal_constraints,
            self._goal_metric_value,
        )

    def _apply_phase_constraints(self, detected_phase: str) -> str:
        return apply_phase_constraints_impl(
            detected_phase,
            self._goal_constraints,
            self._goal_metric_value,
        )

    def _is_progress_transition_element(self, el: Optional[DOMElement]) -> bool:
        if el is None:
            return False
        fields = self._fields_for_element(el)
        return any(
            self._contains_progress_cta_hint(f)
            or self._contains_execute_hint(f)
            or self._contains_apply_hint(f)
            for f in fields
        )

    def _pick_collect_element(self, dom_elements: List[DOMElement]) -> Optional[tuple[int, str]]:
        candidates: List[tuple[float, int, str]] = []
        recent_clicks = self._recent_click_element_ids[-14:]
        for el in dom_elements:
            fields = self._fields_for_element(el)

            ref_id = self._element_ref_ids.get(el.id)
            if not ref_id or self._is_ref_temporarily_blocked(ref_id):
                continue

            role = self._normalize_text(el.role)
            tag = self._normalize_text(el.tag)
            if role not in {"button", "link", "menuitem", ""} and tag not in {"button", "a", "input"}:
                continue

            score = 4.5
            score += 2.0 * self._goal_overlap_score(
                el.text,
                el.aria_label,
                getattr(el, "title", None),
                self._element_full_selectors.get(el.id),
            )

            repeat_count = recent_clicks.count(el.id)
            if repeat_count > 0:
                score -= min(5.0, 1.6 * repeat_count)

            score += self._selector_bias_for_fields(fields)
            score += 0.8 * self._adaptive_intent_bias(self._candidate_intent_key("click", fields))
            score = self._clamp_score(score, low=-20.0, high=30.0)
            if score <= 0.5:
                continue

            label = str(el.text or el.aria_label or getattr(el, "title", None) or f"element:{el.id}")
            reason = f"목표 제약상 수집 단계 유지: {label[:60]}"
            candidates.append((score, el.id, reason))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, element_id, reason = candidates[0]
        return element_id, reason

    def _pick_collect_context_shift_element(
        self,
        dom_elements: List[DOMElement],
        used_element_ids: set[int],
    ) -> Optional[tuple[int, str, str]]:
        candidates: List[tuple[float, int, str, str]] = []
        recent_clicks = self._recent_click_element_ids[-12:]
        for el in dom_elements:
            if el.id in used_element_ids:
                continue
            ref_id = self._element_ref_ids.get(el.id)
            if not ref_id or self._is_ref_temporarily_blocked(ref_id):
                continue

            fields = self._fields_for_element(el)
            selector = self._element_full_selectors.get(el.id) or self._element_selectors.get(el.id) or ""
            role = self._normalize_text(el.role)
            tag = self._normalize_text(el.tag)
            is_navigation_candidate = role in {"tab", "link", "button", "menuitem"} or tag in {"a", "button"}
            if not is_navigation_candidate:
                continue

            normalized_selector = self._normalize_text(selector)
            text = self._normalize_text(el.text)
            aria = self._normalize_text(el.aria_label)
            has_arrow = any(ch in text or ch in aria for ch in ("›", "»", "→", ">"))
            nav_like_selector = any(k in normalized_selector for k in ("page", "pager", "nav", "tab"))
            if not (has_arrow or nav_like_selector):
                continue

            score = 12.0
            if el.id in recent_clicks:
                score -= 2.4
            if has_arrow:
                score += 2.8
            if self._is_numeric_page_label(el.text) or self._is_numeric_page_label(el.aria_label) or self._is_numeric_page_label(getattr(el, "title", None)):
                score -= 3.0
            score += self._goal_overlap_score(el.text, el.aria_label, getattr(el, "title", None))

            intent_key = self._candidate_intent_key("click", fields)
            score += self._adaptive_intent_bias(intent_key)
            score = self._clamp_score(score, low=-20.0, high=30.0)
            if score <= 1.0:
                continue

            label = str(el.text or el.aria_label or getattr(el, "title", None) or f"element:{el.id}")
            reason = f"수집 정체 복구: 다음/페이지 전환 우선 ({label[:60]})"
            candidates.append((score, el.id, reason, intent_key))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, element_id, reason, intent_key = candidates[0]
        return element_id, reason, intent_key

    def _pick_no_navigation_click_candidate(
        self,
        dom_elements: List[DOMElement],
        *,
        excluded_ids: Optional[set[int]] = None,
    ) -> Optional[tuple[int, str]]:
        blocked = excluded_ids or set()
        candidates: List[tuple[float, int, str]] = []
        for el in dom_elements:
            if el.id in blocked:
                continue

            ref_id = self._element_ref_ids.get(el.id)
            if not ref_id or self._is_ref_temporarily_blocked(ref_id):
                continue

            if self._is_navigational_href(el.href):
                continue

            fields = self._fields_for_element(el)
            field_blob = " ".join(fields).lower()
            score = 2.5
            score += 2.0 * self._goal_overlap_score(
                el.text,
                el.aria_label,
                getattr(el, "title", None),
            )
            if any(h in field_blob for h in ("detail", "상세", "보기", "view", "open", "expand", "펼치")):
                score += 2.5
            if any(h in field_blob for h in ("modal", "dialog", "overlay", "panel", "sheet", "drawer", "popup")):
                score += 2.0
            if any(h in field_blob for h in ("row", "card", "listitem")):
                score += 1.5
            score += self._selector_bias_for_fields(fields)
            score += self._adaptive_intent_bias(self._candidate_intent_key("click", fields))
            score = self._clamp_score(score, low=-20.0, high=30.0)
            if score <= 1.0:
                continue

            label = str(el.text or el.aria_label or getattr(el, "title", None) or f"element:{el.id}")
            candidates.append((score, el.id, f"페이지 고정 제약 준수: 비내비게이션 요소 우선 ({label[:60]})"))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, element_id, reason = candidates[0]
        return element_id, reason

    def _build_goal_constraint_prompt(self) -> str:
        collect_min = self._goal_constraints.get("collect_min")
        metric_label = str(self._goal_constraints.get("metric_label") or "단위")
        require_no_navigation = bool(self._goal_constraints.get("require_no_navigation"))
        lines: List[str] = []
        if collect_min is not None:
            current = self._goal_metric_value
            current_text = "unknown" if current is None else str(int(current))
            apply_target = self._goal_constraints.get("apply_target")
            target_line = ""
            if apply_target is not None:
                target_line = f"\n   - 최종 목표값: {int(apply_target)}{metric_label}"
            lines.append(
                "\n9. **목표 제약(강제)**"
                f"\n   - 현재 추정값: {current_text}{metric_label}"
                f"\n   - 최소 수집 기준: {int(collect_min)}{metric_label}"
                f"{target_line}"
                "\n   - 최소 수집 기준 미만이면 단계 전환 CTA를 선택하지 말고 수집 액션만 선택하세요."
            )
        if require_no_navigation:
            lines.append(
                "\n10. **페이지 고정 제약(강제)**"
                "\n   - 목표가 '페이지 이동 없이' 검증이므로 URL이 바뀌는 내비게이션 액션은 금지합니다."
                "\n   - 링크 이동보다 현재 페이지의 row/panel/modal/open/expand 계열 상호작용을 우선 선택하세요."
            )
        steering_rule = self._build_steering_prompt()
        if steering_rule:
            lines.append(steering_rule)
        return "".join(lines)

    def _build_steering_prompt(self) -> str:
        policy = self._steering_policy if isinstance(self._steering_policy, dict) else {}
        if not policy or self._steering_remaining_steps <= 0:
            return ""
        rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
        assertions = policy.get("assertions") if isinstance(policy.get("assertions"), list) else []
        if not rules and not assertions:
            return ""
        lines: List[str] = []
        lines.append("\n11. **사용자 스티어링 정책(우선 적용)**")
        lines.append(f"\n   - 남은 TTL: {int(self._steering_remaining_steps)} steps")
        for row in rules[:8]:
            if not isinstance(row, dict):
                continue
            rule_type = str(row.get("type") or "").strip()
            enforcement = str(row.get("enforcement") or "soft").strip().lower()
            tag = str(row.get("tag") or "").strip()
            need = row.get("need")
            if isinstance(need, list):
                need_text = ",".join(str(x) for x in need if str(x).strip())
            else:
                need_text = str(need or "").strip()
            body = tag or need_text
            if not rule_type or not body:
                continue
            lines.append(f"\n   - {enforcement.upper()} {rule_type}: {body}")
        for row in assertions[:4]:
            if not isinstance(row, dict):
                continue
            a_type = str(row.get("type") or "").strip()
            need = row.get("need")
            if isinstance(need, list):
                need_text = ",".join(str(x) for x in need if str(x).strip())
            else:
                need_text = str(need or "").strip()
            if not a_type:
                continue
            lines.append(f"\n   - ASSERT {a_type}: {need_text}")
        lines.append(
            "\n   - HARD 규칙은 반드시 준수하고, SOFT 규칙은 가능한 경우 우선 적용하세요."
        )
        return "".join(lines)

    def _activate_steering_policy(self, goal: TestGoal) -> None:
        self._steering_policy = {}
        self._steering_remaining_steps = 0
        data = goal.test_data if isinstance(goal.test_data, dict) else {}
        policy = data.get("steering_policy")
        if not isinstance(policy, dict):
            return
        rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
        assertions = policy.get("assertions") if isinstance(policy.get("assertions"), list) else []
        if not rules and not assertions:
            return
        try:
            ttl = int(policy.get("ttl_steps") or 8)
        except Exception:
            ttl = 8
        ttl = max(3, min(15, ttl))
        scope = str(policy.get("scope") or "next_n_steps").strip().lower() or "next_n_steps"
        bound_goal_id = str(policy.get("bound_goal_id") or "").strip()
        bound_phase = str(policy.get("bound_phase") or "").strip().upper()
        bound_origin = str(policy.get("bound_origin") or "").strip()

        if scope in {"current_goal", "goal"} and not bound_goal_id:
            bound_goal_id = str(goal.id)
        if scope in {"current_phase", "phase"} and not bound_phase:
            bound_phase = str(self._runtime_phase or "").strip().upper()
        if scope in {"current_origin", "origin"} and not bound_origin:
            try:
                parsed = urlparse(str(goal.start_url or ""))
                if parsed.scheme and parsed.netloc:
                    bound_origin = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                bound_origin = ""

        self._steering_policy = {
            "version": str(policy.get("version") or "steering.v1"),
            "raw_text": str(policy.get("raw_text") or ""),
            "scope": scope,
            "ttl_steps": ttl,
            "ttl_remaining": ttl,
            "priority": str(policy.get("priority") or "normal").strip().lower() or "normal",
            "rules": list(rules),
            "assertions": list(assertions),
            "bound_origin": bound_origin,
            "bound_goal_id": bound_goal_id,
            "bound_phase": bound_phase,
            "compile_confidence": policy.get("compile_confidence"),
            "_soft_relaxed_once": bool(policy.get("_soft_relaxed_once", False)),
        }
        self._steering_remaining_steps = ttl

    def _expire_steering_policy(self, code: str = "steering_expired") -> None:
        if not self._steering_policy and self._steering_remaining_steps <= 0:
            return
        self._steering_policy = {}
        self._steering_remaining_steps = 0
        self._record_reason_code(code)

    def _is_steering_context_valid(self, goal: TestGoal) -> bool:
        policy = self._steering_policy if isinstance(self._steering_policy, dict) else {}
        if not policy:
            return False
        scope = str(policy.get("scope") or "next_n_steps").strip().lower() or "next_n_steps"
        if scope in {"current_goal", "goal"} and str(policy.get("bound_goal_id") or "").strip() == "":
            return False
        if scope in {"current_phase", "phase"} and str(policy.get("bound_phase") or "").strip() == "":
            return False
        if scope in {"current_origin", "origin"} and str(policy.get("bound_origin") or "").strip() == "":
            return False
        bound_goal_id = str(policy.get("bound_goal_id") or "").strip()
        if bound_goal_id and bound_goal_id != str(goal.id):
            return False
        bound_phase = str(policy.get("bound_phase") or "").strip().upper()
        if bound_phase and bound_phase != str(self._runtime_phase or "").upper():
            return False
        bound_origin = str(policy.get("bound_origin") or "").strip().lower()
        if bound_origin:
            goal_origin = ""
            try:
                parsed = urlparse(str(goal.start_url or ""))
                if parsed.scheme and parsed.netloc:
                    goal_origin = f"{parsed.scheme}://{parsed.netloc}".lower()
            except Exception:
                goal_origin = ""
            if goal_origin and goal_origin != bound_origin:
                return False
        return True

    def _element_steering_tags(self, element: DOMElement) -> set[str]:
        fields = [
            element.text,
            element.aria_label,
            getattr(element, "title", None),
            self._element_full_selectors.get(element.id),
            self._element_selectors.get(element.id),
            element.class_name,
        ]
        blob = " ".join(self._normalize_text(v) for v in fields if v)
        tags: set[str] = set()
        if any(token in blob for token in ("바로추가", "바로 추가", "quick add", "add now")):
            tags.add("intent.quick_add")
        if any(token in blob for token in ("제거", "삭제", "remove", "delete", "비우", "clear", "empty")):
            tags.add("intent.remove_item")
        if any(token in blob for token in ("위시리스트", "wishlist")):
            tags.add("target.wishlist")
        return tags

    def _decision_steering_tags(
        self,
        decision: ActionDecision,
        selected_element: Optional[DOMElement],
    ) -> set[str]:
        tags: set[str] = set()
        action_to_tag = {
            ActionType.CLICK: "intent.click",
            ActionType.SELECT: "intent.select",
            ActionType.FILL: "intent.fill",
            ActionType.PRESS: "intent.press",
            ActionType.SCROLL: "intent.scroll",
            ActionType.WAIT: "intent.wait",
            ActionType.NAVIGATE: "intent.navigate",
            ActionType.HOVER: "intent.hover",
        }
        action_tag = action_to_tag.get(decision.action)
        if action_tag:
            tags.add(action_tag)
        if selected_element is not None:
            tags.update(self._element_steering_tags(selected_element))
        reasoning_blob = self._normalize_text(decision.reasoning)
        if any(token in reasoning_blob for token in ("바로추가", "바로 추가", "quick add")):
            tags.add("intent.quick_add")
        if any(token in reasoning_blob for token in ("제거", "삭제", "remove", "비우", "clear")):
            tags.add("intent.remove_item")
        return tags

    def _steering_assertion_haystack(self, dom_elements: List[DOMElement]) -> str:
        chunks: List[str] = []
        for el in dom_elements[:260]:
            chunks.append(str(el.text or ""))
            chunks.append(str(el.aria_label or ""))
            chunks.append(str(getattr(el, "title", "") or ""))
            selector = self._element_full_selectors.get(el.id) or self._element_selectors.get(el.id)
            if selector:
                chunks.append(str(selector))
        return self._normalize_text(" ".join(chunks))

    def _evaluate_steering_assertions(
        self,
        assertions: List[Dict[str, Any]],
        dom_elements: List[DOMElement],
    ) -> Tuple[bool, str]:
        if not assertions:
            return True, ""

        haystack = self._steering_assertion_haystack(dom_elements)
        evidence = self._last_snapshot_evidence if isinstance(self._last_snapshot_evidence, dict) else {}
        modal_open_now = bool(evidence.get("modal_open"))

        for row in assertions:
            if not isinstance(row, dict):
                continue
            assertion_type = str(row.get("type") or "").strip().lower()
            if assertion_type == "text_any":
                needs = row.get("need") if isinstance(row.get("need"), list) else []
                tokens = [self._normalize_text(str(v or "")) for v in needs if str(v or "").strip()]
                if tokens and not any(token in haystack for token in tokens):
                    return False, f"text_any:{'/'.join(tokens[:3])}"
                continue
            if assertion_type == "text_all":
                needs = row.get("need") if isinstance(row.get("need"), list) else []
                tokens = [self._normalize_text(str(v or "")) for v in needs if str(v or "").strip()]
                if any(token not in haystack for token in tokens):
                    return False, f"text_all:{'/'.join(tokens[:3])}"
                continue
            if assertion_type == "regex":
                pattern = str(row.get("pattern") or "").strip()
                if not pattern:
                    return False, "regex:empty_pattern"
                try:
                    if not re.search(pattern, haystack, flags=re.IGNORECASE):
                        return False, f"regex:{pattern}"
                except re.error:
                    return False, "regex:invalid_pattern"
                continue
            if assertion_type == "modal_open":
                expected = bool(row.get("value"))
                if modal_open_now != expected:
                    return False, f"modal_open:{modal_open_now}!={expected}"
                continue

            self._record_reason_code("steering_assertion_unsupported")
            return False, f"unsupported:{assertion_type or 'unknown'}"

        return True, ""

    def _apply_steering_assertions_on_decision(
        self,
        decision: ActionDecision,
        policy: Dict[str, Any],
        dom_elements: List[DOMElement],
    ) -> ActionDecision:
        if not bool(decision.is_goal_achieved):
            return decision
        assertions = policy.get("assertions") if isinstance(policy.get("assertions"), list) else []
        if not assertions:
            return decision
        ok, failed = self._evaluate_steering_assertions(assertions, dom_elements)
        if ok:
            self._record_reason_code("steering_assertion_met")
            return decision
        self._record_reason_code("steering_assertion_not_met")
        return ActionDecision(
            action=decision.action,
            element_id=decision.element_id,
            value=decision.value,
            reasoning=(
                "스티어링 assertion 검증 미충족으로 완료 판정을 보류했습니다"
                + (f" ({failed})" if failed else "")
                + ". "
                + str(decision.reasoning or "")
            ).strip(),
            confidence=max(float(decision.confidence or 0.0) - 0.05, 0.0),
            is_goal_achieved=False,
            goal_achievement_reason=None,
        )

    def _pick_steering_candidate(
        self,
        dom_elements: List[DOMElement],
        *,
        prefer_tags: set[str],
        forbid_tags: set[str],
        target_tokens: List[str],
    ) -> Optional[int]:
        candidates: List[Tuple[float, int]] = []
        for el in dom_elements:
            if not bool(el.is_visible) or not bool(el.is_enabled):
                continue
            if self._normalize_text(el.tag) not in {"button", "a", "input", "div", "span"}:
                continue
            ref_id = self._element_ref_ids.get(el.id)
            if not ref_id or self._is_ref_temporarily_blocked(ref_id):
                continue
            tags = self._element_steering_tags(el)
            if any(tag in forbid_tags for tag in tags):
                continue
            score = 0.0
            if prefer_tags and any(tag in prefer_tags for tag in tags):
                score += 5.0
            blob = " ".join(
                self._normalize_text(v)
                for v in [
                    el.text,
                    el.aria_label,
                    getattr(el, "title", None),
                    self._element_full_selectors.get(el.id),
                    self._element_selectors.get(el.id),
                ]
                if v
            )
            for token in target_tokens:
                if token and token in blob:
                    score += 1.5
            if score <= 0.0:
                continue
            candidates.append((score, int(el.id)))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return int(candidates[0][1])

    def _apply_steering_policy_on_decision(
        self,
        *,
        goal: TestGoal,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> ActionDecision:
        policy = self._steering_policy if isinstance(self._steering_policy, dict) else {}
        if not policy:
            return decision
        if not self._is_steering_context_valid(goal):
            self._expire_steering_policy("steering_expired")
            return decision
        if self._steering_remaining_steps <= 0:
            self._expire_steering_policy("steering_expired")
            return decision

        self._steering_remaining_steps -= 1
        self._steering_policy["ttl_remaining"] = int(self._steering_remaining_steps)

        rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
        hard_forbid: set[str] = set()
        soft_prefer: set[str] = set()
        target_tokens: List[str] = []
        for row in rules:
            if not isinstance(row, dict):
                continue
            rule_type = str(row.get("type") or "").strip()
            enforcement = str(row.get("enforcement") or "soft").strip().lower()
            if rule_type == "forbid_action_tag" and enforcement == "hard":
                tag = str(row.get("tag") or "").strip()
                if tag:
                    hard_forbid.add(tag)
            elif rule_type == "prefer_action_tag":
                tag = str(row.get("tag") or "").strip()
                if tag:
                    soft_prefer.add(tag)
            elif rule_type == "prefer_target_text":
                need = row.get("need")
                if isinstance(need, list):
                    for token in need:
                        normalized = self._normalize_text(str(token or ""))
                        if normalized:
                            target_tokens.append(normalized)

        if not hard_forbid and not soft_prefer and not target_tokens:
            decision = self._apply_steering_assertions_on_decision(decision, policy, dom_elements)
            if self._steering_remaining_steps <= 0:
                self._expire_steering_policy("steering_expired")
            return decision

        selected_element: Optional[DOMElement] = None
        if decision.element_id is not None:
            selected_element = next((el for el in dom_elements if int(el.id) == int(decision.element_id)), None)
        decision_tags = self._decision_steering_tags(decision, selected_element)
        blocked = bool(decision_tags and any(tag in hard_forbid for tag in decision_tags))

        if blocked:
            self._record_reason_code("steering_blocked")
            replacement = self._pick_steering_candidate(
                dom_elements,
                prefer_tags=(soft_prefer or {"intent.remove_item"}),
                forbid_tags=hard_forbid,
                target_tokens=target_tokens,
            )
            if replacement is None:
                soft_relaxed_once = bool(policy.get("_soft_relaxed_once", False))
                if not soft_relaxed_once:
                    replacement = self._pick_steering_candidate(
                        dom_elements,
                        prefer_tags=set(),
                        forbid_tags=hard_forbid,
                        target_tokens=target_tokens,
                    )
                    if replacement is not None:
                        self._record_reason_code("steering_relaxed_soft")
                        self._steering_policy["_soft_relaxed_once"] = True
                        return ActionDecision(
                            action=ActionType.CLICK,
                            element_id=int(replacement),
                            value=None,
                            reasoning=(
                                "스티어링 soft 규칙을 1회 완화해 hard 금지 규칙만 유지한 대체 액션을 선택했습니다. "
                                + str(decision.reasoning or "")
                            ).strip(),
                            confidence=max(float(decision.confidence or 0.0), 0.7),
                            is_goal_achieved=False,
                            goal_achievement_reason=None,
                        )
                self._record_reason_code("steering_infeasible")
                self._action_feedback.append(
                    "스티어링 HARD 규칙으로 실행 가능한 후보가 없습니다. /steer clear 또는 /handoff로 정책을 수정하세요."
                )
                if len(self._action_feedback) > 10:
                    self._action_feedback = self._action_feedback[-10:]
                self._steering_infeasible_block = True
                return ActionDecision(
                    action=ActionType.WAIT,
                    reasoning="스티어링 정책 충돌(steering_infeasible)로 사용자 수정이 필요합니다.",
                    confidence=0.2,
                    is_goal_achieved=False,
                    goal_achievement_reason=None,
                )
            self._record_reason_code("steering_applied")
            return ActionDecision(
                action=ActionType.CLICK,
                element_id=int(replacement),
                value=None,
                reasoning=(
                    "사용자 스티어링(HARD forbid) 적용으로 금지된 후보를 제외하고 대체 액션을 선택했습니다. "
                    + str(decision.reasoning or "")
                ).strip(),
                confidence=max(float(decision.confidence or 0.0), 0.78),
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

        if soft_prefer and decision.action in {ActionType.CLICK, ActionType.SELECT, ActionType.PRESS}:
            if not any(tag in soft_prefer for tag in decision_tags):
                replacement = self._pick_steering_candidate(
                    dom_elements,
                    prefer_tags=soft_prefer,
                    forbid_tags=hard_forbid,
                    target_tokens=target_tokens,
                )
                if replacement is not None and int(replacement) != int(decision.element_id or -1):
                    self._record_reason_code("steering_applied")
                    return ActionDecision(
                        action=ActionType.CLICK,
                        element_id=int(replacement),
                        value=None,
                        reasoning=(
                            "사용자 스티어링(SOFT prefer) 적용으로 선호 후보를 우선 선택했습니다. "
                            + str(decision.reasoning or "")
                        ).strip(),
                        confidence=max(float(decision.confidence or 0.0), 0.72),
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )

        decision = self._apply_steering_assertions_on_decision(decision, policy, dom_elements)
        if self._steering_remaining_steps <= 0:
            self._expire_steering_policy("steering_expired")
        return decision

    def _enforce_goal_constraints_on_decision(
        self,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> ActionDecision:
        modal_open_now = bool(
            self._last_snapshot_evidence.get("modal_open")
            if isinstance(self._last_snapshot_evidence, dict)
            else False
        )
        blocker_modal_now = modal_open_now
        if not blocker_modal_now:
            for el in dom_elements:
                fields = [
                    el.text,
                    el.aria_label,
                    getattr(el, "title", None),
                    self._element_full_selectors.get(el.id),
                    self._element_selectors.get(el.id),
                ]
                blob = " ".join(self._normalize_text(field) for field in fields if field)
                if (
                    ("로그인" in blob and "필요" in blob)
                    or "login required" in blob
                    or "sign in required" in blob
                    or "authentication required" in blob
                ):
                    blocker_modal_now = True
                    break
        if not blocker_modal_now:
            decision_reasoning_blob = self._normalize_text(getattr(decision, "reasoning", None))
            if (
                ("로그인" in decision_reasoning_blob and ("필요" in decision_reasoning_blob or "모달" in decision_reasoning_blob))
                or ("login" in decision_reasoning_blob and ("required" in decision_reasoning_blob or "modal" in decision_reasoning_blob))
            ):
                blocker_modal_now = True
        selected_element: Optional[DOMElement] = None
        if decision.element_id is not None:
            selected_element = next((el for el in dom_elements if el.id == decision.element_id), None)

        if (self._runtime_phase or "").upper() == "AUTH" or self._is_login_gate(dom_elements):
            return ActionDecision(
                action=decision.action,
                element_id=decision.element_id,
                value=decision.value,
                reasoning=decision.reasoning,
                confidence=decision.confidence,
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

        if bool(self._goal_constraints.get("require_no_navigation")) and decision.action == ActionType.NAVIGATE:
            return ActionDecision(
                action=ActionType.WAIT,
                reasoning="페이지 이동 없이 검증해야 하므로 navigate 액션을 차단하고 DOM 재평가를 수행합니다.",
                confidence=max(float(decision.confidence or 0.0) - 0.1, 0.0),
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

        if (
            bool(self._goal_constraints.get("require_no_navigation"))
            and decision.action == ActionType.CLICK
            and decision.element_id is not None
        ):
            selected = next((el for el in dom_elements if el.id == decision.element_id), None)
            if selected and self._is_navigational_href(selected.href):
                alternative = self._pick_no_navigation_click_candidate(
                    dom_elements,
                    excluded_ids={int(decision.element_id)},
                )
                if alternative is not None:
                    alt_id, alt_reason = alternative
                    return ActionDecision(
                        action=ActionType.CLICK,
                        element_id=alt_id,
                        reasoning=(
                            f"{alt_reason}. "
                            "현재 선택 요소는 URL 이동 가능성이 있어 페이지 고정 제약에 맞지 않습니다."
                        ),
                        confidence=max(float(decision.confidence or 0.0) - 0.05, 0.0),
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
                return ActionDecision(
                    action=ActionType.WAIT,
                    reasoning=(
                        "페이지 이동 없이 검증해야 하나 클릭 후보가 내비게이션 링크뿐이라 "
                        "DOM 재수집 후 비내비게이션 요소를 다시 탐색합니다."
                    ),
                    confidence=max(float(decision.confidence or 0.0) - 0.1, 0.0),
                    is_goal_achieved=False,
                    goal_achievement_reason=None,
                )

        try:
            collect_min_value = float(self._goal_constraints.get("collect_min"))
        except Exception:
            collect_min_value = 0.0
        if collect_min_value <= 1.0:
            return ActionDecision(
                action=decision.action,
                element_id=decision.element_id,
                value=decision.value,
                reasoning=decision.reasoning,
                confidence=decision.confidence,
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

        if (
            blocker_modal_now
            and decision.action in {ActionType.CLICK, ActionType.PRESS, ActionType.SELECT}
            and selected_element is not None
        ):
            selected_fields = [
                selected_element.text,
                selected_element.aria_label,
                getattr(selected_element, "title", None),
                self._element_full_selectors.get(selected_element.id),
                self._element_selectors.get(selected_element.id),
            ]
            selected_blob = " ".join(
                self._normalize_text(field) for field in selected_fields if field
            )
            selected_modal_unblock = bool(
                any(self._contains_close_hint(field) for field in selected_fields)
                or any(
                    token in selected_blob
                    for token in (
                        "확인",
                        "ok",
                        "okay",
                        "dismiss",
                        "취소",
                        "cancel",
                        "닫기",
                        "close",
                        "modal",
                        "dialog",
                        "overlay",
                        "backdrop",
                        "popup",
                        "sheet",
                        "drawer",
                    )
                )
            )
            if selected_modal_unblock:
                self._log("🧱 목표 제약 가드 우회: 모달 차단 해제 액션을 우선 수행합니다.")
                return ActionDecision(
                    action=decision.action,
                    element_id=decision.element_id,
                    value=decision.value,
                    reasoning=decision.reasoning,
                    confidence=decision.confidence,
                    is_goal_achieved=False,
                    goal_achievement_reason=None,
                )

        if not self._is_collect_constraint_unmet():
            return decision

        collect_min = int(self._goal_constraints.get("collect_min") or 0)
        metric_label = str(self._goal_constraints.get("metric_label") or "")
        current = self._goal_metric_value
        current_text = "unknown" if current is None else str(int(current))
        collect_gate_override_after = max(
            1, self._loop_policy_value("collect_gate_override_after", 2)
        )

        blocked_goal_done = bool(decision.is_goal_achieved)
        if not blocked_goal_done and decision.action in {ActionType.CLICK, ActionType.PRESS, ActionType.SELECT}:
            if selected_element is not None:
                overlap = self._goal_overlap_score(
                    selected_element.text,
                    selected_element.aria_label,
                    getattr(selected_element, "title", None),
                    self._element_full_selectors.get(selected_element.id),
                )
                if overlap >= 1.0:
                    return ActionDecision(
                        action=decision.action,
                        element_id=decision.element_id,
                        value=decision.value,
                        reasoning=decision.reasoning,
                        confidence=decision.confidence,
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
        elif not blocked_goal_done:
            return decision

        if (
            current is None
            and blocker_modal_now
            and decision.action in {ActionType.CLICK, ActionType.PRESS, ActionType.SELECT}
            and decision.element_id is not None
        ):
            self._log("🧱 목표 제약 가드 우회: 차단 모달 상황에서는 수집보다 차단 해제 액션을 우선합니다.")
            return ActionDecision(
                action=decision.action,
                element_id=decision.element_id,
                value=decision.value,
                reasoning=decision.reasoning,
                confidence=decision.confidence,
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

        if (
            current is None
            and self._no_progress_counter >= collect_gate_override_after
            and decision.action in {ActionType.CLICK, ActionType.PRESS, ActionType.SELECT}
            and decision.element_id is not None
        ):
            self._log(
                "🧱 목표 제약 가드 완화: 수집 지표 unknown 상태 정체가 반복되어 "
                "직접 상호작용 액션을 우선 시도합니다."
            )
            return ActionDecision(
                action=decision.action,
                element_id=decision.element_id,
                value=decision.value,
                reasoning=decision.reasoning,
                confidence=decision.confidence,
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

        if current is None and blocker_modal_now:
            modal_pick = self._pick_modal_unblock_element(
                dom_elements,
                self._element_full_selectors,
            )
            if modal_pick is None:
                modal_pick = self._pick_modal_unblock_element(
                    dom_elements,
                    self._element_selectors,
                )
            if modal_pick is None and isinstance(decision.reasoning, str):
                dom_ids = {int(el.id) for el in dom_elements}
                for match in re.finditer(r"\[(\d+)\]", decision.reasoning):
                    try:
                        candidate_id = int(match.group(1))
                    except Exception:
                        continue
                    if candidate_id not in dom_ids:
                        continue
                    ref_id = self._element_ref_ids.get(candidate_id)
                    if ref_id and not self._is_ref_temporarily_blocked(ref_id):
                        modal_pick = candidate_id
                        break
            if modal_pick is not None:
                self._log("🧱 목표 제약 가드 우회: 수집보다 모달 차단 해제를 우선합니다.")
                return ActionDecision(
                    action=ActionType.CLICK,
                    element_id=modal_pick,
                    reasoning="모달이 열린 상태에서는 배경 수집보다 차단 해제(확인/닫기)를 우선 수행",
                    confidence=0.86,
                    is_goal_achieved=False,
                    goal_achievement_reason=None,
                )

        picked = self._pick_collect_element(dom_elements)
        if picked is not None:
            picked_id, picked_reason = picked
            self._log(
                "🧱 목표 제약 가드: "
                f"현재 {current_text}{metric_label} < 최소 {collect_min}{metric_label}, "
                "수집 액션으로 교체합니다."
            )
            return ActionDecision(
                action=ActionType.CLICK,
                element_id=picked_id,
                reasoning=picked_reason,
                confidence=0.82,
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

        self._log(
            "🧱 목표 제약 가드: 수집 후보를 찾지 못해 대기/컨텍스트 전환을 유도합니다."
        )
        scroll_target_id: Optional[int] = None
        shift_pick = self._pick_collect_context_shift_element(dom_elements, set())
        if shift_pick is not None:
            scroll_target_id = shift_pick[0]
        elif dom_elements:
            for el in dom_elements:
                ref_id = self._element_ref_ids.get(el.id)
                if ref_id and not self._is_ref_temporarily_blocked(ref_id):
                    scroll_target_id = el.id
                    break
        if scroll_target_id is None:
            return ActionDecision(
                action=ActionType.WAIT,
                reasoning=(
                    f"최소 수집 기준({collect_min}{metric_label}) 미달이며 유효한 ref 대상을 찾지 못했습니다. "
                    "DOM 재수집 후 다시 시도합니다."
                ),
                confidence=0.45,
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )
        return ActionDecision(
            action=ActionType.SCROLL,
            element_id=scroll_target_id,
            reasoning=(
                f"최소 수집 기준({collect_min}{metric_label}) 미달 상태입니다. "
                "수집 가능한 요소가 보일 때까지 컨텍스트를 전환합니다."
            ),
            confidence=0.5,
            is_goal_achieved=False,
            goal_achievement_reason=None,
        )

    def _constraint_failure_reason(self) -> Optional[str]:
        return build_constraint_failure_reason_impl(
            self._goal_constraints,
            self._goal_metric_value,
        )

    @classmethod
    def _contains_logout_hint(cls, value: Optional[str]) -> bool:
        return contains_logout_hint_impl(value, cls._normalize_text)

    @classmethod
    def _contains_duplicate_account_hint(cls, value: Optional[str]) -> bool:
        return contains_duplicate_account_hint_impl(value, cls._normalize_text)

    @staticmethod
    def _next_username(base: str) -> str:
        return next_username_impl(base)

    def _rotate_signup_identity(self, goal: TestGoal) -> Optional[str]:
        return rotate_signup_identity_impl(goal, self._next_username)

    def _has_duplicate_account_signal(
        self,
        *,
        state_change: Optional[Dict[str, Any]],
        dom_elements: List[DOMElement],
    ) -> bool:
        return has_duplicate_account_signal_impl(
            state_change=state_change,
            dom_elements=dom_elements,
            contains_duplicate_account_hint_fn=self._contains_duplicate_account_hint,
        )

    def _goal_allows_logout(self) -> bool:
        return goal_allows_logout_impl(self._active_goal_text or "", self._contains_logout_hint)

    def _is_ref_temporarily_blocked(self, ref_id: Optional[str]) -> bool:
        if not ref_id:
            return False
        limit = self._loop_policy_value("ref_soft_fail_limit", 2)
        return int(self._ineffective_ref_counts.get(ref_id, 0)) >= max(1, limit)

    def _track_ref_outcome(
        self,
        *,
        ref_id: Optional[str],
        reason_code: str,
        success: bool,
        changed: bool,
    ) -> None:
        if not ref_id:
            return
        if success and changed:
            self._ineffective_ref_counts.pop(ref_id, None)
            return
        if reason_code in {"no_state_change", "not_actionable", "modal_not_open", "ambiguous_ref_target", "ambiguous_selector"}:
            self._ineffective_ref_counts[ref_id] = int(self._ineffective_ref_counts.get(ref_id, 0)) + 1

    @staticmethod
    def _state_change_indicates_progress(state_change: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(state_change, dict):
            return False
        strong_progress_keys = (
            "url_changed",
            "target_visibility_changed",
            "target_value_changed",
            "target_value_matches",
            "modal_count_changed",
            "backdrop_count_changed",
            "dialog_count_changed",
            "modal_state_changed",
            "auth_state_changed",
            "text_digest_changed",
            "nav_detected",
            "popup_detected",
            "dialog_detected",
        )
        return any(bool(state_change.get(key)) for key in strong_progress_keys)

    @staticmethod
    def _state_change_is_weak(state_change: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(state_change, dict):
            return False
        if GoalDrivenAgent._state_change_indicates_progress(state_change):
            return False
        weak_keys = (
            "effective",
            "dom_changed",
            "text_digest_changed",
            "interactive_count_changed",
            "list_count_changed",
            "counter_changed",
            "number_tokens_changed",
            "status_text_changed",
            "focus_changed",
            "scroll_changed",
        )
        return any(bool(state_change.get(key)) for key in weak_keys)

    def _is_verification_style_goal(self, goal: TestGoal) -> bool:
        text = self._normalize_text(
            " ".join(
                [
                    str(goal.name or ""),
                    str(goal.description or ""),
                    " ".join(str(item or "") for item in (goal.success_criteria or [])),
                ]
            )
        )
        if not text:
            return False

        verify_hints = (
            "검증",
            "확인",
            "작동",
            "동작",
            "되는지",
            "정상",
            "기능",
            "verify",
            "validation",
            "check",
            "works",
            "working",
        )
        operation_hints = (
            "클릭해",
            "눌러",
            "입력해",
            "채워",
            "작성해",
            "제출해",
            "저장해",
            "선택해",
            "실행해",
            "추가해",
            "삭제해",
            "제거해",
            "비우",
            "담기",
            "담아",
            "등록해",
            "login해",
            "로그인해",
            "회원가입해",
            "purchase",
            "submit",
            "clear",
            "remove",
            "click",
            "fill",
            "type",
            "select",
            "press",
        )
        entity_hints = (
            "회원가입",
            "로그인",
            "signup",
            "register",
            "login",
            "결제",
            "구매",
            "checkout",
            "purchase",
        )
        visibility_hints = (
            "보이는지",
            "표시",
            "노출",
            "존재",
            "있는지",
            "열려있는지",
            "링크",
            "버튼",
            "이미",
            "현재",
            "visible",
            "shown",
            "exists",
            "present",
        )
        has_verify_hint = any(hint in text for hint in verify_hints)
        has_operation_hint = any(hint in text for hint in operation_hints)
        has_entity_hint = any(hint in text for hint in entity_hints)
        has_visibility_hint = any(hint in text for hint in visibility_hints)
        if not has_verify_hint:
            return False
        if has_operation_hint:
            return False
        if has_entity_hint and not has_visibility_hint:
            return False
        return True

    def _is_filter_style_goal(self, goal: TestGoal) -> bool:
        text = self._normalize_text(
            " ".join(
                [
                    str(goal.name or ""),
                    str(goal.description or ""),
                    " ".join(str(item or "") for item in (goal.success_criteria or [])),
                ]
            )
        )
        if not text:
            return False
        if not self._is_verification_style_goal(goal):
            return False
        explicit_filter_hints = (
            "필터",
            "filter",
            "학점",
            "credit",
            "정렬",
            "sort",
        )
        category_like_hints = (
            "분류",
            "category",
        )
        readonly_verification_hints = (
            "현재",
            "이미",
            "추가 조작 없이",
            "보이는지",
            "표시",
            "존재",
            "확인",
            "visible",
            "already",
            "without interaction",
        )
        if any(hint in text for hint in category_like_hints) and not any(
            hint in text for hint in explicit_filter_hints
        ):
            return False
        if any(hint in text for hint in readonly_verification_hints) and not any(
            hint in text for hint in explicit_filter_hints
        ):
            return False
        filter_hints = (
            *explicit_filter_hints,
            "분류",
            "category",
        )
        return any(hint in text for hint in filter_hints)

    def _can_finish_by_verification_transition(
        self,
        *,
        goal: TestGoal,
        decision: ActionDecision,
        success: bool,
        changed: bool,
        state_change: Optional[Dict[str, Any]],
        before_dom_count: int,
        after_dom_count: int,
    ) -> bool:
        if not (success and changed):
            return False
        if decision.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.NAVIGATE, ActionType.SELECT}:
            return False
        if not self._is_verification_style_goal(goal):
            return False
        if self._is_filter_style_goal(goal):
            # 필터 검증 목표는 단순 전이 신호로 조기 성공 처리하지 않고
            # semantic filter validation 엔진 결과로 최종 판정한다.
            return False
        goal_text = self._normalize_text(
            " ".join(
                [
                    str(goal.name or ""),
                    str(goal.description or ""),
                    " ".join(str(item or "") for item in (goal.success_criteria or [])),
                ]
            )
        )
        has_close_hint = any(token in goal_text for token in ("닫", "close", "x 버튼", "x버튼", "dismiss"))
        has_list_hint = any(token in goal_text for token in ("목록", "list", "게시판", "게시글", "board", "row"))
        if has_close_hint and has_list_hint:
            # "열기->닫기->목록 복귀" 형태 목표는 단일 전환으로 완료시키지 않는다.
            return False
        if self._is_collect_constraint_unmet():
            return False

        # Collect/apply constraints가 있으면 "전환만으로 완료" 판정을 막는다.
        if self._goal_constraints.get("collect_min") is not None:
            return False
        if self._goal_constraints.get("apply_target") is not None:
            return False

        require_no_navigation = bool(self._goal_constraints.get("require_no_navigation"))

        if not isinstance(state_change, dict):
            if require_no_navigation:
                return False
            return after_dom_count != before_dom_count

        if require_no_navigation and bool(state_change.get("url_changed")):
            return False

        transition_keys = (
            "url_changed",
            "dom_changed",
            "modal_state_changed",
            "modal_count_changed",
            "backdrop_count_changed",
            "dialog_count_changed",
            "status_text_changed",
            "auth_state_changed",
            "text_digest_changed",
            "interactive_count_changed",
            "list_count_changed",
        )
        return any(bool(state_change.get(key)) for key in transition_keys) or (
            after_dom_count != before_dom_count
        )

    def _build_verification_transition_reason(
        self,
        *,
        state_change: Optional[Dict[str, Any]],
        before_dom_count: int,
        after_dom_count: int,
    ) -> str:
        if not isinstance(state_change, dict):
            return (
                "검증형 목표로 판단되어, 액션 후 화면 상태가 변화해 기능 동작을 확인했습니다."
            )

        signals: List[str] = []
        if bool(state_change.get("modal_state_changed")) or bool(state_change.get("dialog_count_changed")):
            signals.append("모달/상세 패널 상태 변화")
        if bool(state_change.get("backdrop_count_changed")):
            signals.append("오버레이(backdrop) 변화")
        if bool(state_change.get("url_changed")):
            signals.append("URL 변화")
        if bool(state_change.get("dom_changed")) or bool(state_change.get("text_digest_changed")):
            signals.append("DOM/본문 변화")
        if bool(state_change.get("interactive_count_changed")) or bool(state_change.get("list_count_changed")):
            signals.append("상호작용/목록 수 변화")
        if not signals and after_dom_count != before_dom_count:
            signals.append(f"DOM 규모 변화({before_dom_count}->{after_dom_count})")

        if not signals:
            return (
                "검증형 목표로 판단되어, 액션 후 상태 변화가 감지되어 기능 동작을 확인했습니다."
            )
        return (
            "검증형 목표로 판단되어, 액션 후 "
            + ", ".join(signals[:3])
            + "가 확인되어 기능 동작으로 판정했습니다."
        )

    def _evaluate_static_verification_on_current_page(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
    ) -> Optional[str]:
        if not self._is_verification_style_goal(goal):
            return None
        if self._is_filter_style_goal(goal):
            return None

        goal_text = self._normalize_text(
            " ".join(
                [
                    str(goal.name or ""),
                    str(goal.description or ""),
                    " ".join(str(item or "") for item in (goal.success_criteria or [])),
                ]
            )
        )
        if not goal_text:
            return None

        static_check_hints = (
            "현재",
            "이미",
            "추가 조작 없이",
            "보이는지",
            "노출",
            "표시",
            "존재하는지",
            "열려있는지",
            "확인",
            "visible",
            "already",
            "without interaction",
        )
        if not any(hint in goal_text for hint in static_check_hints):
            return None

        page_fragments: List[str] = [str(self._active_url or "")]
        for el in dom_elements[:120]:
            page_fragments.extend(
                [
                    str(el.text or ""),
                    str(el.aria_label or ""),
                    str(getattr(el, "title", None) or ""),
                    str(el.placeholder or ""),
                    str(el.href or ""),
                    str(self._element_full_selectors.get(el.id) or self._element_selectors.get(el.id) or ""),
                ]
            )
        page_blob = self._normalize_text(" ".join(fragment for fragment in page_fragments if fragment))

        def _matches_any(*needles: str) -> bool:
            return any(str(needle or "").strip().lower() in page_blob for needle in needles if str(needle or "").strip())

        evidence_labels: List[str] = []
        visible_elements = [el for el in dom_elements if bool(el.is_visible)]
        link_like_count = sum(
            1
            for el in visible_elements
            if str(el.href or "").strip()
            or self._normalize_text(el.tag) == "a"
            or self._normalize_text(el.role) == "link"
        )
        collection_like_count = sum(
            1
            for el in visible_elements
            if self._normalize_text(el.tag) in {"a", "li", "tr", "article"}
            or self._normalize_text(el.role) in {"row", "listitem"}
        )
        title_like_count = sum(
            1
            for el in visible_elements
            if len(str(el.text or "").strip()) >= 12
        )
        has_collection_evidence = bool(link_like_count >= 6 or collection_like_count >= 6 or title_like_count >= 8)

        if any(token in goal_text for token in ("로그인", "login", "sign in", "signin")):
            if not _matches_any("로그인", "login", "sign in", "/login", "signin"):
                return None
            evidence_labels.append("로그인 신호")

        generic_stop_tokens = {
            "현재", "이미", "추가", "조작", "없이", "보이는지", "표시", "존재하는지",
            "열려있는지", "확인", "페이지", "화면", "되는지", "정상", "작동", "검증",
            "상태", "목록이", "목록", "리스트", "테이블", "table", "list", "page",
            "visible", "already", "without", "interaction", "verify", "check", "page",
            "open", "opened", "shown",
        }
        goal_tokens = [
            token for token in self._tokenize_text(goal_text)
            if token not in generic_stop_tokens
        ]
        matched_generic: List[str] = []
        strong_matched = False
        for token in goal_tokens:
            if len(token) < 2:
                continue
            if token in page_blob:
                matched_generic.append(token)
                if token.isdigit() or len(token) >= 4:
                    strong_matched = True

        list_like_hints = ("목록", "리스트", "list", "table", "테이블", "랭킹", "게시판", "카테고리", "태그", "분류", "status", "현황")
        detail_like_hints = ("상세", "detail")
        asks_list_like = any(hint in goal_text for hint in list_like_hints)
        asks_detail_like = any(hint in goal_text for hint in detail_like_hints)

        if asks_list_like and not has_collection_evidence:
            return None
        if asks_list_like and has_collection_evidence:
            evidence_labels.append("목록형 구조")

        if asks_detail_like and not (strong_matched or len(matched_generic) >= 2):
            return None
        if asks_detail_like and (strong_matched or len(matched_generic) >= 2):
            evidence_labels.append("상세 토큰 일치")

        if not evidence_labels:
            if strong_matched:
                evidence_labels.append("핵심 토큰 일치")
            elif len(matched_generic) >= 2:
                evidence_labels.append("토큰 일치")
            elif len(matched_generic) >= 1 and has_collection_evidence:
                evidence_labels.append("토큰+목록 구조 일치")
            else:
                return None

        if matched_generic:
            evidence_labels.extend(sorted(dict.fromkeys(matched_generic[:3])))

        self._record_reason_code("static_verification_pass")
        labels = ", ".join(dict.fromkeys(evidence_labels)) if evidence_labels else "현재 페이지 신호"
        return f"현재 페이지에서 목표 검증 신호를 바로 확인했습니다. ({labels})"

    def _extract_goal_query_tokens(self, goal: TestGoal) -> List[str]:
        goal_text = " ".join(
            [
                str(goal.name or ""),
                str(goal.description or ""),
                " ".join(str(item or "") for item in (goal.success_criteria or [])),
            ]
        )
        quoted = re.findall(r"\"([^\"]{2,})\"|'([^']{2,})'", goal_text)
        quoted_tokens = [next((part for part in group if part), "") for group in quoted]
        tokens: List[str] = [token.strip() for token in quoted_tokens if token.strip()]

        for match in re.findall(r"(?<!\d)(\d{3,6})(?!\d)", goal_text):
            tokens.append(str(match))

        for raw in re.findall(r"[0-9A-Za-z가-힣+/#_-]{2,}", goal_text):
            token = str(raw or "").strip()
            low = token.lower()
            if low in {
                "문제", "페이지", "검색", "search", "open", "detail", "상세", "열어줘",
                "현재", "이미", "확인", "보이는지", "추가", "조작", "없이", "종료해줘",
            }:
                continue
            if any(ch.isdigit() for ch in token) or "+" in token or len(token) >= 4:
                tokens.append(token)

        deduped: List[str] = []
        seen = set()
        for token in tokens:
            norm = self._normalize_text(token)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            deduped.append(token)
        return deduped[:8]

    def _build_deterministic_goal_preplan(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
        steps: Optional[List[StepResult]] = None,
    ) -> Optional[ActionDecision]:
        goal_text = self._normalize_text(
            " ".join(
                [
                    str(goal.name or ""),
                    str(goal.description or ""),
                    " ".join(str(item or "") for item in (goal.success_criteria or [])),
                ]
            )
        )
        if not goal_text:
            return None

        query_tokens = self._extract_goal_query_tokens(goal)
        if not query_tokens:
            return None

        search_hints = ("검색", "search", "query", "find")
        open_hints = ("열어", "open", "상세", "detail")

        if any(hint in goal_text for hint in search_hints):
            search_candidates: List[tuple[float, DOMElement]] = []
            for el in dom_elements:
                if not bool(el.is_visible and el.is_enabled):
                    continue
                tag = self._normalize_text(el.tag)
                etype = self._normalize_text(el.type)
                if tag != "input" and tag != "textarea":
                    continue
                score = 0.0
                if etype in {"search", "text"}:
                    score += 3.0
                if any(
                    token in self._normalize_text(
                        " ".join(
                            [
                                str(el.placeholder or ""),
                                str(el.aria_label or ""),
                                str(el.text or ""),
                                str(self._element_full_selectors.get(el.id) or self._element_selectors.get(el.id) or ""),
                            ]
                        )
                    )
                    for token in ("검색", "search", "query")
                ):
                    score += 4.0
                if score > 0.0:
                    search_candidates.append((score, el))
            if search_candidates:
                search_candidates.sort(key=lambda item: item[0], reverse=True)
                query_value = query_tokens[0]
                search_input = search_candidates[0][1]
                last_step = steps[-1] if steps else None
                repeated_fill = bool(
                    last_step
                    and bool(last_step.success)
                    and last_step.action.action == ActionType.FILL
                    and last_step.action.element_id == search_input.id
                    and self._normalize_text(str(last_step.action.value or "")) == self._normalize_text(query_value)
                )
                if repeated_fill:
                    submit_candidates: List[tuple[float, DOMElement]] = []
                    for el in dom_elements:
                        if not bool(el.is_visible and el.is_enabled):
                            continue
                        tag = self._normalize_text(el.tag)
                        etype = self._normalize_text(el.type)
                        if tag not in {"button", "a", "input"}:
                            continue
                        if tag == "input" and etype not in {"submit", "button"}:
                            continue
                        blob = self._normalize_text(
                            " ".join(
                                [
                                    str(el.text or ""),
                                    str(el.aria_label or ""),
                                    str(getattr(el, "title", None) or ""),
                                    str(self._element_full_selectors.get(el.id) or self._element_selectors.get(el.id) or ""),
                                ]
                            )
                        )
                        score = 0.0
                        if any(token in blob for token in ("검색", "search", "찾기", "go", "submit")):
                            score += 5.0
                        if tag == "button":
                            score += 1.0
                        if score > 0.0:
                            submit_candidates.append((score, el))
                    if submit_candidates:
                        submit_candidates.sort(key=lambda item: item[0], reverse=True)
                        submit_target = submit_candidates[0][1]
                        return ActionDecision(
                            action=ActionType.CLICK,
                            element_id=submit_target.id,
                            value=None,
                            reasoning=f"같은 검색어 `{query_value}` 입력이 이미 끝났으므로 검색 CTA를 바로 실행합니다.",
                            confidence=0.94,
                            is_goal_achieved=False,
                            goal_achievement_reason=None,
                        )
                    return ActionDecision(
                        action=ActionType.PRESS,
                        element_id=search_input.id,
                        value="Enter",
                        reasoning=f"같은 검색어 `{query_value}` 입력이 이미 끝났으므로 Enter로 검색을 제출합니다.",
                        confidence=0.93,
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
                return ActionDecision(
                    action=ActionType.FILL,
                    element_id=search_input.id,
                    value=query_value,
                    reasoning=f"목표에 명시된 검색 토큰 `{query_value}`를 검색 입력에 우선 적용합니다.",
                    confidence=0.92,
                    is_goal_achieved=False,
                    goal_achievement_reason=None,
                )

        if any(hint in goal_text for hint in open_hints):
            candidates: List[tuple[float, DOMElement, str]] = []
            numeric_tokens = [token for token in query_tokens if token.isdigit()]
            for el in dom_elements:
                if not bool(el.is_visible and el.is_enabled):
                    continue
                href = str(el.href or "")
                text = str(el.text or "")
                aria = str(el.aria_label or "")
                blob = self._normalize_text(" ".join([href, text, aria]))
                matched = []
                score = 0.0
                for token in query_tokens:
                    norm = self._normalize_text(token)
                    if not norm:
                        continue
                    if norm in blob:
                        matched.append(token)
                        score += 3.0
                if not matched:
                    continue
                if self._normalize_text(el.tag) == "a":
                    score += 2.0
                if href:
                    score += 1.5
                if any(ch.isdigit() for ch in "".join(matched)) and re.search(r"/[a-z]+/\d+", href):
                    score += 2.0
                for token in numeric_tokens:
                    if re.search(rf"/{re.escape(token)}(?:[/?#]|$)", href):
                        score += 6.0
                    elif token in href:
                        score += 2.0
                candidates.append((score, el, ", ".join(matched[:3])))
            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                _, element, label = candidates[0]
                return ActionDecision(
                    action=ActionType.CLICK,
                    element_id=element.id,
                    value=None,
                    reasoning=f"목표에 명시된 타깃 토큰({label})과 가장 잘 맞는 항목을 직접 엽니다.",
                    confidence=0.9,
                    is_goal_achieved=False,
                    goal_achievement_reason=None,
                )

        return None

    @classmethod
    def _build_click_intent_key(
        cls,
        *,
        element: Optional[DOMElement],
        full_selector: Optional[str],
        selector: Optional[str],
    ) -> str:
        if element is None:
            return ""
        text = cls._normalize_text(element.text)
        aria = cls._normalize_text(element.aria_label)
        role = cls._normalize_text(element.role)
        tag = cls._normalize_text(element.tag)
        sel = cls._normalize_text(full_selector or selector)
        if len(sel) > 120:
            sel = sel[:120]
        return f"{tag}|{role}|{text}|{aria}|{sel}"

    @staticmethod
    def _squash_text(text: str, limit: int = 160) -> str:
        normalized = re.sub(r"\s+", " ", (text or "")).strip().lower()
        if len(normalized) > limit:
            return normalized[:limit]
        return normalized

    def _fields_for_element(self, el: DOMElement) -> List[str]:
        selector = self._element_full_selectors.get(el.id) or self._element_selectors.get(el.id) or ""
        return [
            str(el.text or ""),
            str(el.aria_label or ""),
            str(el.placeholder or ""),
            str(getattr(el, "title", None) or ""),
            str(el.href or ""),
            selector,
            str(el.role or ""),
            str(el.tag or ""),
            str(el.type or ""),
        ]

    def _candidate_intent_key(self, action: str, fields: List[str]) -> str:
        blob = " | ".join(str(x or "") for x in fields if str(x or "").strip())
        return f"{action}:{self._squash_text(blob, limit=180)}"

    @staticmethod
    def _clamp_score(value: float, low: float = -15.0, high: float = 15.0) -> float:
        return max(low, min(high, float(value)))

    def _adaptive_intent_bias(self, intent_key: str) -> float:
        if not intent_key:
            return 0.0
        stat = self._intent_stats.get(intent_key) or {}
        ok = int(stat.get("ok") or 0)
        soft_fail = int(stat.get("soft_fail") or 0)
        hard_fail = int(stat.get("hard_fail") or 0)
        raw = (0.8 * ok) - (1.2 * soft_fail) - (1.5 * hard_fail)
        return self._clamp_score(raw, low=-12.0, high=8.0)

    def _update_intent_stats(
        self,
        *,
        intent_key: str,
        success: bool,
        changed: bool,
        reason_code: str,
    ) -> None:
        if not intent_key:
            return
        stat = self._intent_stats.setdefault(
            intent_key,
            {"ok": 0, "soft_fail": 0, "hard_fail": 0},
        )
        if success and changed:
            stat["ok"] = min(200, int(stat.get("ok") or 0) + 1)
            # 성공 시 누적 실패를 완전히 지우지 않고 완만하게 완화
            if int(stat.get("soft_fail") or 0) > 0:
                stat["soft_fail"] = int(stat["soft_fail"]) - 1
            if int(stat.get("hard_fail") or 0) > 0:
                stat["hard_fail"] = int(stat["hard_fail"]) - 1
            return
        if reason_code in {"no_state_change", "not_actionable", "modal_not_open", "blocked_ref_no_progress", "ambiguous_ref_target", "ambiguous_selector"}:
            stat["soft_fail"] = min(200, int(stat.get("soft_fail") or 0) + 1)
        else:
            stat["hard_fail"] = min(200, int(stat.get("hard_fail") or 0) + 1)

    @staticmethod
    def _normalize_selector_key(selector: str) -> str:
        cleaned = re.sub(r"\s+", " ", (selector or "").strip().lower())
        if len(cleaned) > 180:
            return cleaned[:180]
        return cleaned

    def _selector_bias_for_fields(self, fields: List[str]) -> float:
        if not self._memory_selector_bias:
            return 0.0
        blob = self._normalize_selector_key(" | ".join(str(x or "") for x in fields))
        if not blob:
            return 0.0
        bias = 0.0
        for key, weight in self._memory_selector_bias.items():
            if key and key in blob:
                bias += float(weight)
        return self._clamp_score(bias, low=-10.0, high=10.0)

    def _infer_runtime_phase(self, dom_elements: List[DOMElement]) -> str:
        return infer_runtime_phase_impl(
            dom_elements=dom_elements,
            is_login_gate_fn=self._is_login_gate,
            is_collect_constraint_unmet=self._is_collect_constraint_unmet(),
            progress_counter=self._progress_counter,
            runtime_phase=self._runtime_phase,
        )

    @classmethod
    def _is_login_gate(cls, dom_elements: List[DOMElement]) -> bool:
        return is_login_gate_impl(
            dom_elements,
            normalize_text=cls._normalize_text,
            contains_login_hint_fn=lambda value: cls._contains_login_hint(value),
        )

    @classmethod
    def _is_compact_auth_page(cls, dom_elements: List[DOMElement]) -> bool:
        return is_compact_auth_page_impl(
            dom_elements,
            normalize_text=cls._normalize_text,
            contains_login_hint_fn=lambda value: cls._contains_login_hint(value),
        )

    @classmethod
    def _goal_requires_login_interaction(cls, goal: TestGoal) -> bool:
        return goal_requires_login_interaction_impl(
            goal,
            lambda value: cls._contains_login_hint(value),
        )

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

            if score > 0:
                candidates.append((score, el.id))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    @classmethod
    def _pick_modal_unblock_element(
        cls,
        dom_elements: List[DOMElement],
        selector_map: Dict[int, str],
        modal_regions_hint: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[int]:
        modal_regions: List[Dict[str, float]] = []
        if isinstance(modal_regions_hint, list):
            for region in modal_regions_hint[:8]:
                if not isinstance(region, dict):
                    continue
                try:
                    rx = float(region.get("x", 0.0) or 0.0)
                    ry = float(region.get("y", 0.0) or 0.0)
                    rw = float(region.get("width", 0.0) or 0.0)
                    rh = float(region.get("height", 0.0) or 0.0)
                except Exception:
                    continue
                if rw < 80.0 or rh < 80.0:
                    continue
                modal_regions.append(
                    {
                        "x": rx,
                        "y": ry,
                        "width": rw,
                        "height": rh,
                        "right": rx + rw,
                        "bottom": ry + rh,
                    }
                )
        for container in dom_elements:
            bbox = container.bounding_box if isinstance(container.bounding_box, dict) else {}
            try:
                cx = float(bbox.get("x", 0.0) or 0.0)
                cy = float(bbox.get("y", 0.0) or 0.0)
                cw = float(bbox.get("width", 0.0) or 0.0)
                ch = float(bbox.get("height", 0.0) or 0.0)
            except Exception:
                continue
            if cw < 120.0 or ch < 120.0:
                continue
            role = cls._normalize_text(container.role)
            tag = cls._normalize_text(container.tag)
            class_name = cls._normalize_text(container.class_name)
            aria_modal = cls._normalize_text(container.aria_modal)
            if not (
                aria_modal == "true"
                or role in {"dialog", "alertdialog"}
                or tag == "dialog"
                or any(token in class_name for token in ("modal", "dialog", "popup", "sheet", "drawer", "overlay"))
            ):
                continue
            modal_regions.append(
                {
                    "x": cx,
                    "y": cy,
                    "width": cw,
                    "height": ch,
                    "right": cx + cw,
                    "bottom": cy + ch,
                }
            )
        modal_regions.sort(key=lambda region: region["width"] * region["height"], reverse=True)
        if len(modal_regions) > 1:
            largest_area = modal_regions[0]["width"] * modal_regions[0]["height"]
            compact_regions = [
                region
                for region in modal_regions
                if (region["width"] * region["height"]) <= (largest_area * 0.92)
            ]
            if compact_regions:
                modal_regions = compact_regions
        modal_regions = modal_regions[:4]

        candidates: List[tuple[int, int]] = []
        for el in dom_elements:
            selector = selector_map.get(el.id, "")
            role = cls._normalize_text(el.role)
            tag = cls._normalize_text(el.tag)

            text_fields = [
                el.text,
                el.aria_label,
                el.placeholder,
                getattr(el, "title", None),
                selector,
            ]
            normalized_blob = " ".join(cls._normalize_text(field) for field in text_fields if field)
            score = 0
            close_hint_signal = any(cls._contains_close_hint(field) for field in text_fields)

            if close_hint_signal:
                score += 5
            if any(
                token in normalized_blob
                for token in ("확인", "ok", "okay", "dismiss", "취소", "cancel", "닫기", "close")
            ):
                score += 4
            if any(
                token in normalized_blob
                for token in ("modal", "dialog", "overlay", "backdrop", "popup", "sheet", "drawer")
            ):
                score += 3
            if role in {"button", "dialogclose", "link", "menuitem"} or tag in {"button", "a", "input"}:
                score += 1
            if cls._normalize_text(el.text) in {"x", "×", "확인", "ok", "닫기", "취소", "close"}:
                score += 2
            if cls._normalize_text(el.type) == "submit":
                score -= 1
            bbox = el.bounding_box if isinstance(el.bounding_box, dict) else {}
            try:
                ex = float(bbox.get("x", 0.0) or 0.0)
                ey = float(bbox.get("y", 0.0) or 0.0)
                ew = float(bbox.get("width", 0.0) or 0.0)
                eh = float(bbox.get("height", 0.0) or 0.0)
                ecx = ex + (ew / 2.0)
                ecy = ey + (eh / 2.0)
            except Exception:
                ex = ey = ew = eh = ecx = ecy = 0.0
            inside_modal_region = False
            near_modal_corner = False
            if ew > 0.0 and eh > 0.0:
                if ew <= 96.0 and eh <= 96.0:
                    score += 1
                for region in modal_regions:
                    if not (region["x"] <= ecx <= region["right"] and region["y"] <= ecy <= region["bottom"]):
                        continue
                    inside_modal_region = True
                    rel_x = (ecx - region["x"]) / max(region["width"], 1.0)
                    rel_y = (ecy - region["y"]) / max(region["height"], 1.0)
                    if rel_x >= 0.72 and rel_y <= 0.28:
                        near_modal_corner = True
                        score += 6
                    elif rel_x >= 0.60 and rel_y <= 0.40:
                        score += 3
                    unlabeled_icon = (
                        cls._normalize_text(el.text) in {"", "x", "×", "✕"}
                        and cls._normalize_text(el.aria_label) == ""
                        and cls._normalize_text(getattr(el, "title", None)) == ""
                        and (role in {"button", "dialogclose"} or tag in {"button", "a", "input"})
                        and ew <= 96.0
                        and eh <= 96.0
                    )
                    if unlabeled_icon:
                        score += 4
                        if near_modal_corner:
                            score += 2
                    break
            if modal_regions and not inside_modal_region:
                # 모달이 열려 있는 상황에서는 모달 영역 밖 아이콘/버튼 오클릭을 강하게 억제.
                if not close_hint_signal:
                    continue
                score -= 3

            if modal_regions:
                if not (close_hint_signal or near_modal_corner):
                    continue
            else:
                if not close_hint_signal:
                    continue
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

    def _request_user_intervention(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self._intervention_callback:
            return None
        try:
            resp = self._intervention_callback(payload)
            return resp if isinstance(resp, dict) else None
        except Exception as exc:
            self._log(f"사용자 개입 콜백 오류: {exc}")
            return None

    @staticmethod
    def _merge_test_data(
        goal: TestGoal,
        payload: Dict[str, Any],
        *,
        blocked_keys: set[str] | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            return
        blocked = blocked_keys or set()
        if not isinstance(goal.test_data, dict):
            goal.test_data = {}
        for key, value in payload.items():
            norm_key = str(key or "").strip()
            if not norm_key or norm_key in blocked:
                continue
            if value is None:
                continue
            if isinstance(value, str):
                cleaned = value.strip()
                if not cleaned:
                    continue
                goal.test_data[norm_key] = cleaned
                continue
            goal.test_data[norm_key] = value

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "f", "no", "n", "off"}:
            return False
        return default

    def _request_goal_clarification(self, goal: TestGoal) -> bool:
        text = f"{goal.name} {goal.description}".strip().lower()
        if not text:
            return False

        ambiguous_tokens = {"안녕", "하이", "hello", "hi", "test", "테스트", "해봐", "해줘"}
        tokens = {w.strip() for w in text.replace("/", " ").split() if w.strip()}
        looks_ambiguous = len(text) < 8 or (tokens and tokens.issubset(ambiguous_tokens))

        sensitive_hints = (
            "로그인",
            "회원가입",
            "인증",
            "결제",
            "payment",
            "purchase",
            "구매",
            "주문",
            "예약",
        )
        needs_sensitive_data = any(h in text for h in sensitive_hints)

        if not looks_ambiguous and not (needs_sensitive_data and not self._has_login_test_data(goal)):
            self._handoff_state = {
                "kind": "clarification",
                "provided": False,
                "phase": self._runtime_phase,
            }
            return True

        callback_payload = {
            "kind": "clarification",
            "goal_name": goal.name,
            "goal_description": goal.description,
            "question": (
                "목표가 모호하거나 중요한 입력 정보가 부족합니다. "
                "구체 목표와 필요한 입력(id/pw/email 등)을 제공해 주세요."
            ),
            "fields": ["goal_text", "username", "email", "password", "proceed"],
        }
        callback_resp = self._request_user_intervention(callback_payload)
        if callback_resp is not None:
            callback_reason_code = str(callback_resp.get("reason_code") or "").strip().lower()
            if str(callback_resp.get("action") or "").lower() in {"cancel", "deny", "no"}:
                self._record_reason_code(callback_reason_code or "user_intervention_missing")
                return False

            goal_text = str(callback_resp.get("goal_text") or "").strip()
            if goal_text:
                goal.name = goal_text[:40]
                goal.description = goal_text
                goal.success_criteria = [goal_text]

            username = str(callback_resp.get("username") or "").strip()
            email = str(callback_resp.get("email") or "").strip()
            password = str(callback_resp.get("password") or "").strip()
            if username or email or password:
                if not isinstance(goal.test_data, dict):
                    goal.test_data = {}
                if username:
                    goal.test_data["username"] = username
                if email:
                    goal.test_data["email"] = email
                if password:
                    goal.test_data["password"] = password
            self._merge_test_data(
                goal,
                callback_resp,
                blocked_keys={"action", "proceed", "goal_text", "username", "email", "password"},
            )
            self._handoff_state = {
                "kind": "clarification",
                "provided": True,
                "phase": self._runtime_phase,
                "timestamp": int(time.time()),
            }
            if callback_reason_code:
                self._record_reason_code(callback_reason_code)
            proceed = callback_resp.get("proceed")
            if isinstance(proceed, bool):
                return proceed
            if isinstance(proceed, str):
                return self._to_bool(proceed, default=True)
            return True

        self._log("🙋 사용자 개입 필요: 목표가 모호하거나 중요한 정보가 부족합니다.")
        try:
            interactive_stdin = bool(os.isatty(0))
        except Exception:
            interactive_stdin = False
        if not interactive_stdin:
            self._handoff_state = {
                "kind": "clarification",
                "provided": False,
                "phase": self._runtime_phase,
                "requested": True,
                "timestamp": int(time.time()),
            }
            self._record_reason_code("user_intervention_missing")
            self._log(
                "⏸️ 비대화 실행이라 추가 입력을 받을 수 없습니다. "
                "실행을 일시 중지하고 사용자 응답(/handoff 또는 재실행 인자) 대기 상태로 전환합니다."
            )
            return False
        try:
            refined = input("구체 목표를 입력하세요 (비우면 기존 목표 유지): ").strip()
        except (EOFError, KeyboardInterrupt):
            self._record_reason_code("user_intervention_missing")
            self._log("사용자 입력이 중단되었습니다.")
            return False
        if refined:
            goal.name = refined[:40]
            goal.description = refined
            goal.success_criteria = [refined]
            self._handoff_state = {
                "kind": "clarification",
                "provided": True,
                "phase": self._runtime_phase,
                "timestamp": int(time.time()),
            }

        if needs_sensitive_data and not self._has_login_test_data(goal):
            try:
                login_id = input("아이디/이메일 (건너뛰려면 Enter): ").strip()
                password = input("비밀번호 (건너뛰려면 Enter): ").strip()
            except (EOFError, KeyboardInterrupt):
                self._log("사용자 입력이 중단되었습니다.")
                return False
            if login_id or password:
                if not isinstance(goal.test_data, dict):
                    goal.test_data = {}
                if login_id:
                    goal.test_data["username"] = login_id
                    if "@" in login_id and not str(goal.test_data.get("email") or "").strip():
                        goal.test_data["email"] = login_id
                if password:
                    goal.test_data["password"] = password
        return True

    def _request_login_intervention(self, goal: TestGoal) -> bool:
        self._log("🙋 사용자 개입 필요: 로그인/인증 화면이 감지되었습니다.")
        self._handoff_state = {
            "kind": "auth",
            "phase": self._runtime_phase,
            "requested": True,
            "timestamp": int(time.time()),
        }
        callback_payload = {
            "kind": "auth",
            "goal_name": goal.name,
            "goal_description": goal.description,
            "question": (
                "로그인/인증 정보가 필요합니다. "
                "진행 여부와 계정 정보(username/email/password) 또는 수동 로그인 완료 여부를 알려주세요. "
                "회원가입으로 진행하려면 auth_mode=signup을 함께 전달하세요."
            ),
            "fields": [
                "proceed",
                "auth_mode",
                "manual_done",
                "username",
                "email",
                "password",
                "department",
                "grade_year",
                "return_credentials",
            ],
        }
        callback_resp = self._request_user_intervention(callback_payload)
        if callback_resp is None:
            try:
                interactive_stdin = bool(os.isatty(0))
            except Exception:
                interactive_stdin = False
            if not interactive_stdin:
                self._handoff_state["provided"] = False
                self._handoff_state["mode"] = "awaiting_user_input"
                self._log(
                    "⏸️ 로그인/인증 개입이 필요하지만 비대화 실행이라 입력을 받을 수 없습니다. "
                    "실행을 중단하고 사용자 응답(회원가입 포함)을 기다립니다."
                )
                return False
        if callback_resp is not None:
            if str(callback_resp.get("action") or "").lower() in {"cancel", "deny", "no"}:
                self._log("로그인 개입이 취소되었습니다.")
                return False
            if bool(callback_resp.get("manual_done")):
                self._log("사용자가 수동 로그인 완료를 전달했습니다.")
                self._handoff_state["provided"] = True
                self._handoff_state["mode"] = "manual_done"
                return True
            auth_mode = str(callback_resp.get("auth_mode") or "").strip().lower()
            username = str(callback_resp.get("username") or "").strip()
            email = str(callback_resp.get("email") or "").strip()
            password = str(callback_resp.get("password") or "").strip()
            login_id = username or email
            department = str(callback_resp.get("department") or "").strip()
            grade_year = str(callback_resp.get("grade_year") or "").strip()
            return_credentials = self._to_bool(callback_resp.get("return_credentials"), default=False)

            if auth_mode in {"signup", "register"}:
                if not login_id:
                    suffix = int(time.time()) % 100000
                    login_id = f"gaia_user_{suffix:05d}"
                if not password:
                    suffix = int(time.time()) % 100000
                    password = f"Gaia!{suffix:05d}"
                if "@" in login_id:
                    email = email or login_id
                    username = username or login_id.split("@")[0]
                elif not email:
                    email = f"{login_id}@gaia.local"
                if not isinstance(goal.test_data, dict):
                    goal.test_data = {}
                goal.test_data["auth_mode"] = "signup"
                goal.test_data["username"] = username or login_id
                goal.test_data["email"] = email
                goal.test_data["password"] = password
                if department:
                    goal.test_data["department"] = department
                if grade_year:
                    goal.test_data["grade_year"] = grade_year
                goal.test_data["return_credentials"] = return_credentials
                self._merge_test_data(
                    goal,
                    callback_resp,
                    blocked_keys={
                        "action",
                        "proceed",
                        "auth_mode",
                        "manual_done",
                        "username",
                        "email",
                        "password",
                        "department",
                        "grade_year",
                        "return_credentials",
                    },
                )
                self._log("사용자 요청에 따라 회원가입 모드로 진행합니다.")
                if return_credentials:
                    self._log(
                        f"회원가입에 사용할 계정: username={goal.test_data.get('username')} "
                        f"email={goal.test_data.get('email')} password={goal.test_data.get('password')}"
                    )
                self._handoff_state["provided"] = True
                self._handoff_state["mode"] = "signup"
                return True

            if login_id and password:
                if not isinstance(goal.test_data, dict):
                    goal.test_data = {}
                goal.test_data["username"] = login_id
                if email or ("@" in login_id and not str(goal.test_data.get("email") or "").strip()):
                    goal.test_data["email"] = email or login_id
                goal.test_data["password"] = password
                self._merge_test_data(
                    goal,
                    callback_resp,
                    blocked_keys={
                        "action",
                        "proceed",
                        "auth_mode",
                        "manual_done",
                        "username",
                        "email",
                        "password",
                        "department",
                        "grade_year",
                        "return_credentials",
                    },
                )
                self._log("사용자 로그인 정보가 test_data에 반영되었습니다.")
                self._handoff_state["provided"] = True
                self._handoff_state["mode"] = "login"
                return True
            self._log("로그인 정보가 충분하지 않습니다.")
            return False

        try:
            answer = input("로그인을 진행할까요? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._log("사용자 입력이 중단되었습니다.")
            return False

        if answer in {"n", "no"}:
            self._log("로그인 개입이 취소되었습니다.")
            return False

        try:
            login_id = input("아이디/이메일 (비우면 브라우저에서 수동 로그인): ").strip()
        except (EOFError, KeyboardInterrupt):
            self._log("사용자 입력이 중단되었습니다.")
            return False

        if not login_id:
            self._log("브라우저에서 직접 로그인 후 Enter를 눌러 계속하세요.")
            try:
                input("로그인 완료 후 Enter: ")
            except (EOFError, KeyboardInterrupt):
                self._log("사용자 입력이 중단되었습니다.")
                return False
            self._handoff_state["provided"] = True
            self._handoff_state["mode"] = "manual_done"
            return True

        try:
            password = input("비밀번호: ")
        except (EOFError, KeyboardInterrupt):
            self._log("사용자 입력이 중단되었습니다.")
            return False

        if not str(password or "").strip():
            self._log("비밀번호가 비어 있어 진행을 중단합니다.")
            return False

        if not isinstance(goal.test_data, dict):
            goal.test_data = {}
        goal.test_data["username"] = login_id
        if "@" in login_id and not str(goal.test_data.get("email") or "").strip():
            goal.test_data["email"] = login_id
        goal.test_data["password"] = password
        self._log("사용자 로그인 정보가 test_data에 반영되었습니다.")
        self._handoff_state["provided"] = True
        self._handoff_state["mode"] = "login"
        return True

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
    def _error_indicates_overlay_intercept(error: Optional[str]) -> bool:
        text = str(error or "").lower()
        if not text:
            return False
        if "intercepts pointer events" in text:
            return True
        if "subtree intercepts pointer events" in text:
            return True
        return False

    def _pick_context_shift_element(
        self,
        dom_elements: List[DOMElement],
        used_element_ids: set[int],
    ) -> Optional[tuple[int, str, str]]:
        self._context_shift_round += 1
        phase = (self._runtime_phase or "COLLECT").upper()
        exploration_slot = (self._context_shift_round % 4) == 0
        collect_unmet = self._is_collect_constraint_unmet()

        add_candidates_visible = False
        for probe_el in dom_elements:
            probe_fields = [
                str(probe_el.text or "").strip(),
                str(probe_el.aria_label or "").strip(),
                str(probe_el.placeholder or "").strip(),
                str(getattr(probe_el, "title", None) or "").strip(),
                str(self._element_full_selectors.get(probe_el.id) or self._element_selectors.get(probe_el.id) or ""),
            ]
            if any(self._contains_add_like_hint(f) for f in probe_fields):
                add_candidates_visible = True
                break

        candidates: List[tuple[float, int, str, str]] = []
        for el in dom_elements:
            if el.id in used_element_ids:
                continue
            selector = self._element_full_selectors.get(el.id) or self._element_selectors.get(el.id) or ""
            text = str(el.text or "").strip()
            aria_label = str(el.aria_label or "").strip()
            title = str(getattr(el, "title", None) or "").strip()
            href = str(el.href or "").strip()
            fields = [
                text,
                aria_label,
                el.placeholder,
                title,
                selector,
                href,
            ]

            has_context_shift = any(self._contains_context_shift_hint(f) for f in fields)
            has_expand = any(self._contains_expand_hint(f) for f in fields)
            has_next = any(self._contains_next_pagination_hint(f) for f in fields)
            has_progress = any(self._contains_progress_cta_hint(f) for f in fields)
            has_wishlist_like = any(self._contains_wishlist_like_hint(f) for f in fields)
            has_add_like = any(self._contains_add_like_hint(f) for f in fields)
            has_configure = any(self._contains_configure_hint(f) for f in fields)
            has_execute = any(self._contains_execute_hint(f) for f in fields)
            has_apply = any(self._contains_apply_hint(f) for f in fields)

            score = 0.0
            if has_context_shift:
                score += 3.5
            if has_next:
                score += 4.5
            if has_progress:
                score += 5.0
            if has_expand:
                score += 0.8

            role = self._normalize_text(el.role)
            tag = self._normalize_text(el.tag)
            if role in {"tab", "link", "button", "menuitem"}:
                score += 1.8
            if tag in {"a", "button"}:
                score += 1.2

            normalized_selector = self._normalize_text(selector)
            if any(k in normalized_selector for k in ("pagination", "pager", "page", "tab", "tabs", "nav")):
                score += 2.2
            if any(k in normalized_selector for k in ("next", "다음", "pager-next", "page-next", "nav-next")):
                score += 2.8
            if any(k in normalized_selector for k in ("prev", "previous", "back", "이전")):
                score -= 5.0
            if any(k in normalized_selector for k in ("active", "current", "selected")):
                score -= 2.0

            is_numeric_page = (
                self._is_numeric_page_label(text)
                or self._is_numeric_page_label(aria_label)
                or self._is_numeric_page_label(title)
            )
            if is_numeric_page and not has_next:
                score -= 3.5

            if phase in {"AUTH", "COLLECT"}:
                if has_progress:
                    score += 2.0
                if has_next:
                    score += 1.5
                if has_expand and not has_wishlist_like:
                    score -= 1.0
            elif phase == "COMPOSE":
                if has_configure:
                    score += 2.5
                if has_context_shift:
                    score += 1.8
                if has_progress:
                    score += 3.0
                if has_add_like:
                    score -= 1.5
            elif phase == "APPLY":
                if has_execute or has_progress:
                    score += 4.0
                if has_next:
                    score += 2.2
                if has_add_like:
                    score -= 2.5
            elif phase == "VERIFY":
                if has_apply or has_progress:
                    score += 4.5
                if has_add_like:
                    score -= 3.5

            if collect_unmet:
                if has_next:
                    score += 5.5
                if has_progress or has_execute or has_apply:
                    score -= 6.0
                if has_add_like:
                    score += 0.8
                if is_numeric_page and not has_next:
                    score -= 5.0
                if any(k in normalized_selector for k in ("last", "first", "처음", "마지막")):
                    score -= 2.5

            intent_key = self._candidate_intent_key("click", fields)
            score += self._adaptive_intent_bias(intent_key)
            score += self._selector_bias_for_fields(fields)

            if intent_key and intent_key == self._last_context_shift_intent:
                score -= 3.0

            if exploration_slot:
                score += 0.6
                if has_next or has_progress or has_context_shift:
                    score += 1.1

            score = self._clamp_score(score, low=-20.0, high=25.0)

            if score <= 1.0:
                continue

            label = (el.text or el.aria_label or getattr(el, "title", None) or selector or f"element:{el.id}")
            if has_next:
                reason_core = "페이지네이션 전환"
            elif has_progress:
                reason_core = "단계 전환 CTA"
            elif has_context_shift:
                reason_core = "컨텍스트 전환"
            elif has_expand and not has_wishlist_like:
                reason_core = "콘텐츠 확장"
            else:
                reason_core = "반복 탈출"
            reason = (
                f"{reason_core} 우선 시도: {str(label)[:60]} "
                f"(phase={phase}, score={score:.1f})"
            )
            candidates.append((score, el.id, reason, intent_key))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, element_id, reason, intent_key = candidates[0]
        return element_id, reason, intent_key

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
        if "empty_response_from_codex_exec" in text or "empty_response_from_model" in text:
            return (
                "LLM 호출이 중단되었습니다: 모델 응답이 비어 있습니다. "
                "Codex CLI 버전/로그인 상태를 확인하고 다시 시도하세요."
            )
        if "failed to read prompt from stdin" in text or "not valid utf-8" in text:
            return (
                "LLM 호출이 중단되었습니다: Codex CLI 입력 인코딩(UTF-8) 오류입니다. "
                "최신 코드로 업데이트 후 다시 실행하세요."
            )
        if "codex exec failed" in text or "unexpected argument" in text:
            return (
                "LLM 호출이 중단되었습니다: Codex CLI 실행 인자/버전 오류입니다. "
                "`codex exec --help`로 옵션 호환성을 확인하세요."
            )
        return None

    @staticmethod
    def _dom_progress_signature(dom_elements: List[DOMElement]) -> str:
        count = len(dom_elements)
        if count < 50:
            bucket = "lt50"
        elif count < 100:
            bucket = "50_99"
        elif count < 150:
            bucket = "100_149"
        elif count < 220:
            bucket = "150_219"
        else:
            bucket = "220p"
        chunks: List[str] = []
        for el in dom_elements[:20]:
            chunks.append(
                f"{el.tag}|{(el.text or '')[:40]}|{el.role or ''}|{el.type or ''}|{el.aria_label or ''}"
            )
        return f"{bucket}#" + "||".join(chunks)

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
        intent_key: Optional[str] = None,
    ):
        code = reason_code or (self._last_exec_result.reason_code if self._last_exec_result else "unknown")
        self._record_reason_code(str(code or "unknown"))
        self._update_intent_stats(
            intent_key=intent_key or "",
            success=bool(success),
            changed=bool(changed),
            reason_code=str(code or "unknown"),
        )
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
            self._memory_selector_bias = {}
            return ""
        hints = self._memory_retriever.retrieve_lightweight(
            domain=self._memory_domain,
            goal_text=f"{goal.name} {goal.description}",
            action_history=self._action_history[-6:],
        )

        bias: Dict[str, float] = {}
        for item in hints:
            selector_hint = self._normalize_selector_key(str(item.selector_hint or ""))
            if not selector_hint:
                continue
            confidence = max(0.0, min(1.0, float(item.confidence or 0.0)))
            weight = 0.0
            if item.source == "success_pattern":
                weight += 0.7 + (0.6 * confidence)
            elif item.source == "failure_pattern":
                weight -= 0.6 + (0.7 * confidence)
            elif item.source == "recovery":
                if str(item.reason_code or "") in {"no_state_change", "not_actionable", "not_found"}:
                    weight -= 0.4
                else:
                    weight += 0.2

            if str(item.reason_code or "") in {"no_state_change", "not_actionable", "blocked_ref_no_progress"}:
                weight -= 0.35
            elif str(item.reason_code or "") == "ok":
                weight += 0.2

            merged = float(bias.get(selector_hint, 0.0)) + weight
            bias[selector_hint] = self._clamp_score(merged, low=-4.0, high=4.0)

        if len(bias) > 40:
            top = sorted(bias.items(), key=lambda kv: abs(kv[1]), reverse=True)[:40]
            self._memory_selector_bias = dict(top)
        else:
            self._memory_selector_bias = bias

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

        constraint_reason = self._constraint_failure_reason()
        if constraint_reason:
            return False, constraint_reason

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
        self._reason_code_counts = {}
        self._recovery_retry_streaks = {}
        self._overlay_intercept_pending = False
        steps: List[StepResult] = []
        self._active_goal_text = f"{goal.name} {goal.description}".strip().lower()
        self._steering_infeasible_block = False
        self._ineffective_ref_counts = {}
        self._last_success_click_intent = ""
        self._success_click_intent_streak = 0
        self._intent_stats = {}
        self._context_shift_round = 0
        self._last_context_shift_intent = ""
        self._runtime_phase = "COLLECT"
        self._progress_counter = 0
        self._no_progress_counter = 0
        self._modal_opened_once = False
        self._modal_closed_after_open = False
        self._close_intent_success_once = False
        self._close_click_success_once = False
        self._handoff_state = {}
        self._memory_selector_bias = {}
        self._recent_click_element_ids = []
        self._last_dom_top_ids = []
        self._goal_tokens = self._derive_goal_tokens(goal)
        self._goal_constraints = self._derive_goal_constraints(goal)
        self._activate_steering_policy(goal)
        self._goal_metric_value = None
        self._last_filter_semantic_report = None
        self._filter_validation_contract = None
        filter_goal_active = self._is_filter_style_goal(goal)
        filter_semantic_attempts = 0
        filter_semantic_attempt_limit = self._env_int(
            "GAIA_FILTER_SEMANTIC_SELECT_LIMIT",
            12,
            low=3,
            high=200,
        )
        filter_semantic_max_cases = self._env_int(
            "GAIA_FILTER_SEMANTIC_MAX_CASES",
            20,
            low=1,
            high=50,
        )
        filter_semantic_current_only = bool(
            self._env_int(
                "GAIA_FILTER_SEMANTIC_CURRENT_ONLY",
                0,
                low=0,
                high=1,
            )
        )

        collect_min = self._goal_constraints.get("collect_min")
        apply_target = self._goal_constraints.get("apply_target")
        metric_label = str(self._goal_constraints.get("metric_label") or "")
        if collect_min is not None:
            msg = f"🧩 목표 제약 감지: 최소 수집 {int(collect_min)}{metric_label}"
            if apply_target is not None:
                msg += f", 적용 목표 {int(apply_target)}{metric_label}"
            self._log(msg)

        self._log(f"🎯 목표 시작: {goal.name}")
        self._log(f"   설명: {goal.description}")
        self._log(f"   성공 조건: {goal.success_criteria}")

        if not self._request_goal_clarification(goal):
            return self._build_failure_result(
                goal=goal,
                steps=[],
                step_count=0,
                start_time=start_time,
                reason=(
                    "중요 정보/목표 명확화가 필요하지만 사용자 입력이 제공되지 않아 중단했습니다. "
                    "목표를 더 구체화하거나 test_data를 함께 제공해 주세요."
                ),
            )

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
        if goal.start_url:
            self._log(f"📍 시작 URL로 이동: {goal.start_url}")
            self._execute_action("goto", url=goal.start_url)
            time.sleep(2)  # 페이지 로드 대기

        requires_login_interaction = self._goal_requires_login_interaction(goal)
        has_login_test_data = self._has_login_test_data(goal)
        orchestrator = FlowMasterOrchestrator(goal=goal, max_steps=goal.max_steps)
        master_orchestrator = MasterOrchestrator()
        sub_agent = StepSubAgent(self)
        ineffective_action_streak = 0
        scroll_streak = 0
        login_intervention_asked = False
        force_context_shift = False
        context_shift_used_elements: set[int] = set()
        context_shift_fail_streak = 0
        last_metric_value: Optional[float] = None
        collect_metric_stall_count = 0
        context_shift_cooldown = 0

        while orchestrator.can_continue():
            step_count = orchestrator.begin_step()
            step_start = time.time()
            if context_shift_cooldown > 0:
                context_shift_cooldown -= 1

            self._log(f"\n--- Step {step_count}/{orchestrator.max_steps} ---")

            # 1. 현재 페이지 DOM 분석
            dom_elements = self._analyze_dom()
            if not dom_elements:
                self._log("⚠️ DOM 요소를 찾을 수 없음, 잠시 대기 후 재시도")
                dom_elements = self._recover_dom_after_empty(goal)
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

            self._goal_metric_value = self._estimate_goal_metric_from_dom(dom_elements)
            collect_unmet = self._is_collect_constraint_unmet()
            if collect_unmet:
                if self._goal_metric_value is None:
                    collect_metric_stall_count += 1
                elif last_metric_value is None:
                    collect_metric_stall_count = 0
                elif float(self._goal_metric_value) <= float(last_metric_value) + 1e-9:
                    collect_metric_stall_count += 1
                else:
                    collect_metric_stall_count = 0
            else:
                collect_metric_stall_count = 0
            if self._goal_metric_value is not None:
                last_metric_value = float(self._goal_metric_value)

            orchestrator.observe_dom(dom_elements)
            if orchestrator.stop_reason:
                if collect_unmet and "화면 상태가 반복" in str(orchestrator.stop_reason):
                    self._log("🧭 수집 기준 미충족 상태에서 화면 반복 감지: 즉시 컨텍스트 전환으로 복구 시도합니다.")
                    orchestrator.stop_reason = None
                    orchestrator.same_dom_count = 0
                    force_context_shift = True
                else:
                    return self._build_failure_result(
                        goal=goal,
                        steps=steps,
                        step_count=step_count,
                        start_time=start_time,
                        reason=orchestrator.stop_reason,
                    )
            if hasattr(orchestrator, "consume_oscillation") and bool(orchestrator.consume_oscillation()):
                self._log("🧭 화면 진동(ABAB) 패턴 감지: 컨텍스트 전환을 잠시 중단하고 직접 상호작용 후보를 우선 시도합니다.")
                force_context_shift = False
                context_shift_used_elements.clear()
                context_shift_fail_streak = 0
                context_shift_cooldown = max(
                    int(context_shift_cooldown),
                    self._loop_policy_value("context_shift_cooldown_steps", 4),
                )
                self._action_feedback.append(
                    "화면 상태가 ABAB로 반복되어 컨텍스트 전환을 중지합니다. "
                    "현재 리스트/모달의 직접 상호작용 후보를 우선 선택하세요."
                )
                if len(self._action_feedback) > 10:
                    self._action_feedback = self._action_feedback[-10:]

            detected_phase = self._infer_runtime_phase(dom_elements)
            guarded_phase = self._apply_phase_constraints(detected_phase)
            if guarded_phase != detected_phase:
                self._log(f"🧱 제약 가드: phase {detected_phase} -> {guarded_phase}")
            detected_phase = guarded_phase
            if detected_phase != self._runtime_phase:
                self._log(f"🔁 phase 전환: {self._runtime_phase} -> {detected_phase}")
            self._runtime_phase = detected_phase
            master_orchestrator.set_phase(detected_phase)

            before_signature = self._dom_progress_signature(dom_elements)
            heuristic_login_gate = self._is_login_gate(dom_elements)
            modal_open_hint = bool(self._last_snapshot_evidence.get("modal_open")) if isinstance(self._last_snapshot_evidence, dict) else False
            compact_auth_page = self._is_compact_auth_page(dom_elements)
            login_gate_visible = bool(
                heuristic_login_gate and (modal_open_hint or compact_auth_page)
            )
            if heuristic_login_gate and not login_gate_visible:
                self._log("ℹ️ 로그인 힌트는 감지됐지만 modal_open/compact_auth 조건이 없어 AUTH 분기를 보류합니다.")
            login_intervention = handle_login_intervention(
                agent=self,
                goal=goal,
                login_gate_visible=login_gate_visible,
                has_login_test_data=has_login_test_data,
                login_intervention_asked=login_intervention_asked,
            )
            has_login_test_data = bool(login_intervention.get("has_login_test_data", has_login_test_data))
            login_intervention_asked = bool(
                login_intervention.get("login_intervention_asked", login_intervention_asked)
            )
            if bool(login_intervention.get("aborted")):
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=str(login_intervention.get("reason") or "로그인 개입 요청이 거부되어 중단했습니다."),
                )

            # 2. 스크린샷 캡처
            screenshot = self._capture_screenshot()

            # 2.5 CAPTCHA 감지 및 자동 해결
            captcha_skip_until = int(getattr(self, "_captcha_solver_skip_until_step", 0) or 0)
            captcha_solver_allowed = (
                screenshot
                and not getattr(self, "_captcha_solver_skip", False)
                and int(step_count) >= captcha_skip_until
            )
            if captcha_solver_allowed:
                if not hasattr(self, "_captcha_solver"):
                    captcha_attempts = self._loop_policy_value("captcha_solver_attempt_limit", 2)
                    if captcha_attempts <= 0:
                        captcha_attempts = 2
                    self._captcha_solver = CaptchaSolver(
                        vision_client=self.llm,
                        execute_fn=self._execute_action,
                        mcp_host_url=self.mcp_host_url,
                        session_id=self.session_id,
                        max_attempts=captcha_attempts,
                        log_fn=self._log,
                    )
                captcha_result = self._captcha_solver.detect_and_handle(
                    screenshot=screenshot,
                    page_url=getattr(self, "_current_url", goal.start_url or ""),
                    capture_fn=self._capture_screenshot,
                )
                if captcha_result.solved:
                    self._log(f"🔓 CAPTCHA 해결 완료 ({captcha_result.attempts}회 시도)")
                    self._captcha_solver_skip_until_step = 0
                    self._action_history.append(
                        f"Step {step_count}: captcha_solve - CAPTCHA 자동 해결 ({captcha_result.status})"
                    )
                    time.sleep(1)
                    continue  # DOM 재수집 후 다음 스텝
                elif captcha_result.status == "gave_up":
                    self._log("🏳️ CAPTCHA 해결 포기 — 일반 LLM 흐름으로 계속")
                    cooldown_steps = self._loop_policy_value("captcha_solver_cooldown_steps", 4)
                    if cooldown_steps <= 0:
                        cooldown_steps = 4
                    self._captcha_solver_skip_until_step = int(step_count) + int(cooldown_steps)
                    self._action_feedback.append(
                        "CAPTCHA가 감지되었으나 자동 해결에 실패했습니다. "
                        "가능하면 CAPTCHA를 우회하는 경로를 찾거나, 사용자 개입이 필요합니다."
                    )
                    if len(self._action_feedback) > 10:
                        self._action_feedback = self._action_feedback[-10:]
            elif screenshot and int(step_count) < captcha_skip_until:
                self._log(
                    f"⏭️ CAPTCHA solver cooldown 적용 중(step<{captcha_skip_until}) — 일반 실행 흐름 유지"
                )
                # no_captcha 또는 unsupported → 일반 흐름 계속

            static_verification_reason = self._evaluate_static_verification_on_current_page(
                goal=goal,
                dom_elements=dom_elements,
            )
            if static_verification_reason:
                self._log(f"✅ 목표 달성! 이유: {static_verification_reason}")
                result = GoalResult(
                    goal_id=goal.id,
                    goal_name=goal.name,
                    success=True,
                    steps_taken=steps,
                    total_steps=max(0, len(steps)),
                    final_reason=static_verification_reason,
                    duration_seconds=time.time() - start_time,
                )
                self._record_goal_summary(
                    goal=goal,
                    status="success",
                    reason=result.final_reason,
                    step_count=result.total_steps,
                    duration_seconds=result.duration_seconds,
                )
                return result

            directive = orchestrator.next_directive(
                login_gate_visible=login_gate_visible,
                requires_login_interaction=requires_login_interaction,
                has_login_test_data=has_login_test_data,
                close_element_id=None,
            )
            master_directive = master_orchestrator.next_directive(
                auth_required=bool(login_gate_visible and not has_login_test_data)
            )

            if directive.kind == "stop":
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=directive.reason or "마스터 오케스트레이터가 실행을 중단했습니다.",
                )

            handoff_result = handle_master_handoff(
                agent=self,
                goal=goal,
                master_directive=master_directive,
                context_shift_fail_streak=context_shift_fail_streak,
                context_shift_cooldown=context_shift_cooldown,
                force_context_shift=force_context_shift,
            )
            force_context_shift = bool(handoff_result.get("force_context_shift", force_context_shift))
            if bool(handoff_result.get("aborted")):
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=str(handoff_result.get("abort_reason") or "사용자 요청으로 실행을 중단했습니다."),
                )

            if collect_unmet and collect_metric_stall_count >= 2 and context_shift_cooldown <= 0:
                force_context_shift = True
                self._action_feedback.append(
                    "수집 지표가 정체되어 페이지/탭/섹션 전환을 강제합니다."
                )
                if len(self._action_feedback) > 10:
                    self._action_feedback = self._action_feedback[-10:]

            context_shift_result = handle_forced_context_shift(
                agent=self,
                goal=goal,
                orchestrator=orchestrator,
                step_count=step_count,
                step_start=step_start,
                dom_elements=dom_elements,
                before_signature=before_signature,
                collect_unmet=collect_unmet,
                sub_agent=sub_agent,
                steps=steps,
                context_shift_used_elements=context_shift_used_elements,
                context_shift_fail_streak=context_shift_fail_streak,
                force_context_shift=force_context_shift,
                context_shift_cooldown=context_shift_cooldown,
                ineffective_action_streak=ineffective_action_streak,
            )
            force_context_shift = bool(context_shift_result.get("force_context_shift", force_context_shift))
            context_shift_fail_streak = int(
                context_shift_result.get("context_shift_fail_streak", context_shift_fail_streak)
            )
            context_shift_cooldown = int(
                context_shift_result.get("context_shift_cooldown", context_shift_cooldown)
            )
            ineffective_action_streak = int(
                context_shift_result.get("ineffective_action_streak", ineffective_action_streak)
            )
            if bool(context_shift_result.get("continue_loop")):
                continue

            deterministic_preplan = self._build_deterministic_goal_preplan(
                goal=goal,
                dom_elements=dom_elements,
                steps=steps,
            )
            if deterministic_preplan is not None:
                decision = deterministic_preplan
                self._log(f"규칙 기반 선결정: {decision.action.value} - {decision.reasoning}")
            else:
                # 3. LLM에게 다음 액션 결정 요청 (OpenClaw 철학 정렬: 계획은 LLM, 실행은 ref-only)
                memory_context = self._build_memory_context(goal)
                decision = self._decide_next_action(
                    dom_elements=dom_elements,
                    goal=goal,
                    screenshot=screenshot,
                    memory_context=memory_context,
                )
                self._log(f"LLM 결정: {decision.action.value} - {decision.reasoning}")

            if decision.action == ActionType.SCROLL:
                scroll_streak += 1
            else:
                scroll_streak = 0

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

            decision = self._enforce_goal_constraints_on_decision(decision, dom_elements)
            decision = self._apply_steering_policy_on_decision(
                goal=goal,
                decision=decision,
                dom_elements=dom_elements,
            )
            if bool(self._steering_infeasible_block):
                self._steering_infeasible_block = False
                callback_payload = {
                    "kind": "clarification",
                    "reason_code": "steering_infeasible",
                    "question": (
                        "스티어링 HARD 규칙으로 실행 후보가 없습니다. "
                        "/steer clear 또는 /handoff로 수정 후 /resume 하시겠습니까?"
                    ),
                    "fields": ["proceed", "instruction"],
                    "current_url": self._active_url,
                    "step": int(step_count),
                }
                callback_resp = self._request_user_intervention(callback_payload)
                proceed = self._to_bool(
                    (callback_resp or {}).get("proceed"),
                    default=False,
                ) if isinstance(callback_resp, dict) else False
                if proceed:
                    self._expire_steering_policy("steering_expired")
                    continue
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason="스티어링 정책 충돌로 사용자 개입이 필요합니다.",
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
            selected_element = None
            if decision.element_id is not None:
                selected_element = next((el for el in dom_elements if el.id == decision.element_id), None)
            selected_fields = []
            if selected_element is not None:
                selected_fields = [
                    selected_element.text,
                    selected_element.aria_label,
                    getattr(selected_element, "title", None),
                    self._element_full_selectors.get(selected_element.id),
                    self._element_selectors.get(selected_element.id),
                ]
            modal_open_now = bool(self._last_snapshot_evidence.get("modal_open")) if isinstance(self._last_snapshot_evidence, dict) else False
            overlay_intercept_pending = bool(getattr(self, "_overlay_intercept_pending", False))
            active_goal_text_norm = self._normalize_text(self._active_goal_text or "")
            x_button_goal_required = any(
                token in active_goal_text_norm
                for token in ("x 버튼", "x버튼", "우상단 x", "닫기 버튼", "close button", "x icon", "x 아이콘")
            )
            if x_button_goal_required and decision.action == ActionType.PRESS:
                modal_regions_hint = []
                if isinstance(self._last_snapshot_evidence, dict):
                    raw_regions = self._last_snapshot_evidence.get("modal_regions")
                    if isinstance(raw_regions, list):
                        modal_regions_hint = raw_regions
                modal_pick_for_x = self._pick_modal_unblock_element(
                    dom_elements,
                    self._element_full_selectors,
                    modal_regions_hint=modal_regions_hint,
                )
                if modal_pick_for_x is None:
                    modal_pick_for_x = self._pick_modal_unblock_element(
                        dom_elements,
                        self._element_selectors,
                        modal_regions_hint=modal_regions_hint,
                    )
                if modal_pick_for_x is not None:
                    decision = ActionDecision(
                        action=ActionType.CLICK,
                        element_id=modal_pick_for_x,
                        value=None,
                        reasoning=(
                            "목표가 X 버튼 닫기 검증을 요구하므로 key press 대신 모달 닫기 후보 클릭으로 강제 전환합니다. "
                            + str(decision.reasoning or "")
                        ).strip(),
                        confidence=max(float(decision.confidence or 0.0), 0.84),
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
                    selected_element = next((el for el in dom_elements if el.id == modal_pick_for_x), None)
                    selected_fields = []
                    if selected_element is not None:
                        selected_fields = [
                            selected_element.text,
                            selected_element.aria_label,
                            getattr(selected_element, "title", None),
                            self._element_full_selectors.get(selected_element.id),
                            self._element_selectors.get(selected_element.id),
                        ]
                    self._log("🧭 X 버튼 요구 목표: press 액션을 닫기 클릭으로 변환합니다.")
            selected_close_signal = any(self._contains_close_hint(field) for field in selected_fields)
            if not selected_close_signal and selected_element is not None:
                selected_close_signal = self._normalize_text(selected_element.text) in {"x", "×", "닫기", "close"}
            decision_reasoning_text = self._normalize_text(decision.reasoning)
            reasoning_close_intent = bool(
                any(
                    token in decision_reasoning_text
                    for token in (
                        "닫",
                        "close",
                        "dismiss",
                        "종료",
                        "x 버튼",
                        "우상단 x",
                    )
                )
            )
            if (
                overlay_intercept_pending
                and decision.action == ActionType.CLICK
                and not selected_close_signal
            ):
                modal_regions_hint = []
                if isinstance(self._last_snapshot_evidence, dict):
                    raw_regions = self._last_snapshot_evidence.get("modal_regions")
                    if isinstance(raw_regions, list):
                        modal_regions_hint = raw_regions
                modal_pick = self._pick_modal_unblock_element(
                    dom_elements,
                    self._element_full_selectors,
                    modal_regions_hint=modal_regions_hint,
                )
                if modal_pick is None:
                    modal_pick = self._pick_modal_unblock_element(
                        dom_elements,
                        self._element_selectors,
                        modal_regions_hint=modal_regions_hint,
                    )
                if modal_pick is not None and modal_pick != decision.element_id:
                    self._log("🧭 overlay intercept 감지: 배경 클릭을 중단하고 모달 닫기 후보로 강제 전환합니다.")
                    decision = ActionDecision(
                        action=ActionType.CLICK,
                        element_id=modal_pick,
                        value=None,
                        reasoning="배경 요소 클릭이 오버레이에 가로막혀 모달 닫기 후보로 강제 전환",
                        confidence=max(float(decision.confidence or 0.0), 0.86),
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
                    selected_element = next((el for el in dom_elements if el.id == modal_pick), None)
                    selected_fields = []
                    if selected_element is not None:
                        selected_fields = [
                            selected_element.text,
                            selected_element.aria_label,
                            getattr(selected_element, "title", None),
                            self._element_full_selectors.get(selected_element.id),
                            self._element_selectors.get(selected_element.id),
                        ]
                    selected_close_signal = any(self._contains_close_hint(field) for field in selected_fields)
                    if not selected_close_signal and selected_element is not None:
                        selected_close_signal = self._normalize_text(selected_element.text) in {"x", "×", "닫기", "close"}
                    reasoning_close_intent = True
            if (
                modal_open_now
                and decision.action == ActionType.CLICK
                and reasoning_close_intent
                and not selected_close_signal
            ):
                modal_regions_hint = []
                if isinstance(self._last_snapshot_evidence, dict):
                    raw_regions = self._last_snapshot_evidence.get("modal_regions")
                    if isinstance(raw_regions, list):
                        modal_regions_hint = raw_regions
                modal_pick = self._pick_modal_unblock_element(
                    dom_elements,
                    self._element_full_selectors,
                    modal_regions_hint=modal_regions_hint,
                )
                if modal_pick is None:
                    modal_pick = self._pick_modal_unblock_element(
                        dom_elements,
                        self._element_selectors,
                        modal_regions_hint=modal_regions_hint,
                    )
                if modal_pick is not None and modal_pick != decision.element_id:
                    decision = ActionDecision(
                        action=ActionType.CLICK,
                        element_id=modal_pick,
                        value=decision.value,
                        reasoning=(
                            "모달 닫기 의도가 감지되어 우상단/닫기 후보 ref로 재매핑합니다. "
                            + str(decision.reasoning or "")
                        ).strip(),
                        confidence=max(float(decision.confidence or 0.0), 0.82),
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
                    selected_element = next((el for el in dom_elements if el.id == modal_pick), None)
                    selected_fields = []
                    if selected_element is not None:
                        selected_fields = [
                            selected_element.text,
                            selected_element.aria_label,
                            getattr(selected_element, "title", None),
                            self._element_full_selectors.get(selected_element.id),
                            self._element_selectors.get(selected_element.id),
                        ]
                    selected_close_signal = any(self._contains_close_hint(field) for field in selected_fields)
                    if not selected_close_signal and selected_element is not None:
                        selected_close_signal = self._normalize_text(selected_element.text) in {"x", "×", "닫기", "close"}
                    self._log("🧭 모달 닫기 의도 보정: 닫기 후보 ref로 액션 대상을 재선택합니다.")
            close_like_click_intent = bool(
                decision.action == ActionType.CLICK
                and (
                    selected_close_signal
                    or (modal_open_now and reasoning_close_intent)
                )
            )
            if close_like_click_intent and not modal_open_now:
                self._log("🧭 모달이 열려있지 않아 close 클릭을 건너뛰고 재계획합니다.")
                self._action_feedback.append(
                    "현재 modal_open=false 상태입니다. 닫기 버튼 대신 현재 화면의 진행 가능 CTA를 선택하세요."
                )
                if len(self._action_feedback) > 10:
                    self._action_feedback = self._action_feedback[-10:]
                _ = self._analyze_dom()
                ineffective_action_streak = 0
                force_context_shift = False
                time.sleep(0.2)
                continue
            click_intent_key = self._build_click_intent_key(
                element=selected_element,
                full_selector=self._element_full_selectors.get(selected_element.id) if selected_element else None,
                selector=self._element_selectors.get(selected_element.id) if selected_element else None,
            )
            intent_fields = [str(v or "") for v in selected_fields if str(v or "").strip()]
            if decision.value:
                intent_fields.append(str(decision.value))
            if not intent_fields and decision.reasoning:
                intent_fields.append(str(decision.reasoning))
            action_intent_key = self._candidate_intent_key(decision.action.value, intent_fields)

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
                attempt_logs = (
                    self._last_exec_result.attempt_logs
                    if isinstance(getattr(self, "_last_exec_result", None), ActionExecResult)
                    else None
                )
                if isinstance(attempt_logs, list) and attempt_logs:
                    last_attempt = attempt_logs[-1] if isinstance(attempt_logs[-1], dict) else {}
                    if last_attempt:
                        self._log(
                            "↳ 실행 상세: "
                            f"mode={last_attempt.get('mode')}, "
                            f"reason_code={last_attempt.get('reason_code')}, "
                            f"error={last_attempt.get('error')}"
                        )
            if decision.action == ActionType.CLICK and decision.element_id is not None:
                self._recent_click_element_ids.append(int(decision.element_id))
                if len(self._recent_click_element_ids) > 24:
                    self._recent_click_element_ids = self._recent_click_element_ids[-24:]

            progress_eval = evaluate_post_action_progress(
                agent=self,
                goal=goal,
                decision=decision,
                success=success,
                before_signature=before_signature,
                dom_elements=dom_elements,
                step_count=step_count,
                steps=steps,
                start_time=start_time,
            )
            post_dom = progress_eval.get("post_dom") or []
            state_change = progress_eval.get("state_change")
            changed = bool(progress_eval.get("changed"))
            if isinstance(state_change, dict):
                changed = self._state_change_indicates_progress(state_change)
            terminal_result = progress_eval.get("terminal_result")
            if terminal_result is not None:
                return terminal_result

            if filter_goal_active and decision.action == ActionType.SELECT and bool(success):
                filter_semantic_attempts += 1
                selected_value_hint = str(decision.value or "").strip()
                if self._filter_validation_contract is None:
                    try:
                        self._filter_validation_contract = self._build_filter_validation_contract(
                            goal=goal,
                            dom_elements=post_dom if isinstance(post_dom, list) and post_dom else dom_elements,
                        )
                    except Exception as contract_exc:
                        self._log(f"⚠️ 필터 검증 계약 생성 실패: {contract_exc}")
                        self._filter_validation_contract = None
                semantic_report = self.run_filter_semantic_validation(
                    goal_text=goal.description,
                    max_pages=2,
                    max_cases=filter_semantic_max_cases,
                    use_current_selection_only=filter_semantic_current_only,
                    forced_selected_value=selected_value_hint,
                    validation_contract=(
                        self._filter_validation_contract
                        if isinstance(self._filter_validation_contract, dict)
                        else None
                    ),
                )
                if isinstance(semantic_report, dict):
                    self._last_filter_semantic_report = semantic_report
                    rc_summary = semantic_report.get("reason_code_summary")
                    if isinstance(rc_summary, dict):
                        for code, count in rc_summary.items():
                            try:
                                repeats = int(count)
                            except Exception:
                                repeats = 0
                            repeats = max(0, min(repeats, 50))
                            for _ in range(repeats):
                                self._record_reason_code(str(code))

                    summary = semantic_report.get("summary")
                    summary_dict = summary if isinstance(summary, dict) else {}
                    strict_failed = bool(summary_dict.get("strict_failed"))
                    goal_satisfied = bool(summary_dict.get("goal_satisfied", semantic_report.get("success")))

                    if strict_failed:
                        failed_mandatory = int(summary_dict.get("failed_mandatory_checks") or 0)
                        reason = (
                            "필터 의미 검증 실패: "
                            f"필수 체크 실패 {failed_mandatory}건"
                        )
                        self._log(f"❌ {reason}")
                        return self._build_failure_result(
                            goal=goal,
                            steps=steps,
                            step_count=step_count,
                            start_time=start_time,
                            reason=reason,
                        )

                    if goal_satisfied:
                        passed_checks = int(summary_dict.get("passed_checks") or 0)
                        total_checks = int(summary_dict.get("total_checks") or 0)
                        success_reason = f"필터 의미 검증 통과 ({passed_checks}/{total_checks})"
                        self._log(f"✅ {success_reason}")
                        result = GoalResult(
                            goal_id=goal.id,
                            goal_name=goal.name,
                            success=True,
                            steps_taken=steps,
                            total_steps=step_count,
                            final_reason=success_reason,
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
                    else:
                        required_count = int(summary_dict.get("required_option_count") or 0)
                        covered_count = int(summary_dict.get("covered_option_count") or 0)
                        self._log(
                            "🧪 필터 의미 검증 진행 중: "
                            f"옵션 커버리지 {covered_count}/{required_count}"
                        )
                        missing_options = semantic_report.get("missing_required_options")
                        if isinstance(missing_options, list) and missing_options:
                            labels: List[str] = []
                            for row in missing_options[:6]:
                                if not isinstance(row, dict):
                                    continue
                                label = str(row.get("text") or row.get("value") or "").strip()
                                if label:
                                    labels.append(label)
                            if labels:
                                self._action_feedback.append(
                                    "아직 검증되지 않은 필터 옵션: " + ", ".join(labels)
                                )
                                if len(self._action_feedback) > 10:
                                    self._action_feedback = self._action_feedback[-10:]

                if filter_semantic_attempts >= filter_semantic_attempt_limit:
                    reason = (
                        "필터 의미 검증 결과를 확보하지 못해 중단합니다. "
                        f"(select 시도 {filter_semantic_attempts}회)"
                    )
                    self._log(f"❌ {reason}")
                    return self._build_failure_result(
                        goal=goal,
                        steps=steps,
                        step_count=step_count,
                        start_time=start_time,
                        reason=reason,
                    )

            weak_only = (not changed) and self._state_change_is_weak(state_change)
            if changed:
                self._progress_counter += 1
                self._no_progress_counter = 0
                self._weak_progress_streak = 0
            else:
                self._no_progress_counter += 1
                if weak_only:
                    self._weak_progress_streak += 1
                    weak_limit = max(1, self._loop_policy_value("weak_progress_streak_limit", 3))
                    if self._weak_progress_streak >= weak_limit:
                        self._record_reason_code("weak_progress_only")
                        force_context_shift = True
                else:
                    self._weak_progress_streak = 0
            master_orchestrator.record_progress(
                changed=changed,
                signal={
                    "reason_code": self._last_exec_result.reason_code if self._last_exec_result else "unknown",
                    "phase": self._runtime_phase,
                    "step": step_count,
                },
            )
            self._record_action_feedback(
                step_number=step_count,
                decision=decision,
                success=success,
                changed=changed,
                error=error,
                reason_code=self._last_exec_result.reason_code if self._last_exec_result else None,
                state_change=state_change,
                intent_key=action_intent_key,
            )
            self._record_action_memory(
                goal=goal,
                step_number=step_count,
                decision=decision,
                success=success,
                changed=changed,
                error=error,
            )
            reason_code = self._last_exec_result.reason_code if self._last_exec_result else "unknown"
            if bool(success and changed):
                self._overlay_intercept_pending = False
            elif reason_code in {"not_actionable", "no_state_change"} and self._error_indicates_overlay_intercept(error):
                self._overlay_intercept_pending = True
                self._record_reason_code("overlay_intercept_detected")
            ref_used = self._last_exec_result.ref_id_used if self._last_exec_result else ""
            self._track_ref_outcome(
                ref_id=ref_used,
                reason_code=reason_code,
                success=success,
                changed=changed,
            )
            if action_intent_key:
                intent_soft_fail_streaks = getattr(self, "_intent_soft_fail_streaks", {}) or {}
                if success and changed:
                    intent_soft_fail_streaks.pop(action_intent_key, None)
                elif reason_code in {
                    "no_state_change",
                    "not_actionable",
                    "ambiguous_ref_target",
                    "blocked_ref_no_progress",
                }:
                    streak = int(intent_soft_fail_streaks.get(action_intent_key, 0)) + 1
                    intent_soft_fail_streaks[action_intent_key] = streak
                    if streak >= 2:
                        force_context_shift = True
                        intent_soft_fail_streaks[action_intent_key] = 0
                        self._action_feedback.append(
                            "같은 의도를 반복했지만 진행 신호가 없습니다. "
                            "다른 페이지/섹션/탭으로 전환한 뒤 다음 행동을 선택하세요."
                        )
                        if len(self._action_feedback) > 10:
                            self._action_feedback = self._action_feedback[-10:]
                else:
                    intent_soft_fail_streaks.pop(action_intent_key, None)
                self._intent_soft_fail_streaks = intent_soft_fail_streaks
            if (
                login_gate_visible
                and decision.action == ActionType.CLICK
                and reason_code in {"no_state_change", "not_actionable"}
                and self._has_duplicate_account_signal(state_change=state_change, dom_elements=post_dom)
            ):
                new_username = self._rotate_signup_identity(goal)
                if new_username:
                    self._log(
                        f"🪪 회원가입 아이디 중복 메시지 감지: username을 `{new_username}`로 갱신 후 재시도합니다."
                    )
                    self._action_feedback.append(
                        "회원가입 오류 감지: 아이디가 이미 사용 중입니다. username/email을 새 값으로 갱신했으니 "
                        "아이디 필드부터 다시 입력하세요."
                    )
                    if len(self._action_feedback) > 10:
                        self._action_feedback = self._action_feedback[-10:]
                    ineffective_action_streak = 0
                    force_context_shift = False
                    time.sleep(0.2)
                    continue
            recovery_result = handle_action_recovery(
                agent=self,
                goal=goal,
                decision=decision,
                success=success,
                changed=changed,
                reason_code=reason_code,
                login_gate_visible=login_gate_visible,
                has_login_test_data=has_login_test_data,
                post_dom=post_dom,
                force_context_shift=force_context_shift,
                ineffective_action_streak=ineffective_action_streak,
            )
            force_context_shift = bool(recovery_result.get("force_context_shift", force_context_shift))
            ineffective_action_streak = int(
                recovery_result.get("ineffective_action_streak", ineffective_action_streak)
            )
            if bool(recovery_result.get("continue_loop")):
                continue

            streak_result = update_action_streaks_and_loops(
                agent=self,
                goal=goal,
                decision=decision,
                success=success,
                changed=changed,
                click_intent_key=click_intent_key,
                scroll_streak=scroll_streak,
                ineffective_action_streak=ineffective_action_streak,
                force_context_shift=force_context_shift,
                context_shift_fail_streak=context_shift_fail_streak,
                context_shift_cooldown=context_shift_cooldown,
                steps=steps,
                step_count=step_count,
                start_time=start_time,
            )
            scroll_streak = int(streak_result.get("scroll_streak", scroll_streak))
            ineffective_action_streak = int(
                streak_result.get("ineffective_action_streak", ineffective_action_streak)
            )
            force_context_shift = bool(streak_result.get("force_context_shift", force_context_shift))
            context_shift_fail_streak = int(
                streak_result.get("context_shift_fail_streak", context_shift_fail_streak)
            )
            context_shift_cooldown = int(
                streak_result.get("context_shift_cooldown", context_shift_cooldown)
            )
            terminal_result = streak_result.get("terminal_result")
            if terminal_result is not None:
                return terminal_result

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
        last_error: Optional[str] = None
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    f"{self.mcp_host_url}/execute",
                    json={
                        "action": "browser_snapshot",
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
                    last_error = f"HTTP {response.status_code} - {detail}"
                    if attempt < 3:
                        self._record_reason_code("dom_snapshot_retry")
                        time.sleep(0.25 * attempt)
                        continue
                    self._log(f"DOM 분석 오류: {last_error}")
                    return []

                if "error" in data:
                    last_error = str(data.get("error") or "snapshot_error")
                    if attempt < 3:
                        self._record_reason_code("dom_snapshot_retry")
                        time.sleep(0.25 * attempt)
                        continue
                    self._log(f"DOM 분석 오류: {last_error}")
                    return []

                raw_elements = data.get("elements", []) or data.get("dom_elements", [])
                if not raw_elements and attempt < 3:
                    last_error = "empty_dom_elements"
                    self._record_reason_code("dom_snapshot_retry")
                    time.sleep(0.25 * attempt)
                    continue

                # 셀렉터 맵 초기화
                self._element_selectors = {}
                self._element_full_selectors = {}
                self._element_ref_ids = {}
                self._selector_to_ref_id = {}
                self._element_scopes = {}
                self._active_snapshot_id = str(data.get("snapshot_id") or "")
                self._active_dom_hash = str(data.get("dom_hash") or "")
                self._active_snapshot_epoch = int(data.get("epoch") or 0)
                self._active_url = str(data.get("url") or self._active_url or "")
                evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
                self._last_snapshot_evidence = evidence

                # DOMElement로 변환 (ID 부여)
                elements = []
                for idx, el in enumerate(raw_elements):
                    attrs = el.get("attributes", {})
                    disabled_attr = attrs.get("disabled")
                    disabled_flag = (
                        disabled_attr is not None
                        and str(disabled_attr).strip().lower() not in {"false", "0", "none"}
                    )
                    aria_disabled_flag = str(attrs.get("aria-disabled") or "").strip().lower() == "true"
                    gaia_disabled_flag = str(attrs.get("gaia-disabled") or "").strip().lower() == "true"
                    is_enabled = not (disabled_flag or aria_disabled_flag or gaia_disabled_flag)

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
                        if selector:
                            self._selector_to_ref_id[selector] = ref_id
                        if full_selector:
                            self._selector_to_ref_id[full_selector] = ref_id
                    if isinstance(scope, dict):
                        self._element_scopes[idx] = scope

                    elements.append(
                        DOMElement(
                            id=idx,
                            tag=el.get("tag", ""),
                            text=el.get("text", "")[:100],
                            role=attrs.get("role"),
                            type=attrs.get("type"),
                            placeholder=attrs.get("placeholder"),
                            aria_label=attrs.get("aria-label"),
                            aria_modal=attrs.get("aria-modal"),
                            title=attrs.get("title"),
                            class_name=attrs.get("class"),
                            href=attrs.get("href"),
                            bounding_box=el.get("bounding_box"),
                            options=attrs.get("options"),
                            selected_value=str(attrs.get("selected_value") or ""),
                            is_visible=bool(el.get("is_visible", True)),
                            is_enabled=is_enabled,
                        )
                    )
                return elements

            except Exception as e:
                last_error = str(e)
                if attempt < 3:
                    self._record_reason_code("dom_snapshot_retry")
                    time.sleep(0.25 * attempt)
                    continue
                self._log(f"DOM 분석 실패: {e}")
                return []

        if last_error:
            self._log(f"DOM 분석 실패: {last_error}")
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

    def run_filter_semantic_validation(
        self,
        goal_text: str,
        *,
        max_pages: int = 2,
        max_cases: int = 3,
        use_current_selection_only: bool = False,
        forced_selected_value: Optional[str] = None,
        validation_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Deterministic semantic validation for filter-style goals."""
        try:
            from .filter_validation_engine import build_filter_validation_config, run_filter_validation

            adapter = _GoalFilterValidationAdapter(self)
            report = run_filter_validation(
                adapter=adapter,
                goal_text=goal_text,
                config=build_filter_validation_config(
                    max_pages=max(1, int(max_pages)),
                    max_cases=max(1, int(max_cases)),
                    use_current_selection_only=bool(use_current_selection_only),
                    forced_selected_value=str(forced_selected_value or "").strip(),
                    validation_contract=dict(validation_contract or {}),
                ),
            )
            if isinstance(report, dict):
                return report
        except Exception as exc:
            self._log(f"⚠️ semantic filter validation 실패: {exc}")
        return {
            "mode": "filter_semantic_v2",
            "success": False,
            "summary": {
                "goal_type": "filter_validation_semantic",
                "total_checks": 1,
                "passed_checks": 0,
                "failed_checks": 1,
                "skipped_checks": 0,
                "failed_mandatory_checks": 1,
                "success_rate": 0.0,
                "strict_failed": True,
            },
            "checks": [
                {
                    "check_id": "filter_engine_error",
                    "name": "필터 의미 검증 엔진 실행",
                    "status": "fail",
                    "step": 1,
                    "action": "verify",
                    "input_value": "-",
                    "error": "semantic filter validation failed",
                    "check_type": "engine_error",
                    "mandatory": True,
                    "scope": "global",
                    "expected": "엔진 정상 실행",
                    "observed": "실패",
                    "evidence": {},
                }
            ],
            "rules_used": [],
            "pages_checked": 1,
            "cases": [],
            "failed_mandatory_count": 1,
            "reason_code_summary": {"filter_case_failed": 1},
        }

    def _build_filter_validation_contract(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
    ) -> Dict[str, Any]:
        option_rows: List[Dict[str, Any]] = []
        best_select_options: List[Dict[str, str]] = []
        best_score = -1.0
        for el in dom_elements:
            if self._normalize_text(el.tag) != "select":
                continue
            if not isinstance(el.options, list) or len(el.options) < 2:
                continue
            local_rows: List[Dict[str, str]] = []
            for item in el.options:
                if not isinstance(item, dict):
                    continue
                value = str(item.get("value") or "").strip()
                text = str(item.get("text") or "").strip()
                if not value:
                    continue
                lowered = self._normalize_text(f"{value} {text}")
                if any(tok in lowered for tok in ("전체", "all", "선택", "default")):
                    continue
                local_rows.append({"value": value, "text": text})
            if not local_rows:
                continue
            blob = self._normalize_text(
                " ".join(
                    [
                        str(el.text or ""),
                        str(el.aria_label or ""),
                        str(el.title or ""),
                        str(el.class_name or ""),
                        " ".join(str(x.get("text") or "") for x in local_rows[:8]),
                    ]
                )
            )
            score = float(len(local_rows))
            if "학점" in blob or "credit" in blob:
                score += 100.0
            if score > best_score:
                best_score = score
                best_select_options = local_rows

        option_rows = list(best_select_options)

        if not option_rows:
            return {
                "source": "fallback_empty",
                "required_options": [],
                "require_pagination_if_available": True,
            }

        prompt = f"""당신은 테스트 목표를 검증 계약(JSON)으로 변환하는 엔진입니다.
아래 목표를 보고, 검증해야 할 필터 옵션만 deterministic JSON으로 반환하세요.

목표:
{goal.description}

사용 가능한 옵션 목록(JSON):
{json.dumps(option_rows, ensure_ascii=False)}

반드시 다음 스키마만 반환:
{{
  "required_options": [{{"value":"...", "text":"..."}}],
  "require_pagination_if_available": true
}}

규칙:
1) required_options는 반드시 위 옵션 목록에 있는 값만 사용
2) 목표가 특정 옵션 집합(예: 1,2,3학점)을 요구하면 그 집합만 포함
3) 목표가 '전체/전부/모두/자세히 검증'이면 가능한 옵션을 모두 포함
4) 설명 문장/마크다운 없이 JSON만 반환
"""

        try:
            raw = self._call_llm_text_only(prompt)
            text = str(raw or "").strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if not text.startswith("{"):
                first = text.find("{")
                last = text.rfind("}")
                if first != -1 and last != -1 and last > first:
                    text = text[first : last + 1].strip()
            data = json.loads(text)
            if isinstance(data, dict):
                raw_required = data.get("required_options")
                sanitized: List[Dict[str, str]] = []
                value_allow = {str(row.get("value") or "").strip(): str(row.get("text") or "").strip() for row in option_rows}
                if isinstance(raw_required, list):
                    for item in raw_required:
                        if not isinstance(item, dict):
                            continue
                        val = str(item.get("value") or "").strip()
                        txt = str(item.get("text") or "").strip()
                        if val in value_allow:
                            sanitized.append({"value": val, "text": value_allow.get(val) or txt})
                if not sanitized:
                    sanitized = [{"value": str(row.get("value") or ""), "text": str(row.get("text") or "")} for row in option_rows]
                return {
                    "source": "llm_contract",
                    "required_options": sanitized,
                    "require_pagination_if_available": bool(data.get("require_pagination_if_available", True)),
                }
        except Exception as exc:
            self._log(f"⚠️ LLM 계약 파싱 실패, fallback 사용: {exc}")

        return {
            "source": "fallback_all_options",
            "required_options": [{"value": str(row.get("value") or ""), "text": str(row.get("text") or "")} for row in option_rows],
            "require_pagination_if_available": True,
        }

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
        recent_repeated = self._recent_click_element_ids[-8:]
        recent_block_text = (
            ", ".join(str(x) for x in recent_repeated)
            if recent_repeated
            else "없음"
        )
        signup_rule = ""
        if self._goal_mentions_signup(goal):
            signup_rule = """
5. **회원가입 목표 특별 규칙(강제)**
   - 회원가입 화면/모달 진입만으로는 절대 성공이 아닙니다.
   - 입력값 채움 + 제출 버튼 클릭 + 완료 신호(완료 문구/로그인 상태 변화) 확인 전까지 is_goal_achieved=false를 유지하세요.
"""
        constraint_rule = self._build_goal_constraint_prompt()

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

## 현재 실행 phase (참고)
- phase: {self._runtime_phase}
- AUTH=인증/로그인 처리, COLLECT=후보 수집, COMPOSE=조합/설정, APPLY=반영/실행, VERIFY=완료 검증
- phase는 가이드일 뿐이며, 실제 DOM/상태 변화 증거를 우선하세요.

## 사용 가능한 테스트 데이터
{json.dumps(goal.test_data, ensure_ascii=False, indent=2)}

## 지금까지 수행한 액션
{chr(10).join(self._action_history[-5:]) if self._action_history else '없음 (첫 번째 스텝)'}

## 최근 액션 실행 피드백
{chr(10).join(self._action_feedback[-5:]) if self._action_feedback else '없음'}

## 최근 반복 클릭 element_id (가능하면 회피)
{recent_block_text}

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
{constraint_rule}
6. **무효 액션 반복 금지**
   - 최근 실행 피드백에서 changed=false 또는 success=false인 액션/요소 조합은 반복하지 마세요.
   - 같은 요소를 2회 연속 클릭했는데 changed=false라면 다른 요소/전략을 선택하세요.
7. **컨텍스트 전환 규칙**
   - 같은 의도가 2회 이상 changed=false이면, 다음/페이지네이션/탭/필터/정렬 전환으로 화면 컨텍스트를 바꾼 뒤 다시 시도하세요.
   - 목표 단계 전환 CTA가 안 보일 때 `확장/더보기/show more/expand`는 **콘텐츠 영역 확장일 때만** 우선 선택하세요.
   - 목록형 페이지에서는 동일 카드 반복 클릭보다 다른 카드/다음 페이지 이동을 우선하세요.
   - 페이지네이션에서 "다음/next/›/»"가 보이면 숫자 페이지 버튼(1,2,3,4...)보다 우선 선택하세요.
   - 숫자 페이지 버튼만 반복 클릭하지 말고, 진행 정체 시 반드시 "다음"으로 넘어가세요.
8. **단계 전환 규칙(강제)**
   - 동일한 클릭 의도가 여러 번 연속 성공해도 목표가 완료되지 않으면, 다음 액션은 단계 전환 CTA를 우선 선택하세요.
   - 해당 CTA가 보이지 않으면 스크롤/탭 전환/다음 페이지 이동으로 CTA를 먼저 찾으세요.

## 응답 형식 (JSON만, 마크다운 없이)
{{
    "action": "click" | "fill" | "press" | "scroll" | "wait" | "select",
    "element_id": 요소ID (숫자),
    "value": "입력값 (fill), 키 이름 (press), select 값(문자열/콤마구분/JSON 배열), wait 조건(JSON 또는 ms)",
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
        phase = (self._runtime_phase or "COLLECT").upper()

        def _score(el: DOMElement) -> float:
            text = self._normalize_text(el.text)
            aria = self._normalize_text(el.aria_label)
            role = self._normalize_text(el.role)
            tag = self._normalize_text(el.tag)
            selector = self._element_full_selectors.get(el.id) or self._element_selectors.get(el.id) or ""
            fields = self._fields_for_element(el)

            has_progress = any(self._contains_progress_cta_hint(f) for f in fields)
            has_next = any(self._contains_next_pagination_hint(f) for f in fields)
            has_context = any(self._contains_context_shift_hint(f) for f in fields)
            has_expand = any(self._contains_expand_hint(f) for f in fields)
            has_wishlist_like = any(self._contains_wishlist_like_hint(f) for f in fields)
            has_add_like = any(self._contains_add_like_hint(f) for f in fields)
            has_login_hint = any(self._contains_login_hint(f) for f in fields)
            has_configure = any(self._contains_configure_hint(f) for f in fields)
            has_execute = any(self._contains_execute_hint(f) for f in fields)
            has_apply = any(self._contains_apply_hint(f) for f in fields)

            score = 0.0
            if has_progress:
                score += 6.0
            if has_next:
                score += 4.0
            if has_context:
                score += 3.0
            if has_login_hint:
                score += 2.0

            if role in {"button", "tab", "link", "menuitem"}:
                score += 2.5
            if tag in {"button", "a", "input", "select"}:
                score += 1.7

            normalized_selector = self._normalize_text(selector)
            if any(k in normalized_selector for k in ("pagination", "pager", "page", "tab", "tabs")):
                score += 2.0
            if any(k in normalized_selector for k in ("prev", "previous", "back", "이전")):
                score -= 4.0
            if any(k in normalized_selector for k in ("active", "current", "selected")):
                score -= 1.5
            if (self._is_numeric_page_label(el.text) or self._is_numeric_page_label(el.aria_label)) and not has_next:
                score -= 2.0

            if has_expand and not has_progress:
                score -= 2.0

            if phase in {"AUTH", "COLLECT"}:
                if has_add_like:
                    score += 4.0
                if has_progress:
                    score += 1.5
                if has_apply:
                    score -= 1.0
            elif phase == "COMPOSE":
                if has_configure:
                    score += 4.0
                if has_progress:
                    score += 2.5
                if has_add_like:
                    score -= 1.5
            elif phase == "APPLY":
                if has_execute or has_progress or has_apply:
                    score += 5.0
                if has_next:
                    score += 2.0
                if has_add_like:
                    score -= 2.5
            elif phase == "VERIFY":
                if has_apply or has_progress:
                    score += 5.5
                if has_add_like:
                    score -= 3.0

            score += self._selector_bias_for_fields(fields)
            score += 0.8 * self._adaptive_intent_bias(self._candidate_intent_key("click", fields))

            if text:
                score += min(2.5, len(text) / 18.0)

            recent_clicks = self._recent_click_element_ids[-10:]
            if recent_clicks:
                for offset, recent_id in enumerate(reversed(recent_clicks), start=1):
                    if recent_id == el.id:
                        score -= max(1.2, 4.5 - (offset * 0.45))
                        break
                repeat_count = recent_clicks.count(el.id)
                if repeat_count > 1:
                    score -= min(4.0, 0.9 * (repeat_count - 1))

            if self._last_dom_top_ids and el.id in recent_clicks:
                try:
                    previous_rank = self._last_dom_top_ids.index(el.id)
                except ValueError:
                    previous_rank = -1
                if 0 <= previous_rank < 5:
                    score -= max(1.0, 3.2 - (previous_rank * 0.5))

            return self._clamp_score(score, low=-25.0, high=35.0)

        ranked = sorted(elements, key=_score, reverse=True)
        self._last_dom_top_ids = [el.id for el in ranked[:12]]
        try:
            dom_limit = int(os.getenv("GAIA_LLM_DOM_LIMIT", "260"))
        except Exception:
            dom_limit = 260
        dom_limit = max(80, min(dom_limit, 800))
        selected: List[DOMElement] = ranked[:dom_limit]

        lines = []
        for el in selected:
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
            # select 요소의 option 목록 표시
            if el.tag == "select" and el.options:
                opt_strs = [f'{o.get("value","")}: {o.get("text","")}' for o in el.options[:10]]
                parts.append(f'options=[{" | ".join(opt_strs)}]')

            lines.append(" ".join(parts))

        if len(elements) > len(selected):
            lines.append(f"... ({len(elements) - len(selected)} more elements omitted)")
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
        if not text:
            return ActionDecision(
                action=ActionType.WAIT,
                reasoning="LLM 오류: empty_response_from_model",
                confidence=0.0,
            )

        # Codex CLI 로그가 앞에 붙을 수 있어 JSON 부분만 추출
        if not text.startswith("{"):
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last != -1 and last > first:
                text = text[first:last + 1].strip()

        try:
            data = json.loads(text)
            action_raw = str(data.get("action", "wait")).strip().lower()
            raw_value = data.get("value")
            normalized_value: Optional[str]
            if raw_value is None:
                normalized_value = None
            elif isinstance(raw_value, str):
                normalized_value = raw_value
            elif action_raw in {"wait", "select"} and isinstance(raw_value, (dict, list, int, float, bool)):
                normalized_value = json.dumps(raw_value, ensure_ascii=False)
            else:
                normalized_value = str(raw_value)

            return ActionDecision(
                action=ActionType(data.get("action", "wait")),
                element_id=data.get("element_id"),
                value=normalized_value,
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
        requires_ref = decision.action in {
            ActionType.CLICK,
            ActionType.FILL,
            ActionType.PRESS,
            ActionType.HOVER,
            ActionType.SCROLL,
            ActionType.SELECT,
        }
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
            if requires_ref and (not ref_id or not self._active_snapshot_id):
                _ = self._analyze_dom()
                selector = self._element_selectors.get(decision.element_id)
                full_selector = self._element_full_selectors.get(decision.element_id)
                ref_id = self._element_ref_ids.get(decision.element_id)
                if not ref_id:
                    selector_to_ref = getattr(self, "_selector_to_ref_id", {}) or {}
                    for candidate in (full_selector, selector):
                        if candidate:
                            mapped_ref = selector_to_ref.get(candidate)
                            if mapped_ref:
                                ref_id = mapped_ref
                                break
                if not ref_id or not self._active_snapshot_id:
                    self._last_exec_result = ActionExecResult(
                        success=False,
                        effective=False,
                        reason_code="ref_required",
                        reason=(
                            "Ref-only policy: 선택된 요소의 ref_id/snapshot_id가 없습니다. "
                            "최신 snapshot 재수집 후 다시 결정해야 합니다."
                        ),
                    )
                    return False, self._last_exec_result.as_error_message()
        selected_element = None
        if decision.element_id is not None:
            try:
                selected_element = next((el for el in dom_elements if el.id == decision.element_id), None)
            except Exception:
                selected_element = None

        element_actions = {
            ActionType.CLICK,
            ActionType.FILL,
            ActionType.PRESS,
            ActionType.HOVER,
            ActionType.SCROLL,
            ActionType.SELECT,
        }
        retriable_reason_codes = {
            "snapshot_not_found",
            "stale_snapshot",
            "ref_required",
            "not_found",
            "ambiguous_ref_target",
            "no_state_change",
            "not_actionable",
        }

        def _refresh_ref_binding() -> None:
            nonlocal selector, full_selector, ref_id
            _ = self._analyze_dom()
            selector_to_ref = getattr(self, "_selector_to_ref_id", {}) or {}
            if decision.element_id is not None:
                selector = self._element_selectors.get(decision.element_id) or selector
                full_selector = self._element_full_selectors.get(decision.element_id) or full_selector
                ref_id = self._element_ref_ids.get(decision.element_id) or ref_id
            if not ref_id:
                for candidate in (full_selector, selector):
                    if candidate:
                        mapped_ref = selector_to_ref.get(candidate)
                        if mapped_ref:
                            ref_id = mapped_ref
                            break

        def _execute_with_ref_recovery(
            action_name: str,
            action_value: Optional[str] = None,
        ) -> tuple[bool, Optional[str]]:
            nonlocal selector, full_selector, ref_id
            self._last_exec_result = self._execute_action(
                action_name,
                selector=selector,
                full_selector=full_selector,
                ref_id=ref_id,
                value=action_value,
            )
            should_retry = (
                decision.action in element_actions
                and self._last_exec_result.reason_code in retriable_reason_codes
            )
            if should_retry:
                prev_snapshot = self._active_snapshot_id
                prev_ref = ref_id or ""
                _refresh_ref_binding()
                if ref_id and self._active_snapshot_id:
                    self._last_exec_result = self._execute_action(
                        action_name,
                        selector=selector,
                        full_selector=full_selector,
                        ref_id=ref_id,
                        value=action_value,
                    )
                    if (
                        self._last_exec_result.success
                        and self._last_exec_result.effective
                        and (prev_snapshot != self._active_snapshot_id or prev_ref != (ref_id or ""))
                    ):
                        self._log("♻️ stale/ref 오류 복구: 최신 snapshot/ref 재매핑 후 재시도 성공")
            return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

        try:
            if decision.action in {
                ActionType.CLICK,
                ActionType.FILL,
                ActionType.PRESS,
                ActionType.HOVER,
                ActionType.SCROLL,
                ActionType.SELECT,
            } and decision.element_id is None:
                self._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="missing_element_id",
                    reason=f"{decision.action.value} 액션에는 element_id가 필요함",
                )
                return False, f"{decision.action.value} 액션에는 element_id가 필요함"
            if decision.action == ActionType.CLICK and selected_element is not None and not self._goal_allows_logout():
                logout_fields = [
                    selected_element.text,
                    selected_element.aria_label,
                    selected_element.title,
                    selector,
                    full_selector,
                ]
                if any(self._contains_logout_hint(field) for field in logout_fields):
                    self._last_exec_result = ActionExecResult(
                        success=False,
                        effective=False,
                        reason_code="blocked_logout_action",
                        reason="목표와 무관한 로그아웃 액션을 차단했습니다.",
                    )
                    return False, self._last_exec_result.as_error_message()
            if decision.action in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS} and self._is_ref_temporarily_blocked(ref_id):
                self._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="blocked_ref_no_progress",
                    reason=(
                        "같은 ref에서 상태 변화 없는 실패가 반복되어 임시 차단했습니다. "
                        "다른 요소/페이지 전환을 시도합니다."
                    ),
                    ref_id_used=ref_id or "",
                )
                return False, self._last_exec_result.as_error_message()

            if decision.action == ActionType.CLICK:
                click_value: Any = decision.value
                reasoning_norm = self._normalize_text(decision.reasoning)
                if any(
                    token in reasoning_norm
                    for token in ("닫", "close", "dismiss", "x 버튼", "우상단 x")
                ):
                    click_value = "__close_intent__"
                return _execute_with_ref_recovery("click", action_value=click_value)

            elif decision.action == ActionType.FILL:
                if not decision.value:
                    self._last_exec_result = ActionExecResult(
                        success=False,
                        effective=False,
                        reason_code="invalid_input",
                        reason="fill 액션에 value가 필요함",
                    )
                    return False, "fill 액션에 value가 필요함"
                return _execute_with_ref_recovery("fill", action_value=decision.value)

            elif decision.action == ActionType.PRESS:
                # press 액션은 키보드 입력 (Enter, Tab 등)
                key = decision.value or "Enter"
                return _execute_with_ref_recovery("press", action_value=key)

            elif decision.action == ActionType.SCROLL:
                scroll_value = decision.value or "down"
                return _execute_with_ref_recovery("scroll", action_value=scroll_value)

            elif decision.action == ActionType.SELECT:
                if not decision.value:
                    self._last_exec_result = ActionExecResult(
                        success=False,
                        effective=False,
                        reason_code="invalid_input",
                        reason="select 액션에 value(values)가 필요함",
                    )
                    return False, "select 액션에 value(values)가 필요함"
                return _execute_with_ref_recovery("select", action_value=decision.value)

            elif decision.action == ActionType.WAIT:
                wait_value = decision.value
                if wait_value is None or (isinstance(wait_value, str) and not wait_value.strip()):
                    wait_value = {"timeMs": 700}
                self._last_exec_result = self._execute_action("wait", value=wait_value)
                return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

            elif decision.action == ActionType.NAVIGATE:
                self._last_exec_result = self._execute_action("goto", url=decision.value)
                return bool(self._last_exec_result.success and self._last_exec_result.effective), self._last_exec_result.as_error_message()

            elif decision.action == ActionType.HOVER:
                return _execute_with_ref_recovery("hover")

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
        values: Optional[List[str]] = None,
        url: Optional[str] = None,
    ) -> ActionExecResult:
        """MCP Host를 통해 액션 실행"""

        use_ref_protocol = bool(
            ref_id
            and self._active_snapshot_id
            and action in {"click", "fill", "press", "hover", "scroll", "scrollIntoView", "select"}
        )
        is_element_action = action in {
            "click",
            "fill",
            "press",
            "hover",
            "scroll",
            "scrollIntoView",
            "select",
            "dragAndDrop",
            "dragSlider",
        }
        if is_element_action and not use_ref_protocol:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="ref_required",
                reason="Ref-only policy: snapshot_id + ref_id가 필요합니다.",
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
            if action == "select":
                parsed_values = values or parse_multi_values(value)
                if not parsed_values:
                    return ActionExecResult(
                        success=False,
                        effective=False,
                        reason_code="invalid_input",
                        reason="select 액션에는 values가 필요합니다.",
                    )
                params["values"] = parsed_values
                params["value"] = parsed_values if len(parsed_values) > 1 else parsed_values[0]
            elif value is not None:
                params["value"] = value
            request_action = "browser_act"
        else:
            if action == "wait":
                wait_payload = parse_wait_payload(value)
                simple_wait_only = bool(wait_payload) and set(wait_payload.keys()).issubset({"time_ms", "timeMs"})
                if simple_wait_only:
                    wait_ms = wait_payload.get("time_ms", wait_payload.get("timeMs", 1000))
                    try:
                        wait_ms = max(0, int(wait_ms))
                    except Exception:
                        wait_ms = 1000
                    params = {
                        "session_id": self.session_id,
                        "action": "wait",
                        "value": wait_ms,
                        "url": url or "",
                    }
                    request_action = "browser_act"
                else:
                    params = {"session_id": self.session_id}
                    params.update(wait_payload)
                    request_action = "browser_wait"
            else:
                params = {
                    "session_id": self.session_id,
                    "action": action,
                    "url": url or "",
                    "selector": full_selector or selector or "",
                }
                if value is not None:
                    params["value"] = value
                if action == "goto" and url:
                    params["value"] = url
                request_action = "browser_act"

        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": request_action,
                    "params": params,
                },
                timeout=(10, 120),
            )
            try:
                data = response.json()
            except Exception:
                data = {"error": response.text or "invalid_json_response"}

            if response.status_code >= 400:
                status_family = "http_4xx" if 400 <= response.status_code < 500 else "http_5xx"
                detail_raw = data.get("detail")
                if isinstance(detail_raw, dict):
                    reason_code, detail = extract_reason_fields(
                        {"detail": detail_raw},
                        response.status_code,
                    )
                else:
                    reason_code = status_family
                    detail = str(data.get("detail") or data.get("error") or response.reason or "HTTP error")
                attempt_logs = data.get("attempt_logs") if isinstance(data.get("attempt_logs"), list) else []
                retry_path = data.get("retry_path") if isinstance(data.get("retry_path"), list) else []
                attempt_count = int(data.get("attempt_count") or len(attempt_logs) or 0)
                return ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code=reason_code,
                    reason=detail,
                    state_change={},
                    attempt_logs=attempt_logs,
                    retry_path=retry_path,
                    attempt_count=attempt_count,
                    snapshot_id_used=str(data.get("snapshot_id_used") or ""),
                    ref_id_used=str(data.get("ref_id_used") or ""),
                )

            is_success = bool(data.get("success"))
            is_effective = bool(data.get("effective", True))
            attempt_logs = data.get("attempt_logs")
            retry_path = data.get("retry_path")
            attempt_count = int(
                data.get("attempt_count")
                or (len(attempt_logs) if isinstance(attempt_logs, list) else 0)
                or 0
            )
            if is_success and is_effective:
                return ActionExecResult(
                    success=True,
                    effective=True,
                    reason_code="ok",
                    reason="ok",
                    state_change=data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
                    attempt_logs=attempt_logs if isinstance(attempt_logs, list) else [],
                    retry_path=retry_path if isinstance(retry_path, list) else [],
                    attempt_count=attempt_count,
                    snapshot_id_used=str(data.get("snapshot_id_used") or ""),
                    ref_id_used=str(data.get("ref_id_used") or ""),
                )

            reason_code, reason = extract_reason_fields(data, response.status_code)
            if reason_code in {"snapshot_not_found", "stale_snapshot", "ambiguous_ref_target", "ambiguous_selector"}:
                reason = (
                    f"{reason} | 최신 snapshot/ref로 다시 시도해야 합니다."
                    if reason
                    else "최신 snapshot/ref로 다시 시도해야 합니다."
                )
            if isinstance(attempt_logs, list) and attempt_logs:
                reason = f"{reason} (attempts={len(attempt_logs)})"
            return ActionExecResult(
                success=is_success,
                effective=is_effective,
                reason_code=reason_code,
                reason=reason,
                state_change=data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
                attempt_logs=attempt_logs if isinstance(attempt_logs, list) else [],
                retry_path=retry_path if isinstance(retry_path, list) else [],
                attempt_count=attempt_count,
                snapshot_id_used=str(data.get("snapshot_id_used") or ""),
                ref_id_used=str(data.get("ref_id_used") or ""),
            )

        except Exception as e:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="request_exception",
                reason=add_no_retry_hint(str(e)),
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
