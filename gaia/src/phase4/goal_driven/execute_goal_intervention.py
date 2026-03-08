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
        if not login_intervention_asked:
            has_login_test_data = agent._has_login_test_data(goal)
            if not has_login_test_data:
                if not agent._request_login_intervention(goal):
                    return {
                        "aborted": True,
                        "reason_code": "login_required",
                        "reason": (
                            "로그인 또는 회원가입이 필요한 화면이 열려 실행을 잠시 멈췄습니다. "
                            "계정 정보를 전달하거나, 브라우저에서 직접 로그인한 뒤 다시 진행해 주세요. "
                            "회원가입으로 진행하려면 auth_mode=signup을 함께 전달하면 됩니다."
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
        "reason_code": "",
        "reason": "",
        "has_login_test_data": has_login_test_data,
        "login_intervention_asked": login_intervention_asked,
    }
