from gaia.terminal import _should_run_terminal_semantic_filter_validation


class _TerminalGateAgent:
    def __init__(self, goal_text: str) -> None:
        self._active_goal_text = goal_text

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()


def test_terminal_semantic_gate_skips_generic_filter_change_goal() -> None:
    agent = _TerminalGateAgent(
        "구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘."
    )

    assert _should_run_terminal_semantic_filter_validation("filter_validation", agent) is False


def test_terminal_semantic_gate_keeps_credit_semantic_goal() -> None:
    agent = _TerminalGateAgent("학점 필터가 실제 결과 과목의 학점과 맞게 동작하는지 의미 검증해줘.")

    assert _should_run_terminal_semantic_filter_validation("filter_validation", agent) is True


def test_terminal_semantic_gate_ignores_non_filter_goal_type() -> None:
    agent = _TerminalGateAgent("로그인이 되는지 검증해줘.")

    assert _should_run_terminal_semantic_filter_validation("auth_validation", agent) is False
