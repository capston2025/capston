from __future__ import annotations

import json
from typing import List, Optional

from .goal_kinds import GoalKind
from .models import ActionDecision, ActionType, DOMElement, TestGoal


def decide_next_action(
    agent,
    dom_elements: List[DOMElement],
    goal: TestGoal,
    screenshot: Optional[str] = None,
    memory_context: str = "",
) -> ActionDecision:
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
                str(getattr(element, "role", "") or "").lower() == "button"
                or str(getattr(element, "tag", "") or "").lower() == "button"
            ):
                if any(token in blob for token in ("로그인", "login", "sign in", "signin")) and login_button is None:
                    login_button = element
        if auth_has_credentials:
            if not username_done and username_field is not None:
                return ActionDecision(
                    action=ActionType.FILL,
                    element_id=username_field.id,
                    value=str(
                        (goal.test_data or {}).get("username")
                        or (goal.test_data or {}).get("email")
                        or ""
                    ),
                    reasoning="AUTH 단계 강제 규칙: 닫기/X 대신 로그인 식별자 입력을 우선합니다.",
                    confidence=0.95,
                )
            if not password_done and password_field is not None:
                return ActionDecision(
                    action=ActionType.FILL,
                    element_id=password_field.id,
                    value=str((goal.test_data or {}).get("password") or ""),
                    reasoning="AUTH 단계 강제 규칙: 닫기/X 대신 비밀번호 입력을 우선합니다.",
                    confidence=0.95,
                )
            submit_attempts = int(getattr(agent, "_auth_submit_attempts", 0) or 0)
            last_submit_at = float(getattr(agent, "_last_auth_submit_at", 0.0) or 0.0)
            can_retry_submit = (
                login_button is not None
                and submit_attempts < 3
                and (not bool(getattr(agent, "_auth_submit_attempted", False)) or (time.time() - last_submit_at) >= 3.0)
            )
            if can_retry_submit:
                return ActionDecision(
                    action=ActionType.CLICK,
                    element_id=login_button.id,
                    reasoning="AUTH 단계 강제 규칙: 닫기/X 대신 로그인 제출을 우선합니다.",
                    confidence=0.95,
                )
        return ActionDecision(
            action=ActionType.WAIT,
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

    def _find_post_auth_resume_decision(resume_elements: List[DOMElement]) -> Optional[ActionDecision]:
        blocked_intent = getattr(agent, "_blocked_intent", {}) or {}
        if not isinstance(blocked_intent, dict) or not blocked_intent:
            return None
        if bool(getattr(agent, "_blocked_intent_resumed", False)):
            return None
        if int(getattr(agent, "_blocked_intent_resume_attempts", 0) or 0) > 0:
            return None
        target_blob = agent._normalize_text(
            " ".join(
                [
                    str(blocked_intent.get("text") or ""),
                    str(blocked_intent.get("aria_label") or ""),
                    str(blocked_intent.get("title") or ""),
                    str(blocked_intent.get("container_name") or ""),
                    str(blocked_intent.get("context_text") or ""),
                ]
            )
        )
        best_element: Optional[DOMElement] = None
        best_score = 0.0
        for element in resume_elements:
            if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
                continue
            if str(getattr(element, "role", "") or "").lower() not in {"button", "link"} and str(getattr(element, "tag", "") or "").lower() not in {"button", "a"}:
                continue
            element_blob = agent._normalize_text(
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
            score = 0.0
            if target_blob and element_blob:
                if target_blob == element_blob:
                    score += 8.0
                if str(getattr(element, "container_ref_id", "") or "").strip() and str(getattr(element, "container_ref_id", "") or "").strip() == str(blocked_intent.get("container_ref_id") or "").strip():
                    score += 4.0
                overlap = sum(
                    1
                    for token in target_blob.split()
                    if len(token) >= 2 and token in element_blob
                )
                score += min(overlap, 6)
            if score > best_score:
                best_score = score
                best_element = element
        if best_element is None or best_score < 4.0:
            return None
        agent._pending_resume_element_id = best_element.id
        agent._record_reason_code("post_auth_resume")
        return ActionDecision(
            action=ActionType.CLICK,
            element_id=best_element.id,
            reasoning="AUTH interrupt 해제 후 원래 막혔던 CTA intent를 1회 재개합니다.",
            confidence=0.92,
        )

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

    def _find_post_auth_reacquire_decision(reacquire_elements: List[DOMElement]) -> Optional[ActionDecision]:
        blocked_intent = getattr(agent, "_blocked_intent", {}) or {}
        if not isinstance(blocked_intent, dict) or not blocked_intent:
            return None
        if bool(getattr(agent, "_blocked_intent_resumed", False)):
            return None

        target_blob = agent._normalize_text(
            " ".join(
                [
                    str(blocked_intent.get("text") or ""),
                    str(blocked_intent.get("aria_label") or ""),
                    str(blocked_intent.get("title") or ""),
                    str(blocked_intent.get("container_name") or ""),
                    str(blocked_intent.get("context_text") or ""),
                    str(getattr(goal, "name", "") or ""),
                    str(getattr(goal, "description", "") or ""),
                ]
            )
        )
        target_tokens = [token for token in target_blob.split() if len(token) >= 2]
        if not target_tokens:
            return None

        visible_target_context = False
        footer_pagination_score = 0.0
        for element in reacquire_elements:
            if not bool(getattr(element, "is_visible", True)):
                continue
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
            if any(token in blob for token in target_tokens):
                visible_target_context = True
                break
            if any(token in blob for token in ("페이지", "pagination", "pager", "next", "prev", "이전", "다음", "page ")):
                footer_pagination_score += 1.0
            role = str(getattr(element, "role", "") or "").lower()
            tag = str(getattr(element, "tag", "") or "").lower()
            if role in {"link", "button"} or tag in {"a", "button"}:
                if blob.isdigit():
                    footer_pagination_score += 0.5
        if visible_target_context:
            return None
        if footer_pagination_score >= 2.0:
            agent._record_reason_code("post_auth_reacquire_home")
            return ActionDecision(
                action=ActionType.PRESS,
                value="Home",
                reasoning="인증 직후 현재 DOM에 원래 목표 카드 문맥이 보이지 않고 하단 페이지네이션/목록 하부만 노출되어 있어, 먼저 상단으로 복귀해 원래 타깃 카드를 다시 확보합니다.",
                confidence=0.88,
            )
        return None

    def _find_destination_surface_reveal_decision(reveal_elements: List[DOMElement]) -> Optional[ActionDecision]:
        semantics = getattr(agent, "_goal_semantics", None)
        if semantics is None or getattr(semantics, "goal_kind", None) not in {GoalKind.REMOVE_FROM_LIST, GoalKind.CLEAR_LIST}:
            return None
        current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip()
        if current_phase not in {"reveal_destination_surface", "act_on_target", "verify_removal", "verify_empty"}:
            return None
        destination_terms = [
            agent._normalize_text(term)
            for term in list(getattr(semantics, "destination_terms", []) or [])
            if str(term or "").strip()
        ]
        target_terms = [
            agent._normalize_text(term)
            for term in list(getattr(semantics, "target_terms", []) or [])
            if str(term or "").strip()
        ]
        if not destination_terms:
            return None

        def _blob(el: DOMElement) -> str:
            return agent._normalize_text(
                " ".join(
                    [
                        str(getattr(el, "text", "") or ""),
                        str(getattr(el, "aria_label", "") or ""),
                        str(getattr(el, "title", "") or ""),
                        str(getattr(el, "container_name", "") or ""),
                        str(getattr(el, "context_text", "") or ""),
                    ]
                )
            )

        target_container_refs: set[str] = set()
        for element in reveal_elements:
            blob = _blob(element)
            if any(term in blob for term in target_terms):
                container_ref = str(getattr(element, "container_ref_id", "") or "").strip()
                if container_ref:
                    target_container_refs.add(container_ref)

        remove_visible = False
        for element in reveal_elements:
            if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
                continue
            blob = _blob(element)
            same_target_container = bool(str(getattr(element, "container_ref_id", "") or "").strip()) and str(
                getattr(element, "container_ref_id", "") or ""
            ).strip() in target_container_refs
            if any(token in blob for token in ("삭제", "제거", "remove", "delete", "clear", "비우")) and (
                same_target_container or any(term in blob for term in destination_terms)
            ):
                remove_visible = True
                break
        if remove_visible:
            return None

        best_secondary_element: Optional[DOMElement] = None
        best_secondary_score = 0.0
        for element in reveal_elements:
            if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
                continue
            container_ref = str(getattr(element, "container_ref_id", "") or "").strip()
            if not container_ref or container_ref not in target_container_refs:
                continue
            blob = _blob(element)
            tag = str(getattr(element, "tag", "") or "").lower()
            role = str(getattr(element, "role", "") or "").lower()
            score = 0.0
            if role in {"button", "link", "tab"} or tag in {"button", "a"}:
                score += 6.0
            if any(term in blob for term in target_terms):
                score += 5.0
            if any(token in blob for token in ("더보기", "show more", "view all", "expand", "펼치", "열기", "menu", "옵션", "option", "more", "편집", "edit", "상세", "details", "⋯", "...")):
                score += 6.0
            labels = getattr(element, "group_action_labels", None) or []
            if isinstance(labels, list) and len([x for x in labels if str(x or "").strip()]) >= 2:
                score += 3.0
            if any(token in blob for token in ("페이지", "pagination", "next", "prev", "정렬", "sort", "filter", "검색", "search")):
                score -= 8.0
            if score > best_secondary_score:
                best_secondary_score = score
                best_secondary_element = element
        if best_secondary_element is not None and best_secondary_score >= 8.0:
            agent._record_reason_code("target_row_secondary_reveal")
            return ActionDecision(
                action=ActionType.CLICK,
                element_id=best_secondary_element.id,
                reasoning="목표 항목이 있는 행/카드 내부에서 보조 affordance를 먼저 열어 제거 CTA를 드러냅니다.",
                confidence=0.9,
            )

        best_element: Optional[DOMElement] = None
        best_score = 0.0
        for element in reveal_elements:
            if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
                continue
            blob = _blob(element)
            tag = str(getattr(element, "tag", "") or "").lower()
            role = str(getattr(element, "role", "") or "").lower()
            score = 0.0
            if any(term in blob for term in destination_terms):
                score += 10.0
            if role in {"button", "link", "tab"} or tag in {"button", "a"}:
                score += 3.0
            if any(token in blob for token in ("더보기", "show more", "view all", "expand", "펼치", "열기", "내 목록", "saved", "favorites", "selected")):
                score += 2.0
            if any(token in blob for token in ("페이지", "pagination", "next", "prev", "정렬", "sort", "filter", "검색", "search")):
                score -= 8.0
            if score > best_score:
                best_score = score
                best_element = element
        if best_element is not None and best_score >= 8.0:
            agent._record_reason_code("destination_surface_reveal")
            return ActionDecision(
                action=ActionType.CLICK,
                element_id=best_element.id,
                reasoning="목적지 영역은 확인되지만 제거 CTA가 직접 보이지 않아, 목적지 surface를 먼저 드러내는 컨트롤을 선택합니다.",
                confidence=0.9,
            )

        return None

    def _find_target_row_affordance_decision(surface_elements: List[DOMElement]) -> Optional[ActionDecision]:
        semantics = getattr(agent, "_goal_semantics", None)
        if semantics is None:
            return None
        goal_kind = getattr(semantics, "goal_kind", None)
        if goal_kind not in {GoalKind.ADD_TO_LIST, GoalKind.APPLY_SELECTION, GoalKind.REMOVE_FROM_LIST, GoalKind.CLEAR_LIST}:
            return None
        if auth_phase_active:
            return None

        current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip()
        allowed_phases = {
            GoalKind.ADD_TO_LIST: {"locate_target", "verify_destination_membership"},
            GoalKind.APPLY_SELECTION: {"locate_target", "verify_destination_membership"},
            GoalKind.REMOVE_FROM_LIST: {"reveal_destination_surface", "act_on_target", "verify_removal"},
            GoalKind.CLEAR_LIST: {"reveal_destination_surface", "act_on_target", "verify_empty"},
        }
        if current_phase not in allowed_phases.get(goal_kind, set()):
            return None

        mutation_direction = str(getattr(semantics, "mutation_direction", "") or "").strip().lower()
        target_terms = [
            agent._normalize_text(term)
            for term in list(getattr(semantics, "target_terms", []) or [])
            if str(term or "").strip()
        ]
        active_ref = str(getattr(agent, "_active_scoped_container_ref", "") or "").strip()
        if not target_terms and not active_ref:
            return None

        def _blob(el: DOMElement) -> str:
            return agent._normalize_text(
                " ".join(
                    [
                        str(getattr(el, "text", "") or ""),
                        str(getattr(el, "aria_label", "") or ""),
                        str(getattr(el, "title", "") or ""),
                        str(getattr(el, "container_name", "") or ""),
                        str(getattr(el, "context_text", "") or ""),
                    ]
                )
            )

        def _is_actionable(el: DOMElement) -> bool:
            role = str(getattr(el, "role", "") or "").lower()
            tag = str(getattr(el, "tag", "") or "").lower()
            return bool(getattr(el, "is_visible", True)) and bool(getattr(el, "is_enabled", True)) and (
                role in {"button", "link", "tab"} or tag in {"button", "a"}
            )

        def _matches_primary_mutation(blob: str) -> bool:
            if mutation_direction == "increase":
                return any(token in blob for token in ("추가", "담기", "바로 추가", "add", "append", "apply", "select", "반영", "넣기"))
            if mutation_direction == "decrease":
                return any(token in blob for token in ("삭제", "제거", "remove", "delete", "clear", "비우"))
            if mutation_direction == "clear":
                return any(token in blob for token in ("전체 삭제", "전부 삭제", "clear", "empty", "remove all", "비우"))
            return False

        target_container_refs: set[str] = set()
        for element in surface_elements:
            container_ref = str(getattr(element, "container_ref_id", "") or "").strip()
            if not container_ref:
                continue
            blob = _blob(element)
            if any(term in blob for term in target_terms):
                target_container_refs.add(container_ref)
        if not target_container_refs and active_ref:
            target_container_refs.add(active_ref)
        if not target_container_refs:
            return None

        for element in surface_elements:
            container_ref = str(getattr(element, "container_ref_id", "") or "").strip()
            if container_ref not in target_container_refs or not _is_actionable(element):
                continue
            if _matches_primary_mutation(_blob(element)):
                return None

        best_element: Optional[DOMElement] = None
        best_score = 0.0
        for element in surface_elements:
            container_ref = str(getattr(element, "container_ref_id", "") or "").strip()
            if container_ref not in target_container_refs or not _is_actionable(element):
                continue
            blob = _blob(element)
            score = 0.0
            if any(token in blob for token in ("더보기", "show more", "view all", "expand", "펼치", "열기", "menu", "옵션", "option", "more", "편집", "edit", "상세", "details", "⋯", "...")):
                score += 7.0
            labels = getattr(element, "group_action_labels", None) or []
            if isinstance(labels, list) and len([x for x in labels if str(x or "").strip()]) >= 2:
                score += 3.0
            if active_ref and container_ref == active_ref:
                score += 2.0
            if any(token in blob for token in ("페이지", "pagination", "pager", "next", "prev", "정렬", "sort", "filter", "검색", "search")):
                score -= 8.0
            if score > best_score:
                best_score = score
                best_element = element
        if best_element is None or best_score < 7.0:
            return None
        agent._record_reason_code("target_row_affordance_reveal")
        return ActionDecision(
            action=ActionType.CLICK,
            element_id=best_element.id,
            reasoning="현재 활성 행/카드에서 직접 mutation CTA가 보이지 않아, 같은 행 내부의 보조 affordance를 먼저 열어 후속 액션을 노출합니다.",
            confidence=0.88,
        )

    auth_phase_active = bool(
        str(getattr(agent, "_goal_policy_phase", "") or "").strip() == "handle_auth_or_block"
        or bool((getattr(agent, "_last_snapshot_evidence", {}) or {}).get("auth_prompt_visible"))
    )
    auth_mode = str(((goal.test_data or {}) if isinstance(goal.test_data, dict) else {}).get("auth_mode") or "").strip().lower()
    auth_has_credentials = bool(agent._has_login_test_data(goal))
    handoff_mode = str((getattr(agent, "_handoff_state", {}) or {}).get("mode") or "").strip().lower()
    auth_close_allowed = auth_mode in {"skip", "declined", "dismiss", "close", "no_login"} or handoff_mode in {"declined", "skip"}

    base_dom_elements = dom_elements
    if auth_phase_active and not auth_close_allowed:
        base_dom_elements = _auth_dom_for_prompt()
    else:
        if bool(getattr(agent, "_auth_interrupt_active", False)) and not bool((getattr(agent, "_last_snapshot_evidence", {}) or {}).get("auth_prompt_visible")):
            setattr(agent, "_auth_interrupt_active", False)
        agent._auth_interrupt_scope_ref = ""
        agent._auth_interrupt_scope_source = ""

    elements_for_prompt = base_dom_elements
    if auth_phase_active and not auth_close_allowed:
        scoped_ref_id = str(getattr(agent, "_auth_interrupt_scope_ref", "") or "").strip()
        scoped_name = "auth-interrupt-surface" if scoped_ref_id else ""
        scoped_source = str(getattr(agent, "_auth_interrupt_scope_source", "") or "").strip()
        scoped_score = 0.0
        scoped_ambiguous = False
    else:
        preferred_surface_ref = ""
        if bool(getattr(agent, "_surface_reacquire_pending", False)) or bool(getattr(agent, "_auth_resume_pending", False)):
            preferred_surface_ref = _find_interaction_surface_ref(base_dom_elements)
        if preferred_surface_ref:
            rescoped_dom = agent._analyze_dom(scope_container_ref_id=preferred_surface_ref)
            if rescoped_dom and 0 < len(rescoped_dom) <= len(base_dom_elements):
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
                if rescoped_dom and 0 < len(rescoped_dom) < len(base_dom_elements):
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
    auth_prompt_visible_now = bool((getattr(agent, "_last_snapshot_evidence", {}) or {}).get("auth_prompt_visible"))
    likely_post_auth_resume = bool(
        not auth_phase_active
        and not auth_prompt_visible_now
        and bool(getattr(agent, "_auth_resume_pending", False))
        and (
            bool(getattr(agent, "_auth_submit_attempted", False))
            or float(getattr(agent, "_auth_resolved_at", 0.0) or 0.0) > 0.0
        )
    )
    if likely_post_auth_resume:
        if bool(getattr(agent, "_surface_reacquire_pending", False)) or int(
            getattr(agent, "_blocked_intent_resume_attempts", 0) or 0
        ) > 0:
            reacquire_decision = _find_post_auth_reacquire_decision(elements_for_prompt or base_dom_elements or dom_elements)
            if reacquire_decision is not None:
                return reacquire_decision
        resumed_decision = _find_post_auth_resume_decision(elements_for_prompt or base_dom_elements or dom_elements)
        if resumed_decision is not None:
            return resumed_decision
        reacquire_decision = _find_post_auth_reacquire_decision(elements_for_prompt or base_dom_elements or dom_elements)
        if reacquire_decision is not None:
            return reacquire_decision
    if scoped_ref_id and bool(getattr(agent, "_surface_reacquire_pending", False)):
        agent._surface_reacquire_pending = False
    row_affordance_decision = _find_target_row_affordance_decision(elements_for_prompt or base_dom_elements or dom_elements)
    if row_affordance_decision is not None:
        return row_affordance_decision
    reveal_decision = _find_destination_surface_reveal_decision(elements_for_prompt or base_dom_elements or dom_elements)
    if reveal_decision is not None:
        return reveal_decision
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

    prompt = f"""당신은 웹 테스트 자동화 에이전트입니다.
현재 화면의 DOM 요소와 목표를 분석하고, 다음에 수행할 액션을 결정하세요.

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

## 현재 화면의 DOM 요소 (클릭/입력 가능한 요소들)
{elements_text}

## 현재 선택된 우선 컨테이너
{scoped_hint}

## 중요 지시사항
0. **키워드 우선 탐색**: 키워드와 관련된 요소를 먼저 찾아서 목표 달성에 활용하세요.
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
        if screenshot:
            response_text = agent.llm.analyze_with_vision(prompt, screenshot)
        else:
            response_text = agent._call_llm_text_only(prompt)
        decision = agent._parse_decision(response_text)
        if auth_phase_active and not auth_close_allowed:
            selected_element = next(
                (el for el in (elements_for_prompt or dom_elements) if int(getattr(el, "id", -1)) == int(decision.element_id or -9999)),
                None,
            )
            reasoning_norm = agent._normalize_text(str(getattr(decision, "reasoning", "") or ""))
            selected_blob = _label_blob(selected_element) if selected_element is not None else ""
            close_intent = any(
                token in reasoning_norm or token in selected_blob
                for token in ("닫", "close", "dismiss", "x 버튼", "x버튼", "우상단 x", "닫기")
            )
            if close_intent:
                return _find_auth_phase_fallback()
        return decision
    except Exception as e:
        agent._log(f"LLM 결정 실패: {e}")
        return ActionDecision(
            action=ActionType.WAIT,
            reasoning=f"LLM 오류: {e}",
            confidence=0.0,
        )
