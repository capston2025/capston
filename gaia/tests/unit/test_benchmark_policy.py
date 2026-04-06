from gaia.harness.benchmark_policy import apply_benchmark_success_policy


def test_apply_benchmark_success_policy_rejects_wait_fallback_success() -> None:
    status, reason, meta = apply_benchmark_success_policy(
        status="SUCCESS",
        reason="모델 판단으로 완료",
        summary={"goal_completion_source": "wait_fallback"},
    )

    assert status == "FAIL"
    assert "benchmark_policy_rejected_completion_source(wait_fallback)" in reason
    assert meta["rejected_completion_source"] == "wait_fallback"


def test_apply_benchmark_success_policy_keeps_judge_success() -> None:
    status, reason, meta = apply_benchmark_success_policy(
        status="SUCCESS",
        reason="judge completed",
        summary={"goal_completion_source": "judge"},
    )

    assert status == "SUCCESS"
    assert reason == "judge completed"
    assert meta == {}


def test_apply_benchmark_success_policy_leaves_failures_unchanged() -> None:
    status, reason, meta = apply_benchmark_success_policy(
        status="FAIL",
        reason="timeout",
        summary={"goal_completion_source": "wait_fallback"},
    )

    assert status == "FAIL"
    assert reason == "timeout"
    assert meta == {}
