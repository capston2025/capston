# Core Lead 작업 가이드 (Codex 권장)

## 담당자
- 메인 개발자 (Core AI/Execution Lead)

## 권장 도구
- 1순위: Codex
- 대체: Claude Code

## 오너 파일
- `gaia/src/phase4/goal_driven/agent.py`
- `gaia/src/phase4/goal_driven/agent.py`
- `gaia/src/phase4/mcp_host.py`

## 이번 스프린트 작업
1. Master/Worker 전환 규칙 정리(phase 전환, no_progress 탈출)
2. ref-only/stale 복구 정책 점검
3. 실행 실패 상위 reason_code 3개 개선

## 리뷰 기준
- reason_code 변화가 로그로 설명 가능해야 함
- 회귀 시나리오 2개 이상 재현 명령 첨부

## Codex 프롬프트 템플릿
```text
목표: <문제 시나리오>
제약: ref-only 유지, 하드코딩 금지
검증: 동일 시나리오 2회 재실행 시 reason_code 비교
수정 파일: <파일 목록>
```
