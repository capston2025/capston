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
        session_id: str = "default",
    ) -> None:
        """
        Initialize the master orchestrator.

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
        self._screenshot_callback = screenshot_callback
        self.session_id = session_id
        self.intelligent_orch = IntelligentOrchestrator(
            tracker=self.tracker,
            mcp_config=self.mcp_config,
            llm_client=self.llm_client,
            screenshot_callback=screenshot_callback,
            session_id=session_id
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
            page_hash = page_info.get("hash")

            self._log(f"\n📄 Page {page_idx}/{len(site_map)}: {page_name}", progress_callback)

            # Navigate to page if needed (skip for home page)
            if page_hash:
                self._log(f"  🔗 Navigating to: {page_url}", progress_callback)
                success = self._navigate_to_page_url(page_url)
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

            # Aggregate results and mark tests as executed
            for scenario_result in page_results["scenarios"]:
                scenario_id = scenario_result["id"]
                status = scenario_result["status"]
                verification = scenario_result.get("verification")

                # Log scenario result with verification details
                self._log(f"  🔍 Processing {scenario_id} with status={status}", progress_callback)

                # Log IntelligentOrchestrator's Vision AI verification results
                if verification:
                    verified = verification.get("success", False)
                    confidence = verification.get("confidence", 0)
                    matched_indicators = verification.get("matched_indicators", [])
                    reasoning = verification.get("reasoning", "")

                    self._log(f"     🔬 Step-level Vision AI Verification:", progress_callback)
                    self._log(f"        - Verified: {'✅ YES' if verified else '❌ NO'}", progress_callback)
                    self._log(f"        - Confidence: {confidence}%", progress_callback)
                    if matched_indicators:
                        self._log(f"        - Matched Indicators: {', '.join(matched_indicators)}", progress_callback)
                    self._log(f"        - Reasoning: {reasoning[:100]}...", progress_callback)

                # MASTER ORCHESTRATOR-LEVEL VERIFICATION
                # Perform additional verification: Does the final screenshot match the scenario description?
                if status not in ("skipped",):  # Only verify scenarios that were actually executed
                    # Find the original scenario object to get the description
                    scenario_obj = next((s for s in remaining_scenarios if s.id == scenario_id), None)

                    if scenario_obj:
                        self._log(f"     🎯 Master Orchestrator final verification...", progress_callback)

                        # Use screenshot captured by IntelligentOrchestrator (includes toast messages!)
                        # This ensures we capture the screenshot immediately after scenario completion,
                        # before temporary UI elements (toasts) disappear
                        final_screenshot = scenario_result.get("after_screenshot", "")
                        current_url = scenario_result.get("current_url", page_url)

                        # Only verify if screenshot is available
                        try:
                            if final_screenshot:
                                # Verify with LLM: Does this screenshot match the scenario description?
                                master_verification = self.llm_client.verify_scenario_outcome(
                                    scenario_description=scenario_obj.scenario,
                                    final_screenshot=final_screenshot,
                                    url=current_url
                                )

                                matches = master_verification.get("matches", False)
                                master_confidence = master_verification.get("confidence", 0)
                                master_reasoning = master_verification.get("reasoning", "")
                                observations = master_verification.get("observations", [])

                                self._log(f"     🎯 Master Verification Result:", progress_callback)
                                self._log(f"        - Matches Scenario: {'✅ YES' if matches else '❌ NO'}", progress_callback)
                                self._log(f"        - Confidence: {master_confidence}%", progress_callback)
                                self._log(f"        - Observations: {', '.join(observations[:3])}", progress_callback)
                                self._log(f"        - Reasoning: {master_reasoning[:100]}...", progress_callback)

                                # Store master verification in result
                                scenario_result["master_verification"] = master_verification

                                # Adjust status based on Master Orchestrator verification
                                # If Master says it doesn't match, downgrade success to partial
                                if status == "success" and not matches and master_confidence >= 60:
                                    self._log(f"     ⚠️ Master verification disagrees with step-level verification", progress_callback)
                                    self._log(f"     ⚠️ Downgrading status from 'success' to 'partial'", progress_callback)
                                    status = "partial"
                                    scenario_result["status"] = "partial"
                                    scenario_result["status_adjusted_by_master"] = True
                                # If Master says it matches but step-level failed, log but keep failed
                                elif status in ("failed", "partial") and matches and master_confidence >= 80:
                                    self._log(f"     💡 Master verification suggests success despite step issues", progress_callback)
                                    self._log(f"     💡 Keeping status as '{status}' (step-level issues detected)", progress_callback)

                        except Exception as e:
                            self._log(f"     ⚠️ Master verification failed: {e}", progress_callback)

                # Only count each scenario once (skip if already executed on another page)
                if scenario_id not in self._executed_test_ids:
                    aggregated_results["scenarios"].append(scenario_result)

                    if status in ("passed", "success"):  # IntelligentOrchestrator returns "success"
                        aggregated_results["passed"] += 1
                        # Mark passed tests as executed
                        self._executed_test_ids.add(scenario_id)
                        self._log(f"     ✅ Scenario passed, moving to next scenario", progress_callback)
                    elif status in ("failed", "partial"):  # Also handle "partial" status
                        aggregated_results["failed"] += 1
                        # Mark failed tests as executed
                        self._executed_test_ids.add(scenario_id)
                        self._log(f"     ❌ Scenario {'failed' if status == 'failed' else 'partially completed'}, moving to next scenario", progress_callback)
                    elif status == "skipped":
                        # Don't mark skipped tests as executed
                        # They might be executable on another page
                        self._log(f"     ⏭️ Scenario skipped on this page, will retry on other pages", progress_callback)
                        pass
                else:
                    self._log(f"  🔁 Scenario already executed on another page, skipping", progress_callback)

            # IntelligentOrchestrator returns "success"/"partial"/"failed"/"skipped", not "passed"
            success_count = page_results.get('success', 0)
            partial_count = page_results.get('partial', 0)
            failed_count = page_results.get('failed', 0)
            skipped_count = page_results.get('skipped', 0)
            self._log(f"  📊 Page {page_idx} results: ✅{success_count} success, ⚠️{partial_count} partial, ❌{failed_count} failed, ⏭️{skipped_count} skipped", progress_callback)
            self._log(f"  🗂️ DEBUG: Currently executed test IDs: {self._executed_test_ids}", progress_callback)
            self._log(f"  📝 DEBUG: Remaining tests: {len(scenarios) - len(self._executed_test_ids)}", progress_callback)

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

            dom_payload = {"action": "analyze_page", "params": {"url": url, "session_id": self.session_id}}
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=dom_payload,
                timeout=90  # Increased from 30s to 90s for complex operations
            )
            response.raise_for_status()
            dom_data = response.json()
            dom_elements = dom_data.get("elements", [])

            screenshot_payload = {"action": "capture_screenshot", "params": {"url": url, "session_id": self.session_id}}
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=screenshot_payload,
                timeout=90  # Increased from 30s to 90s for complex operations
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
                for elem in dom_elements[:100]  # Limit to 100 for better coverage (increased from 50)
            ])

            prompt = f"""You are a website navigation agent. Analyze this page to find navigation links/buttons to other pages.

**Current Page:** {url}

**DOM Elements:**
{dom_summary}

**Your Task:**
1. Look at the screenshot to identify main navigation elements
2. Infer which page each link/button leads to (especially hash-based routes like #basics, #forms, #interactions)
3. Find category buttons, menu links, tabs, etc.

**CRITICAL INSTRUCTIONS:**
- You MUST respond with ONLY valid JSON array
- Do NOT include any explanatory text outside the JSON
- Do NOT use markdown code blocks (no ```)
- First item is always current page (hash: null)
- For hash-based SPA routes, include the hash fragment (e.g., "basics", "forms", "interactions")
- Only include important pages (login, search, cart, main features)

**Response Format (JSON ONLY):**
[
  {{"name": "Home", "hash": null, "description": "Main homepage"}},
  {{"name": "Basic Features", "hash": "basics", "description": "Basic website features page"}},
  {{"name": "Forms & Feedback", "hash": "forms", "description": "Form inputs and feedback page"}},
  {{"name": "Interactions & Data", "hash": "interactions", "description": "Interactive components and data visualization"}}
]

**Response (JSON array only, no other text):**"""

            response_text = self.llm_client.analyze_with_vision(
                prompt=prompt,
                screenshot_base64=screenshot
            )

            # Parse LLM response
            pages = json.loads(response_text)

            # Build full URL with hash for each page
            result = []
            base_url = url.split('#')[0]  # Remove any existing hash
            for page in pages:
                page_hash = page.get("hash")
                if page_hash:
                    page_url = f"{base_url}#{page_hash}"
                else:
                    page_url = base_url

                result.append({
                    "name": page["name"],
                    "url": page_url,
                    "hash": page_hash  # Keep hash for reference
                })

            return result

        except Exception as e:
            self._log(f"⚠️ Site exploration failed: {e}", progress_callback)
            return []

    def _navigate_to_page_url(self, url: str) -> bool:
        """
        Navigate to a page by going directly to its URL.

        Args:
            url: Full URL to navigate to (including hash)

        Returns:
            True if navigation succeeded, False otherwise
        """
        import requests

        payload = {
            "action": "execute_action",
            "params": {
                "url": url,  # Current page URL (ignored for goto)
                "selector": "",
                "action": "goto",
                "value": url,  # Target URL for goto action
                "session_id": self.session_id
            }
        }

        try:
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=90  # Increased from 30s to 90s for complex operations
            )
            response.raise_for_status()
            data = response.json()
            success = data.get("success", False)
            if success:
                self._log(f"  ✅ Navigation successful: loaded {url}", None)
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
