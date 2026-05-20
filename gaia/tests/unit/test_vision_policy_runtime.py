from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.models import DOMElement
from gaia.src.phase4.goal_driven.vision_policy_runtime import (
    dom_first_vision_enabled,
    looks_like_wait_needs_visual_context,
    should_capture_decision_screenshot,
)


def _goal(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=text,
        description=text,
        success_criteria=[text],
        failure_criteria=[],
    )


def test_dom_first_policy_skips_screenshot_for_semantically_rich_dom() -> None:
    dom = [
        DOMElement(id=1, tag="a", text="상품 상세 보기", context_text="노트북 파우치 13인치 가격 배송"),
        DOMElement(id=2, tag="button", text="장바구니", context_text="상품 구매 영역"),
        DOMElement(id=3, tag="button", text="구매하기", context_text="네이버페이 구매 버튼"),
        DOMElement(id=4, tag="select", text="색상 선택", options=[{"text": "블랙"}, {"text": "블루"}]),
    ]

    policy = should_capture_decision_screenshot(
        goal=_goal("노트북 파우치 구매 직전까지 진행"),
        dom_elements=dom,
        env={"GAIA_DOM_FIRST_VISION": "1"},
    )

    assert policy.use_screenshot is False
    assert policy.reason == "dom_semantic_enough"


def test_dom_first_policy_can_be_disabled() -> None:
    policy = should_capture_decision_screenshot(
        goal=_goal("검색 결과 확인"),
        dom_elements=[
            DOMElement(id=1, tag="button", text="검색"),
            DOMElement(id=2, tag="a", text="결과 상세"),
            DOMElement(id=3, tag="button", text="필터"),
            DOMElement(id=4, tag="button", text="정렬"),
        ],
        env={"GAIA_DOM_FIRST_VISION": "0"},
    )

    assert dom_first_vision_enabled({"GAIA_DOM_FIRST_VISION": "0"}) is False
    assert policy.use_screenshot is True
    assert policy.reason == "dom_first_disabled"


def test_dom_first_policy_keeps_readonly_visibility_text_only() -> None:
    policy = should_capture_decision_screenshot(
        goal=_goal("순위표 상위 3개 팀 확인"),
        dom_elements=[],
        readonly_visibility_goal=True,
    )

    assert policy.use_screenshot is False
    assert policy.reason == "readonly_visibility_goal"


def test_dom_first_policy_uses_vision_for_visual_goal_or_sparse_surface() -> None:
    visual_goal = should_capture_decision_screenshot(
        goal=_goal("강의 재생 화면이 실제로 재생 중인지 확인"),
        dom_elements=[
            DOMElement(id=1, tag="button", text="재생"),
            DOMElement(id=2, tag="video", text=""),
        ],
    )
    sparse_canvas = should_capture_decision_screenshot(
        goal=_goal("게임 상태 확인"),
        dom_elements=[DOMElement(id=1, tag="canvas", text="")],
    )

    assert visual_goal.use_screenshot is True
    assert visual_goal.reason == "goal_requires_visual_context"
    assert sparse_canvas.use_screenshot is True
    assert sparse_canvas.reason == "sparse_visual_surface"


def test_dom_first_policy_uses_vision_for_recovery_and_captcha() -> None:
    recovery = should_capture_decision_screenshot(
        goal=_goal("상품 주문서 이동"),
        dom_elements=[
            DOMElement(id=1, tag="button", text="주문하기"),
            DOMElement(id=2, tag="button", text="장바구니"),
        ],
        no_progress_counter=1,
    )
    captcha = should_capture_decision_screenshot(
        goal=_goal("로그인"),
        dom_elements=[DOMElement(id=1, tag="div", text="로봇이 아닙니다")],
    )

    assert recovery.use_screenshot is True
    assert recovery.reason == "recovery_after_no_progress"
    assert captcha.use_screenshot is True
    assert captcha.reason == "captcha_surface_signal"


def test_wait_reasoning_can_escalate_text_only_decision_to_vision() -> None:
    reasoning = "현재 DOM 정보만으로는 버튼 상태를 확인할 수 없어 화면을 다시 확인하기 위해 잠시 대기합니다."

    assert looks_like_wait_needs_visual_context(reasoning) is True
