"""
Scheduler Integration with GAIA Phases

Connects Adaptive Scheduler with Phase 3 (DSL) and Phase 4 (MCP execution).
"""
from __future__ import annotations

import requests
from typing import Any, Dict, List

from .adaptive_scheduler import AdaptiveScheduler, compute_dom_signature

try:
    from gaia.src.utils.config import CONFIG
    DEFAULT_MCP_URL = CONFIG.mcp.host_url
except (ImportError, AttributeError):
    DEFAULT_MCP_URL = "http://localhost:8001"


class SchedulerIntegration:
    """
    Integration layer between Adaptive Scheduler and GAIA execution phases.

    Workflow:
    External Agent → Priority Items → Scheduler → MCP Host → Results → Re-score
    """

    def __init__(
        self,
        scheduler: AdaptiveScheduler | None = None,
        mcp_host_url: str | None = None,
    ):
        """
        Initialize integration layer.

        Args:
            scheduler: Adaptive scheduler instance
            mcp_host_url: MCP host URL (defaults to config or localhost:8001)
        """
        self.scheduler = scheduler or AdaptiveScheduler()
        self.mcp_host_url = mcp_host_url or DEFAULT_MCP_URL

    def receive_from_agent(self, agent_output: Dict[str, Any]) -> None:
        """
        Receive test items from external agent service.

        Expected format:
        {
            "checklist": [
                {
                    "id": "TC001",
                    "name": "Login functionality",
                    "priority": "MUST",
                    "steps": [...],
                    ...
                }
            ]
        }

        Args:
            agent_output: Agent service output (from /api/analyze)
        """
        if not isinstance(agent_output, dict):
            return  # Silently ignore invalid input

        checklist = agent_output.get("checklist", [])
        if not isinstance(checklist, list):
            return  # Silently ignore invalid checklist

        # Convert agent format to scheduler format
        scheduler_items = []
        for item in checklist:
            scheduler_item = {
                "id": item.get("id", ""),
                "priority": item.get("priority", "MAY"),
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "steps": item.get("steps", []),
                "precondition": item.get("precondition", ""),
                "expected_result": item.get("expected_result", ""),
                # Initialize with default values
                "new_elements": 0,
                "target_url": None,
                "no_dom_change": False,
            }
            scheduler_items.append(scheduler_item)

        self.scheduler.ingest_items(scheduler_items)

    def execute_with_mcp(
        self,
        item: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute test item using MCP host.

        Args:
            item: Test item to execute

        Returns:
            Execution result with DOM signature
        """
        # Convert scheduler item to MCP test scenario format
        mcp_scenario = self._convert_to_mcp_scenario(item)

        try:
            # Call MCP host /execute endpoint
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "execute_scenario",
                    "params": {"scenario": mcp_scenario}
                },
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                status = result.get("status", "failed")

                # Analyze DOM if successful
                dom_signature = None
                new_elements_count = 0

                if status == "success":
                    # Optional: Analyze page after execution
                    dom_data = self._analyze_page_dom(mcp_scenario.get("target_url"))
                    if dom_data:
                        dom_signature = compute_dom_signature(dom_data)
                        new_elements_count = len(dom_data.get("elements", []))

                return {
                    "status": status,
                    "dom_signature": dom_signature,
                    "new_elements": new_elements_count,
                    "logs": result.get("logs", []),
                    "mcp_result": result,
                }

            else:
                return {
                    "status": "failed",
                    "error": f"MCP host returned {response.status_code}",
                }

        except Exception as e:
            return {
                "status": "failed",
                "error": str(e),
                "fatal": True,
            }

    def run_adaptive_execution(
        self,
        max_rounds: int = 20,
        completion_threshold: float = 0.9,
    ) -> Dict[str, Any]:
        """
        Run full adaptive execution loop.

        Args:
            max_rounds: Maximum execution rounds
            completion_threshold: Completion ratio to stop

        Returns:
            Final execution summary
        """
        return self.scheduler.execute_until_complete(
            executor=self.execute_with_mcp,
            max_rounds=max_rounds,
            completion_threshold=completion_threshold,
        )

    def _convert_to_mcp_scenario(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert scheduler item to MCP test scenario format.

        Args:
            item: Scheduler test item

        Returns:
            MCP-compatible scenario dict
        """
        # Extract steps from item
        steps = item.get("steps", [])

        # Convert to MCP TestStep format
        mcp_steps = []
        for step in steps:
            if isinstance(step, str):
                # Simple string step - try to parse action
                mcp_steps.append({
                    "description": step,
                    "action": "click",  # Default action
                    "selector": "",
                    "params": [],
                })
            elif isinstance(step, dict):
                # Already structured
                mcp_steps.append(step)

        # Create assertion
        expected_result = item.get("expected_result", "")
        assertion = {
            "description": expected_result,
            "selector": "body",  # Default
            "condition": "is_visible",
            "params": [],
        }

        return {
            "id": item.get("id", ""),
            "priority": item.get("priority", "MAY"),
            "scenario": item.get("name", ""),
            "steps": mcp_steps,
            "assertion": assertion,
        }

    def _analyze_page_dom(self, url: str | None) -> Dict[str, Any] | None:
        """
        Analyze page DOM using MCP host.

        Args:
            url: URL to analyze

        Returns:
            DOM data or None if failed
        """
        if not url:
            return None

        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "analyze_page",
                    "params": {"url": url}
                },
                timeout=20,
            )

            if response.status_code == 200:
                return response.json()

        except Exception:
            pass

        return None

    def get_scheduler_summary(self) -> Dict[str, Any]:
        """
        Get current scheduler summary.

        Returns:
            Scheduler state and statistics
        """
        return self.scheduler._generate_summary()


def create_scheduler_pipeline(
    agent_output: Dict[str, Any],
    mcp_host_url: str | None = None,
) -> Dict[str, Any]:
    """
    Convenience function to create and run full adaptive scheduling pipeline.

    Args:
        agent_output: Output from external agent service
        mcp_host_url: Optional MCP host URL

    Returns:
        Final execution summary

    Example:
        >>> agent_data = {"checklist": [...]}
        >>> summary = create_scheduler_pipeline(agent_data)
        >>> print(summary["execution_stats"])
    """
    integration = SchedulerIntegration(mcp_host_url=mcp_host_url)

    # Step 1: Receive items from agent
    integration.receive_from_agent(agent_output)

    # Step 2: Run adaptive execution
    summary = integration.run_adaptive_execution()

    return summary
