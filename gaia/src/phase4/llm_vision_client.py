"""
LLM Vision Client for intelligent browser automation.
Uses GPT-4V for DOM + screenshot analysis.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import openai

from gaia.src.utils.models import DomElement


class LLMVisionClient:
    """Client for LLM-powered vision analysis of web pages."""

    def __init__(self, api_key: str | None = None) -> None:
        """
        Initialize the LLM vision client.

        Args:
            api_key: OpenAI API key (if None, reads from OPENAI_API_KEY env var)
        """
        self.client = openai.OpenAI(api_key=api_key)
        self.model = "gpt-4o"  # GPT-4 with vision (faster and cheaper than gpt-4-turbo)

    def select_element_for_step(
        self,
        step_description: str,
        dom_elements: List[DomElement],
        screenshot_base64: str,
        url: str,
    ) -> Dict[str, Any]:
        """
        Ask LLM to select the best DOM element for a given step.

        Args:
            step_description: Description of the action (e.g., "로그인 버튼 클릭")
            dom_elements: List of available DOM elements
            screenshot_base64: Base64-encoded screenshot of the page
            url: Current page URL

        Returns:
            Dict with:
                - selector: CSS selector to use
                - action: Action type (click, fill, press, etc.)
                - reasoning: Why this element was selected
                - confidence: Confidence score (0-100)
        """
        # Format DOM elements for LLM
        dom_list = []
        for idx, elem in enumerate(dom_elements[:50]):  # Limit to 50 for token efficiency
            dom_list.append({
                "index": idx,
                "tag": elem.tag,
                "selector": elem.selector,
                "text": elem.text,
                "type": elem.element_type,
                "attributes": elem.attributes or {}
            })

        prompt = f"""당신은 QA 자동화 에이전트입니다. 주어진 웹페이지에서 특정 작업을 수행하기 위해 어떤 DOM 요소를 사용해야 하는지 판단해야 합니다.

**현재 페이지:**
- URL: {url}
- 스크린샷: 첨부됨
- DOM 요소: {len(dom_elements)}개

**수행할 작업:**
{step_description}

**사용 가능한 DOM 요소 목록:**
{json.dumps(dom_list, ensure_ascii=False, indent=2)}

**요청사항:**
1. 스크린샷을 보고 페이지 레이아웃을 이해하세요
2. "{step_description}" 작업을 수행하기 위해 가장 적합한 DOM 요소를 선택하세요
3. 선택한 요소의 selector를 반환하세요

**응답 형식 (JSON):**
{{
    "selector": "실제 사용할 CSS selector",
    "action": "click|fill|press",
    "reasoning": "이 요소를 선택한 이유 (1-2문장)",
    "confidence": 85
}}

**중요:**
- selector는 DOM 요소 목록에서 실제로 존재하는 것을 선택하세요
- 스크린샷에서 시각적으로 확인 가능한 요소를 우선하세요
- confidence는 0-100 사이의 숫자입니다 (80 이상이면 실행)
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{screenshot_base64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ]
            )

            # Extract text from response
            response_text = response.choices[0].message.content or ""

            # Parse JSON from response
            # Strip markdown code blocks if present
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            result = json.loads(response_text)
            return result

        except Exception as e:
            print(f"LLM vision analysis failed: {e}")
            return {
                "selector": "",
                "action": "skip",
                "reasoning": f"Analysis failed: {e}",
                "confidence": 0
            }

    def verify_action_result(
        self,
        expected_result: str,
        before_screenshot: str,
        after_screenshot: str,
        url: str,
    ) -> Dict[str, Any]:
        """
        Verify if an action succeeded by comparing before/after screenshots.

        Args:
            expected_result: Expected outcome description
            before_screenshot: Screenshot before action
            after_screenshot: Screenshot after action
            url: Current page URL

        Returns:
            Dict with:
                - success: Boolean indicating if verification passed
                - reasoning: Why it passed/failed
                - confidence: Confidence score (0-100)
        """
        prompt = f"""당신은 QA 자동화 에이전트입니다. 브라우저에서 작업을 수행한 후 결과를 검증해야 합니다.

**기대했던 결과:**
{expected_result}

**현재 페이지:** {url}

**스크린샷:**
- 작업 전: 첫 번째 이미지
- 작업 후: 두 번째 이미지

**요청사항:**
1. 두 스크린샷을 비교하세요
2. "{expected_result}"가 달성되었는지 판단하세요
3. 성공/실패 여부와 이유를 설명하세요

**응답 형식 (JSON):**
{{
    "success": true,
    "reasoning": "로그인 후 대시보드 페이지가 보임",
    "confidence": 90
}}
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{before_screenshot}"
                                }
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{after_screenshot}"
                                }
                            }
                        ]
                    }
                ]
            )

            # Extract text from response
            response_text = response.choices[0].message.content or ""

            # Parse JSON
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            result = json.loads(response_text)
            return result

        except Exception as e:
            print(f"LLM verification failed: {e}")
            return {
                "success": False,
                "reasoning": f"Verification failed: {e}",
                "confidence": 0
            }


__all__ = ["LLMVisionClient"]
