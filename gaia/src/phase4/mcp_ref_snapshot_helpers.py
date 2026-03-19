from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Page


from gaia.src.phase4.mcp_snapshot_ref_utils import (
    _dedupe_elements_by_dom_ref as _dedupe_elements_by_dom_ref_impl,
    _element_is_interactive as _element_is_interactive_impl,
    _element_signal_score as _element_signal_score_impl,
    _extract_elements_by_ref as _extract_elements_by_ref_impl,
)

from gaia.src.phase4.mcp_snapshot_close_intent import (
    _collect_close_ref_candidates as _collect_close_ref_candidates_impl,
    _collect_modal_regions_from_snapshot as _collect_modal_regions_from_snapshot_impl,
    _is_close_intent_ref as _is_close_intent_ref_impl,
    _is_modal_corner_close_candidate as _is_modal_corner_close_candidate_impl,
    _normalize_bbox_dict as _normalize_bbox_dict_impl,
    _rank_close_ref_candidate as _rank_close_ref_candidate_impl,
)

from gaia.src.phase4.mcp_snapshot_text_runtime import (
    _build_context_snapshot_from_elements as _build_context_snapshot_from_elements_impl,
    _build_role_refs_from_elements as _build_role_refs_from_elements_impl,
    _build_role_snapshot_from_ai_text as _build_role_snapshot_from_ai_text_impl,
    _build_role_snapshot_from_aria_text as _build_role_snapshot_from_aria_text_impl,
    _build_snapshot_text as _build_snapshot_text_impl,
    _compact_role_tree as _compact_role_tree_impl,
    _limit_snapshot_text as _limit_snapshot_text_impl,
    _parse_ai_ref as _parse_ai_ref_impl,
    _role_snapshot_stats as _role_snapshot_stats_impl,
    _snapshot_line_depth as _snapshot_line_depth_impl,
    _try_snapshot_for_ai as _try_snapshot_for_ai_impl,
)


def _extract_elements_by_ref(snapshot_result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return _extract_elements_by_ref_impl(snapshot_result)



def _element_signal_score(item: Dict[str, Any]) -> int:
    return _element_signal_score_impl(item)

def _dedupe_elements_by_dom_ref(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _dedupe_elements_by_dom_ref_impl(elements)

def _element_is_interactive(item: Dict[str, Any]) -> bool:
    return _element_is_interactive_impl(item)


def _dedupe_elements_by_dom_ref(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    index_by_dom_ref: Dict[str, int] = {}
    for raw in elements:
        if not isinstance(raw, dict):
            continue
        dom_ref = str(raw.get("dom_ref") or "").strip()
        if not dom_ref:
            deduped.append(raw)
            continue
        prev_idx = index_by_dom_ref.get(dom_ref)
        if prev_idx is None:
            index_by_dom_ref[dom_ref] = len(deduped)
            deduped.append(raw)
            continue
        prev = deduped[prev_idx]
        if _element_signal_score(raw) > _element_signal_score(prev):
            deduped[prev_idx] = raw
    return deduped


def _element_is_interactive(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    tag = str(item.get("tag") or "").strip().lower()
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    role = str(attrs.get("role") or "").strip().lower()
    element_type = str(item.get("element_type") or "").strip().lower()
    interactive_tags = {"button", "a", "input", "select", "textarea", "option", "summary"}
    interactive_roles = {
        "button",
        "link",
        "tab",
        "menuitem",
        "checkbox",
        "radio",
        "switch",
        "combobox",
        "textbox",
        "option",
        "slider",
    }
    if tag in interactive_tags:
        return True
    if role in interactive_roles:
        return True
    if element_type in {"button", "link", "input", "checkbox", "radio", "select", "textarea", "semantic"}:
        return True
    if str(attrs.get("onclick") or "").strip():
        return True
    return False


def _is_close_intent_ref(meta: Dict[str, Any]) -> bool:
    return _is_close_intent_ref_impl(meta)


def _rank_close_ref_candidate(
    meta: Dict[str, Any],
    *,
    requested_meta: Optional[Dict[str, Any]] = None,
    modal_regions: Optional[List[Dict[str, float]]] = None,
) -> int:
    return _rank_close_ref_candidate_impl(
        meta,
        requested_meta=requested_meta,
        modal_regions=modal_regions,
    )


def _normalize_bbox_dict(raw_bbox: Any) -> Optional[Dict[str, float]]:
    return _normalize_bbox_dict_impl(raw_bbox)


def _collect_modal_regions_from_snapshot(snapshot: Optional[Dict[str, Any]]) -> List[Dict[str, float]]:
    return _collect_modal_regions_from_snapshot_impl(snapshot)

def _is_modal_corner_close_candidate(
    meta: Dict[str, Any],
    modal_regions: List[Dict[str, float]],
) -> bool:
    return _is_modal_corner_close_candidate_impl(meta, modal_regions)

def _collect_close_ref_candidates(
    snapshot: Optional[Dict[str, Any]],
    requested_ref: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return _collect_close_ref_candidates_impl(
        snapshot,
        requested_ref=requested_ref,
    )


def _snapshot_line_depth(line: str) -> int:
    return _snapshot_line_depth_impl(line)


def _compact_role_tree(snapshot: str) -> str:
    return _compact_role_tree_impl(snapshot)



def _limit_snapshot_text(snapshot: str, max_chars: int) -> tuple[str, bool]:
    return _limit_snapshot_text_impl(snapshot, max_chars)



def _parse_ai_ref(suffix: str) -> Optional[str]:
    return _parse_ai_ref_impl(suffix)



def _role_snapshot_stats(snapshot: str, refs: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    return _role_snapshot_stats_impl(snapshot, refs)


def _build_role_snapshot_from_aria_text(
    aria_snapshot: str,
    *,
    ref_prefix: str = "e",
) -> Dict[str, Any]:
    return _build_role_snapshot_from_aria_text_impl(
        aria_snapshot,
        ref_prefix=ref_prefix,
    )


def _build_role_snapshot_from_ai_text(
    ai_snapshot: str,
    *,
    fallback_prefix: str = "e",
) -> Dict[str, Any]:
    return _build_role_snapshot_from_ai_text_impl(
        ai_snapshot,
        fallback_prefix=fallback_prefix,
    )

def _build_role_refs_from_elements(elements: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return _build_role_refs_from_elements_impl(elements)


def _build_context_snapshot_from_elements(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    return _build_context_snapshot_from_elements_impl(elements)


async def _try_snapshot_for_ai(page: Page, timeout_ms: int = 5000) -> Optional[str]:
    return await _try_snapshot_for_ai_impl(page, timeout_ms=timeout_ms)

def _build_snapshot_text(
    elements: List[Dict[str, Any]],
    *,
    interactive_only: bool,
    compact: bool,
    limit: int,
    max_chars: int,
) -> Dict[str, Any]:
    return _build_snapshot_text_impl(
        elements,
        interactive_only=interactive_only,
        compact=compact,
        limit=limit,
        max_chars=max_chars,
    )
