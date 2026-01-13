# Goal-Driven Test Automation

범용적인 AI 기반 테스트 자동화 시스템

## 개요

코드 기반 테스트 스크립트 없이 AI가 자율적으로 웹 애플리케이션을 테스트합니다.

## 두 가지 모드

### 1. 🎯 Goal-Driven Mode (체크리스트 기반)

목표만 주면 AI가 화면을 분석해서 달성하는 모드

**특징:**
- 목표(Goal)만 정의, 세부 스텝은 AI가 결정
- 성공/실패 조건만 명시
- 여러 사이트에서 범용적으로 작동

**사용 예시:**
```python
from gaia.src.phase4.goal_driven import GoalDrivenAgent, TestGoal

# 목표 정의 (세부 스텝 없음!)
goal = TestGoal(
    id="TC001",
    name="로그인 성공",
    description="유효한 자격 증명으로 로그인",
    test_data={
        "email": "test@example.com",
        "password": "password123"
    },
    success_criteria=[
        "환영 메시지 표시",
        "로그아웃 버튼 표시"
    ],
    max_steps=15
)

# Agent 실행
agent = GoalDrivenAgent(mcp_host_url="http://localhost:8000")
result = agent.execute_goal(goal)

print(f"성공: {result.success}")
print(f"스텝 수: {result.total_steps}")
```

### 2. 🔍 Exploratory Mode (완전 자율 탐색)

목표 없이 화면의 모든 UI 요소를 자율적으로 탐색하고 테스트하는 모드

**특징:**
- 사전 정의된 목표 없음
- 화면의 모든 버튼, 링크, 입력 필드를 자동으로 찾아서 테스트
- While문처럼 계속 돌면서 새로운 요소 발견 및 테스트
- 버그, 에러, 이상 동작 자동 감지
- 테스트 커버리지 추적

**사용 예시:**
```python
from gaia.src.phase4.goal_driven import ExploratoryAgent, ExplorationConfig

# 설정
config = ExplorationConfig(
    max_actions=100,  # 최대 100개 액션
    max_depth=5,      # 최대 5단계 깊이
    prioritize_untested=True,  # 미테스트 요소 우선
    avoid_destructive=True,    # 삭제/파괴적 액션 회피
    test_forms=True,           # 폼 테스트
    test_navigation=True,      # 네비게이션 테스트
)

# Agent 생성 및 실행
agent = ExploratoryAgent(
    mcp_host_url="http://localhost:8000",
    config=config
)

result = agent.explore("https://example.com")

# 결과 확인
print(f"총 액션: {result.total_actions}")
print(f"테스트 커버리지: {result.get_coverage_percentage():.1f}%")
print(f"발견한 이슈: {len(result.issues_found)}개")

# 발견된 이슈 확인
for issue in result.issues_found:
    print(f"[{issue.severity}] {issue.title}")
    print(f"  - {issue.description}")
```

## 실행 방법

### 1. MCP Host 시작
```bash
python -m gaia.src.phase4.mcp_host
```

### 2. Goal-Driven 테스트 실행
```bash
python -m gaia.src.phase4.goal_driven.test_agent --test login
```

### 3. Exploratory 테스트 실행
```bash
# 기본 실행 (50개 액션)
python -m gaia.src.phase4.goal_driven.test_exploratory

# 커스텀 설정
python -m gaia.src.phase4.goal_driven.test_exploratory \
    --url https://your-site.com \
    --max-actions 100
```

## Exploratory Mode 동작 방식

```
1. 시작 URL로 이동
2. While (액션 수 < max_actions):
   a. 현재 페이지의 모든 상호작용 가능한 요소 분석
   b. 콘솔 에러 확인
   c. 스크린샷 캡처
   d. LLM에게 "다음에 뭘 테스트할까?" 물어보기
   e. 선택된 요소에 대해 액션 실행 (클릭, 입력 등)
   f. 새로운 에러/버그 감지
   g. 새로운 페이지 발견 시 탐색 계속
   h. 테스트 완료로 마킹
3. 결과 리포트 생성
   - 테스트 커버리지
   - 발견된 이슈 목록
   - 실행 단계 기록
```

## 발견되는 이슈 타입

- `ERROR`: JavaScript 에러
- `BROKEN_LINK`: 깨진 링크
- `VISUAL_GLITCH`: 시각적 버그
- `UNEXPECTED_BEHAVIOR`: 예상치 못한 동작
- `ACCESSIBILITY`: 접근성 문제
- `PERFORMANCE`: 성능 문제
- `TIMEOUT`: 타임아웃

## 결과 저장

탐색 결과는 자동으로 `artifacts/exploration_results/` 디렉토리에 JSON 형태로 저장됩니다.

```json
{
  "session_id": "exploration_1234567890",
  "total_actions": 50,
  "total_pages_visited": 5,
  "total_elements_tested": 45,
  "coverage": {
    "total_interactive_elements": 120,
    "tested_elements": 45,
    "coverage_percentage": 37.5
  },
  "issues_found": [
    {
      "issue_id": "ERR_123",
      "issue_type": "error",
      "severity": "high",
      "title": "JavaScript 에러 발생",
      "description": "...",
      "steps_to_reproduce": ["...", "..."]
    }
  ]
}
```

## 장점

### Exploratory Mode
✅ **완전 자동**: 테스트 스크립트 작성 불필요
✅ **범용적**: 어떤 사이트든 작동
✅ **버그 발견**: 예상치 못한 버그 자동 감지
✅ **커버리지**: 놓칠 수 있는 엣지 케이스 발견
✅ **지속적**: 계속 돌면서 새로운 영역 탐색

### Goal-Driven Mode
✅ **유연성**: 목표만 주면 AI가 경로 탐색
✅ **범용성**: 여러 사이트에서 동일한 플랜 사용 가능
✅ **유지보수**: UI 변경에도 selector 수정 불필요

## 제한사항

⚠️ LLM 호출 비용 발생
⚠️ 완전한 커버리지 보장 안 됨 (max_actions 제한)
⚠️ 파괴적 액션은 기본적으로 회피 (설정으로 변경 가능)

## 향후 개선 사항

- [ ] 체크리스트 + 자율 탐색 하이브리드 모드
- [ ] Visual regression 테스트
- [ ] AI가 생성한 테스트 케이스를 코드로 변환
- [ ] 멀티 세션 병렬 탐색
- [ ] 학습된 패턴 재사용
