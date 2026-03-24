from __future__ import annotations

import json
import os
import re
import time
from typing import List, Optional

from .goal_policy_phase_runtime import goal_phase_intent
from .goal_replanning_runtime import sync_goal_replanning_state
from .goal_kinds import GoalKind
from .models import ActionDecision, ActionType, DOMElement, TestGoal


def decide_next_action(
    agent,
    dom_elements: List[DOMElement],
    goal: TestGoal,
    screenshot: Optional[str] = None,
    memory_context: str = "",
) -> ActionDecision:
    agent._last_llm_trace = {
        "used_llm": False,
        "llm_ms": 0,
        "path": "deterministic_or_cached",
        "owner": "gaia_pre_llm",
    }
    current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
    current_phase_intent = str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase))
    goal_state = sync_goal_replanning_state(
        agent,
        goal=goal,
        dom_elements=dom_elements,
        current_phase=current_phase,
        current_intent=current_phase_intent,
        event="decision_turn",
    )

    def _label_blob(element: DOMElement) -> str:
        return agent._normalize_text(
            " ".join(
                [
                    str(getattr(element, "text", "") or ""),
                    str(getattr(element, "aria_label", None) or ""),
                    str(getattr(element, "placeholder", None) or ""),
                    str(getattr(element, "title", None) or ""),
                    str(getattr(element, "type", None) or ""),
                ]
            )
        )

    def _find_auth_phase_fallback() -> Optional[ActionDecision]:
        username_done = bool(getattr(agent, "_auth_identifier_done", False))
        password_done = bool(getattr(agent, "_auth_password_done", False))
        fill_memory = getattr(agent, "_auth_fill_memory", None)
        auth_test_data = (goal.test_data or {}) if isinstance(goal.test_data, dict) else {}
        identifier_value = str(
            auth_test_data.get("username")
            or auth_test_data.get("email")
            or auth_test_data.get("login_id")
            or auth_test_data.get("user_id")
            or ""
        )
        password_value = str(auth_test_data.get("password") or "")

        def _field_key(element: DOMElement) -> str:
            try:
                return str(
                    agent._element_ref_ids.get(element.id)
                    or agent._element_full_selectors.get(element.id)
                    or agent._element_selectors.get(element.id)
                    or _label_blob(element)
                    or ""
                ).strip()
            except Exception:
                return _label_blob(element)

        def _memory_has(kind: str, element: Optional[DOMElement], value: str) -> bool:
            if not isinstance(fill_memory, set) or element is None:
                return False
            key = _field_key(element)
            value_norm = agent._normalize_text(value)
            try:
                for entry in fill_memory:
                    if not (isinstance(entry, tuple) and len(entry) >= 3):
                        continue
                    if str(entry[0] or "") != kind:
                        continue
                    if key and str(entry[1] or "") != key:
                        continue
                    if value_norm and str(entry[2] or "") != value_norm:
                        continue
                    return True
            except Exception:
                return False
            return False

        def _planned_fill_sig(kind: str, element: Optional[DOMElement], value: str) -> tuple[str, int | None, str]:
            return (
                str(kind or ""),
                getattr(element, "id", None) if element is not None else None,
                agent._normalize_text(value),
            )

        if isinstance(fill_memory, set) and fill_memory:
            try:
                username_done = username_done or any(
                    isinstance(entry, tuple) and len(entry) >= 1 and str(entry[0] or "") == "identifier"
                    for entry in fill_memory
                )
                password_done = password_done or any(
                    isinstance(entry, tuple) and len(entry) >= 1 and str(entry[0] or "") == "password"
                    for entry in fill_memory
                )
            except Exception:
                pass
        login_button: Optional[DOMElement] = None
        username_field: Optional[DOMElement] = None
        password_field: Optional[DOMElement] = None
        for element in elements_for_prompt or dom_elements:
            if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
                continue
            blob = _label_blob(element)
            if element.tag in {"input", "textarea"}:
                if ("password" in blob or "비밀번호" in blob) and password_field is None:
                    password_field = element
                elif any(token in blob for token in ("아이디", "username", "email", "이메일", "user")) and username_field is None:
                    username_field = element
            elif (
                str(getattr(element, "role", "") or "").lower() in {"button", "link", "tab", "menuitem"}
                or str(getattr(element, "tag", "") or "").lower() in {"button", "a"}
            ):
                if any(token in blob for token in ("로그인", "login", "sign in", "signin")) and login_button is None:
                    login_button = element
        if username_field is not None and _memory_has("identifier", username_field, identifier_value):
            username_done = True
        if password_field is not None and _memory_has("password", password_field, password_value):
            password_done = True
        last_planned_fill = getattr(agent, "_auth_last_planned_fill", None)
        if username_field is not None and last_planned_fill == _planned_fill_sig("identifier", username_field, identifier_value):
            username_done = True
        if password_field is not None and last_planned_fill == _planned_fill_sig("password", password_field, password_value):
            password_done = True
        if auth_has_credentials:
            submit_attempted = bool(getattr(agent, "_auth_submit_attempted", False))
            last_submit_at = float(getattr(agent, "_last_auth_submit_at", 0.0) or 0.0)
            submit_attempts = int(getattr(agent, "_auth_submit_attempts", 0) or 0)
            if submit_attempted and last_submit_at > 0.0:
                since_submit = time.time() - last_submit_at
                settle_window = 3.0 if submit_attempts <= 1 else 8.0
                settle_ms = 900 if submit_attempts <= 1 else 2500
                if since_submit < settle_window:
                    return ActionDecision(
                        action=ActionType.WAIT,
                        value=json.dumps({"time_ms": settle_ms}),
                        reasoning="AUTH 단계 강제 규칙: 제출 직후에는 재입력보다 전환/리다이렉트 settle을 먼저 기다립니다.",
                        confidence=0.82,
                    )
            if not username_done and username_field is not None and identifier_value.strip():
                agent._auth_identifier_done = True
                agent._auth_last_planned_fill = _planned_fill_sig("identifier", username_field, identifier_value)
                return ActionDecision(
                    action=ActionType.FILL,
                    element_id=username_field.id,
                    value=identifier_value,
                    reasoning="AUTH 단계 강제 규칙: 닫기/X 대신 로그인 식별자 입력을 우선합니다.",
                    confidence=0.95,
                )
            if not password_done and password_field is not None and password_value.strip():
                agent._auth_password_done = True
                agent._auth_last_planned_fill = _planned_fill_sig("password", password_field, password_value)
                return ActionDecision(
                    action=ActionType.FILL,
                    element_id=password_field.id,
                    value=password_value,
                    reasoning="AUTH 단계 강제 규칙: 닫기/X 대신 비밀번호 입력을 우선합니다.",
                    confidence=0.95,
                )
            if (not username_done and username_field is None) or (not password_done and password_field is None):
                return ActionDecision(
                    action=ActionType.WAIT,
                    value='{"time_ms": 300}',
                    reasoning="AUTH 단계 강제 규칙: 제출보다 입력 필드 재획득이 우선이므로 최신 인증 UI를 다시 기다립니다.",
                    confidence=0.7,
                )
            last_submit_at = float(getattr(agent, "_last_auth_submit_at", 0.0) or 0.0)
            retry_cooldown = 4.0 if submit_attempts <= 1 else 8.0
            can_retry_submit = (
                login_button is not None
                and username_done
                and password_done
                and submit_attempts < 3
                and (not bool(getattr(agent, "_auth_submit_attempted", False)) or (time.time() - last_submit_at) >= retry_cooldown)
            )
            if can_retry_submit:
                submit_element_id = getattr(login_button, "id", None)
                if submit_element_id:
                    agent._auth_last_planned_fill = None
                    return ActionDecision(
                        action=ActionType.CLICK,
                        element_id=submit_element_id,
                        reasoning="AUTH 단계 강제 규칙: 닫기/X 대신 로그인 제출을 우선합니다.",
                        confidence=0.95,
                    )
                agent._auth_last_planned_fill = None
                return ActionDecision(
                    action=ActionType.PRESS,
                    value="Enter",
                    reasoning="AUTH 단계 강제 규칙: submit control id가 불안정해 Enter 제출을 우선합니다.",
                    confidence=0.95,
                )
        return ActionDecision(
            action=ActionType.WAIT,
            value='{"time_ms": 300}',
            reasoning="AUTH 단계 강제 규칙: 로그인 게이트가 열려 있어 닫기/X 대신 사용자 개입 또는 인증 입력을 우선합니다.",
            confidence=0.6,
        )

    def _auth_surface_score(element: DOMElement) -> float:
        if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
            return 0.0
        blob = _label_blob(element)
        score = 0.0
        if element.tag in {"input", "textarea"}:
            score += 1.0
            if any(token in blob for token in ("password", "비밀번호")):
                score += 6.0
            if any(token in blob for token in ("username", "email", "이메일", "아이디", "user")):
                score += 4.0
        if any(token in blob for token in ("로그인", "login", "sign in", "signin", "회원가입", "signup", "submit", "continue", "next")):
            score += 3.0
        if str(getattr(element, "aria_modal", "") or "").strip().lower() == "true":
            score += 3.0
        meta_blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(element, "container_name", "") or ""),
                    str(getattr(element, "context_text", "") or ""),
                ]
            )
        )
        if any(token in meta_blob for token in ("로그인", "login", "sign in", "signin", "회원가입", "signup", "password", "비밀번호", "email", "이메일", "아이디")):
            score += 2.0
        return score

    def _auth_surface_ref(elements: List[DOMElement]) -> str:
        bucket_scores: dict[str, float] = {}
        for element in elements:
            container_ref = str(getattr(element, "container_ref_id", "") or "").strip()
            if not container_ref:
                continue
            bucket_scores[container_ref] = float(bucket_scores.get(container_ref, 0.0)) + _auth_surface_score(element)
        if not bucket_scores:
            return ""
        best_ref, best_score = max(bucket_scores.items(), key=lambda item: item[1])
        return best_ref if best_score >= 5.0 else ""

    def _has_auth_fields(elements: List[DOMElement]) -> bool:
        for element in elements:
            if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
                continue
            if element.tag not in {"input", "textarea"}:
                continue
            blob = _label_blob(element)
            if any(token in blob for token in ("password", "비밀번호", "username", "email", "이메일", "아이디", "user")):
                return True
        return False

    def _has_auth_submit_control(elements: List[DOMElement]) -> bool:
        for element in elements:
            if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
                continue
            role = str(getattr(element, "role", "") or "").lower()
            tag = str(getattr(element, "tag", "") or "").lower()
            if role not in {"button", "link"} and tag not in {"button", "a"}:
                continue
            blob = _label_blob(element)
            if any(token in blob for token in ("로그인", "login", "sign in", "signin", "continue", "submit")):
                return True
        return False

    def _auth_dom_for_prompt() -> List[DOMElement]:
        auth_elements = list(dom_elements or [])
        if _has_auth_fields(auth_elements):
            return auth_elements
        full_dom = agent._analyze_dom(scope_container_ref_id="")
        if not full_dom:
            return auth_elements
        auth_scope_ref = _auth_surface_ref(full_dom)
        if auth_scope_ref:
            scoped_auth_dom = agent._analyze_dom(scope_container_ref_id=auth_scope_ref)
            if scoped_auth_dom:
                agent._auth_interrupt_scope_ref = str(auth_scope_ref)
                agent._auth_interrupt_scope_source = "auth-interrupt-scope"
                agent._active_scoped_container_ref = str(auth_scope_ref)
                agent._active_interaction_surface = {
                    "kind": "auth",
                    "ref_id": str(auth_scope_ref),
                    "source": "auth-interrupt-scope",
                    "sticky_until": 0.0,
                }
                agent._record_reason_code("auth_interrupt_scoped")
                return scoped_auth_dom
        agent._auth_interrupt_scope_ref = ""
        agent._auth_interrupt_scope_source = ""
        agent._active_scoped_container_ref = ""
        agent._record_reason_code("auth_interrupt_unscoped")
        return full_dom

    def _find_interaction_surface_ref(surface_elements: List[DOMElement]) -> str:
        active_surface = getattr(agent, "_active_interaction_surface", {}) or {}
        preferred_refs: List[str] = []
        for candidate in (
            str(active_surface.get("ref_id") or "").strip(),
            str(getattr(agent, "_active_scoped_container_ref", "") or "").strip(),
            str(getattr(agent, "_pre_auth_surface_ref", "") or "").strip(),
            str((getattr(agent, "_blocked_intent", {}) or {}).get("container_ref_id") or "").strip(),
        ):
            if candidate and candidate not in preferred_refs:
                preferred_refs.append(candidate)
        for ref in preferred_refs:
            if any(str(getattr(el, "container_ref_id", "") or "").strip() == ref for el in surface_elements):
                return ref
        return ""

    def _role_prefilter_elements(candidates: List[DOMElement]) -> List[DOMElement]:
        role_snapshot = getattr(agent, "_last_role_snapshot", None)
        if not isinstance(role_snapshot, dict) or not candidates:
            return candidates
        goal_tokens = set(getattr(agent, "_goal_tokens", set()) or set())
        if not goal_tokens:
            return candidates
        quoted_matches = [agent._normalize_text(v) for v in re.findall(r'"([^"]+)"', str(getattr(agent, "_active_goal_text", "") or "")) if agent._normalize_text(v)]
        tree_nodes = role_snapshot.get("tree") if isinstance(role_snapshot.get("tree"), list) else []
        tree_score_by_ref = {}
        if tree_nodes:
            for node in tree_nodes:
                if not isinstance(node, dict):
                    continue
                ref = str(node.get("ref") or "").strip()
                if not ref:
                    continue
                node_name = agent._normalize_text(str(node.get("name") or ""))
                node_role = agent._normalize_text(str(node.get("role") or ""))
                ancestor_blob = agent._normalize_text(" ".join(node.get("ancestor_names") or []))
                score = 0.0
                score += 1.5 * len(goal_tokens.intersection(set(agent._tokenize_text(node_name))))
                score += 1.0 * len(goal_tokens.intersection(set(agent._tokenize_text(ancestor_blob))))
                if node_role in {"button", "link", "tab", "menuitem", "option"}:
                    score += 0.75
                for phrase in quoted_matches:
                    if phrase and phrase in node_name:
                        score += 3.0
                    if phrase and phrase in ancestor_blob:
                        score += 1.5
                if score > 0.0:
                    tree_score_by_ref[ref] = max(float(tree_score_by_ref.get(ref, 0.0)), score)
        ranked: List[tuple[float, DOMElement]] = []
        for element in candidates:
            role_name = agent._normalize_text(str(getattr(element, "role_ref_name", None) or ""))
            role_role = agent._normalize_text(str(getattr(element, "role_ref_role", None) or ""))
            if not role_name and not role_role:
                ref_id = str((getattr(agent, "_element_ref_ids", {}) or {}).get(getattr(element, "id", -1)) or "").strip()
                if not ref_id or ref_id not in tree_score_by_ref:
                    continue
            score = 0.0
            score += 1.5 * len(goal_tokens.intersection(set(agent._tokenize_text(role_name))))
            if role_role in {"button", "link", "tab", "menuitem", "option"}:
                score += 0.75
            for phrase in quoted_matches:
                if phrase and phrase in role_name:
                    score += 3.0
            if str(getattr(element, "container_source", None) or "") == "semantic-first":
                score += 1.0
            ref_id = str((getattr(agent, "_element_ref_ids", {}) or {}).get(getattr(element, "id", -1)) or "").strip()
            if ref_id:
                score += float(tree_score_by_ref.get(ref_id, 0.0))
            if score > 0.0:
                ranked.append((score, element))
        if not ranked:
            return candidates
        ranked.sort(key=lambda item: item[0], reverse=True)
        kept: List[DOMElement] = []
        keep_container_refs = {
            str(getattr(element, "container_ref_id", "") or "").strip()
            for score, element in ranked[: min(6, len(ranked))]
            if score >= max(2.0, ranked[0][0] - 2.0)
        }
        for element in candidates:
            container_ref = str(getattr(element, "container_ref_id", "") or "").strip()
            if container_ref and container_ref in keep_container_refs:
                kept.append(element)
                continue
            if any(id(element) == id(candidate) for _, candidate in ranked[: min(8, len(ranked))]):
                kept.append(element)
        if 0 < len(kept) < len(candidates):
            agent._record_reason_code("role_snapshot_prefilter")
            return kept
        return candidates

    def _find_deterministic_verify_completion(verify_elements: List[DOMElement]) -> Optional[ActionDecision]:
        current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
        if current_phase.startswith("precheck"):
            return None
        if str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase)) != "evidence_only":
            return None
        completion_reason = agent._evaluate_goal_target_completion(
            goal=goal,
            dom_elements=verify_elements or [],
        )
        if not completion_reason:
            return None
        agent._record_reason_code("deterministic_verify_completion")
        return ActionDecision(
            action=ActionType.WAIT,
            reasoning="현재 DOM 증거만으로 목표 달성이 확인되어 추가 추론 없이 종료합니다.",
            confidence=0.98,
            is_goal_achieved=True,
            goal_achievement_reason=completion_reason,
        )

    def _decision_is_verify_blocked_mutation(decision: ActionDecision, candidates: List[DOMElement]) -> bool:
        current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
        if str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase)) != "evidence_only":
            return False
        if decision.action not in {ActionType.CLICK, ActionType.SELECT, ActionType.PRESS}:
            return False
        semantics = getattr(agent, "_goal_semantics", None)
        mutation_direction = str(getattr(semantics, "mutation_direction", "") or "").strip().lower()
        if mutation_direction not in {"increase", "decrease", "clear"}:
            return False
        selected_element = next(
            (el for el in candidates if int(getattr(el, "id", -1)) == int(decision.element_id or -9999)),
            None,
        )
        if selected_element is None:
            return False
        role = str(getattr(selected_element, "role", "") or "").lower()
        tag = str(getattr(selected_element, "tag", "") or "").lower()
        if role not in {"button", "link", "tab"} and tag not in {"button", "a"}:
            return False
        blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(selected_element, "text", "") or ""),
                    str(getattr(selected_element, "aria_label", "") or ""),
                    str(getattr(selected_element, "title", "") or ""),
                    str(getattr(selected_element, "container_name", "") or ""),
                    str(getattr(selected_element, "context_text", "") or ""),
                ]
            )
        )
        if mutation_direction == "increase":
            return any(token in blob for token in ("추가", "담기", "바로 추가", "add", "append", "apply", "select", "반영", "넣기"))
        if mutation_direction == "decrease":
            return any(token in blob for token in ("삭제", "제거", "remove", "delete", "clear", "비우"))
        return any(token in blob for token in ("전체 삭제", "전부 삭제", "clear", "empty", "remove all", "비우"))

    auth_phase_active = bool(
        str(getattr(agent, "_goal_policy_phase", "") or "").strip() == "handle_auth_or_block"
        or bool((getattr(agent, "_last_snapshot_evidence", {}) or {}).get("auth_prompt_visible"))
    )
    goal_state_membership_belief = str((goal_state or {}).get("membership_belief") or "").strip().lower()
    try:
        goal_state_membership_confidence = float((goal_state or {}).get("membership_confidence") or 0.0)
    except Exception:
        goal_state_membership_confidence = 0.0
    branch_requires_removal = bool(
        str((goal_state or {}).get("target_locus") or "").strip().lower() == "destination"
        and goal_state_membership_belief == "present"
        and goal_state_membership_confidence >= 0.7
        and not bool(getattr(agent, "_goal_plan_remediation_completed", False))
    )
    auth_mode = str(((goal.test_data or {}) if isinstance(goal.test_data, dict) else {}).get("auth_mode") or "").strip().lower()
    auth_has_credentials = bool(agent._has_login_test_data(goal))
    handoff_mode = str((getattr(agent, "_handoff_state", {}) or {}).get("mode") or "").strip().lower()
    auth_close_allowed = auth_mode in {"skip", "declined", "dismiss", "close", "no_login"} or handoff_mode in {"declined", "skip"}
    prompt_phase_intent = str(
        getattr(agent, "_goal_phase_intent", "")
        or goal_phase_intent(str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower())
    )
    prompt_goal_kind_raw = getattr(getattr(agent, "_goal_semantics", None), "goal_kind", None)
    prompt_goal_kind = str(getattr(prompt_goal_kind_raw, "value", prompt_goal_kind_raw) or "").strip().lower()
    browser_backend_name = str(
        getattr(agent, "_browser_backend_name", "")
        or os.getenv("GAIA_BROWSER_BACKEND", "")
        or ""
    ).strip().lower()
    openclaw_agentic_mode = bool(browser_backend_name == "openclaw")
    base_dom_elements = dom_elements
    if auth_phase_active and not auth_close_allowed and not openclaw_agentic_mode:
        base_dom_elements = _auth_dom_for_prompt()
    else:
        if (
            bool(getattr(agent, "_auth_interrupt_active", False))
            and not bool((getattr(agent, "_last_snapshot_evidence", {}) or {}).get("auth_prompt_visible"))
            and not _has_auth_fields(base_dom_elements)
        ):
            setattr(agent, "_auth_interrupt_active", False)
        agent._auth_interrupt_scope_ref = ""
        agent._auth_interrupt_scope_source = ""
        if browser_backend_name != "openclaw":
            base_dom_elements = _role_prefilter_elements(base_dom_elements)
    mutate_target_visible_in_base_dom = False
    if prompt_phase_intent == "mutate" and prompt_goal_kind in {"add_to_list", "apply_selection"}:
        target_terms_for_scope = [
            agent._normalize_text(term)
            for term in list(getattr(getattr(agent, "_goal_semantics", None), "target_terms", []) or [])
            if str(term or "").strip()
        ]
        if target_terms_for_scope:
            for element in base_dom_elements or []:
                blob = agent._normalize_text(
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
                if any(term in blob for term in target_terms_for_scope):
                    mutate_target_visible_in_base_dom = True
                    break
    if branch_requires_removal and browser_backend_name != "openclaw":
        def _is_increase_candidate(element: DOMElement) -> bool:
            role = str(getattr(element, "role", "") or "").lower()
            tag = str(getattr(element, "tag", "") or "").lower()
            if role not in {"button", "link", "tab"} and tag not in {"button", "a"}:
                return False
            blob = agent._normalize_text(
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
            return any(token in blob for token in ("추가", "담기", "바로 추가", "add", "append", "apply", "select", "반영", "넣기"))

        removal_filtered = [element for element in base_dom_elements if not _is_increase_candidate(element)]
        if removal_filtered and len(removal_filtered) < len(base_dom_elements):
            base_dom_elements = removal_filtered
            agent._record_reason_code("conditional_branch_removal_filter")

    elements_for_prompt = base_dom_elements
    if browser_backend_name == "openclaw":
        scoped_ref_id = ""
        scoped_name = ""
        scoped_source = ""
        scoped_score = 0.0
        scoped_ambiguous = False
        agent._active_scoped_container_ref = ""
    elif auth_phase_active and not auth_close_allowed:
        scoped_ref_id = str(getattr(agent, "_auth_interrupt_scope_ref", "") or "").strip()
        scoped_name = "auth-interrupt-surface" if scoped_ref_id else ""
        scoped_source = str(getattr(agent, "_auth_interrupt_scope_source", "") or "").strip()
        scoped_score = 0.0
        scoped_ambiguous = False
    else:
        def _reject_small_mutate_scope(scoped_dom: List[DOMElement]) -> bool:
            if prompt_phase_intent != "mutate" or not scoped_dom or len(scoped_dom) > 3:
                return False
            for element in scoped_dom:
                blob = agent._normalize_text(
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
                if any(token in blob for token in ("로그아웃", "logout", "닫기", "close", "dismiss")):
                    return True
            return False

        if (
            prompt_phase_intent == "evidence_only"
            or mutate_target_visible_in_base_dom
        ):
            scoped_ref_id = ""
            scoped_name = ""
            scoped_source = ""
            scoped_score = 0.0
            scoped_ambiguous = False
            agent._active_scoped_container_ref = ""
            if mutate_target_visible_in_base_dom:
                agent._record_reason_code("mutate_visible_target_skip_scope")
        else:
            preferred_surface_ref = ""
            if bool(getattr(agent, "_surface_reacquire_pending", False)) or bool(getattr(agent, "_auth_resume_pending", False)):
                preferred_surface_ref = _find_interaction_surface_ref(base_dom_elements)
            if preferred_surface_ref:
                rescoped_dom = agent._analyze_dom(scope_container_ref_id=preferred_surface_ref)
                if _reject_small_mutate_scope(rescoped_dom):
                    scoped_ref_id = ""
                    scoped_name = ""
                    scoped_source = ""
                    scoped_score = 0.0
                    scoped_ambiguous = False
                    agent._active_scoped_container_ref = ""
                    agent._record_reason_code("mutate_scope_rejected")
                elif rescoped_dom and 0 < len(rescoped_dom) <= len(base_dom_elements):
                    elements_for_prompt = rescoped_dom
                    scoped_ref_id = str(preferred_surface_ref)
                    scoped_name = "active-interaction-surface"
                    scoped_source = "interaction-surface"
                    scoped_score = 1.0
                    scoped_ambiguous = False
                    agent._active_scoped_container_ref = str(scoped_ref_id)
                    agent._record_reason_code("interaction_surface_reacquired")
                else:
                    scoped_ref_id = ""
                    scoped_name = ""
                    scoped_source = ""
                    scoped_score = 0.0
                    scoped_ambiguous = False
            else:
                scoped_ref_id, scoped_name, scoped_source, scoped_score, scoped_ambiguous = agent._pick_scoped_container(base_dom_elements)
                if scoped_ambiguous:
                    agent._record_reason_code("context_target_ambiguous")
                elif scoped_ref_id:
                    rescoped_dom = agent._analyze_dom(scope_container_ref_id=scoped_ref_id)
                    if _reject_small_mutate_scope(rescoped_dom):
                        scoped_ref_id = ""
                        scoped_name = ""
                        scoped_source = ""
                        scoped_score = 0.0
                        scoped_ambiguous = False
                        agent._active_scoped_container_ref = ""
                        agent._record_reason_code("mutate_scope_rejected")
                    elif rescoped_dom and 0 < len(rescoped_dom) < len(base_dom_elements):
                        elements_for_prompt = rescoped_dom
                        agent._active_scoped_container_ref = str(scoped_ref_id)
                        if scoped_source == "semantic-first":
                            agent._record_reason_code("semantic_container_scoped")
                        else:
                            agent._record_reason_code("container_scoped_snapshot")
                    else:
                        agent._record_reason_code("container_context_missing")
                else:
                    agent._record_reason_code("container_context_missing")

    elements_text = agent._format_dom_for_llm(elements_for_prompt)
    scoped_hint = "없음"
    if scoped_ref_id and scoped_name:
        scoped_hint = f'{scoped_name} (ref={scoped_ref_id}, source={scoped_source or "unknown"}, score={scoped_score:.2f})'
    recent_repeated = agent._recent_click_element_ids[-8:]
    recent_block_text = ", ".join(str(x) for x in recent_repeated) if recent_repeated else "없음"
    auth_ui_still_present = bool(
        _has_auth_fields(elements_for_prompt or base_dom_elements or dom_elements)
        or _has_auth_submit_control(elements_for_prompt or base_dom_elements or dom_elements)
    )
    auth_prompt_visible_now = bool((getattr(agent, "_last_snapshot_evidence", {}) or {}).get("auth_prompt_visible"))
    auth_submit_attempts_now = int(getattr(agent, "_auth_submit_attempts", 0) or 0)
    last_auth_submit_at = float(getattr(agent, "_last_auth_submit_at", 0.0) or 0.0)
    auth_surface_progressed = bool(getattr(agent, "_auth_surface_progressed", False))
    auth_fallback_escalated = bool(
        auth_phase_active
        and auth_ui_still_present
        and (
            auth_surface_progressed
            or (
                auth_submit_attempts_now >= 2
                and last_auth_submit_at > 0.0
                and (time.time() - last_auth_submit_at) >= 5.0
            )
        )
    )
    if (
        auth_phase_active
        and not auth_close_allowed
        and bool(getattr(agent, "_auth_submit_attempted", False))
        and not auth_ui_still_present
    ):
        auth_phase_active = False
        auth_prompt_visible_now = False
        agent._auth_interrupt_scope_ref = ""
        agent._auth_interrupt_scope_source = ""
    current_phase_norm = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
    current_phase_intent = str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase_norm))
    likely_post_auth_resume = bool(
        (not openclaw_agentic_mode)
        and
        not auth_phase_active
        and not auth_prompt_visible_now
        and not branch_requires_removal
        and current_phase_intent != "evidence_only"
        and bool(getattr(agent, "_auth_resume_pending", False))
        and (
            bool(getattr(agent, "_auth_submit_attempted", False))
            or float(getattr(agent, "_auth_resolved_at", 0.0) or 0.0) > 0.0
        )
    )
    if (
        auth_phase_active
        and not auth_close_allowed
        and auth_ui_still_present
        and not auth_fallback_escalated
        and not openclaw_agentic_mode
    ):
        auth_fallback = _find_auth_phase_fallback()
        if auth_fallback is not None:
            return auth_fallback
    if auth_fallback_escalated:
        agent._record_reason_code("auth_fallback_escalated_to_llm")
    verify_completion_elements = (
        dom_elements
        if current_phase_intent == "evidence_only"
        else (elements_for_prompt or base_dom_elements or dom_elements)
    )
    if not openclaw_agentic_mode:
        verify_completion_decision = _find_deterministic_verify_completion(
            verify_completion_elements
        )
        if verify_completion_decision is not None:
            return verify_completion_decision
    if scoped_ref_id and bool(getattr(agent, "_surface_reacquire_pending", False)):
        agent._surface_reacquire_pending = False
    resume_after_auth_rule = ""
    if likely_post_auth_resume:
        resume_after_auth_rule = """
9. **인증 직후 재개 규칙(강화)**
   - 방금 로그인/인증이 끝난 직후라면, 원래 막혔던 목표 CTA/같은 target 카드의 액션을 먼저 1회 재시도하세요.
   - 이 경우 최근 반복 클릭 회피 규칙보다 목표 CTA 재개가 우선입니다.
   - 인증 직후에는 바로 wait나 목적지 검증으로 넘어가지 말고, 먼저 target 카드/행 문맥을 다시 확보한 뒤 차단되었던 CTA를 재시도하세요.
"""
    auth_gate_rule = ""
    if auth_phase_active:
        if auth_close_allowed:
            auth_gate_rule = """
10. **로그인 거부 후 처리 규칙**
   - 사용자가 로그인하지 않고 계속 진행하라고 명시했습니다.
   - 이 경우에만 X/닫기/뒤로가기/모달 닫기가 허용됩니다.
"""
        elif auth_has_credentials:
            auth_gate_rule = """
10. **인증 게이트 규칙(강제)**
   - 현재 로그인/회원가입 화면이 열려 있고 로그인 정보가 준비되어 있습니다.
   - X/닫기/뒤로가기/모달 닫기 액션은 금지입니다.
   - 아이디/이메일 입력 -> 비밀번호 입력 -> 로그인 제출 순서만 선택하세요.
"""
        else:
            auth_gate_rule = """
10. **인증 게이트 규칙(강제)**
   - 현재 로그인/회원가입 화면이 열려 있습니다.
   - X/닫기/뒤로가기/모달 닫기 액션은 금지입니다.
   - 로그인 정보가 없으면 사용자 개입 요청 외 다른 액션을 선택하지 마세요.
"""
    remediation_rule = ""
    semantics = getattr(agent, "_goal_semantics", None)
    if (
        semantics is not None
        and getattr(semantics, "goal_kind", None) == GoalKind.ADD_TO_LIST
        and bool(getattr(semantics, "conditional_remediation", False))
    ):
        remediation_rule = """
11. **조건부 remediation 규칙(강제)**
   - 기본 경로는 항상 추가/반영 확인입니다.
   - \"이미 추가되어 있으면 삭제 후 다시 추가\" 같은 문구가 있어도, 삭제/제거/비우기 액션은 pre-action 증거로 이미 반영된 상태가 확인된 경우에만 선택하세요.
   - 그 증거가 없으면 삭제 흐름으로 들어가지 말고 추가 또는 반영 검증을 계속하세요.
"""
    signup_rule = ""
    if agent._goal_mentions_signup(goal):
        signup_rule = """
5. **회원가입 목표 특별 규칙(강제)**
   - 회원가입 화면/모달 진입만으로는 절대 성공이 아닙니다.
   - 입력값 채움 + 제출 버튼 클릭 + 완료 신호(완료 문구/로그인 상태 변화) 확인 전까지 is_goal_achieved=false를 유지하세요.
"""
    constraint_rule = agent._build_goal_constraint_prompt()
    goal_state_summary = "없음"
    if isinstance(goal_state, dict) and goal_state:
        summary_target_locus = goal_state.get("target_locus")
        summary_subgoal = goal_state.get("subgoal")
        if goal_state_membership_belief not in {"present", "absent"} or goal_state_membership_confidence < 0.7:
            summary_target_locus = None
            summary_subgoal = None
        proof_summary = {}
        raw_proof = goal_state.get("proof")
        if isinstance(raw_proof, dict):
            proof_summary = {
                str(key): value
                for key, value in raw_proof.items()
                if bool(value)
            }
        goal_state_summary = json.dumps(
            {
                "membership_belief": goal_state.get("membership_belief"),
                "membership_confidence": goal_state.get("membership_confidence"),
                "target_locus": summary_target_locus,
                "subgoal": summary_subgoal,
                "proof": proof_summary,
                "contradiction_signals": list(goal_state.get("contradiction_signals") or [])[-4:],
            },
            ensure_ascii=False,
            indent=2,
        )
    openclaw_agentic_rule = ""
    if openclaw_agentic_mode:
        openclaw_agentic_rule = """
12. **OpenClaw agentic replanning 규칙(강화)**
   - 현재 턴에서는 phase 이름보다 최신 DOM/스크린샷과 상태 캐시를 더 신뢰하세요.
   - OpenClaw 경로에서는 숫자 `element_id`보다 각 요소 줄에 함께 보이는 `ref="..."`를 우선 사용하세요.
   - 로컬 heuristic이나 직전 필드 추정치를 믿지 말고, 현재 스크린샷에 실제로 보이는 입력창/버튼/에러 문구를 기준으로 다음 행동을 고르세요.
   - 인증 모달이 열려 있으면 현재 보이는 surface를 새 화면으로 보고 다시 읽으세요. 이전 surface의 element_id나 필드 의미를 그대로 가정하지 마세요.
   - 인증 오류 문구가 보이면, 그 오류가 붙은 현재 surface에서 아이디/비밀번호/제출 순서를 다시 계획하세요.
   - 상태 캐시의 `membership_belief`가 `present`이고 confidence가 높거나, 이미 removal proof가 일부라도 있으면 destination row/slot/action을 우선 탐색하세요.
   - 그 확신이 약하면 source 카드와 destination evidence를 둘 다 다시 비교하고, source 카드의 직접 매칭 CTA를 먼저 고려하세요.
   - `subgoal`이 `activate_destination_row`, `remove_membership`, `verify_final_presence`, `source_readd` 중 하나로 확정돼 있을 때만 그 의미에 맞는 다음 행동을 직접 다시 계획하세요.
   - 코드가 미리 고른 reveal/scroll 후보를 맹신하지 말고, 현재 화면에서 목표 과목명과 직접 연결된 요소를 우선 선택하세요.
   - 현재 보이는 요소의 self label(text/title/aria)에 목표 과목명이 직접 없으면, 그 row/action을 목표 row로 가정하지 마세요.
"""

    if openclaw_agentic_mode:
        compact_test_data = {}
        if auth_phase_active:
            compact_test_data = goal.test_data if isinstance(goal.test_data, dict) else {}
        prompt = f"""당신은 OpenClaw 스타일의 웹 작업 에이전트입니다.
현재 화면과 직전 결과를 다시 읽고, 다음 한 단계만 결정하세요.

## 목표
- 이름: {goal.name}
- 설명: {goal.description}
- 성공 조건: {', '.join(goal.success_criteria)}
- 실패 조건: {', '.join(goal.failure_criteria) if goal.failure_criteria else '없음'}

## 사용 가능한 테스트 데이터
{json.dumps(compact_test_data, ensure_ascii=False, indent=2)}

## 최근 액션 기록
{chr(10).join(agent._action_history[-3:]) if agent._action_history else '없음'}

## 최근 실행 피드백
{chr(10).join(agent._action_feedback[-3:]) if agent._action_feedback else '없음'}

## 현재 상태 캐시
{goal_state_summary}

## 현재 화면의 DOM 요소와 목표 관련 증거
{elements_text}

## 현재 우선 컨테이너
{scoped_hint}

## 작업 규칙
1. phase 이름보다 최신 DOM/스크린샷을 우선 신뢰하세요.
2. 목표 과목/대상과 직접 연결된 카드, 행(row/slot/card), 버튼을 먼저 찾으세요.
3. source 카드에서 액션이 no-op 이었거나 상태 캐시가 destination 쪽을 가리키면, source CTA 반복 대신 destination row/slot/action을 먼저 보세요.
4. 삭제/제거 버튼이 안 보이면 목표 row/slot/card를 먼저 활성화하세요.
5. 스크롤은 목표와 직접 연결된 row/card/action이 정말 안 보일 때만 고르세요.
6. 로그인/인증 surface가 보이면 현재 surface를 새 화면으로 간주하고 다시 읽으세요.
7. 목표 대상명을 직접 포함하지 않는 토스트/경고/에러/충돌 메시지는 우선순위가 낮습니다. 목표와 무관한 토스트를 닫거나 해결하려고 먼저 움직이지 마세요.
8. 모달/오버레이가 실제로 열려 있지 않다면 닫기/close/dismiss를 고르지 마세요.
9. 로그아웃, 다운로드, PDF 저장, 전체삭제 같은 전역/파괴적 컨트롤은 목표가 직접 요구하지 않는 한 선택하지 마세요.
10. 목표가 이미 달성됐다고 판단되면 `is_goal_achieved=true`와 이유를 반환하세요.
11. OpenClaw 경로에서는 클릭/입력 대상이 보이면 `ref_id`를 함께 반환하세요. `element_id`는 없으면 null이어도 됩니다.

## 응답 형식 (JSON만, 마크다운 없이)
{{
    \"action\": \"click\" | \"fill\" | \"press\" | \"scroll\" | \"wait\" | \"select\",
    \"ref_id\": 요소 ref ID (문자열, OpenClaw 경로에서는 이것을 우선 사용),
    \"element_id\": 요소ID (숫자, 없으면 null 허용),
    \"value\": \"입력값 (fill), 키 이름 (press), select 값(문자열/콤마구분/JSON 배열), wait 조건(JSON 또는 ms)\",
    \"reasoning\": \"현재 화면 기준으로 이 행동이 왜 다음 단계인지\",
    \"confidence\": 0.0~1.0,
    \"is_goal_achieved\": true | false,
    \"goal_achievement_reason\": \"목표 달성 판단 이유 (is_goal_achieved가 true인 경우)\"
}}

JSON 응답:"""
    else:
        prompt = f"""당신은 웹 테스트 자동화 에이전트입니다.
현재 화면의 DOM 요소와 화면 증거를 분석하고, 다음에 수행할 액션을 결정하세요.

## 목표
- 이름: {goal.name}
- 설명: {goal.description}
- 우선순위: {getattr(goal, 'priority', 'MAY')}
- 성공 조건: {', '.join(goal.success_criteria)}
- 실패 조건: {', '.join(goal.failure_criteria) if goal.failure_criteria else '없음'}
 - 키워드: {', '.join(getattr(goal, 'keywords', []) or []) if getattr(goal, 'keywords', None) else '없음'}

## 현재 실행 phase (참고)
- phase: {agent._runtime_phase}
- AUTH=인증/로그인 처리, COLLECT=후보 수집, COMPOSE=조합/설정, APPLY=반영/실행, VERIFY=완료 검증
- phase는 가이드일 뿐이며, 실제 DOM/상태 변화 증거를 우선하세요.

## 사용 가능한 테스트 데이터
{json.dumps(goal.test_data, ensure_ascii=False, indent=2)}

## 지금까지 수행한 액션
{chr(10).join(agent._action_history[-5:]) if agent._action_history else '없음 (첫 번째 스텝)'}

## 최근 액션 실행 피드백
{chr(10).join(agent._action_feedback[-5:]) if agent._action_feedback else '없음'}

## 최근 반복 클릭 element_id (가능하면 회피)
{recent_block_text}

## 도메인 실행 기억(KB)
{memory_context or '없음'}

## 현재 agentic 상태 캐시
{goal_state_summary}

## 현재 화면의 DOM 요소와 목표 관련 증거
{elements_text}

## 현재 선택된 우선 컨테이너
{scoped_hint}

## 중요 지시사항
0. **키워드 우선 탐색**: 키워드와 관련된 요소를 먼저 찾아서 목표 달성에 활용하세요.
1. **source-card 기본 규칙**: membership 상태가 아직 불확실하면, 목표 과목명이 같은 카드/컨테이너 안에 직접 보이고 그 카드 안에 하나의 적절한 CTA가 있으면 scroll보다 그 CTA 클릭을 우선하세요.
2. **목적지 증거 우선(조건부)**: membership이 이미 present로 강하게 확인됐거나 removal proof가 일부라도 있으면, source 쪽 추가 버튼보다 destination row·slot·card 증거를 우선 신뢰하세요.
3. **row 활성화 우선(조건부)**: 삭제 버튼이 바로 안 보여도 목표 과목명이 직접 보이는 destination row/slot이 있으면 먼저 그 row/slot을 활성화해 후속 action을 드러내세요.
4. **scroll은 마지막 수단**: 목표 과목명이 직접 보이는 row/slot/card 또는 source 카드 CTA가 하나라도 있으면, 먼저 그 요소를 클릭/활성화하세요. scroll은 해당 요소 자체가 안 보일 때만 선택하세요.
1. **탭/섹션 UI 확인**: role=\"tab\"인 요소가 있으면 먼저 해당 탭을 클릭해야 합니다!
   - 예: 로그인 탭, 회원가입 탭이 있으면 → 먼저 로그인 탭 클릭 → 그 다음 폼 입력

2. **입력 전 활성화 확인**: 입력 필드가 비활성 상태일 수 있으므로 탭/버튼을 먼저 클릭

3. **목표 달성 여부 확인**
   - 성공 조건에 해당하는 요소가 보이면 is_goal_achieved: true

4. **중간 단계 파악**: 기획서에 없는 단계도 스스로 파악하세요
   - 예: \"로그인\" 목표 → (1)로그인 탭 클릭 → (2)이메일 입력 → (3)비밀번호 입력 → (4)제출 버튼 클릭
{signup_rule}
{constraint_rule}
6. **무효 액션 반복 금지**
   - 최근 실행 피드백에서 changed=false 또는 success=false인 액션/요소 조합은 반복하지 마세요.
   - 같은 요소를 2회 연속 클릭했는데 changed=false라면 다른 요소/전략을 선택하세요.
7. **컨텍스트 전환 규칙**
   - 같은 의도가 2회 이상 changed=false이면, 다음/페이지네이션/탭/필터/정렬 전환으로 화면 컨텍스트를 바꾼 뒤 다시 시도하세요.
   - 목표 단계 전환 CTA가 안 보일 때 `확장/더보기/show more/expand`는 **콘텐츠 영역 확장일 때만** 우선 선택하세요.
   - 목록형 페이지에서는 동일 카드 반복 클릭보다 다른 카드/다음 페이지 이동을 우선하세요.
   - 페이지네이션에서 \"다음/next/›/»\"가 보이면 숫자 페이지 버튼(1,2,3,4...)보다 우선 선택하세요.
   - 숫자 페이지 버튼만 반복 클릭하지 말고, 진행 정체 시 반드시 \"다음\"으로 넘어가세요.
8. **단계 전환 규칙(강제)**
   - 동일한 클릭 의도가 여러 번 연속 성공해도 목표가 완료되지 않으면, 다음 액션은 단계 전환 CTA를 우선 선택하세요.
   - 해당 CTA가 보이지 않으면 스크롤/탭 전환/다음 페이지 이동으로 CTA를 먼저 찾으세요.
{resume_after_auth_rule}
{auth_gate_rule}
{remediation_rule}
{openclaw_agentic_rule}

## 응답 형식 (JSON만, 마크다운 없이)
{{
    \"action\": \"click\" | \"fill\" | \"press\" | \"scroll\" | \"wait\" | \"select\",
    \"element_id\": 요소ID (숫자),
    \"value\": \"입력값 (fill), 키 이름 (press), select 값(문자열/콤마구분/JSON 배열), wait 조건(JSON 또는 ms)\",
    \"reasoning\": \"이 액션을 선택한 이유\",
    \"confidence\": 0.0~1.0,
    \"is_goal_achieved\": true | false,
    \"goal_achievement_reason\": \"목표 달성 판단 이유 (is_goal_achieved가 true인 경우)\"
}}

JSON 응답:"""

    try:
        llm_started = time.perf_counter()
        if screenshot:
            response_text = agent.llm.analyze_with_vision(prompt, screenshot)
        else:
            response_text = agent._call_llm_text_only(prompt)
        agent._last_llm_trace = {
            "used_llm": True,
            "llm_ms": int((time.perf_counter() - llm_started) * 1000),
            "path": "vision" if screenshot else "text_only",
            "owner": "llm",
        }
        agent._log(f"🧪 llm trace: {agent._last_llm_trace}")
        decision = agent._parse_decision(response_text)
        if (not openclaw_agentic_mode) and _decision_is_verify_blocked_mutation(decision, elements_for_prompt or dom_elements):
            agent._record_reason_code("verify_mutation_guard")
            return ActionDecision(
                action=ActionType.WAIT,
                value='{"time_ms": 700}',
                reasoning="verify 단계에서 같은 종류의 mutation CTA 재실행은 차단되었습니다. 목적지 반영 증거를 먼저 수집합니다.",
                confidence=0.95,
            )
        selected_element = None
        if openclaw_agentic_mode and getattr(decision, "ref_id", None):
            selected_element = next(
                (
                    el for el in (elements_for_prompt or dom_elements)
                    if str(getattr(el, "ref_id", "") or "").strip() == str(getattr(decision, "ref_id", "") or "").strip()
                ),
                None,
            )
        if selected_element is None:
            selected_element = next(
                (el for el in (elements_for_prompt or dom_elements) if int(getattr(el, "id", -1)) == int(decision.element_id or -9999)),
                None,
            )
        if openclaw_agentic_mode and selected_element is not None:
            ref_id = str((getattr(agent, "_element_ref_ids", {}) or {}).get(getattr(selected_element, "id", -1)) or "").strip()
            decision_ref_id = str(getattr(decision, "ref_id", "") or "").strip()
            line_parts = [f"[{getattr(selected_element, 'id', None)}] <{getattr(selected_element, 'tag', '') or ''}>"]
            if decision_ref_id:
                line_parts.append(f'decision-ref="{decision_ref_id}"')
            container_name = str(getattr(selected_element, "container_name", "") or "").strip()
            if container_name:
                line_parts.append(f'within="{container_name}"')
            selected_text_raw = str(getattr(selected_element, "text", "") or "").strip()
            if selected_text_raw:
                line_parts.append(f'"{selected_text_raw}"')
            selected_role_raw = str(getattr(selected_element, "role", "") or "").strip()
            if selected_role_raw:
                line_parts.append(f"role={selected_role_raw}")
            selected_context_raw = str(getattr(selected_element, "context_text", "") or "").strip()
            if selected_context_raw:
                line_parts.append(f'context="{selected_context_raw}"')
            action_labels = list(getattr(selected_element, "group_action_labels", None) or [])
            if action_labels:
                line_parts.append(f'actions=[{" | ".join(str(v) for v in action_labels[:5])}]')
            selected_aria_raw = str(getattr(selected_element, "aria_label", "") or "").strip()
            if selected_aria_raw:
                line_parts.append(f'aria-label="{selected_aria_raw}"')
            selected_title_raw = str(getattr(selected_element, "title", "") or "").strip()
            if selected_title_raw:
                line_parts.append(f'title="{selected_title_raw}"')
            line_parts.append(f"ref_id={ref_id or '<none>'}")
            agent._log(
                "🧪 selected-element trace: "
                + " ".join(line_parts)
            )
        selected_blob = _label_blob(selected_element) if selected_element is not None else ""
        reasoning_norm = agent._normalize_text(str(getattr(decision, "reasoning", "") or ""))
        close_tokens = ("닫", "close", "dismiss", "x 버튼", "x버튼", "우상단 x", "닫기", "취소", "cancel")
        close_intent = any(token in reasoning_norm or token in selected_blob for token in close_tokens)
        selected_role = str(getattr(selected_element, "role", "") or "").strip().lower()
        selected_text = agent._normalize_text(str(getattr(selected_element, "text", "") or ""))
        selected_name = agent._normalize_text(str(getattr(selected_element, "name", "") or ""))
        selected_aria = agent._normalize_text(str(getattr(selected_element, "aria_label", "") or ""))
        selected_placeholder = agent._normalize_text(str(getattr(selected_element, "placeholder", "") or ""))
        close_control_signal = " ".join(
            part for part in (selected_text, selected_name, selected_aria, selected_placeholder, selected_blob) if part
        )
        looks_like_close_control = bool(
            selected_element is not None
            and selected_role in {"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio"}
            and (
                any(token in close_control_signal for token in close_tokens)
                or selected_text in {"x", "×", "✕", "✖", "close"}
                or selected_name in {"x", "×", "✕", "✖", "close"}
            )
        )
        if (((auth_phase_active and not openclaw_agentic_mode) or (current_phase_intent == "evidence_only" and not openclaw_agentic_mode))) and close_intent and not looks_like_close_control:
            agent._surface_reacquire_pending = True
            agent._record_reason_code("close_control_guard")
            return ActionDecision(
                action=ActionType.WAIT,
                value='{"time_ms": 300}',
                reasoning="닫기 의도는 확인됐지만 선택된 요소가 실제 close-like control로 보이지 않아 재탐색합니다.",
                confidence=0.95,
            )
        close_like_decision = (
            current_phase_intent == "evidence_only"
            and decision.action in {ActionType.CLICK, ActionType.PRESS}
            and close_intent
        )
        if close_like_decision and not openclaw_agentic_mode:
            agent._surface_reacquire_pending = True
            agent._record_reason_code("verify_close_guard")
            return ActionDecision(
                action=ActionType.WAIT,
                value='{"time_ms": 300}',
                reasoning="verify 단계에서 같은 close/unblock affordance를 반복하지 않고, 화면 문맥을 다시 확보해 목적지 증거를 수집합니다.",
                confidence=0.95,
            )
        if openclaw_agentic_mode and selected_element is not None and decision.action in {ActionType.CLICK, ActionType.PRESS, ActionType.SELECT}:
            destructive_self_blob = agent._normalize_text(
                " ".join(
                    [
                        str(getattr(selected_element, "text", "") or ""),
                        str(getattr(selected_element, "aria_label", "") or ""),
                        str(getattr(selected_element, "title", "") or ""),
                        str(getattr(selected_element, "placeholder", "") or ""),
                    ]
                )
            )
            if any(
                token in destructive_self_blob
                for token in (
                    "로그아웃",
                    "logout",
                    "pdf",
                    "download",
                    "다운로드",
                    "내보내기",
                    "export",
                    "시간표를 pdf로 저장",
                    "전체 삭제",
                    "전부 삭제",
                    "remove all",
                    "clear all",
                )
            ):
                agent._record_reason_code("openclaw_forbidden_global_control")
                return ActionDecision(
                    action=ActionType.WAIT,
                    value='{"time_ms": 400}',
                    reasoning="전역 또는 파괴적 컨트롤로 보여 재계획합니다.",
                    confidence=0.9,
                )
        if auth_phase_active and not auth_close_allowed and not openclaw_agentic_mode:
            if close_intent:
                return _find_auth_phase_fallback()
        return decision
    except Exception as e:
        agent._last_llm_trace = {
            "used_llm": True,
            "llm_ms": int((time.perf_counter() - llm_started) * 1000) if "llm_started" in locals() else 0,
            "path": "exception",
            "owner": "llm",
        }
        agent._log(f"🧪 llm trace: {agent._last_llm_trace}")
        agent._log(f"LLM 결정 실패: {e}")
        return ActionDecision(
            action=ActionType.WAIT,
            reasoning=f"LLM 오류: {e}",
            confidence=0.0,
        )
