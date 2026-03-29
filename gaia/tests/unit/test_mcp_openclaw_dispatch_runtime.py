from __future__ import annotations

from gaia.src.phase4 import mcp_openclaw_dispatch_runtime as runtime


def test_resolve_base_url_uses_embedded_runtime_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("GAIA_OPENCLAW_BASE_URL", raising=False)
    monkeypatch.setattr(
        runtime,
        "ensure_embedded_openclaw_base_url",
        lambda: "http://127.0.0.1:18791",
    )

    assert runtime._resolve_base_url(None) == "http://127.0.0.1:18791"


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
