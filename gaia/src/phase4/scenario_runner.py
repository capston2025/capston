"""Playwright scenario execution helpers extracted from mcp_host."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from playwright.async_api import expect


async def run_test_scenario_with_playwright(playwright_instance, scenario: Any) -> Dict[str, Any]:
    """Execute a full test scenario using Playwright with lightweight network monitoring."""
    logs = []
    network_requests = []

    browser = await playwright_instance.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    page = await browser.new_page()

    await page.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false,
        });
        window.chrome = { runtime: {} };
        """
    )

    async def log_request(request):
        network_requests.append(
            {"method": request.method, "url": request.url, "timestamp": time.time()}
        )

    async def log_response(response):
        for req in network_requests:
            if req["url"] == response.url and "status" not in req:
                req["status"] = response.status
                req["response_time"] = time.time()
                req["duration_ms"] = int((req["response_time"] - req["timestamp"]) * 1000)
                try:
                    if response.headers.get("content-type", "").startswith("application/json"):
                        req["response_body"] = await response.json()
                except Exception:
                    pass
                break

    page.on("request", lambda request: asyncio.create_task(log_request(request)))
    page.on("response", lambda response: asyncio.create_task(log_response(response)))

    try:
        if scenario.steps and scenario.steps[0].action == "goto":
            step = scenario.steps.pop(0)
            url = step.params[0] if step.params else "about:blank"
            await page.goto(url, timeout=30000)
            logs.append(f"SUCCESS: Navigated to {url}")

        for step in scenario.steps:
            logs.append(f"Executing step: {step.description}")

            if step.action in {"note", ""}:
                logs.append(f"NOTE: {step.description}")
                continue

            if step.action == "tab":
                await page.keyboard.press("Tab")
                logs.append("SUCCESS: Tab key pressed")
                continue
            if step.action == "scroll":
                if step.selector:
                    element = page.locator(step.selector).first
                    await element.scroll_into_view_if_needed(timeout=10000)
                    logs.append(f"SUCCESS: Scrolled '{step.selector}' into view")
                else:
                    scroll_amount = int(step.params[0]) if step.params else 500
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    logs.append(f"SUCCESS: Scrolled page by {scroll_amount}px")
                continue

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

        logs.append(f"Executing assertion: {scenario.assertion.description}")
        assertion = scenario.assertion
        if assertion.condition in {"note", ""}:
            logs.append(f"NOTE: {assertion.description}")
            logs.append("SUCCESS: All assertions passed.")
            return {"status": "success", "logs": logs, "network_requests": network_requests}

        element = page.locator(assertion.selector)
        if assertion.condition == "is_visible":
            await expect(element).to_be_visible(timeout=10000)
        elif assertion.condition == "contains_text":
            await expect(element).to_contain_text(str(assertion.params[0]), timeout=10000)
        elif assertion.condition == "url_contains":
            await expect(page).to_have_url(
                lambda url: str(assertion.params[0]) in url,
                timeout=10000,
            )
        elif assertion.condition == "network_request":
            method = assertion.params[0] if len(assertion.params) > 0 else "POST"
            url_pattern = assertion.params[1] if len(assertion.params) > 1 else ""
            expected_status = assertion.params[2] if len(assertion.params) > 2 else 200
            matching_requests = [
                req for req in network_requests if req["method"] == method and url_pattern in req["url"]
            ]
            if not matching_requests:
                raise AssertionError(f"No {method} request to URL containing '{url_pattern}'")
            if matching_requests[-1].get("status") != expected_status:
                raise AssertionError(
                    f"Request status {matching_requests[-1].get('status')} != {expected_status}"
                )
            logs.append(f"SUCCESS: Network request validated - {method} {url_pattern} → {expected_status}")
        elif assertion.condition == "element_count":
            expected_count = int(assertion.params[0])
            actual_count = await element.count()
            if actual_count != expected_count:
                raise AssertionError(f"Expected {expected_count} elements, found {actual_count}")
            logs.append(f"SUCCESS: Element count = {expected_count}")
        elif assertion.condition == "toast_visible":
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
                    logs.append(f"SUCCESS: Toast/notification visible with text '{expected_text}'")
                    break
                except Exception:
                    continue
            if not toast_found:
                raise AssertionError(f"No toast/notification found with text '{expected_text}'")
        elif assertion.condition == "api_response_contains":
            url_pattern = assertion.params[0] if len(assertion.params) > 0 else ""
            expected_key = assertion.params[1] if len(assertion.params) > 1 else ""
            expected_value = assertion.params[2] if len(assertion.params) > 2 else None
            matching_requests = [
                req for req in network_requests if url_pattern in req["url"] and "response_body" in req
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
        elif assertion.condition == "response_time_under":
            url_pattern = assertion.params[0] if len(assertion.params) > 0 else ""
            max_duration_ms = int(assertion.params[1]) if len(assertion.params) > 1 else 1000
            matching_requests = [
                req for req in network_requests if url_pattern in req["url"] and "duration_ms" in req
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

        logs.append("SUCCESS: All assertions passed.")
        return {"status": "success", "logs": logs, "network_requests": network_requests}
    except Exception as e:
        error_message = f"ERROR: {type(e).__name__} - {str(e)}"
        logs.append(error_message)
        return {"status": "failed", "logs": logs, "error": error_message}
    finally:
        await browser.close()
