from __future__ import annotations

from gaia.src.phase4.goal_driven.parsing import parse_wait_payload


def test_parse_wait_payload_maps_for_network_idle_to_load_state() -> None:
    payload = parse_wait_payload('{"for_network_idle": true}')

    assert payload == {"load_state": "networkidle"}


def test_parse_wait_payload_preserves_network_idle_with_timeout() -> None:
    payload = parse_wait_payload('{"for_network_idle": true, "timeoutMs": 9000}')

    assert payload == {
        "load_state": "networkidle",
        "timeout_ms": 9000,
    }
