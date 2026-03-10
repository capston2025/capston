from __future__ import annotations

import re
from typing import Any, Dict, Optional, Sequence


def is_dynamic_selector(selector: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pattern, selector) for pattern in patterns)


def create_stable_selector(elem, patterns: Sequence[str]) -> Optional[str]:
    is_dynamic = is_dynamic_selector(elem.selector, patterns)

    if is_dynamic:
        print(f"[Stable Selector] ⚠️ Dynamic ID detected: {elem.selector}")

    if elem.text and elem.text.strip():
        attrs = elem.attributes or {}

        if elem.tag == "button" and attrs.get("type"):
            button_type = attrs.get("type")
            stable_selector = f'{elem.tag}[type="{button_type}"]:has-text("{elem.text}")'
            if is_dynamic:
                print(f"[Stable Selector] ✅ Using specific button selector: {stable_selector}")
            return stable_selector
        if attrs.get("role") and attrs.get("role") != "button":
            role = attrs.get("role")
            stable_selector = f'{elem.tag}[role="{role}"]:has-text("{elem.text}")'
            if is_dynamic:
                print(f"[Stable Selector] ✅ Using role-based selector: {stable_selector}")
            return stable_selector

        stable_selector = f'{elem.tag}:has-text("{elem.text}")'
        if is_dynamic:
            print(f"[Stable Selector] ✅ Using text selector: {stable_selector}")
        return stable_selector

    aria_label = elem.attributes.get("aria-label", "")
    if aria_label:
        stable_selector = f'[aria-label="{aria_label}"]'
        if is_dynamic:
            print(f"[Stable Selector] ✅ Using ARIA label: {stable_selector}")
        return stable_selector

    test_id = elem.attributes.get("data-testid", "")
    if test_id:
        stable_selector = f'[data-testid="{test_id}"]'
        if is_dynamic:
            print(f"[Stable Selector] ✅ Using data-testid: {stable_selector}")
        return stable_selector

    if elem.selector.startswith("[id=") and not is_dynamic:
        return elem.selector

    if is_dynamic:
        print("[Stable Selector] ⚠️ No stable alternative found, will use vision fallback")
    return None


def validate_cached_selector(
    cached_data: Dict[str, Any],
    current_url: str,
    patterns: Sequence[str],
) -> Optional[str]:
    _ = current_url
    cached_selector = cached_data.get("selector", "")
    cached_text = cached_data.get("element_text", "")
    cached_tag = cached_data.get("element_tag", "")

    if is_dynamic_selector(cached_selector, patterns):
        print(f"[Cache Validation] ⚠️ Dynamic ID detected in cache: {cached_selector}")

        if cached_text:
            attrs = cached_data.get("attributes", {})

            if cached_tag == "button" and attrs.get("type"):
                button_type = attrs.get("type")
                new_selector = f'{cached_tag}[type="{button_type}"]:has-text("{cached_text}")'
                print(f"[Cache Validation] ✅ Regenerated specific button selector: {new_selector}")
                cached_data["selector"] = new_selector
                return new_selector
            if attrs.get("role") and attrs.get("role") != "button":
                role = attrs.get("role")
                new_selector = f'{cached_tag}[role="{role}"]:has-text("{cached_text}")'
                print(f"[Cache Validation] ✅ Regenerated role-based selector: {new_selector}")
                cached_data["selector"] = new_selector
                return new_selector

            new_selector = f'{cached_tag}:has-text("{cached_text}")'
            print(f"[Cache Validation] ✅ Regenerated text selector: {new_selector}")
            cached_data["selector"] = new_selector
            return new_selector

        print("[Cache Validation] ❌ Cache invalidated (no text metadata)")
        return None

    print(f"[Cache Validation] ✅ Using cached selector: {cached_selector}")
    return cached_selector
