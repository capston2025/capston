from __future__ import annotations

from gaia.src.phase4.goal_driven.auth_hints import contains_close_hint
from gaia.src.phase4.goal_driven.modal_runtime import pick_modal_unblock_element
from gaia.src.phase4.goal_driven.models import DOMElement


class _ModalAgent:
    @staticmethod
    def _normalize_text(value: str | None) -> str:
        return (value or "").strip().lower()

    @classmethod
    def _contains_close_hint(cls, value: str | None) -> bool:
        return contains_close_hint(value, cls._normalize_text)


def test_contains_close_hint_recognizes_public_notice_dismiss_copy() -> None:
    assert _ModalAgent._contains_close_hint("오늘 하루 보지 않기")
    assert _ModalAgent._contains_close_hint("다시 보지 않기")
    assert _ModalAgent._contains_close_hint("button.btnClose")


def test_pick_modal_unblock_selects_notice_dismiss_button() -> None:
    elements = [
        DOMElement(
            id=1,
            tag="div",
            text="이벤트 안내",
            role="dialog",
            aria_modal="true",
            class_name="main-popup",
            bounding_box={"x": 100, "y": 100, "width": 520, "height": 360},
        ),
        DOMElement(
            id=2,
            tag="a",
            text="자세히 보기",
            role="link",
            bounding_box={"x": 160, "y": 220, "width": 120, "height": 38},
        ),
        DOMElement(
            id=3,
            tag="button",
            text="오늘 하루 보지 않기",
            role="button",
            bounding_box={"x": 360, "y": 405, "width": 180, "height": 36},
        ),
    ]

    selected_id = pick_modal_unblock_element(
        _ModalAgent,
        elements,
        selector_map={2: "a.event-link", 3: "button.notice-dismiss"},
    )

    assert selected_id == 3


def test_pick_modal_unblock_selects_unlabeled_top_right_icon() -> None:
    elements = [
        DOMElement(
            id=1,
            tag="div",
            text="공지사항",
            role="dialog",
            aria_modal="true",
            class_name="notice-popup",
            bounding_box={"x": 100, "y": 100, "width": 520, "height": 360},
        ),
        DOMElement(
            id=2,
            tag="button",
            text="",
            role="button",
            bounding_box={"x": 568, "y": 108, "width": 32, "height": 32},
        ),
        DOMElement(
            id=3,
            tag="a",
            text="이벤트 자세히 보기",
            role="link",
            bounding_box={"x": 180, "y": 240, "width": 160, "height": 40},
        ),
    ]

    selected_id = pick_modal_unblock_element(
        _ModalAgent,
        elements,
        selector_map={2: "button.popup-icon", 3: "a.event-link"},
    )

    assert selected_id == 2


def test_pick_modal_unblock_does_not_close_security_gate_dialog() -> None:
    elements = [
        DOMElement(
            id=1,
            tag="div",
            text="Access Denied 보안 확인 후 다시 시도하세요.",
            role="dialog",
            aria_modal="true",
            class_name="security-popup",
            bounding_box={"x": 100, "y": 100, "width": 520, "height": 360},
        ),
        DOMElement(
            id=2,
            tag="button",
            text="닫기",
            role="button",
            bounding_box={"x": 568, "y": 108, "width": 32, "height": 32},
        ),
    ]

    selected_id = pick_modal_unblock_element(
        _ModalAgent,
        elements,
        selector_map={2: "button.btn-close"},
    )

    assert selected_id is None
