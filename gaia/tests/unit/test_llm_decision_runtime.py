from __future__ import annotations

import json
from types import SimpleNamespace

from gaia.src.phase4.goal_driven.agent import GoalDrivenAgent
from gaia.src.phase4.goal_driven.llm_decision_runtime import (
    _build_active_surface_summary,
    _build_auth_surface_summary,
    _build_feedback_signal_summary,
    _build_goal_state_summary,
    _is_forbidden_global_control,
    _build_target_destination_summary,
)
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, DOMElement


class _FakeAgent:
    def __init__(self) -> None:
        self._goal_semantics = SimpleNamespace(target_terms=["포용사회와문화탐방1"], destination_terms=["시간표", "내 시간표"])
        self._last_snapshot_evidence = {}

    def _normalize_text(self, value: object) -> str:
        return str(value or "").strip().lower()


def test_build_goal_state_summary_suppresses_low_confidence_belief_in_thin_wrapper_mode():
    summary, meta = _build_goal_state_summary(
        {
            "membership_belief": "absent",
            "membership_confidence": 0.5,
            "target_locus": "source",
            "subgoal": "source_add",
            "proof": {
                "precheck_present": False,
                "precheck_absent": False,
                "add_done": False,
            },
            "contradiction_signals": [],
        },
        thin_wrapper_mode=True,
    )

    assert summary == "불확실"
    assert meta["membership_hint_included"] is False
    assert meta["suppressed_low_confidence_belief"] is True


def test_build_goal_state_summary_keeps_membership_belief_in_classic_mode():
    summary, meta = _build_goal_state_summary(
        {
            "membership_belief": "absent",
            "membership_confidence": 0.5,
            "target_locus": "source",
            "subgoal": "source_add",
            "proof": {
                "precheck_present": False,
                "precheck_absent": False,
                "add_done": False,
            },
            "contradiction_signals": [],
        },
        thin_wrapper_mode=False,
    )

    parsed = json.loads(summary)
    assert parsed["membership_belief"] == "absent"
    assert parsed["subgoal"] is None
    assert meta["membership_hint_included"] is True


def test_build_auth_surface_summary_describes_auth_controls_and_background_ctas():
    agent = _FakeAgent()
    dom = [
        DOMElement(
            id=4,
            tag="input",
            role="textbox",
            text="아이디를 입력하세요",
            aria_label="아이디를 입력하세요",
            placeholder="아이디를 입력하세요",
            title="아이디를 입력하세요",
            ref_id="e677",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="textbox",
            role_ref_name="아이디를 입력하세요",
        ),
        DOMElement(
            id=6,
            tag="input",
            role="textbox",
            text="비밀번호를 입력하세요",
            aria_label="비밀번호를 입력하세요",
            placeholder="비밀번호를 입력하세요",
            title="비밀번호를 입력하세요",
            ref_id="e680",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="textbox",
            role_ref_name="비밀번호를 입력하세요",
        ),
        DOMElement(
            id=7,
            tag="button",
            role="button",
            text="로그인",
            aria_label="로그인",
            title="로그인",
            ref_id="e681",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="로그인",
        ),
        DOMElement(
            id=74,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            title="바로 추가",
            ref_id="e213",
            container_name="검색 결과(총 2,894개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(HUSS국립부경대)포용사회와문화탐방1 | 검색 결과",
            role_ref_role="button",
            role_ref_name="바로 추가",
        ),
    ]

    summary = _build_auth_surface_summary(
        agent,
        dom,
        {"username": "202101681", "password": "qwer"},
    )

    assert "identifier input: ref=e677" in summary
    assert 'value="202101681"' in summary
    assert "password input: ref=e680" in summary
    assert 'value="qwer"' in summary
    assert 'submit candidate: ref=e681 label="로그인"' in summary
    assert 'background CTA: ref=e213 "바로 추가"' in summary


def test_build_feedback_signal_summary_prefers_destination_inspection_over_close():
    agent = _FakeAgent()
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X | 과목 검색",
            aria_label="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X | 과목 검색",
            ref_id="e4",
            container_role="generic",
            container_source="openclaw-role-tree",
            context_text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X",
            role_ref_role="generic",
            role_ref_name="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\"",
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="X",
            aria_label="X",
            title="X",
            ref_id="e5",
            container_role="generic",
            container_source="openclaw-role-tree",
            context_text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\"",
            role_ref_role="button",
            role_ref_name="X",
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="내 시간표 보기 (10)",
            aria_label="내 시간표 보기 (10)",
            title="내 시간표 보기 (10)",
            ref_id="e724",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="내 시간표 보기 (10)",
        ),
    ]

    summary = _build_feedback_signal_summary(agent, dom)

    assert 'result signal: ref=e4' in summary
    assert 'inspect destination: ref=e724 label="내 시간표 보기 (10)"' in summary
    assert 'dismiss only: ref=e5 label="X"' in summary


def test_build_feedback_signal_summary_warns_when_feedback_names_different_course():
    agent = _FakeAgent()
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="\"'(HUSS국립부경대)과거사청산과포용의문화' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X | 내 시간표",
            aria_label="\"'(HUSS국립부경대)과거사청산과포용의문화' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X | 내 시간표",
            ref_id="e4",
            container_role="generic",
            container_source="openclaw-role-tree",
            context_text="\"'(HUSS국립부경대)과거사청산과포용의문화' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X",
            role_ref_role="generic",
            role_ref_name="\"'(HUSS국립부경대)과거사청산과포용의문화' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\"",
        ),
    ]

    summary = _build_feedback_signal_summary(agent, dom)

    assert "다른 과목/상태" in summary
    assert "삭제 대상을 정하지 마세요" in summary


def test_build_feedback_signal_summary_treats_success_toast_as_provisional_until_destination_checked():
    agent = _FakeAgent()
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\" | X | 과목 검색",
            aria_label="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\" | X | 과목 검색",
            ref_id="e4",
            container_role="generic",
            container_source="openclaw-role-tree",
            context_text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\" | X",
            role_ref_role="generic",
            role_ref_name="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\"",
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="X",
            aria_label="X",
            title="X",
            ref_id="e5",
            container_role="generic",
            container_source="openclaw-role-tree",
            context_text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\"",
            role_ref_role="button",
            role_ref_name="X",
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="내 시간표 보기 (10)",
            aria_label="내 시간표 보기 (10)",
            title="내 시간표 보기 (10)",
            ref_id="e724",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="내 시간표 보기 (10)",
        ),
    ]

    summary = _build_feedback_signal_summary(agent, dom)

    assert 'result signal: ref=e4' in summary
    assert 'inspect destination: ref=e724 label="내 시간표 보기 (10)"' in summary
    assert "토스트/스낵바는 약한 진행 신호" in summary


def test_forbidden_global_control_does_not_block_destination_reveal_with_logout_in_context():
    agent = _FakeAgent()
    element = DOMElement(
        id=10,
        tag="button",
        role="button",
        text="내 시간표 보기 (9)",
        aria_label="내 시간표 보기 (9)",
        title="내 시간표 보기 (9)",
        ref_id="e1092",
        container_role="generic",
        container_source="openclaw-role-tree",
        context_text="X | 과목 검색 | 로그아웃",
        role_ref_role="button",
        role_ref_name="내 시간표 보기 (9)",
    )
    decision = ActionDecision(action=ActionType.CLICK, ref_id="e1092")

    assert _is_forbidden_global_control(agent, element, decision) is False


def test_build_active_surface_summary_describes_foreground_surface_and_occluded_background():
    agent = _FakeAgent()
    agent._last_snapshot_evidence = {"modal_open": True}
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="내 시간표",
            aria_label="내 시간표",
            title="내 시간표",
            ref_id="e704",
            container_role="generic",
            container_source="openclaw-role-tree",
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            ref_id="e706",
            container_role="generic",
            container_source="openclaw-role-tree",
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="시간표에서 제거",
            aria_label="시간표에서 제거",
            title="시간표에서 제거",
            ref_id="e753",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="시간표에서 제거",
        ),
        DOMElement(
            id=4,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            title="바로 추가",
            ref_id="e72",
            container_name="검색 결과(총 2,894개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(HUSS국립부경대)포용사회와문화탐방1 | 검색 결과",
            role_ref_role="button",
            role_ref_name="바로 추가",
        ),
    ]

    summary = _build_active_surface_summary(agent, dom)

    assert 'active surface: ref=e704 label="내 시간표"' in summary
    assert 'exit surface: ref=e706 label="[icon-only]"' in summary
    assert 'background CTA behind surface: ref=e72 "바로 추가"' in summary


def test_build_target_destination_summary_prefers_remove_after_target_row():
    agent = _FakeAgent()
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="button",
            text="시간표에서 제거",
            aria_label="시간표에서 제거",
            title="시간표에서 제거",
            ref_id="e1046",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="시간표에서 제거",
        ),
        DOMElement(
            id=2,
            tag="heading",
            role="heading",
            text="(HUSS국립부경대)포용사회와문화탐방1",
            aria_label="(HUSS국립부경대)포용사회와문화탐방1",
            title="(HUSS국립부경대)포용사회와문화탐방1",
            ref_id="e1054",
            container_role="generic",
            container_source="openclaw-role-tree",
            context_text="전심 | 1학점",
            role_ref_role="heading",
            role_ref_name="(HUSS국립부경대)포용사회와문화탐방1",
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="시간표에서 제거",
            aria_label="시간표에서 제거",
            title="시간표에서 제거",
            ref_id="e1082",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="시간표에서 제거",
        ),
    ]

    summary = _build_target_destination_summary(agent, dom)

    assert 'target evidence in destination: ref=e1054' in summary
    assert 'preferred target-row remove candidate: ref=e1082 label="시간표에서 제거"' in summary


def test_decision_signature_distinguishes_ref_based_clicks():
    first = ActionDecision(action=ActionType.CLICK, ref_id="e72", element_id=None, value="")
    second = ActionDecision(action=ActionType.CLICK, ref_id="e1046", element_id=None, value="")

    assert GoalDrivenAgent._decision_signature(first) != GoalDrivenAgent._decision_signature(second)
