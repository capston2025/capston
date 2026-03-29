from __future__ import annotations

import json
import time
from typing import Any, List, Optional

from .dom_prompt_formatting import detect_active_surface_context, semantic_tags_for_element
from .goal_policy_phase_runtime import goal_phase_intent
from .goal_replanning_runtime import sync_goal_replanning_state
from .models import ActionDecision, ActionType, DOMElement, TestGoal
from .wrapper_trace_runtime import dump_wrapper_trace, serialize_dom_elements, thin_wrapper_enabled, wrapper_mode_name


def _thin_wrapper_mode(agent: Any) -> bool:
    return thin_wrapper_enabled(agent)


def _ref_for_prompt(el: Optional[DOMElement]) -> str:
    if el is None:
        return "없음"
    ref_id = str(getattr(el, "ref_id", "") or "").strip()
    if ref_id:
        return ref_id
    return str(getattr(el, "id", "") or "")


def _label_for_prompt(el: Optional[DOMElement]) -> str:
    if el is None:
        return ""
    for value in (
        getattr(el, "text", None),
        getattr(el, "aria_label", None),
        getattr(el, "placeholder", None),
        getattr(el, "title", None),
        getattr(el, "role_ref_name", None),
    ):
        text = str(value or "").strip()
        if text:
            return text
    role = str(getattr(el, "role", "") or "").strip().lower()
    tag = str(getattr(el, "tag", "") or "").strip().lower()
    if role in {"button", "link"} or tag in {"button", "a"}:
        return "[icon-only]"
    return tag or "element"


def _build_goal_state_summary(goal_state: Any, *, thin_wrapper_mode: bool) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "mode": "thin" if thin_wrapper_mode else "classic",
        "membership_hint_included": False,
        "suppressed_low_confidence_belief": False,
        "proof_keys": [],
        "visible_proof_keys": [],
    }
    if not isinstance(goal_state, dict) or not goal_state:
        return "없음", meta

    belief = str(goal_state.get("membership_belief") or "").strip().lower()
    try:
        confidence = float(goal_state.get("membership_confidence") or 0.0)
    except Exception:
        confidence = 0.0
    proof_summary = {}
    raw_proof = goal_state.get("proof")
    if isinstance(raw_proof, dict):
        proof_summary = {
            str(key): value
            for key, value in raw_proof.items()
            if bool(value)
        }
    contradiction_signals = list(goal_state.get("contradiction_signals") or [])[-4:]
    meta["proof_keys"] = sorted(proof_summary.keys())
    meta["membership_confidence"] = confidence

    if thin_wrapper_mode:
        stable_proof_summary = {
            key: value
            for key, value in proof_summary.items()
            if key in {"remove_done", "add_done", "readd_done", "final_present_verified"}
        }
        summary_payload: dict[str, Any] = {}
        if stable_proof_summary:
            summary_payload["verified_proof"] = stable_proof_summary
        meta["visible_proof_keys"] = sorted(stable_proof_summary.keys())
        if contradiction_signals:
            summary_payload["contradiction_signals"] = contradiction_signals
        include_membership = (
            belief == "present"
            and confidence >= 0.85
            and bool(stable_proof_summary or contradiction_signals)
        )
        if include_membership:
            summary_payload["membership_hint"] = belief
            summary_payload["membership_confidence"] = confidence
            meta["membership_hint_included"] = True
        elif belief in {"present", "absent"} and confidence > 0.0:
            meta["suppressed_low_confidence_belief"] = True
        if not summary_payload:
            return "불확실", meta
        return json.dumps(summary_payload, ensure_ascii=False, indent=2), meta

    summary_target_locus = goal_state.get("target_locus")
    summary_subgoal = goal_state.get("subgoal")
    if belief not in {"present", "absent"} or confidence < 0.7:
        summary_target_locus = None
        summary_subgoal = None
    if belief in {"present", "absent"}:
        meta["membership_hint_included"] = True
    return json.dumps(
        {
            "membership_belief": goal_state.get("membership_belief"),
            "membership_confidence": goal_state.get("membership_confidence"),
            "target_locus": summary_target_locus,
            "subgoal": summary_subgoal,
            "proof": proof_summary,
            "contradiction_signals": contradiction_signals,
        },
        ensure_ascii=False,
        indent=2,
    ), meta


def _label_blob(agent: Any, element: Optional[DOMElement]) -> str:
    if element is None:
        return ""
    return agent._normalize_text(
        " ".join(
            [
                str(getattr(element, "text", "") or ""),
                str(getattr(element, "aria_label", None) or ""),
                str(getattr(element, "placeholder", None) or ""),
                str(getattr(element, "title", None) or ""),
                str(getattr(element, "type", None) or ""),
                str(getattr(element, "container_name", None) or ""),
                str(getattr(element, "context_text", None) or ""),
            ]
        )
    )


def _has_auth_surface(agent: Any, dom_elements: List[DOMElement]) -> bool:
    for element in dom_elements or []:
        if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
            continue
        blob = _label_blob(agent, element)
        tag = str(getattr(element, "tag", "") or "").lower()
        role = str(getattr(element, "role", "") or "").lower()
        if tag in {"input", "textarea"} and any(
            token in blob for token in ("password", "비밀번호", "username", "email", "이메일", "아이디", "user")
        ):
            return True
        if (role in {"button", "link"} or tag in {"button", "a"}) and any(
            token in blob for token in ("로그인", "login", "sign in", "signin", "continue", "submit")
        ):
            return True
    return False


def _build_auth_surface_summary(
    agent: Any,
    dom_elements: List[DOMElement],
    prompt_test_data: dict[str, Any],
) -> str:
    identifier_field: Optional[DOMElement] = None
    password_field: Optional[DOMElement] = None
    submit_field: Optional[DOMElement] = None
    background_mutations: List[DOMElement] = []

    for element in dom_elements or []:
        tags = set(semantic_tags_for_element(agent, element))
        if "auth_identifier_field" in tags and identifier_field is None:
            identifier_field = element
        if "auth_password_field" in tags and password_field is None:
            password_field = element
        if "auth_submit_candidate" in tags and submit_field is None:
            submit_field = element
        if "source_mutation_candidate" in tags and len(background_mutations) < 3:
            background_mutations.append(element)

    if not any((identifier_field, password_field, submit_field)):
        return ""

    auth_lines = ["## 현재 인증 surface"]
    if identifier_field is not None:
        identifier_value = prompt_test_data.get("username") or prompt_test_data.get("email") or prompt_test_data.get("user_id")
        identifier_suffix = f' value="{identifier_value}"' if identifier_value else ""
        auth_lines.append(
            f'- identifier input: ref={_ref_for_prompt(identifier_field)} label="{_label_for_prompt(identifier_field)}"{identifier_suffix}'
        )
    if password_field is not None:
        password_value = prompt_test_data.get("password")
        password_suffix = f' value="{password_value}"' if password_value else ""
        auth_lines.append(
            f'- password input: ref={_ref_for_prompt(password_field)} label="{_label_for_prompt(password_field)}"{password_suffix}'
        )
    if submit_field is not None:
        auth_lines.append(
            f'- submit candidate: ref={_ref_for_prompt(submit_field)} label="{_label_for_prompt(submit_field)}"'
        )
    if background_mutations:
        auth_lines.append(
            "- background CTA: "
            + ", ".join(
                f'ref={_ref_for_prompt(el)} "{_label_for_prompt(el)}"'
                for el in background_mutations
            )
            + " <- 인증 surface가 보이는 동안에는 뒤쪽 페이지 CTA일 가능성이 높습니다."
        )
    return "\n".join(auth_lines)


def _build_feedback_signal_summary(agent: Any, dom_elements: List[DOMElement]) -> str:
    signal_element: Optional[DOMElement] = None
    signal_kind: str = ""
    destination_reveal: Optional[DOMElement] = None
    close_element: Optional[DOMElement] = None

    for element in dom_elements or []:
        tags = set(semantic_tags_for_element(agent, element))
        if "feedback_conflict_signal" in tags and signal_element is None:
            signal_element = element
            signal_kind = "conflict"
        elif "feedback_success_signal" in tags and signal_element is None:
            signal_element = element
            signal_kind = "success"
        if "destination_reveal_candidate" in tags and destination_reveal is None:
            destination_reveal = element
        if "close_like" in tags and close_element is None:
            close_element = element

    if signal_element is None:
        return ""

    signal_lines = [
        "## 현재 결과/경고 신호",
        f'- result signal: ref={_ref_for_prompt(signal_element)} label="{_label_for_prompt(signal_element)}"',
    ]
    if destination_reveal is not None:
        signal_lines.append(
            f'- inspect destination: ref={_ref_for_prompt(destination_reveal)} label="{_label_for_prompt(destination_reveal)}"'
        )
    if close_element is not None:
        signal_lines.append(
            f'- dismiss only: ref={_ref_for_prompt(close_element)} label="{_label_for_prompt(close_element)}"'
        )
    signal_blob = agent._normalize_text(_label_for_prompt(signal_element))
    target_terms = [
        agent._normalize_text(term)
        for term in list(getattr(getattr(agent, "_goal_semantics", None), "target_terms", []) or [])
        if str(term or "").strip()
    ]
    target_hit = any(term and term in signal_blob for term in target_terms)
    if signal_blob and not target_hit:
        signal_lines.append(
            "- warning: 이 피드백은 목표 과목명이 아니라 다른 과목/상태를 가리킬 수 있습니다. 목표 과목이 목적지에 실제로 보이지 않으면 이 신호만으로 삭제 대상을 정하지 마세요."
        )
    if signal_kind == "success":
        signal_lines.append(
            "- 직전 mutation의 성공 토스트/스낵바는 약한 진행 신호입니다. 목표가 시간표/목록 반영 확인이면 토스트만 보고 완료나 wait로 멈추지 말고, 목적지 reveal/counter/row 같은 지속 증거를 먼저 확인하세요."
        )
    else:
        signal_lines.append(
            "- 직전 mutation 뒤에 충돌/중복/시간겹침 신호가 뜨면, 닫기보다 현재 시간표/목록 상태를 먼저 확인하는 쪽이 목표 판정에 더 직접적입니다."
        )
    return "\n".join(signal_lines)


def _build_active_surface_summary(agent: Any, dom_elements: List[DOMElement]) -> str:
    surface_context = detect_active_surface_context(agent, dom_elements or [])
    if not surface_context.get("active"):
        return ""

    heading = surface_context.get("heading")
    action_elements = list(surface_context.get("action_elements") or [])
    close_candidate = surface_context.get("close_candidate")
    background_elements = list(surface_context.get("background_elements") or [])

    lines = [
        "## 현재 전경 surface",
        f'- active surface: ref={_ref_for_prompt(heading)} label="{_label_for_prompt(heading)}"',
    ]
    if action_elements:
        lines.append(
            "- in-surface actions: "
            + ", ".join(
                f'ref={_ref_for_prompt(el)} "{_label_for_prompt(el)}"'
                for el in action_elements[:4]
            )
        )
    if close_candidate is not None:
        lines.append(
            f'- exit surface: ref={_ref_for_prompt(close_candidate)} label="{_label_for_prompt(close_candidate)}"'
        )
    if background_elements:
        lines.append(
            "- background CTA behind surface: "
            + ", ".join(
                f'ref={_ref_for_prompt(el)} "{_label_for_prompt(el)}"'
                for el in background_elements[:3]
            )
        )
    lines.append(
        "- 현재 foreground surface가 열린 동안 배경 CTA는 DOM에 보여도 `not visible`로 실패할 수 있습니다. 배경 검색결과를 쓰려면 먼저 현재 surface를 닫거나 벗어나세요."
    )
    return "\n".join(lines)


def _build_target_destination_summary(agent: Any, dom_elements: List[DOMElement]) -> str:
    target_indices: List[int] = []
    remove_indices: List[int] = []

    def _is_source_like(el: DOMElement) -> bool:
        blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(el, "container_name", "") or ""),
                    str(getattr(el, "context_text", "") or ""),
                ]
            )
        )
        return any(token in blob for token in ("검색 결과", "search result", "result list"))

    for index, element in enumerate(dom_elements or []):
        tags = set(semantic_tags_for_element(agent, element))
        if "destination_remove_candidate" in tags:
            remove_indices.append(index)
        if "target_match" in tags and not _is_source_like(element):
            target_indices.append(index)

    if not target_indices:
        return ""

    target_index = target_indices[0]
    target_element = dom_elements[target_index]
    preferred_remove_index = next((idx for idx in remove_indices if idx > target_index), None)
    if preferred_remove_index is None:
        before_candidates = [idx for idx in remove_indices if idx < target_index]
        preferred_remove_index = before_candidates[-1] if before_candidates else None
    preferred_remove = dom_elements[preferred_remove_index] if preferred_remove_index is not None else None

    lines = [
        "## 목표 대상 상태",
        f'- target evidence in destination: ref={_ref_for_prompt(target_element)} label="{_label_for_prompt(target_element)}"',
    ]
    if preferred_remove is not None:
        lines.append(
            f'- preferred target-row remove candidate: ref={_ref_for_prompt(preferred_remove)} label="{_label_for_prompt(preferred_remove)}"'
        )
    lines.append(
        "- 삭제가 필요하면 목표 과목 행에 직접 연결된 제거 버튼만 사용하세요. 충돌 토스트에 나온 다른 과목명을 제거 대상으로 해석하지 마세요."
    )
    return "\n".join(lines)


def _selected_element_from_decision(
    agent: Any,
    decision: ActionDecision,
    dom_elements: List[DOMElement],
) -> Optional[DOMElement]:
    if getattr(decision, "ref_id", None):
        selected = next(
            (
                el for el in (dom_elements or [])
                if str(getattr(el, "ref_id", "") or "").strip() == str(getattr(decision, "ref_id", "") or "").strip()
            ),
            None,
        )
        if selected is not None:
            return selected
    return next(
        (el for el in (dom_elements or []) if int(getattr(el, "id", -1)) == int(decision.element_id or -9999)),
        None,
    )


def _is_forbidden_global_control(agent: Any, element: Optional[DOMElement], decision: ActionDecision) -> bool:
    if element is None or decision.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.SELECT}:
        return False
    semantic_tags = set(semantic_tags_for_element(agent, element))
    if semantic_tags.intersection(
        {
            "destination_reveal_candidate",
            "destination_remove_candidate",
            "target_row_secondary_reveal_candidate",
            "surface_close_candidate",
        }
    ):
        return False
    blob = agent._normalize_text(
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
    return any(
        token in blob
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
    )


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
        "path": "agentic_wrapper",
        "owner": "gaia_pre_llm",
    }
    current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
    current_phase_intent = str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase))
    thin_wrapper_mode = _thin_wrapper_mode(agent)
    wrapper_mode = wrapper_mode_name(agent)
    goal_state = sync_goal_replanning_state(
        agent,
        goal=goal,
        dom_elements=dom_elements,
        current_phase=current_phase,
        current_intent=current_phase_intent,
        event="decision_turn",
    )
    goal_state_summary, goal_state_trace = _build_goal_state_summary(
        goal_state,
        thin_wrapper_mode=thin_wrapper_mode,
    )
    auth_phase_active = bool(
        current_phase == "handle_auth_or_block"
        or bool((getattr(agent, "_last_snapshot_evidence", {}) or {}).get("auth_prompt_visible"))
        or _has_auth_surface(agent, dom_elements or [])
    )
    prompt_test_data = goal.test_data if auth_phase_active and isinstance(goal.test_data, dict) else {}
    auth_surface_summary = _build_auth_surface_summary(agent, dom_elements or [], prompt_test_data) if auth_phase_active else ""
    feedback_signal_summary = _build_feedback_signal_summary(agent, dom_elements or [])
    active_surface_summary = _build_active_surface_summary(agent, dom_elements or [])
    target_destination_summary = _build_target_destination_summary(agent, dom_elements or [])
    elements_for_prompt = list(dom_elements or [])
    elements_text = agent._format_dom_for_llm(elements_for_prompt)
    recent_block_text = ", ".join(str(x) for x in (getattr(agent, "_recent_click_element_ids", []) or [])[-8:]) or "없음"
    state_cache_title = "현재 wrapper 관찰값(약한 힌트)" if thin_wrapper_mode else "현재 상태 요약(약한 힌트)"
    semantic_hint_rule = """
## 후보 의미 힌트
- 각 DOM 줄의 `semantics=[...]`는 wrapper가 붙인 약한 힌트입니다. 정답으로 확정하지 말고 현재 DOM 문맥으로 다시 검증하세요.
- `destination_reveal_candidate`와 `close_like`가 함께 보이면 닫기/취소 계열일 가능성을 먼저 의심하세요.
- `source_mutation_candidate`가 보여도 최근 피드백이 no-op이거나 duplicate 경고가 있으면 같은 CTA를 반복하지 마세요.
- `auth_identifier_field`, `auth_password_field`, `auth_submit_candidate`는 로그인 surface 안에서만 참고할 약한 힌트입니다.
- `surface_close_candidate`는 현재 foreground surface를 닫고 배경으로 돌아가는 약한 힌트입니다.
- `occluded_background_candidate`는 DOM에 보여도 현재 surface 뒤에 가려져 클릭 실패할 수 있습니다.
- 상태 요약이 `불확실`이면 wrapper belief를 버리고 현재 DOM과 스크린샷만으로 판단하세요.
"""
    prompt = f"""당신은 OpenClaw 스타일의 웹 작업 에이전트입니다.
현재 화면과 직전 결과를 다시 읽고, 다음 한 단계만 결정하세요.

## 목표
- 이름: {goal.name}
- 설명: {goal.description}
- 성공 조건: {', '.join(goal.success_criteria)}
- 실패 조건: {', '.join(goal.failure_criteria) if goal.failure_criteria else '없음'}

## 사용 가능한 테스트 데이터
{json.dumps(prompt_test_data, ensure_ascii=False, indent=2)}

{auth_surface_summary if auth_surface_summary else ''}
{feedback_signal_summary if feedback_signal_summary else ''}
{active_surface_summary if active_surface_summary else ''}
{target_destination_summary if target_destination_summary else ''}

## 최근 액션 기록
{chr(10).join(agent._action_history[-5:]) if agent._action_history else '없음'}

## 최근 실행 피드백
{chr(10).join(agent._action_feedback[-5:]) if agent._action_feedback else '없음'}

## 최근 반복 클릭 element_id
{recent_block_text}

## 도메인 실행 기억(KB)
{memory_context or '없음'}

## {state_cache_title}
{goal_state_summary}

## 현재 화면의 DOM 요소와 목표 관련 증거
{elements_text}

{semantic_hint_rule}

## 작업 규칙
1. phase 이름이나 wrapper 상태보다 최신 DOM/스크린샷을 우선 신뢰하세요.
2. 목표 과목/대상과 직접 연결된 카드, 행(row/slot/card), 버튼을 먼저 찾으세요.
3. source/destination 중 어느 쪽이든 현재 화면에서 직접 연결된 증거가 더 강한 쪽을 고르세요.
4. 로그인/인증 surface가 보이면 현재 surface를 새 화면으로 간주하고, 제공된 테스트 데이터가 있으면 그 화면 안에서만 처리하세요.
5. 최근 피드백이 no-op/duplicate/이미 추가됨이면 같은 CTA를 반복하지 말고 다른 직접 증거나 다른 경로를 찾으세요.
6. 모달/오버레이가 실제로 열려 있지 않다면 닫기/close/dismiss를 고르지 마세요.
7. 로그아웃, 다운로드, PDF 저장, 전체삭제 같은 전역/파괴적 컨트롤은 목표가 직접 요구하지 않는 한 선택하지 마세요.
8. 방금 뜬 성공 토스트/스낵바/배너는 임시 피드백일 수 있습니다. 목표가 목록/시간표 반영 확인이면 destination row, counter, reveal surface 같은 지속 증거를 먼저 찾고, 그런 증거 없이는 완료나 wait로 멈추지 마세요.
9. 목표가 이미 달성됐다고 판단되면 `is_goal_achieved=true`와 이유를 반환하세요.
10. DOM 요소에 `[ref=...]`가 표시된 경우 반드시 해당 `ref_id`를 응답에 포함하세요. `element_id`는 없으면 null이어도 됩니다. DOM 리스트에 없는 ref_id나 element_id를 추측하지 마세요.

## 응답 형식 (JSON만, 마크다운 없이)
{{
    \"action\": \"click\" | \"fill\" | \"press\" | \"scroll\" | \"wait\" | \"select\",
    \"ref_id\": 요소 ref ID (문자열, DOM에 [ref=...]로 표시된 값을 우선 사용),
    \"element_id\": 요소ID (숫자, 없으면 null 허용),
    \"value\": \"입력값 (fill), 키 이름 (press), select 값(문자열/콤마구분/JSON 배열), wait 조건(JSON 또는 ms)\",
    \"reasoning\": \"현재 화면 기준으로 이 행동이 왜 다음 단계인지\",
    \"confidence\": 0.0~1.0,
    \"is_goal_achieved\": true | false,
    \"goal_achievement_reason\": \"목표 달성 판단 이유 (is_goal_achieved가 true인 경우)\"
}}

JSON 응답:"""

    try:
        dump_wrapper_trace(
            agent,
            kind="pre_decision",
            payload={
                "goal": {
                    "id": getattr(goal, "id", ""),
                    "name": getattr(goal, "name", ""),
                    "description": getattr(goal, "description", ""),
                },
                "runtime_phase": str(getattr(agent, "_runtime_phase", "") or ""),
                "goal_policy_phase": current_phase,
                "goal_phase_intent": current_phase_intent,
                "goal_state": goal_state,
                "goal_state_summary": goal_state_summary,
                "elements_text": elements_text,
                "prompt": prompt,
                "prompt_mode": "agentic",
                "elements": serialize_dom_elements(elements_for_prompt, agent=agent),
                "prompt_elements": serialize_dom_elements(elements_for_prompt, agent=agent),
                "recent_action_history": list(getattr(agent, "_action_history", []) or [])[-5:],
                "recent_action_feedback": list(getattr(agent, "_action_feedback", []) or [])[-5:],
                "llm_path": "vision" if screenshot else "text_only",
                "uses_openclaw_backend": str(getattr(agent, "_browser_backend_name", "") or "").strip().lower() == "openclaw",
                "agentic_wrapper_mode": True,
                "wrapper_mode": wrapper_mode,
                "goal_state_trace": goal_state_trace,
            },
        )
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
        dump_wrapper_trace(
            agent,
            kind="post_decision",
            payload={
                "goal_policy_phase": current_phase,
                "goal_phase_intent": current_phase_intent,
                "goal_state": goal_state,
                "goal_state_summary": goal_state_summary,
                "raw_response": response_text,
                "prompt_mode": "agentic",
                "parsed_decision": decision.model_dump() if hasattr(decision, "model_dump") else str(decision),
                "llm_trace": dict(getattr(agent, "_last_llm_trace", {}) or {}),
                "elements": serialize_dom_elements(elements_for_prompt, agent=agent),
                "prompt_elements": serialize_dom_elements(elements_for_prompt, agent=agent),
                "agentic_wrapper_mode": True,
                "wrapper_mode": wrapper_mode,
            },
        )
        selected_element = _selected_element_from_decision(agent, decision, elements_for_prompt)
        if selected_element is not None:
            ref_id = str((getattr(agent, "_element_ref_ids", {}) or {}).get(getattr(selected_element, "id", -1)) or "").strip()
            decision_ref_id = str(getattr(decision, "ref_id", "") or "").strip()
            line_parts = [f"[{getattr(selected_element, 'id', None)}] <{getattr(selected_element, 'tag', '') or ''}>"]
            if decision_ref_id:
                line_parts.append(f'decision-ref="{decision_ref_id}"')
            if getattr(selected_element, "container_name", None):
                line_parts.append(f'within="{getattr(selected_element, "container_name", "")}"')
            if getattr(selected_element, "text", None):
                line_parts.append(f'"{getattr(selected_element, "text", "")}"')
            if getattr(selected_element, "context_text", None):
                line_parts.append(f'context="{getattr(selected_element, "context_text", "")}"')
            line_parts.append(f"ref_id={ref_id or '<none>'}")
            agent._log("🧪 selected-element trace: " + " ".join(line_parts))
        if _is_forbidden_global_control(agent, selected_element, decision):
            if callable(getattr(agent, "_record_reason_code", None)):
                agent._record_reason_code("openclaw_forbidden_global_control")
            return ActionDecision(
                action=ActionType.WAIT,
                value='{"time_ms": 400}',
                reasoning="전역 또는 파괴적 컨트롤로 보여 재계획합니다.",
                confidence=0.9,
            )
        return decision
    except Exception as exc:
        agent._last_llm_trace = {
            "used_llm": True,
            "llm_ms": int((time.perf_counter() - llm_started) * 1000) if "llm_started" in locals() else 0,
            "path": "exception",
            "owner": "llm",
        }
        agent._log(f"🧪 llm trace: {agent._last_llm_trace}")
        agent._log(f"LLM 결정 실패: {exc}")
        return ActionDecision(
            action=ActionType.WAIT,
            reasoning=f"LLM 오류: {exc}",
            confidence=0.0,
        )
