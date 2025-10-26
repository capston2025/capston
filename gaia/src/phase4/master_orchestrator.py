"""
Master Orchestrator - Automatic site exploration and test execution.
Discovers navigation links and executes tests page-by-page.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Sequence

from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator
from gaia.src.phase4.llm_vision_client import LLMVisionClient
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.config import CONFIG, MCPConfig
from gaia.src.utils.models import TestScenario


class MasterOrchestrator:
    """
    Master orchestrator that explores entire site and executes tests page-by-page.

    Flow:
    1. Analyze home page → discover navigation links (LLM)
    2. For each page in site map:
       - Navigate to page
       - Execute tests using IntelligentOrchestrator
       - Track which tests were executed
    3. Return aggregated results
    """

    def __init__(
        self,
        tracker: ChecklistTracker | None = None,
        mcp_config: MCPConfig | None = None,
        llm_client: LLMVisionClient | None = None,
        screenshot_callback=None,
    ) -> None:
        """
        Initialize the master orchestrator.

        Args:
            tracker: Checklist tracker for marking progress
            mcp_config: MCP host configuration
            llm_client: LLM vision client (defaults to GPT-4o)
            screenshot_callback: Optional callback for real-time screenshot updates
        """
        self.tracker = tracker or ChecklistTracker()
        self.mcp_config = mcp_config or CONFIG.mcp
        self.llm_client = llm_client or LLMVisionClient()
        self._screenshot_callback = screenshot_callback
        self.intelligent_orch = IntelligentOrchestrator(
            tracker=self.tracker,
            mcp_config=self.mcp_config,
            llm_client=self.llm_client,
            screenshot_callback=screenshot_callback
        )
        self._execution_logs: List[str] = []
        self._executed_test_ids: set[str] = set()

    def execute_scenarios(
        self,
        url: str,
        scenarios: Sequence[TestScenario],
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Execute test scenarios with automatic site exploration.

        Flow:
        1. Explore site → build page map
        2. For each page:
           - Navigate to page
           - Execute relevant tests
        3. Aggregate results

        Args:
            url: Starting URL (home page)
            scenarios: List of test scenarios to execute
            progress_callback: Optional callback for progress updates

        Returns:
            Dict with execution results and logs
        """
        self._execution_logs = []
        self._executed_test_ids = set()

        aggregated_results = {
            "total": len(scenarios),
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "scenarios": [],
            "pages_explored": 0
        }

        # Step 1: Explore site to discover pages
        self._log("🗺️  Step 1: Exploring site to discover pages...", progress_callback)
        site_map = self._explore_site(url, progress_callback)

        if not site_map:
            self._log("⚠️ No pages discovered, executing tests on home page only", progress_callback)
            site_map = [{"name": "Home", "url": url, "selector": None}]

        self._log(f"✅ Discovered {len(site_map)} pages to explore", progress_callback)
        aggregated_results["pages_explored"] = len(site_map)

        # Step 2: Execute tests page-by-page
        for page_idx, page_info in enumerate(site_map, start=1):
            page_name = page_info["name"]
            page_url = page_info["url"]
            nav_selector = page_info.get("selector")

            self._log(f"\n📄 Page {page_idx}/{len(site_map)}: {page_name}", progress_callback)

            # Navigate to page if needed
            if nav_selector:
                self._log(f"  🔗 Navigating via: {nav_selector}", progress_callback)
                success = self._navigate_to_page(page_url, nav_selector)
                if not success:
                    self._log(f"  ⚠️ Navigation failed, skipping page", progress_callback)
                    continue
                # Wait for page to load and stabilize
                import time
                time.sleep(2)

            # Filter scenarios that haven't been executed yet
            remaining_scenarios = [
                s for s in scenarios
                if s.id not in self._executed_test_ids
            ]

            if not remaining_scenarios:
                self._log(f"  ✅ All tests already executed, skipping page", progress_callback)
                continue

            self._log(f"  🚀 Executing {len(remaining_scenarios)} remaining tests on this page", progress_callback)

            # Execute tests on this page
            page_results = self.intelligent_orch.execute_scenarios(
                url=page_url,
                scenarios=remaining_scenarios,
                progress_callback=progress_callback
            )

            # Aggregate results
            for scenario_result in page_results["scenarios"]:
                scenario_id = scenario_result["id"]

                # Only count each scenario once (skip if already executed on another page)
                if scenario_id not in self._executed_test_ids:
                    aggregated_results["scenarios"].append(scenario_result)

                    status = scenario_result["status"]
                    if status == "passed":
                        aggregated_results["passed"] += 1
                        self._executed_test_ids.add(scenario_id)
                    elif status == "failed":
                        aggregated_results["failed"] += 1
                        self._executed_test_ids.add(scenario_id)
                    elif status == "skipped":
                        # Don't mark as executed - might be executable on another page
                        pass

            self._log(f"  📊 Page {page_idx} results: {page_results['passed']} passed, {page_results['failed']} failed, {page_results['skipped']} skipped", progress_callback)

        # Calculate final skip count
        aggregated_results["skipped"] = (
            aggregated_results["total"]
            - aggregated_results["passed"]
            - aggregated_results["failed"]
        )

        self._log(f"\n🎉 Site exploration complete!", progress_callback)
        self._log(f"   📄 Pages explored: {aggregated_results['pages_explored']}", progress_callback)
        self._log(f"   ✅ Passed: {aggregated_results['passed']}/{aggregated_results['total']}", progress_callback)
        self._log(f"   ❌ Failed: {aggregated_results['failed']}/{aggregated_results['total']}", progress_callback)
        self._log(f"   ⏭️  Skipped: {aggregated_results['skipped']}/{aggregated_results['total']}", progress_callback)

        return aggregated_results

    def _explore_site(
        self,
        url: str,
        progress_callback=None,
    ) -> List[Dict[str, str]]:
        """
        Explore site to discover navigation structure.

        Uses LLM + screenshot to identify navigation links/buttons.

        Returns:
            List of pages: [{"name": "Home", "url": "...", "selector": None}, ...]
        """
        try:
            # Analyze DOM and capture screenshot
            import requests
            import json

            dom_payload = {"action": "analyze_page", "params": {"url": url}}
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=dom_payload,
                timeout=30
            )
            response.raise_for_status()
            dom_data = response.json()
            dom_elements = dom_data.get("elements", [])

            screenshot_payload = {"action": "capture_screenshot", "params": {"url": url}}
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=screenshot_payload,
                timeout=30
            )
            response.raise_for_status()
            screenshot_data = response.json()
            screenshot = screenshot_data.get("screenshot", "")

            if not dom_elements or not screenshot:
                self._log("⚠️ Failed to analyze home page", progress_callback)
                return []

            # Ask LLM to identify navigation links
            dom_summary = "\n".join([
                f"- [{elem.get('element_type')}] {elem.get('tag')}: {elem.get('selector')} (text: {elem.get('text', '')[:50]})"
                for elem in dom_elements[:50]
            ])

            prompt = f"""You are a website navigation agent. Analyze this page to find navigation links/buttons to other pages.

**Current Page:** {url}

**DOM Elements:**
{dom_summary}

**Your Task:**
1. Look at the screenshot to identify main navigation elements
2. Infer which page each link/button leads to
3. Find category buttons, menu links, tabs, etc.

**CRITICAL INSTRUCTIONS:**
- You MUST respond with ONLY valid JSON array
- Do NOT include any explanatory text outside the JSON
- Do NOT use markdown code blocks (no ```)
- First item is always current page (selector: null)
- selector must exist in DOM elements list above
- Only include important pages (login, search, cart, main features)

**Response Format (JSON ONLY):**
[
  {{"name": "Home", "selector": null, "description": "Main homepage"}},
  {{"name": "Basic Features", "selector": "button:has-text(\\"기본 기능\\")", "description": "Basic website features page"}},
  {{"name": "Forms & Feedback", "selector": "button:has-text(\\"폼과 피드백\\")", "description": "Form inputs and feedback page"}}
]

**Response (JSON array only, no other text):**"""

            response_text = self.llm_client.analyze_with_vision(
                prompt=prompt,
                screenshot_base64=screenshot
            )

            # Parse LLM response
            pages = json.loads(response_text)

            # Add current URL to each page
            result = []
            for page in pages:
                result.append({
                    "name": page["name"],
                    "url": url,  # For now, all pages use same URL (SPA)
                    "selector": page.get("selector")
                })

            return result

        except Exception as e:
            self._log(f"⚠️ Site exploration failed: {e}", progress_callback)
            return []

    def _navigate_to_page(self, url: str, selector: str) -> bool:
        """
        Navigate to a page by clicking a selector.

        Args:
            url: Current page URL
            selector: CSS selector to click

        Returns:
            True if navigation succeeded, False otherwise
        """
        import requests

        payload = {
            "action": "execute_action",
            "params": {
                "url": url,
                "selector": selector,
                "action": "click"
            }
        }

        try:
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            success = data.get("success", False)
            if success:
                self._log(f"  ✅ Navigation successful: clicked {selector}", None)
            else:
                self._log(f"  ❌ Navigation returned success=false: {data.get('message', 'no message')}", None)
            return success
        except Exception as e:
            self._log(f"  ❌ Navigation error: {e}", None)
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


__all__ = ["MasterOrchestrator"]
