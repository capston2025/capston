from __future__ import annotations

import json as json_module
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from gaia.src.phase4.mcp_ref.input_helpers import trusted_click_point
from gaia.src.phase4.mcp_ref.post_click_watch import watch_after_trusted_click


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
        plan = await page.evaluate(
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
                if (Number(style.opacity || '1') <= 0.02) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 4 && rect.height > 4;
              });
              if (!visible.length) {
                return { found: false, reason: 'no_visible_backdrop' };
              }
              const target = visible[0];
              const rect = target.getBoundingClientRect();
              const x = rect.left + rect.width / 2;
              const y = rect.top + rect.height / 2;
              return {
                found: true,
                reason: 'backdrop_point',
                x,
                y,
                rect: {
                  left: rect.left,
                  top: rect.top,
                  width: rect.width,
                  height: rect.height
                }
              };
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
    if not isinstance(plan, dict) or not bool(plan.get("found")):
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.backdrop",
                "reason_code": "not_found",
                "error": str((plan or {}).get("reason") or "no visible backdrop candidate"),
            }
        )
        return {"success": False}
    watch_ms = 1200
    settle_ms = 900
    try:
        watch_ms = int(str(os.getenv("GAIA_FALLBACK_WATCH_MS", "1200")).strip() or "1200")
        settle_ms = int(
            str(os.getenv("GAIA_FALLBACK_WATCH_SETTLE_MS", "900")).strip() or "900"
        )
    except Exception:
        watch_ms = 1200
        settle_ms = 900

    click_meta: Dict[str, Any] = {}

    async def _click() -> None:
        nonlocal click_meta
        click_meta = await trusted_click_point(
            page,
            float(plan.get("x") or 0.0),
            float(plan.get("y") or 0.0),
            delay_ms=50,
            move_first=True,
            clamp_to_viewport=True,
        )
        if not bool(click_meta.get("clicked")):
            raise RuntimeError(str(click_meta.get("error") or "playwright_mouse_click_failed"))

    post_watch = await watch_after_trusted_click(
        page,
        _click,
        watch_ms=watch_ms,
        settle_ms=settle_ms,
        wait_until="commit",
        watch_popup=True,
        watch_navigation=True,
        watch_dialog=True,
        auto_dismiss_dialog=True,
        auto_close_popup=False,
    )
    click_meta = {**plan, **click_meta}
    if not bool(click_meta.get("clicked")):
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.backdrop",
                "reason_code": "not_actionable",
                "fallback": "backdrop_click",
                "meta": click_meta,
                "error": str(click_meta.get("error") or "playwright_mouse_click_failed"),
                "post_watch": post_watch,
            }
        )
        return {"success": False}
    await page.wait_for_timeout(250)
    backdrop_change = await collect_state_change_probe_fn(
        probe_wait_ms=250,
        probe_scroll="backdrop_fallback",
    )
    if bool(backdrop_change.get("effective", True)):
        backdrop_change["post_watch"] = post_watch
        if bool(post_watch.get("nav_detected")) or bool(post_watch.get("popup_detected")):
            backdrop_change["resnapshot_required"] = True
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.backdrop",
                "reason_code": "ok",
                "fallback": "backdrop_click",
                "meta": click_meta,
                "post_watch": post_watch,
                "state_change": backdrop_change,
            }
        )
        return {"success": True, "state_change": backdrop_change}
    attempt_logs.append(
        {
            "attempt": attempt_idx,
            "mode": f"{mode}.backdrop",
            "reason_code": "no_state_change",
            "fallback": "backdrop_click",
            "meta": click_meta,
            "post_watch": post_watch,
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
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    if not close_like_click or deadline_exceeded_fn():
        return {"success": False}
    for log in attempt_logs:
        if (
            int(log.get("attempt") or -1) == int(attempt_idx)
            and str(log.get("fallback") or "") == "modal_corner_click"
        ):
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": f"{mode}.modal_corner",
                    "reason_code": "not_found",
                    "error": "modal_corner_already_attempted",
                }
            )
            return {"success": False}

    if min_confidence is None:
        try:
            min_confidence = float(
                str(os.getenv("GAIA_MODAL_CORNER_MIN_CONFIDENCE", "0.55")).strip()
            )
        except Exception:
            min_confidence = 0.55
    try:
        min_confidence = max(0.0, min(1.0, float(min_confidence)))
    except Exception:
        min_confidence = 0.55

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
        plan = await page.evaluate(
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
                .slice(0, 24);

              if (!merged.length) return { found: false, reason: 'no_modal_region' };

              const rankedRegions = merged
                .map((r) => {
                  const area = Number(r.width || 0) * Number(r.height || 0);
                  const overlayRoot = Number(r.width || 0) >= viewportW * 0.88 && Number(r.height || 0) >= viewportH * 0.88;
                  return { ...r, _area: area, _overlay_root: overlayRoot };
                })
                .sort((a, b) => {
                  if (Number(a._overlay_root) !== Number(b._overlay_root)) {
                    return Number(a._overlay_root) - Number(b._overlay_root);
                  }
                  return Number(b._area || 0) - Number(a._area || 0);
                })
                .slice(0, 6);

              const getMeta = (el) => {
                try {
                  const rect = el.getBoundingClientRect();
                  return {
                    tag: (el.tagName || '').toLowerCase(),
                    role: (el.getAttribute && el.getAttribute('role')) || '',
                    aria_label: (el.getAttribute && el.getAttribute('aria-label')) || '',
                    title: (el.getAttribute && el.getAttribute('title')) || '',
                    data_testid: (el.getAttribute && el.getAttribute('data-testid')) || '',
                    class: (el.className && String(el.className)) || '',
                    text: ((el.innerText || '').trim().slice(0, 80)),
                    href: (el.tagName && el.tagName.toLowerCase() === 'a') ? (el.getAttribute('href') || '') : '',
                    has_svg: !!(el.querySelector && el.querySelector('svg')),
                    rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
                  };
                } catch (_e) {
                  return { tag: '', role: '', aria_label: '', title: '', data_testid: '', class: '', text: '', href: '', has_svg: false, rect: null };
                }
              };

              const scoreCloseCandidate = (meta, region) => {
                const reasons = [];
                const blob = (
                  String(meta.aria_label || '') + ' ' +
                  String(meta.title || '') + ' ' +
                  String(meta.text || '') + ' ' +
                  String(meta.class || '')
                ).toLowerCase();
                let score = 0.0;
                let iconOnlyFullPattern = false;
                if (/(^|\\b)(close|dismiss|닫기)(\\b|$)/i.test(blob)) { score += 0.70; reasons.push('kw:close'); }
                if (/(^|\\b)(취소)(\\b|$)/i.test(blob)) { score += 0.20; reasons.push('kw:cancel'); }
                if (/[×✕xX]/.test(String(meta.text || ''))) { score += 0.55; reasons.push('sym:x'); }
                if (/(btn-close|modal-close|icon-close|close-btn|closebutton)/i.test(blob)) { score += 0.35; reasons.push('cls:close'); }
                if (meta.tag === 'button' || meta.role === 'button') { score += 0.10; reasons.push('role:button'); }
                if (meta.tag === 'a' && meta.href) { score -= 0.25; reasons.push('penalty:link'); }

                if (meta.rect && region) {
                  const w = Number(meta.rect.width || 0);
                  const h = Number(meta.rect.height || 0);
                  const cx = Number(meta.rect.left || 0) + w / 2;
                  const cy = Number(meta.rect.top || 0) + h / 2;
                  if (w > 0 && h > 0 && w <= 80 && h <= 80) { score += 0.15; reasons.push('geom:small'); }
                  const nearTopRight = (
                    cx > (region.x + region.width * 0.72) &&
                    cy < (region.y + region.height * 0.28)
                  );
                  if (nearTopRight) { score += 0.25; reasons.push('geom:near_tr'); }
                  const btnRight = Number(meta.rect.left || 0) + w;
                  const btnTop = Number(meta.rect.top || 0);
                  const unlabeled = (
                    String(meta.text || '').trim() === '' &&
                    String(meta.aria_label || '').trim() === '' &&
                    String(meta.title || '').trim() === '' &&
                    String(meta.data_testid || '').trim() === ''
                  );
                  const strictNearTopRight = (
                    Math.abs((region.x + region.width) - btnRight) <= 32 &&
                    Math.abs(btnTop - region.y) <= 32
                  );
                  if (
                    unlabeled &&
                    !!meta.has_svg &&
                    (meta.tag === 'button' || meta.role === 'button') &&
                    w > 0 &&
                    h > 0 &&
                    w <= 56 &&
                    h <= 56 &&
                    strictNearTopRight
                  ) {
                    score += 0.45;
                    reasons.push('pattern:icon_only_full');
                    iconOnlyFullPattern = true;
                  }
                }
                score = Math.max(0.0, Math.min(1.0, score));
                return { score, reasons, iconOnlyFullPattern };
              };

              let best = null;
              const byRegion = (meta, region) => {
                if (!meta || !meta.rect || !region) return false;
                const w = Number(meta.rect.width || 0);
                const h = Number(meta.rect.height || 0);
                if (w <= 0 || h <= 0) return false;
                const cx = Number(meta.rect.left || 0) + w / 2;
                const cy = Number(meta.rect.top || 0) + h / 2;
                return (
                  cx >= region.x &&
                  cx <= region.x + region.width &&
                  cy >= region.y &&
                  cy <= region.y + region.height
                );
              };
              const scoreAndPick = (candidate) => {
                if (!candidate || !candidate.found) return;
                if (!best || Number(candidate.confidence || 0) > Number(best.confidence || 0)) {
                  best = candidate;
                }
              };

              for (let i = 0; i < rankedRegions.length; i++) {
                const region = rankedRegions[i];
                const allTargets = Array.from(document.querySelectorAll('button,[role="button"],[aria-label],[title],[tabindex]'))
                  .filter((el) => el instanceof HTMLElement)
                  .filter((el) => {
                    const style = window.getComputedStyle(el);
                    if (!style) return false;
                    if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') return false;
                    if (Number(style.opacity || '1') <= 0.02) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width >= 10 && rect.height >= 10;
                  });
                for (const target of allTargets) {
                  const targetMeta = getMeta(target);
                  if (!byRegion(targetMeta, region)) continue;
                  const scored = scoreCloseCandidate(targetMeta, region);
                  scoreAndPick({
                    found: true,
                    reason: 'modal_internal_candidate',
                    region_index: i,
                    x: Number((targetMeta.rect.left || 0) + (targetMeta.rect.width || 0) / 2),
                    y: Number((targetMeta.rect.top || 0) + (targetMeta.rect.height || 0) / 2),
                    confidence: scored.score,
                    confidence_reasons: scored.reasons,
                    icon_only_full_pattern: Boolean(scored.iconOnlyFullPattern),
                    target_meta: targetMeta,
                  });
                }

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
                  const targetMeta = getMeta(target);
                  const scored = scoreCloseCandidate(targetMeta, region);
                  const candidate = {
                    found: true,
                    reason: 'modal_corner_point',
                    region_index: i,
                    x,
                    y,
                    confidence: scored.score,
                    confidence_reasons: scored.reasons,
                    icon_only_full_pattern: Boolean(scored.iconOnlyFullPattern),
                    target_meta: targetMeta,
                  };
                  scoreAndPick(candidate);
                }
              }
              if (best) return best;
              return { found: false, reason: 'corner_point_miss' };
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
    if not isinstance(plan, dict) or not bool(plan.get("found")):
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.modal_corner",
                "reason_code": "not_found",
                "error": str((plan or {}).get("reason") or "modal corner candidate not found"),
            }
        )
        return {"success": False}

    try:
        confidence = float(plan.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    threshold = float(min_confidence)
    target_meta = plan.get("target_meta") if isinstance(plan.get("target_meta"), dict) else {}
    target_text = str(target_meta.get("text") or "").strip()
    if bool(plan.get("icon_only_full_pattern")):
        threshold = max(0.0, threshold - 0.15)
    elif target_text in {"×", "✕", "x", "X"}:
        threshold = max(0.0, threshold - 0.10)
    elif str(target_meta.get("aria_label") or "").strip().lower() in {"close", "닫기"}:
        threshold = max(0.0, threshold - 0.15)
    if confidence < threshold:
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.modal_corner",
                "reason_code": "not_found",
                "fallback": "modal_corner_click",
                "error": f"low_confidence_skip(conf={confidence:.2f}, threshold={threshold:.2f})",
                "meta": plan,
            }
        )
        return {"success": False}

    watch_ms = 1200
    settle_ms = 900
    try:
        watch_ms = int(str(os.getenv("GAIA_FALLBACK_WATCH_MS", "1200")).strip() or "1200")
        settle_ms = int(
            str(os.getenv("GAIA_FALLBACK_WATCH_SETTLE_MS", "900")).strip() or "900"
        )
    except Exception:
        watch_ms = 1200
        settle_ms = 900

    click_meta: Dict[str, Any] = {}

    async def _click() -> None:
        nonlocal click_meta
        click_meta = await trusted_click_point(
            page,
            float(plan.get("x") or 0.0),
            float(plan.get("y") or 0.0),
            delay_ms=50,
            move_first=True,
            clamp_to_viewport=True,
        )
        if not bool(click_meta.get("clicked")):
            raise RuntimeError(str(click_meta.get("error") or "playwright_mouse_click_failed"))

    post_watch = await watch_after_trusted_click(
        page,
        _click,
        watch_ms=watch_ms,
        settle_ms=settle_ms,
        wait_until="commit",
        watch_popup=True,
        watch_navigation=True,
        watch_dialog=True,
        auto_dismiss_dialog=True,
        auto_close_popup=False,
    )
    click_meta = {**plan, **click_meta}
    if not bool(click_meta.get("clicked")):
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.modal_corner",
                "reason_code": "not_actionable",
                "fallback": "modal_corner_click",
                "error": str(click_meta.get("error") or "playwright_mouse_click_failed"),
                "meta": click_meta,
                "post_watch": post_watch,
            }
        )
        return {"success": False}

    await page.wait_for_timeout(250)
    corner_change = await collect_state_change_probe_fn(
        probe_wait_ms=300,
        probe_scroll="modal_corner_fallback",
    )
    if bool(corner_change.get("effective", True)):
        corner_change["post_watch"] = post_watch
        if bool(post_watch.get("nav_detected")) or bool(post_watch.get("popup_detected")):
            corner_change["resnapshot_required"] = True
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": f"{mode}.modal_corner",
                "reason_code": "ok",
                "fallback": "modal_corner_click",
                "meta": click_meta,
                "post_watch": post_watch,
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
            "meta": click_meta,
            "post_watch": post_watch,
            "state_change": corner_change,
        }
    )
    return {"success": False}
