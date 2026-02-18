"""
LLM Vision Client for intelligent browser automation.
Uses GPT-4V for DOM + screenshot analysis.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import openai

from gaia.src.utils.models import DomElement


class LLMVisionClient:
    """Client for LLM-powered vision analysis of web pages."""

    def __init__(self, api_key: str | None = None) -> None:
        """
        Initialize the LLM vision client.

        Args:
            api_key: OpenAI API key (if None, reads from OPENAI_API_KEY env var or .env file)
        """
        # Load .env file if it exists
        if api_key is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key is None:
                # Try loading from .env file
                env_file = Path(__file__).parent.parent.parent / ".env"
                if env_file.exists():
                    with open(env_file) as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("OPENAI_API_KEY="):
                                api_key = line.split("=", 1)[1].strip()
                                os.environ["OPENAI_API_KEY"] = api_key
                                break

        self.client = openai.OpenAI(api_key=api_key, timeout=60.0)  # 60 second timeout
        configured_model = (
            os.getenv("GAIA_LLM_MODEL")
            or os.getenv("VISION_MODEL")
            or "gpt-5.2"
        ).strip()
        if configured_model.lower().startswith("gemini-"):
            configured_model = "gpt-5.2"
        self.model = configured_model
        self._auth_source = self._load_auth_source()
        model_prefers_codex = "codex" in self.model.lower()
        self._prefer_codex_cli = (
            (self._auth_source.startswith("oauth_codex_cli") or model_prefers_codex)
            and shutil.which("codex") is not None
        )
        if self._prefer_codex_cli:
            print("🔐 OpenAI OAuth(Codex) 감지: Codex CLI 경로를 우선 사용합니다.")
        print(f"🤖 Vision AI: Using OpenAI ({self.model})")

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        value = (text or "").strip()
        if value.startswith("```json"):
            value = value[7:]
        if value.startswith("```"):
            value = value[3:]
        if value.endswith("```"):
            value = value[:-3]
        return value.strip()

    @staticmethod
    def _load_auth_source() -> str:
        env_source = os.getenv("GAIA_OPENAI_AUTH_SOURCE")
        if isinstance(env_source, str) and env_source.strip():
            return env_source.strip()
        profile_path = Path.home() / ".gaia" / "auth" / "profiles.json"
        try:
            raw = json.loads(profile_path.read_text(encoding="utf-8"))
            profile = raw.get("openai", {}) if isinstance(raw, dict) else {}
            source = profile.get("source", "")
            return source if isinstance(source, str) else ""
        except Exception:
            return ""

    @staticmethod
    def _is_quota_error(error_text: str) -> bool:
        lowered = (error_text or "").lower()
        return "insufficient_quota" in lowered or "quota" in lowered

    @staticmethod
    def _decode_image_to_file(image_b64: str, path: Path) -> None:
        raw = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
        path.write_bytes(base64.b64decode(raw))

    def _run_codex_exec(self, prompt: str, images: List[str] | None = None) -> str:
        codex_bin = shutil.which("codex")
        if not codex_bin:
            raise RuntimeError("codex CLI를 찾을 수 없습니다.")

        images = images or []
        with tempfile.TemporaryDirectory(prefix="gaia-codex-") as tmpdir:
            tmp_path = Path(tmpdir)
            output_file = tmp_path / "last_message.txt"
            cmd = [
                codex_bin,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--output-last-message",
                str(output_file),
            ]

            # Model 지정이 실패하면 기본 모델로 재시도할 수 있도록 2회 시도.
            candidates = [self.model, ""]
            last_error = ""

            for candidate_model in candidates:
                run_cmd = list(cmd)
                if candidate_model:
                    run_cmd.extend(["-m", candidate_model])

                for idx, image_b64 in enumerate(images):
                    image_path = tmp_path / f"input_{idx}.png"
                    self._decode_image_to_file(image_b64, image_path)
                    run_cmd.extend(["-i", str(image_path)])

                run_cmd.append("-")
                completed = subprocess.run(
                    run_cmd,
                    input=prompt.encode("utf-8"),
                    capture_output=True,
                    check=False,
                )
                stdout_text = (
                    completed.stdout.decode("utf-8", errors="replace")
                    if isinstance(completed.stdout, (bytes, bytearray))
                    else str(completed.stdout or "")
                )
                stderr_text = (
                    completed.stderr.decode("utf-8", errors="replace")
                    if isinstance(completed.stderr, (bytes, bytearray))
                    else str(completed.stderr or "")
                )
                if completed.returncode == 0:
                    if output_file.exists():
                        return output_file.read_text(encoding="utf-8").strip()
                    return (stdout_text or "").strip()

                last_error = (stderr_text or stdout_text or "").strip()
                # 모델 지정 실패 계열이면 기본 모델로 재시도
                lower_error = last_error.lower()
                if candidate_model and (
                    "unknown model" in lower_error
                    or "invalid model" in lower_error
                    or "unsupported model" in lower_error
                ):
                    continue
                break

        raise RuntimeError(f"codex exec failed: {last_error or 'unknown error'}")

    @staticmethod
    def _response_text(response: Any) -> str:
        try:
            content = response.choices[0].message.content
        except Exception:
            return ""
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

    def analyze_text(
        self,
        prompt: str,
        *,
        max_completion_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> str:
        if self._prefer_codex_cli:
            return self._strip_code_fences(self._run_codex_exec(prompt, []))

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=max_completion_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._strip_code_fences(self._response_text(response))
        except Exception as exc:
            if self._is_quota_error(str(exc)) and shutil.which("codex"):
                return self._strip_code_fences(self._run_codex_exec(prompt, []))
            raise

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
            attrs = elem.attributes or {}
            dom_list.append({
                "index": idx,
                "tag": elem.tag,
                "selector": elem.selector,
                "text": elem.text,
                "type": elem.element_type,
                # CRITICAL: Expose key attributes at top level for LLM visibility
                "role": attrs.get("role"),
                "button_type": attrs.get("type"),  # For buttons: submit, button, reset
                "data_state": attrs.get("data-state"),  # Radix UI state (active, inactive)
                "aria_selected": attrs.get("aria-selected"),  # Tab selection state
                "attributes": attrs
            })

        # Build prompt using string concatenation to avoid f-string brace conflicts with JSON
        dom_json = json.dumps(dom_list, ensure_ascii=False, indent=2)
        prompt = f"""Analyze this webpage and select a DOM element for test automation.

Page URL: {url}
Task: {step_description}

Available elements (JSON):
{dom_json}

**CRITICAL RULES - SELECTOR GENERATION:**
1. **NEVER use generic class selectors**: If you're tempted to use `.flex`, `.items-center`, `.gap-2`, or similar utility classes, set confidence to 0 instead
2. **NEVER create invalid CSS**: Selectors like `input.file:text-foreground` are INVALID (you can't combine pseudo-classes with plain text)
3. **ALWAYS prefer text-based selectors** for buttons/links: Use `button:has-text("exact text")` or `a:has-text("exact text")`
4. **ONLY use selectors that appear in the JSON above**: Don't make up new selectors

**Selector Priority (HIGHEST to LOWEST):**
1. **Form context priority (CRITICAL!)**: If task is '회원가입', '제출', '저장', '확인', '가입', '로그인' etc:
   - ✅ MUST select: Elements where `button_type="submit"` (this is the REAL submit button!)
   - ❌ NEVER select: Elements where `role="tab"` or `data_state="active"` (these are TAB CONTROLS, not submit buttons!)
   - **Check the JSON fields**: Look at `role`, `button_type`, `data_state` to distinguish tabs from buttons
   - **Generate SPECIFIC selectors ONLY when button_type exists**:
     - If `button_type="submit"` → use `button[type="submit"]:has-text("TEXT")`
     - If `button_type="button"` or `button_type` is null/missing → use `button:has-text("TEXT")` (don't add [type])
   - Example: Two buttons with text "회원가입":
     - Button A: `{{"role": "tab", "button_type": "button", "data_state": "active"}}` ← This is a TAB, skip it!
     - Button B: `{{"role": null, "button_type": "submit", "data_state": null}}` ← This is the SUBMIT BUTTON!
       → Return selector: `button[type="submit"]:has-text("회원가입")`
   - Example: Single button without type attribute:
     - Button: `{{"role": null, "button_type": null, "text": "성공"}}` ← No type attribute
       → Return selector: `button:has-text("성공")` (NOT `button[type="submit"]:has-text("성공")`)
2. Text-based: `button:has-text("폼과 피드백")` ✅ BEST
3. ARIA role-based: `[role="switch"]`, `[role="slider"]` ✅ BEST (for custom components)
4. Data attribute: `[data-slot="switch"]`, `[data-slot="slider"]` ✅ EXCELLENT (for Radix UI)
5. ID-based: `#submit-button` ✅ GOOD - BUT check if `role` matches intent first
6. Type + text: `input[placeholder="이메일"]` ✅ ACCEPTABLE
7. Generic classes: `.flex`, `.items-center` ❌ FORBIDDEN (set confidence to 0)

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
- **Tab**: `role="tab"` → ONLY if task explicitly mentions "탭" or "이동" or "전환"! Otherwise set Confidence: 0
- **Menu/Dropdown**: `role="menu"` or `role="menuitem"` → Selector: `[role="menu"]`, Confidence: 90+
- **Combobox/Select**: `role="combobox"` → Selector: `[role="combobox"]`, Confidence: 90+
- **Search**: `role="searchbox"` → Selector: `[role="searchbox"]`, Action: fill, Confidence: 90+

**CRITICAL EXAMPLE - Tab vs Submit Button:**
Task: "회원가입 버튼 클릭"
Available elements in JSON:
```json
[
  {{
    "index": 0,
    "tag": "button",
    "text": "회원가입",
    "role": "tab",           // ← TAB CONTROL!
    "button_type": "button",
    "data_state": "active",
    "selector": "#radix-:r0:-trigger-signup"
  }},
  {{
    "index": 1,
    "tag": "button",
    "text": "회원가입",
    "role": null,            // ← NOT a tab
    "button_type": "submit", // ← SUBMIT BUTTON!
    "data_state": null,
    "selector": "button:has-text(\\"회원가입\\")"
  }}
]
```
→ You MUST select index 1 and return this selector:
  - `button[type="submit"]:has-text("회원가입")` (SPECIFIC - only matches submit button!)
  - NOT `button:has-text("회원가입")` (TOO BROAD - matches both buttons!)
  - Reasoning:
    - Task is "버튼 클릭" (form submission intent)
    - Element 0 has `role="tab"` → Skip it (tab control)
    - Element 1 has `button_type="submit"` → Correct choice!
    - Use attribute selector to be specific

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
            response_text = self.analyze_with_vision(prompt, screenshot_base64)

            # Parse JSON from response
            response_text = self._strip_code_fences(response_text)

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
        if self._prefer_codex_cli:
            return self._strip_code_fences(self._run_codex_exec(prompt, [screenshot_base64]))

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

            return self._strip_code_fences(self._response_text(response))

        except Exception as e:
            if self._is_quota_error(str(e)) and shutil.which("codex"):
                try:
                    print("ℹ️ OpenAI API quota 제한 감지: Codex CLI 경로로 재시도합니다.")
                    return self._strip_code_fences(
                        self._run_codex_exec(prompt, [screenshot_base64])
                    )
                except Exception as codex_exc:
                    print(f"LLM vision analysis failed: {e}")
                    print(f"Codex fallback failed: {codex_exc}")
                    raise
            print(f"LLM vision analysis failed: {e}")
            raise

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
        This is more holistic than single-action verification.

        Args:
            scenario_description: Description of the scenario (e.g., "회원가입 성공 시 자동 로그인된다")
            expected_outcome: Overall expected outcome
            success_indicators: List of indicators that show success
            before_screenshot: Screenshot before scenario execution
            after_screenshot: Screenshot after scenario execution
            url: Current page URL

        Returns:
            Dict with:
                - success: Boolean indicating if scenario succeeded
                - reasoning: Why it passed/failed
                - confidence: Confidence score (0-100)
                - matched_indicators: List of success indicators found
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
3. **Examples of equivalent success indicators**:
   - "회원가입이 완료되었습니다!" ≈ "회원가입이 완료되었습니다!" (toast)
   - "로그인되었습니다!" ≈ "사용자님 환영합니다!" ≈ "OOO님 환영합니다!"
   - "장바구니에 추가" ≈ "장바구니에 담겼습니다" ≈ cart count increased
4. **Consider UI state changes**:
   - Login button → Logout button
   - Empty form → Filled form
   - Modal closed → Modal open
   - Item count: 0 → Item count: 1
5. **Temporary UI elements** (toasts, notifications):
   - Even if toast disappeared, look for OTHER indicators of success
   - Example: Toast gone but user is now logged in = SUCCESS

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
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=1024,
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
                print("LLM scenario verification returned empty response")
                return {
                    "success": False,
                    "reasoning": "LLM returned empty response",
                    "confidence": 0,
                    "matched_indicators": []
                }

            # Try to parse JSON
            try:
                result = json.loads(response_text)
                # Ensure matched_indicators exists
                if "matched_indicators" not in result:
                    result["matched_indicators"] = []
                return result
            except json.JSONDecodeError as json_err:
                print(f"LLM scenario verification returned non-JSON response: {response_text[:200]}")
                return {
                    "success": False,
                    "reasoning": f"LLM returned non-JSON: {response_text[:100]}",
                    "confidence": 0,
                    "matched_indicators": []
                }

        except Exception as e:
            print(f"LLM scenario verification failed: {e}")
            return {
                "success": False,
                "reasoning": f"Verification failed: {e}",
                "confidence": 0,
                "matched_indicators": []
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
        prompt = f"""Compare these two screenshots to verify test results.

Expected: {expected_result}
Page: {url}

Images: Before (first) and After (second)

**CRITICAL - SEMANTIC MATCHING RULES:**
1. **DO NOT require exact text match** - Look for SEMANTIC equivalence
2. **Example matches:**
   - Expected: "로그인되었습니다!" → Actual: "사용자님 환영합니다!" ✅ BOTH mean login succeeded
   - Expected: "추가되었습니다" → Actual: "상품이 장바구니에 담겼습니다" ✅ BOTH mean item added
   - Expected: "삭제 완료" → Actual: "제거되었습니다" ✅ BOTH mean deletion succeeded
3. **Focus on the OUTCOME, not the exact wording**
4. **Common patterns to recognize:**
   - Login success: "로그인되었습니다", "환영합니다", "Welcome", "사용자님"
   - Form submission: "제출되었습니다", "전송 완료", "Success", "완료"
   - Item added: "추가되었습니다", "담겼습니다", "Added to cart"
   - Deletion: "삭제되었습니다", "제거되었습니다", "Deleted"

Task: Did the expected OUTCOME occur? (Not exact text, but semantic meaning)

Required JSON format (no markdown):
{{
    "success": true,
    "reasoning": "what changed and why it matches the expected outcome",
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

**CRITICAL MATCHING RULES - READ CAREFULLY:**

1. **SEMANTIC MATCHING (NOT literal text matching):**
   - "이름 입력" = ANY name input field
     ✓ Label showing "이름"
     ✓ Placeholder "홍길동", "Enter name", "Name"
     ✓ Field with label "Name" or "이름" nearby

   - "이메일 입력" = ANY email input field
     ✓ Label "이메일" or "Email"
     ✓ input[type="email"]
     ✓ Placeholder "@example.com" patterns

   - "비밀번호 입력" = ANY password field
     ✓ Label "비밀번호" or "Password"
     ✓ input[type="password"]
     ✓ Field with lock icon nearby

2. **IGNORE exact text match requirement**
   - The description is a HINT about PURPOSE, not exact text to find
   - Look for the FUNCTION of the element, not the exact wording

3. **Example:**
   - Request: "이름 입력"
   - Screenshot shows: Label "이름" with input field (placeholder "홍길동")
   - **CORRECT**: Return coordinates with confidence 0.9+ ✓
   - **WRONG**: Return confidence 0.0 because text doesn't exactly say "이름 입력" ✗

**CRITICAL: Respond with ONLY valid JSON. No explanations, no markdown.**

Analyze the screenshot and return the CENTER COORDINATES of the target element:

{{
  "x": <pixel x coordinate of element center>,
  "y": <pixel y coordinate of element center>,
  "confidence": <0.0 to 1.0>,
  "reasoning": "<brief explanation>"
}}

If the element is truly not visible (not just mismatched text), set confidence to 0.0

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

    def find_exploreable_element(
        self,
        screenshot_base64: str,
        target_description: str,
    ) -> Dict[str, Any]:
        """
        타겟 요소가 안 보일 때, 어떤 요소를 클릭하면 나타날지 찾기.

        이 메서드는 스크린샷에서 타겟 요소가 보이지 않을 때,
        탭, 모달, 드롭다운 등을 클릭하면 타겟이 나타날 수 있는지 분석합니다.

        Args:
            screenshot_base64: Base64-encoded screenshot
            target_description: 찾으려는 요소 설명 (예: "이름 입력", "장바구니 수량 조절")

        Returns:
            Dict with:
                - found_exploreable: bool (탐색 가능한 요소를 찾았는지)
                - x: int (클릭할 x 좌표)
                - y: int (클릭할 y 좌표)
                - element_type: str ("tab" | "modal" | "dropdown" | "accordion")
                - element_text: str (버튼/탭에 있는 텍스트)
                - confidence: float (0.0-1.0)
                - reasoning: str (왜 이 요소를 클릭해야 하는지 설명)
        """
        prompt = f"""You are trying to find: "{target_description}"

But it's NOT visible in the current screenshot.

**Task**: Find a button/tab/trigger that might reveal the target element when clicked.

**Common patterns to look for:**
1. **Tab buttons** - Often at top of sections with labels like "로그인", "회원가입", "Settings", etc.
2. **Modal/Dialog triggers** - Buttons that open popups (often have text like "Open", "Show", "View Details")
3. **Dropdown buttons** - Select boxes, combo boxes with arrows
4. **Accordion/Expand buttons** - Sections that can be expanded (▶, ▼ icons or "More", "Expand" text)

**Examples:**
- Target: "이름 입력" → Look for "회원가입" or "Sign Up" tab
- Target: "장바구니 수량 조절" → Look for "장바구니 보기" or "Cart" button
- Target: "설정 옵션" → Look for "설정" or "Settings" button

**CRITICAL: Respond with ONLY valid JSON. No markdown.**

{{
  "found_exploreable": true,
  "x": <center x coordinate of button/tab to click>,
  "y": <center y coordinate of button/tab to click>,
  "element_type": "tab" | "modal" | "dropdown" | "accordion",
  "element_text": "<visible text on the button/tab>",
  "confidence": <0.0 to 1.0>,
  "reasoning": "<why clicking this might reveal the target>"
}}

If you don't see any reasonable button/tab to explore, set:
{{
  "found_exploreable": false,
  "confidence": 0.0,
  "reasoning": "No exploreable elements found"
}}

**JSON ONLY (no markdown):**"""

        try:
            response_text = self.analyze_with_vision(prompt, screenshot_base64)

            # Parse JSON response
            result = json.loads(response_text.strip())

            # Validate response structure
            if "found_exploreable" not in result:
                return {
                    "found_exploreable": False,
                    "confidence": 0.0,
                    "reasoning": "Invalid response structure"
                }

            return result

        except json.JSONDecodeError as e:
            print(f"LLM exploreable element detection failed (JSON parse error): {e}")
            print(f"Response was: {response_text[:200]}")
            return {
                "found_exploreable": False,
                "confidence": 0.0,
                "reasoning": f"JSON parse error: {e}"
            }
        except Exception as e:
            print(f"LLM exploreable element detection failed: {e}")
            return {
                "found_exploreable": False,
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

**Decision Rules (in priority order):**
1. **Confidence-first approach**: If one method has significantly higher confidence (5+ points difference), prefer it UNLESS there's strong evidence it's wrong
2. **Selector validity**: ARIA selectors like `[role="radio"]:has-text("text")` are ONLY valid if the text is actually INSIDE the role element. If the text is in a separate label, the selector won't work - prefer Semantic in this case
3. **Agreement bonus**: If 2+ methods agree on the same element → HIGH confidence boost (+10)
4. **Method reliability** (use as tiebreaker only):
   - ARIA: Best for custom components (React, Radix UI) IF the selector is valid
   - Semantic: Best for text-based matching, very reliable for buttons/links with visible text
   - Vision: Can hallucinate, use as last resort
5. **Single method**: If only 1 method succeeded → use it but keep original confidence

**CRITICAL: Practical validation**
- ARIA `[role="X"]:has-text("Y")` requires Y to be INSIDE the role element (not in a sibling label)
- If ARIA and Semantic both found the element but with different selectors, check if the text is actually in the ARIA element
- When in doubt between ARIA and Semantic, prefer the one with higher confidence

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

            # Debug: Print raw LLM response
            print(f"[Aggregator] Raw LLM response: {response_text[:300]}")

            # Strip markdown
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            if not response_text:
                print("[Aggregator] LLM returned empty response after stripping, using vision result")
                return vision_result

            try:
                result = json.loads(response_text)
                print(f"[Aggregator] ✓ Successfully parsed JSON: {result.get('selector', 'N/A')} (conf: {result.get('confidence', 0)})")
                return result
            except json.JSONDecodeError as e:
                print(f"[Aggregator] JSON parse failed: {e}")
                print(f"[Aggregator] Response text was: {response_text[:300]}")
                return vision_result

        except Exception as e:
            print(f"[Aggregator] Failed: {e}, using vision result")
            return vision_result

    def verify_scenario_outcome(
        self,
        scenario_description: str,
        final_screenshot: str,
        url: str,
    ) -> Dict[str, Any]:
        """
        Verify if the final screenshot matches the scenario description.

        This is a Master Orchestrator-level verification that checks:
        "Does this final state match what the scenario intended to achieve?"

        Unlike verify_scenario_success() which compares before/after and looks for
        specific indicators, this method takes a holistic view and asks:
        "Is the natural language scenario description reflected in this screenshot?"

        Args:
            scenario_description: Natural language description of the scenario
                                 (e.g., "사용자가 로그인할 수 있다")
            final_screenshot: Screenshot of the final state after scenario execution
            url: Current page URL

        Returns:
            Dict with:
                - matches: Boolean indicating if screenshot matches scenario
                - confidence: Confidence score (0-100)
                - reasoning: Detailed explanation of why it matches/doesn't match
                - observations: List of key observations from the screenshot
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
2. **Consider semantic equivalence**:
   - "로그인할 수 있다" → Look for signs user is logged in (profile, logout button, welcome message)
   - "장바구니에 추가" → Look for cart count increase, success message, or item in cart
   - "검색할 수 있다" → Look for search results, result count, relevant items
3. **UI State Changes** are valid evidence:
   - Modal opened/closed
   - New content displayed
   - Navigation occurred
   - Form submitted/cleared
   - Items added/removed from lists
4. **Error states** are important:
   - If error message visible → scenario likely failed
   - If stuck on same page without change → scenario likely failed
5. **Success indicators** to look for:
   - Confirmation messages (toasts, alerts, banners)
   - UI state changes consistent with scenario goal
   - New content/page reflecting the intended action
   - Expected elements visible (buttons, forms, data)

**Important Notes:**
- Some success messages may be temporary (toasts) - look for OTHER evidence
- Korean and English messages are both valid
- Focus on OUTCOME, not the exact steps taken

Required JSON format (no markdown):
{{
    "matches": true,
    "confidence": 85,
    "reasoning": "detailed explanation of why the screenshot matches or doesn't match the scenario description",
    "observations": ["key observation 1", "key observation 2", "key observation 3"]
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
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{final_screenshot}"
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
                print("LLM scenario outcome verification returned empty response")
                return {
                    "matches": False,
                    "confidence": 0,
                    "reasoning": "LLM returned empty response",
                    "observations": []
                }

            # Try to parse JSON
            try:
                result = json.loads(response_text)
                # Ensure required fields exist
                if "observations" not in result:
                    result["observations"] = []
                return result
            except json.JSONDecodeError as json_err:
                print(f"LLM scenario outcome verification returned non-JSON response: {response_text[:200]}")
                return {
                    "matches": False,
                    "confidence": 0,
                    "reasoning": f"LLM returned non-JSON: {response_text[:100]}",
                    "observations": []
                }

        except Exception as e:
            print(f"LLM scenario outcome verification failed: {e}")
            return {
                "matches": False,
                "confidence": 0,
                "reasoning": f"Verification failed: {e}",
                "observations": []
            }


    def analyze_action_failure(
        self,
        action: str,
        selector: str,
        error_message: str,
        screenshot_base64: str,
        dom_elements: List[DomElement],
        url: str,
        step_description: str = "",
    ) -> Dict[str, Any]:
        """
        Analyze why an action failed and suggest recovery strategies.

        This method implements dynamic execution by understanding failure context
        and suggesting concrete fixes like closing overlays, scrolling, or using JavaScript.

        Args:
            action: The action that failed (click, fill, press, etc.)
            selector: The selector that was used
            error_message: The error message from Playwright
            screenshot_base64: Screenshot showing current state
            dom_elements: Available DOM elements
            url: Current page URL
            step_description: Human-readable description of what the step is trying to do

        Returns:
            Dict with:
                - failure_reason: Why the action failed (overlay, not_visible, invalid_selector, etc.)
                - suggested_fixes: List of fix strategies to try
                - confidence: Confidence in the analysis (0-100)
                - reasoning: Detailed explanation
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

            # Detect modals, dialogs, dropdowns, popovers
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

        # Add step description context if available
        description_context = ""
        if step_description:
            description_context = f"\n**Step Description:** {step_description}\n(Use this to understand what the action is trying to accomplish - e.g., if description says '이름 입력' (name input), look for name input field in the user registration section, not search field)\n"

        prompt = f"""You are a test automation expert analyzing why an action failed.

**Failed Action:** {action}
**Selector:** {selector}
**Error Message:** {error_message}
**Page URL:** {url}
{description_context}{overlay_context}
**Available DOM Elements (top 50):**
{dom_json}

**Common Failure Patterns:**

1. **Overlay/Modal Interception**
   - Error contains: "intercepts pointer events", "covered by", "not clickable"
   - Fix: Close overlay with Escape key, click backdrop, or wait for it to disappear
   - Example fixes:
     * {{"type": "close_overlay", "method": "press_escape", "description": "Press Escape to close modal/dropdown"}}
     * {{"type": "close_overlay", "method": "click_backdrop", "description": "Click outside modal to close it"}}

2. **Element Not Visible**
   - Error contains: "not visible", "hidden", "outside viewport"
   - Fix: Scroll element into view, wait for animation, or check if it's in a closed dropdown/tab
   - Example fixes:
     * {{"type": "scroll", "method": "scroll_into_view", "selector": "{selector}", "description": "Scroll element into viewport"}}
     * {{"type": "wait", "duration": 500, "description": "Wait for animation to complete"}}
     * {{"type": "open_container", "method": "click_parent", "description": "Open parent dropdown/accordion"}}

3. **Invalid/Ambiguous Selector**
   - Error contains: "failed", "no element", "multiple elements"
   - Fix: Use JavaScript to find element by text, use more specific selector, or try alternative approach
   - Example fixes:
     * {{"type": "javascript", "script": "document.querySelector('button').click()", "description": "Direct JavaScript click"}}
     * {{"type": "use_alternative_selector", "selector": "alternative selector", "description": "Try different selector"}}

4. **Timing Issues**
   - Error contains: "timeout", "detached", "stale"
   - Fix: Wait for element to be stable, retry after delay
   - Example fixes:
     * {{"type": "wait", "duration": 1000, "description": "Wait for element to stabilize"}}
     * {{"type": "retry", "delay": 500, "description": "Retry action after delay"}}

**Your Task:**
1. Analyze the error message and screenshot
2. Identify the most likely failure reason
3. Suggest 1-3 concrete fixes to try (in priority order)
4. Each fix should be actionable and specific

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
            response = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=1024,
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
                                    "url": f"data:image/png;base64,{screenshot_base64}"
                                }
                            }
                        ]
                    }
                ]
            )

            response_text = response.choices[0].message.content or ""

            # Parse JSON
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            if not response_text:
                print("LLM error analysis returned empty response")
                return {
                    "failure_reason": "unknown",
                    "suggested_fixes": [],
                    "confidence": 0,
                    "reasoning": "LLM returned empty response"
                }

            try:
                result = json.loads(response_text)
                if "suggested_fixes" not in result:
                    result["suggested_fixes"] = []
                return result
            except json.JSONDecodeError:
                print(f"LLM error analysis returned non-JSON: {response_text[:200]}")
                return {
                    "failure_reason": "unknown",
                    "suggested_fixes": [],
                    "confidence": 0,
                    "reasoning": f"LLM returned non-JSON: {response_text[:100]}"
                }

        except Exception as e:
            print(f"LLM error analysis failed: {e}")
            return {
                "failure_reason": "unknown",
                "suggested_fixes": [],
                "confidence": 0,
                "reasoning": f"Analysis failed: {e}"
            }


def get_vision_client():
    """
    Factory function to get the appropriate vision client.

    Priority:
      1) GAIA_LLM_PROVIDER (set by gaia CLI launcher)
      2) VISION_PROVIDER (manual override)
      3) .env fallback
    """
    env_file = Path(__file__).parent.parent.parent.parent / ".env"
    provider = (
        os.getenv("GAIA_LLM_PROVIDER")
        or os.getenv("VISION_PROVIDER")
        or ""
    ).strip()

    if not provider and env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GAIA_LLM_PROVIDER="):
                    provider = line.split("=", 1)[1].strip()
                    break
                if line.startswith("VISION_PROVIDER="):
                    provider = line.split("=", 1)[1].strip()
                    break

    if provider.lower() == "gemini":
        from gaia.src.phase4.llm_vision_client_gemini import GeminiVisionClient
        return GeminiVisionClient()
    else:
        return LLMVisionClient()


__all__ = ["LLMVisionClient", "get_vision_client"]
