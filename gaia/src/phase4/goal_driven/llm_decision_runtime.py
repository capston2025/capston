from __future__ import annotations

import json
import os
import time
from typing import Any, List, Optional

from .browser_action_rules import (
    build_browser_action_rules_for_agent,
    slice_recent_prompt_items,
)
from .dom_prompt_formatting import detect_active_surface_context, semantic_tags_for_element
from .goal_policy_phase_runtime import goal_phase_intent
from .goal_completion_helpers import build_text_evidence_memory_block
from .goal_replanning_runtime import sync_goal_replanning_state
from .media_playback_helpers import (
    collect_visible_play_controls,
    describe_play_control,
    dom_has_media_player_surface,
    goal_requires_media_playback,
)
from .models import ActionDecision, ActionType, DOMElement, TestGoal
from .multi_user_interaction_runtime import (
    build_multi_user_interaction_skill_prompt,
    build_participant_prompt_block,
    participant_test_data_for_prompt,
)
from .run_history_runtime import (
    build_run_history_replay_packet_context as build_run_history_replay_packet_context_impl,
    record_run_history_transcript as record_run_history_transcript_impl,
)
from .wrapper_trace_runtime import dump_wrapper_trace, serialize_dom_elements, thin_wrapper_enabled, wrapper_mode_name


def _thin_wrapper_mode(agent: Any) -> bool:
    return thin_wrapper_enabled(agent)


def _llm_decision_retry_attempts() -> int:
    raw = str(os.getenv("GAIA_LLM_DECISION_RETRY_ATTEMPTS", "1") or "1").strip()
    try:
        attempts = int(raw)
    except Exception:
        attempts = 1
    return max(0, min(attempts, 2))


def _llm_decision_retry_delay_seconds() -> float:
    raw = str(os.getenv("GAIA_LLM_DECISION_RETRY_DELAY_MS", "800") or "800").strip()
    try:
        delay_ms = int(raw)
    except Exception:
        delay_ms = 800
    return max(0, min(delay_ms, 3000)) / 1000.0


def _llm_decision_retryable_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    hard_fail_tokens = (
        "insufficient_quota",
        "quota exceeded",
        "resource_exhausted",
        "invalid_api_key",
        "incorrect api key",
        "forbidden",
        "403",
        "unexpected argument",
        "not valid utf-8",
    )
    if any(token in text for token in hard_fail_tokens):
        return False
    retryable_tokens = (
        "authentication",
        "unauthorized",
        "401",
        "empty_response_from_codex_exec",
        "empty_response_from_model",
        "codex_exec_timeout",
        "timeout",
        "timed out",
        "connection reset",
        "temporarily unavailable",
        "econnreset",
    )
    return any(token in text for token in retryable_tokens)


def _call_llm_decision_with_retry(
    agent: Any,
    *,
    prompt: str,
    screenshot: Optional[str],
) -> str:
    max_retries = _llm_decision_retry_attempts()
    attempts_total = max_retries + 1
    last_exc: Optional[Exception] = None
    for attempt_index in range(attempts_total):
        try:
            if screenshot:
                return agent.llm.analyze_with_vision(prompt, screenshot)
            return agent._call_llm_text_only(prompt)
        except Exception as exc:
            last_exc = exc
            if attempt_index >= max_retries or not _llm_decision_retryable_error(exc):
                raise
            log = getattr(agent, "_log", None)
            if callable(log):
                log(
                    "♻️ LLM 결정 호출 일시 오류 감지: "
                    f"{exc} — {attempt_index + 1}/{max_retries}회 재호출합니다."
                )
            time.sleep(_llm_decision_retry_delay_seconds())
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("LLM decision call failed without exception")


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
        "- 현재 foreground surface가 목표와 무관하게 진행을 실제로 막고 있을 때만 먼저 닫거나 벗어나세요. 임시 성공 토스트/배너처럼 배경 진행을 막지 않는 약한 신호라면 닫기보다 원래 목표 진행을 우선하세요."
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


def _build_new_page_signal_summary(agent: Any) -> str:
    exec_result = getattr(agent, "_last_exec_result", None)
    state_change = getattr(exec_result, "state_change", None)
    if not isinstance(state_change, dict) or not bool(state_change.get("new_page_detected")):
        return ""

    try:
        new_page_count = int(state_change.get("new_page_count") or 0)
    except Exception:
        new_page_count = 0
    try:
        same_origin_count = int(state_change.get("new_page_same_origin_count") or 0)
    except Exception:
        same_origin_count = 0

    lines = ["## 직전 액션 이후 새 창/페이지 신호"]
    if new_page_count > 0:
        lines.append(f"- new page count: {new_page_count}")
    if same_origin_count > 0:
        lines.append(f"- same-origin new pages: {same_origin_count}")

    raw_new_pages = state_change.get("new_pages") if isinstance(state_change.get("new_pages"), list) else []
    if raw_new_pages:
        for idx, item in enumerate(raw_new_pages[:3], start=1):
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("target_id") or item.get("tab_id") or "").strip()
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            kind_guess = str(item.get("kind_guess") or "").strip()
            same_origin = bool(item.get("same_origin"))
            details = [
                f"target_id={target_id}" if target_id else "",
                f"url={url}" if url else "",
                f'title="{title}"' if title else "",
                f"kind={kind_guess}" if kind_guess else "",
                "same_origin=true" if same_origin else "same_origin=false",
            ]
            lines.append(f"- candidate {idx}: " + " ".join(part for part in details if part))
    else:
        urls = state_change.get("new_page_urls") if isinstance(state_change.get("new_page_urls"), list) else []
        titles = state_change.get("new_page_titles") if isinstance(state_change.get("new_page_titles"), list) else []
        kinds = state_change.get("new_page_kinds") if isinstance(state_change.get("new_page_kinds"), list) else []
        for idx, raw_url in enumerate(urls[:3], start=1):
            url = str(raw_url or "").strip()
            title = str(titles[idx - 1] or "").strip() if idx - 1 < len(titles) else ""
            kind_guess = str(kinds[idx - 1] or "").strip() if idx - 1 < len(kinds) else ""
            details = [
                f"url={url}" if url else "",
                f'title="{title}"' if title else "",
                f"kind={kind_guess}" if kind_guess else "",
            ]
            lines.append(f"- candidate {idx}: " + " ".join(part for part in details if part))

    lines.append(
        "- 이 신호는 직전 클릭의 후속 결과일 수 있습니다. 현재 goal과 직접 관련된 same-origin viewer/help 창이라면 "
        "기존 opener CTA를 반복하기 전에 그 창이 새 작업 surface인지 먼저 고려하세요. "
        "그 창으로 전환해야 하면 `action=\"focus\"`와 candidate의 `target_id`를 사용하세요."
    )
    return "\n".join(lines)


def _compact_self_state_text(value: Any, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _build_self_state_memory_block(agent: Any) -> str:
    """Summarize the agent's own recent state beliefs for the next decision.

    This is intentionally not a step checklist. It is a short continuity layer
    derived from actions that actually executed in this run so the next LLM call
    does not re-infer state only from whatever controls are visible now.
    """

    recent_actions = [
        _compact_self_state_text(item, limit=260)
        for item in list(getattr(agent, "_action_history", []) or [])[-8:]
        if str(item or "").strip()
    ]
    persistent_inputs = []
    for item in list(getattr(agent, "_persistent_state_memory", []) or [])[-5:]:
        if not isinstance(item, dict):
            continue
        value = _compact_self_state_text(item.get("expected_value"), limit=80)
        if not value:
            continue
        context = _compact_self_state_text(
            item.get("context_text") or item.get("container_name") or item.get("role_ref_name"),
            limit=140,
        )
        kind = _compact_self_state_text(item.get("kind"), limit=24) or "input"
        persistent_inputs.append(
            {
                "kind": kind,
                "value": value,
                "context": context,
            }
        )

    if not recent_actions and not persistent_inputs:
        return ""

    lines = [
        "## 작업 자기 상태 메모리",
        "- 이 블록은 목표 checklist가 아니라, 이 run에서 실제로 수행한 행동으로부터 만든 현재 belief입니다.",
        "- 최신 DOM/URL/명시적 오류가 이 belief를 반박하지 않으면, 같은 의미의 검색/선택/탭 전환을 반복하지 마세요.",
        "- 검색창이나 최근 검색어가 다시 보여도, 아래에 같은 query/result 선택이 이미 있으면 `아직 검색 전`으로 되돌아가지 말고 다음 미해결 상태를 찾으세요.",
    ]
    if recent_actions:
        lines.append("- recent effective actions:")
        lines.extend(f"  - {item}" for item in recent_actions[-6:])
    if persistent_inputs:
        lines.append("- committed input/select beliefs:")
        for item in persistent_inputs:
            suffix = f" | context={item['context']}" if item.get("context") else ""
            lines.append(f"  - {item['kind']}: {item['value']}{suffix}")
    lines.append(
        "- 다음 판단 순서: 현재 화면이 belief를 반박하는가? 아니면 이미 한 행동을 반복하려는가? "
        "반복이라면 inspect/다음 필터/다음 tab처럼 새로운 상태 확인으로 전환하세요."
    )
    return "\n".join(lines)


def _build_media_playback_signal_summary(
    agent: Any,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> str:
    if not goal_requires_media_playback(agent.__class__, goal):
        return ""

    play_controls = collect_visible_play_controls(agent.__class__, dom_elements or [], limit=3)
    if not play_controls:
        return ""

    lines = ["## media/player 재생 신호"]
    if dom_has_media_player_surface(agent.__class__, dom_elements or []):
        lines.append("- current surface looks like a media/player viewer.")
    for idx, element in enumerate(play_controls, start=1):
        lines.append(f"- play candidate {idx}: {describe_play_control(element)}")
    lines.append(
        "- 목표가 재생/play/watch/listen을 직접 요구합니다. viewer surface 진입만으로 완료 처리하지 말고 "
        "가능한 경우 위 play candidate를 먼저 실행하세요."
    )
    lines.append(
        "- 위 play/start control 클릭이 목표의 마지막 단계라면 해당 click action에서 "
        "`is_goal_achieved=true`를 함께 반환할 수 있습니다."
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
    logout_tokens = ("로그아웃", "logout", "log out", "sign out", "signout")
    if any(token in blob for token in logout_tokens):
        goal_allows_logout = getattr(agent, "_goal_allows_logout", None)
        if callable(goal_allows_logout):
            try:
                if bool(goal_allows_logout()):
                    return False
            except Exception:
                pass
        return True
    return any(
        token in blob
        for token in (
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
        "vision_policy": dict(getattr(agent, "_last_vision_policy_trace", {}) or {}),
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
    prompt_test_data = participant_test_data_for_prompt(agent, goal) if auth_phase_active else {}
    auth_surface_summary = _build_auth_surface_summary(agent, dom_elements or [], prompt_test_data) if auth_phase_active else ""
    feedback_signal_summary = _build_feedback_signal_summary(agent, dom_elements or [])
    new_page_signal_summary = _build_new_page_signal_summary(agent)
    media_playback_signal_summary = _build_media_playback_signal_summary(agent, goal, dom_elements or [])
    active_surface_summary = _build_active_surface_summary(agent, dom_elements or [])
    target_destination_summary = _build_target_destination_summary(agent, dom_elements or [])
    wrapper_observation_lines = [
        summary
        for summary in (
            auth_surface_summary,
            feedback_signal_summary,
            new_page_signal_summary,
            media_playback_signal_summary,
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
    self_state_memory_block = _build_self_state_memory_block(agent)
    self_state_prompt_block = self_state_memory_block or "## 작업 자기 상태 메모리\n없음"
    text_evidence_memory_block = build_text_evidence_memory_block(agent, max_entries=4, max_lines_per_entry=8)
    text_evidence_prompt_block = text_evidence_memory_block or "## 누적 텍스트 evidence\n없음"
    run_history_replay_packet = build_run_history_replay_packet_context_impl(agent, goal=goal)
    run_history_replay_block = run_history_replay_packet or "## 세션 continuity replay packet\n없음"
    participant_skill_prompt = build_multi_user_interaction_skill_prompt()
    participant_prompt_block = build_participant_prompt_block(agent)
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
    visual_input_block = (
        "## 시각 입력 상태\n- screenshot: 제공됨. DOM/ref와 함께 현재 화면 증거로 사용하세요."
        if screenshot
        else (
            "## 시각 입력 상태\n"
            "- screenshot: 제공되지 않음. 현재 판단은 DOM/role tree와 실행 피드백만으로 수행하세요.\n"
            "- DOM만으로 다음 ref/action을 확정할 수 없고 실제 화면 확인이 필요하면 추측하지 말고 wait로 "
            "화면 컨텍스트 필요성을 reasoning에 명시하세요."
        )
    )
    prompt = f"""당신은 OpenClaw 스타일의 웹 작업 에이전트입니다.
현재 화면과 직전 결과를 다시 읽고, 다음 한 단계만 결정하세요.

## 목표
- 이름: {goal.name}
- 설명: {goal.description}
- 성공 조건: {', '.join(goal.success_criteria)}
- 실패 조건: {', '.join(goal.failure_criteria) if goal.failure_criteria else '없음'}

{visual_input_block}

## 사용 가능한 테스트 데이터
{json.dumps(prompt_test_data, ensure_ascii=False, indent=2)}

{pre_dom_wrapper_observation_block}

## 최근 액션 기록
{chr(10).join(recent_action_history) if recent_action_history else '없음'}

## 최근 실행 피드백
{chr(10).join(recent_action_feedback) if recent_action_feedback else '없음'}

{self_state_prompt_block}

{text_evidence_prompt_block}

{participant_prompt_block}

## 최근 반복 클릭 element_id
{recent_block_text}

## 세션 연속성 우선순위
- 1순위: replay packet 첫머리의 replay boundary, resume checklist, recent attempt digest를 먼저 읽는다.
- 2순위: session summary의 Startup Continuity Audit와 Session Start Rules를 먼저 읽는다.
- 3순위: MEMORY에서 이전 run의 recent attempts, outcome, resume hint를 읽는다.
- 4순위: retrieval hit는 현재 goal/reason_code와 직접 맞는 항목만 반영한다.
- 5순위: compact state는 보조 기록으로만 쓴다.

## 진행 위생 규칙
- mutation/수집/적용 goal에서는 새 CTA를 반복하기 전에 현재 열린 modal/overlay/panel이 목표와 무관하게 진행을 실제로 막는지 먼저 확인하세요. 막고 있을 때만 원래 작업 surface로 복귀하는 한 단계를 우선하고, 임시 성공 토스트/배너처럼 약한 신호는 닫기보다 원래 목표 진행을 우선하세요.
- 로그인/인증/OTP/보안문자/정답 입력처럼 현재 화면에서 사용자의 실제 값이 필요하지만 `사용 가능한 테스트 데이터`에 그 값이 없으면 추측하지 마세요. 이때는 아래 human_answer skill을 호출하세요.
- human_answer skill 사용법: `action`은 `wait`, `value`는 JSON 문자열/객체 `{{"skill":"human_answer","question":"사용자에게 물어볼 질문","fields":["필요한_key"],"reason_code":"human_answer_required"}}`로 응답합니다. 필요한 필드명은 현재 화면과 목표를 보고 직접 정하세요.
- human_answer는 사용자에게 묻기 위한 skill입니다. 버튼 클릭/입력으로 해결 가능한 단계에는 쓰지 말고, 모델이 알 수 없는 실제 비밀값/정답/인증값이 필요할 때만 사용하세요.
- 목표 달성 여부를 사용자에게 확인하려고 human_answer를 호출하지 마세요. 순위표/목록/기사/검색결과처럼 화면 증거로 검증 가능한 목표는 `is_goal_achieved=true`와 `goal_achievement_reason`으로 선언하면 검증 에이전트가 DOM 증거로 판정합니다.
- 목표가 여러 카드/행/댓글/기사/검색결과의 텍스트를 읽고 세거나 필드를 비교하는 목록 수집형이라고 판단되면 `collect_text_evidence=true`로 두세요. 현재 화면에서 수집할 필드(예: 제목, 출처, 시간, 요약, 댓글 본문)는 `text_evidence_focus`에 적으세요.
- `collect_text_evidence`는 action을 대체하지 않습니다. evidence를 수집하면서도 다음 단계가 필요하면 click/scroll/inspect를 그대로 선택하고, 충분히 수집했다고 판단될 때만 `is_goal_achieved=true`를 선언하세요.

{participant_skill_prompt}

{run_history_replay_block}

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
    \"action\": \"click\" | \"fill\" | \"type\" | \"inspect\" | \"focus\" | \"press\" | \"scroll\" | \"wait\" | \"select\",
    \"ref_id\": 요소 ref ID (문자열, DOM에 [ref=...]로 표시된 값을 우선 사용; inspect/focus/wait면 null 허용),
    \"element_id\": 요소ID (숫자, 없으면 null 허용; inspect/focus/wait면 null 허용),
    \"value\": \"입력값 (fill/type), inspect 질문/관찰 목적, target_id/tab_id (focus), 키 이름 (press), select 값(문자열/콤마구분/JSON 배열), wait 조건(JSON 또는 ms), 또는 human_answer skill JSON\",
    \"reasoning\": \"현재 화면 기준으로 이 행동이 왜 다음 단계인지\",
    \"confidence\": 0.0~1.0,
    \"is_goal_achieved\": true | false,
    \"goal_achievement_reason\": \"목표 달성 판단 이유 (is_goal_achieved가 true인 경우)\",
    \"collect_text_evidence\": true | false,
    \"text_evidence_reason\": \"목록/카드/댓글/기사 텍스트 evidence를 이번 턴에 누적해야 하는 이유 또는 null\",
    \"text_evidence_focus\": [\"수집할 필드/관찰 포인트\", \"예: 제목\", \"예: 출처/시간/요약\"],
    \"participant_id\": \"다중 참여자 모드에서 현재 액션을 수행할 participant id 또는 null\",
    \"next_participant\": \"현재 액션 이후 우선 실행할 participant id 또는 null\",
    \"turn_control\": {{
        \"status\": \"continue\" | \"wait_for\" | \"done\",
        \"wait_for\": [
            {{\"kind\":\"blackboard_key\", \"blackboard_key\":\"message_sent\", \"note\":\"receiver는 sender가 메시지를 보낸 뒤 확인한다\"}},
            {{\"kind\":\"timeout\", \"timeout_seconds\":10, \"note\":\"이벤트가 늦게 도착할 수 있어 짧게 재확인한다\"}}
        ],
        \"reason\": \"이 action 이후 같은 참여자를 계속 실행할지, 이벤트를 기다릴지, 종료할지\"
    }} 또는 null,
    \"participant_plan\": {{
        \"skill\": \"multi_user_interaction\",
        \"required\": true | false,
        \"reason\": \"단일 세션으로 검증할 수 없는 이유\",
        \"participants\": [
            {{\"id\":\"sender\", \"role\":\"sender\", \"display_name\":\"Sender\", \"persona\":\"메시지를 보내는 사용자\"}},
            {{\"id\":\"receiver\", \"role\":\"receiver\", \"display_name\":\"Receiver\", \"persona\":\"메시지를 받는 사용자\"}}
        ],
        \"credential_requests\": [
            {{\"participant_id\":\"sender\", \"fields\":[\"username\",\"password\"], \"required\":true}},
            {{\"participant_id\":\"receiver\", \"fields\":[\"username\",\"password\"], \"required\":true}}
        ],
        \"coordination_plan\": [\"sender가 메시지를 보낸다\", \"receiver가 수신 여부를 확인한다\"],
        \"expected_events\": [\"message_sent\", \"message_received\", \"notification_visible\"]
    }} 또는 null,
    \"blackboard_event\": \"message_sent/message_received/notification_visible 같은 공유 관찰 key 또는 null\",
    \"blackboard_payload\": {{}}
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
                "self_state_memory_block": self_state_memory_block,
                "llm_path": "vision" if screenshot else "text_only",
                "uses_openclaw_backend": str(getattr(agent, "_browser_backend_name", "") or "").strip().lower() == "openclaw",
                "agentic_wrapper_mode": True,
                "wrapper_mode": wrapper_mode,
                "goal_state_trace": goal_state_trace,
            },
        )
        record_run_history_transcript_impl(
            agent,
            stage="actor_decision_prompt",
            role="user",
            content=prompt,
            metadata={
                "goal_id": getattr(goal, "id", ""),
                "goal_name": getattr(goal, "name", ""),
                "phase": current_phase,
                "path": "vision" if screenshot else "text_only",
            },
        )
        llm_started = time.perf_counter()
        response_text = _call_llm_decision_with_retry(
            agent,
            prompt=prompt,
            screenshot=screenshot,
        )
        agent._last_llm_trace = {
            "used_llm": True,
            "llm_ms": int((time.perf_counter() - llm_started) * 1000),
            "path": "vision" if screenshot else "text_only",
            "vision_policy": dict(getattr(agent, "_last_vision_policy_trace", {}) or {}),
            "owner": "llm",
        }
        agent._log(f"🧪 llm trace: {agent._last_llm_trace}")
        record_run_history_transcript_impl(
            agent,
            stage="actor_decision_response",
            role="assistant",
            content=response_text,
            metadata={
                "goal_id": getattr(goal, "id", ""),
                "goal_name": getattr(goal, "name", ""),
                "phase": current_phase,
                "path": "vision" if screenshot else "text_only",
            },
        )
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
            "vision_policy": dict(getattr(agent, "_last_vision_policy_trace", {}) or {}),
            "owner": "llm",
        }
        agent._log(f"🧪 llm trace: {agent._last_llm_trace}")
        agent._log(f"LLM 결정 실패: {exc}")
        return ActionDecision(
            action=ActionType.WAIT,
            reasoning=f"LLM 오류: {exc}",
            confidence=0.0,
        )
