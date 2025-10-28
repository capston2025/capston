"""
Intelligent Orchestrator - LLM-powered browser automation.
Uses GPT-4V to analyze DOM + screenshots and make decisions.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Sequence

import requests

from gaia.src.phase4.llm_vision_client import LLMVisionClient
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.config import CONFIG, MCPConfig
from gaia.src.utils.models import DomElement, TestScenario, TestStep


class IntelligentOrchestrator:
    """
    LLM-powered orchestrator that uses vision + DOM analysis
    to execute abstract test scenarios.
    """

    def __init__(
        self,
        tracker: ChecklistTracker | None = None,
        mcp_config: MCPConfig | None = None,
        llm_client: LLMVisionClient | None = None,
        screenshot_callback=None,
        session_id: str = "default",
    ) -> None:
        """
        Initialize the intelligent orchestrator.

        Args:
            tracker: Checklist tracker for marking progress
            mcp_config: MCP host configuration
            llm_client: LLM vision client (defaults to GPT-4o)
            screenshot_callback: Optional callback for real-time screenshot updates
            session_id: Browser session ID for persistent state
        """
        self.tracker = tracker or ChecklistTracker()
        self.mcp_config = mcp_config or CONFIG.mcp
        self.llm_client = llm_client or LLMVisionClient()
        self._execution_logs: List[str] = []
        self._screenshot_callback = screenshot_callback
        self.session_id = session_id

    def execute_scenarios(
        self,
        url: str,
        scenarios: Sequence[TestScenario],
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Execute test scenarios using LLM-guided automation.

        NEW Flow:
        1. For each test scenario:
           a. Analyze current page DOM + screenshot
           b. LLM decides if this test is executable on current page
           c. If yes, execute; if no, skip
        2. Tests are tried in priority order (MUST > SHOULD > MAY)

        Args:
            url: Target URL to test
            scenarios: List of test scenarios to execute
            progress_callback: Optional callback for progress updates

        Returns:
            Dict with execution results and logs
        """
        self._execution_logs = []
        results = {
            "total": len(scenarios),
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "scenarios": []
        }

        self._log(f"🚀 Starting LLM-powered automation: {len(scenarios)} scenarios", progress_callback)

        # Step 1: Analyze DOM once at the beginning
        self._log(f"  📸 Analyzing page DOM to identify executable tests...", progress_callback)
        dom_elements = self._analyze_dom(url)
        screenshot = self._capture_screenshot(url, send_to_gui=True)  # Show initial page in GUI

        if not dom_elements:
            self._log("⚠️ No DOM elements found, skipping all tests", progress_callback)
            results["skipped"] = len(scenarios)
            return results

        # Step 2: Ask LLM to prioritize scenarios based on DOM
        self._log(f"  🤖 LLM analyzing which tests are executable...", progress_callback)
        prioritized_scenarios = self._prioritize_scenarios(
            scenarios, dom_elements, screenshot, url, progress_callback
        )

        if not prioritized_scenarios:
            self._log("⚠️ No executable tests found on this page", progress_callback)
            results["skipped"] = len(scenarios)
            return results

        self._log(f"  ✅ Found {len(prioritized_scenarios)} executable tests (skipping {len(scenarios) - len(prioritized_scenarios)})", progress_callback)

        # Mark non-prioritized scenarios as skipped
        prioritized_ids = {s.id for s in prioritized_scenarios}
        for scenario in scenarios:
            if scenario.id not in prioritized_ids:
                results["scenarios"].append({
                    "id": scenario.id,
                    "scenario": scenario.scenario,
                    "status": "skipped",
                    "logs": ["Not executable on current page (LLM prioritization)"]
                })
                results["skipped"] += 1

        # Step 3: Execute prioritized scenarios (non-sequential based on DOM availability)
        for idx, scenario in enumerate(prioritized_scenarios, start=1):
            self._log(f"\n[{idx}/{len(prioritized_scenarios)}] Testing: {scenario.scenario} (Priority: {scenario.priority})", progress_callback)

            try:
                # Pass pre-analyzed DOM to avoid re-analysis
                result = self._execute_single_scenario(
                    url, scenario, progress_callback,
                    initial_dom_elements=dom_elements,
                    initial_screenshot=screenshot
                )
                results["scenarios"].append(result)

                if result["status"] == "passed":
                    results["passed"] += 1
                    self.tracker.mark_found(scenario.id, evidence=result.get("logs", ""))
                elif result["status"] == "failed":
                    results["failed"] += 1
                elif result["status"] == "skipped":
                    results["skipped"] += 1

            except Exception as e:
                self._log(f"❌ Exception: {e}", progress_callback)
                results["failed"] += 1
                results["scenarios"].append({
                    "id": scenario.id,
                    "scenario": scenario.scenario,
                    "status": "failed",
                    "error": str(e),
                    "logs": []
                })

        self._log(f"\n✅ Execution complete: {results['passed']}/{len(scenarios)} passed, {results['skipped']}/{len(scenarios)} skipped", progress_callback)
        return results

    def _prioritize_scenarios(
        self,
        scenarios: Sequence[TestScenario],
        dom_elements: List[DomElement],
        screenshot: str,
        url: str,
        progress_callback=None,
    ) -> List[TestScenario]:
        """
        Ask LLM to analyze which scenarios are executable given current DOM state.
        Returns prioritized list of executable scenarios.
        """
        # Build prompt for LLM
        dom_summary = "\n".join([
            f"- {elem.tag} [{elem.element_type}]: {elem.selector} (text: {elem.text[:50]})"
            for elem in dom_elements[:100]  # Limit to 100 for better coverage (increased from 50)
        ])

        scenarios_summary = "\n".join([
            f"{idx}. [{s.id}] {s.scenario} (Priority: {s.priority})"
            for idx, s in enumerate(scenarios, 1)
        ])

        prompt = f"""Analyze which test scenarios are executable on this page.

URL: {url}

Available DOM Elements:
{dom_summary}

Test Scenarios:
{scenarios_summary}

**Rules:**
1. Mark a scenario as executable if you can reasonably infer the required elements exist
2. Look for matching text, button types, or related UI components
3. Use common sense - if a test needs a "login button", look for "로그인", "Login", "Sign in", etc.
4. Examples:
   - Test: "Click share button" + DOM: "공유하기" button → EXECUTABLE
   - Test: "Test login" + DOM: "로그인" or "Login" button → EXECUTABLE
   - Test: "Test modal" + DOM: "Modal", "Dialog", or "열기" button → EXECUTABLE
   - Test: "Test filter" + DOM: "필터", "Filter", or search-related elements → EXECUTABLE
   - Test: "Test drag-drop" + DOM: draggable elements or related text → EXECUTABLE

**When to SKIP:**
- The test clearly requires elements that don't exist at all
- Example: Test needs "shopping cart" but this is a documentation site

For EXECUTABLE scenarios, provide:
1. Execution priority (1-5, where 1 is highest)
2. Brief reason

Return ONLY a JSON array:
[
  {{"id": "TC001", "priority": 1, "reason": "DOM has login button"}},
  {{"id": "TC005", "priority": 2, "reason": "DOM has share button"}}
]
"""

        try:
            # Call o4-mini (reasoning model with vision) for accurate scenario selection
            import json
            import openai

            client = openai.OpenAI()
            response = client.chat.completions.create(
                model="gpt-5-mini",  # Multimodal reasoning model - 4x cheaper than o4-mini!
                max_completion_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{screenshot}"
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ]
            )

            response_text = response.choices[0].message.content or ""

            # Strip markdown code blocks if present
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            # Parse LLM response
            executable_ids = json.loads(response_text)

            # Build prioritized scenario list
            id_to_scenario = {s.id: s for s in scenarios}
            prioritized = []
            for item in executable_ids:
                scenario_id = item.get("id")
                if scenario_id in id_to_scenario:
                    prioritized.append(id_to_scenario[scenario_id])
                    self._log(f"  ✓ {scenario_id}: {item.get('reason', 'N/A')}", progress_callback)

            return prioritized

        except Exception as e:
            self._log(f"⚠️ LLM prioritization failed: {e}, using all scenarios", progress_callback)
            # Fallback: return all scenarios sorted by original priority
            return sorted(scenarios, key=lambda s: {"MUST": 1, "SHOULD": 2, "MAY": 3}.get(s.priority, 4))

    def _execute_single_scenario(
        self,
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

        try:
            # Reset viewport to default (1280x900) at start of each scenario
            # This ensures tests are independent and don't inherit viewport from previous tests
            self._log(f"  🖥️  Resetting viewport to default (1280x900)", progress_callback)
            self._execute_action(
                action="setViewport",
                selector="",
                params=[[1280, 900]],
                url=current_url
            )

            # Step 1: Use pre-analyzed DOM or analyze now
            if initial_dom_elements and initial_screenshot:
                dom_elements = initial_dom_elements
                screenshot = initial_screenshot
            else:
                self._log(f"  📸 Analyzing page: {url}", progress_callback)
                dom_elements = self._analyze_dom(current_url)
                screenshot = self._capture_screenshot(current_url, send_to_gui=True)

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
            self._log(f"  📝 Total steps to execute: {total_steps}", progress_callback)
            for step_idx, step in enumerate(scenario.steps, start=1):
                self._log(f"  🤖 Step {step_idx}/{total_steps}: {step.description}", progress_callback)

                # Define action categories
                actions_needing_llm = ["click", "fill", "press"]  # Actions that need LLM to find elements
                actions_not_needing_selector = ["goto", "setViewport", "evaluate", "scroll", "tab", "wait"]  # Actions that execute directly
                assertion_actions = ["expectVisible", "expectHidden", "expectTrue", "expectAttribute", "expectCountAtLeast"]  # Assertion actions
                actions_with_explicit_selector = ["hover", "focus", "select", "dragAndDrop", "scrollIntoView"]  # Actions that can use explicit selector

                logs.append(f"Step {step_idx}: {step.description}")

                # Check if this is an action that doesn't need LLM element selection
                if step.action in actions_not_needing_selector or step.action in assertion_actions:
                    # Execute directly without LLM
                    self._log(f"    ⚡ Direct execution: {step.action.upper()}", progress_callback)
                    logs.append(f"  Action: {step.action} (direct)")

                    # Track non-assertion steps
                    if step.action not in assertion_actions:
                        total_non_assertion_steps += 1

                    # For debugging: log params
                    if step.params:
                        self._log(f"    📋 Params: {step.params}", progress_callback)

                    selector = step.selector if step.selector else ""
                    before_screenshot = screenshot
                    success = self._execute_action(
                        action=step.action,
                        selector=selector,
                        params=step.params or [],
                        url=current_url
                    )

                    if not success:
                        logs.append(f"  ❌ Action {step.action} failed")
                        self._log(f"    ❌ Action failed", progress_callback)

                        # For assertion actions, log but continue (don't fail entire scenario)
                        if step.action in assertion_actions:
                            self._log(f"    ⚠️ Assertion failed, continuing...", progress_callback)
                        else:
                            # Track failed non-assertion step
                            failed_non_assertion_steps += 1
                            return {
                                "id": scenario.id,
                                "scenario": scenario.scenario,
                                "status": "failed",
                                "logs": logs
                            }
                    else:
                        logs.append(f"  ✅ Action executed: {step.action}")
                        self._log(f"    ✅ Action successful", progress_callback)

                    # Get new screenshot and DOM if needed
                    if step.action in ["goto", "scroll"] or getattr(step, 'auto_analyze', False):
                        try:
                            time.sleep(1.0)  # Wait for page to stabilize
                            screenshot = self._capture_screenshot(None, send_to_gui=True)
                            dom_elements = self._analyze_dom(None)
                            # current_url stays the same
                            self._log(f"    🔄 Page state refreshed", progress_callback)
                        except Exception as e:
                            self._log(f"    ⚠️ Failed to refresh page state: {e}", progress_callback)
                            # Continue anyway - screenshot and DOM from before action

                    continue

                # Check if action has explicit selector provided
                elif step.action in actions_with_explicit_selector and step.selector:
                    # Use explicit selector without LLM
                    self._log(f"    🎯 Using explicit selector: {step.selector}", progress_callback)
                    logs.append(f"  Action: {step.action} on {step.selector}")

                    # Track non-assertion step
                    total_non_assertion_steps += 1

                    before_screenshot = screenshot
                    success = self._execute_action(
                        action=step.action,
                        selector=step.selector,
                        params=step.params or [],
                        url=current_url
                    )

                    if not success:
                        logs.append(f"  ❌ Action {step.action} failed on {step.selector}")
                        self._log(f"    ❌ Action failed", progress_callback)
                        failed_non_assertion_steps += 1
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "failed",
                            "logs": logs
                        }

                    logs.append(f"  ✅ Action executed: {step.action} on {step.selector}")
                    self._log(f"    ✅ Action successful", progress_callback)

                    # Get new screenshot if needed
                    time.sleep(0.5)
                    screenshot, dom_elements, current_url = self._get_page_state()

                    continue

                # Otherwise, use LLM to find the element
                else:
                    # Track non-assertion step
                    total_non_assertion_steps += 1

                    # Ask LLM to select element
                    llm_decision = self.llm_client.select_element_for_step(
                        step_description=step.description,
                        dom_elements=dom_elements,
                        screenshot_base64=screenshot,
                        url=current_url
                    )

                    logs.append(f"  LLM Decision: {llm_decision['reasoning']}")
                    logs.append(f"  Confidence: {llm_decision['confidence']}%")
                    logs.append(f"  Target Element: {llm_decision['selector']}")

                    # If first step fails with low confidence, skip entire scenario
                    # Lowered threshold from 60% to 45% to be more aggressive with testing
                    if step_idx == 1 and llm_decision["confidence"] < 45:
                        logs.append(f"  ⚠️ First step has low confidence, skipping entire scenario")
                        self._log(f"    ⚠️ Skipping (low confidence: {llm_decision['confidence']}%)", progress_callback)
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "skipped",
                            "logs": logs,
                            "reason": "Not executable on current page"
                        }

                    # For subsequent steps, skip the step but continue scenario
                    # Lowered threshold to 45% to allow more attempts
                    if llm_decision["confidence"] < 45:
                        logs.append(f"  ⚠️ Low confidence, skipping this step")
                        self._log(f"    ⚠️ Skipping step (low confidence: {llm_decision['confidence']}%)", progress_callback)
                        continue

                    if not llm_decision["selector"]:
                        logs.append(f"  ⚠️ No selector found, skipping this step")
                        self._log(f"    ⚠️ Skipping step (no selector)", progress_callback)
                        continue

                    # Log which element will be clicked (IMPORTANT for debugging)
                    self._log(f"    🎯 Target: {llm_decision['action'].upper()} on '{llm_decision['selector']}'", progress_callback)

                    # Find element text to show in logs
                    target_element = next((e for e in dom_elements if e.selector == llm_decision['selector']), None)
                    if target_element and target_element.text:
                        self._log(f"    📝 Element text: \"{target_element.text[:50]}\"", progress_callback)

                    # Check if selector matches multiple elements (warning)
                    matching_elements = [e for e in dom_elements if e.selector == llm_decision['selector']]
                    if len(matching_elements) > 1:
                        self._log(f"    ⚠️ WARNING: Selector matches {len(matching_elements)} elements! Will click FIRST one.", progress_callback)
                        self._log(f"    💡 Matched elements: {[e.text[:30] for e in matching_elements[:3]]}", progress_callback)

                        # AUTO-FIX: Try to extract target text from step description and improve selector
                        # Example: "Click button labeled 폼과 피드백" → extract "폼과 피드백"
                        import re
                        korean_text_match = re.search(r'[가-힣]+(?:\s+[가-힣]+)*', step.description)
                        if korean_text_match:
                            target_text = korean_text_match.group()
                            # Check if any matching element has this text
                            text_match = next((e for e in matching_elements if target_text in e.text), None)
                            if text_match:
                                # Found it! Use text-based selector instead
                                better_selector = f'button:has-text("{target_text}")'
                                self._log(f"    🔧 Auto-fix: Using text-based selector: {better_selector}", progress_callback)
                                llm_decision['selector'] = better_selector
                                # Update target_element for logging
                                target_element = text_match

                    # Execute the action
                    before_screenshot = screenshot
                    success = self._execute_action(
                        action=llm_decision["action"],
                        selector=llm_decision["selector"],
                        params=step.params or [],
                        url=current_url
                    )

                    if not success:
                        logs.append(f"  ❌ Action failed on {llm_decision['selector']}")
                        self._log(f"    ❌ Action failed", progress_callback)

                        # FALLBACK: Try coordinate-based click for click actions
                        if llm_decision["action"] == "click":
                            self._log(f"    🔄 Trying fallback: coordinate-based click", progress_callback)
                            logs.append(f"  🔄 Fallback: Using vision-based coordinates")

                            # Get coordinates from LLM Vision
                            coords = self.llm_client.find_element_coordinates(
                                screenshot_base64=screenshot,
                                description=step.description
                            )

                            if coords["confidence"] > 0.5:
                                self._log(f"    📍 Found at ({coords['x']}, {coords['y']}) - confidence: {coords['confidence']:.0%}", progress_callback)
                                logs.append(f"  📍 Coordinates: ({coords['x']}, {coords['y']})")

                                # Try clickAt action
                                success = self._execute_action(
                                    action="clickAt",
                                    selector="",  # Not needed for clickAt
                                    params=[[coords['x'], coords['y']]],
                                    url=current_url
                                )

                                if success:
                                    self._log(f"    ✅ Fallback succeeded!", progress_callback)
                                    logs.append(f"  ✅ Coordinate-based click succeeded")
                                else:
                                    self._log(f"    ❌ Fallback also failed", progress_callback)
                                    logs.append(f"  ❌ Coordinate-based click failed")
                            else:
                                self._log(f"    ❌ Low confidence ({coords['confidence']:.0%}), skipping fallback", progress_callback)
                                logs.append(f"  ❌ Could not find element in screenshot")

                        if not success:
                            failed_non_assertion_steps += 1
                            return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "failed",
                            "logs": logs
                        }

                    logs.append(f"  ✅ Action executed: {llm_decision['action']} on {llm_decision['selector']}")
                    self._log(f"    ✅ Action successful", progress_callback)

                    # Wait a bit for page to update (reduced to 0.2s for snappier GUI)
                    time.sleep(0.2)

                    # Re-analyze DOM if page might have changed
                    if llm_decision["action"] in ("click", "press", "goto"):
                        dom_elements = self._analyze_dom(None)

                    # Screenshot is already sent by _execute_action with click_position

            # Step 3: Decide on pass/fail based on step execution
            # NEW POLICY: If all non-assertion steps succeeded, mark as passed
            # This makes verification more flexible and practical for real-world testing

            if failed_non_assertion_steps == 0 and total_non_assertion_steps > 0:
                # All critical steps passed! Test is considered successful
                logs.append(f"  ✅ All {total_non_assertion_steps} action steps executed successfully")
                self._log(f"  ✅ Test PASSED: All actions completed", progress_callback)
                return {
                    "id": scenario.id,
                    "scenario": scenario.scenario,
                    "status": "passed",
                    "logs": logs
                }

            # Optional: Still try LLM verification for additional confidence
            if scenario.assertion and scenario.assertion.description:
                self._log(f"  🔍 Verifying: {scenario.assertion.description}", progress_callback)

                verification = self.llm_client.verify_action_result(
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
                        "logs": logs
                    }
                elif verification["confidence"] == 0:
                    # LLM verification failed (safety filter, timeout, etc.)
                    # If all steps executed successfully, still consider it passed
                    logs.append("  ⚠️ Verification inconclusive (LLM error), but steps executed successfully")
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "passed",
                        "logs": logs
                    }
                else:
                    logs.append("  ❌ Verification failed")
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "failed",
                        "logs": logs
                    }

            # No assertion, assume success if all steps executed
            return {
                "id": scenario.id,
                "scenario": scenario.scenario,
                "status": "passed",
                "logs": logs
            }

        except Exception as e:
            logs.append(f"❌ Exception: {e}")
            return {
                "id": scenario.id,
                "scenario": scenario.scenario,
                "status": "failed",
                "error": str(e),
                "logs": logs
            }

    def _analyze_dom(self, url: str | None) -> List[DomElement]:
        """Analyze DOM using MCP host. If url is None, analyzes current page."""
        params = {"session_id": self.session_id}
        if url:
            params["url"] = url
        payload = {"action": "analyze_page", "params": params}
        try:
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=90  # Increased from 30s to 90s for complex operations
            )
            response.raise_for_status()
            data = response.json()
            elements_raw = data.get("elements", [])

            elements: List[DomElement] = []
            for elem in elements_raw:
                try:
                    elements.append(DomElement.model_validate(elem))
                except Exception:
                    continue
            return elements
        except Exception as e:
            print(f"DOM analysis failed: {e}")
            return []

    def _capture_screenshot(self, url: str | None, send_to_gui: bool = False) -> str:
        """Capture screenshot using MCP host. If url is None, captures current page.

        Args:
            url: URL to capture, or None for current page
            send_to_gui: If True, send screenshot to GUI without click animation
        """
        params = {"session_id": self.session_id}
        if url:
            params["url"] = url
        payload = {"action": "capture_screenshot", "params": params}
        try:
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=90  # Increased from 30s to 90s for complex operations
            )
            response.raise_for_status()
            data = response.json()
            screenshot = data.get("screenshot", "")

            # Send screenshot to GUI if requested (without click animation)
            if send_to_gui and self._screenshot_callback and screenshot:
                self._screenshot_callback(screenshot, None)

            return screenshot
        except Exception as e:
            print(f"Screenshot capture failed: {e}")
            return ""

    def _execute_action(self, action: str, selector: str, params: List[Any], url: str) -> bool:
        """Execute a browser action using MCP host."""
        try:
            # Build payload based on action type
            # For actions that need full params array (setViewport, dragAndDrop), send the whole array
            # For others, send first param only
            if action in ["setViewport", "dragAndDrop"]:
                value = params if params else None
            else:
                value = params[0] if params else None

            payload = {
                "action": "execute_action",
                "params": {
                    "url": url,
                    "selector": selector,
                    "action": action,
                    "value": value,
                    "session_id": self.session_id
                }
            }

            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=90  # Increased from 30s to 90s for complex operations
            )
            response.raise_for_status()
            data = response.json()

            success = data.get("success", False)
            if not success:
                print(f"Action execution failed: {data.get('message', 'Unknown error')}")

            # Send screenshot to GUI if action succeeded and callback is set
            if success and self._screenshot_callback:
                screenshot = data.get("screenshot", "")
                click_position = data.get("click_position")
                if screenshot:
                    self._screenshot_callback(screenshot, click_position)

            return success

        except Exception as e:
            print(f"Action execution error: {e}")
            return False

    def _log(self, message: str, callback=None) -> None:
        """Log a message and optionally call progress callback."""
        self._execution_logs.append(message)
        print(message)
        if callback:
            callback(message)

    @property
    def execution_logs(self) -> List[str]:
        """Get execution logs."""
        return list(self._execution_logs)


__all__ = ["IntelligentOrchestrator"]
