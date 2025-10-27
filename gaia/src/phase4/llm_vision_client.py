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
        self.model = "gpt-5-mini"  # Multimodal reasoning model - 4x cheaper than o4-mini!

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

        prompt = f"""Analyze this webpage and select a DOM element for test automation.

Page URL: {url}
Task: {step_description}

Available elements (JSON):
{json.dumps(dom_list, ensure_ascii=False, indent=2)}

**Rules:**
1. Choose a selector from the "Available elements" list above
2. Look for elements that reasonably match the task description
3. Use context clues - buttons near each other might be related
4. If you can't find a good match, return LOW confidence (<60)
5. Examples of GOOD matches:
   - Task: "Click Share button" + Element: "공유하기" → confidence: 95
   - Task: "Click Login" + Element: "로그인" → confidence: 90
   - Task: "Open modal" + Element: "열기", "Dialog", "모달" → confidence: 85
6. Examples of BAD matches:
   - Task: "Click Filter" + Element: "공유하기" → confidence: 30 (wrong element!)
   - Task: "Click Help" + ONLY "공유하기" exists → confidence: 40 (no good match)

**Matching tips:**
- Look for exact text matches first (HIGHEST PRIORITY!)
- **PREFER text-based selectors like 'button:has-text("폼과 피드백")' over generic class selectors**
- **AVOID generic selectors like 'button.flex', '.items-center' that match multiple elements**
- If the selector you choose would match multiple elements, LOWER your confidence to <60
- Check element type (button for clicks, input for fill)
- If truly no match exists, return confidence: 0-40

Required JSON format (no markdown):
{{
    "selector": "css_selector_from_list_above",
    "action": "click",
    "reasoning": "why this element matches the task (or why confidence is low)",
    "confidence": 85
}}

JSON response:"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=1024,
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

            # Handle empty response
            if not response_text:
                print("LLM element selection returned empty response")
                return {
                    "selector": "",
                    "action": "skip",
                    "reasoning": "LLM returned empty response",
                    "confidence": 0
                }

            # Try to parse JSON
            try:
                result = json.loads(response_text)
                return result
            except json.JSONDecodeError:
                print(f"LLM element selection returned non-JSON: {response_text[:200]}")
                return {
                    "selector": "",
                    "action": "skip",
                    "reasoning": f"LLM returned non-JSON: {response_text[:100]}",
                    "confidence": 0
                }

        except Exception as e:
            print(f"LLM vision analysis failed: {e}")
            return {
                "selector": "",
                "action": "skip",
                "reasoning": f"Analysis failed: {e}",
                "confidence": 0
            }

    def analyze_with_vision(
        self,
        prompt: str,
        screenshot_base64: str,
    ) -> str:
        """
        General-purpose vision analysis with screenshot.

        Args:
            prompt: Text prompt for LLM
            screenshot_base64: Base64-encoded screenshot

        Returns:
            LLM response as string
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=2048,
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

            # Strip markdown code blocks if present
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            return response_text.strip()

        except Exception as e:
            print(f"LLM vision analysis failed: {e}")
            raise

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
        prompt = f"""Compare these two screenshots to verify test results.

Expected: {expected_result}
Page: {url}

Images: Before (first) and After (second)

Task: Did the expected result occur?

Required JSON format (no markdown):
{{
    "success": true,
    "reasoning": "what changed",
    "confidence": 90
}}

JSON response:"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=512,
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

            # Handle empty response
            if not response_text:
                print("LLM verification returned empty response")
                return {
                    "success": False,
                    "reasoning": "LLM returned empty response",
                    "confidence": 0
                }

            # Try to parse JSON, with better error handling
            try:
                result = json.loads(response_text)
                return result
            except json.JSONDecodeError as json_err:
                print(f"LLM verification returned non-JSON response: {response_text[:200]}")
                return {
                    "success": False,
                    "reasoning": f"LLM returned non-JSON: {response_text[:100]}",
                    "confidence": 0
                }

        except Exception as e:
            print(f"LLM verification failed: {e}")
            return {
                "success": False,
                "reasoning": f"Verification failed: {e}",
                "confidence": 0
            }


__all__ = ["LLMVisionClient"]
