from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional


_DISABLED_VALUES = {"0", "false", "off", "no", "disabled"}


@dataclass(frozen=True)
class DecisionVisionPolicy:
    use_screenshot: bool
    reason: str
    visible_elements: int = 0
    labeled_elements: int = 0
    semantic_chars: int = 0

    def as_trace(self) -> dict[str, Any]:
        return {
            "use_screenshot": bool(self.use_screenshot),
            "reason": self.reason,
            "visible_elements": int(self.visible_elements),
            "labeled_elements": int(self.labeled_elements),
            "semantic_chars": int(self.semantic_chars),
        }


def dom_first_vision_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if env is None else env
    raw = str(source.get("GAIA_DOM_FIRST_VISION", "1") or "1").strip().lower()
    return raw not in _DISABLED_VALUES


def should_capture_decision_screenshot(
    *,
    goal: Any,
    dom_elements: Iterable[Any],
    readonly_visibility_goal: bool = False,
    no_progress_counter: int = 0,
    ineffective_action_streak: int = 0,
    last_auth_submit_at: float = 0.0,
    now: Optional[float] = None,
    env: Optional[Mapping[str, str]] = None,
) -> DecisionVisionPolicy:
    profile = _semantic_profile(dom_elements)

    if readonly_visibility_goal:
        return DecisionVisionPolicy(False, "readonly_visibility_goal", **profile)
    if not dom_first_vision_enabled(env):
        return DecisionVisionPolicy(True, "dom_first_disabled", **profile)
    if profile["visible_elements"] <= 0:
        return DecisionVisionPolicy(True, "empty_dom", **profile)
    if int(no_progress_counter or 0) > 0 or int(ineffective_action_streak or 0) > 0:
        return DecisionVisionPolicy(True, "recovery_after_no_progress", **profile)
    if _auth_observer_window_active(last_auth_submit_at, now=now):
        return DecisionVisionPolicy(True, "auth_captcha_watch_window", **profile)

    goal_blob = _goal_blob(goal)
    page_blob = _page_blob(dom_elements)
    if _has_captcha_signal(page_blob):
        return DecisionVisionPolicy(True, "captcha_surface_signal", **profile)
    if _goal_requires_visual_context(goal_blob):
        return DecisionVisionPolicy(True, "goal_requires_visual_context", **profile)
    if _has_sparse_visual_surface(dom_elements, profile):
        return DecisionVisionPolicy(True, "sparse_visual_surface", **profile)
    if _dom_is_semantically_rich(profile):
        return DecisionVisionPolicy(False, "dom_semantic_enough", **profile)
    return DecisionVisionPolicy(True, "dom_semantic_sparse", **profile)


def looks_like_wait_needs_visual_context(reasoning: str) -> bool:
    text = str(reasoning or "").strip()
    if not text:
        return False
    lowered = text.lower()
    has_visual_request = (
        "화면" in text
        or "시각" in text
        or "스크린샷" in text
        or "screenshot" in lowered
        or "screen" in lowered
        or "visual" in lowered
        or "vision" in lowered
    )
    has_dom_insufficient = (
        "dom" in lowered
        and (
            "부족" in text
            or "확인할 수 없" in text
            or "알 수 없" in text
            or "불확실" in text
            or "제공된 정보" in text
            or "missing" in lowered
            or "insufficient" in lowered
            or "cannot" in lowered
        )
    )
    has_wait_or_blocked = (
        "기다" in text
        or "대기" in text
        or "재확인" in text
        or "다시 확인" in text
        or "wait" in lowered
        or "retry" in lowered
    )
    return bool((has_visual_request or has_dom_insufficient) and has_wait_or_blocked)


def _semantic_profile(dom_elements: Iterable[Any]) -> dict[str, int]:
    visible = 0
    labeled = 0
    semantic_chars = 0
    for el in list(dom_elements or []):
        if not bool(getattr(el, "is_visible", True)):
            continue
        visible += 1
        blob = _element_semantic_blob(el)
        if blob:
            labeled += 1
            semantic_chars += len(blob)
    return {
        "visible_elements": visible,
        "labeled_elements": labeled,
        "semantic_chars": semantic_chars,
    }


def _dom_is_semantically_rich(profile: Mapping[str, int]) -> bool:
    visible = int(profile.get("visible_elements", 0) or 0)
    labeled = int(profile.get("labeled_elements", 0) or 0)
    chars = int(profile.get("semantic_chars", 0) or 0)
    if visible >= 4 and labeled >= 3 and chars >= 60:
        return True
    if labeled >= 6 and chars >= 48:
        return True
    return False


def _goal_blob(goal: Any) -> str:
    parts = [
        str(getattr(goal, "name", "") or ""),
        str(getattr(goal, "description", "") or ""),
        " ".join(str(item or "") for item in (getattr(goal, "success_criteria", []) or [])),
        " ".join(str(item or "") for item in (getattr(goal, "failure_criteria", []) or [])),
    ]
    return " ".join(part for part in parts if part).strip().lower()


def _page_blob(dom_elements: Iterable[Any]) -> str:
    return " ".join(_element_semantic_blob(el) for el in list(dom_elements or [])).lower()


def _element_semantic_blob(el: Any) -> str:
    parts = [
        str(getattr(el, "text", "") or ""),
        str(getattr(el, "aria_label", "") or ""),
        str(getattr(el, "placeholder", "") or ""),
        str(getattr(el, "title", "") or ""),
        str(getattr(el, "container_name", "") or ""),
        str(getattr(el, "context_text", "") or ""),
        str(getattr(el, "role_ref_name", "") or ""),
        _list_blob(getattr(el, "group_action_labels", None)),
        _options_blob(getattr(el, "options", None)),
    ]
    return " ".join(part.strip() for part in parts if str(part or "").strip())


def _list_blob(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return " ".join(str(item or "") for item in value if str(item or "").strip())


def _options_blob(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            parts.append(str(item.get("text") or item.get("label") or item.get("value") or ""))
        else:
            parts.append(str(item or ""))
    return " ".join(part for part in parts if part.strip())


def _goal_requires_visual_context(goal_blob: str) -> bool:
    tokens = (
        "스크린샷",
        "화면 캡처",
        "이미지",
        "사진",
        "그림",
        "색상",
        "색깔",
        "영상",
        "동영상",
        "재생",
        "지도",
        "차트",
        "그래프",
        "캔버스",
        "screenshot",
        "image",
        "photo",
        "video",
        "playback",
        "map",
        "chart",
        "graph",
        "canvas",
        "visual",
    )
    return any(token in goal_blob for token in tokens)


def _has_captcha_signal(page_blob: str) -> bool:
    tokens = (
        "captcha",
        "recaptcha",
        "hcaptcha",
        "turnstile",
        "cloudflare",
        "i'm not a robot",
        "로봇이 아닙니다",
        "보안 문자",
        "자동 입력 방지",
    )
    return any(token in page_blob for token in tokens)


def _has_sparse_visual_surface(dom_elements: Iterable[Any], profile: Mapping[str, int]) -> bool:
    if _dom_is_semantically_rich(profile):
        return False
    sparse_visual_tags = {"canvas", "video"}
    weak_visual_tags = {"img", "image", "svg"}
    sparse_roles = {"img", "graphics-document", "graphics-symbol", "application"}
    weak_visual_count = 0
    for el in list(dom_elements or []):
        tag = str(getattr(el, "tag", "") or "").strip().lower()
        role = str(getattr(el, "role", "") or "").strip().lower()
        if tag in sparse_visual_tags or role in sparse_roles:
            return True
        if tag in weak_visual_tags:
            weak_visual_count += 1
    return weak_visual_count >= 2


def _auth_observer_window_active(last_auth_submit_at: float, *, now: Optional[float]) -> bool:
    submitted_at = float(last_auth_submit_at or 0.0)
    if submitted_at <= 0.0:
        return False
    reference = time.time() if now is None else float(now)
    return 0.0 <= (reference - submitted_at) < 30.0
