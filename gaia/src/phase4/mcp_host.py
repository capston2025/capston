import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Playwright, expect
from typing import Dict, Any, Optional, List

app = FastAPI(title="MCP Host", description="Model Context Protocol Host for Browser Automation")

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
                    if (el.id) return `#${el.id}`;

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


async def analyze_page(url: str) -> Dict[str, Any]:
    """Navigate to the given URL and analyze its interactive elements."""
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    browser = await playwright_instance.chromium.launch(headless=True)
    page = await browser.new_page()

    try:
        await page.goto(url, timeout=30000)
        return await analyze_page_elements(page)
    finally:
        await browser.close()


async def capture_screenshot(url: str) -> Dict[str, Any]:
    """Capture a screenshot of the given URL."""
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    browser = await playwright_instance.chromium.launch(headless=True)
    page = await browser.new_page()

    try:
        await page.goto(url, timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            await page.wait_for_timeout(2000)

        # Capture screenshot as base64
        screenshot_bytes = await page.screenshot(full_page=False)
        import base64
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

        return {
            "screenshot": screenshot_base64,
            "url": page.url,
            "title": await page.title()
        }
    finally:
        await browser.close()


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
            element = page.locator(step.selector)

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

    if action == "analyze_page":
        url = params.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="URL is required for 'analyze_page'.")
        return await analyze_page(url)

    elif action == "capture_screenshot":
        url = params.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="URL is required for 'capture_screenshot'.")
        return await capture_screenshot(url)

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

@app.get("/")
async def root():
    return {"message": "MCP Host is running."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
