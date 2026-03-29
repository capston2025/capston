# GAIA Agent Harness Playbook

## 1. 목적

이 문서는 앞으로 GAIA 작업을 `planner -> developer -> verifier` 기본 루프와 주기적 `cleanup` 루프로 운영하기 위한 하네스 규약이다.

핵심 목표는 두 가지다.

1. OpenClaw의 판단력을 최대한 보존한다.
2. GAIA는 실행 안정화, 검증, 추적성, 정리 작업에 집중한다.

이 문서는 기능 문서가 아니라 작업 운영 문서다. 즉 "코드를 어떻게 짜는가"보다 "에이전트가 어떤 순서로 어떤 책임을 지는가"를 고정한다.

---

## 2. 핵심 원칙

### 2.1 OpenClaw 우선

- 화면 판단과 액션 선택은 가능한 한 OpenClaw가 본 원본 구조를 기준으로 한다.
- GAIA는 decision-time에 과도한 의미 재해석을 하지 않는다.
- raw role tree, stable ref, 실제 post-action 관찰값을 1차 근거로 사용한다.

### 2.2 Wrapper는 얇게 유지

- GAIA의 책임은 아래로 제한한다.
- 실행 디스패치
- stale ref 복구
- post-action probe
- 최종 성공/실패 판정
- trace/log 수집

### 2.3 독립 검증 필수

- 구현을 끝낸 에이전트가 자기 결과를 최종 판정하지 않는다.
- 항상 별도의 verifier agent가 독립적으로 코드, 테스트, trace, 실제 실행 결과를 검토한다.

### 2.4 주기적 정리 필수

- 기능 추가만 계속하지 않는다.
- AI가 만든 중복 로직, wrapper 과잉 개입, 죽은 코드, stale flag, 임시 호환 경로는 cleanup agent가 주기적으로 제거한다.

### 2.5 증거 기반 보고

- 모든 완료 보고는 아래 중 최소 하나를 포함해야 한다.
- 실행 trace
- 재현 명령
- 테스트 결과
- 로그/스크린샷
- 실제 DOM 또는 API 증거

---

## 3. 기본 하네스 구성

### 3.1 Planner

역할:

- 문제를 짧고 정확하게 다시 정의한다.
- 변경 범위를 나눈다.
- 위험 요소와 검증 기준을 먼저 만든다.
- 어떤 작업을 메인 에이전트가 하고, 어떤 작업을 서브에이전트에 줄지 결정한다.

해야 하는 일:

- 목표를 한 문장으로 정리
- 현재 병목 식별
- 변경 파일 후보 식별
- acceptance criteria 작성
- verifier가 확인할 체크리스트 작성

하지 말아야 할 일:

- 구현 확정 전에 세부 정책을 과도하게 주입
- 증거 없이 원인 단정
- 코드 수정

출력 형식:

```md
## Goal
- ...

## Risks
- ...

## Change Scope
- ...

## Acceptance Criteria
- ...

## Verification Plan
- ...
```

### 3.2 Developer

역할:

- planner가 정한 범위 안에서 실제 구현을 수행한다.
- 필요하면 bounded 서브에이전트에 작업을 분할한다.
- 변경 후 직접 기본 검증까지 수행한다.

해야 하는 일:

- 구현
- 테스트 추가/수정
- 재현 명령 준비
- trace/log 남기기

하지 말아야 할 일:

- 자신의 구현을 최종 승인
- unrelated cleanup을 섞어 scope를 흐리기
- 임시 우회만 남기고 책임 경계를 흐리기

출력 형식:

```md
## What Changed
- ...

## Why
- ...

## Verification Performed
- ...

## Known Risks
- ...
```

### 3.3 Verifier

역할:

- developer와 독립적으로 결과를 검토한다.
- 코드 리뷰, 테스트 재실행, trace 확인, live reproduction 중 필요한 것을 수행한다.
- findings-first 방식으로 결과를 낸다.

해야 하는 일:

- 가장 위험한 가정부터 반박 시도
- false positive 여부 확인
- trace와 최종 판정의 모순 확인
- 테스트가 실제 버그를 막는지 확인

하지 말아야 할 일:

- developer 설명을 그대로 신뢰
- 검증 없이 "문제 없음" 선언
- 기능 확장 제안으로 검토를 흐리기

출력 형식:

```md
## Findings
1. ...

## Reproduced Evidence
- ...

## Residual Risks
- ...

## Verdict
- pass | pass_with_risk | fail
```

### 3.4 Cleanup Agent

역할:

- 기능 개발과 별도로 코드베이스를 정리한다.
- "잘 돌아간다"는 이유로 쌓인 과잉 래퍼, AI 슬롭, 중복 헬퍼, stale compat path를 걷어낸다.

주요 임무:

- dead code 제거
- duplicate helper 제거
- thin-wrapper 철칙 위반 탐지
- 불필요한 env flag/feature flag 정리
- deprecated path 및 fallback 누적 제거
- 긴 함수/과도한 조건문 분해 또는 삭제

출력 형식:

```md
## Slop / Waste Found
- ...

## Safe Deletions
- ...

## Follow-up Refactors
- ...

## Risk Notes
- ...
```

---

## 4. 표준 실행 순서

모든 실질 변경은 아래 순서를 기본으로 한다.

1. Planner가 목표, 범위, 위험, 검증 기준을 먼저 고정한다.
2. Developer가 구현과 1차 검증을 수행한다.
3. Verifier가 독립적으로 반박 검증을 수행한다.
4. 필요한 수정이 있으면 Developer가 반영한다.
5. 최종 보고는 `본 작업 / 검증 결과 / 남은 리스크` 순서로 정리한다.
6. 일정 주기 또는 누적 조건이 맞으면 Cleanup Agent를 별도 실행한다.

---

## 5. Cleanup Agent 실행 트리거

cleanup agent는 아래 중 하나라도 만족하면 돌린다.

- 같은 서브시스템에 기능 수정이 3턴 이상 연속 누적됨
- feature flag 또는 wrapper mode 분기가 늘어남
- 동일 의미의 helper/heuristic이 2개 이상 생김
- live bugfix를 위해 임시 우회가 들어감
- release/demo 직전
- diff가 크지만 삭제보다 우회가 많음

권장 리듬:

- 큰 기능 1개 완료 후 1회
- 릴리스/발표 전 1회
- 주간 1회

---

## 6. OpenClaw 철칙

이 하네스에서 planner, developer, verifier, cleanup 모두 아래 원칙을 지켜야 한다.

1. decision-time에는 OpenClaw 원본 구조를 우선한다.
2. GAIA sidecar 힌트는 보조 정보여야 한다.
3. wrapper는 액션을 대신 결정하지 않는다.
4. 성공 판정은 DOM/trace/probe 같은 실제 증거 기반이어야 한다.
5. fallback은 명시적으로 추적 가능해야 한다.
6. stale state, cached belief, broad semantic tag가 원본 판단을 덮지 않도록 한다.

---

## 7. 작업 단위 규칙

### 7.1 작은 작업

- planner와 developer를 같은 메인 에이전트가 수행해도 된다.
- verifier는 반드시 분리한다.

### 7.2 큰 작업

- planner는 먼저 분할 계획을 만든다.
- developer 작업은 write scope가 겹치지 않게 나눈다.
- verifier는 코드 리뷰와 실행 검증을 분리할 수 있다.

### 7.3 고위험 작업

아래에 해당하면 live verification까지 포함한다.

- 인증
- 결제/민감 정보
- destructive action
- stateful browser workflow
- final verdict logic
- wrapper/backend selection

---

## 8. 보고 형식

사용자 보고는 아래 순서를 기본으로 한다.

1. 코드베이스를 모르는 사람도 이해할 수 있는 문제 설명
2. 왜 그 문제가 중요한지
3. 어떤 방향으로 해결했는지
4. 실제 변경 파일과 구현 내용
5. verifier 결과
6. 남은 리스크

짧은 예시:

```md
문제는 AI가 원본 화면 구조보다 wrapper 힌트에 더 끌려가던 점이었다.
그래서 원본 OpenClaw role tree를 먼저 보게 하고, wrapper는 검증과 추적 역할로만 줄였다.

구현:
- ...

검증:
- ...

남은 리스크:
- ...
```

---

## 9. 하네스 템플릿

아래 템플릿을 매 작업 시작 시 복사해 사용한다.

```md
# Harness Run

## Goal
- ...

## Planner
- Problem:
- Scope:
- Risks:
- Acceptance Criteria:
- Verification Plan:

## Developer
- Planned Changes:
- Files:
- Local Checks:

## Verifier
- Independent Checks:
- Evidence to Review:
- Pass/Fail Rules:

## Cleanup
- Needed Now?: yes | no
- If yes, targets:

## Final Report
- User-level explanation:
- Code-level explanation:
- Verification result:
- Residual risk:
```

---

## 10. 유지 정책

- 이 문서는 실제 운영 방식이 바뀔 때마다 같이 수정한다.
- agent 역할이 늘어나더라도 `planner / developer / verifier / cleanup` 네 축은 유지한다.
- 예외가 생기면 예외를 늘리기보다 기본 하네스를 다시 단순화하는 방향을 우선한다.
