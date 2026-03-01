# Product/UI 작업 가이드 (Claude Code 권장)

## 담당자
- 창업/UI 관심 팀원

## 권장 도구
- 1순위: Claude Code
- 대체: Codex

## 오너 파일
- `gaia/main.py`
- `gaia/src/gui/*`
- `README.md`

## 이번 스프린트 작업
1. 온보딩/결과 패널 정보 구조 단순화
2. 실패 원인 표시(사용자 액션 가이드 포함)
3. 데모 시나리오 3개 문서화

## 리뷰 기준
- 비개발자가 1회 읽고 실행할 수 있어야 함
- GUI 결과/텔레그램 결과 문구 일관성 유지

## Claude 프롬프트 템플릿
```text
목표: 비개발자 데모용 UX 개선
범위: gaia/main.py, gaia/src/gui/*, README.md
필수: 성공/실패/개입대기 상태를 한눈에 구분
제약: 기능 로직 변경 최소화, 표현 계층 중심
```
