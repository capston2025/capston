from gaia.src.phase4.mcp_server.error_converter import to_ai_friendly_error_str


def test_to_ai_friendly_error_str_reports_pointer_interceptor_before_visibility_timeout() -> None:
    message = """
Timeout 5000ms exceeded.
Call log:
  - waiting for locator('aria-ref=e131')
    - locator resolved to <a href="#" class="skyview">스카이뷰</a>
    - element is visible, enabled and stable
    - <div id="dimmedLayer" class="DimmedLayer"></div> intercepts pointer events
"""

    friendly = to_ai_friendly_error_str(message, ref_id="e131")

    assert "가려져 상호작용할 수 없습니다" in friendly
    assert "찾을 수 없거나 표시되지" not in friendly
