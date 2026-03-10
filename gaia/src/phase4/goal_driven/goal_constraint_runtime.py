from __future__ import annotations

import re
from typing import Optional

from .models import ActionDecision, ActionType, DOMElement, TestGoal


def apply_steering_policy_on_decision(
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

def enforce_goal_constraints_on_decision(
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

    def _is_search_like_element(element: Optional[DOMElement]) -> bool:
        if element is None:
            return False
        fields = [
            element.text,
            element.aria_label,
            getattr(element, "title", None),
            element.placeholder,
            self._element_full_selectors.get(element.id),
            self._element_selectors.get(element.id),
        ]
        blob = " ".join(self._normalize_text(field) for field in fields if field)
        tag = self._normalize_text(getattr(element, "tag", None))
        etype = self._normalize_text(getattr(element, "type", None))
        if any(token in blob for token in ("검색", "search", "query", "찾기")):
            return True
        return tag in {"input", "textarea"} and etype == "search"

    if bool(self._goal_constraints.get("forbid_search_action")):
        search_like = False
        if decision.action in {ActionType.FILL, ActionType.PRESS, ActionType.CLICK}:
            search_like = _is_search_like_element(selected_element)
            if not search_like and decision.action == ActionType.PRESS and str(decision.value or "").strip().lower() == "enter":
                search_like = _is_search_like_element(selected_element)
        if search_like:
            alternative = self._pick_context_target_click_candidate(dom_elements, excluded_ids={int(decision.element_id) if decision.element_id is not None else -1})
            if alternative is not None:
                alt_id, alt_reason = alternative
                return ActionDecision(
                    action=ActionType.CLICK,
                    element_id=alt_id,
                    reasoning=f"{alt_reason}. 현재 화면/검색 금지 제약 때문에 검색 상호작용은 차단합니다.",
                    confidence=max(float(decision.confidence or 0.0) - 0.05, 0.0),
                    is_goal_achieved=False,
                    goal_achievement_reason=None,
                )
            return ActionDecision(
                action=ActionType.WAIT,
                reasoning="현재 화면/검색 금지 제약이 활성화되어 검색 상호작용을 차단했습니다. 현재 보이는 카드에서 타깃을 다시 찾습니다.",
                confidence=max(float(decision.confidence or 0.0) - 0.1, 0.0),
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
