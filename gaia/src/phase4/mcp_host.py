import asyncio
import base64
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Playwright, expect, Browser, Page
from typing import Dict, Any, Optional, List

app = FastAPI(title="MCP Host", description="Model Context Protocol Host for Browser Automation")

# 라이브 미리보기를 위한 전역 상태
live_preview_subscribers: List[asyncio.Queue] = []
current_page_screenshot: str = ""

# 브라우저 세션 관리
class BrowserSession:
    """상태 기반 테스트를 위해 지속적인 브라우저 세션을 유지합니다"""
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.current_url: str = ""

    async def get_or_create_page(self) -> Page:
        """기존 페이지를 가져오거나 새 브라우저 세션을 생성합니다"""
        if not self.browser:
            if not playwright_instance:
                raise HTTPException(status_code=503, detail="Playwright not initialized")
            self.browser = await playwright_instance.chromium.launch(headless=True)
            self.page = await self.browser.new_page()
        return self.page

    async def close(self):
        """브라우저 세션을 종료합니다"""
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
async def _execute_assertion(page: Page, action: str, selector: str, value: Any) -> Dict[str, Any]:
    """검증 작업을 수행하고 결과를 반환합니다"""
    try:
        if action == "expectVisible":
            # 요소가 보이는지 확인합니다
            if not selector:
                return {"success": False, "message": "Selector required for expectVisible"}
            element = page.locator(selector).first
            await element.wait_for(state="visible", timeout=30000)
            return {"success": True, "message": f"Element {selector} is visible"}

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
    """현재 페이지에서 상호작용 가능한 요소를 추출합니다."""
    try:
        try:
            await page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            await page.wait_for_timeout(2000)

        elements_data = await page.evaluate('''
            () => {
                const elements = [];

                function isVisible(el) {
                    const style = window.getComputedStyle(el);
                    // React SPA를 위한 더 완화된 표시 여부 검사
                    // DOM에 있지만 화면 밖이거나 애니메이션 중인 요소도 허용
                    return style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        parseFloat(style.opacity) > 0.1 &&  // Allow fade-in animations (changed from strict '0' check)
                        el.offsetWidth > 0 &&
                        el.offsetHeight > 0;
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

                document.querySelectorAll('[onclick], [class*="btn"], [class*="button"]').forEach(el => {
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

        print(f"Found {len(elements_data)} interactive elements")
        # 디버깅용으로 처음 10개 요소를 출력합니다
        if len(elements_data) <= 10:
            element_strs = [f"{e.get('tag', '')}:{e.get('text', '')[:20]}" for e in elements_data]
            print(f"  Elements: {element_strs}")
        return {"elements": elements_data}

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


async def execute_simple_action(url: str, selector: str, action: str, value: str = None, session_id: str = "default") -> Dict[str, Any]:
    """
    Execute a simple action (click, fill, press, scroll, tab) using persistent session.

    Args:
        url: Page URL
        selector: CSS selector (not used for 'tab' action)
        action: Action type (click, fill, press, scroll, tab)
        value: Value for fill/press actions, or scroll amount for scroll action
        session_id: Browser session ID (default: "default")

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
                await element.evaluate(value)
            else:
                # 페이지에서 평가합니다
                await page.evaluate(value)

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

        elif action in ("expectVisible", "expectHidden", "expectTrue", "expectAttribute", "expectCountAtLeast"):
            # 검증 동작은 결과를 반환하는 방식으로 처리됩니다
            # 이 동작은 실행되지 않고 검증 결과만 반환합니다
            result = await _execute_assertion(page, action, selector, value)

            # 검증 결과용 스크린샷을 캡처합니다
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            return {
                "success": result["success"],
                "message": result["message"],
                "screenshot": screenshot_base64
            }

        elif action in ("click", "fill", "press"):
            # 선택자가 필요한 동작
            element = page.locator(selector).first

            # 클릭 애니메이션을 위해 요소 위치를 구합니다
            try:
                bounding_box = await element.bounding_box()
                if bounding_box:
                    click_position = {
                        "x": bounding_box["x"] + bounding_box["width"] / 2,
                        "y": bounding_box["y"] + bounding_box["height"] / 2
                    }
            except Exception:
                pass

            if action == "click":
                await element.click(timeout=30000)  # 10초에서 30초로 증가시켰습니다
            elif action == "fill":
                if value is None:
                    raise ValueError("Value is required for 'fill' action")
                await element.fill(value, timeout=30000)  # 10초에서 30초로 증가시켰습니다
            elif action == "press":
                if value is None:
                    raise ValueError("Value is required for 'press' action")
                await element.press(value, timeout=30000)  # 10초에서 30초로 증가시켰습니다

        else:
            raise ValueError(f"Unsupported action: {action}")

        # 상태 변경을 기다립니다
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)  # 3초에서 5초로 증가시켰습니다
        except Exception:
            await page.wait_for_timeout(1500)  # 1초에서 1.5초로 증가시켰습니다

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

    browser = await playwright_instance.chromium.launch(headless=True)
    page = await browser.new_page()

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
                await element.click(timeout=30000)  # 10초에서 30초로 증가시켰습니다
            elif step.action == 'fill':
                await element.fill(str(step.params[0]), timeout=30000)  # 10초에서 30초로 증가시켰습니다
            elif step.action == 'press':
                await element.press(str(step.params[0]), timeout=30000)  # 10초에서 30초로 증가시켰습니다
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

        # goto, setViewport, evaluate, tab, scroll, wait, waitForTimeout, clickAt, click_at_coordinates 같은 동작은 선택자가 필요 없습니다
        # 검증 동작도 선택자가 필요 없으며 value 매개변수를 사용합니다
        actions_not_needing_selector = ["goto", "setViewport", "evaluate", "tab", "scroll", "wait", "waitForTimeout", "clickAt", "click_at_coordinates",
                                        "expectTrue", "expectAttribute", "expectCountAtLeast"]

        if not action_type:
            raise HTTPException(status_code=400, detail="action is required for 'execute_action'.")

        if action_type not in actions_not_needing_selector and not selector:
            raise HTTPException(status_code=400, detail=f"selector is required for action '{action_type}'.")

        return await execute_simple_action(url, selector, action_type, value, session_id)

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


@app.get("/")
async def root():
    return {"message": "MCP Host is running.", "active_sessions": len(active_sessions)}

def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
