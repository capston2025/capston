from __future__ import annotations

from typing import Any, Dict, List




def _extract_elements_by_ref(snapshot_result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = snapshot_result.get("dom_elements") or snapshot_result.get("elements") or []
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                ref_id = str(item.get("ref_id") or "")
                if ref_id:
                    out[ref_id] = item
    return out

def _element_signal_score(item: Dict[str, Any]) -> int:
    score = 0
    text = str(item.get("text") or "").strip()
    if text:
        score += min(12, len(text))
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    for key in ("aria-label", "title", "placeholder", "href"):
        if str(attrs.get(key) or "").strip():
            score += 2
    if str(item.get("element_type") or "").strip():
        score += 1
    return score


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