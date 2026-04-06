from __future__ import annotations

import json
import time
from typing import Any, List, Optional

from .browser_action_rules import (
    build_browser_action_rules_for_agent,
    slice_recent_prompt_items,
)
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
    identifier_candidates: List[DOMElement] = []
    password_candidates: List[DOMElement] = []
    submit_candidates: List[DOMElement] = []
    background_mutations: List[DOMElement] = []

    def _auth_candidate_score(element: DOMElement) -> tuple[int, int]:
        blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(element, "text", "") or ""),
                    str(getattr(element, "placeholder", "") or ""),
                    str(getattr(element, "aria_label", "") or ""),
                    str(getattr(element, "title", "") or ""),
                    str(getattr(element, "role_ref_name", "") or ""),
                    str(getattr(element, "container_name", "") or ""),
                    str(getattr(element, "context_text", "") or ""),
                ]
            )
        )
        score = 0
        if any(token in blob for token in ("아이디", "username", "email", "비밀번호", "password", "로그인")):
            score += 4
        if "로그인" in str(getattr(element, "container_name", "") or ""):
            score += 4
        if "아이디" in blob and "비밀번호" in blob:
            score += 2
        if "과목 검색" in blob:
            score -= 3
        if "바로 추가" in blob:
            score -= 4
        return score, -int(getattr(element, "id", 0) or 0)

    for element in dom_elements or []:
        tags = set(semantic_tags_for_element(agent, element))
        if "auth_identifier_field" in tags:
            identifier_candidates.append(element)
        if "auth_password_field" in tags:
            password_candidates.append(element)
        if "auth_submit_candidate" in tags:
            submit_candidates.append(element)
        if "source_mutation_candidate" in tags and len(background_mutations) < 3:
            background_mutations.append(element)

    identifier_field = max(identifier_candidates, key=_auth_candidate_score, default=None)
    password_field = max(password_candidates, key=_auth_candidate_score, default=None)
    submit_field = max(submit_candidates, key=_auth_candidate_score, default=None)

    if not any((identifier_field, password_field, submit_field)):
        return ""

    auth_lines = ["## 현재 인증 surface"]
    if prompt_test_data.get("username") or prompt_test_data.get("email") or prompt_test_data.get("user_id") or prompt_test_data.get("password"):
        auth_lines.append('- `fill_with="..."`는 현재 입력값이 아니라, 이 필드에 직접 타이핑해야 할 자격증명입니다.')
    if identifier_field is not None:
        identifier_value = prompt_test_data.get("username") or prompt_test_data.get("email") or prompt_test_data.get("user_id")
        identifier_suffix = f' fill_with="{identifier_value}"' if identifier_value else ""
        auth_lines.append(
            f'- identifier input: ref={_ref_for_prompt(identifier_field)} label="{_label_for_prompt(identifier_field)}"{identifier_suffix}'
        )
    if password_field is not None:
        password_value = prompt_test_data.get("password")
        password_suffix = f' fill_with="{password_value}"' if password_value else ""
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
    wrapper_observation_lines = [
        summary
        for summary in (
            auth_surface_summary,
            feedback_signal_summary,
            active_surface_summary,
            target_destination_summary,
        )
        if summary
    ]
    wrapper_observation_block = "\n".join(wrapper_observation_lines)
    elements_for_prompt = list(dom_elements or [])
    elements_text = agent._format_dom_for_llm(elements_for_prompt)
    backend_name = str(getattr(agent, "_browser_backend_name", "") or "").strip().lower()
    recent_block_text = ", ".join(str(x) for x in (getattr(agent, "_recent_click_element_ids", []) or [])[-8:]) or "없음"
    recent_action_history = slice_recent_prompt_items(
        list(getattr(agent, "_action_history", []) or []),
        default=5,
    )
    recent_action_feedback = slice_recent_prompt_items(
        list(getattr(agent, "_action_feedback", []) or []),
        default=5,
    )
    state_cache_title = "현재 wrapper 관찰값(약한 힌트)" if thin_wrapper_mode else "현재 상태 요약(약한 힌트)"
    pre_dom_wrapper_observation_block = ""
    post_dom_wrapper_observation_block = wrapper_observation_block
    if not (backend_name == "openclaw" and thin_wrapper_mode):
        pre_dom_wrapper_observation_block = wrapper_observation_block
        post_dom_wrapper_observation_block = ""
    post_dom_wrapper_observation_section = (
        f"## Wrapper 보조 관찰(후순위)\n{post_dom_wrapper_observation_block}"
        if post_dom_wrapper_observation_block
        else ""
    )
    semantic_hint_rule = """
## 후보 의미 힌트
- 각 DOM 줄의 `semantics=[...]`는 wrapper가 붙인 약한 힌트입니다. 정답으로 확정하지 말고 현재 DOM 문맥으로 다시 검증하세요.
- `destination_reveal_candidate`와 `close_like`가 함께 보이면 닫기/취소 계열일 가능성을 먼저 의심하세요.
- `source_mutation_candidate`가 보여도 최근 피드백이 no-op이거나 duplicate 경고가 있으면 같은 CTA를 반복하지 마세요.
- `auth_identifier_field`, `auth_password_field`, `auth_submit_candidate`는 로그인 surface 안에서만 참고할 약한 힌트입니다.
- 인증 surface 요약에 `fill_with="..."`가 보이면 그것은 현재 DOM 값이 아니라, 그 입력칸에 넣어야 할 자격증명입니다.
- 인증 surface 안에 identifier/password 입력 ref와 `fill_with`가 함께 보이면, 방금 그 ref를 채운 직후가 아닌 한 submit보다 fill을 우선하세요.
- `surface_close_candidate`는 현재 foreground surface를 닫고 배경으로 돌아가는 약한 힌트입니다.
- `occluded_background_candidate`는 DOM에 보여도 현재 surface 뒤에 가려져 클릭 실패할 수 있습니다.
- 상태 요약이 `불확실`이면 wrapper belief를 버리고 현재 DOM과 스크린샷만으로 판단하세요.
"""
    openclaw_primary_rule = """
## OpenClaw 원본 우선 규칙
- `## OpenClaw 원본 역할 트리 (주 입력)`은 wrapper가 재가공하기 전 OpenClaw snapshot 발췌입니다. action을 고를 때 가장 먼저 신뢰하세요.
- `## 구조화 보조 힌트`와 `semantics=[...]`는 2차 힌트입니다. 원본 role tree의 ref/role/name/트리 위치와 충돌하면 원본 역할 트리를 우선하세요.
- 같은 이름 CTA가 여러 개면 `ref`, 트리 위치, 같은 row/section 주변 raw line으로 구분하세요.
""" if backend_name == "openclaw" else ""
    browser_action_rules_block = build_browser_action_rules_for_agent(agent)
    prompt = f"""당신은 OpenClaw 스타일의 웹 작업 에이전트입니다.
현재 화면과 직전 결과를 다시 읽고, 다음 한 단계만 결정하세요.

## 목표
- 이름: {goal.name}
- 설명: {goal.description}
- 성공 조건: {', '.join(goal.success_criteria)}
- 실패 조건: {', '.join(goal.failure_criteria) if goal.failure_criteria else '없음'}

## 사용 가능한 테스트 데이터
{json.dumps(prompt_test_data, ensure_ascii=False, indent=2)}

{pre_dom_wrapper_observation_block}

## 최근 액션 기록
{chr(10).join(recent_action_history) if recent_action_history else '없음'}

## 최근 실행 피드백
{chr(10).join(recent_action_feedback) if recent_action_feedback else '없음'}

## 최근 반복 클릭 element_id
{recent_block_text}

## 도메인 실행 기억(KB)
{memory_context or '없음'}

## {state_cache_title}
{goal_state_summary}

## 현재 화면의 DOM 요소와 목표 관련 증거
{elements_text}

{post_dom_wrapper_observation_section}

{openclaw_primary_rule}
{semantic_hint_rule}

{browser_action_rules_block}

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
                "recent_action_history": recent_action_history,
                "recent_action_feedback": recent_action_feedback,
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
