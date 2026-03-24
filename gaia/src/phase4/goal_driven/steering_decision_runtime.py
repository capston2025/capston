from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .models import ActionDecision, ActionType, DOMElement, TestGoal
from gaia.src.phase4.mcp_local_dispatch_runtime import execute_mcp_action


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


def _capture_screenshot(self) -> Optional[str]:
    """스크린샷 캡처"""
    try:
        response = execute_mcp_action(
            self.mcp_host_url,
            action="capture_screenshot",
            params={
                "session_id": self.session_id,
            },
            timeout=30,
        )
        data = response.payload if not hasattr(response, "json") else response.json()
        if response.status_code >= 400:
            detail = data.get("detail") or data.get("error") or getattr(response, "text", "") or "HTTP error"
            self._log(f"스크린샷 캡처 오류: HTTP {response.status_code} - {detail}")
            return None
        screenshot = data.get("screenshot")

        if screenshot and self._screenshot_callback:
            self._screenshot_callback(screenshot)

        return screenshot

    except Exception as e:
        self._log(f"스크린샷 캡처 실패: {e}")
        return None


expire_steering_policy = _expire_steering_policy
is_steering_context_valid = _is_steering_context_valid
element_steering_tags = _element_steering_tags
decision_steering_tags = _decision_steering_tags
evaluate_steering_assertions = _evaluate_steering_assertions
apply_steering_assertions_on_decision = _apply_steering_assertions_on_decision
pick_steering_candidate = _pick_steering_candidate
capture_screenshot = _capture_screenshot
