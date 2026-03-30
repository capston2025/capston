from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple


def select_frame_for_ref(page: Any, ref_meta: Dict[str, Any]) -> Tuple[Any, int]:
    scope = ref_meta.get("scope", {}) if isinstance(ref_meta.get("scope"), dict) else {}
    frame_index = int(scope.get("frame_index", ref_meta.get("frame_index", 0)) or 0)
    try:
        frames = page.frames
        if 0 <= frame_index < len(frames):
            return frames[frame_index], frame_index
    except Exception:
        pass
    return page.main_frame, 0


async def resolve_locator_from_ref(
    page: Any,
    ref_meta: Dict[str, Any],
    selector_hint_raw: str,
) -> Tuple[Optional[Any], int, str, str]:
    frame, frame_index = select_frame_for_ref(page, ref_meta)
    selector_hint = str(selector_hint_raw or "").strip()
    if selector_hint.startswith("role_ref:"):
        try:
            payload = json.loads(selector_hint[len("role_ref:"):])
        except Exception:
            return None, frame_index, selector_hint, "invalid_role_ref_hint"
        role = str(payload.get("role") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not role or not name:
            return None, frame_index, selector_hint, "invalid_role_ref_hint"
        try:
            nth = int(payload.get("nth", 0) or 0)
        except Exception:
            nth = 0
        try:
            locator_group = frame.get_by_role(role, name=name, exact=True)
            match_count = await locator_group.count()
            if match_count <= 0:
                return None, frame_index, selector_hint, "role_ref_not_found"
            if nth >= match_count:
                nth = match_count - 1
            nth = max(0, nth)
            return locator_group.nth(nth), frame_index, f'role={role} name="{name}" nth={nth}', ""
        except Exception as exc:
            return None, frame_index, selector_hint, str(exc) or "role_ref_not_found"

    dom_ref = str(ref_meta.get("dom_ref") or "").strip()
    if not dom_ref:
        return None, frame_index, "", "dom_ref_missing"

    selector_to_use = f'[data-gaia-dom-ref="{dom_ref}"]'
    try:
        locator_group = frame.locator(selector_to_use)
        match_count = await locator_group.count()
        if match_count <= 0:
            return None, frame_index, selector_to_use, "not_found"
        if match_count == 1:
            return locator_group.nth(0), frame_index, selector_to_use, ""

        bbox = ref_meta.get("bounding_box") if isinstance(ref_meta.get("bounding_box"), dict) else {}
        try:
            target_cx = float(
                bbox.get("center_x", (float(bbox.get("x", 0.0)) + float(bbox.get("width", 0.0)) / 2.0))
            )
            target_cy = float(
                bbox.get("center_y", (float(bbox.get("y", 0.0)) + float(bbox.get("height", 0.0)) / 2.0))
            )
        except Exception:
            target_cx = None
            target_cy = None

        best_idx = None
        best_dist = None
        inspect_limit = min(match_count, 25)
        if target_cx is not None and target_cy is not None:
            for idx in range(inspect_limit):
                candidate = locator_group.nth(idx)
                try:
                    cand_box = await candidate.bounding_box()
                except Exception:
                    cand_box = None
                if not cand_box:
                    continue
                cx = float(cand_box.get("x", 0.0)) + (float(cand_box.get("width", 0.0)) / 2.0)
                cy = float(cand_box.get("y", 0.0)) + (float(cand_box.get("height", 0.0)) / 2.0)
                dist = ((cx - target_cx) ** 2) + ((cy - target_cy) ** 2)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_idx = idx
        if best_idx is not None:
            return locator_group.nth(best_idx), frame_index, f"{selector_to_use} [nth={best_idx}]", ""
        return None, frame_index, selector_to_use, f"ambiguous_selector_matches:{match_count}"
    except Exception as exc:
        return None, frame_index, selector_to_use, str(exc)


def parse_scroll_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, (int, float)):
        return {"mode": "delta", "delta": int(value)}

    text = str(value or "down").strip().lower()
    if text in {"down", "pagedown", "page_down"}:
        return {"mode": "delta", "delta": 800}
    if text in {"up", "pageup", "page_up"}:
        return {"mode": "delta", "delta": -800}
    if text == "top":
        return {"mode": "top", "delta": 0}
    if text == "bottom":
        return {"mode": "bottom", "delta": 0}
    try:
        return {"mode": "delta", "delta": int(float(text))}
    except Exception:
        return {"mode": "delta", "delta": 800}


async def reveal_locator_in_scroll_context(locator: Any) -> Dict[str, Any]:
    return await locator.evaluate(
        """
        (el) => {
          const margin = 24;
          const isScrollable = (node) => {
            const style = window.getComputedStyle(node);
            const oy = `${style.overflowY || ''} ${style.overflow || ''}`.toLowerCase();
            const ox = `${style.overflowX || ''} ${style.overflow || ''}`.toLowerCase();
            const canY = /(auto|scroll|overlay)/.test(oy) && node.scrollHeight > node.clientHeight + 2;
            const canX = /(auto|scroll|overlay)/.test(ox) && node.scrollWidth > node.clientWidth + 2;
            return canY || canX;
          };

          let container = null;
          let p = el.parentElement;
          while (p) {
            if (isScrollable(p)) {
              container = p;
              break;
            }
            p = p.parentElement;
          }

          let moved = false;
          if (container) {
            const er = el.getBoundingClientRect();
            const cr = container.getBoundingClientRect();
            let dy = 0;
            let dx = 0;
            if (er.top < cr.top + margin) dy = er.top - (cr.top + margin);
            else if (er.bottom > cr.bottom - margin) dy = er.bottom - (cr.bottom - margin);
            if (er.left < cr.left + margin) dx = er.left - (cr.left + margin);
            else if (er.right > cr.right - margin) dx = er.right - (cr.right - margin);
            if (dy !== 0) {
              container.scrollTop += dy;
              moved = true;
            }
            if (dx !== 0) {
              container.scrollLeft += dx;
              moved = true;
            }
          }

          try {
            el.scrollIntoView({ behavior: "instant", block: "center", inline: "nearest" });
          } catch (_) {}

          return {
            moved,
            container: container ? container.tagName.toLowerCase() : "window",
          };
        }
        """
    )


async def scroll_locator_container(locator: Any, value: Any) -> Dict[str, Any]:
    payload = parse_scroll_payload(value)
    return await locator.evaluate(
        """
        (el, payload) => {
          const isScrollable = (node) => {
            const style = window.getComputedStyle(node);
            const oy = `${style.overflowY || ''} ${style.overflow || ''}`.toLowerCase();
            const ox = `${style.overflowX || ''} ${style.overflow || ''}`.toLowerCase();
            const canY = /(auto|scroll|overlay)/.test(oy) && node.scrollHeight > node.clientHeight + 2;
            const canX = /(auto|scroll|overlay)/.test(ox) && node.scrollWidth > node.clientWidth + 2;
            return canY || canX;
          };

          let container = null;
          let p = el.parentElement;
          while (p) {
            if (isScrollable(p)) {
              container = p;
              break;
            }
            p = p.parentElement;
          }

          const target = container || document.scrollingElement || document.documentElement;
          const beforeTop = target.scrollTop;
          const beforeLeft = target.scrollLeft;

          if (payload.mode === "top") {
            target.scrollTop = 0;
          } else if (payload.mode === "bottom") {
            target.scrollTop = target.scrollHeight;
          } else {
            target.scrollTop += Number(payload.delta || 0);
          }

          try {
            el.scrollIntoView({ behavior: "instant", block: "nearest", inline: "nearest" });
          } catch (_) {}

          return {
            target: container ? container.tagName.toLowerCase() : "window",
            moved: target.scrollTop !== beforeTop || target.scrollLeft !== beforeLeft,
            top: target.scrollTop,
          };
        }
        """,
        payload,
    )


def validate_upload_path(path: str) -> str:
    resolved = os.path.realpath(path)
    upload_dir = os.getenv("GAIA_UPLOAD_DIR", "")
    if upload_dir:
        allowed = os.path.realpath(upload_dir)
        if not resolved.startswith(allowed + os.sep) and resolved != allowed:
            raise ValueError(f"File path not allowed (outside GAIA_UPLOAD_DIR): {path}")
    if not os.path.isfile(resolved):
        raise ValueError(f"File not found: {path}")
    return resolved
