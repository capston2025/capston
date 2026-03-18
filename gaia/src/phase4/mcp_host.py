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
from gaia.src.phase4.mcp_interaction_runtime import (
    browser_dialog_arm as _browser_dialog_arm_impl,
    browser_download_wait as _browser_download_wait_impl,
    browser_env as _browser_env_impl,
    browser_file_chooser_arm as _browser_file_chooser_arm_impl,
    browser_state as _browser_state_impl,
    get_interaction_handlers as _get_interaction_handlers_impl,
)
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
from gaia.src.phase4.mcp_browser_tabs_runtime import (
    browser_install as _browser_install_impl,
    browser_profiles as _browser_profiles_impl,
    browser_start as _browser_start_impl,
    browser_tabs as _browser_tabs_impl,
    browser_tabs_action as _browser_tabs_action_impl,
    browser_tabs_close as _browser_tabs_close_impl,
    browser_tabs_focus as _browser_tabs_focus_impl,
    browser_tabs_open as _browser_tabs_open_impl,
)
from gaia.src.phase4.mcp_locator_runtime import (
    parse_scroll_payload as _parse_scroll_payload_impl,
    reveal_locator_in_scroll_context as _reveal_locator_in_scroll_context_impl,
    resolve_locator_from_ref as _resolve_locator_from_ref_impl,
    scroll_locator_container as _scroll_locator_container_impl,
    select_frame_for_ref as _select_frame_for_ref_impl,
    validate_upload_path as _validate_upload_path_impl,
)
from gaia.src.phase4.mcp_browser_observability_runtime import (
    browser_console_get as _browser_console_get_impl,
    browser_errors_get as _browser_errors_get_impl,
    browser_pdf as _browser_pdf_impl,
    browser_requests_get as _browser_requests_get_impl,
    browser_response_body as _browser_response_body_impl,
    browser_screenshot as _browser_screenshot_impl,
    browser_trace_start as _browser_trace_start_impl,
    browser_trace_stop as _browser_trace_stop_impl,
)
from gaia.src.phase4.mcp_browser_snapshot_runtime import browser_snapshot as _browser_snapshot_impl
from gaia.src.phase4.mcp_browser_action_runtime import browser_act as _browser_act_impl
from gaia.src.phase4.mcp_browser_wait_runtime import browser_wait as _browser_wait_impl
from gaia.src.phase4.mcp_browser_highlight_runtime import browser_highlight as _browser_highlight_impl
from gaia.src.phase4.mcp_page_evidence_runtime import (
    build_ref_candidates as _build_ref_candidates_impl,
    collect_page_evidence as _collect_page_evidence_impl,
    collect_page_evidence_light as _collect_page_evidence_light_impl,
    extract_live_texts as _extract_live_texts_impl,
    normalize_snapshot_text as _normalize_snapshot_text_impl,
    read_focus_signature as _read_focus_signature_impl,
    resolve_ref_meta_from_snapshot as _resolve_ref_meta_from_snapshot_impl,
    resolve_stale_ref as _resolve_stale_ref_impl,
    safe_read_target_state as _safe_read_target_state_impl,
    sorted_text_list as _sorted_text_list_impl,
    state_change_flags as _state_change_flags_impl,
)
from gaia.src.phase4.mcp_ref_snapshot_helpers import (
    _build_context_snapshot_from_elements,
    _build_role_refs_from_elements,
    _build_role_snapshot_from_ai_text,
    _build_role_snapshot_from_aria_text,
    _build_snapshot_text,
    _dedupe_elements_by_dom_ref,
    _element_signal_score,
    _extract_elements_by_ref,
    _try_snapshot_for_ai,
)


from gaia.src.phase4.mcp_dom_snapshot_runtime import (
    apply_selector_strategy as _apply_selector_strategy_impl,
    build_snapshot_dom_hash as _build_snapshot_dom_hash_impl,
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
    return _build_snapshot_dom_hash_impl(url, elements)

def _normalize_snapshot_text(value: Any) -> str:
    return _normalize_snapshot_text_impl(value)


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
    return await _collect_page_evidence_impl(page)


async def _collect_page_evidence_light(page: Page) -> Dict[str, Any]:
    return await _collect_page_evidence_light_impl(page)


def _sorted_text_list(value: Any) -> List[str]:
    return _sorted_text_list_impl(value)


def _extract_live_texts(value: Any, limit: int = 8) -> List[str]:
    return _extract_live_texts_impl(value, limit)


async def _read_focus_signature(page: Page) -> str:
    return await _read_focus_signature_impl(page)


async def _safe_read_target_state(locator) -> Dict[str, Any]:
    return await _safe_read_target_state_impl(locator)


def _build_ref_candidates(ref_meta: Dict[str, Any]) -> List[Tuple[str, str]]:
    return _build_ref_candidates_impl(ref_meta)


def _resolve_ref_meta_from_snapshot(
    snapshot: Dict[str, Any],
    ref_id: str,
) -> Optional[Dict[str, Any]]:
    return _resolve_ref_meta_from_snapshot_impl(snapshot, ref_id)


def _resolve_stale_ref(
    old_meta: Optional[Dict[str, Any]],
    fresh_snapshot: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    return _resolve_stale_ref_impl(old_meta, fresh_snapshot)


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
    return _state_change_flags_impl(
        action,
        value,
        before_url,
        after_url,
        before_dom_hash,
        after_dom_hash,
        before_evidence,
        after_evidence,
        before_target,
        after_target,
        before_focus,
        after_focus,
    )


def _apply_selector_strategy(elements: List[Dict[str, Any]], strategy: str) -> None:
    _apply_selector_strategy_impl(elements, strategy)


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

                function normalizeText(value) {
                    return String(value || '').replace(/\s+/g, ' ').trim();
                }

                function compactLines(value, limit = 2) {
                    const lines = String(value || '')
                        .split(/\\n+/)
                        .map((line) => normalizeText(line))
                        .filter(Boolean);
                    return lines.slice(0, limit).join(' | ');
                }

                function accessibleName(el) {
                    if (!(el instanceof HTMLElement)) return '';
                    const aria = normalizeText(el.getAttribute('aria-label'));
                    if (aria) return aria;
                    const labelledBy = normalizeText(el.getAttribute('aria-labelledby'));
                    if (labelledBy) {
                        const parts = labelledBy
                            .split(/\s+/)
                            .map((id) => document.getElementById(id))
                            .filter(Boolean)
                            .map((node) => normalizeText(node.textContent || ''))
                            .filter(Boolean);
                        if (parts.length > 0) return parts.join(' ');
                    }
                    const title = normalizeText(el.getAttribute('title'));
                    if (title) return title;
                    const placeholder = normalizeText(el.getAttribute('placeholder'));
                    if (placeholder) return placeholder;
                    return normalizeText(el.innerText || el.textContent || '');
                }

                const INTERACTIVE_SELECTOR = 'button,[role="button"],a[href],[role="link"],input[type="button"],input[type="submit"],select,textarea,input:not([type="hidden"])';
                const containerMetricsCache = new WeakMap();

                function semanticContainerCandidates(targetEl, startNode = null) {
                    const candidates = [];
                    let current = startNode instanceof Element
                        ? startNode
                        : (targetEl instanceof Element ? targetEl.parentElement : null);
                    let distance = 0;
                    while (current && current instanceof HTMLElement && distance < 10) {
                        const tag = (current.tagName || '').toLowerCase();
                        if (tag === 'body' || tag === 'html') break;
                        candidates.push({ el: current, distance });
                        current = current.parentElement;
                        distance += 1;
                    }
                    return candidates;
                }

                function semanticContainerName(candidate) {
                    if (!(candidate instanceof HTMLElement)) return '';
                    return containerName(candidate);
                }

                function semanticStructureScore(candidateEl, metrics) {
                    if (!(candidateEl instanceof HTMLElement) || !metrics) return -Infinity;
                    let score = 0;
                    if (metrics.semanticRoleScore > 0) score += 4;
                    if (metrics.semanticTagScore > 0) score += 3;
                    if (metrics.headingPresent) score += 3;
                    const explicitName = semanticContainerName(candidateEl);
                    if (explicitName) score += 2;
                    if (metrics.repeatedSiblingPattern) score += 2;
                    if (metrics.interactiveDescendants >= 2) score += 1.5;
                    else if (metrics.interactiveDescendants === 1) score += 0.75;
                    if (metrics.meaningfulTextBlock) score += 1;
                    if (metrics.areaRatio > 0.90) score -= 4;
                    else if (metrics.areaRatio > 0.75) score -= 2;
                    if (metrics.genericWrapperOnly) score -= 3;
                    return score;
                }

                function namedSemanticContainer(targetEl, startNode = null) {
                    const candidates = semanticContainerCandidates(targetEl, startNode);
                    let best = null;
                    let bestScore = -Infinity;
                    for (const candidate of candidates) {
                        const el = candidate.el;
                        const metrics = getContainerMetrics(el);
                        if (!metrics) continue;
                        const structuralScore = semanticStructureScore(el, metrics);
                        if (structuralScore < 6.0) continue;
                        const semanticScore = scoreSemanticContainer(el, targetEl, candidate.distance);
                        if (semanticScore < 4.0) continue;
                        const combinedScore = structuralScore + semanticScore;
                        if (combinedScore <= bestScore) continue;
                        best = {
                            el,
                            score: combinedScore,
                            distance: candidate.distance,
                            source: 'semantic-first',
                        };
                        bestScore = combinedScore;
                    }
                    return best;
                }

                function repeatedSiblingPattern(el) {
                    if (!(el instanceof HTMLElement) || !(el.parentElement instanceof HTMLElement)) return false;
                    const parent = el.parentElement;
                    const tag = (el.tagName || '').toLowerCase();
                    const role = normalizeText(el.getAttribute('role')).toLowerCase();
                    let similar = 0;
                    for (const child of Array.from(parent.children)) {
                        if (!(child instanceof HTMLElement)) continue;
                        const childTag = (child.tagName || '').toLowerCase();
                        const childRole = normalizeText(child.getAttribute('role')).toLowerCase();
                        if (tag && childTag === tag) {
                            similar += 1;
                            continue;
                        }
                        if (role && childRole && childRole === role) {
                            similar += 1;
                        }
                    }
                    return similar >= 3;
                }

                function getContainerMetrics(el) {
                    if (!(el instanceof HTMLElement)) return null;
                    const cached = containerMetricsCache.get(el);
                    if (cached) return cached;

                    const tag = (el.tagName || '').toLowerCase();
                    const role = normalizeText(el.getAttribute('role')).toLowerCase();
                    const classBlob = normalizeText(el.className).toLowerCase();
                    const heading = el.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
                    const headingName = heading ? accessibleName(heading) : '';
                    const textBlob = normalizeText(el.innerText || el.textContent || '');
                    const rect = el.getBoundingClientRect();
                    const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
                    const rectArea = Math.max(0, rect.width) * Math.max(0, rect.height);
                    const areaRatio = rectArea / viewportArea;
                    const interactiveDescendants = el.querySelectorAll(INTERACTIVE_SELECTOR).length;

                    const metrics = {
                        tag,
                        role,
                        headingName,
                        headingPresent: Boolean(headingName),
                        semanticRoleScore: ['listitem', 'row', 'article', 'region', 'group'].includes(role) ? 4 : 0,
                        semanticTagScore: ['li', 'tr', 'article', 'section'].includes(tag) ? 3 : 0,
                        weakClassHint: /(card|item|row|list|result|product|course|subject)/.test(classBlob),
                        repeatedSiblingPattern: repeatedSiblingPattern(el),
                        interactiveDescendants,
                        meaningfulTextBlock: textBlob.length >= 20 && textBlob.length <= 500,
                        genericWrapperOnly:
                            !['listitem', 'row', 'article', 'region', 'group'].includes(role)
                            && !['li', 'tr', 'article', 'section'].includes(tag)
                            && !headingName
                            && interactiveDescendants < 2
                            && textBlob.length < 24,
                        areaRatio,
                        textBlob,
                    };
                    containerMetricsCache.set(el, metrics);
                    return metrics;
                }

                function scoreSemanticContainer(candidate, targetEl, distance = 0) {
                    const metrics = getContainerMetrics(candidate);
                    if (!metrics) return -Infinity;
                    let score = 0;
                    score += metrics.semanticRoleScore;
                    score += metrics.semanticTagScore;
                    if (metrics.headingPresent) score += 3;
                    if (metrics.repeatedSiblingPattern) score += 2;
                    if (metrics.interactiveDescendants >= 2) score += 2;
                    else if (metrics.interactiveDescendants === 1) score += 1;
                    if (metrics.meaningfulTextBlock) score += 1;
                    if (metrics.weakClassHint) score += 0.5;
                    if (metrics.areaRatio > 0.90) score -= 3;
                    else if (metrics.areaRatio > 0.75) score -= 2;
                    else if (metrics.areaRatio > 0.55) score -= 1;
                    if (metrics.genericWrapperOnly) score -= 2;
                    score += Math.max(0, 2 - (distance * 0.35));
                    return score;
                }

                function bestSemanticContainer(targetEl, startNode = null) {
                    const semanticMatch = namedSemanticContainer(targetEl, startNode);
                    if (semanticMatch && semanticMatch.el instanceof HTMLElement) {
                        return semanticMatch;
                    }
                    const candidates = semanticContainerCandidates(targetEl, startNode);
                    let best = null;
                    let bestScore = -Infinity;
                    for (const candidate of candidates) {
                        const score = scoreSemanticContainer(candidate.el, targetEl, candidate.distance);
                        if (score > bestScore) {
                            best = candidate.el;
                            bestScore = score;
                        }
                    }
                    if (!(best instanceof HTMLElement)) return null;
                    if (bestScore < 3.0) return null;
                    return { el: best, score: bestScore, source: 'scored-fallback' };
                }

                function containerName(container) {
                    if (!(container instanceof HTMLElement)) return '';
                    const metrics = getContainerMetrics(container);
                    const headingName = metrics?.headingName || '';
                    if (headingName) return headingName;
                    const labelled = accessibleName(container);
                    if (labelled) return labelled;
                    const leadLink = container.querySelector('a[href]');
                    const leadLinkName = leadLink ? accessibleName(leadLink) : '';
                    if (leadLinkName) return leadLinkName;
                    const emphasis = container.querySelector('strong,b,[data-testid*="title"],[data-testid*="name"]');
                    const emphasisName = emphasis ? accessibleName(emphasis) : '';
                    if (emphasisName) return emphasisName;
                    return compactLines(container.innerText || container.textContent || '', 2);
                }

                function siblingActionLabels(container) {
                    if (!(container instanceof HTMLElement)) return [];
                    const labels = [];
                    const nodes = Array.from(
                        container.querySelectorAll('button,[role="button"],a[href],[role="link"],input[type="button"],input[type="submit"]')
                    );
                    for (const node of nodes) {
                        const label = accessibleName(node);
                        if (label && !labels.includes(label)) labels.push(label);
                    }
                    return labels.slice(0, 8);
                }

                function containerContextText(container) {
                    if (!(container instanceof HTMLElement)) return '';
                    const fragments = [];
                    const seen = new Set();
                    const push = (value) => {
                        const normalized = normalizeText(value);
                        if (!normalized || seen.has(normalized)) return;
                        seen.add(normalized);
                        fragments.push(normalized);
                    };

                    const metrics = getContainerMetrics(container);
                    if (metrics?.headingName) push(metrics.headingName);

                    const leadLink = container.querySelector('a[href]');
                    if (leadLink) push(accessibleName(leadLink));

                    const metaNodes = Array.from(
                        container.querySelectorAll(
                            'small,time,strong,b,[data-testid*="meta"],[data-testid*="badge"],[data-testid*="title"],[data-testid*="name"],[class*="badge"],[class*="meta"],[class*="price"],[class*="credit"],[class*="time"]'
                        )
                    );
                    for (const node of metaNodes.slice(0, 6)) {
                        push(accessibleName(node));
                    }

                    if (fragments.length < 3) {
                        const fallbackLines = String(container.innerText || container.textContent || '')
                            .split(/\\n+/)
                            .map((line) => normalizeText(line))
                            .filter(Boolean);
                        for (const line of fallbackLines) {
                            push(line);
                            if (fragments.length >= 4) break;
                        }
                    }

                    return fragments.slice(0, 4).join(' | ');
                }

                function withContext(el, attrs = {}) {
                    if (!(el instanceof HTMLElement)) return attrs;
                    const containerMatch = bestSemanticContainer(el);
                    const container = containerMatch && containerMatch.el instanceof HTMLElement ? containerMatch.el : null;
                    if (!(container instanceof HTMLElement)) return attrs;
                    const containerDomRef = assignDomRef(container);
                    const parentMatch = bestSemanticContainer(container, container.parentElement);
                    const parentContainer = parentMatch && parentMatch.el instanceof HTMLElement ? parentMatch.el : null;
                    const parentDomRef = parentContainer instanceof HTMLElement ? assignDomRef(parentContainer) : '';
                    attrs.container_name = containerName(container);
                    attrs.container_role = normalizeText(container.getAttribute('role')) || normalizeText(container.tagName).toLowerCase();
                    attrs.container_ref_id = containerDomRef;
                    attrs.container_dom_ref = containerDomRef;
                    attrs.container_parent_ref_id = parentDomRef || '';
                    attrs.container_parent_dom_ref = parentDomRef || '';
                    attrs.context_text = containerContextText(container) || compactLines(container.innerText || container.textContent || '', 3);
                    attrs.group_action_labels = siblingActionLabels(container);
                    attrs.container_source = containerMatch && containerMatch.source ? String(containerMatch.source) : '';
                    if (containerMatch && Number.isFinite(containerMatch.score)) {
                        attrs.context_score_hint = Number(containerMatch.score.toFixed(2));
                    }
                    return attrs;
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

                    withContext(el, entry.attributes);

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
                    withContext(el, elements[elements.length - 1].attributes);
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
                    withContext(el, elements[elements.length - 1].attributes);
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
                            withContext(el, elements[elements.length - 1].attributes);
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
                    withContext(el, elements[elements.length - 1].attributes);
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


async def snapshot_page(
    url: str = None,
    session_id: str = "default",
    scope_container_ref_id: str = "",
) -> Dict[str, Any]:
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
    scoped_container_ref_id = str(scope_container_ref_id or "").strip()
    if scoped_container_ref_id and isinstance(elements, list):
        scoped_elements: List[Dict[str, Any]] = []
        for elem in elements:
            if not isinstance(elem, dict):
                continue
            attrs = elem.get("attributes") if isinstance(elem.get("attributes"), dict) else {}
            elem_dom_ref = str(elem.get("dom_ref") or "").strip()
            container_ref = str(attrs.get("container_ref_id") or attrs.get("container_dom_ref") or "").strip()
            parent_container_ref = str(
                attrs.get("container_parent_ref_id") or attrs.get("container_parent_dom_ref") or ""
            ).strip()
            if (
                elem_dom_ref == scoped_container_ref_id
                or container_ref == scoped_container_ref_id
                or parent_container_ref == scoped_container_ref_id
            ):
                scoped_elements.append(elem)
        if scoped_elements:
            elements = scoped_elements
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

    role_refs = _build_role_refs_from_elements(elements)
    for elem in elements:
        if not isinstance(elem, dict):
            continue
        ref_id = str(elem.get("ref_id") or "").strip()
        attrs = elem.get("attributes") if isinstance(elem.get("attributes"), dict) else {}
        role_ref = role_refs.get(ref_id) if ref_id else None
        if not isinstance(role_ref, dict):
            continue
        elem["role_ref_role"] = role_ref.get("role")
        elem["role_ref_name"] = role_ref.get("name")
        elem["role_ref_nth"] = role_ref.get("nth")
        attrs["role_ref_role"] = role_ref.get("role")
        attrs["role_ref_name"] = role_ref.get("name")
        attrs["role_ref_nth"] = role_ref.get("nth")

    context_snapshot = _build_context_snapshot_from_elements(elements)

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
        "scope_container_ref_id": scoped_container_ref_id,
        "elements_by_ref": elements_by_ref,
        "context_snapshot": context_snapshot,
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
    result["context_snapshot"] = context_snapshot
    result["scope_container_ref_id"] = scoped_container_ref_id
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
    return _select_frame_for_ref_impl(page, ref_meta)


async def _resolve_locator_from_ref(page: Page, ref_meta: Dict[str, Any], _selector_hint: str):
    return await _resolve_locator_from_ref_impl(page, ref_meta, _selector_hint)


def _parse_scroll_payload(value: Any) -> Dict[str, Any]:
    return _parse_scroll_payload_impl(value)


async def _reveal_locator_in_scroll_context(locator) -> Dict[str, Any]:
    return await _reveal_locator_in_scroll_context_impl(locator)


async def _scroll_locator_container(locator, value: Any) -> Dict[str, Any]:
    return await _scroll_locator_container_impl(locator, value)


def _validate_upload_path(path: str) -> str:
    return _validate_upload_path_impl(path)


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


def _browser_tabs_runtime_ctx() -> Dict[str, Any]:
    return {
        "resolve_session_page": _resolve_session_page,
        "normalize_url": normalize_url,
        "get_tab_index": _get_tab_index,
        "get_page_target_id": _get_page_target_id,
        "tab_payload_async": _tab_payload_async,
        "tabs_payload_async": _tabs_payload_async,
        "resolve_page_from_tab_identifier": _resolve_page_from_tab_identifier,
        "build_error": build_error,
        "HTTPException": HTTPException,
        "active_sessions": active_sessions,
        "playwright_instance": _get_playwright_instance,
    }


def _browser_observability_runtime_ctx() -> Dict[str, Any]:
    return {
        "resolve_session_page": _resolve_session_page,
        "normalize_url": normalize_url,
        "get_tab_index": _get_tab_index,
        "build_error": build_error,
    }


def _browser_wait_runtime_ctx() -> Dict[str, Any]:
    return {
        "resolve_session_page": _resolve_session_page,
        "normalize_url": normalize_url,
        "get_tab_index": _get_tab_index,
        "build_error": build_error,
        "HTTPException": HTTPException,
    }


def _browser_highlight_runtime_ctx() -> Dict[str, Any]:
    return {
        "resolve_session_page": _resolve_session_page,
        "get_tab_index": _get_tab_index,
        "build_error": build_error,
        "resolve_ref_meta_from_snapshot": _resolve_ref_meta_from_snapshot,
        "build_ref_candidates": _build_ref_candidates,
        "resolve_locator_from_ref": _resolve_locator_from_ref,
    }


def _browser_snapshot_runtime_ctx() -> Dict[str, Any]:
    return {
        "HTTPException": HTTPException,
        "coerce_tab_id": _coerce_tab_id,
        "resolve_session_page": _resolve_session_page,
        "normalize_url": normalize_url,
        "snapshot_page": snapshot_page,
        "extract_elements_by_ref": _extract_elements_by_ref,
        "build_role_refs_from_elements": _build_role_refs_from_elements,
        "build_role_snapshot_from_aria_text": _build_role_snapshot_from_aria_text,
        "build_role_snapshot_from_ai_text": _build_role_snapshot_from_ai_text,
        "build_snapshot_text": _build_snapshot_text,
        "try_snapshot_for_ai": _try_snapshot_for_ai,
        "get_tab_index": _get_tab_index,
    }


def _browser_action_runtime_ctx() -> Dict[str, Any]:
    return {
        "HTTPException": HTTPException,
        "resolve_session_page": _resolve_session_page,
        "get_tab_index": _get_tab_index,
        "browser_tabs_close": _browser_tabs_close,
        "browser_wait": _browser_wait,
        "execute_ref_action_with_snapshot": execute_ref_action_with_snapshot,
        "execute_simple_action": execute_simple_action,
        "is_element_action": is_element_action,
    }


async def _browser_start(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_start_impl(params, _browser_tabs_runtime_ctx())


async def _browser_install(_params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_install_impl(_params, _browser_tabs_runtime_ctx())


async def _browser_profiles(_params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_profiles_impl(_params, _browser_tabs_runtime_ctx())


async def _browser_tabs(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_tabs_impl(params, _browser_tabs_runtime_ctx())


async def _browser_tabs_open(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_tabs_open_impl(params, _browser_tabs_runtime_ctx())


async def _browser_tabs_focus(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_tabs_focus_impl(params, _browser_tabs_runtime_ctx())


async def _browser_tabs_close(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_tabs_close_impl(params, _browser_tabs_runtime_ctx())


async def _browser_tabs_action(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_tabs_action_impl(params, _browser_tabs_runtime_ctx())


async def _browser_snapshot(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_snapshot_impl(params, _browser_snapshot_runtime_ctx())


async def _browser_act(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_act_impl(params, _browser_action_runtime_ctx())


async def _browser_wait(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_wait_impl(params, _browser_wait_runtime_ctx())


async def _browser_screenshot(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_screenshot_impl(params, _browser_observability_runtime_ctx())


async def _browser_pdf(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_pdf_impl(params, _browser_observability_runtime_ctx())


async def _browser_console_get(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_console_get_impl(params, _browser_observability_runtime_ctx())


async def _browser_errors_get(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_errors_get_impl(params, _browser_observability_runtime_ctx())


async def _browser_requests_get(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_requests_get_impl(params, _browser_observability_runtime_ctx())


async def _browser_response_body(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_response_body_impl(params, _browser_observability_runtime_ctx())


async def _browser_trace_start(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_trace_start_impl(params, _browser_observability_runtime_ctx())


async def _browser_trace_stop(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_trace_stop_impl(params, _browser_observability_runtime_ctx())


async def _browser_highlight(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_highlight_impl(params, _browser_highlight_runtime_ctx())


def _get_interaction_handlers() -> Dict[str, Any]:
    return _get_interaction_handlers_impl(
        resolve_session_page_fn=_resolve_session_page,
        get_tab_index_fn=_get_tab_index,
        build_error_fn=build_error,
        browser_state_store_cls=BrowserStateStore,
    )

async def _browser_dialog_arm(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_dialog_arm_impl(params, handlers=_get_interaction_handlers())


async def _browser_file_chooser_arm(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_file_chooser_arm_impl(params, handlers=_get_interaction_handlers())


async def _browser_download_wait(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_download_wait_impl(params, handlers=_get_interaction_handlers())


async def _browser_state(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_state_impl(params, handlers=_get_interaction_handlers())


async def _browser_env(params: Dict[str, Any]) -> Dict[str, Any]:
    return await _browser_env_impl(params, handlers=_get_interaction_handlers())

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
