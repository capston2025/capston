from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from gaia.src.phase4.mcp_snapshot_ref_utils import _element_is_interactive




def _is_close_intent_ref(meta: Dict[str, Any]) -> bool:
    if not isinstance(meta, dict):
        return False
    attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
    visible = str(meta.get("text") or "").strip()
    visible_l = visible.lower()
    aria_label = str(attrs.get("aria-label") or "").strip()
    aria_l = aria_label.lower()
    title = str(attrs.get("title") or "").strip()
    title_l = title.lower()
    class_name = str(attrs.get("class") or "").strip().lower()
    element_id = str(attrs.get("id") or meta.get("id") or "").strip().lower()
    is_x_like_text = visible in {"x", "X", "✕", "✖", "×"}

    # 닫기 라벨/타이틀의 명시 신호
    explicit_korean_close = any(
        token in f"{visible} {aria_label} {title}"
        for token in ("닫기", "취소", "나가기")
    )
    explicit_english_close = bool(
        re.search(r"\b(close|dismiss|cancel|exit)\b", f"{visible_l} {aria_l} {title_l}")
    )
    class_close_hint = bool(
        re.search(
            r"(^|[-_\\s])(close|dismiss|modal-close|dialog-close|btn-close|icon-close)($|[-_\\s])",
            class_name,
        )
        or re.search(
            r"(^|[-_\\s])(close|dismiss|modal-close|dialog-close)($|[-_\\s])",
            element_id,
        )
    )

    if explicit_korean_close or explicit_english_close or class_close_hint:
        if is_x_like_text:
            # "X" 텍스트 + class/selector에 close 토큰 → soft close
            # aria-label/title이 명시적으로 close를 포함할 때만 hard close
            explicit_label = aria_l
            explicit_title = title_l
            has_explicit_close = (
                any(kw in explicit_label for kw in ("close", "닫기", "dismiss"))
                or any(kw in explicit_title for kw in ("close", "닫기", "dismiss"))
            )
            if not has_explicit_close:
                meta["_soft_close"] = True
        return True
    if is_x_like_text:
        # class/selector에 close 토큰 없이 "X" 텍스트만 있는 경우도 soft close
        meta["_soft_close"] = True
        return True
    return False


def _rank_close_ref_candidate(
    meta: Dict[str, Any],
    *,
    requested_meta: Optional[Dict[str, Any]] = None,
    modal_regions: Optional[List[Dict[str, float]]] = None,
) -> int:
    if not isinstance(meta, dict):
        return -1
    attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
    text = str(meta.get("text") or "").strip().lower()
    selector = str(meta.get("selector") or "").strip().lower()
    full_selector = str(meta.get("full_selector") or "").strip().lower()
    aria_label = str(attrs.get("aria-label") or "").strip().lower()
    title = str(attrs.get("title") or "").strip().lower()
    class_name = str(attrs.get("class") or "").strip().lower()
    role = str(attrs.get("role") or "").strip().lower()
    score = 0
    if text in {"x", "✕", "✖", "×"}:
        score += 6
    if role == "button":
        score += 3
    if "close" in aria_label or "닫기" in aria_label:
        score += 4
    if "close" in title or "닫기" in title:
        score += 3
    if "close" in selector or "close" in full_selector:
        score += 2
    if "close" in class_name or "dismiss" in class_name or "modal" in class_name:
        score += 1
    if _is_modal_corner_close_candidate(meta, modal_regions or []):
        score += 5
    if requested_meta and isinstance(requested_meta, dict):
        req_scope = (
            requested_meta.get("scope")
            if isinstance(requested_meta.get("scope"), dict)
            else {}
        )
        cand_scope = meta.get("scope") if isinstance(meta.get("scope"), dict) else {}
        if req_scope.get("frame_index") == cand_scope.get("frame_index"):
            score += 2
        if req_scope.get("tab_index") == cand_scope.get("tab_index"):
            score += 2
    return score

def _normalize_bbox_dict(raw_bbox: Any) -> Optional[Dict[str, float]]:
    if not isinstance(raw_bbox, dict):
        return None
    try:
        x = float(raw_bbox.get("x", 0.0) or 0.0)
        y = float(raw_bbox.get("y", 0.0) or 0.0)
        width = float(raw_bbox.get("width", 0.0) or 0.0)
        height = float(raw_bbox.get("height", 0.0) or 0.0)
    except Exception:
        return None
    if width <= 0.0 or height <= 0.0:
        return None
    center_x = float(raw_bbox.get("center_x", x + width / 2.0) or (x + width / 2.0))
    center_y = float(raw_bbox.get("center_y", y + height / 2.0) or (y + height / 2.0))
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "center_x": center_x,
        "center_y": center_y,
        "right": x + width,
        "bottom": y + height,
    }


def _collect_modal_regions_from_snapshot(snapshot: Optional[Dict[str, Any]]) -> List[Dict[str, float]]:
    if not isinstance(snapshot, dict):
        return []
    by_ref = snapshot.get("elements_by_ref")
    if not isinstance(by_ref, dict):
        return []
    regions: List[Dict[str, float]] = []
    for _, raw_meta in by_ref.items():
        if not isinstance(raw_meta, dict):
            continue
        attrs = raw_meta.get("attributes") if isinstance(raw_meta.get("attributes"), dict) else {}
        role = str(attrs.get("role") or "").strip().lower()
        aria_modal = str(attrs.get("aria-modal") or "").strip().lower()
        class_name = str(attrs.get("class") or "").strip().lower()
        tag = str(raw_meta.get("tag") or "").strip().lower()
        if not (
            aria_modal == "true"
            or role in {"dialog", "alertdialog"}
            or tag == "dialog"
            or any(token in class_name for token in ("modal", "dialog", "popup", "sheet", "drawer"))
        ):
            continue
        bbox = _normalize_bbox_dict(raw_meta.get("bounding_box"))
        if not bbox:
            continue
        if bbox["width"] < 120 or bbox["height"] < 120:
            continue
        regions.append(bbox)
    regions.sort(key=lambda r: r["width"] * r["height"], reverse=True)
    return regions[:6]

def _is_modal_corner_close_candidate(
    meta: Dict[str, Any],
    modal_regions: List[Dict[str, float]],
) -> bool:
    if not isinstance(meta, dict):
        return False
    if not modal_regions:
        return False
    if not _element_is_interactive(meta):
        return False
    bbox = _normalize_bbox_dict(meta.get("bounding_box"))
    if not bbox:
        return False
    if bbox["width"] > 72 or bbox["height"] > 72:
        return False
    if (bbox["width"] * bbox["height"]) > 3600:
        return False
    cx = bbox["center_x"]
    cy = bbox["center_y"]
    for region in modal_regions:
        if not (region["x"] <= cx <= region["right"] and region["y"] <= cy <= region["bottom"]):
            continue
        rel_x = (cx - region["x"]) / max(region["width"], 1.0)
        rel_y = (cy - region["y"]) / max(region["height"], 1.0)
        if rel_x >= 0.72 and rel_y <= 0.28:
            return True
    return False

def _collect_close_ref_candidates(
    *,
    snapshot: Optional[Dict[str, Any]],
    requested_meta: Optional[Dict[str, Any]],
    exclude_ref_id: str,
    limit: int = 5,
) -> List[Tuple[str, Dict[str, Any]]]:
    if not isinstance(snapshot, dict):
        return []
    by_ref = snapshot.get("elements_by_ref")
    if not isinstance(by_ref, dict):
        return []
    req_scope = (
        requested_meta.get("scope")
        if isinstance(requested_meta, dict) and isinstance(requested_meta.get("scope"), dict)
        else {}
    )
    modal_regions = _collect_modal_regions_from_snapshot(snapshot)
    ranked: List[Tuple[int, str, Dict[str, Any]]] = []
    for raw_ref_id, raw_meta in by_ref.items():
        ref_key = str(raw_ref_id or "").strip()
        if not ref_key or ref_key == exclude_ref_id:
            continue
        if not isinstance(raw_meta, dict):
            continue
        if not (
            _is_close_intent_ref(raw_meta)
            or _is_modal_corner_close_candidate(raw_meta, modal_regions)
        ):
            continue
        cand_scope = raw_meta.get("scope") if isinstance(raw_meta.get("scope"), dict) else {}
        try:
            req_tab = req_scope.get("tab_index")
            cand_tab = cand_scope.get("tab_index")
            if req_tab is not None and cand_tab is not None and int(req_tab) != int(cand_tab):
                continue
        except Exception:
            pass
        try:
            req_frame = req_scope.get("frame_index")
            cand_frame = cand_scope.get("frame_index")
            if req_frame is not None and cand_frame is not None and int(req_frame) != int(cand_frame):
                continue
        except Exception:
            pass
        score = _rank_close_ref_candidate(
            raw_meta,
            requested_meta=requested_meta,
            modal_regions=modal_regions,
        )
        ranked.append((score, ref_key, raw_meta))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [(rid, meta) for _, rid, meta in ranked[: max(1, limit)]]


_ROLE_INTERACTIVE = {
    "button",
    "link",
    "textbox",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "treeitem",
}

_ROLE_CONTENT = {
    "heading",
    "cell",
    "gridcell",
    "columnheader",
    "rowheader",
    "listitem",
    "article",
    "region",
    "main",
    "navigation",
}

_ROLE_STRUCTURAL = {
    "generic",
    "group",
    "list",
    "table",
    "row",
    "rowgroup",
    "grid",
    "treegrid",
    "menu",
    "menubar",
    "toolbar",
    "tablist",
    "tree",
    "directory",
    "document",
    "application",
    "presentation",
    "none",
}