import asyncio
import os
import base64
import uuid
import time
import hashlib
import json as json_module
import traceback
import re
import weakref
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from playwright.async_api import (
    async_playwright,
    Playwright,
    Browser,
    Page,
    CDPSession,
)
from typing import Dict, Any, Optional, List, Tuple

from gaia.src.phase4.mcp_browser_session import BrowserSession, ensure_session
from gaia.src.phase4.openclaw_protocol import (
    ELEMENT_ACTIONS,
    build_error,
    is_element_action,
    legacy_selector_forbidden,
)
from gaia.src.phase4.mcp_legacy_dispatch import handle_legacy_action
from gaia.src.phase4.mcp_route_helpers import (
    build_root_payload,
    close_session_impl,
    websocket_screencast_loop,
)
from gaia.src.phase4.mcp_route_dispatch import dispatch_execute_action_route
from gaia.src.phase4.mcp_interaction_handlers import build_interaction_handlers
from gaia.src.phase4.scenario_runner import run_test_scenario_with_playwright
from gaia.src.phase4.state_store import BrowserStateStore
from gaia.src.phase4.mcp_bootstrap import resolve_bind_host_port
from gaia.src.phase4.mcp_tab_resolution import (
    coerce_tab_id as coerce_tab_id_impl,
    resolve_page_from_tab_identifier as _resolve_page_from_tab_identifier_impl,
    resolve_session_page as _resolve_session_page_impl,
)
from gaia.src.phase4.mcp_simple_action_utils import (
    normalize_timeout_ms as _normalize_timeout_ms,
    evaluate_js_with_timeout as _evaluate_js_with_timeout,
)
from gaia.src.phase4.mcp_ref_snapshot_helpers import (
    _build_role_refs_from_elements,
    _build_role_snapshot_from_ai_text,
    _build_role_snapshot_from_aria_text,
    _build_snapshot_text,
    _dedupe_elements_by_dom_ref,
    _element_signal_score,
    _extract_elements_by_ref,
)

logger = logging.getLogger("gaia.mcp_host")


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    """FastAPI lifespan handler for Playwright startup/shutdown."""
    global playwright_instance
    logger.info("Initializing Playwright...")
    playwright_instance = await async_playwright().start()
    logger.info("Playwright initialized.")
    try:
        yield
    finally:
        if playwright_instance:
            logger.info("Stopping Playwright...")
            await playwright_instance.stop()
            logger.info("Playwright stopped.")


app = FastAPI(
    title="MCP Host",
    description="Model Context Protocol Host for Browser Automation",
    lifespan=app_lifespan,
)

# 라이브 미리보기를 위한 전역 상태 (CDP 스크린캐스트용)
screencast_subscribers: List[WebSocket] = []
current_screencast_frame: Optional[str] = None
MCP_HOST_VERSION = os.getenv("GAIA_MCP_VERSION", "0.1.0")
MCP_STARTED_AT = time.time()
MCP_REQUEST_COUNT = 0
MCP_ERROR_COUNT = 0
MCP_REASON_CODE_COUNTER: Dict[str, int] = defaultdict(int)


def _get_playwright_instance() -> Optional[Playwright]:
    return playwright_instance


def _set_current_screencast_frame(frame_data: str) -> None:
    global current_screencast_frame
    current_screencast_frame = frame_data


def _record_reason_code(code: str) -> None:
    key = str(code or "").strip()
    if not key:
        return
    MCP_REASON_CODE_COUNTER[key] = int(MCP_REASON_CODE_COUNTER.get(key, 0)) + 1


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    global MCP_REQUEST_COUNT, MCP_ERROR_COUNT
    MCP_REQUEST_COUNT += 1
    try:
        response = await call_next(request)
    except Exception:
        MCP_ERROR_COUNT += 1
        _record_reason_code("http_5xx")
        raise

    if int(response.status_code) >= 400:
        MCP_ERROR_COUNT += 1

    if request.url.path == "/execute":
        reason_code = ""
        body = getattr(response, "body", None)
        if isinstance(body, (bytes, bytearray)) and body:
            try:
                payload = json_module.loads(body.decode("utf-8"))
            except Exception:
                payload = None
            if isinstance(payload, dict):
                reason_code = str(payload.get("reason_code") or "").strip()
                detail = payload.get("detail")
                if not reason_code and isinstance(detail, dict):
                    reason_code = str(detail.get("reason_code") or "").strip()
        if not reason_code:
            if 400 <= int(response.status_code) < 500:
                reason_code = "http_4xx"
            elif int(response.status_code) >= 500:
                reason_code = "http_5xx"
        if reason_code:
            _record_reason_code(reason_code)

    return response


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "uptime_sec": round(max(0.0, time.time() - MCP_STARTED_AT), 3),
        "active_sessions": len(active_sessions),
        "version": MCP_HOST_VERSION,
    }


@app.get("/metrics-lite")
async def metrics_lite() -> Dict[str, Any]:
    top = sorted(
        MCP_REASON_CODE_COUNTER.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:10]
    return {
        "status": "ok",
        "request_count": int(MCP_REQUEST_COUNT),
        "error_count": int(MCP_ERROR_COUNT),
        "reason_code_top": [
            {"reason_code": code, "count": int(count)}
            for code, count in top
        ],
        "active_sessions": len(active_sessions),
        "uptime_sec": round(max(0.0, time.time() - MCP_STARTED_AT), 3),
        "version": MCP_HOST_VERSION,
    }


# 활성 세션 저장소
active_sessions: Dict[str, BrowserSession] = {}
_page_target_id_cache: "weakref.WeakKeyDictionary[Page, str]" = weakref.WeakKeyDictionary()


def _build_snapshot_dom_hash(url: str, elements: List[Dict[str, Any]]) -> str:
    compact: List[Dict[str, Any]] = []
    for el in elements:
        attrs = el.get("attributes") or {}
        compact.append(
            {
                "tag": el.get("tag", ""),
                "text": (el.get("text") or "")[:80],
                "selector": el.get("selector", ""),
                "full_selector": el.get("full_selector", ""),
                "frame_index": el.get("frame_index", 0),
                "role": attrs.get("role", ""),
                "type": attrs.get("type", ""),
                "aria_label": attrs.get("aria-label", ""),
            }
        )
    raw = json_module.dumps(
        {
            "url": (url or "").strip(),
            "elements": compact,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_snapshot_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _get_tab_index(page: Page) -> int:
    try:
        return page.context.pages.index(page)
    except Exception:
        return 0


def _tab_payload(session: BrowserSession, page: Page, idx: int) -> Dict[str, Any]:
    active = bool(session.page is page)
    title = ""
    try:
        title = page.url or ""
    except Exception:
        title = ""
    return {
        "tab_id": idx,
        "index": idx,
        "targetId": idx,
        "url": str(page.url or ""),
        "title": str(title),
        "active": active,
    }


async def _get_page_target_id(page: Page) -> str:
    cached = _page_target_id_cache.get(page)
    if isinstance(cached, str) and cached.strip():
        return cached

    cdp_session: Optional[CDPSession] = None
    try:
        cdp_session = await page.context.new_cdp_session(page)
        info = await cdp_session.send("Target.getTargetInfo")
        target_info = info.get("targetInfo") if isinstance(info, dict) else {}
        target_id = str((target_info or {}).get("targetId") or "").strip()
        if target_id:
            _page_target_id_cache[page] = target_id
        return target_id
    except Exception:
        return ""
    finally:
        if cdp_session is not None:
            try:
                await cdp_session.detach()
            except Exception:
                pass


async def _list_browser_targets(browser: Optional[Browser]) -> List[Dict[str, str]]:
    if browser is None:
        return []
    browser_cdp: Optional[CDPSession] = None
    try:
        browser_cdp = await browser.new_browser_cdp_session()
        payload = await browser_cdp.send("Target.getTargets")
        infos = payload.get("targetInfos") if isinstance(payload, dict) else []
        out: List[Dict[str, str]] = []
        if isinstance(infos, list):
            for info in infos:
                if not isinstance(info, dict):
                    continue
                target_id = str(info.get("targetId") or "").strip()
                target_url = str(info.get("url") or "").strip()
                if target_id:
                    out.append({"targetId": target_id, "url": target_url})
        return out
    except Exception:
        return []
    finally:
        if browser_cdp is not None:
            try:
                await browser_cdp.detach()
            except Exception:
                pass


async def _resolve_page_from_tab_identifier(
    pages: List[Page],
    tab_identifier: Any,
    browser: Optional[Browser] = None,
) -> Tuple[str, Optional[int], Optional[Page], List[str]]:
    return await _resolve_page_from_tab_identifier_impl(
        pages=pages,
        tab_identifier=tab_identifier,
        browser=browser,
        get_page_target_id_fn=_get_page_target_id,
        list_browser_targets_fn=_list_browser_targets,
    )


async def _tab_payload_async(session: BrowserSession, page: Page, idx: int) -> Dict[str, Any]:
    payload = _tab_payload(session, page, idx)
    target_id = await _get_page_target_id(page)
    if target_id:
        payload["cdp_target_id"] = target_id
    return payload


async def _tabs_payload_async(session: BrowserSession, pages: List[Page]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, candidate in enumerate(pages):
        out.append(await _tab_payload_async(session, candidate, idx))
    return out


def _coerce_tab_id(tab_id: Any) -> Optional[int]:
    return coerce_tab_id_impl(tab_id)


async def _resolve_session_page(session_id: str, tab_id: Optional[Any] = None) -> Tuple[BrowserSession, Page]:
    return await _resolve_session_page_impl(
        session_id=session_id,
        tab_id=tab_id,
        active_sessions=active_sessions,
        ensure_session_fn=ensure_session,
        playwright_getter_fn=_get_playwright_instance,
        screencast_subscribers=screencast_subscribers,
        frame_setter=_set_current_screencast_frame,
        logger=logger,
        resolve_page_from_tab_identifier_fn=_resolve_page_from_tab_identifier,
    )


def _split_full_selector(full_selector: str) -> Tuple[str, str]:
    if " >>> " not in full_selector:
        return "", full_selector
    prefix, inner = full_selector.split(" >>> ", 1)
    return prefix.strip(), inner.strip()


async def _compute_runtime_dom_hash(page: Page) -> str:
    try:
        signature = await page.evaluate(
            """
            () => {
                const nodes = Array.from(document.querySelectorAll('input, textarea, select, button, a, [role="button"], [role="tab"], [role="dialog"], [aria-label], [type="submit"]'))
                    .slice(0, 220);
                const parts = nodes.map((el) => {
                    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 64);
                    const tag = el.tagName ? el.tagName.toLowerCase() : '';
                    const role = el.getAttribute('role') || '';
                    const type = el.getAttribute('type') || '';
                    const id = el.id || '';
                    return `${tag}|${role}|${type}|${id}|${text}`;
                });
                return parts.join('||');
            }
            """
        )
    except Exception:
        signature = str(page.url or "")
    return hashlib.sha256(str(signature).encode("utf-8")).hexdigest()


async def _collect_page_evidence(page: Page) -> Dict[str, Any]:
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


async def _collect_page_evidence_light(page: Page) -> Dict[str, Any]:
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


def _sorted_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized = [str(v).strip() for v in value if str(v).strip()]
    normalized.sort()
    return normalized[:100]


def _extract_live_texts(value: Any, limit: int = 8) -> List[str]:
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


async def _read_focus_signature(page: Page) -> str:
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


async def _safe_read_target_state(locator) -> Dict[str, Any]:
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


def _build_ref_candidates(ref_meta: Dict[str, Any]) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    dom_ref = str(ref_meta.get("dom_ref") or "").strip()
    if dom_ref:
        candidates.append(("dom_ref", dom_ref))

    dedup: List[Tuple[str, str]] = []
    seen = set()
    for mode, selector_value in candidates:
        key = (mode, selector_value)
        if key in seen:
            continue
        seen.add(key)
        dedup.append((mode, selector_value))
    return dedup


def _resolve_ref_meta_from_snapshot(
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


def _resolve_stale_ref(
    old_meta: Optional[Dict[str, Any]],
    fresh_snapshot: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    fresh_map = fresh_snapshot.get("elements_by_ref", {})
    if not isinstance(fresh_map, dict) or not fresh_map:
        return None
    if old_meta is None:
        return None

    old_dom_ref = _normalize_snapshot_text(old_meta.get("dom_ref"))
    if old_dom_ref:
        for meta in fresh_map.values():
            if not isinstance(meta, dict):
                continue
            if _normalize_snapshot_text(meta.get("dom_ref")) == old_dom_ref:
                return meta

    old_full = _normalize_snapshot_text(old_meta.get("full_selector"))
    old_selector = _normalize_snapshot_text(old_meta.get("selector"))
    old_text = _normalize_snapshot_text(old_meta.get("text"))
    old_tag = _normalize_snapshot_text(old_meta.get("tag"))
    old_role = _normalize_snapshot_text((old_meta.get("attributes") or {}).get("role"))
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

    best_score = -1
    best_meta: Optional[Dict[str, Any]] = None
    for meta in fresh_map.values():
        if not isinstance(meta, dict):
            continue
        score = 0
        meta_full = _normalize_snapshot_text(meta.get("full_selector"))
        meta_selector = _normalize_snapshot_text(meta.get("selector"))
        meta_text = _normalize_snapshot_text(meta.get("text"))
        meta_tag = _normalize_snapshot_text(meta.get("tag"))
        meta_role = _normalize_snapshot_text((meta.get("attributes") or {}).get("role"))
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

    if best_score < 6:
        return None
    return best_meta


def _state_change_flags(
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
        "counter_changed": _sorted_text_list(before_evidence.get("counters")) != _sorted_text_list(after_evidence.get("counters")),
        "number_tokens_changed": _sorted_text_list(before_evidence.get("number_tokens")) != _sorted_text_list(after_evidence.get("number_tokens")),
        "status_text_changed": _sorted_text_list(before_evidence.get("live_texts")) != _sorted_text_list(after_evidence.get("live_texts")),
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


def _apply_selector_strategy(elements: List[Dict[str, Any]], strategy: str) -> None:
    select_index = 0
    tag_indices: Dict[str, int] = {}

    for element in elements:
        tag = element.get("tag") or ""
        text = (element.get("text") or "").strip()
        attrs = element.get("attributes") or {}

        if tag == "select":
            element["selector"] = f"select >> nth={select_index}"
            select_index += 1
            continue

        if strategy == "role":
            role = attrs.get("role")
            aria_label = attrs.get("aria-label") or ""
            placeholder = attrs.get("placeholder") or ""
            safe_text = text.replace('"', "'") if text else ""
            safe_label = aria_label.replace('"', "'") if aria_label else ""
            safe_placeholder = placeholder.replace('"', "'") if placeholder else ""
            if role and safe_text:
                element["selector"] = f'role={role}[name="{safe_text}"]'
                continue
            if safe_label:
                element["selector"] = f'[aria-label="{safe_label}"]'
                continue
            if safe_placeholder and tag in {"input", "textarea"}:
                element["selector"] = f'{tag}[placeholder="{safe_placeholder}"]'
                continue

        if strategy == "nth":
            index = tag_indices.get(tag, 0)
            element["selector"] = f"{tag} >> nth={index}"
            tag_indices[tag] = index + 1
            continue

        if strategy == "text" and ":has-text" in (element.get("selector") or ""):
            safe_text = (
                text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
            )
            safe_text = safe_text.replace('"', "'") if safe_text else ""
            if safe_text:
                element["selector"] = f"text={safe_text}"


# --- URL 정규화 도우미 ---
def normalize_url(url: str) -> str:
    """
    일관된 비교를 위해 URL을 정규화합니다.
    해시 내비게이션과 끝에 붙는 슬래시 차이를 처리합니다.

    예시:
        "https://example.com/#hash" -> "https://example.com#hash"
        "https://example.com/" -> "https://example.com"
        "https://example.com/#basics" -> "https://example.com#basics"
    """
    if not url:
        return url
    # 일관된 비교를 위해 "/#"를 "#"로 바꿉니다
    normalized = url.replace("/#", "#")
    # 프로토콜 이후 문자 없이 슬래시만 있을 때를 제외하고 끝 슬래시를 제거합니다
    if normalized.endswith("/") and not normalized.endswith("://"):
        normalized = normalized.rstrip("/")
    return normalized


# --- Assertion Helper Functions ---
async def _execute_assertion(
    page: Page, action: str, selector: str, value: Any, before_screenshot: str = None
) -> Dict[str, Any]:
    """검증 작업을 수행하고 결과를 반환합니다 (하이브리드: DOM + Vision)"""
    try:
        if action == "expectVisible":
            # 요소가 보이는지 확인합니다
            if not selector and not value:
                return {
                    "success": False,
                    "message": "Selector or text value required for expectVisible",
                }

            # Phase 1: DOM 기반 검증 시도 (빠름 ~100ms)
            dom_success = False
            dom_error = None

            try:
                if selector:
                    # Case A: selector로 찾기
                    element = page.locator(selector).first
                    await element.wait_for(
                        state="visible", timeout=500
                    )  # 짧은 타임아웃
                    return {
                        "success": True,
                        "method": "dom_selector",
                        "message": f"Element {selector} is visible",
                    }
                else:
                    # Case B: 텍스트로 찾기
                    element = page.get_by_text(value, exact=False).first
                    await element.wait_for(
                        state="visible", timeout=500
                    )  # 짧은 타임아웃
                    return {
                        "success": True,
                        "method": "dom_text",
                        "message": f"Text '{value}' is visible",
                    }
            except Exception as e:
                dom_error = str(e)
                # DOM으로 못 찾음 → Vision으로 fallback

            # Phase 2: Vision AI Fallback (느림 ~2s, 하지만 더 정확)
            if before_screenshot:
                print(
                    f"⚠️ DOM check failed ({dom_error[:50]}...), trying Vision AI verification..."
                )

                # After 스크린샷 캡처
                after_screenshot_bytes = await page.screenshot(full_page=False)
                after_screenshot = base64.b64encode(after_screenshot_bytes).decode(
                    "utf-8"
                )

                # Vision AI로 검증 (LLMVisionClient 사용)
                try:
                    from gaia.src.phase4.llm_vision_client import LLMVisionClient

                    llm_client = LLMVisionClient()
                    vision_result = llm_client.verify_action_result(
                        expected_result=value or f"Element {selector} is visible",
                        before_screenshot=before_screenshot,
                        after_screenshot=after_screenshot,
                        url=str(page.url),
                    )

                    # Debug: Print Vision AI response
                    print(f"🔍 Vision AI Result:")
                    print(f"   - Success: {vision_result.get('success')}")
                    print(f"   - Confidence: {vision_result.get('confidence', 0)}")
                    print(f"   - Reasoning: {vision_result.get('reasoning', 'N/A')}")

                    if (
                        vision_result.get("success")
                        and vision_result.get("confidence", 0) > 70
                    ):
                        return {
                            "success": True,
                            "method": "vision_ai",
                            "confidence": vision_result["confidence"],
                            "reasoning": vision_result["reasoning"],
                            "message": f"Vision AI verified: {value}",
                        }
                    else:
                        return {
                            "success": False,
                            "method": "vision_ai_failed",
                            "confidence": vision_result.get("confidence", 0),
                            "reasoning": vision_result.get("reasoning", "Unknown"),
                            "dom_error": dom_error,
                            "message": f"Both DOM and Vision failed for '{value}'",
                        }
                except Exception as vision_error:
                    print(f"❌ Vision AI failed: {vision_error}")
                    return {
                        "success": False,
                        "method": "both_failed",
                        "dom_error": dom_error,
                        "vision_error": str(vision_error),
                        "message": f"Could not verify '{value}'",
                    }
            else:
                # before_screenshot 없으면 DOM 실패가 최종 실패
                return {
                    "success": False,
                    "method": "dom_only_failed",
                    "message": f"Element not found: {dom_error}",
                }

        elif action == "expectHidden":
            # 요소가 숨겨져 있는지 확인합니다
            if not selector:
                return {
                    "success": False,
                    "message": "Selector required for expectHidden",
                }
            element = page.locator(selector).first
            await element.wait_for(state="hidden", timeout=30000)
            return {"success": True, "message": f"Element {selector} is hidden"}

        elif action == "expectTrue":
            # 자바스크립트 표현식을 평가해 참인지 확인합니다
            if value is None:
                return {
                    "success": False,
                    "message": "Value (expression) required for expectTrue",
                }
            result = await page.evaluate(value)
            if result:
                return {
                    "success": True,
                    "message": f"Expression '{value}' evaluated to true",
                }
            else:
                return {
                    "success": False,
                    "message": f"Expression '{value}' evaluated to false",
                }

        elif action == "expectText":
            # 요소의 텍스트 내용을 확인합니다
            if not selector or value is None:
                return {
                    "success": False,
                    "message": "Selector and expected text value required for expectText",
                }

            try:
                element = page.locator(selector).first
                text_content = await element.text_content(timeout=5000)

                # Check if expected text is in the element's text content
                if value in (text_content or ""):
                    return {
                        "success": True,
                        "message": f"Found text '{value}' in element {selector}",
                    }
                else:
                    return {
                        "success": False,
                        "message": f"Expected '{value}', found '{text_content}' in {selector}",
                    }
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Element {selector} not found or timeout: {str(e)}",
                }

        elif action == "expectAttribute":
            # 요소 속성 값을 확인합니다
            if not selector or value is None:
                return {
                    "success": False,
                    "message": "Selector and value [attr, expected] required",
                }
            element = page.locator(selector).first
            if isinstance(value, list) and len(value) >= 2:
                attr_name, expected_value = value[0], value[1]
            else:
                return {
                    "success": False,
                    "message": "Value must be [attribute_name, expected_value]",
                }

            actual_value = await element.get_attribute(attr_name)
            if actual_value == expected_value:
                return {
                    "success": True,
                    "message": f"Attribute {attr_name}={expected_value}",
                }
            else:
                return {
                    "success": False,
                    "message": f"Attribute {attr_name}={actual_value}, expected {expected_value}",
                }

        elif action == "expectCountAtLeast":
            # 최소 요소 개수를 확인합니다
            if not selector or value is None:
                return {
                    "success": False,
                    "message": "Selector and value (min count) required",
                }
            elements = page.locator(selector)
            count = await elements.count()
            min_count = int(value) if not isinstance(value, int) else value
            if count >= min_count:
                return {
                    "success": True,
                    "message": f"Found {count} elements (>= {min_count})",
                }
            else:
                return {
                    "success": False,
                    "message": f"Found {count} elements (< {min_count})",
                }

        else:
            return {"success": False, "message": f"Unknown assertion action: {action}"}

    except Exception as e:
        return {"success": False, "message": f"Assertion failed: {str(e)}"}


# --- Data Models for Test Scenarios ---
class TestStep(BaseModel):
    description: str
    action: str
    selector: str
    params: List[Any] = []
    auto_analyze: bool = False  # DOM 재분석 여부 (네비게이션 후)


class Assertion(BaseModel):
    description: str
    selector: str
    condition: str
    params: List[Any] = []


class NetworkAssertion(BaseModel):
    """네트워크 요청/응답 검증"""

    description: str
    method: str  # GET, POST 등
    url_pattern: str  # 정규식 또는 부분 문자열
    expected_status: int = 200
    response_contains: Optional[Dict[str, Any]] = None  # JSON 응답 검증


class UIAssertion(BaseModel):
    """UI 상태 검증"""

    description: str
    assertion_type: str  # 토스트, 모달, element_count 등
    selector: Optional[str] = None
    expected_text: Optional[str] = None
    expected_count: Optional[int] = None


class TestScenario(BaseModel):
    id: str
    priority: str
    scenario: str
    steps: List[TestStep]
    assertion: Assertion


class McpRequest(BaseModel):
    action: str = Field(
        ...,
        description="The action to perform, e.g., 'analyze_page' or 'execute_scenario'.",
    )
    params: Dict[str, Any] = Field(
        default_factory=dict, description="Parameters for the action."
    )


# 전역 Playwright 인스턴스
playwright_instance: Optional[Playwright] = None


async def analyze_page_elements(page) -> Dict[str, Any]:
    """현재 페이지에서 상호작용 가능한 요소를 추출합니다 (iframe 포함)."""
    try:
        try:
            await page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            await page.wait_for_timeout(2000)

        # 모든 프레임(메인 + iframe)에서 요소 수집
        all_elements = []
        frames = page.frames

        print(f"Analyzing {len(frames)} frames (main + iframes)...")

        for frame_index, frame in enumerate(frames):
            try:
                # 각 프레임에서 요소 수집
                frame_elements = await frame.evaluate("""
            () => {
                const elements = [];
                let gaiaRefSeq = 0;

                const scanRoots = (() => {
                    const roots = [document];
                    const seen = new Set([document]);
                    const queue = [document];
                    while (queue.length > 0) {
                        const root = queue.shift();
                        let nodes = [];
                        try {
                            nodes = Array.from(root.querySelectorAll('*'));
                        } catch (_) {
                            nodes = [];
                        }
                        for (const node of nodes) {
                            if (!node || !node.shadowRoot) continue;
                            if (seen.has(node.shadowRoot)) continue;
                            seen.add(node.shadowRoot);
                            roots.push(node.shadowRoot);
                            queue.push(node.shadowRoot);
                        }
                    }
                    return roots;
                })();

                function queryAll(selector) {
                    const out = [];
                    const seen = new Set();
                    for (const root of scanRoots) {
                        let found = [];
                        try {
                            found = Array.from(root.querySelectorAll(selector));
                        } catch (_) {
                            continue;
                        }
                        for (const el of found) {
                            if (!el || seen.has(el)) continue;
                            seen.add(el);
                            out.push(el);
                        }
                    }
                    return out;
                }

                function getActionability(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const displayVisible = style.display !== 'none' && style.visibility !== 'hidden';
                    const opacity = Number(style.opacity || '1');
                    const pointerEvents = (style.pointerEvents || '').toLowerCase();
                    const hasRect = rect.width > 1 && rect.height > 1;
                    const onViewport =
                        rect.bottom >= -2 &&
                        rect.right >= -2 &&
                        rect.top <= (window.innerHeight + 2) &&
                        rect.left <= (window.innerWidth + 2);
                    const disabled =
                        el.disabled === true ||
                        String(el.getAttribute('disabled') || '').toLowerCase() === 'true' ||
                        String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                    // OpenClaw-aligned split:
                    // - collect visibility: allow offscreen candidates (no viewport gating)
                    // - execution-time actionability: handled at action phase (scroll/reveal/probe)
                    const collectVisible = displayVisible && opacity > 0.02 && pointerEvents !== 'none' && hasRect;
                    const visible = collectVisible;
                    return {
                        visible,
                        actionable: collectVisible && !disabled,
                        disabled,
                        opacity,
                        onViewport,
                        pointerEvents: style.pointerEvents || '',
                    };
                }

                function isVisible(el) {
                    return getActionability(el).visible;
                }

                function assignDomRef(el) {
                    const existing = (el.getAttribute('data-gaia-dom-ref') || '').trim();
                    if (existing) {
                        return existing;
                    }
                    const tag = (el.tagName || 'el').toLowerCase();
                    const ref = `gaia-${tag}-${Date.now().toString(36)}-${gaiaRefSeq++}`;
                    try {
                        el.setAttribute('data-gaia-dom-ref', ref);
                    } catch (_) {}
                    return ref;
                }

                function getUniqueSelector(el) {
                    if (el.id) {
                        if (window.CSS && typeof CSS.escape === 'function') {
                            return `#${CSS.escape(el.id)}`;
                        }
                        return `${el.tagName.toLowerCase()}[id="${el.id}"]`;
                    }

                    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;

                    if (el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;

                    if (el.getAttribute('aria-label')) {
                        return `${el.tagName.toLowerCase()}[aria-label="${el.getAttribute('aria-label')}"]`;
                    }

                    // 입력 요소는 텍스트나 클래스로 넘어가기 전에 placeholder를 확인
                    if (el.tagName === 'INPUT' && el.placeholder) {
                        return `${el.tagName.toLowerCase()}[placeholder="${el.placeholder}"]`;
                    }

                    const text = el.innerText?.trim();
                    if (text && text.length < 50) {
                        return `${el.tagName.toLowerCase()}:has-text("${text}")`;
                    }

                    if (el.className && typeof el.className === 'string') {
                        const classes = el.className.split(' ').filter(c =>
                            c &&
                            !c.match(/^(active|hover|focus|selected)/) &&
                            !c.match(/^(sc-|css-|makeStyles-|emotion-)/)
                        );
                        if (classes.length > 0) {
                            return `${el.tagName.toLowerCase()}.${classes.slice(0, 2).join('.')}`;
                        }
                    }

                    const parent = el.parentElement;
                    if (parent) {
                        const siblings = Array.from(parent.children);
                        const index = siblings.indexOf(el) + 1;
                        return `${el.tagName.toLowerCase()}:nth-child(${index})`;
                    }

                    return el.tagName.toLowerCase();
                }

                function getBoundingBox(el) {
                    const rect = el.getBoundingClientRect();
                    return {
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height,
                        center_x: rect.x + rect.width / 2,
                        center_y: rect.y + rect.height / 2
                    };
                }

                queryAll('input, textarea, select').forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;

                    const entry = {
                        tag: el.tagName.toLowerCase(),
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: '',
                        attributes: {
                            type: el.type || 'text',
                            id: el.id || null,
                            name: el.name || null,
                            placeholder: el.placeholder || '',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'input',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    };

                    // select 요소의 option 목록 수집 (최대 20개)
                    if (el.tagName.toLowerCase() === 'select') {
                        const opts = [];
                        const optEls = el.querySelectorAll('option');
                        const limit = Math.min(optEls.length, 20);
                        for (let i = 0; i < limit; i++) {
                            const o = optEls[i];
                            opts.push({ value: o.value, text: (o.textContent || '').trim() });
                        }
                        if (optEls.length > 20) {
                            opts.push({ value: '__truncated__', text: '...' + (optEls.length - 20) + ' more' });
                        }
                        entry.attributes['options'] = opts;
                        // 현재 선택된 값도 기록
                        entry.attributes['selected_value'] = el.value || '';
                    }

                    elements.push(entry);
                });

                // 버튼과 상호작용 가능한 역할 요소를 수집
                // 상호작용 UI에서 자주 사용하는 ARIA 역할
                queryAll(`
                    button,
                    a:not([href]),
                    [role="button"],
                    [role="tab"],
                    [role="menuitem"],
                    [role="menuitemcheckbox"],
                    [role="menuitemradio"],
                    [role="option"],
                    [role="radio"],
                    [role="switch"],
                    [role="treeitem"],
                    [role="link"],
                    [type="submit"],
                    input[type="button"]
                `.replace(/\s+/g, '')).forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;

                    let text = el.innerText?.trim() || el.value || '';
                    if (!text) {
                        text = el.getAttribute('aria-label') || el.getAttribute('title') || '';
                    }
                    if (!text) {
                        const svg = el.querySelector('svg');
                        if (svg) {
                            text = svg.getAttribute('aria-label') || svg.getAttribute('title') || '[icon]';
                        }
                    }

                    // For switches/toggles, try to find nearby label text
                    if (el.getAttribute('role') === 'switch' && (!text || text === 'on' || text === 'off')) {
                        // Look for label in parent container
                        const parent = el.parentElement;
                        if (parent) {
                            const parentContainer = parent.parentElement;
                            if (parentContainer) {
                                const label = parentContainer.querySelector('label');
                                if (label && label.innerText) {
                                    text = label.innerText.trim();
                                }
                            }
                        }
                    }

                    elements.push({
                        tag: el.tagName.toLowerCase(),
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            type: el.type || 'button',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            role: el.getAttribute('role') || '',
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'button',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                });

                // 페이지네이션/네비게이션 시그널 수집 (아이콘형 next/prev 포함)
                queryAll('button, a, [role="button"], [role="link"]').forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;

                    const rawText = (el.innerText || el.textContent || '').trim();
                    const ariaLabel = (el.getAttribute('aria-label') || '').trim();
                    const title = (el.getAttribute('title') || '').trim();
                    const cls = (el.className && typeof el.className === 'string') ? el.className : '';
                    const dataPage = (el.getAttribute('data-page') || '').trim();
                    const ariaCurrent = (el.getAttribute('aria-current') || '').trim();
                    const role = (el.getAttribute('role') || '').trim();
                    const blob = `${rawText} ${ariaLabel} ${title} ${cls} ${dataPage}`.toLowerCase();
                    const hasPaginationSignal =
                        /(pagination|pager|page-|page_|\\bpage\\b|next|prev|previous|다음|이전|chevron|arrow)/.test(blob)
                        || !!ariaCurrent
                        || /^[<>‹›«»→←]+$/.test(rawText);
                    if (!hasPaginationSignal) return;

                    const text = rawText || ariaLabel || title || dataPage || '[page-nav]';
                    elements.push({
                        tag: el.tagName.toLowerCase(),
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            role: role,
                            class: cls || '',
                            'aria-label': ariaLabel,
                            title: title,
                            'aria-current': ariaCurrent,
                            'data-page': dataPage,
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'pagination',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                });

                queryAll('[onclick], [class*="btn"], [class*="button"], [class*="cursor-pointer"]').forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;
                    if (el.tagName === 'BUTTON') return;
                    if (el.tagName === 'A' && el.hasAttribute('href')) return;

                    const style = window.getComputedStyle(el);
                    if (style.cursor === 'pointer' || el.onclick) {
                        const text = el.innerText?.trim() || '';
                        if (text && text.length < 100) {
                            elements.push({
                                tag: el.tagName.toLowerCase(),
                                dom_ref: assignDomRef(el),
                                selector: getUniqueSelector(el),
                                text: text,
                                attributes: {
                            class: el.className,
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'clickable',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                        }
                    }
                });

                queryAll('a[href]').forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;

                    const href = el.href;
                    let text = el.innerText?.trim() || '';

                    if (!text) {
                        const img = el.querySelector('img');
                        text = (img && img.getAttribute('alt')) ||
                            el.getAttribute('aria-label') ||
                            el.getAttribute('title') ||
                            '[link]';
                    }

                    elements.push({
                        tag: 'a',
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            href: href,
                            target: el.target || '',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'link',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                });

                // 시맨틱/구조 신호 수집 (OpenClaw 스타일 보강)
                queryAll(`
                    [aria-controls],
                    [aria-expanded],
                    [aria-haspopup],
                    [tabindex]:not([tabindex="-1"]),
                    [data-testid],
                    [data-test],
                    [data-qa],
                    [contenteditable="true"],
                    summary,
                    details > summary,
                    tr,
                    td,
                    li,
                    article,
                    [role="row"],
                    [role="cell"],
                    [role="gridcell"],
                    [role="listitem"],
                    [class*="row"],
                    [class*="item"],
                    [class*="card"],
                    [class*="list"]
                `.replace(/\s+/g, '')).forEach(el => {
                    const actionability = getActionability(el);
                    if (!actionability.visible) return;
                    if (!el || !el.tagName) return;

                    const tag = el.tagName.toLowerCase();
                    if (['html', 'body', 'head', 'meta', 'style', 'script', 'link'].includes(tag)) return;

                    const role = (el.getAttribute('role') || '').trim().toLowerCase();
                    const ariaLabel = (el.getAttribute('aria-label') || '').trim();
                    const title = (el.getAttribute('title') || '').trim();
                    const text = (el.innerText || '').trim();
                    const testid =
                        (el.getAttribute('data-testid') || '').trim() ||
                        (el.getAttribute('data-test') || '').trim() ||
                        (el.getAttribute('data-qa') || '').trim();
                    const style = window.getComputedStyle(el);
                    const pointerLike = style.cursor === 'pointer';
                    const roleValue = (role || '').toLowerCase();
                    const classBlob = (el.className && typeof el.className === 'string') ? el.className.toLowerCase() : '';
                    const rowLike =
                        roleValue === 'row' ||
                        roleValue === 'cell' ||
                        roleValue === 'gridcell' ||
                        roleValue === 'listitem' ||
                        ['tr', 'td', 'li', 'article'].includes(tag) ||
                        /(?:^|\\s)(row|item|card|list)(?:-|_|\\s|$)/.test(classBlob);
                    const hasClickableChild = !!el.querySelector('a,button,[role="button"],[role="link"],[onclick]');
                    const textualCandidate = !!text && text.length >= 2 && text.length <= 320;
                    const box = getBoundingBox(el);

                    // 너무 의미 없는 wrapper 노드는 제외
                    const hasSignal =
                        !!role ||
                        !!ariaLabel ||
                        !!title ||
                        !!testid ||
                        pointerLike ||
                        (text && text.length <= 180) ||
                        (rowLike && (pointerLike || hasClickableChild || textualCandidate));
                    if (!hasSignal) return;
                    if (box.width <= 0 || box.height <= 0) return;

                    elements.push({
                        tag: tag,
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text ? text.slice(0, 260) : '',
                        attributes: {
                            role: role,
                            'aria-label': ariaLabel,
                            'aria-modal': el.getAttribute('aria-modal') || '',
                            title: title,
                            class: el.className || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            'aria-controls': el.getAttribute('aria-controls') || '',
                            'aria-expanded': el.getAttribute('aria-expanded') || '',
                            'aria-haspopup': el.getAttribute('aria-haspopup') || '',
                            tabindex: el.getAttribute('tabindex') || '',
                            'data-testid': testid,
                            'gaia-visible-strict': actionability.visible ? 'true' : 'false',
                            'gaia-actionable': actionability.actionable ? 'true' : 'false',
                            'gaia-disabled': actionability.disabled ? 'true' : 'false',
                            'gaia-on-viewport': actionability.onViewport ? 'true' : 'false',
                            'gaia-pointer-events': actionability.pointerEvents || '',
                            'gaia-opacity': String(actionability.opacity),
                        },
                        bounding_box: box,
                        element_type: 'semantic',
                        actionable: actionability.actionable,
                        visible_strict: actionability.visible,
                    });
                });

                return elements;
            }
        """)

                # None 체크
                if frame_elements is None:
                    frame_elements = []

                selector_strategy = os.environ.get("MCP_SELECTOR_STRATEGY", "text")
                _apply_selector_strategy(frame_elements, selector_strategy)

                # 프레임 정보 추가
                frame_name = frame.name or f"frame_{frame_index}"
                is_main_frame = frame == page.main_frame

                print(
                    f"  Frame {frame_index} ({frame_name}): {len(frame_elements)} elements"
                )

                # 각 요소에 프레임 정보 추가
                for elem in frame_elements:
                    elem["frame_index"] = frame_index
                    elem["frame_name"] = frame_name
                    elem["is_main_frame"] = is_main_frame

                    # iframe 내부 요소는 selector에 frame 정보 추가
                    if not is_main_frame:
                        # iframe selector 생성 (name 또는 index 사용)
                        if frame.name:
                            frame_selector = f'iframe[name="{frame.name}"]'
                        else:
                            frame_selector = f"iframe:nth-of-type({frame_index})"
                        elem["frame_selector"] = frame_selector
                        # 전체 selector는 "frame_selector >>> element_selector" 형식
                        elem["full_selector"] = (
                            f"{frame_selector} >>> {elem['selector']}"
                        )
                    else:
                        elem["full_selector"] = elem["selector"]

                all_elements.extend(frame_elements)

            except Exception as frame_error:
                import traceback

                print(
                    f"  Error analyzing frame {frame_index} ({frame.name or 'unnamed'}): {frame_error}"
                )
                print(f"  Traceback: {traceback.format_exc()}")
                continue

        # 중복 제거 후 시그널 점수 기반으로 상위 요소 유지 (밀도는 높이고 노이즈는 억제)
        all_elements = _dedupe_elements_by_dom_ref(all_elements)
        try:
            max_elements = int(os.getenv("GAIA_DOM_MAX_ELEMENTS", "2200"))
        except Exception:
            max_elements = 2200
        max_elements = max(200, min(max_elements, 8000))
        if len(all_elements) > max_elements:
            all_elements = sorted(
                all_elements,
                key=_element_signal_score,
                reverse=True,
            )[:max_elements]

        print(f"Total found {len(all_elements)} interactive/semantic elements across all frames")
        # 디버깅용으로 처음 10개 요소를 출력합니다
        if len(all_elements) <= 10:
            element_strs = [
                f"{e.get('tag', '')}:{e.get('text', '')[:20]}" for e in all_elements
            ]
            print(f"  Elements: {element_strs}")
        return {"elements": all_elements}

    except Exception as e:
        current_url = getattr(page, "url", "unknown")
        print(f"Error analyzing page {current_url}: {e}")
        return {"error": str(e)}


async def snapshot_page(url: str = None, session_id: str = "default") -> Dict[str, Any]:
    """페이지 스냅샷 생성 (snapshot_id/dom_hash/ref 포함)."""
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    # 세션을 가져오거나 생성합니다
    session = ensure_session(
        active_sessions=active_sessions,
        session_id=session_id,
        playwright_getter=_get_playwright_instance,
        screencast_subscribers=screencast_subscribers,
        frame_setter=_set_current_screencast_frame,
        logger=logger,
    )
    page = await session.get_or_create_page()

    def _is_retryable_page_detach_error(exc: BaseException) -> bool:
        message = str(exc or "").strip().lower()
        if not message:
            return False
        return (
            "frame has been detached" in message
            or "target page, context or browser has been closed" in message
        )

    async def _goto_with_retry(target_page: Any, target_url: str, *, timeout: int) -> None:
        try:
            await target_page.goto(target_url, timeout=timeout)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            await target_page.goto(target_url, timeout=timeout)

    async def _screenshot_with_retry(target_page: Any, **kwargs: Any) -> bytes:
        try:
            return await target_page.screenshot(**kwargs)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            return await target_page.screenshot(**kwargs)

    async def _title_with_retry(target_page: Any) -> str:
        try:
            return await target_page.title()
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            return await target_page.title()

    # URL이 주어지고 현재 브라우저 URL과 다를 때에만 이동합니다
    if url:
        current_browser_url = page.url
        current_normalized = normalize_url(current_browser_url)
        requested_normalized = normalize_url(url)

        print(
            f"[analyze_page] Current browser URL: {current_browser_url} (normalized: {current_normalized})"
        )
        print(
            f"[analyze_page] Requested URL: {url} (normalized: {requested_normalized})"
        )

        if current_normalized != requested_normalized:
            print(f"[analyze_page] URLs differ, navigating to: {url}")
            await _goto_with_retry(page, url, timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            # 이동 후 React/Figma SPA가 하이드레이션되도록 대기합니다
            await page.wait_for_timeout(3000)

        # session.current_url을 실제 브라우저 URL과 항상 동기화합니다
        session.current_url = page.url
        print(f"[analyze_page] Synced session.current_url to: {session.current_url}")

    # 요소를 수집하고 현재 URL을 응답에 추가합니다
    result = await analyze_page_elements(page)
    should_retry_snapshot = False
    if isinstance(result, dict):
        err_text = str(result.get("error") or "").strip().lower()
        if (
            "frame has been detached" in err_text
            or "target page, context or browser has been closed" in err_text
        ):
            should_retry_snapshot = True
    try:
        if not should_retry_snapshot and bool(page.is_closed()):
            should_retry_snapshot = True
    except Exception:
        if not should_retry_snapshot:
            should_retry_snapshot = True
    if should_retry_snapshot:
        page = await session.get_or_create_page()
        result = await analyze_page_elements(page)
    elements = result.get("elements", []) if isinstance(result, dict) else []
    if isinstance(elements, list):
        elements = _dedupe_elements_by_dom_ref(elements)
    tab_index = _get_tab_index(page)
    session.snapshot_epoch += 1
    epoch = session.snapshot_epoch
    dom_hash = _build_snapshot_dom_hash(page.url, elements)
    snapshot_id = f"{session.session_id}:{epoch}:{dom_hash[:12]}"
    captured_at = int(time.time() * 1000)

    for idx, elem in enumerate(elements):
        frame_index = int(elem.get("frame_index", 0) or 0)
        ref_id = f"t{tab_index}-f{frame_index}-e{idx}"
        elem["ref_id"] = ref_id
        elem["scope"] = {
            "tab_index": tab_index,
            "frame_index": frame_index,
            "is_main_frame": bool(elem.get("is_main_frame", True)),
        }

    elements_by_ref: Dict[str, Dict[str, Any]] = {
        elem["ref_id"]: elem for elem in elements if isinstance(elem, dict) and elem.get("ref_id")
    }
    snapshot_record = {
        "snapshot_id": snapshot_id,
        "session_id": session_id,
        "url": page.url,
        "tab_index": tab_index,
        "dom_hash": dom_hash,
        "epoch": epoch,
        "captured_at": captured_at,
        "elements_by_ref": elements_by_ref,
    }
    session.snapshots[snapshot_id] = snapshot_record
    session.current_snapshot_id = snapshot_id
    session.current_dom_hash = dom_hash

    # 오래된 스냅샷 정리
    if len(session.snapshots) > 20:
        oldest = sorted(
            session.snapshots.items(),
            key=lambda item: int((item[1] or {}).get("epoch", 0)),
        )
        for old_snapshot_id, _ in oldest[: len(session.snapshots) - 20]:
            session.snapshots.pop(old_snapshot_id, None)

    result["url"] = page.url
    result["snapshot_id"] = snapshot_id
    result["dom_hash"] = dom_hash
    result["epoch"] = epoch
    result["tab_index"] = tab_index
    result["captured_at"] = captured_at
    result["dom_elements"] = elements
    try:
        result["evidence"] = await _collect_page_evidence(page)
    except Exception:
        result["evidence"] = {}
    return result


async def analyze_page(url: str = None, session_id: str = "default") -> Dict[str, Any]:
    """지속 세션을 사용해 페이지 요소를 분석합니다."""
    return await snapshot_page(url=url, session_id=session_id)


async def capture_screenshot(
    url: str = None, session_id: str = "default"
) -> Dict[str, Any]:
    """지속 세션을 사용해 스크린샷을 캡처합니다."""
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    # 세션을 가져오거나 생성합니다
    session = ensure_session(
        active_sessions=active_sessions,
        session_id=session_id,
        playwright_getter=_get_playwright_instance,
        screencast_subscribers=screencast_subscribers,
        frame_setter=_set_current_screencast_frame,
        logger=logger,
    )
    page = await session.get_or_create_page()

    def _is_retryable_page_detach_error(exc: BaseException) -> bool:
        message = str(exc or "").strip().lower()
        if not message:
            return False
        return (
            "frame has been detached" in message
            or "target page, context or browser has been closed" in message
        )

    async def _goto_with_retry(target_page: Any, target_url: str, *, timeout: int) -> None:
        try:
            await target_page.goto(target_url, timeout=timeout)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            await target_page.goto(target_url, timeout=timeout)

    async def _screenshot_with_retry(target_page: Any, **kwargs: Any) -> bytes:
        try:
            return await target_page.screenshot(**kwargs)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            return await target_page.screenshot(**kwargs)

    async def _title_with_retry(target_page: Any) -> str:
        try:
            return await target_page.title()
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            return await target_page.title()

    # URL이 주어지고 현재 브라우저 URL과 다를 때에만 이동합니다
    if url:
        current_browser_url = page.url
        current_normalized = normalize_url(current_browser_url)
        requested_normalized = normalize_url(url)

        if current_normalized != requested_normalized:
            await _goto_with_retry(page, url, timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                await page.wait_for_timeout(2000)

        # session.current_url을 실제 브라우저 URL과 항상 동기화합니다
        session.current_url = page.url

    # 현재 페이지(위치와 관계없이)를 캡처합니다
    screenshot_bytes = await _screenshot_with_retry(page, full_page=False)
    screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    return {
        "screenshot": screenshot_base64,
        "url": page.url,
        "title": await _title_with_retry(page),
    }


async def _reset_session_connection(session: BrowserSession, reason: str = "") -> None:
    try:
        if session.cdp_session is not None:
            try:
                await session.cdp_session.detach()
            except Exception:
                pass
    finally:
        session.cdp_session = None

    if session.browser is not None:
        try:
            await session.browser.close()
        except Exception:
            pass

    session.browser = None
    session.page = None
    session.current_url = ""
    session.screencast_active = False
    session.dialog_listener_armed = False
    session.file_chooser_listener_armed = False
    session.current_snapshot_id = ""
    session.current_dom_hash = ""
    session.snapshots = {}
    if reason:
        print(f"[session-reset] {session.session_id}: {reason}")


async def execute_simple_action(
    url: str,
    selector: str,
    action: str,
    value: str = None,
    session_id: str = "default",
    before_screenshot: str = None,
    action_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from gaia.src.phase4.mcp_simple_action_executor import execute_simple_action_impl

    return await execute_simple_action_impl(
        url=url,
        selector=selector,
        action=action,
        value=value,
        session_id=session_id,
        before_screenshot=before_screenshot,
        action_options=action_options,
        playwright_instance=playwright_instance,
        ensure_session=ensure_session,
        active_sessions=active_sessions,
        _get_playwright_instance=_get_playwright_instance,
        screencast_subscribers=screencast_subscribers,
        _set_current_screencast_frame=_set_current_screencast_frame,
        logger=logger,
        is_element_action=is_element_action,
        legacy_selector_forbidden=legacy_selector_forbidden,
        normalize_url=normalize_url,
        _scroll_locator_container=_scroll_locator_container,
        _normalize_timeout_ms=_normalize_timeout_ms,
        _evaluate_js_with_timeout=_evaluate_js_with_timeout,
        _reset_session_connection=_reset_session_connection,
        _execute_assertion=_execute_assertion,
        _reveal_locator_in_scroll_context=_reveal_locator_in_scroll_context,
    )


def _select_frame_for_ref(page: Page, ref_meta: Dict[str, Any]):
    scope = ref_meta.get("scope", {}) if isinstance(ref_meta.get("scope"), dict) else {}
    frame_index = int(scope.get("frame_index", ref_meta.get("frame_index", 0)) or 0)
    try:
        frames = page.frames
        if 0 <= frame_index < len(frames):
            return frames[frame_index], frame_index
    except Exception:
        pass
    return page.main_frame, 0


async def _resolve_locator_from_ref(page: Page, ref_meta: Dict[str, Any], _selector_hint: str):
    frame, frame_index = _select_frame_for_ref(page, ref_meta)
    dom_ref = str(ref_meta.get("dom_ref") or "").strip()

    if not dom_ref:
        return None, frame_index, "", "dom_ref_missing"

    try:
        selector_to_use = f'[data-gaia-dom-ref="{dom_ref}"]'
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
            return (
                locator_group.nth(best_idx),
                frame_index,
                f'{selector_to_use} [nth={best_idx}]',
                "",
            )
        return None, frame_index, selector_to_use, f"ambiguous_selector_matches:{match_count}"
    except Exception as exc:
        selector_to_use = f'[data-gaia-dom-ref="{dom_ref}"]'
        return None, frame_index, selector_to_use, str(exc)


def _parse_scroll_payload(value: Any) -> Dict[str, Any]:
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


async def _reveal_locator_in_scroll_context(locator) -> Dict[str, Any]:
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


async def _scroll_locator_container(locator, value: Any) -> Dict[str, Any]:
    payload = _parse_scroll_payload(value)
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


def _validate_upload_path(path: str) -> str:
    """업로드 파일 경로를 검증합니다. Path traversal 방지."""
    import os as _os

    resolved = _os.path.realpath(path)
    upload_dir = _os.getenv("GAIA_UPLOAD_DIR", "")
    if upload_dir:
        allowed = _os.path.realpath(upload_dir)
        if not resolved.startswith(allowed + _os.sep) and resolved != allowed:
            raise ValueError(f"File path not allowed (outside GAIA_UPLOAD_DIR): {path}")
    if not _os.path.isfile(resolved):
        raise ValueError(f"File not found: {path}")
    return resolved


async def _execute_action_on_locator(
    action: str,
    page: Page,
    locator,
    value: Any,
    options: Optional[Dict[str, Any]] = None,
):
    opts = dict(options or {})

    def _normalize_timeout(raw: Any, default_ms: int) -> int:
        return _normalize_timeout_ms(raw if raw is not None else default_ms, default_ms)

    if action == "click":
        await _reveal_locator_in_scroll_context(locator)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 8000)
        button = str(opts.get("button") or "left").strip().lower()
        if button not in {"left", "right", "middle"}:
            button = "left"
        modifiers_raw = opts.get("modifiers")
        modifiers: Optional[List[str]] = None
        if isinstance(modifiers_raw, list):
            allowed_mods = {"Alt", "Control", "Meta", "Shift"}
            normalized_mods = [str(m).strip() for m in modifiers_raw if str(m).strip() in allowed_mods]
            if normalized_mods:
                modifiers = normalized_mods
        double_click = bool(opts.get("doubleClick") or opts.get("double_click"))
        click_kwargs: Dict[str, Any] = {
            "button": button,
            "timeout": timeout_ms,
            "no_wait_after": True,
        }
        if modifiers:
            click_kwargs["modifiers"] = modifiers
        if double_click:
            await locator.dblclick(**click_kwargs)
        else:
            await locator.click(**click_kwargs)
        return
    if action == "fill":
        if value is None:
            raise ValueError("fill requires value")
        await _reveal_locator_in_scroll_context(locator)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 10000)
        slowly = bool(opts.get("slowly") or opts.get("sequentialKeystrokes"))
        if slowly:
            # React/Vue 등 keystroke 이벤트가 필요한 프레임워크용
            # locator.fill()은 value 속성을 직접 설정하므로 onChange 미발화
            # locator.type()은 개별 키스트로크를 발생시켜 이벤트 핸들러 동작
            await locator.clear(timeout=timeout_ms)
            delay_ms = int(opts.get("delay", 75))
            delay_ms = max(10, min(300, delay_ms))
            await locator.type(str(value), delay=delay_ms, timeout=timeout_ms)
        else:
            await locator.fill(str(value), timeout=timeout_ms)
        return
    if action == "press":
        key = str(value or "Enter")
        await _reveal_locator_in_scroll_context(locator)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 8000)
        await locator.press(key, timeout=timeout_ms, no_wait_after=True)
        return
    if action == "hover":
        await _reveal_locator_in_scroll_context(locator)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 10000)
        await locator.hover(timeout=timeout_ms)
        return
    if action == "setChecked":
        # checkbox/radio 전용: Playwright setChecked()는 이미 해당 상태인 경우 skip
        await _reveal_locator_in_scroll_context(locator)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 8000)
        _FALSY_VALUES = {False, "false", "0", 0, None, ""}
        checked = value not in _FALSY_VALUES
        await locator.set_checked(checked, timeout=timeout_ms)
        return
    if action == "scroll":
        await _scroll_locator_container(locator, value)
        return
    if action == "scrollIntoView":
        await _reveal_locator_in_scroll_context(locator)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 10000)
        await locator.scroll_into_view_if_needed(timeout=timeout_ms)
        return
    if action == "select":
        if value is None:
            raise ValueError("select requires value")
        await _reveal_locator_in_scroll_context(locator)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 10000)
        if isinstance(value, dict):
            payload = dict(value)
            if "index" in payload:
                payload["index"] = int(payload["index"])
            await locator.select_option(**payload, timeout=timeout_ms)
        elif isinstance(value, list):
            normalized_values = [str(item).strip() for item in value if str(item).strip()]
            if not normalized_values:
                raise ValueError("select requires at least one value")
            await locator.select_option(value=normalized_values, timeout=timeout_ms)
        else:
            await locator.select_option(value=str(value), timeout=timeout_ms)
        return
    if action == "dragAndDrop":
        if value is None:
            raise ValueError("dragAndDrop requires target_selector value")
        target_selector = str(value.get("target_selector") if isinstance(value, dict) else value)
        if not target_selector:
            raise ValueError("dragAndDrop requires non-empty target_selector")
        target = page.locator(target_selector).first
        await _reveal_locator_in_scroll_context(locator)
        await _reveal_locator_in_scroll_context(target)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 10000)
        await locator.drag_to(target, timeout=timeout_ms)
        return
    if action == "dragSlider":
        if value is None:
            raise ValueError("dragSlider requires numeric value")
        try:
            float(value)
        except (TypeError, ValueError):
            raise ValueError(f"dragSlider requires numeric value, got: {value!r}")
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 10000)
        ok = await locator.evaluate(
            """
            (el, payload) => {
              const { targetValue, timeoutMs } = payload;
              return new Promise((resolve, reject) => {
                const timer = setTimeout(
                  () => reject(new Error("dragSlider timed out after " + timeoutMs + "ms")),
                  timeoutMs
                );
                try {
                  const num = Number(targetValue);
                  if (Number.isNaN(num)) { clearTimeout(timer); resolve(false); return; }
                  if (el.value === undefined) { clearTimeout(timer); resolve(false); return; }
                  el.focus();
                  el.value = String(num);
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                  clearTimeout(timer);
                  resolve(true);
                } catch (e) {
                  clearTimeout(timer);
                  reject(e);
                }
              });
            }
            """,
            {"targetValue": value, "timeoutMs": timeout_ms},
        )
        if not ok:
            raise ValueError("dragSlider target is not an input-like element")
        return
    if action == "uploadFile":
        if value is None:
            raise ValueError("uploadFile requires file path value")
        await _reveal_locator_in_scroll_context(locator)
        timeout_ms = _normalize_timeout(opts.get("timeoutMs", opts.get("timeout_ms")), 30000)
        raw_paths = value if isinstance(value, list) else [str(value)]
        file_paths = [_validate_upload_path(p) for p in raw_paths]
        await locator.set_input_files(file_paths, timeout=timeout_ms)
        # setInputFiles 후 input/change 이벤트 수동 dispatch
        # React/Vue 등 프레임워크 호환성 보장
        await locator.dispatch_event("input", {"bubbles": True})
        await locator.dispatch_event("change", {"bubbles": True})
        return
    raise ValueError(f"Unsupported ref action: {action}")


async def _try_click_container_ancestor(page: Page, locator) -> Dict[str, Any]:
    try:
        payload = await locator.evaluate(
            """
            (el) => {
              const candidates = [
                '[role="row"]',
                'tr',
                'li',
                '[role="listitem"]',
                '[data-row]',
                '[data-item]',
                '[class*="row"]',
                '[class*="item"]',
                '[class*="card"]'
              ];
              const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
              const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;

              const isVisible = (node) => {
                if (!(node instanceof HTMLElement)) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (Number(style.opacity || '1') <= 0.02) return false;
                if (style.pointerEvents === 'none') return false;
                const rect = node.getBoundingClientRect();
                if (rect.width < 24 || rect.height < 20) return false;
                if (rect.right < 1 || rect.bottom < 1) return false;
                if (rect.left > viewportW - 1 || rect.top > viewportH - 1) return false;
                return true;
              };

              let current = el instanceof Element ? el : null;
              for (let depth = 0; current && depth < 8; depth++) {
                for (const selector of candidates) {
                  const node = current.matches(selector) ? current : null;
                  if (!node || !(node instanceof HTMLElement) || node === el) continue;
                  if (!isVisible(node)) continue;

                  node.scrollIntoView({ block: 'center', inline: 'nearest' });
                  const rect = node.getBoundingClientRect();
                  const clickX = Math.max(1, Math.min(viewportW - 1, Math.round(rect.left + rect.width / 2)));
                  const clickY = Math.max(1, Math.min(viewportH - 1, Math.round(rect.top + rect.height / 2)));

                  return {
                    clicked: true,
                    selector,
                    reason: 'ancestor_container_click',
                    clickX,
                    clickY,
                    tag: (node.tagName || '').toLowerCase(),
                  };
                }
                current = current.parentElement;
              }
              return { clicked: false, selector: '' };
            }
            """
        )
        if not isinstance(payload, dict):
            return {"clicked": False, "selector": "", "error": "invalid_payload"}
        if not bool(payload.get("clicked")):
            return payload

        try:
            click_x = float(payload.get("clickX") or 0.0)
            click_y = float(payload.get("clickY") or 0.0)
        except Exception:
            return {"clicked": False, "selector": "", "error": "invalid_click_point"}

        await page.mouse.click(click_x, click_y, delay=50)
        payload["input"] = "playwright_mouse"
        return payload
    except Exception as exc:
        return {"clicked": False, "selector": "", "error": str(exc)}


async def _try_click_hit_target_from_point(
    page: Page,
    locator,
    ref_meta: Optional[Dict[str, Any]] = None,
    *,
    close_like_click: bool = False,
) -> Dict[str, Any]:
    point_x: Optional[float] = None
    point_y: Optional[float] = None
    try:
        box = await locator.bounding_box()
        if isinstance(box, dict):
            width = float(box.get("width", 0.0) or 0.0)
            height = float(box.get("height", 0.0) or 0.0)
            if width > 0.0 and height > 0.0:
                point_x = float(box.get("x", 0.0) or 0.0) + width / 2.0
                point_y = float(box.get("y", 0.0) or 0.0) + height / 2.0
    except Exception:
        point_x = None
        point_y = None

    if point_x is None or point_y is None:
        bbox = ref_meta.get("bounding_box") if isinstance(ref_meta, dict) and isinstance(ref_meta.get("bounding_box"), dict) else {}
        try:
            x = float(bbox.get("x", 0.0) or 0.0)
            y = float(bbox.get("y", 0.0) or 0.0)
            width = float(bbox.get("width", 0.0) or 0.0)
            height = float(bbox.get("height", 0.0) or 0.0)
            if width > 0.0 and height > 0.0:
                point_x = float(bbox.get("center_x", x + width / 2.0) or (x + width / 2.0))
                point_y = float(bbox.get("center_y", y + height / 2.0) or (y + height / 2.0))
        except Exception:
            point_x = None
            point_y = None

    if point_x is None or point_y is None:
        return {"clicked": False, "selector": "", "error": "point_not_available"}

    try:
        min_confidence = float(
            str(os.getenv("GAIA_HIT_TARGET_MIN_CONFIDENCE", "0.35")).strip()
        )
    except Exception:
        min_confidence = 0.35
    min_confidence = max(0.0, min(1.0, float(min_confidence)))
    allow_external_nav = str(
        os.getenv("GAIA_HIT_TARGET_ALLOW_EXTERNAL_NAV", "0")
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    require_close_hint = str(
        os.getenv("GAIA_CLOSE_HINT_REQUIRED_FOR_HIT_TARGET", "1")
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    auto_close_popup_on_close = str(
        os.getenv("GAIA_CLOSE_FALLBACK_AUTOCLOSE_POPUP", "1")
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    try:
        watch_ms = int(str(os.getenv("GAIA_FALLBACK_WATCH_MS", "1200")).strip() or "1200")
    except Exception:
        watch_ms = 1200
    try:
        settle_ms = int(
            str(os.getenv("GAIA_FALLBACK_WATCH_SETTLE_MS", "900")).strip() or "900"
        )
    except Exception:
        settle_ms = 900

    try:
        payload = await page.evaluate(
            """
            ({ pointX, pointY, allowExternalNav, closeLikeClick, requireCloseHint, minConfidence }) => {
              const clickableSelectors = [
                'button',
                'a[href]',
                '[role="button"]',
                '[role="link"]',
                '[onclick]',
                'input[type="button"]',
                'input[type="submit"]',
                '[tabindex]:not([tabindex="-1"])'
              ];

              const isVisible = (node) => {
                if (!(node instanceof HTMLElement)) return false;
                const style = window.getComputedStyle(node);
                if (!style) return false;
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (Number(style.opacity || '1') <= 0) return false;
                if (style.pointerEvents === 'none') return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 2 && rect.height > 2;
              };
              const norm = (v) => String(v || '').toLowerCase();

              const hasCloseHint = (node) => {
                if (!(node instanceof HTMLElement)) return false;
                const txt = norm(node.innerText || node.textContent || '').trim();
                const aria = norm(node.getAttribute('aria-label'));
                const title = norm(node.getAttribute('title'));
                const testid = norm(node.getAttribute('data-testid'));
                const id = norm(node.id);
                const cls = norm(node.className);
                const pool = [txt, aria, title, testid, id, cls].filter(Boolean).join(' ');
                if (!pool) return false;
                const hints = ['close', 'dismiss', 'cancel', 'exit', '닫기', '취소'];
                if (hints.some((h) => pool.includes(h))) return true;
                if (txt === 'x' || txt === '×' || txt === '✕') return true;
                return false;
              };

              const pickClickable = (startNode) => {
                let current = startNode instanceof Element ? startNode : null;
                for (let depth = 0; current && depth < 10; depth++) {
                  if (current instanceof HTMLElement && isVisible(current)) {
                    if (clickableSelectors.some((selector) => current.matches(selector))) {
                      return current;
                    }
                  }
                  current = current.parentElement;
                }
                return null;
              };

              const buildMeta = (node) => {
                if (!(node instanceof HTMLElement)) return null;
                const rect = node.getBoundingClientRect();
                let href = '';
                let target = '';
                if (node.tagName && node.tagName.toLowerCase() === 'a') {
                  href = node.getAttribute('href') || '';
                  target = node.getAttribute('target') || '';
                }
                return {
                  tag: (node.tagName || '').toLowerCase(),
                  role: node.getAttribute('role') || '',
                  aria_label: node.getAttribute('aria-label') || '',
                  title: node.getAttribute('title') || '',
                  class: node.className ? String(node.className) : '',
                  text: (node.innerText || '').trim().slice(0, 80),
                  href,
                  target,
                  rect: {
                    left: rect.left,
                    top: rect.top,
                    width: rect.width,
                    height: rect.height,
                    right: rect.right,
                    bottom: rect.bottom,
                  },
                };
              };

              const scoreMeta = (meta) => {
                const reasons = [];
                const risks = [];
                let score = 0.10;
                if (!meta) return { score: 0.0, reasons: ['no_meta'], risks };

                if (meta.tag === 'button' || meta.role === 'button') { score += 0.35; reasons.push('button'); }
                if (meta.tag === 'input') { score += 0.20; reasons.push('input'); }
                if (meta.tag === 'a' && meta.href) {
                  reasons.push('link');
                  const href = String(meta.href || '').trim();
                  if (/^(javascript:|#)/i.test(href)) {
                    score += 0.10;
                    reasons.push('link:safe_href');
                  } else if (/^(mailto:|tel:)/i.test(href)) {
                    score -= 0.30;
                    risks.push('link:mailto_tel');
                  } else {
                    try {
                      const url = new URL(href, window.location.href);
                      if (url.origin !== window.location.origin) {
                        risks.push('link:external');
                        score -= allowExternalNav ? 0.10 : 0.45;
                        reasons.push(allowExternalNav ? 'external_allowed' : 'external_blocked');
                      } else {
                        score += 0.10;
                        reasons.push('same_origin');
                      }
                    } catch (_) {
                      score -= 0.10;
                      reasons.push('bad_url');
                    }
                  }
                  if ((meta.target || '').toLowerCase() === '_blank') {
                    score -= 0.10;
                    risks.push('link:new_tab');
                  }
                }

                const label = (String(meta.aria_label || '') + ' ' + String(meta.title || '') + ' ' + String(meta.text || '')).toLowerCase();
                if (label.trim().length > 0) score += 0.05;

                const vw = window.innerWidth || document.documentElement.clientWidth || 0;
                const vh = window.innerHeight || document.documentElement.clientHeight || 0;
                const w = Number(meta.rect && meta.rect.width) || 0;
                const h = Number(meta.rect && meta.rect.height) || 0;
                if (w > 0 && h > 0) {
                  if (w <= 90 && h <= 90) score += 0.10;
                  if (vw > 0 && vh > 0 && (w >= vw * 0.92 || h >= vh * 0.92)) score -= 0.20;
                }
                score = Math.max(0.0, Math.min(1.0, score));
                return { score, reasons, risks };
              };

              let rootNode = document.elementFromPoint(pointX, pointY);
              if (!rootNode) {
                return {
                  clicked: false,
                  selector: '',
                  reason: 'elementFromPoint_null',
                  clickX: pointX,
                  clickY: pointY
                };
              }

              // page.mouse.click는 뷰포트 좌표 기준이므로 iframe 내부여도 전역 좌표 클릭이 동작합니다.
              // 따라서 iframe 내부 DOM 직접 접근/dispatch 대신 전역 좌표를 반환합니다.
              if (rootNode instanceof HTMLIFrameElement) {
                const confidence = closeLikeClick ? 0.15 : 0.55;
                if (closeLikeClick && requireCloseHint) {
                  return {
                    clicked: false,
                    selector: 'iframe',
                    reason: 'close_hint_missing',
                    confidence,
                    confidence_reasons: ['iframe', 'close_hint_missing'],
                    close_hint: false,
                    risk_flags: ['iframe_point', 'close_hint_missing'],
                    clickX: pointX,
                    clickY: pointY
                  };
                }
                return {
                  clicked: confidence >= minConfidence,
                  selector: 'iframe',
                  reason: 'iframe_point_click',
                  confidence,
                  confidence_reasons: ['iframe'],
                  risk_flags: [],
                  close_hint: false,
                  clickX: pointX,
                  clickY: pointY
                };
              }

              const picked = pickClickable(rootNode);
              const target = (picked && picked instanceof HTMLElement)
                ? picked
                : (rootNode instanceof HTMLElement ? rootNode : null);
              if (!target) {
                return {
                  clicked: false,
                  selector: '',
                  reason: 'raw_point_click',
                  confidence: 0.0,
                  confidence_reasons: ['no_target'],
                  risk_flags: ['no_target'],
                  clickX: pointX,
                  clickY: pointY
                };
              }
              target.scrollIntoView({ block: 'center', inline: 'nearest' });
              const meta = buildMeta(target);
              const scored = scoreMeta(meta);
              const closeHint = hasCloseHint(target);
              const rect = meta && meta.rect ? meta.rect : null;
              const clickX = rect ? (rect.left + rect.width / 2) : pointX;
              const clickY = rect ? (rect.top + rect.height / 2) : pointY;

              const risks = Array.isArray(scored.risks) ? [...scored.risks] : [];
              if (closeLikeClick && !closeHint) risks.push('close_hint_missing');

              if (closeLikeClick && requireCloseHint && !closeHint) {
                return {
                  clicked: false,
                  selector: (meta && meta.tag) ? meta.tag : '',
                  reason: 'close_hint_missing',
                  clickX,
                  clickY,
                  confidence: 0.0,
                  confidence_reasons: ['close_hint_missing'],
                  close_hint: false,
                  risk_flags: risks,
                  target_meta: meta,
                };
              }

              return {
                clicked: true,
                selector: (meta && meta.tag) ? meta.tag : '',
                reason: picked ? 'hit_target_click' : 'raw_point_click',
                clickX,
                clickY,
                confidence: scored.score,
                confidence_reasons: scored.reasons,
                close_hint: closeHint,
                risk_flags: risks,
                target_meta: meta,
              };
            }
            """,
            {
                "pointX": point_x,
                "pointY": point_y,
                "allowExternalNav": allow_external_nav,
                "closeLikeClick": bool(close_like_click),
                "requireCloseHint": bool(require_close_hint),
                "minConfidence": float(min_confidence),
            },
        )
        if not isinstance(payload, dict):
            return {"clicked": False, "selector": "", "error": "invalid_payload"}

        if not bool(payload.get("clicked")):
            return payload

        try:
            confidence = float(payload.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if confidence < min_confidence:
            payload["clicked"] = False
            payload["error"] = f"low_confidence_skip(conf={confidence:.2f} < thr={min_confidence:.2f})"
            payload["reason"] = str(payload.get("reason") or "") + ":low_confidence_skip"
            return payload

        try:
            click_x = float(payload.get("clickX", point_x) or point_x)
            click_y = float(payload.get("clickY", point_y) or point_y)
        except Exception:
            click_x = point_x
            click_y = point_y

        from gaia.src.phase4.mcp_ref_post_click_watch import watch_after_trusted_click

        async def _click() -> None:
            await page.mouse.click(click_x, click_y, delay=50)

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
            auto_close_popup=bool(close_like_click and auto_close_popup_on_close),
        )
        payload["clickX"] = click_x
        payload["clickY"] = click_y
        payload["x"] = click_x
        payload["y"] = click_y
        payload["input"] = "playwright_mouse"
        payload["post_watch"] = post_watch
        return payload
    except Exception as exc:
        return {"clicked": False, "selector": "", "error": str(exc)}


async def execute_ref_action_with_snapshot(
    *,
    session_id: str,
    snapshot_id: str,
    ref_id: str,
    action: str,
    value: Any = None,
    options: Optional[Dict[str, Any]] = None,
    url: str = "",
    selector_hint: str = "",
    verify: bool = True,
    tab_id: Optional[Any] = None,
) -> Dict[str, Any]:
    from gaia.src.phase4.mcp_ref_action_executor import execute_ref_action_with_snapshot_impl

    return await execute_ref_action_with_snapshot_impl(
        session_id=session_id,
        snapshot_id=snapshot_id,
        ref_id=ref_id,
        action=action,
        value=value,
        options=options,
        url=url,
        selector_hint=selector_hint,
        verify=verify,
        tab_id=tab_id,
        ctx={
            "playwright_instance": playwright_instance,
            "HTTPException": HTTPException,
            "active_sessions": active_sessions,
            "ensure_session": ensure_session,
            "_get_playwright_instance": _get_playwright_instance,
            "screencast_subscribers": screencast_subscribers,
            "_set_current_screencast_frame": _set_current_screencast_frame,
            "logger": logger,
            "normalize_url": normalize_url,
            "snapshot_page": snapshot_page,
            "_resolve_session_page": _resolve_session_page,
            "_get_tab_index": _get_tab_index,
            "_resolve_ref_meta_from_snapshot": _resolve_ref_meta_from_snapshot,
            "_resolve_stale_ref": _resolve_stale_ref,
            "_build_ref_candidates": _build_ref_candidates,
            "_resolve_locator_from_ref": _resolve_locator_from_ref,
            "_execute_action_on_locator": _execute_action_on_locator,
            "_try_click_hit_target_from_point": _try_click_hit_target_from_point,
            "_try_click_container_ancestor": _try_click_container_ancestor,
            "_extract_live_texts": _extract_live_texts,
            "_collect_page_evidence": _collect_page_evidence,
            "_collect_page_evidence_light": _collect_page_evidence_light,
            "_compute_runtime_dom_hash": _compute_runtime_dom_hash,
            "_state_change_flags": _state_change_flags,
            "_safe_read_target_state": _safe_read_target_state,
            "_read_focus_signature": _read_focus_signature,
        },
    )


async def _browser_start(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    url = str(params.get("url") or "")
    tab_id = params.get("tab_id")
    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    if url:
        current = normalize_url(page.url)
        target = normalize_url(url)
        if current != target:
            await page.goto(url, timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
    session.current_url = page.url
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": _get_tab_index(page),
        "targetId": _get_tab_index(page),
        "current_url": page.url,
    }


async def _browser_install(_params: Dict[str, Any]) -> Dict[str, Any]:
    installed = bool(playwright_instance is not None)
    return {
        "success": installed,
        "reason_code": "ok" if installed else "not_found",
        "installed": installed,
        "message": "Playwright initialized" if installed else "Playwright not initialized",
        "hint": "python -m playwright install chromium",
    }


async def _browser_profiles(_params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        "reason_code": "ok",
        "profiles": [
            {
                "profile_id": "default",
                "name": "default",
                "sessions": sorted(active_sessions.keys()),
            }
        ],
    }


async def _browser_tabs(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    tabs = await _tabs_payload_async(session, list(page.context.pages))
    current_tab_id = _get_tab_index(page)
    current_target_id = await _get_page_target_id(page)
    current_tab_payload = await _tab_payload_async(session, page, current_tab_id)
    return {
        "success": True,
        "reason_code": "ok",
        "running": True,
        "session_id": session_id,
        "tabs": tabs,
        "current_tab_id": current_tab_id,
        "targetId": current_tab_id,
        "cdp_target_id": current_target_id,
        "tab": current_tab_payload,
    }


async def _browser_tabs_open(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    def as_bool(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            return default
        if value is None:
            return default
        return bool(value)

    session_id = str(pick("session_id", "default"))
    url = str(pick("url") or "")
    activate = as_bool(pick("activate", True), True)
    session, page = await _resolve_session_page(session_id)
    context = page.context
    new_page = await context.new_page()
    session.observability.attach_page(new_page)
    if url:
        await new_page.goto(url, timeout=60000)
        try:
            await new_page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
    if activate:
        session.page = new_page
        session.dialog_listener_armed = False
        session.file_chooser_listener_armed = False
    current_page = session.page or new_page
    session.observability.attach_page(current_page)
    tabs = await _tabs_payload_async(session, list(context.pages))
    current_tab_id = _get_tab_index(current_page)
    current_target_id = await _get_page_target_id(current_page)
    opened_tab_payload = await _tab_payload_async(session, new_page, _get_tab_index(new_page))
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab": opened_tab_payload,
        "tabs": tabs,
        "current_tab_id": current_tab_id,
        "targetId": current_tab_id,
        "cdp_target_id": current_target_id,
    }


async def _browser_tabs_focus(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    target_id_raw = pick("targetId", pick("tab_id", pick("index")))
    if target_id_raw is None or not str(target_id_raw).strip():
        return build_error("invalid_input", "targetId/tab_id/index is required for tabs.focus")
    try:
        session, focused_page = await _resolve_session_page(session_id, tab_id=target_id_raw)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        extra: Dict[str, Any] = {}
        if isinstance(detail.get("matches"), list):
            extra["matches"] = detail.get("matches")
        return build_error(
            str(detail.get("reason_code") or "not_found"),
            str(detail.get("message") or detail or "tab not found"),
            **extra,
        )
    tabs = await _tabs_payload_async(session, list(focused_page.context.pages))
    current_tab_id = _get_tab_index(focused_page)
    current_target_id = await _get_page_target_id(focused_page)
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "current_tab_id": current_tab_id,
        "targetId": current_tab_id,
        "cdp_target_id": current_target_id,
        "tab": await _tab_payload_async(session, focused_page, current_tab_id),
        "tabs": tabs,
    }


async def _browser_tabs_close(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    pages = list(page.context.pages)
    target_raw = pick("targetId", pick("tab_id", pick("index")))
    if target_raw is None or not str(target_raw).strip():
        target_page = page
    else:
        status, _, resolved_page, matches = await _resolve_page_from_tab_identifier(
            pages,
            target_raw,
            session.browser,
        )
        if status == "ambiguous":
            return build_error("ambiguous_target_id", "ambiguous target id prefix", matches=matches)
        if status != "ok" or resolved_page is None:
            return build_error("not_found", f"tab not found: {target_raw}")
        target_page = resolved_page

    target_id = _get_tab_index(target_page)
    was_active = session.page is target_page
    await target_page.close()
    remaining = page.context.pages
    if not remaining:
        fallback_page = await page.context.new_page()
        remaining = [fallback_page]
    if was_active or session.page not in remaining:
        next_idx = min(target_id, len(remaining) - 1)
        session.page = remaining[next_idx]
        session.dialog_listener_armed = False
        session.file_chooser_listener_armed = False
    active_page = session.page or remaining[0]
    session.observability.attach_page(active_page)
    tabs = await _tabs_payload_async(session, list(active_page.context.pages))
    current_tab_id = _get_tab_index(active_page)
    current_target_id = await _get_page_target_id(active_page)
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "closed_tab_id": target_id,
        "current_tab_id": current_tab_id,
        "targetId": current_tab_id,
        "cdp_target_id": current_target_id,
        "current_url": active_page.url,
        "tab": await _tab_payload_async(session, active_page, current_tab_id),
        "tabs": tabs,
    }


async def _browser_tabs_action(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    op = str(
        params.get("tab_action")
        or params.get("op")
        or params.get("action")
        or (payload.get("tab_action") if isinstance(payload, dict) else None)
        or (payload.get("op") if isinstance(payload, dict) else None)
        or (payload.get("action") if isinstance(payload, dict) else None)
        or "list"
    ).strip().lower()
    if op in {"list"}:
        return await _browser_tabs(params)
    if op in {"new", "open"}:
        return await _browser_tabs_open(params)
    if op in {"select", "focus"}:
        return await _browser_tabs_focus(params)
    if op in {"close", "delete"}:
        return await _browser_tabs_close(params)
    return build_error("invalid_input", "tabs.action must be one of: list|new|open|select|focus|close")


async def _browser_snapshot(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    url = str(pick("url") or "")
    snapshot_format = str(pick("format") or "").strip().lower()
    mode = str(pick("mode") or "").strip().lower()
    refs_mode = str(pick("refs", "ref") or "ref").strip().lower()
    labels = bool(pick("labels", False))
    if refs_mode not in {"ref", "role", "aria"}:
        refs_mode = "ref"
    if snapshot_format and snapshot_format not in {"ai", "aria", "role", "ref"}:
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "invalid_snapshot_options",
                "message": "format must be one of: ai, aria, role, ref",
            },
        )

    if mode == "efficient" and snapshot_format == "aria":
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "invalid_snapshot_options",
                "message": "mode=efficient is not allowed with format=aria",
            },
        )
    if labels and snapshot_format == "aria":
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "invalid_snapshot_options",
                "message": "labels require format=ai|role|ref",
            },
        )

    normalized_tab_id = _coerce_tab_id(tab_id)

    def _is_retryable_page_detach_error(exc: BaseException) -> bool:
        message = str(exc or "").strip().lower()
        if not message:
            return False
        return (
            "frame has been detached" in message
            or "target page, context or browser has been closed" in message
        )

    async def _goto_with_retry(target_page: Any, target_url: str, *, timeout: int) -> None:
        try:
            await target_page.goto(target_url, timeout=timeout)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            await target_page.goto(target_url, timeout=timeout)

    if normalized_tab_id is not None:
        session, page = await _resolve_session_page(session_id, tab_id=normalized_tab_id)
        if url:
            current = normalize_url(page.url)
            target = normalize_url(url)
            if current != target:
                await _goto_with_retry(page, url, timeout=60000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
        snap = await snapshot_page(url="", session_id=session_id)
    else:
        snap = await snapshot_page(url=url, session_id=session_id)
        session, page = await _resolve_session_page(session_id)
    elements = snap.get("dom_elements") or snap.get("elements") or []
    elements_by_ref = _extract_elements_by_ref(snap)
    result = {
        "success": True,
        "ok": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": _get_tab_index(page),
        "targetId": _get_tab_index(page),
        "snapshot_id": snap.get("snapshot_id", ""),
        "epoch": int(snap.get("epoch") or 0),
        "dom_hash": str(snap.get("dom_hash") or ""),
        "mode": "ref",
        "format": snapshot_format or "ref",
        "elements": elements,
        "dom_elements": elements,
        "elements_by_ref": elements_by_ref,
        "current_url": page.url,
    }

    wants_text_snapshot = bool(snapshot_format in {"ai", "aria", "role"} or mode == "efficient")
    if wants_text_snapshot:
        interactive = bool(pick("interactive", mode == "efficient"))
        compact = bool(pick("compact", mode == "efficient"))
        limit = int(pick("limit") or 700)
        max_chars = int(pick("max_chars") or pick("maxChars") or 64000)
        timeout_ms = int(pick("timeout_ms") or pick("timeoutMs") or 5000)
        max_depth_raw = pick("max_depth", pick("maxDepth"))
        max_depth: Optional[int] = None
        if max_depth_raw is not None and str(max_depth_raw).strip() != "":
            try:
                max_depth = max(0, int(max_depth_raw))
            except Exception:
                max_depth = None
        selector = str(pick("selector") or "").strip()
        frame_filter = pick("frame")
        requested_format = snapshot_format or ("ai" if mode == "efficient" else "ref")

        if refs_mode == "aria" and (selector or frame_filter is not None):
            raise HTTPException(
                status_code=400,
                detail={
                    "reason_code": "invalid_snapshot_options",
                    "message": "refs=aria does not support selector/frame snapshots yet.",
                },
            )

        filtered_elements = elements
        if selector:
            needle = selector.lower()
            filtered_elements = [
                el for el in filtered_elements
                if needle in str(el.get("selector") or "").lower()
                or needle in str(el.get("full_selector") or "").lower()
            ]
        if frame_filter is not None:
            try:
                frame_idx = int(frame_filter)
                filtered_elements = [
                    el for el in filtered_elements
                    if int(((el.get("scope") or {}).get("frame_index", el.get("frame_index", 0)) or 0)) == frame_idx
                ]
            except Exception:
                pass

        refs_from_elements = _build_role_refs_from_elements(filtered_elements)
        meta_base = {
            "selector": selector,
            "frame": frame_filter,
            "interactive": interactive,
            "compact": compact,
            "limit": limit,
            "max_chars": max_chars,
            "max_depth": max_depth,
            "timeout_ms": timeout_ms,
            "refs_mode_requested": refs_mode,
        }

        used_special_snapshot = False

        # OpenClaw parity: role/aria snapshot 우선
        if requested_format in {"role", "aria"}:
            aria_text = ""
            try:
                target_locator = None
                if frame_filter is not None:
                    frame_idx = int(frame_filter)
                    frames = page.frames
                    if frame_idx < 0 or frame_idx >= len(frames):
                        raise ValueError(f"frame index out of range: {frame_idx}")
                    frame_obj = frames[frame_idx]
                    if selector:
                        target_locator = frame_obj.locator(selector).first
                    else:
                        target_locator = frame_obj.locator(":root")
                else:
                    if selector:
                        target_locator = page.locator(selector).first
                    else:
                        target_locator = page.locator(":root")
                aria_text = await target_locator.aria_snapshot(timeout=max(500, min(timeout_ms, 60000)))
            except Exception:
                aria_text = ""

            if isinstance(aria_text, str) and aria_text.strip():
                role_payload = _build_role_snapshot_from_aria_text(
                    aria_text,
                    interactive=interactive,
                    compact=compact,
                    max_depth=max_depth,
                    line_limit=max(1, min(limit, 2000)),
                    max_chars=max_chars,
                )
                role_refs = role_payload.get("refs") if isinstance(role_payload.get("refs"), dict) else {}
                effective_refs_mode = refs_mode
                if refs_mode == "aria":
                    # Python 환경에서는 role 경로로 폴백될 수 있음
                    effective_refs_mode = "role"
                result.update(
                    {
                        "format": requested_format,
                        "mode": mode or "full",
                        "refs_mode": effective_refs_mode,
                        "snapshot": role_payload.get("snapshot", ""),
                        "snapshot_lines": str(role_payload.get("snapshot", "")).split("\n"),
                        "snapshot_stats": role_payload.get("stats", {}),
                        "refs": role_refs,
                        "labels": [] if labels else None,
                        "labelsCount": 0 if labels else None,
                        "labelsSkipped": 0 if labels else None,
                        "meta": {**meta_base, "snapshot_source": "aria_snapshot"},
                    }
                )
                used_special_snapshot = True

        # OpenClaw parity: ai snapshot (_snapshotForAI) 우선 시도
        if (not used_special_snapshot) and requested_format in {"ai"}:
            ai_text = await _try_snapshot_for_ai(page, timeout_ms=timeout_ms)
            if isinstance(ai_text, str) and ai_text.strip():
                ai_payload = _build_role_snapshot_from_ai_text(
                    ai_text,
                    interactive=interactive,
                    compact=compact,
                    max_depth=max_depth,
                    line_limit=max(1, min(limit, 5000)),
                    max_chars=max_chars,
                )
                parsed_refs = ai_payload.get("refs") if isinstance(ai_payload.get("refs"), dict) else {}
                effective_refs = parsed_refs or refs_from_elements
                effective_refs_mode = "aria" if parsed_refs else "role"
                if refs_mode == "ref":
                    effective_refs_mode = "ref"
                result.update(
                    {
                        "format": requested_format,
                        "mode": mode or "full",
                        "refs_mode": effective_refs_mode,
                        "snapshot": ai_payload.get("snapshot", ""),
                        "snapshot_lines": str(ai_payload.get("snapshot", "")).split("\n"),
                        "snapshot_stats": ai_payload.get("stats", {}),
                        "refs": effective_refs,
                        "labels": [] if labels else None,
                        "labelsCount": 0 if labels else None,
                        "labelsSkipped": 0 if labels else None,
                        "meta": {**meta_base, "snapshot_source": "ai_snapshot"},
                    }
                )
                used_special_snapshot = True

        if not used_special_snapshot:
            text_payload = _build_snapshot_text(
                filtered_elements,
                interactive_only=interactive,
                compact=compact,
                limit=limit,
                max_chars=max_chars,
            )
            result.update(
                {
                    "format": requested_format,
                    "mode": mode or "full",
                    "refs_mode": refs_mode,
                    "snapshot": text_payload.get("text", ""),
                    "snapshot_lines": text_payload.get("lines", []),
                    "snapshot_stats": text_payload.get("stats", {}),
                    "refs": refs_from_elements,
                    "labels": [] if labels else None,
                    "labelsCount": 0 if labels else None,
                    "labelsSkipped": 0 if labels else None,
                    "meta": {**meta_base, "snapshot_source": "dom_elements"},
                }
            )
    result["ok"] = bool(result.get("success", True))
    return result


async def _browser_act(params: Dict[str, Any]) -> Dict[str, Any]:
    trace_started_at = time.perf_counter()
    trace_auth_submit_enabled = str(os.getenv("GAIA_TRACE_AUTH_SUBMIT", "0")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    selector_raw = pick("selector")
    selector_provided = bool(str(selector_raw or "").strip())
    raw_action = str(
        (payload.get("kind") if isinstance(payload, dict) else None)
        or params.get("kind")
        or (payload.get("action") if isinstance(payload, dict) else None)
        or params.get("action")
        or ""
    ).strip()
    action = raw_action
    force_double_click = False
    action_lower = action.lower()
    type_submit = False
    if action_lower in {"doubleclick", "dblclick"}:
        action = "click"
        force_double_click = True
    elif action_lower == "type":
        action = "fill"
        type_submit = bool(pick("submit", False))
    elif action_lower == "drag":
        action = "dragAndDrop"
    url = str(pick("url") or "")
    value = pick("value")
    if action == "fill" and value is None:
        text_value = pick("text")
        if text_value is not None:
            value = str(text_value)
    values = pick("values")
    fields = pick("fields")
    verify = bool(pick("verify", True))
    snapshot_id = str(pick("snapshot_id") or pick("snapshotId") or "")
    ref_id = str(pick("ref_id") or pick("refId") or pick("ref") or "")
    selector_hint = str(
        pick("selector_hint")
        or pick("selectorHint")
        or pick("selector")
        or ""
    )
    trace_auth_submit = any(
        token in selector_hint.lower()
        for token in ("로그인", "login", "sign in", "회원가입", "sign up", "register")
    )
    action_options: Dict[str, Any] = {}
    for option_key in ("timeoutMs", "timeout_ms", "doubleClick", "double_click", "button", "modifiers"):
        option_value = pick(option_key)
        if option_value is not None:
            action_options[option_key] = option_value
    if force_double_click:
        action_options["doubleClick"] = True

    if not action:
        raise HTTPException(status_code=400, detail="action is required for 'browser_act'.")
    if selector_provided and action != "wait":
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "legacy_selector_forbidden",
                "message": "'selector' is not supported for /act. Use snapshot refs.",
            },
        )

    evaluate_enabled_raw = str(os.getenv("GAIA_BROWSER_EVALUATE_ENABLED", "true")).strip().lower()
    evaluate_enabled = evaluate_enabled_raw not in {"0", "false", "no", "off"}

    if action == "evaluate":
        eval_expr = pick("fn") if pick("fn") is not None else value
        if eval_expr is None or not str(eval_expr).strip():
            raise HTTPException(
                status_code=400,
                detail={
                    "reason_code": "invalid_input",
                    "message": "fn is required for evaluate",
                },
            )
        if not evaluate_enabled:
            raise HTTPException(
                status_code=403,
                detail={
                    "reason_code": "not_actionable",
                    "message": (
                        "evaluate is disabled by config (browser.evaluateEnabled=false).\n"
                        "Docs: /gateway/configuration#browser-openclaw-managed-browser"
                    ),
                },
            )
        value = eval_expr

    if action == "resize":
        width = pick("width")
        height = pick("height")
        if width is None or height is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason_code": "invalid_input",
                    "message": "width and height are required for resize",
                },
            )

    if action == "select" and values is None and (value is None or not str(value).strip()):
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "invalid_input",
                "message": "ref and values are required for select",
            },
        )

    if action == "fill" and isinstance(fields, list):
        if not snapshot_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason_code": "ref_required",
                    "message": "snapshot_id is required when using fill fields[]",
                },
            )
        field_results: List[Dict[str, Any]] = []
        for idx, field in enumerate(fields, start=1):
            if not isinstance(field, dict):
                continue
            field_ref = str(field.get("ref") or field.get("refId") or field.get("ref_id") or "")
            if not field_ref:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "reason_code": "ref_required",
                        "message": f"fields[{idx}] missing ref/refId",
                    },
                )
            field_type = str(field.get("type") or "text").strip().lower()
            field_value = field.get("value")
            if field_type in {"select", "dropdown"}:
                action_name = "select"
                action_value = field.get("values") if isinstance(field.get("values"), list) else field_value
            elif field_type in {"checkbox", "radio", "toggle", "switch"}:
                # setChecked 사용: 이미 해당 상태면 skip, 토글 오류 방지
                action_name = "setChecked"
                action_value = field_value
            else:
                action_name = "fill"
                action_value = "" if field_value is None else str(field_value)
            single_result = await execute_ref_action_with_snapshot(
                session_id=session_id,
                snapshot_id=snapshot_id,
                ref_id=field_ref,
                action=action_name,
                value=action_value,
                options=action_options,
                url=url,
                selector_hint=selector_hint,
                verify=verify,
                tab_id=tab_id,
            )
            field_results.append(
                {
                    "index": idx,
                    "ref_id": field_ref,
                    "type": field_type,
                    "action": action_name,
                    "success": bool(single_result.get("success", False)),
                    "effective": bool(single_result.get("effective", False)),
                    "reason_code": str(single_result.get("reason_code") or "unknown_error"),
                    "reason": str(single_result.get("reason") or ""),
                }
            )
            if not bool(single_result.get("success", False)) or not bool(single_result.get("effective", False)):
                return {
                    "success": False,
                    "effective": False,
                    "reason_code": str(single_result.get("reason_code") or "unknown_error"),
                    "reason": str(single_result.get("reason") or "fill fields execution failed"),
                    "fields": field_results,
                    "snapshot_id_used": snapshot_id,
                }
        return {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "reason": "fill fields applied",
            "fields": field_results,
            "snapshot_id_used": snapshot_id,
        }

    if action == "select" and isinstance(values, list):
        normalized_values = [str(item).strip() for item in values if str(item).strip()]
        if normalized_values:
            value = normalized_values if len(normalized_values) > 1 else normalized_values[0]

    if is_element_action(action):
        if not snapshot_id or not ref_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason_code": "ref_required",
                    "message": "snapshot_id + ref_id are required for element actions",
                },
            )
        if trace_auth_submit and trace_auth_submit_enabled:
            print(
                f"[trace_browser_act] start action={action} verify={verify} "
                f"ref_id={ref_id} selector_hint={selector_hint!r}"
            )
        ref_dispatch_started_at = time.perf_counter()
        result = await execute_ref_action_with_snapshot(
            session_id=session_id,
            snapshot_id=snapshot_id,
            ref_id=ref_id,
            action=action,
            value=value,
            options=action_options,
            url=url,
            selector_hint=selector_hint,
            verify=verify,
            tab_id=tab_id,
        )
        if trace_auth_submit and trace_auth_submit_enabled:
            print(
                f"[trace_browser_act] ref_dispatch_ms={int((time.perf_counter() - ref_dispatch_started_at) * 1000)} "
                f"success={bool(result.get('success'))} effective={bool(result.get('effective', False))} "
                f"reason_code={result.get('reason_code')}"
            )
        if type_submit and bool(result.get("success")) and bool(result.get("effective")):
            press_result = await execute_ref_action_with_snapshot(
                session_id=session_id,
                snapshot_id=snapshot_id,
                ref_id=ref_id,
                action="press",
                value="Enter",
                options=action_options,
                url=url,
                selector_hint=selector_hint,
                verify=verify,
                tab_id=tab_id,
            )
            if not bool(press_result.get("success")) or not bool(press_result.get("effective")):
                return press_result
            result = press_result
        result.setdefault("snapshot_id_used", snapshot_id)
        result.setdefault("ref_id_used", ref_id)
        result.setdefault("retry_path", [])
        result.setdefault("attempt_logs", [])
        result.setdefault("attempt_count", len(result.get("attempt_logs", [])))
        result.setdefault("state_change", {})
        if trace_auth_submit and trace_auth_submit_enabled:
            print(
                f"[trace_browser_act] total_ms={int((time.perf_counter() - trace_started_at) * 1000)} "
                f"return_reason={result.get('reason_code')}"
            )
        return result

    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    if action == "close":
        close_result = await _browser_tabs_close(
            {
                "session_id": session_id,
                "targetId": tab_id if tab_id is not None else _get_tab_index(page),
            }
        )
        ok = bool(close_result.get("success"))
        return {
            "success": ok,
            "effective": ok,
            "reason_code": str(close_result.get("reason_code") or ("ok" if ok else "failed")),
            "reason": str(close_result.get("reason") or ("tab closed" if ok else "tab close failed")),
            "state_change": {"effective": ok, "tab_closed": ok},
            "attempt_logs": [],
            "snapshot_id_used": snapshot_id,
            "ref_id_used": ref_id,
            "retry_path": [],
            "attempt_count": 0,
            "current_url": page.url,
            "tab": close_result.get("tab"),
            "tabs": close_result.get("tabs", []),
        }

    if action == "wait":
        wait_payload: Dict[str, Any] = {"session_id": session_id}
        if tab_id is not None:
            wait_payload["tab_id"] = tab_id
        if isinstance(value, dict):
            wait_payload.update(dict(value))
        for key in (
            "selector",
            "selector_state",
            "js",
            "fn",
            "url",
            "load_state",
            "loadState",
            "text",
            "text_gone",
            "textGone",
            "timeout_ms",
            "timeoutMs",
            "time_ms",
            "timeMs",
        ):
            picked = pick(key)
            if picked is not None:
                wait_payload[key] = picked
        if "loadState" in wait_payload and "load_state" not in wait_payload:
            wait_payload["load_state"] = wait_payload.pop("loadState")
        if "textGone" in wait_payload and "text_gone" not in wait_payload:
            wait_payload["text_gone"] = wait_payload.pop("textGone")
        if "timeoutMs" in wait_payload and "timeout_ms" not in wait_payload:
            wait_payload["timeout_ms"] = wait_payload.pop("timeoutMs")
        if "timeMs" in wait_payload and "time_ms" not in wait_payload:
            wait_payload["time_ms"] = wait_payload.pop("timeMs")
        if "fn" in wait_payload and "js" not in wait_payload:
            wait_payload["js"] = wait_payload.pop("fn")

        rich_wait_keys = {"selector", "js", "url", "load_state", "text", "text_gone", "time_ms"}
        if any(wait_payload.get(k) not in (None, "") for k in rich_wait_keys):
            return await _browser_wait(wait_payload)
        wait_ms: int
        if wait_payload.get("timeout_ms") not in (None, ""):
            try:
                wait_ms = max(0, int(wait_payload.get("timeout_ms")))
            except Exception:
                wait_ms = 500
        elif isinstance(value, (int, str)) and str(value).strip():
            try:
                wait_ms = max(0, int(value))
            except Exception:
                wait_ms = 500
        else:
            wait_ms = 500
        await page.wait_for_timeout(max(0, wait_ms))
        session.current_url = page.url
        screenshot_bytes = await page.screenshot(full_page=False)
        screenshot = base64.b64encode(screenshot_bytes).decode("utf-8")
        return {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "reason": "wait completed",
            "state_change": {"effective": True, "wait_ms": wait_ms},
            "attempt_logs": [],
            "snapshot_id_used": snapshot_id,
            "ref_id_used": ref_id,
            "retry_path": [],
            "attempt_count": 0,
            "current_url": session.current_url,
            "tab_id": _get_tab_index(page),
            "targetId": _get_tab_index(page),
            "screenshot": screenshot,
        }

    legacy = await execute_simple_action(
        url=url,
        selector="",
        action=("setViewport" if action == "resize" else action),
        value=(
            value
            if action != "resize"
            else [pick("width"), pick("height")]
        ) if action != "evaluate" else (pick("fn") if pick("fn") is not None else value),
        session_id=session_id,
        before_screenshot=None,
        action_options=action_options,
    )
    ok = bool(legacy.get("success"))
    reason = str(legacy.get("message") or legacy.get("reason") or "")
    reason_code = str(legacy.get("reason_code") or ("ok" if ok else "failed"))
    effective = bool(legacy.get("effective", ok))
    return {
        "success": ok,
        "effective": effective,
        "reason_code": reason_code,
        "reason": reason or ("ok" if ok else "action_failed"),
        "state_change": {"effective": effective},
        "attempt_logs": [],
        "snapshot_id_used": snapshot_id,
        "ref_id_used": ref_id,
        "retry_path": [],
        "attempt_count": 0,
        "current_url": legacy.get("current_url", page.url),
        "tab_id": _get_tab_index(page),
        "targetId": _get_tab_index(page),
        "screenshot": legacy.get("screenshot"),
    }


async def _browser_wait(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def _coerce_scalar_str(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            for key in ("text", "textContains", "url", "loadState", "selector"):
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    return nested
            return ""
        return ""

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    timeout_ms = int(pick("timeout_ms") or pick("timeoutMs") or 20000)
    selector = _coerce_scalar_str(pick("selector"))
    selector_state = _coerce_scalar_str(pick("selector_state") or "visible") or "visible"
    js_expr = _coerce_scalar_str(pick("js") or pick("fn"))
    target_url = _coerce_scalar_str(pick("url"))
    load_state = _coerce_scalar_str(pick("load_state") or pick("loadState"))
    text_contains = _coerce_scalar_str(pick("text"))
    text_gone = _coerce_scalar_str(pick("text_gone") or pick("textGone"))
    allowed_load_states = {"load", "domcontentloaded", "networkidle"}
    if load_state and load_state not in allowed_load_states:
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "invalid_input",
                "message": "load_state must be one of: load, domcontentloaded, networkidle",
            },
        )
    evaluate_enabled_raw = str(os.getenv("GAIA_BROWSER_EVALUATE_ENABLED", "true")).strip().lower()
    evaluate_enabled = evaluate_enabled_raw not in {"0", "false", "no", "off"}
    if js_expr and not evaluate_enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "reason_code": "not_actionable",
                "message": (
                    "wait --fn is disabled by config (browser.evaluateEnabled=false).\n"
                    "Docs: /gateway/configuration#browser-openclaw-managed-browser"
                ),
            },
        )
    time_ms = pick("time_ms", pick("timeMs"))
    explicit_time_ms: Optional[int] = None
    if isinstance(time_ms, (int, str)) and str(time_ms).strip():
        try:
            explicit_time_ms = max(0, int(time_ms))
            timeout_ms = max(timeout_ms, explicit_time_ms)
        except Exception:
            pass

    if (
        explicit_time_ms is None
        and not selector
        and not text_contains
        and not text_gone
        and not target_url
        and not load_state
        and not js_expr
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "invalid_input",
                "message": "wait requires at least one of: timeMs, text, textGone, selector, url, loadState, fn",
            },
        )

    has_wait_conditions = any((target_url, load_state, selector, text_contains, text_gone, js_expr))
    if explicit_time_ms is not None and not has_wait_conditions:
        await page.wait_for_timeout(explicit_time_ms)

    if target_url:
        current = normalize_url(page.url)
        target = normalize_url(target_url)
        if current != target:
            try:
                await page.goto(target_url, timeout=max(timeout_ms, 1000))
            except Exception as exc:
                message = str(exc or "").strip().lower()
                if (
                    "frame has been detached" not in message
                    and "target page, context or browser has been closed" not in message
                ):
                    raise
                await page.wait_for_timeout(150)
                await page.goto(target_url, timeout=max(timeout_ms, 1000))
    if load_state:
        await page.wait_for_load_state(load_state, timeout=timeout_ms)
    if selector:
        await page.locator(selector).first.wait_for(state=selector_state, timeout=timeout_ms)
    if text_contains:
        await page.locator(f"text={text_contains}").first.wait_for(state="visible", timeout=timeout_ms)
    if text_gone:
        await page.locator(f"text={text_gone}").first.wait_for(state="hidden", timeout=timeout_ms)
    if js_expr:
        start = time.time()
        ok = False
        while (time.time() - start) * 1000 < timeout_ms:
            try:
                if await page.evaluate(js_expr):
                    ok = True
                    break
            except Exception:
                pass
            await page.wait_for_timeout(200)
        if not ok:
            return build_error("not_found", "js condition not satisfied", timeout_ms=timeout_ms)

    session.current_url = page.url
    tab_idx = _get_tab_index(page)
    return {
        "success": True,
        "reason_code": "ok",
        "current_url": session.current_url,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "meta": {
            "selector": selector,
            "selector_state": selector_state,
            "text": text_contains,
            "text_gone": text_gone,
            "load_state": load_state,
            "js": bool(js_expr),
            "timeout_ms": timeout_ms,
        },
    }


async def _browser_screenshot(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    def as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            return default
        if value is None:
            return default
        return bool(value)

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    url = str(pick("url") or "")
    full_page = as_bool(pick("full_page", pick("fullPage", False)), False)
    image_type = str(pick("type") or "png").strip().lower()
    if image_type not in {"png", "jpeg", "webp"}:
        image_type = "png"
    quality_raw = pick("quality")
    quality = None
    if quality_raw is not None and str(quality_raw).strip():
        try:
            quality = max(1, min(100, int(quality_raw)))
        except Exception:
            quality = None
    output_path = str(pick("path") or "")

    session, page = await _resolve_session_page(session_id, tab_id=tab_id)

    def _is_retryable_page_detach_error(exc: BaseException) -> bool:
        message = str(exc or "").strip().lower()
        if not message:
            return False
        return (
            "frame has been detached" in message
            or "target page, context or browser has been closed" in message
        )

    async def _goto_with_retry(target_page: Any, target_url: str, *, timeout: int) -> None:
        try:
            await target_page.goto(target_url, timeout=timeout)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            await target_page.goto(target_url, timeout=timeout)

    async def _screenshot_with_retry(target_page: Any, **kwargs: Any) -> bytes:
        try:
            return await target_page.screenshot(**kwargs)
        except Exception as exc:
            if not _is_retryable_page_detach_error(exc):
                raise
            await target_page.wait_for_timeout(150)
            return await target_page.screenshot(**kwargs)

    if url:
        current = normalize_url(page.url)
        target = normalize_url(url)
        if current != target:
            await _goto_with_retry(page, url, timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

    screenshot_kwargs: Dict[str, Any] = {"full_page": full_page, "type": image_type}
    if quality is not None and image_type in {"jpeg", "webp"}:
        screenshot_kwargs["quality"] = quality
    screenshot_bytes = await _screenshot_with_retry(page, **screenshot_kwargs)
    screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    saved_path = ""
    if output_path:
        screenshot_root = (Path.home() / ".gaia" / "screenshots").resolve()
        screenshot_root.mkdir(parents=True, exist_ok=True)
        requested = Path(output_path).expanduser().resolve()
        if not requested.is_relative_to(screenshot_root):
            return build_error("not_actionable", f"screenshot path must be under {screenshot_root}")
        requested.parent.mkdir(parents=True, exist_ok=True)
        requested.write_bytes(screenshot_bytes)
        saved_path = str(requested)

    session.current_url = page.url
    tab_idx = _get_tab_index(page)
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "current_url": page.url,
        "screenshot": screenshot_base64,
        "mime_type": f"image/{image_type}",
        "saved_path": saved_path,
        "meta": {"full_page": full_page, "type": image_type, "quality": quality},
    }


async def _browser_pdf(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    def as_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
            return default
        if value is None:
            return default
        return bool(value)

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    url = str(pick("url") or "")
    output_path = str(pick("path") or "")
    fmt = str(pick("format") or "A4")
    landscape = as_bool(pick("landscape", False), False)
    print_background = as_bool(pick("printBackground", pick("print_background", True)), True)
    scale_raw = pick("scale")
    scale = None
    if scale_raw is not None and str(scale_raw).strip():
        try:
            scale = max(0.1, min(2.0, float(scale_raw)))
        except Exception:
            scale = None
    margin = pick("margin")
    margin_dict = margin if isinstance(margin, dict) else None

    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    if url:
        current = normalize_url(page.url)
        target = normalize_url(url)
        if current != target:
            await page.goto(url, timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

    pdf_root = (Path.home() / ".gaia" / "pdf").resolve()
    pdf_root.mkdir(parents=True, exist_ok=True)
    if output_path:
        requested = Path(output_path).expanduser().resolve()
        if not requested.is_relative_to(pdf_root):
            return build_error("not_actionable", f"pdf path must be under {pdf_root}")
        final_path = requested
    else:
        final_path = (pdf_root / f"{session_id}_{int(time.time())}.pdf").resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)

    pdf_kwargs: Dict[str, Any] = {
        "path": str(final_path),
        "format": fmt,
        "landscape": landscape,
        "print_background": print_background,
    }
    if scale is not None:
        pdf_kwargs["scale"] = scale
    if margin_dict is not None:
        pdf_kwargs["margin"] = margin_dict
    await page.pdf(**pdf_kwargs)

    session.current_url = page.url
    tab_idx = _get_tab_index(page)
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "current_url": page.url,
        "path": str(final_path),
        "meta": {
            "format": fmt,
            "landscape": landscape,
            "print_background": print_background,
            "scale": scale,
        },
    }


async def _browser_console_get(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    session, _ = await _resolve_session_page(session_id, tab_id=pick("tab_id", pick("targetId")))
    limit = int(pick("limit") or 100)
    level = str(pick("level") or "")
    tab_idx = _get_tab_index(session.page) if session.page else 0
    return {
        "success": True,
        "reason_code": "ok",
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "items": session.observability.get_console(limit=limit, level=level),
        "meta": {"limit": limit, "level": level},
    }


async def _browser_errors_get(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    session, _ = await _resolve_session_page(session_id, tab_id=pick("tab_id", pick("targetId")))
    limit = int(pick("limit") or 100)
    tab_idx = _get_tab_index(session.page) if session.page else 0
    return {
        "success": True,
        "reason_code": "ok",
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "items": session.observability.get_errors(limit=limit),
        "meta": {"limit": limit},
    }


async def _browser_requests_get(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    session, _ = await _resolve_session_page(session_id, tab_id=pick("tab_id", pick("targetId")))
    limit = int(pick("limit") or 100)
    url_contains = str(pick("url_contains") or "")
    pattern = str(pick("pattern") or pick("filter") or "")
    method = str(pick("method") or "")
    resource_type = str(pick("resource_type") or "")
    clear_raw = pick("clear", False)
    if isinstance(clear_raw, str):
        clear = clear_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        clear = bool(clear_raw)
    status = pick("status")
    status_int = int(status) if isinstance(status, (int, str)) and str(status).strip() else None
    if clear:
        session.observability.clear_requests()
    items = session.observability.get_requests(
        limit=limit,
        url_contains=url_contains,
        pattern=pattern,
        method=method,
        resource_type=resource_type,
        status=status_int,
    )
    tab_idx = _get_tab_index(session.page) if session.page else 0
    return {
        "success": True,
        "reason_code": "ok",
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "items": items,
        "meta": {
            "limit": limit,
            "url_contains": url_contains,
            "pattern": pattern,
            "method": method,
            "resource_type": resource_type,
            "status": status_int,
            "clear": clear,
        },
    }


async def _browser_response_body(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    session, _ = await _resolve_session_page(session_id, tab_id=pick("tab_id", pick("targetId")))
    request_id = str(pick("request_id") or "")
    url = str(pick("url") or "")
    url_contains = str(pick("url_contains") or "")
    pattern = str(pick("pattern") or pick("filter") or "")
    method = str(pick("method") or "")
    max_chars_raw = pick("max_chars", pick("maxChars"))
    max_chars = int(max_chars_raw) if isinstance(max_chars_raw, (int, str)) and str(max_chars_raw).strip() else 200_000
    result = await session.observability.get_response_body(
        request_id=request_id,
        url=url,
        url_contains=url_contains,
        pattern=pattern,
        method=method,
        max_chars=max_chars,
    )
    if not result.get("success"):
        return result
    tab_idx = _get_tab_index(session.page) if session.page else 0
    return {
        "success": True,
        "reason_code": "ok",
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "item": result.get("body", {}),
        "meta": {
            "request_id": request_id,
            "url": url,
            "url_contains": url_contains,
            "pattern": pattern,
            "method": method,
            "max_chars": max_chars,
        },
    }


async def _browser_trace_start(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    def as_bool(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "yes", "on"}:
                return True
            if v in {"0", "false", "no", "off"}:
                return False
            return default
        if value is None:
            return default
        return bool(value)

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    tab_idx = _get_tab_index(page)
    if session.trace_active:
        return {
            "success": True,
            "reason_code": "ok",
            "active": True,
            "message": "trace already active",
            "tab_id": tab_idx,
            "targetId": tab_idx,
        }
    screenshots = as_bool(pick("screenshots", True), True)
    snapshots = as_bool(pick("snapshots", True), True)
    sources = as_bool(pick("sources", True), True)
    await page.context.tracing.start(screenshots=screenshots, snapshots=snapshots, sources=sources)
    session.trace_active = True
    return {
        "success": True,
        "reason_code": "ok",
        "active": True,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "meta": {"screenshots": screenshots, "snapshots": snapshots, "sources": sources},
    }


async def _browser_trace_stop(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    output_path = str(pick("path") or "")
    trace_root = (Path.home() / ".gaia" / "traces").resolve()
    trace_root.mkdir(parents=True, exist_ok=True)
    if output_path:
        requested = Path(output_path).expanduser().resolve()
        if not requested.is_relative_to(trace_root):
            return build_error(
                "not_actionable",
                f"trace path must be under {trace_root}",
            )
        final_path = requested
    else:
        final_path = (trace_root / f"{session_id}_{int(time.time())}.zip").resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if session.trace_active:
        await page.context.tracing.stop(path=str(final_path))
        session.trace_active = False
        session.trace_path = str(final_path)
    tab_idx = _get_tab_index(page)
    return {
        "success": True,
        "reason_code": "ok",
        "active": False,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "path": str(final_path),
        "meta": {"trace_root": str(trace_root)},
    }


async def _browser_highlight(params: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    session_id = str(pick("session_id", "default"))
    session, page = await _resolve_session_page(session_id)
    selector = str(pick("selector") or "")
    snapshot_id = str(pick("snapshot_id") or "")
    ref_id = str(pick("ref_id") or pick("ref") or "")
    duration_ms = int(pick("duration_ms", 1200) or 1200)

    if selector:
        return build_error(
            "legacy_selector_forbidden",
            "selector is not supported for highlight; use ref (and optional snapshot_id).",
        )
    if not ref_id:
        return build_error("ref_required", "ref is required for highlight.")
    if not snapshot_id:
        snapshot_id = str(session.current_snapshot_id or "")
    if not snapshot_id and session.snapshots:
        try:
            snapshot_id = max(
                session.snapshots.keys(),
                key=lambda sid: int((session.snapshots.get(sid) or {}).get("epoch") or 0),
            )
        except Exception:
            snapshot_id = next(iter(session.snapshots.keys()), "")
    if not snapshot_id:
        return build_error("snapshot_not_found", "snapshot_id is required for highlight.")

    locator = None
    snap = session.snapshots.get(snapshot_id)
    if not snap:
        return build_error("snapshot_not_found", f"snapshot not found: {snapshot_id}")
    meta = _resolve_ref_meta_from_snapshot(snap, ref_id)
    if not meta:
        return build_error("not_found", f"ref not found in snapshot: {ref_id}")
    candidates = _build_ref_candidates(meta)
    for _, cand in candidates:
        loc, _, _, _ = await _resolve_locator_from_ref(page, meta, cand)
        if loc is not None:
            locator = loc
            break
    if locator is None:
        return build_error("not_found", "target not found for highlight")

    await locator.evaluate(
        """
        (el, durationMs) => {
          const prevOutline = el.style.outline;
          const prevOffset = el.style.outlineOffset;
          el.style.outline = "3px solid #ff4d4f";
          el.style.outlineOffset = "2px";
          setTimeout(() => {
            el.style.outline = prevOutline;
            el.style.outlineOffset = prevOffset;
          }, durationMs);
          return true;
        }
        """,
        duration_ms,
    )
    screenshot_bytes = await page.screenshot(full_page=False)
    screenshot = base64.b64encode(screenshot_bytes).decode("utf-8")
    tab_idx = _get_tab_index(page)
    return {
        "success": True,
        "reason_code": "ok",
        "duration_ms": duration_ms,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "screenshot": screenshot,
    }


_INTERACTION_HANDLERS: Optional[Dict[str, Any]] = None


def _get_interaction_handlers() -> Dict[str, Any]:
    global _INTERACTION_HANDLERS
    if _INTERACTION_HANDLERS is None:
        _INTERACTION_HANDLERS = build_interaction_handlers(
            resolve_session_page_fn=_resolve_session_page,
            get_tab_index_fn=_get_tab_index,
            build_error_fn=build_error,
            browser_state_store_cls=BrowserStateStore,
        )
    return _INTERACTION_HANDLERS

async def _browser_dialog_arm(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _get_interaction_handlers()["dialog_arm"](params)


async def _browser_file_chooser_arm(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _get_interaction_handlers()["file_chooser_arm"](params)


async def _browser_download_wait(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _get_interaction_handlers()["download_wait"](params)


async def _browser_state(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _get_interaction_handlers()["state"](params)


async def _browser_env(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _get_interaction_handlers()["env"](params)

async def run_test_scenario(scenario: TestScenario) -> Dict[str, Any]:
    """Executes a full test scenario using Playwright."""
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")
    return await run_test_scenario_with_playwright(playwright_instance, scenario)


@app.post("/execute")
async def execute_action(request: McpRequest):
    """
    Executes a browser automation action.
    """
    return await dispatch_execute_action_route(
        request=request,
        namespace=globals(),
        close_session_fn=close_session,
        mcp_request_cls=McpRequest,
        handle_legacy_action_fn=handle_legacy_action,
        execute_simple_action_fn=execute_simple_action,
        browser_act_fn=_browser_act,
        browser_console_get_fn=_browser_console_get,
        resolve_session_page_fn=_resolve_session_page,
        browser_snapshot_fn=_browser_snapshot,
        capture_screenshot_fn=capture_screenshot,
    )


@app.post("/close_session")
async def close_session(request: McpRequest):
    """브라우저 세션을 닫고 리소스를 정리합니다."""
    session_id = request.params.get("session_id", "default")
    return await close_session_impl(active_sessions, session_id)


@app.websocket("/ws/screencast")
async def websocket_screencast(websocket: WebSocket):
    """
    WebSocket 엔드포인트: 실시간 스크린캐스트 프레임을 스트리밍합니다.
    클라이언트가 연결하면 CDP에서 전송하는 모든 프레임을 실시간으로 받습니다.
    """
    await websocket_screencast_loop(
        websocket,
        screencast_subscribers,
        lambda: current_screencast_frame,
        logger,
    )


@app.get("/")
async def root():
    return build_root_payload(
        playwright_instance=playwright_instance,
        active_sessions=active_sessions,
        screencast_subscribers=screencast_subscribers,
    )


def main() -> None:
    import uvicorn

    bind_host, bind_port = resolve_bind_host_port()
    uvicorn.run(app, host=bind_host, port=bind_port)


if __name__ == "__main__":
    main()
