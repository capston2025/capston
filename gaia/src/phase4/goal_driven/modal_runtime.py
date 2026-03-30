from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import DOMElement


def pick_login_modal_close_element(
    cls,
    dom_elements: List[DOMElement],
    selector_map: Dict[int, str],
) -> Optional[int]:
    candidates: List[tuple[int, int]] = []
    for el in dom_elements:
        selector = selector_map.get(el.id, "")
        score = 0

        text_fields = [
            el.text,
            el.aria_label,
            el.placeholder,
            getattr(el, "title", None),
            selector,
        ]
        if any(cls._contains_close_hint(field) for field in text_fields):
            score += 3
        if cls._normalize_text(el.text) in {"x", "×", "닫기", "close"}:
            score += 3
        if cls._normalize_text(el.tag) in {"button", "a"}:
            score += 1
        if cls._normalize_text(el.role) in {"button", "dialogclose"}:
            score += 1

        normalized_selector = cls._normalize_text(selector)
        if any(h in normalized_selector for h in ("close", "cancel", "modal", "dialog", "dismiss")):
            score += 2

        if any(cls._contains_login_hint(field) for field in text_fields):
            score -= 2
        if cls._normalize_text(el.type) == "submit":
            score -= 2

        if score > 0:
            candidates.append((score, el.id))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]

def pick_modal_unblock_element(
    cls,
    dom_elements: List[DOMElement],
    selector_map: Dict[int, str],
    modal_regions_hint: Optional[List[Dict[str, Any]]] = None,
) -> Optional[int]:
    modal_regions: List[Dict[str, float]] = []
    if isinstance(modal_regions_hint, list):
        for region in modal_regions_hint[:8]:
            if not isinstance(region, dict):
                continue
            try:
                rx = float(region.get("x", 0.0) or 0.0)
                ry = float(region.get("y", 0.0) or 0.0)
                rw = float(region.get("width", 0.0) or 0.0)
                rh = float(region.get("height", 0.0) or 0.0)
            except Exception:
                continue
            if rw < 80.0 or rh < 80.0:
                continue
            modal_regions.append(
                {
                    "x": rx,
                    "y": ry,
                    "width": rw,
                    "height": rh,
                    "right": rx + rw,
                    "bottom": ry + rh,
                }
            )
    for container in dom_elements:
        bbox = container.bounding_box if isinstance(container.bounding_box, dict) else {}
        try:
            cx = float(bbox.get("x", 0.0) or 0.0)
            cy = float(bbox.get("y", 0.0) or 0.0)
            cw = float(bbox.get("width", 0.0) or 0.0)
            ch = float(bbox.get("height", 0.0) or 0.0)
        except Exception:
            continue
        if cw < 120.0 or ch < 120.0:
            continue
        role = cls._normalize_text(container.role)
        tag = cls._normalize_text(container.tag)
        class_name = cls._normalize_text(container.class_name)
        aria_modal = cls._normalize_text(container.aria_modal)
        if not (
            aria_modal == "true"
            or role in {"dialog", "alertdialog"}
            or tag == "dialog"
            or any(token in class_name for token in ("modal", "dialog", "popup", "sheet", "drawer", "overlay"))
        ):
            continue
        modal_regions.append(
            {
                "x": cx,
                "y": cy,
                "width": cw,
                "height": ch,
                "right": cx + cw,
                "bottom": cy + ch,
            }
        )
    modal_regions.sort(key=lambda region: region["width"] * region["height"], reverse=True)
    if len(modal_regions) > 1:
        largest_area = modal_regions[0]["width"] * modal_regions[0]["height"]
        compact_regions = [
            region
            for region in modal_regions
            if (region["width"] * region["height"]) <= (largest_area * 0.92)
        ]
        if compact_regions:
            modal_regions = compact_regions
    modal_regions = modal_regions[:4]

    candidates: List[tuple[int, int]] = []
    for el in dom_elements:
        selector = selector_map.get(el.id, "")
        role = cls._normalize_text(el.role)
        tag = cls._normalize_text(el.tag)

        text_fields = [
            el.text,
            el.aria_label,
            el.placeholder,
            getattr(el, "title", None),
            selector,
        ]
        normalized_blob = " ".join(cls._normalize_text(field) for field in text_fields if field)
        score = 0
        close_hint_signal = any(cls._contains_close_hint(field) for field in text_fields)

        if close_hint_signal:
            score += 5
        if any(
            token in normalized_blob
            for token in ("확인", "ok", "okay", "dismiss", "취소", "cancel", "닫기", "close")
        ):
            score += 4
        if any(
            token in normalized_blob
            for token in ("modal", "dialog", "overlay", "backdrop", "popup", "sheet", "drawer")
        ):
            score += 3
        if role in {"button", "dialogclose", "link", "menuitem"} or tag in {"button", "a", "input"}:
            score += 1
        if cls._normalize_text(el.text) in {"x", "×", "확인", "ok", "닫기", "취소", "close"}:
            score += 2
        if cls._normalize_text(el.type) == "submit":
            score -= 1
        bbox = el.bounding_box if isinstance(el.bounding_box, dict) else {}
        try:
            ex = float(bbox.get("x", 0.0) or 0.0)
            ey = float(bbox.get("y", 0.0) or 0.0)
            ew = float(bbox.get("width", 0.0) or 0.0)
            eh = float(bbox.get("height", 0.0) or 0.0)
            ecx = ex + (ew / 2.0)
            ecy = ey + (eh / 2.0)
        except Exception:
            ex = ey = ew = eh = ecx = ecy = 0.0
        inside_modal_region = False
        near_modal_corner = False
        if ew > 0.0 and eh > 0.0:
            if ew <= 96.0 and eh <= 96.0:
                score += 1
            for region in modal_regions:
                if not (region["x"] <= ecx <= region["right"] and region["y"] <= ecy <= region["bottom"]):
                    continue
                inside_modal_region = True
                rel_x = (ecx - region["x"]) / max(region["width"], 1.0)
                rel_y = (ecy - region["y"]) / max(region["height"], 1.0)
                if rel_x >= 0.72 and rel_y <= 0.28:
                    near_modal_corner = True
                    score += 6
                elif rel_x >= 0.60 and rel_y <= 0.40:
                    score += 3
                unlabeled_icon = (
                    cls._normalize_text(el.text) in {"", "x", "×", "✕"}
                    and cls._normalize_text(el.aria_label) == ""
                    and cls._normalize_text(getattr(el, "title", None)) == ""
                    and (role in {"button", "dialogclose"} or tag in {"button", "a", "input"})
                    and ew <= 96.0
                    and eh <= 96.0
                )
                if unlabeled_icon:
                    score += 4
                    if near_modal_corner:
                        score += 2
                break
        if modal_regions and not inside_modal_region:
            # 모달이 열려 있는 상황에서는 모달 영역 밖 아이콘/버튼 오클릭을 강하게 억제.
            if not close_hint_signal:
                continue
            score -= 3

        if modal_regions:
            if not (close_hint_signal or near_modal_corner):
                continue
        else:
            if not close_hint_signal:
                continue
        if score > 0 and el.id in selector_map:
            candidates.append((score, el.id))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]
