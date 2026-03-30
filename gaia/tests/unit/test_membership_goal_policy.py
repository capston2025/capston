from __future__ import annotations

import re
from types import SimpleNamespace

from gaia.src.phase4.goal_driven.dom_prompt_formatting import semantic_tags_for_element
from gaia.src.phase4.goal_driven.goal_kinds import GoalKind
from gaia.src.phase4.goal_driven.goal_policy_phase_runtime import advance_goal_policy_phase
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, DOMElement, TestGoal as GoalSpec
from gaia.src.phase4.goal_driven.policies.add_to_list import AddToListPolicy
from gaia.src.phase4.goal_driven.runtime import ActionExecResult


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _make_agent(**overrides):
    proof = {
        "precheck_present": False,
        "precheck_absent": False,
        "remove_done": False,
        "add_pending": False,
        "add_done": False,
        "readd_pending": False,
        "readd_done": False,
        "final_present_verified": False,
    }
    agent = SimpleNamespace(
        _normalize_text=_normalize_text,
        _estimate_goal_metric_from_dom=lambda _dom: None,
        _goal_policy=AddToListPolicy(),
        _goal_semantics=SimpleNamespace(
            target_terms=["포용사회와문화탐방1"],
            destination_terms=["내 시간표", "시간표"],
            requires_pre_action_membership_check=True,
            goal_kind=GoalKind.ADD_TO_LIST,
            mutation_direction="increase",
            remediation_trigger="already_present",
            already_satisfied_ok=False,
            mutate_required=True,
        ),
        _goal_policy_phase="locate_target",
        _goal_phase_intent="mutate",
        _goal_plan_requires_precheck=True,
        _goal_plan_precheck_done=False,
        _goal_plan_precheck_result="",
        _goal_plan_remediation_completed=False,
        _goal_policy_destination_anchor_seen=False,
        _goal_policy_target_seen_in_destination=False,
        _goal_policy_baseline_evidence=None,
        _goal_state_cache={
            "membership_belief": "unknown",
            "membership_confidence": 0.0,
            "target_locus": "source",
            "subgoal": "locate_target",
            "target_terms": ["포용사회와문화탐방1"],
            "destination_terms": ["내 시간표", "시간표"],
            "proof": proof,
            "contradiction_signals": [],
            "updated_at": 0.0,
        },
        _last_snapshot_evidence={"live_texts": []},
        _last_backend_post_action_snapshot={},
        _last_backend_trace={},
        _last_exec_result=None,
        _active_snapshot_id="snapshot-1",
        _active_url="https://example.com",
        _auth_resume_pending=False,
        _auth_submit_attempted=False,
        _surface_reacquire_pending=False,
        _recent_click_element_ids=[],
        _no_progress_counter=0,
        _record_reason_code=lambda _code: None,
        _contains_close_hint=lambda value: any(token in _normalize_text(value) for token in ("닫", "close", "취소", "cancel", "dismiss")),
        _element_full_selectors={},
        _element_selectors={},
        _goal_tokens={"포용사회와문화탐방1", "시간표", "추가"},
    )
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


def _make_goal() -> GoalSpec:
    return GoalSpec(
        id="goal-1",
        name="포용사회와문화탐방1 바로 추가",
        description="포용사회와문화탐방1 과목을 바로 추가하고 이미 있으면 삭제 후 다시 추가",
        success_criteria=["내 시간표에 포용사회와문화탐방1 표시"],
    )


def test_auth_resolved_resets_stale_precheck_and_restarts_membership_check():
    agent = _make_agent(
        _goal_policy_phase="handle_auth_or_block",
        _goal_phase_intent="auth",
        _goal_plan_precheck_done=True,
        _goal_plan_precheck_result="absent",
        _auth_submit_attempted=True,
    )
    agent._goal_state_cache["proof"]["precheck_absent"] = True
    decision = ActionDecision(action=ActionType.CLICK, element_id=1, reasoning="submit login")
    dom = [DOMElement(id=1, tag="button", role="button", text="메인으로", is_visible=True, is_enabled=True)]

    result = advance_goal_policy_phase(
        agent,
        goal=_make_goal(),
        decision=decision,
        success=True,
        changed=True,
        dom_elements=dom,
    )

    assert result["event"] == "auth_resolved"
    assert result["current_phase"] == "precheck_destination_membership"
    assert agent._goal_plan_precheck_done is False
    assert agent._goal_plan_precheck_result == ""
    assert agent._goal_state_cache["proof"]["precheck_present"] is False
    assert agent._goal_state_cache["proof"]["precheck_absent"] is False


def test_duplicate_membership_signal_promotes_to_remediation_branch():
    agent = _make_agent(
        _goal_policy_phase="locate_target",
        _goal_phase_intent="mutate",
        _last_exec_result=ActionExecResult(
            success=True,
            effective=True,
            reason_code="ok",
            reason="ok",
            state_change={"live_texts_after": ["이미 시간표에 추가된 과목입니다."]},
        ),
    )
    decision = ActionDecision(action=ActionType.CLICK, element_id=1, reasoning="바로 추가")
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="button",
            text="바로 추가",
            container_name="포용사회와문화탐방1",
            context_text="미배정",
            is_visible=True,
            is_enabled=True,
        )
    ]

    result = advance_goal_policy_phase(
        agent,
        goal=_make_goal(),
        decision=decision,
        success=True,
        changed=True,
        dom_elements=dom,
    )

    assert result["event"] == "possible_present_noop"
    assert result["current_phase"] == "remediate_existing_membership"
    assert agent._goal_plan_precheck_done is True
    assert agent._goal_plan_precheck_result == "present"
    assert agent._goal_state_cache["proof"]["precheck_present"] is True
    assert agent._goal_state_cache["proof"]["precheck_absent"] is False


def test_semantic_tags_mark_source_mutation_candidate():
    agent = _make_agent()
    element = DOMElement(
        id=10,
        tag="button",
        role="button",
        text="바로 추가",
        container_name="포용사회와문화탐방1",
        context_text="미배정",
        is_visible=True,
        is_enabled=True,
    )

    tags = semantic_tags_for_element(agent, element)

    assert "target_match" in tags
    assert "source_mutation_candidate" in tags


def test_semantic_tags_mark_destination_reveal_candidate_without_forcing_action():
    agent = _make_agent()
    element = DOMElement(
        id=20,
        tag="button",
        role="button",
        text="내 시간표 보기 (10)",
        context_text="시간표 메뉴",
        is_visible=True,
        is_enabled=True,
    )

    tags = semantic_tags_for_element(agent, element)

    assert "destination_reveal_candidate" in tags
    assert "close_like" not in tags


def test_semantic_tags_mark_close_like_separately_from_destination_reveal():
    agent = _make_agent()
    element = DOMElement(
        id=15,
        tag="button",
        role="button",
        text="닫기",
        aria_label="내 시간표 닫기",
        context_text="시간표 패널",
        is_visible=True,
        is_enabled=True,
    )

    tags = semantic_tags_for_element(agent, element)

    assert "close_like" in tags
    assert "destination_reveal_candidate" not in tags
