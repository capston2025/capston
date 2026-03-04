"""
GAIA CAPTCHA Solver — 테스트 실행 중 CAPTCHA 자동 감지/해결 모듈

통합 포인트: goal_driven/agent.py의 execute_goal() 메인 루프에서
스크린샷 캡처 후 LLM 결정 전에 CAPTCHA를 감지하고 해결합니다.

해결 전략:
  - 단순 텍스트/슬라이더 → ddddocr (로컬 OCR, 빠름)
  - 이미지 선택형 (hCaptcha, reCAPTCHA) → GPT Vision (기존 OAuth 인증 활용)
  - 실패 시 → 새 문제 요청 후 재시도 (최대 max_attempts)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 데이터 클래스
# ---------------------------------------------------------------------------

@dataclass
class CaptchaDetectionResult:
    detected: bool = False
    captcha_type: str = "none"
    confidence: int = 0
    reasoning: str = ""


@dataclass
class CaptchaSolution:
    instruction: str = ""
    selected_cells: list[int] = field(default_factory=list)
    grid_size: dict[str, int] = field(default_factory=lambda: {"rows": 0, "cols": 0})
    text: str = ""
    cell_descriptions: dict[str, str] = field(default_factory=dict)
    confidence: int = 0
    reasoning: str = ""


@dataclass
class CaptchaAction:
    action: str = ""       # "click", "type", "submit", "wait", "request_new_challenge"
    target: str = ""       # selector 또는 설명
    cell_index: int = 0    # 셀 번호 (1-based)
    value: str = ""        # type 액션의 입력값
    duration_ms: int = 0   # wait 액션의 대기 시간


@dataclass
class CaptchaStepResult:
    solved: bool = False
    status: str = "pending"   # "solved", "new_challenge", "failed", "gave_up"
    attempts: int = 0
    reasoning: str = ""


# ---------------------------------------------------------------------------
# ddddocr lazy init
# ---------------------------------------------------------------------------

_ocr = None
_det = None
_slide = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        import ddddocr
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def _get_det():
    global _det
    if _det is None:
        import ddddocr
        _det = ddddocr.DdddOcr(det=True, show_ad=False)
    return _det


def _get_slide():
    global _slide
    if _slide is None:
        import ddddocr
        _slide = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
    return _slide


# ---------------------------------------------------------------------------
# CaptchaSolver 클래스
# ---------------------------------------------------------------------------

class CaptchaSolver:
    """GAIA 테스트 파이프라인에서 CAPTCHA를 감지하고 해결하는 솔버.

    사용법 (agent.py execute_goal 루프 내부):
        solver = CaptchaSolver(vision_client=self.llm, execute_fn=self._execute_action)
        ...
        screenshot = self._capture_screenshot()
        captcha_result = solver.detect_and_handle(
            screenshot=screenshot,
            page_url=current_url,
            capture_fn=self._capture_screenshot,
        )
        if captcha_result.solved or captcha_result.status == "gave_up":
            continue  # DOM 재수집 후 다음 스텝
    """

    def __init__(
        self,
        vision_client: Any,
        execute_fn: Callable[..., Any],
        mcp_host_url: str = "",
        session_id: str = "",
        max_attempts: int = 5,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Args:
            vision_client: LLMVisionClient 인스턴스 (analyze_with_vision 메서드 필요)
            execute_fn: MCP Host 액션 실행 함수 (agent._execute_action)
            mcp_host_url: MCP Host URL (스크린샷 등 직접 호출 시)
            session_id: MCP 세션 ID
            max_attempts: CAPTCHA 당 최대 시도 횟수
            log_fn: 로그 출력 함수 (agent._log)
        """
        self._vision = vision_client
        self._execute = execute_fn
        self._mcp_host_url = mcp_host_url
        self._session_id = session_id
        self._max_attempts = max_attempts
        self._log = log_fn or (lambda msg: logger.info(msg))
        try:
            self._sleep_scale = float(os.getenv("GAIA_CAPTCHA_SLEEP_SCALE", "0.35") or 0.35)
        except Exception:
            self._sleep_scale = 0.35
        self._sleep_scale = max(0.05, min(self._sleep_scale, 1.0))
        try:
            self._max_total_seconds = float(os.getenv("GAIA_CAPTCHA_MAX_TOTAL_SECONDS", "8.0") or 8.0)
        except Exception:
            self._max_total_seconds = 8.0
        self._max_total_seconds = max(2.0, min(self._max_total_seconds, 30.0))

    def _sleep(self, base_seconds: float) -> None:
        try:
            seconds = max(0.0, float(base_seconds) * self._sleep_scale)
        except Exception:
            seconds = 0.0
        if seconds > 0:
            time.sleep(seconds)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def detect_captcha(self, screenshot_b64: str, page_url: str = "") -> CaptchaDetectionResult:
        """스크린샷에서 CAPTCHA가 있는지 감지한다."""
        prompt = f"""Analyze this screenshot. Is there a CAPTCHA challenge visible?

Page URL: {page_url or "unknown"}

Look for:
- hCaptcha widget (grid of images with a question)
- reCAPTCHA v2 (checkbox "I'm not a robot" or image grid)
- Cloudflare Turnstile challenge
- Text/image CAPTCHA input fields
- Slider puzzle CAPTCHA

IMPORTANT: Only report CAPTCHA if it is CLEARLY visible and blocking page interaction.
Do NOT report normal login forms or buttons as CAPTCHA.

Required JSON format (no markdown):
{{
  "detected": true,
  "captcha_type": "hcaptcha" | "recaptcha_v2" | "recaptcha_v3" | "text" | "slider" | "cloudflare_turnstile" | "none",
  "confidence": 85,
  "reasoning": "brief explanation"
}}

JSON response:"""

        try:
            resp = self._vision.analyze_with_vision(prompt, screenshot_b64)
            data = json.loads(resp)
            return CaptchaDetectionResult(
                detected=bool(data.get("detected", False)),
                captcha_type=str(data.get("captcha_type", "none")),
                confidence=int(data.get("confidence", 0)),
                reasoning=str(data.get("reasoning", "")),
            )
        except Exception as exc:
            self._log(f"⚠️ CAPTCHA 감지 실패: {exc}")
            return CaptchaDetectionResult()

    def detect_and_handle(
        self,
        screenshot: str,
        page_url: str = "",
        capture_fn: Optional[Callable[[], Optional[str]]] = None,
    ) -> CaptchaStepResult:
        """CAPTCHA를 감지하고, 있으면 해결을 시도한다.

        Args:
            screenshot: 현재 페이지 스크린샷 (base64)
            page_url: 현재 페이지 URL
            capture_fn: 스크린샷 재촬영 함수 (재시도 시 사용)
        Returns:
            CaptchaStepResult
        """
        detection = self.detect_captcha(screenshot, page_url)
        if not detection.detected:
            return CaptchaStepResult(status="no_captcha")

        self._log(f"🔒 CAPTCHA 감지됨: {detection.captcha_type} (confidence: {detection.confidence})")

        if detection.captcha_type == "text":
            return self._handle_text_captcha(screenshot, page_url)
        elif detection.captcha_type == "slider":
            return self._handle_slider_captcha(screenshot, page_url)
        elif detection.captcha_type in ("hcaptcha", "recaptcha_v2", "image_selection"):
            return self._handle_image_selection_captcha(
                screenshot=screenshot,
                page_url=page_url,
                captcha_type=detection.captcha_type,
                capture_fn=capture_fn,
            )
        elif detection.captcha_type == "cloudflare_turnstile":
            return self._handle_turnstile(page_url)
        else:
            self._log(f"⚠️ 지원하지 않는 CAPTCHA 타입: {detection.captcha_type}")
            return CaptchaStepResult(status="unsupported", reasoning=detection.captcha_type)

    # ------------------------------------------------------------------
    # 이미지 선택형 CAPTCHA (hCaptcha, reCAPTCHA v2)
    # ------------------------------------------------------------------

    def _handle_image_selection_captcha(
        self,
        screenshot: str,
        page_url: str,
        captcha_type: str,
        capture_fn: Optional[Callable[[], Optional[str]]] = None,
    ) -> CaptchaStepResult:
        """이미지 선택형 CAPTCHA를 다단계로 처리한다."""
        previous_instruction = ""
        started_at = time.time()

        for attempt in range(1, self._max_attempts + 1):
            if (time.time() - started_at) >= self._max_total_seconds:
                self._log(
                    f"⏱️ CAPTCHA 처리 시간 예산 초과({self._max_total_seconds:.1f}s). 조기 종료합니다."
                )
                return CaptchaStepResult(
                    status="gave_up",
                    attempts=max(1, attempt - 1),
                    reasoning=f"timeout_budget_exceeded:{self._max_total_seconds:.1f}s",
                )
            self._log(f"🧩 CAPTCHA 시도 {attempt}/{self._max_attempts}")

            # 1. 현재 스크린샷 분석
            solution = self._analyze_image_captcha(screenshot, captcha_type)
            if not solution.selected_cells:
                self._log(f"⚠️ 선택할 셀이 없음: {solution.reasoning}")
                # 제출만 시도
                self._click_verify_button(page_url)
            else:
                self._log(
                    f"🖱️ 셀 선택: {solution.selected_cells} "
                    f"(질문: {solution.instruction}, confidence: {solution.confidence})"
                )
                previous_instruction = solution.instruction

                # 2. 셀 클릭
                for cell_idx in solution.selected_cells:
                    self._click_captcha_cell(cell_idx, solution.grid_size)
                    self._sleep(0.3)

                # 3. 제출 버튼 클릭
                self._sleep(0.5)
                self._click_verify_button(page_url)

            # 4. 결과 확인 (1.5초 대기 후 스크린샷)
            self._sleep(1.5)
            before_screenshot = screenshot
            new_screenshot = capture_fn() if capture_fn else None
            if not new_screenshot:
                return CaptchaStepResult(
                    status="failed", attempts=attempt,
                    reasoning="스크린샷 재촬영 실패",
                )

            # 5. 상태 판단
            verify = self._verify_captcha_result(before_screenshot, new_screenshot, page_url)
            status = verify.get("status", "failed")

            if status == "solved":
                self._log("✅ CAPTCHA 해결 성공!")
                return CaptchaStepResult(solved=True, status="solved", attempts=attempt)

            elif status == "new_challenge":
                self._log("🔄 새 이미지가 로드됨, 재분석...")
                screenshot = new_screenshot

                # 다단계 처리: 새로 로드된 셀 분석
                refresh_result = self._analyze_refreshed_captcha(
                    new_screenshot, previous_instruction, attempt,
                )
                if refresh_result.get("selected_cells"):
                    for cell_idx in refresh_result["selected_cells"]:
                        self._click_captcha_cell(cell_idx, refresh_result.get("grid_size", {"rows": 3, "cols": 3}))
                        self._sleep(0.3)

                    if refresh_result.get("should_submit", True):
                        self._sleep(0.5)
                        self._click_verify_button(page_url)
                        self._sleep(1.5)

                        # 다시 확인
                        final_screenshot = capture_fn() if capture_fn else None
                        if final_screenshot:
                            final_verify = self._verify_captcha_result(new_screenshot, final_screenshot, page_url)
                            if final_verify.get("status") == "solved":
                                self._log("✅ CAPTCHA 해결 성공! (다단계)")
                                return CaptchaStepResult(solved=True, status="solved", attempts=attempt)
                            screenshot = final_screenshot
                else:
                    # 새 셀이 없으면 제출
                    self._click_verify_button(page_url)
                    self._sleep(1.5)
                    screenshot = capture_fn() if capture_fn else new_screenshot

            elif status == "failed":
                self._log(f"❌ CAPTCHA 실패 (시도 {attempt}): {verify.get('reasoning', '')}")
                if attempt < self._max_attempts:
                    # 새 문제 요청
                    self._request_new_challenge(page_url)
                    self._sleep(2)
                    screenshot = capture_fn() if capture_fn else new_screenshot
                continue

        self._log(f"🏳️ CAPTCHA 해결 포기 ({self._max_attempts}회 시도)")
        return CaptchaStepResult(
            status="gave_up", attempts=self._max_attempts,
            reasoning=f"{self._max_attempts}회 시도 후 실패",
        )

    # ------------------------------------------------------------------
    # 텍스트 CAPTCHA
    # ------------------------------------------------------------------

    def _handle_text_captcha(self, screenshot: str, page_url: str) -> CaptchaStepResult:
        """텍스트 CAPTCHA 처리: ddddocr → GPT Vision 폴백."""
        started_at = time.time()
        for attempt in range(1, self._max_attempts + 1):
            if (time.time() - started_at) >= self._max_total_seconds:
                return CaptchaStepResult(
                    status="gave_up",
                    attempts=max(1, attempt - 1),
                    reasoning=f"timeout_budget_exceeded:{self._max_total_seconds:.1f}s",
                )
            # ddddocr 먼저 시도
            text = self._ocr_text(screenshot)
            if not text:
                # GPT Vision 폴백
                text = self._vision_ocr_text(screenshot)

            if not text:
                self._log(f"⚠️ 텍스트 인식 실패 (시도 {attempt})")
                continue

            self._log(f"📝 인식된 텍스트: {text}")
            self._type_captcha_text(text)
            self._sleep(0.5)
            self._click_verify_button(page_url)
            self._sleep(1.5)
            return CaptchaStepResult(solved=True, status="solved", attempts=attempt)

        return CaptchaStepResult(status="gave_up", attempts=self._max_attempts)

    # ------------------------------------------------------------------
    # 슬라이더 CAPTCHA
    # ------------------------------------------------------------------

    def _handle_slider_captcha(self, screenshot: str, page_url: str) -> CaptchaStepResult:
        """슬라이더 CAPTCHA: GPT Vision으로 위치 분석."""
        self._log("🎚️ 슬라이더 CAPTCHA 감지")
        # 슬라이더는 정확한 이미지 분리가 필요해서 GPT Vision으로 위치 안내
        prompt = """Analyze this slider CAPTCHA. Find:
1. The slider handle position (drag start point)
2. The target gap/slot position (drag end point)
3. The drag distance in pixels

Required JSON format (no markdown):
{
  "start_x": 50, "start_y": 300,
  "end_x": 250, "end_y": 300,
  "drag_distance": 200,
  "confidence": 80,
  "reasoning": "explanation"
}

JSON response:"""

        try:
            resp = self._vision.analyze_with_vision(prompt, screenshot)
            data = json.loads(resp)
            self._log(f"🎚️ 슬라이더: {data.get('drag_distance', 0)}px 이동 필요")
            # 실제 드래그 실행은 MCP Host의 drag 기능 사용
            self._execute(
                "evaluate",
                script=f"""
                    const slider = document.querySelector('.slider-handle, [class*=slider], [role=slider]');
                    if (slider) {{
                        const rect = slider.getBoundingClientRect();
                        const startX = rect.left + rect.width / 2;
                        const startY = rect.top + rect.height / 2;
                        const endX = startX + {data.get('drag_distance', 200)};
                        // Dispatch mouse events
                        slider.dispatchEvent(new MouseEvent('mousedown', {{clientX: startX, clientY: startY, bubbles: true}}));
                        slider.dispatchEvent(new MouseEvent('mousemove', {{clientX: endX, clientY: startY, bubbles: true}}));
                        slider.dispatchEvent(new MouseEvent('mouseup', {{clientX: endX, clientY: startY, bubbles: true}}));
                    }}
                """,
            )
            self._sleep(1)
            return CaptchaStepResult(solved=True, status="solved", attempts=1)
        except Exception as exc:
            self._log(f"⚠️ 슬라이더 CAPTCHA 실패: {exc}")
            return CaptchaStepResult(status="failed", attempts=1, reasoning=str(exc))

    # ------------------------------------------------------------------
    # Cloudflare Turnstile
    # ------------------------------------------------------------------

    def _handle_turnstile(self, page_url: str) -> CaptchaStepResult:
        """Cloudflare Turnstile: 체크박스 클릭 후 대기."""
        self._log("☁️ Cloudflare Turnstile 감지 — 체크박스 클릭 시도")
        try:
            self._execute(
                "evaluate",
                script="""
                    const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                    if (iframe) { iframe.contentDocument?.querySelector('[type=checkbox]')?.click(); }
                """,
            )
            self._sleep(3)
            return CaptchaStepResult(solved=True, status="solved", attempts=1)
        except Exception as exc:
            self._log(f"⚠️ Turnstile 실패: {exc}")
            return CaptchaStepResult(status="failed", attempts=1, reasoning=str(exc))

    # ------------------------------------------------------------------
    # Vision 분석 헬퍼
    # ------------------------------------------------------------------

    def _analyze_image_captcha(self, screenshot_b64: str, captcha_type: str) -> CaptchaSolution:
        """이미지 선택형 CAPTCHA를 GPT Vision으로 분석."""
        prompt = f"""You are solving a {captcha_type} image selection CAPTCHA.

Analyze this screenshot:
1. Read the instruction/question EXACTLY
2. Identify the grid layout (3x3, 4x4, etc.)
3. Describe what is in EACH cell
4. Select cells that match the instruction

Cell numbering (left-to-right, top-to-bottom, 1-based):
3x3: [1][2][3] / [4][5][6] / [7][8][9]
4x4: [1][2][3][4] / [5][6][7][8] / [9][10][11][12] / [13][14][15][16]

CRITICAL for unusual objects (hCaptcha speciality):
- "pipe mouse" = a mouse made of pipes, literally
- "worm" = actual worms, gummy worms, or worm-like shapes
- Take the instruction LITERALLY

When unsure, lean toward INCLUDING the cell.

Required JSON (no markdown):
{{
  "instruction": "the CAPTCHA question",
  "selected_cells": [1, 4, 7],
  "grid_size": {{"rows": 3, "cols": 3}},
  "cell_descriptions": {{"1": "content", "2": "content"}},
  "confidence": 75,
  "reasoning": "explanation"
}}

JSON response:"""

        try:
            resp = self._vision.analyze_with_vision(prompt, screenshot_b64)
            data = json.loads(resp)
            return CaptchaSolution(
                instruction=str(data.get("instruction", "")),
                selected_cells=list(data.get("selected_cells", [])),
                grid_size=data.get("grid_size", {"rows": 3, "cols": 3}),
                cell_descriptions=data.get("cell_descriptions", {}),
                confidence=int(data.get("confidence", 0)),
                reasoning=str(data.get("reasoning", "")),
            )
        except Exception as exc:
            return CaptchaSolution(reasoning=f"분석 실패: {exc}")

    def _analyze_refreshed_captcha(
        self, screenshot_b64: str, previous_instruction: str, attempt: int,
    ) -> dict[str, Any]:
        """셀이 교체된 후 재분석."""
        prompt = f"""A CAPTCHA challenge has refreshed some cells after your previous selection.

Previous instruction: "{previous_instruction}"
Attempt: {attempt}

Check if:
1. The instruction is the SAME
2. Which cells have NEW images (they look different/fresher)
3. Do any NEW cells match the instruction?

If new cells match → select them
If NO new cells match → submit with empty selection

Required JSON (no markdown):
{{
  "instruction": "current instruction",
  "selected_cells": [3, 6],
  "grid_size": {{"rows": 3, "cols": 3}},
  "should_submit": true,
  "confidence": 70,
  "reasoning": "explanation"
}}

JSON response:"""

        try:
            resp = self._vision.analyze_with_vision(prompt, screenshot_b64)
            return json.loads(resp)
        except Exception:
            return {"selected_cells": [], "should_submit": True, "grid_size": {"rows": 3, "cols": 3}}

    def _verify_captcha_result(
        self, before_b64: str, after_b64: str, page_url: str,
    ) -> dict[str, Any]:
        """제출 후 CAPTCHA가 풀렸는지 확인."""
        prompt = f"""Compare BEFORE (first) and AFTER (second) screenshots.

Page URL: {page_url or "unknown"}

Determine the CAPTCHA status:
- "solved": CAPTCHA disappeared, page progressed
- "new_challenge": CAPTCHA still there but some cells got new images
- "failed": Error message visible or same state

Required JSON (no markdown):
{{
  "status": "solved" | "new_challenge" | "failed",
  "reasoning": "explanation"
}}

JSON response:"""

        try:
            response = self._vision.client.chat.completions.create(
                model=self._vision.model,
                max_completion_tokens=512,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{before_b64}"}},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{after_b64}"}},
                    ],
                }],
            )
            resp_text = self._vision._strip_code_fences(self._vision._response_text(response))
            return json.loads(resp_text)
        except Exception as exc:
            return {"status": "failed", "reasoning": str(exc)}

    def _ocr_text(self, screenshot_b64: str) -> str:
        """ddddocr로 텍스트 인식."""
        try:
            img_bytes = base64.b64decode(screenshot_b64)
            return _get_ocr().classification(img_bytes) or ""
        except Exception:
            return ""

    def _vision_ocr_text(self, screenshot_b64: str) -> str:
        """GPT Vision으로 텍스트 인식."""
        prompt = """Read the distorted text in this CAPTCHA image.
Characters are typically alphanumeric (a-z, A-Z, 0-9).
Required JSON (no markdown): {"text": "recognized text"}
JSON response:"""

        try:
            resp = self._vision.analyze_with_vision(prompt, screenshot_b64)
            return json.loads(resp).get("text", "")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 브라우저 액션 헬퍼
    # ------------------------------------------------------------------

    def _click_captcha_cell(self, cell_index: int, grid_size: dict[str, int]) -> None:
        """CAPTCHA 그리드의 특정 셀을 JavaScript로 클릭."""
        rows = grid_size.get("rows", 3)
        cols = grid_size.get("cols", 3)
        row = (cell_index - 1) // cols
        col = (cell_index - 1) % cols

        # hCaptcha/reCAPTCHA iframe 내부 클릭
        script = f"""
        (function() {{
            // hCaptcha 셀 클릭 시도
            const hcaptchaIframe = document.querySelector('iframe[src*="hcaptcha.com/captcha"]');
            if (hcaptchaIframe && hcaptchaIframe.contentDocument) {{
                const cells = hcaptchaIframe.contentDocument.querySelectorAll('.task-image, .image-wrapper, [class*=cell]');
                if (cells.length > 0) {{
                    const idx = {cell_index - 1};
                    if (idx < cells.length) {{ cells[idx].click(); return 'hcaptcha:' + idx; }}
                }}
            }}

            // reCAPTCHA 셀 클릭 시도
            const recaptchaIframe = document.querySelector('iframe[src*="recaptcha"]');
            if (recaptchaIframe && recaptchaIframe.contentDocument) {{
                const cells = recaptchaIframe.contentDocument.querySelectorAll('.rc-imageselect-tile, td[role=button]');
                if (cells.length > 0) {{
                    const idx = {cell_index - 1};
                    if (idx < cells.length) {{ cells[idx].click(); return 'recaptcha:' + idx; }}
                }}
            }}

            // 일반 CAPTCHA 그리드 (iframe 외부)
            const allCells = document.querySelectorAll(
                '.captcha-cell, .captcha-image, [class*=captcha] img, ' +
                '.task-image, .image-wrapper'
            );
            if (allCells.length > 0) {{
                const idx = {cell_index - 1};
                if (idx < allCells.length) {{ allCells[idx].click(); return 'direct:' + idx; }}
            }}

            // 좌표 기반 클릭 (fallback)
            const captchaContainer = document.querySelector(
                '[class*=captcha], [id*=captcha], [class*=hcaptcha], [id*=hcaptcha]'
            );
            if (captchaContainer) {{
                const rect = captchaContainer.getBoundingClientRect();
                const cellW = rect.width / {cols};
                const cellH = rect.height / {rows};
                const x = rect.left + ({col} * cellW) + (cellW / 2);
                const y = rect.top + ({row} * cellH) + (cellH / 2);
                const el = document.elementFromPoint(x, y);
                if (el) {{ el.click(); return 'coord:' + x + ',' + y; }}
            }}

            return 'no_target_found';
        }})();
        """
        try:
            self._execute("evaluate", script=script)
        except Exception as exc:
            self._log(f"⚠️ 셀 {cell_index} 클릭 실패: {exc}")

    def _click_verify_button(self, page_url: str = "") -> None:
        """CAPTCHA 제출/확인 버튼 클릭."""
        script = """
        (function() {
            // hCaptcha 확인 버튼
            const hBtn = document.querySelector(
                '.button-submit, [class*=submit], [data-action=submit]'
            );
            if (hBtn) { hBtn.click(); return 'direct_submit'; }

            // iframe 내부 확인 버튼
            const iframes = document.querySelectorAll('iframe[src*="hcaptcha"], iframe[src*="recaptcha"]');
            for (const iframe of iframes) {
                try {
                    const btn = iframe.contentDocument?.querySelector(
                        '.button-submit, #recaptcha-verify-button, [class*=verify], [class*=submit]'
                    );
                    if (btn) { btn.click(); return 'iframe_submit'; }
                } catch(e) {}
            }

            // 일반 verify 버튼
            const verifyBtns = document.querySelectorAll('button, [role=button]');
            for (const btn of verifyBtns) {
                const text = (btn.textContent || '').toLowerCase();
                if (text.includes('verify') || text.includes('submit') ||
                    text.includes('확인') || text.includes('검증')) {
                    btn.click();
                    return 'text_match_submit';
                }
            }
            return 'no_submit_found';
        })();
        """
        try:
            self._execute("evaluate", script=script)
        except Exception as exc:
            self._log(f"⚠️ 제출 버튼 클릭 실패: {exc}")

    def _type_captcha_text(self, text: str) -> None:
        """텍스트 CAPTCHA 입력."""
        script = f"""
        (function() {{
            const input = document.querySelector(
                'input[name*=captcha], input[id*=captcha], ' +
                'input[class*=captcha], input[placeholder*=captcha], ' +
                'input[placeholder*=code], input[name*=code]'
            );
            if (input) {{
                input.focus();
                input.value = '{text}';
                input.dispatchEvent(new Event('input', {{bubbles: true}}));
                input.dispatchEvent(new Event('change', {{bubbles: true}}));
                return 'typed';
            }}
            return 'no_input_found';
        }})();
        """
        try:
            self._execute("evaluate", script=script)
        except Exception as exc:
            self._log(f"⚠️ 텍스트 입력 실패: {exc}")

    def _request_new_challenge(self, page_url: str = "") -> None:
        """새 CAPTCHA 문제 요청 (리프레시 버튼 클릭)."""
        script = """
        (function() {
            // hCaptcha 새로고침
            const refreshBtns = document.querySelectorAll(
                '[class*=refresh], [aria-label*=refresh], [aria-label*=new], ' +
                '[title*=refresh], [title*=new], .refresh-button'
            );
            for (const btn of refreshBtns) {
                btn.click();
                return 'refreshed';
            }

            // iframe 내부
            const iframes = document.querySelectorAll('iframe[src*="hcaptcha"], iframe[src*="recaptcha"]');
            for (const iframe of iframes) {
                try {
                    const btn = iframe.contentDocument?.querySelector(
                        '#recaptcha-reload-button, [class*=refresh], [class*=reload]'
                    );
                    if (btn) { btn.click(); return 'iframe_refreshed'; }
                } catch(e) {}
            }
            return 'no_refresh_found';
        })();
        """
        try:
            self._execute("evaluate", script=script)
            self._log("🔄 새 CAPTCHA 문제 요청됨")
        except Exception as exc:
            self._log(f"⚠️ 새 문제 요청 실패: {exc}")
