from __future__ import annotations

from typing import Any, Dict


BLOCKED_USER_ACTION_STATUS = "BLOCKED_USER_ACTION"
BLOCKED_CAPTCHA_REASON_CODE = "blocked_captcha"
BLOCKED_EXTERNAL_SERVICE_REASON_CODE = "blocked_external_service"
BLOCKED_LOGIN_REASON_CODE = "blocked_login_gate"
BLOCKED_USER_ACTION_REASON_CODE = "blocked_user_action"

_CAPTCHA_GATE_MARKERS = (
    "captcha",
    "캡차",
    "보안문자",
    "보안 문자",
    "보안 확인",
    "보안확인",
    "인증문자",
    "security check",
    "security verification",
    "human verification",
    "verify you are human",
    "are you human",
    "unusual traffic",
    "automated queries",
    "bot wall",
    "bot-wall",
    "robot",
    "로봇",
    "자동입력 방지",
    "자동 입력 방지",
    "비정상적인 접근",
)

_USER_ACTION_MARKERS = (
    "사용자 개입",
    "사용자 입력",
    "사용자가 필요한 입력",
    "필요한 입력 제공을 취소",
    "사람의 입력",
    "human_answer",
    "목표를 더 구체적으로",
    "login required",
    "로그인 필요",
    "로그인이 필요",
)

_EXTERNAL_SERVICE_MARKERS = (
    "external_service_unavailable",
    "access denied",
    "service unavailable",
    "temporarily unavailable",
    "too many requests",
    "page not found",
    "404 not found",
    "페이지를 찾을 수 없습니다",
    "서비스 이용에 불편",
    "서비스 지연",
    "외부 서비스 오류",
    "외부 서비스 지연",
    "외부 페이지가 렌더링됐지만 접근 가능한 dom을 제공하지 않아",
    "anti-bot",
    "bot-wall",
    "challenge",
    "ret9999",
    "시스템 오류 발생",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def summary_reason_code_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    summary = row.get("summary")
    if not isinstance(summary, dict):
        return {}
    data = summary.get("reason_code_summary")
    return data if isinstance(data, dict) else {}


def _status(row: Dict[str, Any]) -> str:
    return str(row.get("status") or "").strip().upper()


def _row_blocking_text(row: Dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("reason", "captured_log", "stderr", "stdout"):
        value = row.get(key)
        if value is not None:
            parts.append(str(value))

    summary = row.get("summary")
    if isinstance(summary, dict):
        for key in (
            "final_status",
            "status",
            "reason",
            "failure_reason",
            "last_error",
            "goal_completion_source",
            "blocked_reason_code",
        ):
            value = summary.get(key)
            if value is not None:
                parts.append(str(value))
        parts.extend(str(code) for code in summary_reason_code_summary(row).keys())

    return "\n".join(parts).lower()


def is_captcha_or_security_gate(row: Dict[str, Any]) -> bool:
    if BLOCKED_CAPTCHA_REASON_CODE in {str(code).strip().lower() for code in summary_reason_code_summary(row).keys()}:
        return True
    text = _row_blocking_text(row)
    return any(marker in text for marker in _CAPTCHA_GATE_MARKERS)


def is_external_service_blocked(row: Dict[str, Any]) -> bool:
    if _status(row) == "SUCCESS":
        return False
    if BLOCKED_EXTERNAL_SERVICE_REASON_CODE in {
        str(code).strip().lower() for code in summary_reason_code_summary(row).keys()
    }:
        return True
    if "external_service_unavailable" in {
        str(code).strip().lower() for code in summary_reason_code_summary(row).keys()
    }:
        return True
    text = _row_blocking_text(row)
    return any(marker in text for marker in _EXTERNAL_SERVICE_MARKERS)


def is_blocked_user_action(row: Dict[str, Any]) -> bool:
    if _status(row) == "SUCCESS":
        return False
    summary = row.get("summary")
    if isinstance(summary, dict) and str(summary.get("final_status") or "").strip().upper() == BLOCKED_USER_ACTION_STATUS:
        return True
    if _status(row) == BLOCKED_USER_ACTION_STATUS:
        return True
    if is_captcha_or_security_gate(row):
        return True
    if is_external_service_blocked(row):
        return True
    text = _row_blocking_text(row)
    return any(marker in text for marker in _USER_ACTION_MARKERS)


def _blocked_reason_code(row: Dict[str, Any]) -> str:
    if is_captcha_or_security_gate(row):
        return BLOCKED_CAPTCHA_REASON_CODE
    if is_external_service_blocked(row):
        return BLOCKED_EXTERNAL_SERVICE_REASON_CODE
    text = _row_blocking_text(row)
    if "login required" in text or "로그인 필요" in text or "로그인이 필요" in text:
        return BLOCKED_LOGIN_REASON_CODE
    return BLOCKED_USER_ACTION_REASON_CODE


def _blocked_reason_prefix(reason_code: str) -> str:
    if reason_code == BLOCKED_CAPTCHA_REASON_CODE:
        return "CAPTCHA/security verification gate detected; user action required."
    if reason_code == BLOCKED_EXTERNAL_SERVICE_REASON_CODE:
        return "External service unavailable or access gate detected; excluded from primary benchmark."
    if reason_code == BLOCKED_LOGIN_REASON_CODE:
        return "Login gate detected; user action required."
    return "User action required."


def normalize_blocked_user_action_row(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict) or not is_blocked_user_action(row):
        return row

    normalized = dict(row)
    summary = dict(normalized.get("summary") if isinstance(normalized.get("summary"), dict) else {})
    reason_code = _blocked_reason_code(normalized)
    prefix = _blocked_reason_prefix(reason_code)
    original_reason = str(normalized.get("reason") or summary.get("reason") or "").strip()
    reason = original_reason if original_reason.startswith(prefix) else f"{prefix} {original_reason}".strip()

    rc_summary = dict(summary.get("reason_code_summary") if isinstance(summary.get("reason_code_summary"), dict) else {})
    if _safe_int(rc_summary.get(reason_code)) < 1:
        rc_summary[reason_code] = 1

    normalized["status"] = BLOCKED_USER_ACTION_STATUS
    normalized["reason"] = reason
    normalized["blocked_reason_code"] = reason_code
    summary["final_status"] = BLOCKED_USER_ACTION_STATUS
    summary["reason"] = reason
    summary["blocked_reason_code"] = reason_code
    summary["reason_code_summary"] = rc_summary
    normalized["summary"] = summary
    return normalized
