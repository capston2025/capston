from __future__ import annotations

import hashlib
import json as json_module
from typing import Any, Dict, List


def build_snapshot_dom_hash(url: str, elements: List[Dict[str, Any]]) -> str:
    compact: List[Dict[str, Any]] = []
    for el in elements:
        attrs = el.get("attributes") or {}
        compact.append(
            {
                "tag": el.get("tag", ""),
                "text": (el.get("text") or "")[:80],
                "selector": el.get("selector", ""),
                "full_selector": el.get("full_selector", ""),
                "frame_index": el.get("frame_index", 0),
                "role": attrs.get("role", ""),
                "type": attrs.get("type", ""),
                "aria_label": attrs.get("aria-label", ""),
            }
        )

    raw = json_module.dumps(
        {
            "url": (url or "").strip(),
            "elements": compact,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def apply_selector_strategy(elements: List[Dict[str, Any]], strategy: str) -> None:
    select_index = 0
    tag_indices: Dict[str, int] = {}

    for element in elements:
        tag = element.get("tag") or ""
        text = (element.get("text") or "").strip()
        attrs = element.get("attributes") or {}

        if tag == "select":
            element["selector"] = f"select >> nth={select_index}"
            select_index += 1
            continue

        if strategy == "role":
            role = attrs.get("role")
            aria_label = attrs.get("aria-label") or ""
            placeholder = attrs.get("placeholder") or ""
            safe_text = text.replace('"', "'") if text else ""
            safe_label = aria_label.replace('"', "'") if aria_label else ""
            safe_placeholder = placeholder.replace('"', "'") if placeholder else ""

            if role and safe_text:
                element["selector"] = f'role={role}[name="{safe_text}"]'
                continue
            if safe_label:
                element["selector"] = f'[aria-label="{safe_label}"]'
                continue
            if safe_placeholder and tag in {"input", "textarea"}:
                element["selector"] = f'{tag}[placeholder="{safe_placeholder}"]'
                continue

        if strategy == "nth":
            index = tag_indices.get(tag, 0)
            element["selector"] = f"{tag} >> nth={index}"
            tag_indices[tag] = index + 1
            continue

        if strategy == "text" and ":has-text" in (element.get("selector") or ""):
            safe_text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()
            safe_text = safe_text.replace('"', "'") if safe_text else ""
            if safe_text:
                element["selector"] = f"text={safe_text}"
