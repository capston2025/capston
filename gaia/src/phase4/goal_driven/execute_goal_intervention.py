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
                            "로그인 화면에서 사용자 개입이 필요하지만 입력이 제공되지 않아 중단했습니다. "
                            "다시 실행 후 로그인 진행 여부/계정 정보를 입력해 주세요."
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
