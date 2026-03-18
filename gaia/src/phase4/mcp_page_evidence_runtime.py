from __future__ import annotations

import json as json_module
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Page


def normalize_snapshot_text(value: Any) -> str:
    return str(value or "").strip().lower()


async def collect_page_evidence(page: Page) -> Dict[str, Any]:
    try:
        raw = await page.evaluate(
            """
            () => {
                const bodyText = ((document.body && document.body.innerText) || '')
                  .replace(/\\s+/g, ' ')
                  .trim();
                const clipped = bodyText.slice(0, 4000);
                const numberTokens = (clipped.match(/\\d+/g) || []).slice(0, 40);

                const liveNodes = Array.from(document.querySelectorAll(
                  '[role="status"],[aria-live],.toast,.alert,.snackbar,[class*="toast"],[class*="alert"],[class*="snackbar"],[class*="notification"]'
                )).slice(0, 20);
                const liveTexts = liveNodes
                  .map((el) => ((el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim()))
                  .filter(Boolean)
                  .map((t) => t.slice(0, 140));

                const counterNodes = Array.from(document.querySelectorAll(
                  '[aria-live], [role="status"], [class*="badge"], [class*="count"], [data-count], [data-badge]'
                )).slice(0, 60);
                const counters = counterNodes
                  .map((el) => (
                    (el.textContent || '').trim() ||
                    (el.getAttribute('data-count') || '').trim() ||
                    (el.getAttribute('data-badge') || '').trim()
                  ))
                  .filter(Boolean)
                  .map((t) => t.slice(0, 60));

                const listCount = document.querySelectorAll(
                  'li, tr, [role="row"], [role="listitem"], [class*="item"], [class*="row"], [class*="card"]'
                ).length;
                const interactiveCount = document.querySelectorAll(
                  'button, a, input, textarea, select, [role="button"], [role="tab"], [role="menuitem"], [role="link"]'
                ).length;

                const loginVisible = /(로그인|log in|sign in)/i.test(clipped);
                const logoutVisible = /(로그아웃|log out|sign out)/i.test(clipped);
                const viewportWidth = Number(window.innerWidth || document.documentElement.clientWidth || 0);
                const viewportHeight = Number(window.innerHeight || document.documentElement.clientHeight || 0);
                const modalNodes = Array.from(document.querySelectorAll(
                  'dialog, [role="dialog"], [role="alertdialog"], [aria-modal="true"], [class*="modal"], [class*="dialog"], [class*="sheet"], [class*="drawer"], [class*="popup"], [class*="overlay"], [class*="backdrop"]'
                ));
                const visibleModalNodes = modalNodes.filter((el) => {
                  if (!(el instanceof Element)) return false;
                  const style = window.getComputedStyle(el);
                  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0) {
                    return false;
                  }
                  if ((el.getAttribute('aria-hidden') || '').toLowerCase() === 'true') {
                    return false;
                  }
                  const rect = el.getBoundingClientRect();
                  if (rect.width < 24 || rect.height < 24) {
                    return false;
                  }
                  const tag = (el.tagName || '').toLowerCase();
                  const role = (el.getAttribute('role') || '').toLowerCase();
                  const ariaModal = (el.getAttribute('aria-modal') || '').toLowerCase();
                  const classes = String(el.getAttribute('class') || '').toLowerCase();
                  const hasHint = /(modal|dialog|sheet|drawer|popup|overlay|backdrop)/i.test(classes);
                  const zIndex = Number.parseInt(style.zIndex || '0', 10);
                  const layered = style.position === 'fixed' || style.position === 'sticky' || style.position === 'absolute' || Number.isFinite(zIndex) && zIndex >= 40;
                  const a11yDialog = tag === 'dialog' || role === 'dialog' || role === 'alertdialog' || ariaModal === 'true';
                  return a11yDialog || (hasHint && layered);
                });
                const centerAncestors = [];
                let centerNode = document.elementFromPoint(viewportWidth / 2, viewportHeight / 2);
                for (let depth = 0; centerNode instanceof Element && depth < 8; depth++) {
                  centerAncestors.push(centerNode);
                  centerNode = centerNode.parentElement;
                }
                const genericCenterLayers = centerAncestors.filter((el) => {
                  if (!(el instanceof Element)) return false;
                  const style = window.getComputedStyle(el);
                  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0) {
                    return false;
                  }
                  if (style.pointerEvents === 'none') return false;
                  const rect = el.getBoundingClientRect();
                  if (rect.width < 24 || rect.height < 24) return false;
                  const zIndex = Number.parseInt(style.zIndex || '0', 10);
                  const layered = style.position === 'fixed' || style.position === 'sticky' || (style.position === 'absolute' && Number.isFinite(zIndex) && zIndex >= 20);
                  if (!layered) return false;
                  const coversCenter =
                    rect.left <= viewportWidth / 2 &&
                    rect.right >= viewportWidth / 2 &&
                    rect.top <= viewportHeight / 2 &&
                    rect.bottom >= viewportHeight / 2;
                  if (!coversCenter) return false;
                  const fullScreenish = rect.width >= viewportWidth * 0.85 && rect.height >= viewportHeight * 0.5;
                  const largeEnough = rect.width >= Math.max(280, viewportWidth * 0.3) && rect.height >= Math.max(160, viewportHeight * 0.18);
                  const authHint = /(로그인|회원가입|sign in|log in|login|password|비밀번호)/i.test(String(el.textContent || '').slice(0, 260));
                  const hasInteractiveChild = Boolean(el.querySelector('input, textarea, select, button, [role="button"]'));
                  return Number.isFinite(zIndex) && zIndex >= 20 && (fullScreenish || (largeEnough && (authHint || hasInteractiveChild)));
                });
                const genericBackdropCount = genericCenterLayers.filter((el) => {
                  const rect = el.getBoundingClientRect();
                  return rect.width >= viewportWidth * 0.85 && rect.height >= viewportHeight * 0.5;
                }).length;
                const backdropCount = Array.from(document.querySelectorAll(
                  '.modal-backdrop, [class*="backdrop"], [class*="overlay"], [data-backdrop], [data-overlay]'
                )).filter((el) => {
                  if (!(el instanceof Element)) return false;
                  const style = window.getComputedStyle(el);
                  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0) {
                    return false;
                  }
                  const rect = el.getBoundingClientRect();
                  return rect.width >= 24 && rect.height >= 24;
                }).length;
                const dialogCount = Array.from(document.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]')).filter((el) => {
                  if (!(el instanceof Element)) return false;
                  const style = window.getComputedStyle(el);
                  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0) {
                    return false;
                  }
                  const rect = el.getBoundingClientRect();
                  return rect.width >= 24 && rect.height >= 24;
                }).length;
                const scrollY = Number(window.scrollY || 0);
                const docHeight = Number((document.documentElement && document.documentElement.scrollHeight) || 0);

                return {
                  text_digest: clipped.slice(0, 2000),
                  number_tokens: numberTokens,
                  live_texts: liveTexts,
                  counters: counters,
                  list_count: Number(listCount || 0),
                  interactive_count: Number(interactiveCount || 0),
                  login_visible: Boolean(loginVisible),
                  logout_visible: Boolean(logoutVisible),
                  modal_count: Number((visibleModalNodes.length || 0) + (genericCenterLayers.length || 0)),
                  backdrop_count: Number((backdropCount || 0) + (genericBackdropCount || 0)),
                  dialog_count: Number(dialogCount || 0),
                  modal_open: Boolean(visibleModalNodes.length > 0 || backdropCount > 0 || dialogCount > 0 || genericCenterLayers.length > 0),
                  scroll_y: scrollY,
                  doc_height: docHeight
                };
            }
            """
        )
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {
        "text_digest": "",
        "number_tokens": [],
        "live_texts": [],
        "counters": [],
        "list_count": 0,
        "interactive_count": 0,
        "login_visible": False,
        "logout_visible": False,
        "modal_count": 0,
        "backdrop_count": 0,
        "dialog_count": 0,
        "modal_open": False,
        "scroll_y": 0,
        "doc_height": 0,
    }


async def collect_page_evidence_light(page: Page) -> Dict[str, Any]:
    try:
        raw = await page.evaluate(
            """
            () => {
              const listCount = document.querySelectorAll(
                'li, tr, [role="row"], [role="listitem"], [class*="item"], [class*="row"], [class*="card"]'
              ).length;
              const interactiveCount = document.querySelectorAll(
                'button, a, input, textarea, select, [role="button"], [role="tab"], [role="menuitem"], [role="link"]'
              ).length;
              const bodyText = ((document.body && document.body.innerText) || '');
              const clipped = bodyText.replace(/\\s+/g, ' ').trim().slice(0, 800);
              const liveNodes = Array.from(document.querySelectorAll(
                '[role="status"],[aria-live],.toast,.alert,.snackbar,[class*="toast"],[class*="alert"],[class*="snackbar"],[class*="notification"]'
              )).slice(0, 8);
              const liveTexts = liveNodes
                .map((el) => ((el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim()))
                .filter(Boolean)
                .map((t) => t.slice(0, 100));
              const loginVisible = /(로그인|log in|sign in)/i.test(bodyText);
              const logoutVisible = /(로그아웃|log out|sign out)/i.test(bodyText);
              const viewportWidth = Number(window.innerWidth || document.documentElement.clientWidth || 0);
              const viewportHeight = Number(window.innerHeight || document.documentElement.clientHeight || 0);
              const modalCount = Array.from(document.querySelectorAll(
                'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"], [class*="modal"], [class*="dialog"], [class*="sheet"], [class*="drawer"], [class*="popup"], [class*="overlay"], [class*="backdrop"]'
              )).filter((el) => {
                if (!(el instanceof Element)) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0) {
                  return false;
                }
                const rect = el.getBoundingClientRect();
                return rect.width >= 24 && rect.height >= 24;
              }).length;
              const centerAncestors = [];
              let centerNode = document.elementFromPoint(viewportWidth / 2, viewportHeight / 2);
              for (let depth = 0; centerNode instanceof Element && depth < 8; depth++) {
                centerAncestors.push(centerNode);
                centerNode = centerNode.parentElement;
              }
              const genericCenterLayers = centerAncestors.filter((el) => {
                if (!(el instanceof Element)) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0) {
                  return false;
                }
                if (style.pointerEvents === 'none') return false;
                const rect = el.getBoundingClientRect();
                if (rect.width < 24 || rect.height < 24) return false;
                const zIndex = Number.parseInt(style.zIndex || '0', 10);
                const layered = style.position === 'fixed' || style.position === 'sticky' || (style.position === 'absolute' && Number.isFinite(zIndex) && zIndex >= 20);
                if (!layered) return false;
                const coversCenter =
                  rect.left <= viewportWidth / 2 &&
                  rect.right >= viewportWidth / 2 &&
                  rect.top <= viewportHeight / 2 &&
                  rect.bottom >= viewportHeight / 2;
                if (!coversCenter) return false;
                const fullScreenish = rect.width >= viewportWidth * 0.85 && rect.height >= viewportHeight * 0.5;
                const largeEnough = rect.width >= Math.max(280, viewportWidth * 0.3) && rect.height >= Math.max(160, viewportHeight * 0.18);
                const authHint = /(로그인|회원가입|sign in|log in|login|password|비밀번호)/i.test(String(el.textContent || '').slice(0, 260));
                const hasInteractiveChild = Boolean(el.querySelector('input, textarea, select, button, [role="button"]'));
                return Number.isFinite(zIndex) && zIndex >= 20 && (fullScreenish || (largeEnough && (authHint || hasInteractiveChild)));
              });
              const genericBackdropCount = genericCenterLayers.filter((el) => {
                const rect = el.getBoundingClientRect();
                return rect.width >= viewportWidth * 0.85 && rect.height >= viewportHeight * 0.5;
              }).length;
              const backdropCount = Array.from(document.querySelectorAll(
                '.modal-backdrop, [class*="backdrop"], [class*="overlay"], [data-backdrop], [data-overlay]'
              )).filter((el) => {
                if (!(el instanceof Element)) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0) {
                  return false;
                }
                const rect = el.getBoundingClientRect();
                return rect.width >= 24 && rect.height >= 24;
              }).length;
              const dialogCount = Array.from(document.querySelectorAll('dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]')).filter((el) => {
                if (!(el instanceof Element)) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0) {
                  return false;
                }
                const rect = el.getBoundingClientRect();
                return rect.width >= 24 && rect.height >= 24;
              }).length;
              const scrollY = Number(window.scrollY || 0);
              const docHeight = Number((document.documentElement && document.documentElement.scrollHeight) || 0);
              return {
                text_digest: clipped,
                number_tokens: [],
                live_texts: liveTexts,
                counters: [],
                list_count: Number(listCount || 0),
                interactive_count: Number(interactiveCount || 0),
                login_visible: Boolean(loginVisible),
                logout_visible: Boolean(logoutVisible),
                modal_count: Number((modalCount || 0) + (genericCenterLayers.length || 0)),
                backdrop_count: Number((backdropCount || 0) + (genericBackdropCount || 0)),
                dialog_count: Number(dialogCount || 0),
                modal_open: Boolean(modalCount > 0 || backdropCount > 0 || dialogCount > 0 || genericCenterLayers.length > 0),
                scroll_y: scrollY,
                doc_height: docHeight
              };
            }
            """
        )
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {
        "text_digest": "",
        "number_tokens": [],
        "live_texts": [],
        "counters": [],
        "list_count": 0,
        "interactive_count": 0,
        "login_visible": False,
        "logout_visible": False,
        "modal_count": 0,
        "backdrop_count": 0,
        "dialog_count": 0,
        "modal_open": False,
        "scroll_y": 0,
        "doc_height": 0,
    }


def sorted_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized = [str(v).strip() for v in value if str(v).strip()]
    normalized.sort()
    return normalized[:100]


def extract_live_texts(value: Any, limit: int = 8) -> List[str]:
    if not isinstance(value, list):
        return []
    dedup: List[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(text[:200])
        if len(dedup) >= max(1, int(limit)):
            break
    return dedup


async def read_focus_signature(page: Page) -> str:
    try:
        return await page.evaluate(
            """
            () => {
                const el = document.activeElement;
                if (!el) return '';
                const tag = el.tagName ? el.tagName.toLowerCase() : '';
                const id = el.id || '';
                const name = el.getAttribute('name') || '';
                const aria = el.getAttribute('aria-label') || '';
                return `${tag}|${id}|${name}|${aria}`;
            }
            """
        )
    except Exception:
        return ""


async def safe_read_target_state(locator) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "visible": None,
        "value": None,
        "focused": None,
        "checked": None,
        "aria_expanded": None,
        "aria_pressed": None,
        "aria_selected": None,
        "disabled": None,
        "aria_disabled": None,
    }
    try:
        state["visible"] = await locator.is_visible()
    except Exception:
        pass
    try:
        state["value"] = await locator.input_value(timeout=1000)
    except Exception:
        try:
            state["value"] = await locator.evaluate("el => (el.value !== undefined ? String(el.value) : null)")
        except Exception:
            pass
    try:
        state["focused"] = await locator.evaluate("el => document.activeElement === el")
    except Exception:
        pass
    try:
        semantic_state = await locator.evaluate(
            """
            el => {
                const readAria = (name) => {
                    const raw = el.getAttribute(name);
                    if (raw === null || raw === undefined) return null;
                    return String(raw).trim().toLowerCase();
                };
                const checked =
                    typeof el.checked === 'boolean'
                        ? !!el.checked
                        : null;
                const disabled =
                    typeof el.disabled === 'boolean'
                        ? !!el.disabled
                        : null;
                return {
                    checked,
                    aria_expanded: readAria('aria-expanded'),
                    aria_pressed: readAria('aria-pressed'),
                    aria_selected: readAria('aria-selected'),
                    disabled,
                    aria_disabled: readAria('aria-disabled'),
                };
            }
            """
        )
        if isinstance(semantic_state, dict):
            state.update(semantic_state)
    except Exception:
        pass
    return state


def build_ref_candidates(ref_meta: Dict[str, Any]) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    dom_ref = str(ref_meta.get("dom_ref") or "").strip()
    if dom_ref:
        candidates.append(("dom_ref", dom_ref))
    attrs = ref_meta.get("attributes") if isinstance(ref_meta.get("attributes"), dict) else {}
    role = str(ref_meta.get("role_ref_role") or attrs.get("role_ref_role") or "").strip()
    name = str(ref_meta.get("role_ref_name") or attrs.get("role_ref_name") or "").strip()
    if role and name:
        payload: Dict[str, Any] = {"role": role, "name": name}
        try:
            nth_value = ref_meta.get("role_ref_nth", attrs.get("role_ref_nth"))
            nth = int(nth_value)
            if nth >= 0:
                payload["nth"] = nth
        except Exception:
            pass
        candidates.append(("role_ref", f"role_ref:{json_module.dumps(payload, ensure_ascii=False)}"))

    dedup: List[Tuple[str, str]] = []
    seen = set()
    for mode, selector_value in candidates:
        key = (mode, selector_value)
        if key in seen:
            continue
        seen.add(key)
        dedup.append((mode, selector_value))
    return dedup


def resolve_ref_meta_from_snapshot(
    snapshot: Dict[str, Any],
    ref_id: str,
) -> Optional[Dict[str, Any]]:
    elements_by_ref = snapshot.get("elements_by_ref", {})
    if not isinstance(elements_by_ref, dict):
        return None
    ref_meta = elements_by_ref.get(ref_id)
    if isinstance(ref_meta, dict):
        return ref_meta
    return None


def resolve_stale_ref(
    old_meta: Optional[Dict[str, Any]],
    fresh_snapshot: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    fresh_map = fresh_snapshot.get("elements_by_ref", {})
    if not isinstance(fresh_map, dict) or not fresh_map:
        return None
    if old_meta is None:
        return None

    old_dom_ref = normalize_snapshot_text(old_meta.get("dom_ref"))
    if old_dom_ref:
        for meta in fresh_map.values():
            if not isinstance(meta, dict):
                continue
            if normalize_snapshot_text(meta.get("dom_ref")) == old_dom_ref:
                return meta

    old_full = normalize_snapshot_text(old_meta.get("full_selector"))
    old_selector = normalize_snapshot_text(old_meta.get("selector"))
    old_text = normalize_snapshot_text(old_meta.get("text"))
    old_tag = normalize_snapshot_text(old_meta.get("tag"))
    old_attrs = old_meta.get("attributes") if isinstance(old_meta.get("attributes"), dict) else {}
    old_role = normalize_snapshot_text(old_attrs.get("role"))
    old_role_ref_role = normalize_snapshot_text(old_meta.get("role_ref_role") or old_attrs.get("role_ref_role"))
    old_role_ref_name = normalize_snapshot_text(old_meta.get("role_ref_name") or old_attrs.get("role_ref_name"))
    old_container_name = normalize_snapshot_text(old_meta.get("container_name") or old_attrs.get("container_name"))
    try:
        old_role_ref_nth = int(old_meta.get("role_ref_nth", old_attrs.get("role_ref_nth", 0)) or 0)
    except Exception:
        old_role_ref_nth = 0
    old_scope = old_meta.get("scope") if isinstance(old_meta.get("scope"), dict) else {}
    old_frame_index = int(old_scope.get("frame_index", old_meta.get("frame_index", 0)) or 0)
    old_tab_index = int(old_scope.get("tab_index", old_meta.get("tab_index", 0)) or 0)
    old_bbox = old_meta.get("bounding_box") if isinstance(old_meta.get("bounding_box"), dict) else {}
    try:
        old_cx = float(old_bbox.get("center_x", (float(old_bbox.get("x", 0.0)) + float(old_bbox.get("width", 0.0)) / 2.0)))
        old_cy = float(old_bbox.get("center_y", (float(old_bbox.get("y", 0.0)) + float(old_bbox.get("height", 0.0)) / 2.0)))
    except Exception:
        old_cx = None
        old_cy = None

    context_snapshot = fresh_snapshot.get("context_snapshot", {})
    role_groups_by_container_ref = (
        context_snapshot.get("role_groups_by_container_ref")
        if isinstance(context_snapshot, dict) and isinstance(context_snapshot.get("role_groups_by_container_ref"), dict)
        else {}
    )

    def _matching_role_group_bonus(container_ref: str, candidate_role: str, candidate_name: str) -> int:
        if not container_ref or not candidate_role or not candidate_name:
            return 0
        groups = role_groups_by_container_ref.get(container_ref)
        if not isinstance(groups, list) or not groups:
            return 0
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_role = normalize_snapshot_text(group.get("role"))
            group_name = normalize_snapshot_text(group.get("name"))
            labels = " ".join(str(v) for v in (group.get("labels") or []) if v)
            labels_norm = normalize_snapshot_text(labels)
            if candidate_role != group_role:
                continue
            if candidate_name == group_name or candidate_name in labels_norm:
                if old_role_ref_role == candidate_role and old_role_ref_name == candidate_name:
                    return 1
        return 0

    best_score = -1
    best_meta: Optional[Dict[str, Any]] = None
    for meta in fresh_map.values():
        if not isinstance(meta, dict):
            continue
        score = 0
        meta_full = normalize_snapshot_text(meta.get("full_selector"))
        meta_selector = normalize_snapshot_text(meta.get("selector"))
        meta_text = normalize_snapshot_text(meta.get("text"))
        meta_tag = normalize_snapshot_text(meta.get("tag"))
        meta_attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
        meta_role = normalize_snapshot_text(meta_attrs.get("role"))
        meta_role_ref_role = normalize_snapshot_text(meta.get("role_ref_role") or meta_attrs.get("role_ref_role"))
        meta_role_ref_name = normalize_snapshot_text(meta.get("role_ref_name") or meta_attrs.get("role_ref_name"))
        meta_container_name = normalize_snapshot_text(meta.get("container_name") or meta_attrs.get("container_name"))
        meta_container_ref = normalize_snapshot_text(meta.get("container_ref_id") or meta_attrs.get("container_ref_id"))
        try:
            meta_role_ref_nth = int(meta.get("role_ref_nth", meta_attrs.get("role_ref_nth", 0)) or 0)
        except Exception:
            meta_role_ref_nth = 0
        meta_scope = meta.get("scope") if isinstance(meta.get("scope"), dict) else {}
        meta_frame_index = int(meta_scope.get("frame_index", meta.get("frame_index", 0)) or 0)
        meta_tab_index = int(meta_scope.get("tab_index", meta.get("tab_index", 0)) or 0)
        meta_bbox = meta.get("bounding_box") if isinstance(meta.get("bounding_box"), dict) else {}

        if old_full and old_full == meta_full:
            score += 8
        if old_selector and old_selector == meta_selector:
            score += 6
        if old_tag and old_tag == meta_tag:
            score += 2
        if old_text and old_text == meta_text:
            score += 3
        if old_role and old_role == meta_role:
            score += 2
        if old_role_ref_role and old_role_ref_role == meta_role_ref_role:
            score += 4
        if old_role_ref_name and old_role_ref_name == meta_role_ref_name:
            score += 5
        if (
            old_role_ref_role
            and old_role_ref_name
            and old_role_ref_role == meta_role_ref_role
            and old_role_ref_name == meta_role_ref_name
        ):
            if old_role_ref_nth == meta_role_ref_nth:
                score += 6
            else:
                score -= min(abs(meta_role_ref_nth - old_role_ref_nth), 3)
        if old_container_name and old_container_name == meta_container_name:
            score += 3
        score += _matching_role_group_bonus(meta_container_ref, meta_role_ref_role, meta_role_ref_name)
        if old_text and meta_text and old_text in meta_text:
            score += 1
        if old_frame_index == meta_frame_index:
            score += 4
        if old_tab_index == meta_tab_index:
            score += 2
        if old_cx is not None and old_cy is not None:
            try:
                meta_cx = float(meta_bbox.get("center_x", (float(meta_bbox.get("x", 0.0)) + float(meta_bbox.get("width", 0.0)) / 2.0)))
                meta_cy = float(meta_bbox.get("center_y", (float(meta_bbox.get("y", 0.0)) + float(meta_bbox.get("height", 0.0)) / 2.0)))
                dist = ((meta_cx - old_cx) ** 2) + ((meta_cy - old_cy) ** 2)
                if dist <= 400:
                    score += 5
                elif dist <= 2500:
                    score += 3
                elif dist <= 10000:
                    score += 1
            except Exception:
                pass
        if score > best_score:
            best_score = score
            best_meta = meta
        elif score == best_score and best_meta is not None:
            best_attrs = best_meta.get("attributes") if isinstance(best_meta.get("attributes"), dict) else {}
            try:
                best_nth = int(best_meta.get("role_ref_nth", best_attrs.get("role_ref_nth", 0)) or 0)
            except Exception:
                best_nth = 0
            if abs(meta_role_ref_nth - old_role_ref_nth) < abs(best_nth - old_role_ref_nth):
                best_meta = meta

    if best_score < 6:
        if old_role_ref_role and old_role_ref_name:
            role_matches: List[Dict[str, Any]] = []
            for meta in fresh_map.values():
                if not isinstance(meta, dict):
                    continue
                meta_attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
                meta_role_ref_role = normalize_snapshot_text(meta.get("role_ref_role") or meta_attrs.get("role_ref_role"))
                meta_role_ref_name = normalize_snapshot_text(meta.get("role_ref_name") or meta_attrs.get("role_ref_name"))
                if meta_role_ref_role != old_role_ref_role or meta_role_ref_name != old_role_ref_name:
                    continue
                role_matches.append(meta)
            if role_matches:
                role_matches.sort(
                    key=lambda item: int(
                        (
                            item.get("role_ref_nth")
                            or (
                                item.get("attributes").get("role_ref_nth")
                                if isinstance(item.get("attributes"), dict)
                                else 0
                            )
                            or 0
                        )
                    )
                )
                if 0 <= old_role_ref_nth < len(role_matches):
                    return role_matches[old_role_ref_nth]
                return role_matches[0]
        return None
    return best_meta


def state_change_flags(
    action: str,
    value: Any,
    before_url: str,
    after_url: str,
    before_dom_hash: str,
    after_dom_hash: str,
    before_evidence: Dict[str, Any],
    after_evidence: Dict[str, Any],
    before_target: Dict[str, Any],
    after_target: Dict[str, Any],
    before_focus: str,
    after_focus: str,
) -> Dict[str, bool]:
    before_value = before_target.get("value")
    after_value = after_target.get("value")
    expected_value = str(value) if value is not None else None

    flags: Dict[str, bool] = {
        "url_changed": before_url != after_url,
        "dom_changed": before_dom_hash != after_dom_hash,
        "target_visibility_changed": before_target.get("visible") != after_target.get("visible"),
        "target_value_changed": before_value != after_value,
        "target_value_matches": expected_value is not None and after_value is not None and str(after_value) == expected_value,
        "target_focus_changed": before_target.get("focused") != after_target.get("focused"),
        "focus_changed": before_focus != after_focus,
        "target_checked_changed": before_target.get("checked") != after_target.get("checked"),
        "target_aria_expanded_changed": before_target.get("aria_expanded") != after_target.get("aria_expanded"),
        "target_aria_pressed_changed": before_target.get("aria_pressed") != after_target.get("aria_pressed"),
        "target_aria_selected_changed": before_target.get("aria_selected") != after_target.get("aria_selected"),
        "target_disabled_changed": (
            before_target.get("disabled") != after_target.get("disabled")
            or before_target.get("aria_disabled") != after_target.get("aria_disabled")
        ),
        "counter_changed": sorted_text_list(before_evidence.get("counters")) != sorted_text_list(after_evidence.get("counters")),
        "number_tokens_changed": sorted_text_list(before_evidence.get("number_tokens")) != sorted_text_list(after_evidence.get("number_tokens")),
        "status_text_changed": sorted_text_list(before_evidence.get("live_texts")) != sorted_text_list(after_evidence.get("live_texts")),
        "list_count_changed": int(before_evidence.get("list_count", 0) or 0) != int(after_evidence.get("list_count", 0) or 0),
        "interactive_count_changed": int(before_evidence.get("interactive_count", 0) or 0) != int(after_evidence.get("interactive_count", 0) or 0),
        "modal_count_changed": int(before_evidence.get("modal_count", 0) or 0) != int(after_evidence.get("modal_count", 0) or 0),
        "backdrop_count_changed": int(before_evidence.get("backdrop_count", 0) or 0) != int(after_evidence.get("backdrop_count", 0) or 0),
        "dialog_count_changed": int(before_evidence.get("dialog_count", 0) or 0) != int(after_evidence.get("dialog_count", 0) or 0),
        "modal_state_changed": bool(before_evidence.get("modal_open")) != bool(after_evidence.get("modal_open")),
        "auth_state_changed": (
            bool(before_evidence.get("login_visible")) != bool(after_evidence.get("login_visible"))
            or bool(before_evidence.get("logout_visible")) != bool(after_evidence.get("logout_visible"))
        ),
        "text_digest_changed": str(before_evidence.get("text_digest", "")) != str(after_evidence.get("text_digest", "")),
    }
    flags["evidence_changed"] = bool(
        flags["counter_changed"]
        or flags["number_tokens_changed"]
        or flags["status_text_changed"]
        or flags["list_count_changed"]
        or flags["interactive_count_changed"]
        or flags["modal_count_changed"]
        or flags["backdrop_count_changed"]
        or flags["dialog_count_changed"]
        or flags["modal_state_changed"]
        or flags["auth_state_changed"]
        or flags["text_digest_changed"]
    )

    if action == "fill":
        flags["effective"] = bool(
            flags["target_value_changed"] or flags["target_value_matches"] or flags["evidence_changed"]
        )
    elif action == "click":
        flags["effective"] = bool(
            flags["url_changed"]
            or flags["dom_changed"]
            or flags["target_visibility_changed"]
            or flags["focus_changed"]
            or flags["target_focus_changed"]
            or flags["target_checked_changed"]
            or flags["target_aria_expanded_changed"]
            or flags["target_aria_pressed_changed"]
            or flags["target_aria_selected_changed"]
            or flags["target_disabled_changed"]
            or flags["evidence_changed"]
        )
    elif action == "press":
        flags["effective"] = bool(
            flags["url_changed"]
            or flags["dom_changed"]
            or flags["focus_changed"]
            or flags["target_focus_changed"]
            or flags["evidence_changed"]
        )
    elif action == "hover":
        flags["effective"] = bool(
            flags["target_visibility_changed"] or flags["focus_changed"] or flags["dom_changed"] or flags["evidence_changed"]
        )
    else:
        flags["effective"] = True

    return flags
