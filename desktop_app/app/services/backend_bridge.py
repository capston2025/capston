"""Bridges desktop app workflows with the FastAPI service modules."""
from __future__ import annotations

import asyncio
from typing import Iterable, Sequence

try:
    # FastAPI 앱 내부 모듈을 직접 임포트해서 HTTP 왕복 없이 재사용한다.
    from server.main import (  # type: ignore
        DomElement,
        TestScenario,
        analyze_website_dom,
        call_openai_api,
        call_openai_api_with_dom,
    )
except Exception:  # pragma: no cover - 서버 코드가 없거나 수정된 경우 대비
    DomElement = object  # type: ignore[misc,assignment]
    TestScenario = object  # type: ignore[misc,assignment]
    analyze_website_dom = None  # type: ignore[assignment]
    call_openai_api = None  # type: ignore[assignment]
    call_openai_api_with_dom = None  # type: ignore[assignment]


class BackendBridge:
    """Facade that marshals desktop requests into server logic calls."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def generate_plan_from_document(self, document_text: str) -> Sequence[TestScenario]:
        if not call_openai_api:
            raise RuntimeError("FastAPI call_openai_api 함수가 임포트되지 않았습니다.")
        return self._loop.run_until_complete(call_openai_api(document_text))

    def analyze_url_and_generate_plan(
        self,
        url: str,
        document_text: str | None = None,
    ) -> Sequence[TestScenario]:
        if not analyze_website_dom or not call_openai_api_with_dom:
            raise RuntimeError("FastAPI 분석 함수가 임포트되지 않았습니다.")

        async def _run() -> Sequence[TestScenario]:
            dom_elements: Iterable[DomElement] = await analyze_website_dom(url)
            scenarios = await call_openai_api_with_dom(list(dom_elements), document_text)
            return scenarios

        return self._loop.run_until_complete(_run())

    def shutdown(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()
