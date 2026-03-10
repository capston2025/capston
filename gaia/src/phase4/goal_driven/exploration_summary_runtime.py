from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse


def calculate_coverage(visited_pages: Dict[str, Any], tested_elements: Any) -> Dict[str, Any]:
    """테스트 커버리지 계산"""
    total_elements = 0
    tested_count = len(tested_elements)

    for page in visited_pages.values():
        total_elements += len(page.interactive_elements)

    return {
        "total_interactive_elements": total_elements,
        "tested_elements": tested_count,
        "coverage_percentage": (tested_count / total_elements * 100) if total_elements > 0 else 0,
        "total_pages": len(visited_pages),
    }


def determine_completion_reason(
    forced_completion_reason: Optional[str],
    config: Any,
    action_count: int,
    steps: List[Any],
    duration_seconds: float = 0.0,
) -> str:
    """탐색 종료 이유 결정"""
    if forced_completion_reason:
        return forced_completion_reason
    if (
        config.loop_mode == "time"
        and int(config.time_budget_seconds or 0) > 0
        and duration_seconds >= int(config.time_budget_seconds)
    ):
        return f"시간 예산 도달 ({int(config.time_budget_seconds)}s)"
    if action_count >= config.max_actions:
        return f"최대 액션 수 도달 ({config.max_actions})"
    if steps and not steps[-1].decision.should_continue:
        return steps[-1].decision.reasoning
    return "탐색 완료"


def print_summary(log_fn: Callable[[str], None], result: Any) -> None:
    """탐색 결과 요약 출력"""
    log_fn("\n" + "=" * 60)
    log_fn("🎉 탐색 완료!")
    log_fn("=" * 60)
    log_fn(f"총 액션 수: {result.total_actions}")
    log_fn(f"방문한 페이지: {result.total_pages_visited}개")
    log_fn(f"테스트한 요소: {result.total_elements_tested}개")
    log_fn(f"커버리지: {result.get_coverage_percentage():.1f}%")
    log_fn(f"발견한 이슈: {len(result.issues_found)}개")

    if result.issues_found:
        critical = len([i for i in result.issues_found if i.severity == "critical"])
        high = len([i for i in result.issues_found if i.severity == "high"])
        medium = len([i for i in result.issues_found if i.severity == "medium"])
        low = len([i for i in result.issues_found if i.severity == "low"])

        log_fn(f"  - Critical: {critical}개")
        log_fn(f"  - High: {high}개")
        log_fn(f"  - Medium: {medium}개")
        log_fn(f"  - Low: {low}개")

    log_fn(f"소요 시간: {result.duration_seconds:.1f}초")
    log_fn(f"종료 이유: {result.completion_reason}")
    log_fn("=" * 60)


def hash_url(url: str) -> str:
    """URL 해시 생성 (중복 방지)"""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    query = parsed.query or ""
    if any(key in query for key in ["id=", "item=", "product="]):
        base_url = f"{base_url}?{query}"
    return hashlib.md5(base_url.encode()).hexdigest()[:12]


def call_llm_text_only(llm: Any, prompt: str) -> str:
    """스크린샷 없이 텍스트만으로 LLM 호출 (provider 자동 선택)"""
    if hasattr(llm, "analyze_text"):
        return str(llm.analyze_text(prompt, max_completion_tokens=4096, temperature=0.2))

    if hasattr(llm, "client") and hasattr(getattr(llm, "client"), "models"):
        try:
            from google.genai import types

            response = llm.client.models.generate_content(
                model=llm.model,
                contents=[types.Content(parts=[types.Part(text=prompt)])],
                config=types.GenerateContentConfig(
                    max_output_tokens=4096,
                    temperature=0.2,
                ),
            )
            text = getattr(response, "text", None)
            if isinstance(text, str):
                return text
        except Exception:
            pass

    response = llm.client.chat.completions.create(
        model=llm.model,
        max_completion_tokens=4096,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.choices[0].message.content if response.choices else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
                continue
            text = getattr(item, "text", None)
            if isinstance(text, str):
                chunks.append(text)
        return "\n".join(chunks).strip()
    return str(content or "")
