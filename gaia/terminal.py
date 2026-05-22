"""Terminal-facing entrypoint for GAIA without GUI."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from gaia.src.phase4.goal_driven import ExplorationConfig, ExploratoryAgent, GoalDrivenAgent, TestGoal
from gaia.src.phase4.goal_driven.adaptive_qa_runtime import (
    ADAPTIVE_QA_MODE,
    DEEP_ADAPTIVE_QA_MODE,
    adaptive_qa_enabled,
    adaptive_qa_is_deep,
    adaptive_qa_mode,
    build_edge_goal,
    classify_adaptive_edge_status,
    filter_new_edge_cases,
    generate_adaptive_qa_plan,
    merge_adaptive_qa_plans,
    summarize_adaptive_qa_report,
)
from gaia.src.phase4.goal_driven.multi_user_interaction_runtime import close_participant_browser_contexts
from gaia.src.phase4.goal_driven.goal_verification_helpers import derive_achieved_signals
from gaia.src.phase4.goal_driven.site_auth_store import load_site_credentials
from gaia.src.phase4.mcp_local_dispatch_runtime import close_mcp_session, execute_mcp_action
from gaia.src.phase4.validation_rail import run_validation_rail
from gaia.src.phase4.session import WORKSPACE_DEFAULT
from gaia.src.screenshot_quality import is_low_information_screenshot
from gaia.src.utils.config import CONFIG


def _extract_inline_test_data(query: str) -> tuple[str, Dict[str, str]]:
    tokens = [t for t in str(query or "").split() if t.strip()]
    data: Dict[str, str] = {}
    keep: list[str] = []
    aliases = {
        "id": "username",
        "user": "username",
        "username": "username",
        "email": "email",
        "pw": "password",
        "password": "password",
        "qa_mode": "qa_mode",
        "mode": "qa_mode",
        "adaptive_qa": ADAPTIVE_QA_MODE,
        "deep_adaptive_qa": DEEP_ADAPTIVE_QA_MODE,
        "deep_qa": DEEP_ADAPTIVE_QA_MODE,
    }
    for tok in tokens:
        if "=" not in tok:
            keep.append(tok)
            continue
        k, v = tok.split("=", 1)
        key = aliases.get(k.strip().lower())
        if not key:
            keep.append(tok)
            continue
        value = v.strip().strip('"').strip("'")
        if value:
            data[key] = value
    return " ".join(keep).strip(), data


def _build_test_goal(url: str, query: str) -> TestGoal:
    clean_query, inline_data = _extract_inline_test_data(query)
    query_text = clean_query or query
    ts = int(time.time())
    words = [w for w in query_text.replace("/", " ").split() if w.strip()]
    keywords = words[:5]
    try:
        max_steps = int(str(os.getenv("GAIA_GOAL_MAX_STEPS_OVERRIDE", "20")).strip())
    except Exception:
        max_steps = 20
    max_steps = max(1, max_steps)
    if str(os.getenv("GAIA_ADAPTIVE_QA", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        inline_data.setdefault(ADAPTIVE_QA_MODE, {"enabled": True})
    if str(os.getenv("GAIA_DEEP_ADAPTIVE_QA", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        inline_data.setdefault(DEEP_ADAPTIVE_QA_MODE, {"enabled": True})
    qa_mode = str(inline_data.get("qa_mode") or "").strip().lower()
    if qa_mode in {"deep", "deep_qa", "aggressive_qa", DEEP_ADAPTIVE_QA_MODE}:
        inline_data.setdefault(DEEP_ADAPTIVE_QA_MODE, {"enabled": True})
    if qa_mode in {"adaptive", ADAPTIVE_QA_MODE, "progressive_qa"}:
        inline_data.setdefault(ADAPTIVE_QA_MODE, {"enabled": True})
    if str(inline_data.get(ADAPTIVE_QA_MODE) or "").strip().lower() in {"1", "true", "yes", "on"}:
        inline_data[ADAPTIVE_QA_MODE] = {"enabled": True}
    if str(inline_data.get(DEEP_ADAPTIVE_QA_MODE) or "").strip().lower() in {"1", "true", "yes", "on"}:
        inline_data[DEEP_ADAPTIVE_QA_MODE] = {"enabled": True}
    return TestGoal(
        id=f"CHAT_{ts}",
        name=(query_text[:40] or "chat test").strip(),
        description=query_text,
        priority="MUST",
        keywords=keywords,
        success_criteria=[query_text],
        max_steps=max_steps,
        start_url=url,
        test_data=inline_data,
    )


def _augment_goal_with_env_credentials(goal: TestGoal, query_text: str, url: str) -> None:
    if not isinstance(goal.test_data, dict):
        goal.test_data = {}
    text = str(query_text or "")
    lowered = text.lower()
    needs_auth_context = any(
        token in lowered
        for token in (
            "로그인",
            "login",
            "auth",
            "인증",
            "회원가입",
            "signup",
            "register",
        )
    )
    if not needs_auth_context:
        return
    username = (os.getenv("GAIA_TEST_USERNAME") or os.getenv("GAIA_AUTH_USERNAME") or "").strip()
    password = (os.getenv("GAIA_TEST_PASSWORD") or os.getenv("GAIA_AUTH_PASSWORD") or "").strip()
    email = (os.getenv("GAIA_TEST_EMAIL") or os.getenv("GAIA_AUTH_EMAIL") or "").strip()
    if username and password:
        goal.test_data.setdefault("username", username)
        goal.test_data.setdefault("password", password)
        goal.test_data.setdefault("auth_mode", "provided_credentials")
        goal.test_data.setdefault("return_credentials", True)
        if email:
            goal.test_data.setdefault("email", email)
        return
    stored = load_site_credentials(url)
    if stored:
        for key, value in stored.items():
            goal.test_data.setdefault(key, value)


def _infer_goal_type(query_text: str) -> str:
    text = str(query_text or "").lower()
    explicit_filter_tokens = ("필터", "filter", "정렬", "sort")
    category_like_tokens = ("category", "분류")
    readonly_tokens = ("현재", "이미", "보이는지", "확인", "추가 조작 없이", "visible", "already")
    if any(token in text for token in explicit_filter_tokens):
        return "filter_validation"
    if any(token in text for token in category_like_tokens) and not any(
        token in text for token in readonly_tokens
    ):
        return "filter_validation"
    if any(token in text for token in ("로그인", "login", "auth", "인증")):
        return "auth_validation"
    if any(token in text for token in ("회원가입", "signup", "register")):
        return "signup_validation"
    return "goal_execution"


def _action_label(action_name: str, goal_type: str, reasoning: str) -> str:
    action = str(action_name or "").lower()
    reasoning_low = str(reasoning or "").lower()
    if goal_type == "filter_validation":
        if action == "select":
            return "필터 값 변경"
        if action == "fill":
            return "필터 검색어 입력"
        if action == "wait":
            return "필터 반영 대기"
        if action == "click":
            if any(t in reasoning_low for t in ("적용", "apply", "search", "검색", "필터")):
                return "필터 적용 요청"
            return "필터 관련 클릭"
    mapping = {
        "click": "요소 클릭 검증",
        "fill": "입력 동작 검증",
        "select": "선택 동작 검증",
        "press": "키 입력 동작 검증",
        "scroll": "스크롤 동작 검증",
        "wait": "대기/반응 검증",
        "navigate": "페이지 이동 검증",
        "hover": "호버 동작 검증",
    }
    return mapping.get(action, f"{action or 'unknown'} 동작 검증")


def _build_validation_report(
    query_text: str,
    result: Any,
) -> Dict[str, Any]:
    goal_type = _infer_goal_type(query_text)
    filter_mode = goal_type == "filter_validation"
    filter_tokens = ("필터", "filter", "category", "분류", "정렬", "sort")
    steps = list(getattr(result, "steps_taken", []) or [])
    checks: list[Dict[str, Any]] = []

    for step in steps:
        action_obj = getattr(step, "action", None)
        action_raw = getattr(action_obj, "action", "")
        action_name = (
            str(getattr(action_raw, "value", "") or action_raw or "").strip().lower()
        )
        reasoning = str(getattr(action_obj, "reasoning", "") or "").strip()
        reasoning_low = reasoning.lower()
        is_relevant = True
        if filter_mode:
            is_relevant = action_name in {"select", "fill", "click", "wait"} and (
                any(token in reasoning_low for token in filter_tokens) or action_name in {"select", "fill"}
            )
        if not is_relevant:
            continue

        success = bool(getattr(step, "success", False))
        status = "pass" if success else "fail"
        element_id = getattr(action_obj, "element_id", None)
        input_value = getattr(action_obj, "value", None)
        error_message = str(getattr(step, "error_message", "") or "").strip()

        checks.append(
            {
                "check_id": f"step_{int(getattr(step, 'step_number', len(checks) + 1) or (len(checks) + 1))}",
                "name": _action_label(action_name, goal_type, reasoning),
                "status": status,
                "step": int(getattr(step, "step_number", len(checks) + 1) or (len(checks) + 1)),
                "action": action_name or "unknown",
                "element_id": element_id,
                "input_value": input_value,
                "reasoning": reasoning,
                "error": error_message,
            }
        )

    if not checks:
        for step in steps[:10]:
            action_obj = getattr(step, "action", None)
            action_raw = getattr(action_obj, "action", "")
            action_name = (
                str(getattr(action_raw, "value", "") or action_raw or "").strip().lower()
            )
            reasoning = str(getattr(action_obj, "reasoning", "") or "").strip()
            success = bool(getattr(step, "success", False))
            checks.append(
                {
                    "check_id": f"step_{int(getattr(step, 'step_number', len(checks) + 1) or (len(checks) + 1))}",
                    "name": _action_label(action_name, "goal_execution", reasoning),
                    "status": "passed" if success else "failed",
                    "step": int(getattr(step, "step_number", len(checks) + 1) or (len(checks) + 1)),
                    "action": action_name or "unknown",
                    "element_id": getattr(action_obj, "element_id", None),
                    "input_value": getattr(action_obj, "value", None),
                    "reasoning": reasoning,
                    "error": str(getattr(step, "error_message", "") or "").strip(),
                }
            )

    total = len(checks)
    passed = sum(1 for c in checks if str(c.get("status")).lower() in {"pass", "passed"})
    failed = sum(1 for c in checks if str(c.get("status")).lower() in {"fail", "failed"})
    skipped = max(0, total - passed - failed)
    success_rate = round((passed / total) * 100, 1) if total > 0 else 0.0

    summary = {
        "goal_type": goal_type,
        "total_checks": total,
        "passed_checks": passed,
        "failed_checks": failed,
        "skipped_checks": skipped,
        "success_rate": success_rate,
    }
    return {
        "summary": summary,
        "checks": checks,
    }


def _is_strict_validation_failed(report: Dict[str, Any]) -> bool:
    if not isinstance(report, dict):
        return False
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return False
    if bool(summary.get("strict_failed")):
        return True
    try:
        failed_mandatory = int(summary.get("failed_mandatory_checks") or 0)
        skipped_mandatory = int(summary.get("skipped_mandatory_checks") or 0)
        return (failed_mandatory + skipped_mandatory) > 0
    except Exception:
        return False


def _is_goal_satisfaction_failed(report: Dict[str, Any]) -> bool:
    return False


def _should_preserve_runtime_success_from_validation(agent: Any, result: Any) -> bool:
    if not bool(getattr(result, "success", False)):
        return False
    source = str(getattr(agent, "_last_goal_completion_source", "") or "").strip().lower()
    return source == "judge"


def _apply_terminal_validation_outcome(
    *,
    result_success: bool,
    result_reason: str,
    validation_report: Dict[str, Any],
    preserve_runtime_success: bool = False,
) -> tuple[bool, str]:
    effective_reason = str(result_reason or "")
    if bool(result_success) and preserve_runtime_success:
        return True, effective_reason

    validation_failed = _is_strict_validation_failed(validation_report)
    goal_unsatisfied = _is_goal_satisfaction_failed(validation_report)
    effective_success = bool(result_success) and not validation_failed and not goal_unsatisfied
    if validation_failed:
        effective_reason = (
            "검증 리포트에서 필수 항목 실패가 발생했습니다. "
            + (effective_reason or "검증 리포트를 확인하세요.")
        ).strip()
    elif goal_unsatisfied:
        effective_reason = (
            "검증 리포트에서 목표 커버리지가 충족되지 않았습니다. "
            + (effective_reason or "검증 리포트를 확인하세요.")
        ).strip()
    return effective_success, effective_reason


def _detect_blocked_user_action(
    reason: str,
    reason_summary: Dict[str, int],
) -> bool:
    reason_text = str(reason or "").strip().lower()
    blocked_codes = {
        "auth_required",
        "login_required",
        "captcha_detected",
        "2fa_required",
        "permission_prompt_detected",
        "blocked_timeout",
        "blocked_user_action",
        "steering_infeasible",
        "user_intervention_missing",
        "clarification_required",
        "clarification_timeout",
        "intervention_timeout",
    }
    for code in blocked_codes:
        if int(reason_summary.get(code) or 0) > 0:
            return True
    blocked_terms = (
        "captcha",
        "2fa",
        "권한 허용",
        "사용자 요청으로 실행을 중단",
        "추가 입력",
        "사용자 개입",
        "resume",
        "목표 명확화",
        "명확화가 필요",
        "사용자 입력이 제공되지 않아 중단",
    )
    return any(term in reason_text for term in blocked_terms)


def _derive_final_status(
    *,
    result_success: bool,
    reason: str,
    validation_report: Dict[str, Any],
    reason_summary: Dict[str, int],
) -> str:
    if result_success:
        return "SUCCESS"
    if _detect_blocked_user_action(reason, reason_summary):
        return "BLOCKED_USER_ACTION"
    if _is_strict_validation_failed(validation_report):
        return "FAIL"
    if _is_goal_satisfaction_failed(validation_report):
        return "FAIL"
    return "FAIL"


def _build_step_timeline(result: Any, *, limit: int = 12) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    steps = list(getattr(result, "steps_taken", []) or [])
    for step in steps[: max(1, int(limit))]:
        try:
            step_no = int(getattr(step, "step_number", len(rows) + 1) or (len(rows) + 1))
        except Exception:
            step_no = len(rows) + 1
        action_obj = getattr(step, "action", None)
        action_raw = getattr(action_obj, "action", "")
        action_name = str(getattr(action_raw, "value", "") or action_raw or "").strip().lower()
        reasoning = str(getattr(action_obj, "reasoning", "") or "").strip()
        duration_ms = getattr(step, "duration_ms", None)
        duration_seconds: float = 0.0
        try:
            if duration_ms is not None:
                duration_seconds = round(float(duration_ms) / 1000.0, 2)
        except Exception:
            duration_seconds = 0.0
        rows.append(
            {
                "step": step_no,
                "action": action_name or "unknown",
                "reasoning": reasoning,
                "duration_seconds": duration_seconds,
                "success": bool(getattr(step, "success", False)),
                "error": str(getattr(step, "error_message", "") or "").strip(),
            }
        )
    return rows


def _limit_attachments_for_status(
    attachments: Any,
    *,
    final_status: str,
) -> List[Dict[str, Any]]:
    if not isinstance(attachments, list):
        return []
    max_images_per_run = 3
    status_key = str(final_status or "").strip().upper()
    per_status_limit = {
        "SUCCESS": 1,
        "FAIL": 1,
        "BLOCKED_USER_ACTION": 1,
    }
    keep_limit = int(per_status_limit.get(status_key, 1))
    keep_limit = max(1, min(max_images_per_run, keep_limit))
    images: List[Dict[str, Any]] = []
    for row in attachments:
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "").strip().lower() != "image_base64":
            continue
        data = row.get("data")
        if not isinstance(data, str) or not data.strip():
            continue
        images.append(row)
        if len(images) >= keep_limit:
            break
    return images


def _capture_final_evidence_attachment(session_id: str) -> Optional[Dict[str, Any]]:
    """Capture proof before the runner closes the browser session."""
    last_error = ""
    for attempt in range(2):
        if attempt:
            time.sleep(0.45)
        try:
            response = execute_mcp_action(
                CONFIG.mcp.host_url,
                action="browser_screenshot",
                params={
                    "session_id": session_id,
                    "full_page": False,
                    "type": "png",
                },
                timeout=90,
            )
            data = response.payload if not hasattr(response, "json") else response.json()
            if int(getattr(response, "status_code", 500) or 500) >= 400:
                last_error = str(data.get("detail") or data.get("error") or getattr(response, "text", "") or "")
                continue
            screenshot = str(data.get("screenshot") or "").strip()
            if not screenshot:
                last_error = "empty_screenshot"
                continue
            current_url = str(data.get("current_url") or "").strip()
            if current_url.lower() == "about:blank":
                last_error = "about_blank_screenshot"
                continue
            if is_low_information_screenshot(screenshot):
                last_error = "low_information_screenshot"
                continue
            attachment: Dict[str, Any] = {
                "kind": "image_base64",
                "mime": str(data.get("mime_type") or "image/png"),
                "data": screenshot,
                "label": "최종 증거 화면",
            }
            saved_path = str(data.get("saved_path") or "").strip()
            if saved_path:
                attachment["path"] = saved_path
            if current_url:
                attachment["current_url"] = current_url
            return attachment
        except Exception as exc:
            last_error = str(exc)
    if last_error:
        return {
            "kind": "evidence_capture_error",
            "reason": last_error,
        }
    return None


def _captured_snapshot_attachments(captured_shots: Sequence[str]) -> List[Dict[str, Any]]:
    sample = [
        shot
        for shot in list(captured_shots or [])[-3:]
        if isinstance(shot, str) and shot.strip() and not is_low_information_screenshot(shot)
    ]
    return [
        {
            "kind": "image_base64",
            "mime": "image/png",
            "data": shot,
            "label": f"실행 스냅샷 {idx + 1}/{len(sample)}",
        }
        for idx, shot in enumerate(sample)
    ]


def _merge_reason_code_summary(
    base: Dict[str, Any],
    extra: Dict[str, Any],
) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for source in (base, extra):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            name = str(key or "").strip()
            if not name:
                continue
            try:
                count = int(value)
            except Exception:
                continue
            merged[name] = int(merged.get(name, 0)) + count
    return merged


def _print_llm_failure_help(reason: str) -> None:
    text = (reason or "").lower()
    if "insufficient_quota" not in text:
        return
    print("\n실행 안내")
    print("- 원인: OpenAI API quota/billing 부족 (429 insufficient_quota)")
    print("- 현재 경로는 OpenAI Platform API 호출이라 API 크레딧이 필요합니다.")
    print("- 해결 방법:")
    print("  1) OpenAI API 결제/크레딧 설정 후 다시 실행")
    print("  2) provider를 gemini로 전환해서 실행")
    print("  3) OpenAI는 manual(API key)로 재인증")


def _run_single_chat_goal(
    url: str,
    query: str,
    session_id: str = WORKSPACE_DEFAULT,
    steering_policy: Optional[Dict[str, Any]] = None,
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    live_intervention_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    prepared_goal: Optional[TestGoal] = None,
) -> Tuple[int, Dict[str, Any]]:
    def _emit_progress(kind: str, **payload: Any) -> None:
        if progress_callback is None:
            return
        try:
            event = {"kind": kind, "ts": time.time()}
            event.update(payload)
            progress_callback(event)
        except Exception:
            pass

    def _default_intervention_callback(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = str((payload or {}).get("kind") or "").strip().lower()
        if kind in {"clarification", "no_progress"}:
            question = str((payload or {}).get("question") or "").strip()
            return {
                "action": "continue",
                "proceed": True,
                "instruction": question or "현재 목표 범위에서 계속 진행하세요.",
            }
        if kind == "auth":
            username = (
                os.getenv("GAIA_TEST_USERNAME")
                or os.getenv("GAIA_AUTH_USERNAME")
                or ""
            ).strip()
            password = (
                os.getenv("GAIA_TEST_PASSWORD")
                or os.getenv("GAIA_AUTH_PASSWORD")
                or ""
            ).strip()
            email = (
                os.getenv("GAIA_TEST_EMAIL")
                or os.getenv("GAIA_AUTH_EMAIL")
                or ""
            ).strip()
            if username and password:
                response: Dict[str, Any] = {
                    "action": "continue",
                    "proceed": True,
                    "username": username,
                    "password": password,
                }
                if email:
                    response["email"] = email
                return response
            stored = load_site_credentials(url)
            if stored:
                response = {
                    "action": "continue",
                    "proceed": True,
                    "username": str(stored.get("username") or ""),
                    "password": str(stored.get("password") or ""),
                }
                stored_email = str(stored.get("email") or "").strip()
                if stored_email:
                    response["email"] = stored_email
                return response
            return {"action": "cancel", "proceed": False}
        return {"action": "cancel", "proceed": False}

    if intervention_callback is None:
        intervention_callback = _default_intervention_callback

    goal = prepared_goal or _build_test_goal(url=url, query=query)
    _augment_goal_with_env_credentials(goal, query, url)
    if isinstance(steering_policy, dict) and steering_policy:
        if not isinstance(goal.test_data, dict):
            goal.test_data = {}
        goal.test_data["steering_policy"] = dict(steering_policy)
    captured_shots: list[str] = []
    captured_hashes: set[str] = set()

    def _on_screenshot(base64_image: str) -> None:
        if not isinstance(base64_image, str):
            return
        payload = base64_image.strip()
        if not payload:
            return
        marker = payload[:96]
        if marker in captured_hashes:
            return
        captured_hashes.add(marker)
        captured_shots.append(payload)
        if len(captured_shots) > 8:
            removed = captured_shots.pop(0)
            try:
                captured_hashes.discard(removed[:96])
            except Exception:
                pass

        # GUI live preview hook — GAIA_LIVE_PREVIEW_PATH 환경변수가 설정되어 있으면
        # 캡처된 screenshot을 즉시 파일로 dump하여 GUI 폴링이 실시간 표시 가능하도록 함.
        # _on_screenshot은 모든 screenshot capture 경로를 통과하므로 가장 안정적인 hook 위치.
        preview_path_env = os.getenv("GAIA_LIVE_PREVIEW_PATH")
        if preview_path_env:
            try:
                import base64 as _b64
                from pathlib import Path as _Path
                _target = _Path(preview_path_env)
                _target.parent.mkdir(parents=True, exist_ok=True)
                _tmp = _target.with_suffix(_target.suffix + ".tmp")
                _tmp.write_bytes(_b64.b64decode(payload))
                _tmp.replace(_target)
            except Exception:
                pass

    normalized_session_id = session_id or WORKSPACE_DEFAULT
    agent = GoalDrivenAgent(
        mcp_host_url=CONFIG.mcp.host_url,
        session_id=normalized_session_id,
        screenshot_callback=_on_screenshot,
        intervention_callback=intervention_callback,
    )
    if live_intervention_provider is not None:
        try:
            agent._live_user_instruction_provider = live_intervention_provider
        except Exception:
            pass

    # ── GUI live preview ─────────────────────────────────────────────────
    # OpenClaw MCP runtime은 동시 capture_screenshot 요청을 처리할 free port가 없어서
    # 외부 thread에서 별도로 캡처할 수 없다 ("no free port set available" 에러).
    # 따라서 agent가 자체적으로 호출하는 `_capture_screenshot()` → `_on_screenshot`
    # callback에 piggyback하는 방식만 사용. 이미 _on_screenshot에 GAIA_LIVE_PREVIEW_PATH로
    # 파일을 dump하는 로직이 들어가 있음.
    # → 결과:
    #   - 인터랙티브 시나리오 (click/fill 등 동작 있는 케이스): 각 step마다 캡처 → 실시간 OK
    #   - readonly visibility 시나리오 (위키 "보이는지 확인" 류): agent가 screenshot 자체를 안 찍음
    #     → live preview는 비어있는 placeholder 유지 (대안 없음)
    _live_preview_path_env = os.getenv("GAIA_LIVE_PREVIEW_PATH")
    _live_preview_stop = None
    _live_preview_thread = None
    if _live_preview_path_env:
        print(
            f"📷 live preview enabled — path={_live_preview_path_env} "
            f"(captures piggyback on agent screenshots)",
            flush=True,
        )

    try:
        print(f"목표 실행: {goal.description}")
        _emit_progress("goal_started", goal=goal.description, url=url)
        result = agent.execute_goal(goal)
        validation_report = _build_validation_report(
            goal.description,
            result,
        )
        preserve_runtime_success = _should_preserve_runtime_success_from_validation(agent, result)
        effective_success, effective_reason = _apply_terminal_validation_outcome(
            result_success=bool(result.success),
            result_reason=str(result.final_reason or ""),
            validation_report=validation_report,
            preserve_runtime_success=preserve_runtime_success,
        )
        rail_result = run_validation_rail(
            target_url=url,
            run_id=normalized_session_id,
        )
        rail_summary = rail_result.get("summary") if isinstance(rail_result, dict) else {}
        rail_cases = rail_result.get("cases") if isinstance(rail_result, dict) else []
        rail_artifacts = rail_result.get("artifacts") if isinstance(rail_result, dict) else {}
        if not isinstance(rail_summary, dict):
            rail_summary = {}
        if not isinstance(rail_cases, list):
            rail_cases = []
        if not isinstance(rail_artifacts, dict):
            rail_artifacts = {}
        rail_mode = str(rail_summary.get("mode") or "soft").strip().lower()
        rail_status = str(rail_summary.get("status") or "").strip().lower()
        if rail_mode == "hard" and rail_status in {"failed", "timeout", "error"}:
            effective_success = False
            effective_reason = (
                "검증 레일 실패가 감지되었습니다. "
                + (effective_reason or str(rail_summary.get("reason") or "validation rail failed"))
            ).strip()

        report_reason_summary = (
            validation_report.get("reason_code_summary")
            if isinstance(validation_report.get("reason_code_summary"), dict)
            else {}
        )
        reason_summary = _merge_reason_code_summary(
            dict(getattr(agent, "_reason_code_counts", {}) or {}),
            report_reason_summary,
        )
        rail_reason_code = str(rail_summary.get("reason_code") or "").strip()
        if rail_reason_code:
            try:
                reason_summary[rail_reason_code] = int(reason_summary.get(rail_reason_code) or 0) + 1
            except Exception:
                reason_summary[rail_reason_code] = 1
        final_status = _derive_final_status(
            result_success=bool(effective_success),
            reason=effective_reason,
            validation_report=validation_report,
            reason_summary=reason_summary,
        )
        effective_success = final_status == "SUCCESS"

        print("\n실행 결과")
        print(f"goal: {result.goal_name}")
        print(f"status: {'success' if effective_success else 'failed'}")
        print(f"final_status: {final_status}")
        print(f"steps: {result.total_steps}")
        print(f"reason: {effective_reason}")
        print(f"duration: {result.duration_seconds:.2f}s")
        if not effective_success:
            _print_llm_failure_help(effective_reason)
        _emit_progress(
            "goal_finished",
            goal=result.goal_name,
            status=final_status,
            success=bool(effective_success),
            reason=effective_reason,
            steps=result.total_steps,
            duration_seconds=round(float(result.duration_seconds), 2),
        )

        adaptive_qa_report: Dict[str, Any] | None = None
        if adaptive_qa_enabled(goal):
            qa_mode_label = adaptive_qa_mode(goal) or ADAPTIVE_QA_MODE
            print(f"\n🧪 {qa_mode_label} 확장 모드")
            _emit_progress("adaptive_started", mode=qa_mode_label)
            adaptive_primary_result = result.model_copy(
                update={"success": bool(effective_success), "final_reason": effective_reason}
            )
            is_deep_qa = adaptive_qa_is_deep(goal)
            plans: list[Dict[str, Any]] = []
            generated_edge_cases: list[Dict[str, Any]] = []
            seen_edge_fingerprints: set[str] = set()
            edge_results: list[Dict[str, Any]] = []
            round_index = 1
            while True:
                current_dom = agent._analyze_dom() or []
                plan = generate_adaptive_qa_plan(
                    agent,
                    goal=goal,
                    primary_result=adaptive_primary_result,
                    dom_elements=current_dom,
                    previous_edge_cases=generated_edge_cases,
                    round_index=round_index,
                )
                plans.append(plan)
                raw_edge_cases = list(plan.get("edge_cases") or []) if isinstance(plan, dict) else []
                edge_cases = filter_new_edge_cases(raw_edge_cases, seen_edge_fingerprints)
                if not edge_cases or not adaptive_primary_result.success:
                    print("   새로 실행할 안전 엣지 케이스가 없습니다.")
                    _emit_progress(
                        "adaptive_done",
                        reason="새로 실행할 안전 엣지 케이스가 없습니다.",
                        round=round_index,
                        executed=len(edge_results),
                    )
                    break
                print(f"   round {round_index}: 안전 엣지 케이스 {len(edge_cases)}개 실행")
                _emit_progress(
                    "adaptive_round_started",
                    mode=qa_mode_label,
                    round=round_index,
                    count=len(edge_cases),
                )
                for edge_case in edge_cases:
                    generated_edge_cases.append(edge_case)
                    edge_goal = build_edge_goal(goal, edge_case, index=len(edge_results) + 1)
                    print(f"   [{len(edge_results) + 1}] {edge_goal.name}")
                    edge_index = len(edge_results) + 1
                    _emit_progress(
                        "edge_started",
                        index=edge_index,
                        name=edge_goal.name,
                        description=edge_goal.description,
                    )
                    edge_result = agent.execute_goal(edge_goal)
                    edge_status = classify_adaptive_edge_status(edge_case, edge_result)
                    edge_results.append(
                        {
                            "id": edge_goal.id,
                            "name": edge_goal.name,
                            "status": edge_status,
                            "reason": edge_result.final_reason,
                            "steps": edge_result.total_steps,
                        }
                    )
                    print(f"      {edge_status}: {edge_result.final_reason}")
                    _emit_progress(
                        "edge_finished",
                        index=edge_index,
                        name=edge_goal.name,
                        status=edge_status,
                        reason=edge_result.final_reason,
                        steps=edge_result.total_steps,
                    )
                if not is_deep_qa:
                    break
                round_index += 1
            merged_plan = merge_adaptive_qa_plans(plans)
            merged_plan["edge_cases"] = generated_edge_cases
            adaptive_qa_report = summarize_adaptive_qa_report(
                primary_goal=goal,
                primary_result=adaptive_primary_result,
                plan=merged_plan,
                edge_results=edge_results,
            )
            _emit_progress(
                "adaptive_finished",
                mode=qa_mode_label,
                summary=adaptive_qa_report.get("summary", {}) if isinstance(adaptive_qa_report, dict) else {},
            )

        expected_signals = [
            str(item or "").strip().lower()
            for item in list(getattr(goal, "expected_signals", []) or [])
            if str(item or "").strip()
        ]
        last_state_change = (
            dict(getattr(getattr(agent, "_last_exec_result", None), "state_change", {}) or {})
            if getattr(agent, "_last_exec_result", None) is not None
            else {}
        )
        final_dom = agent._analyze_dom() or []
        achieved_signals = derive_achieved_signals(
            agent,
            goal=goal,
            state_change=last_state_change,
            dom_elements=final_dom,
        )

        validation_attachments = (
            validation_report.get("attachments")
            if isinstance(validation_report.get("attachments"), list)
            else []
        )
        evidence_attachment = _capture_final_evidence_attachment(normalized_session_id)
        summary_attachments: List[Dict[str, Any]] = []
        if isinstance(evidence_attachment, dict) and evidence_attachment.get("kind") == "image_base64":
            summary_attachments.append(evidence_attachment)
        else:
            summary_attachments.extend(_captured_snapshot_attachments(captured_shots))
        summary_attachments.extend(
            item for item in validation_attachments if isinstance(item, dict)
        )

        summary = {
            "goal": result.goal_name,
            "status": "success" if effective_success else "failed",
            "final_status": final_status,
            "steps": result.total_steps,
            "reason": effective_reason,
            "duration_seconds": round(float(result.duration_seconds), 2),
            "goal_completion_source": str(getattr(agent, "_last_goal_completion_source", "") or ""),
            "step_timeline": _build_step_timeline(result),
            "expected_signals": expected_signals,
            "achieved_signals": achieved_signals,
            "reason_code_summary": reason_summary,
            "container_source_summary": dict(getattr(agent, "_last_container_source_summary", {}) or {}),
            "active_scoped_container_ref": str(getattr(agent, "_active_scoped_container_ref", "") or ""),
            "validation_summary": validation_report.get("summary", {}),
            "validation_checks": validation_report.get("checks", []),
            "verification_report": validation_report,
            "validation_rail_summary": rail_summary,
            "validation_rail_cases": rail_cases,
            "validation_rail_artifacts": rail_artifacts,
            "attachments": summary_attachments,
        }
        if adaptive_qa_report is not None:
            summary["adaptive_qa_report"] = adaptive_qa_report
        if isinstance(evidence_attachment, dict) and evidence_attachment.get("kind") == "evidence_capture_error":
            summary["evidence_capture_error"] = str(evidence_attachment.get("reason") or "")
        summary["attachments"] = _limit_attachments_for_status(
            summary.get("attachments"),
            final_status=final_status,
        )
        if isinstance(goal.test_data, dict):
            auth_payload = {}
            for key in (
                "auth_mode",
                "username",
                "email",
                "password",
                "department",
                "grade_year",
                "return_credentials",
            ):
                value = goal.test_data.get(key)
                if value not in (None, "", False):
                    auth_payload[key] = value
            if auth_payload:
                summary["auth"] = auth_payload
        return (0 if effective_success else 1), summary
    finally:
        try:
            close_participant_browser_contexts(agent)
        except Exception:
            pass
        try:
            close_mcp_session(
                CONFIG.mcp.host_url,
                session_id=normalized_session_id,
                timeout=(3, 10),
            )
        except Exception:
            pass


def run_chat_terminal_once(
    *,
    url: str,
    query: str,
    session_id: str = WORKSPACE_DEFAULT,
    steering_policy: Optional[Dict[str, Any]] = None,
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    live_intervention_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    prepared_goal: Optional[TestGoal] = None,
) -> Tuple[int, Dict[str, Any]]:
    return _run_single_chat_goal(
        url=url,
        query=query,
        session_id=session_id,
        steering_policy=steering_policy,
        intervention_callback=intervention_callback,
        progress_callback=progress_callback,
        live_intervention_provider=live_intervention_provider,
        prepared_goal=prepared_goal,
    )


def run_chat_terminal(
    *,
    url: str,
    initial_query: str | None = None,
    repl: bool = True,
    session_id: str = WORKSPACE_DEFAULT,
) -> int:
    if not url:
        print("URL is required for terminal chat mode.", file=sys.stderr)
        return 2

    if not repl:
        if not initial_query:
            print("A query is required when repl=False.", file=sys.stderr)
            return 2
        try:
            code, _ = _run_single_chat_goal(url, initial_query, session_id=session_id)
            return code
        except Exception as exc:
            print(f"Terminal chat failed: {exc}", file=sys.stderr)
            return 1

    print("GAIA Terminal Chat")
    print(f"- url: {url}")
    print("명령: /help, /url <new-url>, /exit")
    print("자연어로 테스트 목표를 입력하면 1회 실행합니다.")

    current_url = url
    while True:
        try:
            line = input("chat> ").strip()
        except KeyboardInterrupt:
            print("\n중단되었습니다.")
            return 130
        except EOFError:
            print()
            return 0

        if not line:
            continue
        if line == "/exit":
            return 0
        if line == "/help":
            print("/help")
            print("/url <new-url>")
            print("/exit")
            print("<자연어 목표>")
            continue
        if line.startswith("/url "):
            new_url = line[5:].strip()
            if not new_url:
                print("URL을 입력해주세요.")
                continue
            current_url = new_url
            print(f"url 변경됨: {current_url}")
            continue
        if line.startswith("/"):
            print("알 수 없는 명령입니다. /help를 확인하세요.")
            continue

        try:
            code, _ = _run_single_chat_goal(current_url, line, session_id=session_id)
            if code != 0:
                print(f"실행 종료 코드: {code}")
        except Exception as exc:
            print(f"Terminal chat failed: {exc}", file=sys.stderr)


def _run_ai_terminal_impl(
    *,
    url: str,
    max_actions: int = 50,
    session_id: str = WORKSPACE_DEFAULT,
    time_budget_seconds: int | None = None,
    intervention_callback: Optional[Callable[[str, str], Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    if not url:
        print("URL is required for terminal ai mode.", file=sys.stderr)
        return 2, {
            "goal": "autonomous_exploration",
            "status": "failed",
            "steps": 0,
            "reason": "url_required",
            "duration_seconds": 0.0,
            "reason_code_summary": {},
            "validation_summary": {},
            "validation_checks": [],
            "verification_report": {},
        }

    actions = max(1, int(max_actions))
    budget = int(time_budget_seconds or 0)
    if budget < 0:
        budget = 0
    if budget > 0:
        config = ExplorationConfig(
            loop_mode="time",
            time_budget_seconds=budget,
            max_actions=max(actions, 1),
            non_stop_mode=True,
        )
    else:
        config = ExplorationConfig(max_actions=actions, non_stop_mode=True)
    try:
        if intervention_callback is None:
            def _default_ai_intervention_callback(reason: str, current_url: str) -> Dict[str, Any]:
                _ = reason
                _ = current_url
                username = (
                    os.getenv("GAIA_TEST_USERNAME")
                    or os.getenv("GAIA_AUTH_USERNAME")
                    or ""
                ).strip()
                password = (
                    os.getenv("GAIA_TEST_PASSWORD")
                    or os.getenv("GAIA_AUTH_PASSWORD")
                    or ""
                ).strip()
                email = (
                    os.getenv("GAIA_TEST_EMAIL")
                    or os.getenv("GAIA_AUTH_EMAIL")
                    or ""
                ).strip()
                if username and password:
                    payload: Dict[str, Any] = {
                        "proceed": True,
                        "username": username,
                        "password": password,
                    }
                    if email:
                        payload["email"] = email
                    return payload
                return {"action": "cancel", "proceed": False, "reason_code": "user_intervention_missing"}

            intervention_callback = _default_ai_intervention_callback
        agent = ExploratoryAgent(
            mcp_host_url=CONFIG.mcp.host_url,
            session_id=session_id or WORKSPACE_DEFAULT,
            config=config,
            user_intervention_callback=intervention_callback,
        )
        if budget > 0:
            print(f"AI 자율 탐색 시작: {url} (time_budget={budget}s)")
        else:
            print(f"AI 탐색 시작: {url} (max_actions={actions})")
        result = agent.explore(url)
        print("\n탐색 결과")
        print(f"actions: {result.total_actions}")
        print(f"pages: {result.total_pages_visited}")
        print(f"issues: {len(result.issues_found)}")
        print(f"reason: {result.completion_reason}")
        if result.screenshots_dir:
            print(f"screenshots_dir: {result.screenshots_dir}")
        if result.recording_gif_path:
            print(f"gif: {result.recording_gif_path}")
        validation_summary = (
            dict(result.validation_summary)
            if isinstance(result.validation_summary, dict)
            else {}
        )
        validation_checks = (
            list(result.validation_checks)
            if isinstance(result.validation_checks, list)
            else []
        )
        verification_report = (
            dict(result.verification_report)
            if isinstance(result.verification_report, dict)
            else {}
        )
        strict_failed = False
        try:
            strict_failed = bool(validation_summary.get("strict_failed")) or int(
                validation_summary.get("failed_mandatory_checks") or 0
            ) > 0
        except Exception:
            strict_failed = False

        completion_reason = str(result.completion_reason or "")
        _print_llm_failure_help(completion_reason)
        code = 0
        if "insufficient_quota" in completion_reason.lower() or strict_failed:
            code = 1
        reason = completion_reason
        if strict_failed:
            reason = (
                "탐색 검증 필수 항목 실패가 감지되었습니다. "
                + (completion_reason or "")
            ).strip()
        summary = {
            "goal": "autonomous_exploration",
            "status": "success" if code == 0 else "failed",
            "steps": int(result.total_actions or 0),
            "reason": reason,
            "duration_seconds": round(float(result.duration_seconds), 2),
            "reason_code_summary": (
                verification_report.get("reason_code_summary")
                if isinstance(verification_report.get("reason_code_summary"), dict)
                else {}
            ),
            "container_source_summary": (
                verification_report.get("container_source_summary")
                if isinstance(verification_report.get("container_source_summary"), dict)
                else {}
            ),
            "active_scoped_container_ref": str(
                verification_report.get("active_scoped_container_ref") or ""
            ),
            "validation_summary": validation_summary,
            "validation_checks": validation_checks,
            "verification_report": verification_report,
        }
        return code, summary
    except KeyboardInterrupt:
        print("\n중단되었습니다.")
        return 130, {
            "goal": "autonomous_exploration",
            "status": "failed",
            "steps": 0,
            "reason": "keyboard_interrupt",
            "duration_seconds": 0.0,
            "reason_code_summary": {},
            "validation_summary": {},
            "validation_checks": [],
            "verification_report": {},
        }
    except Exception as exc:
        print(f"Terminal AI failed: {exc}", file=sys.stderr)
        return 1, {
            "goal": "autonomous_exploration",
            "status": "failed",
            "steps": 0,
            "reason": str(exc),
            "duration_seconds": 0.0,
            "reason_code_summary": {},
            "validation_summary": {},
            "validation_checks": [],
            "verification_report": {},
        }


def run_ai_terminal(
    *,
    url: str,
    max_actions: int = 50,
    session_id: str = WORKSPACE_DEFAULT,
    time_budget_seconds: int | None = None,
    intervention_callback: Optional[Callable[[str, str], Any]] = None,
) -> int:
    code, _ = _run_ai_terminal_impl(
        url=url,
        max_actions=max_actions,
        session_id=session_id,
        time_budget_seconds=time_budget_seconds,
        intervention_callback=intervention_callback,
    )
    return code


def run_ai_terminal_with_summary(
    *,
    url: str,
    max_actions: int = 50,
    session_id: str = WORKSPACE_DEFAULT,
    time_budget_seconds: int | None = None,
    intervention_callback: Optional[Callable[[str, str], Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    return _run_ai_terminal_impl(
        url=url,
        max_actions=max_actions,
        session_id=session_id,
        time_budget_seconds=time_budget_seconds,
        intervention_callback=intervention_callback,
    )


__all__ = [
    "run_chat_terminal",
    "run_chat_terminal_once",
    "run_ai_terminal",
    "run_ai_terminal_with_summary",
]
