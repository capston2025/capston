from gaia.src.phase4.goal_driven.agent import GoalDrivenAgent


def test_fatal_llm_reason_prefers_codex_timeout_message() -> None:
    reason = GoalDrivenAgent._fatal_llm_reason(
        "codex exec failed: codex_exec_timeout:300s"
    )

    assert reason is not None
    assert "제한 시간 안에 끝나지 않았습니다" in reason
    assert "실행 인자/버전 오류" not in reason
