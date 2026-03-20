from __future__ import annotations

import re
from typing import Any, List, Optional

from .evidence_bundle import EvidenceBundle
from .goal_policy_validators import run_policy_validators
from .models import DOMElement, TestGoal


def goal_quoted_terms(agent: Any, goal: TestGoal) -> List[str]:
    quoted: List[str] = []
    pattern = r"\"([^\"]{2,})\"|'([^']{2,})'"
    fields = [
        str(goal.description or ""),
        *(str(item or "") for item in (goal.success_criteria or [])),
        str(goal.name or ""),
    ]
    for field in fields:
        for match in re.finditer(pattern, str(field or "")):
            token = str(match.group(1) or match.group(2) or "").strip()
            if token:
                quoted.append(token)
    deduped: List[str] = []
    seen: set[str] = set()
    for token in quoted:
        norm = agent._normalize_text(token)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(token)
    return deduped[:6]


def goal_target_terms(agent: Any, goal: TestGoal) -> List[str]:
    semantics = getattr(agent, "_goal_semantics", None)
    if semantics and getattr(semantics, "target_terms", None):
        return list(semantics.target_terms[:6])
    explicit = [str(x).strip() for x in (agent._goal_constraints.get("target_terms") or []) if str(x).strip()]
    if explicit:
        return explicit[:6]
    fallback: List[str] = []
    for token in agent._extract_goal_query_tokens(goal):
        norm = agent._normalize_text(token)
        if len(norm) < 4 or norm.isdigit():
            continue
        fallback.append(token)
        if len(fallback) >= 4:
            break
    return fallback


def goal_destination_terms(agent: Any, goal: TestGoal) -> List[str]:
    semantics = getattr(agent, "_goal_semantics", None)
    if semantics and getattr(semantics, "destination_terms", None):
        return list(semantics.destination_terms[:8])
    goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
    groups = [
        ("위시리스트", ("위시리스트", "wishlist", "wish list")),
        ("장바구니", ("장바구니", "cart", "basket")),
        ("시간표", ("시간표", "timetable", "schedule")),
        ("선택목록", ("선택 목록", "선택목록", "selected list", "selected items")),
        ("내 목록", ("내 목록", "my list", "saved list")),
    ]
    matched: List[str] = []
    for canonical, hints in groups:
        if any(agent._normalize_text(hint) in goal_blob for hint in hints):
            matched.extend(hints)
            matched.append(canonical)
    deduped: List[str] = []
    seen: set[str] = set()
    for token in matched:
        norm = agent._normalize_text(token)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(token)
    return deduped[:8]


def build_goal_policy_evidence_bundle(
    agent: Any,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
    auth_prompt_visible: bool = False,
    modal_open: bool = False,
) -> Optional[EvidenceBundle]:
    semantics = getattr(agent, "_goal_semantics", None)
    if semantics is None:
        return None
    target_terms = [agent._normalize_text(term) for term in (semantics.target_terms or []) if str(term).strip()]
    destination_terms = [agent._normalize_text(term) for term in (semantics.destination_terms or []) if str(term).strip()]
    page_fragments: List[str] = []
    destination_anchor_found = False
    destination_surface_actionable = False
    target_in_destination = False
    target_hits: List[str] = []
    empty_state_visible = False
    target_action_cta_visible = False
    destination_reveal_action_available = False
    target_row_secondary_reveal_available = False
    scanned_elements: List[tuple[Any, str, bool, List[str], str, str, str, List[str], bool, bool, bool, str]] = []
    target_container_refs: set[str] = set()

    def _element_blob(el: DOMElement) -> str:
        labels = getattr(el, "group_action_labels", None) or []
        label_blob = " ".join(str(x or "") for x in labels if str(x or "").strip()) if isinstance(labels, list) else ""
        return agent._normalize_text(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", None) or ""),
                    str(getattr(el, "container_name", None) or ""),
                    str(getattr(el, "container_role", None) or ""),
                    str(getattr(el, "context_text", None) or ""),
                    label_blob,
                ]
            )
        )

    for el in dom_elements:
        if not bool(getattr(el, "is_visible", True)):
            continue
        blob = _element_blob(el)
        if not blob:
            continue
        page_fragments.append(blob)
        has_destination = bool(destination_terms) and any(term and term in blob for term in destination_terms)
        matched_targets = [term for term in target_terms if term and term in blob]
        role = str(getattr(el, "role", "") or "").lower()
        tag = str(getattr(el, "tag", "") or "").lower()
        container_role = str(getattr(el, "container_role", "") or "").lower()
        group_labels = getattr(el, "group_action_labels", None) or []
        is_actionable = bool(getattr(el, "is_enabled", True)) and (
            role in {"button", "link", "tab"}
            or tag in {"button", "a"}
        )
        remove_like = any(token in blob for token in ("삭제", "제거", "remove", "delete", "clear", "비우"))
        reveal_like = any(
            token in blob
            for token in (
                "위시리스트",
                "wishlist",
                "장바구니",
                "cart",
                "시간표",
                "timetable",
                "내 목록",
                "saved",
                "favorites",
                "선택 목록",
                "selected",
                "더보기",
                "show more",
                "view all",
                "expand",
                "펼치",
                "열기",
            )
        )
        container_ref = str(getattr(el, "container_ref_id", "") or "").strip()
        scanned_elements.append(
            (el, blob, has_destination, matched_targets, role, tag, container_role, group_labels, is_actionable, remove_like, reveal_like, container_ref)
        )
        if matched_targets and container_ref:
            target_container_refs.add(container_ref)
        if has_destination:
            destination_anchor_found = True
        destination_surface_like = has_destination and (
            bool(matched_targets)
            or any(token in blob for token in ("비어", "empty", "없음", "0개", "0학점", "총", "개", "학점"))
            or container_role in {"listitem", "row", "article", "region", "group"}
            or role in {"tabpanel", "region", "list", "listitem", "row", "grid", "table"}
            or tag in {"li", "tr", "section", "article", "table"}
            or (isinstance(group_labels, list) and len([x for x in group_labels if str(x or "").strip()]) >= 2)
            or (is_actionable and (remove_like or reveal_like))
        )
        if destination_surface_like:
            destination_surface_actionable = True
        if matched_targets:
            target_hits.extend(matched_targets)
        if has_destination and matched_targets:
            target_in_destination = True
        if is_actionable and remove_like and (has_destination or matched_targets):
            target_action_cta_visible = True
        if is_actionable and reveal_like and (has_destination or any(term and term in blob for term in destination_terms)):
            destination_reveal_action_available = True
        if has_destination and any(token in blob for token in ("비어", "empty", "없음", "0개", "0학점")):
            empty_state_visible = True

    for (
        _el,
        blob,
        _has_destination,
        matched_targets,
        _role,
        _tag,
        _container_role,
        group_labels,
        is_actionable,
        remove_like,
        _reveal_like,
        container_ref,
    ) in scanned_elements:
        same_target_container = bool(container_ref) and container_ref in target_container_refs
        if not same_target_container or not is_actionable:
            continue
        secondary_reveal_like = any(
            token in blob
            for token in (
                "더보기",
                "show more",
                "view all",
                "expand",
                "펼치",
                "열기",
                "menu",
                "옵션",
                "option",
                "more",
                "편집",
                "edit",
                "상세",
                "details",
                "⋯",
                "...",
            )
        )
        if remove_like:
            target_action_cta_visible = True
        if secondary_reveal_like or (isinstance(group_labels, list) and len([x for x in group_labels if str(x or "").strip()]) >= 2):
            target_row_secondary_reveal_available = True

    evidence = agent._last_snapshot_evidence if isinstance(agent._last_snapshot_evidence, dict) else {}
    text_digest = str(evidence.get("text_digest") or "").strip()
    if text_digest:
        page_fragments.append(text_digest)
    live_texts = evidence.get("live_texts") if isinstance(evidence.get("live_texts"), list) else []
    page_fragments.extend(str(item or "").strip() for item in live_texts[:12] if str(item or "").strip())
    page_blob = agent._normalize_text(" ".join(page_fragments))
    if destination_surface_actionable and any(token in page_blob for token in ("비어", "empty", "없음", "0개", "0학점")):
        empty_state_visible = True
    if destination_anchor_found:
        agent._goal_policy_destination_anchor_seen = True
    if target_in_destination:
        agent._goal_policy_target_seen_in_destination = True

    aggregate_metric = agent._estimate_goal_metric_from_dom(dom_elements) if dom_elements else None
    baseline_bundle = getattr(agent, "_goal_policy_baseline_evidence", None)
    if baseline_bundle is None:
        baseline_bundle = EvidenceBundle(
            raw={"auth_prompt_visible": auth_prompt_visible, "modal_open": modal_open},
            derived={
            "destination_anchor_found": destination_anchor_found,
            "target_in_destination": target_in_destination,
            "target_hits": list(dict.fromkeys(target_hits[:6])),
            "empty_state_visible": empty_state_visible,
                "target_seen_during_run": bool(getattr(agent, "_goal_policy_target_seen_in_destination", False)),
                "destination_anchor_seen_during_run": bool(getattr(agent, "_goal_policy_destination_anchor_seen", False)),
                "target_action_cta_visible": target_action_cta_visible,
                "destination_reveal_action_available": destination_reveal_action_available,
                "target_row_secondary_reveal_available": target_row_secondary_reveal_available,
            },
            baseline={},
            current={
                "aggregate_metric": aggregate_metric,
                "target_in_destination": target_in_destination,
                "destination_anchor_found": destination_anchor_found,
                "destination_surface_actionable": destination_surface_actionable,
                "empty_state_visible": empty_state_visible,
                "target_action_cta_visible": target_action_cta_visible,
            },
            delta={},
        )
        agent._goal_policy_baseline_evidence = baseline_bundle

    baseline_current = dict(getattr(baseline_bundle, "current", {}) or {})
    already_satisfied_pre_action = bool(
        baseline_current.get("target_in_destination")
        and baseline_current.get("destination_anchor_found")
        and baseline_current.get("destination_surface_actionable")
    )
    remediation_needed = bool(
        str(getattr(semantics, "remediation_trigger", "") or "").strip().lower() == "already_present"
        and already_satisfied_pre_action
    )
    baseline_metric = baseline_current.get("aggregate_metric")
    aggregate_metric_delta = None
    if isinstance(aggregate_metric, (int, float)) and isinstance(baseline_metric, (int, float)):
        aggregate_metric_delta = float(aggregate_metric) - float(baseline_metric)

    filter_report = getattr(agent, "_last_filter_semantic_report", None)
    filter_validation_passed = False
    if isinstance(filter_report, dict):
        summary = filter_report.get("summary") if isinstance(filter_report.get("summary"), dict) else {}
        failed_mandatory = int(summary.get("failed_mandatory_checks", summary.get("failed_checks", 0)) or 0)
        total_checks = int(summary.get("total_checks", 0) or 0)
        filter_validation_passed = total_checks > 0 and failed_mandatory == 0

    return EvidenceBundle(
        raw={
            "snapshot_id": agent._active_snapshot_id,
            "url": str(agent._active_url or ""),
            "page_text": page_blob,
            "auth_prompt_visible": auth_prompt_visible,
            "modal_open": modal_open,
            "aggregate_metric": aggregate_metric,
        },
        derived={
            "target_hits": list(dict.fromkeys(target_hits[:6])),
            "destination_anchor_found": destination_anchor_found,
            "destination_surface_actionable": destination_surface_actionable,
            "target_in_destination": target_in_destination,
            "already_satisfied_pre_action": already_satisfied_pre_action,
            "remediation_needed": remediation_needed,
            "already_satisfied": bool(target_in_destination and semantics.already_satisfied_ok and not semantics.mutate_required),
            "filter_validation_passed": filter_validation_passed,
            "empty_state_visible": empty_state_visible,
            "target_seen_during_run": bool(getattr(agent, "_goal_policy_target_seen_in_destination", False)),
            "destination_anchor_seen_during_run": bool(getattr(agent, "_goal_policy_destination_anchor_seen", False)),
            "target_action_cta_visible": target_action_cta_visible,
            "destination_reveal_action_available": destination_reveal_action_available,
            "target_row_secondary_reveal_available": target_row_secondary_reveal_available,
        },
        baseline=baseline_current,
        current={
            "aggregate_metric": aggregate_metric,
            "target_in_destination": target_in_destination,
            "destination_anchor_found": destination_anchor_found,
            "destination_surface_actionable": destination_surface_actionable,
            "empty_state_visible": empty_state_visible,
            "target_action_cta_visible": target_action_cta_visible,
            "target_row_secondary_reveal_available": target_row_secondary_reveal_available,
        },
        delta={"aggregate_metric_delta": aggregate_metric_delta},
    )


def run_goal_policy_closer(
    agent: Any,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
    auth_prompt_visible: bool = False,
    modal_open: bool = False,
) -> Optional[str]:
    policy = getattr(agent, "_goal_policy", None)
    semantics = getattr(agent, "_goal_semantics", None)
    if policy is None or semantics is None:
        return None
    evidence = build_goal_policy_evidence_bundle(
        agent,
        goal=goal,
        dom_elements=dom_elements,
        auth_prompt_visible=auth_prompt_visible,
        modal_open=modal_open,
    )
    if evidence is None:
        return None
    closer_result = policy.run_closer(
        getattr(agent, "_goal_policy_phase", "") or policy.initial_phase(semantics),
        agent,
        semantics,
        evidence,
        run_policy_validators(
            policy,
            getattr(agent, "_goal_policy_phase", "") or policy.initial_phase(semantics),
            agent,
            semantics,
            evidence,
        ),
    )
    if getattr(closer_result, "status", "") == "success":
        text = str(getattr(closer_result, "proof", "") or getattr(closer_result, "reason_code", "") or "").strip()
        return text or None
    return None
