from __future__ import annotations

import re
from typing import Any, Dict, Optional


def extract_pointer_interceptor(exc_or_message: Any) -> Optional[Dict[str, str]]:
    message = str(exc_or_message or "")
    lower = message.lower()
    if (
        "intercepts pointer events" not in lower
        and "not receive pointer events" not in lower
    ):
        return None
    result: Dict[str, str] = {"description": "unknown pointer interceptor"}
    match = re.search(
        r"<(?P<tag>[A-Za-z][\w:-]*)(?P<attrs>[^>]*)>[^<\n]*</?[A-Za-z0-9:-]*>?\s*intercepts pointer events",
        message,
    )
    if not match:
        match = re.search(
            r"<(?P<tag>[A-Za-z][\w:-]*)(?P<attrs>[^>]*)>.*?intercepts pointer events",
            message,
            flags=re.DOTALL,
        )
    if match:
        attrs = match.group("attrs") or ""
        tag = match.group("tag") or ""
        result["tag"] = tag.lower()
        id_match = re.search(r"""\bid=(["'])(?P<value>.*?)\1""", attrs)
        class_match = re.search(r"""\bclass=(["'])(?P<value>.*?)\1""", attrs)
        if id_match:
            result["id"] = id_match.group("value")
        if class_match:
            result["class"] = class_match.group("value")
        selector = result.get("tag", "")
        if result.get("id"):
            selector += f"#{result['id']}"
        if result.get("class"):
            selector += "." + ".".join(result["class"].split())
        result["description"] = selector or result["description"]
    return result
