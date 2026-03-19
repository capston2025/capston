from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from playwright.async_api import Page

from gaia.src.phase4.mcp_snapshot_ref_utils import _element_is_interactive



def _snapshot_line_depth(line: str) -> int:
    indent = len(line) - len(line.lstrip(" "))
    return max(0, indent // 2)


def _compact_role_tree(snapshot: str) -> str:
    lines = snapshot.split("\n")
    out: List[str] = []
    for i, line in enumerate(lines):
        if "[ref=" in line:
            out.append(line)
            continue
        if ":" in line and not line.rstrip().endswith(":"):
            out.append(line)
            continue
        current_depth = _snapshot_line_depth(line)
        has_ref_child = False
        for j in range(i + 1, len(lines)):
            child_depth = _snapshot_line_depth(lines[j])
            if child_depth <= current_depth:
                break
            if "[ref=" in lines[j]:
                has_ref_child = True
                break
        if has_ref_child:
            out.append(line)
    return "\n".join(out)


def _limit_snapshot_text(snapshot: str, max_chars: int) -> tuple[str, bool]:
    limit = max(200, min(int(max_chars or 24000), 120000))
    if len(snapshot) <= limit:
        return snapshot, False
    return f"{snapshot[:limit]}\n\n[...TRUNCATED - page too large]", True


def _parse_ai_ref(suffix: str) -> Optional[str]:
    m = re.search(r"\[ref=(e\d+)\]", suffix or "", flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def _role_snapshot_stats(snapshot: str, refs: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    interactive = 0
    for item in refs.values():
        role = str((item or {}).get("role") or "").strip().lower()
        if role in _ROLE_INTERACTIVE:
            interactive += 1
    return {
        "lines": len(snapshot.split("\n")) if snapshot else 0,
        "chars": len(snapshot),
        "refs": len(refs),
        "interactive": interactive,
    }


def _build_role_snapshot_from_aria_text(
    aria_snapshot: str,
    *,
    interactive: bool,
    compact: bool,
    max_depth: Optional[int] = None,
    line_limit: int = 500,
    max_chars: int = 64000,
) -> Dict[str, Any]:
    lines = str(aria_snapshot or "").split("\n")
    refs: Dict[str, Dict[str, Any]] = {}
    refs_by_key: Dict[str, List[str]] = defaultdict(list)
    counts_by_key: Dict[str, int] = defaultdict(int)
    out: List[str] = []
    ref_counter = 0

    def _next_ref() -> str:
        nonlocal ref_counter
        ref_counter += 1
        return f"e{ref_counter}"

    for line in lines:
        depth = _snapshot_line_depth(line)
        if max_depth is not None and depth > max_depth:
            continue

        m = re.match(r'^(\s*-\s*)(\w+)(?:\s+"([^"]*)")?(.*)$', line)
        if not m:
            if not interactive:
                out.append(line)
            continue

        prefix, role_raw, name, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        if role_raw.startswith("/"):
            if not interactive:
                out.append(line)
            continue

        role = (role_raw or "").lower()
        if interactive and role not in _ROLE_INTERACTIVE:
            continue
        if compact and role in _ROLE_STRUCTURAL and not name:
            continue

        should_have_ref = role in _ROLE_INTERACTIVE or (role in _ROLE_CONTENT and bool(name))
        if not should_have_ref:
            out.append(line)
            continue

        ref = _next_ref()
        key = f"{role}:{name or ''}"
        nth = counts_by_key[key]
        counts_by_key[key] += 1
        refs_by_key[key].append(ref)

        ref_payload: Dict[str, Any] = {"role": role}
        if name:
            ref_payload["name"] = name
        if nth > 0:
            ref_payload["nth"] = nth
        refs[ref] = ref_payload

        enhanced = f"{prefix}{role_raw}"
        if name:
            enhanced += f' "{name}"'
        enhanced += f" [ref={ref}]"
        if nth > 0:
            enhanced += f" [nth={nth}]"
        if suffix:
            enhanced += suffix
        out.append(enhanced)

    duplicate_keys = {k for k, v in refs_by_key.items() if len(v) > 1}
    for ref, data in refs.items():
        key = f"{data.get('role', '')}:{data.get('name', '')}"
        if key not in duplicate_keys:
            data.pop("nth", None)

    snapshot = "\n".join(out) or "(empty)"
    if compact:
        snapshot = _compact_role_tree(snapshot)
    trimmed_lines = snapshot.split("\n")[: max(1, min(int(line_limit or 500), 5000))]
    snapshot = "\n".join(trimmed_lines)
    snapshot, truncated = _limit_snapshot_text(snapshot, max_chars=max_chars)
    return {
        "snapshot": snapshot,
        "refs": refs,
        "truncated": truncated,
        "stats": _role_snapshot_stats(snapshot, refs),
    }


def _build_role_snapshot_from_ai_text(
    ai_snapshot: str,
    *,
    interactive: bool,
    compact: bool,
    max_depth: Optional[int] = None,
    line_limit: int = 500,
    max_chars: int = 64000,
) -> Dict[str, Any]:
    lines = str(ai_snapshot or "").split("\n")
    refs: Dict[str, Dict[str, Any]] = {}
    out: List[str] = []

    for line in lines:
        depth = _snapshot_line_depth(line)
        if max_depth is not None and depth > max_depth:
            continue

        m = re.match(r'^(\s*-\s*)(\w+)(?:\s+"([^"]*)")?(.*)$', line)
        if not m:
            out.append(line)
            continue

        _, role_raw, name, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        if role_raw.startswith("/"):
            out.append(line)
            continue

        role = (role_raw or "").lower()
        if interactive and role not in _ROLE_INTERACTIVE:
            continue
        if compact and role in _ROLE_STRUCTURAL and not name:
            continue

        ref = _parse_ai_ref(suffix or "")
        if ref:
            refs[ref] = {"role": role, **({"name": name} if name else {})}
        out.append(line)

    snapshot = "\n".join(out) or "(empty)"
    if compact:
        snapshot = _compact_role_tree(snapshot)
    trimmed_lines = snapshot.split("\n")[: max(1, min(int(line_limit or 500), 5000))]
    snapshot = "\n".join(trimmed_lines)
    snapshot, truncated = _limit_snapshot_text(snapshot, max_chars=max_chars)
    return {
        "snapshot": snapshot,
        "refs": refs,
        "truncated": truncated,
        "stats": _role_snapshot_stats(snapshot, refs),
    }


def _build_role_refs_from_elements(elements: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    refs: Dict[str, Dict[str, Any]] = {}
    counts_by_key: Dict[str, int] = defaultdict(int)
    refs_by_key: Dict[str, List[str]] = defaultdict(list)

    for item in elements:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref_id") or "").strip()
        if not ref:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        role = str(attrs.get("role") or "").strip().lower()
        if not role:
            tag = str(item.get("tag") or "").strip().lower()
            if tag == "a":
                role = "link"
            elif tag in {"input", "textarea"}:
                role = "textbox"
            elif tag == "select":
                role = "combobox"
            elif tag == "button":
                role = "button"
            else:
                role = "generic"

        name = str(item.get("text") or attrs.get("aria-label") or "").strip() or None
        key = f"{role}:{name or ''}"
        nth = counts_by_key[key]
        counts_by_key[key] += 1
        refs_by_key[key].append(ref)

        payload: Dict[str, Any] = {"role": role}
        if name:
            payload["name"] = name
        if nth > 0:
            payload["nth"] = nth
        refs[ref] = payload

    duplicate_keys = {k for k, v in refs_by_key.items() if len(v) > 1}
    for ref, data in refs.items():
        key = f"{data.get('role', '')}:{data.get('name', '')}"
        if key not in duplicate_keys:
            data.pop("nth", None)
    return refs


def _build_context_snapshot_from_elements(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    container_entries: Dict[str, Dict[str, Any]] = {}
    child_refs_by_dom_ref: Dict[str, List[str]] = defaultdict(list)

    for item in elements:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        container_dom_ref = str(attrs.get("container_dom_ref") or "").strip()
        if not container_dom_ref:
            continue
        if container_dom_ref not in container_entries:
            container_entries[container_dom_ref] = {
                "role": str(attrs.get("container_role") or "").strip() or None,
                "name": str(attrs.get("container_name") or "").strip() or None,
                "parent_dom_ref": str(attrs.get("container_parent_dom_ref") or "").strip() or None,
                "context_text": str(attrs.get("context_text") or "").strip() or None,
                "interactive": False,
            }
        ref_id = str(item.get("ref_id") or "").strip()
        if ref_id:
            child_refs_by_dom_ref[container_dom_ref].append(ref_id)
            container_entries[container_dom_ref]["interactive"] = True

    container_ref_by_dom_ref: Dict[str, str] = {}
    nodes: List[Dict[str, Any]] = []
    for index, (dom_ref, meta) in enumerate(container_entries.items()):
        ref_id = f"ctx-{index}"
        container_ref_by_dom_ref[dom_ref] = ref_id
        nodes.append(
            {
                "ref_id": ref_id,
                "role": meta.get("role"),
                "name": meta.get("name"),
                "parent_ref_id": None,
                "child_ref_ids": child_refs_by_dom_ref.get(dom_ref, []),
                "interactive": bool(meta.get("interactive")),
                "context_text": meta.get("context_text"),
                "_parent_dom_ref": meta.get("parent_dom_ref"),
            }
        )

    for node in nodes:
        parent_dom_ref = str(node.pop("_parent_dom_ref") or "").strip()
        if parent_dom_ref:
            node["parent_ref_id"] = container_ref_by_dom_ref.get(parent_dom_ref)

    for item in elements:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        container_dom_ref = str(attrs.get("container_dom_ref") or "").strip()
        if container_dom_ref and container_dom_ref in container_ref_by_dom_ref:
            context_ref = container_ref_by_dom_ref[container_dom_ref]
            attrs["container_ref_id"] = context_ref
            item["container_ref_id"] = context_ref

    node_by_ref = {str(node.get("ref_id") or ""): node for node in nodes if str(node.get("ref_id") or "")}
    return {
        "nodes": nodes,
        "node_by_ref": node_by_ref,
        "container_ref_by_dom_ref": container_ref_by_dom_ref,
    }


async def _try_snapshot_for_ai(page: Page, timeout_ms: int = 5000) -> Optional[str]:
    timeout_ms = max(500, min(int(timeout_ms or 5000), 60000))

    # Playwright 내부 채널 snapshotForAI 시도 (OpenClaw parity)
    try:
        impl = getattr(page, "_impl_obj", None)
        channel = getattr(impl, "_channel", None)
        send = getattr(channel, "send", None)
        if callable(send):
            res = await send("snapshotForAI", {"timeout": timeout_ms, "track": "response"})
            if isinstance(res, dict):
                text = str(res.get("full") or "")
                if text.strip():
                    return text
    except Exception:
        pass

    # fallback: 접근성 스냅샷 문자열
    try:
        locator = page.locator(":root")
        aria_text = await locator.aria_snapshot(timeout=timeout_ms)
        if isinstance(aria_text, str) and aria_text.strip():
            return aria_text
    except Exception:
        pass
    return None


def _build_snapshot_text(
    elements: List[Dict[str, Any]],
    *,
    interactive_only: bool,
    compact: bool,
    limit: int,
    max_chars: int,
) -> Dict[str, Any]:
    lines: List[str] = []
    char_count = 0
    max_items = max(1, min(int(limit or 200), 5000))
    max_chars = max(200, min(int(max_chars or 24000), 120000))
    for idx, item in enumerate(elements):
        if not isinstance(item, dict):
            continue
        if interactive_only and not _element_is_interactive(item):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        tag = str(item.get("tag") or "").strip().lower() or "node"
        role = str(attrs.get("role") or "").strip().lower()
        ref = str(item.get("ref_id") or "").strip() or f"e{idx}"
        text = str(item.get("text") or "").strip()
        aria_label = str(attrs.get("aria-label") or "").strip()
        placeholder = str(attrs.get("placeholder") or "").strip()
        title = str(attrs.get("title") or "").strip()
        label = text or aria_label or placeholder or title
        label = re.sub(r"\s+", " ", label).strip()
        if len(label) > 140:
            label = label[:140]
        kind = role or tag
        if compact:
            if label:
                line = f"- {kind} \"{label}\" [ref={ref}]"
            else:
                line = f"- {kind} [ref={ref}]"
        else:
            line = f"- tag={tag} role={role or '-'} ref={ref}"
            if label:
                line += f" text=\"{label}\""
            if placeholder:
                line += f" placeholder=\"{placeholder[:80]}\""
        if char_count + len(line) + 1 > max_chars:
            break
        lines.append(line)
        char_count += len(line) + 1
        if len(lines) >= max_items:
            break
    return {
        "lines": lines,
        "text": "\n".join(lines),
        "stats": {
            "line_count": len(lines),
            "char_count": char_count,
            "interactive_only": bool(interactive_only),
            "compact": bool(compact),
            "limit": max_items,
            "max_chars": max_chars,
        },
    }
