from __future__ import annotations

import json
from typing import List, Optional

import requests


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
        response = None
        last_exc: Optional[Exception] = None
        payload = {
            "action": "capture_screenshot",
            "params": {"session_id": agent.session_id},
        }
        for attempt in range(2):
            try:
                response = requests.post(
                    f"{agent.mcp_host_url}/execute",
                    json=payload,
                    timeout=(connect_timeout, read_timeout),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if (
                    attempt == 0
                    and agent._is_mcp_transport_error(str(exc))
                    and agent._recover_mcp_host(context="capture_screenshot")
                ):
                    continue
                raise
        if response is None and last_exc is not None:
            raise last_exc
        data = response.json()
        screenshot = data.get("screenshot")

        if screenshot and agent._screenshot_callback:
            agent._screenshot_callback(screenshot)

        return screenshot

    except Exception as e:
        agent._log(f"스크린샷 캡처 실패: {e}")
        return None


def check_console_errors(agent) -> List[str]:
    try:
        response = None
        last_exc: Optional[Exception] = None
        payload = {
            "action": "get_console_logs",
            "params": {"session_id": agent.session_id, "type": "error"},
        }
        for attempt in range(2):
            try:
                response = requests.post(
                    f"{agent.mcp_host_url}/execute",
                    json=payload,
                    timeout=(3, 10),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if (
                    attempt == 0
                    and agent._is_mcp_transport_error(str(exc))
                    and agent._recover_mcp_host(context="console_logs")
                ):
                    continue
                raise
        if response is None and last_exc is not None:
            raise last_exc
        data = response.json()
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
        response = None
        last_exc: Optional[Exception] = None
        payload = {
            "action": "get_current_url",
            "params": {"session_id": agent.session_id},
        }
        for attempt in range(2):
            try:
                response = requests.post(
                    f"{agent.mcp_host_url}/execute",
                    json=payload,
                    timeout=(3, 10),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if (
                    attempt == 0
                    and agent._is_mcp_transport_error(str(exc))
                    and agent._recover_mcp_host(context="current_url")
                ):
                    continue
                raise
        if response is None and last_exc is not None:
            raise last_exc
        data = response.json()
        return data.get("url", agent._current_url)

    except Exception:
        return agent._current_url
