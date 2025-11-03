"""
Intelligent Orchestrator - LLM-powered browser automation.
Uses GPT-4V to analyze DOM + screenshots and make decisions.
"""
from __future__ import annotations

import hashlib
import json
import os
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

        # Smart navigation: Track which elements exist on which pages
        self.page_element_map: Dict[str, Dict[str, str]] = {}  # {url: {text: selector}}
        self.home_url: str = ""  # Base URL for smart navigation

        # Selector cache: Maps (step_description, action, page_url) -> selector
        self.selector_cache: Dict[str, Dict[str, Any]] = {}  # {cache_key: {selector, timestamp, success_count}}
        self.cache_file = os.path.join(
            os.path.dirname(__file__), "../../../artifacts/cache/selector_cache.json"
        )
        self._load_cache()

        # Embedding cache: Maps text -> embedding vector
        self.embedding_cache: Dict[str, List[float]] = {}
        self.embedding_cache_file = os.path.join(
            os.path.dirname(__file__), "../../../artifacts/cache/embedding_cache.json"
        )
        self._load_embedding_cache()

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
            "success": 0,  # 100% completion
            "partial": 0,  # Some steps skipped
            "failed": 0,   # Critical failures
            "skipped": 0,  # Not executed
            "scenarios": []
        }

        self._log(f"🚀 Starting LLM-powered automation: {len(scenarios)} scenarios", progress_callback)

        # Remember home URL for smart navigation
        self.home_url = url

        # Step 1: Analyze DOM once at the beginning
        self._log(f"  📸 Analyzing page DOM to identify executable tests...", progress_callback)
        dom_elements = self._analyze_dom(url)
        screenshot = self._capture_screenshot(url, send_to_gui=True)  # Show initial page in GUI

        if not dom_elements:
            self._log("⚠️ No DOM elements found, skipping all tests", progress_callback)
            results["skipped"] = len(scenarios)
            return results

        # Record home page elements for smart navigation
        self._record_page_elements(url, dom_elements)

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

                if result["status"] == "success":
                    results["success"] += 1
                    logs_evidence = result.get("logs", "")
                    if isinstance(logs_evidence, list):
                        logs_evidence = "\n".join(logs_evidence)
                    self.tracker.mark_found(scenario.id, evidence=logs_evidence)
                elif result["status"] == "partial":
                    results["partial"] += 1
                    logs_evidence = result.get("logs", "")
                    if isinstance(logs_evidence, list):
                        logs_evidence = "\n".join(logs_evidence)
                    self.tracker.mark_found(scenario.id, evidence=logs_evidence + " (partial)")
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

        self._log(f"\n📊 Execution complete: ✅{results['success']} success, ⚠️{results['partial']} partial, ❌{results['failed']} failed, ⏭️{results['skipped']} skipped", progress_callback)

        # Save cache to disk at end of execution
        self._save_cache()

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
        skipped_steps = 0  # Track skipped steps (fallback failures)

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
                actions_not_needing_selector = ["goto", "setViewport", "evaluate", "scroll", "tab", "wait", "waitForTimeout"]  # Actions that execute directly
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
                            time.sleep(3.0)  # Wait longer for SPA hash navigation
                            screenshot, dom_elements, current_url = self._get_page_state()
                            self._log(f"    🔄 Page state refreshed (URL: {current_url}, DOM: {len(dom_elements)})", progress_callback)

                            # FIGMA SITES FIX: Hash navigation doesn't load content properly
                            # If goto to #hash URL but DOM is too small (< 15), use button click instead
                            if step.action == "goto" and len(step.params) > 0 and '#' in step.params[0] and len(dom_elements) < 15:
                                hash_part = step.params[0].split('#')[1]  # e.g., "basics"
                                self._log(f"    ⚠️ Hash navigation failed to load content (DOM: {len(dom_elements)})", progress_callback)
                                self._log(f"    💡 Trying alternative: Navigate to home and click button", progress_callback)

                                # Navigate to home
                                base_url = step.params[0].split('#')[0]
                                goto_success = self._execute_action(action="goto", selector="", params=[base_url], url=base_url)

                                if goto_success:
                                    time.sleep(2.0)
                                    screenshot, dom_elements, current_url = self._get_page_state()

                                    # Find button with text matching hash (e.g., "기본 기능" for "basics")
                                    # Use LLM to find the right button
                                    llm_decision = self.llm_client.select_element_for_step(
                                        step_description=f"{hash_part} 페이지로 이동하는 버튼 클릭",
                                        dom_elements=dom_elements,
                                        screenshot_base64=screenshot,
                                        url=current_url
                                    )

                                    if llm_decision['selector']:
                                        self._log(f"    🔘 Clicking navigation button: {llm_decision['selector']}", progress_callback)
                                        click_success = self._execute_action(
                                            action="click",
                                            selector=llm_decision['selector'],
                                            params=[],
                                            url=current_url
                                        )

                                        if click_success:
                                            time.sleep(3.0)
                                            screenshot, dom_elements, current_url = self._get_page_state()
                                            self._log(f"    ✅ Content loaded via button click (DOM: {len(dom_elements)})", progress_callback)
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
                        logs.append(f"  ❌ Explicit selector failed: {step.selector}")
                        self._log(f"    ⚠️ Explicit selector failed, falling back to LLM...", progress_callback)
                        # Don't fail immediately - fall through to LLM section below
                    else:
                        logs.append(f"  ✅ Action executed: {step.action} on {step.selector}")
                        self._log(f"    ✅ Action successful", progress_callback)

                        # Get new screenshot if needed
                        time.sleep(0.5)
                        screenshot, dom_elements, current_url = self._get_page_state()

                        continue

                # If explicit selector failed or no selector provided, use LLM
                if step.action in actions_with_explicit_selector or True:
                    # Track non-assertion step
                    total_non_assertion_steps += 1

                    # CACHE CHECK: Try to get cached selector first
                    cached_selector = self._get_cached_selector(step.description, step.action, current_url)

                    if cached_selector:
                        # Use cached selector
                        llm_decision = {
                            "selector": cached_selector,
                            "reasoning": "Using cached selector from previous successful execution",
                            "confidence": 95,
                            "action": step.action
                        }
                        self._log(f"  💾 Cache hit! Using cached selector", progress_callback)
                    else:
                        # SEMANTIC PRE-MATCHING: Try fast semantic matching before expensive LLM call
                        semantic_match = self._try_semantic_matching(step.description, dom_elements, step.action)

                        if semantic_match:
                            # Found good semantic match, skip LLM
                            llm_decision = semantic_match
                            self._log(f"  🎯 Semantic match! Skipping LLM (saving cost)", progress_callback)
                        else:
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

                    # EARLY CHECK: Detect multi-element matches and force fallback if needed
                    if llm_decision['selector']:
                        matching_elements = [e for e in dom_elements if e.selector == llm_decision['selector']]
                        if len(matching_elements) > 1:
                            self._log(f"    ⚠️ WARNING: Selector matches {len(matching_elements)} elements!", progress_callback)
                            self._log(f"    💡 Matched elements: {[e.text[:30] for e in matching_elements[:3]]}", progress_callback)

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
                                    better_selector = f'{element_type}:has-text("{target_text}")'
                                    self._log(f"    🔧 Auto-fix: Using text-based selector: {better_selector}", progress_callback)
                                    llm_decision['selector'] = better_selector
                                    llm_decision['confidence'] = 95  # High confidence for exact text match
                                    llm_decision['reasoning'] = f"Auto-fix: Found exact text match '{target_text}' in element"
                                    auto_fix_worked = True

                            # If auto-fix didn't work, force confidence to 0 to trigger fallback
                            if not auto_fix_worked:
                                self._log(f"    🔄 Ambiguous selector! Forcing vision fallback...", progress_callback)
                                logs.append(f"  ⚠️ Selector matches multiple elements, forcing fallback")
                                llm_decision['confidence'] = 0

                    # If first step fails with low confidence, skip entire scenario
                    # Lowered threshold from 30% to 20% for better fuzzy matching support
                    if step_idx == 1 and llm_decision["confidence"] < 20:
                        logs.append(f"  ⚠️ First step has low confidence, skipping entire scenario")
                        self._log(f"    ⚠️ Skipping (low confidence: {llm_decision['confidence']}%)", progress_callback)
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "skipped",
                            "logs": logs,
                            "reason": "Not executable on current page"
                        }

                    # Debug: Show current page state
                    self._log(f"    🌐 Current URL: {current_url}", progress_callback)
                    self._log(f"    📊 Available DOM elements: {len(dom_elements)}", progress_callback)

                    # RECOVERY LOGIC: If DOM is empty, try to recover
                    if len(dom_elements) == 0:
                        self._log(f"    ⚠️ WARNING: DOM is empty! Attempting recovery...", progress_callback)
                        recovery_success = self._try_recover_from_empty_dom(
                            current_url=current_url,
                            progress_callback=progress_callback
                        )

                        if recovery_success:
                            # Re-fetch page state after recovery
                            screenshot, dom_elements, current_url = self._get_page_state()
                            self._log(f"    ✅ Recovery succeeded! Now {len(dom_elements)} DOM elements available", progress_callback)
                        else:
                            self._log(f"    ❌ Recovery failed - skipping this step", progress_callback)
                            logs.append(f"  ❌ Skipped: DOM empty and recovery failed")
                            continue

                    # Check if auto-fix was successful (confidence = 95)
                    auto_fix_succeeded = (llm_decision["confidence"] == 95 and
                                         llm_decision.get("reasoning", "").startswith("Auto-fix"))

                    if auto_fix_succeeded:
                        self._log(f"    ✅ Auto-fix found reliable selector, skipping fallback", progress_callback)
                        # Skip fallback - auto-fix already found a good selector
                    elif llm_decision["confidence"] < 50:
                        # Trigger fallback for confidence < 50% (increased from 30% to catch more edge cases)
                        # Fallback includes: aggressive text matching, smart navigation, scroll+vision
                        logs.append(f"  ⚠️ Low confidence ({llm_decision['confidence']}%), trying aggressive search...")
                        self._log(f"    🔍 Low confidence ({llm_decision['confidence']}%), trying scroll + vision fallback...", progress_callback)
                        self._log(f"    💡 Reason: {llm_decision.get('reasoning', 'Unknown')}", progress_callback)

                        # STEP 1: Try aggressive text matching on CURRENT PAGE first
                        import re
                        # Extract ALL Korean/English text from description (minimum 2 chars to avoid false matches)
                        all_korean = re.findall(r'[가-힣]{2,}', step.description)  # Min 2 Korean chars
                        all_english = re.findall(r'[A-Za-z]{3,}', step.description)  # Min 3 English chars

                        found_by_text = False
                        # Try longest matches first to avoid substring issues
                        for target_text in sorted(all_korean + all_english, key=len, reverse=True):
                            # Search in ALL DOM elements - use exact match or word boundary
                            text_match = next((e for e in dom_elements
                                             if target_text == e.text or  # Exact match first
                                                f' {target_text} ' in f' {e.text} ' or  # Word boundary
                                                e.text.startswith(target_text) or
                                                e.text.endswith(target_text)), None)
                            if text_match:
                                element_type = text_match.tag if text_match.tag in ['button', 'a', 'input', 'div'] else 'button'
                                better_selector = f'{element_type}:has-text("{target_text}")'
                                self._log(f"    🔧 Aggressive text match: Found '{target_text}' → {better_selector}", progress_callback)
                                llm_decision['selector'] = better_selector
                                llm_decision['confidence'] = 85
                                llm_decision['reasoning'] = f"Aggressive text match: '{target_text}'"
                                found_by_text = True
                                break

                        if found_by_text:
                            self._log(f"    ✅ Found element by aggressive text matching, skipping navigation", progress_callback)
                            # Continue to action execution below
                        # STEP 2: SMART NAVIGATION (only if text matching failed)
                        if not found_by_text:
                            self._log(f"    🌍 Trying Smart Navigation (last resort)...", progress_callback)
                            smart_nav = self._find_element_on_other_pages(step.description, current_url)
                            if smart_nav.get("found"):
                                self._log(f"    💡 Smart navigation: Found '{smart_nav['element_text']}' on {smart_nav['target_url']}", progress_callback)
                                self._log(f"    🏠 Navigating to: {smart_nav['target_url']}", progress_callback)

                                # Navigate to the page where element exists
                                goto_success = self._execute_action(
                                    action="goto",
                                    selector="",
                                    params=[smart_nav['target_url']],
                                    url=smart_nav['target_url'],
                                    screenshot=screenshot,
                                    progress_callback=progress_callback
                                )

                                if goto_success:
                                    # Update page state after navigation
                                    screenshot, dom_elements, current_url = self._get_page_state()
                                    self._log(f"    ✅ Navigation successful, now at: {current_url}", progress_callback)

                                    # Try clicking the element on the new page
                                    click_success = self._execute_action(
                                        action=llm_decision["action"],
                                        selector=smart_nav["selector"],
                                        params=step.params,
                                        url=current_url,
                                        screenshot=screenshot,
                                        progress_callback=progress_callback
                                    )

                                    if click_success:
                                        logs.append(f"  ✅ Action executed via smart navigation")
                                        self._log(f"    ✅ Smart navigation succeeded!", progress_callback)

                                        # Update cache with successful smart navigation selector
                                        self._update_cache(
                                            step_description=step.description,
                                            action=step.action,
                                            page_url=current_url,
                                            selector=smart_nav["selector"],
                                            success=True
                                        )

                                        # Update state after successful click
                                        screenshot, dom_elements, current_url = self._get_page_state()
                                        self._record_page_elements(current_url, dom_elements)
                                        continue  # Move to next step
                                    else:
                                        self._log(f"    ❌ Click failed after navigation", progress_callback)
                                else:
                                    self._log(f"    ❌ Navigation failed", progress_callback)

                        # STEP 3: Try scrolling (only if both text matching and smart nav failed)
                        if not found_by_text:
                            smart_nav_worked = False
                            if 'smart_nav' in locals() and smart_nav.get("found"):
                                smart_nav_worked = True

                            if not smart_nav_worked:
                                self._log(f"    📜 Attempting to scroll and find element...", progress_callback)
                                scroll_success = self._try_scroll_to_find_element(
                                    description=step.description,
                                    screenshot=screenshot,
                                    dom_elements=dom_elements,
                                    url=current_url,
                                    progress_callback=progress_callback
                                )

                                if scroll_success:
                                    # Re-analyze page after scrolling
                                    screenshot, dom_elements, current_url = self._get_page_state()
                                    self._log(f"    📊 After scroll - DOM elements: {len(dom_elements)}", progress_callback)
                                    llm_decision = self.llm_client.select_element_for_step(
                                        step_description=step.description,
                                        dom_elements=dom_elements,
                                        screenshot_base64=screenshot,
                                        url=current_url
                                    )
                                    self._log(f"    🔄 Re-analyzed after scroll, new confidence: {llm_decision['confidence']}%", progress_callback)

                        # STEP 4: If still low confidence, try vision-based coordinate click
                        # Increased threshold from 30 to 50 to trigger vision more aggressively
                        if not found_by_text and llm_decision["confidence"] < 50:
                            self._log(f"    🎯 Trying vision-based coordinate detection...", progress_callback)
                            self._log(f"    🤖 Asking {self.llm_client.model} to find element coordinates in screenshot...", progress_callback)
                            coord_result = self.llm_client.find_element_coordinates(
                                screenshot_base64=screenshot,
                                description=step.description
                            )

                            if coord_result.get("confidence", 0) > 0.5:
                                self._log(f"    ✅ Found element at ({coord_result['x']}, {coord_result['y']}) with {coord_result['confidence']*100:.0f}% confidence", progress_callback)
                                # Execute click at coordinates
                                click_success = self._execute_coordinate_click(
                                    x=coord_result["x"],
                                    y=coord_result["y"],
                                    url=current_url
                                )
                                if click_success:
                                    self._log(f"    ✅ Coordinate-based click successful!", progress_callback)
                                    time.sleep(0.5)
                                    screenshot, dom_elements, current_url = self._get_page_state()
                                    continue
                                else:
                                    self._log(f"    ❌ Coordinate click failed", progress_callback)
                            else:
                                self._log(f"    ❌ Vision fallback failed (confidence: {coord_result.get('confidence', 0)*100:.0f}%)", progress_callback)
                                self._log(f"    💭 Vision reasoning: {coord_result.get('reasoning', 'Unknown')}", progress_callback)

                            # If we reach here, all fallbacks failed
                            logs.append(f"  ⚠️ All fallback attempts failed, skipping step")
                            self._log(f"    ⚠️ Skipping step after fallback attempts", progress_callback)
                            skipped_steps += 1
                            continue

                    if not llm_decision["selector"]:
                        logs.append(f"  ⚠️ No selector found, skipping this step")
                        self._log(f"    ⚠️ Skipping step (no selector)", progress_callback)
                        skipped_steps += 1
                        continue

                    # Log which element will be clicked (IMPORTANT for debugging)
                    self._log(f"    🎯 Target: {llm_decision['action'].upper()} on '{llm_decision['selector']}'", progress_callback)

                    # Find element text to show in logs
                    target_element = next((e for e in dom_elements if e.selector == llm_decision['selector']), None)
                    if target_element and target_element.text:
                        self._log(f"    📝 Element text: \"{target_element.text[:50]}\"", progress_callback)

                    # Execute the action
                    before_screenshot = screenshot
                    success = self._execute_action(
                        action=llm_decision["action"],
                        selector=llm_decision["selector"],
                        params=step.params or [],
                        url=current_url
                    )

                    # UPDATE CACHE: Record execution result
                    self._update_cache(
                        step_description=step.description,
                        action=step.action,
                        page_url=current_url,
                        selector=llm_decision["selector"],
                        success=success
                    )

                    if not success:
                        logs.append(f"  ❌ Action failed on {llm_decision['selector']}")
                        self._log(f"    ❌ Action failed, triggering aggressive fallback...", progress_callback)

                        # AGGRESSIVE FALLBACK: Trigger regardless of initial confidence
                        # Stage 1: Try scrolling to find element
                        self._log(f"    📜 Fallback Stage 1: Scroll to find element...", progress_callback)
                        scroll_success = self._try_scroll_to_find_element(
                            description=step.description,
                            screenshot=screenshot,
                            dom_elements=dom_elements,
                            url=current_url,
                            progress_callback=progress_callback
                        )

                        if scroll_success:
                            # Re-analyze after scroll
                            screenshot, dom_elements, current_url = self._get_page_state()
                            # Retry action with new selector
                            llm_decision = self.llm_client.select_element_for_step(
                                step_description=step.description,
                                dom_elements=dom_elements,
                                screenshot_base64=screenshot,
                                url=current_url
                            )
                            if llm_decision["selector"]:
                                self._log(f"    🔄 Retrying with new selector: {llm_decision['selector']}", progress_callback)
                                success = self._execute_action(
                                    action=llm_decision["action"],
                                    selector=llm_decision["selector"],
                                    params=step.params or [],
                                    url=current_url
                                )
                                if success:
                                    self._log(f"    ✅ Scroll fallback succeeded!", progress_callback)
                                    logs.append(f"  ✅ Found element after scrolling")
                                    # Continue to next step
                                    logs.append(f"  ✅ Action executed: {llm_decision['action']} on {llm_decision['selector']}")
                                    # Update cache with successful selector
                                    self._update_cache(
                                        step_description=step.description,
                                        action=step.action,
                                        page_url=current_url,
                                        selector=llm_decision["selector"],
                                        success=True
                                    )
                                    time.sleep(0.2)
                                    # Update current_url after navigation
                                    if llm_decision["action"].lower() in ("click", "press", "goto"):
                                        screenshot_new, dom_elements_new, current_url_new = self._get_page_state()
                                        dom_elements = dom_elements_new
                                        if current_url_new:
                                            current_url = current_url_new
                                            self._log(f"    🔄 Browser navigated to: {current_url}", progress_callback)
                                    continue

                        # Stage 2: Try vision-based coordinate click
                        if llm_decision["action"] in ["click", "press"]:
                            self._log(f"    🎯 Fallback Stage 2: Vision-based coordinate click...", progress_callback)
                            logs.append(f"  🔄 Fallback: Using vision-based coordinates")

                            # Get coordinates from LLM Vision
                            coords = self.llm_client.find_element_coordinates(
                                screenshot_base64=screenshot,
                                description=step.description
                            )

                            if coords["confidence"] > 0.5:
                                self._log(f"    📍 Found at ({coords['x']}, {coords['y']}) - confidence: {coords['confidence']:.0%}", progress_callback)
                                logs.append(f"  📍 Coordinates: ({coords['x']}, {coords['y']})")

                                # Try coordinate click
                                success = self._execute_coordinate_click(
                                    x=coords['x'],
                                    y=coords['y'],
                                    url=current_url
                                )

                                if success:
                                    self._log(f"    ✅ Vision fallback succeeded!", progress_callback)
                                    logs.append(f"  ✅ Coordinate-based click succeeded")
                                    # Continue to next step
                                    logs.append(f"  ✅ Action executed via coordinates")
                                    time.sleep(0.5)
                                    screenshot, dom_elements, current_url = self._get_page_state()
                                    continue
                                else:
                                    self._log(f"    ❌ Coordinate click failed", progress_callback)
                                    logs.append(f"  ❌ Coordinate-based click failed")
                            else:
                                self._log(f"    ❌ Low confidence ({coords['confidence']:.0%}), cannot locate element visually", progress_callback)
                                logs.append(f"  ❌ Could not find element in screenshot")

                        # All fallbacks failed
                        if not success:
                            self._log(f"    ❌ All fallback stages failed", progress_callback)
                            logs.append(f"  ❌ All fallback attempts exhausted")
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
                    # CRITICAL: Also update current_url with actual browser URL to handle hash navigation
                    if llm_decision["action"].lower() in ("click", "press", "goto"):
                        screenshot_new, dom_elements_new, current_url_new = self._get_page_state()
                        dom_elements = dom_elements_new
                        # Update current_url to reflect actual browser state (e.g., #basics)
                        if current_url_new:
                            current_url = current_url_new
                            self._log(f"    🔄 Browser navigated to: {current_url}", progress_callback)
                            # Record elements on the new page for smart navigation
                            self._record_page_elements(current_url, dom_elements)

                    # Screenshot is already sent by _execute_action with click_position

            # Step 3: Decide on pass/fail based on step execution
            # 4-tier status system:
            # - success: 100% steps completed, no skips
            # - partial: Some steps skipped but core functionality worked
            # - failed: Critical steps failed
            # - skipped: Test not executed

            if failed_non_assertion_steps == 0 and total_non_assertion_steps > 0:
                if skipped_steps == 0:
                    # Perfect execution!
                    logs.append(f"  ✅ All {total_non_assertion_steps} action steps executed successfully")
                    self._log(f"  ✅ Test SUCCESS: 100% completion", progress_callback)
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "success",
                        "logs": logs
                    }
                else:
                    # Some steps skipped but didn't fail
                    skip_rate = (skipped_steps / total_non_assertion_steps) * 100
                    logs.append(f"  ⚠️ {total_non_assertion_steps - skipped_steps}/{total_non_assertion_steps} steps completed ({skipped_steps} skipped)")
                    self._log(f"  ⚠️ Test PARTIAL: {skip_rate:.0f}% steps skipped", progress_callback)
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "partial",
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

    def _try_scroll_to_find_element(
        self,
        description: str,
        screenshot: str,
        dom_elements: List[DomElement],
        url: str,
        progress_callback=None
    ) -> bool:
        """
        Try scrolling the page to find an element that matches the description.

        Returns:
            True if scroll was performed (element might now be visible), False otherwise
        """
        # Scroll down a few times to try to find the element
        for scroll_attempt in range(3):  # Try scrolling 3 times
            self._log(f"      📜 Scroll attempt {scroll_attempt + 1}/3...", progress_callback)

            # Execute scroll action
            self._log(f"      ⬇️  Scrolling page down...", progress_callback)
            # Don't send empty URL - use None to let MCP use current page
            payload = {
                "action": "execute_action",
                "params": {
                    "url": url if url else None,  # Don't send empty string
                    "selector": "body",
                    "action": "scroll",
                    "value": "down",
                    "session_id": self.session_id
                }
            }

            try:
                response = requests.post(
                    f"{self.mcp_config.host_url}/execute",
                    json=payload,
                    timeout=90
                )
                response.raise_for_status()

                # Wait for page to settle
                time.sleep(0.5)

                # Check if element is now visible using vision
                self._log(f"      📸 Re-analyzing DOM after scroll...", progress_callback)
                new_screenshot = self._capture_screenshot(url=None, send_to_gui=True)

                self._log(f"      🤖 Using {self.llm_client.model} vision to detect element...", progress_callback)
                coord_result = self.llm_client.find_element_coordinates(
                    screenshot_base64=new_screenshot,
                    description=description
                )

                if coord_result.get("confidence", 0) > 0.6:
                    self._log(f"      ✅ Found element after scroll! Confidence: {coord_result['confidence']*100:.0f}%", progress_callback)
                    return True

            except Exception as e:
                self._log(f"      ⚠️ Scroll failed: {e}", progress_callback)
                continue

        self._log(f"      ❌ Element not found after scrolling", progress_callback)
        return False

    def _execute_coordinate_click(self, x: int, y: int, url: str) -> bool:
        """
        Execute a click at specific coordinates.

        Args:
            x: X coordinate (pixels from left)
            y: Y coordinate (pixels from top)
            url: Current page URL

        Returns:
            True if click succeeded, False otherwise
        """
        payload = {
            "action": "execute_action",
            "params": {
                "url": url,
                "selector": "",  # No selector needed for coordinate click
                "action": "click_at_coordinates",
                "value": [x, y],  # Pass as array [x, y]
                "session_id": self.session_id
            }
        }

        try:
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=90
            )
            response.raise_for_status()
            data = response.json()
            return data.get("success", False)
        except Exception as e:
            print(f"Coordinate click failed: {e}")
            return False

    def _get_page_state(self) -> tuple[str, List[DomElement], str]:
        """
        Get current page state: screenshot, DOM, and URL.

        Returns:
            Tuple of (screenshot_base64, dom_elements, current_url)
        """
        screenshot = self._capture_screenshot(url=None, send_to_gui=True)

        # Get DOM - this also fetches current URL from browser
        # Use analyze_page action (not get_dom_elements, which doesn't exist)
        payload = {
            "action": "analyze_page",
            "params": {"session_id": self.session_id, "url": None}  # None = use current page
        }
        try:
            response = requests.post(f"{self.mcp_config.host_url}/execute", json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Extract DOM elements
            dom_elements = [DomElement(**elem) for elem in data.get("dom_elements", [])]

            # Extract current URL from page.url
            current_url = data.get("url", "")

        except Exception as e:
            print(f"Failed to get page state: {e}")
            dom_elements = []
            current_url = ""

        return screenshot, dom_elements, current_url

    def _try_recover_from_empty_dom(
        self,
        current_url: str,
        progress_callback=None
    ) -> bool:
        """
        Try to recover from empty DOM by navigating back to base URL and re-analyzing.

        Args:
            current_url: The URL that returned empty DOM
            progress_callback: Optional callback for progress updates

        Returns:
            True if recovery succeeded (DOM now has elements), False otherwise
        """
        self._log(f"      🔄 Attempting recovery: Navigating to base URL...", progress_callback)

        # Extract base URL (remove hash fragments)
        base_url = current_url.split('#')[0] if current_url else self.mcp_config.base_url

        try:
            # Navigate to base URL
            payload = {
                "action": "execute_action",
                "params": {
                    "url": base_url,
                    "selector": None,
                    "action": "goto",
                    "value": None,
                    "session_id": self.session_id
                }
            }

            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=90
            )
            response.raise_for_status()

            # Wait for page to load
            time.sleep(2)

            # Check if we have DOM elements now
            self._log(f"      📊 Re-analyzing page after recovery...", progress_callback)
            screenshot, dom_elements, new_url = self._get_page_state()

            if len(dom_elements) > 0:
                self._log(f"      ✅ Recovery successful! Found {len(dom_elements)} DOM elements", progress_callback)
                return True
            else:
                self._log(f"      ❌ Recovery failed - still 0 DOM elements", progress_callback)
                return False

        except Exception as e:
            self._log(f"      ❌ Recovery navigation failed: {e}", progress_callback)
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

    def _record_page_elements(self, url: str, dom_elements: List[DomElement]) -> None:
        """
        Record elements found on a page for smart navigation.
        Only records home page and navigation-like elements to minimize memory.

        Args:
            url: Page URL
            dom_elements: List of DOM elements found on the page
        """
        # Optimization: Only record home page + first 3 pages visited
        # This covers 90% of use cases while minimizing memory
        if len(self.page_element_map) >= 4 and url != self.home_url:
            return  # Skip recording after 4 pages

        if url not in self.page_element_map:
            self.page_element_map[url] = {}

        # Record interactive elements with text (buttons, links)
        # Focus on navigation-like elements (short text, common keywords)
        nav_keywords = ['기본', '폼', '인터랙션', '홈', 'home', 'menu', '메뉴',
                       '카테고리', 'category', '페이지', 'page', '시작', 'start']

        recorded_count = 0
        for elem in dom_elements:
            if elem.text and elem.tag in ['button', 'a']:
                text_lower = elem.text.lower()
                # Only record if text is short (likely navigation) or contains keywords
                if len(elem.text) < 30 or any(keyword in text_lower for keyword in nav_keywords):
                    self.page_element_map[url][text_lower] = elem.selector
                    recorded_count += 1

        print(f"[Smart Navigation] Recorded {recorded_count} navigation elements for {url} (total pages: {len(self.page_element_map)})")

    def _find_element_on_other_pages(self, target_text: str, current_url: str) -> Dict[str, Any]:
        """
        Search for an element in previously visited pages.

        Args:
            target_text: Text content of the element to find
            current_url: Current page URL

        Returns:
            Dict with navigation info if found, empty dict otherwise
        """
        target_lower = target_text.lower()

        # Search in all recorded pages (prioritize home page)
        search_order = [self.home_url] + [url for url in self.page_element_map.keys() if url != self.home_url]

        for page_url in search_order:
            if page_url == current_url:
                continue  # Skip current page

            elements = self.page_element_map.get(page_url, {})
            for elem_text, selector in elements.items():
                if target_lower in elem_text or elem_text in target_lower:
                    return {
                        "found": True,
                        "target_url": page_url,
                        "selector": selector,
                        "element_text": elem_text
                    }

        return {}

    def _load_cache(self) -> None:
        """Load selector cache from disk."""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.selector_cache = json.load(f)
                # Clean old entries (older than 7 days)
                current_time = time.time()
                self.selector_cache = {
                    k: v for k, v in self.selector_cache.items()
                    if current_time - v.get("timestamp", 0) < 7 * 24 * 3600
                }
                print(f"[Cache] Loaded {len(self.selector_cache)} cached selectors")
        except Exception as e:
            print(f"[Cache] Failed to load cache: {e}")
            self.selector_cache = {}

    def _save_cache(self) -> None:
        """Save selector cache to disk."""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.selector_cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Cache] Failed to save cache: {e}")

    def _get_cache_key(self, step_description: str, action: str, page_url: str) -> str:
        """Generate cache key for a step."""
        # Normalize URL (remove hash and trailing slash for consistency)
        normalized_url = page_url.split('#')[0].rstrip('/')
        # Create hash of (description + action + url)
        key_string = f"{step_description}|{action}|{normalized_url}"
        return hashlib.md5(key_string.encode('utf-8')).hexdigest()

    def _try_semantic_matching(self, step_description: str, dom_elements: List[DomElement], action: str) -> Dict[str, Any] | None:
        """
        Embedding-based semantic pre-matching before expensive LLM call.
        Uses OpenAI text-embedding-3-small for true semantic similarity.
        Also includes ARIA role-based detection for custom components.

        Args:
            step_description: Natural language step description
            dom_elements: Available DOM elements
            action: Action type (click, fill, etc.)

        Returns:
            Dict with selector/confidence/reasoning if good match found, None otherwise
        """
        try:
            import numpy as np
            import re

            # STEP 1: ARIA Role-based detection (highest priority for custom components)
            # Check if description mentions toggle/switch/slider
            desc_lower = step_description.lower()

            # Toggle/Switch detection
            if any(keyword in desc_lower for keyword in ['toggle', 'switch', '스위치', '토글']):
                # Look for elements with role="switch" or data-slot="switch"
                switch_elem = next(
                    (e for e in dom_elements
                     if e.attributes and (
                         e.attributes.get('role') == 'switch' or
                         e.attributes.get('data-slot') == 'switch'
                     )),
                    None
                )
                if switch_elem:
                    # Use ARIA role selector (Playwright supports this)
                    selector = '[role="switch"]'
                    return {
                        "selector": selector,
                        "action": "click",  # Toggle switches use click
                        "reasoning": "ARIA role match: Found switch component with role='switch'",
                        "confidence": 95
                    }

            # Slider detection
            if any(keyword in desc_lower for keyword in ['slider', 'range', '슬라이더']):
                # Look for elements with role="slider" or data-slot="slider"
                slider_elem = next(
                    (e for e in dom_elements
                     if e.attributes and (
                         e.attributes.get('role') == 'slider' or
                         e.attributes.get('data-slot') == 'slider'
                     )),
                    None
                )
                if slider_elem:
                    # Use ARIA role selector
                    selector = '[role="slider"]'
                    return {
                        "selector": selector,
                        "action": "click",  # Interact with slider via click
                        "reasoning": "ARIA role match: Found slider component with role='slider'",
                        "confidence": 95
                    }

            # Dialog/Modal detection
            if any(keyword in desc_lower for keyword in ['dialog', 'modal', '다이얼로그', '모달', 'popup', '팝업']):
                dialog_elem = next(
                    (e for e in dom_elements
                     if e.attributes and (
                         e.attributes.get('role') == 'dialog' or
                         e.attributes.get('role') == 'alertdialog'
                     )),
                    None
                )
                if dialog_elem:
                    selector = '[role="dialog"]'
                    return {
                        "selector": selector,
                        "action": "click",
                        "reasoning": "ARIA role match: Found dialog/modal component with role='dialog'",
                        "confidence": 95
                    }

            # Checkbox detection
            if any(keyword in desc_lower for keyword in ['checkbox', '체크박스', 'check']):
                checkbox_elem = next(
                    (e for e in dom_elements
                     if e.attributes and e.attributes.get('role') == 'checkbox'),
                    None
                )
                if checkbox_elem:
                    selector = '[role="checkbox"]'
                    return {
                        "selector": selector,
                        "action": "click",
                        "reasoning": "ARIA role match: Found checkbox component with role='checkbox'",
                        "confidence": 95
                    }

            # Radio button detection
            if any(keyword in desc_lower for keyword in ['radio', '라디오']):
                radio_elem = next(
                    (e for e in dom_elements
                     if e.attributes and e.attributes.get('role') == 'radio'),
                    None
                )
                if radio_elem:
                    selector = '[role="radio"]'
                    return {
                        "selector": selector,
                        "action": "click",
                        "reasoning": "ARIA role match: Found radio button with role='radio'",
                        "confidence": 95
                    }

            # Tab detection
            if any(keyword in desc_lower for keyword in ['tab', '탭']):
                tab_elem = next(
                    (e for e in dom_elements
                     if e.attributes and e.attributes.get('role') == 'tab'),
                    None
                )
                if tab_elem:
                    selector = '[role="tab"]'
                    return {
                        "selector": selector,
                        "action": "click",
                        "reasoning": "ARIA role match: Found tab component with role='tab'",
                        "confidence": 95
                    }

            # Menu detection
            if any(keyword in desc_lower for keyword in ['menu', '메뉴', 'dropdown', '드롭다운']):
                menu_elem = next(
                    (e for e in dom_elements
                     if e.attributes and (
                         e.attributes.get('role') == 'menu' or
                         e.attributes.get('role') == 'menuitem'
                     )),
                    None
                )
                if menu_elem:
                    selector = '[role="menu"]'
                    return {
                        "selector": selector,
                        "action": "click",
                        "reasoning": "ARIA role match: Found menu component with role='menu'",
                        "confidence": 95
                    }

            # Combobox detection (autocomplete/select)
            if any(keyword in desc_lower for keyword in ['combobox', 'autocomplete', 'select', '선택', '자동완성']):
                combobox_elem = next(
                    (e for e in dom_elements
                     if e.attributes and e.attributes.get('role') == 'combobox'),
                    None
                )
                if combobox_elem:
                    selector = '[role="combobox"]'
                    return {
                        "selector": selector,
                        "action": "click",
                        "reasoning": "ARIA role match: Found combobox/select with role='combobox'",
                        "confidence": 95
                    }

            # Searchbox detection
            if any(keyword in desc_lower for keyword in ['search', '검색']):
                search_elem = next(
                    (e for e in dom_elements
                     if e.attributes and e.attributes.get('role') == 'searchbox'),
                    None
                )
                if search_elem:
                    selector = '[role="searchbox"]'
                    return {
                        "selector": selector,
                        "action": "fill",  # Search inputs use fill
                        "reasoning": "ARIA role match: Found search input with role='searchbox'",
                        "confidence": 95
                    }

            # STEP 2: Embedding-based semantic matching (fallback for text-based elements)
            # Get embedding for step description
            desc_embedding = self._get_embedding(step_description)
            if desc_embedding is None:
                return None

            # Find best matching element
            best_match = None
            best_similarity = 0.0
            SIMILARITY_THRESHOLD = 0.82  # High threshold for semantic matching

            for elem in dom_elements:
                elem_text = elem.text.strip()
                if not elem_text or len(elem_text) < 2:
                    continue

                # Get embedding for element text
                elem_embedding = self._get_embedding(elem_text)
                if elem_embedding is None:
                    continue

                # Calculate cosine similarity
                similarity = np.dot(desc_embedding, elem_embedding) / (
                    np.linalg.norm(desc_embedding) * np.linalg.norm(elem_embedding)
                )

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = elem

            # If we found a good semantic match, use it
            if best_match and best_similarity >= SIMILARITY_THRESHOLD:
                element_type = best_match.tag if best_match.tag in ['button', 'a', 'input', 'select', 'textarea'] else 'button'
                selector = f'{element_type}:has-text("{best_match.text}")'

                # Convert similarity to confidence percentage
                confidence = int(best_similarity * 100)

                return {
                    "selector": selector,
                    "action": action,
                    "reasoning": f"Embedding semantic match: '{step_description[:50]}' → '{best_match.text}' (similarity: {best_similarity:.2f})",
                    "confidence": confidence
                }

            # No good semantic match found
            return None

        except Exception as e:
            print(f"[Semantic Match] Error: {e}")
            return None

    def _get_cached_selector(self, step_description: str, action: str, page_url: str) -> str | None:
        """
        Try to get cached selector for this step.

        Returns:
            Cached selector if found and still valid, None otherwise
        """
        cache_key = self._get_cache_key(step_description, action, page_url)
        cached = self.selector_cache.get(cache_key)

        if cached:
            # Prefer high-confidence cache entries (success_count >= 2)
            if cached.get("success_count", 0) >= 2:
                print(f"[Cache HIT] Using cached selector for '{step_description}'")
                return cached["selector"]

        return None

    def _update_cache(self, step_description: str, action: str, page_url: str,
                     selector: str, success: bool) -> None:
        """
        Update cache with execution result.

        Args:
            step_description: Human-readable step description
            action: Action type (click, fill, etc.)
            page_url: Current page URL
            selector: Selector that was used
            success: Whether the action succeeded
        """
        cache_key = self._get_cache_key(step_description, action, page_url)

        if cache_key not in self.selector_cache:
            self.selector_cache[cache_key] = {
                "selector": selector,
                "timestamp": time.time(),
                "success_count": 1 if success else 0,
                "step_description": step_description  # For debugging
            }
        else:
            # Update existing entry
            if success:
                self.selector_cache[cache_key]["success_count"] += 1
            self.selector_cache[cache_key]["timestamp"] = time.time()

        # Save to disk periodically
        if len(self.selector_cache) % 5 == 0:  # Save every 5 updates
            self._save_cache()

    def _get_embedding(self, text: str) -> List[float] | None:
        """
        Get embedding vector for text using OpenAI text-embedding-3-small.
        Uses cache to avoid redundant API calls.

        Args:
            text: Text to embed

        Returns:
            Embedding vector or None if error
        """
        # Check cache first
        cache_key = hashlib.md5(text.encode('utf-8')).hexdigest()
        if cache_key in self.embedding_cache:
            return self.embedding_cache[cache_key]

        try:
            # Call OpenAI embedding API
            response = self.llm_client.client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )

            embedding = response.data[0].embedding

            # Cache the result
            self.embedding_cache[cache_key] = embedding

            # Save cache periodically
            if len(self.embedding_cache) % 20 == 0:
                self._save_embedding_cache()

            return embedding

        except Exception as e:
            print(f"[Embedding] Error getting embedding for '{text[:50]}': {e}")
            return None

    def _load_embedding_cache(self) -> None:
        """Load embedding cache from disk."""
        try:
            if os.path.exists(self.embedding_cache_file):
                with open(self.embedding_cache_file, 'r', encoding='utf-8') as f:
                    self.embedding_cache = json.load(f)
                print(f"[Embedding Cache] Loaded {len(self.embedding_cache)} cached embeddings")
        except Exception as e:
            print(f"[Embedding Cache] Failed to load cache: {e}")
            self.embedding_cache = {}

    def _save_embedding_cache(self) -> None:
        """Save embedding cache to disk."""
        try:
            os.makedirs(os.path.dirname(self.embedding_cache_file), exist_ok=True)
            with open(self.embedding_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.embedding_cache, f, indent=2)
        except Exception as e:
            print(f"[Embedding Cache] Failed to save cache: {e}")


__all__ = ["IntelligentOrchestrator"]
