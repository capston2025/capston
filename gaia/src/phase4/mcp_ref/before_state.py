from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict


async def capture_before_state(
    *,
    page: Any,
    locator: Any,
    submit_like_click: bool,
    collect_page_evidence_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
    collect_page_evidence_light_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
    compute_runtime_dom_hash_fn: Callable[[Any], Awaitable[str]],
    read_focus_signature_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
    safe_read_target_state_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    evidence_collector = (
        collect_page_evidence_light_fn if submit_like_click else collect_page_evidence_fn
    )
    before_url = page.url
    before_dom_hash = await compute_runtime_dom_hash_fn(page)
    before_evidence = await evidence_collector(page)
    before_focus = await read_focus_signature_fn(page)
    before_target = await safe_read_target_state_fn(locator)
    return {
        "before_url": before_url,
        "before_dom_hash": before_dom_hash,
        "before_evidence": before_evidence,
        "before_focus": before_focus,
        "before_target": before_target,
        "evidence_collector": evidence_collector,
    }


def unpack_before_state(
    *,
    before_state: Dict[str, Any],
    fallback_url: str,
    fallback_evidence_collector: Callable[[Any], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    before_url = str(before_state.get("before_url") or fallback_url)
    before_dom_hash = str(before_state.get("before_dom_hash") or "")
    before_evidence = (
        before_state.get("before_evidence")
        if isinstance(before_state.get("before_evidence"), dict)
        else {}
    )
    before_focus = (
        before_state.get("before_focus")
        if isinstance(before_state.get("before_focus"), dict)
        else {}
    )
    before_target = (
        before_state.get("before_target")
        if isinstance(before_state.get("before_target"), dict)
        else {}
    )
    evidence_collector = before_state.get("evidence_collector")
    if not callable(evidence_collector):
        evidence_collector = fallback_evidence_collector
    return {
        "before_url": before_url,
        "before_dom_hash": before_dom_hash,
        "before_evidence": before_evidence,
        "before_focus": before_focus,
        "before_target": before_target,
        "evidence_collector": evidence_collector,
    }
