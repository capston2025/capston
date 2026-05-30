from gaia.src.phase4.mcp_ref.action_executor import _is_fatal_timeout_abort, _is_visibility_timeout_abort
from gaia.src.phase4.mcp_ref.actionability_errors import extract_pointer_interceptor


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


def test_extract_pointer_interceptor_from_playwright_actionability_log() -> None:
    message = """
Timeout 5000ms exceeded.
Call log:
  - waiting for locator('aria-ref=e131')
    - locator resolved to <a href="#" class="skyview">스카이뷰</a>
    - element is visible, enabled and stable
    - <div id="dimmedLayer" class="DimmedLayer"></div> intercepts pointer events
"""

    blocker = extract_pointer_interceptor(message)

    assert blocker == {
        "tag": "div",
        "id": "dimmedLayer",
        "class": "DimmedLayer",
        "description": "div#dimmedLayer.DimmedLayer",
    }
