# GAIA 팀 오너십 맵 (4인)

## 목표
- 기능 확장을 우선하면서, 1인 병목을 줄이기 위해 코드 오너십을 고정합니다.

## 역할 배정

### 1) Core AI/Execution Lead (메인)
- 오너:
  - `gaia/src/phase4/goal_driven/agent.py`
  - `gaia/src/phase4/intelligent_orchestrator.py`
  - `gaia/src/phase4/mcp_host.py`
- 책임:
  - Master/Worker 전략 품질 결정
  - ref-only/stale 복구 정책 최종 승인
  - 아키텍처 리뷰 승인권

### 2) Platform/DevOps Owner (클라우드 관심)
- 오너:
  - `.github/workflows/*`
  - `scripts/*`
  - `homebrew/Formula/gaia.rb`
- 책임:
  - CI 3단계(lint/static, smoke, packaging)
  - 배포 자동화/헬스체크 운영
  - 설치/환경 가이드 품질

### 3) Product UX & Presentation Owner (창업/UI 관심)
- 오너:
  - `gaia/main.py`
  - `gaia/src/gui/*`
  - `README.md`
- 책임:
  - 사용자 온보딩/결과 가독성 개선
  - 데모 시나리오 3개 운영
  - 텔레그램/GUI 결과 표현 일관성

### 4) Reliability/Test Harness Owner (임베디드)
- 오너:
  - `gaia/src/phase4/tool_loop_detector.py`
  - `gaia/src/phase4/session/*`
  - `gaia/src/phase4/memory/*`
  - `gaia/src/phase4/goal_driven/test_*.py`
  - `gaia/tests/scenarios/*`
- 책임:
  - 회귀 테스트/하드닝 러너 운영
  - reason_code 분포 리포트 자동화
  - no_state_change 루프/복구 정책 검증

## 공통 규칙
- PR 400라인 내(코어 파일 제외)
- 모든 PR에 재현 명령 1개 + 기대 로그 3줄 첨부
- `phase4/*`는 메인 승인 없이는 merge 금지
- 문서 영향 체크 필수 (README 또는 docs 업데이트)
