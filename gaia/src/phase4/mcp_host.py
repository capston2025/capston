import asyncio
import base64
import uuid
import json as json_module
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Playwright, expect, Browser, Page, CDPSession
from typing import Dict, Any, Optional, List

app = FastAPI(title="MCP Host", description="Model Context Protocol Host for Browser Automation")

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
        self.stored_css_values: Dict[str, str] = {}  # CSS 값 저장소 (storeCSSValue/expectCSSChanged용)

    async def get_or_create_page(self) -> Page:
        """기존 페이지를 가져오거나 새 브라우저 세션을 생성합니다"""
        if not self.browser:
            if not playwright_instance:
                raise HTTPException(status_code=503, detail="Playwright not initialized")

            # 자동화 감지 우회 설정
            self.browser = await playwright_instance.chromium.launch(
                headless=False,  # 사용자 개입(로그인 등)을 위해 브라우저 표시
                args=[
                    '--disable-blink-features=AutomationControlled',  # 자동화 감지 비활성화
                    '--disable-dev-shm-usage',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                ]
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
                self.cdp_session.on('Page.screencastFrame', self._handle_screencast_frame)

                # 스크린캐스트 시작
                await self.cdp_session.send('Page.startScreencast', {
                    'format': 'jpeg',
                    'quality': 80,
                    'maxWidth': 1280,
                    'maxHeight': 720,
                    'everyNthFrame': 3  # 3프레임마다 1번 전송 (깜빡임 감소, 부하 감소)
                })

                self.screencast_active = True
                print(f"[CDP Screencast] Started for session {self.session_id}")
            except Exception as e:
                print(f"[CDP Screencast] Failed to start: {e}")

    async def _handle_screencast_frame(self, payload: Dict[str, Any]):
        """스크린캐스트 프레임을 처리하고 구독자에게 전송합니다"""
        global current_screencast_frame

        # 프레임 데이터 추출 (이미 base64 인코딩됨)
        frame_data = payload.get('data')
        session_id = payload.get('sessionId')

        if frame_data:
            # 전역 상태 업데이트
            current_screencast_frame = frame_data

            # 모든 WebSocket 구독자에게 프레임 전송
            disconnected_clients = []
            for ws in screencast_subscribers:
                try:
                    await ws.send_json({
                        'type': 'screencast_frame',
                        'session_id': self.session_id,
                        'frame': frame_data,
                        'timestamp': asyncio.get_event_loop().time()
                    })
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
                await self.cdp_session.send('Page.screencastFrameAck', {'sessionId': session_id})
            except Exception as e:
                print(f"[CDP Screencast] Failed to ack frame: {e}")

    async def stop_screencast(self):
        """CDP 스크린캐스트를 중지합니다"""
        if self.cdp_session and self.screencast_active:
            try:
                await self.cdp_session.send('Page.stopScreencast')
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
async def _execute_assertion(page: Page, action: str, selector: str, value: Any, before_screenshot: str = None) -> Dict[str, Any]:
    """검증 작업을 수행하고 결과를 반환합니다 (하이브리드: DOM + Vision)"""
    try:
        if action == "expectVisible":
            # 요소가 보이는지 확인합니다
            if not selector and not value:
                return {"success": False, "message": "Selector or text value required for expectVisible"}

            # Phase 1: DOM 기반 검증 시도 (빠름 ~100ms)
            dom_success = False
            dom_error = None

            try:
                if selector:
                    # Case A: selector로 찾기
                    element = page.locator(selector).first
                    await element.wait_for(state="visible", timeout=500)  # 짧은 타임아웃
                    return {"success": True, "method": "dom_selector", "message": f"Element {selector} is visible"}
                else:
                    # Case B: 텍스트로 찾기
                    element = page.get_by_text(value, exact=False).first
                    await element.wait_for(state="visible", timeout=500)  # 짧은 타임아웃
                    return {"success": True, "method": "dom_text", "message": f"Text '{value}' is visible"}
            except Exception as e:
                dom_error = str(e)
                # DOM으로 못 찾음 → Vision으로 fallback

            # Phase 2: Vision AI Fallback (느림 ~2s, 하지만 더 정확)
            if before_screenshot:
                print(f"⚠️ DOM check failed ({dom_error[:50]}...), trying Vision AI verification...")

                # After 스크린샷 캡처
                after_screenshot_bytes = await page.screenshot(full_page=False)
                after_screenshot = base64.b64encode(after_screenshot_bytes).decode("utf-8")

                # Vision AI로 검증 (LLMVisionClient 사용)
                try:
                    from gaia.src.phase4.llm_vision_client import LLMVisionClient

                    llm_client = LLMVisionClient()
                    vision_result = llm_client.verify_action_result(
                        expected_result=value or f"Element {selector} is visible",
                        before_screenshot=before_screenshot,
                        after_screenshot=after_screenshot,
                        url=str(page.url)
                    )

                    # Debug: Print Vision AI response
                    print(f"🔍 Vision AI Result:")
                    print(f"   - Success: {vision_result.get('success')}")
                    print(f"   - Confidence: {vision_result.get('confidence', 0)}")
                    print(f"   - Reasoning: {vision_result.get('reasoning', 'N/A')}")

                    if vision_result.get("success") and vision_result.get("confidence", 0) > 70:
                        return {
                            "success": True,
                            "method": "vision_ai",
                            "confidence": vision_result["confidence"],
                            "reasoning": vision_result["reasoning"],
                            "message": f"Vision AI verified: {value}"
                        }
                    else:
                        return {
                            "success": False,
                            "method": "vision_ai_failed",
                            "confidence": vision_result.get("confidence", 0),
                            "reasoning": vision_result.get("reasoning", "Unknown"),
                            "dom_error": dom_error,
                            "message": f"Both DOM and Vision failed for '{value}'"
                        }
                except Exception as vision_error:
                    print(f"❌ Vision AI failed: {vision_error}")
                    return {
                        "success": False,
                        "method": "both_failed",
                        "dom_error": dom_error,
                        "vision_error": str(vision_error),
                        "message": f"Could not verify '{value}'"
                    }
            else:
                # before_screenshot 없으면 DOM 실패가 최종 실패
                return {
                    "success": False,
                    "method": "dom_only_failed",
                    "message": f"Element not found: {dom_error}"
                }

        elif action == "expectHidden":
            # 요소가 숨겨져 있는지 확인합니다
            if not selector:
                return {"success": False, "message": "Selector required for expectHidden"}
            element = page.locator(selector).first
            await element.wait_for(state="hidden", timeout=30000)
            return {"success": True, "message": f"Element {selector} is hidden"}

        elif action == "expectTrue":
            # 자바스크립트 표현식을 평가해 참인지 확인합니다
            if value is None:
                return {"success": False, "message": "Value (expression) required for expectTrue"}
            result = await page.evaluate(value)
            if result:
                return {"success": True, "message": f"Expression '{value}' evaluated to true"}
            else:
                return {"success": False, "message": f"Expression '{value}' evaluated to false"}

        elif action == "expectText":
            # 요소의 텍스트 내용을 확인합니다
            if not selector or value is None:
                return {"success": False, "message": "Selector and expected text value required for expectText"}

            try:
                element = page.locator(selector).first
                text_content = await element.text_content(timeout=5000)

                # Check if expected text is in the element's text content
                if value in (text_content or ""):
                    return {"success": True, "message": f"Found text '{value}' in element {selector}"}
                else:
                    return {"success": False, "message": f"Expected '{value}', found '{text_content}' in {selector}"}
            except Exception as e:
                return {"success": False, "message": f"Element {selector} not found or timeout: {str(e)}"}

        elif action == "expectAttribute":
            # 요소 속성 값을 확인합니다
            if not selector or value is None:
                return {"success": False, "message": "Selector and value [attr, expected] required"}
            element = page.locator(selector).first
            if isinstance(value, list) and len(value) >= 2:
                attr_name, expected_value = value[0], value[1]
            else:
                return {"success": False, "message": "Value must be [attribute_name, expected_value]"}

            actual_value = await element.get_attribute(attr_name)
            if actual_value == expected_value:
                return {"success": True, "message": f"Attribute {attr_name}={expected_value}"}
            else:
                return {"success": False, "message": f"Attribute {attr_name}={actual_value}, expected {expected_value}"}

        elif action == "expectCountAtLeast":
            # 최소 요소 개수를 확인합니다
            if not selector or value is None:
                return {"success": False, "message": "Selector and value (min count) required"}
            elements = page.locator(selector)
            count = await elements.count()
            min_count = int(value) if not isinstance(value, int) else value
            if count >= min_count:
                return {"success": True, "message": f"Found {count} elements (>= {min_count})"}
            else:
                return {"success": False, "message": f"Found {count} elements (< {min_count})"}

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
    action: str = Field(..., description="The action to perform, e.g., 'analyze_page' or 'execute_scenario'.")
    params: Dict[str, Any] = Field(default_factory=dict, description="Parameters for the action.")

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
                frame_elements = await frame.evaluate('''
            () => {
                const elements = [];

                function isVisible(el) {
                    const style = window.getComputedStyle(el);
                    // 매우 완화된 표시 여부 검사 - iframe 내부 요소도 감지
                    // display:none과 visibility:hidden만 제외
                    return style.display !== 'none' && style.visibility !== 'hidden';
                }

                function getUniqueSelector(el) {
                    // 특수 문자가 포함된 ID(예: :, ., [, ])는 속성 선택자를 사용
                    if (el.id) {
                        if (/[:\.\[\]\(\)]/.test(el.id)) {
                            return `[id="${el.id}"]`;
                        }
                        return `#${el.id}`;
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
                            'aria-label': el.getAttribute('aria-label') || ''
                        },
                        element_type: 'input'
                    });
                });

                // 버튼과 상호작용 가능한 역할 요소를 수집
                // 상호작용 UI에서 자주 사용하는 ARIA 역할
                document.querySelectorAll(`
                    button,
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
                        element_type: 'button'
                    });
                });

                document.querySelectorAll('[onclick], [class*="btn"], [class*="button"], [class*="cursor-pointer"]').forEach(el => {
                    if (!isVisible(el)) return;
                    if (el.tagName === 'BUTTON' || el.tagName === 'A') return;

                    const style = window.getComputedStyle(el);
                    if (style.cursor === 'pointer' || el.onclick) {
                        const text = el.innerText?.trim() || '';
                        if (text && text.length < 100) {
                            elements.push({
                                tag: el.tagName.toLowerCase(),
                                selector: getUniqueSelector(el),
                                text: text,
                                attributes: {
                                    class: el.className
                                },
                                element_type: 'clickable'
                            });
                        }
                    }
                });

                document.querySelectorAll('a[href]').forEach(el => {
                    if (!isVisible(el)) return;

                    const href = el.href;
                    const text = el.innerText?.trim() || '';

                    if (href.includes('#') && href.split('#')[0] === window.location.href.split('#')[0]) return;
                    if (!text) return;

                    elements.push({
                        tag: 'a',
                        selector: getUniqueSelector(el),
                        text: text,
                        attributes: {
                            href: href,
                            target: el.target || ''
                        },
                        element_type: 'link'
                    });
                });

                return elements;
            }
        ''')

                # None 체크
                if frame_elements is None:
                    frame_elements = []

                # 프레임 정보 추가
                frame_name = frame.name or f"frame_{frame_index}"
                is_main_frame = frame == page.main_frame

                print(f"  Frame {frame_index} ({frame_name}): {len(frame_elements)} elements")

                # 각 요소에 프레임 정보 추가
                for elem in frame_elements:
                    elem['frame_index'] = frame_index
                    elem['frame_name'] = frame_name
                    elem['is_main_frame'] = is_main_frame

                    # iframe 내부 요소는 selector에 frame 정보 추가
                    if not is_main_frame:
                        # iframe selector 생성 (name 또는 index 사용)
                        if frame.name:
                            frame_selector = f'iframe[name="{frame.name}"]'
                        else:
                            frame_selector = f'iframe:nth-of-type({frame_index})'
                        elem['frame_selector'] = frame_selector
                        # 전체 selector는 "frame_selector >>> element_selector" 형식
                        elem['full_selector'] = f"{frame_selector} >>> {elem['selector']}"
                    else:
                        elem['full_selector'] = elem['selector']

                all_elements.extend(frame_elements)

            except Exception as frame_error:
                import traceback
                print(f"  Error analyzing frame {frame_index} ({frame.name or 'unnamed'}): {frame_error}")
                print(f"  Traceback: {traceback.format_exc()}")
                continue

        print(f"Total found {len(all_elements)} interactive elements across all frames")
        # 디버깅용으로 처음 10개 요소를 출력합니다
        if len(all_elements) <= 10:
            element_strs = [f"{e.get('tag', '')}:{e.get('text', '')[:20]}" for e in all_elements]
            print(f"  Elements: {element_strs}")
        return {"elements": all_elements}

    except Exception as e:
        current_url = getattr(page, "url", "unknown")
        print(f"Error analyzing page {current_url}: {e}")
        return {"error": str(e)}


async def analyze_page(url: str = None, session_id: str = "default") -> Dict[str, Any]:
    """지속 세션을 사용해 페이지 요소를 분석합니다."""
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

        print(f"[analyze_page] Current browser URL: {current_browser_url} (normalized: {current_normalized})")
        print(f"[analyze_page] Requested URL: {url} (normalized: {requested_normalized})")

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
    result["url"] = page.url  # 현재 브라우저 URL을 응답에 추가합니다

    # 오케스트레이터와의 하위 호환을 위해 dom_elements 키도 제공합니다
    if "elements" in result:
        result["dom_elements"] = result["elements"]

    return result


async def capture_screenshot(url: str = None, session_id: str = "default") -> Dict[str, Any]:
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
    screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

    return {
        "screenshot": screenshot_base64,
        "url": page.url,
        "title": await page.title()
    }


async def execute_simple_action(url: str, selector: str, action: str, value: str = None, session_id: str = "default", before_screenshot: str = None) -> Dict[str, Any]:
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

        print(f"[execute_simple_action] Current page URL: {current_page_url} (normalized: {current_normalized})")
        print(f"[execute_simple_action] Requested URL: {url} (normalized: {requested_normalized})")

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
            await page.wait_for_timeout(5000)  # React/Figma가 렌더링되도록 5초 동안 대기합니다(해시 내비게이션을 고려해 증가)

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
                            "y": bounding_box["y"] + bounding_box["height"] / 2
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
                raise ValueError("Value [width, height] is required for 'setViewport' action")
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
            wait_time_ms = int(value) if isinstance(value, (int, str)) else int(value[0])
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
                print(f"JS click failed at ({x}, {y}), falling back to mouse.click: {e}")
                await page.mouse.click(x, y)

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
                "current_url": session.current_url
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
                        "y": bounding_box["y"] + bounding_box["height"] / 2
                    }
            except Exception:
                pass
            await element.hover(timeout=30000)

        elif action == "dragAndDrop":
            # 드래그 앤 드롭을 수행합니다(값에 대상 선택자 포함)
            if not selector or not value:
                raise ValueError("Both selector and value (target) required for 'dragAndDrop' action")
            source = page.locator(selector).first
            target = page.locator(value).first
            await source.drag_to(target, timeout=30000)

        elif action == "dragSlider":
            # Radix UI 슬라이더를 특정 값으로 드래그합니다
            # value는 목표 값 (예: "1000")
            if not selector:
                raise ValueError("Selector is required for 'dragSlider' action")
            if value is None:
                raise ValueError("Value (target value) is required for 'dragSlider' action")

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

                print(f"🎚️ Slider: min={min_val}, max={max_val}, current={aria_now}, target={target_val}")

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
                    track_box = await thumb.evaluate('''el => {
                        const track = el.closest('[data-slot="slider"]')?.querySelector('[data-slot="slider-track"]');
                        if (track) {
                            const rect = track.getBoundingClientRect();
                            return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                        }
                        return null;
                    }''')

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

                            print(f"🎚️ Dragged slider from ({start_x:.0f}, {start_y:.0f}) to ({target_x:.0f}, {target_y:.0f})")
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
                        "y": thumb_box["y"] + thumb_box["height"] / 2
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
                raise ValueError("Value (CSS property name) is required for 'storeCSSValue' action")

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
                        "y": bounding_box["y"] + bounding_box["height"] / 2
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
            await element.select_option(value, timeout=30000)

        elif action == "uploadFile":
            # 파일을 업로드합니다 (input[type='file']에 파일 경로 설정)
            if not selector or value is None:
                raise ValueError("Selector and file path required for 'uploadFile' action")
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
                raise ValueError("Value (CSS property name) is required for 'expectCSSChanged' action")

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
                    "screenshot": screenshot_base64
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
                    "screenshot": screenshot_base64
                }
            else:
                return {
                    "success": False,
                    "message": f"CSS '{css_property}' did not change (still '{current_css_value}')",
                    "screenshot": screenshot_base64
                }

        elif action in ("expectVisible", "expectHidden", "expectTrue", "expectText", "expectAttribute", "expectCountAtLeast"):
            # 검증 동작은 결과를 반환하는 방식으로 처리됩니다
            # 이 동작은 실행되지 않고 검증 결과만 반환합니다
            result = await _execute_assertion(page, action, selector, value, before_screenshot=before_screenshot)

            # 검증 결과용 스크린샷을 캡처합니다
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            return {
                "success": result["success"],
                "message": result["message"],
                "screenshot": screenshot_base64
            }

        elif action in ("click", "fill", "press"):
            # :has-text() 실패 시 :text()로 자동 재시도 (fallback)
            # [type="submit"] 실패 시 제거해서 재시도 (fallback)
            # [role="switch"]:has-text() → 부모 컨테이너로 탐색 (토글 스위치 특수 처리)
            fallback_selectors = []

            # 토글 스위치 특수 처리: [role="switch"]:has-text("XXX") 패턴 감지
            if '[role="switch"]' in selector and ':has-text(' in selector:
                import re
                # :has-text("텍스트") 추출
                text_match = re.search(r':has-text\(["\']([^"\']+)["\']\)', selector)
                if text_match:
                    text = text_match.group(1)
                    # 토글 스위치는 보통 label과 함께 있으므로 부모 컨테이너에서 찾기
                    fallback_selectors.append(f'.flex:has(label:has-text("{text}")) button[role="switch"]')
                    fallback_selectors.append(f'div:has(label:has-text("{text}")) button[role="switch"]')

            if ':has-text(' in selector:
                fallback_selectors.append(selector.replace(':has-text(', ':text('))
            if '[type="submit"]' in selector:
                fallback_selectors.append(selector.replace('[type="submit"]', ''))
            if '[type="submit"]' in selector and ':has-text(' in selector:
                # 둘 다 제거한 버전도 추가
                fallback_selectors.append(selector.replace('[type="submit"]', '').replace(':has-text(', ':text('))

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
                        "y": bounding_box["y"] + bounding_box["height"] / 2
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
                                "y": bounding_box["y"] + bounding_box["height"] / 2
                            }
                            print(f"⚠️  :has-text() failed, using :text() instead")
                    except Exception:
                        pass

            if action == "click":
                # Scroll element into view before clicking to prevent timeout issues
                try:
                    await element.evaluate("el => el.scrollIntoView({ behavior: 'smooth', block: 'center' })")
                    await page.wait_for_timeout(500)  # Wait for scroll animation
                except Exception as scroll_error:
                    print(f"Warning: Could not scroll element into view: {scroll_error}")

                # For switch/toggle elements, use JavaScript click for reliability
                # Playwright's click() sometimes doesn't trigger onChange handlers properly
                use_js_click = any(pattern in selector for pattern in [
                    "[data-slot='switch']",
                    "[role='switch']",
                    "switch",
                    "toggle"
                ])

                try:
                    if use_js_click:
                        print(f"🔧 Using JavaScript click for switch/toggle element")
                        await element.evaluate("el => el.click()")
                        await page.wait_for_timeout(300)  # Wait for state change
                    else:
                        await element.click(timeout=10000)
                except Exception as click_error:
                    error_msg = str(click_error)

                    # "element is not visible" 에러 감지 시 부모 hover 시도
                    if 'element is not visible' in error_msg or 'not visible' in error_msg:
                        print(f"⚠️  Element not visible, trying to hover parent menu first...")
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
                                parent_locator = page.locator(f"a:text('{parent_selector}'), button:text('{parent_selector}')").first
                                await parent_locator.hover(timeout=5000)
                                print(f"✅ Hovered parent menu, waiting for submenu...")
                                await page.wait_for_timeout(1000)  # 서브메뉴 나타날 시간 증가

                                # 다시 클릭 시도
                                await element.click(timeout=10000)
                                print(f"✅ Successfully clicked after hovering parent")
                            else:
                                print(f"⚠️  No suitable parent found for hovering")
                                raise click_error
                        except Exception as hover_error:
                            print(f"⚠️  Parent hover failed: {hover_error}")
                            # 부모 hover 실패 시 원래 fallback 로직 계속
                            if fallback_selectors and 'Timeout' in error_msg:
                                for fb_selector in fallback_selectors:
                                    try:
                                        print(f"⚠️  Original selector failed, retrying with: {fb_selector}")
                                        element = page.locator(fb_selector).first
                                        await element.evaluate("el => el.scrollIntoView({ behavior: 'smooth', block: 'center' })")
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
                    elif fallback_selectors and 'Timeout' in error_msg:
                        for fb_selector in fallback_selectors:
                            try:
                                print(f"⚠️  Original selector failed, retrying with: {fb_selector}")
                                element = page.locator(fb_selector).first
                                await element.evaluate("el => el.scrollIntoView({ behavior: 'smooth', block: 'center' })")
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
                    if fallback_selectors and 'Timeout' in str(fill_error):
                        for fb_selector in fallback_selectors:
                            try:
                                print(f"⚠️  Original selector failed, retrying with: {fb_selector}")
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
                    if fallback_selectors and 'Timeout' in str(press_error):
                        for fb_selector in fallback_selectors:
                            try:
                                print(f"⚠️  Original selector failed, retrying with: {fb_selector}")
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
            "click_position": click_position  # 애니메이션용 클릭 위치를 추가합니다
        }

    except Exception as e:
        return {"success": False, "message": f"Action failed: {str(e)}"}

    # 브라우저를 닫지 말고 세션을 유지합니다!


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
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
        ]
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
        network_requests.append({
            "method": request.method,
            "url": request.url,
            "timestamp": time.time()
        })

    async def log_response(response):
        for req in network_requests:
            if req["url"] == response.url and "status" not in req:
                req["status"] = response.status
                req["response_time"] = time.time()
                req["duration_ms"] = int((req["response_time"] - req["timestamp"]) * 1000)
                try:
                    if response.headers.get("content-type", "").startswith("application/json"):
                        req["response_body"] = await response.json()
                except:
                    pass
                break

    page.on("request", lambda request: asyncio.create_task(log_request(request)))
    page.on("response", lambda response: asyncio.create_task(log_response(response)))

    try:
        # 첫 단계로 지정된 초기 내비게이션을 처리합니다
        if scenario.steps and scenario.steps[0].action == 'goto':
            step = scenario.steps.pop(0)
            url = step.params[0] if step.params else "about:blank"
            await page.goto(url, timeout=30000)
            logs.append(f"SUCCESS: Navigated to {url}")

        # 나머지 단계를 실행합니다
        for step in scenario.steps:
            logs.append(f"Executing step: {step.description}")

            # 'note' 동작(문서화/검증 단계)을 건너뜁니다
            if step.action == 'note' or step.action == '':
                logs.append(f"NOTE: {step.description}")
                continue

            # 선택자가 필요 없는 동작을 처리합니다
            if step.action == 'tab':
                await page.keyboard.press("Tab")  # keyboard.press는 타임아웃을 지원하지 않습니다
                logs.append(f"SUCCESS: Tab key pressed")
                continue
            elif step.action == 'scroll':
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

            if step.action == 'click':
                await element.click(timeout=10000)
            elif step.action == 'fill':
                await element.fill(str(step.params[0]), timeout=10000)
            elif step.action == 'press':
                await element.press(str(step.params[0]), timeout=10000)
            else:
                raise ValueError(f"Unsupported action: {step.action}")
            logs.append(f"SUCCESS: {step.action} on '{step.selector}'")

        # 검증을 실행합니다
        logs.append(f"Executing assertion: {scenario.assertion.description}")
        assertion = scenario.assertion

        # 'note' 검증(문서용)만 건너뜁니다
        if assertion.condition == 'note' or assertion.condition == '':
            logs.append(f"NOTE: {assertion.description}")
            logs.append(f"SUCCESS: All assertions passed.")
            return {
                "status": "success",
                "logs": logs,
                "network_requests": network_requests
            }

        element = page.locator(assertion.selector)

        if assertion.condition == 'is_visible':
            await expect(element).to_be_visible(timeout=10000)
        elif assertion.condition == 'contains_text':
            await expect(element).to_contain_text(str(assertion.params[0]), timeout=10000)
        elif assertion.condition == 'url_contains':
            await expect(page).to_have_url(lambda url: str(assertion.params[0]) in url, timeout=10000)

        # 🆕 Advanced assertions
        elif assertion.condition == 'network_request':
            # 네트워크 요청 검증
            method = assertion.params[0] if len(assertion.params) > 0 else "POST"
            url_pattern = assertion.params[1] if len(assertion.params) > 1 else ""
            expected_status = assertion.params[2] if len(assertion.params) > 2 else 200

            matching_requests = [
                req for req in network_requests
                if req["method"] == method and url_pattern in req["url"]
            ]

            if not matching_requests:
                raise AssertionError(f"No {method} request to URL containing '{url_pattern}'")

            if matching_requests[-1].get("status") != expected_status:
                raise AssertionError(f"Request status {matching_requests[-1].get('status')} != {expected_status}")

            logs.append(f"SUCCESS: Network request validated - {method} {url_pattern} → {expected_status}")

        elif assertion.condition == 'element_count':
            # 요소 개수 검증
            expected_count = int(assertion.params[0])
            actual_count = await element.count()
            if actual_count != expected_count:
                raise AssertionError(f"Expected {expected_count} elements, found {actual_count}")
            logs.append(f"SUCCESS: Element count = {expected_count}")

        elif assertion.condition == 'toast_visible':
            # 토스트 메시지 검증 (일반적인 selector들)
            toast_selectors = [
                '[role="alert"]',
                '.toast',
                '.notification',
                '[class*="toast"]',
                '[class*="snackbar"]'
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
                    logs.append(f"SUCCESS: Toast/notification visible with text '{expected_text}'")
                    break
                except:
                    continue

            if not toast_found:
                raise AssertionError(f"No toast/notification found with text '{expected_text}'")

        elif assertion.condition == 'api_response_contains':
            # API 응답 내용 검증
            url_pattern = assertion.params[0] if len(assertion.params) > 0 else ""
            expected_key = assertion.params[1] if len(assertion.params) > 1 else ""
            expected_value = assertion.params[2] if len(assertion.params) > 2 else None

            matching_requests = [
                req for req in network_requests
                if url_pattern in req["url"] and "response_body" in req
            ]

            if not matching_requests:
                raise AssertionError(f"No API response found for URL containing '{url_pattern}'")

            response_body = matching_requests[-1]["response_body"]
            if expected_key not in response_body:
                raise AssertionError(f"Response missing key '{expected_key}'")

            if expected_value is not None and response_body[expected_key] != expected_value:
                raise AssertionError(
                    f"Response[{expected_key}] = {response_body[expected_key]}, expected {expected_value}"
                )

            logs.append(f"SUCCESS: API response validated - {expected_key} = {response_body.get(expected_key)}")

        elif assertion.condition == 'response_time_under':
            # API 응답 시간 검증
            url_pattern = assertion.params[0] if len(assertion.params) > 0 else ""
            max_duration_ms = int(assertion.params[1]) if len(assertion.params) > 1 else 1000

            matching_requests = [
                req for req in network_requests
                if url_pattern in req["url"] and "duration_ms" in req
            ]

            if not matching_requests:
                raise AssertionError(f"No API response found for URL containing '{url_pattern}'")

            actual_duration = matching_requests[-1]["duration_ms"]
            if actual_duration > max_duration_ms:
                raise AssertionError(
                    f"API response time {actual_duration}ms exceeds limit {max_duration_ms}ms"
                )

            logs.append(f"SUCCESS: API response time {actual_duration}ms < {max_duration_ms}ms")

        else:
            raise ValueError(f"Unsupported condition: {assertion.condition}")

        logs.append(f"SUCCESS: All assertions passed.")
        return {
            "status": "success",
            "logs": logs,
            "network_requests": network_requests  # 디버깅용
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
        url = params.get("url")  # 현재 페이지를 사용하려면 url을 None으로 둘 수 있습니다
        return await analyze_page(url, session_id)

    elif action == "capture_screenshot":
        url = params.get("url")  # 현재 페이지를 사용하려면 url을 None으로 둘 수 있습니다
        return await capture_screenshot(url, session_id)

    elif action == "execute_action":
        # 전체 시나리오 없이 단순 동작(클릭, 입력, 키 입력)을 실행합니다
        url = params.get("url")
        selector = params.get("selector", "")  # 일부 동작은 선택자가 비어 있을 수 있습니다
        action_type = params.get("action")
        value = params.get("value")
        before_screenshot = params.get("before_screenshot")  # Vision AI용 이전 스크린샷

        # goto, setViewport, evaluate, tab, scroll, wait, waitForTimeout, clickAt, click_at_coordinates 같은 동작은 선택자가 필요 없습니다
        # 검증 동작도 선택자가 필요 없으며 value 매개변수를 사용합니다
        actions_not_needing_selector = ["goto", "setViewport", "evaluate", "tab", "scroll", "wait", "waitForTimeout", "clickAt", "click_at_coordinates",
                                        "expectTrue", "expectAttribute", "expectCountAtLeast", "expectVisible", "expectHidden"]

        if not action_type:
            raise HTTPException(status_code=400, detail="action is required for 'execute_action'.")

        if action_type not in actions_not_needing_selector and not selector:
            raise HTTPException(status_code=400, detail=f"selector is required for action '{action_type}'.")

        return await execute_simple_action(url, selector, action_type, value, session_id, before_screenshot=before_screenshot)

    elif action == "execute_scenario":
        scenario_data = params.get("scenario")
        if not scenario_data:
            raise HTTPException(status_code=400, detail="Scenario is required for 'execute_scenario'.")

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
    print(f"[WebSocket] New screencast subscriber connected (total: {len(screencast_subscribers)})")

    try:
        # 연결 유지 - 클라이언트가 메시지를 보내거나 연결이 끊어질 때까지 대기
        while True:
            # 클라이언트로부터 메시지를 받습니다 (ping/pong 등)
            data = await websocket.receive_text()

            # 클라이언트가 요청하면 현재 프레임을 즉시 전송
            if data == "get_current_frame" and current_screencast_frame:
                await websocket.send_json({
                    'type': 'screencast_frame',
                    'frame': current_screencast_frame,
                    'timestamp': asyncio.get_event_loop().time()
                })

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
        "screencast_active": any(s.screencast_active for s in active_sessions.values())
    }

def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
