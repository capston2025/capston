from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional

from gaia.src.phase4.orchestrator_healed_cache import (
    get_healed_selector as get_healed_selector_from_cache,
    load_healed_selector_cache as load_healed_selector_cache_from_disk,
    save_healed_selectors as save_healed_selectors_to_disk,
)
from gaia.src.phase4.orchestrator_utils import (
    build_cache_key,
    load_json_file,
    save_json_file,
)


def load_cache(orchestrator: Any) -> None:
    try:
        orchestrator.selector_cache = load_json_file(orchestrator.cache_file)
        if orchestrator.selector_cache:
            current_time = time.time()
            orchestrator.selector_cache = {
                k: v for k, v in orchestrator.selector_cache.items()
                if current_time - v.get("timestamp", 0) < 7 * 24 * 3600
            }
            print(f"[Cache] Loaded {len(orchestrator.selector_cache)} cached selectors")
    except Exception as e:
        print(f"[Cache] Failed to load cache: {e}")
        orchestrator.selector_cache = {}


def save_cache(orchestrator: Any) -> None:
    try:
        save_json_file(orchestrator.cache_file, orchestrator.selector_cache, ensure_ascii=False)
    except Exception as e:
        print(f"[Cache] Failed to save cache: {e}")


def save_healed_selectors(orchestrator: Any, scenario_id: str, progress_callback=None) -> None:
    if scenario_id not in orchestrator.healed_selectors:
        return
    healed = orchestrator.healed_selectors[scenario_id]
    if not healed:
        return
    try:
        saved_count, healed_cache = save_healed_selectors_to_disk(
            orchestrator.cache_file,
            scenario_id,
            healed,
        )
        if healed_cache:
            orchestrator.healed_selector_cache = healed_cache
        orchestrator._log(f"  💾 Saved {saved_count} healed selector(s) to cache", progress_callback)
        print(f"[Healed Cache] Saved {saved_count} healed selectors for {scenario_id}")
    except Exception as e:
        print(f"[Healed Cache] Failed to save: {e}")


def load_healed_selector_cache(orchestrator: Any) -> None:
    try:
        orchestrator.healed_selector_cache = load_healed_selector_cache_from_disk(orchestrator.cache_file)
        if orchestrator.healed_selector_cache:
            total_selectors = sum(len(v) for v in orchestrator.healed_selector_cache.values())
            print(f"[Healed Cache] Loaded {total_selectors} healed selectors for {len(orchestrator.healed_selector_cache)} scenarios")
    except Exception as e:
        print(f"[Healed Cache] Failed to load: {e}")
        orchestrator.healed_selector_cache = {}


def get_healed_selector(orchestrator: Any, scenario_id: str, original_selector: str) -> str | None:
    return get_healed_selector_from_cache(
        orchestrator.healed_selector_cache,
        scenario_id,
        original_selector,
    )


def get_cache_key(step_description: str, action: str, page_url: str, dom_context: str = "") -> str:
    return build_cache_key(step_description, action, page_url, dom_context)


def detect_dom_context(dom_elements: List[Any]) -> str:
    context_parts = []
    for elem in dom_elements:
        elem_role = elem.attributes.get("role", "")
        if elem_role == "tab":
            attrs_str = str(elem.attributes)
            if (("aria-selected" in elem.attributes and elem.attributes.get("aria-selected") == "true") or
                "data-state='active'" in attrs_str or
                "data-state='checked'" in attrs_str):
                tab_text = elem.text[:20] if elem.text else ""
                if tab_text:
                    context_parts.append(f"tab:{tab_text}")
                    break
    for elem in dom_elements:
        elem_role = elem.attributes.get("role", "")
        if elem_role in ["dialog", "alertdialog"]:
            attrs_str = str(elem.attributes)
            if "data-state='open'" in attrs_str or elem.attributes.get("data-state") == "open":
                modal_text = elem.text[:20] if elem.text else "modal"
                context_parts.append(f"modal:{modal_text}")
                break
    return "|".join(context_parts)


def get_cached_selector(orchestrator: Any, step_description: str, action: str, page_url: str, dom_context: str = "") -> str | None:
    cache_key = get_cache_key(step_description, action, page_url, dom_context)
    cached = orchestrator.selector_cache.get(cache_key)
    if cached:
        validated_selector = orchestrator._validate_cached_selector(cached, page_url)
        if validated_selector:
            if validated_selector != cached.get('selector'):
                cached['selector'] = validated_selector
                print(f"[Cache] Updated with regenerated selector: {validated_selector}")
            if cached.get("success_count", 0) >= 2:
                context_info = f" [context: {dom_context}]" if dom_context else ""
                print(f"[Cache HIT] Using validated selector for '{step_description}'{context_info}")
                return cached['selector']
        else:
            print(f"[Cache MISS] Cached selector invalid, removing: {cache_key}")
            del orchestrator.selector_cache[cache_key]
    return None


def update_cache(orchestrator: Any, step_description: str, action: str, page_url: str,
                 selector: str, success: bool, dom_context: str = "",
                 element_text: str = "", element_tag: str = "",
                 attributes: dict = None) -> None:
    if orchestrator._is_dynamic_selector(selector):
        print(f"[Cache] ⚠️ Dynamic ID detected, caching with metadata for regeneration: {selector}")
    cache_key = get_cache_key(step_description, action, page_url, dom_context)
    if cache_key not in orchestrator.selector_cache:
        orchestrator.selector_cache[cache_key] = {
            "selector": selector,
            "timestamp": time.time(),
            "success_count": 1 if success else 0,
            "step_description": step_description,
            "element_text": element_text,
            "element_tag": element_tag,
            "attributes": attributes or {},
        }
    else:
        if success:
            orchestrator.selector_cache[cache_key]["success_count"] += 1
        orchestrator.selector_cache[cache_key]["timestamp"] = time.time()
    if len(orchestrator.selector_cache) % 5 == 0:
        save_cache(orchestrator)


def get_embedding(orchestrator: Any, text: str) -> List[float] | None:
    cache_key = hashlib.md5(text.encode('utf-8')).hexdigest()
    if cache_key in orchestrator.embedding_cache:
        return orchestrator.embedding_cache[cache_key]
    try:
        response = orchestrator.llm_client.client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        embedding = response.data[0].embedding
        orchestrator.embedding_cache[cache_key] = embedding
        if len(orchestrator.embedding_cache) % 20 == 0:
            save_embedding_cache(orchestrator)
        return embedding
    except Exception as e:
        print(f"[Embedding] Error getting embedding for '{text[:50]}': {e}")
        local_embedding = orchestrator._local_embedding(text)
        if local_embedding is not None:
            print("[Embedding] Using local deterministic embedding fallback")
            orchestrator.embedding_cache[cache_key] = local_embedding
            if len(orchestrator.embedding_cache) % 20 == 0:
                save_embedding_cache(orchestrator)
            return local_embedding
        return None


def load_embedding_cache(orchestrator: Any) -> None:
    try:
        orchestrator.embedding_cache = load_json_file(orchestrator.embedding_cache_file)
        if orchestrator.embedding_cache:
            print(f"[Embedding Cache] Loaded {len(orchestrator.embedding_cache)} cached embeddings")
    except Exception as e:
        print(f"[Embedding Cache] Failed to load cache: {e}")
        orchestrator.embedding_cache = {}


def save_embedding_cache(orchestrator: Any) -> None:
    try:
        save_json_file(orchestrator.embedding_cache_file, orchestrator.embedding_cache, ensure_ascii=False)
    except Exception as e:
        print(f"[Embedding Cache] Failed to save cache: {e}")
