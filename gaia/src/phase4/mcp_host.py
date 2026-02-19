import asyncio
import os
import base64
import uuid
import time
import hashlib
import json as json_module
import traceback
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
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

from gaia.src.phase4.observability import SessionObservability
from gaia.src.phase4.openclaw_protocol import (
    ELEMENT_ACTIONS,
    build_error,
    is_element_action,
    legacy_selector_forbidden,
)
from gaia.src.phase4.state_store import BrowserStateStore

app = FastAPI(
    title="MCP Host", description="Model Context Protocol Host for Browser Automation"
)

# 라이브 미리보기를 위한 전역 상태 (CDP 스크린캐스트용)
screencast_subscribers: List[WebSocket] = []
current_screencast_frame: Optional[str] = None


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


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
        self.observability = SessionObservability()
        self.trace_active: bool = False
        self.trace_path: str = ""
        self.dialog_listener_armed: bool = False
        self.dialog_mode: str = "dismiss"
        self.dialog_prompt_text: str = ""
        self.file_chooser_listener_armed: bool = False
        self.file_chooser_files: List[str] = []
        self.env_overrides: Dict[str, Any] = {}

    async def get_or_create_page(self) -> Page:
        """기존 페이지를 가져오거나 새 브라우저 세션을 생성합니다"""
        if not self.browser:
            if not playwright_instance:
                raise HTTPException(
                    status_code=503, detail="Playwright not initialized"
                )

            # 자동화 감지 우회 설정
            try:
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
            except Exception as exc:
                msg = str(exc)
                if "Executable doesn't exist" in msg or "browserType.launch" in msg:
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "Chromium executable not found. "
                            "Run: python -m playwright install chromium"
                        ),
                    ) from exc
                raise

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
        if self.page:
            self.observability.attach_page(self.page)
            self._ensure_dialog_listener()
            self._ensure_file_chooser_listener()
        return self.page

    def _ensure_dialog_listener(self) -> None:
        if not self.page or self.dialog_listener_armed:
            return

        async def _handle_dialog(dialog):
            payload = {
                "type": dialog.type,
                "message": dialog.message,
                "default_value": dialog.default_value,
                "mode": self.dialog_mode,
            }
            self.observability.add_dialog_event(payload)
            try:
                if self.dialog_mode == "accept":
                    await dialog.accept(self.dialog_prompt_text or "")
                else:
                    await dialog.dismiss()
            except Exception as exc:
                self.observability.add_dialog_event(
                    {
                        "type": dialog.type,
                        "mode": self.dialog_mode,
                        "error": str(exc),
                    }
                )

        def _on_dialog(dialog):
            asyncio.create_task(_handle_dialog(dialog))

        self.page.on("dialog", _on_dialog)
        self.dialog_listener_armed = True

    def _ensure_file_chooser_listener(self) -> None:
        if not self.page or self.file_chooser_listener_armed:
            return

        async def _handle_file_chooser(file_chooser):
            files = [p for p in self.file_chooser_files if p]
            if not files:
                return
            try:
                await file_chooser.set_files(files)
            except Exception as exc:
                self.observability.add_dialog_event(
                    {"type": "file_chooser", "error": str(exc), "files": files}
                )

        def _on_file_chooser(file_chooser):
            asyncio.create_task(_handle_file_chooser(file_chooser))

        self.page.on("filechooser", _on_file_chooser)
        self.file_chooser_listener_armed = True

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
async def _resolve_single_locator(page: Page, selector: str, timeout_ms: int = 1000):
    """selector가 정확히 1개 요소에 매칭될 때만 Locator를 반환합니다."""
    locator = page.locator(selector)
    count = await locator.count()
    if count == 0:
        return None, f"not_found: selector '{selector}' matched 0 elements"
    if count > 1:
        return None, f"ambiguous_selector: selector '{selector}' matched {count} elements"

    element = locator.nth(0)
    try:
        await element.wait_for(state="attached", timeout=timeout_ms)
    except Exception as e:
        print(
            f"Warning: _resolve_single_locator에서 '{selector}' 엘리먼트를 기다리는 중 오류 발생: {e}"
        )
    return element, None


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
                    element, locator_err = await _resolve_single_locator(page, selector)
                    if locator_err:
                        raise ValueError(locator_err)
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
                    elements = page.get_by_text(value, exact=False)
                    count = await elements.count()
                    if count != 1:
                        error_prefix = (
                            "ambiguous_text_target" if count > 1 else "not_found_text_target"
                        )
                        raise ValueError(
                            f"{error_prefix}: text '{value}' matched {count} elements"
                        )
                    element = elements.nth(0)
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
            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                return {"success": False, "message": locator_err}
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
                element, locator_err = await _resolve_single_locator(page, selector)
                if locator_err:
                    return {"success": False, "message": locator_err}
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
            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                return {"success": False, "message": locator_err}
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

                function isVisible(el) {
                    const style = window.getComputedStyle(el);
                    // 매우 완화된 표시 여부 검사 - iframe 내부 요소도 감지
                    // display:none과 visibility:hidden만 제외
                    return style.display !== 'none' && style.visibility !== 'hidden';
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
                    if (!isVisible(el)) return;

                    elements.push({
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
                            title: el.getAttribute('title') || ''
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'input'
                    });
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
                        dom_ref: assignDomRef(el),
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

                queryAll('[onclick], [class*="btn"], [class*="button"], [class*="cursor-pointer"]').forEach(el => {
                    if (!isVisible(el)) return;
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
                            title: el.getAttribute('title') || ''
                        },
                        bounding_box: getBoundingBox(el),
                        element_type: 'clickable'
                    });
                        }
                    }
                });

                queryAll('a[href]').forEach(el => {
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
                        dom_ref: assignDomRef(el),
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
                    details > summary
                `.replace(/\s+/g, '')).forEach(el => {
                    if (!isVisible(el)) return;
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
                    const box = getBoundingBox(el);

                    // 너무 의미 없는 wrapper 노드는 제외
                    const hasSignal =
                        !!role ||
                        !!ariaLabel ||
                        !!title ||
                        !!testid ||
                        pointerLike ||
                        (text && text.length <= 180);
                    if (!hasSignal) return;
                    if (box.width <= 0 || box.height <= 0) return;

                    elements.push({
                        tag: tag,
                        dom_ref: assignDomRef(el),
                        selector: getUniqueSelector(el),
                        text: text ? text.slice(0, 180) : '',
                        attributes: {
                            role: role,
                            'aria-label': ariaLabel,
                            title: title,
                            placeholder: el.getAttribute('placeholder') || '',
                            'aria-controls': el.getAttribute('aria-controls') || '',
                            'aria-expanded': el.getAttribute('aria-expanded') || '',
                            'aria-haspopup': el.getAttribute('aria-haspopup') || '',
                            tabindex: el.getAttribute('tabindex') || '',
                            'data-testid': testid,
                        },
                        bounding_box: box,
                        element_type: 'semantic'
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
                # 특정 요소 기준으로 가장 가까운 스크롤 컨테이너를 우선 스크롤합니다.
                element, locator_err = await _resolve_single_locator(page, selector)
                if locator_err:
                    raise ValueError(locator_err)
                try:
                    bounding_box = await element.bounding_box()
                    if bounding_box:
                        click_position = {
                            "x": bounding_box["x"] + bounding_box["width"] / 2,
                                "y": bounding_box["y"] + bounding_box["height"] / 2,
                        }
                except Exception:
                    pass
                try:
                    await _scroll_locator_container(element, value)
                except Exception:
                    # 컨테이너 스크롤이 실패하면 기존 동작으로 fallback
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
                element, locator_err = await _resolve_single_locator(page, selector)
                if locator_err:
                    raise ValueError(locator_err)
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
            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                raise ValueError(locator_err)
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
            source, source_err = await _resolve_single_locator(page, selector)
            if source_err:
                raise ValueError(source_err)
            target, target_err = await _resolve_single_locator(page, value)
            if target_err:
                raise ValueError(target_err)
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
            thumb, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                raise ValueError(locator_err)

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

            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                raise ValueError(locator_err)
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
            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                raise ValueError(locator_err)
            await element.scroll_into_view_if_needed(timeout=10000)

        elif action == "focus":
            # 요소에 포커스를 맞춥니다
            if not selector:
                raise ValueError("Selector is required for 'focus' action")
            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                raise ValueError(locator_err)
            await element.focus(timeout=30000)

        elif action == "select":
            # 드롭다운에서 옵션을 선택합니다(값에 옵션 값 포함)
            if not selector or value is None:
                raise ValueError("Selector and value required for 'select' action")
            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                raise ValueError(locator_err)

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
            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                raise ValueError(locator_err)
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

            element, locator_err = await _resolve_single_locator(page, selector)
            if locator_err:
                raise ValueError(locator_err)
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

            # 선택자가 필요한 동작 (strict single-match)
            element, locator_err = await _resolve_single_locator(page, selector, timeout_ms=5000)
            if locator_err:
                return {"success": False, "message": locator_err}

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
                        element, fallback_err = await _resolve_single_locator(page, fallback_selector, timeout_ms=5000)
                        if fallback_err:
                            raise ValueError(fallback_err)
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
                    await _reveal_locator_in_scroll_context(element)
                    await page.wait_for_timeout(150)
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
                                parent_locator_selector = (
                                    f"a:text('{parent_selector}'), button:text('{parent_selector}')"
                                )
                                parent_locator, parent_err = await _resolve_single_locator(
                                    page, parent_locator_selector, timeout_ms=5000
                                )
                                if parent_err:
                                    raise ValueError(parent_err)
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
                                        element, fb_err = await _resolve_single_locator(page, fb_selector)
                                        if fb_err:
                                            raise ValueError(fb_err)
                                        await _reveal_locator_in_scroll_context(element)
                                        await page.wait_for_timeout(150)
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
                                element, fb_err = await _resolve_single_locator(page, fb_selector)
                                if fb_err:
                                    raise ValueError(fb_err)
                                await _reveal_locator_in_scroll_context(element)
                                await page.wait_for_timeout(150)
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
                    await _reveal_locator_in_scroll_context(element)
                    await element.fill(value, timeout=10000)
                except Exception as fill_error:
                    # Fallback 시도
                    if fallback_selectors and "Timeout" in str(fill_error):
                        for fb_selector in fallback_selectors:
                            try:
                                print(
                                    f"⚠️  Original selector failed, retrying with: {fb_selector}"
                                )
                                element, fb_err = await _resolve_single_locator(page, fb_selector)
                                if fb_err:
                                    raise ValueError(fb_err)
                                await _reveal_locator_in_scroll_context(element)
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
                    await _reveal_locator_in_scroll_context(element)
                    await element.press(value, timeout=10000)
                except Exception as press_error:
                    # Fallback 시도
                    if fallback_selectors and "Timeout" in str(press_error):
                        for fb_selector in fallback_selectors:
                            try:
                                print(
                                    f"⚠️  Original selector failed, retrying with: {fb_selector}"
                                )
                                element, fb_err = await _resolve_single_locator(page, fb_selector)
                                if fb_err:
                                    raise ValueError(fb_err)
                                await _reveal_locator_in_scroll_context(element)
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


async def _resolve_locator_from_ref(page: Page, ref_meta: Dict[str, Any], _selector_hint: str):
    frame, frame_index = _select_frame_for_ref(page, ref_meta)
    dom_ref = str(ref_meta.get("dom_ref") or "").strip()
    selector_hint = str(_selector_hint or "").strip()

    async def _resolve_with_selector_hint():
        if not selector_hint:
            return None, frame_index, "", ""
        try:
            hint_group = frame.locator(selector_hint)
            hint_count = await hint_group.count()
            if hint_count <= 0:
                return None, frame_index, selector_hint, "hint_not_found"
            if hint_count == 1:
                return hint_group.nth(0), frame_index, selector_hint, ""

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
            inspect_limit = min(hint_count, 25)
            if target_cx is not None and target_cy is not None:
                for idx in range(inspect_limit):
                    candidate = hint_group.nth(idx)
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
                return hint_group.nth(best_idx), frame_index, f"{selector_hint} [hint:nth={best_idx}]", ""
            return None, frame_index, selector_hint, f"ambiguous_selector_matches:{hint_count}"
        except Exception as hint_exc:
            return None, frame_index, selector_hint, f"hint_error:{hint_exc}"

    if not dom_ref:
        fallback_locator, fallback_frame_idx, fallback_selector, fallback_error = await _resolve_with_selector_hint()
        if fallback_locator is not None:
            return fallback_locator, fallback_frame_idx, fallback_selector, ""
        return None, frame_index, selector_hint or "", "dom_ref_missing"

    try:
        selector_to_use = f'[data-gaia-dom-ref="{dom_ref}"]'
        locator_group = frame.locator(selector_to_use)
        match_count = await locator_group.count()
        if match_count <= 0:
            fallback_locator, fallback_frame_idx, fallback_selector, fallback_error = await _resolve_with_selector_hint()
            if fallback_locator is not None:
                return fallback_locator, fallback_frame_idx, fallback_selector, ""
            return None, frame_index, fallback_selector or selector_to_use, fallback_error or "not_found"
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

        fallback_locator, fallback_frame_idx, fallback_selector, fallback_error = await _resolve_with_selector_hint()
        if fallback_locator is not None:
            return fallback_locator, fallback_frame_idx, fallback_selector, ""
        return None, frame_index, fallback_selector or selector_to_use, fallback_error or f"ambiguous_selector_matches:{match_count}"
    except Exception as exc:
        fallback_locator, fallback_frame_idx, fallback_selector, fallback_error = await _resolve_with_selector_hint()
        if fallback_locator is not None:
            return fallback_locator, fallback_frame_idx, fallback_selector, ""
        return None, frame_index, fallback_selector or selector_to_use, fallback_error or str(exc)


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


async def _execute_action_on_locator(action: str, page: Page, locator, value: Any):
    if action == "click":
        await _reveal_locator_in_scroll_context(locator)
        await locator.click(timeout=8000, no_wait_after=True)
        return
    if action == "fill":
        if value is None:
            raise ValueError("fill requires value")
        await _reveal_locator_in_scroll_context(locator)
        await locator.fill(str(value), timeout=10000)
        return
    if action == "press":
        key = str(value or "Enter")
        await _reveal_locator_in_scroll_context(locator)
        await locator.press(key, timeout=8000, no_wait_after=True)
        return
    if action == "hover":
        await _reveal_locator_in_scroll_context(locator)
        await locator.hover(timeout=10000)
        return
    if action == "scroll":
        await _scroll_locator_container(locator, value)
        return
    if action == "select":
        if value is None:
            raise ValueError("select requires value")
        await _reveal_locator_in_scroll_context(locator)
        if isinstance(value, dict):
            payload = dict(value)
            if "index" in payload:
                payload["index"] = int(payload["index"])
            await locator.select_option(**payload, timeout=10000)
        else:
            await locator.select_option(str(value), timeout=10000)
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
        await locator.drag_to(target, timeout=10000)
        return
    if action == "dragSlider":
        if value is None:
            raise ValueError("dragSlider requires numeric value")
        ok = await locator.evaluate(
            """
            (el, targetValue) => {
              const num = Number(targetValue);
              if (Number.isNaN(num)) return false;
              if (el.value === undefined) return false;
              el.focus();
              el.value = String(num);
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }
            """,
            value,
        )
        if not ok:
            raise ValueError("dragSlider target is not an input-like element")
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

    try:
        max_action_seconds = float(os.getenv("GAIA_REF_ACTION_MAX_SECONDS", "45"))
    except Exception:
        max_action_seconds = 45.0
    max_action_seconds = max(10.0, min(120.0, max_action_seconds))
    action_started_at = time.monotonic()

    def _deadline_exceeded() -> bool:
        return (time.monotonic() - action_started_at) >= max_action_seconds

    attempt_logs: List[Dict[str, Any]] = []
    retry_path: List[str] = []
    stale_recovered = False
    reason_code = "unknown_error"
    last_live_texts: List[str] = []

    requested_snapshot = session.snapshots.get(snapshot_id)
    requested_meta = (
        _resolve_ref_meta_from_snapshot(requested_snapshot, ref_id)
        if requested_snapshot
        else None
    )
    initial_ref_state: Optional[str] = None
    if not requested_snapshot:
        initial_ref_state = "snapshot_not_found"
    elif requested_meta is None:
        initial_ref_state = "not_found"
    elif not str(requested_meta.get("dom_ref") or "").strip():
        initial_ref_state = "stale_snapshot"

    if initial_ref_state:
        retry_path.append(f"recover:{initial_ref_state}")
        try:
            fresh_snapshot_result = await snapshot_page(
                url=(page.url or None), session_id=session_id
            )
            fresh_snapshot_id = str(fresh_snapshot_result.get("snapshot_id") or "")
            fresh_snapshot = (
                session.snapshots.get(fresh_snapshot_id)
                if fresh_snapshot_id
                else None
            )
            recovered_meta: Optional[Dict[str, Any]] = None
            recovered_ref_id = ref_id

            if isinstance(fresh_snapshot, dict):
                recovered_meta = _resolve_ref_meta_from_snapshot(fresh_snapshot, ref_id)
                if recovered_meta is None:
                    recovered_meta = _resolve_stale_ref(requested_meta, fresh_snapshot)
                    if isinstance(recovered_meta, dict):
                        recovered_ref_id = str(
                            recovered_meta.get("ref_id") or recovered_ref_id
                        )
                if isinstance(recovered_meta, dict):
                    requested_snapshot = fresh_snapshot
                    requested_meta = recovered_meta
                    snapshot_id = fresh_snapshot_id or snapshot_id
                    ref_id = recovered_ref_id
                    stale_recovered = True
                    reason_code = "stale_ref_recovered"
                    retry_path.append("recover:ok")
        except Exception as recover_exc:
            retry_path.append(f"recover:error:{recover_exc}")

    if (
        not isinstance(requested_snapshot, dict)
        or not isinstance(requested_meta, dict)
        or not str(requested_meta.get("dom_ref") or "").strip()
    ):
        if initial_ref_state == "snapshot_not_found":
            fail_message = "snapshot을 찾을 수 없습니다. 최신 snapshot 기준으로 다시 의사결정하세요."
            fail_code = "snapshot_not_found"
        elif initial_ref_state == "not_found":
            fail_message = "snapshot 내 ref를 찾을 수 없습니다. 최신 snapshot 기준으로 다시 의사결정하세요."
            fail_code = "not_found"
        else:
            fail_message = "snapshot/ref가 stale 상태입니다. 최신 snapshot 기준으로 다시 의사결정하세요."
            fail_code = "stale_snapshot"
        return {
            "success": False,
            "effective": False,
            "reason_code": fail_code,
            "reason": fail_message,
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
    deduped: List[Tuple[str, str]] = []
    seen_selectors = set()
    for mode, cand in candidates:
        key = cand.strip()
        if not key or key in seen_selectors:
            continue
        seen_selectors.add(key)
        deduped.append((mode, cand))
    candidates = deduped[:3]
    if not candidates:
        return {
            "success": False,
            "effective": False,
            "reason_code": "not_found",
            "reason": "ref metadata에 dom_ref가 없어 요소를 찾을 수 없습니다. 최신 snapshot이 필요합니다.",
            "stale_recovered": stale_recovered,
            "retry_path": retry_path,
            "attempt_logs": attempt_logs,
        }
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
        "live_texts_after": [],
    }
    ref_attrs = requested_meta.get("attributes") if isinstance(requested_meta.get("attributes"), dict) else {}
    ref_selector_text = " ".join(
        [
            str(requested_meta.get("selector") or ""),
            str(requested_meta.get("full_selector") or ""),
            str(requested_meta.get("text") or ""),
            str((ref_attrs or {}).get("type") or ""),
            str((ref_attrs or {}).get("role") or ""),
            str((ref_attrs or {}).get("aria-label") or ""),
        ]
    ).lower()
    submit_like_click = bool(
        action == "click"
        and (
            str((ref_attrs or {}).get("type") or "").lower() == "submit"
            or "submit" in ref_selector_text
            or "로그인" in ref_selector_text
            or "회원가입" in ref_selector_text
            or "sign in" in ref_selector_text
            or "log in" in ref_selector_text
            or "sign up" in ref_selector_text
            or "register" in ref_selector_text
        )
    )
    probe_wait_schedule: Tuple[int, ...] = (250,) if submit_like_click else (350, 700, 1500)
    verify_for_action = verify and (not submit_like_click)
    if submit_like_click:
        max_action_seconds = min(max_action_seconds, 20.0)

    for attempt_idx, (mode, candidate_selector) in enumerate(candidates, start=1):
        if _deadline_exceeded():
            reason_code = "action_timeout"
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": mode,
                    "selector": candidate_selector,
                    "reason_code": reason_code,
                    "error": f"action budget exceeded ({max_action_seconds:.1f}s)",
                }
            )
            break
        retry_path.append(f"{attempt_idx}:{mode}")
        locator, frame_index, resolved_selector, locator_error = await _resolve_locator_from_ref(
            page, requested_meta, candidate_selector
        )
        if locator is None:
            locator_error_text = str(locator_error or "")
            if locator_error_text.startswith("ambiguous_selector_matches"):
                reason_code = "ambiguous_ref_target"
            elif locator_error_text in {"dom_ref_missing"}:
                reason_code = "stale_snapshot"
            else:
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
        evidence_collector = _collect_page_evidence_light if submit_like_click else _collect_page_evidence
        before_evidence = await evidence_collector(page)
        before_focus = await _read_focus_signature(page)
        before_target = await _safe_read_target_state(locator)

        try:
            await _execute_action_on_locator(action, page, locator, value)
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

        if submit_like_click:
            await page.wait_for_timeout(250)

        effective = False
        for probe_wait_ms in probe_wait_schedule:
            if _deadline_exceeded():
                reason_code = "action_timeout"
                break
            await page.wait_for_timeout(probe_wait_ms)
            after_url = page.url
            after_dom_hash = await _compute_runtime_dom_hash(page)
            after_evidence = await evidence_collector(page)
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
            live_texts_after = _extract_live_texts(after_evidence.get("live_texts"))
            if live_texts_after:
                state_change["live_texts_after"] = live_texts_after
                last_live_texts = live_texts_after
            state_change["probe_wait_ms"] = probe_wait_ms
            state_change["probe_scroll"] = "none"
            effective = bool(state_change.get("effective", True)) if verify_for_action else True
            if effective:
                break

        if verify_for_action and not effective and action in {"click", "press"}:
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
                if _deadline_exceeded():
                    reason_code = "action_timeout"
                    break
                try:
                    await page.evaluate(probe_script)
                except Exception:
                    pass
                await page.wait_for_timeout(250)
                after_url = page.url
                after_dom_hash = await _compute_runtime_dom_hash(page)
                after_evidence = await evidence_collector(page)
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
                live_texts_after = _extract_live_texts(after_evidence.get("live_texts"))
                if live_texts_after:
                    state_change["live_texts_after"] = live_texts_after
                    last_live_texts = live_texts_after
                state_change["probe_wait_ms"] = 1500
                state_change["probe_scroll"] = probe_name
                effective = bool(state_change.get("effective", True))
                if effective:
                    break

        if reason_code == "action_timeout":
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": mode,
                    "selector": resolved_selector,
                    "frame_index": frame_index,
                    "reason_code": reason_code,
                    "error": f"action budget exceeded ({max_action_seconds:.1f}s)",
                }
            )
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
                "live_texts": last_live_texts,
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
        "live_texts": last_live_texts,
        "retry_path": retry_path,
        "attempt_count": len(attempt_logs),
        "attempt_logs": attempt_logs,
        "screenshot": screenshot,
        "current_url": session.current_url,
    }


def _tab_payload(session: BrowserSession, page: Page, idx: int) -> Dict[str, Any]:
    current = session.page is page
    title = ""
    try:
        title = page.url
    except Exception:
        title = ""
    return {
        "tab_id": idx,
        "active": current,
        "url": page.url,
        "title": title,
    }


async def _resolve_session_page(session_id: str, tab_id: Optional[int] = None) -> Tuple[BrowserSession, Page]:
    if session_id not in active_sessions:
        active_sessions[session_id] = BrowserSession(session_id)
    session = active_sessions[session_id]
    page = await session.get_or_create_page()

    if tab_id is not None:
        try:
            pages = page.context.pages
            idx = int(tab_id)
            if 0 <= idx < len(pages):
                page = pages[idx]
                session.page = page
        except Exception:
            pass

    session.observability.attach_page(page)
    session._ensure_dialog_listener()
    session._ensure_file_chooser_listener()
    return session, page


def _extract_elements_by_ref(snapshot_result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = snapshot_result.get("dom_elements") or snapshot_result.get("elements") or []
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                ref_id = str(item.get("ref_id") or "")
                if ref_id:
                    out[ref_id] = item
    return out


def _element_signal_score(item: Dict[str, Any]) -> int:
    score = 0
    text = str(item.get("text") or "").strip()
    if text:
        score += min(12, len(text))
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    for key in ("aria-label", "title", "placeholder", "href"):
        if str(attrs.get(key) or "").strip():
            score += 2
    if str(item.get("element_type") or "").strip():
        score += 1
    return score


def _dedupe_elements_by_dom_ref(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    index_by_dom_ref: Dict[str, int] = {}
    for raw in elements:
        if not isinstance(raw, dict):
            continue
        dom_ref = str(raw.get("dom_ref") or "").strip()
        if not dom_ref:
            deduped.append(raw)
            continue
        prev_idx = index_by_dom_ref.get(dom_ref)
        if prev_idx is None:
            index_by_dom_ref[dom_ref] = len(deduped)
            deduped.append(raw)
            continue
        prev = deduped[prev_idx]
        if _element_signal_score(raw) > _element_signal_score(prev):
            deduped[prev_idx] = raw
    return deduped


def _element_is_interactive(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    tag = str(item.get("tag") or "").strip().lower()
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    role = str(attrs.get("role") or "").strip().lower()
    element_type = str(item.get("element_type") or "").strip().lower()
    interactive_tags = {"button", "a", "input", "select", "textarea", "option", "summary"}
    interactive_roles = {
        "button",
        "link",
        "tab",
        "menuitem",
        "checkbox",
        "radio",
        "switch",
        "combobox",
        "textbox",
        "option",
        "slider",
    }
    if tag in interactive_tags:
        return True
    if role in interactive_roles:
        return True
    if element_type in {"button", "link", "input", "checkbox", "radio", "select", "textarea", "semantic"}:
        return True
    if str(attrs.get("onclick") or "").strip():
        return True
    return False


_ROLE_INTERACTIVE = {
    "button",
    "link",
    "textbox",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "treeitem",
}

_ROLE_CONTENT = {
    "heading",
    "cell",
    "gridcell",
    "columnheader",
    "rowheader",
    "listitem",
    "article",
    "region",
    "main",
    "navigation",
}

_ROLE_STRUCTURAL = {
    "generic",
    "group",
    "list",
    "table",
    "row",
    "rowgroup",
    "grid",
    "treegrid",
    "menu",
    "menubar",
    "toolbar",
    "tablist",
    "tree",
    "directory",
    "document",
    "application",
    "presentation",
    "none",
}


def _snapshot_line_depth(line: str) -> int:
    indent = len(line) - len(line.lstrip(" "))
    return max(0, indent // 2)


def _compact_role_tree(snapshot: str) -> str:
    lines = snapshot.split("\n")
    out: List[str] = []
    for i, line in enumerate(lines):
        if "[ref=" in line:
            out.append(line)
            continue
        if ":" in line and not line.rstrip().endswith(":"):
            out.append(line)
            continue
        current_depth = _snapshot_line_depth(line)
        has_ref_child = False
        for j in range(i + 1, len(lines)):
            child_depth = _snapshot_line_depth(lines[j])
            if child_depth <= current_depth:
                break
            if "[ref=" in lines[j]:
                has_ref_child = True
                break
        if has_ref_child:
            out.append(line)
    return "\n".join(out)


def _limit_snapshot_text(snapshot: str, max_chars: int) -> tuple[str, bool]:
    limit = max(200, min(int(max_chars or 24000), 120000))
    if len(snapshot) <= limit:
        return snapshot, False
    return f"{snapshot[:limit]}\n\n[...TRUNCATED - page too large]", True


def _parse_ai_ref(suffix: str) -> Optional[str]:
    m = re.search(r"\[ref=(e\d+)\]", suffix or "", flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def _role_snapshot_stats(snapshot: str, refs: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    interactive = 0
    for item in refs.values():
        role = str((item or {}).get("role") or "").strip().lower()
        if role in _ROLE_INTERACTIVE:
            interactive += 1
    return {
        "lines": len(snapshot.split("\n")) if snapshot else 0,
        "chars": len(snapshot),
        "refs": len(refs),
        "interactive": interactive,
    }


def _build_role_snapshot_from_aria_text(
    aria_snapshot: str,
    *,
    interactive: bool,
    compact: bool,
    max_depth: Optional[int] = None,
    line_limit: int = 500,
    max_chars: int = 64000,
) -> Dict[str, Any]:
    lines = str(aria_snapshot or "").split("\n")
    refs: Dict[str, Dict[str, Any]] = {}
    refs_by_key: Dict[str, List[str]] = defaultdict(list)
    counts_by_key: Dict[str, int] = defaultdict(int)
    out: List[str] = []
    ref_counter = 0

    def _next_ref() -> str:
        nonlocal ref_counter
        ref_counter += 1
        return f"e{ref_counter}"

    for line in lines:
        depth = _snapshot_line_depth(line)
        if max_depth is not None and depth > max_depth:
            continue

        m = re.match(r'^(\s*-\s*)(\w+)(?:\s+"([^"]*)")?(.*)$', line)
        if not m:
            if not interactive:
                out.append(line)
            continue

        prefix, role_raw, name, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        if role_raw.startswith("/"):
            if not interactive:
                out.append(line)
            continue

        role = (role_raw or "").lower()
        if interactive and role not in _ROLE_INTERACTIVE:
            continue
        if compact and role in _ROLE_STRUCTURAL and not name:
            continue

        should_have_ref = role in _ROLE_INTERACTIVE or (role in _ROLE_CONTENT and bool(name))
        if not should_have_ref:
            out.append(line)
            continue

        ref = _next_ref()
        key = f"{role}:{name or ''}"
        nth = counts_by_key[key]
        counts_by_key[key] += 1
        refs_by_key[key].append(ref)

        ref_payload: Dict[str, Any] = {"role": role}
        if name:
            ref_payload["name"] = name
        if nth > 0:
            ref_payload["nth"] = nth
        refs[ref] = ref_payload

        enhanced = f"{prefix}{role_raw}"
        if name:
            enhanced += f' "{name}"'
        enhanced += f" [ref={ref}]"
        if nth > 0:
            enhanced += f" [nth={nth}]"
        if suffix:
            enhanced += suffix
        out.append(enhanced)

    duplicate_keys = {k for k, v in refs_by_key.items() if len(v) > 1}
    for ref, data in refs.items():
        key = f"{data.get('role', '')}:{data.get('name', '')}"
        if key not in duplicate_keys:
            data.pop("nth", None)

    snapshot = "\n".join(out) or "(empty)"
    if compact:
        snapshot = _compact_role_tree(snapshot)
    trimmed_lines = snapshot.split("\n")[: max(1, min(int(line_limit or 500), 5000))]
    snapshot = "\n".join(trimmed_lines)
    snapshot, truncated = _limit_snapshot_text(snapshot, max_chars=max_chars)
    return {
        "snapshot": snapshot,
        "refs": refs,
        "truncated": truncated,
        "stats": _role_snapshot_stats(snapshot, refs),
    }


def _build_role_snapshot_from_ai_text(
    ai_snapshot: str,
    *,
    interactive: bool,
    compact: bool,
    max_depth: Optional[int] = None,
    line_limit: int = 500,
    max_chars: int = 64000,
) -> Dict[str, Any]:
    lines = str(ai_snapshot or "").split("\n")
    refs: Dict[str, Dict[str, Any]] = {}
    out: List[str] = []

    for line in lines:
        depth = _snapshot_line_depth(line)
        if max_depth is not None and depth > max_depth:
            continue

        m = re.match(r'^(\s*-\s*)(\w+)(?:\s+"([^"]*)")?(.*)$', line)
        if not m:
            out.append(line)
            continue

        _, role_raw, name, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        if role_raw.startswith("/"):
            out.append(line)
            continue

        role = (role_raw or "").lower()
        if interactive and role not in _ROLE_INTERACTIVE:
            continue
        if compact and role in _ROLE_STRUCTURAL and not name:
            continue

        ref = _parse_ai_ref(suffix or "")
        if ref:
            refs[ref] = {"role": role, **({"name": name} if name else {})}
        out.append(line)

    snapshot = "\n".join(out) or "(empty)"
    if compact:
        snapshot = _compact_role_tree(snapshot)
    trimmed_lines = snapshot.split("\n")[: max(1, min(int(line_limit or 500), 5000))]
    snapshot = "\n".join(trimmed_lines)
    snapshot, truncated = _limit_snapshot_text(snapshot, max_chars=max_chars)
    return {
        "snapshot": snapshot,
        "refs": refs,
        "truncated": truncated,
        "stats": _role_snapshot_stats(snapshot, refs),
    }


def _build_role_refs_from_elements(elements: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    refs: Dict[str, Dict[str, Any]] = {}
    counts_by_key: Dict[str, int] = defaultdict(int)
    refs_by_key: Dict[str, List[str]] = defaultdict(list)

    for item in elements:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref_id") or "").strip()
        if not ref:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        role = str(attrs.get("role") or "").strip().lower()
        if not role:
            tag = str(item.get("tag") or "").strip().lower()
            if tag == "a":
                role = "link"
            elif tag in {"input", "textarea"}:
                role = "textbox"
            elif tag == "select":
                role = "combobox"
            elif tag == "button":
                role = "button"
            else:
                role = "generic"

        name = str(item.get("text") or attrs.get("aria-label") or "").strip() or None
        key = f"{role}:{name or ''}"
        nth = counts_by_key[key]
        counts_by_key[key] += 1
        refs_by_key[key].append(ref)

        payload: Dict[str, Any] = {"role": role}
        if name:
            payload["name"] = name
        if nth > 0:
            payload["nth"] = nth
        refs[ref] = payload

    duplicate_keys = {k for k, v in refs_by_key.items() if len(v) > 1}
    for ref, data in refs.items():
        key = f"{data.get('role', '')}:{data.get('name', '')}"
        if key not in duplicate_keys:
            data.pop("nth", None)
    return refs


async def _try_snapshot_for_ai(page: Page, timeout_ms: int = 5000) -> Optional[str]:
    timeout_ms = max(500, min(int(timeout_ms or 5000), 60000))

    # Playwright 내부 채널 snapshotForAI 시도 (OpenClaw parity)
    try:
        impl = getattr(page, "_impl_obj", None)
        channel = getattr(impl, "_channel", None)
        send = getattr(channel, "send", None)
        if callable(send):
            res = await send("snapshotForAI", {"timeout": timeout_ms, "track": "response"})
            if isinstance(res, dict):
                text = str(res.get("full") or "")
                if text.strip():
                    return text
    except Exception:
        pass

    # fallback: 접근성 스냅샷 문자열
    try:
        locator = page.locator(":root")
        aria_text = await locator.aria_snapshot(timeout=timeout_ms)
        if isinstance(aria_text, str) and aria_text.strip():
            return aria_text
    except Exception:
        pass
    return None


def _build_snapshot_text(
    elements: List[Dict[str, Any]],
    *,
    interactive_only: bool,
    compact: bool,
    limit: int,
    max_chars: int,
) -> Dict[str, Any]:
    lines: List[str] = []
    char_count = 0
    max_items = max(1, min(int(limit or 200), 5000))
    max_chars = max(200, min(int(max_chars or 24000), 120000))
    for idx, item in enumerate(elements):
        if not isinstance(item, dict):
            continue
        if interactive_only and not _element_is_interactive(item):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        tag = str(item.get("tag") or "").strip().lower() or "node"
        role = str(attrs.get("role") or "").strip().lower()
        ref = str(item.get("ref_id") or "").strip() or f"e{idx}"
        text = str(item.get("text") or "").strip()
        aria_label = str(attrs.get("aria-label") or "").strip()
        placeholder = str(attrs.get("placeholder") or "").strip()
        title = str(attrs.get("title") or "").strip()
        label = text or aria_label or placeholder or title
        label = re.sub(r"\s+", " ", label).strip()
        if len(label) > 140:
            label = label[:140]
        kind = role or tag
        if compact:
            if label:
                line = f"- {kind} \"{label}\" [ref={ref}]"
            else:
                line = f"- {kind} [ref={ref}]"
        else:
            line = f"- tag={tag} role={role or '-'} ref={ref}"
            if label:
                line += f" text=\"{label}\""
            if placeholder:
                line += f" placeholder=\"{placeholder[:80]}\""
        if char_count + len(line) + 1 > max_chars:
            break
        lines.append(line)
        char_count += len(line) + 1
        if len(lines) >= max_items:
            break
    return {
        "lines": lines,
        "text": "\n".join(lines),
        "stats": {
            "line_count": len(lines),
            "char_count": char_count,
            "interactive_only": bool(interactive_only),
            "compact": bool(compact),
            "limit": max_items,
            "max_chars": max_chars,
        },
    }


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
    session_id = str(params.get("session_id", "default"))
    session, page = await _resolve_session_page(session_id)
    tabs = [_tab_payload(session, p, idx) for idx, p in enumerate(page.context.pages)]
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tabs": tabs,
        "current_tab_id": _get_tab_index(page),
    }


async def _browser_snapshot(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    tab_id = params.get("tab_id")
    url = str(params.get("url") or "")
    snapshot_format = str(params.get("format") or "").strip().lower()
    mode = str(params.get("mode") or "").strip().lower()
    refs_mode = str(params.get("refs") or "ref").strip().lower()
    if refs_mode not in {"ref", "role", "aria"}:
        refs_mode = "ref"

    if mode == "efficient" and snapshot_format == "aria":
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "invalid_snapshot_options",
                "message": "mode=efficient is not allowed with format=aria",
            },
        )

    if tab_id is not None:
        session, page = await _resolve_session_page(session_id, tab_id=int(tab_id))
        if url:
            current = normalize_url(page.url)
            target = normalize_url(url)
            if current != target:
                await page.goto(url, timeout=60000)
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
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": _get_tab_index(page),
        "snapshot_id": snap.get("snapshot_id", ""),
        "epoch": int(snap.get("epoch") or 0),
        "dom_hash": str(snap.get("dom_hash") or ""),
        "mode": "ref",
        "elements": elements,
        "dom_elements": elements,
        "elements_by_ref": elements_by_ref,
        "current_url": page.url,
    }

    wants_text_snapshot = bool(snapshot_format in {"ai", "aria", "role"} or mode == "efficient")
    if wants_text_snapshot:
        interactive = bool(params.get("interactive", mode == "efficient"))
        compact = bool(params.get("compact", mode == "efficient"))
        limit = int(params.get("limit") or 700)
        max_chars = int(params.get("max_chars") or params.get("maxChars") or 64000)
        timeout_ms = int(params.get("timeout_ms") or params.get("timeoutMs") or 5000)
        max_depth_raw = params.get("max_depth", params.get("maxDepth"))
        max_depth: Optional[int] = None
        if max_depth_raw is not None and str(max_depth_raw).strip() != "":
            try:
                max_depth = max(0, int(max_depth_raw))
            except Exception:
                max_depth = None
        selector = str(params.get("selector") or "").strip()
        frame_filter = params.get("frame")
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
                    "meta": {**meta_base, "snapshot_source": "dom_elements"},
                }
            )

    return result


async def _browser_act(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    action = str(params.get("action") or "")
    url = str(params.get("url") or "")
    value = params.get("value")
    verify = bool(params.get("verify", True))
    snapshot_id = str(params.get("snapshot_id") or "")
    ref_id = str(params.get("ref_id") or "")
    selector_hint = str(params.get("selector_hint") or params.get("selector") or "")

    if not action:
        raise HTTPException(status_code=400, detail="action is required for 'browser_act'.")

    if is_element_action(action):
        if not snapshot_id or not ref_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason_code": "ref_required",
                    "message": "snapshot_id + ref_id are required for element actions",
                },
            )
        result = await execute_ref_action_with_snapshot(
            session_id=session_id,
            snapshot_id=snapshot_id,
            ref_id=ref_id,
            action=action,
            value=value,
            url=url,
            selector_hint=selector_hint,
            verify=verify,
        )
        result.setdefault("snapshot_id_used", snapshot_id)
        result.setdefault("ref_id_used", ref_id)
        result.setdefault("retry_path", [])
        result.setdefault("attempt_logs", [])
        result.setdefault("attempt_count", len(result.get("attempt_logs", [])))
        result.setdefault("state_change", {})
        return result

    session, page = await _resolve_session_page(session_id)
    if action == "wait":
        wait_ms = int(value) if isinstance(value, (int, str)) and str(value).strip() else 500
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
            "screenshot": screenshot,
        }

    legacy = await execute_simple_action(
        url=url,
        selector=str(params.get("selector") or ""),
        action=action,
        value=value,
        session_id=session_id,
        before_screenshot=None,
    )
    ok = bool(legacy.get("success"))
    reason = str(legacy.get("message") or legacy.get("reason") or "")
    reason_code = "ok" if ok else "failed"
    return {
        "success": ok,
        "effective": ok,
        "reason_code": reason_code,
        "reason": reason or ("ok" if ok else "action_failed"),
        "state_change": {"effective": ok},
        "attempt_logs": [],
        "snapshot_id_used": snapshot_id,
        "ref_id_used": ref_id,
        "retry_path": [],
        "attempt_count": 0,
        "current_url": legacy.get("current_url", page.url),
        "screenshot": legacy.get("screenshot"),
    }


async def _browser_wait(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    tab_id = params.get("tab_id")
    session, page = await _resolve_session_page(session_id, tab_id=tab_id)
    timeout_ms = int(params.get("timeout_ms") or 15000)
    selector = str(params.get("selector") or "")
    selector_state = str(params.get("selector_state") or "visible")
    js_expr = str(params.get("js") or "")
    target_url = str(params.get("url") or "")
    load_state = str(params.get("load_state") or "")
    text_contains = str(params.get("text") or "")
    text_gone = str(params.get("text_gone") or params.get("textGone") or "")
    time_ms = params.get("time_ms", params.get("timeMs"))
    if isinstance(time_ms, (int, str)) and str(time_ms).strip():
        try:
            timeout_ms = max(timeout_ms, int(time_ms))
        except Exception:
            pass

    if target_url:
        current = normalize_url(page.url)
        target = normalize_url(target_url)
        if current != target:
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
    return {
        "success": True,
        "reason_code": "ok",
        "current_url": session.current_url,
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


async def _browser_console_get(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    session, _ = await _resolve_session_page(session_id)
    limit = int(params.get("limit") or 100)
    level = str(params.get("level") or "")
    return {
        "success": True,
        "reason_code": "ok",
        "items": session.observability.get_console(limit=limit, level=level),
        "meta": {"limit": limit, "level": level},
    }


async def _browser_errors_get(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    session, _ = await _resolve_session_page(session_id)
    limit = int(params.get("limit") or 100)
    return {
        "success": True,
        "reason_code": "ok",
        "items": session.observability.get_errors(limit=limit),
        "meta": {"limit": limit},
    }


async def _browser_requests_get(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    session, _ = await _resolve_session_page(session_id)
    limit = int(params.get("limit") or 100)
    url_contains = str(params.get("url_contains") or "")
    status = params.get("status")
    status_int = int(status) if isinstance(status, (int, str)) and str(status).strip() else None
    items = session.observability.get_requests(limit=limit, url_contains=url_contains, status=status_int)
    return {
        "success": True,
        "reason_code": "ok",
        "items": items,
        "meta": {"limit": limit, "url_contains": url_contains, "status": status_int},
    }


async def _browser_response_body(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    session, _ = await _resolve_session_page(session_id)
    request_id = str(params.get("request_id") or "")
    url = str(params.get("url") or "")
    result = await session.observability.get_response_body(request_id=request_id, url=url)
    if not result.get("success"):
        return result
    return {
        "success": True,
        "reason_code": "ok",
        "item": result.get("body", {}),
        "meta": {"request_id": request_id, "url": url},
    }


async def _browser_trace_start(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    session, page = await _resolve_session_page(session_id)
    if session.trace_active:
        return {"success": True, "reason_code": "ok", "active": True, "message": "trace already active"}
    await page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
    session.trace_active = True
    return {"success": True, "reason_code": "ok", "active": True}


async def _browser_trace_stop(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    session, page = await _resolve_session_page(session_id)
    output_path = str(params.get("path") or "")
    if not output_path:
        output_path = str(
            Path.home()
            / ".gaia"
            / "traces"
            / f"{session_id}_{int(time.time())}.zip"
        )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if session.trace_active:
        await page.context.tracing.stop(path=output_path)
        session.trace_active = False
        session.trace_path = output_path
    return {"success": True, "reason_code": "ok", "active": False, "path": output_path}


async def _browser_highlight(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    session, page = await _resolve_session_page(session_id)
    selector = str(params.get("selector") or "")
    snapshot_id = str(params.get("snapshot_id") or "")
    ref_id = str(params.get("ref_id") or "")
    duration_ms = int(params.get("duration_ms") or 1200)

    locator = None
    if snapshot_id and ref_id:
        snap = session.snapshots.get(snapshot_id)
        if snap:
            meta = _resolve_ref_meta_from_snapshot(snap, ref_id)
            if meta:
                candidates = _build_ref_candidates(meta)
                for _, cand in candidates:
                    loc, _, _, _ = await _resolve_locator_from_ref(page, meta, cand)
                    if loc is not None:
                        locator = loc
                        break
    if locator is None and selector:
        locator = page.locator(selector).first
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
    return {"success": True, "reason_code": "ok", "duration_ms": duration_ms, "screenshot": screenshot}


async def _browser_dialog_arm(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    mode = str(params.get("mode") or "dismiss").strip().lower()
    if mode not in {"accept", "dismiss"}:
        return build_error("not_actionable", "mode must be accept|dismiss")
    prompt_text = str(params.get("prompt_text") or "")
    session, _ = await _resolve_session_page(session_id)
    session.dialog_mode = mode
    session.dialog_prompt_text = prompt_text
    session._ensure_dialog_listener()
    return {"success": True, "reason_code": "ok", "mode": mode}


async def _browser_file_chooser_arm(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    files = params.get("files")
    if isinstance(files, str):
        file_list = [files]
    elif isinstance(files, list):
        file_list = [str(p) for p in files if str(p).strip()]
    else:
        file_list = []
    session, _ = await _resolve_session_page(session_id)
    session.file_chooser_files = file_list
    session._ensure_file_chooser_listener()
    return {"success": True, "reason_code": "ok", "files": file_list}


async def _browser_download_wait(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    timeout_ms = int(params.get("timeout_ms") or 20000)
    path = str(params.get("path") or "")
    session, page = await _resolve_session_page(session_id)

    download = await page.wait_for_event("download", timeout=timeout_ms)
    suggested_name = download.suggested_filename
    save_path = path
    if not save_path:
        save_path = str(Path.home() / ".gaia" / "downloads" / f"{int(time.time())}_{suggested_name}")
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    await download.save_as(save_path)
    payload = {
        "url": download.url,
        "suggested_filename": suggested_name,
        "saved_path": save_path,
    }
    session.observability.add_download_event(payload)
    return {"success": True, "reason_code": "ok", "item": payload}


async def _browser_state(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    op = str(params.get("op") or "get").strip().lower()
    session, page = await _resolve_session_page(session_id)
    if op == "get":
        state = await BrowserStateStore.get_state(page)
        return {"success": True, "reason_code": "ok", "state": state}
    if op == "set":
        state_payload = params.get("state") if isinstance(params.get("state"), dict) else {}
        meta = await BrowserStateStore.set_state(page, state_payload)
        return {"success": True, "reason_code": "ok", "meta": meta}
    if op == "clear":
        clear_payload = params.get("state") if isinstance(params.get("state"), dict) else {}
        meta = await BrowserStateStore.clear_state(page, clear_payload)
        return {"success": True, "reason_code": "ok", "meta": meta}
    return build_error("not_actionable", "state op must be get|set|clear")


async def _browser_env(params: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    op = str(params.get("op") or "get").strip().lower()
    session, page = await _resolve_session_page(session_id)
    if op == "get":
        return {
            "success": True,
            "reason_code": "ok",
            "state": dict(session.env_overrides),
        }
    if op == "set":
        env_payload = params.get("env") if isinstance(params.get("env"), dict) else {}
        result = await BrowserStateStore.apply_env(page, env_payload)
        session.env_overrides.update(result.get("applied", {}))
        return {
            "success": True,
            "reason_code": "ok",
            "state": dict(session.env_overrides),
            "meta": result,
        }
    return build_error("not_actionable", "env op must be get|set")

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
                    element, step_err = await _resolve_single_locator(page, step.selector)
                    if step_err:
                        raise ValueError(step_err)
                    await element.scroll_into_view_if_needed(timeout=10000)
                    logs.append(f"SUCCESS: Scrolled '{step.selector}' into view")
                else:
                    scroll_amount = int(step.params[0]) if step.params else 500
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    logs.append(f"SUCCESS: Scrolled page by {scroll_amount}px")
                continue

            # strict single-match 정책으로 대상 요소를 선택합니다
            element, step_err = await _resolve_single_locator(page, step.selector)
            if step_err:
                raise ValueError(step_err)

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
                    matches = page.locator(selector)
                    if await matches.count() != 1:
                        continue
                    toast = matches.nth(0)
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
    try:
        action = request.action
        params = request.params
        session_id = params.get("session_id", "default")

        action_aliases = {
            "start": "browser_start",
            "install": "browser_install",
            "profiles": "browser_profiles",
            "tabs": "browser_tabs",
            "snapshot": "browser_snapshot",
            "act": "browser_act",
            "wait": "browser_wait",
            "console": "browser_console_get",
            "console_get": "browser_console_get",
            "errors": "browser_errors_get",
            "errors_get": "browser_errors_get",
            "requests": "browser_requests_get",
            "requests_get": "browser_requests_get",
            "response_body": "browser_response_body",
            "trace_start": "browser_trace_start",
            "trace_stop": "browser_trace_stop",
            "highlight": "browser_highlight",
            "dialog_arm": "browser_dialog_arm",
            "file_chooser_arm": "browser_file_chooser_arm",
            "download_wait": "browser_download_wait",
            "state": "browser_state",
            "env": "browser_env",
            "close": "browser_close",
            "browser.start": "browser_start",
            "browser.install": "browser_install",
            "browser.profiles": "browser_profiles",
            "browser.tabs": "browser_tabs",
            "browser.snapshot": "browser_snapshot",
            "browser.act": "browser_act",
            "browser.wait": "browser_wait",
            "browser.console_get": "browser_console_get",
            "browser.errors_get": "browser_errors_get",
            "browser.requests_get": "browser_requests_get",
            "browser.response_body": "browser_response_body",
            "browser.trace_start": "browser_trace_start",
            "browser.trace_stop": "browser_trace_stop",
            "browser.highlight": "browser_highlight",
            "browser.dialog_arm": "browser_dialog_arm",
            "browser.file_chooser_arm": "browser_file_chooser_arm",
            "browser.download_wait": "browser_download_wait",
            "browser.state": "browser_state",
            "browser.env": "browser_env",
            "browser.close": "browser_close",
        }
        action = action_aliases.get(action, action)

        if action == "browser_start":
            return await _browser_start(params)

        elif action == "browser_install":
            return await _browser_install(params)

        elif action == "browser_profiles":
            return await _browser_profiles(params)

        elif action == "browser_tabs":
            return await _browser_tabs(params)

        elif action == "browser_snapshot":
            return await _browser_snapshot(params)

        elif action == "browser_act":
            return await _browser_act(params)

        elif action == "browser_wait":
            return await _browser_wait(params)

        elif action == "browser_console_get":
            return await _browser_console_get(params)

        elif action == "browser_errors_get":
            return await _browser_errors_get(params)

        elif action == "browser_requests_get":
            return await _browser_requests_get(params)

        elif action == "browser_response_body":
            return await _browser_response_body(params)

        elif action == "browser_trace_start":
            return await _browser_trace_start(params)

        elif action == "browser_trace_stop":
            return await _browser_trace_stop(params)

        elif action == "browser_highlight":
            return await _browser_highlight(params)

        elif action == "browser_dialog_arm":
            return await _browser_dialog_arm(params)

        elif action == "browser_file_chooser_arm":
            return await _browser_file_chooser_arm(params)

        elif action == "browser_download_wait":
            return await _browser_download_wait(params)

        elif action == "browser_state":
            return await _browser_state(params)

        elif action == "browser_env":
            return await _browser_env(params)

        elif action == "browser_close":
            close_req = McpRequest(action="close_session", params={"session_id": session_id})
            result = await close_session(close_req)
            result.setdefault("reason_code", "ok" if result.get("success") else "not_found")
            return result

        elif action == "get_console_logs":
            level = str(params.get("type") or params.get("level") or "")
            limit = int(params.get("limit") or 100)
            data = await _browser_console_get(
                {"session_id": session_id, "level": level, "limit": limit}
            )
            return {"success": True, "logs": data.get("items", [])}

        elif action == "get_current_url":
            _, page = await _resolve_session_page(session_id)
            return {"success": True, "url": page.url}

        elif action == "analyze_page":
            url = params.get(
                "url"
            )  # 현재 페이지를 사용하려면 url을 None으로 둘 수 있습니다
            return await _browser_snapshot({"session_id": session_id, "url": url or ""})

        elif action == "snapshot_page":
            url = params.get("url")
            return await _browser_snapshot({"session_id": session_id, "url": url or ""})

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

            if legacy_selector_forbidden(action_type):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "reason_code": "ref_required",
                        "message": "selector_not_allowed_use_browser_act_ref",
                    },
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
            return await _browser_act(
                {
                    "session_id": session_id,
                    "snapshot_id": snapshot_id,
                    "ref_id": ref_id,
                    "action": action_type,
                    "value": value,
                    "url": url,
                    "selector_hint": selector_hint,
                    "verify": verify,
                }
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
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


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

    bind_host = os.getenv("MCP_HOST_BIND_HOST", "0.0.0.0")
    bind_port_raw = os.getenv("MCP_HOST_BIND_PORT")
    if bind_port_raw:
        try:
            bind_port = int(bind_port_raw)
        except ValueError:
            bind_port = 8001
    else:
        raw_url = (os.getenv("MCP_HOST_URL", "http://127.0.0.1:8001") or "").strip()
        if "://" not in raw_url:
            raw_url = f"http://{raw_url}"
        parsed = urlparse(raw_url)
        bind_port = parsed.port or 8001

    uvicorn.run(app, host=bind_host, port=bind_port)


if __name__ == "__main__":
    main()
