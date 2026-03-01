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
        self._handoff_state: Dict[str, Any] = {}
        self._memory_selector_bias: Dict[str, float] = {}
        self._recent_click_element_ids: List[int] = []
        self._last_dom_top_ids: List[int] = []
        self._goal_constraints: Dict[str, Any] = {}
        self._goal_metric_value: Optional[float] = None
        self._goal_tokens: set[str] = set()

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
            "dismiss",
        )
        if any(h in text for h in hints):
            return True
        tokens = [tok for tok in re.split(r"[^a-zA-Z0-9가-힣×]+", text) if tok]
        return any(tok in {"x", "×"} for tok in tokens)

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
        text = (value or "").strip()
        return bool(re.fullmatch(r"\d{1,3}", text))

    @staticmethod
    def _is_navigational_href(value: Optional[str]) -> bool:
        href = (value or "").strip().lower()
        if not href:
            return False
        if href.startswith("#"):
            return False
        if href.startswith("javascript:"):
            return False
        if href.startswith("mailto:") or href.startswith("tel:"):
            return False
        return True

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
        for attempt in range(2):
            time.sleep(0.8 + (0.4 * attempt))
            dom = self._analyze_dom()
            if dom:
                return dom
        # 이미 진행된 실행 컨텍스트를 잃지 않기 위해, 중후반 phase에서는 시작 URL 강제 복귀를 피합니다.
        if (self._runtime_phase or "").upper() in {"AUTH", "COMPOSE", "APPLY", "VERIFY"} or self._no_progress_counter > 0:
            self._log("🛠️ DOM 복구: 현재 컨텍스트 유지(시작 URL 강제 복귀 생략)")
            return []
        start_url = str(getattr(goal, "start_url", "") or "").strip()
        if start_url:
            self._log("🛠️ DOM 복구: 시작 URL로 재동기화 시도")
            _ = self._execute_action("goto", url=start_url)
            time.sleep(1.2)
            dom = self._analyze_dom()
            if dom:
                return dom
        return []

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
        text = cls._normalize_text(value)
        if not text:
            return False
        return any(ch in text for ch in ("›", "»", ">"))

    @classmethod
    def _derive_goal_constraints(cls, goal: TestGoal) -> Dict[str, Any]:
        blob = cls._goal_text_blob(goal)
        text = cls._normalize_text(blob)
        if not text:
            return {}

        no_navigation_hints = (
            "페이지 이동 없이",
            "url 변화 없이",
            "url 변경 없이",
            "같은 페이지",
            "no navigation",
            "without navigation",
            "stay on page",
            "same page",
        )
        require_no_navigation = any(hint in text for hint in no_navigation_hints)
        numeric_values: List[int] = []
        metric_terms: List[str] = []
        number_pattern = r"(\d{1,3}(?:,\d{3})*|\d{1,6})"
        for match in re.finditer(rf"(?<!\d){number_pattern}(?!\d)\s*([^\d\s,.;:()]{1,12})?", text):
            value = int(str(match.group(1)).replace(",", ""))
            numeric_values.append(value)
            maybe_term = (match.group(2) or "").strip()
            if maybe_term:
                metric_terms.append(maybe_term)

        if not numeric_values:
            if require_no_navigation:
                return {"require_no_navigation": True}
            return {}

        collect_min: Optional[int] = None
        apply_target: Optional[int] = None

        if len(numeric_values) >= 2:
            collect_min = max(numeric_values)
            apply_target = min(numeric_values)
        else:
            collect_min = numeric_values[0]

        if apply_target is not None and collect_min is not None and apply_target >= collect_min:
            apply_target = None

        term_freq: Dict[str, int] = {}
        for term in metric_terms:
            term_freq[term] = int(term_freq.get(term, 0)) + 1
        sorted_terms = sorted(term_freq.items(), key=lambda kv: kv[1], reverse=True)
        top_terms = [t for t, _ in sorted_terms[:4]]
        metric_label = top_terms[0] if top_terms else "count"
        require_collect_before_progress = bool(collect_min is not None and apply_target is not None)

        return {
            "metric": "numeric",
            "metric_label": metric_label,
            "metric_terms": top_terms,
            "collect_min": collect_min,
            "apply_target": apply_target,
            "require_collect_before_progress": require_collect_before_progress,
            "require_no_navigation": require_no_navigation,
        }

    @classmethod
    def _extract_metric_values_from_text(cls, value: str, metric_terms: List[str]) -> List[int]:
        text = cls._normalize_text(value)
        if not text:
            return []

        number_pattern = r"(\d{1,3}(?:,\d{3})*|\d{1,6})"

        def _to_int(raw: str) -> int:
            return int(str(raw).replace(",", ""))

        numbers: List[int] = []
        term_matches = 0
        for term in metric_terms or []:
            safe_term = re.escape(str(term))
            for m in re.finditer(rf"{number_pattern}\s*{safe_term}", text):
                numbers.append(_to_int(m.group(1)))
                term_matches += 1
            for m in re.finditer(rf"{safe_term}\s*{number_pattern}", text):
                numbers.append(_to_int(m.group(1)))
                term_matches += 1
        if term_matches > 0:
            numbers.extend(_to_int(m.group(1)) for m in re.finditer(rf"\({number_pattern}\)", text))
            return numbers

        # metric_terms가 있는데 매칭이 없으면 잡음 가능성이 높으므로 보수적으로 무시합니다.
        if metric_terms:
            return []

        contextual_numbers: List[int] = []
        context_patterns = [
            rf"(?:총|합계|count|counts|items?|item|total|현재|수량|개수|학점)\s*[:=]?\s*{number_pattern}",
            rf"{number_pattern}\s*(?:개|건|명|점|학점|items?|item|count)",
        ]
        for pattern in context_patterns:
            for m in re.finditer(pattern, text):
                contextual_numbers.append(_to_int(m.group(1)))
        if contextual_numbers:
            return contextual_numbers

        # 마지막 fallback은 괄호형 수치만 허용해 일반 DOM 숫자 노이즈를 줄입니다.
        return [_to_int(m.group(1)) for m in re.finditer(rf"\({number_pattern}\)", text)]

    def _estimate_goal_metric_from_dom(self, dom_elements: List[DOMElement]) -> Optional[float]:
        metric_kind = str(self._goal_constraints.get("metric") or "").strip().lower()
        if metric_kind != "numeric":
            return None
        metric_terms = [str(x) for x in (self._goal_constraints.get("metric_terms") or []) if str(x).strip()]

        values: List[int] = []
        for el in dom_elements:
            fields = [
                el.text,
                el.aria_label,
                el.placeholder,
                getattr(el, "title", None),
            ]
            for field in fields:
                if not field:
                    continue
                values.extend(self._extract_metric_values_from_text(str(field), metric_terms))

        collect_min = self._goal_constraints.get("collect_min")
        apply_target = self._goal_constraints.get("apply_target")
        dynamic_upper = 10000
        try:
            if collect_min is not None:
                dynamic_upper = max(dynamic_upper, int(collect_min) * 4)
            if apply_target is not None:
                dynamic_upper = max(dynamic_upper, int(apply_target) * 4)
        except Exception:
            pass
        dynamic_upper = min(dynamic_upper, 1_000_000)

        filtered = [v for v in values if 0 <= int(v) <= dynamic_upper]
        if not filtered:
            return None
        return float(max(filtered))

    def _is_collect_constraint_unmet(self) -> bool:
        collect_min = self._goal_constraints.get("collect_min")
        if collect_min is None:
            return False
        current = self._goal_metric_value
        if current is None:
            return True
        return float(current) + 1e-9 < float(collect_min)

    def _apply_phase_constraints(self, detected_phase: str) -> str:
        if not self._is_collect_constraint_unmet():
            return detected_phase
        if detected_phase in {"COMPOSE", "APPLY", "VERIFY"}:
            return "COLLECT"
        return detected_phase

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
        return "".join(lines)

    def _enforce_goal_constraints_on_decision(
        self,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> ActionDecision:
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

        if not self._is_collect_constraint_unmet():
            return decision

        collect_min = int(self._goal_constraints.get("collect_min") or 0)
        metric_label = str(self._goal_constraints.get("metric_label") or "")
        current = self._goal_metric_value
        current_text = "unknown" if current is None else str(int(current))

        selected_element: Optional[DOMElement] = None
        if decision.element_id is not None:
            selected_element = next((el for el in dom_elements if el.id == decision.element_id), None)

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
        if not self._is_collect_constraint_unmet():
            return None
        collect_min = int(self._goal_constraints.get("collect_min") or 0)
        metric_label = str(self._goal_constraints.get("metric_label") or "")
        current = self._goal_metric_value
        current_text = "unknown" if current is None else str(int(current))
        return (
            f"목표 제약 미충족: 최소 {collect_min}{metric_label} 수집 전에는 완료로 판정할 수 없습니다. "
            f"(현재 추정값: {current_text}{metric_label})"
        )

    @classmethod
    def _contains_logout_hint(cls, value: Optional[str]) -> bool:
        text = cls._normalize_text(value)
        if not text:
            return False
        hints = (
            "로그아웃",
            "log out",
            "logout",
            "sign out",
            "signout",
        )
        return any(h in text for h in hints)

    @classmethod
    def _contains_duplicate_account_hint(cls, value: Optional[str]) -> bool:
        text = cls._normalize_text(value)
        if not text:
            return False
        hints = (
            "이미 사용 중인 아이디",
            "이미 사용중인 아이디",
            "이미 사용 중",
            "아이디 중복",
            "중복된 아이디",
            "already in use",
            "already exists",
            "duplicate",
        )
        return any(h in text for h in hints)

    @staticmethod
    def _next_username(base: str) -> str:
        seed = re.sub(r"[^a-zA-Z0-9_]", "", (base or "").strip())
        if not seed:
            seed = "gaiauser"
        seed = seed[:20]
        suffix = int(time.time() * 1000) % 1000000
        return f"{seed}_{suffix}"

    def _rotate_signup_identity(self, goal: TestGoal) -> Optional[str]:
        if not isinstance(goal.test_data, dict):
            goal.test_data = {}
        current_username = str(goal.test_data.get("username") or "").strip()
        base = current_username.split("@", 1)[0] if current_username else "gaiauser"
        new_username = self._next_username(base)
        if current_username and new_username == current_username:
            new_username = self._next_username(f"{base}x")
        goal.test_data["username"] = new_username
        goal.test_data.setdefault("auth_mode", "signup")
        email = str(goal.test_data.get("email") or "").strip()
        if email:
            domain = email.split("@", 1)[1] if "@" in email else "example.com"
            goal.test_data["email"] = f"{new_username}@{domain}"
        return new_username

    def _has_duplicate_account_signal(
        self,
        *,
        state_change: Optional[Dict[str, Any]],
        dom_elements: List[DOMElement],
    ) -> bool:
        if isinstance(state_change, dict):
            live_texts = state_change.get("live_texts_after")
            if isinstance(live_texts, list):
                for text in live_texts:
                    if self._contains_duplicate_account_hint(str(text)):
                        return True
        for el in dom_elements:
            if self._contains_duplicate_account_hint(el.text) or self._contains_duplicate_account_hint(el.aria_label):
                return True
        return False

    def _goal_allows_logout(self) -> bool:
        text = self._active_goal_text or ""
        if not text:
            return False
        return self._contains_logout_hint(text)

    def _is_ref_temporarily_blocked(self, ref_id: Optional[str]) -> bool:
        if not ref_id:
            return False
        return int(self._ineffective_ref_counts.get(ref_id, 0)) >= 2

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
        if bool(state_change.get("effective")):
            return True
        progress_keys = (
            "url_changed",
            "dom_changed",
            "target_visibility_changed",
            "target_value_changed",
            "target_value_matches",
            "target_focus_changed",
            "focus_changed",
            "counter_changed",
            "number_tokens_changed",
            "status_text_changed",
            "list_count_changed",
            "interactive_count_changed",
            "modal_count_changed",
            "backdrop_count_changed",
            "dialog_count_changed",
            "modal_state_changed",
            "auth_state_changed",
            "text_digest_changed",
            "evidence_changed",
        )
        return any(bool(state_change.get(key)) for key in progress_keys)

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
            "회원가입",
            "로그인",
            "결제",
            "구매",
            "삭제",
            "수정",
            "추가",
            "등록",
            "signup",
            "register",
            "login",
            "checkout",
            "purchase",
            "submit",
        )
        has_verify_hint = any(hint in text for hint in verify_hints)
        has_operation_hint = any(hint in text for hint in operation_hints)
        return bool(has_verify_hint and not has_operation_hint)

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
        if decision.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.NAVIGATE}:
            return False
        if not self._is_verification_style_goal(goal):
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
        if self._is_login_gate(dom_elements):
            return "AUTH"
        if self._is_collect_constraint_unmet():
            return "COLLECT"
        if self._progress_counter > 0:
            if self._runtime_phase in {"COLLECT", "COMPOSE"}:
                return "APPLY"
            if self._runtime_phase:
                return self._runtime_phase
        return self._runtime_phase or "COLLECT"

    @classmethod
    def _is_login_gate(cls, dom_elements: List[DOMElement]) -> bool:
        auth_hits = 0
        has_password_field = False
        has_id_or_email_field = False
        modal_auth_hits = 0
        modal_shell_hits = 0
        for el in dom_elements:
            text = cls._normalize_text(el.text)
            placeholder = cls._normalize_text(el.placeholder)
            aria = cls._normalize_text(el.aria_label)
            role = cls._normalize_text(el.role)
            typ = cls._normalize_text(el.type)
            class_name = cls._normalize_text(el.class_name)
            aria_modal = cls._normalize_text(el.aria_modal)

            fields = [text, placeholder, aria, role]
            if any(cls._contains_login_hint(v) for v in fields):
                auth_hits += 1

            if typ == "password" or "password" in placeholder or "비밀번호" in placeholder or "password" in aria:
                has_password_field = True

            if (
                typ in {"email", "text"}
                and any(k in (placeholder or text or aria) for k in ("email", "이메일", "아이디", "username", "user id"))
            ):
                has_id_or_email_field = True

            modal_attr_blob = " ".join([role, class_name, aria_modal])
            is_modal_shell = (
                role in {"dialog", "alertdialog"}
                or aria_modal == "true"
                or any(k in modal_attr_blob for k in ("modal", "dialog", "popup", "sheet", "drawer", "overlay"))
            )
            if is_modal_shell:
                modal_shell_hits += 1
            if is_modal_shell and any(k in " ".join(fields) for k in ("로그인", "회원가입", "signin", "signup", "login", "register", "auth")):
                modal_auth_hits += 1

        # 기본: 아이디/이메일 + 비밀번호가 실제로 보이고, 모달/다이얼로그 셸이 동반될 때만 인증 게이트로 판정
        if has_password_field and has_id_or_email_field and modal_shell_hits > 0:
            return True
        # 예외: 모달이 없는 전용 로그인 페이지(요소 수가 적고 인증 힌트가 매우 높은 경우)
        if has_password_field and has_id_or_email_field and auth_hits >= 8 and len(dom_elements) <= 120:
            return True
        if modal_shell_hits >= 2 and modal_auth_hits >= 2 and auth_hits >= 4 and has_password_field and has_id_or_email_field:
            return True
        return False

    @classmethod
    def _is_compact_auth_page(cls, dom_elements: List[DOMElement]) -> bool:
        auth_hits = 0
        has_password_field = False
        has_id_or_email_field = False
        for el in dom_elements:
            text = cls._normalize_text(el.text)
            placeholder = cls._normalize_text(el.placeholder)
            aria = cls._normalize_text(el.aria_label)
            typ = cls._normalize_text(el.type)
            if any(cls._contains_login_hint(v) for v in (text, placeholder, aria)):
                auth_hits += 1
            if typ == "password" or "password" in placeholder or "비밀번호" in placeholder or "password" in aria:
                has_password_field = True
            if (
                typ in {"email", "text"}
                and any(k in (placeholder or text or aria) for k in ("email", "이메일", "아이디", "username", "user id"))
            ):
                has_id_or_email_field = True
        return bool(has_password_field and has_id_or_email_field and auth_hits >= 6 and len(dom_elements) <= 120)

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
            if str(callback_resp.get("action") or "").lower() in {"cancel", "deny", "no"}:
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
            proceed = callback_resp.get("proceed")
            if isinstance(proceed, bool):
                return proceed
            if isinstance(proceed, str):
                return self._to_bool(proceed, default=True)
            return True

        self._log("🙋 사용자 개입 필요: 목표가 모호하거나 중요한 정보가 부족합니다.")
        try:
            refined = input("구체 목표를 입력하세요 (비우면 기존 목표 유지): ").strip()
        except (EOFError, KeyboardInterrupt):
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
                "진행 여부와 계정 정보(username/email/password) 또는 수동 로그인 완료 여부를 알려주세요."
            ),
            "fields": ["proceed", "username", "email", "password", "manual_done"],
        }
        callback_resp = self._request_user_intervention(callback_payload)
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
        intent_key: Optional[str] = None,
    ):
        code = reason_code or (self._last_exec_result.reason_code if self._last_exec_result else "unknown")
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
        steps: List[StepResult] = []
        self._active_goal_text = f"{goal.name} {goal.description}".strip().lower()
        self._ineffective_ref_counts = {}
        self._last_success_click_intent = ""
        self._success_click_intent_streak = 0
        self._intent_stats = {}
        self._context_shift_round = 0
        self._last_context_shift_intent = ""
        self._runtime_phase = "COLLECT"
        self._progress_counter = 0
        self._no_progress_counter = 0
        self._handoff_state = {}
        self._memory_selector_bias = {}
        self._recent_click_element_ids = []
        self._last_dom_top_ids = []
        self._goal_tokens = self._derive_goal_tokens(goal)
        self._goal_constraints = self._derive_goal_constraints(goal)
        self._goal_metric_value = None

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

            detected_phase = self._infer_runtime_phase(dom_elements)
            guarded_phase = self._apply_phase_constraints(detected_phase)
            if guarded_phase != detected_phase:
                self._log(f"🧱 제약 가드: phase {detected_phase} -> {guarded_phase}")
            detected_phase = guarded_phase
            if detected_phase != self._runtime_phase:
                self._log(f"🔁 phase 전환: {self._runtime_phase} -> {detected_phase}")
            self._runtime_phase = detected_phase
            master_orchestrator.set_phase(detected_phase)

            self._log(f"📊 DOM 요소 {len(dom_elements)}개 발견")
            before_signature = self._dom_progress_signature(dom_elements)
            heuristic_login_gate = self._is_login_gate(dom_elements)
            modal_open_hint = bool(self._last_snapshot_evidence.get("modal_open")) if isinstance(self._last_snapshot_evidence, dict) else False
            compact_auth_page = self._is_compact_auth_page(dom_elements)
            login_gate_visible = bool(
                heuristic_login_gate and (modal_open_hint or compact_auth_page)
            )
            if heuristic_login_gate and not login_gate_visible:
                self._log("ℹ️ 로그인 힌트는 감지됐지만 modal_open/compact_auth 조건이 없어 AUTH 분기를 보류합니다.")
            if login_gate_visible:
                self._log("🔐 로그인/인증 화면이 감지되었습니다.")
                if not login_intervention_asked:
                    has_login_test_data = self._has_login_test_data(goal)
                    if not has_login_test_data:
                        if not self._request_login_intervention(goal):
                            return self._build_failure_result(
                                goal=goal,
                                steps=steps,
                                step_count=step_count,
                                start_time=start_time,
                                reason=(
                                    "로그인 화면에서 사용자 개입이 필요하지만 입력이 제공되지 않아 중단했습니다. "
                                    "다시 실행 후 로그인 진행 여부/계정 정보를 입력해 주세요."
                                ),
                            )
                        has_login_test_data = self._has_login_test_data(goal)
                    else:
                        self._log("🔁 기존 로그인/회원가입 입력 데이터를 재사용합니다.")
                    login_intervention_asked = True
            else:
                login_intervention_asked = False

            # 2. 스크린샷 캡처
            screenshot = self._capture_screenshot()

            # 2.5 CAPTCHA 감지 및 자동 해결
            if screenshot and not getattr(self, "_captcha_solver_skip", False):
                if not hasattr(self, "_captcha_solver"):
                    self._captcha_solver = CaptchaSolver(
                        vision_client=self.llm,
                        execute_fn=self._execute_action,
                        mcp_host_url=self.mcp_host_url,
                        session_id=self.session_id,
                        max_attempts=5,
                        log_fn=self._log,
                    )
                captcha_result = self._captcha_solver.detect_and_handle(
                    screenshot=screenshot,
                    page_url=getattr(self, "_current_url", goal.start_url or ""),
                    capture_fn=self._capture_screenshot,
                )
                if captcha_result.solved:
                    self._log(f"🔓 CAPTCHA 해결 완료 ({captcha_result.attempts}회 시도)")
                    self._action_history.append(
                        f"Step {step_count}: captcha_solve - CAPTCHA 자동 해결 ({captcha_result.status})"
                    )
                    time.sleep(1)
                    continue  # DOM 재수집 후 다음 스텝
                elif captcha_result.status == "gave_up":
                    self._log("🏳️ CAPTCHA 해결 포기 — 일반 LLM 흐름으로 계속")
                    self._action_feedback.append(
                        "CAPTCHA가 감지되었으나 자동 해결에 실패했습니다. "
                        "가능하면 CAPTCHA를 우회하는 경로를 찾거나, 사용자 개입이 필요합니다."
                    )
                    if len(self._action_feedback) > 10:
                        self._action_feedback = self._action_feedback[-10:]
                # no_captcha 또는 unsupported → 일반 흐름 계속

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

            if master_directive.kind == "handoff" and master_directive.reason == "auth_required":
                self._handoff_state = {
                    "kind": "auth_required",
                    "phase": self._runtime_phase,
                    "url": goal.start_url,
                }

            if master_directive.kind == "handoff" and master_directive.reason == "no_progress":
                no_progress_count = int(
                    (master_directive.handoff_payload or {}).get("count")
                    or self._no_progress_counter
                    or 0
                )
                self._handoff_state = {
                    "kind": "no_progress",
                    "phase": self._runtime_phase,
                    "url": goal.start_url,
                    "count": no_progress_count,
                }
                callback_resp = self._request_user_intervention(
                    {
                        "kind": "no_progress",
                        "goal_name": goal.name,
                        "goal_description": goal.description,
                        "phase": self._runtime_phase,
                        "question": (
                            f"상태 변화가 {no_progress_count}회 연속으로 감지되지 않았습니다. "
                            "추가 지시(예: 우선할 버튼/필터/입력값)를 제공하거나 proceed=true로 계속하세요."
                        ),
                        "fields": ["instruction", "proceed"],
                    }
                )
                if isinstance(callback_resp, dict):
                    proceed = self._to_bool(callback_resp.get("proceed"), default=True)
                    instruction = str(callback_resp.get("instruction") or "").strip()
                    if instruction:
                        self._action_feedback.append(f"사용자 추가 지시: {instruction}")
                        if len(self._action_feedback) > 10:
                            self._action_feedback = self._action_feedback[-10:]
                    if not proceed:
                        return self._build_failure_result(
                            goal=goal,
                            steps=steps,
                            step_count=step_count,
                            start_time=start_time,
                            reason="사용자 요청으로 실행을 중단했습니다.",
                        )
                if context_shift_fail_streak >= 3 or context_shift_cooldown > 0:
                    force_context_shift = False
                    self._action_feedback.append(
                        "컨텍스트 전환이 연속 실패해 일반 LLM 액션으로 복귀합니다."
                    )
                    if len(self._action_feedback) > 10:
                        self._action_feedback = self._action_feedback[-10:]
                else:
                    force_context_shift = True

            if collect_unmet and collect_metric_stall_count >= 2 and context_shift_cooldown <= 0:
                force_context_shift = True
                self._action_feedback.append(
                    "수집 지표가 정체되어 페이지/탭/섹션 전환을 강제합니다."
                )
                if len(self._action_feedback) > 10:
                    self._action_feedback = self._action_feedback[-10:]

            if force_context_shift:
                picked = (
                    self._pick_collect_context_shift_element(dom_elements, context_shift_used_elements)
                    if collect_unmet
                    else None
                )
                if picked is None:
                    picked = self._pick_context_shift_element(dom_elements, context_shift_used_elements)
                if picked is not None:
                    picked_id, picked_reason, picked_intent_key = picked
                    context_shift_used_elements.add(picked_id)
                    self._last_context_shift_intent = picked_intent_key
                    shift_decision = ActionDecision(
                        action=ActionType.CLICK,
                        element_id=picked_id,
                        reasoning=picked_reason,
                        confidence=0.9,
                    )
                    self._log("🧭 무효 반복 감지: 페이지/섹션 전환을 우선 시도합니다.")
                    step_result, success, error = sub_agent.run_step(
                        step_number=step_count,
                        step_start=step_start,
                        decision=shift_decision,
                        dom_elements=dom_elements,
                    )
                    steps.append(step_result)
                    if success:
                        self._action_history.append(
                            f"Step {step_count}: {shift_decision.action.value} - {shift_decision.reasoning}"
                        )
                    else:
                        self._log(f"⚠️ 컨텍스트 전환 실패: {error}")

                    post_dom = self._analyze_dom()
                    changed = bool(post_dom) and self._dom_progress_signature(post_dom) != before_signature
                    self._record_action_feedback(
                        step_number=step_count,
                        decision=shift_decision,
                        success=success,
                        changed=changed,
                        error=error,
                        reason_code=self._last_exec_result.reason_code if self._last_exec_result else None,
                        state_change=self._last_exec_result.state_change if self._last_exec_result else None,
                        intent_key=picked_intent_key,
                    )
                    self._record_action_memory(
                        goal=goal,
                        step_number=step_count,
                        decision=shift_decision,
                        success=success,
                        changed=changed,
                        error=error,
                    )

                    if success and changed:
                        ineffective_action_streak = 0
                        force_context_shift = False
                        context_shift_used_elements.clear()
                        self._last_context_shift_intent = ""
                        orchestrator.same_dom_count = 0
                        context_shift_fail_streak = 0
                        context_shift_cooldown = 0
                    else:
                        context_shift_fail_streak += 1
                        if len(context_shift_used_elements) > 20:
                            context_shift_used_elements.clear()
                        if context_shift_fail_streak >= 3:
                            self._log(
                                "🧭 컨텍스트 전환이 연속 실패해 일반 액션 전략으로 복귀합니다."
                            )
                            force_context_shift = False
                            context_shift_used_elements.clear()
                            self._last_context_shift_intent = ""
                            context_shift_cooldown = 4
                        else:
                            force_context_shift = True
                    time.sleep(0.4)
                    continue
                else:
                    if collect_unmet:
                        self._log("🧭 전환 후보 부족: 수집 CTA 노출을 위해 스크롤 전환을 시도합니다.")
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
                            self._log("🧭 스크롤 전환 대상(ref)을 찾지 못해 이번 스텝은 대기로 전환합니다.")
                            shift_decision = ActionDecision(
                                action=ActionType.WAIT,
                                reasoning="컨텍스트 전환 대상(ref) 부재로 DOM 재수집 대기",
                                confidence=0.45,
                            )
                        else:
                            shift_decision = ActionDecision(
                                action=ActionType.SCROLL,
                                element_id=scroll_target_id,
                                reasoning="수집 목표 미달 상태에서 새 수집 요소 탐색을 위한 스크롤 전환",
                                confidence=0.6,
                            )
                        step_result, success, error = sub_agent.run_step(
                            step_number=step_count,
                            step_start=step_start,
                            decision=shift_decision,
                            dom_elements=dom_elements,
                        )
                        steps.append(step_result)
                        post_dom = self._analyze_dom()
                        changed = bool(post_dom) and self._dom_progress_signature(post_dom) != before_signature
                        if success and changed:
                            context_shift_fail_streak = 0
                            force_context_shift = False
                            context_shift_cooldown = 0
                        else:
                            context_shift_fail_streak += 1
                            force_context_shift = context_shift_fail_streak < 3
                            if context_shift_fail_streak >= 3:
                                context_shift_cooldown = 4
                        time.sleep(0.3)
                        continue
                    self._log("🧭 컨텍스트 전환 후보를 찾지 못해 기본 LLM 흐름으로 계속 진행합니다.")
                    force_context_shift = False

            # 3. LLM에게 다음 액션 결정 요청 (OpenClaw 철학 정렬: 계획은 LLM, 실행은 ref-only)
            memory_context = self._build_memory_context(goal)
            decision = self._decide_next_action(
                dom_elements=dom_elements,
                goal=goal,
                screenshot=screenshot,
                memory_context=memory_context,
            )
            self._log(f"🤖 LLM 결정: {decision.action.value} - {decision.reasoning}")

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
            selected_close_signal = any(self._contains_close_hint(field) for field in selected_fields)
            if not selected_close_signal and selected_element is not None:
                selected_close_signal = self._normalize_text(selected_element.text) in {"x", "×", "닫기", "close"}
            close_like_click_intent = bool(decision.action == ActionType.CLICK and selected_close_signal)
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
            if decision.action == ActionType.CLICK and decision.element_id is not None:
                self._recent_click_element_ids.append(int(decision.element_id))
                if len(self._recent_click_element_ids) > 24:
                    self._recent_click_element_ids = self._recent_click_element_ids[-24:]

            post_dom = self._analyze_dom()
            refreshed_metric = self._estimate_goal_metric_from_dom(post_dom) if post_dom else None
            if refreshed_metric is not None:
                self._goal_metric_value = refreshed_metric
            state_change = self._last_exec_result.state_change if self._last_exec_result else None
            changed_by_state = self._state_change_indicates_progress(state_change)
            changed_by_dom = bool(post_dom) and self._dom_progress_signature(post_dom) != before_signature
            changed = bool(changed_by_state or changed_by_dom)

            if bool(self._goal_constraints.get("require_no_navigation")) and isinstance(state_change, dict):
                if bool(state_change.get("url_changed")):
                    self._log("🧱 제약 가드: '페이지 이동 없이' 목표라 URL 변경 액션은 진행으로 인정하지 않습니다.")
                    changed = False
                    start_url = str(goal.start_url or "").strip()
                    if start_url:
                        self._log("↩️ 페이지 고정 제약 복구: 시작 URL로 복귀합니다.")
                        _ = self._execute_action("goto", url=start_url)
                        time.sleep(0.8)
                        recovered_dom = self._analyze_dom()
                        if recovered_dom:
                            post_dom = recovered_dom

            if self._can_finish_by_verification_transition(
                goal=goal,
                decision=decision,
                success=success,
                changed=changed,
                state_change=state_change,
                before_dom_count=len(dom_elements),
                after_dom_count=len(post_dom or []),
            ):
                completion_reason = self._build_verification_transition_reason(
                    state_change=state_change,
                    before_dom_count=len(dom_elements),
                    after_dom_count=len(post_dom or []),
                )
                self._log(f"✅ 목표 달성! 이유: {completion_reason}")
                result = GoalResult(
                    goal_id=goal.id,
                    goal_name=goal.name,
                    success=True,
                    steps_taken=steps,
                    total_steps=step_count,
                    final_reason=completion_reason,
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

            if changed:
                self._progress_counter += 1
                self._no_progress_counter = 0
            else:
                self._no_progress_counter += 1
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
            if not success or not changed:
                self._record_recovery_hints(goal, reason_code)
                auth_mode = ""
                if isinstance(goal.test_data, dict):
                    auth_mode = str(goal.test_data.get("auth_mode") or "").strip().lower()
                is_auth_flow = login_gate_visible and (
                    auth_mode in {"signup", "register", "login", "signin"}
                    or has_login_test_data
                )

                if (
                    is_auth_flow
                    and decision.action == ActionType.CLICK
                    and reason_code in {"no_state_change", "not_actionable"}
                ):
                    self._action_feedback.append(
                        "인증 모달 제출이 반영되지 않았습니다. 모달 내부 오류/필수 입력값을 확인하고 "
                        "같은 모달 안에서 재시도하세요. 페이지/섹션 전환은 금지합니다."
                    )
                    if len(self._action_feedback) > 10:
                        self._action_feedback = self._action_feedback[-10:]
                    ineffective_action_streak = 0
                    force_context_shift = False
                    time.sleep(0.25)
                    continue
                if reason_code == "modal_not_open":
                    self._log("🧭 close 대상 모달이 현재 열려있지 않아 재계획합니다.")
                    self._action_feedback.append(
                        "닫기 액션 시점에 모달이 열려있지 않았습니다. 최신 화면 기준으로 후보를 다시 수집하고 "
                        "닫기 대신 현재 활성 CTA를 선택하세요."
                    )
                    if len(self._action_feedback) > 10:
                        self._action_feedback = self._action_feedback[-10:]
                    _ = self._analyze_dom()
                    ineffective_action_streak = 0
                    force_context_shift = True
                    time.sleep(0.2)
                    continue

                if self._no_progress_counter >= 2 and reason_code in {"no_state_change", "not_actionable", "ambiguous_ref_target", "ambiguous_selector", "blocked_ref_no_progress", "blocked_logout_action"} and decision.action in {
                    ActionType.CLICK,
                    ActionType.FILL,
                    ActionType.PRESS,
                }:
                    force_context_shift = True
                if reason_code in {"snapshot_not_found", "stale_snapshot", "ref_required", "ambiguous_ref_target", "ambiguous_selector", "not_found"}:
                    self._log("♻️ snapshot/ref 갱신이 필요해 DOM을 재수집합니다.")
                    _ = self._analyze_dom()
                    ineffective_action_streak = 0
                    force_context_shift = False
                    time.sleep(0.25)
                    continue
                if reason_code in {"request_exception", "http_5xx"}:
                    attempt_count = self._last_exec_result.attempt_count if self._last_exec_result else 0
                    backoff = min(2.5, 0.6 + (0.25 * max(0, attempt_count)))
                    self._log(
                        f"🌐 일시적 통신 오류({reason_code}) 감지: {backoff:.2f}s 대기 후 재시도합니다."
                    )
                    _ = self._analyze_dom()
                    ineffective_action_streak = 0
                    force_context_shift = False
                    time.sleep(backoff)
                    continue

            if decision.action in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS, ActionType.NAVIGATE, ActionType.SCROLL}:
                if success and changed:
                    ineffective_action_streak = 0
                    context_shift_fail_streak = 0
                    context_shift_cooldown = 0
                else:
                    ineffective_action_streak += 1
            else:
                ineffective_action_streak = 0

            if scroll_streak >= 3:
                self._log("🧭 스크롤이 연속 선택되어 컨텍스트 전환을 강제합니다.")
                force_context_shift = True
                scroll_streak = 0

            if decision.action == ActionType.CLICK:
                if click_intent_key and (not success or not changed):
                    if click_intent_key == self._last_success_click_intent:
                        self._success_click_intent_streak += 1
                    else:
                        self._last_success_click_intent = click_intent_key
                        self._success_click_intent_streak = 1
                elif click_intent_key and success and changed:
                    self._last_success_click_intent = click_intent_key
                    self._success_click_intent_streak = 0
                else:
                    self._success_click_intent_streak = 0
            elif decision.action in {ActionType.CLICK, ActionType.SCROLL, ActionType.NAVIGATE, ActionType.PRESS}:
                self._last_success_click_intent = ""
                self._success_click_intent_streak = 0

            if self._success_click_intent_streak >= 3 and self._no_progress_counter >= 2:
                self._log("🧭 동일 클릭 의도 반복 감지: 단계 전환 CTA 탐색으로 전환합니다.")
                force_context_shift = True

            if ineffective_action_streak >= 3 and self._no_progress_counter >= 2:
                force_context_shift = True
            if ineffective_action_streak >= 8:
                return self._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=(
                        "무효 액션이 장시간 반복되어 중단했습니다. "
                        "컨텍스트 전환(페이지/탭/필터) 시도 후에도 상태 변화가 없습니다."
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
            self._selector_to_ref_id = {}
            self._element_scopes = {}
            self._active_snapshot_id = str(data.get("snapshot_id") or "")
            self._active_dom_hash = str(data.get("dom_hash") or "")
            self._active_snapshot_epoch = int(data.get("epoch") or 0)
            evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
            self._last_snapshot_evidence = evidence

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
                        text=el.get("text", "")[:100],  # 텍스트 길이 제한
                        role=attrs.get("role"),
                        type=attrs.get("type"),
                        placeholder=attrs.get("placeholder"),
                        aria_label=attrs.get("aria-label"),
                        aria_modal=attrs.get("aria-modal"),
                        title=attrs.get("title"),
                        class_name=attrs.get("class"),
                        href=attrs.get("href"),
                        bounding_box=el.get("bounding_box"),
                        is_visible=bool(el.get("is_visible", True)),
                        is_enabled=bool(el.get("is_enabled", True)),
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
                return _execute_with_ref_recovery("click")

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
                self._last_exec_result = self._execute_action("wait", value=decision.value)
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
