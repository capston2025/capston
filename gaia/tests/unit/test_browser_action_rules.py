from __future__ import annotations

from gaia.src.phase4.goal_driven.browser_action_rules import (
    ANTI_LOOP_RULES,
    CONTEXT_SHIFT_RULES,
    DIALOG_AVOIDANCE_RULES,
    DOM_TRUST_RULES,
    GOAL_COMPLETION_RULES,
    LOADING_STATE_RULES,
    MEDIA_PLAYBACK_RULES,
    RESULT_RECOVERY_RULES,
    STALE_REF_RULES,
    _detect_repeated_failure,
    _detect_repeated_wait,
    build_browser_action_rules_block,
    build_browser_action_rules_for_agent,
    slice_recent_prompt_items,
)


class _FakeAgent:
    def __init__(self) -> None:
        self._action_history: list[str] = []
        self._action_feedback: list[str] = []


def test_all_rule_lists_are_nonempty():
    """각 규칙 카테고리에 최소 1개 이상의 규칙이 있다."""
    for rule_list in (
        ANTI_LOOP_RULES,
        STALE_REF_RULES,
        LOADING_STATE_RULES,
        MEDIA_PLAYBACK_RULES,
        DIALOG_AVOIDANCE_RULES,
        CONTEXT_SHIFT_RULES,
        DOM_TRUST_RULES,
        GOAL_COMPLETION_RULES,
    ):
        assert len(rule_list) >= 1


def test_no_domain_specific_keywords():
    """규칙에 domain-specific 키워드가 없다."""
    domain_keywords = ["학점", "위시리스트", "시간표", "포용사회", "과목", "timetable", "wishlist"]
    block = build_browser_action_rules_block()
    for keyword in domain_keywords:
        assert keyword not in block, f"domain keyword '{keyword}' found in rules"


def test_build_browser_action_rules_block_structure():
    """규칙 블록이 올바른 헤더와 번호 매기기를 포함한다."""
    block = build_browser_action_rules_block()
    assert block.startswith("## 작업 규칙")
    assert "1. " in block
    lines = [line for line in block.splitlines() if line.strip().startswith(("1.", "2.", "3."))]
    assert len(lines) >= 3


def test_build_browser_action_rules_block_total_rule_count():
    """전체 규칙 수가 모든 카테고리의 합과 일치한다."""
    total = (
        len(DOM_TRUST_RULES)
        + len(ANTI_LOOP_RULES)
        + len(STALE_REF_RULES)
        + len(LOADING_STATE_RULES)
        + len(RESULT_RECOVERY_RULES)
        + len(MEDIA_PLAYBACK_RULES)
        + len(DIALOG_AVOIDANCE_RULES)
        + len(CONTEXT_SHIFT_RULES)
        + len(GOAL_COMPLETION_RULES)
    )
    block = build_browser_action_rules_block()
    numbered_lines = [
        line
        for line in block.splitlines()
        if line.strip() and line.strip()[0].isdigit() and ". " in line
    ]
    assert len(numbered_lines) == total


def test_detect_repeated_wait_true():
    assert _detect_repeated_wait(["click(e1)", "wait", "wait"]) is True


def test_detect_repeated_wait_false_not_enough():
    assert _detect_repeated_wait(["wait"]) is False


def test_detect_repeated_wait_false_mixed():
    assert _detect_repeated_wait(["wait", "click(e1)"]) is False


def test_detect_repeated_failure_true():
    assert _detect_repeated_failure(
        ["click(e1)", "click(e1)"],
        ["no-op: element not found", "fail: element not found"],
    ) is True


def test_detect_repeated_failure_false_success():
    assert _detect_repeated_failure(
        ["click(e1)", "click(e2)"],
        ["success", "fail: not found"],
    ) is False


def test_detect_repeated_failure_false_not_enough():
    assert _detect_repeated_failure(["click"], ["fail"]) is False


def test_build_for_agent_no_warnings():
    """경고 조건이 없으면 긴급 행동 경고가 포함되지 않는다."""
    agent = _FakeAgent()
    agent._action_history = ["click(e1)", "fill(e2)"]
    agent._action_feedback = ["success", "success"]
    result = build_browser_action_rules_for_agent(agent)
    assert "긴급 행동 경고" not in result
    assert "## 작업 규칙" in result


def test_build_for_agent_wait_warning():
    """wait 반복 시 긴급 경고가 추가된다."""
    agent = _FakeAgent()
    agent._action_history = ["wait", "wait"]
    agent._action_feedback = ["success", "success"]
    result = build_browser_action_rules_for_agent(agent)
    assert "긴급 행동 경고" in result
    assert "wait" in result.split("긴급 행동 경고")[1]


def test_build_for_agent_failure_warning():
    """반복 실패 시 긴급 경고가 추가된다."""
    agent = _FakeAgent()
    agent._action_history = ["click(e1)", "click(e1)"]
    agent._action_feedback = ["fail: not found", "fail: not found"]
    result = build_browser_action_rules_for_agent(agent)
    assert "긴급 행동 경고" in result
    assert "반복 실패" in result.split("긴급 행동 경고")[1]


def test_slice_recent_prompt_items_can_disable_limit(monkeypatch):
    monkeypatch.setenv("GAIA_LLM_RECENT_HISTORY_LIMIT", "0")
    items = ["a", "b", "c", "d", "e", "f"]
    assert slice_recent_prompt_items(items) == items


def test_build_for_agent_respects_recent_history_limit(monkeypatch):
    monkeypatch.setenv("GAIA_LLM_RECENT_HISTORY_LIMIT", "1")
    agent = _FakeAgent()
    agent._action_history = ["click(e1)", "wait", "wait"]
    agent._action_feedback = ["success", "success", "success"]
    result = build_browser_action_rules_for_agent(agent)
    assert "긴급 행동 경고" not in result


def test_rules_block_is_concise():
    """규칙 블록이 지나치게 길지 않다 (40줄 이내)."""
    block = build_browser_action_rules_block()
    assert len(block.splitlines()) <= 40


def test_rules_block_includes_media_playback_guidance():
    block = build_browser_action_rules_block()
    assert "재생/play/watch/listen" in block
    assert "viewer 진입만으로 완료 처리하지 말고" in block


def test_rules_block_includes_no_unbound_targeted_action_guidance():
    block = build_browser_action_rules_block()
    assert "click/fill/select/press" in block
    assert "ref_id 또는 element_id를 반드시 포함" in block
