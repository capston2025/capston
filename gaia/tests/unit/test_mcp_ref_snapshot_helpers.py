from gaia.src.phase4.mcp_host import _build_ref_candidates, _resolve_stale_ref
from gaia.src.phase4.mcp_ref.snapshot_helpers import (
    _build_context_snapshot_from_elements,
    _build_role_snapshot_from_elements,
)


def _element(
    ref_id: str,
    dom_ref: str,
    *,
    text: str,
    role: str = "button",
    name: str | None = None,
    nth: int = 0,
    container_dom_ref: str | None = None,
    container_name: str | None = None,
    container_role: str | None = None,
):
    attrs = {
        "role_ref_role": role,
        "role_ref_name": name or text,
        "role_ref_nth": nth,
    }
    if container_dom_ref:
        attrs["container_dom_ref"] = container_dom_ref
    if container_name:
        attrs["container_name"] = container_name
    if container_role:
        attrs["container_role"] = container_role
    return {
        "ref_id": ref_id,
        "tag": "button",
        "dom_ref": dom_ref,
        "selector": f'[data-gaia-dom-ref="{dom_ref}"]',
        "text": text,
        "attributes": attrs,
    }


def test_build_ref_candidates_includes_role_ref_after_dom_ref() -> None:
    candidates = _build_ref_candidates(
        {
            "dom_ref": "gaia-button-1",
            "role_ref_role": "button",
            "role_ref_name": "담기",
            "role_ref_nth": 2,
        }
    )
    assert candidates[0][0] == "dom_ref"
    assert candidates[1][0] == "role_ref"
    assert str(candidates[1][1]).startswith("role_ref:")


def test_resolve_stale_ref_uses_role_ref_name_nth() -> None:
    fresh_snapshot = {
        "elements_by_ref": {
            "11": _element("11", "gaia-button-11", text="담기", nth=0),
            "12": _element("12", "gaia-button-12", text="담기", nth=1),
            "13": _element("13", "gaia-button-13", text="담기", nth=2),
        }
    }
    old_meta = {
        "dom_ref": "missing-old-ref",
        "role_ref_role": "button",
        "role_ref_name": "담기",
        "role_ref_nth": 2,
    }
    resolved = _resolve_stale_ref(old_meta, fresh_snapshot)
    assert resolved is not None
    assert resolved.get("ref_id") == "13"


def test_build_context_snapshot_assigns_container_ref_ids_and_children() -> None:
    elements = [
        _element(
            "21",
            "gaia-button-21",
            text="담기",
            container_dom_ref="gaia-card-1",
            container_name="자기주도학습컨설팅",
            container_role="article",
        ),
        _element(
            "22",
            "gaia-button-22",
            text="강의평",
            container_dom_ref="gaia-card-1",
            container_name="자기주도학습컨설팅",
            container_role="article",
        ),
    ]
    snapshot = _build_context_snapshot_from_elements(elements)
    assert snapshot["nodes"]
    node = snapshot["nodes"][0]
    assert node["name"] == "자기주도학습컨설팅"
    assert set(node["child_ref_ids"]) == {"21", "22"}
    assert elements[0]["attributes"]["container_ref_id"].startswith("ctx-")
    assert node["role_groups"]
    summaries = {str(group.get("summary") or "") for group in node["role_groups"]}
    assert 'button "담기" x1' in summaries
    assert 'button "강의평" x1' in summaries


def test_build_role_snapshot_from_elements_includes_ref_role_name_and_nth() -> None:
    elements = [
        _element("31", "gaia-button-31", text="담기", nth=0),
        _element("32", "gaia-button-32", text="담기", nth=1),
    ]
    payload = _build_role_snapshot_from_elements(elements)
    assert payload["refs_mode"] == "role"
    assert 'button "담기" [ref=31]' in payload["snapshot"]
    assert '[nth=1]' in payload["snapshot"]
    assert payload["refs"]["31"]["role"] == "button"
    assert payload["refs"]["32"]["nth"] == 1
