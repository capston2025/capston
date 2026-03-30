from __future__ import annotations

import base64

import requests

from gaia.src.phase4 import mcp_openclaw_dispatch_runtime as runtime


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


def test_build_snapshot_payload_preserves_raw_role_snapshot_when_scope_applied(monkeypatch) -> None:
    raw_snapshot = {
        "snapshot": '- button "원본 버튼" [ref=e1]',
        "refs": {"e1": {"role": "button", "name": "원본 버튼"}},
    }

    monkeypatch.setattr(
        runtime,
        "_pseudo_elements_from_role_snapshot",
        lambda snapshot, refs: (
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
