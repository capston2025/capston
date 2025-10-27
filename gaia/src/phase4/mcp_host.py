import asyncio
import base64
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Playwright, expect, Browser, Page
from typing import Dict, Any, Optional, List

app = FastAPI(title="MCP Host", description="Model Context Protocol Host for Browser Automation")

# Global state for live preview
live_preview_subscribers: List[asyncio.Queue] = []
current_page_screenshot: str = ""

# Browser session management
class BrowserSession:
    """Maintains a persistent browser session for stateful testing"""
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.current_url: str = ""

    async def get_or_create_page(self) -> Page:
        """Get existing page or create new browser session"""
        if not self.browser:
            if not playwright_instance:
                raise HTTPException(status_code=503, detail="Playwright not initialized")
            self.browser = await playwright_instance.chromium.launch(headless=True)
            self.page = await self.browser.new_page()
        return self.page

    async def close(self):
        """Close browser session"""
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.page = None

# Active sessions storage
active_sessions: Dict[str, BrowserSession] = {}

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
    method: str  # GET, POST, etc.
    url_pattern: str  # regex or substring
    expected_status: int = 200
    response_contains: Optional[Dict[str, Any]] = None  # JSON 응답 검증

class UIAssertion(BaseModel):
    """UI 상태 검증"""
    description: str
    assertion_type: str  # toast, modal, element_count, etc.
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

# Global Playwright instance
playwright_instance: Optional[Playwright] = None

@app.on_event("startup")
async def startup_event():
    """Initializes the Playwright instance on server startup."""
    global playwright_instance
    print("Initializing Playwright...")
    playwright_instance = await async_playwright().start()
    print("Playwright initialized.")

@app.on_event("shutdown")
async def shutdown_event():
    """Stops the Playwright instance on server shutdown."""
    if playwright_instance:
        print("Stopping Playwright...")
        await playwright_instance.stop()
        print("Playwright stopped.")

async def analyze_page_elements(page) -> Dict[str, Any]:
    """Extract interactive elements from the current page."""
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
                    return style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        style.opacity !== '0' &&
                        el.offsetWidth > 0 &&
                        el.offsetHeight > 0;
                }

                function getUniqueSelector(el) {
                    // Use attribute selector for IDs with special characters (like :, ., [, ])
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

                document.querySelectorAll('button, [role="button"], [type="submit"], input[type="button"]').forEach(el => {
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
        return {"elements": elements_data}

    except Exception as e:
        current_url = getattr(page, "url", "unknown")
        print(f"Error analyzing page {current_url}: {e}")
        return {"error": str(e)}


async def analyze_page(url: str = None, session_id: str = "default") -> Dict[str, Any]:
    """Analyze page elements using persistent session."""
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    # Get or create session
    if session_id not in active_sessions:
        active_sessions[session_id] = BrowserSession(session_id)

    session = active_sessions[session_id]
    page = await session.get_or_create_page()

    # Navigate only if URL is provided AND different from current
    if url and session.current_url != url:
        await page.goto(url, timeout=30000)
        session.current_url = url

    return await analyze_page_elements(page)


async def capture_screenshot(url: str = None, session_id: str = "default") -> Dict[str, Any]:
    """Capture a screenshot using persistent session."""
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    # Get or create session
    if session_id not in active_sessions:
        active_sessions[session_id] = BrowserSession(session_id)

    session = active_sessions[session_id]
    page = await session.get_or_create_page()

    # Navigate only if URL is provided AND different from current
    if url and session.current_url != url:
        await page.goto(url, timeout=30000)
        session.current_url = url
        try:
            await page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            await page.wait_for_timeout(2000)

    # Capture screenshot of current page (wherever it is)
    screenshot_bytes = await page.screenshot(full_page=False)
    screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

    return {
        "screenshot": screenshot_base64,
        "url": page.url,
        "title": await page.title()
    }


async def execute_simple_action(url: str, selector: str, action: str, value: str = None, session_id: str = "default") -> Dict[str, Any]:
    """
    Execute a simple action (click, fill, press) using persistent session.

    Args:
        url: Page URL
        selector: CSS selector
        action: Action type (click, fill, press)
        value: Value for fill/press actions
        session_id: Browser session ID (default: "default")

    Returns:
        Dict with success status and screenshot
    """
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    # Get or create session
    if session_id not in active_sessions:
        active_sessions[session_id] = BrowserSession(session_id)

    session = active_sessions[session_id]
    page = await session.get_or_create_page()

    try:
        # Navigate only if URL changed
        if session.current_url != url:
            await page.goto(url, timeout=30000)
            session.current_url = url
            try:
                await page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                await page.wait_for_timeout(2000)

        # Use .first to handle multiple matches (avoid strict mode violation)
        element = page.locator(selector).first

        # Get element position before action (for click animation)
        click_position = None
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
            await element.click(timeout=10000)
        elif action == "fill":
            if value is None:
                raise ValueError("Value is required for 'fill' action")
            await element.fill(value, timeout=10000)
        elif action == "press":
            if value is None:
                raise ValueError("Value is required for 'press' action")
            await element.press(value, timeout=10000)
        else:
            raise ValueError(f"Unsupported action: {action}")

        # Wait for state change
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            await page.wait_for_timeout(1000)

        # Update current URL in case of navigation
        session.current_url = page.url

        # Capture screenshot after action for real-time preview
        screenshot_bytes = await page.screenshot(full_page=False)
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        return {
            "success": True,
            "message": f"Action '{action}' executed on '{selector}'",
            "screenshot": screenshot_base64,
            "current_url": session.current_url,
            "click_position": click_position  # Add click position for animation
        }

    except Exception as e:
        return {"success": False, "message": f"Action failed: {str(e)}"}

    # Don't close browser - keep session alive!


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

    # Network request/response listener
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
        # Handle initial navigation if specified as the first step
        if scenario.steps and scenario.steps[0].action == 'goto':
            step = scenario.steps.pop(0)
            url = step.params[0] if step.params else "about:blank"
            await page.goto(url, timeout=30000)
            logs.append(f"SUCCESS: Navigated to {url}")

        # Execute remaining steps
        for step in scenario.steps:
            logs.append(f"Executing step: {step.description}")

            # Skip 'note' actions (documentation/assertion steps)
            if step.action == 'note' or step.action == '':
                logs.append(f"NOTE: {step.description}")
                continue

            # Use .first to handle multiple matches (avoid strict mode violation)
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

        # Execute assertion
        logs.append(f"Executing assertion: {scenario.assertion.description}")
        assertion = scenario.assertion

        # Skip 'note' assertions (documentation only)
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
        url = params.get("url")  # url can be None to use current page
        return await analyze_page(url, session_id)

    elif action == "capture_screenshot":
        url = params.get("url")  # url can be None to use current page
        return await capture_screenshot(url, session_id)

    elif action == "execute_action":
        # Simple action execution (click, fill, press) without full scenario
        url = params.get("url")
        selector = params.get("selector")
        action_type = params.get("action")
        value = params.get("value")

        if not url or not selector or not action_type:
            raise HTTPException(status_code=400, detail="url, selector, and action are required for 'execute_action'.")

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
    """Close a browser session and clean up resources."""
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
