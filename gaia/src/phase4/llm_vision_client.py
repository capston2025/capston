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
        self.client = openai.OpenAI(api_key=api_key, timeout=60.0)  # 60 second timeout
        # Use GPT-5-mini for vision tasks (cost optimization)
        # Master orchestrator still uses GPT-5 for critical DOM analysis
        self.model = "gpt-5-mini"

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
        # Filter out disabled elements first
        enabled_elements = [
            elem for elem in dom_elements
            if not (elem.attributes and (
                elem.attributes.get("aria-disabled") == "true" or
                elem.attributes.get("disabled") == "true" or
                elem.attributes.get("disabled") == True
            ))
        ]

        dom_list = []
        for idx, elem in enumerate(enabled_elements[:150]):  # Limit to 150 for better coverage (increased from 100)
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

**CRITICAL RULES - SELECTOR GENERATION:**
1. **NEVER use generic class selectors**: If you're tempted to use `.flex`, `.items-center`, `.gap-2`, or similar utility classes, set confidence to 0 instead
2. **NEVER create invalid CSS**: Selectors like `input.file:text-foreground` are INVALID (you can't combine pseudo-classes with plain text)
3. **ALWAYS prefer text-based selectors** for buttons/links: Use `button:has-text("exact text")` or `a:has-text("exact text")`
4. **ONLY use selectors that appear in the JSON above**: Don't make up new selectors

**Selector Priority (HIGHEST to LOWEST):**
1. Text-based: `button:has-text("폼과 피드백")` ✅ BEST
2. ARIA role-based: `[role="switch"]`, `[role="slider"]` ✅ BEST (for custom components)
3. Data attribute: `[data-slot="switch"]`, `[data-slot="slider"]` ✅ EXCELLENT (for Radix UI)
4. ID-based: `#submit-button` ✅ GOOD
5. Type + text: `input[placeholder="이메일"]` ✅ ACCEPTABLE
6. Generic classes: `.flex`, `.items-center` ❌ FORBIDDEN (set confidence to 0)

**Action Detection Rules:**
- If task mentions "wait", "verify", "check", or "confirm" → action should be "waitForTimeout" or "expectVisible", NOT "click"
- If task is "대기" or "확인용 대기" → action is "waitForTimeout", NOT "click"
- If task is about filling forms → action is "fill"
- If task involves keyboard input → action is "press"
- If task involves navigation → action is "goto"

**Custom Component Detection (CRITICAL FOR RADIX UI / ARIA):**
**ALWAYS check ARIA roles FIRST before using class/id selectors!**

- **Switch/Toggle**: `role="switch"` → Selector: `[role="switch"]`, Action: click, Confidence: 90+
- **Slider/Range**: `role="slider"` → Selector: `[role="slider"]`, Action: click, Confidence: 90+
- **Dialog/Modal**: `role="dialog"` or `role="alertdialog"` → Selector: `[role="dialog"]`, Confidence: 90+
- **Checkbox**: `role="checkbox"` → Selector: `[role="checkbox"]`, Action: click, Confidence: 90+
- **Radio**: `role="radio"` → Selector: `[role="radio"]`, Action: click, Confidence: 90+
- **Tab**: `role="tab"` → Selector: `[role="tab"]`, Action: click, Confidence: 90+
- **Menu/Dropdown**: `role="menu"` or `role="menuitem"` → Selector: `[role="menu"]`, Confidence: 90+
- **Combobox/Select**: `role="combobox"` → Selector: `[role="combobox"]`, Confidence: 90+
- **Search**: `role="searchbox"` → Selector: `[role="searchbox"]`, Action: fill, Confidence: 90+

**Keywords to ARIA mapping:**
- toggle, switch, 스위치, 토글 → `[role="switch"]`
- slider, range, 슬라이더 → `[role="slider"]`
- dialog, modal, 다이얼로그, 모달, popup, 팝업 → `[role="dialog"]`
- checkbox, 체크박스 → `[role="checkbox"]`
- radio, 라디오 → `[role="radio"]`
- tab, 탭 → `[role="tab"]`
- menu, 메뉴, dropdown, 드롭다운 → `[role="menu"]`
- search, 검색 → `[role="searchbox"]`

**CRITICAL**: Custom components may NOT be standard HTML inputs. Look for ARIA roles first!

**Fuzzy Matching Rules (CRITICAL FOR NAVIGATION):**
When task uses vague terms, match to similar Korean/English equivalents:
- "forms section" / "폼" → matches "폼과 피드백", "Forms & Feedback", or similar
- "feedback section" / "피드백" → matches "폼과 피드백", "Feedback", or similar
- "interactions" / "인터랙션" → matches "인터랙션과 데이터", "Interactions", or similar
- "basics" / "기본" → matches "기본 기능", "Basic Features", or similar
- "home" / "홈" → matches "홈", "홈으로", "Home", or similar
- Be FLEXIBLE: If task says "navigate to X section", look for buttons containing related keywords
- HIGH CONFIDENCE (70-90) for partial matches that are semantically similar

**Matching Rules:**
1. Choose a selector from the "Available elements" list above
2. Look for elements that reasonably match the task description
3. **USE FUZZY/SEMANTIC MATCHING**: "forms section" should match "폼과 피드백" with HIGH confidence
4. **CRITICAL: If task mentions context like "under X" or "in Y section", find that context element FIRST**
5. **For multiple identical buttons**:
   - Task: "Click 둘러보기 under 기본 기능" → Find element with text "기본 기능", then find nearby "둘러보기"
   - Use parent/sibling relationships to disambiguate
   - If no context given, pick the FIRST matching element
6. If you can't find ANY reasonable semantic match, return LOW confidence (<30)
7. **If your selector would match multiple elements**: Set confidence to 20 (ambiguous)

**Examples of GOOD decisions:**
- Task: "Navigate to forms section" + Element: `button:has-text("폼과 피드백")` → confidence: 85 ✅ (fuzzy match!)
- Task: "Click Share button" + Element: `button:has-text("공유하기")` → confidence: 95 ✅
- Task: "Wait for page title" + Action: "waitForTimeout" → confidence: 90 ✅
- Task: "Fill email input" + Element: `input[type="email"]` → confidence: 85 ✅
- Task: "Click on home or 홈" + Element: `button:has-text("홈으로")` → confidence: 80 ✅ (partial match OK!)
- Task: "Toggle switch on and off" + Element has `role="switch"` → Selector: `[role="switch"]`, confidence: 90 ✅ (ARIA role!)
- Task: "Use slider control" + Element has `role="slider"` → Selector: `[role="slider"]`, confidence: 90 ✅ (ARIA role!)

**Examples of BAD decisions (AVOID THESE):**
- Task: "Click button" + Selector: `button.flex.items-center` → confidence: 0 ❌ (generic classes!)
- Task: "Wait for animation" + Action: "click" → confidence: 0 ❌ (wrong action!)
- Task: "Click filter" + Selector matching 4 elements → confidence: 0 ❌ (ambiguous!)
- Selector: `input.file:text-foreground` → confidence: 0 ❌ (invalid CSS!)

Required JSON format (no markdown):
{{
    "selector": "css_selector_from_list_above_or_empty_if_bad_match",
    "action": "click_or_fill_or_press_or_goto_or_waitForTimeout_or_expectVisible",
    "reasoning": "why this element matches (or why confidence is 0)",
    "confidence": 85
}}

JSON response:"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=2048,  # Increased from 1024 for GPT-5
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

    def find_element_coordinates(
        self,
        screenshot_base64: str,
        description: str,
    ) -> Dict[str, Any]:
        """
        Find element coordinates from screenshot (vision-based fallback).

        Args:
            screenshot_base64: Base64-encoded screenshot
            description: Element description (e.g., "Click submit button")

        Returns:
            Dict with x, y, confidence, reasoning
        """
        try:
            prompt = f"""You are a UI element locator. Find the element described as: "{description}"

**CRITICAL: Respond with ONLY valid JSON. No explanations, no markdown.**

Analyze the screenshot and return the CENTER COORDINATES of the target element:

{{
  "x": <pixel x coordinate of element center>,
  "y": <pixel y coordinate of element center>,
  "confidence": <0.0 to 1.0>,
  "reasoning": "<brief explanation>"
}}

If the element is not visible or unclear, set confidence to 0.0

**JSON ONLY (no markdown):**"""

            response_text = self.analyze_with_vision(prompt, screenshot_base64)

            # Parse JSON response
            result = json.loads(response_text.strip())

            # Validate response structure
            if not all(k in result for k in ["x", "y", "confidence"]):
                return {
                    "x": 0,
                    "y": 0,
                    "confidence": 0.0,
                    "reasoning": "Invalid response structure"
                }

            return result

        except json.JSONDecodeError as e:
            print(f"LLM coordinate extraction failed (JSON parse error): {e}")
            print(f"Response was: {response_text[:200]}")
            return {
                "x": 0,
                "y": 0,
                "confidence": 0.0,
                "reasoning": f"JSON parse error: {e}"
            }
        except Exception as e:
            print(f"LLM coordinate extraction failed: {e}")
            return {
                "x": 0,
                "y": 0,
                "confidence": 0.0,
                "reasoning": f"Error: {e}"
            }

    def aggregate_matching_results(
        self,
        step_description: str,
        aria_result: Dict[str, Any] | None,
        semantic_result: Dict[str, Any] | None,
        vision_result: Dict[str, Any],
        url: str,
    ) -> Dict[str, Any]:
        """
        병렬로 실행된 3가지 매칭 결과를 LLM이 분석해서 최종 결정.

        Args:
            step_description: 스텝 설명
            aria_result: ARIA 탐지 결과 (None 가능)
            semantic_result: 시맨틱 매칭 결과 (None 가능)
            vision_result: LLM 비전 분석 결과
            url: 현재 페이지 URL

        Returns:
            최종 선택된 selector/action/confidence
        """
        # 결과를 JSON 형식으로 정리
        results_summary = {
            "aria": aria_result if aria_result else {"status": "no_match", "confidence": 0},
            "semantic": semantic_result if semantic_result else {"status": "no_match", "confidence": 0},
            "vision": vision_result
        }

        prompt = f"""You are a test automation expert deciding which element selector to use.

Task: {step_description}
Page URL: {url}

Three different methods analyzed the page:

**1. ARIA Role Detection:**
{json.dumps(results_summary['aria'], ensure_ascii=False, indent=2)}

**2. Semantic Text Matching (embedding-based):**
{json.dumps(results_summary['semantic'], ensure_ascii=False, indent=2)}

**3. Vision + DOM Analysis (your previous analysis):**
{json.dumps(results_summary['vision'], ensure_ascii=False, indent=2)}

**Decision Rules:**
1. If 2+ methods agree on the same element → HIGH confidence
2. If ARIA + Semantic agree (even if different from Vision) → prefer them (Vision might hallucinate)
3. If only 1 method succeeded → use it (but lower confidence)
4. If all 3 point to different elements → pick the one with highest confidence BUT penalize confidence by 20%
5. Consider method reliability: ARIA (most reliable for custom components) > Semantic (good for text) > Vision (can hallucinate)

**CRITICAL: Cross-validation**
- If ARIA found multiple elements but didn't disambiguate → Vision/Semantic can help choose
- If Vision selector doesn't match any ARIA/Semantic results → be suspicious (낮은 confidence)

Required JSON format (no markdown):
{{
    "selector": "final_css_selector",
    "action": "click_or_fill_or_press_etc",
    "reasoning": "why this was chosen (method agreement, confidence comparison, etc)",
    "confidence": 85,
    "method_used": "aria" or "semantic" or "vision" or "consensus"
}}

JSON response:"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            response_text = response.choices[0].message.content or ""

            # Strip markdown
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            if not response_text:
                print("[Aggregator] LLM returned empty response, using vision result")
                return vision_result

            try:
                result = json.loads(response_text)
                return result
            except json.JSONDecodeError:
                print(f"[Aggregator] JSON parse failed, using vision result: {response_text[:100]}")
                return vision_result

        except Exception as e:
            print(f"[Aggregator] Failed: {e}, using vision result")
            return vision_result


__all__ = ["LLMVisionClient"]
