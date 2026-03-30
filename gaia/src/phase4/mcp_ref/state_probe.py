from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict


async def collect_state_change_probe(
    *,
    page: Any,
    locator: Any,
    action: str,
    value: Any,
    before_url: str,
    before_dom_hash: str,
    before_evidence: Dict[str, Any],
    before_focus: Dict[str, Any],
    before_target: Dict[str, Any],
    probe_wait_ms: int,
    probe_scroll: str,
    ancestor_click_fallback: bool,
    ancestor_click_selector: str,
    compute_runtime_dom_hash_fn: Callable[[Any], Awaitable[str]],
    evidence_collector_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
    read_focus_signature_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
    safe_read_target_state_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
    state_change_flags_fn: Callable[..., Dict[str, Any]],
    extract_live_texts_fn: Callable[[Any], Any],
) -> Dict[str, Any]:
    after_url = page.url
    after_dom_hash = await compute_runtime_dom_hash_fn(page)
    after_evidence = await evidence_collector_fn(page)
    after_focus = await read_focus_signature_fn(page)
    after_target = await safe_read_target_state_fn(locator)
    change = state_change_flags_fn(
        action=action,
        value=value,
        before_url=before_url,
        after_url=after_url,
        before_dom_hash=before_dom_hash,
        after_dom_hash=after_dom_hash,
        before_evidence=before_evidence,
        after_evidence=after_evidence,
        before_target=before_target,
        after_target=after_target,
        before_focus=before_focus,
        after_focus=after_focus,
    )
    live_texts_after = extract_live_texts_fn(after_evidence.get("live_texts"))
    if live_texts_after:
        change["live_texts_after"] = live_texts_after
    change["probe_wait_ms"] = probe_wait_ms
    change["probe_scroll"] = probe_scroll
    if ancestor_click_fallback:
        change["ancestor_click_fallback"] = True
        change["ancestor_click_selector"] = ancestor_click_selector
    return {
        "change": change,
        "live_texts_after": live_texts_after if live_texts_after else [],
    }
