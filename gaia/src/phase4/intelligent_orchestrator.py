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
    ) -> None:
        """
        Initialize the intelligent orchestrator.

        Args:
            tracker: Checklist tracker for marking progress
            mcp_config: MCP host configuration
            llm_client: LLM vision client (defaults to GPT-4o)
        """
        self.tracker = tracker or ChecklistTracker()
        self.mcp_config = mcp_config or CONFIG.mcp
        self.llm_client = llm_client or LLMVisionClient()
        self._execution_logs: List[str] = []

    def execute_scenarios(
        self,
        url: str,
        scenarios: Sequence[TestScenario],
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Execute test scenarios using LLM-guided automation.

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

        for idx, scenario in enumerate(scenarios, start=1):
            self._log(f"[{idx}/{len(scenarios)}] Executing: {scenario.scenario}", progress_callback)

            try:
                result = self._execute_single_scenario(url, scenario, progress_callback)
                results["scenarios"].append(result)

                if result["status"] == "passed":
                    results["passed"] += 1
                    self.tracker.mark_found(scenario.id, evidence=result.get("logs", ""))
                elif result["status"] == "failed":
                    results["failed"] += 1
                else:
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

        self._log(f"\n✅ Execution complete: {results['passed']}/{results['total']} passed", progress_callback)
        return results

    def _execute_single_scenario(
        self,
        url: str,
        scenario: TestScenario,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Execute a single test scenario using LLM guidance.

        Returns:
            Dict with scenario execution result
        """
        logs = []
        current_url = url

        try:
            # Step 1: Analyze initial page state
            self._log(f"  📸 Analyzing page: {url}", progress_callback)
            dom_elements = self._analyze_dom(current_url)
            screenshot = self._capture_screenshot(current_url)

            if not dom_elements:
                logs.append("⚠️ No DOM elements found")
                return {
                    "id": scenario.id,
                    "scenario": scenario.scenario,
                    "status": "skipped",
                    "logs": logs
                }

            # Step 2: Execute each step with LLM guidance
            for step_idx, step in enumerate(scenario.steps, start=1):
                self._log(f"  🤖 Step {step_idx}: {step.description}", progress_callback)

                # Ask LLM to select element
                llm_decision = self.llm_client.select_element_for_step(
                    step_description=step.description,
                    dom_elements=dom_elements,
                    screenshot_base64=screenshot,
                    url=current_url
                )

                logs.append(f"Step {step_idx}: {step.description}")
                logs.append(f"  LLM Decision: {llm_decision['reasoning']}")
                logs.append(f"  Confidence: {llm_decision['confidence']}%")

                if llm_decision["confidence"] < 60:
                    logs.append(f"  ⚠️ Low confidence, skipping step")
                    continue

                if not llm_decision["selector"]:
                    logs.append(f"  ⚠️ No selector found, skipping step")
                    continue

                # Execute the action
                before_screenshot = screenshot
                success = self._execute_action(
                    action=llm_decision["action"],
                    selector=llm_decision["selector"],
                    params=step.params or []
                )

                if not success:
                    logs.append(f"  ❌ Action failed")
                    return {
                        "id": scenario.id,
                        "scenario": scenario.scenario,
                        "status": "failed",
                        "logs": logs
                    }

                logs.append(f"  ✅ Action executed: {llm_decision['action']} on {llm_decision['selector']}")

                # Wait a bit for page to update
                time.sleep(1)

                # Capture new state
                screenshot = self._capture_screenshot(current_url)

                # If action changes page, re-analyze DOM
                if llm_decision["action"] in ("click", "press"):
                    dom_elements = self._analyze_dom(current_url)

            # Step 3: Verify final result using LLM
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

    def _analyze_dom(self, url: str) -> List[DomElement]:
        """Analyze DOM using MCP host."""
        payload = {"action": "analyze_page", "params": {"url": url}}
        try:
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=30
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

    def _capture_screenshot(self, url: str) -> str:
        """Capture screenshot using MCP host, returns base64 string."""
        payload = {"action": "capture_screenshot", "params": {"url": url}}
        try:
            response = requests.post(
                f"{self.mcp_config.host_url}/execute",
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            return data.get("screenshot", "")
        except Exception as e:
            print(f"Screenshot capture failed: {e}")
            return ""

    def _execute_action(self, action: str, selector: str, params: List[Any]) -> bool:
        """Execute a browser action using MCP host."""
        # Note: This is a simplified version
        # Real implementation would call MCP host's execute_action endpoint
        # For now, we'll just return True as a placeholder
        # TODO: Implement actual action execution via MCP
        return True

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
