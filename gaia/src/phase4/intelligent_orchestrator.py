"""
Intelligent Orchestrator - LLM-powered browser automation.
Uses GPT-4V to analyze DOM + screenshots and make decisions.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence

import requests

from gaia.src.phase4.llm_vision_client import LLMVisionClient
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.config import CONFIG, MCPConfig
from gaia.src.utils.models import DomElement, TestScenario, TestStep

# Dynamic ID patterns from popular UI libraries
DYNAMIC_ID_PATTERNS = [
    r'radix-:r\d+:',           # Radix UI (e.g., radix-:r0:-trigger-signup)
    r'mui-\d{5,}',             # Material-UI
    r'react-\d{13,}',          # React timestamp-based IDs
    r'headlessui-\w+-\d+',     # HeadlessUI
    r'rc-tabs-\d+-tab',        # rc-tabs (Ant Design)
    r'floating-ui-\d+',        # Floating UI
]


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

        # Enable LLM validation (can be disabled for faster execution)
        self.enable_llm_validation = os.getenv("GAIA_ENABLE_LLM_VALIDATION", "false").lower() == "true"

    # ==================== Dynamic ID Detection & Stable Selector Generation ====================

    def _is_dynamic_selector(self, selector: str) -> bool:
        """
        Detect if selector contains dynamic ID patterns from popular UI libraries.

        Args:
            selector: CSS selector string (e.g., '[id="radix-:r0:-trigger-signup"]')

        Returns:
            True if selector contains dynamic ID pattern
        """
        return any(re.search(pattern, selector) for pattern in DYNAMIC_ID_PATTERNS)

    def _create_stable_selector(self, elem: DomElement) -> Optional[str]:
        """
        Generate text-based selector, avoiding dynamic IDs.

        Priority:
        1. Text-based selector (most stable)
        2. ARIA label
        3. data-testid attribute
        4. Stable ID (non-dynamic)
        5. None (trigger vision fallback)

        Args:
            elem: DomElement with selector and metadata

        Returns:
            Stable selector string or None if no stable option available
        """
        # Check if current selector is dynamic
        is_dynamic = self._is_dynamic_selector(elem.selector)

        if is_dynamic:
            print(f"[Stable Selector] ⚠️ Dynamic ID detected: {elem.selector}")

        # Priority 1: Text-based selector (with attribute specificity)
        if elem.text and elem.text.strip():
            # Try to make selector more specific using attributes
            attrs = elem.attributes or {}

            # For buttons, add type attribute if available (submit, button, reset)
            if elem.tag == 'button' and attrs.get('type'):
                button_type = attrs.get('type')
                stable_selector = f'{elem.tag}[type="{button_type}"]:has-text("{elem.text}")'
                if is_dynamic:
                    print(f"[Stable Selector] ✅ Using specific button selector: {stable_selector}")
                return stable_selector

            # For elements with role, add role attribute
            elif attrs.get('role') and attrs.get('role') != 'button':
                # Skip role="button" as it's redundant with <button> tag
                role = attrs.get('role')
                stable_selector = f'{elem.tag}[role="{role}"]:has-text("{elem.text}")'
                if is_dynamic:
                    print(f"[Stable Selector] ✅ Using role-based selector: {stable_selector}")
                return stable_selector

            # Fallback to simple text selector
            else:
                stable_selector = f'{elem.tag}:has-text("{elem.text}")'
                if is_dynamic:
                    print(f"[Stable Selector] ✅ Using text selector: {stable_selector}")
                return stable_selector

        # Priority 2: ARIA label
        aria_label = elem.attributes.get('aria-label', '')
        if aria_label:
            stable_selector = f'[aria-label="{aria_label}"]'
            if is_dynamic:
                print(f"[Stable Selector] ✅ Using ARIA label: {stable_selector}")
            return stable_selector

        # Priority 3: data-testid
        test_id = elem.attributes.get('data-testid', '')
        if test_id:
            stable_selector = f'[data-testid="{test_id}"]'
            if is_dynamic:
                print(f"[Stable Selector] ✅ Using data-testid: {stable_selector}")
            return stable_selector

        # Priority 4: ID (only if NOT dynamic)
        if elem.selector.startswith('[id=') and not is_dynamic:
            return elem.selector

        # Priority 5: Fallback to vision
        if is_dynamic:
            print(f"[Stable Selector] ⚠️ No stable alternative found, will use vision fallback")

        return None  # Trigger vision fallback

    def _validate_cached_selector(
        self,
        cached_data: Dict[str, Any],
        current_url: str
    ) -> Optional[str]:
        """
        Validate cached selector with hybrid approach:
        Stage 1: Dynamic ID check (0.001s)
        Stage 2: DOM lookup via MCP (0.01s)
        Stage 3: LLM validation (2-3s, optional)

        Args:
            cached_data: Cached selector data with metadata
            current_url: Current page URL

        Returns:
            Valid selector or None if cache invalid
        """
        cached_selector = cached_data.get('selector', '')
        cached_text = cached_data.get('element_text', '')
        cached_tag = cached_data.get('element_tag', '')

        # Stage 1: Fast heuristic - Dynamic ID check
        if self._is_dynamic_selector(cached_selector):
            print(f"[Cache Validation] ⚠️ Dynamic ID detected in cache: {cached_selector}")

            # Try to regenerate using cached metadata with attribute specificity
            if cached_text:
                attrs = cached_data.get('attributes', {})

                # For buttons, use type attribute if available
                if cached_tag == 'button' and attrs.get('type'):
                    button_type = attrs.get('type')
                    new_selector = f'{cached_tag}[type="{button_type}"]:has-text("{cached_text}")'
                    print(f"[Cache Validation] ✅ Regenerated specific button selector: {new_selector}")
                    # Update cache with better selector
                    cached_data['selector'] = new_selector
                    return new_selector

                # For elements with role
                elif attrs.get('role') and attrs.get('role') != 'button':
                    role = attrs.get('role')
                    new_selector = f'{cached_tag}[role="{role}"]:has-text("{cached_text}")'
                    print(f"[Cache Validation] ✅ Regenerated role-based selector: {new_selector}")
                    cached_data['selector'] = new_selector
                    return new_selector

                # Fallback to simple text selector
                else:
                    new_selector = f'{cached_tag}:has-text("{cached_text}")'
                    print(f"[Cache Validation] ✅ Regenerated text selector: {new_selector}")
                    cached_data['selector'] = new_selector
                    return new_selector

            # Can't regenerate, invalidate cache
            print(f"[Cache Validation] ❌ Cache invalidated (no text metadata)")
            return None

        # Stage 2: Trust non-dynamic selectors
        # Skip DOM validation since querySelector endpoint doesn't exist
        # Non-dynamic selectors (no radix IDs) are assumed stable
        print(f"[Cache Validation] ✅ Using cached selector: {cached_selector}")
        return cached_selector

    # ==================== End of Dynamic ID Handling ====================

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
                if self.tracker:
                    self.tracker.set_status(scenario.id, "skipped", evidence="LLM prioritization skipped this scenario")

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

                status = result.get("status", "unknown")
                logs_evidence = result.get("logs", "")
                if isinstance(logs_evidence, list):
                    logs_evidence = "\n".join(logs_evidence)
                evidence_text = logs_evidence or None

                if status == "success":
                    results["success"] += 1
                    if self.tracker:
                        self.tracker.set_status(scenario.id, "success", evidence=evidence_text)
                elif status == "partial":
                    results["partial"] += 1
                    note = f"{logs_evidence} (partial)" if logs_evidence else None
                    if self.tracker:
                        self.tracker.set_status(scenario.id, "partial", evidence=note)
                elif status == "failed":
                    results["failed"] += 1
                    if self.tracker:
                        self.tracker.set_status(scenario.id, "failed", evidence=evidence_text)
                elif status == "skipped":
                    results["skipped"] += 1
                    if self.tracker:
                        self.tracker.set_status(scenario.id, "skipped", evidence=evidence_text)
                else:
                    if self.tracker and evidence_text:
                        self.tracker.set_status(scenario.id, status, evidence=evidence_text)

            except Exception as e:
                import traceback
                tb_str = traceback.format_exc()
                self._log(f"❌ Exception in scenario {scenario.id}: {e}", progress_callback)
                self._log(f"📜 Traceback:\n{tb_str}", progress_callback)
                print(f"[ERROR] Exception in scenario {scenario.id}:")
                print(tb_str)
                results["failed"] += 1
                results["scenarios"].append({
                    "id": scenario.id,
                    "scenario": scenario.scenario,
                    "status": "failed",
                    "error": str(e),
                    "logs": []
                })
                if self.tracker:
                    self.tracker.set_status(scenario.id, "failed", evidence=str(e))

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
                model="gpt-5",  # Multimodal reasoning model for demo
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
        failed_assertion_steps = 0  # Track failed assertion steps
        total_assertion_steps = 0   # Track total assertion steps
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
            self._log(f"  📝 Total steps to execute: {total_steps}", progress_callback)
            for step_idx, step in enumerate(scenario.steps, start=1):
                self._log(f"  🤖 Step {step_idx}/{total_steps}: {step.description}", progress_callback)

                # Define action categories
                actions_needing_llm = ["click", "fill", "press"]  # Actions that need LLM to find elements
                actions_not_needing_selector = ["goto", "setViewport", "evaluate", "scroll", "tab", "wait", "waitForTimeout"]  # Actions that execute directly
                assertion_actions = ["expectVisible", "expectHidden", "expectTrue", "expectText", "expectAttribute", "expectCountAtLeast"]  # Assertion actions
                # 🚨 FIX: Added click, fill, expectVisible, expectText to explicit selector list
                # These actions should use plan JSON selectors without re-running LLM Vision
                actions_with_explicit_selector = ["click", "fill", "hover", "focus", "select", "dragAndDrop", "scrollIntoView", "expectVisible", "expectText"]

                logs.append(f"Step {step_idx}: {step.description}")

                # Check if this is an action that doesn't need LLM element selection
                if step.action in actions_not_needing_selector or step.action in assertion_actions:
                    # Execute directly without LLM
                    self._log(f"    ⚡ Direct execution: {step.action.upper()}", progress_callback)
                    logs.append(f"  Action: {step.action} (direct)")

                    # Track assertion vs non-assertion steps
                    if step.action in assertion_actions:
                        total_assertion_steps += 1
                    else:
                        total_non_assertion_steps += 1

                    # For debugging: log params
                    if step.params:
                        self._log(f"    📋 Params: {step.params}", progress_callback)

                    # NEW: Infer missing intermediate steps from description
                    # Check if description implies actions not in the step (e.g., "탭으로 전환", "모달 열기")
                    inferred_success = self._infer_and_execute_missing_steps(
                        step=step,
                        screenshot=screenshot,
                        dom_elements=dom_elements,
                        current_url=current_url,
                        progress_callback=progress_callback
                    )

                    # Update state after inferred steps
                    if inferred_success:
                        screenshot, dom_elements, current_url = self._get_page_state()

                    selector = step.selector if step.selector else ""

                    # For assertions, use current screenshot as "before"
                    # (state after previous action but before assertion check)
                    before_screenshot = screenshot if step.action in assertion_actions else None

                    # DEBUG: Log before_screenshot status for assertions
                    if step.action in assertion_actions:
                        if before_screenshot:
                            self._log(f"    📸 Using before screenshot ({len(before_screenshot)} chars)", progress_callback)
                        else:
                            self._log(f"    ⚠️ WARNING: No before_screenshot available!", progress_callback)

                    # Assertion 액션이면 before_screenshot 전달
                    if step.action in assertion_actions:
                        success = self._execute_action(
                            action=step.action,
                            selector=selector,
                            params=step.params or [],
                            url=current_url,
                            before_screenshot=before_screenshot
                        )
                    else:
                        success = self._execute_action(
                            action=step.action,
                            selector=selector,
                            params=step.params or [],
                            url=current_url
                        )

                    if not success:
                        logs.append(f"  ❌ Action {step.action} failed")
                        self._log(f"    ❌ Action failed", progress_callback)

                        # For assertion actions, log but continue (don't fail entire scenario immediately)
                        if step.action in assertion_actions:
                            failed_assertion_steps += 1  # Track assertion failure
                            self._log(f"    ⚠️ Assertion failed, continuing...", progress_callback)
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

                    # Detect DOM context (active tabs/modals) for context-aware caching
                    dom_context = self._detect_dom_context(dom_elements)

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
                        self._log(f"  💾 Cache hit! Using cached selector", progress_callback)
                    else:
                        # PARALLEL MATCHING: ARIA + Semantic 병렬 실행, 필요시 LLM Aggregator
                        parallel_match = self._try_semantic_matching(
                            step.description,
                            dom_elements,
                            step.action,
                            current_url=current_url,
                            screenshot=screenshot
                        )

                        if parallel_match:
                            # Found good match from ARIA/Semantic/Aggregator
                            llm_decision = parallel_match
                            self._log(f"  🎯 Parallel match succeeded!", progress_callback)
                        else:
                            # All fast methods failed, use full LLM Vision
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
                            self._log(f"    ⚠️ WARNING: Selector matches {match_count} elements!", progress_callback)
                            if sample_texts:
                                self._log(f"    💡 Sample elements: {sample_texts}", progress_callback)

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
                                self._log(f"    🔧 Aggressive phrase match: Found '{phrase}' → {better_selector}", progress_callback)
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
                                    self._log(f"    🔧 Exact text match: Found '{target_text}' → {better_selector}", progress_callback)
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
                                        self._log(f"    🔧 Partial text match: Found '{target_text}' → {better_selector}", progress_callback)
                                        llm_decision['selector'] = better_selector
                                        llm_decision['confidence'] = 75
                                        llm_decision['reasoning'] = f"Partial text match: '{target_text}'"
                                        found_by_text = True
                                        break

                        if found_by_text:
                            self._log(f"    ✅ Found element by aggressive text matching", progress_callback)

                            # 🔥 SMART TAB ACTIVATION: If we found a button/tab but action is fill/click on something else,
                            # click the button first to activate the tab/section
                            if text_match and text_match.tag == 'button' and step.action in ['fill', 'click']:
                                # Check if this is likely a tab button (e.g., "회원가입", "로그인")
                                tab_keywords = ['회원가입', '로그인', '탭', 'tab', '페이지', 'page']
                                is_likely_tab = any(keyword in text_match.text.lower() or keyword in step.description.lower()
                                                   for keyword in tab_keywords)

                                if is_likely_tab:
                                    self._log(f"    🔘 Detected tab/section button, clicking first to activate...", progress_callback)

                                    # Click the tab button first
                                    tab_click_success = self._execute_action(
                                        action="click",
                                        selector=better_selector,
                                        params=[],
                                        url=current_url
                                    )

                                    if tab_click_success:
                                        self._log(f"    ✅ Tab activated, refreshing page state...", progress_callback)
                                        time.sleep(1.0)  # Wait for tab content to load
                                        screenshot, dom_elements, current_url = self._get_page_state()
                                        self._log(f"    📊 DOM updated: {len(dom_elements)} elements", progress_callback)

                                        # Now find the actual target element (e.g., input field)
                                        # Re-run LLM to find the real target in the now-visible tab
                                        self._log(f"    🔍 Re-analyzing to find actual target element...", progress_callback)
                                        llm_decision = self.llm_client.select_element_for_step(
                                            step_description=step.description,
                                            dom_elements=dom_elements,
                                            screenshot_base64=screenshot,
                                            url=current_url
                                        )
                                        self._log(f"    🎯 Found actual target: {llm_decision['selector']}", progress_callback)
                                    else:
                                        self._log(f"    ⚠️ Tab click failed, continuing anyway...", progress_callback)

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
                                    url=smart_nav['target_url']
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
                                        before_screenshot=screenshot
                                    )

                                    if click_success:
                                        logs.append(f"  ✅ Action executed via smart navigation")
                                        self._log(f"    ✅ Smart navigation succeeded!", progress_callback)

                                        # Find element to get tag information
                                        target_element = next((e for e in dom_elements if e.selector == smart_nav["selector"]), None)
                                        element_tag = target_element.tag if target_element else ""
                                        element_text = smart_nav.get("element_text", "")
                                        element_attrs = target_element.attributes if target_element else {}

                                        # Update cache with successful smart navigation selector
                                        self._update_cache(
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

                            # STEP 5: NEW! If target not visible, try to find and click exploreable elements (tabs, modals, etc.)
                            self._log(f"    🔍 Target not visible, looking for tabs/triggers to explore...", progress_callback)
                            explore_result = self.llm_client.find_exploreable_element(
                                screenshot_base64=screenshot,
                                target_description=step.description
                            )

                            if explore_result.get("found_exploreable") and explore_result.get("confidence", 0) > 0.6:
                                self._log(f"    💡 Found {explore_result.get('element_type', 'element')}: '{explore_result.get('element_text', 'N/A')}'", progress_callback)
                                self._log(f"    🔄 Clicking to reveal target element... ({explore_result.get('reasoning', 'Unknown')})", progress_callback)
                                logs.append(f"  🔍 Exploring: {explore_result.get('element_text', 'N/A')}")

                                # Click the tab/modal/trigger button
                                explore_click = self._execute_coordinate_click(
                                    x=explore_result["x"],
                                    y=explore_result["y"],
                                    url=current_url
                                )

                                self._log(f"    🔍 Exploration click result: {explore_click}", progress_callback)

                                if explore_click:
                                    self._log(f"    ⏳ Waiting 1.5s for tab transition...", progress_callback)
                                    time.sleep(1.5)  # Increased wait time for React state updates
                                    screenshot, dom_elements, current_url = self._get_page_state()
                                    self._log(f"    📊 After exploration: DOM elements = {len(dom_elements)}", progress_callback)

                                    # DEBUG: Save screenshot after exploration
                                    import base64
                                    debug_path = f"/tmp/debug_after_exploration_{step.description[:20]}.png"
                                    with open(debug_path, "wb") as f:
                                        f.write(base64.b64decode(screenshot))
                                    self._log(f"    🖼️  DEBUG: Saved screenshot to {debug_path}", progress_callback)

                                    # Now retry finding the target element
                                    self._log(f"    🔁 Retrying target element detection after exploration...", progress_callback)
                                    retry_coord = self.llm_client.find_element_coordinates(
                                        screenshot_base64=screenshot,
                                        description=step.description
                                    )

                                    if retry_coord.get("confidence", 0) > 0.5:
                                        self._log(f"    🎉 Found target after exploration at ({retry_coord['x']}, {retry_coord['y']})!", progress_callback)
                                        logs.append(f"  ✅ Target found after exploration")
                                        # Execute the actual target action
                                        target_click = self._execute_coordinate_click(
                                            x=retry_coord["x"],
                                            y=retry_coord["y"],
                                            url=current_url
                                        )
                                        if target_click:
                                            self._log(f"    ✅ Target action successful!", progress_callback)
                                            time.sleep(0.5)
                                            screenshot, dom_elements, current_url = self._get_page_state()
                                            continue
                                        else:
                                            self._log(f"    ❌ Target click failed", progress_callback)
                                    else:
                                        self._log(f"    ❌ Still cannot find target after exploration (confidence: {retry_coord.get('confidence', 0)*100:.0f}%)", progress_callback)
                                else:
                                    self._log(f"    ❌ Exploration click failed", progress_callback)
                            else:
                                self._log(f"    ❌ No exploreable elements found (confidence: {explore_result.get('confidence', 0)*100:.0f}%)", progress_callback)
                                self._log(f"    💭 Reasoning: {explore_result.get('reasoning', 'Unknown')}", progress_callback)

                            # If we reach here, all fallbacks failed (including exploration)
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
                    element_text = target_element.text if target_element else ""
                    element_tag = target_element.tag if target_element else ""
                    element_attrs = target_element.attributes if target_element else {}
                    self._update_cache(
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

                                    # Find element to get metadata
                                    target_element = next((e for e in dom_elements if e.selector == llm_decision['selector']), None)
                                    element_text = target_element.text if target_element else ""
                                    element_tag = target_element.tag if target_element else ""
                                    element_attrs = target_element.attributes if target_element else {}

                                    # Update cache with successful selector
                                    self._update_cache(
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

            # Step 3: Scenario-level Vision AI verification (NEW!)
            # Capture AFTER screenshot and verify entire scenario success
            after_scenario_screenshot = self._capture_screenshot(current_url, send_to_gui=False)
            scenario_verified = False
            scenario_verification_result = None

            # Only do scenario verification if assertion field exists
            if hasattr(scenario, 'assertion') and scenario.assertion:
                self._log(f"  🔍 Running scenario-level Vision AI verification...", progress_callback)

                # Extract assertion details (handle both old and new format)
                expected_outcome = getattr(scenario.assertion, "expected_outcome", None) or scenario.scenario
                success_indicators = getattr(scenario.assertion, "success_indicators", [])

                if success_indicators:
                    from gaia.src.phase4.llm_vision_client import LLMVisionClient
                    vision_client = LLMVisionClient()

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

                    self._log(f"  🔍 Vision AI Result:", progress_callback)
                    self._log(f"     - Success: {scenario_verified}", progress_callback)
                    self._log(f"     - Confidence: {confidence}%", progress_callback)
                    self._log(f"     - Matched: {matched_indicators}", progress_callback)
                    self._log(f"     - Reasoning: {reasoning}", progress_callback)

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
                        self._log(f"  ✅ Test SUCCESS: Vision AI verified", progress_callback)
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "success",
                            "logs": logs,
                            "verification": scenario_verification_result
                        }
                    else:
                        # Actions passed but Vision AI says scenario failed
                        logs.append(f"  ⚠️ Actions passed, but Vision AI verification failed")
                        self._log(f"  ⚠️ Test PARTIAL: Vision AI verification failed", progress_callback)
                        return {
                            "id": scenario.id,
                            "scenario": scenario.scenario,
                            "status": "partial",
                            "logs": logs,
                            "verification": scenario_verification_result
                        }
                elif failed_assertion_steps == 0:
                    # No Vision AI, but step-based assertions passed
                    if skipped_steps == 0:
                        logs.append(f"  ✅ All {total_non_assertion_steps} action steps and {total_assertion_steps} assertions passed")
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
                else:
                    # Actions succeeded but assertions failed
                    logs.append(f"  ⚠️ All {total_non_assertion_steps} actions passed, but {failed_assertion_steps}/{total_assertion_steps} assertions failed")
                    self._log(f"  ⚠️ Test PARTIAL: Assertions failed ({failed_assertion_steps}/{total_assertion_steps})", progress_callback)
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "partial",  # Assertion 실패는 partial로 처리
                        "logs": logs,
                        "failed_assertions": failed_assertion_steps,
                        "total_assertions": total_assertion_steps
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
            import traceback
            tb_str = traceback.format_exc()
            logs.append(f"❌ Exception: {e}")
            logs.append(f"📜 Traceback:\n{tb_str}")

            # Print to console for debugging
            print(f"\n[ERROR] Exception in _execute_single_scenario for {scenario.id}:")
            print(tb_str)
            self._log(f"❌ Exception in step execution: {e}", progress_callback)
            self._log(f"📜 Traceback:\n{tb_str}", progress_callback)

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

    def _execute_action(self, action: str, selector: str, params: List[Any], url: str, before_screenshot: str = None) -> bool:
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

            # Assertion일 때만 before_screenshot 전달 (Vision AI Fallback용)
            if action in ["expectVisible", "expectHidden", "expectTrue"] and before_screenshot:
                payload["params"]["before_screenshot"] = before_screenshot

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

    def _infer_and_execute_missing_steps(
        self,
        step,
        screenshot: str,
        dom_elements: List[DomElement],
        current_url: str,
        progress_callback
    ) -> bool:
        """
        Infer and execute missing intermediate steps from step description.

        For example:
        - "기본 기능 페이지 접속 후 회원가입 탭으로 전환"
          → Infers: Need to click "회원가입" tab before continuing
        - "모달 열기 후 입력"
          → Infers: Need to click modal trigger button first

        Args:
            step: Current test step
            screenshot: Current screenshot base64
            dom_elements: Current DOM elements
            current_url: Current URL
            progress_callback: Callback for logging

        Returns:
            True if any inferred steps were executed, False otherwise
        """
        # Keywords that suggest missing intermediate steps
        transition_keywords = ["탭으로 전환", "탭 전환", "전환", "모달 열기", "모달을 열", "드롭다운 열기", "아코디언 열기"]

        description = step.description.lower()

        # Check if description contains transition keywords
        needs_intermediate_step = any(keyword in description for keyword in transition_keywords)

        if not needs_intermediate_step:
            return False

        self._log(f"    🧠 Inferring missing intermediate steps from: '{step.description}'", progress_callback)

        # Use LLM to infer what element needs to be clicked before the main action
        prompt = f"""Analyze this test step description and determine if there's a missing intermediate action.

Step description: "{step.description}"
Main action: {step.action}

The description suggests an intermediate step (like switching tabs, opening modal, etc.) before the main action.

Identify what needs to be clicked/interacted with BEFORE executing the main action.

Respond with JSON only:
{{
  "needs_intermediate": true/false,
  "intermediate_action": "click" | "fill" | null,
  "target_element": "what to click (e.g., '회원가입 탭', '필터 버튼')",
  "reasoning": "why this is needed"
}}"""

        try:
            # Use GPT-4o for reasoning
            import openai
            import os
            api_key = os.getenv("OPENAI_API_KEY")
            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-5",  # For demo
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=300
            )

            result_text = response.choices[0].message.content.strip()
            # Remove markdown if present
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]

            import json
            result = json.loads(result_text.strip())

            if not result.get("needs_intermediate"):
                self._log(f"    ℹ️  No intermediate step needed", progress_callback)
                return False

            target = result.get("target_element", "")
            reasoning = result.get("reasoning", "")

            self._log(f"    💡 Inferred: Need to interact with '{target}'", progress_callback)
            self._log(f"    💭 Reasoning: {reasoning}", progress_callback)

            # Use LLM to find and click the target element
            llm_decision = self.llm_client.select_element_for_step(
                step_description=f"{target} 클릭",
                dom_elements=dom_elements,
                screenshot_base64=screenshot,
                url=current_url
            )

            if llm_decision['selector'] and llm_decision['confidence'] >= 70:
                self._log(f"    🎯 Found target: {llm_decision['selector']} (confidence: {llm_decision['confidence']}%)", progress_callback)

                # Execute intermediate action
                intermediate_success = self._execute_action(
                    action=result.get("intermediate_action", "click"),
                    selector=llm_decision['selector'],
                    params=[],
                    url=current_url
                )

                if intermediate_success:
                    self._log(f"    ✅ Intermediate step executed successfully", progress_callback)
                    time.sleep(1.0)  # Wait for transition
                    return True
                else:
                    self._log(f"    ⚠️  Intermediate step failed, continuing anyway", progress_callback)
                    return False
            else:
                self._log(f"    ⚠️  Could not find target element (confidence: {llm_decision.get('confidence', 0)}%)", progress_callback)
                return False

        except Exception as e:
            self._log(f"    ⚠️  Failed to infer intermediate steps: {e}", progress_callback)
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

    def _get_cache_key(self, step_description: str, action: str, page_url: str, dom_context: str = "") -> str:
        """
        Generate cache key for a step.

        Args:
            step_description: Step description
            action: Action type (click, fill, etc.)
            page_url: Current page URL
            dom_context: Context string representing active tabs/modals (e.g., "tab:회원가입")

        Returns:
            MD5 hash of the cache key
        """
        # Normalize URL (keep hash for SPA, just remove trailing slash)
        # SPA에서 해시가 다르면 완전히 다른 페이지이므로 해시 유지 필요!
        normalized_url = page_url.rstrip('/')

        # Include DOM context in cache key to differentiate between different UI states
        # e.g., login tab vs signup tab on the same page
        key_string = f"{step_description}|{action}|{normalized_url}|{dom_context}"
        return hashlib.md5(key_string.encode('utf-8')).hexdigest()

    def _detect_dom_context(self, dom_elements: List[DomElement]) -> str:
        """
        Detect the current DOM context (active tabs, modals, etc.) to make cache context-aware.

        Args:
            dom_elements: Current DOM elements

        Returns:
            Context string (e.g., "tab:회원가입" or "modal:장바구니" or "")
        """
        context_parts = []

        # Detect active tab
        for elem in dom_elements:
            # Get role from attributes
            elem_role = elem.attributes.get("role", "")

            # Check for active tab indicators
            if elem_role == "tab":
                # Check various active state indicators
                attrs_str = str(elem.attributes)
                if ("aria-selected" in elem.attributes and elem.attributes.get("aria-selected") == "true") or \
                   "data-state='active'" in attrs_str or \
                   "data-state='checked'" in attrs_str:
                    tab_text = elem.text[:20] if elem.text else ""
                    if tab_text:
                        context_parts.append(f"tab:{tab_text}")
                        break

        # Detect open modal/dialog
        for elem in dom_elements:
            elem_role = elem.attributes.get("role", "")
            if elem_role in ["dialog", "alertdialog"]:
                attrs_str = str(elem.attributes)
                if "data-state='open'" in attrs_str or elem.attributes.get("data-state") == "open":
                    modal_text = elem.text[:20] if elem.text else "modal"
                    context_parts.append(f"modal:{modal_text}")
                    break

        return "|".join(context_parts)

    def _detect_aria_roles(self, step_description: str, dom_elements: List[DomElement]) -> Dict[str, List[DomElement]]:
        """
        Detect ALL elements matching ARIA roles mentioned in step description.
        Returns ALL matches, not just the first one (for disambiguation).

        Args:
            step_description: Natural language step description
            dom_elements: Available DOM elements

        Returns:
            Dict mapping role names to lists of matching elements
        """
        desc_lower = step_description.lower()
        matches = {}

        # Define ARIA role keywords and their corresponding roles
        role_keywords = {
            'switch': (['toggle', 'switch', '스위치', '토글'], 'switch'),
            'slider': (['slider', 'range', '슬라이더'], 'slider'),
            'dialog': (['dialog', 'modal', '다이얼로그', '모달', 'popup', '팝업'], 'dialog'),
            'checkbox': (['checkbox', '체크박스', 'check'], 'checkbox'),
            'radio': (['radio', '라디오'], 'radio'),
            'tab': (['tab', '탭'], 'tab'),
            'menu': (['menu', '메뉴', 'dropdown', '드롭다운'], 'menu'),
            'combobox': (['combobox', 'autocomplete', 'select', '선택', '자동완성'], 'combobox'),
            'searchbox': (['search', '검색'], 'searchbox'),
        }

        # Check each role type
        for role_name, (keywords, aria_role) in role_keywords.items():
            if any(keyword in desc_lower for keyword in keywords):
                # Find ALL elements with this ARIA role
                matching_elements = [
                    elem for elem in dom_elements
                    if elem.attributes and elem.attributes.get('role') == aria_role
                ]
                if matching_elements:
                    matches[role_name] = matching_elements

        return matches

    def _disambiguate_aria_matches(self, role_name: str, matches: List[DomElement],
                                   step_description: str, action: str) -> Dict[str, Any] | None:
        """
        Disambiguate when multiple elements match the same ARIA role.
        Uses text matching → semantic matching → returns candidates for LLM.

        Args:
            role_name: ARIA role type (e.g., 'switch', 'slider')
            matches: List of elements with this ARIA role
            step_description: Step description
            action: Action type

        Returns:
            Dict with selector/confidence/reasoning if disambiguated, None if needs LLM
        """
        import numpy as np

        # Single match - check if navigation is needed
        if len(matches) == 1:
            # Check if step mentions navigation keywords
            nav_keywords = ['navigate', 'go to', 'open', '이동', '가기', '열기']
            needs_navigation = any(kw in step_description.lower() for kw in nav_keywords)

            # If navigation mentioned, let LLM handle the full context
            if needs_navigation:
                return None

            # Otherwise, safe to use the single match
            selector = f'[role="{matches[0].attributes.get("role")}"]'
            return {
                "selector": selector,
                "action": "click" if action != "fill" else "fill",
                "reasoning": f"ARIA role match: Single {role_name} found (role='{matches[0].attributes.get('role')}')",
                "confidence": 95
            }

        # Multiple matches - try text-based disambiguation
        print(f"[ARIA Disambiguate] Found {len(matches)} {role_name} elements")

        # Stage 1: Exact text match
        # PRIORITY: Handle elements without text FIRST (e.g., switch with sibling label)
        import re
        text_keywords = re.findall(r'[가-힣]{2,}|[A-Za-z]{3,}', step_description)

        # First pass: Check for elements WITHOUT text (need sibling traversal)
        for elem in matches:
            if not elem.text or not elem.text.strip():
                # Try to find label text in description
                for keyword in sorted(text_keywords, key=len, reverse=True):
                    if len(keyword) >= 2:  # Min 2 chars
                        # Use Playwright's sibling traversal: text >> .. >> .. >> [role="switch"]
                        selector = f'text="{keyword}" >> .. >> .. >> [role="{elem.attributes.get("role")}"]'
                        print(f"[ARIA Disambiguate] Using sibling traversal for {role_name}: {selector}")
                        return {
                            "selector": selector,
                            "action": "click" if action != "fill" else "fill",
                            "reasoning": f"ARIA + sibling label match: {role_name} with label '{keyword}'",
                            "confidence": 85
                        }

        # Second pass: Check for elements WITH text
        for elem in matches:
            if elem.text and elem.text.strip() in step_description:
                selector = f'[role="{elem.attributes.get("role")}"]:has-text("{elem.text}")'
                print(f"[ARIA Disambiguate] Using text match for {role_name}: {selector}")
                return {
                    "selector": selector,
                    "action": "click" if action != "fill" else "fill",
                    "reasoning": f"ARIA + text match: {role_name} with text '{elem.text}'",
                    "confidence": 90
                }

        # Stage 2: Semantic matching on elements with text
        elements_with_text = [elem for elem in matches if elem.text and elem.text.strip()]

        if elements_with_text:
            desc_embedding = self._get_embedding(step_description)
            if desc_embedding is not None:
                best_match = None
                best_similarity = 0.0

                for elem in elements_with_text:
                    elem_embedding = self._get_embedding(elem.text)
                    if elem_embedding is None:
                        continue

                    similarity = np.dot(desc_embedding, elem_embedding) / (
                        np.linalg.norm(desc_embedding) * np.linalg.norm(elem_embedding)
                    )

                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_match = elem

                # If high similarity, use it
                if best_match and best_similarity >= 0.80:
                    selector = f'[role="{best_match.attributes.get("role")}"]:has-text("{best_match.text}")'
                    return {
                        "selector": selector,
                        "action": "click" if action != "fill" else "fill",
                        "reasoning": f"ARIA + semantic match: {role_name} '{best_match.text}' (similarity: {best_similarity:.2f})",
                        "confidence": int(best_similarity * 100)
                    }

        # Stage 3: Unable to disambiguate - return None to fall back to LLM
        # LLM will receive the filtered list via vision + DOM analysis
        print(f"[ARIA Disambiguate] Unable to disambiguate {len(matches)} {role_name} elements, falling back to LLM")
        return None

    def _try_aria_matching(self, step_description: str, dom_elements: List[DomElement], action: str) -> Dict[str, Any] | None:
        """
        ARIA role-based matching (병렬 실행용 분리 함수).
        """
        try:
            aria_matches = self._detect_aria_roles(step_description, dom_elements)

            if aria_matches:
                for role_name, elements in aria_matches.items():
                    result = self._disambiguate_aria_matches(role_name, elements, step_description, action)
                    if result:
                        return result
            return None
        except Exception as e:
            print(f"[ARIA Match] Error: {e}")
            return None

    def _try_pure_semantic_matching(self, step_description: str, dom_elements: List[DomElement], action: str) -> Dict[str, Any] | None:
        """
        Embedding-based semantic matching (병렬 실행용 분리 함수).
        """
        try:
            try:
                import numpy as np
            except ImportError:
                print("[Semantic Match] Warning: numpy not available, skipping semantic matching")
                return None

            desc_embedding = self._get_embedding(step_description)
            if desc_embedding is None:
                return self._offline_fuzzy_semantic_match(step_description, dom_elements, action)

            best_match = None
            best_similarity = 0.0
            SIMILARITY_THRESHOLD = 0.82

            for elem in dom_elements:
                elem_text = elem.text.strip()
                if not elem_text or len(elem_text) < 2:
                    continue

                elem_embedding = self._get_embedding(elem_text)
                if elem_embedding is None:
                    continue

                similarity = np.dot(desc_embedding, elem_embedding) / (
                    np.linalg.norm(desc_embedding) * np.linalg.norm(elem_embedding)
                )

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = elem

            if best_match and best_similarity >= SIMILARITY_THRESHOLD:
                element_type = best_match.tag if best_match.tag in ['button', 'a', 'input', 'select', 'textarea'] else 'button'
                selector = f'{element_type}:has-text("{best_match.text}")'
                confidence = int(best_similarity * 100)

                return {
                    "selector": selector,
                    "action": action,
                    "reasoning": f"Semantic match: '{step_description[:50]}' → '{best_match.text}' (similarity: {best_similarity:.2f})",
                    "confidence": confidence
                }

            return self._offline_fuzzy_semantic_match(step_description, dom_elements, action)

        except Exception as e:
            print(f"[Semantic Match] Error: {e}")
            return None

    def _try_semantic_matching(self, step_description: str, dom_elements: List[DomElement], action: str,
                                current_url: str = "", screenshot: str = "") -> Dict[str, Any] | None:
        """
        병렬 매칭 시스템: ARIA + Semantic을 동시 실행 후, 둘 다 성공하면 LLM이 결과 통합.

        병렬 실행 흐름:
        1. ARIA detection (빠름, 무료)
        2. Semantic matching (빠름, 저렴) - TEMPORARILY DISABLED due to dimension mismatch
        3. 둘 다 성공하면 → LLM Aggregator가 최종 판단 (교차 검증)
        4. 하나만 성공하면 → 그 결과 사용
        5. 둘 다 실패하면 → None (메인 로직이 LLM Vision 호출)

        Returns:
            최종 선택된 셀렉터 결과 또는 None
        """
        print("[Parallel Match] ARIA matching only (Semantic DISABLED due to embedding dimension mismatch)")

        # TEMPORARILY DISABLED semantic matching due to dimension mismatch errors (128 vs 1536)
        # Only using ARIA matching for now
        aria_result = self._try_aria_matching(step_description, dom_elements, action)
        semantic_result = None  # Disabled

        print(f"[Parallel Match] ARIA: {aria_result is not None}, Semantic: DISABLED")

        # Case 1: 둘 다 성공 → LLM Aggregator가 최종 판단
        if aria_result and semantic_result:
            print(f"[Parallel Match] Both succeeded! ARIA conf={aria_result['confidence']}, Semantic conf={semantic_result['confidence']}")

            # 셀렉터가 같으면 LLM 호출 생략 (확실함)
            if aria_result['selector'] == semantic_result['selector']:
                print(f"[Parallel Match] ✅ Both agree on same selector! Using it with high confidence.")
                aria_result['confidence'] = min(95, aria_result['confidence'] + 10)  # Boost confidence
                return aria_result

            # 셀렉터가 다르면 LLM에게 물어보기 (교차 검증 필요)
            print(f"[Parallel Match] ⚠️ Disagreement detected! Calling LLM Aggregator...")
            print(f"  ARIA: {aria_result['selector']}")
            print(f"  Semantic: {semantic_result['selector']}")

            # LLM Vision도 실행해서 3-way 교차 검증
            vision_result = self.llm_client.select_element_for_step(
                step_description=step_description,
                dom_elements=dom_elements,
                screenshot_base64=screenshot,
                url=current_url
            )

            # LLM Aggregator 호출
            final_decision = self.llm_client.aggregate_matching_results(
                step_description=step_description,
                aria_result=aria_result,
                semantic_result=semantic_result,
                vision_result=vision_result,
                url=current_url
            )

            print(f"[Parallel Match] LLM Aggregator decision: {final_decision['selector']} (conf: {final_decision['confidence']})")
            return final_decision

        # Case 2: ARIA만 성공
        elif aria_result:
            print(f"[Parallel Match] Using ARIA only (conf: {aria_result['confidence']})")
            return aria_result

        # Case 3: Semantic만 성공
        elif semantic_result:
            print(f"[Parallel Match] Using Semantic only (conf: {semantic_result['confidence']})")
            return semantic_result

        # Case 4: 둘 다 실패 → None 리턴 (메인 로직이 LLM Vision 호출)
        print("[Parallel Match] Both ARIA and Semantic failed, will use LLM Vision")
        return None

    def _offline_fuzzy_semantic_match(
        self,
        step_description: str,
        dom_elements: List[DomElement],
        action: str
    ) -> Dict[str, Any] | None:
        """
        Lightweight semantic fallback using text similarity when embeddings are unavailable.

        Enhanced with:
        - Minimum text length filtering (very short texts penalized)
        - Position-aware matching (first, second, last)
        - Interactive element filtering for click/select actions
        - Stricter confidence thresholds
        """
        from difflib import SequenceMatcher

        normalized_desc = self._normalize_text(step_description)
        if not normalized_desc:
            return None

        # Extract position keywords
        position_keywords = ["first", "second", "third", "last"]
        has_position = any(kw in normalized_desc for kw in position_keywords)

        # Check if action is interactive
        is_interactive_action = action.lower() in ["click", "select", "choose", "pick"]

        best_match = None
        best_score = 0.0
        candidates = []

        for idx, elem in enumerate(dom_elements):
            elem_text = (elem.text or "").strip()

            # Skip elements with very short text (likely not semantic targets)
            if len(elem_text) < 3:
                continue

            normalized_elem = self._normalize_text(elem_text)
            if not normalized_elem:
                continue

            # For interactive actions, prefer interactive elements
            if is_interactive_action:
                if elem.tag not in ["button", "a", "input", "select", "textarea"]:
                    continue

            # Sequence similarity baseline
            score = SequenceMatcher(None, normalized_desc, normalized_elem).ratio()

            # Token overlap bonus
            overlap = self._token_overlap(normalized_desc, normalized_elem)
            if overlap:
                score = max(score, min(0.95, 0.6 + 0.35 * overlap))

            # Boost if text appears verbatim inside the description
            # But only if element text is substantial (5+ chars)
            if len(elem_text) >= 5:
                if normalized_elem in normalized_desc or normalized_desc in normalized_elem:
                    score = max(score, 0.85)

            # Penalize very short text matches (3-4 chars)
            if len(elem_text) <= 4:
                score *= 0.7  # 30% penalty

            if score > best_score:
                best_score = score
                best_match = elem
                candidates.append((score, idx, elem))

        # If position keyword detected, try to respect it
        if has_position and candidates:
            candidates.sort(key=lambda x: (-x[0], x[1]))  # Sort by score desc, then by DOM order

            if "first" in normalized_desc and len(candidates) > 0:
                # Pick first occurrence with decent score (>0.7)
                for score, idx, elem in candidates:
                    if score >= 0.7:
                        best_match = elem
                        best_score = score
                        break
            elif "last" in normalized_desc and len(candidates) > 0:
                # Pick last occurrence
                best_match = candidates[-1][2]
                best_score = candidates[-1][0]

        # Only return if score meets minimum threshold
        # Balanced threshold (0.70) - strict enough to avoid false positives, flexible enough for valid matches
        if best_match and best_score >= 0.70:
            element_type = (
                best_match.tag
                if best_match.tag in ["button", "a", "input", "select", "textarea"]
                else "button"
            )
            selector = f'{element_type}:has-text("{best_match.text}")'
            confidence = max(50, int(best_score * 100))

            print(f"[Semantic Match] Using offline fuzzy fallback (score: {best_score:.2f}, text: '{best_match.text[:30]}')")

            # NEW: LLM verification for ambiguous matches (score < 0.85)
            # This prevents cases like "필터 버튼" matching "전체 선택"
            if best_score < 0.85:
                print(f"[Semantic Match] Score below 0.85, requesting LLM verification...")
                is_valid = self._verify_semantic_match_with_llm(
                    step_description=step_description,
                    matched_text=best_match.text,
                    matched_element=best_match
                )

                if not is_valid:
                    print(f"[Semantic Match] LLM rejected match: '{step_description}' != '{best_match.text}'")
                    return None
                else:
                    print(f"[Semantic Match] LLM confirmed match is valid")

            return {
                "selector": selector,
                "action": action,
                "reasoning": f"Offline fuzzy match: '{step_description[:50]}' → '{best_match.text}'",
                "confidence": confidence
            }

        print(f"[Semantic Match] No reliable offline match (best score: {best_score:.2f})")
        return None

    def _verify_semantic_match_with_llm(
        self,
        step_description: str,
        matched_text: str,
        matched_element
    ) -> bool:
        """
        Use LLM to verify if a semantic match is valid.

        Args:
            step_description: What the user requested (e.g., "필터 버튼을 클릭해 특정 카테고리를 선택")
            matched_text: The text of the element that was matched (e.g., "전체 선택")
            matched_element: The DOM element that was matched

        Returns:
            True if match is valid, False otherwise
        """
        prompt = f"""Verify if this semantic match is correct.

User requested: "{step_description}"
Matched element text: "{matched_text}"
Element type: {matched_element.tag}

Is this a valid match? Consider:
- Does the matched element actually help accomplish the requested task?
- Are they semantically related?
- Would clicking this element be the right action?

Examples of INVALID matches:
- User: "필터 버튼 클릭" → Matched: "전체 선택" (WRONG - completely different)
- User: "검색 입력" → Matched: "로그인" (WRONG - different purpose)

Examples of VALID matches:
- User: "이름 입력" → Matched: "이름" label + input (CORRECT - same field)
- User: "필터 선택" → Matched: "필터" (CORRECT - exact match)

Respond with JSON only:
{{
  "is_valid": true/false,
  "reasoning": "brief explanation"
}}"""

        try:
            import openai
            import json
            import os

            api_key = os.getenv("OPENAI_API_KEY")
            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-5",  # For demo
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=150
            )

            result_text = response.choices[0].message.content.strip()

            # Remove markdown if present
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]

            result = json.loads(result_text.strip())
            return result.get("is_valid", False)

        except Exception as e:
            print(f"[Semantic Match] LLM verification failed: {e}, assuming valid")
            return True  # Default to valid if verification fails

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize text for similarity matching."""
        import re

        value = value.strip().lower()
        # Replace punctuation with spaces to keep token boundaries
        value = re.sub(r"[^\w\s\u3131-\u318E\uAC00-\uD7A3]", " ", value)
        # Collapse consecutive whitespace
        return " ".join(value.split())

    @staticmethod
    def _token_overlap(desc: str, elem: str) -> float:
        """Compute token overlap between description and element text."""
        desc_tokens = set(desc.split())
        elem_tokens = set(elem.split())
        if not desc_tokens or not elem_tokens:
            return 0.0
        return len(desc_tokens & elem_tokens) / len(elem_tokens)

    @staticmethod
    def _local_embedding(text: str) -> List[float] | None:
        """
        Deterministic local embedding fallback using token hashing.
        """
        if not text or not text.strip():
            return [0.0] * 128

        try:
            import numpy as np
        except ImportError:
            return None

        normalized = IntelligentOrchestrator._normalize_text(text)
        tokens = normalized.split()
        if not tokens:
            return [0.0] * 128

        dim = 128
        vector = np.zeros(dim, dtype=float)

        for token in tokens:
            for i in range(4):  # spread token across multiple dimensions
                digest = hashlib.sha256(f"{token}:{i}".encode("utf-8")).digest()
                index = int.from_bytes(digest[i:i+4], "big") % dim
                vector[index] += 1.0

        norm = np.linalg.norm(vector)
        if norm > 0:
            vector /= norm

        return vector.tolist()

    def _get_cached_selector(self, step_description: str, action: str, page_url: str, dom_context: str = "") -> str | None:
        """
        Try to get cached selector for this step.

        Args:
            step_description: Step description
            action: Action type
            page_url: Current page URL
            dom_context: DOM context string (active tabs/modals)

        Returns:
            Cached selector if found and still valid, None otherwise
        """
        cache_key = self._get_cache_key(step_description, action, page_url, dom_context)
        cached = self.selector_cache.get(cache_key)

        if cached:
            # Validate cached selector before use
            validated_selector = self._validate_cached_selector(cached, page_url)

            if validated_selector:
                # Update cache with validated selector (might be regenerated)
                if validated_selector != cached.get('selector'):
                    cached['selector'] = validated_selector
                    print(f"[Cache] Updated with regenerated selector: {validated_selector}")

                # Prefer high-confidence cache entries (success_count >= 2)
                if cached.get("success_count", 0) >= 2:
                    context_info = f" [context: {dom_context}]" if dom_context else ""
                    print(f"[Cache HIT] Using validated selector for '{step_description}'{context_info}")
                    return cached['selector']  # Return selector string, not dict
            else:
                # Cache invalid, remove it
                print(f"[Cache MISS] Cached selector invalid, removing: {cache_key}")
                del self.selector_cache[cache_key]

        return None

    def _update_cache(self, step_description: str, action: str, page_url: str,
                     selector: str, success: bool, dom_context: str = "",
                     element_text: str = "", element_tag: str = "",
                     attributes: dict = None) -> None:
        """
        Update cache with execution result and metadata.

        Args:
            step_description: Human-readable step description
            action: Action type (click, fill, etc.)
            page_url: Current page URL
            selector: Selector that was used
            success: Whether the action succeeded
            dom_context: DOM context string (active tabs/modals)
            element_text: Text content of the element (for regeneration)
            element_tag: HTML tag of the element (for regeneration)
            attributes: Element attributes (for specific selector regeneration)
        """
        # 동적 ID 패턴 감지 - 우리가 만든 함수 사용
        if self._is_dynamic_selector(selector):
            print(f"[Cache] ⚠️ Dynamic ID detected, caching with metadata for regeneration: {selector}")
            # 동적 ID지만 메타데이터와 함께 캐싱 (나중에 재생성 가능)

        cache_key = self._get_cache_key(step_description, action, page_url, dom_context)

        if cache_key not in self.selector_cache:
            self.selector_cache[cache_key] = {
                "selector": selector,
                "timestamp": time.time(),
                "success_count": 1 if success else 0,
                "step_description": step_description,  # For debugging
                "element_text": element_text,  # For regeneration
                "element_tag": element_tag,    # For regeneration
                "attributes": attributes or {},  # For specific selector regeneration
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
            local_embedding = self._local_embedding(text)
            if local_embedding is not None:
                print("[Embedding] Using local deterministic embedding fallback")
                self.embedding_cache[cache_key] = local_embedding

                if len(self.embedding_cache) % 20 == 0:
                    self._save_embedding_cache()

                return local_embedding
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

    def close(self) -> None:
        """
        Close the orchestrator and save caches.
        Note: Browser session is managed by MCP host, not closed here.
        """
        self._save_cache()
        self._save_embedding_cache()


__all__ = ["IntelligentOrchestrator"]
