from gaia.src.phase4.mcp_ref_action_executor import _is_fatal_timeout_abort, _is_visibility_timeout_abort


def test_is_visibility_timeout_abort_detects_locator_visibility_timeout() -> None:
    message = 'Timeout 5000ms exceeded while waiting for locator("#foo") to be visible'

    assert _is_visibility_timeout_abort(message) is True


def test_is_visibility_timeout_abort_ignores_generic_budget_timeout() -> None:
    assert _is_visibility_timeout_abort("action budget exceeded (45.0s)") is False


def test_is_fatal_timeout_abort_ignores_locator_visibility_timeout() -> None:
    message = 'Timeout 5000ms exceeded while waiting for locator("#foo") to be visible'

    assert _is_fatal_timeout_abort(message) is False


def test_is_fatal_timeout_abort_detects_budget_deadline_timeout() -> None:
    assert _is_fatal_timeout_abort("action budget exceeded (45.0s)") is True
