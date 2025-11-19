"""
Gemini Vision Client for intelligent browser automation.
Uses Gemini 3 for DOM + screenshot analysis.

To use: set VISION_PROVIDER=gemini in .env
"""
from __future__ import annotations

import json
import os
import base64
from pathlib import Path
from typing import Any, Dict, List

from google import genai
from google.genai import types

from gaia.src.utils.models import DomElement


class GeminiVisionClient:
    """Client for Gemini-powered vision analysis of web pages."""

    def __init__(self, api_key: str | None = None) -> None:
        """
        Initialize the Gemini vision client.

        Args:
            api_key: Gemini API key (if None, reads from GEMINI_API_KEY env var or .env file)
        """
        # Load .env file if it exists
        env_file = Path(__file__).parent.parent.parent.parent / ".env"
        env_vars = {}
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        key, value = line.split("=", 1)
                        env_vars[key.strip()] = value.strip()

        gemini_key = api_key or os.getenv("GEMINI_API_KEY") or env_vars.get("GEMINI_API_KEY")
        if not gemini_key:
            raise ValueError("GEMINI_API_KEY is required")

        # v1alpha API version required for media_resolution parameter
        self.client = genai.Client(
            api_key=gemini_key,
            http_options={'api_version': 'v1alpha'}
        )
        self.model = "gemini-3-pro-preview"
        print(f"🤖 Vision AI: Using Gemini ({self.model})")

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
            response_text = self._call_vision_api(prompt, [screenshot_base64])

            # Strip markdown code blocks if present
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            return response_text.strip()

        except Exception as e:
            print(f"Gemini vision analysis failed: {e}")
            raise

    def _call_vision_api(self, prompt: str, images: List[str], max_tokens: int = 16384) -> str:
        """Call Gemini vision API with prompt and images."""
        # Build parts with media_resolution_high for best image quality
        parts = [types.Part(text=prompt)]

        for img_base64 in images:
            img_bytes = base64.b64decode(img_base64)
            parts.append(types.Part(
                inline_data=types.Blob(
                    mime_type="image/png",
                    data=img_bytes,
                )
            ))

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[types.Content(parts=parts)],
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.1
                )
            )
        except Exception as e:
            print(f"⚠️ Gemini API call failed: {e}")
            raise

        # Debug: print raw response structure
        print(f"📝 Gemini raw response type: {type(response)}")

        result_text = ""
        try:
            if response.text:
                result_text = response.text
        except Exception:
            # response.text may throw if no candidates
            pass

        if not result_text:
            # Try to get text from candidates
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and candidate.content:
                    if hasattr(candidate.content, 'parts') and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, 'text') and part.text:
                                result_text = part.text
                                break

        if not result_text:
            print(f"⚠️ Gemini returned empty response. Full response: {response}")

        return result_text

    def verify_action_result(
        self,
        expected_result: str,
        before_screenshot: str,
        after_screenshot: str,
        url: str,
    ) -> Dict[str, Any]:
        """
        Verify if an action succeeded by comparing before/after screenshots.
        """
        prompt = f"""Compare these two screenshots to verify test results.

Expected: {expected_result}
Page: {url}

Images: Before (first) and After (second)

**CRITICAL - SEMANTIC MATCHING RULES:**
1. **DO NOT require exact text match** - Look for SEMANTIC equivalence
2. **Example matches:**
   - Expected: "로그인되었습니다!" → Actual: "사용자님 환영합니다!" ✅ BOTH mean login succeeded
   - Expected: "추가되었습니다" → Actual: "상품이 장바구니에 담겼습니다" ✅ BOTH mean item added
3. **Focus on the OUTCOME, not the exact wording**

Task: Did the expected OUTCOME occur? (Not exact text, but semantic meaning)

Required JSON format (no markdown):
{{
    "success": true,
    "reasoning": "what changed and why it matches the expected outcome",
    "confidence": 90
}}

JSON response:"""

        try:
            response_text = self._call_vision_api(prompt, [before_screenshot, after_screenshot], max_tokens=8192)

            # Parse JSON
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            if not response_text:
                return {"success": False, "reasoning": "Empty response", "confidence": 0}

            try:
                return json.loads(response_text)
            except json.JSONDecodeError:
                return {"success": False, "reasoning": f"Invalid JSON: {response_text[:100]}", "confidence": 0}

        except Exception as e:
            print(f"Gemini verification failed: {e}")
            return {"success": False, "reasoning": f"Error: {e}", "confidence": 0}

    def verify_scenario_success(
        self,
        scenario_description: str,
        expected_outcome: str,
        success_indicators: List[str],
        before_screenshot: str,
        after_screenshot: str,
        url: str,
    ) -> Dict[str, Any]:
        """
        Verify if a test scenario succeeded by comparing before/after screenshots.
        """
        indicators_text = "\n".join([f"  - {indicator}" for indicator in success_indicators])

        prompt = f"""Analyze these screenshots to verify if a test scenario succeeded.

**Scenario**: {scenario_description}
**Expected Outcome**: {expected_outcome}

**Success Indicators** (look for ANY of these):
{indicators_text}

**Page**: {url}
**Images**: Before (first) and After (second)

**CRITICAL - Flexible Matching Rules:**
1. **Look for SEMANTIC equivalence**, not exact text
2. **ANY success indicator is enough** - you don't need all of them
3. **Consider UI state changes**: Login button → Logout button, Modal closed → Modal open, etc.

**Task**: Did the scenario succeed based on visible changes?

Required JSON format (no markdown):
{{
    "success": true,
    "reasoning": "detailed explanation of what changed and which indicators were found",
    "confidence": 90,
    "matched_indicators": ["list of success indicators that were found"]
}}

JSON response:"""

        try:
            response_text = self._call_vision_api(prompt, [before_screenshot, after_screenshot], max_tokens=8192)

            # Parse JSON
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            if not response_text:
                return {"success": False, "reasoning": "Empty response", "confidence": 0, "matched_indicators": []}

            try:
                result = json.loads(response_text)
                if "matched_indicators" not in result:
                    result["matched_indicators"] = []
                return result
            except json.JSONDecodeError:
                return {"success": False, "reasoning": f"Invalid JSON: {response_text[:100]}", "confidence": 0, "matched_indicators": []}

        except Exception as e:
            print(f"Gemini scenario verification failed: {e}")
            return {"success": False, "reasoning": f"Error: {e}", "confidence": 0, "matched_indicators": []}

    def verify_scenario_outcome(
        self,
        scenario_description: str,
        final_screenshot: str,
        url: str,
    ) -> Dict[str, Any]:
        """
        Verify if the final screenshot matches the scenario description.
        """
        prompt = f"""You are a test verification expert. Your task is to verify if a scenario was successfully executed.

**Scenario Description (Natural Language):**
{scenario_description}

**Current Page URL:**
{url}

**Task:**
Look at the screenshot and determine: Does this final state match what the scenario intended to achieve?

**Verification Guidelines:**
1. **Focus on the INTENT** of the scenario, not exact text matching
2. **Consider semantic equivalence**
3. **UI State Changes** are valid evidence
4. **Error states** indicate failure
5. **Success indicators**: Confirmation messages, UI state changes, new content

Required JSON format (no markdown):
{{
    "matches": true,
    "confidence": 85,
    "reasoning": "detailed explanation",
    "observations": ["key observation 1", "key observation 2"]
}}

JSON response:"""

        try:
            response_text = self._call_vision_api(prompt, [final_screenshot], max_tokens=8192)

            # Parse JSON
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            if not response_text:
                return {"matches": False, "confidence": 0, "reasoning": "Empty response", "observations": []}

            try:
                result = json.loads(response_text)
                if "observations" not in result:
                    result["observations"] = []
                return result
            except json.JSONDecodeError:
                return {"matches": False, "confidence": 0, "reasoning": f"Invalid JSON: {response_text[:100]}", "observations": []}

        except Exception as e:
            print(f"Gemini outcome verification failed: {e}")
            return {"matches": False, "confidence": 0, "reasoning": f"Error: {e}", "observations": []}

    def analyze_action_failure(
        self,
        action: str,
        selector: str,
        error_message: str,
        screenshot_base64: str,
        dom_elements: List[DomElement],
        url: str,
    ) -> Dict[str, Any]:
        """
        Analyze why an action failed and suggest recovery strategies.
        """
        # Format DOM elements (limited to 50 for error analysis)
        dom_list = []
        for idx, elem in enumerate(dom_elements[:50]):
            attrs = elem.attributes or {}
            dom_list.append({
                "index": idx,
                "tag": elem.tag,
                "selector": elem.selector,
                "text": elem.text,
                "role": attrs.get("role"),
                "aria_hidden": attrs.get("aria-hidden"),
            })

        dom_json = json.dumps(dom_list, ensure_ascii=False, indent=2)

        # Check if there are open overlays/modals in DOM
        overlay_elements = []
        for elem in dom_elements[:50]:
            attrs = elem.attributes or {}
            role = attrs.get('role', '')
            aria_modal = attrs.get('aria-modal', '')
            tag = elem.tag

            if (role in ['dialog', 'alertdialog', 'menu', 'listbox'] or
                aria_modal == 'true' or
                tag in ['dialog'] or
                'modal' in (elem.text or '').lower()):
                overlay_elements.append({
                    "tag": tag,
                    "role": role,
                    "text": elem.text[:50] if elem.text else "",
                    "selector": elem.selector
                })

        overlay_context = ""
        if overlay_elements:
            overlay_context = f"\n**⚠️ DETECTED OPEN OVERLAYS:** {len(overlay_elements)} overlay element(s) found:\n"
            for ov in overlay_elements[:3]:
                overlay_context += f"  - {ov['tag']} role={ov['role']} text=\"{ov['text']}\"\n"
            overlay_context += "This strongly suggests overlay interception is the cause.\n"

        prompt = f"""You are a test automation expert analyzing why an action failed.

**Failed Action:** {action}
**Selector:** {selector}
**Error Message:** {error_message}
**Page URL:** {url}
{overlay_context}
**Available DOM Elements (top 50):**
{dom_json}

**Common Failure Patterns:**

1. **Overlay/Modal Interception**
   - Error contains: "intercepts pointer events", "covered by", "not clickable"
   - Fix: Close overlay with Escape key, click backdrop, or wait for it to disappear

2. **Element Not Visible**
   - Error contains: "not visible", "hidden", "outside viewport"
   - Fix: Scroll element into view, wait for animation

3. **Invalid/Ambiguous Selector**
   - Error contains: "failed", "no element", "multiple elements"
   - Fix: Use JavaScript to find element, use more specific selector

4. **Timing Issues**
   - Error contains: "timeout", "detached", "stale"
   - Fix: Wait for element to be stable, retry after delay

**Your Task:**
1. Analyze the error message and screenshot
2. Identify the most likely failure reason
3. Suggest 1-3 concrete fixes to try (in priority order)

Required JSON format (no markdown):
{{
    "failure_reason": "overlay_interception" | "not_visible" | "invalid_selector" | "timing_issue" | "element_disabled" | "unknown",
    "suggested_fixes": [
        {{
            "type": "close_overlay" | "scroll" | "javascript" | "wait" | "retry" | "open_container" | "use_alternative_selector",
            "priority": 1,
            "description": "Human-readable description of the fix",
            "method": "press_escape" | "click_backdrop" | "scroll_into_view" | "direct_click" | null,
            "selector": "selector if needed" | null,
            "script": "JavaScript code if type is javascript" | null,
            "duration": "wait duration in ms if type is wait" | null
        }}
    ],
    "confidence": 85,
    "reasoning": "Detailed explanation of why this failure occurred and why these fixes should work"
}}

JSON response:"""

        try:
            response_text = self._call_vision_api(prompt, [screenshot_base64], max_tokens=8192)

            # Parse JSON
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            if not response_text:
                return {
                    "failure_reason": "unknown",
                    "suggested_fixes": [],
                    "confidence": 0,
                    "reasoning": "Gemini returned empty response"
                }

            try:
                result = json.loads(response_text)
                if "suggested_fixes" not in result:
                    result["suggested_fixes"] = []
                return result
            except json.JSONDecodeError:
                return {
                    "failure_reason": "unknown",
                    "suggested_fixes": [],
                    "confidence": 0,
                    "reasoning": f"Gemini returned non-JSON: {response_text[:100]}"
                }

        except Exception as e:
            print(f"Gemini error analysis failed: {e}")
            return {
                "failure_reason": "unknown",
                "suggested_fixes": [],
                "confidence": 0,
                "reasoning": f"Error: {e}"
            }


__all__ = ["GeminiVisionClient"]
