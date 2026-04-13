from __future__ import annotations

from typing import List, Optional

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
