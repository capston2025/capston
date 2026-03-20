from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, Optional

from gaia.src.phase4.captcha_solver import CaptchaSolver


def _iter_element_blobs(dom_elements: Iterable[Any]) -> Iterable[str]:
    for el in dom_elements or []:
        parts = [
            str(getattr(el, "text", "") or ""),
            str(getattr(el, "aria_label", "") or ""),
            str(getattr(el, "placeholder", "") or ""),
            str(getattr(el, "title", "") or ""),
            str(getattr(el, "container_name", "") or ""),
            str(getattr(el, "context_text", "") or ""),
        ]
        blob = " ".join(part for part in parts if part).strip().lower()
        if blob:
            yield blob


def _has_captcha_surface_signal(dom_elements: Iterable[Any]) -> bool:
    tokens = (
        "captcha",
        "recaptcha",
        "hcaptcha",
        "turnstile",
        "cloudflare",
        "i'm not a robot",
        "로봇이 아닙니다",
        "보안 문자",
        "자동 입력 방지",
    )
    for blob in _iter_element_blobs(dom_elements):
        if any(token in blob for token in tokens):
            return True
    return False


def _ensure_captcha_solver(agent: Any) -> CaptchaSolver:
    solver = getattr(agent, "_captcha_solver", None)
    if solver is None:
        captcha_attempts = agent._loop_policy_value("captcha_solver_attempt_limit", 2)
        if captcha_attempts <= 0:
            captcha_attempts = 2
        solver = CaptchaSolver(
            vision_client=agent.llm,
            execute_fn=agent._execute_action,
            mcp_host_url=agent.mcp_host_url,
            session_id=agent.session_id,
            max_attempts=captcha_attempts,
            log_fn=agent._log,
        )
        agent._captcha_solver = solver
    return solver


def _ensure_captcha_observer_executor(agent: Any) -> ThreadPoolExecutor:
    executor = getattr(agent, "_captcha_observer_executor", None)
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gaia-captcha")
        agent._captcha_observer_executor = executor
    return executor


def _capture_detection_payload(solver: CaptchaSolver, screenshot: str, page_url: str, step_count: int) -> Dict[str, Any]:
    detection = solver.detect_captcha(screenshot, page_url)
    return {
        "detected": bool(detection.detected),
        "captcha_type": str(detection.captcha_type or "none"),
        "confidence": int(detection.confidence or 0),
        "reasoning": str(detection.reasoning or ""),
        "step_count": int(step_count),
        "page_url": str(page_url or ""),
        "checked_at": time.time(),
    }


def _maybe_reap_captcha_observer(agent: Any) -> None:
    future = getattr(agent, "_captcha_observer_future", None)
    if future is None or not future.done():
        return
    agent._captcha_observer_future = None
    try:
        payload = future.result()
    except Exception as exc:
        agent._captcha_observer_state = {
            "status": "error",
            "error": str(exc),
            "updated_at": time.time(),
        }
        agent._captcha_observer_last_result = dict(agent._captcha_observer_state)
        agent._captcha_observer_confirmed = False
        return
    if not isinstance(payload, dict):
        payload = {"detected": False, "confidence": 0, "captcha_type": "none", "reasoning": ""}
    detected = bool(payload.get("detected"))
    confidence = int(payload.get("confidence") or 0)
    status = "confirmed" if detected and confidence >= int(getattr(agent, "_captcha_observer_confirm_threshold", 85) or 85) else ("clear" if not detected else "suspected")
    agent._captcha_observer_state = {
        **payload,
        "status": status,
        "updated_at": time.time(),
        "consumed": False,
    }
    agent._captcha_observer_last_result = dict(agent._captcha_observer_state)
    agent._captcha_observer_confirmed = status == "confirmed"


def _should_start_captcha_observer(agent: Any, dom_elements: Iterable[Any], screenshot: str) -> bool:
    if not screenshot or getattr(agent, "_captcha_solver_skip", False):
        return False
    if getattr(agent, "_captcha_observer_future", None) is not None:
        return False
    screenshot_key = str((screenshot or "")[:128])
    if screenshot_key and screenshot_key == str(getattr(agent, "_captcha_observer_last_key", "") or ""):
        return False
    min_interval = float(getattr(agent, "_captcha_observer_min_interval_sec", 5.0) or 5.0)
    last_started = float(getattr(agent, "_captcha_observer_last_started_at", 0.0) or 0.0)
    if last_started > 0.0 and (time.time() - last_started) < min_interval:
        return False
    watch_sec = float(getattr(agent, "_captcha_observer_watch_window_sec", 30.0) or 30.0)
    recent_auth_submit_at = float(getattr(agent, "_last_auth_submit_at", 0.0) or 0.0)
    auth_window_active = recent_auth_submit_at > 0.0 and (time.time() - recent_auth_submit_at) < watch_sec
    return bool(auth_window_active or _has_captcha_surface_signal(dom_elements))


def run_captcha_observer(
    agent: Any,
    *,
    goal: Any,
    dom_elements: Iterable[Any],
    screenshot: Optional[str],
    step_count: int,
    steps: list[Any],
    start_time: float,
) -> Optional[Any]:
    _maybe_reap_captcha_observer(agent)

    state = getattr(agent, "_captcha_observer_state", {}) or {}
    status = str(state.get("status") or "").strip().lower()
    consumed = bool(state.get("consumed"))
    if status == "confirmed" and not consumed:
        agent._captcha_observer_state["consumed"] = True
        agent._record_reason_code("captcha_detected")
        solver = _ensure_captcha_solver(agent)
        captcha_result = solver.detect_and_handle(
            screenshot=screenshot or "",
            page_url=str(getattr(agent, "_current_url", goal.start_url or "") or ""),
            capture_fn=agent._capture_screenshot,
        )
        if captcha_result.solved:
            agent._log(f"🔓 CAPTCHA 해결 완료 ({captcha_result.attempts}회 시도)")
            agent._captcha_observer_state = {
                "status": "clear",
                "updated_at": time.time(),
                "consumed": True,
            }
            agent._captcha_observer_last_result = dict(agent._captcha_observer_state)
            agent._captcha_observer_confirmed = False
            agent._captcha_solver_skip_until_step = 0
            agent._action_history.append(
                f"Step {step_count}: captcha_solve - CAPTCHA 자동 해결 ({captcha_result.status})"
            )
            return {"continue_loop": True}

        if captcha_result.status == "gave_up":
            cooldown_steps = agent._loop_policy_value("captcha_solver_cooldown_steps", 4)
            if cooldown_steps <= 0:
                cooldown_steps = 4
            agent._captcha_solver_skip_until_step = int(step_count) + int(cooldown_steps)
            reason = (
                "CAPTCHA가 감지되었고 자동 해결에 실패했습니다. "
                f"(type={state.get('captcha_type')}, confidence={state.get('confidence')})"
            )
            agent._action_feedback.append(reason)
            if len(agent._action_feedback) > 10:
                agent._action_feedback = agent._action_feedback[-10:]
            return {
                "terminal_result": agent._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=reason,
                )
            }

        agent._captcha_observer_state = {
            "status": "clear",
            "updated_at": time.time(),
            "consumed": True,
        }
        agent._captcha_observer_last_result = dict(agent._captcha_observer_state)
        agent._captcha_observer_confirmed = False

    if not _should_start_captcha_observer(agent, dom_elements, screenshot or ""):
        return None

    solver = _ensure_captcha_solver(agent)
    executor = _ensure_captcha_observer_executor(agent)
    page_url = str(getattr(agent, "_current_url", goal.start_url or "") or "")
    agent._captcha_observer_last_started_at = time.time()
    agent._captcha_observer_last_key = str((screenshot or "")[:128])
    agent._captcha_observer_state = {
        "status": "pending",
        "step_count": int(step_count),
        "page_url": page_url,
        "updated_at": time.time(),
        "consumed": False,
    }
    agent._captcha_observer_future = executor.submit(
        _capture_detection_payload,
        solver,
        screenshot or "",
        page_url,
        int(step_count),
    )
    recent_auth_submit_at = float(getattr(agent, "_last_auth_submit_at", 0.0) or 0.0)
    if recent_auth_submit_at > 0.0:
        agent._log("🛰️ 인증 직후 CAPTCHA observer 백그라운드 검사 시작")
    return None
