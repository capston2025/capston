"""Browser tab context helpers for reducing manual intervention.

The goal-driven loop should not need to ask a human to switch into obvious
same-origin viewer/pop-up tabs. This module keeps that decision deterministic
and small so the OpenClaw dispatch layer can update its session target safely.
"""

from __future__ import annotations

import os
from typing import Any, Mapping


AUTO_FOLLOW_ENV = "GAIA_OPENCLAW_AUTO_FOLLOW_NEW_TABS"


def auto_follow_new_tabs_enabled() -> bool:
    raw = str(os.getenv(AUTO_FOLLOW_ENV, "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def choose_auto_follow_tab(new_page_evidence: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Pick a safe newly opened tab to follow automatically.

    We only follow tabs that are likely to continue the current task:
    same-origin tabs, or viewer/player-like tabs. Ad/help-like tabs are ignored.
    """

    if not isinstance(new_page_evidence, Mapping):
        return None
    pages = new_page_evidence.get("new_pages")
    if not isinstance(pages, list) or not pages:
        return None

    best: tuple[int, int, dict[str, Any]] | None = None
    for index, raw_page in enumerate(pages):
        if not isinstance(raw_page, Mapping):
            continue
        page = dict(raw_page)
        kind = str(page.get("kind_guess") or "").strip().lower()
        if kind in {"ad_like", "help_like"}:
            continue
        target_id = str(page.get("target_id") or page.get("tab_id") or "").strip()
        url = str(page.get("url") or "").strip()
        if not target_id and not url:
            continue

        same_origin = bool(page.get("same_origin"))
        viewer_like = kind == "viewer_like"
        if not same_origin and not viewer_like:
            continue

        score = 0
        if viewer_like:
            score += 6
        if same_origin:
            score += 4
        if bool(page.get("active")):
            score += 1
        if best is None or (score, -index) > (best[0], best[1]):
            best = (score, -index, page)

    if best is None:
        return None
    return best[2]


def build_auto_follow_state_update(
    new_page_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not auto_follow_new_tabs_enabled():
        return {}

    tab = choose_auto_follow_tab(new_page_evidence)
    if not tab:
        return {}

    target_id = str(tab.get("target_id") or tab.get("tab_id") or "").strip()
    current_url = str(tab.get("url") or "").strip()
    if not target_id and not current_url:
        return {}

    kind = str(tab.get("kind_guess") or "").strip() or "unknown"
    same_origin = bool(tab.get("same_origin"))
    reason_bits = []
    if kind == "viewer_like":
        reason_bits.append("viewer_like")
    if same_origin:
        reason_bits.append("same_origin")
    reason = "+".join(reason_bits) or kind

    return {
        "auto_followed_new_page": True,
        "auto_follow_reason": reason,
        "auto_follow_target_id": target_id,
        "auto_follow_tab_id": str(tab.get("tab_id") or "").strip(),
        "auto_follow_url": current_url,
        "auto_follow_title": str(tab.get("title") or "").strip(),
        "auto_follow_kind": kind,
        "auto_follow_same_origin": same_origin,
    }
