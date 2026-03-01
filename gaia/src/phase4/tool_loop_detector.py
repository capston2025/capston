from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional


@dataclass(slots=True)
class LoopDetectionResult:
    stuck: bool
    level: str = ""
    detector: str = ""
    count: int = 0
    message: str = ""


class ToolLoopDetector:
    """OpenClaw 스타일의 no-progress / ping-pong loop guard."""

    def __init__(
        self,
        *,
        warning_threshold: int = 3,
        critical_threshold: int = 5,
        ping_pong_warning_threshold: int = 4,
        ping_pong_critical_threshold: int = 6,
        max_history: int = 120,
    ) -> None:
        self.warning_threshold = max(2, int(warning_threshold))
        self.critical_threshold = max(self.warning_threshold + 1, int(critical_threshold))
        self.ping_pong_warning_threshold = max(3, int(ping_pong_warning_threshold))
        self.ping_pong_critical_threshold = max(
            self.ping_pong_warning_threshold + 1,
            int(ping_pong_critical_threshold),
        )
        self._history: Deque[Dict[str, Any]] = deque(maxlen=max(40, int(max_history)))

    @staticmethod
    def _hash_tool_call(tool_name: str, params: Dict[str, Any]) -> str:
        normalized = json.dumps(
            {"tool": str(tool_name or "").strip().lower(), "params": params or {}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def check(self, tool_name: str, params: Dict[str, Any] | None = None) -> LoopDetectionResult:
        tool = str(tool_name or "").strip().lower()
        if not tool:
            return LoopDetectionResult(stuck=False)
        call_hash = self._hash_tool_call(tool, params or {})

        no_progress_same_call = 0
        for item in reversed(self._history):
            if bool(item.get("progress")):
                break
            if item.get("tool") == tool and item.get("hash") == call_hash:
                no_progress_same_call += 1
            else:
                break

        if no_progress_same_call >= self.critical_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="global_circuit_breaker",
                count=no_progress_same_call,
                message=(
                    f"CRITICAL: {tool} 동일 호출이 no-progress 상태로 "
                    f"{no_progress_same_call}회 반복되어 차단합니다."
                ),
            )
        if no_progress_same_call >= self.warning_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="warning",
                detector="known_poll_no_progress",
                count=no_progress_same_call,
                message=(
                    f"WARNING: {tool} 동일 호출이 no-progress 상태로 "
                    f"{no_progress_same_call}회 반복되었습니다."
                ),
            )

        ping_pong_streak = self._ping_pong_streak_with_current(call_hash)
        if ping_pong_streak >= self.ping_pong_critical_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="ping_pong",
                count=ping_pong_streak,
                message=(
                    "CRITICAL: 상호 교대(ping-pong) no-progress 루프가 감지되어 "
                    "전략 전환이 필요합니다."
                ),
            )
        if ping_pong_streak >= self.ping_pong_warning_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="warning",
                detector="ping_pong",
                count=ping_pong_streak,
                message="WARNING: 상호 교대(ping-pong) no-progress 패턴이 감지되었습니다.",
            )

        return LoopDetectionResult(stuck=False)

    def record(
        self,
        tool_name: str,
        params: Dict[str, Any] | None,
        *,
        progress: bool,
        result_hash: str = "",
    ) -> None:
        tool = str(tool_name or "").strip().lower()
        if not tool:
            return
        self._history.append(
            {
                "tool": tool,
                "hash": self._hash_tool_call(tool, params or {}),
                "progress": bool(progress),
                "result_hash": str(result_hash or ""),
            }
        )

    def _ping_pong_streak_with_current(self, current_hash: str) -> int:
        no_progress_hashes: List[str] = []
        for item in reversed(self._history):
            if bool(item.get("progress")):
                break
            h = str(item.get("hash") or "").strip()
            if h:
                no_progress_hashes.append(h)
        no_progress_hashes.reverse()
        no_progress_hashes.append(current_hash)

        if len(no_progress_hashes) < 4:
            return 0
        a = no_progress_hashes[-1]
        b = no_progress_hashes[-2]
        if not a or not b or a == b:
            return 0

        streak = 2
        expected = a
        for idx in range(len(no_progress_hashes) - 3, -1, -1):
            expected = b if expected == a else a
            if no_progress_hashes[idx] == expected:
                streak += 1
            else:
                break
        return streak

