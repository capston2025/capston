from __future__ import annotations

import json
import re
import time
import hashlib
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional

from gaia.src.phase4.mcp_local_dispatch_runtime import (
    close_mcp_session,
    delete_browser_profile,
    ensure_browser_profile,
    execute_mcp_action,
)
from gaia.src.phase4.participants.models import (
    ContextMode,
    ParticipantBrowserBinding,
    ParticipantCredentialRequest,
    ParticipantPlan,
    ParticipantSpec,
    TurnControl,
    TurnControlStatus,
    WakeCondition,
    WakeConditionKind,
)
from gaia.src.phase4.participants.registry import ParticipantRegistry

from .human_answer_runtime import request_human_answer
from .models import ActionDecision, TestGoal


_SKILL_NAMES = {"multi_user_interaction", "participant_plan"}


def build_multi_user_interaction_skill_prompt() -> str:
    return """## multi_user_interaction skill
- 단일 브라우저 세션만으로 목표를 검증할 수 없을 때만 사용하세요.
- 대표 상황: 사용자 간 채팅/메시지, 친구 요청/수락, 초대, 알림 전달, buyer-seller, requester-approver, owner-collaborator, role별 표시/권한 검증.
- 버튼 라벨이나 UI 모양만 보고 하네스가 자동 생성하지 않습니다. 필요하다고 판단하면 `participant_plan.required=true`를 명시적으로 선언하세요.
- 기본값은 `participant_plan=null`입니다. 단일 유저 로그인/검색/폼 제출/필터 확인처럼 한 세션으로 검증 가능한 목표에서는 절대 required=true를 선언하지 마세요.
- participant `id`는 안정적인 actor 이름입니다. 예: `sender`, `receiver`, `approver`, `admin`, `buyer`, `seller`.
- 계정/비밀번호/OTP 같은 실제 값이 없으면 participant별 credential_requests를 선언하세요. 하네스가 `sender_username`, `sender_password`처럼 participant_id 접두사를 붙여 human_answer로 요청합니다.
- 참여자 간 공유해야 하는 관찰은 `blackboard_event`로 명시적으로 게시하세요. 예: `message_sent`, `message_received`, `notification_visible`.
- 각 action 후에는 `turn_control`을 명시하세요. 같은 참여자가 계속해야 하면 `{"status":"continue"}`, 이벤트를 기다리면 `{"status":"wait_for","wait_for":[...]}`, 끝났으면 `{"status":"done"}`입니다.
- `turn_control.status="done"`은 해당 참여자가 전체 goal에서 더 이상 행동하지 않아도 될 때만 사용하세요. 단순히 현재 턴을 마치고 다른 참여자에게 넘기는 경우에는 `next_participant`와 `wait_for`를 사용하세요.
- 다음 차례를 특정 참여자로 넘겨야 하면 `next_participant`에 그 id를 적으세요. 없으면 하네스는 다른 참여자를 자동 실행하지 않습니다."""


def build_participant_prompt_block(agent: Any) -> str:
    registry = getattr(agent, "_participant_registry", None)
    if not isinstance(registry, ParticipantRegistry):
        return ""

    active_id = str(getattr(agent, "_active_participant_id", "") or registry.active_participant_id or "")
    active = registry.participants.get(active_id) if active_id else None
    participant_lines = []
    for pid, runtime in registry.participants.items():
        binding = runtime.browser_session
        session_id = str(getattr(binding, "session_id", "") or "")
        profile_name = str(getattr(binding, "profile_name", "") or "")
        role = str(getattr(runtime.spec, "role", "") or "").strip()
        label = runtime.spec.resolved_display_name()
        current = " (current)" if pid == active_id else ""
        role_part = f", role={role}" if role else ""
        profile_part = f", profile={profile_name}" if profile_name else ""
        participant_lines.append(f"- {pid}{current}: name={label}{role_part}, session_id={session_id}{profile_part}")

    blackboard_summary = registry.blackboard.to_prompt_summary(
        active_id or "default",
        limit=12,
        name_resolver=registry.display_name_resolver(),
    )
    if not blackboard_summary:
        blackboard_summary = "없음"

    active_hint = ""
    if active is not None:
        active_hint = (
            f"- 현재 decision은 기본적으로 participant `{active_id}`의 독립 브라우저 세션에서 실행됩니다.\n"
            "- 같은 참여자를 계속 실행하려면 `turn_control.status=\"continue\"`를 사용하세요.\n"
            "- 다른 참여자에게 넘기려면 `next_participant`를 사용하세요.\n"
            "- 이벤트를 기다려야 하면 `turn_control.status=\"wait_for\"`와 wake condition을 명시하세요."
        )

    return "\n".join(
        [
            "## 다중 참여자 실행 상태",
            *participant_lines,
            active_hint,
            "## Blackboard 공유 관찰",
            blackboard_summary,
        ]
    ).strip()


def participant_test_data_for_prompt(agent: Any, goal: TestGoal) -> dict[str, Any]:
    """Return only the active participant's credentials in multi-user mode."""
    base_data = dict(goal.test_data) if isinstance(getattr(goal, "test_data", None), dict) else {}
    registry = getattr(agent, "_participant_registry", None)
    if not isinstance(registry, ParticipantRegistry) or not registry.is_multi():
        return base_data

    active_id = str(getattr(agent, "_active_participant_id", "") or registry.active_participant_id or "")
    if not active_id or active_id not in registry.participants:
        return {}

    participant_ids = set(registry.participants.keys())
    public_data = {
        key: value
        for key, value in base_data.items()
        if _is_public_test_data_key(str(key), participant_ids)
    }
    runtime = registry.get(active_id)
    participant_data = dict(runtime.spec.test_data or {})
    if active_id:
        participant_data.setdefault("participant_id", active_id)
    role = str(getattr(runtime.spec, "role", "") or "").strip()
    if role:
        participant_data.setdefault("participant_role", role)
    return {**public_data, **participant_data}


def parse_multi_user_interaction_request(
    value: Any = None,
    *,
    participant_plan: Optional[ParticipantPlan] = None,
) -> Optional[ParticipantPlan]:
    if participant_plan is not None:
        return participant_plan
    raw: Any = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except Exception:
            return None
    if not isinstance(raw, Mapping):
        return None

    skill = str(raw.get("skill") or raw.get("kind") or "").strip().lower()
    if skill in _SKILL_NAMES:
        raw = raw.get("participant_plan") if isinstance(raw.get("participant_plan"), Mapping) else raw
    elif isinstance(raw.get("participant_plan"), Mapping):
        raw = raw.get("participant_plan")
    elif "required" not in raw and "participants" not in raw:
        return None

    try:
        return ParticipantPlan.model_validate(raw)
    except Exception:
        return None


def activate_multi_user_interaction(
    agent: Any,
    goal: TestGoal,
    plan: ParticipantPlan,
) -> tuple[bool, str]:
    if not plan.required:
        return True, "multi_user_interaction skill이 필요 없다고 선언되어 단일 유저 경로를 유지합니다."
    existing = getattr(agent, "_participant_registry", None)
    if isinstance(existing, ParticipantRegistry) and existing.is_multi():
        return True, "multi_user_interaction은 이미 활성화되어 있습니다."

    specs = _resolve_participant_specs(plan)
    if not specs:
        return False, "participant_plan.required=true 이지만 participants가 비어 있습니다."

    missing_fields = _missing_credential_fields(goal, specs, plan.credential_requests)
    if missing_fields:
        ok, reason = request_human_answer(
            agent,
            goal,
            {
                "question": _credential_question(plan, missing_fields),
                "fields": missing_fields,
                "reason_code": "multi_user_credentials_required",
                "sensitive": True,
                "instructions": (
                    "각 값은 participant_id 접두사가 붙은 key=value로 입력하세요. "
                    "예: sender_username=... sender_password=... receiver_username=..."
                ),
            },
        )
        if not ok:
            return False, reason

    resolved_specs = [
        _merge_participant_credentials(goal, spec, plan.credential_requests, len(specs))
        for spec in specs
    ]
    _purge_scoped_credential_fields(goal, resolved_specs, plan.credential_requests)
    base_session_id = _base_session_id(agent)
    registry = ParticipantRegistry.bootstrap(
        specs=resolved_specs,
        context_mode=getattr(goal, "context_mode", None) or ContextMode.ISOLATED,
        turn_policy=getattr(goal, "turn_policy", None),
        goal_run_id=base_session_id,
        default_start_url=getattr(goal, "start_url", None),
        default_test_data={},
    )

    for pid, runtime in registry.participants.items():
        binding = ParticipantBrowserBinding(
            participant_id=pid,
            session_id=_participant_session_id(base_session_id, pid),
            profile_name=_participant_profile_name(base_session_id, pid),
            start_url=runtime.spec.start_url or getattr(goal, "start_url", None),
            context_args=dict(runtime.spec.context_args or {}),
            storage_state_path=runtime.spec.storage_state_path,
        )
        created = _create_participant_browser_context(agent, binding)
        runtime.browser_session = binding.model_copy(update={"created": created})

    goal.participants = [runtime.spec for runtime in registry.participants.values()]
    agent._participant_registry = registry
    agent._participant_plan = plan
    agent._base_session_id = base_session_id
    agent._active_participant_id = registry.active_participant_id
    if registry.active_participant_id:
        registry.scheduler.request_next(registry.active_participant_id)
    registry.post_blackboard(
        "harness",
        "participant_plan_activated",
        {
            "reason": plan.reason,
            "participants": list(registry.participants.keys()),
            "expected_events": list(plan.expected_events or []),
        },
    )
    return True, f"multi_user_interaction 활성화: {', '.join(registry.participants.keys())}"


def begin_participant_turn(agent: Any) -> Optional[str]:
    registry = getattr(agent, "_participant_registry", None)
    if not isinstance(registry, ParticipantRegistry) or not registry.is_multi():
        return None

    participant_id = registry.scheduler.next_participant()
    if not participant_id:
        return None

    runtime = registry.get(participant_id)
    binding = runtime.browser_session
    session_id = str(getattr(binding, "session_id", "") or "")
    if not session_id:
        return None

    registry.set_active(participant_id)
    agent._active_participant_id = participant_id
    agent.session_id = session_id
    return participant_id


def complete_participant_turn(
    agent: Any,
    *,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    step_count: int,
) -> None:
    registry = getattr(agent, "_participant_registry", None)
    if not isinstance(registry, ParticipantRegistry) or not registry.is_multi():
        return

    participant_id = (
        str(getattr(decision, "participant_id", "") or "").strip()
        or str(getattr(agent, "_active_participant_id", "") or "").strip()
        or registry.active_participant_id
    )
    if not participant_id or participant_id not in registry.participants:
        return

    event_key = str(getattr(decision, "blackboard_event", "") or "").strip()
    if event_key:
        payload = dict(getattr(decision, "blackboard_payload", {}) or {})
        payload.setdefault("action", str(getattr(decision.action, "value", decision.action)))
        payload.setdefault("success", bool(success))
        registry.post_blackboard(participant_id, event_key, payload, step=step_count)

    registry.scheduler.record_outcome(
        participant_id,
        observation_changed=bool(success and changed),
    )
    turn_control = getattr(decision, "turn_control", None)
    next_participant = str(getattr(decision, "next_participant", "") or "").strip()

    status = _turn_status(turn_control)
    if bool(getattr(decision, "is_goal_achieved", False)):
        registry.scheduler.mark_done(participant_id)
    elif status == TurnControlStatus.DONE:
        if next_participant:
            # Models often use "done" to mean "this actor's current turn is done".
            # Preserve explicit handoff without permanently retiring this participant.
            registry.scheduler.mark_idle(participant_id, wake_conditions=[])
        else:
            registry.scheduler.mark_done(participant_id)
    elif status == TurnControlStatus.CONTINUE:
        registry.scheduler.request_next(participant_id)
    elif status == TurnControlStatus.WAIT_FOR:
        registry.scheduler.mark_idle(
            participant_id,
            wake_conditions=_turn_wait_conditions(turn_control),
        )
    else:
        registry.scheduler.mark_idle(
            participant_id,
            wake_conditions=[],
        )

    if next_participant and next_participant in registry.participants:
        registry.scheduler.request_next(next_participant)


@contextmanager
def participant_decision_session(
    agent: Any,
    decision: ActionDecision,
    *,
    restore: bool = True,
) -> Iterator[None]:
    registry = getattr(agent, "_participant_registry", None)
    target_id = str(getattr(decision, "participant_id", "") or "").strip()
    if not target_id or not isinstance(registry, ParticipantRegistry) or target_id not in registry.participants:
        yield
        return

    previous_session_id = getattr(agent, "session_id", "")
    previous_active_id = str(getattr(agent, "_active_participant_id", "") or "")
    runtime = registry.get(target_id)
    session_id = str(getattr(runtime.browser_session, "session_id", "") or "")
    if session_id:
        agent.session_id = session_id
        agent._active_participant_id = target_id
        registry.set_active(target_id)
    try:
        yield
    finally:
        if not restore:
            return
        agent.session_id = previous_session_id
        agent._active_participant_id = previous_active_id
        if previous_active_id and previous_active_id in registry.participants:
            registry.set_active(previous_active_id)


def close_participant_browser_contexts(agent: Any) -> None:
    """Best-effort cleanup for temporary participant sessions and OpenClaw profiles."""
    registry = getattr(agent, "_participant_registry", None)
    if not isinstance(registry, ParticipantRegistry) or not registry.is_multi():
        return

    host_url = getattr(agent, "mcp_host_url", "")
    for runtime in registry.participants.values():
        binding = runtime.browser_session
        session_id = str(getattr(binding, "session_id", "") or "").strip()
        profile_name = str(getattr(binding, "profile_name", "") or "").strip()
        if session_id:
            try:
                close_mcp_session(host_url, session_id=session_id, timeout=(3, 10))
            except Exception:
                pass
        if profile_name.startswith("gaia-"):
            try:
                delete_browser_profile(host_url, profile=profile_name, timeout=(3, 15))
            except Exception:
                pass


def _resolve_participant_specs(plan: ParticipantPlan) -> list[ParticipantSpec]:
    specs_by_id: dict[str, ParticipantSpec] = {
        spec.id: spec for spec in list(plan.participants or []) if str(spec.id or "").strip()
    }
    for request in list(plan.credential_requests or []):
        pid = str(request.participant_id or "").strip()
        if pid and pid not in specs_by_id:
            specs_by_id[pid] = ParticipantSpec(id=pid, role=pid)
    return list(specs_by_id.values())


def _turn_status(turn_control: Optional[TurnControl]) -> Optional[TurnControlStatus]:
    if turn_control is None:
        return None
    try:
        return TurnControlStatus(turn_control.status)
    except Exception:
        return None


def _turn_wait_conditions(turn_control: Optional[TurnControl]) -> list[WakeCondition]:
    if turn_control is None:
        return []
    return list(turn_control.wait_for or [])


def _missing_credential_fields(
    goal: TestGoal,
    specs: list[ParticipantSpec],
    requests: list[ParticipantCredentialRequest],
) -> list[str]:
    missing: list[str] = []
    spec_ids = {spec.id for spec in specs}
    for request in requests:
        pid = str(request.participant_id or "").strip()
        if not pid or pid not in spec_ids or not request.required:
            continue
        spec = next((candidate for candidate in specs if candidate.id == pid), None)
        for field in request.fields:
            field_name = str(field or "").strip()
            if not field_name:
                continue
            if _credential_value(goal, spec, pid, field_name, len(specs)) is None:
                missing.append(_scoped_field(pid, field_name))
    return list(dict.fromkeys(missing))


def _merge_participant_credentials(
    goal: TestGoal,
    spec: ParticipantSpec,
    requests: list[ParticipantCredentialRequest],
    participant_count: int,
) -> ParticipantSpec:
    data = dict(spec.test_data or {})
    for request in requests:
        if request.participant_id != spec.id:
            continue
        for field in request.fields:
            field_name = str(field or "").strip()
            if not field_name:
                continue
            value = _credential_value(goal, spec, spec.id, field_name, participant_count)
            if value is not None:
                data[field_name] = value
    return spec.model_copy(update={"test_data": data})


def _purge_scoped_credential_fields(
    goal: TestGoal,
    specs: list[ParticipantSpec],
    requests: list[ParticipantCredentialRequest],
) -> None:
    if not isinstance(getattr(goal, "test_data", None), dict):
        return

    keys_to_remove: set[str] = set()
    spec_ids = {spec.id for spec in specs}
    for request in requests:
        pid = str(request.participant_id or "").strip()
        if not pid or pid not in spec_ids:
            continue
        for field in request.fields:
            field_name = str(field or "").strip()
            if not field_name:
                continue
            keys_to_remove.update(
                {
                    _scoped_field(pid, field_name),
                    f"{pid}.{field_name}",
                    f"{pid}:{field_name}",
                }
            )

    for key in keys_to_remove:
        goal.test_data.pop(key, None)


def _credential_value(
    goal: TestGoal,
    spec: Optional[ParticipantSpec],
    participant_id: str,
    field: str,
    participant_count: int,
) -> Any:
    if spec is not None and field in (spec.test_data or {}):
        value = spec.test_data.get(field)
        if _has_value(value):
            return value

    data = goal.test_data if isinstance(getattr(goal, "test_data", None), dict) else {}
    for key in (_scoped_field(participant_id, field), f"{participant_id}.{field}", f"{participant_id}:{field}"):
        value = data.get(key)
        if _has_value(value):
            return value

    nested = data.get("participants")
    if isinstance(nested, Mapping):
        raw_participant = nested.get(participant_id)
        if isinstance(raw_participant, Mapping):
            value = raw_participant.get(field)
            if _has_value(value):
                return value

    if participant_count == 1:
        value = data.get(field)
        if _has_value(value):
            return value
    return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _credential_question(plan: ParticipantPlan, missing_fields: list[str]) -> str:
    reason = str(plan.reason or "").strip()
    prefix = f"{reason}\n" if reason else ""
    return prefix + "멀티 유저 상호작용 테스트를 계속하려면 참여자별 계정 정보가 필요합니다: " + ", ".join(missing_fields)


def _scoped_field(participant_id: str, field: str) -> str:
    return f"{participant_id}_{field}"


def _is_public_test_data_key(key: str, participant_ids: set[str]) -> bool:
    if key == "participants":
        return False
    for participant_id in participant_ids:
        if key.startswith(f"{participant_id}_"):
            return False
        if key.startswith(f"{participant_id}."):
            return False
        if key.startswith(f"{participant_id}:"):
            return False
    return True


def _base_session_id(agent: Any) -> str:
    base = str(getattr(agent, "_base_session_id", "") or getattr(agent, "session_id", "") or "goal_driven").strip()
    agent._base_session_id = base
    return base


def _participant_session_id(base_session_id: str, participant_id: str) -> str:
    return f"{_safe_segment(base_session_id)}::participant::{_safe_segment(participant_id)}"


def _participant_profile_name(base_session_id: str, participant_id: str) -> str:
    digest = hashlib.sha1(str(base_session_id or "goal-driven").encode("utf-8")).hexdigest()[:8]
    participant = re.sub(r"[^a-z0-9-]+", "-", str(participant_id or "").strip().lower())
    participant = participant.strip("-") or "participant"
    return f"gaia-{digest}-{participant[:40]}".strip("-")


def _safe_segment(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_.:-]+", "-", str(value or "").strip())
    return text.strip("-") or f"p{int(time.time())}"


def _create_participant_browser_context(agent: Any, binding: ParticipantBrowserBinding) -> bool:
    start_url = str(binding.start_url or "").strip()
    profile_name = str(binding.profile_name or "").strip()
    if profile_name:
        profile_response = ensure_browser_profile(
            getattr(agent, "mcp_host_url", ""),
            profile=profile_name,
            timeout=(5, 45),
        )
        profile_payload = profile_response.payload if isinstance(profile_response.payload, dict) else {}
        if int(profile_response.status_code) >= 400 or not bool(profile_payload.get("success", profile_payload.get("ok", True))):
            return False
    if not start_url:
        return True
    response = execute_mcp_action(
        getattr(agent, "mcp_host_url", ""),
        action="browser_act",
        params={
            "session_id": binding.session_id,
            "profile": profile_name,
            "action": "goto",
            "url": start_url,
            "value": start_url,
        },
        timeout=(5, 45),
    )
    payload = response.payload if isinstance(response.payload, dict) else {}
    return int(response.status_code) < 400 and bool(payload.get("success", True))
