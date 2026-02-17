import asyncio
import os
import base64
import uuid
import time
import hashlib
import json as json_module
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from playwright.async_api import (
    async_playwright,
    Playwright,
    expect,
    Browser,
    Page,
    CDPSession,
)
from typing import Dict, Any, Optional, List, Tuple

app = FastAPI(
    title="MCP Host", description="Model Context Protocol Host for Browser Automation"
)

# 라이브 미리보기를 위한 전역 상태 (CDP 스크린캐스트용)
screencast_subscribers: List[WebSocket] = []
current_screencast_frame: Optional[str] = None


# 브라우저 세션 관리
class BrowserSession:
    """상태 기반 테스트를 위해 지속적인 브라우저 세션을 유지합니다"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.current_url: str = ""
        self.cdp_session: Optional[CDPSession] = None
        self.screencast_active: bool = False
        self.stored_css_values: Dict[
            str, str
        ] = {}  # CSS 값 저장소 (storeCSSValue/expectCSSChanged용)
        self.snapshot_epoch: int = 0
        self.current_snapshot_id: str = ""
        self.current_dom_hash: str = ""
        self.snapshots: Dict[str, Dict[str, Any]] = {}

    async def get_or_create_page(self) -> Page:
        """기존 페이지를 가져오거나 새 브라우저 세션을 생성합니다"""
        if not self.browser:
            if not playwright_instance:
                raise HTTPException(
                    status_code=503, detail="Playwright not initialized"
                )

            # 자동화 감지 우회 설정
            self.browser = await playwright_instance.chromium.launch(
                headless=False,  # 사용자 개입(로그인 등)을 위해 브라우저 표시
                args=[
                    "--disable-blink-features=AutomationControlled",  # 자동화 감지 비활성화
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )

            # 페이지 생성 및 자동화 감지 우회 스크립트 주입
            self.page = await self.browser.new_page()

            # navigator.webdriver 속성 제거 및 기타 자동화 감지 우회
            await self.page.add_init_script("""
                // navigator.webdriver 제거
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => false,
                });

                // Chrome 객체 추가 (자동화 도구는 보통 없음)
                window.chrome = {
                    runtime: {},
                };

                // Permissions API 오버라이드
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // Plugin 배열 추가
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });

                // Languages 설정
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['ko-KR', 'ko', 'en-US', 'en'],
                });
            """)

            # 페이지 생성 후 바로 CDP 스크린캐스트 시작
            await self.start_screencast()
        return self.page

    async def start_screencast(self):
        """CDP 스크린캐스트를 시작합니다 - 브라우저 변경사항을 실시간 스트리밍"""
        if self.page and not self.cdp_session:
            try:
                # CDP 세션 생성
                self.cdp_session = await self.page.context.new_cdp_session(self.page)

                # 스크린캐스트 프레임 이벤트 리스너 등록
                self.cdp_session.on(
                    "Page.screencastFrame", self._handle_screencast_frame
                )

                # 스크린캐스트 시작
                await self.cdp_session.send(
                    "Page.startScreencast",
                    {
                        "format": "jpeg",
                        "quality": 80,
                        "maxWidth": 1280,
                        "maxHeight": 720,
                        "everyNthFrame": 3,  # 3프레임마다 1번 전송 (깜빡임 감소, 부하 감소)
                    },
                )

                self.screencast_active = True
                print(f"[CDP Screencast] Started for session {self.session_id}")
            except Exception as e:
                print(f"[CDP Screencast] Failed to start: {e}")

    async def _handle_screencast_frame(self, payload: Dict[str, Any]):
        """스크린캐스트 프레임을 처리하고 구독자에게 전송합니다"""
        global current_screencast_frame

        # 프레임 데이터 추출 (이미 base64 인코딩됨)
        frame_data = payload.get("data")
        session_id = payload.get("sessionId")

        if frame_data:
            # 전역 상태 업데이트
            current_screencast_frame = frame_data

            # 모든 WebSocket 구독자에게 프레임 전송
            disconnected_clients = []
            for ws in screencast_subscribers:
                try:
                    await ws.send_json(
                        {
                            "type": "screencast_frame",
                            "session_id": self.session_id,
                            "frame": frame_data,
                            "timestamp": asyncio.get_event_loop().time(),
                        }
                    )
                except Exception as e:
                    print(f"[CDP Screencast] Failed to send to subscriber: {e}")
                    disconnected_clients.append(ws)

            # 연결이 끊어진 클라이언트 제거
            for ws in disconnected_clients:
                if ws in screencast_subscribers:
                    screencast_subscribers.remove(ws)

        # CDP에 프레임 수신 확인 (다음 프레임 요청)
        if self.cdp_session and session_id:
            try:
                await self.cdp_session.send(
                    "Page.screencastFrameAck", {"sessionId": session_id}
                )
            except Exception as e:
                print(f"[CDP Screencast] Failed to ack frame: {e}")

    async def stop_screencast(self):
        """CDP 스크린캐스트를 중지합니다"""
        if self.cdp_session and self.screencast_active:
            try:
                await self.cdp_session.send("Page.stopScreencast")
                self.screencast_active = False
                print(f"[CDP Screencast] Stopped for session {self.session_id}")
            except Exception as e:
                print(f"[CDP Screencast] Failed to stop: {e}")

    async def close(self):
        """브라우저 세션을 종료합니다"""
        if self.screencast_active:
            await self.stop_screencast()

        if self.cdp_session:
            await self.cdp_session.detach()
            self.cdp_session = None

        if self.browser:
            await self.browser.close()
            self.browser = None
            self.page = None


# 활성 세션 저장소
active_sessions: Dict[str, BrowserSession] = {}


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
        "scroll_y": 0,
        "doc_height": 0,
    }


def _sorted_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized = [str(v).strip() for v in value if str(v).strip()]
    normalized.sort()
    return normalized[:100]


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
    state: Dict[str, Any] = {"visible": None, "value": None, "focused": None}
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
    return state


def _build_ref_candidates(ref_meta: Dict[str, Any]) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    full_selector = (ref_meta.get("full_selector") or "").strip()
    selector = (ref_meta.get("selector") or "").strip()
    text = (ref_meta.get("text") or "").strip()
    tag = (ref_meta.get("tag") or "").strip()

    if full_selector:
        candidates.append(("full_selector", full_selector))
    if selector:
        candidates.append(("selector", selector))
    if tag and text and len(text) <= 80:
        escaped_text = text.replace('"', "'")
        candidates.append(("text_selector", f'{tag}:has-text("{escaped_text}")'))
        candidates.append(("text_locator", f'text={escaped_text}'))

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

    old_full = _normalize_snapshot_text(old_meta.get("full_selector"))
    old_selector = _normalize_snapshot_text(old_meta.get("selector"))
    old_text = _normalize_snapshot_text(old_meta.get("text"))
    old_tag = _normalize_snapshot_text(old_meta.get("tag"))
    old_role = _normalize_snapshot_text((old_meta.get("attributes") or {}).get("role"))

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
        if score > best_score:
            best_score = score
            best_meta = meta

    if best_score < 3:
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
        "counter_changed": _sorted_text_list(before_evidence.get("counters")) != _sorted_text_list(after_evidence.get("counters")),
        "number_tokens_changed": _sorted_text_list(before_evidence.get("number_tokens")) != _sorted_text_list(after_evidence.get("number_tokens")),
        "status_text_changed": _sorted_text_list(before_evidence.get("live_texts")) != _sorted_text_list(after_evidence.get("live_texts")),
        "list_count_changed": int(before_evidence.get("list_count", 0) or 0) != int(after_evidence.get("list_count", 0) or 0),
        "interactive_count_changed": int(before_evidence.get("interactive_count", 0) or 0) != int(after_evidence.get("interactive_count", 0) or 0),
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


@app.on_event("startup")
async def startup_event():
    """서버가 시작될 때 Playwright 인스턴스를 초기화합니다."""
    global playwright_instance
    print("Initializing Playwright...")
    playwright_instance = await async_playwright().start()
    print("Playwright initialized.")


@app.on_event("shutdown")
async def shutdown_event():
    """서버가 종료될 때 Playwright 인스턴스를 중지합니다."""
    if playwright_instance:
        print("Stopping Playwright...")
        await playwright_instance.stop()
        print("Playwright stopped.")


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

                function isVisible(el) {
                    const style = window.getComputedStyle(el);
                    // 매우 완화된 표시 여부 검사 - iframe 내부 요소도 감지
                    // display:none과 visibility:hidden만 제외
                    return style.display !== 'none' && style.visibility !== 'hidden';
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

                document.querySelectorAll('input, textarea, select').forEach(el => {
                    if (!isVisible(el)) return;

                    elements.push({
                        tag: el.tagName.toLowerCase(),
                        selector: getUniqueSelector(el),
                        text: '',
                        attributes: {
                            type: el.type || 'text',
                            id: el.id || null,
                            name: el.name || null,
                            placeholder: el.placeholder || '',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || ''
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'input'
                    });
                });

                // 버튼과 상호작용 가능한 역할 요소를 수집
                // 상호작용 UI에서 자주 사용하는 ARIA 역할
                document.querySelectorAll(`
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
                    if (!isVisible(el)) return;

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
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            type: el.type || 'button',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            role: el.getAttribute('role') || ''
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'button'
                    });
                });

                document.querySelectorAll('[onclick], [class*="btn"], [class*="button"], [class*="cursor-pointer"]').forEach(el => {
                    if (!isVisible(el)) return;
                    if (el.tagName === 'BUTTON') return;
                    if (el.tagName === 'A' && el.hasAttribute('href')) return;

                    const style = window.getComputedStyle(el);
                    if (style.cursor === 'pointer' || el.onclick) {
                        const text = el.innerText?.trim() || '';
                        if (text && text.length < 100) {
                            elements.push({
                                tag: el.tagName.toLowerCase(),
                                selector: getUniqueSelector(el),
                                text: text,
                                attributes: {
                            class: el.className,
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || ''
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'clickable'
                    });
                        }
                    }
                });

                document.querySelectorAll('a[href]').forEach(el => {
                    if (!isVisible(el)) return;

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
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            href: href,
                            target: el.target || '',
                            'aria-label': el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || ''
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'link'
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

        print(f"Total found {len(all_elements)} interactive elements across all frames")
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
    if session_id not in active_sessions:
        active_sessions[session_id] = BrowserSession(session_id)

    session = active_sessions[session_id]
    page = await session.get_or_create_page()

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
            await page.goto(url, timeout=30000)
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
    elements = result.get("elements", []) if isinstance(result, dict) else []
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
    if session_id not in active_sessions:
        active_sessions[session_id] = BrowserSession(session_id)

    session = active_sessions[session_id]
    page = await session.get_or_create_page()

    # URL이 주어지고 현재 브라우저 URL과 다를 때에만 이동합니다
    if url:
        current_browser_url = page.url
        current_normalized = normalize_url(current_browser_url)
        requested_normalized = normalize_url(url)

        if current_normalized != requested_normalized:
            await page.goto(url, timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                await page.wait_for_timeout(2000)

        # session.current_url을 실제 브라우저 URL과 항상 동기화합니다
        session.current_url = page.url

    # 현재 페이지(위치와 관계없이)를 캡처합니다
    screenshot_bytes = await page.screenshot(full_page=False)
    screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    return {
        "screenshot": screenshot_base64,
        "url": page.url,
        "title": await page.title(),
    }


async def execute_simple_action(
    url: str,
    selector: str,
    action: str,
    value: str = None,
    session_id: str = "default",
    before_screenshot: str = None,
) -> Dict[str, Any]:
    """
    Execute a simple action (click, fill, press, scroll, tab) using persistent session.

    Args:
        url: Page URL
        selector: CSS selector (not used for 'tab' action)
        action: Action type (click, fill, press, scroll, tab)
        value: Value for fill/press actions, or scroll amount for scroll action
        session_id: Browser session ID (default: "default")
        before_screenshot: Base64 screenshot before action (for Vision AI fallback)

    Returns:
        Dict with success status and screenshot
    """
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    # 세션을 가져오거나 생성합니다
    if session_id not in active_sessions:
        active_sessions[session_id] = BrowserSession(session_id)

    session = active_sessions[session_id]
    page = await session.get_or_create_page()

    try:
        # URL이 변경되었고 비어 있지 않을 때에만 이동합니다
        # 캐시된 세션 URL이 아닌 실제 브라우저 URL과 비교합니다
        current_page_url = page.url
        current_normalized = normalize_url(current_page_url)
        requested_normalized = normalize_url(url) if url else None

        print(
            f"[execute_simple_action] Current page URL: {current_page_url} (normalized: {current_normalized})"
        )
        print(
            f"[execute_simple_action] Requested URL: {url} (normalized: {requested_normalized})"
        )

        if requested_normalized and current_normalized != requested_normalized:
            print(f"[execute_simple_action] URLs differ, navigating to: {url}")
            await page.goto(url, timeout=60000)  # 30초에서 60초로 증가시켰습니다
            session.current_url = url
            try:
                # 네트워크가 유휴 상태가 될 때까지 대기합니다(요청 없음)
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass  # networkidle이 타임아웃되어도 계속 진행합니다

            # React SPA가 하이드레이션/렌더링되도록 추가로 대기합니다
            # 분석 전에 DOM이 완전히 채워지도록 보장합니다
            # Figma 사이트는 해시 내비게이션에 추가 시간이 필요합니다
            await page.wait_for_timeout(
                5000
            )  # React/Figma가 렌더링되도록 5초 동안 대기합니다(해시 내비게이션을 고려해 증가)

        # 동작 전에 요소 위치를 기록합니다(클릭 애니메이션용)
        click_position = None

        # 선택자가 필요 없는 동작을 처리합니다
        if action == "tab":
            # 페이지에서 Tab 키를 누릅니다(keyboard.press는 타임아웃을 지원하지 않음)
            await page.keyboard.press("Tab")

        elif action == "scroll":
            # 페이지나 요소를 스크롤합니다
            if selector and selector != "body":
                # 특정 요소가 화면에 보이도록 스크롤합니다(선택자가 "body"가 아닐 때만)
                element = page.locator(selector).first
                try:
                    bounding_box = await element.bounding_box()
                    if bounding_box:
                        click_position = {
                            "x": bounding_box["x"] + bounding_box["width"] / 2,
                            "y": bounding_box["y"] + bounding_box["height"] / 2,
                        }
                except Exception:
                    pass
                await element.scroll_into_view_if_needed(timeout=10000)
            else:
                # 지정한 양이나 방향으로 페이지를 스크롤합니다
                if value in ["down", "up", "bottom", "top"]:
                    # 방향 기반 스크롤링
                    if value == "down":
                        scroll_amount = 800  # 800px만큼 아래로 스크롤합니다
                    elif value == "up":
                        scroll_amount = -800  # 800px만큼 위로 스크롤합니다
                    elif value == "bottom":
                        scroll_amount = 999999  # 맨 아래로 스크롤합니다
                    elif value == "top":
                        scroll_amount = -999999  # 맨 위로 스크롤합니다
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                else:
                    # 수치 기반 스크롤링
                    scroll_amount = int(value) if value else 500
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")

        elif action == "goto":
            # 값에 포함된 URL로 이동합니다
            if value is None:
                raise ValueError("Value (URL) is required for 'goto' action")
            await page.goto(value, timeout=60000, wait_until="networkidle")

        elif action == "setViewport":
            # 뷰포트 크기를 변경합니다(값은 [width, height] 또는 [[width, height]] 형식의 JSON 배열)
            if value is None:
                raise ValueError(
                    "Value [width, height] is required for 'setViewport' action"
                )
            import json

            if isinstance(value, str):
                width, height = json.loads(value)
            else:
                # [width, height]와 [[width, height]] 두 형식을 모두 처리합니다
                if isinstance(value, list) and len(value) > 0:
                    if isinstance(value[0], list):
                        # 이중 중첩 형식: [[width, height]]
                        width, height = value[0][0], value[0][1]
                    else:
                        # 단일 배열 형식: [width, height]
                        width, height = value[0], value[1]
                else:
                    raise ValueError(f"Invalid viewport value format: {value}")
            await page.set_viewport_size({"width": int(width), "height": int(height)})

        elif action == "wait" or action == "waitForTimeout":
            # 지정된 시간(밀리초) 동안 대기합니다(값에 대기 시간이 포함)
            import asyncio

            if value is None:
                raise ValueError("Value (milliseconds) is required for 'wait' action")
            wait_time_ms = (
                int(value) if isinstance(value, (int, str)) else int(value[0])
            )
            await asyncio.sleep(wait_time_ms / 1000.0)

        elif action == "clickAt" or action == "click_at_coordinates":
            # 지정한 좌표를 클릭합니다(값은 [x, y])
            if value is None:
                raise ValueError("Value [x, y] is required for 'clickAt' action")

            # 좌표를 파싱합니다
            if isinstance(value, str):
                import json

                coords = json.loads(value)
            elif isinstance(value, list):
                coords = value if len(value) == 2 else [value[0], value[1]]
            else:
                raise ValueError(f"Invalid coordinates format: {value}")

            x, y = int(coords[0]), int(coords[1])

            # 애니메이션을 위해 클릭 위치를 저장합니다
            click_position = {"x": x, "y": y}

            # React 이벤트가 정확히 발생하도록 자바스크립트로 좌표를 클릭합니다
            # 해당 좌표의 요소를 찾아 프로그래밍 방식으로 클릭합니다
            try:
                await page.evaluate(f"""
                    (async () => {{
                        const element = document.elementFromPoint({x}, {y});
                        if (element) {{
                            element.click();
                            return true;
                        }}
                        return false;
                    }})();
                """)
            except Exception as e:
                # 자바스크립트 클릭이 실패하면 마우스 클릭으로 대체합니다
                print(
                    f"JS click failed at ({x}, {y}), falling back to mouse.click: {e}"
                )
                await page.mouse.click(x, y)

        elif action == "fillAt" or action == "fill_at_coordinates":
            # 좌표 기반 입력 (값은 {x, y, text} 또는 [x, y, text])
            if value is None:
                raise ValueError("Value {x, y, text} is required for 'fillAt' action")

            if isinstance(value, str):
                import json

                coords = json.loads(value)
            else:
                coords = value

            if isinstance(coords, dict):
                x = coords.get("x")
                y = coords.get("y")
                text = coords.get("text") or coords.get("value")
            elif isinstance(coords, list) and len(coords) >= 3:
                x, y, text = coords[0], coords[1], coords[2]
            else:
                raise ValueError(f"Invalid fillAt value format: {value}")

            if x is None or y is None or text is None:
                raise ValueError("fillAt requires x, y, and text")

            x, y = int(x), int(y)

            # 좌표 위치의 요소에 값 주입 + 이벤트 발생
            filled = await page.evaluate(
                """
                ({ x, y, text }) => {
                  const element = document.elementFromPoint(x, y);
                  if (!element) return false;

                  const tag = element.tagName.toLowerCase();
                  const isEditable = element.isContentEditable;
                  if (tag === 'input' || tag === 'textarea') {
                    element.focus();
                    element.value = text;
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                  }
                  if (isEditable) {
                    element.focus();
                    element.textContent = text;
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    return true;
                  }
                  return false;
                }
                """,
                {"x": x, "y": y, "text": str(text)},
            )

            if not filled:
                raise ValueError("No editable element found at coordinates")

        elif action == "evaluate":
            # 자바스크립트를 실행합니다(값에 스크립트 포함)
            if value is None:
                raise ValueError("Value (script) is required for 'evaluate' action")
            if selector:
                # 특정 요소에서 평가합니다
                element = page.locator(selector).first
                eval_result = await element.evaluate(value)
            else:
                # 페이지에서 평가합니다
                eval_result = await page.evaluate(value)

            # 평가 결과를 스크린샷과 함께 반환합니다
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return {
                "success": True,
                "message": "JavaScript evaluation completed",
                "result": eval_result,
                "screenshot": screenshot_base64,
            }

        elif action == "hover":
            # 요소 위에 호버합니다
            if not selector:
                raise ValueError("Selector is required for 'hover' action")
            element = page.locator(selector).first
            try:
                bounding_box = await element.bounding_box()
                if bounding_box:
                    click_position = {
                        "x": bounding_box["x"] + bounding_box["width"] / 2,
                        "y": bounding_box["y"] + bounding_box["height"] / 2,
                    }
            except Exception:
                pass
            await element.hover(timeout=30000)

        elif action == "dragAndDrop":
            # 드래그 앤 드롭을 수행합니다(값에 대상 선택자 포함)
            if not selector or not value:
                raise ValueError(
                    "Both selector and value (target) required for 'dragAndDrop' action"
                )
            source = page.locator(selector).first
            target = page.locator(value).first
            await source.drag_to(target, timeout=30000)

        elif action == "dragSlider":
            # Radix UI 슬라이더를 특정 값으로 드래그합니다
            # value는 목표 값 (예: "1000")
            if not selector:
                raise ValueError("Selector is required for 'dragSlider' action")
            if value is None:
                raise ValueError(
                    "Value (target value) is required for 'dragSlider' action"
                )

            # 슬라이더 thumb 요소 찾기
            thumb = page.locator(selector).first

            try:
                # 슬라이더의 aria 속성에서 범위 정보 가져오기
                aria_min = await thumb.get_attribute("aria-valuemin") or "0"
                aria_max = await thumb.get_attribute("aria-valuemax") or "100"
                aria_now = await thumb.get_attribute("aria-valuenow") or "0"

                min_val = float(aria_min)
                max_val = float(aria_max)
                target_val = float(value)

                print(
                    f"🎚️ Slider: min={min_val}, max={max_val}, current={aria_now}, target={target_val}"
                )

                # 방법 1: 키보드로 슬라이더 조작 (가장 안정적)
                # End 키로 최댓값, Home 키로 최솟값
                if target_val >= max_val:
                    await thumb.focus()
                    await thumb.press("End")
                    print(f"🎚️ Pressed End key to move slider to max value")
                elif target_val <= min_val:
                    await thumb.focus()
                    await thumb.press("Home")
                    print(f"🎚️ Pressed Home key to move slider to min value")
                else:
                    # 중간 값으로 이동: JavaScript로 직접 값 설정
                    await thumb.focus()

                    # Radix 슬라이더는 aria-valuenow로 현재 값을 추적
                    # 키보드로 한 스텝씩 이동하거나, 드래그로 위치 조정
                    # 여기서는 비율 계산 후 드래그 사용

                    # 슬라이더 트랙 찾기 (thumb의 부모 요소)
                    track_box = await thumb.evaluate("""el => {
                        const track = el.closest('[data-slot="slider"]')?.querySelector('[data-slot="slider-track"]');
                        if (track) {
                            const rect = track.getBoundingClientRect();
                            return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                        }
                        return null;
                    }""")

                    if track_box:
                        # 목표 위치 계산
                        ratio = (target_val - min_val) / (max_val - min_val)
                        target_x = track_box["x"] + (track_box["width"] * ratio)
                        target_y = track_box["y"] + track_box["height"] / 2

                        # thumb의 현재 위치
                        thumb_box = await thumb.bounding_box()
                        if thumb_box:
                            start_x = thumb_box["x"] + thumb_box["width"] / 2
                            start_y = thumb_box["y"] + thumb_box["height"] / 2

                            # 드래그 실행
                            await page.mouse.move(start_x, start_y)
                            await page.mouse.down()
                            await page.mouse.move(target_x, target_y, steps=10)
                            await page.mouse.up()

                            print(
                                f"🎚️ Dragged slider from ({start_x:.0f}, {start_y:.0f}) to ({target_x:.0f}, {target_y:.0f})"
                            )
                    else:
                        # 트랙을 찾지 못하면 키보드로 이동
                        # 현재 값에서 목표 값까지의 스텝 수 계산
                        current_val = float(aria_now)
                        steps = int(abs(target_val - current_val))
                        key = "ArrowRight" if target_val > current_val else "ArrowLeft"

                        for _ in range(min(steps, 100)):  # 최대 100번
                            await thumb.press(key)

                        print(f"🎚️ Pressed {key} {min(steps, 100)} times")

                # 값 변경 후 잠시 대기
                await page.wait_for_timeout(300)

                # 클릭 위치 저장 (애니메이션용)
                thumb_box = await thumb.bounding_box()
                if thumb_box:
                    click_position = {
                        "x": thumb_box["x"] + thumb_box["width"] / 2,
                        "y": thumb_box["y"] + thumb_box["height"] / 2,
                    }

            except Exception as slider_error:
                print(f"❌ Slider drag failed: {slider_error}")
                raise ValueError(f"Failed to drag slider: {str(slider_error)}")

        elif action == "storeCSSValue":
            # CSS 값을 저장합니다 (나중에 expectCSSChanged로 비교)
            # value는 CSS 속성명 (예: "background-color", "opacity")
            if not selector:
                raise ValueError("Selector is required for 'storeCSSValue' action")
            if value is None:
                raise ValueError(
                    "Value (CSS property name) is required for 'storeCSSValue' action"
                )

            element = page.locator(selector).first
            css_property = value if isinstance(value, str) else value[0]

            # CSS 값 가져오기
            css_value = await element.evaluate(f'''el => {{
                const style = window.getComputedStyle(el);
                return style.getPropertyValue("{css_property}");
            }}''')

            # 세션에 저장 (selector + property를 키로 사용)
            storage_key = f"{selector}::{css_property}"
            session.stored_css_values[storage_key] = css_value

            print(f"💾 Stored CSS value: {storage_key} = {css_value}")

            # 클릭 위치 저장 (애니메이션용)
            try:
                bounding_box = await element.bounding_box()
                if bounding_box:
                    click_position = {
                        "x": bounding_box["x"] + bounding_box["width"] / 2,
                        "y": bounding_box["y"] + bounding_box["height"] / 2,
                    }
            except Exception:
                pass

        elif action == "scrollIntoView":
            # 요소가 화면에 보이도록 스크롤합니다
            if not selector:
                raise ValueError("Selector is required for 'scrollIntoView' action")
            element = page.locator(selector).first
            await element.scroll_into_view_if_needed(timeout=10000)

        elif action == "focus":
            # 요소에 포커스를 맞춥니다
            if not selector:
                raise ValueError("Selector is required for 'focus' action")
            element = page.locator(selector).first
            await element.focus(timeout=30000)

        elif action == "select":
            # 드롭다운에서 옵션을 선택합니다(값에 옵션 값 포함)
            if not selector or value is None:
                raise ValueError("Selector and value required for 'select' action")
            element = page.locator(selector).first

            # 옵션 값 확인 후 유효하지 않으면 첫 번째 옵션으로 대체
            options = await element.evaluate(
                """
                (el) => Array.from(el.options || []).map((opt) => opt.value)
                """
            )
            if not options:
                raise ValueError("No options found for select element")

            if value not in options:
                value = options[0]

            await element.select_option(value, timeout=30000)

        elif action == "uploadFile":
            # 파일을 업로드합니다 (input[type='file']에 파일 경로 설정)
            if not selector or value is None:
                raise ValueError(
                    "Selector and file path required for 'uploadFile' action"
                )
            element = page.locator(selector).first
            # value는 파일 경로 문자열 또는 파일 경로 리스트
            if isinstance(value, str):
                await element.set_input_files(value, timeout=30000)
            elif isinstance(value, list):
                await element.set_input_files(value, timeout=30000)
            else:
                raise ValueError(f"Invalid value type for uploadFile: {type(value)}")

        elif action == "expectCSSChanged":
            # 저장된 CSS 값과 현재 값을 비교하여 변경 여부 확인
            if not selector:
                raise ValueError("Selector is required for 'expectCSSChanged' action")
            if value is None:
                raise ValueError(
                    "Value (CSS property name) is required for 'expectCSSChanged' action"
                )

            element = page.locator(selector).first
            css_property = value if isinstance(value, str) else value[0]

            # 현재 CSS 값 가져오기
            current_css_value = await element.evaluate(f'''el => {{
                const style = window.getComputedStyle(el);
                return style.getPropertyValue("{css_property}");
            }}''')

            # 저장된 값과 비교
            storage_key = f"{selector}::{css_property}"
            stored_value = session.stored_css_values.get(storage_key)

            if stored_value is None:
                # 저장된 값이 없으면 실패
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                return {
                    "success": False,
                    "message": f"No stored CSS value for '{storage_key}'. Use storeCSSValue first.",
                    "screenshot": screenshot_base64,
                }

            # 값이 변경되었는지 확인
            changed = stored_value != current_css_value
            print(f"🔍 CSS comparison: {storage_key}")
            print(f"   Before: {stored_value}")
            print(f"   After:  {current_css_value}")
            print(f"   Changed: {changed}")

            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            if changed:
                return {
                    "success": True,
                    "message": f"CSS '{css_property}' changed from '{stored_value}' to '{current_css_value}'",
                    "screenshot": screenshot_base64,
                }
            else:
                return {
                    "success": False,
                    "message": f"CSS '{css_property}' did not change (still '{current_css_value}')",
                    "screenshot": screenshot_base64,
                }

        elif action in (
            "expectVisible",
            "expectHidden",
            "expectTrue",
            "expectText",
            "expectAttribute",
            "expectCountAtLeast",
        ):
            # 검증 동작은 결과를 반환하는 방식으로 처리됩니다
            # 이 동작은 실행되지 않고 검증 결과만 반환합니다
            result = await _execute_assertion(
                page, action, selector, value, before_screenshot=before_screenshot
            )

            # 검증 결과용 스크린샷을 캡처합니다
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            return {
                "success": result["success"],
                "message": result["message"],
                "screenshot": screenshot_base64,
            }

        elif action in ("click", "fill", "press"):
            # :has-text() 실패 시 :text()로 자동 재시도 (fallback)
            # [type="submit"] 실패 시 제거해서 재시도 (fallback)
            # [role="switch"]:has-text() → 부모 컨테이너로 탐색 (토글 스위치 특수 처리)
            fallback_selectors = []

            # 토글 스위치 특수 처리: [role="switch"]:has-text("XXX") 패턴 감지
            if '[role="switch"]' in selector and ":has-text(" in selector:
                import re

                # :has-text("텍스트") 추출
                text_match = re.search(r':has-text\(["\']([^"\']+)["\']\)', selector)
                if text_match:
                    text = text_match.group(1)
                    # 토글 스위치는 보통 label과 함께 있으므로 부모 컨테이너에서 찾기
                    fallback_selectors.append(
                        f'.flex:has(label:has-text("{text}")) button[role="switch"]'
                    )
                    fallback_selectors.append(
                        f'div:has(label:has-text("{text}")) button[role="switch"]'
                    )

            if ":has-text(" in selector:
                fallback_selectors.append(selector.replace(":has-text(", ":text("))
            if '[type="submit"]' in selector:
                fallback_selectors.append(selector.replace('[type="submit"]', ""))
            if '[type="submit"]' in selector and ":has-text(" in selector:
                # 둘 다 제거한 버전도 추가
                fallback_selectors.append(
                    selector.replace('[type="submit"]', "").replace(
                        ":has-text(", ":text("
                    )
                )

            fallback_selector = fallback_selectors[0] if fallback_selectors else None

            # 선택자가 필요한 동작
            element = page.locator(selector).first

            # 클릭 애니메이션을 위해 요소 위치를 구합니다
            click_position = None
            try:
                bounding_box = await element.bounding_box(timeout=5000)
                if bounding_box:
                    click_position = {
                        "x": bounding_box["x"] + bounding_box["width"] / 2,
                        "y": bounding_box["y"] + bounding_box["height"] / 2,
                    }
            except Exception:
                # bounding_box 실패 시 fallback 시도
                if fallback_selector:
                    try:
                        element = page.locator(fallback_selector).first
                        bounding_box = await element.bounding_box(timeout=5000)
                        if bounding_box:
                            click_position = {
                                "x": bounding_box["x"] + bounding_box["width"] / 2,
                                "y": bounding_box["y"] + bounding_box["height"] / 2,
                            }
                            print(f"⚠️  :has-text() failed, using :text() instead")
                    except Exception:
                        pass

            if action == "click":
                # Scroll element into view before clicking to prevent timeout issues
                try:
                    await element.evaluate(
                        "el => el.scrollIntoView({ behavior: 'smooth', block: 'center' })"
                    )
                    await page.wait_for_timeout(500)  # Wait for scroll animation
                except Exception as scroll_error:
                    print(
                        f"Warning: Could not scroll element into view: {scroll_error}"
                    )

                # For switch/toggle elements, use JavaScript click for reliability
                # Playwright's click() sometimes doesn't trigger onChange handlers properly
                use_js_click = any(
                    pattern in selector
                    for pattern in [
                        "[data-slot='switch']",
                        "[role='switch']",
                        "switch",
                        "toggle",
                    ]
                )

                try:
                    if use_js_click:
                        print(f"🔧 Using JavaScript click for switch/toggle element")
                        await element.evaluate("el => el.click()")
                        await page.wait_for_timeout(300)  # Wait for state change
                    else:
                        await element.click(timeout=10000)
                except Exception as click_error:
                    # Retry with force click for overlay/intercept issues
                    try:
                        if not use_js_click:
                            print("⚠️  click failed, retrying with force=True")
                            await element.click(timeout=5000, force=True)
                            await page.wait_for_timeout(300)
                            screenshot_bytes = await page.screenshot(full_page=False)
                            screenshot_base64 = base64.b64encode(
                                screenshot_bytes
                            ).decode("utf-8")
                            return {
                                "success": True,
                                "message": "Click action completed with force",
                                "screenshot": screenshot_base64,
                            }
                    except Exception:
                        pass

                    # Final fallback to JS click
                    try:
                        await element.evaluate("el => el.click()")
                        await page.wait_for_timeout(300)
                        screenshot_bytes = await page.screenshot(full_page=False)
                        screenshot_base64 = base64.b64encode(screenshot_bytes).decode(
                            "utf-8"
                        )
                        return {
                            "success": True,
                            "message": "Click action completed via JS fallback",
                            "screenshot": screenshot_base64,
                        }
                    except Exception:
                        raise click_error
                    error_msg = str(click_error)

                    # "element is not visible" 에러 감지 시 부모 hover 시도
                    if (
                        "element is not visible" in error_msg
                        or "not visible" in error_msg
                    ):
                        print(
                            f"⚠️  Element not visible, trying to hover parent menu first..."
                        )
                        try:
                            # JavaScript로 부모 셀렉터 찾기
                            parent_selector = await element.evaluate("""
                                el => {
                                    // 부모 요소 찾기 (li > a 구조에서 li, nav, 또는 부모 링크)
                                    let parent = el.parentElement;
                                    while (parent && parent !== document.body) {
                                        const tagName = parent.tagName.toLowerCase();
                                        const role = parent.getAttribute('role');
                                        const className = parent.className || '';

                                        // 네비게이션 메뉴 아이템 찾기
                                        if (tagName === 'li' || role === 'menuitem') {
                                            // li 내부의 최상위 링크/버튼 찾기
                                            const topLink = parent.querySelector(':scope > a, :scope > button');
                                            if (topLink && topLink !== el) {
                                                return topLink.textContent.trim();
                                            }
                                        }

                                        parent = parent.parentElement;
                                    }
                                    return null;
                                }
                            """)

                            if parent_selector:
                                print(f"🎯 Found parent menu: {parent_selector}")
                                # Playwright의 실제 hover() 사용
                                parent_locator = page.locator(
                                    f"a:text('{parent_selector}'), button:text('{parent_selector}')"
                                ).first
                                await parent_locator.hover(timeout=5000)
                                print(f"✅ Hovered parent menu, waiting for submenu...")
                                await page.wait_for_timeout(
                                    1000
                                )  # 서브메뉴 나타날 시간 증가

                                # 다시 클릭 시도
                                await element.click(timeout=10000)
                                print(f"✅ Successfully clicked after hovering parent")
                            else:
                                print(f"⚠️  No suitable parent found for hovering")
                                raise click_error
                        except Exception as hover_error:
                            print(f"⚠️  Parent hover failed: {hover_error}")
                            # 부모 hover 실패 시 원래 fallback 로직 계속
                            if fallback_selectors and "Timeout" in error_msg:
                                for fb_selector in fallback_selectors:
                                    try:
                                        print(
                                            f"⚠️  Original selector failed, retrying with: {fb_selector}"
                                        )
                                        element = page.locator(fb_selector).first
                                        await element.evaluate(
                                            "el => el.scrollIntoView({ behavior: 'smooth', block: 'center' })"
                                        )
                                        await page.wait_for_timeout(500)
                                        await element.click(timeout=10000)
                                        break  # 성공하면 루프 종료
                                    except Exception:
                                        continue  # 다음 fallback 시도
                                else:
                                    # 모든 fallback 실패
                                    raise click_error
                            else:
                                raise click_error
                    # Fallback 시도: :has-text() → :text(), [type="submit"] 제거 등
                    elif fallback_selectors and "Timeout" in error_msg:
                        for fb_selector in fallback_selectors:
                            try:
                                print(
                                    f"⚠️  Original selector failed, retrying with: {fb_selector}"
                                )
                                element = page.locator(fb_selector).first
                                await element.evaluate(
                                    "el => el.scrollIntoView({ behavior: 'smooth', block: 'center' })"
                                )
                                await page.wait_for_timeout(500)
                                await element.click(timeout=10000)
                                break  # 성공하면 루프 종료
                            except Exception:
                                continue  # 다음 fallback 시도
                        else:
                            # 모든 fallback 실패
                            raise click_error
                    else:
                        raise
            elif action == "fill":
                if value is None:
                    raise ValueError("Value is required for 'fill' action")
                try:
                    await element.fill(value, timeout=10000)
                except Exception as fill_error:
                    # Fallback 시도
                    if fallback_selectors and "Timeout" in str(fill_error):
                        for fb_selector in fallback_selectors:
                            try:
                                print(
                                    f"⚠️  Original selector failed, retrying with: {fb_selector}"
                                )
                                element = page.locator(fb_selector).first
                                await element.fill(value, timeout=10000)
                                break
                            except Exception:
                                continue
                        else:
                            raise fill_error
                    else:
                        raise
            elif action == "press":
                if value is None:
                    raise ValueError("Value is required for 'press' action")
                try:
                    await element.press(value, timeout=10000)
                except Exception as press_error:
                    # Fallback 시도
                    if fallback_selectors and "Timeout" in str(press_error):
                        for fb_selector in fallback_selectors:
                            try:
                                print(
                                    f"⚠️  Original selector failed, retrying with: {fb_selector}"
                                )
                                element = page.locator(fb_selector).first
                                await element.press(value, timeout=10000)
                                break
                            except Exception:
                                continue
                        else:
                            raise press_error
                    else:
                        raise

        else:
            raise ValueError(f"Unsupported action: {action}")

        # 상태 변경을 기다립니다 (CLICK on button[type="submit"]일 때만)
        # 폼 입력 중간에는 네비게이션 대기하지 않음 (홈페이지로 튕기는 문제 방지)
        if action == "click" and "submit" in selector.lower():
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                await page.wait_for_timeout(1500)
        else:
            # 폼 입력/일반 클릭은 짧게만 대기
            await page.wait_for_timeout(300)

        # 내비게이션이 발생하면 현재 URL을 업데이트합니다
        session.current_url = page.url

        # 실시간 미리보기용으로 동작 후 스크린샷을 캡처합니다
        screenshot_bytes = await page.screenshot(full_page=False)
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        return {
            "success": True,
            "message": f"Action '{action}' executed on '{selector if selector else 'page'}'",
            "screenshot": screenshot_base64,
            "current_url": session.current_url,
            "click_position": click_position,  # 애니메이션용 클릭 위치를 추가합니다
        }

    except Exception as e:
        return {"success": False, "message": f"Action failed: {str(e)}"}

    # 브라우저를 닫지 말고 세션을 유지합니다!


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


async def _resolve_locator_from_ref(page: Page, ref_meta: Dict[str, Any], selector_hint: str):
    frame, frame_index = _select_frame_for_ref(page, ref_meta)
    selector_to_use = selector_hint.strip()
    if not selector_to_use:
        return None, frame_index, "", "empty_selector"

    if " >>> " in selector_to_use:
        _, selector_to_use = _split_full_selector(selector_to_use)
        selector_to_use = selector_to_use.strip()

    try:
        locator = frame.locator(selector_to_use).first
        await locator.count()
        return locator, frame_index, selector_to_use, ""
    except Exception as exc:
        return None, frame_index, selector_to_use, str(exc)


async def _execute_action_on_locator(action: str, locator, value: Any):
    if action == "click":
        await locator.evaluate("el => el.scrollIntoView({ behavior: 'smooth', block: 'center' })")
        await locator.click(timeout=10000)
        return
    if action == "fill":
        if value is None:
            raise ValueError("fill requires value")
        await locator.fill(str(value), timeout=10000)
        return
    if action == "press":
        key = str(value or "Enter")
        await locator.press(key, timeout=10000)
        return
    if action == "hover":
        await locator.hover(timeout=10000)
        return
    raise ValueError(f"Unsupported ref action: {action}")


async def execute_ref_action_with_snapshot(
    *,
    session_id: str,
    snapshot_id: str,
    ref_id: str,
    action: str,
    value: Any = None,
    url: str = "",
    selector_hint: str = "",
    verify: bool = True,
) -> Dict[str, Any]:
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    if session_id not in active_sessions:
        active_sessions[session_id] = BrowserSession(session_id)
    session = active_sessions[session_id]
    page = await session.get_or_create_page()

    if url:
        current_normalized = normalize_url(page.url)
        requested_normalized = normalize_url(url)
        if current_normalized != requested_normalized:
            await page.goto(url, timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(1000)

    attempt_logs: List[Dict[str, Any]] = []
    retry_path: List[str] = []
    stale_recovered = False
    reason_code = "unknown_error"

    requested_snapshot = session.snapshots.get(snapshot_id)
    requested_meta = (
        _resolve_ref_meta_from_snapshot(requested_snapshot, ref_id)
        if requested_snapshot
        else None
    )
    if not requested_snapshot:
        reason_code = "snapshot_not_found"
    elif session.current_snapshot_id and session.current_snapshot_id != snapshot_id:
        reason_code = "stale_snapshot"

    if reason_code in {"snapshot_not_found", "stale_snapshot"} or requested_meta is None:
        fresh = await snapshot_page(session_id=session_id)
        fresh_snapshot = session.snapshots.get(fresh.get("snapshot_id", ""))
        recovered_meta = _resolve_stale_ref(requested_meta, fresh_snapshot or {})
        if recovered_meta is not None:
            stale_recovered = True
            snapshot_id = fresh.get("snapshot_id", snapshot_id)
            ref_id = recovered_meta.get("ref_id", ref_id)
            requested_snapshot = fresh_snapshot
            requested_meta = recovered_meta
            reason_code = "stale_ref_recovered"
            print(f"[execute_ref_action] stale recovered: old_ref={ref_id} snapshot={snapshot_id}")
        elif requested_meta is None and fresh_snapshot:
            direct_meta = _resolve_ref_meta_from_snapshot(fresh_snapshot, ref_id)
            if direct_meta is not None:
                stale_recovered = True
                snapshot_id = fresh.get("snapshot_id", snapshot_id)
                requested_snapshot = fresh_snapshot
                requested_meta = direct_meta
                reason_code = "stale_ref_recovered"
            else:
                return {
                    "success": False,
                    "effective": False,
                    "reason_code": "not_found",
                    "reason": "ref_id를 최신 snapshot에서 찾지 못했습니다.",
                    "stale_recovered": stale_recovered,
                    "retry_path": retry_path,
                    "attempt_logs": attempt_logs,
                }
        else:
            return {
                "success": False,
                "effective": False,
                "reason_code": "not_found",
                "reason": "ref_id를 snapshot에서 찾지 못했습니다.",
                "stale_recovered": stale_recovered,
                "retry_path": retry_path,
                "attempt_logs": attempt_logs,
            }

    if not isinstance(requested_meta, dict):
        return {
            "success": False,
            "effective": False,
            "reason_code": "not_found",
            "reason": "유효한 ref metadata가 없습니다.",
            "stale_recovered": stale_recovered,
            "retry_path": retry_path,
            "attempt_logs": attempt_logs,
        }

    scope = requested_meta.get("scope", {}) if isinstance(requested_meta.get("scope"), dict) else {}
    current_tab_index = _get_tab_index(page)
    ref_tab_index = scope.get("tab_index")
    if ref_tab_index is not None:
        try:
            if int(ref_tab_index) != current_tab_index:
                return {
                    "success": False,
                    "effective": False,
                    "reason_code": "tab_scope_mismatch",
                    "reason": f"ref tab scope mismatch: ref={ref_tab_index}, current={current_tab_index}",
                    "stale_recovered": stale_recovered,
                    "retry_path": retry_path,
                    "attempt_logs": attempt_logs,
                }
        except Exception:
            pass

    ref_frame_index = int(scope.get("frame_index", requested_meta.get("frame_index", 0)) or 0)
    if ref_frame_index < 0 or ref_frame_index >= len(page.frames):
        return {
            "success": False,
            "effective": False,
            "reason_code": "frame_scope_mismatch",
            "reason": f"ref frame scope mismatch: ref={ref_frame_index}, frame_count={len(page.frames)}",
            "stale_recovered": stale_recovered,
            "retry_path": retry_path,
            "attempt_logs": attempt_logs,
        }

    candidates = _build_ref_candidates(requested_meta)
    hint = selector_hint.strip()
    if hint:
        candidates.insert(0, ("hint", hint))
    deduped: List[Tuple[str, str]] = []
    seen_selectors = set()
    for mode, cand in candidates:
        key = cand.strip()
        if not key or key in seen_selectors:
            continue
        seen_selectors.add(key)
        deduped.append((mode, cand))
    candidates = deduped[:3]
    transport_success = True
    locator_found = False
    interaction_success = False
    state_change = {
        "url_changed": False,
        "dom_changed": False,
        "target_visibility_changed": False,
        "target_value_changed": False,
        "target_value_matches": False,
        "target_focus_changed": False,
        "focus_changed": False,
        "counter_changed": False,
        "number_tokens_changed": False,
        "status_text_changed": False,
        "list_count_changed": False,
        "interactive_count_changed": False,
        "auth_state_changed": False,
        "text_digest_changed": False,
        "evidence_changed": False,
        "probe_wait_ms": 0,
        "probe_scroll": "none",
    }

    for attempt_idx, (mode, candidate_selector) in enumerate(candidates, start=1):
        retry_path.append(f"{attempt_idx}:{mode}")
        locator, frame_index, resolved_selector, locator_error = await _resolve_locator_from_ref(
            page, requested_meta, candidate_selector
        )
        if locator is None:
            reason_code = "not_found"
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": mode,
                    "selector": resolved_selector,
                    "reason_code": reason_code,
                    "error": locator_error,
                }
            )
            print(f"[execute_ref_action] step={attempt_idx} mode={mode} reason={reason_code}")
            continue

        locator_found = True
        before_url = page.url
        before_dom_hash = await _compute_runtime_dom_hash(page)
        before_evidence = await _collect_page_evidence(page)
        before_focus = await _read_focus_signature(page)
        before_target = await _safe_read_target_state(locator)

        try:
            await _execute_action_on_locator(action, locator, value)
            interaction_success = True
        except Exception as action_exc:
            reason_code = "not_actionable"
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": mode,
                    "selector": resolved_selector,
                    "frame_index": frame_index,
                    "reason_code": reason_code,
                    "error": str(action_exc),
                }
            )
            print(f"[execute_ref_action] step={attempt_idx} mode={mode} reason={reason_code}")
            continue

        effective = False
        for probe_wait_ms in (350, 700, 1500):
            await page.wait_for_timeout(probe_wait_ms)
            after_url = page.url
            after_dom_hash = await _compute_runtime_dom_hash(page)
            after_evidence = await _collect_page_evidence(page)
            after_focus = await _read_focus_signature(page)
            after_target = await _safe_read_target_state(locator)
            state_change = _state_change_flags(
                action=action,
                value=value,
                before_url=before_url,
                after_url=after_url,
                before_dom_hash=before_dom_hash,
                after_dom_hash=after_dom_hash,
                before_evidence=before_evidence,
                after_evidence=after_evidence,
                before_target=before_target,
                after_target=after_target,
                before_focus=before_focus,
                after_focus=after_focus,
            )
            state_change["probe_wait_ms"] = probe_wait_ms
            state_change["probe_scroll"] = "none"
            effective = bool(state_change.get("effective", True)) if verify else True
            if effective:
                break

        if verify and not effective and action in {"click", "press"}:
            scroll_probes: List[Tuple[str, str]] = [
                ("top", "window.scrollTo(0, 0)"),
                (
                    "mid",
                    "window.scrollTo(0, Math.max(0, Math.floor(((document.documentElement && document.documentElement.scrollHeight) || 0) * 0.5)))",
                ),
                (
                    "bottom",
                    "window.scrollTo(0, Math.max(0, ((document.documentElement && document.documentElement.scrollHeight) || 0)))",
                ),
            ]
            for probe_name, probe_script in scroll_probes:
                try:
                    await page.evaluate(probe_script)
                except Exception:
                    pass
                await page.wait_for_timeout(250)
                after_url = page.url
                after_dom_hash = await _compute_runtime_dom_hash(page)
                after_evidence = await _collect_page_evidence(page)
                after_focus = await _read_focus_signature(page)
                after_target = await _safe_read_target_state(locator)
                state_change = _state_change_flags(
                    action=action,
                    value=value,
                    before_url=before_url,
                    after_url=after_url,
                    before_dom_hash=before_dom_hash,
                    after_dom_hash=after_dom_hash,
                    before_evidence=before_evidence,
                    after_evidence=after_evidence,
                    before_target=before_target,
                    after_target=after_target,
                    before_focus=before_focus,
                    after_focus=after_focus,
                )
                state_change["probe_wait_ms"] = 1500
                state_change["probe_scroll"] = probe_name
                effective = bool(state_change.get("effective", True))
                if effective:
                    break

        reason_code = "ok" if effective else "no_state_change"
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": mode,
                "selector": resolved_selector,
                "frame_index": frame_index,
                "reason_code": reason_code,
                "state_change": state_change,
            }
        )
        print(f"[execute_ref_action] step={attempt_idx} mode={mode} reason={reason_code}")
        if effective:
            session.current_url = page.url
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return {
                "success": True,
                "effective": True,
                "reason_code": "ok",
                "reason": "ref action executed and state changed",
                "snapshot_id_used": snapshot_id,
                "ref_id_used": ref_id,
                "stale_recovered": stale_recovered,
                "transport_success": transport_success,
                "locator_found": locator_found,
                "interaction_success": interaction_success,
                "state_change": state_change,
                "retry_path": retry_path,
                "attempt_count": len(attempt_logs),
                "attempt_logs": attempt_logs,
                "screenshot": screenshot_base64,
                "current_url": session.current_url,
            }

    screenshot = None
    try:
        screenshot_bytes = await page.screenshot(full_page=False)
        screenshot = base64.b64encode(screenshot_bytes).decode("utf-8")
    except Exception:
        screenshot = None

    session.current_url = page.url
    return {
        "success": False,
        "effective": False,
        "reason_code": reason_code if reason_code != "unknown_error" else "failed",
        "reason": "ref action failed or no state change",
        "snapshot_id_used": snapshot_id,
        "ref_id_used": ref_id,
        "stale_recovered": stale_recovered,
        "transport_success": transport_success,
        "locator_found": locator_found,
        "interaction_success": interaction_success,
        "state_change": state_change,
        "retry_path": retry_path,
        "attempt_count": len(attempt_logs),
        "attempt_logs": attempt_logs,
        "screenshot": screenshot,
        "current_url": session.current_url,
    }


async def run_test_scenario(scenario: TestScenario) -> Dict[str, Any]:
    """
    Executes a full test scenario using Playwright.
    Enhanced with network monitoring and advanced assertions.
    """
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    logs = []
    network_requests = []

    # 자동화 감지 우회 설정
    browser = await playwright_instance.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    page = await browser.new_page()

    # 자동화 감지 우회 스크립트 주입
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false,
        });
        window.chrome = { runtime: {} };
    """)

    # 네트워크 요청/응답 리스너
    import time

    async def log_request(request):
        network_requests.append(
            {"method": request.method, "url": request.url, "timestamp": time.time()}
        )

    async def log_response(response):
        for req in network_requests:
            if req["url"] == response.url and "status" not in req:
                req["status"] = response.status
                req["response_time"] = time.time()
                req["duration_ms"] = int(
                    (req["response_time"] - req["timestamp"]) * 1000
                )
                try:
                    if response.headers.get("content-type", "").startswith(
                        "application/json"
                    ):
                        req["response_body"] = await response.json()
                except:
                    pass
                break

    page.on("request", lambda request: asyncio.create_task(log_request(request)))
    page.on("response", lambda response: asyncio.create_task(log_response(response)))

    try:
        # 첫 단계로 지정된 초기 내비게이션을 처리합니다
        if scenario.steps and scenario.steps[0].action == "goto":
            step = scenario.steps.pop(0)
            url = step.params[0] if step.params else "about:blank"
            await page.goto(url, timeout=30000)
            logs.append(f"SUCCESS: Navigated to {url}")

        # 나머지 단계를 실행합니다
        for step in scenario.steps:
            logs.append(f"Executing step: {step.description}")

            # 'note' 동작(문서화/검증 단계)을 건너뜁니다
            if step.action == "note" or step.action == "":
                logs.append(f"NOTE: {step.description}")
                continue

            # 선택자가 필요 없는 동작을 처리합니다
            if step.action == "tab":
                await page.keyboard.press(
                    "Tab"
                )  # keyboard.press는 타임아웃을 지원하지 않습니다
                logs.append(f"SUCCESS: Tab key pressed")
                continue
            elif step.action == "scroll":
                if step.selector:
                    element = page.locator(step.selector).first
                    await element.scroll_into_view_if_needed(timeout=10000)
                    logs.append(f"SUCCESS: Scrolled '{step.selector}' into view")
                else:
                    scroll_amount = int(step.params[0]) if step.params else 500
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    logs.append(f"SUCCESS: Scrolled page by {scroll_amount}px")
                continue

            # 여러 매치를 처리하기 위해 .first를 사용합니다(엄격 모드 위반 방지)
            element = page.locator(step.selector).first

            if step.action == "click":
                await element.click(timeout=10000)
            elif step.action == "fill":
                await element.fill(str(step.params[0]), timeout=10000)
            elif step.action == "press":
                await element.press(str(step.params[0]), timeout=10000)
            else:
                raise ValueError(f"Unsupported action: {step.action}")
            logs.append(f"SUCCESS: {step.action} on '{step.selector}'")

        # 검증을 실행합니다
        logs.append(f"Executing assertion: {scenario.assertion.description}")
        assertion = scenario.assertion

        # 'note' 검증(문서용)만 건너뜁니다
        if assertion.condition == "note" or assertion.condition == "":
            logs.append(f"NOTE: {assertion.description}")
            logs.append(f"SUCCESS: All assertions passed.")
            return {
                "status": "success",
                "logs": logs,
                "network_requests": network_requests,
            }

        element = page.locator(assertion.selector)

        if assertion.condition == "is_visible":
            await expect(element).to_be_visible(timeout=10000)
        elif assertion.condition == "contains_text":
            await expect(element).to_contain_text(
                str(assertion.params[0]), timeout=10000
            )
        elif assertion.condition == "url_contains":
            await expect(page).to_have_url(
                lambda url: str(assertion.params[0]) in url, timeout=10000
            )

        # 🆕 Advanced assertions
        elif assertion.condition == "network_request":
            # 네트워크 요청 검증
            method = assertion.params[0] if len(assertion.params) > 0 else "POST"
            url_pattern = assertion.params[1] if len(assertion.params) > 1 else ""
            expected_status = assertion.params[2] if len(assertion.params) > 2 else 200

            matching_requests = [
                req
                for req in network_requests
                if req["method"] == method and url_pattern in req["url"]
            ]

            if not matching_requests:
                raise AssertionError(
                    f"No {method} request to URL containing '{url_pattern}'"
                )

            if matching_requests[-1].get("status") != expected_status:
                raise AssertionError(
                    f"Request status {matching_requests[-1].get('status')} != {expected_status}"
                )

            logs.append(
                f"SUCCESS: Network request validated - {method} {url_pattern} → {expected_status}"
            )

        elif assertion.condition == "element_count":
            # 요소 개수 검증
            expected_count = int(assertion.params[0])
            actual_count = await element.count()
            if actual_count != expected_count:
                raise AssertionError(
                    f"Expected {expected_count} elements, found {actual_count}"
                )
            logs.append(f"SUCCESS: Element count = {expected_count}")

        elif assertion.condition == "toast_visible":
            # 토스트 메시지 검증 (일반적인 selector들)
            toast_selectors = [
                '[role="alert"]',
                ".toast",
                ".notification",
                '[class*="toast"]',
                '[class*="snackbar"]',
            ]
            expected_text = assertion.params[0] if assertion.params else ""

            toast_found = False
            for selector in toast_selectors:
                try:
                    toast = page.locator(selector).first
                    await expect(toast).to_be_visible(timeout=2000)
                    if expected_text:
                        await expect(toast).to_contain_text(expected_text)
                    toast_found = True
                    logs.append(
                        f"SUCCESS: Toast/notification visible with text '{expected_text}'"
                    )
                    break
                except:
                    continue

            if not toast_found:
                raise AssertionError(
                    f"No toast/notification found with text '{expected_text}'"
                )

        elif assertion.condition == "api_response_contains":
            # API 응답 내용 검증
            url_pattern = assertion.params[0] if len(assertion.params) > 0 else ""
            expected_key = assertion.params[1] if len(assertion.params) > 1 else ""
            expected_value = assertion.params[2] if len(assertion.params) > 2 else None

            matching_requests = [
                req
                for req in network_requests
                if url_pattern in req["url"] and "response_body" in req
            ]

            if not matching_requests:
                raise AssertionError(
                    f"No API response found for URL containing '{url_pattern}'"
                )

            response_body = matching_requests[-1]["response_body"]
            if expected_key not in response_body:
                raise AssertionError(f"Response missing key '{expected_key}'")

            if (
                expected_value is not None
                and response_body[expected_key] != expected_value
            ):
                raise AssertionError(
                    f"Response[{expected_key}] = {response_body[expected_key]}, expected {expected_value}"
                )

            logs.append(
                f"SUCCESS: API response validated - {expected_key} = {response_body.get(expected_key)}"
            )

        elif assertion.condition == "response_time_under":
            # API 응답 시간 검증
            url_pattern = assertion.params[0] if len(assertion.params) > 0 else ""
            max_duration_ms = (
                int(assertion.params[1]) if len(assertion.params) > 1 else 1000
            )

            matching_requests = [
                req
                for req in network_requests
                if url_pattern in req["url"] and "duration_ms" in req
            ]

            if not matching_requests:
                raise AssertionError(
                    f"No API response found for URL containing '{url_pattern}'"
                )

            actual_duration = matching_requests[-1]["duration_ms"]
            if actual_duration > max_duration_ms:
                raise AssertionError(
                    f"API response time {actual_duration}ms exceeds limit {max_duration_ms}ms"
                )

            logs.append(
                f"SUCCESS: API response time {actual_duration}ms < {max_duration_ms}ms"
            )

        else:
            raise ValueError(f"Unsupported condition: {assertion.condition}")

        logs.append(f"SUCCESS: All assertions passed.")
        return {
            "status": "success",
            "logs": logs,
            "network_requests": network_requests,  # 디버깅용
        }

    except Exception as e:
        error_message = f"ERROR: {type(e).__name__} - {str(e)}"
        logs.append(error_message)
        print(f"Test scenario failed: {error_message}")
        return {"status": "failed", "logs": logs, "error": error_message}
    finally:
        await browser.close()


@app.post("/execute")
async def execute_action(request: McpRequest):
    """
    Executes a browser automation action.
    """
    action = request.action
    params = request.params
    session_id = params.get("session_id", "default")

    if action == "analyze_page":
        url = params.get(
            "url"
        )  # 현재 페이지를 사용하려면 url을 None으로 둘 수 있습니다
        return await analyze_page(url, session_id)

    elif action == "snapshot_page":
        url = params.get("url")
        return await snapshot_page(url, session_id)

    elif action == "capture_screenshot":
        url = params.get(
            "url"
        )  # 현재 페이지를 사용하려면 url을 None으로 둘 수 있습니다
        return await capture_screenshot(url, session_id)

    elif action == "execute_action":
        # 전체 시나리오 없이 단순 동작(클릭, 입력, 키 입력)을 실행합니다
        url = params.get("url")
        selector = params.get(
            "selector", ""
        )  # 일부 동작은 선택자가 비어 있을 수 있습니다
        action_type = params.get("action")
        value = params.get("value")
        before_screenshot = params.get("before_screenshot")  # Vision AI용 이전 스크린샷

        # goto, setViewport, evaluate, tab, scroll, wait, waitForTimeout, clickAt, click_at_coordinates 같은 동작은 선택자가 필요 없습니다
        # 검증 동작도 선택자가 필요 없으며 value 매개변수를 사용합니다
        actions_not_needing_selector = [
            "goto",
            "setViewport",
            "evaluate",
            "tab",
            "scroll",
            "wait",
            "waitForTimeout",
            "clickAt",
            "click_at_coordinates",
            "fillAt",
            "fill_at_coordinates",
            "expectTrue",
            "expectAttribute",
            "expectCountAtLeast",
            "expectVisible",
            "expectHidden",
        ]

        if not action_type:
            raise HTTPException(
                status_code=400, detail="action is required for 'execute_action'."
            )

        if action_type not in actions_not_needing_selector and not selector:
            raise HTTPException(
                status_code=400,
                detail=f"selector is required for action '{action_type}'.",
            )

        return await execute_simple_action(
            url,
            selector,
            action_type,
            value,
            session_id,
            before_screenshot=before_screenshot,
        )

    elif action == "execute_ref_action":
        snapshot_id = params.get("snapshot_id", "")
        ref_id = params.get("ref_id", "")
        action_type = params.get("action", "")
        value = params.get("value")
        url = params.get("url", "")
        selector_hint = str(params.get("selector_hint", "") or "")
        verify = bool(params.get("verify", True))

        if not snapshot_id:
            raise HTTPException(
                status_code=400, detail="snapshot_id is required for 'execute_ref_action'."
            )
        if not ref_id:
            raise HTTPException(
                status_code=400, detail="ref_id is required for 'execute_ref_action'."
            )
        if not action_type:
            raise HTTPException(
                status_code=400, detail="action is required for 'execute_ref_action'."
            )
        return await execute_ref_action_with_snapshot(
            session_id=session_id,
            snapshot_id=snapshot_id,
            ref_id=ref_id,
            action=action_type,
            value=value,
            url=url,
            selector_hint=selector_hint,
            verify=verify,
        )

    elif action == "execute_scenario":
        scenario_data = params.get("scenario")
        if not scenario_data:
            raise HTTPException(
                status_code=400, detail="Scenario is required for 'execute_scenario'."
            )

        try:
            scenario = TestScenario(**scenario_data)
            result = await run_test_scenario(scenario)
            return result
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid scenario format: {e}")

    raise HTTPException(status_code=400, detail=f"Action '{action}' not supported.")


@app.post("/close_session")
async def close_session(request: McpRequest):
    """브라우저 세션을 닫고 리소스를 정리합니다."""
    session_id = request.params.get("session_id", "default")

    if session_id in active_sessions:
        session = active_sessions[session_id]
        await session.close()
        del active_sessions[session_id]
        return {"success": True, "message": f"Session '{session_id}' closed"}

    return {"success": False, "message": f"Session '{session_id}' not found"}


@app.websocket("/ws/screencast")
async def websocket_screencast(websocket: WebSocket):
    """
    WebSocket 엔드포인트: 실시간 스크린캐스트 프레임을 스트리밍합니다.
    클라이언트가 연결하면 CDP에서 전송하는 모든 프레임을 실시간으로 받습니다.
    """
    await websocket.accept()
    screencast_subscribers.append(websocket)
    print(
        f"[WebSocket] New screencast subscriber connected (total: {len(screencast_subscribers)})"
    )

    try:
        # 연결 유지 - 클라이언트가 메시지를 보내거나 연결이 끊어질 때까지 대기
        while True:
            # 클라이언트로부터 메시지를 받습니다 (ping/pong 등)
            data = await websocket.receive_text()

            # 클라이언트가 요청하면 현재 프레임을 즉시 전송
            if data == "get_current_frame" and current_screencast_frame:
                await websocket.send_json(
                    {
                        "type": "screencast_frame",
                        "frame": current_screencast_frame,
                        "timestamp": asyncio.get_event_loop().time(),
                    }
                )

    except WebSocketDisconnect:
        print(f"[WebSocket] Screencast subscriber disconnected")
    except Exception as e:
        print(f"[WebSocket] Error: {e}")
    finally:
        if websocket in screencast_subscribers:
            screencast_subscribers.remove(websocket)
        print(f"[WebSocket] Subscriber removed (total: {len(screencast_subscribers)})")


@app.get("/")
async def root():
    return {
        "message": "MCP Host is running.",
        "active_sessions": len(active_sessions),
        "screencast_subscribers": len(screencast_subscribers),
        "screencast_active": any(s.screencast_active for s in active_sessions.values()),
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
