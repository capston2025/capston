from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from .models import TestGoal
from .site_auth_store import save_site_credentials


def has_login_test_data(goal: TestGoal) -> bool:
    data = goal.test_data or {}
    if not isinstance(data, dict):
        return False
    keys = {str(k).strip().lower() for k in data.keys()}
    has_id = any(k in keys for k in {"email", "id", "username", "login_id", "user"})
    has_pw = any(k in keys for k in {"password", "pw", "passwd"})
    return has_id and has_pw


def request_user_intervention(agent: Any, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not agent._intervention_callback:
        return None
    try:
        enriched = dict(payload or {})
        attachments = enriched.get("attachments")
        has_image = False
        if isinstance(attachments, list):
            for item in attachments:
                if not isinstance(item, dict):
                    continue
                if str(item.get("kind") or "").strip().lower() == "image_base64":
                    has_image = True
                    break
        if not has_image:
            shot = agent._capture_screenshot()
            if isinstance(shot, str) and shot.strip():
                enriched["attachments"] = [
                    *([item for item in attachments if isinstance(attachments, list)] if isinstance(attachments, list) else []),
                    {
                        "kind": "image_base64",
                        "mime": "image/png",
                        "data": shot,
                        "caption": "현재 화면",
                        "label": "개입 요청 시점 화면",
                    },
                ]
        resp = agent._intervention_callback(enriched)
        return resp if isinstance(resp, dict) else None
    except Exception as exc:
        agent._log(f"사용자 개입 콜백 오류: {exc}")
        return None


def merge_test_data(
    goal: TestGoal,
    payload: Dict[str, Any],
    *,
    blocked_keys: set[str] | None = None,
) -> None:
    if not isinstance(payload, dict):
        return
    blocked = blocked_keys or set()
    if not isinstance(goal.test_data, dict):
        goal.test_data = {}
    for key, value in payload.items():
        norm_key = str(key or "").strip()
        if not norm_key or norm_key in blocked:
            continue
        if value is None:
            continue
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                continue
            goal.test_data[norm_key] = cleaned
            continue
        goal.test_data[norm_key] = value


def to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def request_goal_clarification(agent: Any, goal: TestGoal) -> bool:
    text = f"{goal.name} {goal.description}".strip().lower()
    if not text:
        return False

    ambiguous_tokens = {"안녕", "하이", "hello", "hi", "test", "테스트", "해봐", "해줘"}
    tokens = {w.strip() for w in text.replace("/", " ").split() if w.strip()}
    looks_ambiguous = len(text) < 8 or (tokens and tokens.issubset(ambiguous_tokens))

    sensitive_hints = (
        "로그인",
        "회원가입",
        "인증",
        "결제",
        "payment",
        "purchase",
        "구매",
        "주문",
        "예약",
    )
    needs_sensitive_data = any(h in text for h in sensitive_hints)

    if not looks_ambiguous and not (needs_sensitive_data and not has_login_test_data(goal)):
        agent._handoff_state = {
            "kind": "clarification",
            "provided": False,
            "phase": agent._runtime_phase,
        }
        return True

    callback_payload = {
        "kind": "clarification",
        "goal_name": goal.name,
        "goal_description": goal.description,
        "question": (
            "목표가 모호하거나 중요한 입력 정보가 부족합니다. "
            "구체 목표와 필요한 입력(id/pw/email 등)을 제공해 주세요."
        ),
        "fields": ["goal_text", "username", "email", "password", "proceed"],
    }
    callback_resp = agent._request_user_intervention(callback_payload)
    if callback_resp is not None:
        callback_reason_code = str(callback_resp.get("reason_code") or "").strip().lower()
        if str(callback_resp.get("action") or "").lower() in {"cancel", "deny", "no"}:
            agent._record_reason_code(callback_reason_code or "user_intervention_missing")
            return False

        goal_text = str(callback_resp.get("goal_text") or "").strip()
        if goal_text:
            goal.name = goal_text[:40]
            goal.description = goal_text
            goal.success_criteria = [goal_text]

        username = str(callback_resp.get("username") or "").strip()
        email = str(callback_resp.get("email") or "").strip()
        password = str(callback_resp.get("password") or "").strip()
        if username or email or password:
            if not isinstance(goal.test_data, dict):
                goal.test_data = {}
            if username:
                goal.test_data["username"] = username
            if email:
                goal.test_data["email"] = email
            if password:
                goal.test_data["password"] = password
        merge_test_data(
            goal,
            callback_resp,
            blocked_keys={"action", "proceed", "goal_text", "username", "email", "password"},
        )
        agent._handoff_state = {
            "kind": "clarification",
            "provided": True,
            "phase": agent._runtime_phase,
            "timestamp": int(time.time()),
        }
        if callback_reason_code:
            agent._record_reason_code(callback_reason_code)
        proceed = callback_resp.get("proceed")
        if isinstance(proceed, bool):
            return proceed
        if isinstance(proceed, str):
            return to_bool(proceed, default=True)
        return True

    agent._log("🙋 사용자 개입 필요: 목표가 모호하거나 중요한 정보가 부족합니다.")
    try:
        interactive_stdin = bool(os.isatty(0))
    except Exception:
        interactive_stdin = False
    if not interactive_stdin:
        agent._handoff_state = {
            "kind": "clarification",
            "provided": False,
            "phase": agent._runtime_phase,
            "requested": True,
            "timestamp": int(time.time()),
        }
        agent._record_reason_code("user_intervention_missing")
        agent._log(
            "⏸️ 비대화 실행이라 추가 입력을 받을 수 없습니다. "
            "실행을 일시 중지하고 사용자 응답(/handoff 또는 재실행 인자) 대기 상태로 전환합니다."
        )
        return False
    try:
        refined = input("구체 목표를 입력하세요 (비우면 기존 목표 유지): ").strip()
    except (EOFError, KeyboardInterrupt):
        agent._record_reason_code("user_intervention_missing")
        agent._log("사용자 입력이 중단되었습니다.")
        return False
    if refined:
        goal.name = refined[:40]
        goal.description = refined
        goal.success_criteria = [refined]
        agent._handoff_state = {
            "kind": "clarification",
            "provided": True,
            "phase": agent._runtime_phase,
            "timestamp": int(time.time()),
        }

    if needs_sensitive_data and not has_login_test_data(goal):
        try:
            login_id = input("아이디/이메일 (건너뛰려면 Enter): ").strip()
            password = input("비밀번호 (건너뛰려면 Enter): ").strip()
        except (EOFError, KeyboardInterrupt):
            agent._log("사용자 입력이 중단되었습니다.")
            return False
        if login_id or password:
            if not isinstance(goal.test_data, dict):
                goal.test_data = {}
            if login_id:
                goal.test_data["username"] = login_id
                if "@" in login_id and not str(goal.test_data.get("email") or "").strip():
                    goal.test_data["email"] = login_id
            if password:
                goal.test_data["password"] = password
    return True


def request_login_intervention(agent: Any, goal: TestGoal) -> bool:
    agent._log("🙋 사용자 개입 필요: 로그인 또는 회원가입 화면이 감지되었습니다.")
    agent._handoff_state = {
        "kind": "auth",
        "phase": agent._runtime_phase,
        "requested": True,
        "timestamp": int(time.time()),
    }
    callback_payload = {
        "kind": "auth",
        "goal_name": goal.name,
        "goal_description": goal.description,
        "question": (
            "로그인 또는 회원가입이 필요한 화면이 열렸습니다. "
            "계정 정보를 전달하거나, 브라우저에서 직접 로그인한 뒤 완료 여부를 알려주세요. "
            "회원가입으로 진행하려면 auth_mode=signup, 로그인하지 않고 계속하려면 auth_mode=skip을 함께 보내면 됩니다."
        ),
        "fields": [
            "proceed",
            "auth_mode",
            "manual_done",
            "username",
            "email",
            "password",
            "department",
            "grade_year",
            "return_credentials",
        ],
    }
    callback_resp = agent._request_user_intervention(callback_payload)
    if callback_resp is None:
        try:
            interactive_stdin = bool(os.isatty(0))
        except Exception:
            interactive_stdin = False
        if not interactive_stdin:
            agent._handoff_state["provided"] = False
            agent._handoff_state["mode"] = "awaiting_user_input"
            agent._log(
                "⏸️ 로그인 또는 회원가입 개입이 필요하지만 현재 실행 환경에서는 바로 입력을 받을 수 없습니다. "
                "실행을 멈추고 사용자 응답을 기다립니다."
            )
            return False
    if callback_resp is not None:
        if str(callback_resp.get("action") or "").lower() in {"cancel", "deny", "no"}:
            agent._log("로그인 개입이 취소되었습니다.")
            return False
        if bool(callback_resp.get("manual_done")):
            agent._log("사용자가 수동 로그인 완료를 전달했습니다.")
            agent._handoff_state["provided"] = True
            agent._handoff_state["mode"] = "manual_done"
            return True
        auth_mode = str(callback_resp.get("auth_mode") or "").strip().lower()
        if auth_mode in {"skip", "declined", "dismiss", "close", "no_login"}:
            if not isinstance(goal.test_data, dict):
                goal.test_data = {}
            goal.test_data["auth_mode"] = "skip"
            agent._log("사용자 요청에 따라 로그인하지 않고 진행합니다.")
            agent._handoff_state["provided"] = True
            agent._handoff_state["mode"] = "declined"
            return True
        username = str(callback_resp.get("username") or "").strip()
        email = str(callback_resp.get("email") or "").strip()
        password = str(callback_resp.get("password") or "").strip()
        login_id = username or email
        department = str(callback_resp.get("department") or "").strip()
        grade_year = str(callback_resp.get("grade_year") or "").strip()
        return_credentials = to_bool(callback_resp.get("return_credentials"), default=False)

        if auth_mode in {"signup", "register"}:
            if not login_id:
                suffix = int(time.time()) % 100000
                login_id = f"gaia_user_{suffix:05d}"
            if not password:
                suffix = int(time.time()) % 100000
                password = f"Gaia!{suffix:05d}"
            if "@" in login_id:
                email = email or login_id
                username = username or login_id.split("@")[0]
            elif not email:
                email = f"{login_id}@gaia.local"
            if not isinstance(goal.test_data, dict):
                goal.test_data = {}
            goal.test_data["auth_mode"] = "signup"
            goal.test_data["username"] = username or login_id
            goal.test_data["email"] = email
            goal.test_data["password"] = password
            if department:
                goal.test_data["department"] = department
            if grade_year:
                goal.test_data["grade_year"] = grade_year
            goal.test_data["return_credentials"] = return_credentials
            merge_test_data(
                goal,
                callback_resp,
                blocked_keys={
                    "action",
                    "proceed",
                    "auth_mode",
                    "manual_done",
                    "username",
                    "email",
                    "password",
                    "department",
                    "grade_year",
                    "return_credentials",
                },
            )
            agent._log("사용자 요청에 따라 회원가입 모드로 진행합니다.")
            if return_credentials:
                save_site_credentials(
                    goal.start_url,
                    username=str(goal.test_data.get("username") or ""),
                    password=str(goal.test_data.get("password") or ""),
                    email=str(goal.test_data.get("email") or ""),
                )
            if return_credentials:
                agent._log(
                    f"회원가입에 사용할 계정: username={goal.test_data.get('username')} "
                    f"email={goal.test_data.get('email')} password={goal.test_data.get('password')}"
                )
            agent._handoff_state["provided"] = True
            agent._handoff_state["mode"] = "signup"
            return True

        if login_id and password:
            if not isinstance(goal.test_data, dict):
                goal.test_data = {}
            goal.test_data["username"] = login_id
            if email or ("@" in login_id and not str(goal.test_data.get("email") or "").strip()):
                goal.test_data["email"] = email or login_id
            goal.test_data["password"] = password
            merge_test_data(
                goal,
                callback_resp,
                blocked_keys={
                    "action",
                    "proceed",
                    "auth_mode",
                    "manual_done",
                    "username",
                    "email",
                    "password",
                    "department",
                    "grade_year",
                    "return_credentials",
                },
            )
            save_site_credentials(
                goal.start_url,
                username=login_id,
                password=password,
                email=email or (login_id if "@" in login_id else ""),
            )
            agent._log("사용자 로그인 정보가 test_data에 반영되었습니다.")
            agent._handoff_state["provided"] = True
            agent._handoff_state["mode"] = "login"
            return True
        agent._log("로그인 정보가 충분하지 않습니다.")
        return False

    try:
        answer = input("로그인을 진행할까요? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        agent._log("사용자 입력이 중단되었습니다.")
        return False

    if answer in {"n", "no"}:
        if not isinstance(goal.test_data, dict):
            goal.test_data = {}
        goal.test_data["auth_mode"] = "skip"
        agent._log("사용자 요청에 따라 로그인하지 않고 진행합니다.")
        agent._handoff_state["provided"] = True
        agent._handoff_state["mode"] = "declined"
        return True

    try:
        login_id = input("아이디/이메일 (비우면 브라우저에서 수동 로그인): ").strip()
    except (EOFError, KeyboardInterrupt):
        agent._log("사용자 입력이 중단되었습니다.")
        return False

    if not login_id:
        agent._log("브라우저에서 직접 로그인 후 Enter를 눌러 계속하세요.")
        try:
            input("로그인 완료 후 Enter: ")
        except (EOFError, KeyboardInterrupt):
            agent._log("사용자 입력이 중단되었습니다.")
            return False
        agent._handoff_state["provided"] = True
        agent._handoff_state["mode"] = "manual_done"
        return True

    try:
        password = input("비밀번호: ")
    except (EOFError, KeyboardInterrupt):
        agent._log("사용자 입력이 중단되었습니다.")
        return False

    if not str(password or "").strip():
        agent._log("비밀번호가 비어 있어 진행을 중단합니다.")
        return False

    if not isinstance(goal.test_data, dict):
        goal.test_data = {}
    goal.test_data["username"] = login_id
    if "@" in login_id and not str(goal.test_data.get("email") or "").strip():
        goal.test_data["email"] = login_id
    goal.test_data["password"] = password
    save_site_credentials(
        goal.start_url,
        username=login_id,
        password=password,
        email=str(goal.test_data.get("email") or ""),
    )
    agent._log("사용자 로그인 정보가 test_data에 반영되었습니다.")
    agent._handoff_state["provided"] = True
    agent._handoff_state["mode"] = "login"
    return True
