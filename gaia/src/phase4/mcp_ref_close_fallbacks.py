from __future__ import annotations

import json as json_module
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


async def attempt_close_ref_fallbacks(
    *,
    close_like_click: bool,
    page: Any,
    attempt_idx: int,
    mode: str,
    ref_id: str,
    requested_meta: Optional[Dict[str, Any]],
    requested_snapshot: Optional[Dict[str, Any]],
    attempt_logs: List[Dict[str, Any]],
    deadline_exceeded_fn: Callable[[], bool],
    collect_close_ref_candidates_fn: Callable[..., List[Tuple[str, Dict[str, Any]]]],
    build_ref_candidates_fn: Callable[[Dict[str, Any]], List[Tuple[str, str]]],
    resolve_locator_from_ref_fn: Callable[..., Awaitable[Tuple[Any, Any, Any, Any]]],
    collect_state_change_probe_fn: Callable[..., Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    if not close_like_click:
        return {"success": False}
    close_fallbacks = collect_close_ref_candidates_fn(
        snapshot=requested_snapshot if isinstance(requested_snapshot, dict) else None,
        requested_meta=requested_meta if isinstance(requested_meta, dict) else None,
        exclude_ref_id=ref_id,
        limit=5,
    )
    if close_fallbacks:
        debug_candidates: List[Dict[str, Any]] = []
        for cand_ref_id, cand_meta in close_fallbacks:
            cand_bbox = (
                cand_meta.get("bounding_box")
                if isinstance(cand_meta.get("bounding_box"), dict)
                else {}
            )
            debug_candidates.append(
                {
                    "ref_id": cand_ref_id,
                    "text": str(cand_meta.get("text") or "")[:40],
                    "selector": str(cand_meta.get("selector") or "")[:80],
                    "cx": cand_bbox.get("center_x"),
                    "cy": cand_bbox.get("center_y"),
                }
            )
        print(
            f"[close_diag] close_fallback_candidates={json_module.dumps(debug_candidates, ensure_ascii=False)}"
        )
    if not close_fallbacks:
        return {"success": False}
    for fallback_rank, (fallback_ref_id, fallback_meta) in enumerate(
        close_fallbacks, start=1
    ):
        if deadline_exceeded_fn():
            return {"success": False}
        fallback_candidates = build_ref_candidates_fn(fallback_meta)
        deduped_fallback: List[Tuple[str, str]] = []
        seen_fallback_selectors = set()
        for fallback_mode, fallback_selector in fallback_candidates:
            fallback_key = str(fallback_selector or "").strip()
            if not fallback_key or fallback_key in seen_fallback_selectors:
                continue
            seen_fallback_selectors.add(fallback_key)
            deduped_fallback.append((fallback_mode, fallback_selector))
        for fallback_mode, fallback_selector in deduped_fallback[:2]:
            if deadline_exceeded_fn():
                return {"success": False}
            fallback_locator, fallback_frame_index, fallback_resolved_selector, fallback_locator_error = await resolve_locator_from_ref_fn(
                page, fallback_meta, fallback_selector
            )
            if fallback_locator is None:
                attempt_logs.append(
                    {
                        "attempt": attempt_idx,
                        "mode": f"{mode}.close_ref[{fallback_rank}]",
                        "selector": str(fallback_resolved_selector or fallback_selector),
                        "reason_code": "not_found",
                        "error": str(
                            fallback_locator_error or "fallback ref locator not found"
                        ),
                        "fallback_ref_id": fallback_ref_id,
                    }
                )
                continue
            try:
                await fallback_locator.click(timeout=1500, no_wait_after=True)
            except Exception as fallback_click_exc:
                attempt_logs.append(
                    {
                        "attempt": attempt_idx,
                        "mode": f"{mode}.close_ref[{fallback_rank}]",
                        "selector": fallback_resolved_selector,
                        "frame_index": fallback_frame_index,
                        "reason_code": "not_actionable",
                        "error": str(fallback_click_exc),
                        "fallback_ref_id": fallback_ref_id,
                    }
                )
                continue
            await page.wait_for_timeout(250)
            fallback_change = await collect_state_change_probe_fn(
                probe_wait_ms=250,
                probe_scroll=f"alternate_close_ref:{fallback_ref_id}",
            )
            if bool(fallback_change.get("effective", True)):
                attempt_logs.append(
                    {
                        "attempt": attempt_idx,
                        "mode": f"{mode}.close_ref[{fallback_rank}]",
                        "selector": fallback_resolved_selector,
                        "frame_index": fallback_frame_index,
                        "reason_code": "ok",
                        "fallback": "alternate_close_ref",
                        "fallback_ref_id": fallback_ref_id,
                        "state_change": fallback_change,
                    }
                )
                return {
                    "success": True,
                    "state_change": fallback_change,
                    "ref_id": fallback_ref_id,
                    "requested_meta": fallback_meta,
                }
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": f"{mode}.close_ref[{fallback_rank}]",
                    "selector": fallback_resolved_selector,
                    "frame_index": fallback_frame_index,
                    "reason_code": "no_state_change",
                    "fallback_ref_id": fallback_ref_id,
                    "state_change": fallback_change,
                }
            )
    return {"success": False}


async def attempt_backdrop_close(
    *,
    close_like_click: bool,
    page: Any,
    attempt_idx: int,
    mode: str,
    attempt_logs: List[Dict[str, Any]],
    deadline_exceeded_fn: Callable[[], bool],
    collect_state_change_probe_fn: Callable[..., Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    if not close_like_click or deadline_exceeded_fn():
        return {"success": False}
    try:
        clicked = await page.evaluate(
            """
            () => {
              const nodes = Array.from(document.querySelectorAll(
                '.modal-backdrop, [class*="backdrop"], [class*="overlay"], [data-backdrop], [data-overlay]'
              ));
              const visible = nodes.filter((el) => {
                if (!(el instanceof HTMLElement)) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 4 && rect.height > 4;
              });
              if (!visible.length) return false;
              const target = visible[0];
              const rect = target.getBoundingClientRect();
              const x = rect.left + rect.width / 2;
              const y = rect.top + rect.height / 2;
              const opts = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 };
              target.dispatchEvent(new MouseEvent('mousedown', opts));
              target.dispatchEvent(new MouseEvent('mouseup', opts));
              target.dispatchEvent(new MouseEvent('click', opts));
              return true;
            }
            """
        )
    except Exception as backdrop_exc:
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.backdrop",
                "reason_code": "not_actionable",
                "error": str(backdrop_exc),
            }
        )
        return {"success": False}
    if not bool(clicked):
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.backdrop",
                "reason_code": "not_found",
                "error": "no visible backdrop candidate",
            }
        )
        return {"success": False}
    await page.wait_for_timeout(250)
    backdrop_change = await collect_state_change_probe_fn(
        probe_wait_ms=250,
        probe_scroll="backdrop_fallback",
    )
    if bool(backdrop_change.get("effective", True)):
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.backdrop",
                "reason_code": "ok",
                "fallback": "backdrop_click",
                "state_change": backdrop_change,
            }
        )
        return {"success": True, "state_change": backdrop_change}
    attempt_logs.append(
        {
            "attempt": attempt_idx,
            "mode": f"{mode}.backdrop",
            "reason_code": "no_state_change",
            "state_change": backdrop_change,
        }
    )
    return {"success": False}


async def attempt_modal_corner_close(
    *,
    close_like_click: bool,
    page: Any,
    attempt_idx: int,
    mode: str,
    attempt_logs: List[Dict[str, Any]],
    deadline_exceeded_fn: Callable[[], bool],
    collect_state_change_probe_fn: Callable[..., Awaitable[Dict[str, Any]]],
    modal_regions: Optional[List[Dict[str, float]]] = None,
) -> Dict[str, Any]:
    if not close_like_click or deadline_exceeded_fn():
        return {"success": False}
    normalized_regions: List[Dict[str, float]] = []
    if isinstance(modal_regions, list):
        for raw in modal_regions:
            if not isinstance(raw, dict):
                continue
            try:
                x = float(raw.get("x", 0.0) or 0.0)
                y = float(raw.get("y", 0.0) or 0.0)
                width = float(raw.get("width", 0.0) or 0.0)
                height = float(raw.get("height", 0.0) or 0.0)
            except Exception:
                continue
            if width <= 0.0 or height <= 0.0:
                continue
            normalized_regions.append(
                {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "right": x + width,
                    "bottom": y + height,
                }
            )
    try:
        click_result = await page.evaluate(
            """
            (regions) => {
              const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
              const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;

              const norm = (raw) => {
                if (!raw || typeof raw !== 'object') return null;
                const x = Number(raw.x || 0);
                const y = Number(raw.y || 0);
                const width = Number(raw.width || 0);
                const height = Number(raw.height || 0);
                if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height)) return null;
                if (width <= 0 || height <= 0) return null;
                return { x, y, width, height, right: x + width, bottom: y + height };
              };

              const external = Array.isArray(regions) ? regions.map(norm).filter(Boolean) : [];
              const detected = Array.from(document.querySelectorAll(
                '[role="dialog"], [aria-modal="true"], dialog, [class*="modal"], [class*="dialog"], [class*="popup"], [class*="drawer"]'
              ))
                .filter((el) => {
                  if (!(el instanceof HTMLElement)) return false;
                  const style = window.getComputedStyle(el);
                  if (!style) return false;
                  if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') return false;
                  if (Number(style.opacity || '1') <= 0.02) return false;
                  const rect = el.getBoundingClientRect();
                  return rect.width >= 120 && rect.height >= 120;
                })
                .map((el) => {
                  const rect = el.getBoundingClientRect();
                  return {
                    x: rect.left,
                    y: rect.top,
                    width: rect.width,
                    height: rect.height,
                    right: rect.left + rect.width,
                    bottom: rect.top + rect.height,
                  };
                });

              const merged = [...external, ...detected]
                .filter((r) => r.right > 0 && r.bottom > 0 && r.x < viewportW && r.y < viewportH)
                .sort((a, b) => (b.width * b.height) - (a.width * a.height))
                .slice(0, 6);

              if (!merged.length) return { clicked: false, reason: 'no_modal_region' };

              const dispatchClick = (target, x, y) => {
                const opts = { bubbles: true, cancelable: true, view: window, button: 0, clientX: x, clientY: y };
                target.dispatchEvent(new MouseEvent('mousemove', opts));
                target.dispatchEvent(new MouseEvent('mousedown', opts));
                target.dispatchEvent(new MouseEvent('mouseup', opts));
                target.dispatchEvent(new MouseEvent('click', opts));
              };

              for (let i = 0; i < merged.length; i++) {
                const region = merged[i];
                const points = [
                  {
                    x: Math.min(region.right - 14, Math.max(region.x + 10, region.x + region.width * 0.94)),
                    y: Math.max(region.y + 10, Math.min(region.bottom - 10, region.y + region.height * 0.07)),
                  },
                  {
                    x: Math.min(region.right - 24, Math.max(region.x + 12, region.x + region.width * 0.88)),
                    y: Math.max(region.y + 12, Math.min(region.bottom - 12, region.y + region.height * 0.12)),
                  },
                ];
                for (const p of points) {
                  const x = Math.max(1, Math.min(viewportW - 1, Math.round(p.x)));
                  const y = Math.max(1, Math.min(viewportH - 1, Math.round(p.y)));
                  const hit = document.elementFromPoint(x, y);
                  if (!(hit instanceof HTMLElement)) continue;
                  const actionable = hit.closest('button,[role="button"],[aria-label],[title],a,[tabindex]');
                  const target = actionable instanceof HTMLElement ? actionable : hit;
                  const style = window.getComputedStyle(target);
                  if (!style) continue;
                  if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') continue;
                  if (Number(style.opacity || '1') <= 0.02) continue;
                  dispatchClick(target, x, y);
                  return {
                    clicked: true,
                    reason: 'modal_corner_click',
                    region_index: i,
                    x,
                    y,
                    target_tag: (target.tagName || '').toLowerCase(),
                    target_text: ((target.innerText || '').trim().slice(0, 80)),
                  };
                }
              }
              return { clicked: false, reason: 'corner_point_miss' };
            }
            """,
            normalized_regions,
        )
    except Exception as corner_exc:
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.modal_corner",
                "reason_code": "not_actionable",
                "error": str(corner_exc),
            }
        )
        return {"success": False}
    if not isinstance(click_result, dict) or not bool(click_result.get("clicked")):
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.modal_corner",
                "reason_code": "not_found",
                "error": str((click_result or {}).get("reason") or "modal corner candidate not found"),
            }
        )
        return {"success": False}
    await page.wait_for_timeout(250)
    corner_change = await collect_state_change_probe_fn(
        probe_wait_ms=300,
        probe_scroll="modal_corner_fallback",
    )
    if bool(corner_change.get("effective", True)):
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.modal_corner",
                "reason_code": "ok",
                "fallback": "modal_corner_click",
                "meta": click_result,
                "state_change": corner_change,
            }
        )
        return {"success": True, "state_change": corner_change}
    attempt_logs.append(
        {
            "attempt": attempt_idx,
            "mode": f"{mode}.modal_corner",
            "reason_code": "no_state_change",
            "fallback": "modal_corner_click",
            "meta": click_result,
            "state_change": corner_change,
        }
    )
    return {"success": False}
