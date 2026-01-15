"""
Goal-Driven Agent

목표만 주면 AI가 알아서 DOM을 분석하고 다음 액션을 결정하여 실행
사전 정의된 스텝 없이 동적으로 테스트 수행
"""

from __future__ import annotations
import time
import json
import requests
from typing import Any, Dict, List, Optional, Callable

from .models import (
    TestGoal,
    ActionDecision,
    ActionType,
    GoalResult,
    StepResult,
    DOMElement,
)


class GoalDrivenAgent:
    """
    Goal-Driven 테스트 에이전트

    사용법:
        agent = GoalDrivenAgent(mcp_host_url="http://localhost:8000")
        result = agent.execute_goal(goal)
    """

    def __init__(
        self,
        mcp_host_url: str = "http://localhost:8000",
        gemini_api_key: Optional[str] = None,
        session_id: str = "goal_driven",
        log_callback: Optional[Callable[[str], None]] = None,
        screenshot_callback: Optional[Callable[[str], None]] = None,
    ):
        self.mcp_host_url = mcp_host_url
        self.session_id = session_id
        self._log_callback = log_callback
        self._screenshot_callback = screenshot_callback

        # Gemini 클라이언트 초기화
        from gaia.src.phase4.llm_vision_client_gemini import GeminiVisionClient
        self.llm = GeminiVisionClient(api_key=gemini_api_key)

        # 실행 기록
        self._action_history: List[str] = []

        # DOM 요소의 셀렉터 저장 (element_id -> selector)
        self._element_selectors: Dict[int, str] = {}

    def _log(self, message: str):
        """로그 출력"""
        print(f"[GoalAgent] {message}")
        if self._log_callback:
            self._log_callback(message)

    def execute_goal(self, goal: TestGoal) -> GoalResult:
        """
        목표를 달성할 때까지 실행

        1. DOM 분석
        2. LLM에게 다음 액션 결정 요청
        3. 액션 실행
        4. 목표 달성 여부 확인
        5. 반복
        """
        start_time = time.time()
        self._action_history = []
        steps: List[StepResult] = []

        self._log(f"🎯 목표 시작: {goal.name}")
        self._log(f"   설명: {goal.description}")
        self._log(f"   성공 조건: {goal.success_criteria}")

        # 시작 URL로 이동
        current_url = goal.start_url
        if goal.start_url:
            self._log(f"📍 시작 URL로 이동: {goal.start_url}")
            self._execute_action("goto", url=goal.start_url)
            time.sleep(2)  # 페이지 로드 대기

        step_count = 0

        while step_count < goal.max_steps:
            step_count += 1
            step_start = time.time()

            self._log(f"\n--- Step {step_count}/{goal.max_steps} ---")

            # 1. 현재 페이지 DOM 분석
            dom_elements = self._analyze_dom(url=current_url)
            if not dom_elements:
                self._log("⚠️ DOM 요소를 찾을 수 없음, 잠시 대기 후 재시도")
                time.sleep(1)
                dom_elements = self._analyze_dom()
                if not dom_elements:
                    continue

            self._log(f"📊 DOM 요소 {len(dom_elements)}개 발견")

            # 2. 스크린샷 캡처
            screenshot = self._capture_screenshot()

            # 3. LLM에게 다음 액션 결정 요청
            decision = self._decide_next_action(
                dom_elements=dom_elements,
                goal=goal,
                screenshot=screenshot,
            )

            self._log(f"🤖 LLM 결정: {decision.action.value} - {decision.reasoning}")

            # 4. 목표 달성 확인
            if decision.is_goal_achieved:
                self._log(f"✅ 목표 달성! 이유: {decision.goal_achievement_reason}")

                return GoalResult(
                    goal_id=goal.id,
                    goal_name=goal.name,
                    success=True,
                    steps_taken=steps,
                    total_steps=step_count,
                    final_reason=decision.goal_achievement_reason or "목표 달성됨",
                    duration_seconds=time.time() - start_time,
                )

            # 5. 액션 실행
            success, error = self._execute_decision(decision, dom_elements)

            step_result = StepResult(
                step_number=step_count,
                action=decision,
                success=success,
                error_message=error,
                duration_ms=int((time.time() - step_start) * 1000),
            )
            steps.append(step_result)

            if success:
                self._action_history.append(
                    f"Step {step_count}: {decision.action.value} - {decision.reasoning}"
                )
            else:
                self._log(f"⚠️ 액션 실패: {error}")

            # 다음 스텝 전 잠시 대기
            time.sleep(0.5)

        # max_steps 초과
        self._log(f"❌ 최대 스텝 수 초과 ({goal.max_steps})")

        return GoalResult(
            goal_id=goal.id,
            goal_name=goal.name,
            success=False,
            steps_taken=steps,
            total_steps=step_count,
            final_reason=f"최대 스텝 수 초과 ({goal.max_steps})",
            duration_seconds=time.time() - start_time,
        )

    def _analyze_dom(self, url: Optional[str] = None) -> List[DOMElement]:
        """MCP Host를 통해 DOM 분석"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "analyze_page",
                    "params": {
                        "session_id": self.session_id,
                        "url": url or "",
                    },
                },
                timeout=30,
            )
            data = response.json()

            # analyze_page는 success 필드 없이 elements를 직접 반환
            if "error" in data:
                self._log(f"DOM 분석 오류: {data['error']}")
                return []

            raw_elements = data.get("elements", []) or data.get("dom_elements", [])

            # 셀렉터 맵 초기화
            self._element_selectors = {}

            # DOMElement로 변환 (ID 부여)
            elements = []
            for idx, el in enumerate(raw_elements):
                attrs = el.get("attributes", {})

                # 셀렉터 저장
                selector = el.get("selector", "")
                if selector:
                    self._element_selectors[idx] = selector

                elements.append(
                    DOMElement(
                        id=idx,
                        tag=el.get("tag", ""),
                        text=el.get("text", "")[:100],  # 텍스트 길이 제한
                        role=attrs.get("role"),
                        type=attrs.get("type"),
                        placeholder=attrs.get("placeholder"),
                        aria_label=attrs.get("aria-label"),
                        href=attrs.get("href"),
                    )
                )

            return elements

        except Exception as e:
            self._log(f"DOM 분석 실패: {e}")
            return []

    def _capture_screenshot(self) -> Optional[str]:
        """스크린샷 캡처"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "capture_screenshot",
                    "params": {
                        "session_id": self.session_id,
                    },
                },
                timeout=30,
            )
            data = response.json()
            screenshot = data.get("screenshot")

            if screenshot and self._screenshot_callback:
                self._screenshot_callback(screenshot)

            return screenshot

        except Exception as e:
            self._log(f"스크린샷 캡처 실패: {e}")
            return None

    def _decide_next_action(
        self,
        dom_elements: List[DOMElement],
        goal: TestGoal,
        screenshot: Optional[str] = None,
    ) -> ActionDecision:
        """LLM에게 다음 액션 결정 요청"""

        # DOM 요소를 LLM이 이해하기 쉬운 형태로 변환
        elements_text = self._format_dom_for_llm(dom_elements)

        # 프롬프트 구성
        prompt = f"""당신은 웹 테스트 자동화 에이전트입니다.
현재 화면의 DOM 요소와 목표를 분석하고, 다음에 수행할 액션을 결정하세요.

## 목표
- 이름: {goal.name}
- 설명: {goal.description}
- 우선순위: {getattr(goal, "priority", "MAY")}
- 성공 조건: {', '.join(goal.success_criteria)}
- 실패 조건: {', '.join(goal.failure_criteria) if goal.failure_criteria else '없음'}
 - 키워드: {', '.join(getattr(goal, "keywords", []) or []) if getattr(goal, "keywords", None) else '없음'}

## 사용 가능한 테스트 데이터
{json.dumps(goal.test_data, ensure_ascii=False, indent=2)}

## 지금까지 수행한 액션
{chr(10).join(self._action_history[-5:]) if self._action_history else '없음 (첫 번째 스텝)'}

## 현재 화면의 DOM 요소 (클릭/입력 가능한 요소들)
{elements_text}

## 중요 지시사항
0. **키워드 우선 탐색**: 키워드와 관련된 요소를 먼저 찾아서 목표 달성에 활용하세요.
1. **탭/섹션 UI 확인**: role="tab"인 요소가 있으면 먼저 해당 탭을 클릭해야 합니다!
   - 예: 로그인 탭, 회원가입 탭이 있으면 → 먼저 로그인 탭 클릭 → 그 다음 폼 입력

2. **입력 전 활성화 확인**: 입력 필드가 비활성 상태일 수 있으므로 탭/버튼을 먼저 클릭

3. **목표 달성 여부 확인**
   - 성공 조건에 해당하는 요소가 보이면 is_goal_achieved: true

4. **중간 단계 파악**: 기획서에 없는 단계도 스스로 파악하세요
   - 예: "로그인" 목표 → (1)로그인 탭 클릭 → (2)이메일 입력 → (3)비밀번호 입력 → (4)제출 버튼 클릭

## 응답 형식 (JSON만, 마크다운 없이)
{{
    "action": "click" | "fill" | "press" | "scroll" | "wait",
    "element_id": 요소ID (숫자),
    "value": "입력값 (fill인 경우) 또는 키 이름 (press인 경우, 예: Enter)",
    "reasoning": "이 액션을 선택한 이유",
    "confidence": 0.0~1.0,
    "is_goal_achieved": true | false,
    "goal_achievement_reason": "목표 달성 판단 이유 (is_goal_achieved가 true인 경우)"
}}

JSON 응답:"""

        try:
            # Gemini API 호출
            if screenshot:
                response_text = self.llm.analyze_with_vision(prompt, screenshot)
            else:
                # 스크린샷 없이 텍스트만으로 분석 (fallback)
                response_text = self._call_gemini_text_only(prompt)

            # JSON 파싱
            return self._parse_decision(response_text)

        except Exception as e:
            self._log(f"LLM 결정 실패: {e}")
            # 기본 액션 반환 (대기)
            return ActionDecision(
                action=ActionType.WAIT,
                reasoning=f"LLM 오류: {e}",
                confidence=0.0,
            )

    def _format_dom_for_llm(self, elements: List[DOMElement]) -> str:
        """DOM 요소를 LLM이 이해하기 쉬운 텍스트로 변환"""
        lines = []
        for el in elements[:50]:  # 최대 50개로 제한
            parts = [f"[{el.id}] <{el.tag}>"]

            if el.text:
                parts.append(f'"{el.text}"')
            if el.role:
                parts.append(f"role={el.role}")
            if el.type and el.type != "button":
                parts.append(f"type={el.type}")
            if el.placeholder:
                parts.append(f'placeholder="{el.placeholder}"')
            if el.aria_label:
                parts.append(f'aria-label="{el.aria_label}"')

            lines.append(" ".join(parts))

        return "\n".join(lines)

    def _parse_decision(self, response_text: str) -> ActionDecision:
        """LLM 응답을 ActionDecision으로 파싱"""
        # 마크다운 코드 블록 제거
        text = response_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)

            return ActionDecision(
                action=ActionType(data.get("action", "wait")),
                element_id=data.get("element_id"),
                value=data.get("value"),
                reasoning=data.get("reasoning", ""),
                confidence=data.get("confidence", 0.5),
                is_goal_achieved=data.get("is_goal_achieved", False),
                goal_achievement_reason=data.get("goal_achievement_reason"),
            )

        except (json.JSONDecodeError, ValueError) as e:
            self._log(f"JSON 파싱 실패: {e}, 응답: {text[:200]}")
            return ActionDecision(
                action=ActionType.WAIT,
                reasoning=f"파싱 오류: {e}",
                confidence=0.0,
            )

    def _execute_decision(
        self,
        decision: ActionDecision,
        dom_elements: List[DOMElement],
    ) -> tuple[bool, Optional[str]]:
        """결정된 액션 실행"""

        # 요소 ID로 셀렉터 찾기
        selector = None
        if decision.element_id is not None:
            selector = self._element_selectors.get(decision.element_id)
            if not selector:
                return False, f"요소 ID {decision.element_id}에 대한 셀렉터를 찾을 수 없음"

        try:
            if decision.action == ActionType.CLICK:
                return self._execute_action("click", selector=selector)

            elif decision.action == ActionType.FILL:
                if not decision.value:
                    return False, "fill 액션에 value가 필요함"
                return self._execute_action("fill", selector=selector, value=decision.value)

            elif decision.action == ActionType.PRESS:
                # press 액션은 키보드 입력 (Enter, Tab 등)
                key = decision.value or "Enter"
                return self._execute_action("press", selector=selector or "", value=key)

            elif decision.action == ActionType.SCROLL:
                return self._execute_action("scroll", value="down")

            elif decision.action == ActionType.WAIT:
                time.sleep(1)
                return True, None

            elif decision.action == ActionType.NAVIGATE:
                return self._execute_action("goto", url=decision.value)

            elif decision.action == ActionType.HOVER:
                return self._execute_action("hover", selector=selector)

            else:
                return False, f"지원하지 않는 액션: {decision.action}"

        except Exception as e:
            return False, str(e)

    def _execute_action(
        self,
        action: str,
        selector: Optional[str] = None,
        value: Optional[str] = None,
        url: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """MCP Host를 통해 액션 실행"""

        params = {
            "session_id": self.session_id,
            "action": action,
            "url": url or "",
            "selector": selector or "",
        }

        if value:
            params["value"] = value

        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "execute_action",
                    "params": params,
                },
                timeout=60,
            )
            data = response.json()

            if data.get("success"):
                return True, None
            else:
                return False, data.get("error", "Unknown error")

        except Exception as e:
            return False, str(e)

    def _call_gemini_text_only(self, prompt: str) -> str:
        """스크린샷 없이 텍스트만으로 Gemini 호출 (fallback)"""
        from google import genai
        from google.genai import types

        response = self.llm.client.models.generate_content(
            model=self.llm.model,
            contents=[types.Content(parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(
                max_output_tokens=4096,
                temperature=0.1,
            ),
        )

        return response.text if response.text else ""
