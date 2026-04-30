from __future__ import annotations

from typing import Any, Dict

from .models import TestGoal


def handle_login_intervention(
    *,
    agent: Any,
    goal: TestGoal,
    login_gate_visible: bool,
    has_login_test_data: bool,
    login_intervention_asked: bool,
) -> Dict[str, Any]:
    if login_gate_visible:
        agent._log("🔐 로그인 또는 회원가입 화면이 감지되었습니다.")
        has_login_test_data = agent._has_login_test_data(goal)
        if has_login_test_data:
            agent._log("🔁 기존 로그인/회원가입 입력 데이터를 재사용합니다.")
        elif not login_intervention_asked:
            agent._action_feedback.append(
                "인증 화면이 보입니다. 현재 test_data에 필요한 값이 없으면 human_answer skill을 사용해 "
                "필요한 필드를 직접 지정해서 사용자에게 요청하세요."
            )
            if len(agent._action_feedback) > 10:
                agent._action_feedback = agent._action_feedback[-10:]
            login_intervention_asked = True
    else:
        login_intervention_asked = False

    return {
        "aborted": False,
        "reason_code": "",
        "reason": "",
        "has_login_test_data": has_login_test_data,
        "login_intervention_asked": login_intervention_asked,
    }
