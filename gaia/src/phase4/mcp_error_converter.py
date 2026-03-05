"""AI-Friendly Error Converter

Playwright 에러를 LLM이 이해하고 행동 가능한 메시지로 변환합니다.
OpenClaw의 toAIFriendlyError 패턴을 Python으로 구현.
"""
from __future__ import annotations

import re


class _StrError(Exception):
    """to_ai_friendly_error_str에서 문자열을 감싸기 위한 내부 예외"""


def to_ai_friendly_error(
    exc: BaseException,
    *,
    ref_id: str = "",
    selector: str = "",
) -> str:
    """Playwright 에러 메시지를 LLM 친화적 actionable 메시지로 변환합니다.

    Args:
        exc: 원본 에러
        ref_id: 요소의 ref ID (있으면 메시지에 포함)
        selector: CSS 셀렉터 (있으면 메시지에 포함)

    Returns:
        LLM이 다음 행동을 결정할 수 있는 한국어 가이드 메시지
    """
    message = str(exc)
    identifier = ref_id or selector or "대상 요소"

    # strict mode violation -> 다중 요소 매칭
    if "strict mode violation" in message:
        count_match = re.search(r"resolved to (\d+) elements", message)
        count = count_match.group(1) if count_match else "여러"
        return (
            f'"{identifier}"가 {count}개의 요소와 매칭됩니다. '
            "최신 snapshot을 다시 촬영하여 정확한 ref를 확인하세요."
        )

    # Timeout + not visible 조합 -> 요소 없음/숨김
    if _is_visibility_timeout(message):
        return (
            f'"{identifier}"를 찾을 수 없거나 표시되지 않습니다. '
            "최신 snapshot을 기반으로 요소를 다시 확인하세요."
        )

    # intercepts pointer events -> 다른 요소에 가려짐
    if _is_pointer_blocked(message):
        return (
            f'"{identifier}"가 다른 요소에 가려져 상호작용할 수 없습니다. '
            "스크롤하거나 오버레이를 닫은 후 다시 시도하세요."
        )

    # detached from DOM -> 요소가 DOM에서 제거됨
    if "detached" in message.lower() and "dom" in message.lower():
        return (
            f'"{identifier}"가 DOM에서 제거되었습니다. '
            "페이지 상태가 변경되었으므로 최신 snapshot을 촬영하세요."
        )

    # frame was detached -> iframe/frame 제거
    if "frame was detached" in message.lower():
        return (
            "해당 프레임이 제거되었습니다. "
            "페이지가 전환되었을 수 있으니 최신 snapshot을 촬영하세요."
        )

    # navigation interrupted -> 네비게이션 발생
    if "navigation" in message.lower() and (
        "interrupted" in message.lower() or "navigating" in message.lower()
    ):
        return (
            "액션 수행 중 페이지 네비게이션이 발생했습니다. "
            "새 페이지에서 최신 snapshot을 촬영하세요."
        )

    # 일반 타임아웃
    if "timeout" in message.lower() or "timed out" in message.lower():
        timeout_match = re.search(r"(\d+)ms", message)
        timeout_info = f" ({timeout_match.group(1)}ms)" if timeout_match else ""
        return (
            f'"{identifier}" 액션이 타임아웃되었습니다{timeout_info}. '
            "요소가 로딩 중이거나 비활성 상태일 수 있습니다. "
            "잠시 후 최신 snapshot을 기반으로 다시 시도하세요."
        )

    # 기본: 원본 에러 메시지 유지하되 접두어 추가
    return f"액션 실패 [{type(exc).__name__}]: {message}"


def _is_visibility_timeout(message: str) -> bool:
    """Timeout + visibility 관련 에러인지 확인"""
    lower = message.lower()
    has_timeout = "timeout" in lower or "waiting for" in lower
    has_visibility = (
        "to be visible" in lower
        or "not visible" in lower
        or "to be attached" in lower
        or "waiting for locator" in lower
    )
    return has_timeout and has_visibility


def _is_pointer_blocked(message: str) -> bool:
    """포인터 이벤트 차단 에러인지 확인"""
    lower = message.lower()
    return (
        "intercepts pointer events" in lower
        or "not receive pointer events" in lower
        or "element is not visible" in lower
    )


def to_ai_friendly_error_str(
    message: str,
    *,
    ref_id: str = "",
    selector: str = "",
) -> str:
    """문자열 에러 메시지를 LLM 친화적으로 변환합니다.

    Exception 객체 없이 문자열만 가지고 있을 때 사용.
    """
    return to_ai_friendly_error(
        _StrError(message),
        ref_id=ref_id,
        selector=selector,
    )
