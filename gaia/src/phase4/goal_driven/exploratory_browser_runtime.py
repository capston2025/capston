from __future__ import annotations

import json
from typing import List, Optional

from gaia.src.phase4.mcp_transport_retry_runtime import execute_mcp_action_with_recovery


def capture_screenshot(agent) -> Optional[str]:
    try:
        try:
            connect_timeout = int(
                getattr(agent, "_env_int", lambda *_args, **_kwargs: 3)(
                    "GAIA_SCREENSHOT_CONNECT_TIMEOUT_SEC",
                    3,
                    low=1,
                    high=30,
                )
            )
            read_timeout = int(
                getattr(agent, "_env_int", lambda *_args, **_kwargs: 8)(
                    "GAIA_SCREENSHOT_READ_TIMEOUT_SEC",
                    8,
                    low=2,
                    high=60,
                )
            )
        except Exception:
            connect_timeout = 3
            read_timeout = 8
        payload = {
            "action": "capture_screenshot",
            "params": {"session_id": agent.session_id},
        }
        response = execute_mcp_action_with_recovery(
            raw_base_url=agent.mcp_host_url,
            action=str(payload.get("action") or ""),
            params=dict(payload.get("params") or {}),
            timeout=(connect_timeout, read_timeout),
            attempts=2,
            is_transport_error=agent._is_mcp_transport_error,
            recover_host=agent._recover_mcp_host,
            context="capture_screenshot",
        )
        if hasattr(response, "json"):
            data = response.json()
        else:
            data = response.payload
        screenshot = data.get("screenshot")

        if screenshot and agent._screenshot_callback:
            agent._screenshot_callback(screenshot)

        return screenshot

    except Exception as e:
        agent._log(f"스크린샷 캡처 실패: {e}")
        return None


def check_console_errors(agent) -> List[str]:
    try:
        payload = {
            "action": "get_console_logs",
            "params": {"session_id": agent.session_id, "type": "error"},
        }
        response = execute_mcp_action_with_recovery(
            raw_base_url=agent.mcp_host_url,
            action=str(payload.get("action") or ""),
            params=dict(payload.get("params") or {}),
            timeout=(3, 10),
            attempts=2,
            is_transport_error=agent._is_mcp_transport_error,
            recover_host=agent._recover_mcp_host,
            context="console_logs",
        )
        if hasattr(response, "json"):
            data = response.json()
        else:
            data = response.payload
        logs = data.get("logs", [])
        if not isinstance(logs, list):
            return []
        normalized: List[str] = []
        for item in logs:
            if isinstance(item, str):
                normalized.append(item)
            else:
                try:
                    normalized.append(json.dumps(item, ensure_ascii=False))
                except Exception:
                    normalized.append(str(item))
        return normalized

    except Exception as e:
        agent._log(f"콘솔 로그 확인 실패: {e}")
        return []


def get_current_url(agent) -> str:
    try:
        payload = {
            "action": "get_current_url",
            "params": {"session_id": agent.session_id},
        }
        response = execute_mcp_action_with_recovery(
            raw_base_url=agent.mcp_host_url,
            action=str(payload.get("action") or ""),
            params=dict(payload.get("params") or {}),
            timeout=(3, 10),
            attempts=2,
            is_transport_error=agent._is_mcp_transport_error,
            recover_host=agent._recover_mcp_host,
            context="current_url",
        )
        if hasattr(response, "json"):
            data = response.json()
        else:
            data = response.payload
        return data.get("url", agent._current_url)

    except Exception:
        return agent._current_url
