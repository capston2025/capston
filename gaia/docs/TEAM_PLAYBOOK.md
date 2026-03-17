# GAIA 팀 운영 플레이북 (4주)

## Week 1
- Core: 우선순위 백로그 10개와 인터페이스 스키마 승인
- Platform: CI skeleton + smoke 파이프라인
- UX: 온보딩/결과 패널 와이어프레임
- Reliability: 회귀 시나리오 포맷 + 5개 케이스

## Week 2
- Core: 루프 탈출/세션 안정화 핵심 2개 개선
- Platform: Homebrew/패키징 검증 자동화
- UX: 결과 패널/텔레그램 템플릿 반영
- Reliability: 5분 하드닝 + reason_code 리포트 배치
- Reliability: `/test` 자동 Playwright 검증 레일(smoke) 도입

## Week 3
- Core: 기능 확장 2개(자율모드/디스패치)
- Platform: 실패 자동 진단 로그 수집
- UX: 데모 플로우 3개 완성
- Reliability: 30분 장기 러너 + 회귀 10개

## Week 4
- high severity 결함 triage
- 성능/성공률 리포트 정리
- 졸업작품 발표 패키지 고정

## 운영 리듬
- 월: 백로그/우선순위 동기화
- 수: 중간 리뷰(기술 위험 공유)
- 금: 실패 사례 1개 공유(문제-원인-조치-결과)

## Git 산출물 규칙
- 이미지 산출물(`.png/.jpg/.jpeg/.gif/.webp/.svg/.bmp/.ico`)은 저장소에 올리지 않습니다.
- 실행 증빙은 텍스트 로그/JSON 리포트(`artifacts/reports`, `artifacts/hardening`)로 남깁니다.
- 발표용 이미지 예외가 필요하면 명시적 예외 패턴(`!path/to/file.png`)을 합의 후 추가합니다.

## 산출물 위치
- 회귀 시나리오: `gaia/tests/scenarios/*.json`
- Playwright 검증 레일: `gaia/playwright-rail`
- 레일 아티팩트: `gaia/artifacts/validation-rail/<run_id>/<scope>/`
- 하드닝 결과: `artifacts/hardening/*.json`
- reason_code 리포트: `artifacts/reports/reason_code_report_*.{md,json}`
- 팀 오너십: `gaia/docs/TEAM_OWNERSHIP.md`
