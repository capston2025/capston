"""
Exploratory Testing Agent

완전 자율 탐색 모드 - 화면의 모든 UI 요소를 자동으로 찾아서 테스트
"""

from __future__ import annotations
import time
import json
import hashlib
import requests
from typing import Any, Dict, List, Optional, Set, Callable
from datetime import datetime

from .exploratory_models import (
    ExplorationConfig,
    ExplorationResult,
    ExplorationStep,
    ExplorationDecision,
    TestableAction,
    FoundIssue,
    IssueType,
    PageState,
    ElementState,
)
from .models import DOMElement


class ExploratoryAgent:
    """
    완전 자율 탐색 에이전트

    목표 없이 화면의 모든 UI 요소를 탐색하고 테스트
    버그, 에러, 이상 동작을 자동으로 감지
    """

    def __init__(
        self,
        mcp_host_url: str = "http://localhost:8000",
        gemini_api_key: Optional[str] = None,
        session_id: str = "exploratory",
        config: Optional[ExplorationConfig] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        screenshot_callback: Optional[Callable[[str], None]] = None,
        user_intervention_callback: Optional[Callable[[str, str], bool]] = None,
    ):
        self.mcp_host_url = mcp_host_url
        self.session_id = session_id
        self.config = config or ExplorationConfig()
        self._log_callback = log_callback
        self._screenshot_callback = screenshot_callback
        self._user_intervention_callback = user_intervention_callback

        # Gemini 클라이언트 초기화
        from gaia.src.phase4.llm_vision_client_gemini import GeminiVisionClient
        self.llm = GeminiVisionClient(api_key=gemini_api_key)

        # 탐색 상태 추적
        self._visited_pages: Dict[str, PageState] = {}  # url_hash -> PageState
        self._tested_elements: Set[str] = set()  # element_id
        self._action_history: List[str] = []
        self._found_issues: List[FoundIssue] = []

        # 현재 페이지 상태
        self._current_url: str = ""
        self._element_selectors: Dict[int, str] = {}  # DOM ID -> selector

    def _log(self, message: str):
        """로그 출력"""
        print(f"[ExploratoryAgent] {message}")
        if self._log_callback:
            self._log_callback(message)

    def _is_login_page_with_no_elements(self, page_state: PageState) -> bool:
        """
        로그인 페이지이면서 요소를 찾지 못한 경우 감지

        Args:
            page_state: 현재 페이지 상태

        Returns:
            bool: 사용자 개입이 필요한 로그인 페이지인 경우 True
        """
        # URL에 로그인 관련 키워드가 포함되어 있는지 확인
        login_keywords = ['login', 'signin', 'auth', 'sso', 'portal']
        url_lower = page_state.url.lower()
        has_login_keyword = any(keyword in url_lower for keyword in login_keywords)

        # 요소가 0개이거나 매우 적은 경우
        has_few_elements = len(page_state.interactive_elements) <= 2

        return has_login_keyword and has_few_elements

    def _request_user_intervention(self, reason: str, current_url: str) -> bool:
        """
        사용자 개입 요청

        Args:
            reason: 개입이 필요한 이유 (예: "로그인 필요", "캡챠 해결 필요")
            current_url: 현재 URL

        Returns:
            bool: 사용자가 작업을 완료했으면 True, 탐색 중단하려면 False
        """
        self._log("=" * 60)
        self._log("⏸️  사용자 개입 필요")
        self._log(f"   이유: {reason}")
        self._log(f"   현재 URL: {current_url}")
        self._log("=" * 60)

        # 콜백이 있으면 콜백 사용
        if self._user_intervention_callback:
            return self._user_intervention_callback(reason, current_url)

        # 콜백이 없으면 기본 input() 사용
        print(f"\n🔔 사용자 개입이 필요합니다!")
        print(f"이유: {reason}")
        print(f"현재 URL: {current_url}")
        print(f"\n브라우저에서 필요한 작업(로그인 등)을 완료한 후,")
        user_input = input("계속하려면 'c' 또는 'continue'를 입력하세요 (중단: 'q'): ").strip().lower()

        if user_input in ['c', 'continue', 'yes', 'y']:
            self._log("✅ 사용자가 작업을 완료했습니다. 탐색을 계속합니다.")
            return True
        else:
            self._log("❌ 사용자가 탐색 중단을 요청했습니다.")
            return False

    def explore(self, start_url: str) -> ExplorationResult:
        """
        완전 자율 탐색 시작

        화면의 모든 요소를 찾아서 테스트하고, 버그를 자동으로 발견
        """
        session_id = f"exploration_{int(time.time())}"
        start_time = time.time()
        steps: List[ExplorationStep] = []

        self._log("=" * 60)
        self._log("🔍 완전 자율 탐색 모드 시작")
        self._log(f"   시작 URL: {start_url}")
        self._log(f"   최대 액션: {self.config.max_actions}")
        self._log("=" * 60)

        # 시작 URL로 이동
        self._log(f"📍 시작 URL로 이동")
        self._execute_action("goto", url=start_url)
        time.sleep(2)  # 페이지 로드 대기
        self._current_url = start_url

        action_count = 0

        while action_count < self.config.max_actions:
            action_count += 1
            step_start = time.time()

            self._log(f"\n{'=' * 60}")
            self._log(f"📌 Step {action_count}/{self.config.max_actions}")
            self._log(f"{'=' * 60}")

            # 1. 현재 페이지 상태 분석
            page_state = self._analyze_current_page()
            if not page_state:
                self._log("⚠️  페이지 분석 실패, 잠시 대기 후 재시도")
                time.sleep(2)
                page_state = self._analyze_current_page()
                if not page_state:
                    self._log("❌ 페이지 분석 실패, 탐색 중단")
                    break

            self._log(f"📊 페이지 분석 완료:")
            self._log(f"   - URL: {page_state.url}")
            self._log(f"   - 상호작용 가능한 요소: {len(page_state.interactive_elements)}개")

            untested = [e for e in page_state.interactive_elements if not e.tested]
            self._log(f"   - 미테스트 요소: {len(untested)}개")

            # 로그인 페이지 감지 및 사용자 개입 요청
            if self._is_login_page_with_no_elements(page_state):
                self._log("🔐 로그인 페이지 감지됨 (요소 접근 불가 - cross-origin iframe 또는 특수 인증)")

                if not self._request_user_intervention(
                    reason="로그인이 필요합니다. 브라우저에서 수동으로 로그인해주세요.",
                    current_url=page_state.url
                ):
                    self._log("탐색 중단")
                    break

                # 사용자가 로그인 완료 후 페이지 재분석
                self._log("🔄 로그인 후 페이지 재분석...")
                time.sleep(3)
                page_state = self._analyze_current_page()
                if page_state:
                    self._log(f"✅ 로그인 후 {len(page_state.interactive_elements)}개 요소 발견")
                else:
                    self._log("⚠️  페이지 재분석 실패")
                    break

            # 2. 스크린샷 캡처
            screenshot = self._capture_screenshot()

            # 3. 콘솔 에러 확인
            console_errors = self._check_console_errors()
            if console_errors:
                self._log(f"⚠️  콘솔 에러 발견: {len(console_errors)}개")
                self._report_console_errors(console_errors, screenshot)

            # 4. LLM에게 다음 액션 결정 요청
            decision = self._decide_next_exploration_action(
                page_state=page_state,
                screenshot=screenshot,
                action_count=action_count,
            )

            self._log(f"🤖 LLM 결정:")
            self._log(f"   - 계속 탐색: {decision.should_continue}")
            if decision.selected_action:
                self._log(f"   - 액션: {decision.selected_action.action_type}")
                self._log(f"   - 대상: {decision.selected_action.description}")
            self._log(f"   - 이유: {decision.reasoning}")

            # 5. 탐색 종료 판단
            if not decision.should_continue:
                self._log(f"✅ 탐색 완료: {decision.reasoning}")

                step = ExplorationStep(
                    step_number=action_count,
                    url=page_state.url,
                    decision=decision,
                    success=True,
                    duration_ms=int((time.time() - step_start) * 1000),
                )
                steps.append(step)
                break

            # 6. 액션이 없으면 탐색 완료
            if not decision.selected_action:
                self._log("✅ 더 이상 테스트할 요소가 없습니다")

                step = ExplorationStep(
                    step_number=action_count,
                    url=page_state.url,
                    decision=decision,
                    success=True,
                    duration_ms=int((time.time() - step_start) * 1000),
                )
                steps.append(step)
                break

            # 7. 스크린샷 (액션 실행 전)
            screenshot_before = screenshot

            # 8. 액션 실행
            success, error, issues = self._execute_exploration_action(
                decision=decision,
                page_state=page_state,
            )

            # 9. 액션 결과 기록
            self._action_history.append(
                f"Step {action_count}: {decision.selected_action.action_type} on {decision.selected_action.description}"
            )

            # 10. 요소를 테스트 완료로 마킹
            if decision.selected_action:
                self._tested_elements.add(decision.selected_action.element_id)

            # 11. 스크린샷 (액션 실행 후)
            time.sleep(1)  # UI 변화 대기
            screenshot_after = self._capture_screenshot()

            # 12. 새로운 페이지 발견 확인
            new_url = self._get_current_url()
            new_pages = 1 if new_url != page_state.url else 0
            if new_pages:
                self._log(f"🆕 새 페이지 발견: {new_url}")

            # 13. Step 결과 저장
            step = ExplorationStep(
                step_number=action_count,
                url=page_state.url,
                decision=decision,
                success=success,
                error_message=error,
                issues_found=issues,
                new_pages_found=new_pages,
                screenshot_before=screenshot_before,
                screenshot_after=screenshot_after,
                duration_ms=int((time.time() - step_start) * 1000),
            )
            steps.append(step)

            # 14. 발견된 이슈 추가
            self._found_issues.extend(issues)
            if issues:
                self._log(f"🚨 이슈 발견: {len(issues)}개")
                for issue in issues:
                    self._log(f"   - [{issue.severity}] {issue.title}")

            # 15. 실패한 경우 계속 진행할지 판단
            if not success and error:
                self._log(f"⚠️  액션 실패: {error}")
                # 실패해도 계속 진행 (다른 요소 테스트)

            # 다음 스텝 전 대기
            time.sleep(0.5)

        # 탐색 완료
        duration = time.time() - start_time
        completion_reason = self._determine_completion_reason(action_count, steps)

        # 최종 결과 생성
        result = ExplorationResult(
            session_id=session_id,
            start_url=start_url,
            total_actions=action_count,
            total_pages_visited=len(self._visited_pages),
            total_elements_tested=len(self._tested_elements),
            coverage=self._calculate_coverage(),
            issues_found=self._found_issues,
            steps=steps,
            completion_reason=completion_reason,
            completed_at=datetime.now(),
            duration_seconds=duration,
        )

        # 결과 요약 출력
        self._print_summary(result)

        return result

    def _analyze_current_page(self) -> Optional[PageState]:
        """현재 페이지의 모든 상호작용 가능한 요소 분석"""
        try:
            # URL 가져오기
            current_url = self._get_current_url()
            url_hash = self._hash_url(current_url)

            # DOM 분석
            dom_elements = self._analyze_dom()
            # 요소가 0개라도 PageState를 반환 (사용자 개입 감지를 위해)
            if not dom_elements:
                dom_elements = []

            # AutoCrawler 방식: 중요 요소만 필터링 (광고/푸터 제외)
            interactive_elements = []
            for idx, el in enumerate(dom_elements):
                # 클릭 가능하거나 입력 가능한 요소만
                is_interactive = (
                    el.tag in ["button", "a", "input", "select", "textarea"]
                    or el.role in ["button", "link", "tab", "menuitem"]
                )

                if not is_interactive:
                    continue

                # 광고/푸터/불필요한 요소 제외
                text_lower = el.text.lower() if el.text else ""
                selector_lower = self._element_selectors.get(idx, "").lower()

                # 제외할 키워드
                exclude_keywords = [
                    'advertisement', 'ad-', 'adsbygoogle', 'google_ads',
                    'footer', 'cookie', 'privacy', 'terms',
                    'share', 'facebook', 'twitter', 'instagram',
                    '광고', '공유', '쿠키', '개인정보',
                ]

                should_exclude = any(
                    keyword in text_lower or keyword in selector_lower
                    for keyword in exclude_keywords
                )

                if should_exclude:
                    continue

                element_id = f"{url_hash}:{el.tag}:{el.text[:30]}"
                tested = element_id in self._tested_elements

                interactive_elements.append(
                    ElementState(
                        element_id=element_id,
                        tag=el.tag,
                        text=el.text,
                        selector=self._element_selectors.get(idx, ""),
                        role=el.role,
                        type=el.type,
                        aria_label=el.aria_label,
                        tested=tested,
                    )
                )

            # AutoCrawler 최적화: 최대 30개로 제한 (우선순위: 미테스트 > 테스트됨)
            if len(interactive_elements) > 30:
                untested = [e for e in interactive_elements if not e.tested]
                tested = [e for e in interactive_elements if e.tested]
                interactive_elements = untested[:25] + tested[:5]  # 미테스트 25개 + 테스트됨 5개
                self._log(f"⚡ 요소 샘플링: {len(untested) + len(tested)}개 → 30개")

            # PageState 생성
            page_state = PageState(
                url=current_url,
                url_hash=url_hash,
                interactive_elements=interactive_elements,
            )

            # 방문 기록 업데이트
            if url_hash in self._visited_pages:
                existing = self._visited_pages[url_hash]
                existing.visit_count += 1
                existing.last_visited_at = datetime.now()
                existing.interactive_elements = interactive_elements
            else:
                self._visited_pages[url_hash] = page_state

            return page_state

        except Exception as e:
            self._log(f"페이지 분석 실패: {e}")
            return None

    def _analyze_dom(self) -> List[DOMElement]:
        """MCP Host를 통해 DOM 분석"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "analyze_page",
                    "params": {"session_id": self.session_id},
                },
                timeout=30,
            )
            data = response.json()

            if "error" in data:
                self._log(f"DOM 분석 오류: {data['error']}")
                return []

            raw_elements = data.get("elements", []) or data.get("dom_elements", [])

            # 셀렉터 맵 초기화
            self._element_selectors = {}

            # DOMElement로 변환
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
                        text=el.get("text", "")[:100],
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
                    "params": {"session_id": self.session_id},
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

    def _check_console_errors(self) -> List[str]:
        """콘솔 에러 확인"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "get_console_logs",
                    "params": {"session_id": self.session_id, "type": "error"},
                },
                timeout=10,
            )
            data = response.json()
            logs = data.get("logs", [])
            return logs

        except Exception as e:
            self._log(f"콘솔 로그 확인 실패: {e}")
            return []

    def _get_current_url(self) -> str:
        """현재 URL 가져오기"""
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "get_current_url",
                    "params": {"session_id": self.session_id},
                },
                timeout=10,
            )
            data = response.json()
            return data.get("url", self._current_url)

        except Exception as e:
            return self._current_url

    def _decide_next_exploration_action(
        self,
        page_state: PageState,
        screenshot: Optional[str],
        action_count: int,
    ) -> ExplorationDecision:
        """LLM에게 다음 탐색 액션 결정 요청"""

        # 테스트 가능한 액션 목록 생성
        testable_actions = self._generate_testable_actions(page_state)

        if not testable_actions:
            return ExplorationDecision(
                should_continue=False,
                reasoning="더 이상 테스트할 요소가 없습니다",
                confidence=1.0,
            )

        # 프롬프트 구성
        prompt = self._build_exploration_prompt(
            page_state=page_state,
            testable_actions=testable_actions,
            action_count=action_count,
        )

        try:
            # Gemini API 호출
            if screenshot:
                response_text = self.llm.analyze_with_vision(prompt, screenshot)
            else:
                response_text = self._call_gemini_text_only(prompt)

            # JSON 파싱
            return self._parse_exploration_decision(response_text, testable_actions)

        except Exception as e:
            self._log(f"LLM 결정 실패: {e}")
            # 기본 결정: 첫 번째 미테스트 요소 선택
            if testable_actions:
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=testable_actions[0],
                    reasoning=f"LLM 오류로 기본 액션 선택: {e}",
                    confidence=0.3,
                )
            else:
                return ExplorationDecision(
                    should_continue=False,
                    reasoning="테스트할 요소 없음",
                    confidence=1.0,
                )

    def _generate_testable_actions(self, page_state: PageState) -> List[TestableAction]:
        """페이지 상태에서 테스트 가능한 액션 목록 생성"""
        actions = []

        for element in page_state.interactive_elements:
            # 이미 테스트한 요소는 우선순위 낮게
            priority = 0.3 if element.tested else 0.8

            # 액션 타입 결정
            if element.tag == "input":
                if element.type in ["text", "email", "password", "search"]:
                    action_type = "fill"
                    description = f"입력 필드: {element.placeholder or element.aria_label or element.text}"
                elif element.type in ["checkbox", "radio"]:
                    action_type = "click"
                    description = f"체크박스/라디오: {element.text or element.aria_label}"
                else:
                    action_type = "click"
                    description = f"Input: {element.type}"
            elif element.tag == "a":
                action_type = "click"
                description = f"링크: {element.text or 'Link'}"
                # 외부 링크는 우선순위 낮게
                if element.href and (element.href.startswith("http") and page_state.url not in element.href):
                    priority *= 0.5
            elif element.tag == "button":
                action_type = "click"
                description = f"버튼: {element.text or element.aria_label or 'Button'}"
            elif element.tag == "select":
                action_type = "select"
                description = f"드롭다운: {element.text or element.aria_label}"
            else:
                action_type = "click"
                description = f"{element.tag}: {element.text or element.role}"

            # 파괴적 액션 회피
            if self.config.avoid_destructive:
                destructive_keywords = ["delete", "remove", "삭제", "제거", "clear", "reset"]
                if any(keyword in description.lower() for keyword in destructive_keywords):
                    priority *= 0.1

            actions.append(
                TestableAction(
                    element_id=element.element_id,
                    action_type=action_type,
                    description=description,
                    priority=priority,
                    reasoning=f"{'미테스트' if not element.tested else '재테스트'} 요소",
                )
            )

        # 우선순위로 정렬
        actions.sort(key=lambda x: x.priority, reverse=True)

        return actions

    def _build_exploration_prompt(
        self,
        page_state: PageState,
        testable_actions: List[TestableAction],
        action_count: int,
    ) -> str:
        """탐색 프롬프트 생성"""

        # 테스트 가능한 액션을 텍스트로 변환 (최대 20개)
        actions_text = "\n".join(
            [
                f"[{i}] {action.action_type.upper()}: {action.description} (우선순위: {action.priority:.2f})"
                for i, action in enumerate(testable_actions[:20])
            ]
        )

        # 최근 액션 히스토리
        recent_history = "\n".join(self._action_history[-5:]) if self._action_history else "없음 (첫 탐색)"

        # 발견된 이슈 요약
        issues_summary = f"{len(self._found_issues)}개 이슈 발견" if self._found_issues else "아직 이슈 없음"

        prompt = f"""당신은 웹 애플리케이션 탐색 테스트 에이전트입니다.
화면의 모든 UI 요소를 자율적으로 탐색하고 테스트하여 버그를 찾는 것이 목표입니다.

## 현재 상황
- URL: {page_state.url}
- 탐색 진행: {action_count}/{self.config.max_actions} 액션
- 테스트 완료 요소: {len(self._tested_elements)}개
- 발견된 이슈: {issues_summary}

## 최근 수행한 액션
{recent_history}

## 테스트 가능한 액션 목록 (우선순위 순)
{actions_text}

## 지시사항
1. **우선순위 고려**: 미테스트 요소를 우선 선택하세요
2. **다양성**: 같은 유형만 계속 테스트하지 말고 다양한 UI 요소를 테스트하세요
3. **깊이 우선**: 링크를 따라가서 새로운 페이지도 탐색하세요
4. **버그 탐지**: 에러 메시지, 깨진 UI, 예상치 못한 동작을 찾으세요
5. **종료 조건**: 더 이상 테스트할 요소가 없거나, 충분히 탐색했다면 should_continue: false

## 입력값 생성 규칙 (fill 액션인 경우)
- 이메일 필드: "test.explorer@example.com"
- 비밀번호 필드: "TestPass123!"
- 이름 필드: "Test User"
- 전화번호: "010-1234-5678"
- 일반 텍스트: "Test input"

## 응답 형식 (JSON만, 마크다운 없이)
{{
    "should_continue": true | false,
    "selected_action_index": 액션 인덱스 (0-19, 선택 안 하면 null),
    "input_values": {{"field_name": "value"}},  // fill 액션인 경우만
    "reasoning": "이 액션을 선택한 이유 또는 종료 이유",
    "confidence": 0.0~1.0,
    "expected_outcome": "예상되는 결과"
}}

JSON 응답:"""

        return prompt

    def _parse_exploration_decision(
        self,
        response_text: str,
        testable_actions: List[TestableAction],
    ) -> ExplorationDecision:
        """LLM 응답을 ExplorationDecision으로 파싱"""
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

            should_continue = data.get("should_continue", True)
            action_index = data.get("selected_action_index")
            selected_action = None

            if action_index is not None and 0 <= action_index < len(testable_actions):
                selected_action = testable_actions[action_index]

            return ExplorationDecision(
                should_continue=should_continue,
                selected_action=selected_action,
                input_values=data.get("input_values", {}),
                reasoning=data.get("reasoning", ""),
                confidence=data.get("confidence", 0.5),
                expected_outcome=data.get("expected_outcome", ""),
            )

        except (json.JSONDecodeError, ValueError) as e:
            self._log(f"JSON 파싱 실패: {e}")
            # 기본값: 첫 번째 액션 선택
            if testable_actions:
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=testable_actions[0],
                    reasoning=f"파싱 오류로 기본 액션 선택: {e}",
                    confidence=0.3,
                )
            else:
                return ExplorationDecision(
                    should_continue=False,
                    reasoning="파싱 오류 및 액션 없음",
                    confidence=0.0,
                )

    def _execute_exploration_action(
        self,
        decision: ExplorationDecision,
        page_state: PageState,
    ) -> tuple[bool, Optional[str], List[FoundIssue]]:
        """탐색 액션 실행 및 이슈 감지"""

        if not decision.selected_action:
            return True, None, []

        action = decision.selected_action
        issues = []

        # 셀렉터 찾기
        selector = self._find_selector_by_element_id(action.element_id, page_state)
        if not selector:
            return False, f"셀렉터를 찾을 수 없음: {action.element_id}", []

        self._log(f"🎯 실행: {action.action_type} on {action.description}")

        try:
            # 액션 실행 전 에러 수 확인
            errors_before = len(self._check_console_errors())

            # 액션 실행
            if action.action_type == "click":
                success, error = self._execute_action("click", selector=selector)
            elif action.action_type == "fill":
                # 입력값 결정
                value = self._determine_input_value(action, decision.input_values)
                success, error = self._execute_action("fill", selector=selector, value=value)
            elif action.action_type == "select":
                success, error = self._execute_action("select", selector=selector, value="1")
            elif action.action_type == "hover":
                success, error = self._execute_action("hover", selector=selector)
            else:
                success, error = False, f"지원하지 않는 액션: {action.action_type}"

            # 액션 실행 후 에러 수 확인
            time.sleep(0.5)
            errors_after = len(self._check_console_errors())

            # 새로운 에러 발생했으면 이슈로 기록
            if errors_after > errors_before:
                new_errors = self._check_console_errors()[errors_before:]
                issue = self._create_error_issue(
                    action=action,
                    error_logs=new_errors,
                    url=page_state.url,
                )
                issues.append(issue)

            # 액션 실패도 이슈로 기록
            if not success and error:
                issue = self._create_action_failure_issue(
                    action=action,
                    error_message=error,
                    url=page_state.url,
                )
                issues.append(issue)

            return success, error, issues

        except Exception as e:
            return False, str(e), []

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
                json={"action": "execute_action", "params": params},
                timeout=self.config.action_timeout,
            )

            # HTTP 상태 코드 로깅
            if response.status_code != 200:
                self._log(f"⚠️  HTTP {response.status_code}: {response.text[:200]}")

            data = response.json()

            if data.get("success"):
                return True, None
            else:
                error_msg = data.get("error") or data.get("detail") or f"Unknown error (response: {data})"
                self._log(f"❌ Action failed: {error_msg}")
                return False, error_msg

        except Exception as e:
            return False, str(e)

    def _find_selector_by_element_id(
        self,
        element_id: str,
        page_state: PageState,
    ) -> Optional[str]:
        """element_id로 셀렉터 찾기"""
        for element in page_state.interactive_elements:
            if element.element_id == element_id:
                return element.selector
        return None

    def _determine_input_value(
        self,
        action: TestableAction,
        input_values: Dict[str, str],
    ) -> str:
        """입력 필드에 넣을 값 결정"""
        desc_lower = action.description.lower()

        # 명시적으로 제공된 값 사용
        if input_values:
            for key, value in input_values.items():
                if key.lower() in desc_lower:
                    return value

        # 기본값 생성
        if "email" in desc_lower or "이메일" in desc_lower:
            return "test.explorer@example.com"
        elif "password" in desc_lower or "비밀번호" in desc_lower:
            return "TestPass123!"
        elif "name" in desc_lower or "이름" in desc_lower:
            return "Test User"
        elif "phone" in desc_lower or "전화" in desc_lower:
            return "010-1234-5678"
        elif "search" in desc_lower or "검색" in desc_lower:
            return "test"
        else:
            return "Test input"

    def _create_error_issue(
        self,
        action: TestableAction,
        error_logs: List[str],
        url: str,
    ) -> FoundIssue:
        """콘솔 에러 이슈 생성"""
        issue_id = f"ERR_{int(time.time())}_{len(self._found_issues)}"

        return FoundIssue(
            issue_id=issue_id,
            issue_type=IssueType.ERROR,
            severity="high",
            title=f"JavaScript 에러 발생: {action.description}",
            description=f"액션 실행 후 콘솔 에러가 발생했습니다.\n\n에러 로그:\n" + "\n".join(error_logs[:5]),
            url=url,
            steps_to_reproduce=[
                f"1. {url}로 이동",
                f"2. {action.description}를 {action.action_type}",
            ],
            error_message=error_logs[0] if error_logs else None,
            console_logs=error_logs,
        )

    def _create_action_failure_issue(
        self,
        action: TestableAction,
        error_message: str,
        url: str,
    ) -> FoundIssue:
        """액션 실패 이슈 생성"""
        issue_id = f"FAIL_{int(time.time())}_{len(self._found_issues)}"

        return FoundIssue(
            issue_id=issue_id,
            issue_type=IssueType.UNEXPECTED_BEHAVIOR,
            severity="medium",
            title=f"액션 실행 실패: {action.description}",
            description=f"액션을 실행했지만 실패했습니다.\n\n오류: {error_message}",
            url=url,
            steps_to_reproduce=[
                f"1. {url}로 이동",
                f"2. {action.description}를 {action.action_type}",
            ],
            error_message=error_message,
        )

    def _report_console_errors(self, console_errors: List[str], screenshot: Optional[str]):
        """콘솔 에러 리포트"""
        issue_id = f"CONSOLE_{int(time.time())}"

        issue = FoundIssue(
            issue_id=issue_id,
            issue_type=IssueType.ERROR,
            severity="medium",
            title=f"콘솔 에러 감지: {len(console_errors)}개",
            description=f"페이지 로드 시 콘솔 에러가 발견되었습니다.\n\n" + "\n".join(console_errors[:5]),
            url=self._current_url,
            steps_to_reproduce=[f"1. {self._current_url}로 이동"],
            console_logs=console_errors,
            screenshot_before=screenshot,
        )

        self._found_issues.append(issue)

    def _calculate_coverage(self) -> Dict[str, Any]:
        """테스트 커버리지 계산"""
        total_elements = 0
        tested_elements = len(self._tested_elements)

        for page in self._visited_pages.values():
            total_elements += len(page.interactive_elements)

        return {
            "total_interactive_elements": total_elements,
            "tested_elements": tested_elements,
            "coverage_percentage": (tested_elements / total_elements * 100) if total_elements > 0 else 0,
            "total_pages": len(self._visited_pages),
        }

    def _determine_completion_reason(
        self,
        action_count: int,
        steps: List[ExplorationStep],
    ) -> str:
        """탐색 종료 이유 결정"""
        if action_count >= self.config.max_actions:
            return f"최대 액션 수 도달 ({self.config.max_actions})"
        elif steps and not steps[-1].decision.should_continue:
            return steps[-1].decision.reasoning
        else:
            return "탐색 완료"

    def _print_summary(self, result: ExplorationResult):
        """탐색 결과 요약 출력"""
        self._log("\n" + "=" * 60)
        self._log("🎉 탐색 완료!")
        self._log("=" * 60)
        self._log(f"총 액션 수: {result.total_actions}")
        self._log(f"방문한 페이지: {result.total_pages_visited}개")
        self._log(f"테스트한 요소: {result.total_elements_tested}개")
        self._log(f"커버리지: {result.get_coverage_percentage():.1f}%")
        self._log(f"발견한 이슈: {len(result.issues_found)}개")

        if result.issues_found:
            critical = len([i for i in result.issues_found if i.severity == "critical"])
            high = len([i for i in result.issues_found if i.severity == "high"])
            medium = len([i for i in result.issues_found if i.severity == "medium"])
            low = len([i for i in result.issues_found if i.severity == "low"])

            self._log(f"  - Critical: {critical}개")
            self._log(f"  - High: {high}개")
            self._log(f"  - Medium: {medium}개")
            self._log(f"  - Low: {low}개")

        self._log(f"소요 시간: {result.duration_seconds:.1f}초")
        self._log(f"종료 이유: {result.completion_reason}")
        self._log("=" * 60)

    def _hash_url(self, url: str) -> str:
        """URL 해시 생성 (중복 방지)"""
        # 쿼리 파라미터 제거하고 해시 생성
        base_url = url.split("?")[0].split("#")[0]
        return hashlib.md5(base_url.encode()).hexdigest()[:12]

    def _call_gemini_text_only(self, prompt: str) -> str:
        """스크린샷 없이 텍스트만으로 Gemini 호출"""
        from google.genai import types

        response = self.llm.client.models.generate_content(
            model=self.llm.model,
            contents=[types.Content(parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(
                max_output_tokens=4096,
                temperature=0.2,
            ),
        )

        return response.text if response.text else ""
