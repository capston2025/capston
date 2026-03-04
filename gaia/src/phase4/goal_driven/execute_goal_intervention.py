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
        agent._log("🔐 로그인/인증 화면이 감지되었습니다.")
        if not login_intervention_asked:
            has_login_test_data = agent._has_login_test_data(goal)
            if not has_login_test_data:
                if not agent._request_login_intervention(goal):
                    return {
                        "aborted": True,
                        "reason": (
                        "로그인 화면에서 사용자 개입이 필요해 실행을 일시 중지했습니다. "
                        "사용자 응답(/handoff 또는 재실행 인자)으로 로그인/회원가입(auth_mode=signup) 정보를 제공해 주세요."
                        ),
                        "has_login_test_data": has_login_test_data,
                        "login_intervention_asked": login_intervention_asked,
                    }
                has_login_test_data = agent._has_login_test_data(goal)
            else:
                agent._log("🔁 기존 로그인/회원가입 입력 데이터를 재사용합니다.")
            login_intervention_asked = True
    else:
        login_intervention_asked = False

    return {
        "aborted": False,
        "reason": "",
        "has_login_test_data": has_login_test_data,
        "login_intervention_asked": login_intervention_asked,
    }
