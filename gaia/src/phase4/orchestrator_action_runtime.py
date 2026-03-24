from __future__ import annotations

import time
from typing import Any, Dict, List

from gaia.src.phase4.mcp_transport_retry_runtime import execute_mcp_action_with_recovery


def execute_action_with_self_healing(
    orchestrator,
    action: str,
    selector: str,
    params: List[Any],
    url: str,
    screenshot: str,
    dom_elements,
    step_description: str,
    before_screenshot: str = None,
    progress_callback=None,
    max_retries: int = 3,
    scenario_id: str = "",
) -> bool:
    """
    Execute action with self-healing capabilities.
    """
    original_selector = selector
    success = execute_action(
        orchestrator,
        action,
        selector,
        params,
        url,
        before_screenshot,
    )

    if success:
        return True

    orchestrator._log(
        "    🔧 Action failed, initiating self-healing...", progress_callback
    )

    error_message = (
        orchestrator.last_action_error
        if orchestrator.last_action_error
        else "Action execution failed"
    )

    retry_count = 0
    while retry_count < max_retries:
        retry_count += 1
        orchestrator._log(
            f"    🔄 Self-healing attempt {retry_count}/{max_retries}",
            progress_callback,
        )

        current_screenshot = orchestrator._capture_screenshot(url, send_to_gui=False)

        from .llm_vision_client import get_vision_client

        vision_client = get_vision_client()
        error_analysis = vision_client.analyze_action_failure(
            action=action,
            selector=selector,
            error_message=error_message,
            screenshot_base64=current_screenshot,
            dom_elements=dom_elements,
            url=url,
            step_description=step_description,
        )

        failure_reason = error_analysis.get("failure_reason", "unknown")
        suggested_fixes = error_analysis.get("suggested_fixes", [])
        confidence = error_analysis.get("confidence", 0)
        reasoning = error_analysis.get("reasoning", "")

        orchestrator._log(
            f"    💡 Failure reason: {failure_reason} (confidence: {confidence}%)",
            progress_callback,
        )
        orchestrator._log(
            f"    💭 Analysis: {reasoning[:100]}...", progress_callback
        )

        if not suggested_fixes:
            orchestrator._log("    ❌ No fixes suggested, giving up", progress_callback)
            return False

        for fix_idx, fix in enumerate(suggested_fixes[:2], 1):
            fix_type = fix.get("type")
            fix_description = fix.get("description", "")
            orchestrator._log(f"    🛠️  Fix {fix_idx}: {fix_description}", progress_callback)

            try:
                if fix_type == "close_overlay":
                    method = fix.get("method", "press_escape")
                    if method == "press_escape":
                        execute_action(orchestrator, "press", "", ["Escape"], url)
                    elif method == "click_backdrop":
                        execute_action(orchestrator, "click", "body", [], url)
                    time.sleep(0.3)

                elif fix_type == "scroll":
                    scroll_selector = fix.get("selector", selector)
                    if scroll_selector:
                        execute_action(
                            orchestrator, "scrollIntoView", scroll_selector, [], url
                        )
                    else:
                        execute_action(orchestrator, "scroll", "body", ["down"], url)
                    time.sleep(0.3)

                elif fix_type == "javascript":
                    script = fix.get("script")
                    if script:
                        execute_action(orchestrator, "evaluate", "", [script], url)
                    time.sleep(0.3)

                elif fix_type == "wait":
                    duration = fix.get("duration", 500)
                    time.sleep(duration / 1000.0)

                elif fix_type == "open_container":
                    orchestrator._log(
                        "    ⚠️ 'open_container' fix not yet implemented",
                        progress_callback,
                    )

                elif fix_type == "use_alternative_selector":
                    alternative_selector = fix.get("selector")
                    if alternative_selector:
                        selector = alternative_selector

                orchestrator._log(
                    "    🔁 Retrying original action after fix...", progress_callback
                )
                success = execute_action(
                    orchestrator,
                    action,
                    selector,
                    params,
                    url,
                    before_screenshot,
                )

                if success:
                    orchestrator._log(
                        "    ✅ Self-healing successful! Action succeeded after fix",
                        progress_callback,
                    )
                    if scenario_id and selector != original_selector:
                        if scenario_id not in orchestrator.healed_selectors:
                            orchestrator.healed_selectors[scenario_id] = {}
                        orchestrator.healed_selectors[scenario_id][
                            original_selector
                        ] = selector
                        orchestrator._log(
                            f"    📝 Tracked healed selector: {original_selector} → {selector}",
                            progress_callback,
                        )
                    return True

            except Exception as e:
                orchestrator._log(f"    ⚠️ Fix failed: {e}", progress_callback)
                continue

        orchestrator._log(
            f"    ❌ All fixes failed for attempt {retry_count}", progress_callback
        )

    orchestrator._log(
        f"    ❌ Self-healing failed after {max_retries} attempts", progress_callback
    )
    return False


def execute_action(
    orchestrator,
    action: str,
    selector: str,
    params: List[Any],
    url: str,
    before_screenshot: str = None,
) -> bool:
    """Execute a browser action using MCP host."""
    try:
        if action in ["setViewport", "dragAndDrop"]:
            value = params if params else None
        else:
            value = params[0] if params else None

        element_actions = {
            "click",
            "fill",
            "press",
            "hover",
            "scroll",
            "scrollIntoView",
            "select",
            "dragAndDrop",
            "dragSlider",
        }
        action_name = "click" if action == "focus" else action
        act_params: Dict[str, Any] = {
            "session_id": orchestrator.session_id,
            "url": url,
            "action": action_name,
        }

        if action_name == "setViewport":
            action_name = "resize"
            act_params["action"] = action_name
            width = None
            height = None
            if isinstance(value, list) and len(value) >= 2:
                width, height = value[0], value[1]
            if width is not None and height is not None:
                act_params["width"] = int(width)
                act_params["height"] = int(height)

        if action_name in element_actions:
            ref_id = orchestrator._selector_to_ref_id.get(selector or "")
            snapshot_id = orchestrator._active_snapshot_id
            if not ref_id or not snapshot_id:
                error_msg = "[ref_required] snapshot_id + ref_id required for element actions"
                orchestrator.last_action_error = error_msg
                print(f"Action execution failed: {error_msg}")
                return False
            act_params["snapshot_id"] = snapshot_id
            act_params["ref_id"] = ref_id
            act_params["verify"] = True
            if selector:
                act_params["selector_hint"] = selector
            if value is not None:
                act_params["value"] = value
        else:
            if action_name == "goto" and url:
                act_params["value"] = url
            elif action_name == "evaluate":
                if value is not None:
                    act_params["fn"] = value
            elif value is not None:
                act_params["value"] = value
            if action_name == "wait" and selector:
                act_params["selector"] = selector

        payload = {"action": "browser_act", "params": act_params}

        response = execute_mcp_action_with_recovery(
            raw_base_url=orchestrator.mcp_config.host_url,
            action="browser_act",
            params=act_params,
            timeout=90,
            attempts=2,
            is_transport_error=getattr(orchestrator, "_is_mcp_transport_error", None),
            recover_host=getattr(orchestrator, "_recover_mcp_host", None),
            context=f"orchestrator_action:{action_name}",
        )
        data = response.payload if not hasattr(response, "json") else response.json()

        success = bool(data.get("success", False))
        effective = bool(data.get("effective", True))
        if success and not effective:
            success = False
        if not success:
            error_msg = str(
                data.get("reason")
                or data.get("message")
                or data.get("detail")
                or data.get("error")
                or "Unknown error"
            )
            print(f"Action execution failed: {error_msg}")
            orchestrator.last_action_error = error_msg

        if success and orchestrator._screenshot_callback:
            screenshot = data.get("screenshot", "")
            click_position = data.get("click_position")
            if screenshot:
                orchestrator._screenshot_callback(screenshot, click_position)

        return success

    except Exception as e:
        error_msg = str(e)
        print(f"Action execution error: {error_msg}")
        orchestrator.last_action_error = error_msg
        return False


def execute_coordinate_click(orchestrator, x: int, y: int, url: str) -> bool:
    """
    Execute a click at specific coordinates.
    """
    click_script = (
        f"(() => {{ const el = document.elementFromPoint({int(x)}, {int(y)}); "
        "if (!el) return false; el.click(); return true; }})()"
    )
    payload = {
        "action": "browser_act",
        "params": {
            "session_id": orchestrator.session_id,
            "url": url,
            "action": "evaluate",
            "fn": click_script,
        },
    }

    try:
        response = execute_mcp_action_with_recovery(
            raw_base_url=orchestrator.mcp_config.host_url,
            action="browser_act",
            params=dict(payload.get("params") or {}),
            timeout=90,
            attempts=2,
            is_transport_error=getattr(orchestrator, "_is_mcp_transport_error", None),
            recover_host=getattr(orchestrator, "_recover_mcp_host", None),
            context="orchestrator_coordinate_click",
        )
        data = response.payload if not hasattr(response, "json") else response.json()
        return data.get("success", False)
    except Exception as e:
        print(f"Coordinate click failed: {e}")
        return False
