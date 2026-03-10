from __future__ import annotations

import re
from typing import List, Optional

from .models import DOMElement


def build_click_intent_key(
    agent_cls,
    *,
    element: Optional[DOMElement],
    full_selector: Optional[str],
    selector: Optional[str],
) -> str:
    if element is None:
        return ""
    text = agent_cls._normalize_text(element.text)
    aria = agent_cls._normalize_text(element.aria_label)
    role = agent_cls._normalize_text(element.role)
    tag = agent_cls._normalize_text(element.tag)
    sel = agent_cls._normalize_text(full_selector or selector)
    if len(sel) > 120:
        sel = sel[:120]
    return f"{tag}|{role}|{text}|{aria}|{sel}"


def squash_text(text: str, limit: int = 160) -> str:
    normalized = re.sub(r"\s+", " ", (text or "")).strip().lower()
    if len(normalized) > limit:
        return normalized[:limit]
    return normalized


def candidate_intent_key(agent, action: str, fields: List[str]) -> str:
    blob = " | ".join(str(x or "") for x in fields if str(x or "").strip())
    return f"{action}:{squash_text(blob, limit=180)}"


def adaptive_intent_bias(agent, intent_key: str) -> float:
    if not intent_key:
        return 0.0
    stat = agent._intent_stats.get(intent_key) or {}
    ok = int(stat.get("ok") or 0)
    soft_fail = int(stat.get("soft_fail") or 0)
    hard_fail = int(stat.get("hard_fail") or 0)
    raw = (0.8 * ok) - (1.2 * soft_fail) - (1.5 * hard_fail)
    return agent._clamp_score(raw, low=-12.0, high=8.0)


def update_intent_stats(
    agent,
    *,
    intent_key: str,
    success: bool,
    changed: bool,
    reason_code: str,
) -> None:
    if not intent_key:
        return
    stat = agent._intent_stats.setdefault(
        intent_key,
        {"ok": 0, "soft_fail": 0, "hard_fail": 0},
    )
    if success and changed:
        stat["ok"] = min(200, int(stat.get("ok") or 0) + 1)
        if int(stat.get("soft_fail") or 0) > 0:
            stat["soft_fail"] = int(stat["soft_fail"]) - 1
        if int(stat.get("hard_fail") or 0) > 0:
            stat["hard_fail"] = int(stat["hard_fail"]) - 1
        return
    if reason_code in {"no_state_change", "not_actionable", "modal_not_open", "blocked_ref_no_progress", "ambiguous_ref_target", "ambiguous_selector"}:
        stat["soft_fail"] = min(200, int(stat.get("soft_fail") or 0) + 1)
    else:
        stat["hard_fail"] = min(200, int(stat.get("hard_fail") or 0) + 1)


def normalize_selector_key(selector: str) -> str:
    cleaned = re.sub(r"\s+", " ", (selector or "").strip().lower())
    if len(cleaned) > 180:
        return cleaned[:180]
    return cleaned


def selector_bias_for_fields(agent, fields: List[str]) -> float:
    if not agent._memory_selector_bias:
        return 0.0
    blob = normalize_selector_key(" | ".join(str(x or "") for x in fields))
    if not blob:
        return 0.0
    bias = 0.0
    for key, weight in agent._memory_selector_bias.items():
        if key and key in blob:
            bias += float(weight)
    return agent._clamp_score(bias, low=-10.0, high=10.0)
