from __future__ import annotations

import json
from types import SimpleNamespace

from gaia.src.phase4.goal_driven.goal_kinds import GoalKind
from gaia.src.phase4.goal_driven.models import DOMElement
from gaia.src.phase4.goal_driven.wrapper_trace_runtime import (
    dump_wrapper_trace,
    serialize_dom_elements,
    thin_wrapper_enabled,
    wrapper_mode_name,
)


def test_dump_wrapper_trace_writes_json(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_WRAPPER_TRACE", "1")
    monkeypatch.setenv("GAIA_WRAPPER_TRACE_DIR", str(tmp_path))
    agent = SimpleNamespace(_action_history=["step-1"])

    path = dump_wrapper_trace(
        agent,
        kind="pre_decision",
        payload={"goal": {"name": "test"}, "prompt": "hello"},
    )

    assert path is not None
    saved = json.loads((tmp_path / getattr(agent, "_wrapper_trace_run_id") / "step-02-pre_decision-01.json").read_text(encoding="utf-8"))
    assert saved["goal"]["name"] == "test"
    assert saved["prompt"] == "hello"
    assert saved["kind"] == "pre_decision"


def test_serialize_dom_elements_includes_semantic_tags():
    agent = SimpleNamespace(
        _normalize_text=lambda value: " ".join(str(value or "").lower().split()),
        _goal_semantics=SimpleNamespace(
            goal_kind=GoalKind.ADD_TO_LIST,
            target_terms=["포용사회와문화탐방1"],
            destination_terms=["내 시간표", "시간표"],
        ),
    )
    element = DOMElement(
        id=25,
        ref_id="t0-f0-e25",
        tag="button",
        role="button",
        text="바로 추가",
        container_name="포용사회와문화탐방1",
        context_text="미배정",
        is_visible=True,
        is_enabled=True,
    )

    serialized = serialize_dom_elements([element], agent=agent)

    assert serialized[0]["semantic_tags"] == ["target_match", "source_mutation_candidate"]


def test_wrapper_mode_name_uses_explicit_thin_override(monkeypatch):
    monkeypatch.setenv("GAIA_GOAL_WRAPPER_MODE", "thin")
    agent = SimpleNamespace(_browser_backend_name="local")

    assert wrapper_mode_name(agent) == "thin"
    assert thin_wrapper_enabled(agent) is True
