from types import SimpleNamespace

from gaia.terminal import (
    _apply_terminal_validation_outcome,
    _infer_goal_type,
    _should_preserve_runtime_success_from_validation,
    _should_run_terminal_semantic_filter_validation,
)


class _TerminalGateAgent:
    def __init__(self, goal_text: str, *, completion_source: str = "") -> None:
        self._active_goal_text = goal_text
        self._last_goal_completion_source = completion_source

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()


def test_terminal_semantic_gate_skips_generic_filter_change_goal() -> None:
    agent = _TerminalGateAgent(
        "구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘."
    )

    assert _should_run_terminal_semantic_filter_validation("filter_validation", agent) is False


def test_terminal_semantic_gate_keeps_generic_semantic_filter_goal() -> None:
    agent = _TerminalGateAgent("필터가 실제 결과 목록과 맞게 동작하는지 의미 검증해줘.")

    assert _should_run_terminal_semantic_filter_validation("filter_validation", agent) is True


def test_terminal_semantic_gate_ignores_non_filter_goal_type() -> None:
    agent = _TerminalGateAgent("로그인이 되는지 검증해줘.")

    assert _should_run_terminal_semantic_filter_validation("auth_validation", agent) is False


def test_infer_goal_type_does_not_treat_zero_credit_zero_state_as_filter_validation() -> None:
    goal_type = _infer_goal_type("위시리스트를 전부 비우고 총 0개 과목 또는 0학점 상태를 확인해줘.")

    assert goal_type == "goal_execution"


def test_runtime_judge_success_is_preserved_against_terminal_validation_override() -> None:
    agent = _TerminalGateAgent("응답이 보이면 완료해줘.", completion_source="judge")
    result = SimpleNamespace(success=True)
    report = {
        "summary": {
            "goal_type": "filter_validation_semantic",
            "strict_failed": True,
            "failed_mandatory_checks": 2,
            "goal_satisfied": False,
        }
    }

    preserve = _should_preserve_runtime_success_from_validation(agent, result)
    effective_success, effective_reason = _apply_terminal_validation_outcome(
        result_success=True,
        result_reason="현재 화면 증거상 목표가 완료되었습니다.",
        validation_report=report,
        preserve_runtime_success=preserve,
    )

    assert preserve is True
    assert effective_success is True
    assert effective_reason == "현재 화면 증거상 목표가 완료되었습니다."
