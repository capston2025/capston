# GAIA Agent Harness Playbook

## 목적

이 문서는 앞으로 GAIA 작업을 수행할 때 기본 운영 하네스를 고정하기 위한 실행 규약이다.

기본 하네스는 아래 4개 agent로 구성한다.

1. Planner
2. Developer
3. Verifier
4. Cleanup

목표는 단순하다.

- 구현 전에 문제와 성공 조건을 명확히 한다.
- 구현은 한 agent가 책임지고 끝낸다.
- 검증은 다른 agent가 독립적으로 수행한다.
- 주기적으로 AI 슬롭 코드와 불필요한 레이어를 제거한다.

이 문서는 “누가 무엇을 하고, 언제 handoff하며, 무엇이 완료로 간주되는가”를 정의한다.

---

## 핵심 원칙

### 1. Planner는 방향을 정하고, Developer는 구현하고, Verifier는 승인한다

한 agent가 계획, 구현, 승인까지 모두 하면 자기 확증 편향이 생긴다.

따라서 역할을 분리한다.

- Planner: 문제 구조화
- Developer: 실제 변경
- Verifier: 독립 검증

### 2. Cleanup은 선택이 아니라 정기 작업이다

AI가 빠르게 코드를 추가하면 임시 분기, 중복 함수, stale compatibility path, 과도한 prompt steering이 쌓인다.

따라서 Cleanup agent를 주기적으로 돌려 다음을 정리한다.

- AI 슬롭 코드
- 죽은 코드
- 중복 로직
- 더 이상 필요 없는 fallback
- 원래 아키텍처 목표를 흐리는 heuristic

### 3. OpenClaw 판단을 보존하고 GAIA는 얇은 wrapper로 남는다

GAIA의 목표는 OpenClaw를 대체하는 것이 아니라, 그 위에서 실행 안정화와 QA 루프를 제공하는 것이다.

따라서 아래를 지킨다.

- decision-time의 1차 근거는 OpenClaw raw role tree / raw snapshot이다
- GAIA는 실행, stale recovery, post-action probe, trace, final verdict 위주로 개입한다
- wrapper가 agent보다 먼저 의미를 확정하거나 행동을 강제하는 코드는 최소화한다

### 4. 새 코드를 추가하기 전, 먼저 삭제 가능한 코드를 찾는다

증상이 새 heuristic 하나로 해결되어 보여도, 기존 분기와 충돌하면 오히려 전체 정확도가 떨어진다.

기본 우선순위는 아래와 같다.

1. 불필요한 분기 제거
2. 기존 계약 정리
3. 필요한 최소 변경 추가

### 5. 성공은 “동작함”이 아니라 “독립 검증됨”이다

Developer가 성공했다고 말하는 것만으로는 충분하지 않다.

최소한 아래 둘 중 하나가 필요하다.

- Verifier agent의 독립 재현
- 자동화 테스트 + trace/log 증거

### 6. 독립 검증은 fresh context를 써야 한다

같은 대화 문맥과 같은 서술을 그대로 넘기면 Verifier도 Developer의 결론을 따라가기 쉽다.

따라서 기본 규칙은 아래와 같다.

- Verifier는 가능하면 별도 subagent 또는 fresh session에서 시작한다
- 첫 검증 패스는 Developer 설명보다 artifact와 재현 명령을 먼저 본다
- Developer의 해석은 첫 재현 후 비교 대상으로만 사용한다
- Verifier는 기본적으로 read-only 검토를 우선하고, 수정이 필요하면 Findings로 되돌린다

---

## Agent 역할 정의

## 1. Planner

### 책임

- 사용자 요청을 작업 가능한 문제로 정리한다
- 성공 조건과 실패 조건을 명확히 한다
- 수정 범위를 좁힌다
- 어떤 증거로 검증할지 미리 정한다

### 입력

- 사용자 요청
- 현재 코드 상태
- 관련 trace / log / failing run
- 기존 설계 문서

### 출력

Planner는 최소 아래 항목을 남겨야 한다.

1. 문제 요약
2. 목표 상태
3. 비목표
4. 수정 후보 파일
5. 검증 방법
6. 리스크

### 금지 사항

- 본격적인 구현을 시작하지 않는다
- “대충 여기 고치면 될 듯” 수준의 추정으로 범위를 넓히지 않는다
- 검증 계획 없이 구현 단계로 넘기지 않는다

### Planner 산출물 템플릿

```md
## Planner Brief
- Problem:
- Goal:
- Non-goals:
- Likely files:
- Validation:
- Risks:
```

---

## 2. Developer

### 책임

- Planner가 정의한 범위 안에서 실제 수정한다
- 삭제 가능한 코드를 먼저 제거한다
- 테스트, trace, 로그를 남긴다

### 입력

- Planner brief
- 현재 브랜치 상태
- 관련 테스트와 trace

### 출력

Developer는 최소 아래 항목을 남겨야 한다.

1. 무엇을 바꿨는지
2. 왜 이 방식이 최소 변경인지
3. 어떤 테스트/재현을 돌렸는지
4. 남은 리스크가 무엇인지
5. Verifier가 그대로 재생할 수 있는 실행 메타데이터

### 필수 규칙

- 새 heuristic를 넣기 전 기존 heuristic와 충돌 여부를 확인한다
- 가능하면 add보다 delete를 우선한다
- unrelated diff는 건드리지 않는다
- traceability를 위해 관련 로그나 아티팩트를 남긴다
- run 단위 재현 정보를 빠짐없이 남긴다

### OpenClaw 관련 규칙

- raw role tree를 decision-time 1차 근거로 유지한다
- wrapper 의미 태그는 보조로만 사용한다
- action을 controller가 먼저 확정하는 코드는 경계한다
- success 판단은 DOM evidence, probe, trace로 닫는다

### Developer 산출물 템플릿

```md
## Developer Report
- Change:
- Why this is the minimal change:
- Files touched:
- Validation run:
- Replay command:
- Run id / artifact path:
- Backend:
- Wrapper mode:
- Model:
- Commit SHA:
- Git dirty state:
- Residual risk:
```

---

## 3. Verifier

### 책임

- Developer의 주장을 믿지 않고 독립적으로 검증한다
- 회귀 가능성과 과도한 복잡도 증가를 지적한다
- 필요하면 reject한다

### 입력

- Planner brief
- 재현 명령
- run id / artifact path
- trace / screenshot / log
- commit sha / dirty state / backend / wrapper mode / model
- 수정된 코드
- 테스트 결과

Developer report는 첫 검증 패스가 끝난 뒤 참고 자료로 본다.

### 출력

Verifier는 아래 형태로 남긴다.

1. verdict: pass / conditional pass / fail
2. 핵심 근거
3. 발견한 문제
4. 추가로 필요한 증거

### 검증 기준

- 성공 trace가 실제 목표와 맞는가
- false positive 가능성이 없는가
- 새 분기가 기존 아키텍처 목표를 해치지 않는가
- 테스트가 실제 회귀를 막을 수 있는가
- wrapper가 LLM 판단을 과하게 대신하지 않는가
- replay command만으로 같은 결론을 다시 얻을 수 있는가
- backend / wrapper mode drift가 없는가

### 금지 사항

- 구현을 슬쩍 고쳐서 검증 결과를 만들지 않는다
- Developer의 설명만 보고 pass하지 않는다
- 같은 세션 상태를 그대로 재사용한 채 독립 검증이라고 부르지 않는다

### Verifier 산출물 템플릿

```md
## Verifier Report
- Verdict:
- Evidence:
- Findings:
- Needed follow-up:
```

---

## 4. Cleanup

### 책임

- 주기적으로 코드를 정리해 시스템이 다시 heuristic 덩어리가 되는 것을 막는다
- 기능 추가보다 구조 단순화에 집중한다

### 입력

- 최근 변경 파일
- 누적 trace / logs
- long-lived TODO / fallback / compatibility code

### 출력

Cleanup agent는 아래 둘 중 하나를 남긴다.

1. 실제 제거한 코드 목록
2. 아직 제거하지 못한 항목과 근거

### 기본 탐지 대상

- 더 이상 타지 않는 분기
- 복구 경로와 정상 경로가 뒤섞인 코드
- broad semantic tags
- stale state machine remnants
- sticky process-global fallback
- 중복 prompt rules
- no-op compatibility shim
- dead env var / config path
- trace에만 쓰고 실제 판단에 가치 없는 요약 계층

### AI 슬롭 판정 기준

아래 중 2개 이상이면 Cleanup 대상이다.

- 같은 의미의 조건문이 여러 파일에 퍼져 있음
- 실패 원인을 가리는 fallback이 늘어남
- raw evidence 대신 요약 문자열에 과도하게 의존함
- 이전 버그를 덮으려는 guard가 누적됨
- 테스트가 구현 세부에 과도하게 묶여 있음

### Cleanup 기본 주기

Cleanup agent는 아래 시점마다 돌린다.

1. net 변경이 300라인 이상일 때
2. 새 heuristic / fallback / guard를 추가했을 때
3. release / demo 전
4. 같은 영역을 3회 이상 연속 수정했을 때

### Cleanup 산출물 템플릿

```md
## Cleanup Report
- Removals:
- Simplifications:
- Remaining debt:
- Risk if deferred:
```

---

## 표준 실행 순서

기본 순서는 아래와 같다.

1. Planner brief 작성
2. Developer 구현
3. Verifier 독립 검증
4. 조건 충족 시 Cleanup 수행
5. 최종 보고

짧은 작업도 이 흐름을 유지한다. 다만 산출물 길이만 줄일 수 있다.

### Fast Path

아래 조건이면 축약 실행이 가능하다.

- 변경이 30라인 미만
- 새 heuristic 없음
- 아키텍처 경계 영향 없음

이 경우에도 Verifier는 생략하지 않는다.

---

## 하네스 입력/출력 계약

각 agent는 이전 agent의 출력을 입력으로 사용한다.

### Planner -> Developer

반드시 전달할 것:

- 문제 정의
- 성공 조건
- 수정 범위
- 검증 계획

### Developer -> Verifier

반드시 전달할 것:

- replay command
- run id / artifact path
- trace / screenshot / log 위치
- backend
- wrapper mode
- model
- commit sha
- git dirty state
- 실행한 테스트
- 아직 확실하지 않은 부분

권장:

- 첫 handoff에는 해석보다 증거를 먼저 둔다
- Verifier는 이 입력만으로 1차 재현을 시도한다

### Verifier -> Final

반드시 전달할 것:

- pass / fail
- pass라면 근거
- fail이라면 차단 사유
- 다음 수정 우선순위

---

## OpenClaw Thin-Wrapper 규칙

이 프로젝트에서는 아래를 아키텍처 철칙으로 둔다.

### GAIA가 해야 할 일

- session/bootstrap
- action dispatch
- stale ref recovery
- post-action probe
- trace/log capture
- final QA verdict

### GAIA가 최소화해야 할 일

- decision-time 의미 강제
- LLM보다 먼저 행동 후보 확정
- broad heuristic tagging
- legacy state machine을 통한 과도한 steering

### Verifier가 항상 확인할 것

- raw OpenClaw evidence가 실제로 decision에 쓰였는가
- wrapper summary가 주 입력을 덮지 않았는가
- fallback이 조용히 backend를 바꾸지 않았는가
- 최종 성공이 real evidence인지 false positive인지

---

## 권장 산출물 위치

하네스 자체는 문서 프로세스지만, 가능한 경우 아래 위치를 따른다.

- planner notes: `artifacts/plans/`
- runtime trace: `artifacts/wrapper_trace/`
- validation logs: `artifacts/reports/` 또는 관련 런 디렉터리
- cleanup notes: `gaia/docs/` 또는 관련 PR/commit 메시지

### 최소 재현 번들

Verifier가 없던 버그를 상상하지 않도록, Developer는 최소 아래를 남긴다.

- replay command 1개
- run id 또는 artifact directory 1개
- backend와 wrapper mode
- model
- commit sha
- dirty state 여부
- 핵심 trace/log 경로

---

## 완료 정의

작업은 아래를 만족할 때 완료로 본다.

1. Planner가 성공 조건을 명시했다
2. Developer가 구현과 재현을 남겼다
3. Verifier가 독립 검증했다
4. Cleanup 필요 조건이 있으면 수행했다
5. 최종 보고에 남은 리스크가 분명히 적혔다

이 5개가 충족되지 않으면 “수정은 있었지만 하네스 완료는 아님”으로 간주한다.

---

## 앞으로의 기본 운영 방침

앞으로 GAIA의 비단순 작업은 이 문서를 기본 하네스로 사용한다.

- planner 없이 바로 구현하지 않는다
- verifier 없이 완료 처리하지 않는다
- cleanup을 미루더라도 이유를 남긴다
- OpenClaw 보존 원칙과 충돌하는 heuristic는 우선적으로 제거 후보로 본다

---

## 복사용 Harness Run 템플릿

아래 템플릿은 실제 작업 시작 시 그대로 복사해서 채우면 된다.

```md
# Harness Run

## Planner Brief
- Problem:
- Goal:
- Non-goals:
- Likely files:
- Validation:
- Risks:

## Developer Report
- Change:
- Why this is the minimal change:
- Files touched:
- Validation run:
- Replay command:
- Run id / artifact path:
- Backend:
- Wrapper mode:
- Model:
- Commit SHA:
- Git dirty state:
- Residual risk:

## Verifier Report
- Verdict:
- Evidence:
- Findings:
- Needed follow-up:

## Cleanup Report
- Needed now?: yes | no
- Removals:
- Simplifications:
- Remaining debt:
- Risk if deferred:

## Final User Report
- Plain-language explanation:
- Code-level explanation:
- Verification result:
- Residual risk:
```
