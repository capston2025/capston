from __future__ import annotations

import json
from typing import List, Optional

from .models import ActionDecision, ActionType, DOMElement, TestGoal


def decide_next_action(
    agent,
    dom_elements: List[DOMElement],
    goal: TestGoal,
    screenshot: Optional[str] = None,
    memory_context: str = "",
) -> ActionDecision:
    elements_text = agent._format_dom_for_llm(dom_elements)
    recent_repeated = agent._recent_click_element_ids[-8:]
    recent_block_text = ", ".join(str(x) for x in recent_repeated) if recent_repeated else "없음"
    signup_rule = ""
    if agent._goal_mentions_signup(goal):
        signup_rule = """
5. **회원가입 목표 특별 규칙(강제)**
   - 회원가입 화면/모달 진입만으로는 절대 성공이 아닙니다.
   - 입력값 채움 + 제출 버튼 클릭 + 완료 신호(완료 문구/로그인 상태 변화) 확인 전까지 is_goal_achieved=false를 유지하세요.
"""
    constraint_rule = agent._build_goal_constraint_prompt()

    prompt = f"""당신은 웹 테스트 자동화 에이전트입니다.
현재 화면의 DOM 요소와 목표를 분석하고, 다음에 수행할 액션을 결정하세요.

## 목표
- 이름: {goal.name}
- 설명: {goal.description}
- 우선순위: {getattr(goal, 'priority', 'MAY')}
- 성공 조건: {', '.join(goal.success_criteria)}
- 실패 조건: {', '.join(goal.failure_criteria) if goal.failure_criteria else '없음'}
 - 키워드: {', '.join(getattr(goal, 'keywords', []) or []) if getattr(goal, 'keywords', None) else '없음'}

## 현재 실행 phase (참고)
- phase: {agent._runtime_phase}
- AUTH=인증/로그인 처리, COLLECT=후보 수집, COMPOSE=조합/설정, APPLY=반영/실행, VERIFY=완료 검증
- phase는 가이드일 뿐이며, 실제 DOM/상태 변화 증거를 우선하세요.

## 사용 가능한 테스트 데이터
{json.dumps(goal.test_data, ensure_ascii=False, indent=2)}

## 지금까지 수행한 액션
{chr(10).join(agent._action_history[-5:]) if agent._action_history else '없음 (첫 번째 스텝)'}

## 최근 액션 실행 피드백
{chr(10).join(agent._action_feedback[-5:]) if agent._action_feedback else '없음'}

## 최근 반복 클릭 element_id (가능하면 회피)
{recent_block_text}

## 도메인 실행 기억(KB)
{memory_context or '없음'}

## 현재 화면의 DOM 요소 (클릭/입력 가능한 요소들)
{elements_text}

## 중요 지시사항
0. **키워드 우선 탐색**: 키워드와 관련된 요소를 먼저 찾아서 목표 달성에 활용하세요.
1. **탭/섹션 UI 확인**: role=\"tab\"인 요소가 있으면 먼저 해당 탭을 클릭해야 합니다!
   - 예: 로그인 탭, 회원가입 탭이 있으면 → 먼저 로그인 탭 클릭 → 그 다음 폼 입력

2. **입력 전 활성화 확인**: 입력 필드가 비활성 상태일 수 있으므로 탭/버튼을 먼저 클릭

3. **목표 달성 여부 확인**
   - 성공 조건에 해당하는 요소가 보이면 is_goal_achieved: true

4. **중간 단계 파악**: 기획서에 없는 단계도 스스로 파악하세요
   - 예: \"로그인\" 목표 → (1)로그인 탭 클릭 → (2)이메일 입력 → (3)비밀번호 입력 → (4)제출 버튼 클릭
{signup_rule}
{constraint_rule}
6. **무효 액션 반복 금지**
   - 최근 실행 피드백에서 changed=false 또는 success=false인 액션/요소 조합은 반복하지 마세요.
   - 같은 요소를 2회 연속 클릭했는데 changed=false라면 다른 요소/전략을 선택하세요.
7. **컨텍스트 전환 규칙**
   - 같은 의도가 2회 이상 changed=false이면, 다음/페이지네이션/탭/필터/정렬 전환으로 화면 컨텍스트를 바꾼 뒤 다시 시도하세요.
   - 목표 단계 전환 CTA가 안 보일 때 `확장/더보기/show more/expand`는 **콘텐츠 영역 확장일 때만** 우선 선택하세요.
   - 목록형 페이지에서는 동일 카드 반복 클릭보다 다른 카드/다음 페이지 이동을 우선하세요.
   - 페이지네이션에서 \"다음/next/›/»\"가 보이면 숫자 페이지 버튼(1,2,3,4...)보다 우선 선택하세요.
   - 숫자 페이지 버튼만 반복 클릭하지 말고, 진행 정체 시 반드시 \"다음\"으로 넘어가세요.
8. **단계 전환 규칙(강제)**
   - 동일한 클릭 의도가 여러 번 연속 성공해도 목표가 완료되지 않으면, 다음 액션은 단계 전환 CTA를 우선 선택하세요.
   - 해당 CTA가 보이지 않으면 스크롤/탭 전환/다음 페이지 이동으로 CTA를 먼저 찾으세요.

## 응답 형식 (JSON만, 마크다운 없이)
{{
    \"action\": \"click\" | \"fill\" | \"press\" | \"scroll\" | \"wait\" | \"select\",
    \"element_id\": 요소ID (숫자),
    \"value\": \"입력값 (fill), 키 이름 (press), select 값(문자열/콤마구분/JSON 배열), wait 조건(JSON 또는 ms)\",
    \"reasoning\": \"이 액션을 선택한 이유\",
    \"confidence\": 0.0~1.0,
    \"is_goal_achieved\": true | false,
    \"goal_achievement_reason\": \"목표 달성 판단 이유 (is_goal_achieved가 true인 경우)\"
}}

JSON 응답:"""

    try:
        if screenshot:
            response_text = agent.llm.analyze_with_vision(prompt, screenshot)
        else:
            response_text = agent._call_llm_text_only(prompt)
        return agent._parse_decision(response_text)
    except Exception as e:
        agent._log(f"LLM 결정 실패: {e}")
        return ActionDecision(
            action=ActionType.WAIT,
            reasoning=f"LLM 오류: {e}",
            confidence=0.0,
        )
