"""
Goal-Driven Agent

목표만 주면 AI가 알아서 DOM을 분석하고 다음 액션을 결정하여 실행
사전 정의된 스텝 없이 동적으로 테스트 수행
"""

from __future__ import annotations
import time
import os
import re
import requests
from typing import Any, Dict, List, Optional, Callable
from types import SimpleNamespace

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
    estimate_summary_counter_from_dom as estimate_summary_counter_from_dom_impl,
)
from .evidence_bundle import EvidenceBundle
from .goal_semantics import GoalSemantics
from .goal_policy_helpers import (
    build_goal_policy_evidence_bundle as build_goal_policy_evidence_bundle_impl,
    goal_destination_terms as goal_destination_terms_impl,
    goal_quoted_terms as goal_quoted_terms_impl,
    goal_target_terms as goal_target_terms_impl,
    run_goal_policy_closer as run_goal_policy_closer_impl,
)
from .goal_completion_helpers import (
    evaluate_destination_region_completion as evaluate_destination_region_completion_impl,
    evaluate_goal_target_completion as evaluate_goal_target_completion_impl,
    evaluate_reasoning_only_wait_completion as evaluate_reasoning_only_wait_completion_impl,
    evaluate_explicit_reasoning_proof_completion as evaluate_explicit_reasoning_proof_completion_impl,
    evaluate_wait_goal_completion as evaluate_wait_goal_completion_impl,
)
from .goal_verification_helpers import (
    build_verification_transition_reason as build_verification_transition_reason_impl,
    can_finish_by_verification_transition as can_finish_by_verification_transition_impl,
    evaluate_static_verification_on_current_page as evaluate_static_verification_on_current_page_impl,
    extract_goal_query_tokens as extract_goal_query_tokens_impl,
    is_filter_style_goal as is_filter_style_goal_impl,
    is_verification_style_goal as is_verification_style_goal_impl,
)
from .filter_validation_runtime import (
    build_filter_validation_contract as build_filter_validation_contract_impl,
    run_filter_semantic_validation as run_filter_semantic_validation_impl,
)
from .deterministic_goal_preplan import build_deterministic_goal_preplan as build_deterministic_goal_preplan_impl
from .dom_prompt_formatting import (
    context_match_tokens as context_match_tokens_impl,
    context_score as context_score_impl,
    fields_for_element as fields_for_element_impl,
    format_dom_for_llm as format_dom_for_llm_impl,
    truncate_for_prompt as truncate_for_prompt_impl,
)
from .goal_achievement_runtime import (
    dom_contains_any_hint as dom_contains_any_hint_impl,
    goal_mentions_signup as goal_mentions_signup_impl,
    goal_text_blob as goal_text_blob_impl,
    has_signup_completion_evidence as has_signup_completion_evidence_impl,
    validate_goal_achievement_claim as validate_goal_achievement_claim_impl,
)
from .steering_runtime import (
    activate_steering_policy as activate_steering_policy_impl,
    build_goal_constraint_prompt as build_goal_constraint_prompt_impl,
    build_steering_prompt as build_steering_prompt_impl,
)
from .failure_runtime import (
    build_constraint_failure_reason as build_constraint_failure_reason_impl,
    build_failure_result as build_failure_result_impl,
    record_reason_code as record_reason_code_impl,
)
from .llm_decision_runtime import decide_next_action as decide_next_action_impl
from .post_action_runtime import handle_post_action_runtime
from .action_execution_runtime import (
    execute_action as execute_action_impl,
    execute_decision as execute_decision_impl,
)
from .action_intent_runtime import (
    adaptive_intent_bias as adaptive_intent_bias_impl,
    build_click_intent_key as build_click_intent_key_impl,
    candidate_intent_key as candidate_intent_key_impl,
    normalize_selector_key as normalize_selector_key_impl,
    selector_bias_for_fields as selector_bias_for_fields_impl,
    squash_text as squash_text_impl,
    update_intent_stats as update_intent_stats_impl,
)
from .decision_parsing_runtime import parse_decision as parse_decision_impl
from .ref_tracking_runtime import (
    is_ref_temporarily_blocked as is_ref_temporarily_blocked_impl,
    track_ref_outcome as track_ref_outcome_impl,
)
from .agent_intervention_runtime import (
    has_login_test_data as has_login_test_data_impl,
    merge_test_data as merge_test_data_impl,
    request_goal_clarification as request_goal_clarification_impl,
    request_login_intervention as request_login_intervention_impl,
    request_user_intervention as request_user_intervention_impl,
    to_bool as to_bool_impl,
)
from .agent_memory_runtime import (
    build_memory_context as build_memory_context_impl,
    dom_progress_signature as dom_progress_signature_impl,
    extract_domain as extract_domain_impl,
    record_action_feedback as record_action_feedback_impl,
    record_action_memory as record_action_memory_impl,
    record_goal_summary as record_goal_summary_impl,
    record_recovery_hints as record_recovery_hints_impl,
)
from .heuristic_candidate_selectors import (
    is_progress_transition_element as is_progress_transition_element_impl,
    pick_collect_context_shift_element as pick_collect_context_shift_element_impl,
    pick_collect_element as pick_collect_element_impl,
    pick_context_shift_element as pick_context_shift_element_impl,
    pick_context_target_click_candidate as pick_context_target_click_candidate_impl,
    pick_no_navigation_click_candidate as pick_no_navigation_click_candidate_impl,
)
from .goal_policy_runtime import initialize_goal_policy_runtime, resolve_goal_policy_interrupts
from .goal_execution_setup_runtime import (
    build_success_goal_result as build_success_goal_result_impl,
    initialize_goal_execution_state as initialize_goal_execution_state_impl,
    log_goal_start as log_goal_start_impl,
    prepare_memory_episode as prepare_memory_episode_impl,
)
from .goal_constraint_runtime import (
    apply_steering_policy_on_decision as apply_steering_policy_on_decision_impl,
    enforce_goal_constraints_on_decision as enforce_goal_constraints_on_decision_impl,
)
from .steering_decision_runtime import (
    apply_steering_assertions_on_decision as apply_steering_assertions_on_decision_impl,
    capture_screenshot as capture_screenshot_impl,
    decision_steering_tags as decision_steering_tags_impl,
    element_steering_tags as element_steering_tags_impl,
    evaluate_steering_assertions as evaluate_steering_assertions_impl,
    expire_steering_policy as expire_steering_policy_impl,
    is_steering_context_valid as is_steering_context_valid_impl,
    pick_steering_candidate as pick_steering_candidate_impl,
)
from .modal_runtime import (
    pick_login_modal_close_element as pick_login_modal_close_element_impl,
    pick_modal_unblock_element as pick_modal_unblock_element_impl,
)
from .goal_dom_runtime import analyze_dom as analyze_dom_impl
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
    is_collect_constraint_unmet as is_collect_constraint_unmet_impl,
)
from .execute_goal_context_shift import handle_forced_context_shift
from .execute_goal_handoff import handle_master_handoff
from .runtime import (
    ActionExecResult,
    FlowMasterOrchestrator,
    StepSubAgent,
)
from gaia.src.phase4.captcha_solver import CaptchaSolver
from gaia.src.phase4.memory.retriever import MemoryRetriever
from gaia.src.phase4.memory.store import MemoryStore
from gaia.src.phase4.orchestrator import MasterOrchestrator
from .text_llm_runtime import call_llm_text_only as call_llm_text_only_impl


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
        self._goal_semantics: Optional[GoalSemantics] = None
        self._goal_policy: Any = None
        self._goal_policy_phase: str = ""
        self._goal_policy_baseline_evidence: Optional[EvidenceBundle] = None
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
        record_reason_code_impl(self, code)

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
        recovered = recover_dom_after_empty_impl(
            runtime_phase=self._runtime_phase,
            no_progress_counter=self._no_progress_counter,
            goal_start_url=str(getattr(goal, "start_url", "") or ""),
            analyze_dom_fn=self._analyze_dom,
            log_fn=self._log,
            execute_action_fn=lambda start_url: self._execute_action("goto", url=start_url),
        )
        if recovered:
            return recovered
        return self._force_reset_session_after_empty_dom(goal)

    def _force_reset_session_after_empty_dom(self, goal: "TestGoal") -> List["DOMElement"]:
        start_url = str(getattr(goal, "start_url", "") or "").strip()
        self._log("🛠️ DOM 강제 복구: 현재 브라우저 세션을 재생성합니다.")
        try:
            requests.post(
                f"{self.mcp_host_url.rstrip('/')}/close_session",
                json={
                    "action": "close_session",
                    "params": {
                        "session_id": self.session_id,
                    },
                },
                timeout=(5, 15),
            )
        except Exception as exc:
            self._log(f"⚠️ 세션 재생성 중 close_session 요청 실패: {exc}")
        time.sleep(0.3)
        if start_url:
            try:
                self._last_exec_result = self._execute_action("goto", url=start_url)
            except Exception as exc:
                self._log(f"⚠️ 세션 재생성 후 시작 URL 복구 실패: {exc}")
        time.sleep(0.8)
        try:
            recovered = self._analyze_dom()
        except Exception as exc:
            self._log(f"⚠️ 세션 재생성 후 DOM 재분석 실패: {exc}")
            recovered = []
        if recovered:
            self._record_reason_code("dom_session_reset")
        return recovered

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
        dom_value = estimate_goal_metric_from_dom_impl(
            dom_elements,
            self._goal_constraints,
            self._normalize_text,
        )
        evidence = self._last_snapshot_evidence if isinstance(self._last_snapshot_evidence, dict) else {}
        evidence_fragments: List[str] = []
        text_digest = str(evidence.get("text_digest") or "").strip()
        if text_digest:
            evidence_fragments.append(text_digest)
        live_texts = evidence.get("live_texts") if isinstance(evidence.get("live_texts"), list) else []
        for item in live_texts[:8]:
            text = str(item or "").strip()
            if text:
                evidence_fragments.append(text)
        evidence_value: Optional[int] = None
        if evidence_fragments:
            pseudo_elements = [
                SimpleNamespace(text=fragment, aria_label="", title="", placeholder="")
                for fragment in evidence_fragments
            ]
            evidence_value, _ = estimate_summary_counter_from_dom_impl(
                pseudo_elements,
                self._goal_constraints,
                self._normalize_text,
            )
        if dom_value is None:
            return float(evidence_value) if evidence_value is not None else None
        if evidence_value is None:
            return dom_value
        return float(max(float(dom_value), float(evidence_value)))

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
        return is_progress_transition_element_impl(self, el)

    def _pick_collect_element(self, dom_elements: List[DOMElement]) -> Optional[tuple[int, str]]:
        return pick_collect_element_impl(self, dom_elements)

    def _pick_collect_context_shift_element(
        self,
        dom_elements: List[DOMElement],
        used_element_ids: set[int],
    ) -> Optional[tuple[int, str, str]]:
        return pick_collect_context_shift_element_impl(self, dom_elements, used_element_ids)

    def _pick_no_navigation_click_candidate(
        self,
        dom_elements: List[DOMElement],
        *,
        excluded_ids: Optional[set[int]] = None,
    ) -> Optional[tuple[int, str]]:
        return pick_no_navigation_click_candidate_impl(
            self,
            dom_elements,
            excluded_ids=excluded_ids,
        )

    def _build_goal_constraint_prompt(self) -> str:
        return build_goal_constraint_prompt_impl(self)

    def _build_steering_prompt(self) -> str:
        return build_steering_prompt_impl(self)

    def _activate_steering_policy(self, goal: TestGoal) -> None:
        activate_steering_policy_impl(self, goal)

    def _expire_steering_policy(self, code: str = "steering_expired") -> None:
        expire_steering_policy_impl(self, code=code)

    def _is_steering_context_valid(self, goal: TestGoal) -> bool:
        return is_steering_context_valid_impl(self, goal)

    def _element_steering_tags(self, element: DOMElement) -> set[str]:
        return element_steering_tags_impl(self, element)

    def _decision_steering_tags(
        self,
        decision: ActionDecision,
        selected_element: Optional[DOMElement],
    ) -> set[str]:
        return decision_steering_tags_impl(self, decision, selected_element)

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
        return evaluate_steering_assertions_impl(self, assertions, dom_elements)

    def _apply_steering_assertions_on_decision(
        self,
        decision: ActionDecision,
        policy: Dict[str, Any],
        dom_elements: List[DOMElement],
    ) -> ActionDecision:
        return apply_steering_assertions_on_decision_impl(
            self,
            decision,
            policy,
            dom_elements,
        )

    def _pick_steering_candidate(
        self,
        dom_elements: List[DOMElement],
        *,
        prefer_tags: set[str],
        forbid_tags: set[str],
        target_tokens: List[str],
    ) -> Optional[int]:
        return pick_steering_candidate_impl(
            self,
            dom_elements,
            prefer_tags=prefer_tags,
            forbid_tags=forbid_tags,
            target_tokens=target_tokens,
        )

    def _apply_steering_policy_on_decision(
        self,
        *,
        goal: TestGoal,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> ActionDecision:
        return apply_steering_policy_on_decision_impl(
            self,
            goal=goal,
            decision=decision,
            dom_elements=dom_elements,
        )

    def _enforce_goal_constraints_on_decision(
        self,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> ActionDecision:
        return enforce_goal_constraints_on_decision_impl(self, decision, dom_elements)

    def _pick_context_target_click_candidate(
        self,
        dom_elements: List[DOMElement],
        excluded_ids: Optional[set[int]] = None,
    ) -> Optional[tuple[int, str]]:
        return pick_context_target_click_candidate_impl(
            self,
            dom_elements,
            excluded_ids=excluded_ids,
        )

    def _constraint_failure_reason(self) -> Optional[str]:
        return build_constraint_failure_reason_impl(self)

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
        return is_ref_temporarily_blocked_impl(self, ref_id)

    def _track_ref_outcome(
        self,
        *,
        ref_id: Optional[str],
        reason_code: str,
        success: bool,
        changed: bool,
    ) -> None:
        track_ref_outcome_impl(
            self,
            ref_id=ref_id,
            reason_code=reason_code,
            success=success,
            changed=changed,
        )

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
        return is_verification_style_goal_impl(self, goal)

    def _is_filter_style_goal(self, goal: TestGoal) -> bool:
        return is_filter_style_goal_impl(self, goal)

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
        return can_finish_by_verification_transition_impl(
            self,
            goal=goal,
            decision=decision,
            success=success,
            changed=changed,
            state_change=state_change,
            before_dom_count=before_dom_count,
            after_dom_count=after_dom_count,
        )

    def _build_verification_transition_reason(
        self,
        *,
        state_change: Optional[Dict[str, Any]],
        before_dom_count: int,
        after_dom_count: int,
    ) -> str:
        return build_verification_transition_reason_impl(
            self,
            state_change=state_change,
            before_dom_count=before_dom_count,
            after_dom_count=after_dom_count,
        )

    def _evaluate_static_verification_on_current_page(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
    ) -> Optional[str]:
        return evaluate_static_verification_on_current_page_impl(
            self,
            goal=goal,
            dom_elements=dom_elements,
        )

    def _extract_goal_query_tokens(self, goal: TestGoal) -> List[str]:
        return extract_goal_query_tokens_impl(self, goal)

    def _build_deterministic_goal_preplan(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
        steps: Optional[List[StepResult]] = None,
    ) -> Optional[ActionDecision]:
        return build_deterministic_goal_preplan_impl(
            self,
            goal=goal,
            dom_elements=dom_elements,
            steps=steps,
        )

    def _goal_quoted_terms(self, goal: TestGoal) -> List[str]:
        return goal_quoted_terms_impl(self, goal)

    def _goal_target_terms(self, goal: TestGoal) -> List[str]:
        return goal_target_terms_impl(self, goal)

    def _goal_destination_terms(self, goal: TestGoal) -> List[str]:
        return goal_destination_terms_impl(self, goal)

    def _build_goal_policy_evidence_bundle(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
        auth_prompt_visible: bool = False,
        modal_open: bool = False,
    ) -> Optional[EvidenceBundle]:
        return build_goal_policy_evidence_bundle_impl(
            self,
            goal=goal,
            dom_elements=dom_elements,
            auth_prompt_visible=auth_prompt_visible,
            modal_open=modal_open,
        )

    def _run_goal_policy_closer(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
        auth_prompt_visible: bool = False,
        modal_open: bool = False,
    ) -> Optional[str]:
        return run_goal_policy_closer_impl(
            self,
            goal=goal,
            dom_elements=dom_elements,
            auth_prompt_visible=auth_prompt_visible,
            modal_open=modal_open,
        )

    def _evaluate_destination_region_completion(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
    ) -> Optional[str]:
        return evaluate_destination_region_completion_impl(
            self,
            goal=goal,
            dom_elements=dom_elements,
        )

    def _evaluate_goal_target_completion(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
    ) -> Optional[str]:
        return evaluate_goal_target_completion_impl(
            self,
            goal=goal,
            dom_elements=dom_elements,
        )

    def _evaluate_reasoning_only_wait_completion(
        self,
        *,
        goal: TestGoal,
        decision: ActionDecision,
        dom_elements: Optional[List[DOMElement]] = None,
    ) -> Optional[str]:
        return evaluate_reasoning_only_wait_completion_impl(
            self,
            goal=goal,
            decision=decision,
            dom_elements=dom_elements,
        )

    def _evaluate_explicit_reasoning_proof_completion(
        self,
        *,
        goal: TestGoal,
        decision: ActionDecision,
        dom_elements: Optional[List[DOMElement]] = None,
    ) -> Optional[str]:
        return evaluate_explicit_reasoning_proof_completion_impl(
            self,
            goal=goal,
            decision=decision,
            dom_elements=dom_elements,
        )

    def _evaluate_wait_goal_completion(
        self,
        *,
        goal: TestGoal,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> Optional[str]:
        return evaluate_wait_goal_completion_impl(
            self,
            goal=goal,
            decision=decision,
            dom_elements=dom_elements,
        )

    @classmethod
    def _build_click_intent_key(
        cls,
        *,
        element: Optional[DOMElement],
        full_selector: Optional[str],
        selector: Optional[str],
    ) -> str:
        return build_click_intent_key_impl(
            cls,
            element=element,
            full_selector=full_selector,
            selector=selector,
        )

    @staticmethod
    def _squash_text(text: str, limit: int = 160) -> str:
        return squash_text_impl(text, limit=limit)

    @staticmethod
    def _truncate_for_prompt(text: str, limit: int = 120) -> str:
        return truncate_for_prompt_impl(text, limit)

    def _fields_for_element(self, el: DOMElement) -> List[str]:
        return fields_for_element_impl(self, el)

    def _candidate_intent_key(self, action: str, fields: List[str]) -> str:
        return candidate_intent_key_impl(self, action, fields)

    @staticmethod
    def _clamp_score(value: float, low: float = -15.0, high: float = 15.0) -> float:
        return max(low, min(high, float(value)))

    def _context_match_tokens(self, el: DOMElement) -> List[str]:
        return context_match_tokens_impl(self, el)

    def _context_score(self, el: DOMElement) -> float:
        return context_score_impl(self, el)

    def _adaptive_intent_bias(self, intent_key: str) -> float:
        return adaptive_intent_bias_impl(self, intent_key)

    def _update_intent_stats(
        self,
        *,
        intent_key: str,
        success: bool,
        changed: bool,
        reason_code: str,
    ) -> None:
        update_intent_stats_impl(
            self,
            intent_key=intent_key,
            success=success,
            changed=changed,
            reason_code=reason_code,
        )

    @staticmethod
    def _normalize_selector_key(selector: str) -> str:
        return normalize_selector_key_impl(selector)

    def _selector_bias_for_fields(self, fields: List[str]) -> float:
        return selector_bias_for_fields_impl(self, fields)

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
    def _has_prominent_auth_form(cls, dom_elements: List[DOMElement]) -> bool:
        has_password_input = False
        has_login_cta = False
        for el in dom_elements:
            tag = cls._normalize_text(getattr(el, "tag", None))
            role = cls._normalize_text(getattr(el, "role", None))
            input_type = cls._normalize_text(getattr(el, "type", None))
            fields = [
                getattr(el, "text", None),
                getattr(el, "aria_label", None),
                getattr(el, "placeholder", None),
                getattr(el, "title", None),
            ]
            if tag == "input" and input_type == "password":
                has_password_input = True
            if any(cls._contains_login_hint(field) for field in fields):
                if tag in {"button", "a", "input"} or role == "button" or input_type in {"submit", "button"}:
                    has_login_cta = True
        return bool(has_password_input and has_login_cta)

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
        return pick_login_modal_close_element_impl(cls, dom_elements, selector_map)

    @classmethod
    def _pick_modal_unblock_element(
        cls,
        dom_elements: List[DOMElement],
        selector_map: Dict[int, str],
        modal_regions_hint: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[int]:
        return pick_modal_unblock_element_impl(
            cls,
            dom_elements,
            selector_map,
            modal_regions_hint=modal_regions_hint,
        )

    @staticmethod
    def _has_login_test_data(goal: TestGoal) -> bool:
        return has_login_test_data_impl(goal)

    def _request_user_intervention(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return request_user_intervention_impl(self, payload)

    @staticmethod
    def _merge_test_data(
        goal: TestGoal,
        payload: Dict[str, Any],
        *,
        blocked_keys: set[str] | None = None,
    ) -> None:
        merge_test_data_impl(goal, payload, blocked_keys=blocked_keys)

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        return to_bool_impl(value, default=default)

    def _request_goal_clarification(self, goal: TestGoal) -> bool:
        return request_goal_clarification_impl(self, goal)

    def _request_login_intervention(self, goal: TestGoal) -> bool:
        return request_login_intervention_impl(self, goal)

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
        return pick_context_shift_element_impl(self, dom_elements, used_element_ids)

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
        return dom_progress_signature_impl(dom_elements)

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
        return record_action_feedback_impl(
            self,
            step_number=step_number,
            decision=decision,
            success=success,
            changed=changed,
            error=error,
            reason_code=reason_code,
            state_change=state_change,
            intent_key=intent_key,
        )

    @staticmethod
    def _extract_domain(url: Optional[str]) -> str:
        return extract_domain_impl(url)

    def _build_memory_context(self, goal: TestGoal) -> str:
        return build_memory_context_impl(self, goal)

    def _record_recovery_hints(self, goal: TestGoal, reason_code: str) -> None:
        return record_recovery_hints_impl(self, goal, reason_code)

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
        return record_action_memory_impl(
            self,
            goal=goal,
            step_number=step_number,
            decision=decision,
            success=success,
            changed=changed,
            error=error,
        )

    def _record_goal_summary(
        self,
        *,
        goal: TestGoal,
        status: str,
        reason: str,
        step_count: int,
        duration_seconds: float,
    ) -> None:
        return record_goal_summary_impl(
            self,
            goal=goal,
            status=status,
            reason=reason,
            step_count=step_count,
            duration_seconds=duration_seconds,
        )

    @classmethod
    @classmethod
    def _goal_text_blob(cls, goal: TestGoal) -> str:
        return goal_text_blob_impl(cls, goal)

    @classmethod
    def _goal_mentions_signup(cls, goal: TestGoal) -> bool:
        return goal_mentions_signup_impl(cls, goal)

    @classmethod
    def _dom_contains_any_hint(cls, dom_elements: List[DOMElement], keywords: tuple[str, ...]) -> bool:
        return dom_contains_any_hint_impl(cls, dom_elements, keywords)

    @classmethod
    def _has_signup_completion_evidence(cls, dom_elements: List[DOMElement]) -> bool:
        return has_signup_completion_evidence_impl(cls, dom_elements)

    def _validate_goal_achievement_claim(
        self,
        goal: TestGoal,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> tuple[bool, Optional[str]]:
        return validate_goal_achievement_claim_impl(self, goal, decision, dom_elements)

    def _build_failure_result(
        self,
        *,
        goal: TestGoal,
        steps: List[StepResult],
        step_count: int,
        start_time: float,
        reason: str,
    ) -> GoalResult:
        return build_failure_result_impl(
            self,
            goal=goal,
            steps=steps,
            step_count=step_count,
            start_time=start_time,
            reason=reason,
        )

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
        steps: List[StepResult] = []
        runtime_state = initialize_goal_execution_state_impl(self, goal)
        filter_goal_active = bool(runtime_state.get("filter_goal_active"))
        filter_semantic_attempts = int(runtime_state.get("filter_semantic_attempts", 0) or 0)
        filter_semantic_attempt_limit = int(runtime_state.get("filter_semantic_attempt_limit", 1) or 1)
        filter_semantic_max_cases = int(runtime_state.get("filter_semantic_max_cases", 1) or 1)
        filter_semantic_current_only = bool(runtime_state.get("filter_semantic_current_only"))
        log_goal_start_impl(self, goal, runtime_state)

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

        prepare_memory_episode_impl(self, goal)

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
            target_completion_reason = self._evaluate_goal_target_completion(
                goal=goal,
                dom_elements=dom_elements,
            )
            if target_completion_reason:
                self._record_reason_code("context_target_selected")
                self._log(f"✅ 목표 달성! 이유: {target_completion_reason}")
                return build_success_goal_result_impl(
                    self,
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=target_completion_reason,
                )
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
            prominent_auth_form = self._has_prominent_auth_form(dom_elements)
            login_gate_visible = bool(
                (heuristic_login_gate or prominent_auth_form)
                and (modal_open_hint or compact_auth_page or prominent_auth_form)
            )
            if (heuristic_login_gate or prominent_auth_form) and not login_gate_visible:
                self._log("ℹ️ 로그인 힌트는 감지됐지만 modal_open/compact_auth/auth_form 조건이 없어 AUTH 분기를 보류합니다.")
            interrupt_payload = resolve_goal_policy_interrupts(
                self,
                goal=goal,
                dom_elements=dom_elements,
                login_gate_visible=login_gate_visible,
                has_login_test_data=has_login_test_data,
                login_intervention_asked=login_intervention_asked,
                modal_open_hint=modal_open_hint,
            )
            login_intervention = dict(interrupt_payload.get("login_intervention") or {})
            has_login_test_data = bool(login_intervention.get("has_login_test_data", has_login_test_data))
            login_intervention_asked = bool(
                login_intervention.get("login_intervention_asked", login_intervention_asked)
            )
            if bool(login_intervention.get("aborted")):
                self._record_reason_code(
                    str(login_intervention.get("reason_code") or "user_intervention_missing")
                )
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
                reasoning_only_wait_reason = self._evaluate_reasoning_only_wait_completion(
                    goal=goal,
                    decision=decision,
                    dom_elements=dom_elements,
                )
                if not reasoning_only_wait_reason:
                    reasoning_only_wait_reason = self._evaluate_explicit_reasoning_proof_completion(
                        goal=goal,
                        decision=decision,
                        dom_elements=dom_elements,
                    )
                if reasoning_only_wait_reason:
                    self._log(f"✅ 목표 달성! 이유: {reasoning_only_wait_reason}")
                    result = GoalResult(
                        goal_id=goal.id,
                        goal_name=goal.name,
                        success=True,
                        steps_taken=steps,
                        total_steps=step_count,
                        final_reason=reasoning_only_wait_reason,
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
            if not decision.is_goal_achieved and decision.action == ActionType.WAIT:
                wait_completion_reason = self._evaluate_wait_goal_completion(
                    goal=goal,
                    decision=decision,
                    dom_elements=dom_elements,
                )
                if wait_completion_reason:
                    decision = ActionDecision(
                        action=decision.action,
                        element_id=decision.element_id,
                        value=decision.value,
                        reasoning=decision.reasoning,
                        confidence=max(float(decision.confidence or 0.0), 0.8),
                        is_goal_achieved=True,
                        goal_achievement_reason=wait_completion_reason,
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

            post_action_result = handle_post_action_runtime(
                self,
                goal=goal,
                decision=decision,
                success=success,
                error=error,
                before_signature=before_signature,
                dom_elements=dom_elements,
                steps=steps,
                step_count=step_count,
                start_time=start_time,
                login_gate_visible=login_gate_visible,
                has_login_test_data=has_login_test_data,
                modal_open_hint=modal_open_hint,
                filter_goal_active=filter_goal_active,
                filter_semantic_attempts=filter_semantic_attempts,
                filter_semantic_attempt_limit=filter_semantic_attempt_limit,
                filter_semantic_max_cases=filter_semantic_max_cases,
                filter_semantic_current_only=filter_semantic_current_only,
                scroll_streak=scroll_streak,
                ineffective_action_streak=ineffective_action_streak,
                force_context_shift=force_context_shift,
                context_shift_fail_streak=context_shift_fail_streak,
                context_shift_cooldown=context_shift_cooldown,
                click_intent_key=click_intent_key,
                action_intent_key=action_intent_key,
                master_orchestrator=master_orchestrator,
            )
            post_dom = post_action_result.get("post_dom") or []
            changed = bool(post_action_result.get("changed"))
            state_change = post_action_result.get("state_change")
            filter_semantic_attempts = int(
                post_action_result.get("filter_semantic_attempts", filter_semantic_attempts)
            )
            scroll_streak = int(post_action_result.get("scroll_streak", scroll_streak))
            ineffective_action_streak = int(
                post_action_result.get("ineffective_action_streak", ineffective_action_streak)
            )
            force_context_shift = bool(
                post_action_result.get("force_context_shift", force_context_shift)
            )
            context_shift_fail_streak = int(
                post_action_result.get("context_shift_fail_streak", context_shift_fail_streak)
            )
            context_shift_cooldown = int(
                post_action_result.get("context_shift_cooldown", context_shift_cooldown)
            )
            terminal_result = post_action_result.get("terminal_result")
            if terminal_result is not None:
                return terminal_result
            if bool(post_action_result.get("continue_loop")):
                continue

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
    def _analyze_dom(
        self,
        url: Optional[str] = None,
        scope_container_ref_id: Optional[str] = None,
    ) -> List[DOMElement]:
        return analyze_dom_impl(self, url=url, scope_container_ref_id=scope_container_ref_id)

    def _capture_screenshot(self) -> Optional[str]:
        return capture_screenshot_impl(self)

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
        return run_filter_semantic_validation_impl(
            self,
            goal_text,
            max_pages=max_pages,
            max_cases=max_cases,
            use_current_selection_only=use_current_selection_only,
            forced_selected_value=forced_selected_value,
            validation_contract=validation_contract,
        )

    def _build_filter_validation_contract(
        self,
        *,
        goal: TestGoal,
        dom_elements: List[DOMElement],
    ) -> Dict[str, Any]:
        return build_filter_validation_contract_impl(
            self,
            goal=goal,
            dom_elements=dom_elements,
        )

    def _decide_next_action(
        self,
        dom_elements: List[DOMElement],
        goal: TestGoal,
        screenshot: Optional[str] = None,
        memory_context: str = "",
    ) -> ActionDecision:
        """LLM에게 다음 액션 결정 요청"""
        return decide_next_action_impl(
            self,
            dom_elements,
            goal,
            screenshot=screenshot,
            memory_context=memory_context,
        )

    def _format_dom_for_llm(self, elements: List[DOMElement]) -> str:
        return format_dom_for_llm_impl(self, elements)

    def _pick_scoped_container(
        self,
        elements: List[DOMElement],
    ) -> tuple[Optional[str], Optional[str], Optional[str], float, bool]:
        from .dom_prompt_formatting import pick_scoped_container as pick_scoped_container_impl

        return pick_scoped_container_impl(self, elements)

    def _parse_decision(self, response_text: str) -> ActionDecision:
        return parse_decision_impl(self, response_text)

    def _execute_decision(
        self,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> tuple[bool, Optional[str]]:
        return execute_decision_impl(self, decision, dom_elements)

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
        return execute_action_impl(
            self,
            action,
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value=value,
            values=values,
            url=url,
        )

    def _call_llm_text_only(self, prompt: str) -> str:
        return call_llm_text_only_impl(self, prompt)
