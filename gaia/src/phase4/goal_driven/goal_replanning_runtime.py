from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .models import DOMElement, TestGoal

_DESTINATION_HINTS = (
    "시간표",
    "내 시간표",
    "온라인",
    "시간외",
    "미배정",
    "요일",
    "교시",
    "월",
    "화",
    "수",
    "목",
    "금",
    "토",
    "일",
)
_REMOVE_HINTS = ("삭제", "제거", "빼기", "remove", "delete", "취소")
_ADD_HINTS = ("바로 추가", "추가", "담기", "add", "apply")


def initialize_goal_replanning_state(agent: Any, goal: TestGoal) -> Dict[str, Any]:
    target_terms = _normalize_terms(agent, _goal_terms(agent, goal))
    destination_terms = _normalize_terms(agent, _goal_destinations(agent))
    state = {
        "membership_belief": "unknown",
        "membership_confidence": 0.0,
        "target_locus": "source",
        "subgoal": "locate_target",
        "target_terms": target_terms,
        "destination_terms": destination_terms,
        "proof": {
            "precheck_present": False,
            "precheck_absent": False,
            "remove_done": False,
            "add_pending": False,
            "add_done": False,
            "readd_pending": False,
            "readd_done": False,
            "final_present_verified": False,
        },
        "contradiction_signals": [],
        "updated_at": time.time(),
    }
    agent._goal_state_cache = state
    _sync_legacy_plan_fields(agent, state)
    return state


def sync_goal_replanning_state(
    agent: Any,
    *,
    goal: TestGoal,
    dom_elements: Optional[List[DOMElement]],
    current_phase: str = "",
    current_intent: str = "",
    event: str = "",
) -> Dict[str, Any]:
    state = getattr(agent, "_goal_state_cache", None)
    if not isinstance(state, dict):
        state = initialize_goal_replanning_state(agent, goal)

    target_terms = _normalize_terms(agent, state.get("target_terms") or _goal_terms(agent, goal))
    destination_terms = _normalize_terms(agent, state.get("destination_terms") or _goal_destinations(agent))
    proof = state.setdefault("proof", {})
    contradiction_signals = list(state.get("contradiction_signals") or [])
    elements = dom_elements if isinstance(dom_elements, list) else []

    target_in_destination = False
    strong_target_in_destination = False
    target_in_source = False
    remove_visible = False
    destination_row_visible = False

    for element in elements:
        if not bool(getattr(element, "is_visible", True)):
            continue
        self_blob = _element_self_blob(agent, element)
        row_local_blob = _element_row_local_blob(agent, element)
        destination_blob = _element_destination_blob(agent, element)
        blob = _element_blob(agent, element)
        if not (blob or row_local_blob or self_blob):
            continue
        row_like = _looks_like_destination_row(element)
        matches_target = any(term and term in self_blob for term in target_terms) or (
            row_like and any(term and term in row_local_blob for term in target_terms)
        )
        if not matches_target:
            continue
        matches_destination = any(term and (term in destination_blob or term in self_blob) for term in destination_terms) or any(
            hint in destination_blob for hint in _DESTINATION_HINTS
        )
        if matches_destination:
            target_in_destination = True
            if row_like:
                destination_row_visible = True
                strong_target_in_destination = True
        elif any(token in blob for token in _ADD_HINTS):
            target_in_source = True
        if any(token in blob for token in _REMOVE_HINTS):
            remove_visible = True
            if matches_destination:
                strong_target_in_destination = True

    event_norm = str(event or "").strip().lower()
    phase_norm = str(current_phase or "").strip().lower()
    intent_norm = str(current_intent or "").strip().lower()

    if event_norm == "precheck_present":
        proof["precheck_present"] = True
        _push_signal(contradiction_signals, "precheck_present")
    elif event_norm == "precheck_absent":
        proof["precheck_absent"] = True
    elif event_norm == "possible_present_noop":
        _push_signal(contradiction_signals, "possible_present_noop")

    if strong_target_in_destination and proof.get("precheck_absent") and not proof.get("add_done"):
        proof["precheck_present"] = True
        _push_signal(contradiction_signals, "destination_target_after_absent")

    if phase_norm == "verify_remediation_removal" and not target_in_destination:
        proof["remove_done"] = True
        _push_signal(contradiction_signals, "remove_verified_absent")

    if phase_norm == "verify_destination_membership" and event_norm == "action_ok" and not target_in_destination:
        if proof.get("remove_done"):
            proof["readd_pending"] = True
        else:
            proof["add_pending"] = True

    if phase_norm == "verify_destination_membership" and strong_target_in_destination:
        if proof.get("remove_done"):
            proof["readd_done"] = True
            proof["readd_pending"] = False
        else:
            proof["add_done"] = True
            proof["add_pending"] = False
        proof["final_present_verified"] = True

    belief = "unknown"
    confidence = 0.35
    target_locus = "source"
    subgoal = "locate_target"

    if proof.get("remove_done") and proof.get("readd_pending") and not target_in_destination:
        belief = "absent"
        confidence = 0.96
        target_locus = "destination"
        subgoal = "verify_final_presence"
    elif proof.get("remove_done") and not proof.get("readd_done"):
        belief = "absent"
        confidence = 0.95
        target_locus = "source"
        subgoal = "source_readd"
    elif proof.get("add_pending") and not target_in_destination:
        belief = "absent"
        confidence = 0.8
        target_locus = "destination"
        subgoal = "verify_final_presence"
    elif proof.get("precheck_present") or strong_target_in_destination:
        belief = "present"
        confidence = 0.9 if strong_target_in_destination else 0.72
        target_locus = "destination"
        if remove_visible:
            subgoal = "remove_membership"
        elif destination_row_visible:
            subgoal = "activate_destination_row"
        else:
            subgoal = "reveal_destination_row"
    elif _has_present_contradiction(contradiction_signals):
        belief = "unknown"
        confidence = 0.55
        target_locus = "destination" if (destination_row_visible or remove_visible) else "source"
        if remove_visible:
            subgoal = "remove_membership"
        elif destination_row_visible:
            subgoal = "activate_destination_row"
        else:
            subgoal = "verify_possible_present"
    elif proof.get("precheck_absent") or target_in_source:
        belief = "absent"
        confidence = 0.62 if proof.get("precheck_absent") else 0.5
        target_locus = "source"
        subgoal = "source_add"

    if proof.get("readd_done") and proof.get("final_present_verified"):
        belief = "present"
        confidence = 0.98
        target_locus = "destination"
        subgoal = "verify_final_presence"
    elif proof.get("add_done") and proof.get("final_present_verified"):
        belief = "present"
        confidence = max(confidence, 0.9)
        target_locus = "destination"
        subgoal = "verify_final_presence"

    if intent_norm == "auth":
        subgoal = "complete_auth"

    state["membership_belief"] = belief
    state["membership_confidence"] = confidence
    state["target_locus"] = target_locus
    state["subgoal"] = subgoal
    state["proof"] = proof
    state["target_terms"] = target_terms
    state["destination_terms"] = destination_terms
    state["contradiction_signals"] = contradiction_signals[-8:]
    state["updated_at"] = time.time()

    _sync_legacy_plan_fields(agent, state)
    return state


def _sync_legacy_plan_fields(agent: Any, state: Dict[str, Any]) -> None:
    proof = state.get("proof") or {}

    precheck_done = bool(proof.get("precheck_present") or proof.get("precheck_absent"))
    if proof.get("precheck_present"):
        precheck_result = "present"
    elif proof.get("precheck_absent"):
        precheck_result = "absent"
    else:
        precheck_result = ""

    setattr(agent, "_goal_plan_precheck_done", precheck_done)
    setattr(agent, "_goal_plan_precheck_result", precheck_result)
    setattr(agent, "_goal_plan_remediation_completed", bool(proof.get("remove_done")))


def _goal_terms(agent: Any, goal: TestGoal) -> List[str]:
    semantics = getattr(agent, "_goal_semantics", None)
    terms = list(getattr(semantics, "target_terms", []) or [])
    if not terms:
        terms = [str(getattr(goal, "name", "") or "")]
    return [str(term or "").strip() for term in terms if str(term or "").strip()]


def _goal_destinations(agent: Any) -> List[str]:
    semantics = getattr(agent, "_goal_semantics", None)
    terms = list(getattr(semantics, "destination_terms", []) or [])
    return [str(term or "").strip() for term in terms if str(term or "").strip()]


def _normalize_terms(agent: Any, values: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for value in values:
        norm = agent._normalize_text(str(value or ""))
        if norm and norm not in seen:
            normalized.append(norm)
            seen.add(norm)
    return normalized


def _element_blob(agent: Any, element: DOMElement) -> str:
    parts = [
        str(getattr(element, "text", "") or ""),
        str(getattr(element, "aria_label", "") or ""),
        str(getattr(element, "placeholder", "") or ""),
        str(getattr(element, "title", "") or ""),
        str(getattr(element, "role", "") or ""),
        str(getattr(element, "container_name", "") or ""),
        str(getattr(element, "context_text", "") or ""),
    ]
    group_actions = getattr(element, "group_action_labels", None)
    if isinstance(group_actions, list):
        parts.extend(str(item or "") for item in group_actions)
    return agent._normalize_text(" ".join(parts))


def _element_self_blob(agent: Any, element: DOMElement) -> str:
    return agent._normalize_text(
        " ".join(
            [
                str(getattr(element, "text", "") or ""),
                str(getattr(element, "aria_label", "") or ""),
                str(getattr(element, "title", "") or ""),
            ]
        )
    )


def _element_row_local_blob(agent: Any, element: DOMElement) -> str:
    return agent._normalize_text(
        " ".join(
            [
                str(getattr(element, "text", "") or ""),
                str(getattr(element, "aria_label", "") or ""),
                str(getattr(element, "title", "") or ""),
                str(getattr(element, "container_name", "") or ""),
                str(getattr(element, "context_text", "") or ""),
            ]
        )
    )


def _element_destination_blob(agent: Any, element: DOMElement) -> str:
    return agent._normalize_text(
        " ".join(
            [
                str(getattr(element, "container_name", "") or ""),
                str(getattr(element, "container_role", "") or ""),
                str(getattr(element, "context_text", "") or ""),
            ]
        )
    )


def _looks_like_destination_row(element: DOMElement) -> bool:
    role = str(getattr(element, "role", "") or "").strip().lower()
    tag = str(getattr(element, "tag", "") or "").strip().lower()
    return role in {"row", "listitem", "gridcell", "cell", "article"} or tag in {
        "li",
        "td",
        "tr",
        "article",
        "section",
    }


def _push_signal(signals: List[str], value: str) -> None:
    token = str(value or "").strip().lower()
    if not token:
        return
    if token in signals:
        signals.remove(token)
    signals.append(token)


def _has_present_contradiction(signals: List[str]) -> bool:
    return any(
        token in {"possible_present_noop", "destination_target_after_absent", "precheck_present"}
        for token in signals
    )
