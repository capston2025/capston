from __future__ import annotations

from typing import List, Optional, Type

from .models import DOMElement, TestGoal

_PLAYBACK_GOAL_KEYWORDS = (
    "재생",
    "play",
    "시청",
    "watch",
    "듣기",
    "listen",
)

_PLAYER_SURFACE_KEYWORDS = (
    "video player",
    "audio player",
    "player",
    "viewer",
    "재생기",
    "플레이어",
    "비디오 플레이어",
    "오디오 플레이어",
)

_PLAY_CONTROL_KEYWORDS = (
    "재생",
    "play",
    "resume",
    "시청 시작",
    "watch",
    "듣기",
    "listen",
    "start",
    "▶",
)

_PLAY_CONTROL_EXCLUDE_KEYWORDS = (
    "재생목록",
    "playlist",
    "autoplay",
    "자동재생",
)


def _normalize(agent_cls: Type[object], value: object) -> str:
    normalizer = getattr(agent_cls, "_normalize_text", None)
    if callable(normalizer):
        try:
            return str(normalizer(value) or "").strip().lower()
        except Exception:
            pass
    return str(value or "").strip().lower()


def _goal_text_blob(agent_cls: Type[object], goal: TestGoal) -> str:
    parts = [
        str(getattr(goal, "name", "") or ""),
        str(getattr(goal, "description", "") or ""),
    ]
    parts.extend(str(item or "") for item in list(getattr(goal, "success_criteria", []) or []))
    return _normalize(agent_cls, " ".join(part for part in parts if str(part or "").strip()))


def goal_requires_media_playback(agent_cls: Type[object], goal: TestGoal) -> bool:
    blob = _goal_text_blob(agent_cls, goal)
    if not blob:
        return False
    return any(keyword in blob for keyword in _PLAYBACK_GOAL_KEYWORDS)


def _element_blob(agent_cls: Type[object], element: DOMElement) -> str:
    fields = [
        getattr(element, "text", ""),
        getattr(element, "aria_label", ""),
        getattr(element, "title", ""),
        getattr(element, "placeholder", ""),
        getattr(element, "context_text", ""),
        getattr(element, "container_name", ""),
        getattr(element, "role_ref_name", ""),
    ]
    return _normalize(agent_cls, " ".join(str(field or "") for field in fields if str(field or "").strip()))


def _element_primary_label(element: DOMElement) -> str:
    for value in (
        getattr(element, "text", None),
        getattr(element, "aria_label", None),
        getattr(element, "title", None),
        getattr(element, "role_ref_name", None),
        getattr(element, "placeholder", None),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def dom_has_media_player_surface(agent_cls: Type[object], dom_elements: List[DOMElement]) -> bool:
    for element in list(dom_elements or [])[:120]:
        if not bool(getattr(element, "is_visible", True)):
            continue
        role = _normalize(agent_cls, getattr(element, "role", ""))
        blob = _element_blob(agent_cls, element)
        if any(keyword in blob for keyword in _PLAYER_SURFACE_KEYWORDS):
            return True
        if role == "application" and any(keyword in blob for keyword in ("player", "viewer", "video", "audio")):
            return True
    return False


def collect_visible_play_controls(
    agent_cls: Type[object],
    dom_elements: List[DOMElement],
    *,
    limit: int = 3,
) -> List[DOMElement]:
    matches: List[DOMElement] = []
    seen: set[str] = set()
    for element in list(dom_elements or [])[:180]:
        if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
            continue
        label = _normalize(agent_cls, _element_primary_label(element))
        if not label:
            continue
        role = _normalize(agent_cls, getattr(element, "role", ""))
        tag = _normalize(agent_cls, getattr(element, "tag", ""))
        role_ref_role = _normalize(agent_cls, getattr(element, "role_ref_role", ""))
        actionable_role = role in {"button", "link", "menuitem"}
        actionable_tag = tag in {"button", "a"}
        actionable_ref = role_ref_role in {"button", "link", "menuitem"}
        if not (actionable_role or actionable_tag or actionable_ref):
            exact_play_label = label in {"재생", "play", "resume", "watch", "listen", "start", "▶"}
            if not exact_play_label:
                continue
        if any(keyword in label for keyword in _PLAY_CONTROL_EXCLUDE_KEYWORDS):
            continue
        if not any(keyword in label for keyword in _PLAY_CONTROL_KEYWORDS):
            continue
        key = str(getattr(element, "ref_id", "") or getattr(element, "id", "") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        matches.append(element)
        if len(matches) >= limit:
            break
    return matches


def describe_play_control(element: Optional[DOMElement]) -> str:
    if element is None:
        return ""
    label = _element_primary_label(element) or "[icon-only]"
    ref = str(getattr(element, "ref_id", "") or getattr(element, "id", "") or "").strip()
    role = str(getattr(element, "role", "") or getattr(element, "tag", "") or "").strip()
    details = []
    if ref:
        details.append(f"ref={ref}")
    details.append(f'label="{label}"')
    if role:
        details.append(f"role={role}")
    return " ".join(details)
