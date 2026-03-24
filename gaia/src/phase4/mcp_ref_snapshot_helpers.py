from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Page

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


def _snapshot_line_depth(line: str) -> int:
    indent = len(line) - len(line.lstrip(" "))
    return max(0, indent // 2)


def _compact_role_tree(snapshot: str) -> str:
    lines = snapshot.split("\n")
    out: List[str] = []
    for i, line in enumerate(lines):
        if "[ref=" in line:
            out.append(line)
            continue
        if ":" in line and not line.rstrip().endswith(":"):
            out.append(line)
            continue
        current_depth = _snapshot_line_depth(line)
        has_ref_child = False
        for j in range(i + 1, len(lines)):
            child_depth = _snapshot_line_depth(lines[j])
            if child_depth <= current_depth:
                break
            if "[ref=" in lines[j]:
                has_ref_child = True
                break
        if has_ref_child:
            out.append(line)
    return "\n".join(out)


def _limit_snapshot_text(snapshot: str, max_chars: int) -> tuple[str, bool]:
    limit = max(200, min(int(max_chars or 24000), 120000))
    if len(snapshot) <= limit:
        return snapshot, False
    return f"{snapshot[:limit]}\n\n[...TRUNCATED - page too large]", True


def _parse_ai_ref(suffix: str) -> Optional[str]:
    m = re.search(r"\[ref=(e\d+)\]", suffix or "", flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def _role_snapshot_stats(snapshot: str, refs: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    interactive = 0
    for item in refs.values():
        role = str((item or {}).get("role") or "").strip().lower()
        if role in _ROLE_INTERACTIVE:
            interactive += 1
    return {
        "lines": len(snapshot.split("\n")) if snapshot else 0,
        "chars": len(snapshot),
        "refs": len(refs),
        "interactive": interactive,
    }


def _build_role_tree(snapshot: str, refs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    lines = str(snapshot or "").split("\n")
    tree: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    for line in lines:
        stripped = str(line or "").strip()
        if not stripped:
            continue
        depth = _snapshot_line_depth(line)
        match = re.match(r'^-\s*(\w+)(?:\s+"([^"]*)")?', stripped)
        role = str(match.group(1) or "").strip().lower() if match else ""
        name = str(match.group(2) or "").strip() if match else ""
        ref = _parse_ai_ref(stripped)
        nth_match = re.search(r"\[nth=(\d+)\]", stripped)
        nth = int(nth_match.group(1)) if nth_match else None
        while stack and int(stack[-1].get("depth", 0) or 0) >= depth:
            stack.pop()
        ancestor_names = [str(item.get("name") or "").strip() for item in stack if str(item.get("name") or "").strip()]
        node = {
            "depth": depth,
            "role": role or str((refs.get(ref or "") or {}).get("role") or "").strip().lower(),
            "name": name or str((refs.get(ref or "") or {}).get("name") or "").strip(),
            "ref": ref or None,
            "nth": nth,
            "parent_ref": str(stack[-1].get("ref") or "").strip() or None if stack else None,
            "line": stripped,
            "ancestor_names": ancestor_names,
        }
        tree.append(node)
        stack.append({"depth": depth, "ref": node.get("ref"), "name": node.get("name"), "role": node.get("role")})
    return tree


def _build_role_snapshot_from_aria_text(
    aria_snapshot: str,
    *,
    interactive: bool,
    compact: bool,
    max_depth: Optional[int] = None,
    line_limit: int = 500,
    max_chars: int = 64000,
) -> Dict[str, Any]:
    lines = str(aria_snapshot or "").split("\n")
    refs: Dict[str, Dict[str, Any]] = {}
    refs_by_key: Dict[str, List[str]] = defaultdict(list)
    counts_by_key: Dict[str, int] = defaultdict(int)
    out: List[str] = []
    ref_counter = 0

    def _next_ref() -> str:
        nonlocal ref_counter
        ref_counter += 1
        return f"e{ref_counter}"

    for line in lines:
        depth = _snapshot_line_depth(line)
        if max_depth is not None and depth > max_depth:
            continue

        m = re.match(r'^(\s*-\s*)(\w+)(?:\s+"([^"]*)")?(.*)$', line)
        if not m:
            if not interactive:
                out.append(line)
            continue

        prefix, role_raw, name, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        if role_raw.startswith("/"):
            if not interactive:
                out.append(line)
            continue

        role = (role_raw or "").lower()
        if interactive and role not in _ROLE_INTERACTIVE:
            continue
        if compact and role in _ROLE_STRUCTURAL and not name:
            continue

        should_have_ref = role in _ROLE_INTERACTIVE or (role in _ROLE_CONTENT and bool(name))
        if not should_have_ref:
            out.append(line)
            continue

        ref = _next_ref()
        key = f"{role}:{name or ''}"
        nth = counts_by_key[key]
        counts_by_key[key] += 1
        refs_by_key[key].append(ref)

        ref_payload: Dict[str, Any] = {"role": role}
        if name:
            ref_payload["name"] = name
        if nth > 0:
            ref_payload["nth"] = nth
        refs[ref] = ref_payload

        enhanced = f"{prefix}{role_raw}"
        if name:
            enhanced += f' "{name}"'
        enhanced += f" [ref={ref}]"
        if nth > 0:
            enhanced += f" [nth={nth}]"
        if suffix:
            enhanced += suffix
        out.append(enhanced)

    duplicate_keys = {k for k, v in refs_by_key.items() if len(v) > 1}
    for ref, data in refs.items():
        key = f"{data.get('role', '')}:{data.get('name', '')}"
        if key not in duplicate_keys:
            data.pop("nth", None)

    snapshot = "\n".join(out) or "(empty)"
    if compact:
        snapshot = _compact_role_tree(snapshot)
    trimmed_lines = snapshot.split("\n")[: max(1, min(int(line_limit or 500), 5000))]
    snapshot = "\n".join(trimmed_lines)
    snapshot, truncated = _limit_snapshot_text(snapshot, max_chars=max_chars)
    return {
        "snapshot": snapshot,
        "refs": refs,
        "tree": _build_role_tree(snapshot, refs),
        "truncated": truncated,
        "stats": _role_snapshot_stats(snapshot, refs),
    }


def _build_role_snapshot_from_ai_text(
    ai_snapshot: str,
    *,
    interactive: bool,
    compact: bool,
    max_depth: Optional[int] = None,
    line_limit: int = 500,
    max_chars: int = 64000,
) -> Dict[str, Any]:
    lines = str(ai_snapshot or "").split("\n")
    refs: Dict[str, Dict[str, Any]] = {}
    out: List[str] = []

    for line in lines:
        depth = _snapshot_line_depth(line)
        if max_depth is not None and depth > max_depth:
            continue

        m = re.match(r'^(\s*-\s*)(\w+)(?:\s+"([^"]*)")?(.*)$', line)
        if not m:
            out.append(line)
            continue

        _, role_raw, name, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        if role_raw.startswith("/"):
            out.append(line)
            continue

        role = (role_raw or "").lower()
        if interactive and role not in _ROLE_INTERACTIVE:
            continue
        if compact and role in _ROLE_STRUCTURAL and not name:
            continue

        ref = _parse_ai_ref(suffix or "")
        if ref:
            refs[ref] = {"role": role, **({"name": name} if name else {})}
        out.append(line)

    snapshot = "\n".join(out) or "(empty)"
    if compact:
        snapshot = _compact_role_tree(snapshot)
    trimmed_lines = snapshot.split("\n")[: max(1, min(int(line_limit or 500), 5000))]
    snapshot = "\n".join(trimmed_lines)
    snapshot, truncated = _limit_snapshot_text(snapshot, max_chars=max_chars)
    return {
        "snapshot": snapshot,
        "refs": refs,
        "tree": _build_role_tree(snapshot, refs),
        "truncated": truncated,
        "stats": _role_snapshot_stats(snapshot, refs),
    }


def _build_role_refs_from_elements(elements: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    refs: Dict[str, Dict[str, Any]] = {}
    counts_by_key: Dict[str, int] = defaultdict(int)
    refs_by_key: Dict[str, List[str]] = defaultdict(list)

    for item in elements:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref_id") or "").strip()
        if not ref:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        role = str(attrs.get("role") or "").strip().lower()
        if not role:
            tag = str(item.get("tag") or "").strip().lower()
            if tag == "a":
                role = "link"
            elif tag in {"input", "textarea"}:
                role = "textbox"
            elif tag == "select":
                role = "combobox"
            elif tag == "button":
                role = "button"
            else:
                role = "generic"

        name = str(item.get("text") or attrs.get("aria-label") or "").strip() or None
        key = f"{role}:{name or ''}"
        nth = counts_by_key[key]
        counts_by_key[key] += 1
        refs_by_key[key].append(ref)

        payload: Dict[str, Any] = {"role": role}
        if name:
            payload["name"] = name
        if nth > 0:
            payload["nth"] = nth
        refs[ref] = payload

    duplicate_keys = {k for k, v in refs_by_key.items() if len(v) > 1}
    for ref, data in refs.items():
        key = f"{data.get('role', '')}:{data.get('name', '')}"
        if key not in duplicate_keys:
            data.pop("nth", None)
    return refs


def _build_role_snapshot_from_elements(
    elements: List[Dict[str, Any]],
    *,
    line_limit: int = 500,
    max_chars: int = 24000,
) -> Dict[str, Any]:
    refs = _build_role_refs_from_elements(elements)
    lines: List[str] = []
    for item in elements:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref_id") or "").strip()
        if not ref:
            continue
        ref_meta = refs.get(ref) or {}
        role = str(ref_meta.get("role") or "").strip().lower() or "generic"
        name = str(ref_meta.get("name") or "").strip()
        nth = ref_meta.get("nth")
        parts = [f"- {role}"]
        if name:
            parts.append(f'"{name}"')
        parts.append(f"[ref={ref}]")
        if nth is not None:
            parts.append(f"[nth={nth}]")
        lines.append(" ".join(parts))

    snapshot = "\n".join(lines) or "(empty)"
    trimmed_lines = snapshot.split("\n")[: max(1, min(int(line_limit or 500), 5000))]
    snapshot = "\n".join(trimmed_lines)
    snapshot, truncated = _limit_snapshot_text(snapshot, max_chars=max_chars)
    return {
        "snapshot": snapshot,
        "refs_mode": "role",
        "refs": refs,
        "tree": _build_role_tree(snapshot, refs),
        "truncated": truncated,
        "stats": _role_snapshot_stats(snapshot, refs),
    }


def _build_role_groups_for_container(
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref_id") or "").strip()
        if not ref:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        role = str(attrs.get("role_ref_role") or attrs.get("role") or "").strip().lower()
        if not role:
            tag = str(item.get("tag") or "").strip().lower()
            if tag == "a":
                role = "link"
            elif tag in {"input", "textarea"}:
                role = "textbox"
            elif tag == "select":
                role = "combobox"
            elif tag == "button":
                role = "button"
            else:
                role = "generic"
        name = str(
            attrs.get("role_ref_name")
            or item.get("text")
            or attrs.get("aria-label")
            or attrs.get("title")
            or ""
        ).strip()
        group_key = f"{role}:{name}"
        bucket = grouped.setdefault(
            group_key,
            {
                "role": role,
                "name": name or None,
                "count": 0,
                "refs": [],
                "nths": [],
                "labels": [],
            },
        )
        bucket["count"] += 1
        bucket["refs"].append(ref)
        nth = attrs.get("role_ref_nth")
        if nth is not None:
            bucket["nths"].append(int(nth))
        label = str(item.get("text") or attrs.get("aria-label") or attrs.get("title") or "").strip()
        if label and label not in bucket["labels"]:
            bucket["labels"].append(label)

    role_groups = list(grouped.values())
    role_groups.sort(key=lambda group: (-int(group.get("count", 0) or 0), str(group.get("role") or ""), str(group.get("name") or "")))
    for group in role_groups:
        name = str(group.get("name") or "").strip()
        role = str(group.get("role") or "").strip()
        count = int(group.get("count", 0) or 0)
        suffix = f' "{name}"' if name else ""
        group["summary"] = f'{role}{suffix} x{count}'
    return role_groups


def _build_context_snapshot_from_elements(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    container_entries: Dict[str, Dict[str, Any]] = {}
    child_refs_by_dom_ref: Dict[str, List[str]] = defaultdict(list)
    items_by_container_dom_ref: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for item in elements:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        container_dom_ref = str(attrs.get("container_dom_ref") or "").strip()
        if not container_dom_ref:
            continue
        if container_dom_ref not in container_entries:
            container_entries[container_dom_ref] = {
                "role": str(attrs.get("container_role") or "").strip() or None,
                "name": str(attrs.get("container_name") or "").strip() or None,
                "parent_dom_ref": str(attrs.get("container_parent_dom_ref") or "").strip() or None,
                "context_text": str(attrs.get("context_text") or "").strip() or None,
                "interactive": False,
            }
        ref_id = str(item.get("ref_id") or "").strip()
        if ref_id:
            child_refs_by_dom_ref[container_dom_ref].append(ref_id)
            container_entries[container_dom_ref]["interactive"] = True
            items_by_container_dom_ref[container_dom_ref].append(item)

    container_ref_by_dom_ref: Dict[str, str] = {}
    nodes: List[Dict[str, Any]] = []
    for index, (dom_ref, meta) in enumerate(container_entries.items()):
        ref_id = f"ctx-{index}"
        container_ref_by_dom_ref[dom_ref] = ref_id
        nodes.append(
            {
                "ref_id": ref_id,
                "role": meta.get("role"),
                "name": meta.get("name"),
                "parent_ref_id": None,
                "child_ref_ids": child_refs_by_dom_ref.get(dom_ref, []),
                "interactive": bool(meta.get("interactive")),
                "context_text": meta.get("context_text"),
                "role_groups": [],
                "_parent_dom_ref": meta.get("parent_dom_ref"),
            }
        )

    for node in nodes:
        parent_dom_ref = str(node.pop("_parent_dom_ref") or "").strip()
        if parent_dom_ref:
            node["parent_ref_id"] = container_ref_by_dom_ref.get(parent_dom_ref)

    role_groups_by_container_ref: Dict[str, List[Dict[str, Any]]] = {}
    for dom_ref, items in items_by_container_dom_ref.items():
        context_ref = container_ref_by_dom_ref.get(dom_ref)
        if not context_ref:
            continue
        groups = _build_role_groups_for_container(items)
        role_groups_by_container_ref[context_ref] = groups

    for node in nodes:
        node_ref = str(node.get("ref_id") or "").strip()
        if node_ref:
            node["role_groups"] = role_groups_by_container_ref.get(node_ref, [])

    for item in elements:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        container_dom_ref = str(attrs.get("container_dom_ref") or "").strip()
        if container_dom_ref and container_dom_ref in container_ref_by_dom_ref:
            context_ref = container_ref_by_dom_ref[container_dom_ref]
            attrs["container_ref_id"] = context_ref
            item["container_ref_id"] = context_ref

    node_by_ref = {str(node.get("ref_id") or ""): node for node in nodes if str(node.get("ref_id") or "")}
    return {
        "nodes": nodes,
        "node_by_ref": node_by_ref,
        "container_ref_by_dom_ref": container_ref_by_dom_ref,
        "role_groups_by_container_ref": role_groups_by_container_ref,
    }


async def _try_snapshot_for_ai(page: Page, timeout_ms: int = 5000) -> Optional[str]:
    timeout_ms = max(500, min(int(timeout_ms or 5000), 60000))

    # Playwright 내부 채널 snapshotForAI 시도 (OpenClaw parity)
    try:
        impl = getattr(page, "_impl_obj", None)
        channel = getattr(impl, "_channel", None)
        send = getattr(channel, "send", None)
        if callable(send):
            res = await send("snapshotForAI", {"timeout": timeout_ms, "track": "response"})
            if isinstance(res, dict):
                text = str(res.get("full") or "")
                if text.strip():
                    return text
    except Exception:
        pass

    # fallback: 접근성 스냅샷 문자열
    try:
        locator = page.locator(":root")
        aria_text = await locator.aria_snapshot(timeout=timeout_ms)
        if isinstance(aria_text, str) and aria_text.strip():
            return aria_text
    except Exception:
        pass
    return None


def _build_snapshot_text(
    elements: List[Dict[str, Any]],
    *,
    interactive_only: bool,
    compact: bool,
    limit: int,
    max_chars: int,
) -> Dict[str, Any]:
    lines: List[str] = []
    char_count = 0
    max_items = max(1, min(int(limit or 200), 5000))
    max_chars = max(200, min(int(max_chars or 24000), 120000))
    for idx, item in enumerate(elements):
        if not isinstance(item, dict):
            continue
        if interactive_only and not _element_is_interactive(item):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        tag = str(item.get("tag") or "").strip().lower() or "node"
        role = str(attrs.get("role") or "").strip().lower()
        ref = str(item.get("ref_id") or "").strip() or f"e{idx}"
        text = str(item.get("text") or "").strip()
        aria_label = str(attrs.get("aria-label") or "").strip()
        placeholder = str(attrs.get("placeholder") or "").strip()
        title = str(attrs.get("title") or "").strip()
        label = text or aria_label or placeholder or title
        label = re.sub(r"\s+", " ", label).strip()
        if len(label) > 140:
            label = label[:140]
        kind = role or tag
        if compact:
            if label:
                line = f"- {kind} \"{label}\" [ref={ref}]"
            else:
                line = f"- {kind} [ref={ref}]"
        else:
            line = f"- tag={tag} role={role or '-'} ref={ref}"
            if label:
                line += f" text=\"{label}\""
            if placeholder:
                line += f" placeholder=\"{placeholder[:80]}\""
        if char_count + len(line) + 1 > max_chars:
            break
        lines.append(line)
        char_count += len(line) + 1
        if len(lines) >= max_items:
            break
    return {
        "lines": lines,
        "text": "\n".join(lines),
        "stats": {
            "line_count": len(lines),
            "char_count": char_count,
            "interactive_only": bool(interactive_only),
            "compact": bool(compact),
            "limit": max_items,
            "max_chars": max_chars,
        },
    }
