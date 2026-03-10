from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse

from gaia.src.phase4.memory.models import MemoryActionRecord


def extract_domain(url: str) -> str:
    parsed = urlparse(url or "")
    return (parsed.netloc or "").lower()


def memory_context(agent) -> str:
    if not agent._memory_store.enabled or not agent._memory_domain:
        return ""
    hints = agent._memory_retriever.retrieve_lightweight(
        domain=agent._memory_domain,
        goal_text="exploratory testing",
        action_history=agent._action_history[-6:],
    )
    return agent._memory_retriever.format_for_prompt(hints)


def record_action_memory(
    agent,
    *,
    step_number: int,
    action_type: str,
    selector: str,
    success: bool,
    error: Optional[str],
) -> None:
    if not agent._memory_store.enabled or agent._memory_episode_id is None:
        return
    meta = agent._last_exec_meta or {}
    reason_code = str(meta.get("reason_code") or ("ok" if success else "unknown_error"))
    changed = reason_code not in {"no_state_change"}
    try:
        agent._memory_store.record_action(
            MemoryActionRecord(
                episode_id=agent._memory_episode_id,
                domain=agent._memory_domain,
                url=agent._current_url or "",
                step_number=step_number,
                action=action_type,
                selector=selector,
                full_selector=selector,
                ref_id=str(meta.get("ref_id_used") or ""),
                success=success,
                effective=bool(meta.get("effective", success)),
                changed=changed,
                reason_code=reason_code,
                reason=str(meta.get("reason") or (error or "")),
                snapshot_id=str(meta.get("snapshot_id_used") or agent._active_snapshot_id),
                dom_hash=agent._active_dom_hash,
                epoch=agent._active_snapshot_epoch,
                frame_index=None,
                tab_index=None,
                state_change=meta.get("state_change")
                if isinstance(meta.get("state_change"), dict)
                else {},
                attempt_logs=meta.get("attempt_logs")
                if isinstance(meta.get("attempt_logs"), list)
                else [],
            )
        )
    except Exception:
        return


def is_login_page_with_no_elements(page_state) -> bool:
    login_keywords = ["login", "signin", "auth", "sso", "portal"]
    url_lower = page_state.url.lower()
    has_login_keyword = any(keyword in url_lower for keyword in login_keywords)
    has_few_elements = len(page_state.interactive_elements) <= 2
    return has_login_keyword and has_few_elements


def request_user_intervention(agent, reason: str, current_url: str) -> bool:
    agent._log("=" * 60)
    agent._log("⏸️  사용자 개입 필요")
    agent._log(f"   이유: {reason}")
    agent._log(f"   현재 URL: {current_url}")
    agent._log("=" * 60)

    if agent._user_intervention_callback:
        callback_resp = agent._user_intervention_callback(reason, current_url)
        if isinstance(callback_resp, dict):
            username = str(
                callback_resp.get("username")
                or callback_resp.get("id")
                or callback_resp.get("user")
                or ""
            ).strip()
            email = str(callback_resp.get("email") or "").strip()
            password = str(callback_resp.get("password") or "").strip()
            auth_mode = str(callback_resp.get("auth_mode") or "").strip().lower()
            manual_done = bool(callback_resp.get("manual_done"))
            proceed_raw = callback_resp.get("proceed")
            proceed = True
            if isinstance(proceed_raw, bool):
                proceed = proceed_raw
            elif isinstance(proceed_raw, str):
                proceed = proceed_raw.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "y",
                    "on",
                    "continue",
                    "c",
                }
            if auth_mode in {"signup", "register"}:
                agent._auth_input_values["auth_mode"] = "signup"
            if username:
                agent._auth_input_values["username"] = username
            if email:
                agent._auth_input_values["email"] = email
            if password:
                agent._auth_input_values["password"] = password
            if manual_done:
                agent._auth_input_values["manual_done"] = "true"
            if proceed:
                agent._forced_completion_reason = ""
            else:
                agent._forced_completion_reason = (
                    "auth_required: 로그인 요청이 와서 사용자 입력을 기다리는 중입니다. "
                    "로그인 요청왔는데 어떻게 할까요? 아이디 비밀번호를 알려주세요."
                )
            return proceed
        proceed = bool(callback_resp)
        if proceed:
            agent._forced_completion_reason = ""
        else:
            agent._forced_completion_reason = (
                "auth_required: 로그인 요청이 와서 사용자 입력을 기다리는 중입니다. "
                "로그인 요청왔는데 어떻게 할까요? 아이디 비밀번호를 알려주세요."
            )
        return proceed

    interactive_stdin = False
    try:
        interactive_stdin = bool(os.isatty(0))
    except Exception:
        interactive_stdin = False
    if not interactive_stdin:
        agent._forced_completion_reason = (
            "auth_required: 로그인 요청이 와서 사용자 입력을 기다리는 중입니다. "
            "로그인 요청왔는데 어떻게 할까요? 아이디 비밀번호를 알려주세요."
        )
        agent._log(
            "⏸️ 로그인 요청왔는데 어떻게 할까요? 아이디 비밀번호를 알려주세요. "
            "비대화 실행이라 입력을 받을 수 없어 현재 실행을 일시 중지합니다."
        )
        return False
    print("\n🔔 사용자 개입이 필요합니다!")
    print(f"이유: {reason}")
    print(f"현재 URL: {current_url}")
    print("로그인 요청왔는데 어떻게 할까요? 아이디 비밀번호를 알려주세요.")
    print("\n브라우저에서 필요한 작업(로그인 등)을 완료한 후,")
    user_input = (
        input("계속하려면 'c' 또는 'continue'를 입력하세요 (중단: 'q'): ")
        .strip()
        .lower()
    )

    if user_input in ["c", "continue", "yes", "y"]:
        agent._log("✅ 사용자가 작업을 완료했습니다. 탐색을 계속합니다.")
        return True
    agent._log("❌ 사용자가 탐색 중단을 요청했습니다.")
    return False
