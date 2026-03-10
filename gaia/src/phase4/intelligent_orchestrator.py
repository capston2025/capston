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
from gaia.src.phase4.orchestrator_init_runtime import initialize_runtime_state
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
from gaia.src.phase4.orchestrator_cache_runtime import (
    detect_dom_context as detect_dom_context_impl_runtime,
    get_cache_key as get_cache_key_impl_runtime,
    get_cached_selector as get_cached_selector_impl_runtime,
    get_embedding as get_embedding_impl_runtime,
    get_healed_selector as get_healed_selector_impl_runtime,
    load_cache as load_cache_impl_runtime,
    load_embedding_cache as load_embedding_cache_impl_runtime,
    load_healed_selector_cache as load_healed_selector_cache_impl_runtime,
    save_cache as save_cache_impl_runtime,
    save_embedding_cache as save_embedding_cache_impl_runtime,
    save_healed_selectors as save_healed_selectors_impl_runtime,
    update_cache as update_cache_impl_runtime,
)
from gaia.src.phase4.orchestrator_selector_runtime import (
    create_stable_selector as create_stable_selector_impl,
    is_dynamic_selector as is_dynamic_selector_impl,
    validate_cached_selector as validate_cached_selector_impl,
)
from gaia.src.phase4.orchestrator_page_state_runtime import (
    generate_success_indicators as generate_success_indicators_impl,
    get_page_state as get_page_state_impl,
    record_page_elements as record_page_elements_impl,
    try_recover_from_empty_dom as try_recover_from_empty_dom_impl,
)
from gaia.src.phase4.orchestrator_action_runtime import (
    execute_action as execute_action_impl,
    execute_action_with_self_healing as execute_action_with_self_healing_impl,
    execute_coordinate_click as execute_coordinate_click_impl,
)
from gaia.src.phase4.orchestrator_scenario_runtime import execute_single_scenario_impl
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
        initialize_runtime_state(
            self,
            tracker=tracker,
            mcp_config=mcp_config,
            llm_client=llm_client,
            screenshot_callback=screenshot_callback,
            session_id=session_id,
        )

    # ==================== Dynamic ID Detection & Stable Selector Generation ====================

    def _is_dynamic_selector(self, selector: str) -> bool:
        return is_dynamic_selector_impl(selector, DYNAMIC_ID_PATTERNS)

    def _create_stable_selector(self, elem: DomElement) -> Optional[str]:
        return create_stable_selector_impl(elem, DYNAMIC_ID_PATTERNS)

    def _validate_cached_selector(
        self,
        cached_data: Dict[str, Any],
        current_url: str
    ) -> Optional[str]:
        return validate_cached_selector_impl(cached_data, current_url, DYNAMIC_ID_PATTERNS)

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
        return execute_single_scenario_impl(
            self,
            url,
            scenario,
            progress_callback=progress_callback,
            initial_dom_elements=initial_dom_elements,
            initial_screenshot=initial_screenshot,
        )

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
        return execute_action_with_self_healing_impl(
            self,
            action,
            selector,
            params,
            url,
            screenshot,
            dom_elements,
            step_description,
            before_screenshot,
            progress_callback,
            max_retries,
            scenario_id,
        )

    def _execute_action(self, action: str, selector: str, params: List[Any], url: str, before_screenshot: str = None) -> bool:
        return execute_action_impl(self, action, selector, params, url, before_screenshot)


    def _execute_coordinate_click(self, x: int, y: int, url: str) -> bool:
        return execute_coordinate_click_impl(self, x, y, url)

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
        return get_page_state_impl(self)

    def _try_recover_from_empty_dom(
        self,
        current_url: str,
        progress_callback=None
    ) -> bool:
        return try_recover_from_empty_dom_impl(self, current_url, progress_callback)

    def _generate_success_indicators(
        self,
        scenario_description: str,
        steps: List[TestStep]
    ) -> List[str]:
        return generate_success_indicators_impl(scenario_description, steps)

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
        record_page_elements_impl(self, url, dom_elements)

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
        load_cache_impl_runtime(self)

    def _save_cache(self) -> None:
        save_cache_impl_runtime(self)

    def _save_healed_selectors(self, scenario_id: str, progress_callback=None) -> None:
        save_healed_selectors_impl_runtime(self, scenario_id, progress_callback)

    def _load_healed_selector_cache(self) -> None:
        load_healed_selector_cache_impl_runtime(self)

    def _get_healed_selector(self, scenario_id: str, original_selector: str) -> str | None:
        return get_healed_selector_impl_runtime(self, scenario_id, original_selector)

    def _get_cache_key(self, step_description: str, action: str, page_url: str, dom_context: str = "") -> str:
        return get_cache_key_impl_runtime(step_description, action, page_url, dom_context)

    def _detect_dom_context(self, dom_elements: List[DomElement]) -> str:
        return detect_dom_context_impl_runtime(dom_elements)

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

    def _get_cached_selector(self, step_description: str, action: str, page_url: str, dom_context: str = "") -> Dict[str, Any] | None:
        return get_cached_selector_impl_runtime(self, step_description, action, page_url, dom_context)

    def _update_cache(
        self,
        step_description: str,
        action: str,
        page_url: str,
        selector: str,
        success: bool,
        dom_context: str = "",
        element_text: str = "",
        element_tag: str = "",
        attributes=None,
    ) -> None:
        update_cache_impl_runtime(
            self,
            step_description,
            action,
            page_url,
            selector,
            success,
            dom_context=dom_context,
            element_text=element_text,
            element_tag=element_tag,
            attributes=attributes,
        )

    def _get_embedding(self, text: str):
        return get_embedding_impl_runtime(self, text)

    def _load_embedding_cache(self) -> None:
        load_embedding_cache_impl_runtime(self)

    def _save_embedding_cache(self) -> None:
        save_embedding_cache_impl_runtime(self)

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

    def close(self) -> None:
        """
        Close the orchestrator and save caches.
        Note: Browser session is managed by MCP host, not closed here.
        """
        self._save_cache()
        self._save_embedding_cache()


__all__ = ["IntelligentOrchestrator"]
