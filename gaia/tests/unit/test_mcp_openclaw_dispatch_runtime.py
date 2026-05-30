from __future__ import annotations

import base64

import requests

from gaia.src.phase4 import mcp_openclaw_dispatch_runtime as runtime
from gaia.src.phase4.browser_context_manager import (
    choose_auto_follow_tab,
    looks_like_non_document_surface,
)


_DEFAULT_URL = "https://example.com/app"


def test_openclaw_snapshot_max_chars_defaults_to_full(monkeypatch):
    monkeypatch.delenv("GAIA_OPENCLAW_SNAPSHOT_MAX_CHARS", raising=False)

    assert runtime._openclaw_snapshot_max_chars_param() == 0


def test_openclaw_snapshot_max_chars_accepts_explicit_cap(monkeypatch):
    monkeypatch.setenv("GAIA_OPENCLAW_SNAPSHOT_MAX_CHARS", "120000")

    assert runtime._openclaw_snapshot_max_chars_param() == 120000


def test_normalize_failure_reports_pointer_interceptor_details():
    message = """
Timeout 5000ms exceeded.
Call log:
  - waiting for locator('aria-ref=e131')
    - locator resolved to <a href="#" class="skyview">스카이뷰</a>
    - element is visible, enabled and stable
    - <div id="dimmedLayer" class="DimmedLayer"></div> intercepts pointer events
"""

    status_code, payload, text = runtime._normalize_failure(500, {"error": message}, "")

    assert status_code == 200
    assert payload["success"] is False
    assert payload["reason_code"] == "pointer_intercepted"
    assert payload["attempt_count"] == 1
    assert payload["state_change"]["pointer_interceptor"]["description"] == "div#dimmedLayer.DimmedLayer"
    assert payload["attempt_logs"][0]["pointer_interceptor"]["id"] == "dimmedLayer"
    assert text == message


def test_build_snapshot_payload_merges_dom_text_evidence():
    state = _seed_session("dom-text-session")

    payload = runtime._build_snapshot_payload(
        session_id="dom-text-session",
        target_id="tab-1",
        current_url=_DEFAULT_URL,
        requested_scope_ref_id="",
        raw_snapshot={
            "snapshot": '- link "의견/리뷰 1,402" [ref=e1]',
            "refs": {"e1": {"role": "link", "name": "의견/리뷰 1,402"}},
        },
        state=state,
        dom_text_blocks=[
            {
                "text": "청소기가 좀 시끄러워요. 밤에는 돌리기 어려운 편입니다.",
                "tag": "li",
                "selector": ".post_comments .cmt_list > li:nth-of-type(1)",
                "section": "post_comments cmt_list",
                "score": 72,
            }
        ],
    )

    role_snapshot = payload["role_snapshot"]
    evidence = payload["evidence"]
    assert "[DOM text evidence]" in role_snapshot["snapshot"]
    assert "청소기가 좀 시끄러워요" in role_snapshot["snapshot"]
    assert evidence["dom_text_block_count"] == 1
    assert "청소기가 좀 시끄러워요" in evidence["text_digest"]
    assert any("밤에는 돌리기 어려운 편" in text for text in evidence["live_texts"])


def test_select_ref_actionability_probe_candidates_prioritizes_short_click_targets(monkeypatch):
    monkeypatch.setenv("GAIA_OPENCLAW_ACTIONABILITY_PROBE_LIMIT", "3")
    payload = {
        "role_snapshot": {"ref_line_index": {"e1": 1, "e2": 2, "e3": 3, "e4": 4}},
        "elements": [
            {"ref_id": "e1", "tag": "a", "text": "아주 긴 광고성 링크입니다", "attributes": {"role": "link"}},
            {"ref_id": "e2", "tag": "button", "text": "검색어", "attributes": {"role": "textbox"}},
            {"ref_id": "e3", "tag": "button", "text": "2", "attributes": {"role": "button"}},
            {"ref_id": "e4", "tag": "button", "text": "적용하기", "attributes": {"role": "button"}},
        ],
    }

    refs = runtime._select_ref_actionability_probe_candidates(payload)

    assert refs == ["e3", "e4", "e1"]


def test_apply_ref_actionability_reports_marks_covered_ref_in_payload_and_role_tree():
    state = _seed_session("actionability-mark-session")
    payload = runtime._build_snapshot_payload(
        session_id="actionability-mark-session",
        target_id="tab-1",
        current_url=_DEFAULT_URL,
        requested_scope_ref_id="",
        raw_snapshot={
            "snapshot": '- button "2" [ref=e1]\n- button "적용하기" [ref=e2]',
            "refs": {
                "e1": {"role": "button", "name": "2"},
                "e2": {"role": "button", "name": "적용하기"},
            },
        },
        state=state,
    )

    warnings = runtime._apply_ref_actionability_reports_to_payload(
        payload,
        [
            {
                "ref": "e1",
                "status": "covered",
                "actionable": False,
                "reason": "center_hits_other_element",
                "hit": {"tag": "div", "className": "flex h-full flex-col"},
            }
        ],
    )

    assert len(warnings) == 1
    meta = payload["elements_by_ref"]["e1"]
    assert meta["attributes"]["gaia-disabled"] == "true"
    assert meta["attributes"]["openclaw_actionability"] == "covered"
    assert "not-actionable=covered by div.flex.h-full.flex-col" in payload["role_snapshot"]["snapshot"]
    assert payload["evidence"]["actionability_warning_count"] == 1


def _evidence(text: str, *, live_texts: list[str] | None = None, logout_visible: bool = False) -> dict[str, object]:
    return {
        "text_digest": text,
        "live_texts": list(live_texts or [text]),
        "list_count": 1,
        "interactive_count": 1,
        "modal_count": 0,
        "backdrop_count": 0,
        "dialog_count": 0,
        "modal_open": False,
        "auth_prompt_visible": False,
        "login_visible": False,
        "logout_visible": bool(logout_visible),
    }


def _seed_session(
    session_id: str,
    *,
    target_id: str = "tab-1",
    current_url: str = _DEFAULT_URL,
    profile: str = "openclaw",
    snapshot_counter: int = 0,
    **extra: object,
) -> dict[str, object]:
    state = runtime._session_state(session_id)
    state.clear()
    state.update(
        {
            "target_id": target_id,
            "current_url": current_url,
            "profile": profile,
            "snapshot_counter": snapshot_counter,
            "last_snapshot_id": "",
            "last_snapshot_payload": {},
            "last_tabs_payload": {},
            "last_tabs_target_id": "",
            "last_tabs_profile": "",
            "last_tabs_observed_at": 0.0,
        }
    )
    state.update(extra)
    return state


def _build_cached_snapshot(
    *,
    session_id: str,
    state: dict[str, object],
    current_url: str = _DEFAULT_URL,
    role: str = "textbox",
    name: str = "검색어",
    ref_id: str = "e1",
) -> dict[str, object]:
    return runtime._build_snapshot_payload(
        session_id=session_id,
        target_id="tab-1",
        current_url=current_url,
        requested_scope_ref_id="",
        raw_snapshot={
            "snapshot": f'- {role} "{name}" [ref={ref_id}]',
            "refs": {ref_id: {"role": role, "name": name}},
        },
        state=state,
    )


def test_resolve_base_url_uses_embedded_runtime_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("GAIA_OPENCLAW_BASE_URL", raising=False)
    monkeypatch.setattr(
        runtime,
        "ensure_embedded_openclaw_base_url",
        lambda: "http://127.0.0.1:18791",
    )

    assert runtime._resolve_base_url(None) == "http://127.0.0.1:18791"


def test_coerce_request_timeout_uses_default_tuple_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("GAIA_OPENCLAW_REQUEST_TIMEOUT_S", raising=False)

    assert runtime._coerce_request_timeout(None) == (3.0, 12.0)


def test_request_uses_default_timeout_tuple(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True}

    def fake_request(*, method, url, params, json, headers, timeout):
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(requests, "request", fake_request)

    status_code, data, text = runtime._request(
        "GET",
        base_url="http://127.0.0.1:18791",
        path="/snapshot",
        timeout=None,
    )

    assert status_code == 200
    assert data == {"ok": True}
    assert text == ""
    assert seen["timeout"] == (3.0, 12.0)


def test_request_prefers_payload_profile_over_default_profile(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True}

    def fake_request(*, method, url, params, json, headers, timeout):
        seen["params"] = params
        return _FakeResponse()

    monkeypatch.setattr(requests, "request", fake_request)

    runtime._request(
        "POST",
        base_url="http://127.0.0.1:18791",
        path="/tabs/open",
        payload={"url": "https://example.com", "profile": "gaia-test-sender"},
    )

    assert seen["params"]["profile"] == "gaia-test-sender"


def test_ensure_openclaw_profile_creates_missing_profile_and_starts(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    calls: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        calls.append((method, path, dict(params or {}), dict(payload or {})))
        if path == "/profiles":
            return 200, {"profiles": [{"name": "openclaw"}]}, ""
        if path == "/profiles/create":
            return 200, {"ok": True, "profile": "gaia-test-sender"}, ""
        if path == "/start":
            return 200, {"ok": True, "profile": "gaia-test-sender"}, ""
        raise AssertionError(path)

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.ensure_openclaw_profile(
        None,
        profile="gaia-test-sender",
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["profile"] == "gaia-test-sender"
    assert payload["created"] is True
    assert calls == [
        ("GET", "/profiles", {"profile": "gaia-test-sender"}, {}),
        ("POST", "/profiles/create", {}, {"name": "gaia-test-sender", "profile": "gaia-test-sender"}),
        ("POST", "/start", {"profile": "gaia-test-sender"}, {}),
    ]


def test_delete_openclaw_profile_stops_and_deletes_profile(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        calls.append((method, path, dict(params or {})))
        if path == "/stop":
            return 200, {"ok": True}, ""
        if path == "/profiles/gaia-test-sender":
            return 200, {"ok": True, "deleted": True}, ""
        raise AssertionError(path)

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.delete_openclaw_profile(
        None,
        profile="gaia-test-sender",
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["profile"] == "gaia-test-sender"
    assert calls == [
        ("POST", "/stop", {"profile": "gaia-test-sender"}),
        ("DELETE", "/profiles/gaia-test-sender", {"profile": "gaia-test-sender"}),
    ]


def test_reset_openclaw_scenario_state_clears_storage_and_closes_reset_tab(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(runtime, "_cleanup_about_blank_tabs", lambda **kwargs: None)
    runtime._clear_session_target("bench-s1:reset")
    calls: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        del base_url, timeout
        calls.append((method, path, dict(params or {}), dict(payload or {})))
        if path == "/tabs":
            return 200, {
                "tabs": [
                    {"targetId": "tab-stale", "url": "https://old.example.test/"},
                    {"targetId": "tab-stale-2", "url": "https://ko.wikipedia.org/wiki/K-pop"},
                ]
            }, ""
        if path in {"/tabs/tab-stale", "/tabs/tab-stale-2"}:
            return 200, {"ok": True}, ""
        if path == "/tabs/open":
            return 200, {"targetId": "tab-reset", "url": payload["url"]}, ""
        if path in {"/cookies/clear", "/storage/local/clear", "/storage/session/clear"}:
            return 200, {"ok": True, "targetId": payload["targetId"]}, ""
        if path == "/act":
            assert payload["kind"] == "evaluate"
            assert "indexedDB" in payload["fn"]
            assert "serviceWorker" in payload["fn"]
            return 200, {"ok": True, "targetId": payload["targetId"], "result": {}}, ""
        if path == "/tabs/tab-reset":
            return 200, {"ok": True}, ""
        raise AssertionError(path)

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.reset_openclaw_scenario_state(
        None,
        session_id="bench-s1:reset",
        url="https://shop.example.test/product/1",
        profile="openclaw",
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["targetId"] == "tab-reset"
    assert payload["closed_stale_tab_count"] == 2
    assert [call[1] for call in calls] == [
        "/tabs",
        "/tabs/tab-stale",
        "/tabs/tab-stale-2",
        "/tabs/open",
        "/cookies/clear",
        "/storage/local/clear",
        "/storage/session/clear",
        "/act",
        "/tabs/tab-reset",
    ]
    assert calls[3][3] == {"url": "https://shop.example.test/product/1", "profile": "openclaw"}
    assert calls[4][3] == {"targetId": "tab-reset", "profile": "openclaw"}
    assert calls[7][3]["targetId"] == "tab-reset"


def test_dispatch_openclaw_goto_uses_session_profile(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    runtime._clear_session_target("profile-s1")
    calls: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        calls.append((method, path, dict(params or {}), dict(payload or {})))
        if path == "/tabs/open":
            return 200, {"targetId": "tab-1", "url": payload["url"]}, ""
        raise AssertionError(path)

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={
            "session_id": "profile-s1",
            "profile": "gaia-test-sender",
            "action": "goto",
            "url": "https://chat.example.test",
        },
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["profile"] == "gaia-test-sender"
    assert calls[0][3]["profile"] == "gaia-test-sender"
    assert runtime._session_state("profile-s1")["profile"] == "gaia-test-sender"


def test_session_profile_change_invalidates_cached_snapshot() -> None:
    state = _seed_session(
        "profile-cache-s1",
        snapshot_counter=1,
        last_snapshot_id="openclaw:profile-cache-s1:1",
        last_snapshot_payload={"snapshot_id": "openclaw:profile-cache-s1:1"},
        last_tabs_payload={"tabs": [{"cdp_target_id": "tab-1"}]},
        last_tabs_target_id="tab-1",
        last_tabs_profile="openclaw",
        last_tabs_observed_at=1.0,
    )

    profile = runtime._session_profile("profile-cache-s1", "gaia-test-sender")

    assert profile == "gaia-test-sender"
    assert state["target_id"] == ""
    assert state["current_url"] == ""
    assert state["last_snapshot_id"] == ""
    assert state["last_snapshot_payload"] == {}
    assert state["last_tabs_payload"] == {}
    assert state["last_tabs_target_id"] == ""
    assert state["last_tabs_profile"] == ""
    assert state["last_tabs_observed_at"] == 0.0


def test_ensure_target_adopts_existing_non_blank_tab_and_cleans_blank_tabs(monkeypatch) -> None:
    session_id = "adopt-existing-tab-s1"
    state = _seed_session(session_id, target_id="", current_url="")
    calls: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        calls.append((method, path, dict(params or {}), dict(payload or {})))
        if method == "GET" and path == "/tabs":
            return (
                200,
                {
                    "running": True,
                    "tabs": [
                        {"targetId": "blank-tab", "url": "about:blank"},
                        {"targetId": "tab-1", "url": _DEFAULT_URL, "active": True},
                    ],
                },
                "",
            )
        if method == "DELETE" and path == "/tabs/blank-tab":
            return 200, {"ok": True}, ""
        raise AssertionError((method, path, params, payload))

    monkeypatch.setattr(runtime, "_request", fake_request)

    result = runtime._ensure_target(
        base_url="http://127.0.0.1:18791",
        session_id=session_id,
        requested_url="",
        timeout=None,
    )

    assert result["target_id"] == "tab-1"
    assert result["current_url"] == _DEFAULT_URL
    assert not any(path == "/tabs/open" for _method, path, _params, _payload in calls)
    assert ("DELETE", "/tabs/blank-tab", {"profile": "openclaw"}, {}) in calls
    assert state["last_tabs_payload"] == {}


def test_ensure_target_opens_requested_url_without_about_blank_and_cleans_blank_tabs(monkeypatch) -> None:
    session_id = "open-real-url-s1"
    state = _seed_session(session_id, target_id="", current_url="")
    calls: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        calls.append((method, path, dict(params or {}), dict(payload or {})))
        if method == "POST" and path == "/tabs/open":
            assert payload == {"url": _DEFAULT_URL, "profile": "openclaw"}
            return 200, {"targetId": "tab-1", "url": _DEFAULT_URL}, ""
        if method == "GET" and path == "/tabs":
            return (
                200,
                {
                    "running": True,
                    "tabs": [
                        {"targetId": "blank-tab", "url": "about:blank"},
                        {"targetId": "tab-1", "url": _DEFAULT_URL, "active": True},
                    ],
                },
                "",
            )
        if method == "DELETE" and path == "/tabs/blank-tab":
            return 200, {"ok": True}, ""
        raise AssertionError((method, path, params, payload))

    monkeypatch.setattr(runtime, "_request", fake_request)

    result = runtime._ensure_target(
        base_url="http://127.0.0.1:18791",
        session_id=session_id,
        requested_url=_DEFAULT_URL,
        timeout=None,
    )

    assert result["target_id"] == "tab-1"
    assert result["current_url"] == _DEFAULT_URL
    open_payloads = [payload for _method, path, _params, payload in calls if path == "/tabs/open"]
    assert open_payloads == [{"url": _DEFAULT_URL, "profile": "openclaw"}]
    assert all(payload.get("url") != "about:blank" for payload in open_payloads)
    assert ("DELETE", "/tabs/blank-tab", {"profile": "openclaw"}, {}) in calls
    assert state["last_tabs_payload"] == {}


def test_ensure_target_does_not_open_about_blank_when_url_is_missing(monkeypatch) -> None:
    session_id = "missing-url-s1"
    state = _seed_session(session_id, target_id="", current_url="")
    calls: list[tuple[str, str, dict[str, object], dict[str, object]]] = []

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        calls.append((method, path, dict(params or {}), dict(payload or {})))
        if method == "GET" and path == "/tabs":
            return (
                200,
                {"running": True, "tabs": [{"targetId": "blank-tab", "url": "about:blank"}]},
                "",
            )
        raise AssertionError((method, path, params, payload))

    monkeypatch.setattr(runtime, "_request", fake_request)

    result = runtime._ensure_target(
        base_url="http://127.0.0.1:18791",
        session_id=session_id,
        requested_url="",
        timeout=None,
    )

    assert result["target_id"] == ""
    assert result["current_url"] == ""
    assert not any(path == "/tabs/open" for _method, path, _params, _payload in calls)
    assert state["last_tabs_payload"] == {}


def test_derive_state_change_from_snapshot_payloads_surfaces_new_page_evidence() -> None:
    before_payload = {
        "current_url": "https://cyber.inu.ac.kr/mod/page/view.php?id=123",
        "evidence": _evidence("same", live_texts=["동영상 보기"], logout_visible=True),
    }
    after_payload = {
        "current_url": "https://cyber.inu.ac.kr/mod/page/view.php?id=123",
        "evidence": _evidence("same", live_texts=["동영상 보기"], logout_visible=True),
    }

    state_change = runtime._derive_state_change_from_snapshot_payloads(
        before_payload=before_payload,
        after_payload=after_payload,
        new_page_evidence={
            "new_page_detected": True,
            "new_page_count": 1,
            "new_page_same_origin_detected": True,
            "new_page_same_origin_count": 1,
            "new_page_urls": ["https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868"],
            "new_page_titles": ["대중_6주차_1차시"],
            "new_page_kinds": ["viewer_like"],
        },
    )

    assert state_change["new_page_detected"] is True
    assert state_change["new_page_count"] == 1
    assert state_change["new_page_same_origin_detected"] is True
    assert state_change["new_page_urls"] == ["https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868"]
    assert state_change["backend_progress"] is True
    assert state_change["backend_effective_only"] is False


def test_apply_commit_verification_flags_unreflected_date_apply() -> None:
    before_evidence = _evidence(
        "날짜, 인원 선택 2026.06 06.02(화) • 1박 적용하기",
        live_texts=["날짜, 인원 선택", "2026.06", "06.02(화) • 1박", "적용하기"],
    )
    before_evidence.update({"modal_open": True, "modal_count": 1, "dialog_count": 1})
    before_payload = {
        "evidence": before_evidence,
        "elements_by_ref": {"eApply": {"role": "button", "name": "적용하기"}},
        "role_snapshot": {"snapshot": "- button \"적용하기\" [ref=eApply]"},
    }
    after_payload = {
        "evidence": _evidence(
            "송도/소래포구 05.30~05.31 · 2명 정렬",
            live_texts=["송도/소래포구", "05.30~05.31 · 2명", "정렬"],
        ),
        "role_snapshot": {"snapshot": "- button \"05.30~05.31 · 2명\" [ref=e29]"},
    }

    state_change = runtime._apply_commit_verification_to_state_change(
        state_change={"backend": "openclaw", "backend_progress": True, "effective": True},
        before_payload=before_payload,
        after_payload=after_payload,
        ref_id="eApply",
    )

    assert state_change["commit_verification_failed"] is True
    assert state_change["backend_progress"] is False
    assert state_change["backend_effective_only"] is True
    assert state_change["commit_verification"]["expected_range"] == "06.02~06.03"
    assert state_change["commit_verification"]["observed_ranges"] == ["05.30~05.31"]


def test_apply_commit_verification_accepts_reflected_date_apply() -> None:
    before_evidence = _evidence(
        "날짜, 인원 선택 2026.06 06.02(화) • 1박 적용하기",
        live_texts=["날짜, 인원 선택", "2026.06", "06.02(화) • 1박", "적용하기"],
    )
    before_evidence.update({"modal_open": True, "modal_count": 1, "dialog_count": 1})
    before_payload = {
        "evidence": before_evidence,
        "elements_by_ref": {"eApply": {"role": "button", "name": "적용하기"}},
    }
    after_payload = {
        "evidence": _evidence(
            "송도/소래포구 06.02~06.03 · 2명 정렬",
            live_texts=["송도/소래포구", "06.02~06.03 · 2명", "정렬"],
        ),
        "role_snapshot": {"snapshot": "- button \"06.02~06.03 · 2명\" [ref=e29]"},
    }

    state_change = runtime._apply_commit_verification_to_state_change(
        state_change={"backend": "openclaw", "backend_progress": True, "effective": True},
        before_payload=before_payload,
        after_payload=after_payload,
        ref_id="eApply",
    )

    assert state_change["commit_verified"] is True
    assert state_change["commit_verification_failed"] is False
    assert state_change["backend_progress"] is True


def test_dispatch_openclaw_action_reuses_matching_snapshot_as_before_probe(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    session_id = "cache-s1"
    current_url = _DEFAULT_URL
    state = _seed_session(session_id, current_url=current_url)
    cached_before = _build_cached_snapshot(session_id=session_id, state=state, current_url=current_url)
    after_payload = {
        "snapshot_id": "openclaw:cache-s1:2",
        "current_url": current_url,
        "url": current_url,
        "evidence": _evidence("검색 완료"),
    }
    snapshot_calls: list[dict[str, object]] = []

    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)

    def fake_snapshot_payload_for_target(**kwargs):
        snapshot_calls.append(dict(kwargs))
        return after_payload

    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", fake_snapshot_payload_for_target)

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        assert method == "POST"
        assert path == "/act"
        assert payload["targetId"] == "tab-1"
        assert payload["kind"] == "fill"
        assert payload["fields"][0]["ref"] == "e1"
        return 200, {"ok": True, "url": current_url, "targetId": "tab-1"}, ""

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={
            "session_id": session_id,
            "snapshot_id": cached_before["snapshot_id"],
            "action": "fill",
            "ref_id": "e1",
            "value": "capston",
        },
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert len(snapshot_calls) == 1
    assert payload["backend_trace"]["snapshot_before_cache_hit"] is True
    assert payload["backend_trace"]["snapshot_before_ms"] == 0
    assert payload["state_change"]["snapshot_id_before"] == cached_before["snapshot_id"]


def test_dispatch_openclaw_action_preserves_evaluate_result_in_state_change(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    session_id = "inspect-s1"
    current_url = _DEFAULT_URL
    state = _seed_session(session_id, current_url=current_url)
    cached_before = _build_cached_snapshot(session_id=session_id, state=state, current_url=current_url)
    after_payload = {
        "snapshot_id": "openclaw:inspect-s1:2",
        "current_url": current_url,
        "url": current_url,
        "evidence": _evidence("inspect done"),
    }
    inspection = {
        "activeElement": {"tag": "input", "role": "combobox", "value": "hello"},
        "fields": [{"tag": "input", "value": "hello"}],
    }

    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)
    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", lambda **kwargs: after_payload)

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        assert method == "POST"
        assert path == "/act"
        assert payload["kind"] == "evaluate"
        return 200, {"ok": True, "result": inspection, "url": current_url, "targetId": "tab-1"}, ""

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={
            "session_id": session_id,
            "snapshot_id": cached_before["snapshot_id"],
            "action": "evaluate",
            "fn": "() => ({activeElement: {tag: 'input'}})",
        },
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["state_change"]["evaluate_result"] == inspection


def test_dispatch_openclaw_action_does_not_reuse_scoped_snapshot_as_before_probe(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    session_id = "scoped-cache-s1"
    current_url = _DEFAULT_URL
    state = _seed_session(
        session_id,
        current_url=current_url,
        snapshot_counter=1,
        last_snapshot_id="openclaw:scoped-cache-s1:1",
        last_snapshot_payload={
            "snapshot_id": "openclaw:scoped-cache-s1:1",
            "targetId": "tab-1",
            "scope_applied": True,
            "current_url": current_url,
            "evidence": {"text_digest": "scoped", "live_texts": ["scoped"]},
        },
    )
    before_payload = {
        "snapshot_id": "openclaw:scoped-cache-s1:2",
        "current_url": current_url,
        "url": current_url,
        "evidence": _evidence("full before"),
    }
    after_payload = {
        **before_payload,
        "snapshot_id": "openclaw:scoped-cache-s1:3",
        "evidence": _evidence("full after"),
    }
    snapshots = [before_payload, after_payload]

    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)
    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", lambda **kwargs: snapshots.pop(0))
    monkeypatch.setattr(
        runtime,
        "_request",
        lambda method, *, base_url, path, timeout=None, params=None, payload=None: (
            200,
            {"ok": True, "url": current_url, "targetId": "tab-1"},
            "",
        ),
    )

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={
            "session_id": session_id,
            "snapshot_id": "openclaw:scoped-cache-s1:1",
            "action": "fill",
            "ref_id": "e1",
            "value": "capston",
        },
    )

    assert status_code == 200
    assert text == ""
    assert payload["backend_trace"]["snapshot_before_cache_hit"] is False
    assert payload["state_change"]["snapshot_id_before"] == "openclaw:scoped-cache-s1:2"
    assert snapshots == []


def test_cached_tabs_payload_expires(monkeypatch) -> None:
    state: dict[str, object] = {}
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 10.0)
    runtime._remember_tabs_payload(
        state=state,
        target_id="tab-1",
        profile="openclaw",
        payload={"tabs": [{"cdp_target_id": "tab-1"}]},
    )

    monkeypatch.setattr(runtime.time, "monotonic", lambda: 11.0)
    assert runtime._cached_tabs_payload(state=state, target_id="tab-1", profile="openclaw") == {
        "tabs": [{"cdp_target_id": "tab-1"}]
    }

    monkeypatch.setattr(runtime.time, "monotonic", lambda: 13.1)
    assert runtime._cached_tabs_payload(state=state, target_id="tab-1", profile="openclaw") is None


def test_dispatch_openclaw_action_reuses_recent_tabs_baseline(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    session_id = "tabs-cache-s1"
    current_url = _DEFAULT_URL
    state = _seed_session(session_id, current_url=current_url)
    cached_before = _build_cached_snapshot(
        session_id=session_id,
        state=state,
        current_url=current_url,
        role="button",
        name="열기",
    )
    state.update(
        {
            "last_tabs_payload": {
                "current_tab_id": "1",
                "cdp_target_id": "tab-1",
                "tabs": [{"tab_id": "1", "cdp_target_id": "tab-1", "url": current_url}],
            },
            "last_tabs_target_id": "tab-1",
            "last_tabs_profile": "openclaw",
            "last_tabs_observed_at": 10.0,
        }
    )
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 10.5)
    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)
    monkeypatch.setattr(
        runtime,
        "_snapshot_payload_for_target",
        lambda **kwargs: {
            "snapshot_id": "openclaw:tabs-cache-s1:2",
            "current_url": current_url,
            "url": current_url,
            "evidence": _evidence("열림"),
        },
    )
    tabs_calls: list[dict[str, object]] = []

    def fake_tabs_payload_for_target(**kwargs):
        tabs_calls.append(dict(kwargs))
        return {
            "current_tab_id": "1",
            "cdp_target_id": "tab-1",
            "tabs": [{"tab_id": "1", "cdp_target_id": "tab-1", "url": current_url}],
        }

    monkeypatch.setattr(runtime, "_tabs_payload_for_target", fake_tabs_payload_for_target)
    monkeypatch.setattr(
        runtime,
        "_request",
        lambda method, *, base_url, path, timeout=None, params=None, payload=None: (
            200,
            {"ok": True, "url": current_url, "targetId": "tab-1"},
            "",
        ),
    )

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={
            "session_id": session_id,
            "snapshot_id": cached_before["snapshot_id"],
            "action": "click",
            "ref_id": "e1",
        },
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["backend_trace"]["tabs_before_cache_hit"] is True
    assert len(tabs_calls) == 1


def test_choose_auto_follow_tab_prefers_viewer_and_ignores_ads() -> None:
    chosen = choose_auto_follow_tab(
        {
            "new_pages": [
                {
                    "target_id": "ad-tab",
                    "url": "https://ads.example/promo",
                    "kind_guess": "ad_like",
                    "same_origin": False,
                },
                {
                    "target_id": "viewer-tab",
                    "tab_id": "2",
                    "url": "https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868",
                    "title": "viewer",
                    "kind_guess": "viewer_like",
                    "same_origin": True,
                },
            ]
        }
    )

    assert chosen is not None
    assert chosen["target_id"] == "viewer-tab"


def test_choose_auto_follow_tab_ignores_worker_surfaces() -> None:
    evidence = {
        "new_pages": [
            {
                "target_id": "pow-worker",
                "url": "https://www.daangn.com/kr/pow.worker.js",
                "title": "pow.worker",
                "kind_guess": "unknown",
                "same_origin": True,
                "active": True,
            },
            {
                "target_id": "worker-target",
                "url": "https://www.daangn.com/kr/",
                "title": "",
                "target_type": "service_worker",
                "kind_guess": "unknown",
                "same_origin": True,
            },
        ]
    }

    assert choose_auto_follow_tab(evidence) is None
    assert looks_like_non_document_surface(evidence["new_pages"][0]) is True
    assert looks_like_non_document_surface(evidence["new_pages"][1]) is True


def test_choose_auto_follow_tab_keeps_same_origin_document_page() -> None:
    chosen = choose_auto_follow_tab(
        {
            "new_pages": [
                {
                    "target_id": "result-tab",
                    "url": "https://www.daangn.com/kr/buy-sell/s/?search=%EC%95%84%EC%9D%B4%ED%8F%B015",
                    "title": "아이폰15 검색 결과",
                    "kind_guess": "unknown",
                    "same_origin": True,
                    "active": True,
                }
            ]
        }
    )

    assert chosen is not None
    assert chosen["target_id"] == "result-tab"


def test_dispatch_openclaw_action_auto_follows_same_origin_viewer_new_tab(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_OPENCLAW_AUTO_FOLLOW_NEW_TABS", "1")
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    runtime._clear_session_target("auto-follow-s1")

    main_url = "https://cyber.inu.ac.kr/mod/vod/view.php?id=1346868"
    viewer_url = "https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868"

    def fake_ensure_target(*, base_url, session_id, requested_url, timeout):
        state = runtime._session_state(session_id)
        state["target_id"] = "tab-1"
        state["current_url"] = main_url
        return state

    snapshots = [
        {
            "current_url": main_url,
            "url": main_url,
            "evidence": {
                "text_digest": "same",
                "live_texts": ["동영상 보기"],
                "list_count": 1,
                "interactive_count": 1,
                "modal_count": 0,
                "backdrop_count": 0,
                "dialog_count": 0,
                "modal_open": False,
                "auth_prompt_visible": False,
                "login_visible": False,
                "logout_visible": True,
            },
        },
        {
            "current_url": main_url,
            "url": main_url,
            "evidence": {
                "text_digest": "same",
                "live_texts": ["동영상 보기"],
                "list_count": 1,
                "interactive_count": 1,
                "modal_count": 0,
                "backdrop_count": 0,
                "dialog_count": 0,
                "modal_open": False,
                "auth_prompt_visible": False,
                "login_visible": False,
                "logout_visible": True,
            },
        },
    ]

    tab_payloads = [
        {
            "current_tab_id": "1",
            "cdp_target_id": "tab-1",
            "tabs": [
                {
                    "tab_id": "1",
                    "cdp_target_id": "tab-1",
                    "url": main_url,
                    "title": "main",
                }
            ],
        },
        {
            "current_tab_id": "1",
            "cdp_target_id": "tab-1",
            "tabs": [
                {
                    "tab_id": "1",
                    "cdp_target_id": "tab-1",
                    "url": main_url,
                    "title": "main",
                },
                {
                    "tab_id": "2",
                    "cdp_target_id": "tab-2",
                    "url": viewer_url,
                    "title": "viewer",
                },
            ],
        },
    ]

    monkeypatch.setattr(runtime, "_ensure_target", fake_ensure_target)
    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", lambda **kwargs: snapshots.pop(0))
    monkeypatch.setattr(runtime, "_tabs_payload_for_target", lambda **kwargs: tab_payloads.pop(0))

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        assert method == "POST"
        assert path == "/act"
        assert payload["targetId"] == "tab-1"
        assert payload["kind"] == "click"
        return 200, {"ok": True, "url": main_url, "targetId": "tab-1"}, ""

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={"session_id": "auto-follow-s1", "action": "click", "ref_id": "e1"},
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["targetId"] == "tab-2"
    assert payload["current_url"] == viewer_url
    assert payload["state_change"]["auto_followed_new_page"] is True
    assert payload["state_change"]["auto_follow_reason"] == "viewer_like+same_origin"
    assert payload["backend_trace"]["auto_followed_new_page"] is True
    assert runtime._session_state("auto-follow-s1")["target_id"] == "tab-2"
    assert runtime._session_state("auto-follow-s1")["current_url"] == viewer_url


def test_dispatch_openclaw_action_browser_tabs_focus_switches_session_target(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    runtime._clear_session_target("focus-s1")

    def fake_ensure_target(*, base_url, session_id, requested_url, timeout):
        state = runtime._session_state(session_id)
        state["target_id"] = "tab-1"
        state["current_url"] = "https://cyber.inu.ac.kr/mod/vod/view.php?id=1346868"
        return state

    monkeypatch.setattr(runtime, "_ensure_target", fake_ensure_target)
    monkeypatch.setattr(
        runtime,
        "_tabs_payload_for_target",
        lambda **kwargs: {
            "current_tab_id": "1",
            "cdp_target_id": "tab-1",
            "tabs": [
                {
                    "tab_id": "1",
                    "cdp_target_id": "tab-1",
                    "url": "https://cyber.inu.ac.kr/mod/vod/view.php?id=1346868",
                    "title": "main",
                },
                {
                    "tab_id": "2",
                    "cdp_target_id": "tab-2",
                    "url": "https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868",
                    "title": "viewer",
                },
            ],
        },
    )

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_tabs_focus",
        params={"session_id": "focus-s1", "targetId": "tab-2"},
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["targetId"] == "tab-2"
    assert payload["current_tab_id"] == "2"
    assert payload["current_url"] == "https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868"
    assert runtime._session_state("focus-s1")["target_id"] == "tab-2"


def test_dispatch_openclaw_action_browser_find_prefers_interactive_ref(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    session_id = "find-s1"
    state = _seed_session(session_id)

    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)
    monkeypatch.setattr(
        runtime,
        "_snapshot_payload_for_target",
        lambda **kwargs: {
            "snapshot_id": "openclaw:find-s1:1",
            "current_url": _DEFAULT_URL,
            "url": _DEFAULT_URL,
            "elements": [
                {
                    "ref_id": "e3",
                    "tag": "div",
                    "text": "낮은 가격순",
                    "attributes": {"role": "generic", "aria-label": "낮은 가격순"},
                    "is_visible": True,
                },
                {
                    "ref_id": "e7",
                    "tag": "button",
                    "text": "낮은 가격순",
                    "attributes": {
                        "role": "option",
                        "aria-label": "낮은 가격순",
                        "gaia-actionable": "true",
                        "gaia-custom-option": "true",
                    },
                    "is_visible": True,
                },
            ],
        },
    )

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_find",
        params={"session_id": session_id, "query": "낮은 가격순", "limit": 2},
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["found"] is True
    assert payload["ref_id"] == "e7"
    assert payload["match"]["role"] == "option"
    assert [item["ref_id"] for item in payload["matches"]] == ["e7", "e3"]


def test_dispatch_openclaw_action_browser_find_returns_not_found(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    session_id = "find-miss-s1"
    state = _seed_session(session_id)

    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)
    monkeypatch.setattr(
        runtime,
        "_snapshot_payload_for_target",
        lambda **kwargs: {
            "snapshot_id": "openclaw:find-miss-s1:1",
            "current_url": _DEFAULT_URL,
            "url": _DEFAULT_URL,
            "elements": [
                {
                    "ref_id": "e1",
                    "tag": "button",
                    "text": "무신사 추천순",
                    "attributes": {"role": "button", "aria-label": "무신사 추천순", "gaia-actionable": "true"},
                    "is_visible": True,
                }
            ],
        },
    )

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_find",
        params={"session_id": session_id, "query": "낮은 가격순"},
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["found"] is False
    assert payload["reason_code"] == "not_found"
    assert payload["ref_id"] == ""
    assert payload["matches"] == []


def test_dispatch_openclaw_action_capture_screenshot_returns_base64(monkeypatch, tmp_path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"fake-image-bytes")

    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(
        runtime,
        "_ensure_target",
        lambda **kwargs: {"target_id": "tab-1", "current_url": "https://example.com/app"},
    )
    monkeypatch.setattr(runtime, "_clear_session_target", lambda session_id: None)

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        assert method == "POST"
        assert path == "/screenshot"
        assert payload["targetId"] == "tab-1"
        return (
            200,
            {"ok": True, "path": str(shot), "targetId": "tab-1", "url": "https://example.com/app"},
            "",
        )

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="capture_screenshot",
        params={"session_id": "s1"},
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["reason_code"] == "ok"
    assert payload["current_url"] == "https://example.com/app"
    assert payload["mime_type"] == "image/png"
    assert payload["saved_path"] == str(shot)
    assert payload["screenshot"] == base64.b64encode(b"fake-image-bytes").decode("utf-8")


def test_dispatch_openclaw_action_browser_wait_maps_to_wait_payload(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(
        runtime,
        "_ensure_target",
        lambda **kwargs: {"target_id": "tab-1", "current_url": "https://example.com/app"},
    )

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        assert method == "POST"
        assert path == "/act"
        assert payload["targetId"] == "tab-1"
        assert payload["kind"] == "wait"
        assert payload["text"] == "assistant response ready"
        return 200, {"ok": True, "url": "https://example.com/app", "targetId": "tab-1"}, ""

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_wait",
        params={"session_id": "s1", "text": "assistant response ready"},
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["reason_code"] == "ok"


def test_pseudo_elements_from_role_snapshot_attach_row_local_context_to_action_buttons() -> None:
    snapshot = """
- main [ref=e40]:
  - generic [ref=e44]:
    - generic [ref=e46]:
      - generic [ref=e47]:
        - paragraph [ref=e48]:
          - text: (HUSS국립부경대)과거사청산과포용의문화
          - generic [ref=e49]: (3학점)
        - generic [ref=e50]: 전심
      - generic [ref=e62]:
        - link "강의평" [ref=e63] [cursor=pointer]:
          - generic [ref=e66]: 강의평
        - button "담기" [ref=e67] [cursor=pointer]:
          - text: 담기
        - button "바로 추가" [ref=e72] [cursor=pointer]:
          - text: 바로 추가
    - generic [ref=e190]:
      - generic [ref=e191]:
        - paragraph [ref=e192]:
          - text: (HUSS국립부경대)포용사회와문화탐방1
          - generic [ref=e193]: (1학점)
        - generic [ref=e194]: 전심
      - generic [ref=e205]:
        - link "강의평" [ref=e206] [cursor=pointer]:
          - generic [ref=e209]: 강의평
        - button "담기" [ref=e210] [cursor=pointer]:
          - text: 담기
        - button "바로 추가" [ref=e215] [cursor=pointer]:
          - text: 바로 추가
""".strip()
    refs = {
        "e40": {"role": "main"},
        "e44": {"role": "generic"},
        "e46": {"role": "generic"},
        "e47": {"role": "generic"},
        "e48": {"role": "paragraph"},
        "e49": {"role": "generic"},
        "e50": {"role": "generic"},
        "e62": {"role": "generic"},
        "e63": {"role": "link", "name": "강의평"},
        "e66": {"role": "generic"},
        "e67": {"role": "button", "name": "담기"},
        "e72": {"role": "button", "name": "바로 추가"},
        "e190": {"role": "generic"},
        "e191": {"role": "generic"},
        "e192": {"role": "paragraph"},
        "e193": {"role": "generic"},
        "e194": {"role": "generic"},
        "e205": {"role": "generic"},
        "e206": {"role": "link", "name": "강의평"},
        "e209": {"role": "generic"},
        "e210": {"role": "button", "name": "담기"},
        "e215": {"role": "button", "name": "바로 추가"},
    }

    elements, _ = runtime._pseudo_elements_from_role_snapshot(snapshot, refs)
    elements_by_ref = {str(item.get("ref_id") or ""): item for item in elements}

    assert "(HUSS국립부경대)과거사청산과포용의문화" in str(elements_by_ref["e72"].get("context_text") or "")
    assert "(HUSS국립부경대)포용사회와문화탐방1" in str(elements_by_ref["e215"].get("context_text") or "")


def test_pseudo_elements_from_role_snapshot_attaches_iframe_scope_to_descendants() -> None:
    snapshot = """
- generic [ref=e1]:
  - iframe [ref=e2]:
    - generic "본문 내용" [ref=f7e4]
""".strip()
    refs = {
        "e1": {"role": "generic"},
        "e2": {"role": "iframe"},
        "f7e4": {"role": "generic", "name": "본문 내용"},
    }

    elements, _ = runtime._pseudo_elements_from_role_snapshot(
        snapshot,
        refs,
        [{"selector": "iframe >> nth=4", "visible": True}],
    )
    elements_by_ref = {str(item.get("ref_id") or ""): item for item in elements}
    body = elements_by_ref["f7e4"]
    attrs = dict(body.get("attributes") or {})

    assert attrs["frame_ref_id"] == "e2"
    assert attrs["frame_selector"] == "iframe >> nth=4"
    assert attrs["frame_descendant_selector"] == '[aria-label="본문 내용"]'
    assert attrs["frame_scoped_selector"] == 'iframe >> nth=4 >> internal:control=enter-frame >> [aria-label="본문 내용"]'
    assert body["scope"]["frame_ref_id"] == "e2"


def test_build_openclaw_action_payload_supports_type_selector() -> None:
    payload = runtime._build_openclaw_action_payload(
        target_id="tab-1",
        params={
            "action": "type",
            "selector": 'iframe >> nth=4 >> internal:control=enter-frame >> [aria-label="본문 내용"]',
            "value": "hello",
        },
    )

    assert payload["kind"] == "type"
    assert payload["targetId"] == "tab-1"
    assert payload["selector"] == 'iframe >> nth=4 >> internal:control=enter-frame >> [aria-label="본문 내용"]'
    assert payload["text"] == "hello"


def test_build_snapshot_payload_includes_iframe_body_text_evidence() -> None:
    payload = runtime._build_snapshot_payload(
        session_id="s-frame",
        target_id="tab-1",
        current_url="https://mail.example.test/new",
        requested_scope_ref_id="",
        raw_snapshot={
            "snapshot": """
- generic [ref=e1]:
  - iframe [ref=e2]:
    - generic "본문 내용" [ref=f7e4]
""".strip(),
            "refs": {
                "e1": {"role": "generic"},
                "e2": {"role": "iframe"},
                "f7e4": {"role": "generic", "name": "본문 내용"},
            },
        },
        state={},
        frame_descriptors=[
            {
                "selector": "iframe >> nth=4",
                "visible": True,
                "bodyText": "GAIA iframe 본문 입력 검증",
            }
        ],
    )

    evidence = dict(payload.get("evidence") or {})
    assert evidence["frame_texts"] == ["GAIA iframe 본문 입력 검증"]
    assert "GAIA iframe 본문 입력 검증" in evidence["text_digest"]
    assert "GAIA iframe 본문 입력 검증" in evidence["live_texts"]


def test_build_snapshot_payload_preserves_raw_role_snapshot_when_scope_applied(monkeypatch) -> None:
    raw_snapshot = {
        "snapshot": '- button "원본 버튼" [ref=e1]',
        "refs": {"e1": {"role": "button", "name": "원본 버튼"}},
    }

    monkeypatch.setattr(
        runtime,
        "_pseudo_elements_from_role_snapshot",
        lambda snapshot, refs, frame_descriptors=None: (
            [
                {
                    "ref_id": "e1",
                    "tag": "button",
                    "text": "원본 버튼",
                    "attributes": {
                        "container_ref_id": "ctx-1",
                        "container_name": "검색 결과",
                        "container_role": "main",
                    },
                    "is_visible": True,
                }
            ],
            {
                "snapshot": '- button "원본 버튼" [ref=e1]',
                "refs_mode": "aria",
                "refs": {"e1": {"role": "button", "name": "원본 버튼"}},
                "tree": [{"role": "button", "name": "원본 버튼", "ref": "e1", "depth": 0}],
                "ref_line_index": {"e1": 0},
                "stats": {"lines": 1, "refs": 1, "interactive": 1},
            },
        ),
    )
    monkeypatch.setattr(runtime, "_synthesize_snapshot_evidence", lambda elements: {})
    monkeypatch.setattr(
        runtime,
        "_apply_scope_to_elements",
        lambda elements, requested_scope_ref_id: (
            list(elements),
            {"node_by_ref": {}, "nodes": []},
            True,
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_build_role_snapshot_from_elements",
        lambda elements: {
            "snapshot": '- button "스코프 버튼" [ref=e1]',
            "refs": {"e1": {"role": "button", "name": "스코프 버튼"}},
            "tree": [{"role": "button", "name": "스코프 버튼", "ref": "e1", "depth": 0}],
            "stats": {"lines": 1, "refs": 1, "interactive": 1},
        },
    )

    payload = runtime._build_snapshot_payload(
        session_id="s1",
        target_id="t1",
        current_url="https://example.com",
        requested_scope_ref_id="ctx-1",
        raw_snapshot=raw_snapshot,
        state={},
    )

    role_snapshot = payload["role_snapshot"]
    assert role_snapshot["snapshot"] == '- button "원본 버튼" [ref=e1]'
    assert role_snapshot["scoped_snapshot"] == '- button "스코프 버튼" [ref=e1]'
    assert role_snapshot["scope_applied"] is True
    assert role_snapshot["scope_container_ref_id"] == "ctx-1"


def test_pseudo_elements_from_role_snapshot_preserve_select_options_and_selected_value() -> None:
    snapshot = """
- generic [ref=e30]:
  - combobox "전체" [ref=e33]:
    - option "전체"
    - option "교양"
    - option "전심"
""".strip()
    refs = {
        "e30": {"role": "generic"},
        "e33": {"role": "combobox", "name": "전체"},
    }

    elements, _ = runtime._pseudo_elements_from_role_snapshot(snapshot, refs)
    elements_by_ref = {str(item.get("ref_id") or ""): item for item in elements}
    attrs = dict(elements_by_ref["e33"].get("attributes") or {})

    assert attrs["selected_value"] == "전체"
    assert attrs["options"] == [
        {"value": "전체", "text": "전체"},
        {"value": "교양", "text": "교양"},
        {"value": "전심", "text": "전심"},
    ]


def test_pseudo_elements_from_role_snapshot_uses_selected_option_marker_for_combobox_state() -> None:
    snapshot = """
- generic [ref=e30]:
  - combobox [ref=e35]:
    - option "전체"
    - option "1학점" [selected]
    - option "2학점"
    - option "3학점"
""".strip()
    refs = {
        "e30": {"role": "generic"},
        "e35": {"role": "combobox", "name": "전체"},
    }

    elements, _ = runtime._pseudo_elements_from_role_snapshot(snapshot, refs)
    elements_by_ref = {str(item.get("ref_id") or ""): item for item in elements}
    target = elements_by_ref["e35"]
    attrs = dict(target.get("attributes") or {})

    assert target["text"] == "1학점"
    assert attrs["selected_value"] == "1학점"
    assert attrs["role_ref_name"] == "1학점"


def test_pseudo_elements_from_role_snapshot_promotes_custom_dropdown_items() -> None:
    snapshot = """
- main [ref=e1]:
  - generic [ref=e2]:
    - generic [ref=e3]:
      - text: 무신사 추천순
    - generic [ref=e4]:
      - generic [ref=e5]:
        - text: 무신사 추천순
      - generic [ref=e6]:
        - text: 신상품(재입고)순
      - generic [ref=e7]:
        - text: 낮은 가격순
      - generic [ref=e8]:
        - text: 높은 가격순
      - generic [ref=e9]:
        - text: 할인율순
      - generic [ref=e10]:
        - text: 후기순
""".strip()
    refs = {
        "e1": {"role": "main"},
        "e2": {"role": "generic"},
        "e3": {"role": "generic"},
        "e4": {"role": "generic"},
        "e5": {"role": "generic"},
        "e6": {"role": "generic"},
        "e7": {"role": "generic"},
        "e8": {"role": "generic"},
        "e9": {"role": "generic"},
        "e10": {"role": "generic"},
    }

    elements, _ = runtime._pseudo_elements_from_role_snapshot(snapshot, refs)
    elements_by_ref = {str(item.get("ref_id") or ""): item for item in elements}
    low_price = elements_by_ref["e7"]
    attrs = dict(low_price.get("attributes") or {})

    assert low_price["tag"] == "button"
    assert low_price["element_type"] == "button"
    assert low_price["text"] == "낮은 가격순"
    assert attrs["role"] == "option"
    assert attrs["gaia-custom-option"] == "true"
    assert attrs["gaia-actionable"] == "true"
    assert attrs["openclaw_source_role"] == "generic"


def test_pseudo_elements_from_role_snapshot_does_not_promote_dangerous_custom_dropdown_labels() -> None:
    snapshot = """
- main [ref=e1]:
  - generic [ref=e2]:
    - generic [ref=e3]:
      - text: 로그인
    - generic [ref=e4]:
      - text: 삭제
    - generic [ref=e5]:
      - text: 장바구니
    - generic [ref=e6]:
      - text: 낮은 가격순
""".strip()
    refs = {
        "e1": {"role": "main"},
        "e2": {"role": "generic"},
        "e3": {"role": "generic"},
        "e4": {"role": "generic"},
        "e5": {"role": "generic"},
        "e6": {"role": "generic"},
    }

    elements, _ = runtime._pseudo_elements_from_role_snapshot(snapshot, refs)
    elements_by_ref = {str(item.get("ref_id") or ""): item for item in elements}

    for ref_id in ("e3", "e4", "e5", "e6"):
        attrs = dict(elements_by_ref[ref_id].get("attributes") or {})
        assert attrs.get("gaia-custom-option") is None


def test_collect_act_failure_diagnostics_flags_present_ref(monkeypatch):
    state = _seed_session("act-diag-present", target_id="tab-1")

    def fake_snapshot(**kwargs):
        return {
            "snapshot_id": "openclaw:act-diag-present:7",
            "targetId": "tab-1",
            "elements_by_ref": {
                "e133": {"role": "link", "name": "스카이뷰", "interactive": True},
            },
        }

    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", fake_snapshot)
    monkeypatch.setattr(
        runtime,
        "_probe_ref_actionability",
        lambda **kwargs: {
            "status": "covered",
            "actionable": False,
            "reason": "center_hits_other_element",
            "hit": {"tag": "div", "id": "dimmedLayer"},
            "target": {"tag": "a", "className": "skyview"},
        },
    )

    diagnostics = runtime._collect_act_failure_diagnostics(
        base_url="http://127.0.0.1:18791",
        session_id="act-diag-present",
        state=state,
        target_id="tab-1",
        ref_used="e133",
        timeout=None,
    )

    assert diagnostics["ref_present_in_fresh_snapshot"] is True
    assert diagnostics["target_changed"] is False
    assert diagnostics["fresh_ref_role"] == "link"
    assert diagnostics["fresh_ref_name"] == "스카이뷰"
    assert diagnostics["fresh_snapshot_id"] == "openclaw:act-diag-present:7"
    assert diagnostics["ref_actionability"]["status"] == "covered"
    assert diagnostics["ref_actionability"]["hit"] == "div#dimmedLayer"


def test_collect_act_failure_diagnostics_flags_absent_ref_and_target_drift(monkeypatch):
    state = _seed_session("act-diag-absent", target_id="tab-1")

    def fake_snapshot(**kwargs):
        return {
            "snapshot_id": "openclaw:act-diag-absent:2",
            "targetId": "tab-9",
            "elements_by_ref": {"e7": {"role": "button", "name": "검색"}},
        }

    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", fake_snapshot)
    monkeypatch.setattr(runtime, "_probe_ref_actionability", lambda **kwargs: None)

    diagnostics = runtime._collect_act_failure_diagnostics(
        base_url="http://127.0.0.1:18791",
        session_id="act-diag-absent",
        state=state,
        target_id="tab-1",
        ref_used="e133",
        timeout=None,
    )

    assert diagnostics["ref_present_in_fresh_snapshot"] is False
    assert diagnostics["target_changed"] is True
    assert "fresh_ref_role" not in diagnostics


def test_dispatch_openclaw_action_reveals_present_hidden_ref_then_retries_click(monkeypatch):
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime, "_probe_ref_actionability", lambda **kwargs: None)
    session_id = "act-reveal-retry"
    current_url = _DEFAULT_URL
    state = _seed_session(session_id, current_url=current_url)
    cached_before = _build_cached_snapshot(
        session_id=session_id,
        state=state,
        current_url=current_url,
        role="button",
        name="2",
        ref_id="e9",
    )
    fresh_snapshot = {
        "snapshot_id": "openclaw:act-reveal-retry:2",
        "targetId": "tab-1",
        "current_url": current_url,
        "url": current_url,
        "elements_by_ref": {
            "e9": {"role": "button", "name": "2", "interactive": True},
        },
        "evidence": _evidence("달력 6월"),
    }
    after_payload = {
        "snapshot_id": "openclaw:act-reveal-retry:3",
        "targetId": "tab-1",
        "current_url": current_url,
        "url": current_url,
        "elements_by_ref": {
            "e9": {"role": "button", "name": "2", "interactive": True},
        },
        "evidence": _evidence("날짜 6/2 선택됨"),
    }
    snapshots = [fresh_snapshot, after_payload]
    requests_seen: list[dict[str, object]] = []

    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)
    monkeypatch.setattr(runtime, "_tabs_payload_for_target", lambda **kwargs: {"tabs": []})
    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", lambda **kwargs: snapshots.pop(0))

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        assert method == "POST"
        assert path == "/act"
        payload = dict(payload or {})
        requests_seen.append(payload)
        if len(requests_seen) == 1:
            assert payload["kind"] == "click"
            return 500, {"error": 'Element "e9" not found or not visible'}, ""
        if len(requests_seen) == 2:
            assert payload["kind"] == "evaluate"
            assert payload["ref"] == "e9"
            return 200, {
                "ok": True,
                "targetId": "tab-1",
                "url": current_url,
                "result": {
                    "viewportVisibleBefore": False,
                    "viewportVisibleAfter": True,
                    "scrollChanged": True,
                    "beforeRect": {"top": 900, "height": 24},
                    "afterRect": {"top": 320, "height": 24},
                    "scrollChanges": [{"tag": "div", "beforeTop": 0, "afterTop": 580}],
                },
            }, ""
        assert payload["kind"] == "click"
        assert payload["timeoutMs"] == 6000
        return 200, {"ok": True, "url": current_url, "targetId": "tab-1"}, ""

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={
            "session_id": session_id,
            "snapshot_id": cached_before["snapshot_id"],
            "action": "click",
            "ref_id": "e9",
        },
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["backend_trace"]["visibility_recovery_count"] == 1
    assert payload["state_change"]["recovered_after_ref_visibility_reveal"] is True
    assert payload["state_change"]["action_recovery"]["attempts"][0]["kind"] == "ref_visibility_reveal"
    assert payload["attempt_count"] == 2
    assert requests_seen[1]["kind"] == "evaluate"
    assert snapshots == []


def test_dispatch_openclaw_action_recovers_pointer_interceptor_overlay_then_retries_click(monkeypatch):
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime, "_probe_ref_actionability", lambda **kwargs: None)
    session_id = "act-pointer-overlay-retry"
    current_url = _DEFAULT_URL
    state = _seed_session(session_id, current_url=current_url)
    cached_before = _build_cached_snapshot(
        session_id=session_id,
        state=state,
        current_url=current_url,
        role="button",
        name="스카이뷰",
        ref_id="e152",
    )
    fresh_snapshot = {
        "snapshot_id": "openclaw:act-pointer-overlay-retry:2",
        "targetId": "tab-1",
        "current_url": current_url,
        "url": current_url,
        "elements_by_ref": {
            "e152": {"role": "button", "name": "스카이뷰", "interactive": True},
        },
        "evidence": _evidence("지도 스카이뷰"),
    }
    after_payload = {
        "snapshot_id": "openclaw:act-pointer-overlay-retry:3",
        "targetId": "tab-1",
        "current_url": current_url,
        "url": current_url,
        "elements_by_ref": {
            "e152": {"role": "button", "name": "스카이뷰", "interactive": True},
        },
        "evidence": _evidence("스카이뷰 항공사진"),
    }
    snapshots = [fresh_snapshot, after_payload]
    requests_seen: list[dict[str, object]] = []

    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)
    monkeypatch.setattr(runtime, "_tabs_payload_for_target", lambda **kwargs: {"tabs": []})
    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", lambda **kwargs: snapshots.pop(0))

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        assert method == "POST"
        assert path == "/act"
        payload = dict(payload or {})
        requests_seen.append(payload)
        if len(requests_seen) == 1:
            assert payload["kind"] == "click"
            return 500, {
                "error": (
                    'Error: Element "e152" is not interactable (covered by another element). '
                    '<div id="dimmedLayer" class="DimmedLayer"></div> intercepts pointer events'
                )
            }, ""
        if len(requests_seen) == 2:
            assert payload["kind"] == "evaluate"
            assert payload["ref"] == "e152"
            return 200, {
                "ok": True,
                "targetId": "tab-1",
                "url": current_url,
                "result": {
                    "recovered": True,
                    "action": "pointer_events_none",
                    "bypassed": True,
                    "targetClickableBefore": False,
                    "targetClickableAfter": True,
                    "beforeBlocker": {"tag": "div", "id": "dimmedLayer", "className": "DimmedLayer"},
                    "afterBlocker": {},
                },
            }, ""
        assert payload["kind"] == "click"
        assert payload["timeoutMs"] == 6000
        return 200, {"ok": True, "url": current_url, "targetId": "tab-1"}, ""

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={
            "session_id": session_id,
            "snapshot_id": cached_before["snapshot_id"],
            "action": "click",
            "ref_id": "e152",
        },
    )

    assert status_code == 200
    assert text == ""
    assert payload["success"] is True
    assert payload["backend_trace"]["pointer_interceptor_recovery_count"] == 1
    assert payload["state_change"]["recovered_after_pointer_interceptor_recovery"] is True
    assert payload["state_change"]["action_recovery"]["attempts"][0]["kind"] == "pointer_interceptor_overlay_recovery"
    assert payload["state_change"]["action_recovery"]["attempts"][1]["kind"] == "pointer_interceptor_overlay_retry"
    assert payload["attempt_count"] == 2
    assert requests_seen[1]["kind"] == "evaluate"
    assert snapshots == []


def test_dispatch_openclaw_action_relabels_present_ref_reveal_failure_as_not_actionable(monkeypatch):
    monkeypatch.setattr(runtime, "_resolve_base_url", lambda raw: "http://127.0.0.1:18791")
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime, "_probe_ref_actionability", lambda **kwargs: None)
    session_id = "act-reveal-fails"
    current_url = _DEFAULT_URL
    state = _seed_session(session_id, current_url=current_url)
    cached_before = _build_cached_snapshot(
        session_id=session_id,
        state=state,
        current_url=current_url,
        role="button",
        name="2",
        ref_id="e9",
    )
    fresh_snapshot = {
        "snapshot_id": "openclaw:act-reveal-fails:2",
        "targetId": "tab-1",
        "current_url": current_url,
        "url": current_url,
        "elements_by_ref": {
            "e9": {"role": "button", "name": "2", "interactive": True},
        },
        "evidence": _evidence("달력 6월"),
    }

    monkeypatch.setattr(runtime, "_ensure_target", lambda **kwargs: state)
    monkeypatch.setattr(runtime, "_tabs_payload_for_target", lambda **kwargs: {"tabs": []})
    monkeypatch.setattr(runtime, "_snapshot_payload_for_target", lambda **kwargs: fresh_snapshot)

    def fake_request(method, *, base_url, path, timeout=None, params=None, payload=None):
        assert method == "POST"
        assert path == "/act"
        if (payload or {}).get("kind") == "evaluate":
            return 500, {"error": "evaluate failed while revealing ref"}, ""
        return 500, {"error": 'Element "e9" not found or not visible'}, ""

    monkeypatch.setattr(runtime, "_request", fake_request)

    status_code, payload, text = runtime.dispatch_openclaw_action(
        None,
        action="browser_act",
        params={
            "session_id": session_id,
            "snapshot_id": cached_before["snapshot_id"],
            "action": "click",
            "ref_id": "e9",
        },
    )

    assert status_code == 200
    assert payload["success"] is False
    assert payload["reason_code"] == "not_actionable"
    assert payload["state_change"]["ref_present_but_act_failed"] is True
    assert payload["state_change"]["action_recovery"]["recovered"] is False
    assert payload["attempt_logs"][-1]["kind"] == "ref_visibility_reveal"
    assert text == 'Element "e9" not found or not visible'


def test_runtime_bundle_classifies_pointer_intercept_before_visibility_timeout():
    # Root-cause guard: the embedded OpenClaw bundle must detect a pointer-event
    # interceptor BEFORE the generic "not found or not visible" branch, otherwise
    # overlay-covered clicks (e.g. KakaoMap #dimmedLayer) get mislabeled as stale
    # refs and the interceptor evidence is discarded.
    from pathlib import Path

    bundle = (
        Path(__file__).resolve().parents[3]
        / "vendor"
        / "openclaw-runtime"
        / "gaia-embedded-browser-server.bundle.mjs"
    ).read_text(encoding="utf-8")
    intercept_pos = bundle.find("is not interactable (covered by another element)")
    not_found_pos = bundle.find("not found or not visible. Run a new snapshot")
    assert intercept_pos != -1, "pointer-intercept branch missing from runtime bundle"
    assert not_found_pos != -1, "visibility-timeout branch missing from runtime bundle"
    assert intercept_pos < not_found_pos, "pointer-intercept must be classified before visibility-timeout"
