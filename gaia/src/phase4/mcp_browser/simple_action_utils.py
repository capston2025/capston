from __future__ import annotations

from typing import Any

from playwright.async_api import Page


def normalize_timeout_ms(raw: Any, default_ms: int, min_ms: int = 500, max_ms: int = 120000) -> int:
    try:
        value = int(raw)
    except Exception:
        value = int(default_ms)
    return max(min_ms, min(max_ms, value))


async def evaluate_js_with_timeout(
    page: Page,
    script: str,
    *,
    selector: str = "",
    timeout_ms: int = 20000,
) -> Any:
    fn_text = str(script or "").strip()
    if not fn_text:
        raise ValueError("Value (script) is required for 'evaluate' action")
    timeout_ms = normalize_timeout_ms(timeout_ms, 20000)

    if selector:
        element = page.locator(selector).first
        return await element.evaluate(
            """
            (el, payload) => {
              const { fnBody, timeoutMs } = payload || {};
              try {
                const candidate = eval("(" + fnBody + ")");
                const result = (typeof candidate === "function") ? candidate(el) : candidate;
                if (result && typeof result.then === "function") {
                  return Promise.race([
                    result,
                    new Promise((_, reject) =>
                      setTimeout(() => reject(new Error("evaluate timed out after " + timeoutMs + "ms")), timeoutMs)
                    )
                  ]);
                }
                return result;
              } catch (err) {
                throw new Error("Invalid evaluate function: " + (err && err.message ? err.message : String(err)));
              }
            }
            """,
            {"fnBody": fn_text, "timeoutMs": timeout_ms},
        )

    return await page.evaluate(
        """
        ({ fnBody, timeoutMs }) => {
          try {
            const candidate = eval("(" + fnBody + ")");
            const result = (typeof candidate === "function") ? candidate() : candidate;
            if (result && typeof result.then === "function") {
              return Promise.race([
                result,
                new Promise((_, reject) =>
                  setTimeout(() => reject(new Error("evaluate timed out after " + timeoutMs + "ms")), timeoutMs)
                )
              ]);
            }
            return result;
          } catch (err) {
            throw new Error("Invalid evaluate function: " + (err && err.message ? err.message : String(err)));
          }
        }
        """,
        {"fnBody": fn_text, "timeoutMs": timeout_ms},
    )
