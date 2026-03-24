from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import DOMElement


def truncate_for_prompt(text: str, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if len(normalized) > limit:
        return normalized[: limit - 1] + "…"
    return normalized


def fields_for_element(agent: Any, el: DOMElement) -> List[str]:
    selector = agent._element_full_selectors.get(el.id) or agent._element_selectors.get(el.id) or ""
    backend_name = str(getattr(agent, "_browser_backend_name", "") or "").strip().lower()
    group_action_blob = ""
    if backend_name != "openclaw":
        group_action_blob = " ".join(str(v) for v in (getattr(el, "group_action_labels", None) or []) if v)
    return [
        str(el.text or ""),
        str(el.aria_label or ""),
        str(el.placeholder or ""),
        str(getattr(el, "title", None) or ""),
        str(el.href or ""),
        selector,
        str(el.role or ""),
        str(el.tag or ""),
        str(el.type or ""),
        str(getattr(el, "container_name", None) or ""),
        str(getattr(el, "container_role", None) or ""),
        str(getattr(el, "container_source", None) or ""),
        str(getattr(el, "context_text", None) or ""),
        group_action_blob,
        str(getattr(el, "role_ref_role", None) or ""),
        str(getattr(el, "role_ref_name", None) or ""),
    ]


def context_match_tokens(agent: Any, el: DOMElement) -> List[str]:
    goal_tokens = set(getattr(agent, "_goal_tokens", set()) or set())
    if not goal_tokens:
        return []
    matched: set[str] = set()
    for source in (
        getattr(el, "text", None),
        getattr(el, "container_name", None),
        getattr(el, "context_text", None),
    ):
        if not source:
            continue
        matched.update(goal_tokens.intersection(agent._tokenize_text(str(source))))
    return sorted(matched)


def role_ref_alignment_score(agent: Any, el: DOMElement) -> float:
    goal_tokens = set(getattr(agent, "_goal_tokens", set()) or set())
    if not goal_tokens:
        return 0.0
    backend_name = str(getattr(agent, "_browser_backend_name", "") or "").strip().lower()
    role_name = str(getattr(el, "role_ref_name", None) or "")
    role_role = str(getattr(el, "role_ref_role", None) or "")
    score = 0.0
    score += 1.5 * len(goal_tokens.intersection(set(agent._tokenize_text(role_name))))
    if role_role.lower() in {"row", "listitem", "gridcell", "cell", "article"}:
        score += 1.25
    elif backend_name != "openclaw" and role_role.lower() in {"button", "link", "tab", "menuitem", "option"}:
        score += 0.75
    quoted_matches = re.findall(r'"([^"]+)"', str(getattr(agent, "_active_goal_text", "") or ""))
    for phrase in quoted_matches:
        normalized_phrase = agent._normalize_text(phrase)
        if normalized_phrase and normalized_phrase in agent._normalize_text(role_name):
            score += 3.0
    return float(score)


def context_score(agent: Any, el: DOMElement) -> float:
    goal_tokens = set(getattr(agent, "_goal_tokens", set()) or set())
    if not goal_tokens:
        return 0.0
    backend_name = str(getattr(agent, "_browser_backend_name", "") or "").strip().lower()
    text_tokens = set(agent._tokenize_text(getattr(el, "text", "") or ""))
    container_tokens = set(agent._tokenize_text(getattr(el, "container_name", "") or ""))
    context_tokens = set(agent._tokenize_text(getattr(el, "context_text", "") or ""))
    score = 0.0
    if backend_name == "openclaw":
        score += 1.5 * len(goal_tokens.intersection(text_tokens))
        score += 3.0 * len(goal_tokens.intersection(container_tokens))
        score += 1.5 * len(goal_tokens.intersection(context_tokens))
    else:
        score += 1.25 * len(goal_tokens.intersection(text_tokens))
        score += 2.0 * len(goal_tokens.intersection(container_tokens))
        score += 0.75 * len(goal_tokens.intersection(context_tokens))
    score += role_ref_alignment_score(agent, el)
    quoted_matches = re.findall(r'"([^"]+)"', str(getattr(agent, "_active_goal_text", "") or ""))
    for phrase in quoted_matches:
        normalized_phrase = agent._normalize_text(phrase)
        if normalized_phrase and normalized_phrase in agent._normalize_text(getattr(el, "container_name", "") or ""):
            score += 4.0
        elif normalized_phrase and normalized_phrase in agent._normalize_text(getattr(el, "context_text", "") or ""):
            score += 3.5 if backend_name == "openclaw" else 2.5
    if backend_name != "openclaw":
        action_labels = [agent._normalize_text(v) for v in (getattr(el, "group_action_labels", None) or []) if v]
        duplicate_label = agent._normalize_text(getattr(el, "text", "") or "")
        if duplicate_label and action_labels.count(duplicate_label) > 1 and goal_tokens.intersection(container_tokens):
            score += 1.5
    return float(score)


def pick_scoped_container(
    agent: Any,
    elements: List[DOMElement],
) -> Tuple[Optional[str], Optional[str], Optional[str], float, bool]:
    goal_tokens = set(getattr(agent, "_goal_tokens", set()) or set())
    quoted_matches = re.findall(r'"([^"]+)"', str(getattr(agent, "_active_goal_text", "") or ""))
    normalized_phrases = [agent._normalize_text(v) for v in quoted_matches if agent._normalize_text(v)]
    grouped: Dict[str, Dict[str, Any]] = {}
    for el in elements:
        container_ref_id = getattr(el, "container_ref_id", None)
        container_name = getattr(el, "container_name", None)
        if not container_ref_id or not container_name:
            continue
        bucket = grouped.setdefault(
            str(container_ref_id),
            {
                "name": str(container_name),
                "source": str(getattr(el, "container_source", None) or ""),
                "elements": [],
            },
        )
        bucket["elements"].append(el)

    if not grouped:
        return None, None, None, 0.0, False

    role_groups_by_container_ref = {}
    context_snapshot = getattr(agent, "_last_context_snapshot", None)
    if isinstance(context_snapshot, dict):
        raw_groups = context_snapshot.get("role_groups_by_container_ref")
        if isinstance(raw_groups, dict):
            role_groups_by_container_ref = raw_groups

    ranked: List[Tuple[float, str, str, str]] = []
    for ref_id, bucket in grouped.items():
        group_name = str(bucket["name"] or "")
        group_source = str(bucket["source"] or "")
        group_elements = list(bucket["elements"] or [])
        container_tokens = set(agent._tokenize_text(group_name))
        context_blob = " ".join(
            str(getattr(el, "context_text", None) or "") for el in group_elements if getattr(el, "context_text", None)
        )
        context_tokens = set(agent._tokenize_text(context_blob))
        score = 0.0
        score += 2.5 * len(goal_tokens.intersection(container_tokens))
        score += 1.0 * len(goal_tokens.intersection(context_tokens))
        if group_source == "semantic-first":
            score += 2.5
        for phrase in normalized_phrases:
            if phrase and phrase in agent._normalize_text(group_name):
                score += 5.0
            elif phrase and phrase in agent._normalize_text(context_blob):
                score += 3.0
        score += min(1.5, 0.25 * len(group_elements))
        role_groups = role_groups_by_container_ref.get(ref_id)
        if isinstance(role_groups, list) and role_groups:
            score += min(1.2, 0.2 * len(role_groups))
            role_group_blob = " ".join(
                " ".join(
                    str(v)
                    for v in (
                        group.get("role"),
                        group.get("name"),
                        " ".join(str(label) for label in (group.get("labels") or []) if label),
                    )
                    if v
                )
                for group in role_groups
                if isinstance(group, dict)
            )
            role_group_tokens = set(agent._tokenize_text(role_group_blob))
            score += 0.8 * len(goal_tokens.intersection(role_group_tokens))
            for phrase in normalized_phrases:
                if phrase and phrase in agent._normalize_text(role_group_blob):
                    score += 2.0
        ranked.append((score, ref_id, group_name, group_source))

    ranked.sort(reverse=True)
    best_score, best_ref, best_name, best_source = ranked[0]
    ambiguous = False
    if len(ranked) > 1:
        second_score = ranked[1][0]
        ambiguous = abs(best_score - second_score) < 1.5
    if best_score < 6.0:
        return None, None, None, best_score, ambiguous
    return best_ref, best_name, best_source, float(best_score), ambiguous


def format_dom_for_llm(agent: Any, elements: List[DOMElement]) -> str:
    phase = (agent._runtime_phase or "COLLECT").upper()
    lines = []
    backend_name = str(getattr(agent, "_browser_backend_name", "") or "").strip().lower()

    role_snapshot = getattr(agent, "_last_role_snapshot", None)
    if isinstance(role_snapshot, dict):
        snapshot_text = str(role_snapshot.get("snapshot") or "").strip()
        tree_nodes = role_snapshot.get("tree") if isinstance(role_snapshot.get("tree"), list) else []
        refs_mode = str(role_snapshot.get("refs_mode") or "").strip()
        stats = role_snapshot.get("stats") if isinstance(role_snapshot.get("stats"), dict) else {}
        if tree_nodes or snapshot_text:
            lines.append("## 역할 트리")
            meta_parts = []
            if refs_mode:
                meta_parts.append(f"refs_mode={refs_mode}")
            if stats:
                meta_parts.append(
                    "stats="
                    + ",".join(
                        f"{key}:{value}"
                        for key, value in (
                            ("lines", stats.get("lines")),
                            ("refs", stats.get("refs")),
                            ("interactive", stats.get("interactive")),
                        )
                        if value is not None
                    )
                )
            if meta_parts:
                lines.append(f'- {" ".join(meta_parts)}')
            if tree_nodes:
                goal_tokens = set(getattr(agent, "_goal_tokens", set()) or set())
                quoted_matches = [
                    agent._normalize_text(v)
                    for v in re.findall(r'"([^"]+)"', str(getattr(agent, "_active_goal_text", "") or ""))
                    if agent._normalize_text(v)
                ]
                def _tree_score(node: Dict[str, Any]) -> float:
                    role = str(node.get("role") or "").strip().lower()
                    name = str(node.get("name") or "").strip()
                    ancestors = " ".join(str(v).strip() for v in (node.get("ancestor_names") or []) if str(v).strip())
                    score = 0.0
                    score += 2.0 * len(goal_tokens.intersection(set(agent._tokenize_text(name))))
                    score += 1.25 * len(goal_tokens.intersection(set(agent._tokenize_text(ancestors))))
                    if role in {"row", "listitem", "gridcell", "cell", "article"}:
                        score += 1.75
                    elif role in {"button", "link", "tab", "menuitem"}:
                        score += 0.5
                    for phrase in quoted_matches:
                        if phrase and phrase in agent._normalize_text(name):
                            score += 4.0
                        elif phrase and phrase in agent._normalize_text(ancestors):
                            score += 2.0
                    return score
                ranked_tree_nodes = sorted(tree_nodes, key=_tree_score, reverse=True)
                tree_limit = 6 if backend_name == "openclaw" else 24
                tree_render_nodes = ranked_tree_nodes[:tree_limit]
                rendered_tree = []
                for node in tree_render_nodes:
                    depth = max(0, min(int(node.get("depth", 0) or 0), 6))
                    role = str(node.get("role") or "").strip() or "generic"
                    name = str(node.get("name") or "").strip()
                    ref = str(node.get("ref") or "").strip()
                    nth = node.get("nth")
                    line = f'{"  " * depth}- {role}'
                    if name:
                        line += f' "{name}"'
                    if ref and backend_name != "openclaw":
                        line += f" [ref={ref}]"
                    if nth is not None and backend_name != "openclaw":
                        line += f" [nth={nth}]"
                    rendered_tree.append(line)
                lines.extend(rendered_tree)
                if len(tree_nodes) > len(tree_render_nodes):
                    lines.append(f"... ({len(tree_nodes) - len(tree_render_nodes)} more role tree lines omitted)")
            else:
                snapshot_lines = snapshot_text.split("\n")
                lines.extend(snapshot_lines[:24])
                if len(snapshot_lines) > 24:
                    lines.append(f"... ({len(snapshot_lines) - 24} more role lines omitted)")
            lines.append("")

    def _score(el: DOMElement) -> float:
        text = agent._normalize_text(el.text)
        aria = agent._normalize_text(el.aria_label)
        role = agent._normalize_text(el.role)
        tag = agent._normalize_text(el.tag)
        backend_name = str(getattr(agent, "_browser_backend_name", "") or "").strip().lower()
        selector = agent._element_full_selectors.get(el.id) or agent._element_selectors.get(el.id) or ""
        fields = agent._fields_for_element(el)

        has_progress = any(agent._contains_progress_cta_hint(f) for f in fields)
        has_next = any(agent._contains_next_pagination_hint(f) for f in fields)
        has_context = any(agent._contains_context_shift_hint(f) for f in fields)
        has_expand = any(agent._contains_expand_hint(f) for f in fields)
        has_wishlist_like = any(agent._contains_wishlist_like_hint(f) for f in fields)
        has_add_like = any(agent._contains_add_like_hint(f) for f in fields)
        has_login_hint = any(agent._contains_login_hint(f) for f in fields)
        has_configure = any(agent._contains_configure_hint(f) for f in fields)
        has_execute = any(agent._contains_execute_hint(f) for f in fields)
        has_apply = any(agent._contains_apply_hint(f) for f in fields)

        score = 0.0
        local_context_score = agent._context_score(el)
        role_alignment_score = role_ref_alignment_score(agent, el)
        host_context_score = 0.0
        try:
            host_context_score = float(getattr(el, "context_score_hint", 0.0) or 0.0)
        except Exception:
            host_context_score = 0.0
        container_source = str(getattr(el, "container_source", None) or "")
        if has_progress:
            score += 6.0
        if has_next:
            score += 4.0
        if has_context:
            score += 3.0
        if has_login_hint:
            score += 2.0

        row_like = role in {"row", "listitem", "cell", "gridcell", "article"}
        if role in {"button", "tab", "link", "menuitem"}:
            score += 2.5
        if tag in {"button", "a", "input", "select"}:
            score += 1.7
        if row_like:
            score += 2.4
        if role == "generic" and getattr(el, "group_action_labels", None):
            score += 1.2
        if backend_name == "openclaw" and phase in {"COLLECT", "APPLY"}:
            if role == "button" and has_add_like:
                score += 5.0
            elif role == "link" and not has_add_like and not has_progress:
                score -= 2.0
            elif role == "generic" and not getattr(el, "group_action_labels", None):
                score -= 2.5

        normalized_selector = agent._normalize_text(selector)
        normalized_container_name = agent._normalize_text(getattr(el, "container_name", None) or "")
        normalized_context_text = agent._normalize_text(getattr(el, "context_text", None) or "")
        self_blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", "") or ""),
                ]
            )
        )
        if any(k in normalized_selector for k in ("pagination", "pager", "page", "tab", "tabs")):
            score += 2.0
        if any(k in normalized_selector for k in ("prev", "previous", "back", "이전")):
            score -= 4.0
        if any(k in normalized_selector for k in ("active", "current", "selected")):
            score -= 1.5
        if (agent._is_numeric_page_label(el.text) or agent._is_numeric_page_label(el.aria_label)) and not has_next:
            score -= 2.0

        if has_expand and not has_progress:
            score -= 2.0

        if phase in {"AUTH", "COLLECT"}:
            if has_add_like:
                score += 4.0
            if has_progress:
                score += 1.5
            if has_apply:
                score -= 1.0
        elif phase == "COMPOSE":
            if has_configure:
                score += 4.0
            if has_progress:
                score += 2.5
            if has_add_like:
                score -= 1.5
        elif phase == "APPLY":
            if has_execute or has_progress or has_apply:
                score += 5.0
            if has_next:
                score += 2.0
            if has_add_like:
                score -= 2.5
        elif phase == "VERIFY":
            if has_apply or has_progress:
                score += 5.5
            if has_add_like:
                score -= 3.0

        score += local_context_score
        score += role_alignment_score
        if backend_name == "openclaw":
            score += max(0.0, min(4.0, host_context_score * 0.35))
            phrase_in_self = any(phrase and phrase in self_blob for phrase in normalized_phrases)
            if role in {"button", "tab", "link", "menuitem", "option"}:
                phrase_in_container = any(phrase and phrase in normalized_container_name for phrase in normalized_phrases)
                phrase_in_context = any(phrase and phrase in normalized_context_text for phrase in normalized_phrases)
                if role == "button" and has_add_like:
                    if phrase_in_self:
                        score += 8.0
                    elif phrase_in_container:
                        score += 7.0
                    elif phrase_in_context:
                        score += 4.0
                elif phrase_in_container:
                    score += 3.0
                elif phrase_in_context:
                    score += 2.0
                elif normalized_container_name and not goal_tokens.intersection(container_tokens):
                    score -= 1.5
                if role in {"link", "menuitem", "option"} and not phrase_in_self and (phrase_in_container or phrase_in_context):
                    score -= 4.0
            elif role == "generic":
                phrase_in_container = any(phrase and phrase in normalized_container_name for phrase in normalized_phrases)
                phrase_in_context = any(phrase and phrase in normalized_context_text for phrase in normalized_phrases)
                if not phrase_in_self and (phrase_in_container or phrase_in_context):
                    score -= 5.0
        else:
            score += max(0.0, min(0.75, host_context_score * 0.12))
        if container_source == "semantic-first":
            score += 1.0
        score += agent._selector_bias_for_fields(fields)
        score += 0.8 * agent._adaptive_intent_bias(agent._candidate_intent_key("click", fields))

        if text:
            score += min(2.5, len(text) / 18.0)

        recent_clicks = agent._recent_click_element_ids[-10:]
        if recent_clicks:
            for offset, recent_id in enumerate(reversed(recent_clicks), start=1):
                if recent_id == el.id:
                    score -= max(1.2, 4.5 - (offset * 0.45))
                    break
            repeat_count = recent_clicks.count(el.id)
            if repeat_count > 1:
                score -= min(4.0, 0.9 * (repeat_count - 1))

        if agent._last_dom_top_ids and el.id in recent_clicks:
            try:
                previous_rank = agent._last_dom_top_ids.index(el.id)
            except ValueError:
                previous_rank = -1
            if 0 <= previous_rank < 5:
                score -= max(1.0, 3.2 - (previous_rank * 0.5))

        return agent._clamp_score(score, low=-25.0, high=35.0)

    ranked = sorted(elements, key=_score, reverse=True)
    agent._last_dom_top_ids = [el.id for el in ranked[:12]]
    try:
        dom_limit = int(os.getenv("GAIA_LLM_DOM_LIMIT", "260"))
    except Exception:
        dom_limit = 260
    dom_limit = max(80, min(dom_limit, 800))
    if backend_name == "openclaw":
        dom_limit = min(dom_limit, 140)
    selected: List[DOMElement] = ranked[:dom_limit]

    for el in selected:
        if backend_name == "openclaw" and getattr(el, "ref_id", None):
            parts = [f'[ref={el.ref_id}] <{el.tag}>']
        else:
            parts = [f"[{el.id}] <{el.tag}>"]
        interactive_role = str(getattr(el, "role", "") or "").strip().lower()
        openclaw_interactive = backend_name == "openclaw" and interactive_role in {"button", "link", "tab", "menuitem", "option"}

        if openclaw_interactive and getattr(el, "container_name", None):
            parts.append(f'within="{el.container_name}"')

        if el.text:
            parts.append(f'"{el.text}"')
        if el.role:
            parts.append(f"role={el.role}")
        if el.type and el.type != "button":
            parts.append(f"type={el.type}")
        if getattr(el, "container_name", None) and not openclaw_interactive:
            parts.append(f'container="{el.container_name}"')
        if getattr(el, "container_role", None):
            parts.append(f'container-role="{el.container_role}"')
        if getattr(el, "container_source", None):
            parts.append(f'container-source="{el.container_source}"')
        if getattr(el, "context_text", None):
            parts.append(f'context="{truncate_for_prompt(el.context_text, 120)}"')
        action_labels = getattr(el, "group_action_labels", None) or []
        if action_labels:
            parts.append(f'actions=[{" | ".join(str(v) for v in action_labels[:5])}]')
        role_ref_role = getattr(el, "role_ref_role", None)
        role_ref_name = getattr(el, "role_ref_name", None)
        if role_ref_role and role_ref_name:
            nth = getattr(el, "role_ref_nth", None)
            role_ref = f'{role_ref_role}(name="{role_ref_name}"'
            if nth is not None:
                role_ref += f", nth={nth}"
            role_ref += ")"
            parts.append(f"role_ref={role_ref}")
        if el.placeholder:
            parts.append(f'placeholder="{el.placeholder}"')
        if el.aria_label:
            parts.append(f'aria-label="{el.aria_label}"')
        if el.tag == "select" and el.options:
            opt_strs = [f'{o.get("value","")}: {o.get("text","")}' for o in el.options[:10]]
            parts.append(f'options=[{" | ".join(opt_strs)}]')

        lines.append(" ".join(parts))

    grouped: Dict[str, Dict[str, Any]] = {}
    for el in selected:
        container_ref_id = getattr(el, "container_ref_id", None)
        container_name = getattr(el, "container_name", None)
        if not container_ref_id or not container_name:
            continue
        bucket = grouped.setdefault(
            str(container_ref_id),
            {
                "name": str(container_name),
                "source": str(getattr(el, "container_source", None) or ""),
                "items": [],
            },
        )
        if backend_name == "openclaw" and getattr(el, "ref_id", None):
            bucket["items"].append(f'[ref={el.ref_id} {el.text or el.aria_label or el.tag}]')
        else:
            bucket["items"].append(f'[{el.id} {el.text or el.aria_label or el.tag}]')
    if grouped:
        lines.append("")
        lines.append("## 컨텍스트 그룹")
        for bucket in grouped.values():
            source = f' source={bucket["source"]}' if bucket.get("source") else ""
            lines.append(f'- 카드 "{bucket["name"]}"{source}: {" ".join(bucket["items"][:6])}')

    context_snapshot = getattr(agent, "_last_context_snapshot", None)
    role_groups_by_container_ref = {}
    if isinstance(context_snapshot, dict):
        raw_groups = context_snapshot.get("role_groups_by_container_ref")
        if isinstance(raw_groups, dict):
            role_groups_by_container_ref = raw_groups
    selected_container_refs = [
        str(getattr(el, "container_ref_id", None) or "").strip()
        for el in selected
        if str(getattr(el, "container_ref_id", None) or "").strip()
    ]
    rendered_role_groups = []
    for container_ref in list(dict.fromkeys(selected_container_refs)):
        groups = role_groups_by_container_ref.get(container_ref)
        if not isinstance(groups, list) or not groups:
            continue
        container_name = ""
        for el in selected:
            if str(getattr(el, "container_ref_id", None) or "") == container_ref:
                container_name = str(getattr(el, "container_name", None) or "").strip()
                break
        summaries = [
            str(group.get("summary") or "").strip()
            for group in groups[:4]
            if isinstance(group, dict) and str(group.get("summary") or "").strip()
        ]
        if not summaries:
            continue
        rendered_role_groups.append((container_name or container_ref, summaries))
    if rendered_role_groups:
        lines.append("")
        lines.append("## 역할 그룹")
        for container_name, summaries in rendered_role_groups[:8]:
            lines.append(f'- "{container_name}": {" | ".join(summaries)}')

    if len(elements) > len(selected):
        lines.append(f"... ({len(elements) - len(selected)} more elements omitted)")
    return "\n".join(lines)
