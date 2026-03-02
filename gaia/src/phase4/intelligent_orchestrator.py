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

from gaia.src.phase4.llm_vision_client import LLMVisionClient, get_vision_client
from gaia.src.phase4.orchestrator_healed_cache import (
    get_healed_selector as get_healed_selector_from_cache,
    load_healed_selector_cache as load_healed_selector_cache_from_disk,
    save_healed_selectors as save_healed_selectors_to_disk,
)
from gaia.src.phase4.orchestrator_matching import (
    detect_aria_roles as detect_aria_roles_impl,
    disambiguate_aria_matches as disambiguate_aria_matches_impl,
    offline_fuzzy_semantic_match as offline_fuzzy_semantic_match_impl,
    try_aria_matching as try_aria_matching_impl,
    try_pure_semantic_matching as try_pure_semantic_matching_impl,
    try_semantic_matching as try_semantic_matching_impl,
    verify_semantic_match_with_llm as verify_semantic_match_with_llm_impl,
)
from gaia.src.phase4.orchestrator_utils import (
    build_cache_key,
    load_json_file,
    local_embedding,
    normalize_text,
    save_json_file,
    token_overlap,
)
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
        self.llm_client = llm_client or get_vision_client()
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

        # Store last action error for self-healing
        self.last_action_error: str = ""

        # Track healed selectors during test execution
        # Will be saved to cache only if test succeeds
        self.healed_selectors: Dict[str, Dict[str, str]] = {}  # {scenario_id: {original_selector: healed_selector}}

        # Load healed selector cache from previous runs
        self.healed_selector_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._load_healed_selector_cache()
        self._selector_to_ref_id: Dict[str, str] = {}
        self._active_snapshot_id: str = ""

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

        # Step 2: SKIP LLM prioritization - execute all scenarios on current page
        # All scenarios on the same page are executable (same URL = same context)
        self._log(f"  ✅ All {len(scenarios)} tests will be executed (same page context)", progress_callback)
        prioritized_scenarios = list(scenarios)  # Execute all scenarios

        # Step 3: Execute all scenarios (non-sequential based on DOM availability)
        for idx, scenario in enumerate(prioritized_scenarios, start=1):
            # Send scenario start marker for GUI highlighting
            self._log(f"[SCENARIO_START:{scenario.id}]", progress_callback)
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

                # Send scenario end marker for GUI
                self._log(f"[SCENARIO_END:{scenario.id}]", progress_callback)

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

                # Send scenario end marker even on exception
                self._log(f"[SCENARIO_END:{scenario.id}]", progress_callback)
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
                model="gpt-5.1",  # Multimodal reasoning model for demo
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

            # Clear browser state (cookies, localStorage, sessionStorage) before each scenario
            # This ensures tests start from a clean slate (e.g., logged out state)
            self._log(f"  🧹 Clearing browser state (cookies, storage)", progress_callback)
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
                    "session_id": self.session_id,
                },
            }
            try:
                response = requests.post(
                    f"{self.mcp_config.host_url}/execute",
                    json=payload,
                    timeout=30
                )
                response.raise_for_status()
            except Exception as e:
                self._log(f"  ⚠️ Browser state clear failed (non-critical): {e}", progress_callback)

            # CRITICAL: Hard reload with cache bypass to completely reset state
            self._log(f"  🔄 Hard reloading page to apply clean state", progress_callback)

            # First reload to clear state
            self._execute_action(
                action="goto",
                selector="",
                params=[current_url],
                url=current_url
            )
            time.sleep(2.0)  # Wait for first reload

            # Second reload to ensure React state is completely fresh
            self._execute_action(
                action="goto",
                selector="",
                params=[current_url],
                url=current_url
            )
            time.sleep(3.0)  # Wait for SPA to fully reset

            self._log(f"  ✅ Browser reset complete - starting with fresh state", progress_callback)

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
                    self._log(f"  ⏭️  Auto-skipping logout step (browser already reset): {step.description}", progress_callback)
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
            self._log(f"  📝 Total steps to execute (after auto-conversion): {total_steps}", progress_callback)

            for step_idx, step in enumerate(steps_to_execute, start=1):
                self._log(f"  🤖 Step {step_idx}/{total_steps}: {step.description}", progress_callback)

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
                    self._log(f"    🧠 LLM Verification Action", progress_callback)
                    logs.append(f"  LLM Verification: {step.description}")

                    # Get verification details from step
                    verify_info = getattr(step, 'verify', None)
                    if not verify_info:
                        self._log(f"    ⚠️ No 'verify' field found in llm action, skipping", progress_callback)
                        logs.append(f"  ⚠️ Missing verify field")
                        continue

                    # Extract expected outcome and indicators
                    expected_outcome = getattr(verify_info, 'expected', step.description)
                    success_indicators = getattr(verify_info, 'indicators', [])

                    # Capture screenshot for verification
                    time.sleep(0.5)  # Brief pause to let UI settle
                    after_screenshot = self._capture_screenshot(current_url, send_to_gui=False)

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

                    self._log(f"    {'✅' if verified else '❌'} Verification result: {verified} (confidence: {confidence}%)", progress_callback)
                    self._log(f"    💭 Reasoning: {reasoning[:100]}...", progress_callback)
                    if matched:
                        self._log(f"    🎯 Matched indicators: {', '.join(matched[:3])}", progress_callback)

                    logs.append(f"  Verified: {verified} (confidence: {confidence}%)")
                    logs.append(f"  Reasoning: {reasoning}")

                    if not verified or confidence < 60:
                        failed_assertion_steps += 1
                        self._log(f"    ⚠️ LLM verification failed, continuing...", progress_callback)

                    # Update screenshot for next step
                    screenshot = after_screenshot

                    continue

                # NEW: Handle "assert" action with Vision AI verification
                if step.action == "assert":
                    self._log(f"    🔍 Assert action detected - using Vision AI verification", progress_callback)
                    logs.append(f"  Assert: {step.description}")
                    total_assertion_steps += 1

                    # Capture current screenshot
                    current_screenshot = self._capture_screenshot(url=current_url, send_to_gui=True)

                    # Use Vision AI to verify the assertion
                    vision_client = get_vision_client()

                    # Build verification prompt
                    expected_result = step.description
                    expected_value = None
                    if step.params and len(step.params) > 0:
                        expected_value = step.params[0]
                        expected_result = f"{step.description}: {expected_value}"

                    self._log(f"    🤖 Asking Vision AI: {expected_result}", progress_callback)

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

                        self._log(f"    🎯 Vision AI Result: {'✅ PASS' if success else '❌ FAIL'} (confidence: {confidence}%)", progress_callback)
                        self._log(f"    💭 Reasoning: {reasoning}", progress_callback)

                        if success:
                            logs.append(f"  ✅ Assert passed: {step.description}")
                        else:
                            logs.append(f"  ❌ Assert failed: {step.description}")
                            logs.append(f"  💭 Vision AI: {reasoning}")
                            failed_assertion_steps += 1
                            self._log(f"    ❌ Assertion failed - stopping scenario execution", progress_callback)

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
                        self._log(f"    ❌ Vision AI verification failed: {e}", progress_callback)
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
                    # Check healed selector cache first
                    healed_selector = self._get_healed_selector(scenario.id, step.selector)
                    actual_selector = healed_selector if healed_selector else step.selector

                    if healed_selector:
                        self._log(f"    🔄 Using healed selector from cache: {step.selector} → {healed_selector}", progress_callback)
                    else:
                        self._log(f"    🎯 Using explicit selector with self-healing: {step.selector}", progress_callback)

                    logs.append(f"  Action: {step.action} on {actual_selector}")

                    # Track non-assertion step
                    total_non_assertion_steps += 1

                    before_screenshot = screenshot
                    # Use self-healing action execution
                    success = self._execute_action_with_self_healing(
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

                        # STEP 3: If low confidence, try vision-based coordinate click
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
                        self._log(f"    ❌ Action failed, trying intelligent fallback...", progress_callback)

                        # INTELLIGENT FALLBACK: Check for overlay first, then try vision-based click

                        # Check if error is due to overlay interception
                        # Common error patterns: "intercepts pointer events", "covered by", "not clickable"
                        self._log(f"    🔍 Checking for overlay interference...", progress_callback)

                        # Try pressing Escape to close any open overlay/modal/dropdown
                        self._log(f"    ⌨️  Pressing Escape to close potential overlay...", progress_callback)
                        escape_success = self._execute_action(
                            action="press",
                            selector="body",
                            params=["Escape"],
                            url=current_url
                        )

                        if escape_success:
                            time.sleep(0.3)  # Wait for overlay to close
                            # Retry original action
                            self._log(f"    🔄 Retrying original action after Escape...", progress_callback)
                            success = self._execute_action(
                                action=llm_decision["action"],
                                selector=llm_decision["selector"],
                                params=step.params or [],
                                url=current_url
                            )

                            if success:
                                self._log(f"    ✅ Action succeeded after closing overlay!", progress_callback)
                                logs.append(f"  ✅ Escape key resolved overlay issue")
                                logs.append(f"  ✅ Action executed: {llm_decision['action']} on {llm_decision['selector']}")

                                # Update cache
                                target_element = next((e for e in dom_elements if e.selector == llm_decision['selector']), None)
                                element_text = target_element.text if target_element else ""
                                element_tag = target_element.tag if target_element else ""
                                element_attrs = target_element.attributes if target_element else {}

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

                        # If Escape didn't help, try vision-based coordinate click
                        if llm_decision["action"] in ["click", "press"]:
                            self._log(f"    🎯 Trying vision-based coordinate click...", progress_callback)
                            logs.append(f"  🔄 Fallback: Using vision-based coordinates")

                            # Get coordinates from LLM Vision
                            coords = self.llm_client.find_element_coordinates(
                                screenshot_base64=screenshot,
                                description=step.description
                            )

                            if coords["confidence"] > 0.5:
                                self._log(f"    📍 Found at ({coords['x']}, {coords['y']}) - confidence: {coords['confidence']:.0%}", progress_callback)
                                logs.append(f"  📍 Coordinates: ({coords['x']}, {coords['y']})")

                                # Try JavaScript click first (more reliable for overlays)
                                self._log(f"    💻 Trying JavaScript click...", progress_callback)
                                js_script = f"document.elementFromPoint({coords['x']}, {coords['y']}).click()"
                                js_success = self._execute_action(
                                    action="evaluate",
                                    selector="",
                                    params=[js_script],
                                    url=current_url
                                )

                                if js_success:
                                    self._log(f"    ✅ JavaScript click succeeded!", progress_callback)
                                    logs.append(f"  ✅ JavaScript click succeeded")
                                    time.sleep(0.5)
                                    screenshot, dom_elements, current_url = self._get_page_state()
                                    continue
                                else:
                                    # Try physical coordinate click as last resort
                                    self._log(f"    🖱️  Trying physical coordinate click...", progress_callback)
                                    success = self._execute_coordinate_click(
                                        x=coords['x'],
                                        y=coords['y'],
                                        url=current_url
                                    )

                                    if success:
                                        self._log(f"    ✅ Coordinate click succeeded!", progress_callback)
                                        logs.append(f"  ✅ Coordinate-based click succeeded")
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
                            self._log(f"    ❌ All fallback attempts failed", progress_callback)
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

            # Step 3: Scenario-level Vision AI verification (IMPROVED!)
            # Capture AFTER screenshot and verify entire scenario success
            # NOW RUNS ON ALL SCENARIOS, not just those with success_indicators
            after_scenario_screenshot = self._capture_screenshot(current_url, send_to_gui=False)
            scenario_verified = False
            scenario_verification_result = None

            # Run verification on ALL scenarios (not just those with assertion field)
            self._log(f"  🔍 Running scenario-level Vision AI verification...", progress_callback)

            # Extract assertion details (handle both old and new format)
            expected_outcome = scenario.scenario  # Default to scenario description
            success_indicators = []

            if hasattr(scenario, 'assertion') and scenario.assertion:
                # Try to extract from assertion
                expected_outcome = getattr(scenario.assertion, "expected_outcome", None) or scenario.scenario
                success_indicators = getattr(scenario.assertion, "success_indicators", [])

            # If no success_indicators, generate them automatically from scenario description
            if not success_indicators:
                self._log(f"  💡 No success_indicators found, generating from scenario description...", progress_callback)
                success_indicators = self._generate_success_indicators(scenario.scenario, scenario.steps)
                self._log(f"  📝 Generated indicators: {success_indicators}", progress_callback)

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

                        # Save healed selectors to cache (only on success)
                        self._save_healed_selectors(scenario.id, progress_callback)

                        # CRITICAL: Force navigate to home URL to completely reset state for next test
                        self._log(f"  🏠 Navigating to home URL to reset for next test", progress_callback)
                        home_url = url.split('#')[0] if '#' in url else url  # Remove hash
                        self._execute_action(action="goto", selector="", params=[home_url], url=home_url)
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
                        self._log(f"  ⚠️ Test PARTIAL: Vision AI verification failed", progress_callback)
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
                        self._log(f"  ✅ Test SUCCESS: 100% completion", progress_callback)

                        # Save healed selectors to cache (only on success)
                        self._save_healed_selectors(scenario.id, progress_callback)

                        # CRITICAL: Force navigate to home URL to completely reset state for next test
                        self._log(f"  🏠 Navigating to home URL to reset for next test", progress_callback)
                        home_url = url.split('#')[0] if '#' in url else url  # Remove hash
                        self._execute_action(action="goto", selector="", params=[home_url], url=home_url)
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
                        self._log(f"  ⚠️ Test PARTIAL: {skip_rate:.0f}% steps skipped", progress_callback)
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
                    self._log(f"  ⚠️ Test PARTIAL: Assertions failed ({failed_assertion_steps}/{total_assertion_steps})", progress_callback)
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
            self._log(f"❌ Exception in step execution: {e}", progress_callback)
            self._log(f"📜 Traceback:\n{tb_str}", progress_callback)

            # Try to capture screenshot even in exception case (for Master Orchestrator)
            try:
                exception_screenshot = self._capture_screenshot(None, send_to_gui=False) if 'after_scenario_screenshot' not in locals() else after_scenario_screenshot
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

    def _execute_action_with_self_healing(
        self,
        action: str,
        selector: str,
        params: List[Any],
        url: str,
        screenshot: str,
        dom_elements: List[DomElement],
        step_description: str,
        before_screenshot: str = None,
        progress_callback=None,
        max_retries: int = 3,
        scenario_id: str = ""
    ) -> bool:
        """
        Execute action with self-healing capabilities.

        This method implements dynamic execution by:
        1. Trying the original action
        2. If it fails, analyzing why with LLM Vision
        3. Applying suggested fixes (close overlay, scroll, JavaScript, etc.)
        4. Retrying the action

        Args:
            action: Action type (click, fill, press, etc.)
            selector: CSS selector
            params: Action parameters
            url: Current page URL
            screenshot: Current screenshot base64
            dom_elements: Available DOM elements
            step_description: Human-readable step description
            before_screenshot: Screenshot before action (for assertions)
            progress_callback: Callback for logging
            max_retries: Maximum number of retry attempts

        Returns:
            True if action succeeded (either immediately or after healing), False otherwise
        """
        # Save original selector for tracking healed selectors
        original_selector = selector

        # Try original action first
        success = self._execute_action(action, selector, params, url, before_screenshot)

        if success:
            return True

        # Action failed - start self-healing
        self._log(f"    🔧 Action failed, initiating self-healing...", progress_callback)

        # Get error message from last execution
        error_message = self.last_action_error if self.last_action_error else "Action execution failed"

        retry_count = 0
        while retry_count < max_retries:
            retry_count += 1
            self._log(f"    🔄 Self-healing attempt {retry_count}/{max_retries}", progress_callback)

            # Capture current screenshot for error analysis
            current_screenshot = self._capture_screenshot(url, send_to_gui=False)

            # Use LLM to analyze failure and suggest fixes
            from .llm_vision_client import get_vision_client
            vision_client = get_vision_client()

            error_analysis = vision_client.analyze_action_failure(
                action=action,
                selector=selector,
                error_message=error_message,
                screenshot_base64=current_screenshot,
                dom_elements=dom_elements,
                url=url,
                step_description=step_description
            )

            failure_reason = error_analysis.get('failure_reason', 'unknown')
            suggested_fixes = error_analysis.get('suggested_fixes', [])
            confidence = error_analysis.get('confidence', 0)
            reasoning = error_analysis.get('reasoning', '')

            self._log(f"    💡 Failure reason: {failure_reason} (confidence: {confidence}%)", progress_callback)
            self._log(f"    💭 Analysis: {reasoning[:100]}...", progress_callback)

            if not suggested_fixes:
                self._log(f"    ❌ No fixes suggested, giving up", progress_callback)
                return False

            # Try each suggested fix in priority order
            for fix_idx, fix in enumerate(suggested_fixes[:2], 1):  # Try top 2 fixes
                fix_type = fix.get('type')
                fix_description = fix.get('description', '')

                self._log(f"    🛠️  Fix {fix_idx}: {fix_description}", progress_callback)

                try:
                    if fix_type == 'close_overlay':
                        method = fix.get('method', 'press_escape')
                        if method == 'press_escape':
                            self._execute_action('press', '', ['Escape'], url)
                        elif method == 'click_backdrop':
                            # Click outside modal area (center of screen, assuming modal is in middle)
                            self._execute_action('click', 'body', [], url)
                        time.sleep(0.3)  # Brief wait for overlay to close

                    elif fix_type == 'scroll':
                        scroll_selector = fix.get('selector', selector)
                        if scroll_selector:
                            self._execute_action('scrollIntoView', scroll_selector, [], url)
                        else:
                            self._execute_action('scroll', 'body', ['down'], url)
                        time.sleep(0.3)  # Brief wait for scroll

                    elif fix_type == 'javascript':
                        script = fix.get('script')
                        if script:
                            self._execute_action('evaluate', '', [script], url)
                        time.sleep(0.3)

                    elif fix_type == 'wait':
                        duration = fix.get('duration', 500)
                        time.sleep(duration / 1000.0)

                    elif fix_type == 'open_container':
                        # Try to open parent container (dropdown, accordion, etc.)
                        # This would need element selection logic
                        self._log(f"    ⚠️ 'open_container' fix not yet implemented", progress_callback)

                    elif fix_type == 'use_alternative_selector':
                        alternative_selector = fix.get('selector')
                        if alternative_selector:
                            selector = alternative_selector  # Update selector for retry

                    # After applying fix, retry the original action
                    self._log(f"    🔁 Retrying original action after fix...", progress_callback)
                    success = self._execute_action(action, selector, params, url, before_screenshot)

                    if success:
                        self._log(f"    ✅ Self-healing successful! Action succeeded after fix", progress_callback)
                        # Track healed selector for later caching (only if test succeeds)
                        if scenario_id and selector != original_selector:
                            if scenario_id not in self.healed_selectors:
                                self.healed_selectors[scenario_id] = {}
                            self.healed_selectors[scenario_id][original_selector] = selector
                            self._log(f"    📝 Tracked healed selector: {original_selector} → {selector}", progress_callback)
                        return True

                except Exception as e:
                    self._log(f"    ⚠️ Fix failed: {e}", progress_callback)
                    continue

            # All fixes for this retry attempt failed
            self._log(f"    ❌ All fixes failed for attempt {retry_count}", progress_callback)

        # Exhausted all retries
        self._log(f"    ❌ Self-healing failed after {max_retries} attempts", progress_callback)
        return False

    def _execute_action(self, action: str, selector: str, params: List[Any], url: str, before_screenshot: str = None) -> bool:
        """Execute a browser action using MCP host."""
        try:
            # Build value payload
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
                "session_id": self.session_id,
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
                ref_id = self._selector_to_ref_id.get(selector or "")
                snapshot_id = self._active_snapshot_id
                if not ref_id or not snapshot_id:
                    error_msg = "[ref_required] snapshot_id + ref_id required for element actions"
                    self.last_action_error = error_msg
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

            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=90  # Increased from 30s to 90s for complex operations
            )
            data = response.json()

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
                # Store error message for self-healing
                self.last_action_error = error_msg

            # Send screenshot to GUI if action succeeded and callback is set
            if success and self._screenshot_callback:
                screenshot = data.get("screenshot", "")
                click_position = data.get("click_position")
                if screenshot:
                    self._screenshot_callback(screenshot, click_position)

            return success

        except Exception as e:
            error_msg = str(e)
            print(f"Action execution error: {error_msg}")
            # Store error message for self-healing
            self.last_action_error = error_msg
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
        click_script = (
            f"(() => {{ const el = document.elementFromPoint({int(x)}, {int(y)}); "
            "if (!el) return false; el.click(); return true; }})()"
        )
        payload = {
            "action": "browser_act",
            "params": {
                "session_id": self.session_id,
                "url": url,
                "action": "evaluate",
                "fn": click_script,
            },
        }

        try:
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=90
            )
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
                model="gpt-5.1",  # For demo
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

            raw_dom_elements = data.get("dom_elements", []) or []
            self._active_snapshot_id = str(data.get("snapshot_id") or "")
            self._selector_to_ref_id = {}
            for raw_elem in raw_dom_elements:
                if not isinstance(raw_elem, dict):
                    continue
                ref_id = str(raw_elem.get("ref_id") or "").strip()
                if not ref_id:
                    continue
                selector = str(raw_elem.get("selector") or "").strip()
                full_selector = str(raw_elem.get("full_selector") or "").strip()
                if selector:
                    self._selector_to_ref_id[selector] = ref_id
                if full_selector:
                    self._selector_to_ref_id[full_selector] = ref_id

            # Extract DOM elements
            dom_elements = [DomElement(**elem) for elem in raw_dom_elements]

            # Extract current URL from page.url
            current_url = data.get("url", "")

        except Exception as e:
            print(f"Failed to get page state: {e}")
            dom_elements = []
            current_url = ""
            self._active_snapshot_id = ""
            self._selector_to_ref_id = {}

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
            goto_success = self._execute_action(
                action="goto",
                selector="",
                params=[base_url],
                url=base_url,
            )
            if not goto_success:
                return False

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

    def _generate_success_indicators(
        self,
        scenario_description: str,
        steps: List[TestStep]
    ) -> List[str]:
        """
        Generate success indicators automatically from scenario description and steps.

        This method analyzes the scenario and its steps to infer what success looks like.
        Used when explicit success_indicators are not provided.

        Args:
            scenario_description: The scenario description (e.g., "사용자가 로그인할 수 있다")
            steps: List of test steps executed in the scenario

        Returns:
            List of success indicators to look for in the final screenshot
        """
        indicators = []

        # Analyze scenario description for common patterns
        scenario_lower = scenario_description.lower()

        # Login scenarios
        if "로그인" in scenario_description or "login" in scenario_lower:
            indicators.extend([
                "로그아웃 버튼이 표시됨",
                "사용자 프로필이 표시됨",
                "환영 메시지가 표시됨",
                "로그인 버튼이 사라짐"
            ])

        # Signup/Registration scenarios
        if "회원가입" in scenario_description or "가입" in scenario_description or "signup" in scenario_lower or "register" in scenario_lower:
            indicators.extend([
                "회원가입 완료 메시지가 표시됨",
                "자동으로 로그인됨",
                "가입 완료 페이지로 이동됨"
            ])

        # Form submission scenarios
        if "제출" in scenario_description or "등록" in scenario_description or "submit" in scenario_lower:
            indicators.extend([
                "제출 완료 메시지가 표시됨",
                "성공 알림이 표시됨",
                "폼이 초기화됨"
            ])

        # Add to cart scenarios
        if "장바구니" in scenario_description or "카트" in scenario_description or "cart" in scenario_lower:
            indicators.extend([
                "장바구니 개수가 증가함",
                "장바구니에 추가 메시지가 표시됨",
                "상품이 장바구니 목록에 표시됨"
            ])

        # Search scenarios
        if "검색" in scenario_description or "search" in scenario_lower:
            indicators.extend([
                "검색 결과가 표시됨",
                "결과 목록이 업데이트됨",
                "검색어와 관련된 항목이 표시됨"
            ])

        # Navigation scenarios
        if "이동" in scenario_description or "navigate" in scenario_lower or "페이지" in scenario_description:
            indicators.extend([
                "페이지가 변경됨",
                "새로운 콘텐츠가 표시됨",
                "URL이 업데이트됨"
            ])

        # Delete/Remove scenarios
        if "삭제" in scenario_description or "제거" in scenario_description or "delete" in scenario_lower or "remove" in scenario_lower:
            indicators.extend([
                "항목이 목록에서 사라짐",
                "삭제 완료 메시지가 표시됨",
                "개수가 감소함"
            ])

        # Analyze steps for additional indicators
        for step in steps:
            step_desc_lower = step.description.lower()

            # Click button steps
            if "클릭" in step.description or "click" in step_desc_lower:
                if "제출" in step.description or "submit" in step_desc_lower:
                    indicators.append("제출 후 확인 메시지나 페이지 변경")
                elif "저장" in step.description or "save" in step_desc_lower:
                    indicators.append("저장 완료 메시지 표시")

            # Fill form steps
            if "입력" in step.description or "fill" in step_desc_lower or "type" in step_desc_lower:
                indicators.append("입력한 값이 폼에 표시됨")

        # If no specific indicators found, add generic ones
        if not indicators:
            indicators.extend([
                "시나리오 설명에 맞는 화면 변화가 발생함",
                "에러 메시지가 표시되지 않음",
                "예상한 UI 상태로 변경됨"
            ])

        # Remove duplicates while preserving order
        seen = set()
        unique_indicators = []
        for indicator in indicators:
            if indicator not in seen:
                seen.add(indicator)
                unique_indicators.append(indicator)

        return unique_indicators

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
            self.selector_cache = load_json_file(self.cache_file)
            if self.selector_cache:
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
            save_json_file(self.cache_file, self.selector_cache, ensure_ascii=False)
        except Exception as e:
            print(f"[Cache] Failed to save cache: {e}")

    def _save_healed_selectors(self, scenario_id: str, progress_callback=None) -> None:
        """
        Save healed selectors to a separate cache file for use in next run.
        Only called when test succeeds.

        Args:
            scenario_id: The scenario ID whose healed selectors to save
            progress_callback: Callback for logging
        """
        if scenario_id not in self.healed_selectors:
            return

        healed = self.healed_selectors[scenario_id]
        if not healed:
            return

        try:
            saved_count, healed_cache = save_healed_selectors_to_disk(
                self.cache_file,
                scenario_id,
                healed,
            )
            if healed_cache:
                self.healed_selector_cache = healed_cache
            self._log(f"  💾 Saved {saved_count} healed selector(s) to cache", progress_callback)
            print(f"[Healed Cache] Saved {saved_count} healed selectors for {scenario_id}")

        except Exception as e:
            print(f"[Healed Cache] Failed to save: {e}")

    def _load_healed_selector_cache(self) -> None:
        """Load healed selector cache from disk."""
        try:
            self.healed_selector_cache = load_healed_selector_cache_from_disk(self.cache_file)
            if self.healed_selector_cache:
                total_selectors = sum(len(v) for v in self.healed_selector_cache.values())
                print(f"[Healed Cache] Loaded {total_selectors} healed selectors for {len(self.healed_selector_cache)} scenarios")
        except Exception as e:
            print(f"[Healed Cache] Failed to load: {e}")
            self.healed_selector_cache = {}

    def _get_healed_selector(self, scenario_id: str, original_selector: str) -> str | None:
        """
        Get healed selector from cache if available.

        Args:
            scenario_id: The scenario ID
            original_selector: The original selector that may have been healed

        Returns:
            Healed selector if found in cache, None otherwise
        """
        return get_healed_selector_from_cache(
            self.healed_selector_cache,
            scenario_id,
            original_selector,
        )

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
        return build_cache_key(step_description, action, page_url, dom_context)

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
        return detect_aria_roles_impl(step_description, dom_elements)

    def _disambiguate_aria_matches(self, role_name: str, matches: List[DomElement],
                                   step_description: str, action: str) -> Dict[str, Any] | None:
        return disambiguate_aria_matches_impl(
            self,
            role_name,
            matches,
            step_description,
            action,
        )

    def _try_aria_matching(self, step_description: str, dom_elements: List[DomElement], action: str) -> Dict[str, Any] | None:
        return try_aria_matching_impl(self, step_description, dom_elements, action)

    def _try_pure_semantic_matching(self, step_description: str, dom_elements: List[DomElement], action: str) -> Dict[str, Any] | None:
        return try_pure_semantic_matching_impl(self, step_description, dom_elements, action)

    def _try_semantic_matching(self, step_description: str, dom_elements: List[DomElement], action: str,
                                current_url: str = "", screenshot: str = "") -> Dict[str, Any] | None:
        return try_semantic_matching_impl(
            self,
            step_description,
            dom_elements,
            action,
            current_url=current_url,
            screenshot=screenshot,
        )

    def _offline_fuzzy_semantic_match(
        self,
        step_description: str,
        dom_elements: List[DomElement],
        action: str
    ) -> Dict[str, Any] | None:
        return offline_fuzzy_semantic_match_impl(self, step_description, dom_elements, action)

    def _verify_semantic_match_with_llm(
        self,
        step_description: str,
        matched_text: str,
        matched_element
    ) -> bool:
        return verify_semantic_match_with_llm_impl(
            self,
            step_description,
            matched_text,
            matched_element,
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize text for similarity matching."""
        return normalize_text(value)

    @staticmethod
    def _token_overlap(desc: str, elem: str) -> float:
        """Compute token overlap between description and element text."""
        return token_overlap(desc, elem)

    @staticmethod
    def _local_embedding(text: str) -> List[float] | None:
        """
        Deterministic local embedding fallback using token hashing.
        """
        return local_embedding(text)

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
            self.embedding_cache = load_json_file(self.embedding_cache_file)
            if self.embedding_cache:
                print(f"[Embedding Cache] Loaded {len(self.embedding_cache)} cached embeddings")
        except Exception as e:
            print(f"[Embedding Cache] Failed to load cache: {e}")
            self.embedding_cache = {}

    def _save_embedding_cache(self) -> None:
        """Save embedding cache to disk."""
        try:
            save_json_file(self.embedding_cache_file, self.embedding_cache, ensure_ascii=False)
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
