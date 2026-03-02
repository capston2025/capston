"""Shared utility helpers for IntelligentOrchestrator."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional


def normalize_text(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\s\u3131-\u318E\uAC00-\uD7A3]", " ", value)
    return " ".join(value.split())


def token_overlap(desc: str, elem: str) -> float:
    desc_tokens = set(desc.split())
    elem_tokens = set(elem.split())
    if not desc_tokens or not elem_tokens:
        return 0.0
    return len(desc_tokens & elem_tokens) / len(elem_tokens)


def local_embedding(text: str) -> List[float] | None:
    if not text or not text.strip():
        return [0.0] * 128

    try:
        import numpy as np
    except ImportError:
        return None

    normalized = normalize_text(text)
    tokens = normalized.split()
    if not tokens:
        return [0.0] * 128

    dim = 128
    vector = np.zeros(dim, dtype=float)

    for token in tokens:
        for i in range(4):
            digest = hashlib.sha256(f"{token}:{i}".encode("utf-8")).digest()
            index = int.from_bytes(digest[i : i + 4], "big") % dim
            vector[index] += 1.0

    norm = np.linalg.norm(vector)
    if norm > 0:
        vector /= norm

    return vector.tolist()


def build_cache_key(step_description: str, action: str, page_url: str, dom_context: str = "") -> str:
    normalized_url = page_url.rstrip("/")
    key_string = f"{step_description}|{action}|{normalized_url}|{dom_context}"
    return hashlib.md5(key_string.encode("utf-8")).hexdigest()


def load_json_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data
    return {}


def save_json_file(path: str, payload: Dict[str, Any], ensure_ascii: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=ensure_ascii)
