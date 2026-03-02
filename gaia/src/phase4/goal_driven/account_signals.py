"""Account-related hint and signup identity helpers for GoalDrivenAgent."""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional


NormalizeTextFn = Callable[[Optional[str]], str]


def contains_logout_hint(value: Optional[str], normalize_text: NormalizeTextFn) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    hints = ("로그아웃", "log out", "logout", "sign out", "signout")
    return any(h in text for h in hints)


def contains_duplicate_account_hint(value: Optional[str], normalize_text: NormalizeTextFn) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    hints = (
        "이미 사용 중인 아이디",
        "이미 사용중인 아이디",
        "이미 사용 중",
        "아이디 중복",
        "중복된 아이디",
        "already in use",
        "already exists",
        "duplicate",
    )
    return any(h in text for h in hints)


def next_username(base: str) -> str:
    seed = re.sub(r"[^a-zA-Z0-9_]", "", (base or "").strip())
    if not seed:
        seed = "gaiauser"
    seed = seed[:20]
    suffix = int(time.time() * 1000) % 1000000
    return f"{seed}_{suffix}"


def rotate_signup_identity(goal: Any, next_username_fn: Callable[[str], str]) -> Optional[str]:
    if not isinstance(goal.test_data, dict):
        goal.test_data = {}
    current_username = str(goal.test_data.get("username") or "").strip()
    base = current_username.split("@", 1)[0] if current_username else "gaiauser"
    new_username = next_username_fn(base)
    if current_username and new_username == current_username:
        new_username = next_username_fn(f"{base}x")
    goal.test_data["username"] = new_username
    goal.test_data.setdefault("auth_mode", "signup")
    email = str(goal.test_data.get("email") or "").strip()
    if email:
        domain = email.split("@", 1)[1] if "@" in email else "example.com"
        goal.test_data["email"] = f"{new_username}@{domain}"
    return new_username


def has_duplicate_account_signal(
    *,
    state_change: Optional[Dict[str, Any]],
    dom_elements: List[Any],
    contains_duplicate_account_hint_fn: Callable[[Optional[str]], bool],
) -> bool:
    if isinstance(state_change, dict):
        live_texts = state_change.get("live_texts_after")
        if isinstance(live_texts, list):
            for text in live_texts:
                if contains_duplicate_account_hint_fn(str(text)):
                    return True
    for el in dom_elements:
        if contains_duplicate_account_hint_fn(getattr(el, "text", None)) or contains_duplicate_account_hint_fn(
            getattr(el, "aria_label", None)
        ):
            return True
    return False


def goal_allows_logout(
    active_goal_text: str,
    contains_logout_hint_fn: Callable[[Optional[str]], bool],
) -> bool:
    text = active_goal_text or ""
    if not text:
        return False
    return contains_logout_hint_fn(text)
