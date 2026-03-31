from __future__ import annotations

import asyncio

from gaia.chat_hub import HubContext
from gaia.telegram_bridge import TelegramConfig, _TelegramBridge
from gaia.src.phase4.memory.store import MemoryStore


class _ReplyFailsMessage:
    async def reply_text(self, _text: str) -> None:
        raise TimeoutError("telegram timeout")


def test_safe_reply_text_swallows_timeout() -> None:
    bridge = _TelegramBridge(
        hub_context=HubContext(
            provider="gemini",
            model="gemini-2.5-pro",
            auth_strategy="reuse",
            url="https://example.com",
            runtime="terminal",
            control_channel="telegram",
        ),
        config=TelegramConfig(),
        memory_store=MemoryStore(enabled=False),
    )

    result = asyncio.run(bridge._safe_reply_text(_ReplyFailsMessage(), "queued #1: test"))

    assert result is False
