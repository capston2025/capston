from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .goal_policy_phase_runtime import goal_phase_intent
from .models import ActionDecision, ActionType, DOMElement, TestGoal


def _policy_int(agent: Any, key: str, default: int) -> int:
    cfg = getattr(agent, "_loop_policy", {})
    if isinstance(cfg, dict):
        try:
            return max(0, int(cfg.get(key, default)))
        except Exception:
            return max(0, int(default))
    return max(0, int(default))


def _emit_reason(agent: Any, code: str) -> None:
    if not code:
        return
    recorder = getattr(agent, "_record_reason_code", None)
    if callable(recorder):
        recorder(code)


def _retry_streak(agent: Any, key: str, *, reset: bool = False) -> int:
    bucket = getattr(agent, "_recovery_retry_streaks", {})
    if not isinstance(bucket, dict):
        bucket = {}
    if reset:
        bucket[key] = 0
        agent._recovery_retry_streaks = bucket
        return 0
    value = int(bucket.get(key, 0)) + 1
    bucket[key] = value
    agent._recovery_retry_streaks = bucket
    return value


def _is_discovery_action(agent: Any, decision: ActionDecision, dom_elements: Optional[List[DOMElement]]) -> bool:
    current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
    if current_phase == "locate_target" and decision.action in {ActionType.FILL, ActionType.PRESS, ActionType.SELECT}:
        return True
    if not isinstance(dom_elements, list) or not dom_elements:
        return False
    if decision.action not in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS, ActionType.SELECT}:
        return False
    element_id = getattr(decision, "element_id", None)
    if not isinstance(element_id, int) or element_id < 0 or element_id >= len(dom_elements):
        return False
    element = dom_elements[element_id]
    blob = " ".join(
        [
            str(getattr(element, "text", "") or ""),
            str(getattr(element, "aria_label", "") or ""),
            str(getattr(element, "placeholder", "") or ""),
            str(getattr(element, "title", "") or ""),
            str(getattr(element, "type", "") or ""),
            str(getattr(element, "role", "") or ""),
            str(getattr(element, "container_name", "") or ""),
            str(getattr(element, "context_text", "") or ""),
            str(getattr(decision, "value", "") or ""),
        ]
    ).lower()
    return any(token in blob for token in ("검색", "search", "query", "find", "filter", "필터"))


def handle_action_recovery(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    reason_code: str,
    login_gate_visible: bool,
    has_login_test_data: bool,
    post_dom: Optional[List[DOMElement]],
    force_context_shift: bool,
    ineffective_action_streak: int,
) -> Dict[str, Any]:
    if success and changed:
        _retry_streak(agent, "transient", reset=True)
        _retry_streak(agent, "timeout", reset=True)
        _retry_streak(agent, "discovery_settle", reset=True)
        return {
            "continue_loop": False,
            "force_context_shift": force_context_shift,
            "ineffective_action_streak": ineffective_action_streak,
        }

    agent._record_recovery_hints(goal, reason_code)
    auth_mode = ""
    if isinstance(goal.test_data, dict):
        auth_mode = str(goal.test_data.get("auth_mode") or "").strip().lower()
    is_auth_flow = login_gate_visible and (
        auth_mode in {"signup", "register", "login", "signin"} or has_login_test_data
    )
    current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
    auth_intent_active = (
        goal_phase_intent(current_phase) == "auth"
        or is_auth_flow
        or bool(getattr(agent, "_auth_interrupt_active", False))
        or bool(getattr(agent, "_auth_submit_attempted", False))
    )

    if (
        auth_intent_active
        and reason_code in {
            "no_state_change",
            "not_actionable",
            "action_timeout",
            "snapshot_not_found",
            "stale_snapshot",
            "ref_required",
            "not_found",
        }
    ):
        agent._action_feedback.append(
            "인증 모달 제출/입력 반영이 지연되거나 stale 상태입니다. 모달 내부 오류/필수 입력값을 확인하고 "
            "같은 모달 안에서 재시도하세요. 페이지/섹션 전환은 금지합니다."
        )
        if len(agent._action_feedback) > 10:
            agent._action_feedback = agent._action_feedback[-10:]
        _ = agent._analyze_dom(scope_container_ref_id="")
        time.sleep(0.25)
        return {
            "continue_loop": True,
            "force_context_shift": False,
            "ineffective_action_streak": 0,
        }

    if (
        reason_code in {"no_state_change", "not_actionable"}
        and _is_discovery_action(agent, decision, post_dom)
    ):
        settle_streak = _retry_streak(agent, "discovery_settle", reset=False)
        settle_limit = max(1, _policy_int(agent, "discovery_settle_limit", 1))
        if settle_streak <= settle_limit:
            _emit_reason(agent, "discovery_settle_retry")
            agent._log("🔎 탐색/검색 액션 직후에는 짧게 대기하고 최신 DOM을 다시 수집합니다.")
            time.sleep(0.35)
            _ = agent._analyze_dom()
            return {
                "continue_loop": True,
                "force_context_shift": False,
                "ineffective_action_streak": 0,
            }

    if reason_code == "modal_not_open":
        agent._log("🧭 close 대상 모달이 현재 열려있지 않아 재계획합니다.")
        _emit_reason(agent, "modal_not_open_replan")
        agent._action_feedback.append(
            "닫기 액션 시점에 모달이 열려있지 않았습니다. 최신 화면 기준으로 후보를 다시 수집하고 "
            "닫기 대신 현재 활성 CTA를 선택하세요."
        )
        if len(agent._action_feedback) > 10:
            agent._action_feedback = agent._action_feedback[-10:]
        _ = agent._analyze_dom()
        time.sleep(0.2)
        return {
            "continue_loop": True,
            "force_context_shift": False,
            "ineffective_action_streak": 0,
        }

    if (
        agent._no_progress_counter >= 2
        and reason_code
        in {
            "no_state_change",
            "not_actionable",
            "ambiguous_ref_target",
            "ambiguous_selector",
            "blocked_ref_no_progress",
            "blocked_logout_action",
        }
        and decision.action in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS}
    ):
        force_context_shift = True

    if reason_code in {
        "snapshot_not_found",
        "stale_snapshot",
        "ref_required",
        "ambiguous_ref_target",
        "ambiguous_selector",
        "not_found",
    }:
        _emit_reason(agent, "snapshot_refresh")
        _retry_streak(agent, "transient", reset=True)
        _retry_streak(agent, "timeout", reset=True)
        _retry_streak(agent, "discovery_settle", reset=True)
        agent._log("♻️ snapshot/ref 갱신이 필요해 DOM을 재수집합니다.")
        _ = agent._analyze_dom()
        time.sleep(0.25)
        return {
            "continue_loop": True,
            "force_context_shift": False,
            "ineffective_action_streak": 0,
        }

    if reason_code in {"request_exception", "http_5xx", "action_timeout"}:
        attempt_count = agent._last_exec_result.attempt_count if agent._last_exec_result else 0
        is_timeout = reason_code == "action_timeout"
        _retry_streak(agent, "discovery_settle", reset=True)
        bucket_key = "timeout" if is_timeout else "transient"
        retry_limit = max(
            1,
            _policy_int(
                agent,
                "action_timeout_retry_limit" if is_timeout else "transient_retry_limit",
                2,
            ),
        )
        streak = _retry_streak(agent, bucket_key, reset=False)
        backoff_base = 0.8 if is_timeout else 0.6
        backoff = min(3.5, backoff_base + (0.35 * max(0, attempt_count)) + (0.25 * max(0, streak - 1)))
        if streak > retry_limit:
            _emit_reason(
                agent,
                "action_timeout_retry_exhausted" if is_timeout else "transient_retry_exhausted",
            )
            agent._log(
                f"🌐 재시도 예산 소진({reason_code}, streak={streak}/{retry_limit}): "
                "강제 컨텍스트 전환으로 복구 전략을 변경합니다."
            )
            _ = agent._analyze_dom()
            time.sleep(0.2)
            return {
                "continue_loop": True,
                "force_context_shift": True,
                "ineffective_action_streak": 0,
            }
        _emit_reason(agent, "action_timeout_retry" if is_timeout else "transient_retry")
        agent._log(
            f"🌐 일시적 실행 오류({reason_code}) 감지: "
            f"{backoff:.2f}s 대기 후 재시도합니다. (streak={streak}/{retry_limit})"
        )
        _ = agent._analyze_dom()
        time.sleep(backoff)
        return {
            "continue_loop": True,
            "force_context_shift": False,
            "ineffective_action_streak": 0,
        }

    return {
        "continue_loop": False,
        "force_context_shift": force_context_shift,
        "ineffective_action_streak": ineffective_action_streak,
    }
