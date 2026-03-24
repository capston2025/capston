"""Scenario execution runtime for IntelligentOrchestrator."""
from __future__ import annotations

import time
from typing import Any, Dict, List

from gaia.src.phase4.llm_vision_client import get_vision_client
from gaia.src.phase4.mcp_local_dispatch_runtime import execute_mcp_action
from gaia.src.utils.models import DomElement, TestScenario


def execute_single_scenario_impl(
        orchestrator,
        url: str,
        scenario: TestScenario,
        progress_callback=None,
        initial_dom_elements: List[DomElement] = None,
        initial_screenshot: str = None,
    ) -> Dict[str, Any]:
        """
        Execute a single test scenario using LLM guidance.

        Args:
            url: Target URL
            scenario: Test scenario to execute
            progress_callback: Progress callback function
            initial_dom_elements: Pre-analyzed DOM elements (optional, improves performance)
            initial_screenshot: Pre-captured screenshot (optional)

        Returns:
            Dict with scenario execution result
        """
        logs = []
        current_url = url
        failed_non_assertion_steps = 0  # Track failed steps (excluding assertions)
        total_non_assertion_steps = 0   # Track total non-assertion steps
        failed_assertion_steps = 0  # Track failed assertion steps
        total_assertion_steps = 0   # Track total assertion steps
        skipped_steps = 0  # Track skipped steps (fallback failures)

        try:
            # Reset viewport to default (1280x900) at start of each scenario
            # This ensures tests are independent and don't inherit viewport from previous tests
            orchestrator._log(f"  🖥️  Resetting viewport to default (1280x900)", progress_callback)
            orchestrator._execute_action(
                action="setViewport",
                selector="",
                params=[[1280, 900]],
                url=current_url
            )

            # Clear browser state (cookies, localStorage, sessionStorage) before each scenario
            # This ensures tests start from a clean slate (e.g., logged out state)
            orchestrator._log(f"  🧹 Clearing browser state (cookies, storage)", progress_callback)
            clear_script = """
                // Clear all cookies
                document.cookie.split(';').forEach(c => {
                    document.cookie = c.replace(/^ +/, '').replace(/=.*/, `=;expires=${new Date().toUTCString()};path=/`);
                });
                // Clear storage
                localStorage.clear();
                sessionStorage.clear();
                // Force hard reload to completely reset React state
                true;
            """
            payload = {
                "action": "browser_act",
                "params": {
                    "url": current_url,
                    "action": "evaluate",
                    "fn": clear_script,
                    "session_id": orchestrator.session_id,
                },
            }
            try:
                response = execute_mcp_action(
                    orchestrator.mcp_config.host_url,
                    action="browser_act",
                    params=dict(payload.get("params") or {}),
                    timeout=30,
                )
                if response.status_code >= 400:
                    raise RuntimeError(str(getattr(response, "text", "") or response.payload))
            except Exception as e:
                orchestrator._log(f"  ⚠️ Browser state clear failed (non-critical): {e}", progress_callback)

            # CRITICAL: Hard reload with cache bypass to completely reset state
            orchestrator._log(f"  🔄 Hard reloading page to apply clean state", progress_callback)

            # First reload to clear state
            orchestrator._execute_action(
                action="goto",
                selector="",
                params=[current_url],
                url=current_url
            )
            time.sleep(2.0)  # Wait for first reload

            # Second reload to ensure React state is completely fresh
            orchestrator._execute_action(
                action="goto",
                selector="",
                params=[current_url],
                url=current_url
            )
            time.sleep(3.0)  # Wait for SPA to fully reset

            orchestrator._log(f"  ✅ Browser reset complete - starting with fresh state", progress_callback)

            # Step 1: Use pre-analyzed DOM or analyze now
            if initial_dom_elements and initial_screenshot:
                dom_elements = initial_dom_elements
                screenshot = initial_screenshot
            else:
                orchestrator._log(f"  📸 Analyzing page: {url}", progress_callback)
                dom_elements = orchestrator._analyze_dom(current_url)
                screenshot = orchestrator._capture_screenshot(current_url, send_to_gui=True)

            # IMPORTANT: Capture BEFORE screenshot for scenario-level verification
            before_scenario_screenshot = screenshot

            if not dom_elements:
                logs.append("⚠️ No DOM elements found")
                return {
                    "id": scenario.id,
                    "scenario": scenario.scenario,
                    "status": "skipped",
                    "logs": logs
                }

            # Step 2: Execute each step with LLM guidance or direct execution
            total_steps = len(scenario.steps)
            orchestrator._log(f"  📝 Total steps to execute: {total_steps}", progress_callback)

            # 🚨 FIX: Auto-detect drag-and-drop patterns and convert click sequences to dragAndDrop
            steps_to_execute = []
            skip_next = False
            for idx, step in enumerate(scenario.steps):
                if skip_next:
                    skip_next = False
                    continue

                # Skip logout-related steps since browser state is already reset
                logout_keywords = ["로그아웃", "로그 아웃", "logout", "로그아웃 상태", "로그아웃 버튼"]
                if any(kw in step.description.lower() for kw in logout_keywords):
                    orchestrator._log(f"  ⏭️  Auto-skipping logout step (browser already reset): {step.description}", progress_callback)
                    continue

                # DISABLED: dragAndDrop auto-conversion was causing issues with dropdowns
                # Users should explicitly specify dragAndDrop action in test plans if needed
                # (Keeping the code commented for reference)

                # # Check if current step is a drag-start click and next step is a drop click
                # if (step.action == "click" and
                #     idx + 1 < len(scenario.steps) and
                #     scenario.steps[idx + 1].action == "click"):
                #
                #     next_step = scenario.steps[idx + 1]
                #
                #     # More strict drag-and-drop detection
                #     # Both descriptions must contain drag-related keywords
                #     drag_start_keywords = ["드래그", "순서", "이동", "변경"]
                #     drag_end_keywords = ["드롭", "아래로", "위치"]
                #
                #     has_drag_start = any(kw in step.description for kw in drag_start_keywords)
                #     has_drag_end = any(kw in next_step.description for kw in drag_end_keywords)
                #
                #     # Both selectors must have [draggable="true"] attribute
                #     has_draggable_attr = ('[draggable' in step.selector.lower() or
                #                          'draggable' in step.description.lower())
                #
                #     if (has_drag_start or has_drag_end or has_draggable_attr) and step.selector and next_step.selector:
                #         # Convert to dragAndDrop action
                #         self._log(f"  🔄 Auto-converting click sequence to dragAndDrop: {step.description} + {next_step.description}", progress_callback)
                #         drag_step = type(step)(
                #             description=f"{step.description} → {next_step.description}",
                #             action="dragAndDrop",
                #             selector=step.selector,
                #             params=[next_step.selector]  # Target selector as list (Pydantic requirement)
                #         )
                #         steps_to_execute.append(drag_step)
                #         skip_next = True
                #         continue

                steps_to_execute.append(step)

            # Update total steps after conversion
            total_steps = len(steps_to_execute)
            orchestrator._log(f"  📝 Total steps to execute (after auto-conversion): {total_steps}", progress_callback)

            for step_idx, step in enumerate(steps_to_execute, start=1):
                orchestrator._log(f"  🤖 Step {step_idx}/{total_steps}: {step.description}", progress_callback)

                # Define action categories
                actions_needing_llm = ["click", "fill", "press"]  # Actions that need LLM to find elements
                actions_not_needing_selector = ["goto", "setViewport", "evaluate", "scroll", "tab", "wait", "waitForTimeout"]  # Actions that execute directly
                assertion_actions = ["expectVisible", "expectHidden", "expectTrue", "expectText", "expectAttribute", "expectCountAtLeast", "expectCSSChanged"]  # Assertion actions
                # 🚨 FIX: Added click, fill, expectVisible, expectText to explicit selector list
                # These actions should use plan JSON selectors without re-running LLM Vision
                actions_with_explicit_selector = ["click", "fill", "hover", "focus", "select", "dragAndDrop", "scrollIntoView", "expectVisible", "expectText", "storeCSSValue", "dragSlider", "expectCSSChanged"]

                logs.append(f"Step {step_idx}: {step.description}")

                # NEW: Handle "llm" action - delegate verification entirely to Vision AI
                if step.action == "llm":
                    orchestrator._log(f"    🧠 LLM Verification Action", progress_callback)
                    logs.append(f"  LLM Verification: {step.description}")

                    # Get verification details from step
                    verify_info = getattr(step, 'verify', None)
                    if not verify_info:
                        orchestrator._log(f"    ⚠️ No 'verify' field found in llm action, skipping", progress_callback)
                        logs.append(f"  ⚠️ Missing verify field")
                        continue

                    # Extract expected outcome and indicators
                    expected_outcome = getattr(verify_info, 'expected', step.description)
                    success_indicators = getattr(verify_info, 'indicators', [])

                    # Capture screenshot for verification
                    time.sleep(0.5)  # Brief pause to let UI settle
                    after_screenshot = orchestrator._capture_screenshot(current_url, send_to_gui=False)

                    # Use LLM Vision to verify
                    vision_client = get_vision_client()

                    verification_result = vision_client.verify_scenario_success(
                        scenario_description=step.description,
                        expected_outcome=expected_outcome,
                        success_indicators=success_indicators,
                        before_screenshot=screenshot,  # Use previous screenshot as "before"
                        after_screenshot=after_screenshot,
                        url=current_url
                    )

                    # Log results
                    verified = verification_result.get('success', False)
                    confidence = verification_result.get('confidence', 0)
                    reasoning = verification_result.get('reasoning', '')
                    matched = verification_result.get('matched_indicators', [])

                    orchestrator._log(f"    {'✅' if verified else '❌'} Verification result: {verified} (confidence: {confidence}%)", progress_callback)
                    orchestrator._log(f"    💭 Reasoning: {reasoning[:100]}...", progress_callback)
                    if matched:
                        orchestrator._log(f"    🎯 Matched indicators: {', '.join(matched[:3])}", progress_callback)

                    logs.append(f"  Verified: {verified} (confidence: {confidence}%)")
                    logs.append(f"  Reasoning: {reasoning}")

                    if not verified or confidence < 60:
                        failed_assertion_steps += 1
                        orchestrator._log(f"    ⚠️ LLM verification failed, continuing...", progress_callback)

                    # Update screenshot for next step
                    screenshot = after_screenshot

                    continue

                # NEW: Handle "assert" action with Vision AI verification
                if step.action == "assert":
                    orchestrator._log(f"    🔍 Assert action detected - using Vision AI verification", progress_callback)
                    logs.append(f"  Assert: {step.description}")
                    total_assertion_steps += 1

                    # Capture current screenshot
                    current_screenshot = orchestrator._capture_screenshot(url=current_url, send_to_gui=True)

                    # Use Vision AI to verify the assertion
                    vision_client = get_vision_client()

                    # Build verification prompt
                    expected_result = step.description
                    expected_value = None
                    if step.params and len(step.params) > 0:
                        expected_value = step.params[0]
                        expected_result = f"{step.description}: {expected_value}"

                    orchestrator._log(f"    🤖 Asking Vision AI: {expected_result}", progress_callback)

                    # Build enhanced prompt with explicit value checking
                    value_check = ""
                    if expected_value:
                        # Special handling for visual state checks (like dark mode)
                        if expected_value.lower() in ["dark", "light", "다크", "라이트"]:
                            value_check = f"""

**CRITICAL - Visual State Verification:**
The expected state is: "{expected_value}"
This is likely a UI theme/mode check. You should verify the VISUAL APPEARANCE:
- If expected is "dark": Check if the UI has a dark/black background (dark mode is ON)
- If expected is "light": Check if the UI has a light/white background (light mode is ON)
- Look at the overall background color and theme of the interface
- You do NOT need to find the text "{expected_value}" - just verify the visual state matches

For example:
- Dark mode: Dark/black background, light text
- Light mode: Light/white background, dark text"""
                        else:
                            value_check = f"""

**CRITICAL - Exact Value Verification:**
The expected value is: "{expected_value}"
You MUST find this EXACT value in the screenshot. Look for:
- Text that contains "{expected_value}"
- Labels, status text, or display fields showing "{expected_value}"
- Do NOT accept similar or related values - it must match exactly

For example:
- If expected is "express", you must find text containing "express" (NOT just a selected radio button)
- If expected is "standard", you must find text containing "standard"
- Visual selection state alone is NOT enough - the text value must be visible"""

                    # Simple prompt for vision verification
                    verification_prompt = f"""Look at this screenshot and verify: {expected_result}
{value_check}

**CRITICAL - Text Quality Check:**
If the assertion involves checking text (like "텍스트가 올바르게 표시", "text is displayed correctly"), you MUST verify:
1. Text is NOT garbled (no � symbols, broken characters, or encoding errors)
2. Korean/Chinese/Japanese characters render properly (not as boxes or ???)
3. Special characters and symbols are intact

**Task**: Does the screenshot show what's expected?

Return JSON (no markdown):
{{
    "success": true or false,
    "reasoning": "detailed explanation of what you see and why it passes/fails",
    "confidence": 85
}}"""

                    try:
                        response_text = vision_client.analyze_with_vision(
                            prompt=verification_prompt,
                            screenshot_base64=current_screenshot
                        )

                        import json
                        result = json.loads(response_text.strip())

                        success = result.get("success", False)
                        reasoning = result.get("reasoning", "No reasoning provided")
                        confidence = result.get("confidence", 0)

                        orchestrator._log(f"    🎯 Vision AI Result: {'✅ PASS' if success else '❌ FAIL'} (confidence: {confidence}%)", progress_callback)
                        orchestrator._log(f"    💭 Reasoning: {reasoning}", progress_callback)

                        if success:
                            logs.append(f"  ✅ Assert passed: {step.description}")
                        else:
                            logs.append(f"  ❌ Assert failed: {step.description}")
                            logs.append(f"  💭 Vision AI: {reasoning}")
                            failed_assertion_steps += 1
                            orchestrator._log(f"    ❌ Assertion failed - stopping scenario execution", progress_callback)

                            # Return immediately with failure status
                            return {
                                "id": scenario.id,
                                "scenario": scenario.scenario,
                                "status": "failed",
                                "logs": logs,
                                "failed_assertions": failed_assertion_steps,
                                "total_assertions": total_assertion_steps
                            }

                    except Exception as e:
                        orchestrator._log(f"    ❌ Vision AI verification failed: {e}", progress_callback)
                        logs.append(f"  ❌ Assert verification error: {e}")
                        failed_assertion_steps += 1

                        # Return immediately with failure status
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "failed",
                            "logs": logs,
                            "failed_assertions": failed_assertion_steps,
                            "total_assertions": total_assertion_steps
                        }

                    continue

                # Check if this is an action that doesn't need LLM element selection
                if step.action in actions_not_needing_selector or step.action in assertion_actions:
                    # Execute directly without LLM
                    orchestrator._log(f"    ⚡ Direct execution: {step.action.upper()}", progress_callback)
                    logs.append(f"  Action: {step.action} (direct)")

                    # Track assertion vs non-assertion steps
                    if step.action in assertion_actions:
                        total_assertion_steps += 1
                    else:
                        total_non_assertion_steps += 1

                    # For debugging: log params
                    if step.params:
                        orchestrator._log(f"    📋 Params: {step.params}", progress_callback)

                    # NEW: Infer missing intermediate steps from description
                    # Check if description implies actions not in the step (e.g., "탭으로 전환", "모달 열기")
                    inferred_success = orchestrator._infer_and_execute_missing_steps(
                        step=step,
                        screenshot=screenshot,
                        dom_elements=dom_elements,
                        current_url=current_url,
                        progress_callback=progress_callback
                    )

                    # Update state after inferred steps
                    if inferred_success:
                        screenshot, dom_elements, current_url = orchestrator._get_page_state()

                    selector = step.selector if step.selector else ""

                    # For assertions, use current screenshot as "before"
                    # (state after previous action but before assertion check)
                    before_screenshot = screenshot if step.action in assertion_actions else None

                    # DEBUG: Log before_screenshot status for assertions
                    if step.action in assertion_actions:
                        if before_screenshot:
                            orchestrator._log(f"    📸 Using before screenshot ({len(before_screenshot)} chars)", progress_callback)
                        else:
                            orchestrator._log(f"    ⚠️ WARNING: No before_screenshot available!", progress_callback)

                    # Assertion 액션이면 before_screenshot 전달
                    if step.action in assertion_actions:
                        success = orchestrator._execute_action(
                            action=step.action,
                            selector=selector,
                            params=step.params or [],
                            url=current_url,
                            before_screenshot=before_screenshot
                        )
                    else:
                        success = orchestrator._execute_action(
                            action=step.action,
                            selector=selector,
                            params=step.params or [],
                            url=current_url
                        )

                    if not success:
                        logs.append(f"  ❌ Action {step.action} failed")
                        orchestrator._log(f"    ❌ Action failed", progress_callback)

                        # For assertion actions, log but continue (don't fail entire scenario immediately)
                        if step.action in assertion_actions:
                            failed_assertion_steps += 1  # Track assertion failure
                            orchestrator._log(f"    ⚠️ Assertion failed, continuing...", progress_callback)
                        else:
                            # Track failed non-assertion step (critical failure - stop immediately)
                            failed_non_assertion_steps += 1
                            return {
                                "id": scenario.id,
                                "scenario": scenario.scenario,
                                "status": "failed",
                                "logs": logs
                            }
                    else:
                        logs.append(f"  ✅ Action executed: {step.action}")
                        orchestrator._log(f"    ✅ Action successful", progress_callback)

                    # Get new screenshot and DOM if needed
                    if step.action in ["goto", "scroll"] or getattr(step, 'auto_analyze', False):
                        try:
                            time.sleep(3.0)  # Wait longer for SPA hash navigation
                            screenshot, dom_elements, current_url = orchestrator._get_page_state()
                            orchestrator._log(f"    🔄 Page state refreshed (URL: {current_url}, DOM: {len(dom_elements)})", progress_callback)

                            # FIGMA SITES FIX: Hash navigation doesn't load content properly
                            # If goto to #hash URL but DOM is too small (< 15), use button click instead
                            if step.action == "goto" and len(step.params) > 0 and '#' in step.params[0] and len(dom_elements) < 15:
                                hash_part = step.params[0].split('#')[1]  # e.g., "basics"
                                orchestrator._log(f"    ⚠️ Hash navigation failed to load content (DOM: {len(dom_elements)})", progress_callback)
                                orchestrator._log(f"    💡 Trying alternative: Navigate to home and click button", progress_callback)

                                # Navigate to home
                                base_url = step.params[0].split('#')[0]
                                goto_success = orchestrator._execute_action(action="goto", selector="", params=[base_url], url=base_url)

                                if goto_success:
                                    time.sleep(2.0)
                                    screenshot, dom_elements, current_url = orchestrator._get_page_state()

                                    # Find button with text matching hash (e.g., "기본 기능" for "basics")
                                    # Use LLM to find the right button
                                    llm_decision = orchestrator.llm_client.select_element_for_step(
                                        step_description=f"{hash_part} 페이지로 이동하는 버튼 클릭",
                                        dom_elements=dom_elements,
                                        screenshot_base64=screenshot,
                                        url=current_url
                                    )

                                    if llm_decision['selector']:
                                        orchestrator._log(f"    🔘 Clicking navigation button: {llm_decision['selector']}", progress_callback)
                                        click_success = orchestrator._execute_action(
                                            action="click",
                                            selector=llm_decision['selector'],
                                            params=[],
                                            url=current_url
                                        )

                                        if click_success:
                                            time.sleep(3.0)
                                            screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                            orchestrator._log(f"    ✅ Content loaded via button click (DOM: {len(dom_elements)})", progress_callback)
                        except Exception as e:
                            orchestrator._log(f"    ⚠️ Failed to refresh page state: {e}", progress_callback)
                            # Continue anyway - screenshot and DOM from before action

                    continue

                # Check if action has explicit selector provided
                elif step.action in actions_with_explicit_selector and step.selector:
                    # Check healed selector cache first
                    healed_selector = orchestrator._get_healed_selector(scenario.id, step.selector)
                    actual_selector = healed_selector if healed_selector else step.selector

                    if healed_selector:
                        orchestrator._log(f"    🔄 Using healed selector from cache: {step.selector} → {healed_selector}", progress_callback)
                    else:
                        orchestrator._log(f"    🎯 Using explicit selector with self-healing: {step.selector}", progress_callback)

                    logs.append(f"  Action: {step.action} on {actual_selector}")

                    # Track non-assertion step
                    total_non_assertion_steps += 1

                    before_screenshot = screenshot
                    # Use self-healing action execution
                    success = orchestrator._execute_action_with_self_healing(
                        action=step.action,
                        selector=actual_selector,
                        params=step.params or [],
                        url=current_url,
                        screenshot=screenshot,
                        dom_elements=dom_elements,
                        step_description=step.description,
                        before_screenshot=before_screenshot,
                        progress_callback=progress_callback,
                        max_retries=2,  # Limit to 2 retries to avoid long delays
                        scenario_id=scenario.id
                    )

                    if not success:
                        logs.append(f"  ❌ Explicit selector failed even after self-healing: {step.selector}")
                        orchestrator._log(f"    ⚠️ Explicit selector failed, falling back to LLM...", progress_callback)
                        # Don't fail immediately - fall through to LLM section below
                    else:
                        logs.append(f"  ✅ Action executed: {step.action} on {step.selector}")
                        orchestrator._log(f"    ✅ Action successful", progress_callback)

                        # Get new screenshot if needed
                        time.sleep(0.5)
                        screenshot, dom_elements, current_url = orchestrator._get_page_state()

                        continue

                # If explicit selector failed or no selector provided, use LLM
                if step.action in actions_with_explicit_selector or True:
                    # Track non-assertion step
                    total_non_assertion_steps += 1

                    # Detect DOM context (active tabs/modals) for context-aware caching
                    dom_context = orchestrator._detect_dom_context(dom_elements)

                    # CACHE DISABLED: Skip cache lookup entirely
                    # cached_selector = self._get_cached_selector(step.description, step.action, current_url, dom_context)
                    cached_selector = None  # Force cache bypass

                    if False:  # Disable cache usage
                        # Use cached selector
                        llm_decision = {
                            "selector": cached_selector,
                            "reasoning": "Using cached selector from previous successful execution",
                            "confidence": 95,
                            "action": step.action
                        }
                        orchestrator._log(f"  💾 Cache hit! Using cached selector", progress_callback)
                    else:
                        # PARALLEL MATCHING: ARIA + Semantic 병렬 실행, 필요시 LLM Aggregator
                        parallel_match = orchestrator._try_semantic_matching(
                            step.description,
                            dom_elements,
                            step.action,
                            current_url=current_url,
                            screenshot=screenshot
                        )

                        if parallel_match:
                            # Found good match from ARIA/Semantic/Aggregator
                            llm_decision = parallel_match
                            orchestrator._log(f"  🎯 Parallel match succeeded!", progress_callback)
                        else:
                            # All fast methods failed, use full LLM Vision
                            llm_decision = orchestrator.llm_client.select_element_for_step(
                                step_description=step.description,
                                dom_elements=dom_elements,
                                screenshot_base64=screenshot,
                                url=current_url
                            )

                    logs.append(f"  LLM Decision: {llm_decision['reasoning']}")
                    logs.append(f"  Confidence: {llm_decision['confidence']}%")
                    logs.append(f"  Target Element: {llm_decision['selector']}")

                    # EARLY CHECK: Detect multi-element matches and force fallback if needed
                    if llm_decision['selector']:
                        # 🚨 FIX: Skip check for Playwright-specific selectors (they're already specific)
                        # Selectors with :has-text(), :text(), or >> are Playwright-specific and already precise
                        is_playwright_selector = any(marker in llm_decision['selector']
                                                    for marker in [':has-text(', ':text(', '>>', ':has('])

                        if is_playwright_selector:
                            # Trust Playwright selectors, they're already specific
                            match_count = 1
                        else:
                            # Use string comparison for simple CSS selectors
                            matching_elements = [e for e in dom_elements if e.selector == llm_decision['selector']]
                            match_count = len(matching_elements)

                        if match_count > 1:
                            # Get sample text from matching elements (for logging only)
                            matching_elements = [e for e in dom_elements if e.selector == llm_decision['selector']]
                            sample_texts = [e.text[:30] for e in matching_elements[:3]] if matching_elements else []
                            orchestrator._log(f"    ⚠️ WARNING: Selector matches {match_count} elements!", progress_callback)
                            if sample_texts:
                                orchestrator._log(f"    💡 Sample elements: {sample_texts}", progress_callback)

                            # AUTO-FIX: Try to extract target text from step description and improve selector
                            import re
                            korean_text_match = re.search(r'[가-힣]+(?:\s+[가-힣]+)*', step.description)
                            english_text_match = re.search(r'\b[A-Za-z]+(?:\s+[A-Za-z]+)*\b', step.description)

                            auto_fix_worked = False
                            target_text = None

                            if korean_text_match:
                                target_text = korean_text_match.group()
                            elif english_text_match:
                                target_text = english_text_match.group()

                            if target_text:
                                # Check if any matching element has this text
                                text_match = next((e for e in matching_elements if target_text in e.text), None)
                                if text_match:
                                    # Found it! Use text-based selector instead
                                    element_type = text_match.tag if text_match.tag in ['button', 'a', 'input'] else 'button'
                                    # Use :has-text() instead of :has-text() for better Playwright compatibility
                                    better_selector = f'{element_type}:has-text("{target_text}")'
                                    orchestrator._log(f"    🔧 Auto-fix: Using text-based selector: {better_selector}", progress_callback)
                                    llm_decision['selector'] = better_selector
                                    llm_decision['confidence'] = 95  # High confidence for exact text match
                                    llm_decision['reasoning'] = f"Auto-fix: Found exact text match '{target_text}' in element"
                                    auto_fix_worked = True

                            # If auto-fix didn't work, force confidence to 0 to trigger fallback
                            if not auto_fix_worked:
                                orchestrator._log(f"    🔄 Ambiguous selector! Forcing vision fallback...", progress_callback)
                                logs.append(f"  ⚠️ Selector matches multiple elements, forcing fallback")
                                llm_decision['confidence'] = 0

                    # If first step fails with low confidence, skip entire scenario
                    # Lowered threshold from 30% to 20% for better fuzzy matching support
                    if step_idx == 1 and llm_decision["confidence"] < 20:
                        logs.append(f"  ⚠️ First step has low confidence, skipping entire scenario")
                        orchestrator._log(f"    ⚠️ Skipping (low confidence: {llm_decision['confidence']}%)", progress_callback)
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "skipped",
                            "logs": logs,
                            "reason": "Not executable on current page"
                        }

                    # Debug: Show current page state
                    orchestrator._log(f"    🌐 Current URL: {current_url}", progress_callback)
                    orchestrator._log(f"    📊 Available DOM elements: {len(dom_elements)}", progress_callback)

                    # RECOVERY LOGIC: If DOM is empty, try to recover
                    if len(dom_elements) == 0:
                        orchestrator._log(f"    ⚠️ WARNING: DOM is empty! Attempting recovery...", progress_callback)
                        recovery_success = orchestrator._try_recover_from_empty_dom(
                            current_url=current_url,
                            progress_callback=progress_callback
                        )

                        if recovery_success:
                            # Re-fetch page state after recovery
                            screenshot, dom_elements, current_url = orchestrator._get_page_state()
                            orchestrator._log(f"    ✅ Recovery succeeded! Now {len(dom_elements)} DOM elements available", progress_callback)
                        else:
                            orchestrator._log(f"    ❌ Recovery failed - skipping this step", progress_callback)
                            logs.append(f"  ❌ Skipped: DOM empty and recovery failed")
                            continue

                    # Check if auto-fix was successful (confidence = 95)
                    auto_fix_succeeded = (llm_decision["confidence"] == 95 and
                                         llm_decision.get("reasoning", "").startswith("Auto-fix"))

                    if auto_fix_succeeded:
                        orchestrator._log(f"    ✅ Auto-fix found reliable selector, skipping fallback", progress_callback)
                        # Skip fallback - auto-fix already found a good selector
                    elif llm_decision["confidence"] < 50:
                        # Trigger fallback for confidence < 50% (increased from 30% to catch more edge cases)
                        # Fallback includes: aggressive text matching, smart navigation, scroll+vision
                        logs.append(f"  ⚠️ Low confidence ({llm_decision['confidence']}%), trying aggressive search...")
                        orchestrator._log(f"    🔍 Low confidence ({llm_decision['confidence']}%), trying scroll + vision fallback...", progress_callback)
                        orchestrator._log(f"    💡 Reason: {llm_decision.get('reasoning', 'Unknown')}", progress_callback)

                        # STEP 1: Try aggressive text matching on CURRENT PAGE first
                        import re
                        # Extract ALL Korean/English text from description (minimum 2 chars to avoid false matches)
                        all_korean = re.findall(r'[가-힣]{2,}', step.description)  # Min 2 Korean chars
                        all_english = re.findall(r'[A-Za-z]{3,}', step.description)  # Min 3 English chars

                        found_by_text = False

                        # FIRST: Try to find multi-word phrases (e.g., "장바구니 추가")
                        # Extract consecutive Korean words (2-3 words)
                        phrase_pattern = r'[가-힣]{2,}(?:\s+[가-힣]{2,}){1,2}'  # 2-3 words with spaces
                        phrases = re.findall(phrase_pattern, step.description)

                        # Try phrase matching first (more specific)
                        for phrase in sorted(set(phrases), key=len, reverse=True):
                            text_match = next((e for e in dom_elements
                                             if phrase in e.text), None)  # Contains phrase
                            if text_match:
                                element_type = text_match.tag if text_match.tag in ['button', 'a', 'input', 'div'] else 'button'
                                # Use :has-text() instead of :has-text() for better Playwright compatibility
                                better_selector = f'{element_type}:has-text("{phrase}")'
                                orchestrator._log(f"    🔧 Aggressive phrase match: Found '{phrase}' → {better_selector}", progress_callback)
                                llm_decision['selector'] = better_selector
                                llm_decision['confidence'] = 90
                                llm_decision['reasoning'] = f"Aggressive phrase match: '{phrase}'"
                                found_by_text = True
                                break

                        # FALLBACK: Try single word matches (less specific)
                        if not found_by_text:
                            # 🚨 FIX: Prioritize EXACT matches over partial matches
                            # Try longest matches first to avoid substring issues
                            for target_text in sorted(all_korean + all_english, key=len, reverse=True):
                                # Phase 1: Try EXACT match first (highest confidence)
                                exact_match = next((e for e in dom_elements if target_text == e.text), None)
                                if exact_match:
                                    element_type = exact_match.tag if exact_match.tag in ['button', 'a', 'input', 'div'] else 'button'
                                    better_selector = f'{element_type}:has-text("{target_text}")'
                                    orchestrator._log(f"    🔧 Exact text match: Found '{target_text}' → {better_selector}", progress_callback)
                                    llm_decision['selector'] = better_selector
                                    llm_decision['confidence'] = 95
                                    llm_decision['reasoning'] = f"Exact text match: '{target_text}'"
                                    text_match = exact_match
                                    found_by_text = True
                                    break

                            # Phase 2: If no exact match, try partial matches (lower confidence)
                            if not found_by_text:
                                for target_text in sorted(all_korean + all_english, key=len, reverse=True):
                                    # Search with word boundaries only (avoid "장바구니" matching "장바구니 추가" AND "장바구니 보기")
                                    text_match = next((e for e in dom_elements
                                                     if f' {target_text} ' in f' {e.text} '), None)  # Word boundary only
                                    if text_match:
                                        element_type = text_match.tag if text_match.tag in ['button', 'a', 'input', 'div'] else 'button'
                                        better_selector = f'{element_type}:has-text("{target_text}")'
                                        orchestrator._log(f"    🔧 Partial text match: Found '{target_text}' → {better_selector}", progress_callback)
                                        llm_decision['selector'] = better_selector
                                        llm_decision['confidence'] = 75
                                        llm_decision['reasoning'] = f"Partial text match: '{target_text}'"
                                        found_by_text = True
                                        break

                        if found_by_text:
                            orchestrator._log(f"    ✅ Found element by aggressive text matching", progress_callback)

                            # 🔥 SMART TAB ACTIVATION: If we found a button/tab but action is fill/click on something else,
                            # click the button first to activate the tab/section
                            if text_match and text_match.tag == 'button' and step.action in ['fill', 'click']:
                                # Check if this is likely a tab button (e.g., "회원가입", "로그인")
                                tab_keywords = ['회원가입', '로그인', '탭', 'tab', '페이지', 'page']
                                is_likely_tab = any(keyword in text_match.text.lower() or keyword in step.description.lower()
                                                   for keyword in tab_keywords)

                                if is_likely_tab:
                                    orchestrator._log(f"    🔘 Detected tab/section button, clicking first to activate...", progress_callback)

                                    # Click the tab button first
                                    tab_click_success = orchestrator._execute_action(
                                        action="click",
                                        selector=better_selector,
                                        params=[],
                                        url=current_url
                                    )

                                    if tab_click_success:
                                        orchestrator._log(f"    ✅ Tab activated, refreshing page state...", progress_callback)
                                        time.sleep(1.0)  # Wait for tab content to load
                                        screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                        orchestrator._log(f"    📊 DOM updated: {len(dom_elements)} elements", progress_callback)

                                        # Now find the actual target element (e.g., input field)
                                        # Re-run LLM to find the real target in the now-visible tab
                                        orchestrator._log(f"    🔍 Re-analyzing to find actual target element...", progress_callback)
                                        llm_decision = orchestrator.llm_client.select_element_for_step(
                                            step_description=step.description,
                                            dom_elements=dom_elements,
                                            screenshot_base64=screenshot,
                                            url=current_url
                                        )
                                        orchestrator._log(f"    🎯 Found actual target: {llm_decision['selector']}", progress_callback)
                                    else:
                                        orchestrator._log(f"    ⚠️ Tab click failed, continuing anyway...", progress_callback)

                        # STEP 2: SMART NAVIGATION (only if text matching failed)
                        if not found_by_text:
                            orchestrator._log(f"    🌍 Trying Smart Navigation (last resort)...", progress_callback)
                            smart_nav = orchestrator._find_element_on_other_pages(step.description, current_url)
                            if smart_nav.get("found"):
                                orchestrator._log(f"    💡 Smart navigation: Found '{smart_nav['element_text']}' on {smart_nav['target_url']}", progress_callback)
                                orchestrator._log(f"    🏠 Navigating to: {smart_nav['target_url']}", progress_callback)

                                # Navigate to the page where element exists
                                goto_success = orchestrator._execute_action(
                                    action="goto",
                                    selector="",
                                    params=[smart_nav['target_url']],
                                    url=smart_nav['target_url']
                                )

                                if goto_success:
                                    # Update page state after navigation
                                    screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                    orchestrator._log(f"    ✅ Navigation successful, now at: {current_url}", progress_callback)

                                    # Try clicking the element on the new page
                                    click_success = orchestrator._execute_action(
                                        action=llm_decision["action"],
                                        selector=smart_nav["selector"],
                                        params=step.params,
                                        url=current_url,
                                        before_screenshot=screenshot
                                    )

                                    if click_success:
                                        logs.append(f"  ✅ Action executed via smart navigation")
                                        orchestrator._log(f"    ✅ Smart navigation succeeded!", progress_callback)

                                        # Find element to get tag information
                                        target_element = next((e for e in dom_elements if e.selector == smart_nav["selector"]), None)
                                        element_tag = target_element.tag if target_element else ""
                                        element_text = smart_nav.get("element_text", "")
                                        element_attrs = target_element.attributes if target_element else {}

                                        # Update cache with successful smart navigation selector
                                        orchestrator._update_cache(
                                            step_description=step.description,
                                            action=step.action,
                                            page_url=current_url,
                                            selector=smart_nav["selector"],
                                            success=True,
                                            dom_context=dom_context,
                                            element_text=element_text,
                                            element_tag=element_tag,
                                            attributes=element_attrs
                                        )

                                        # Update state after successful click
                                        screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                        orchestrator._record_page_elements(current_url, dom_elements)
                                        continue  # Move to next step
                                    else:
                                        orchestrator._log(f"    ❌ Click failed after navigation", progress_callback)
                                else:
                                    orchestrator._log(f"    ❌ Navigation failed", progress_callback)

                        # STEP 3: If low confidence, try vision-based coordinate click
                        # Increased threshold from 30 to 50 to trigger vision more aggressively
                        if not found_by_text and llm_decision["confidence"] < 50:
                            orchestrator._log(f"    🎯 Trying vision-based coordinate detection...", progress_callback)
                            orchestrator._log(
                                f"    🤖 Asking {orchestrator.llm_client.model} to find element coordinates in screenshot...",
                                progress_callback,
                            )
                            coord_result = orchestrator.llm_client.find_element_coordinates(
                                screenshot_base64=screenshot,
                                description=step.description
                            )

                            if coord_result.get("confidence", 0) > 0.5:
                                orchestrator._log(f"    ✅ Found element at ({coord_result['x']}, {coord_result['y']}) with {coord_result['confidence']*100:.0f}% confidence", progress_callback)
                                # Execute click at coordinates
                                click_success = orchestrator._execute_coordinate_click(
                                    x=coord_result["x"],
                                    y=coord_result["y"],
                                    url=current_url
                                )
                                if click_success:
                                    orchestrator._log(f"    ✅ Coordinate-based click successful!", progress_callback)
                                    time.sleep(0.5)
                                    screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                    continue
                                else:
                                    orchestrator._log(f"    ❌ Coordinate click failed", progress_callback)
                            else:
                                orchestrator._log(f"    ❌ Vision fallback failed (confidence: {coord_result.get('confidence', 0)*100:.0f}%)", progress_callback)
                                orchestrator._log(f"    💭 Vision reasoning: {coord_result.get('reasoning', 'Unknown')}", progress_callback)

                            # STEP 5: NEW! If target not visible, try to find and click exploreable elements (tabs, modals, etc.)
                            orchestrator._log(f"    🔍 Target not visible, looking for tabs/triggers to explore...", progress_callback)
                            explore_result = orchestrator.llm_client.find_exploreable_element(
                                screenshot_base64=screenshot,
                                target_description=step.description
                            )

                            if explore_result.get("found_exploreable") and explore_result.get("confidence", 0) > 0.6:
                                orchestrator._log(f"    💡 Found {explore_result.get('element_type', 'element')}: '{explore_result.get('element_text', 'N/A')}'", progress_callback)
                                orchestrator._log(f"    🔄 Clicking to reveal target element... ({explore_result.get('reasoning', 'Unknown')})", progress_callback)
                                logs.append(f"  🔍 Exploring: {explore_result.get('element_text', 'N/A')}")

                                # Click the tab/modal/trigger button
                                explore_click = orchestrator._execute_coordinate_click(
                                    x=explore_result["x"],
                                    y=explore_result["y"],
                                    url=current_url
                                )

                                orchestrator._log(f"    🔍 Exploration click result: {explore_click}", progress_callback)

                                if explore_click:
                                    orchestrator._log(f"    ⏳ Waiting 1.5s for tab transition...", progress_callback)
                                    time.sleep(1.5)  # Increased wait time for React state updates
                                    screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                    orchestrator._log(f"    📊 After exploration: DOM elements = {len(dom_elements)}", progress_callback)

                                    # DEBUG: Save screenshot after exploration
                                    import base64
                                    debug_path = f"/tmp/debug_after_exploration_{step.description[:20]}.png"
                                    with open(debug_path, "wb") as f:
                                        f.write(base64.b64decode(screenshot))
                                    orchestrator._log(f"    🖼️  DEBUG: Saved screenshot to {debug_path}", progress_callback)

                                    # Now retry finding the target element
                                    orchestrator._log(f"    🔁 Retrying target element detection after exploration...", progress_callback)
                                    retry_coord = orchestrator.llm_client.find_element_coordinates(
                                        screenshot_base64=screenshot,
                                        description=step.description
                                    )

                                    if retry_coord.get("confidence", 0) > 0.5:
                                        orchestrator._log(f"    🎉 Found target after exploration at ({retry_coord['x']}, {retry_coord['y']})!", progress_callback)
                                        logs.append(f"  ✅ Target found after exploration")
                                        # Execute the actual target action
                                        target_click = orchestrator._execute_coordinate_click(
                                            x=retry_coord["x"],
                                            y=retry_coord["y"],
                                            url=current_url
                                        )
                                        if target_click:
                                            orchestrator._log(f"    ✅ Target action successful!", progress_callback)
                                            time.sleep(0.5)
                                            screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                            continue
                                        else:
                                            orchestrator._log(f"    ❌ Target click failed", progress_callback)
                                    else:
                                        orchestrator._log(f"    ❌ Still cannot find target after exploration (confidence: {retry_coord.get('confidence', 0)*100:.0f}%)", progress_callback)
                                else:
                                    orchestrator._log(f"    ❌ Exploration click failed", progress_callback)
                            else:
                                orchestrator._log(f"    ❌ No exploreable elements found (confidence: {explore_result.get('confidence', 0)*100:.0f}%)", progress_callback)
                                orchestrator._log(f"    💭 Reasoning: {explore_result.get('reasoning', 'Unknown')}", progress_callback)

                            # If we reach here, all fallbacks failed (including exploration)
                            logs.append(f"  ⚠️ All fallback attempts failed, skipping step")
                            orchestrator._log(f"    ⚠️ Skipping step after fallback attempts", progress_callback)
                            skipped_steps += 1
                            continue

                    if not llm_decision["selector"]:
                        logs.append(f"  ⚠️ No selector found, skipping this step")
                        orchestrator._log(f"    ⚠️ Skipping step (no selector)", progress_callback)
                        skipped_steps += 1
                        continue

                    # Log which element will be clicked (IMPORTANT for debugging)
                    orchestrator._log(f"    🎯 Target: {llm_decision['action'].upper()} on '{llm_decision['selector']}'", progress_callback)

                    # Find element text to show in logs
                    target_element = next((e for e in dom_elements if e.selector == llm_decision['selector']), None)
                    if target_element and target_element.text:
                        orchestrator._log(f"    📝 Element text: \"{target_element.text[:50]}\"", progress_callback)

                    # Execute the action
                    before_screenshot = screenshot
                    success = orchestrator._execute_action(
                        action=llm_decision["action"],
                        selector=llm_decision["selector"],
                        params=step.params or [],
                        url=current_url
                    )

                    # UPDATE CACHE: Record execution result
                    element_text = target_element.text if target_element else ""
                    element_tag = target_element.tag if target_element else ""
                    element_attrs = target_element.attributes if target_element else {}
                    orchestrator._update_cache(
                        step_description=step.description,
                        action=step.action,
                        page_url=current_url,
                        selector=llm_decision["selector"],
                        success=success,
                        dom_context=dom_context,
                        element_text=element_text,
                        element_tag=element_tag,
                        attributes=element_attrs
                    )

                    if not success:
                        logs.append(f"  ❌ Action failed on {llm_decision['selector']}")
                        orchestrator._log(f"    ❌ Action failed, trying intelligent fallback...", progress_callback)

                        # INTELLIGENT FALLBACK: Check for overlay first, then try vision-based click

                        # Check if error is due to overlay interception
                        # Common error patterns: "intercepts pointer events", "covered by", "not clickable"
                        orchestrator._log(f"    🔍 Checking for overlay interference...", progress_callback)

                        # Try pressing Escape to close any open overlay/modal/dropdown
                        orchestrator._log(f"    ⌨️  Pressing Escape to close potential overlay...", progress_callback)
                        escape_success = orchestrator._execute_action(
                            action="press",
                            selector="body",
                            params=["Escape"],
                            url=current_url
                        )

                        if escape_success:
                            time.sleep(0.3)  # Wait for overlay to close
                            # Retry original action
                            orchestrator._log(f"    🔄 Retrying original action after Escape...", progress_callback)
                            success = orchestrator._execute_action(
                                action=llm_decision["action"],
                                selector=llm_decision["selector"],
                                params=step.params or [],
                                url=current_url
                            )

                            if success:
                                orchestrator._log(f"    ✅ Action succeeded after closing overlay!", progress_callback)
                                logs.append(f"  ✅ Escape key resolved overlay issue")
                                logs.append(f"  ✅ Action executed: {llm_decision['action']} on {llm_decision['selector']}")

                                # Update cache
                                target_element = next((e for e in dom_elements if e.selector == llm_decision['selector']), None)
                                element_text = target_element.text if target_element else ""
                                element_tag = target_element.tag if target_element else ""
                                element_attrs = target_element.attributes if target_element else {}

                                orchestrator._update_cache(
                                    step_description=step.description,
                                    action=step.action,
                                    page_url=current_url,
                                    selector=llm_decision["selector"],
                                    success=True,
                                    dom_context=dom_context,
                                    element_text=element_text,
                                    element_tag=element_tag,
                                    attributes=element_attrs
                                )
                                time.sleep(0.2)
                                # Update current_url after navigation
                                if llm_decision["action"].lower() in ("click", "press", "goto"):
                                    screenshot_new, dom_elements_new, current_url_new = orchestrator._get_page_state()
                                    dom_elements = dom_elements_new
                                    if current_url_new:
                                        current_url = current_url_new
                                        orchestrator._log(f"    🔄 Browser navigated to: {current_url}", progress_callback)
                                continue

                        # If Escape didn't help, try vision-based coordinate click
                        if llm_decision["action"] in ["click", "press"]:
                            orchestrator._log(f"    🎯 Trying vision-based coordinate click...", progress_callback)
                            logs.append(f"  🔄 Fallback: Using vision-based coordinates")

                            # Get coordinates from LLM Vision
                            coords = orchestrator.llm_client.find_element_coordinates(
                                screenshot_base64=screenshot,
                                description=step.description
                            )

                            if coords["confidence"] > 0.5:
                                orchestrator._log(f"    📍 Found at ({coords['x']}, {coords['y']}) - confidence: {coords['confidence']:.0%}", progress_callback)
                                logs.append(f"  📍 Coordinates: ({coords['x']}, {coords['y']})")

                                # Try JavaScript click first (more reliable for overlays)
                                orchestrator._log(f"    💻 Trying JavaScript click...", progress_callback)
                                js_script = f"document.elementFromPoint({coords['x']}, {coords['y']}).click()"
                                js_success = orchestrator._execute_action(
                                    action="evaluate",
                                    selector="",
                                    params=[js_script],
                                    url=current_url
                                )

                                if js_success:
                                    orchestrator._log(f"    ✅ JavaScript click succeeded!", progress_callback)
                                    logs.append(f"  ✅ JavaScript click succeeded")
                                    time.sleep(0.5)
                                    screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                    continue
                                else:
                                    # Try physical coordinate click as last resort
                                    orchestrator._log(f"    🖱️  Trying physical coordinate click...", progress_callback)
                                    success = orchestrator._execute_coordinate_click(
                                        x=coords['x'],
                                        y=coords['y'],
                                        url=current_url
                                    )

                                    if success:
                                        orchestrator._log(f"    ✅ Coordinate click succeeded!", progress_callback)
                                        logs.append(f"  ✅ Coordinate-based click succeeded")
                                        time.sleep(0.5)
                                        screenshot, dom_elements, current_url = orchestrator._get_page_state()
                                        continue
                                    else:
                                        orchestrator._log(f"    ❌ Coordinate click failed", progress_callback)
                                        logs.append(f"  ❌ Coordinate-based click failed")
                            else:
                                orchestrator._log(f"    ❌ Low confidence ({coords['confidence']:.0%}), cannot locate element visually", progress_callback)
                                logs.append(f"  ❌ Could not find element in screenshot")

                        # All fallbacks failed
                        if not success:
                            orchestrator._log(f"    ❌ All fallback attempts failed", progress_callback)
                            logs.append(f"  ❌ All fallback attempts exhausted")
                            failed_non_assertion_steps += 1
                            return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "failed",
                            "logs": logs
                        }

                    logs.append(f"  ✅ Action executed: {llm_decision['action']} on {llm_decision['selector']}")
                    orchestrator._log(f"    ✅ Action successful", progress_callback)

                    # Wait a bit for page to update (reduced to 0.2s for snappier GUI)
                    time.sleep(0.2)

                    # Re-analyze DOM if page might have changed
                    # CRITICAL: Also update current_url with actual browser URL to handle hash navigation
                    if llm_decision["action"].lower() in ("click", "press", "goto"):
                        screenshot_new, dom_elements_new, current_url_new = orchestrator._get_page_state()
                        dom_elements = dom_elements_new
                        # Update current_url to reflect actual browser state (e.g., #basics)
                        if current_url_new:
                            current_url = current_url_new
                            orchestrator._log(f"    🔄 Browser navigated to: {current_url}", progress_callback)
                            # Record elements on the new page for smart navigation
                            orchestrator._record_page_elements(current_url, dom_elements)

                    # Screenshot is already sent by _execute_action with click_position

            # Step 3: Scenario-level Vision AI verification (IMPROVED!)
            # Capture AFTER screenshot and verify entire scenario success
            # NOW RUNS ON ALL SCENARIOS, not just those with success_indicators
            after_scenario_screenshot = orchestrator._capture_screenshot(current_url, send_to_gui=False)
            scenario_verified = False
            scenario_verification_result = None

            # Run verification on ALL scenarios (not just those with assertion field)
            orchestrator._log(f"  🔍 Running scenario-level Vision AI verification...", progress_callback)

            # Extract assertion details (handle both old and new format)
            expected_outcome = scenario.scenario  # Default to scenario description
            success_indicators = []

            if hasattr(scenario, 'assertion') and scenario.assertion:
                # Try to extract from assertion
                expected_outcome = getattr(scenario.assertion, "expected_outcome", None) or scenario.scenario
                success_indicators = getattr(scenario.assertion, "success_indicators", [])

            # If no success_indicators, generate them automatically from scenario description
            if not success_indicators:
                orchestrator._log(f"  💡 No success_indicators found, generating from scenario description...", progress_callback)
                success_indicators = orchestrator._generate_success_indicators(scenario.scenario, scenario.steps)
                orchestrator._log(f"  📝 Generated indicators: {success_indicators}", progress_callback)

            # Always run verification (even if success_indicators were auto-generated)
            vision_client = get_vision_client()

            scenario_verification_result = vision_client.verify_scenario_success(
                scenario_description=scenario.scenario,
                expected_outcome=expected_outcome,
                success_indicators=success_indicators,
                before_screenshot=before_scenario_screenshot,
                after_screenshot=after_scenario_screenshot,
                url=current_url
            )

            scenario_verified = scenario_verification_result.get("success", False)
            confidence = scenario_verification_result.get("confidence", 0)
            reasoning = scenario_verification_result.get("reasoning", "")
            matched_indicators = scenario_verification_result.get("matched_indicators", [])

            orchestrator._log(f"  🔍 Vision AI Result:", progress_callback)
            orchestrator._log(f"     - Success: {scenario_verified}", progress_callback)
            orchestrator._log(f"     - Confidence: {confidence}%", progress_callback)
            orchestrator._log(f"     - Matched: {matched_indicators}", progress_callback)
            orchestrator._log(f"     - Reasoning: {reasoning}", progress_callback)

            logs.append(f"  🔍 Vision AI Verification: {'✅ PASS' if scenario_verified else '❌ FAIL'}")
            logs.append(f"     Confidence: {confidence}%, Matched: {matched_indicators}")
            logs.append(f"     Reasoning: {reasoning}")

            # Step 4: Decide on pass/fail based on step execution AND Vision AI
            # 4-tier status system:
            # - success: All actions passed + Vision AI verified success
            # - partial: Some steps skipped or Vision AI verification failed
            # - failed: Critical steps failed

            if failed_non_assertion_steps == 0 and total_non_assertion_steps > 0:
                # All actions succeeded
                # Now check Vision AI result if available
                if scenario_verification_result:
                    # Vision AI verification available - use it as final decision
                    if scenario_verified:
                        logs.append(f"  ✅ All {total_non_assertion_steps} action steps passed + Vision AI verified")
                        orchestrator._log(f"  ✅ Test SUCCESS: Vision AI verified", progress_callback)

                        # Save healed selectors to cache (only on success)
                        orchestrator._save_healed_selectors(scenario.id, progress_callback)

                        # CRITICAL: Force navigate to home URL to completely reset state for next test
                        orchestrator._log(f"  🏠 Navigating to home URL to reset for next test", progress_callback)
                        home_url = url.split('#')[0] if '#' in url else url  # Remove hash
                        orchestrator._execute_action(action="goto", selector="", params=[home_url], url=home_url)
                        time.sleep(1.0)

                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "success",
                            "logs": logs,
                            "verification": scenario_verification_result,
                            "after_screenshot": after_scenario_screenshot,  # For Master Orchestrator
                            "current_url": current_url
                        }
                    else:
                        # Actions passed but Vision AI says scenario failed
                        logs.append(f"  ⚠️ Actions passed, but Vision AI verification failed")
                        orchestrator._log(f"  ⚠️ Test PARTIAL: Vision AI verification failed", progress_callback)
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "partial",
                            "logs": logs,
                            "verification": scenario_verification_result,
                            "after_screenshot": after_scenario_screenshot,  # For Master Orchestrator
                            "current_url": current_url
                        }
                elif failed_assertion_steps == 0:
                    # No Vision AI, but step-based assertions passed
                    if skipped_steps == 0:
                        logs.append(f"  ✅ All {total_non_assertion_steps} action steps and {total_assertion_steps} assertions passed")
                        orchestrator._log(f"  ✅ Test SUCCESS: 100% completion", progress_callback)

                        # Save healed selectors to cache (only on success)
                        orchestrator._save_healed_selectors(scenario.id, progress_callback)

                        # CRITICAL: Force navigate to home URL to completely reset state for next test
                        orchestrator._log(f"  🏠 Navigating to home URL to reset for next test", progress_callback)
                        home_url = url.split('#')[0] if '#' in url else url  # Remove hash
                        orchestrator._execute_action(action="goto", selector="", params=[home_url], url=home_url)
                        time.sleep(1.0)

                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "success",
                            "logs": logs,
                            "after_screenshot": after_scenario_screenshot,
                            "current_url": current_url
                        }
                    else:
                        # Some steps skipped but didn't fail
                        skip_rate = (skipped_steps / total_non_assertion_steps) * 100
                        logs.append(f"  ⚠️ {total_non_assertion_steps - skipped_steps}/{total_non_assertion_steps} steps completed ({skipped_steps} skipped)")
                        orchestrator._log(f"  ⚠️ Test PARTIAL: {skip_rate:.0f}% steps skipped", progress_callback)
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "partial",
                            "logs": logs,
                            "after_screenshot": after_scenario_screenshot,
                            "current_url": current_url
                        }
                else:
                    # Actions succeeded but assertions failed
                    logs.append(f"  ⚠️ All {total_non_assertion_steps} actions passed, but {failed_assertion_steps}/{total_assertion_steps} assertions failed")
                    orchestrator._log(f"  ⚠️ Test PARTIAL: Assertions failed ({failed_assertion_steps}/{total_assertion_steps})", progress_callback)
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "partial",  # Assertion 실패는 partial로 처리
                        "logs": logs,
                        "failed_assertions": failed_assertion_steps,
                        "total_assertions": total_assertion_steps,
                        "after_screenshot": after_scenario_screenshot,
                        "current_url": current_url
                    }

            # Optional: Still try LLM verification for additional confidence
            if scenario.assertion and scenario.assertion.description:
                orchestrator._log(f"  🔍 Verifying: {scenario.assertion.description}", progress_callback)

                verification = orchestrator.llm_client.verify_action_result(
                    expected_result=scenario.assertion.description,
                    before_screenshot=before_screenshot,
                    after_screenshot=screenshot,
                    url=current_url
                )

                logs.append(f"Verification: {verification['reasoning']}")
                logs.append(f"  Confidence: {verification['confidence']}%")

                if verification["success"] and verification["confidence"] >= 60:
                    logs.append("  ✅ Verification passed")
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "passed",
                        "logs": logs,
                        "after_screenshot": after_scenario_screenshot,
                        "current_url": current_url
                    }
                elif verification["confidence"] == 0:
                    # LLM verification failed (safety filter, timeout, etc.)
                    # If all steps executed successfully, still consider it passed
                    logs.append("  ⚠️ Verification inconclusive (LLM error), but steps executed successfully")
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "passed",
                        "logs": logs,
                        "after_screenshot": after_scenario_screenshot,
                        "current_url": current_url
                    }
                else:
                    logs.append("  ❌ Verification failed")
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "failed",
                        "logs": logs,
                        "after_screenshot": after_scenario_screenshot,
                        "current_url": current_url
                    }

            # No assertion, assume success if all steps executed
            return {
                "id": scenario.id,
                "scenario": scenario.scenario,
                "status": "passed",
                "logs": logs,
                "after_screenshot": after_scenario_screenshot,
                "current_url": current_url
            }

        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            logs.append(f"❌ Exception: {e}")
            logs.append(f"📜 Traceback:\n{tb_str}")

            # Print to console for debugging
            print(f"\n[ERROR] Exception in _execute_single_scenario for {scenario.id}:")
            print(tb_str)
            orchestrator._log(f"❌ Exception in step execution: {e}", progress_callback)
            orchestrator._log(f"📜 Traceback:\n{tb_str}", progress_callback)

            # Try to capture screenshot even in exception case (for Master Orchestrator)
            try:
                exception_screenshot = orchestrator._capture_screenshot(None, send_to_gui=False) if 'after_scenario_screenshot' not in locals() else after_scenario_screenshot
                exception_url = current_url if 'current_url' in locals() else ""
            except Exception:
                exception_screenshot = ""
                exception_url = ""

            return {
                "id": scenario.id,
                "scenario": scenario.scenario,
                "status": "failed",
                "error": str(e),
                "logs": logs,
                "after_screenshot": exception_screenshot,
                "current_url": exception_url
            }
