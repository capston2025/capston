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


def semantic_tags_for_element(agent: Any, el: DOMElement) -> List[str]:
    semantics = getattr(agent, "_goal_semantics", None)
    if semantics is None:
        return []
    normalize = getattr(agent, "_normalize_text", None)
    if not callable(normalize):
        return []
    target_terms = [
        normalize(term)
        for term in list(getattr(semantics, "target_terms", []) or [])
        if str(term or "").strip()
    ]
    destination_terms = [
        normalize(term)
        for term in list(getattr(semantics, "destination_terms", []) or [])
        if str(term or "").strip()
    ]
    group_labels = getattr(el, "group_action_labels", None) or []
    label_blob = normalize(" ".join(str(item or "") for item in group_labels))
    self_blob = normalize(
        " ".join(
            [
                str(getattr(el, "text", "") or ""),
                str(getattr(el, "aria_label", "") or ""),
                str(getattr(el, "placeholder", "") or ""),
                str(getattr(el, "title", "") or ""),
                str(getattr(el, "role_ref_name", "") or ""),
                str(getattr(el, "role_ref_role", "") or ""),
                str(getattr(el, "type", "") or ""),
            ]
        )
    )
    context_blob = normalize(
        " ".join(
            [
                str(getattr(el, "container_name", "") or ""),
                str(getattr(el, "container_role", "") or ""),
                str(getattr(el, "context_text", "") or ""),
            ]
        )
    )
    blob = normalize(" ".join(part for part in (self_blob, context_blob, label_blob) if part))
    if not blob:
        return []
    role = str(getattr(el, "role", "") or "").lower()
    tag = str(getattr(el, "tag", "") or "").lower()
    clickable = bool(
        getattr(el, "is_enabled", True)
        and (role in {"button", "link", "tab", "menuitem", "option"} or tag in {"button", "a"})
    )
    row_like = str(getattr(el, "container_role", "") or "").lower() in {"listitem", "row", "article", "region", "group"}
    target_hit = any(term and (term in self_blob or term in context_blob) for term in target_terms)
    destination_hit = any(term and (term in blob or term in label_blob) for term in destination_terms)
    add_hit = any(token in blob for token in ("바로 추가", "추가", "담기", "add", "append", "apply", "반영", "넣기"))
    remove_hit = any(token in blob for token in ("삭제", "제거", "remove", "delete", "clear", "비우"))
    reveal_hit = any(
        token in blob
        for token in (
            "더보기",
            "show more",
            "view all",
            "expand",
            "펼치",
            "열기",
            "보기",
            "open",
            "view",
        )
    )
    if not reveal_hit and clickable and destination_hit and role in {"tab", "link"}:
        reveal_hit = True
    secondary_reveal_hit = any(
        token in blob
        for token in ("더보기", "show more", "view all", "expand", "펼치", "열기", "menu", "옵션", "more", "편집", "edit", "상세", "details")
    )
    close_tokens = [token for token in re.split(r"[^0-9A-Za-z가-힣×]+", self_blob) if token]
    close_hit = any(token in blob for token in ("닫", "close", "취소", "cancel", "dismiss")) or any(
        token.lower() in {"x", "×"} for token in close_tokens
    )
    feedback_conflict_hit = any(
        token in blob
        for token in (
            "이미 추가",
            "이미 담은",
            "시간이 겹쳐",
            "충돌",
            "중복",
            "duplicate",
            "already added",
            "already exists",
            "conflict",
            "server 검사",
        )
    )
    feedback_success_hit = any(
        token in blob
        for token in (
            "추가했어요",
            "추가되었습니다",
            "추가되었",
            "담았어요",
            "saved to",
            "added to",
            "successfully added",
        )
    ) and (destination_hit or target_hit)
    input_like = tag in {"input", "textarea"} or role == "textbox"
    auth_self_blob = normalize(
        " ".join(
            [
                self_blob,
                str(getattr(el, "placeholder", "") or ""),
                str(getattr(el, "aria_label", "") or ""),
                str(getattr(el, "title", "") or ""),
                str(getattr(el, "role_ref_name", "") or ""),
            ]
        )
    )
    auth_context_blob = normalize(
        " ".join(
            [
                auth_self_blob,
                str(getattr(el, "container_name", "") or ""),
                str(getattr(el, "container_role", "") or ""),
                str(getattr(el, "context_text", "") or ""),
            ]
        )
    )
    auth_surface_hit = any(
        token in auth_context_blob
        for token in (
            "로그인",
            "login",
            "sign in",
            "signin",
            "회원가입",
            "sign up",
            "signup",
            "auth",
            "username",
            "user id",
            "userid",
            "email",
            "이메일",
            "아이디",
            "identifier",
            "password",
            "비밀번호",
            "passwd",
            "pwd",
        )
    )
    auth_container_blob = normalize(
        " ".join(
            [
                str(getattr(el, "container_name", "") or ""),
                str(getattr(el, "container_role", "") or ""),
                str(getattr(el, "context_text", "") or ""),
            ]
        )
    )
    auth_identifier_hit = any(
        token in auth_self_blob
        for token in ("username", "user id", "userid", "email", "이메일", "아이디", "identifier")
    )
    auth_identifier_context_hit = any(
        token in auth_context_blob
        for token in ("username", "user id", "userid", "email", "이메일", "아이디", "identifier")
    )
    auth_password_hit = any(token in auth_self_blob for token in ("password", "비밀번호", "passwd", "pwd"))
    auth_password_context_hit = any(token in auth_context_blob for token in ("password", "비밀번호", "passwd", "pwd"))
    role_ref_nth = getattr(el, "role_ref_nth", None)
    second_textbox_hint = input_like and auth_surface_hit and role_ref_nth == 1 and not auth_identifier_hit
    auth_submit_hit = any(
        token in auth_self_blob
        for token in ("로그인", "login", "sign in", "signin", "submit", "continue")
    )
    auth_container_hit = any(
        token in auth_container_blob
        for token in (
            "로그인",
            "login",
            "sign in",
            "signin",
            "username",
            "email",
            "아이디",
            "identifier",
            "password",
            "비밀번호",
        )
    )

    tags: List[str] = []
    if target_hit:
        tags.append("target_match")
    if feedback_conflict_hit:
        tags.append("feedback_conflict_signal")
    if feedback_success_hit:
        tags.append("feedback_success_signal")
    if auth_surface_hit and clickable and auth_submit_hit and (
        auth_container_hit or not str(getattr(el, "container_name", "") or "").strip()
    ):
        tags.append("auth_submit_candidate")
    if input_like and auth_surface_hit:
        input_type = str(getattr(el, "type", "") or "").lower()
        if auth_password_hit or input_type == "password" or second_textbox_hint:
            tags.append("auth_password_field")
        elif (
            auth_identifier_hit
            or input_type == "email"
            or (
                input_type in {"", "text", "tel"}
                and (auth_identifier_context_hit or auth_container_hit)
                and not auth_password_context_hit
            )
            or (role_ref_nth == 0 and auth_container_hit and not auth_password_hit)
        ):
            tags.append("auth_identifier_field")
    if close_hit and clickable:
        tags.append("close_like")
    if destination_hit and target_hit and row_like:
        tags.append("destination_target_row")
    if clickable and add_hit and target_hit and not destination_hit:
        tags.append("source_mutation_candidate")
    if clickable and reveal_hit and destination_hit and not close_hit and not add_hit:
        tags.append("destination_reveal_candidate")
    if clickable and remove_hit and (destination_hit or target_hit) and not close_hit:
        tags.append("destination_remove_candidate")
    if clickable and secondary_reveal_hit and target_hit:
        tags.append("target_row_secondary_reveal_candidate")
    return tags


def _is_source_like_element(agent: Any, el: DOMElement) -> bool:
    blob = agent._normalize_text(
        " ".join(
            [
                str(getattr(el, "container_name", "") or ""),
                str(getattr(el, "context_text", "") or ""),
                str(getattr(el, "role_ref_name", "") or ""),
            ]
        )
    )
    return any(
        token in blob
        for token in ("검색 결과", "search result", "result list", "search", "results", "검색", "result")
    )


def detect_active_surface_context(
    agent: Any,
    elements: List[DOMElement],
    semantic_tag_cache: Optional[Dict[int, List[str]]] = None,
) -> Dict[str, Any]:
    if not isinstance(elements, list) or not elements:
        return {"active": False}

    tag_cache: Dict[int, List[str]] = semantic_tag_cache or {
        int(getattr(el, "id", -1)): semantic_tags_for_element(agent, el)
        for el in elements
    }
    snapshot_evidence = (
        getattr(agent, "_last_snapshot_evidence", None)
        if isinstance(getattr(agent, "_last_snapshot_evidence", None), dict)
        else {}
    )
    modal_open_hint = bool((snapshot_evidence or {}).get("modal_open"))
    semantics = getattr(agent, "_goal_semantics", None)
    normalize = getattr(agent, "_normalize_text", None)
    destination_terms = []
    if semantics is not None and callable(normalize):
        destination_terms = [
            normalize(term)
            for term in list(getattr(semantics, "destination_terms", []) or [])
            if str(term or "").strip()
        ]

    destination_heading_index: Optional[int] = None
    destination_heading: Optional[DOMElement] = None
    destination_action_indices: List[int] = []
    background_indices: List[int] = []

    for index, element in enumerate(elements):
        tags = set(tag_cache.get(int(getattr(element, "id", -1)), []) or [])
        blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(element, "text", "") or ""),
                    str(getattr(element, "aria_label", "") or ""),
                    str(getattr(element, "title", "") or ""),
                    str(getattr(element, "role_ref_name", "") or ""),
                    str(getattr(element, "context_text", "") or ""),
                ]
            )
        )
        role = str(getattr(element, "role", "") or "").strip().lower()
        tag = str(getattr(element, "tag", "") or "").strip().lower()
        is_heading_like = role in {"heading", "dialog", "alertdialog", "banner"} or tag in {"h1", "h2", "h3"}
        heading_destination_hit = any(term and term in blob for term in destination_terms)
        if destination_heading is None and is_heading_like and (heading_destination_hit or role in {"dialog", "alertdialog"}):
            destination_heading = element
            destination_heading_index = index
        if (
            ("destination_remove_candidate" in tags or "destination_reveal_candidate" in tags)
            and not _is_source_like_element(agent, element)
        ):
            destination_action_indices.append(index)
        if "source_mutation_candidate" in tags and _is_source_like_element(agent, element):
            background_indices.append(index)

    close_candidate: Optional[DOMElement] = None
    explicit_close: Optional[DOMElement] = None
    if destination_heading_index is not None:
        for index in range(destination_heading_index, min(len(elements), destination_heading_index + 8)):
            element = elements[index]
            tags = set(tag_cache.get(int(getattr(element, "id", -1)), []) or [])
            role = str(getattr(element, "role", "") or "").strip().lower()
            tag = str(getattr(element, "tag", "") or "").strip().lower()
            if role not in {"button", "link"} and tag not in {"button", "a"}:
                continue
            if tags.intersection({"feedback_conflict_signal", "feedback_success_signal"}):
                continue
            label = " ".join(
                str(getattr(element, key, "") or "")
                for key in ("text", "aria_label", "title", "role_ref_name")
            ).strip()
            if label:
                continue
            close_candidate = element
            break
        for index, element in enumerate(elements):
            tags = set(tag_cache.get(int(getattr(element, "id", -1)), []) or [])
            if (
                "close_like" in tags
                and not tags.intersection({"feedback_conflict_signal", "feedback_success_signal"})
                and not _is_source_like_element(agent, element)
            ):
                if abs(index - destination_heading_index) <= 8:
                    explicit_close = element
                    break
    if explicit_close is not None and close_candidate is None:
        close_candidate = explicit_close

    surface_active = bool(
        destination_heading is not None
        and (
            modal_open_hint
            or close_candidate is not None
        )
    )
    if not surface_active:
        return {"active": False}

    action_elements = [elements[index] for index in destination_action_indices[:6]]
    background_elements = [elements[index] for index in background_indices[:4]]
    return {
        "active": True,
        "kind": "destination_surface",
        "heading": destination_heading,
        "heading_index": destination_heading_index,
        "action_elements": action_elements,
        "action_ids": {int(getattr(el, "id", -1)) for el in action_elements},
        "close_candidate": close_candidate,
        "close_ids": {int(getattr(close_candidate, "id", -1))} if close_candidate is not None else set(),
        "background_elements": background_elements,
        "background_ids": {int(getattr(el, "id", -1)) for el in background_elements},
        "modal_open_hint": modal_open_hint,
    }


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


def _collect_openclaw_role_tree_focus_refs(
    agent: Any,
    elements: List[DOMElement],
    active_surface_context: Dict[str, Any],
) -> List[str]:
    recent_blob = "\n".join(
        [
            *(str(item or "") for item in list(getattr(agent, "_action_history", []) or [])[-8:]),
            *(str(item or "") for item in list(getattr(agent, "_action_feedback", []) or [])[-8:]),
        ]
    )
    recent_refs = {match for match in re.findall(r"\b(e\d+)\b", recent_blob)}
    focus_refs: List[Tuple[float, int, str]] = []
    goal_tokens = set(getattr(agent, "_goal_tokens", set()) or set())
    normalized_goal_text = agent._normalize_text(str(getattr(agent, "_active_goal_text", "") or ""))
    normalized_phrases = [
        agent._normalize_text(v)
        for v in re.findall(r'["\']([^"\']+)["\']', str(getattr(agent, "_active_goal_text", "") or ""))
        if agent._normalize_text(v)
    ]
    for index, el in enumerate(elements or []):
        ref_id = str(getattr(el, "ref_id", "") or "").strip()
        if not ref_id:
            continue
        role = str(getattr(el, "role", "") or "").strip().lower()
        tag = str(getattr(el, "tag", "") or "").strip().lower()
        blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "container_name", "") or ""),
                    str(getattr(el, "context_text", "") or ""),
                    str(getattr(el, "role_ref_name", "") or ""),
                ]
            )
        )
        overlap = goal_tokens.intersection(set(agent._tokenize_text(blob)))
        score = 0.0
        if ref_id in recent_refs:
            score += 26.0
        score += min(12.0, 3.0 * len(overlap))
        if normalized_goal_text and normalized_goal_text in blob:
            score += 8.0
        if any(phrase and phrase in blob for phrase in normalized_phrases):
            score += 10.0
        if role in {"textbox", "combobox"} and agent._contains_login_hint(blob):
            score += 18.0
        elif role in {"button", "link"} and agent._contains_login_hint(blob):
            score += 12.0
        elif tag in {"input", "textarea"} and agent._contains_login_hint(blob):
            score += 15.0
        if role in {"button", "link", "tab", "menuitem", "option"}:
            score += 2.0
        if ref_id in {
            str(getattr(active_surface_context.get("heading"), "ref_id", "") or "").strip(),
            str(getattr(active_surface_context.get("close_candidate"), "ref_id", "") or "").strip(),
        }:
            score += 6.0
        if score > 0.0:
            focus_refs.append((score, index, ref_id))
    focus_refs.sort(key=lambda item: (-item[0], item[1], item[2]))
    ordered_unique: List[str] = []
    for _, _, ref_id in focus_refs:
        if ref_id and ref_id not in ordered_unique:
            ordered_unique.append(ref_id)
    return ordered_unique[:18]


def _compute_delta_snapshot(
    prev_lines: List[str],
    cur_lines: List[str],
    context_radius: int = 2,
) -> Tuple[List[str], float]:
    """이전 턴과 현재 턴의 raw snapshot을 비교해 변경된 영역만 추출한다.

    Returns:
        (delta_lines, change_ratio) — change_ratio는 0.0~1.0
    """
    import difflib

    if not prev_lines:
        return cur_lines, 1.0

    matcher = difflib.SequenceMatcher(None, prev_lines, cur_lines, autojunk=False)
    changed_indices: set = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for j in range(max(0, j1 - context_radius), min(len(cur_lines), j2 + context_radius)):
            changed_indices.add(j)

    if not changed_indices:
        return [], 0.0

    change_ratio = len(changed_indices) / max(1, len(cur_lines))

    sorted_indices = sorted(changed_indices)
    delta_lines: List[str] = []
    prev_idx = -2
    for idx in sorted_indices:
        if idx > prev_idx + 1:
            if delta_lines:
                delta_lines.append(f"... (unchanged {idx - prev_idx - 1} lines)")
        delta_lines.append(cur_lines[idx])
        prev_idx = idx

    remaining = len(cur_lines) - 1 - prev_idx
    if remaining > 0:
        delta_lines.append(f"... (unchanged {remaining} lines)")

    return delta_lines, change_ratio


def _goal_requires_full_raw_snapshot(agent: Any) -> bool:
    """수집/변경형 goal은 delta보다 현재 full raw snapshot이 안전하다."""
    goal_constraints = getattr(agent, "_goal_constraints", {}) or {}
    if not isinstance(goal_constraints, dict):
        return False

    mutation_direction = str(goal_constraints.get("mutation_direction") or "").strip().lower()
    if mutation_direction in {"increase", "decrease", "clear"}:
        return True
    if goal_constraints.get("collect_min") is not None:
        return True
    if goal_constraints.get("apply_target") is not None:
        return True
    if bool(goal_constraints.get("require_state_change")):
        return True
    return False


def _render_openclaw_raw_role_tree(
    agent: Any,
    role_snapshot: Dict[str, Any],
    elements: List[DOMElement],
    active_surface_context: Dict[str, Any],
    snapshot_text_override: Optional[str] = None,
) -> List[str]:
    raw_snapshot_text = str(role_snapshot.get("snapshot") or "").strip()
    snapshot_text = str(snapshot_text_override or raw_snapshot_text or "").strip()
    if not snapshot_text:
        return []
    raw_lines = snapshot_text.splitlines()
    try:
        raw_tree_line_limit = int(os.getenv("GAIA_OPENCLAW_RAW_TREE_LINE_LIMIT", "0"))
    except Exception:
        raw_tree_line_limit = 0
    if raw_tree_line_limit > 0 and len(raw_lines) > raw_tree_line_limit:
        result = raw_lines[:raw_tree_line_limit] + [
            f"... ({len(raw_lines) - raw_tree_line_limit} more raw role lines omitted)"
        ]
        agent._prev_raw_snapshot_text = snapshot_text
        return result

    prev_text = str(getattr(agent, "_prev_raw_snapshot_text", "") or "")
    try:
        delta_disabled = str(os.getenv("GAIA_OPENCLAW_DELTA_DISABLED", "0")).strip() == "1"
    except Exception:
        delta_disabled = False
    if _goal_requires_full_raw_snapshot(agent):
        delta_disabled = True
    try:
        fallback_ratio = float(os.getenv("GAIA_OPENCLAW_DELTA_FALLBACK_RATIO", "0.7"))
    except Exception:
        fallback_ratio = 0.7

    if prev_text and not delta_disabled:
        prev_lines = prev_text.splitlines()
        delta_lines, change_ratio = _compute_delta_snapshot(prev_lines, raw_lines)

        if change_ratio == 0.0:
            agent._prev_raw_snapshot_text = snapshot_text
            return ["(DOM 변경 없음 — 이전 턴의 역할 트리와 동일)"]

        if change_ratio < fallback_ratio:
            agent._prev_raw_snapshot_text = snapshot_text
            return [
                f"(변경 영역만 표시 — 전체 {len(raw_lines)}줄 중 {len(delta_lines)}줄, 변경률 {change_ratio:.0%})",
            ] + delta_lines

    agent._prev_raw_snapshot_text = snapshot_text
    return raw_lines


def format_dom_for_llm(agent: Any, elements: List[DOMElement]) -> str:
    phase = (agent._runtime_phase or "COLLECT").upper()
    lines = []
    backend_name = str(getattr(agent, "_browser_backend_name", "") or "").strip().lower()
    goal_tokens = set(getattr(agent, "_goal_tokens", set()) or set())
    normalized_phrases = [
        agent._normalize_text(v)
        for v in re.findall(r'"([^"]+)"', str(getattr(agent, "_active_goal_text", "") or ""))
        if agent._normalize_text(v)
    ]
    semantic_tag_cache: Dict[int, List[str]] = {
        int(getattr(el, "id", -1)): semantic_tags_for_element(agent, el)
        for el in (elements or [])
    }
    active_surface_context = detect_active_surface_context(agent, elements or [], semantic_tag_cache)
    if active_surface_context.get("active"):
        augmented_cache: Dict[int, List[str]] = {
            element_id: list(tags)
            for element_id, tags in semantic_tag_cache.items()
        }
        heading = active_surface_context.get("heading")
        if isinstance(heading, DOMElement):
            heading_id = int(getattr(heading, "id", -1))
            if heading_id in augmented_cache and "active_surface_heading" not in augmented_cache[heading_id]:
                augmented_cache[heading_id].append("active_surface_heading")
        for element_id in set(active_surface_context.get("action_ids") or set()):
            if element_id in augmented_cache and "active_surface_action" not in augmented_cache[element_id]:
                augmented_cache[element_id].append("active_surface_action")
        for element_id in set(active_surface_context.get("close_ids") or set()):
            if element_id in augmented_cache and "surface_close_candidate" not in augmented_cache[element_id]:
                augmented_cache[element_id].append("surface_close_candidate")
        for element_id in set(active_surface_context.get("background_ids") or set()):
            if element_id in augmented_cache and "occluded_background_candidate" not in augmented_cache[element_id]:
                augmented_cache[element_id].append("occluded_background_candidate")
        semantic_tag_cache = augmented_cache
    auth_field_ids = {
        element_id
        for element_id, tags in semantic_tag_cache.items()
        if "auth_identifier_field" in tags or "auth_password_field" in tags
    }
    auth_submit_ids = {
        element_id for element_id, tags in semantic_tag_cache.items() if "auth_submit_candidate" in tags
    }
    auth_surface_active = bool((len(auth_field_ids) >= 2) or (auth_field_ids and auth_submit_ids))
    feedback_signal_active = any(
        ("feedback_conflict_signal" in tags) or ("feedback_success_signal" in tags)
        for tags in semantic_tag_cache.values()
    )
    ref_semantic_tag_cache: Dict[str, set[str]] = {
        str(getattr(el, "ref_id", "") or "").strip(): set(semantic_tag_cache.get(int(getattr(el, "id", -1)), []) or [])
        for el in (elements or [])
        if str(getattr(el, "ref_id", "") or "").strip()
    }

    role_snapshot = getattr(agent, "_last_role_snapshot", None)
    openclaw_raw_prompt_ready = False
    if isinstance(role_snapshot, dict):
        snapshot_text = str(role_snapshot.get("snapshot") or "").strip()
        scoped_snapshot_text = str(role_snapshot.get("scoped_snapshot") or "").strip()
        scope_applied = bool(role_snapshot.get("scope_applied"))
        tree_nodes = role_snapshot.get("tree") if isinstance(role_snapshot.get("tree"), list) else []
        refs_mode = str(role_snapshot.get("refs_mode") or "").strip()
        stats = (
            role_snapshot.get("scoped_stats")
            if scope_applied and isinstance(role_snapshot.get("scoped_stats"), dict)
            else role_snapshot.get("stats")
        )
        stats = stats if isinstance(stats, dict) else {}
        if tree_nodes or snapshot_text:
            if backend_name == "openclaw":
                lines.append(
                    "## OpenClaw scope 역할 트리 (주 입력)"
                    if scope_applied and scoped_snapshot_text
                    else "## OpenClaw 원본 역할 트리 (주 입력)"
                )
            else:
                lines.append("## 역할 트리")
            meta_parts = []
            if refs_mode:
                meta_parts.append(f"refs_mode={refs_mode}")
            if backend_name == "openclaw" and scope_applied and scoped_snapshot_text:
                meta_parts.append(f'scope={str(role_snapshot.get("scope_container_ref_id") or "").strip() or "applied"}')
            if meta_parts:
                lines.append(f'- {" ".join(meta_parts)}')
            if backend_name == "openclaw" and snapshot_text:
                lines.extend(
                    _render_openclaw_raw_role_tree(
                        agent,
                        role_snapshot,
                        elements or [],
                        active_surface_context,
                        snapshot_text_override=scoped_snapshot_text if scope_applied and scoped_snapshot_text else None,
                    )
                )
                openclaw_raw_prompt_ready = True
            elif tree_nodes:
                def _tree_score(node: Dict[str, Any]) -> float:
                    role = str(node.get("role") or "").strip().lower()
                    name = str(node.get("name") or "").strip()
                    ref = str(node.get("ref") or "").strip()
                    ancestors = " ".join(str(v).strip() for v in (node.get("ancestor_names") or []) if str(v).strip())
                    score = 0.0
                    score += 2.0 * len(goal_tokens.intersection(set(agent._tokenize_text(name))))
                    score += 1.25 * len(goal_tokens.intersection(set(agent._tokenize_text(ancestors))))
                    if role in {"row", "listitem", "gridcell", "cell", "article"}:
                        score += 1.75
                    elif role in {"button", "link", "tab", "menuitem"}:
                        score += 0.5
                    for phrase in normalized_phrases:
                        if phrase and phrase in agent._normalize_text(name):
                            score += 4.0
                        elif phrase and phrase in agent._normalize_text(ancestors):
                            score += 2.0
                    if auth_surface_active:
                        node_tags = ref_semantic_tag_cache.get(ref, set())
                        node_blob = agent._normalize_text(" ".join(part for part in (role, name, ancestors) if part))
                        if "auth_identifier_field" in node_tags or "auth_password_field" in node_tags:
                            score += 16.0
                        if "auth_submit_candidate" in node_tags:
                            score += 13.0
                        if "source_mutation_candidate" in node_tags:
                            score -= 12.0
                        if not node_tags and agent._contains_login_hint(node_blob):
                            score += 6.0
                    if feedback_signal_active:
                        node_tags = ref_semantic_tag_cache.get(ref, set())
                        if "feedback_conflict_signal" in node_tags:
                            score += 5.0
                        if "feedback_success_signal" in node_tags:
                            score += 4.0
                        if "destination_reveal_candidate" in node_tags:
                            score += 9.0
                        if "close_like" in node_tags:
                            score -= 6.0
                    if active_surface_context.get("active"):
                        node_tags = ref_semantic_tag_cache.get(ref, set())
                        if "active_surface_heading" in node_tags:
                            score += 7.0
                        if "active_surface_action" in node_tags:
                            score += 8.0
                        if "surface_close_candidate" in node_tags:
                            score += 10.0
                        if "occluded_background_candidate" in node_tags:
                            score -= 14.0
                    return score
                ranked_tree_nodes = sorted(tree_nodes, key=_tree_score, reverse=True)
                tree_limit = 24
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

    if openclaw_raw_prompt_ready:
        return "\n".join(lines)

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
        semantic_tags = set(semantic_tag_cache.get(int(getattr(el, "id", -1)), []) or [])
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
        if auth_surface_active:
            if "auth_identifier_field" in semantic_tags or "auth_password_field" in semantic_tags:
                score += 18.0
            if "auth_submit_candidate" in semantic_tags:
                score += 14.0
                container_role = str(getattr(el, "container_role", "") or "").strip().lower()
                if container_role in {"banner", "navigation", "main"}:
                    score -= 6.0
            if "source_mutation_candidate" in semantic_tags:
                score -= 12.0
            elif has_add_like and role == "button":
                score -= 7.0
        if feedback_signal_active:
            if "feedback_conflict_signal" in semantic_tags:
                score += 6.0
            if "feedback_success_signal" in semantic_tags:
                score += 4.0
            if "destination_reveal_candidate" in semantic_tags:
                score += 10.0
            if "close_like" in semantic_tags:
                score -= 6.0
            if "feedback_success_signal" in semantic_tags and "source_mutation_candidate" in semantic_tags:
                score -= 8.0
        if active_surface_context.get("active"):
            if "active_surface_heading" in semantic_tags:
                score += 7.0
            if "active_surface_action" in semantic_tags:
                score += 8.0
            if "surface_close_candidate" in semantic_tags:
                score += 10.0
            if "occluded_background_candidate" in semantic_tags:
                score -= 14.0
            elif "source_mutation_candidate" in semantic_tags:
                score -= 7.0

        normalized_selector = agent._normalize_text(selector)
        normalized_container_name = agent._normalize_text(getattr(el, "container_name", None) or "")
        normalized_context_text = agent._normalize_text(getattr(el, "context_text", None) or "")
        container_tokens = set(agent._tokenize_text(getattr(el, "container_name", "") or ""))
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

    try:
        dom_limit = int(os.getenv("GAIA_LLM_DOM_LIMIT", "260"))
    except Exception:
        dom_limit = 260
    dom_limit = max(80, min(dom_limit, 800))
    if backend_name == "openclaw":
        selected = list(elements[:dom_limit])
        agent._last_dom_top_ids = [el.id for el in selected[:12]]
    else:
        ranked = sorted(elements, key=_score, reverse=True)
        agent._last_dom_top_ids = [el.id for el in ranked[:12]]
        selected = ranked[:dom_limit]

    # --- goal 관련 interactive 요소 보장 포함 ---
    # 조건 A: 자체+컨텍스트 합산 goal 토큰 2개 이상
    # 조건 B: container/context가 goal quoted phrase를 포함하는 interactive 요소
    if backend_name != "openclaw" and len(ranked) > dom_limit and goal_tokens:
        selected_ids = {el.id for el in selected}
        interactive_tags = {"button", "a", "input", "select"}
        interactive_roles = {"button", "link", "tab", "menuitem", "option", "checkbox", "radio"}
        rescue_limit = max(10, dom_limit // 10)
        rescued = 0

        # goal quoted phrases 준비 (예: "포용사회와문화탐방1")
        _raw_goal_text = str(getattr(agent, "_active_goal_text", "") or "")
        _quoted = re.findall(r"['\"]([^'\"]+)['\"]", _raw_goal_text)
        _norm_phrases = [agent._normalize_text(v) for v in _quoted if len(agent._normalize_text(v)) >= 2]
        # goal 토큰 중 길이 4 이상을 substring 후보로도 사용 (한국어 조사 변형 대응)
        _long_tokens = [t for t in goal_tokens if len(t) >= 4]

        for el in ranked[dom_limit:]:
            if rescued >= rescue_limit:
                break
            if el.id in selected_ids:
                continue
            tag = str(getattr(el, "tag", "") or "").strip().lower()
            role = str(getattr(el, "role", "") or "").strip().lower()
            if tag not in interactive_tags and role not in interactive_roles:
                continue
            el_text = str(getattr(el, "text", "") or "")
            el_aria = str(getattr(el, "aria_label", "") or "")
            el_container = str(getattr(el, "container_name", "") or "")
            el_context = str(getattr(el, "context_text", "") or "")
            self_blob = agent._normalize_text(f"{el_text} {el_aria}")
            ctx_blob = agent._normalize_text(f"{el_container} {el_context}")

            # 토큰 정확 매칭
            self_tokens = set(agent._tokenize_text(f"{el_text} {el_aria}"))
            ctx_tokens = set(agent._tokenize_text(f"{el_container} {el_context}"))
            self_overlap = goal_tokens.intersection(self_tokens)
            ctx_overlap = goal_tokens.intersection(ctx_tokens)
            total_overlap = self_overlap | ctx_overlap

            # 조건 A: 자체 1개+ & 합산 2개+
            if len(self_overlap) >= 1 and len(total_overlap) >= 2:
                selected.append(el)
                selected_ids.add(el.id)
                rescued += 1
                continue

            # 조건 B: container/context가 goal quoted phrase 포함 → 해당 팝업/카드 안의 모든 interactive 요소 rescue
            phrase_in_ctx = any(p and p in ctx_blob for p in _norm_phrases)
            if phrase_in_ctx:
                selected.append(el)
                selected_ids.add(el.id)
                rescued += 1
                continue

            # 조건 C: 자체 텍스트에 goal long token이 substring으로 포함 (조사 변형 대응: 시간표→시간표에서)
            substr_self = sum(1 for t in _long_tokens if t in self_blob)
            substr_ctx = sum(1 for t in _long_tokens if t in ctx_blob)
            if substr_self >= 1 and (substr_self + substr_ctx) >= 2:
                selected.append(el)
                selected_ids.add(el.id)
                rescued += 1
                continue

        # 현재 목적지 surface 안의 목표 증거/이웃 액션은 top-N 밖이어도 보존
        neighbor_limit = max(4, dom_limit // 20)
        rescued_neighbors = 0
        for index, el in enumerate(elements):
            if rescued_neighbors >= neighbor_limit:
                break
            tags = set(semantic_tag_cache.get(int(getattr(el, "id", -1)), []) or [])
            if "target_match" not in tags or _is_source_like_element(agent, el):
                continue
            if el.id not in selected_ids:
                selected.append(el)
                selected_ids.add(el.id)
                rescued_neighbors += 1
            neighbor_window_start = max(0, index - 2)
            neighbor_window_end = min(
                len(elements),
                index + (7 if _is_source_like_element(agent, el) else 3),
            )
            for neighbor in elements[neighbor_window_start:neighbor_window_end]:
                neighbor_role = str(getattr(neighbor, "role", "") or "").strip().lower()
                neighbor_tag = str(getattr(neighbor, "tag", "") or "").strip().lower()
                neighbor_tags = set(semantic_tag_cache.get(int(getattr(neighbor, "id", -1)), []) or [])
                if neighbor.id in selected_ids:
                    continue
                if _is_source_like_element(agent, neighbor):
                    continue
                if (
                    neighbor_role in {"button", "link", "tab", "menuitem", "option"}
                    or neighbor_tag in {"button", "a"}
                    or "active_surface_action" in neighbor_tags
                ):
                    selected.append(neighbor)
                    selected_ids.add(neighbor.id)
                    rescued_neighbors += 1
                    if rescued_neighbors >= neighbor_limit:
                        break

    if selected:
        lines.append("## 구조화 보조 힌트")

    for el in selected:
        el_ref = getattr(el, "ref_id", None)
        if el_ref:
            parts = [f'[ref={el_ref}] <{el.tag}>']
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
        semantic_tags = semantic_tag_cache.get(int(getattr(el, "id", -1)), []) or []
        if semantic_tags:
            parts.append(f'semantics=[{" | ".join(semantic_tags[:6])}]')
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
        if getattr(el, "selected_value", None):
            parts.append(f'selected="{getattr(el, "selected_value", "")}"')
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
        grp_ref = getattr(el, "ref_id", None)
        if grp_ref:
            bucket["items"].append(f'[ref={grp_ref} {el.text or el.aria_label or el.tag}]')
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
