from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from .models import GoalResult, StepResult, TestGoal
from .goal_policy_runtime import initialize_goal_policy_runtime
from .goal_replanning_runtime import initialize_goal_replanning_state
from .wrapper_trace_runtime import thin_wrapper_enabled


def initialize_goal_execution_state(agent: Any, goal: TestGoal) -> Dict[str, Any]:
    try:
        from ..mcp_local_dispatch_runtime import current_browser_backend

        agent._browser_backend_name = current_browser_backend(
            str(getattr(goal, "start_url", "") or "").strip() or None
        )
    except Exception:
        agent._browser_backend_name = str(
            os.getenv("GAIA_BROWSER_BACKEND", "openclaw") or "openclaw"
        ).strip().lower()
    agent._action_history = []
    agent._action_feedback = []
    agent._reason_code_counts = {}
    agent._recovery_retry_streaks = {}
    agent._overlay_intercept_pending = False
    agent._active_goal_text = f"{goal.name} {goal.description}".strip().lower()
    agent._steering_infeasible_block = False
    agent._ineffective_ref_counts = {}
    agent._last_success_click_intent = ""
    agent._success_click_intent_streak = 0
    agent._intent_stats = {}
    agent._context_shift_round = 0
    agent._last_context_shift_intent = ""
    agent._runtime_phase = "COLLECT"
    agent._goal_phase_intent = ""
    agent._goal_phase_resume_after_auth = ""
    agent._progress_counter = 0
    agent._no_progress_counter = 0
    agent._modal_opened_once = False
    agent._modal_closed_after_open = False
    agent._close_intent_success_once = False
    agent._close_click_success_once = False
    agent._handoff_state = {}
    agent._memory_selector_bias = {}
    agent._recent_click_element_ids = []
    agent._last_dom_top_ids = []
    agent._active_scoped_container_ref = ""
    agent._active_interaction_surface = {
        "kind": "",
        "ref_id": "",
        "source": "",
        "sticky_until": 0.0,
    }
    agent._pre_auth_surface_ref = ""
    agent._surface_reacquire_pending = False
    agent._active_interaction_surface_kind = ""
    agent._active_interaction_surface_source = ""
    agent._auth_interrupt_scope_ref = ""
    agent._auth_interrupt_scope_source = ""
    agent._auth_interrupt_active = False
    agent._auth_interrupt_started_at = 0.0
    agent._auth_resume_pending = False
    agent._auth_resolved_at = 0.0
    agent._auth_submit_attempted = False
    agent._auth_submit_attempts = 0
    agent._last_auth_submit_at = 0.0
    agent._last_auth_surface_signature = ""
    agent._auth_surface_progressed = False
    agent._auth_last_planned_fill = None
    agent._auth_identifier_done = False
    agent._auth_password_done = False
    auth_test_data = goal.test_data if isinstance(goal.test_data, dict) else {}
    agent._auth_identifier_values_norm = {
        agent._normalize_text(str(auth_test_data.get(key) or ""))
        for key in ("username", "email", "login_id", "user_id")
    }
    agent._auth_identifier_values_norm = {
        value for value in agent._auth_identifier_values_norm if value
    }
    agent._auth_password_value_norm = agent._normalize_text(
        str(auth_test_data.get("password") or "")
    )
    agent._auth_fill_memory = set()
    agent._captcha_observer_executor = None
    agent._captcha_observer_future = None
    agent._captcha_observer_last_key = ""
    agent._captcha_observer_last_started_at = 0.0
    agent._captcha_observer_last_result = {}
    agent._captcha_observer_confirmed = False
    agent._captcha_observer_state = {}
    agent._blocked_intent = {}
    agent._blocked_intent_resumed = False
    agent._blocked_intent_resume_attempts = 0
    agent._pending_resume_element_id = None
    agent._last_goal_blockable_intent = {}
    agent._verify_reclick_block_ref_id = ""
    agent._verify_reclick_block_container_ref = ""
    agent._verify_reclick_block_until = 0.0
    agent._evidence_only_proof_signature = None
    agent._evidence_only_proof_expires_at = 0.0
    agent._verify_settle_wait_armed = False
    agent._verify_evidence_wait_armed = False
    agent._evidence_only_wait_count = 0
    agent._evidence_only_wait_budget = agent._env_int(
        "GAIA_EVIDENCE_ONLY_WAIT_BUDGET",
        1,
        low=0,
        high=10,
    )
    agent._last_container_source_summary = {}
    agent._last_context_snapshot = {}
    agent._last_role_snapshot = {}
    agent._persistent_state_memory = []
    agent._recent_signal_history = []
    agent._goal_policy_target_seen_in_destination = False
    agent._goal_policy_destination_anchor_seen = False
    agent._goal_plan_requires_precheck = False
    agent._goal_plan_precheck_done = False
    agent._goal_plan_precheck_result = ""
    agent._goal_plan_remediation_completed = False
    agent._locate_target_search_consumed = False
    agent._goal_tokens = agent._derive_goal_tokens(goal)
    agent._goal_constraints = agent._derive_goal_constraints(goal)
    initialize_goal_policy_runtime(agent, goal)
    initialize_goal_replanning_state(agent, goal)
    if thin_wrapper_enabled(agent):
        # Thin wrapper mode keeps goal semantics/replanning, but strips the legacy
        # phase machine so OpenClaw-style judgment is driven by the live DOM.
        agent._goal_policy_phase = ""
        agent._goal_phase_intent = ""
        agent._goal_phase_resume_after_auth = ""
        agent._goal_policy_baseline_evidence = None
        agent._goal_plan_requires_precheck = False
        agent._goal_plan_precheck_done = False
        agent._goal_plan_precheck_result = ""
        agent._goal_plan_remediation_completed = False
    agent._activate_steering_policy(goal)
    agent._goal_metric_value = None
    agent._last_filter_semantic_report = None
    agent._filter_validation_contract = None

    collect_min = agent._goal_constraints.get("collect_min")
    apply_target = agent._goal_constraints.get("apply_target")
    metric_label = str(agent._goal_constraints.get("metric_label") or "")

    return {
        "filter_goal_active": agent._is_filter_style_goal(goal),
        "filter_semantic_attempts": 0,
        "filter_semantic_attempt_limit": agent._env_int(
            "GAIA_FILTER_SEMANTIC_SELECT_LIMIT",
            12,
            low=3,
            high=200,
        ),
        "filter_semantic_max_cases": agent._env_int(
            "GAIA_FILTER_SEMANTIC_MAX_CASES",
            20,
            low=1,
            high=50,
        ),
        "filter_semantic_current_only": bool(
            agent._env_int(
                "GAIA_FILTER_SEMANTIC_CURRENT_ONLY",
                0,
                low=0,
                high=1,
            )
        ),
        "collect_min": collect_min,
        "apply_target": apply_target,
        "metric_label": metric_label,
    }


def log_goal_start(agent: Any, goal: TestGoal, runtime_state: Dict[str, Any]) -> None:
    collect_min = runtime_state.get("collect_min")
    apply_target = runtime_state.get("apply_target")
    metric_label = str(runtime_state.get("metric_label") or "")
    if collect_min is not None:
        msg = f"🧩 목표 제약 감지: 최소 수집 {int(collect_min)}{metric_label}"
        if apply_target is not None:
            msg += f", 적용 목표 {int(apply_target)}{metric_label}"
        agent._log(msg)

    agent._log(f"🎯 목표 시작: {goal.name}")
    agent._log(f"   설명: {goal.description}")
    agent._log(f"   성공 조건: {goal.success_criteria}")


def prepare_memory_episode(agent: Any, goal: TestGoal) -> None:
    agent._memory_domain = agent._extract_domain(goal.start_url)
    agent._memory_episode_id = None
    try:
        agent._memory_store.garbage_collect(retention_days=30)
        agent._memory_episode_id = agent._memory_store.start_episode(
            provider=(os.getenv("GAIA_LLM_PROVIDER") or "openai"),
            model=(os.getenv("GAIA_LLM_MODEL") or os.getenv("VISION_MODEL") or "unknown"),
            runtime="terminal",
            domain=agent._memory_domain,
            goal_text=f"{goal.name} {goal.description}",
            url=goal.start_url or "",
        )
    except Exception:
        agent._memory_episode_id = None


def build_success_goal_result(
    agent: Any,
    *,
    goal: TestGoal,
    steps: List[StepResult],
    step_count: int,
    start_time: float,
    reason: str,
) -> GoalResult:
    result = GoalResult(
        goal_id=goal.id,
        goal_name=goal.name,
        success=True,
        steps_taken=steps,
        total_steps=step_count - 1 if step_count > 0 else 0,
        final_reason=reason,
        duration_seconds=time.time() - start_time,
    )
    agent._record_goal_summary(
        goal=goal,
        status="success",
        reason=result.final_reason,
        step_count=result.total_steps,
        duration_seconds=result.duration_seconds,
    )
    return result
