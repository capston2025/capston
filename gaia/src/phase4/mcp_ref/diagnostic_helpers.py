from __future__ import annotations

import json as json_module
from typing import Any, Dict, List, Optional


async def capture_close_diagnostic(
    *,
    page: Any,
    locator: Any,
    requested_meta: Optional[Dict[str, Any]],
    attempt_idx: int,
    mode: str,
    attempt_logs: List[Dict[str, Any]],
    label: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    point_x = None
    point_y = None
    bbox = (
        requested_meta.get("bounding_box")
        if isinstance(requested_meta, dict)
        and isinstance(requested_meta.get("bounding_box"), dict)
        else {}
    )
    try:
        bx = float(bbox.get("x", 0.0) or 0.0)
        by = float(bbox.get("y", 0.0) or 0.0)
        bw = float(bbox.get("width", 0.0) or 0.0)
        bh = float(bbox.get("height", 0.0) or 0.0)
        if bw > 0.0 and bh > 0.0:
            point_x = float(
                bbox.get("center_x", bx + bw / 2.0) or (bx + bw / 2.0)
            )
            point_y = float(
                bbox.get("center_y", by + bh / 2.0) or (by + bh / 2.0)
            )
    except Exception:
        point_x = None
        point_y = None
    if point_x is None or point_y is None:
        try:
            box = await locator.bounding_box()
            if isinstance(box, dict):
                point_x = float(box.get("x", 0.0) or 0.0) + float(
                    box.get("width", 0.0) or 0.0
                ) / 2.0
                point_y = float(box.get("y", 0.0) or 0.0) + float(
                    box.get("height", 0.0) or 0.0
                ) / 2.0
        except Exception:
            point_x = None
            point_y = None
    if point_x is None:
        point_x = 0.0
    if point_y is None:
        point_y = 0.0

    diagnostic: Dict[str, Any]
    try:
        diagnostic_raw = await page.evaluate(
            """
            async ({ pointX, pointY }) => {
              const modalSelector = 'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"], [class*="modal"], [class*="dialog"], [class*="sheet"], [class*="drawer"], [class*="popup"], [class*="overlay"], [class*="backdrop"]';
              const backdropSelector = '.modal-backdrop, [class*="backdrop"], [class*="overlay"], [data-backdrop], [data-overlay]';
              const pathOf = (node) => {
                if (!(node instanceof Element)) return '';
                const parts = [];
                let current = node;
                for (let depth = 0; current && depth < 6; depth += 1) {
                  const tag = current.tagName.toLowerCase();
                  const id = current.id ? `#${current.id}` : '';
                  const cls = (current.className && typeof current.className === 'string')
                    ? `.${current.className.trim().split(/\\s+/).slice(0, 2).join('.')}`
                    : '';
                  parts.push(`${tag}${id}${cls}`);
                  current = current.parentElement;
                }
                return parts.join(' <- ');
              };
              const isVisible = (el) => {
                if (!(el instanceof HTMLElement)) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (Number(style.opacity || '1') <= 0) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 2 && rect.height > 2;
              };
              const sampleState = () => {
                const modalNodes = Array.from(document.querySelectorAll(modalSelector)).filter(isVisible);
                const backdropNodes = Array.from(document.querySelectorAll(backdropSelector)).filter(isVisible);
                const dialogNodes = Array.from(document.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]')).filter(isVisible);
                const modalOpen = modalNodes.length > 0 || backdropNodes.length > 0 || dialogNodes.length > 0;
                return {
                  modal_open: modalOpen,
                  modal_count: modalNodes.length,
                  backdrop_count: backdropNodes.length,
                  dialog_count: dialogNodes.length,
                };
              };
              const timeline = [];
              for (let i = 0; i < 8; i += 1) {
                timeline.push(sampleState().modal_open);
                await new Promise((resolve) => setTimeout(resolve, 80));
              }
              const state = sampleState();
              const hit = document.elementFromPoint(pointX, pointY);
              let clickable = hit instanceof Element ? hit : null;
              for (let d = 0; clickable && d < 8; d += 1) {
                if (
                  clickable.matches('button, a[href], [role="button"], [role="link"], [onclick], input[type="button"], input[type="submit"], [tabindex]:not([tabindex="-1"])')
                ) {
                  break;
                }
                clickable = clickable.parentElement;
              }
              return {
                ...state,
                timeline,
                hit_path: pathOf(hit),
                clickable_path: pathOf(clickable),
                active_path: pathOf(document.activeElement),
                point_x: pointX,
                point_y: pointY,
              };
            }
            """,
            {"pointX": point_x, "pointY": point_y},
        )
        diagnostic = (
            diagnostic_raw
            if isinstance(diagnostic_raw, dict)
            else {"raw": diagnostic_raw}
        )
    except Exception as diag_exc:
        diagnostic = {"error": str(diag_exc)}

    timeline = diagnostic.get("timeline") if isinstance(diagnostic, dict) else None
    if isinstance(timeline, list) and timeline:
        any_closed = any((v is False) for v in timeline)
        end_open = bool(timeline[-1])
        if any_closed and end_open:
            diagnostic["classification"] = "closed_then_reopened"
        elif all(bool(v) for v in timeline):
            diagnostic["classification"] = "never_closed"
        elif not end_open:
            diagnostic["classification"] = "closed_persisted"
        else:
            diagnostic["classification"] = "unknown_transition"

    record: Dict[str, Any] = {
        "attempt": attempt_idx,
        "mode": f"{mode}.close_diag",
        "reason_code": "diagnostic",
        "label": label,
        "diagnostic": diagnostic,
    }
    if isinstance(extra, dict):
        record.update(extra)
    attempt_logs.append(record)
    print(
        f"[close_diag] label={label} diag={json_module.dumps(diagnostic, ensure_ascii=False)}"
    )
