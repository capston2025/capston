# Platform/DevOps 작업 가이드 (Codex 권장)

## 담당자
- 클라우드 관심 팀원

## 권장 도구
- 1순위: Codex
- 대체: Claude Code

## 오너 파일
- `.github/workflows/*`
- `scripts/*`
- `homebrew/Formula/gaia.rb`

## 이번 스프린트 작업
1. CI 품질 게이트(compile/json/schema/smoke)
2. packaging/release 체크 자동화
3. mcp_host health/metrics-lite 운영 확인 절차 문서화

## 리뷰 기준
- 워크플로 실패 시 원인 텍스트가 즉시 식별 가능해야 함
- clean 환경 설치 명령 1세트 재현 가능

## Codex 프롬프트 템플릿
```text
목표: CI와 배포 검증 자동화
결과물: workflow 파일 + 실행 스모크 명령
검증: gaia --help / gaia chat --help
제약: repo 파일 수정만, 비밀키 노출 금지
```
