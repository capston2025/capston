# Reliability/Test Harness 작업 가이드 (Codex 권장)

## 담당자
- 임베디드 관심 팀원

## 권장 도구
- 1순위: Codex
- 대체: Claude Code

## 오너 파일
- `gaia/src/phase4/tool_loop_detector.py`
- `gaia/src/phase4/session/*`
- `gaia/src/phase4/memory/*`
- `gaia/src/phase4/goal_driven/test_*.py`
- `gaia/tests/scenarios/*`

## 이번 스프린트 작업
1. 5분/30분 하드닝 러너 운영
2. reason_code 분포 리포트 자동 생성
3. 회귀 시나리오 10개 유지/확장

## 리뷰 기준
- 회귀 시나리오 포맷 준수
- 동일 시나리오 재실행 시 결과 비교 가능

## Codex 프롬프트 템플릿
```text
목표: 장시간 실행 안정성 검증
입력: gaia/tests/scenarios/*.json
출력: artifacts/hardening/*.json + artifacts/reports/*.md
제약: 사이트 하드코딩 금지, reason_code 중심 분석
```
