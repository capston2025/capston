"""Auth/login hint and gate helpers for GoalDrivenAgent."""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional


NormalizeTextFn = Callable[[Optional[str]], str]
ContainsLoginHintFn = Callable[[Optional[str]], bool]


def contains_login_hint(value: Optional[str], normalize_text: NormalizeTextFn) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    hints = (
        "로그인",
        "sign in",
        "log in",
        "login",
        "이메일",
        "email",
        "비밀번호",
        "password",
        "아이디",
        "username",
        "인증",
        "auth",
    )
    return any(h in text for h in hints)


def contains_close_hint(value: Optional[str], normalize_text: NormalizeTextFn) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    hints = ("닫", "close", "취소", "cancel", "dismiss")
    if any(h in text for h in hints):
        return True
    tokens = [tok for tok in re.split(r"[^a-zA-Z0-9가-힣×]+", text) if tok]
    return any(tok in {"x", "×"} for tok in tokens)


def is_numeric_page_label(value: Optional[str]) -> bool:
    text = (value or "").strip()
    return bool(re.fullmatch(r"\d{1,3}", text))


def is_navigational_href(value: Optional[str]) -> bool:
    href = (value or "").strip().lower()
    if not href:
        return False
    if href.startswith("#") or href.startswith("javascript:"):
        return False
    if href.startswith("mailto:") or href.startswith("tel:"):
        return False
    return True


def contains_next_pagination_hint(value: Optional[str], normalize_text: NormalizeTextFn) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    if any(token in text for token in ("prev", "previous", "back", "이전", "앞", "prior")):
        return False
    if any(ch in text for ch in ("›", "»", "→", "⟩")):
        return True
    if re.search(r"(?:^|[\s\-_:/\[\]()])next(?:$|[\s\-_:/\[\]()])", text):
        return True
    if any(
        token in text
        for token in (
            "다음",
            "다음페이지",
            "다음 페이지",
            "다음으로",
            "nextpage",
            "page-next",
            "pager-next",
            "nav-next",
            "go-next",
        )
    ):
        return True
    if text.endswith(">") and len(text) <= 5:
        return True
    return False


def recover_dom_after_empty(
    *,
    runtime_phase: str,
    no_progress_counter: int,
    goal_start_url: str,
    analyze_dom_fn: Callable[[], List[Any]],
    log_fn: Callable[[str], None],
    execute_action_fn: Callable[[str], Any],
) -> List[Any]:
    for attempt in range(2):
        time.sleep(0.8 + (0.4 * attempt))
        dom = analyze_dom_fn()
        if dom:
            return dom
    if (runtime_phase or "").upper() in {"AUTH", "COMPOSE", "APPLY", "VERIFY"} or no_progress_counter > 0:
        log_fn("🛠️ DOM 복구: 현재 컨텍스트 유지(시작 URL 강제 복귀 생략)")
        return []
    start_url = str(goal_start_url or "").strip()
    if start_url:
        log_fn("🛠️ DOM 복구: 시작 URL로 재동기화 시도")
        _ = execute_action_fn(start_url)
        time.sleep(1.2)
        dom = analyze_dom_fn()
        if dom:
            return dom
    return []


def infer_runtime_phase(
    *,
    dom_elements: List[Any],
    is_login_gate_fn: Callable[[List[Any]], bool],
    is_collect_constraint_unmet: bool,
    progress_counter: int,
    runtime_phase: str,
) -> str:
    if is_login_gate_fn(dom_elements):
        return "AUTH"
    if is_collect_constraint_unmet:
        return "COLLECT"
    if progress_counter > 0:
        if runtime_phase in {"COLLECT", "COMPOSE"}:
            return "APPLY"
        if runtime_phase:
            return runtime_phase
    return runtime_phase or "COLLECT"


def is_login_gate(
    dom_elements: List[Any],
    *,
    normalize_text: NormalizeTextFn,
    contains_login_hint_fn: ContainsLoginHintFn,
) -> bool:
    auth_hits = 0
    has_password_field = False
    has_id_or_email_field = False
    modal_auth_hits = 0
    modal_shell_hits = 0
    for el in dom_elements:
        text = normalize_text(getattr(el, "text", None))
        placeholder = normalize_text(getattr(el, "placeholder", None))
        aria = normalize_text(getattr(el, "aria_label", None))
        role = normalize_text(getattr(el, "role", None))
        typ = normalize_text(getattr(el, "type", None))
        class_name = normalize_text(getattr(el, "class_name", None))
        aria_modal = normalize_text(getattr(el, "aria_modal", None))

        fields = [text, placeholder, aria, role]
        if any(contains_login_hint_fn(v) for v in fields):
            auth_hits += 1

        if typ == "password" or "password" in placeholder or "비밀번호" in placeholder or "password" in aria:
            has_password_field = True

        if (
            typ in {"email", "text"}
            and any(k in (placeholder or text or aria) for k in ("email", "이메일", "아이디", "username", "user id"))
        ):
            has_id_or_email_field = True

        modal_attr_blob = " ".join([role, class_name, aria_modal])
        is_modal_shell = (
            role in {"dialog", "alertdialog"}
            or aria_modal == "true"
            or any(k in modal_attr_blob for k in ("modal", "dialog", "popup", "sheet", "drawer", "overlay"))
        )
        if is_modal_shell:
            modal_shell_hits += 1
        if is_modal_shell and any(k in " ".join(fields) for k in ("로그인", "회원가입", "signin", "signup", "login", "register", "auth")):
            modal_auth_hits += 1

    if has_password_field and has_id_or_email_field and modal_shell_hits > 0:
        return True
    if has_password_field and has_id_or_email_field and auth_hits >= 8 and len(dom_elements) <= 120:
        return True
    if modal_shell_hits >= 2 and modal_auth_hits >= 2 and auth_hits >= 4 and has_password_field and has_id_or_email_field:
        return True
    return False


def is_compact_auth_page(
    dom_elements: List[Any],
    *,
    normalize_text: NormalizeTextFn,
    contains_login_hint_fn: ContainsLoginHintFn,
) -> bool:
    auth_hits = 0
    has_password_field = False
    has_id_or_email_field = False
    for el in dom_elements:
        text = normalize_text(getattr(el, "text", None))
        placeholder = normalize_text(getattr(el, "placeholder", None))
        aria = normalize_text(getattr(el, "aria_label", None))
        typ = normalize_text(getattr(el, "type", None))
        if any(contains_login_hint_fn(v) for v in (text, placeholder, aria)):
            auth_hits += 1
        if typ == "password" or "password" in placeholder or "비밀번호" in placeholder or "password" in aria:
            has_password_field = True
        if (
            typ in {"email", "text"}
            and any(k in (placeholder or text or aria) for k in ("email", "이메일", "아이디", "username", "user id"))
        ):
            has_id_or_email_field = True
    return bool(has_password_field and has_id_or_email_field and auth_hits >= 6 and len(dom_elements) <= 120)


def goal_requires_login_interaction(goal: Any, contains_login_hint_fn: ContainsLoginHintFn) -> bool:
    if contains_login_hint_fn(getattr(goal, "name", None)) or contains_login_hint_fn(getattr(goal, "description", None)):
        return True
    for criterion in getattr(goal, "success_criteria", []) or []:
        if contains_login_hint_fn(str(criterion)):
            return True
    return False
