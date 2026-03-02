from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional, Callable, List

from fastapi import HTTPException


async def execute_simple_action_impl(
    url: str,
    selector: str,
    action: str,
    value: str = None,
    session_id: str = "default",
    before_screenshot: str = None,
    action_options: Optional[Dict[str, Any]] = None,
    *,
    playwright_instance: Any,
    ensure_session: Callable[..., Any],
    active_sessions: Dict[str, Any],
    _get_playwright_instance: Callable[[], Any],
    screencast_subscribers: List[Any],
    _set_current_screencast_frame: Callable[[str], None],
    logger: Any,
    is_element_action: Callable[[str], bool],
    legacy_selector_forbidden: Callable[[str, str], bool],
    normalize_url: Callable[[str], str],
    _scroll_locator_container: Callable[..., Any],
    _normalize_timeout_ms: Callable[..., int],
    _evaluate_js_with_timeout: Callable[..., Any],
    _reset_session_connection: Callable[..., Any],
    _execute_assertion: Callable[..., Any],
    _reveal_locator_in_scroll_context: Callable[..., Any],
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
    session = ensure_session(
        active_sessions=active_sessions,
        session_id=session_id,
        playwright_getter=_get_playwright_instance,
        screencast_subscribers=screencast_subscribers,
        frame_setter=_set_current_screencast_frame,
        logger=logger,
    )
    page = await session.get_or_create_page()

    if is_element_action(action):
        return {
            "success": False,
            "reason_code": "legacy_selector_forbidden",
            "message": (
                "legacy selector element actions are disabled. "
                "use browser_snapshot + browser_act(snapshot_id, ref_id)."
            ),
        }

    if legacy_selector_forbidden(action, selector):
        return {
            "success": False,
            "reason_code": "legacy_selector_forbidden",
            "message": (
                "legacy selector element actions are disabled. "
                "use browser_snapshot + browser_act(snapshot_id, ref_id)."
            ),
        }

    try:
        # URL이 변경되었고 비어 있지 않을 때에만 이동합니다
        # 캐시된 세션 URL이 아닌 실제 브라우저 URL과 비교합니다
        current_page_url = page.url
        current_normalized = normalize_url(current_page_url)
        requested_normalized = normalize_url(url) if url else None

        logger.debug(
            "[execute_simple_action] Current page URL: %s (normalized: %s)",
            current_page_url,
            current_normalized,
        )
        logger.debug(
            "[execute_simple_action] Requested URL: %s (normalized: %s)",
            url,
            requested_normalized,
        )

        if requested_normalized and current_normalized != requested_normalized:
            logger.info("[execute_simple_action] URLs differ, navigating to: %s", url)
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
            env_default = os.getenv("GAIA_EVALUATE_TIMEOUT_MS", "20000")
            timeout_raw = (
                (action_options or {}).get("timeoutMs")
                if isinstance(action_options, dict)
                else None
            )
            if timeout_raw is None and isinstance(action_options, dict):
                timeout_raw = action_options.get("timeout_ms")
            eval_timeout_ms = _normalize_timeout_ms(
                timeout_raw if timeout_raw is not None else env_default,
                20000,
            )
            try:
                eval_result = await _evaluate_js_with_timeout(
                    page,
                    str(value),
                    selector=selector,
                    timeout_ms=eval_timeout_ms,
                )
            except Exception as eval_exc:
                msg = str(eval_exc)
                lower_msg = msg.lower()
                if "evaluate timed out after" in lower_msg or "timed out" in lower_msg:
                    await _reset_session_connection(
                        session,
                        reason=f"evaluate_timeout:{msg[:180]}",
                    )
                    return {
                        "success": False,
                        "reason_code": "action_timeout",
                        "message": (
                            f"Evaluate timed out after {eval_timeout_ms}ms. "
                            "Session connection was reset; retry with a smaller/bounded fn."
                        ),
                    }
                raise

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
                                element = page.locator(fb_selector).first
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
                                element = page.locator(fb_selector).first
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
                                element = page.locator(fb_selector).first
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


