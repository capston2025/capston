from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from gaia.src.phase4.mcp_host_runtime import ensure_mcp_host_running, wait_for_mcp_ready


def is_mcp_transport_error(error_text: str) -> bool:
    lowered = str(error_text or "").lower()
    transport_markers = (
        "read timed out",
        "connection refused",
        "failed to establish a new connection",
        "max retries exceeded",
        "remote end closed connection",
        "connection aborted",
        "connection reset",
    )
    return any(marker in lowered for marker in transport_markers)


def recover_mcp_host(agent, *, context: str) -> bool:
    if wait_for_mcp_ready(agent.mcp_host_url, timeout_sec=1.2):
        return True
    recovered = ensure_mcp_host_running(agent.mcp_host_url, startup_timeout=8.0)
    if recovered:
        agent._log(f"♻️ MCP host 연결 복구 성공 ({context})")
    else:
        agent._log(f"⚠️ MCP host 연결 복구 실패 ({context})")
    return recovered


def normalize_bbox(bbox: Optional[dict]) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(bbox, dict):
        return None
    try:
        x = float(bbox.get("x"))
        y = float(bbox.get("y"))
        w = float(bbox.get("width"))
        h = float(bbox.get("height"))
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def detect_active_modal_region(agent, dom_elements) -> Optional[Dict[str, float]]:
    normalized_boxes: List[Tuple[float, float, float, float]] = []
    for el in dom_elements:
        box = normalize_bbox(el.bounding_box)
        if box:
            normalized_boxes.append(box)
    if not normalized_boxes:
        return None

    viewport_w = max((x + w) for x, _, w, _ in normalized_boxes)
    viewport_h = max((y + h) for _, y, _, h in normalized_boxes)
    viewport_area = max(1.0, viewport_w * viewport_h)

    candidates: List[Tuple[float, Dict[str, float], float]] = []
    for el in dom_elements:
        box = normalize_bbox(el.bounding_box)
        if not box:
            continue
        x, y, w, h = box
        area = w * h
        frac = area / viewport_area
        if frac < 0.03:
            continue

        role = str(el.role or "").strip().lower()
        aria_modal = str(el.aria_modal or "").strip().lower()
        class_blob = str(el.class_name or "").strip().lower()
        tag = str(el.tag or "").strip().lower()
        looks_modal = (
            role in {"dialog", "alertdialog"}
            or aria_modal == "true"
            or any(
                token in class_blob
                for token in ("modal", "dialog", "drawer", "sheet", "popup")
            )
            or (tag == "dialog")
        )
        if not looks_modal:
            continue

        score = 0.0
        if role in {"dialog", "alertdialog"}:
            score += 6.0
        if aria_modal == "true":
            score += 5.0
        if tag == "dialog":
            score += 2.0
        if frac < 0.98:
            score += 1.0
        if frac > 0.995:
            score -= 2.0
        if 0.05 <= frac <= 0.9:
            score += 1.0
        candidates.append(
            (
                score,
                {"x": x, "y": y, "width": w, "height": h},
                frac,
            )
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = candidates[0][1]
    for _, region, frac in candidates:
        if frac < 0.95:
            selected = region
            break
    return selected


def is_bbox_inside_region(
    agent,
    bbox: Optional[dict],
    region: Dict[str, float],
) -> bool:
    normalized = normalize_bbox(bbox)
    if not normalized:
        return True
    x, y, w, h = normalized
    cx = x + (w / 2.0)
    cy = y + (h / 2.0)
    margin = 8.0
    left = float(region.get("x", 0.0)) - margin
    top = float(region.get("y", 0.0)) - margin
    right = float(region.get("x", 0.0) + region.get("width", 0.0)) + margin
    bottom = float(region.get("y", 0.0) + region.get("height", 0.0)) + margin
    return left <= cx <= right and top <= cy <= bottom


def should_open_menu_for_action(
    agent,
    action,
    selector: str,
) -> bool:
    description = action.description.lower()
    selector_lower = selector.lower()
    if "sidebar" in selector_lower or "menu" in selector_lower:
        return "링크" in description or "메뉴" in description
    return False


def find_open_menu_selector(agent, page_state) -> Optional[str]:
    for element in page_state.interactive_elements:
        if element.tag != "button":
            continue
        label = (element.text or "").lower()
        aria_label = (element.aria_label or "").lower()
        combined = f"{label} {aria_label}".strip()
        if not combined:
            continue
        if "menu" in combined and "close" not in combined and "open" in combined:
            selector = agent._find_selector_by_element_id(
                element.element_id, page_state
            )
            if selector:
                return selector
    return None


def find_close_menu_selector(agent, page_state) -> Optional[str]:
    for element in page_state.interactive_elements:
        if element.tag != "button":
            continue
        label = (element.text or "").lower()
        aria_label = (element.aria_label or "").lower()
        combined = f"{label} {aria_label}".strip()
        if not combined:
            continue
        if "menu" in combined and "close" in combined:
            selector = agent._find_selector_by_element_id(
                element.element_id, page_state
            )
            if selector:
                return selector
    return None
