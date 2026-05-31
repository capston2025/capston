from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .goal_completion_helpers import evaluate_goal_completion_judge
from .goal_policy_phase_runtime import goal_phase_intent
from .models import ActionDecision, ActionType, DOMElement, GoalResult, StepResult, TestGoal


def _emit_reason(agent: Any, code: str) -> None:
    if not code:
        return
    recorder = getattr(agent, "_record_reason_code", None)
    if callable(recorder):
        recorder(code)


def _strong_state_progress(state_change: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state_change, dict):
        return False
    keys = (
        "url_changed",
        "target_visibility_changed",
        "target_value_changed",
        "target_value_matches",
        "counter_changed",
        "number_tokens_changed",
        "status_text_changed",
        "list_count_changed",
        "interactive_count_changed",
        "modal_count_changed",
        "backdrop_count_changed",
        "dialog_count_changed",
        "modal_state_changed",
        "auth_state_changed",
        "scroll_position_changed",
        "text_digest_changed",
        "nav_detected",
        "popup_detected",
        "new_page_detected",
        "dialog_detected",
    )
    return any(bool(state_change.get(key)) for key in keys)


def _is_openclaw_backend_state_change(state_change: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state_change, dict):
        return False
    return str(state_change.get("backend") or "").strip().lower() == "openclaw"


def _commit_verification_failed(state_change: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state_change, dict):
        return False
    return bool(state_change.get("commit_verification_failed") or state_change.get("commit_pending"))


def _post_action_observation_deferred(state_change: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state_change, dict):
        return False
    return bool(state_change.get("post_action_observation_deferred") or state_change.get("backend_pending_observation"))


def _sorted_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized = [str(item).strip() for item in value if str(item).strip()]
    normalized.sort()
    return normalized[:100]


def _strong_evidence_progress(before_evidence: Optional[Dict[str, Any]], after_evidence: Optional[Dict[str, Any]]) -> bool:
    before = before_evidence if isinstance(before_evidence, dict) else {}
    after = after_evidence if isinstance(after_evidence, dict) else {}
    flags = (
        _sorted_text_list(before.get("live_texts")) != _sorted_text_list(after.get("live_texts")),
        _sorted_text_list(before.get("counters")) != _sorted_text_list(after.get("counters")),
        _sorted_text_list(before.get("number_tokens")) != _sorted_text_list(after.get("number_tokens")),
        int(before.get("list_count", 0) or 0) != int(after.get("list_count", 0) or 0),
        int(before.get("interactive_count", 0) or 0) != int(after.get("interactive_count", 0) or 0),
        int(before.get("modal_count", 0) or 0) != int(after.get("modal_count", 0) or 0),
        int(before.get("backdrop_count", 0) or 0) != int(after.get("backdrop_count", 0) or 0),
        int(before.get("dialog_count", 0) or 0) != int(after.get("dialog_count", 0) or 0),
        bool(before.get("modal_open")) != bool(after.get("modal_open")),
        bool(before.get("login_visible")) != bool(after.get("login_visible")),
        bool(before.get("logout_visible")) != bool(after.get("logout_visible")),
        str(before.get("text_digest", "")) != str(after.get("text_digest", "")),
    )
    return any(flags)


def _container_source_summary(dom_elements: List[DOMElement]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for item in dom_elements:
        source = str(getattr(item, "container_source", None) or "").strip()
        if not source:
            continue
        summary[source] = int(summary.get(source, 0)) + 1
    return summary


def _prime_dom_cache_from_backend_snapshot(
    *,
    agent: Any,
    backend_snapshot: Dict[str, Any],
    post_dom: List[DOMElement],
) -> None:
    if not isinstance(backend_snapshot, dict) or not post_dom:
        return
    if bool(backend_snapshot.get("scope_applied")):
        return
    try:
        generation = int(getattr(agent, "_dom_cache_generation", 0) or 0)
        session_id = str(getattr(agent, "session_id", "") or "default")
        snapshot_id = str(backend_snapshot.get("snapshot_id") or "").strip()
        dom_hash = str(backend_snapshot.get("dom_hash") or "").strip()
        epoch = int(backend_snapshot.get("epoch") or 0)
        active_url = str(
            backend_snapshot.get("url")
            or backend_snapshot.get("current_url")
            or getattr(agent, "_active_url", "")
            or ""
        )
        context_snapshot = (
            backend_snapshot.get("context_snapshot")
            if isinstance(backend_snapshot.get("context_snapshot"), dict)
            else {}
        )
        role_snapshot = (
            backend_snapshot.get("role_snapshot")
            if isinstance(backend_snapshot.get("role_snapshot"), dict)
            else {}
        )
        elements_by_ref = (
            backend_snapshot.get("elements_by_ref")
            if isinstance(backend_snapshot.get("elements_by_ref"), dict)
            else {}
        )
        evidence = (
            backend_snapshot.get("evidence")
            if isinstance(backend_snapshot.get("evidence"), dict)
            else {}
        )
        source_summary = _container_source_summary(post_dom)

        if snapshot_id:
            agent._active_snapshot_id = snapshot_id
        agent._active_dom_hash = dom_hash or str(getattr(agent, "_active_dom_hash", "") or "")
        agent._active_snapshot_epoch = epoch or int(getattr(agent, "_active_snapshot_epoch", 0) or 0)
        agent._active_url = active_url
        agent._active_scoped_container_ref = ""
        agent._last_context_snapshot = dict(context_snapshot or {})
        agent._last_role_snapshot = dict(role_snapshot or {})
        agent._last_snapshot_elements_by_ref = dict(elements_by_ref or {})
        agent._last_snapshot_evidence = dict(evidence or {})
        agent._last_container_source_summary = dict(source_summary or {})
        agent._dom_analyze_cache = {
            "key": (generation, session_id, "", ""),
            "elements": list(post_dom),
            "snapshot_id": str(getattr(agent, "_active_snapshot_id", "") or ""),
            "dom_hash": str(getattr(agent, "_active_dom_hash", "") or ""),
            "epoch": int(getattr(agent, "_active_snapshot_epoch", 0) or 0),
            "active_url": active_url,
            "active_scope": "",
            "context_snapshot": dict(context_snapshot or {}),
            "role_snapshot": dict(role_snapshot or {}),
            "elements_by_ref": dict(elements_by_ref or {}),
            "evidence": dict(evidence or {}),
            "container_source_summary": dict(source_summary or {}),
        }
    except Exception:
        return


def _is_weak_dom_only_change(
    *,
    before_count: int,
    after_count: int,
    before_signature: Any,
    after_signature: Any,
) -> bool:
    if before_signature == after_signature:
        return True
    if abs(int(after_count) - int(before_count)) <= 12:
        return True
    return False


def _should_attempt_post_action_judge(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    post_dom: List[DOMElement],
) -> bool:
    if not bool(success and changed):
        return False
    if decision.action in {ActionType.WAIT, ActionType.SCROLL}:
        return False
    if not post_dom:
        return False

    direction = str(getattr(agent, "_goal_constraints", {}).get("mutation_direction") or "").strip().lower()
    semantics = getattr(agent, "_goal_semantics", None)
    goal_kind = str(getattr(semantics, "goal_kind", "") or "").strip().lower()
    mutate_required = bool(getattr(semantics, "mutate_required", False))
    has_destination_terms = bool(getattr(agent, "_goal_destination_terms", lambda _goal: [])(goal))

    return bool(
        direction in {"increase", "decrease", "clear"}
        or goal_kind in {"add_to_list", "remove_from_list", "clear_list", "apply_selection"}
        or mutate_required
        or has_destination_terms
        or _is_readonly_navigation_post_action_judge_candidate(
            agent=agent,
            goal=goal,
            decision=decision,
            direction=direction,
            mutate_required=mutate_required,
        )
        or _is_readonly_search_post_action_judge_candidate(
            agent=agent,
            goal=goal,
            decision=decision,
            direction=direction,
            mutate_required=mutate_required,
        )
    )


def _goal_text_for_post_action_judge(agent: Any, goal: TestGoal) -> str:
    goal_text_blob = getattr(agent, "_goal_text_blob", None)
    if callable(goal_text_blob):
        try:
            return str(goal_text_blob(goal) or "")
        except Exception:
            pass
    fields: List[str] = [
        str(getattr(goal, "name", "") or ""),
        str(getattr(goal, "description", "") or ""),
    ]
    fields.extend(str(item or "") for item in list(getattr(goal, "success_criteria", []) or []))
    return " ".join(item.strip() for item in fields if item.strip())


def _is_readonly_navigation_post_action_judge_candidate(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    direction: str,
    mutate_required: bool,
) -> bool:
    if decision.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.NAVIGATE}:
        return False
    constraints = getattr(agent, "_goal_constraints", {}) if isinstance(getattr(agent, "_goal_constraints", {}), dict) else {}
    if bool(constraints.get("require_no_navigation")):
        return False
    if direction in {"increase", "decrease", "clear"} or mutate_required:
        return False

    normalize = getattr(agent, "_normalize_text", None)
    if callable(normalize):
        goal_blob = str(normalize(_goal_text_for_post_action_judge(agent, goal)) or "")
    else:
        goal_blob = _goal_text_for_post_action_judge(agent, goal).strip().lower()
    if not goal_blob:
        return False

    high_risk_tokens = (
        "로그인",
        "회원가입",
        "인증",
        "비밀번호",
        "login",
        "sign in",
        "signup",
        "password",
        "결제",
        "구매",
        "checkout",
        "payment",
        "재생",
        "시청",
        "play",
        "watch",
        "listen",
    )
    if any(token in goal_blob for token in high_risk_tokens):
        return False

    navigation_tokens = (
        "이동",
        "진입",
        "열고",
        "열어",
        "페이지로",
        "페이지에서",
        "navigate",
        "navigation",
        "go to",
        "open ",
        "visit",
    )
    observation_tokens = (
        "확인",
        "보이는지",
        "보이고",
        "보이며",
        "표시",
        "목록",
        "리스트",
        "list",
        "visible",
        "shown",
        "present",
    )
    return bool(
        any(token in goal_blob for token in navigation_tokens)
        and any(token in goal_blob for token in observation_tokens)
    )


def _is_readonly_search_post_action_judge_candidate(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    direction: str,
    mutate_required: bool,
) -> bool:
    if decision.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.SELECT}:
        return False
    constraints = getattr(agent, "_goal_constraints", {}) if isinstance(getattr(agent, "_goal_constraints", {}), dict) else {}
    if direction in {"increase", "decrease", "clear"} or mutate_required:
        return False
    if bool(constraints.get("requires_test_credentials")):
        return False

    normalize = getattr(agent, "_normalize_text", None)
    if callable(normalize):
        goal_blob = str(normalize(_goal_text_for_post_action_judge(agent, goal)) or "")
    else:
        goal_blob = _goal_text_for_post_action_judge(agent, goal).strip().lower()
    if not goal_blob:
        return False

    high_risk_tokens = (
        "로그인",
        "회원가입",
        "인증",
        "비밀번호",
        "login",
        "sign in",
        "signup",
        "password",
        "결제",
        "구매",
        "checkout",
        "payment",
    )
    if any(token in goal_blob for token in high_risk_tokens):
        return False

    search_tokens = ("검색", "필터", "search", "filter")
    result_tokens = ("결과", "목록", "리스트", "result", "results", "list")
    observation_tokens = ("확인", "보이는지", "표시", "보이고", "visible", "shown", "present")
    return bool(
        any(token in goal_blob for token in search_tokens)
        and any(token in goal_blob for token in result_tokens)
        and any(token in goal_blob for token in observation_tokens)
    )


def _evaluate_post_action_judge_completion(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    post_dom: List[DOMElement],
) -> Optional[str]:
    if not _should_attempt_post_action_judge(
        agent=agent,
        goal=goal,
        decision=decision,
        success=success,
        changed=changed,
        post_dom=post_dom,
    ):
        return None

    synthetic_decision = ActionDecision(
        action=decision.action,
        ref_id=decision.ref_id,
        element_id=decision.element_id,
        value=decision.value,
        reasoning=decision.reasoning,
        confidence=max(float(decision.confidence or 0.0), 0.75),
        is_goal_achieved=True,
        goal_achievement_reason=(
            str(decision.goal_achievement_reason or "").strip()
            or str(decision.reasoning or "").strip()
            or "직전 action 이후 현재 DOM 기준 목표 완료 여부를 다시 판정합니다."
        ),
    )
    return evaluate_goal_completion_judge(
        agent,
        goal=goal,
        decision=synthetic_decision,
        dom_elements=post_dom,
    )


def _evaluate_deferred_action_goal_completion(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    post_dom: List[DOMElement],
) -> Optional[str]:
    if not bool(success and decision.is_goal_achieved):
        return None
    if decision.action == ActionType.WAIT:
        return None

    validator = getattr(agent, "_validate_goal_achievement_claim", None)
    if callable(validator):
        is_valid, invalid_reason = validator(
            goal=goal,
            decision=decision,
            dom_elements=post_dom or [],
        )
        if not is_valid:
            log = getattr(agent, "_log", None)
            if callable(log):
                log(f"⚠️ 실행 후 목표 달성 판정 보류: {invalid_reason}")
            feedback = getattr(agent, "_action_feedback", None)
            if isinstance(feedback, list):
                feedback.append(f"실행 후 목표 달성 판정 보류: {invalid_reason}")
                if len(feedback) > 10:
                    del feedback[:-10]
            return None

    return (
        str(decision.goal_achievement_reason or "").strip()
        or str(decision.reasoning or "").strip()
        or "마지막 실행 액션이 성공하여 목표를 완료로 판정했습니다."
    )


def _evaluate_inspect_action_goal_completion(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    post_dom: List[DOMElement],
) -> Optional[str]:
    if not bool(success) or decision.action != ActionType.INSPECT:
        return None

    state_change = (
        dict(getattr(getattr(agent, "_last_exec_result", None), "state_change", {}) or {})
        if getattr(agent, "_last_exec_result", None) is not None
        else {}
    )
    inspection_summary = str(state_change.get("inspection_summary") or "").strip()
    inspection = state_change.get("inspection") if isinstance(state_change.get("inspection"), dict) else {}
    inspection_text_parts = [
        inspection_summary,
        str(inspection.get("title") or "").strip() if isinstance(inspection, dict) else "",
        str(inspection.get("bodyText") or "").strip() if isinstance(inspection, dict) else "",
    ]
    inspection_blob = " ".join(part for part in inspection_text_parts if part).strip()
    if not inspection_blob:
        return None

    synthetic_decision = decision.model_copy(
        update={
            "confidence": max(float(decision.confidence or 0.0), 0.75),
            "is_goal_achieved": False,
            "goal_achievement_reason": (
                str(decision.goal_achievement_reason or "").strip()
                or f"inspect 결과에서 목표 상태가 확인되었습니다: {inspection_summary or inspection_blob[:180]}"
            ),
            "reasoning": " ".join(
                part
                for part in (
                    str(decision.reasoning or "").strip(),
                    f"inspect 결과: {inspection_blob}",
                )
                if part
            ),
        }
    )
    reasoning_completion = getattr(agent, "_evaluate_reasoning_only_wait_completion", None)
    if callable(reasoning_completion):
        reason = reasoning_completion(
            goal=goal,
            decision=synthetic_decision,
            dom_elements=post_dom or [],
        )
        if reason:
            return reason
    return None


def _selected_element_for_decision(decision: ActionDecision, dom_elements: List[DOMElement]) -> Optional[DOMElement]:
    ref_id = str(getattr(decision, "ref_id", "") or "").strip()
    if ref_id:
        for el in dom_elements or []:
            if str(getattr(el, "ref_id", "") or "").strip() == ref_id:
                return el
    element_id = getattr(decision, "element_id", None)
    if element_id is not None:
        try:
            wanted = int(element_id)
        except Exception:
            wanted = None
        if wanted is not None:
            for el in dom_elements or []:
                if int(getattr(el, "id", -1)) == wanted:
                    return el
    return None


def evaluate_post_action_progress(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    before_signature: Any,
    dom_elements: List[DOMElement],
    step_count: int,
    steps: List[StepResult],
    start_time: float,
) -> Dict[str, Any]:
    if bool(success):
        setattr(agent, "_last_action_selected_element", _selected_element_for_decision(decision, dom_elements))
        setattr(agent, "_last_action_decision", decision)
    else:
        setattr(agent, "_last_action_selected_element", None)
        setattr(agent, "_last_action_decision", decision)

    before_evidence = (
        dict(agent._last_snapshot_evidence)
        if isinstance(getattr(agent, "_last_snapshot_evidence", None), dict)
        else {}
    )
    before_modal_open = bool(before_evidence.get("modal_open"))
    decision_reasoning = str(getattr(decision, "reasoning", "") or "").lower()
    decision_close_intent = bool(
        any(
            token in decision_reasoning
            for token in (
                "닫",
                "close",
                "dismiss",
                "종료",
                "x 버튼",
                "우상단 x",
            )
        )
    )
    state_change = agent._last_exec_result.state_change if agent._last_exec_result else None
    observation_deferred = _post_action_observation_deferred(state_change)
    backend_snapshot = (
        dict(getattr(agent, "_last_backend_post_action_snapshot", None) or {})
        if isinstance(getattr(agent, "_last_backend_post_action_snapshot", None), dict)
        else {}
    )
    backend_post_dom_raw = backend_snapshot.get("dom_elements") if isinstance(backend_snapshot.get("dom_elements"), list) else []
    backend_post_dom: List[DOMElement] = []
    for item in backend_post_dom_raw:
        if isinstance(item, DOMElement):
            backend_post_dom.append(item)
        elif isinstance(item, dict):
            try:
                backend_post_dom.append(DOMElement(**item))
            except Exception:
                continue
    backend_after_evidence = backend_snapshot.get("evidence") if isinstance(backend_snapshot.get("evidence"), dict) else {}
    post_dom_from_backend_snapshot = False
    if backend_post_dom:
        post_dom = backend_post_dom
        post_dom_from_backend_snapshot = True
        after_evidence = dict(backend_after_evidence)
    elif observation_deferred:
        post_dom = []
        after_evidence = dict(before_evidence)
    else:
        post_dom = agent._analyze_dom()
        after_evidence = (
            dict(agent._last_snapshot_evidence)
            if isinstance(getattr(agent, "_last_snapshot_evidence", None), dict)
            else {}
        )
    before_auth_prompt_visible = bool(before_evidence.get("auth_prompt_visible"))
    after_auth_prompt_visible = bool(after_evidence.get("auth_prompt_visible"))
    after_modal_open = bool(after_evidence.get("modal_open"))
    if before_modal_open and after_modal_open:
        agent._modal_opened_once = True
    elif (not before_modal_open) and after_modal_open:
        agent._modal_opened_once = True

    refreshed_metric = agent._estimate_goal_metric_from_dom(post_dom) if post_dom else None
    if refreshed_metric is not None:
        agent._goal_metric_value = refreshed_metric
    commit_failed = _commit_verification_failed(state_change)
    current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
    current_phase_intent = str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase))
    recent_exec = getattr(agent, "_last_exec_result", None)
    close_transition_signal = bool(
        isinstance(state_change, dict)
        and (
            bool(state_change.get("modal_state_changed"))
            or bool(state_change.get("modal_count_changed"))
            or bool(state_change.get("backdrop_count_changed"))
            or bool(state_change.get("dialog_count_changed"))
        )
    )
    modal_visibility_changed = before_modal_open != after_modal_open
    auth_prompt_visibility_changed = before_auth_prompt_visible != after_auth_prompt_visible
    changed_by_state = _strong_state_progress(state_change)
    openclaw_backend_state = _is_openclaw_backend_state_change(state_change)
    openclaw_backend_progress = bool(openclaw_backend_state and bool(state_change.get("backend_progress")))
    if commit_failed:
        changed_by_state = False
        openclaw_backend_progress = False
    openclaw_backend_provisional = bool(
        openclaw_backend_state
        and not commit_failed
        and bool(getattr(recent_exec, "success", False))
        and bool(getattr(recent_exec, "effective", False))
        and str(getattr(recent_exec, "reason_code", "") or "").strip().lower() == "ok"
        and not openclaw_backend_progress
        and decision.action in {ActionType.FILL, ActionType.TYPE, ActionType.SELECT}
    )
    if openclaw_backend_provisional and not changed_by_state:
        changed_by_state = True
    changed_by_evidence = _strong_evidence_progress(before_evidence, after_evidence)
    if commit_failed:
        changed_by_evidence = False
    after_signature = agent._dom_progress_signature(post_dom) if post_dom else before_signature
    changed_by_dom = False
    if bool(post_dom) and before_signature != after_signature:
        weak_dom_only = _is_weak_dom_only_change(
            before_count=len(dom_elements),
            after_count=len(post_dom),
            before_signature=before_signature,
            after_signature=after_signature,
        )
        changed_by_dom = not weak_dom_only
    if commit_failed:
        changed_by_dom = False
        modal_visibility_changed = False
        auth_prompt_visibility_changed = False
    changed = bool(
        changed_by_state
        or changed_by_evidence
        or changed_by_dom
        or modal_visibility_changed
        or auth_prompt_visibility_changed
    )
    if (
        bool(success)
        and not changed
        and not commit_failed
        and not observation_deferred
        and decision.action in {ActionType.CLICK, ActionType.TYPE, ActionType.PRESS, ActionType.SELECT}
    ):
        time.sleep(0.8)
        settled_dom = agent._analyze_dom(scope_container_ref_id="")
        if settled_dom:
            settled_evidence = (
                dict(agent._last_snapshot_evidence)
                if isinstance(getattr(agent, "_last_snapshot_evidence", None), dict)
                else {}
            )
            settled_modal_open = bool(settled_evidence.get("modal_open"))
            settled_auth_prompt_visible = bool(settled_evidence.get("auth_prompt_visible"))
            settled_signature = agent._dom_progress_signature(settled_dom)
            settled_changed_by_evidence = _strong_evidence_progress(before_evidence, settled_evidence)
            settled_changed_by_dom = False
            if before_signature != settled_signature:
                settled_changed_by_dom = not _is_weak_dom_only_change(
                    before_count=len(dom_elements),
                    after_count=len(settled_dom),
                    before_signature=before_signature,
                    after_signature=settled_signature,
                )
            if (
                settled_changed_by_evidence
                or settled_changed_by_dom
                or settled_modal_open != before_modal_open
                or settled_auth_prompt_visible != before_auth_prompt_visible
            ):
                post_dom = settled_dom
                after_evidence = settled_evidence
                after_modal_open = settled_modal_open
                after_auth_prompt_visible = settled_auth_prompt_visible
                after_signature = settled_signature
                post_dom_from_backend_snapshot = False
                modal_visibility_changed = before_modal_open != after_modal_open
                auth_prompt_visibility_changed = before_auth_prompt_visible != after_auth_prompt_visible
                changed = True
    if post_dom_from_backend_snapshot and changed:
        _prime_dom_cache_from_backend_snapshot(
            agent=agent,
            backend_snapshot=backend_snapshot,
            post_dom=post_dom,
        )
    if openclaw_backend_progress:
        _emit_reason(agent, "openclaw_backend_progress")
    elif openclaw_backend_provisional:
        _emit_reason(agent, "openclaw_backend_effective")
    elif changed_by_state:
        _emit_reason(agent, "progress_state_change")
    elif changed_by_evidence:
        _emit_reason(agent, "progress_evidence_delta")
    elif modal_visibility_changed:
        _emit_reason(agent, "progress_modal_visibility")
    elif auth_prompt_visibility_changed:
        _emit_reason(agent, "progress_auth_prompt_visibility")
    elif changed_by_dom:
        _emit_reason(agent, "progress_dom_signature")
    elif observation_deferred:
        _emit_reason(agent, "post_action_observation_deferred")
    elif (
        bool(success)
        and isinstance(state_change, dict)
        and bool(state_change.get("effective"))
    ):
        # OpenClaw-style guard: weak effective(관측상 약한 변화)는 루프 리셋 신호로 쓰지 않는다.
        _emit_reason(agent, "weak_effective_ignored")
    if decision_close_intent and bool(success):
        # close intent 액션이 실제로 실행됐다면, evidence 지연/누락이 있더라도
        # "모달이 열린 상태를 다루는 흐름"으로 간주해 종료 판정 누락을 줄인다.
        agent._modal_opened_once = True
        if bool(changed):
            agent._close_intent_success_once = True
            if decision.action == ActionType.CLICK:
                agent._close_click_success_once = True
    if (
        bool(getattr(agent, "_modal_opened_once", False))
        and (not after_modal_open)
        and (
            before_modal_open
            or close_transition_signal
            or (decision_close_intent and bool(success) and bool(changed))
        )
    ):
        agent._modal_closed_after_open = True

    if bool(agent._goal_constraints.get("require_no_navigation")) and isinstance(state_change, dict):
        if bool(state_change.get("url_changed")):
            agent._log("🧱 제약 가드: '페이지 이동 없이' 목표라 URL 변경 액션은 진행으로 인정하지 않습니다.")
            changed = False
            start_url = str(goal.start_url or "").strip()
            if start_url:
                agent._log("↩️ 페이지 고정 제약 복구: 시작 URL로 복귀합니다.")
                _ = agent._execute_action("goto", url=start_url)
                time.sleep(0.8)
                recovered_dom = agent._analyze_dom()
                if recovered_dom:
                    post_dom = recovered_dom

    terminal_result: Optional[GoalResult] = None
    if terminal_result is None:
        deferred_reason = _evaluate_deferred_action_goal_completion(
            agent=agent,
            goal=goal,
            decision=decision,
            success=success,
            post_dom=post_dom or [],
        )
        if deferred_reason:
            _emit_reason(agent, "goal_achievement_after_action")
            agent._log(f"✅ 목표 달성! 이유: {deferred_reason}")
            terminal_result = GoalResult(
                goal_id=goal.id,
                goal_name=goal.name,
                success=True,
                steps_taken=steps,
                total_steps=step_count,
                final_reason=deferred_reason,
                duration_seconds=time.time() - start_time,
            )
            agent._record_goal_summary(
                goal=goal,
                status="success",
                reason=terminal_result.final_reason,
                step_count=step_count,
                duration_seconds=terminal_result.duration_seconds,
            )
    if terminal_result is None:
        target_reason = agent._evaluate_goal_target_completion(
            goal=goal,
            dom_elements=post_dom or [],
        )
        if target_reason:
            _emit_reason(agent, "context_target_selected")
            agent._log(f"✅ 목표 달성! 이유: {target_reason}")
            terminal_result = GoalResult(
                goal_id=goal.id,
                goal_name=goal.name,
                success=True,
                steps_taken=steps,
                total_steps=step_count,
                final_reason=target_reason,
                duration_seconds=time.time() - start_time,
            )
            agent._record_goal_summary(
                goal=goal,
                status="success",
                reason=terminal_result.final_reason,
                step_count=step_count,
                duration_seconds=terminal_result.duration_seconds,
            )
    if terminal_result is None:
        inspect_reason = _evaluate_inspect_action_goal_completion(
            agent=agent,
            goal=goal,
            decision=decision,
            success=success,
            post_dom=post_dom or [],
        )
        if inspect_reason:
            _emit_reason(agent, "inspect_goal_completion")
            agent._log(f"✅ 목표 달성! 이유: {inspect_reason}")
            terminal_result = GoalResult(
                goal_id=goal.id,
                goal_name=goal.name,
                success=True,
                steps_taken=steps,
                total_steps=step_count,
                final_reason=inspect_reason,
                duration_seconds=time.time() - start_time,
            )
            agent._record_goal_summary(
                goal=goal,
                status="success",
                reason=terminal_result.final_reason,
                step_count=step_count,
                duration_seconds=terminal_result.duration_seconds,
            )
    if terminal_result is None:
        judge_reason = _evaluate_post_action_judge_completion(
            agent=agent,
            goal=goal,
            decision=decision,
            success=success,
            changed=changed,
            post_dom=post_dom or [],
        )
        if judge_reason:
            _emit_reason(agent, "post_action_judge_completion")
            agent._log(f"✅ 목표 달성! 이유: {judge_reason}")
            terminal_result = GoalResult(
                goal_id=goal.id,
                goal_name=goal.name,
                success=True,
                steps_taken=steps,
                total_steps=step_count,
                final_reason=judge_reason,
                duration_seconds=time.time() - start_time,
            )
            agent._record_goal_summary(
                goal=goal,
                status="success",
                reason=terminal_result.final_reason,
                step_count=step_count,
                duration_seconds=terminal_result.duration_seconds,
            )
    if terminal_result is None and decision.action == ActionType.WAIT:
        wait_completion_ready = getattr(agent, "_wait_completion_ready", None)
        wait_ready = bool(wait_completion_ready(post_dom or [])) if callable(wait_completion_ready) else bool(
            int(getattr(agent, "_consecutive_wait_count", 0) or 0) >= 2
        )
        current_phase_name = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
        if not wait_ready:
            wait_reason = None
        elif current_phase_name.startswith("precheck"):
            wait_reason = None
        else:
            wait_reason = agent._evaluate_wait_goal_completion(
                goal=goal,
                decision=decision,
                dom_elements=post_dom or [],
            )
        wait_reason_code = "wait_goal_completion"
        if wait_ready and not wait_reason:
            wait_reason = agent._evaluate_reasoning_only_wait_completion(
                goal=goal,
                decision=decision,
                dom_elements=post_dom or [],
            )
            wait_reason_code = "wait_reasoning_target_completion"
        if wait_reason:
            _emit_reason(agent, wait_reason_code)
            agent._log(f"✅ 목표 달성! 이유: {wait_reason}")
            terminal_result = GoalResult(
                goal_id=goal.id,
                goal_name=goal.name,
                success=True,
                steps_taken=steps,
                total_steps=step_count,
                final_reason=wait_reason,
                duration_seconds=time.time() - start_time,
            )
            agent._record_goal_summary(
                goal=goal,
                status="success",
                reason=terminal_result.final_reason,
                step_count=step_count,
                duration_seconds=terminal_result.duration_seconds,
            )
        else:
            agent._evidence_only_wait_count = 0
    if terminal_result is None:
        goal_blob = f"{goal.name} {goal.description}".strip().lower()
        close_keywords = ("닫", "close", "x 버튼", "우상단 x", "overlay", "오버레이", "modal", "모달")
        list_keywords = ("목록", "list", "게시판", "게시글", "board", "row")
        x_button_keywords = ("x 버튼", "x버튼", "우상단 x", "닫기 버튼", "close button", "close-btn")
        close_goal = any(token in goal_blob for token in close_keywords)
        list_goal = any(token in goal_blob for token in list_keywords)
        x_button_goal = any(token in goal_blob for token in x_button_keywords)
        has_list_like_dom = any(
            (str(getattr(el, "tag", "") or "").lower() in {"tr", "li", "article", "table", "tbody"})
            or (str(getattr(el, "role", "") or "").lower() in {"row", "listitem", "gridcell", "rowheader", "table", "grid"})
            for el in (post_dom or [])
        )
        close_success_gate = bool(getattr(agent, "_close_intent_success_once", False))
        if x_button_goal:
            close_success_gate = bool(getattr(agent, "_close_click_success_once", False))
        close_step_verified = bool(
            decision.action in {ActionType.CLICK, ActionType.PRESS}
            and decision_close_intent
            and bool(success)
            and bool(changed)
            and (not after_modal_open)
        )
        if (
            close_goal
            and list_goal
            and bool(getattr(agent, "_modal_opened_once", False))
            and bool(getattr(agent, "_modal_closed_after_open", False))
            and close_success_gate
            and (has_list_like_dom or close_step_verified)
        ):
            completion_reason = (
                "상세 오버레이 열기/닫기와 목록 복귀 상태가 모두 확인되어 목표를 완료로 판정했습니다."
            )
            agent._log(f"✅ 목표 달성! 이유: {completion_reason}")
            terminal_result = GoalResult(
                goal_id=goal.id,
                goal_name=goal.name,
                success=True,
                steps_taken=steps,
                total_steps=step_count,
                final_reason=completion_reason,
                duration_seconds=time.time() - start_time,
            )
            agent._record_goal_summary(
                goal=goal,
                status="success",
                reason=terminal_result.final_reason,
                step_count=step_count,
                duration_seconds=terminal_result.duration_seconds,
            )

    return {
        "post_dom": post_dom,
        "state_change": state_change,
        "changed": changed,
        "terminal_result": terminal_result,
    }
