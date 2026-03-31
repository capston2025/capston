from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from gaia.src.phase4.mcp_local_dispatch_runtime import (
    DispatchResult,
    execute_mcp_action,
)


def execute_mcp_action_with_recovery(
    *,
    raw_base_url: str,
    action: str,
    params: Dict[str, Any],
    timeout: Any,
    attempts: int = 2,
    is_transport_error: Optional[Callable[[str], bool]] = None,
    recover_host: Optional[Callable[..., bool]] = None,
    context: str = "",
) -> DispatchResult:
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, int(attempts or 1))):
        try:
            return execute_mcp_action(
                raw_base_url,
                action=action,
                params=dict(params or {}),
                timeout=timeout,
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= (max(1, int(attempts or 1)) - 1):
                raise
            if callable(is_transport_error):
                try:
                    if is_transport_error(str(exc)):
                        continue
                except Exception:
                    pass
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("execute_mcp_action_with_recovery failed without exception")
