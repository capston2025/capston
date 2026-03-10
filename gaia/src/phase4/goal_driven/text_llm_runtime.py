from __future__ import annotations

from typing import List


def call_llm_text_only(agent, prompt: str) -> str:
    """스크린샷 없이 텍스트만으로 LLM 호출"""
    if hasattr(agent.llm, "analyze_text"):
        return str(agent.llm.analyze_text(prompt, max_completion_tokens=4096, temperature=0.1))

    if hasattr(agent.llm, "client") and hasattr(getattr(agent.llm, "client"), "models"):
        try:
            from google.genai import types

            response = agent.llm.client.models.generate_content(
                model=agent.llm.model,
                contents=[types.Content(parts=[types.Part(text=prompt)])],
                config=types.GenerateContentConfig(
                    max_output_tokens=4096,
                    temperature=0.1,
                ),
            )
            text = getattr(response, "text", None)
            if isinstance(text, str):
                return text
        except Exception:
            pass

    response = agent.llm.client.chat.completions.create(
        model=agent.llm.model,
        max_completion_tokens=4096,
        temperature=0.1,
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
