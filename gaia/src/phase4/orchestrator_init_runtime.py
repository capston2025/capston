from __future__ import annotations

import os
from typing import Any, Dict, List

from gaia.src.phase4.llm_vision_client import get_vision_client
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.config import CONFIG


def initialize_runtime_state(
    orchestrator,
    *,
    tracker=None,
    mcp_config=None,
    llm_client=None,
    screenshot_callback=None,
    session_id: str = "default",
) -> None:
    orchestrator.tracker = tracker or ChecklistTracker()
    orchestrator.mcp_config = mcp_config or CONFIG.mcp
    orchestrator.llm_client = llm_client or get_vision_client()
    orchestrator._execution_logs = []
    orchestrator._screenshot_callback = screenshot_callback
    orchestrator.session_id = session_id

    orchestrator.page_element_map = {}
    orchestrator.home_url = ""

    orchestrator.selector_cache = {}
    orchestrator.cache_file = os.path.join(
        os.path.dirname(__file__), "../../artifacts/cache/selector_cache.json"
    )
    orchestrator._load_cache()

    orchestrator.embedding_cache = {}
    orchestrator.embedding_cache_file = os.path.join(
        os.path.dirname(__file__), "../../artifacts/cache/embedding_cache.json"
    )
    orchestrator._load_embedding_cache()

    orchestrator.enable_llm_validation = (
        os.getenv("GAIA_ENABLE_LLM_VALIDATION", "false").lower() == "true"
    )
    orchestrator.last_action_error = ""
    orchestrator.healed_selectors = {}
    orchestrator.healed_selector_cache = {}
    orchestrator._load_healed_selector_cache()
    orchestrator._selector_to_ref_id = {}
    orchestrator._active_snapshot_id = ""
