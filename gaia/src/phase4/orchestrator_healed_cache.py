"""Healed selector cache helpers for IntelligentOrchestrator."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

from gaia.src.phase4.orchestrator_utils import load_json_file, save_json_file


def healed_cache_file_path(cache_file: str) -> str:
    return os.path.join(os.path.dirname(cache_file), "healed_selector_cache.json")


def load_healed_selector_cache(cache_file: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    path = healed_cache_file_path(cache_file)
    data = load_json_file(path)
    if not isinstance(data, dict):
        return {}
    return data  # type: ignore[return-value]


def save_healed_selectors(
    cache_file: str,
    scenario_id: str,
    healed: Dict[str, str],
) -> Tuple[int, Dict[str, Dict[str, Dict[str, Any]]]]:
    if not healed:
        return 0, {}

    path = healed_cache_file_path(cache_file)
    healed_cache = load_healed_selector_cache(cache_file)
    if scenario_id not in healed_cache:
        healed_cache[scenario_id] = {}

    for original, healed_selector in healed.items():
        prev = healed_cache.get(scenario_id, {}).get(original, {})
        healed_cache[scenario_id][original] = {
            "healed_selector": healed_selector,
            "timestamp": time.time(),
            "success_count": int(prev.get("success_count", 0)) + 1,
        }

    save_json_file(path, healed_cache, ensure_ascii=False)
    return len(healed), healed_cache


def get_healed_selector(
    healed_selector_cache: Dict[str, Dict[str, Dict[str, Any]]],
    scenario_id: str,
    original_selector: str,
) -> Optional[str]:
    scenario_cache = healed_selector_cache.get(scenario_id, {})
    if original_selector not in scenario_cache:
        return None
    cached = scenario_cache.get(original_selector, {})
    value = cached.get("healed_selector")
    if isinstance(value, str) and value:
        return value
    return None
