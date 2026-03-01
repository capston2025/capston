# GAIA 개발 포트폴리오 실행 로그

작성 목적:
- 취업용 포트폴리오에서 "문제 해결 능력"을 증명하기 위한 실전 개발 기록.
- 단순 성공 기록이 아니라 실패 원인, 가설, 수정, 재검증 결과까지 남긴다.

---

## 기록 규칙

1. 모든 항목은 아래 8개 필드로 기록:
- 날짜
- 목표
- 증상/실패
- 원인 분석
- 적용 변경
- 검증 방법
- 결과
- 회고/다음 액션

2. 상태 표기:
- `DONE`: 목표 달성
- `PARTIAL`: 일부 개선, 미해결 잔존
- `BLOCKED`: 외부 요인/의존성으로 진행 불가

3. 업데이트 방식:
- 작업 단위(기능 구현/버그 수정/재실행 검증)마다 항목 1개 추가.
- "왜 실패했는지"를 반드시 기록.

---

## 템플릿 (복붙용)

```md
### [YYYY-MM-DD] 작업 제목
- 상태: DONE | PARTIAL | BLOCKED
- 목표:
- 증상/실패:
- 원인 분석:
- 적용 변경:
- 검증 방법:
- 결과:
- 회고/다음 액션:
```

---

## 작업 로그

### [2026-02-16] Homebrew 배포 초기화
- 상태: PARTIAL
- 목표: `brew tap` + `brew install gaia`로 CLI 설치 가능하게 만들기.
- 증상/실패:
  - `sha256 :no_check`/체크섬 불일치.
  - `Neither setup.py nor pyproject.toml found`로 설치 실패.
  - formula install 과정에서 pip 호출 방식 오류.
- 원인 분석:
  - `main.tar.gz`는 커밋 변경 시 체크섬이 계속 바뀜.
  - 루트 패키징 메타 파일 부재로 pip install 대상 인식 실패.
  - Homebrew의 virtualenv/pip 호출 방식과 formula 코드 불일치.
- 적용 변경:
  - `homebrew-gaia` 별도 tap 저장소 구성.
  - formula `url`, `sha256`, `install` 로직 정비.
  - 루트 패키징 구조 정비(installable 패키지 형태).
- 검증 방법:
  - `brew update`
  - `brew reinstall gaia`
  - `gaia --help`
- 결과:
  - 설치 자체는 통과했으나 GUI 의존성(Pyside/WebEngine) 관련 경고/링키지 이슈가 남음.
- 회고/다음 액션:
  - Homebrew 1차 배포는 CLI 안정화 우선, GUI 의존성 분리 필요.

### [2026-02-17] 단일 진입점 + Chat Hub UX 재설계
- 상태: DONE
- 목표: `gaia` 실행 시 설정 후 바로 실행 가능한 UX 확보.
- 증상/실패:
  - 기존 `plan/spec/resume` 중심 진입이 사용자 입장에서 복잡.
  - `gaia start`에서 무엇을 입력해야 하는지 불명확.
- 원인 분석:
  - 내부 실행 경로 기준 UI였고 사용자 목표 흐름(모델 선택 → 인증 → 실행)이 반영되지 않음.
- 적용 변경:
  - `gaia` 단일 진입.
  - provider/model/auth/url/runtime 설정 후 Chat Hub 진입 구조로 전환.
  - `gaia start`는 alias 유지.
- 검증 방법:
  - `python -m gaia.cli`
  - hub 명령(`/help`, `/test`, `/ai`, `/plan`) 수동 확인.
- 결과:
  - 기본 사용 흐름은 단순화됨.
- 회고/다음 액션:
  - 로그인/인증이 끼는 케이스에서 개입-재개 UX를 더 강화해야 함.

### [2026-02-17] OpenAI OAuth(Codex) 연동 전환
- 상태: DONE
- 목표: API 키 수동 발급 대신 OAuth 기반 인증 흐름 도입.
- 증상/실패:
  - 초기 구현은 API 키 입력 유도.
  - `bytes literal` 비ASCII 문법 오류, 프롬프트/토글 UX 불안정.
- 원인 분석:
  - 인증 방식 요구사항(재사용/신규 OAuth)과 실제 구현이 불일치.
  - CLI 입력 처리 코드 안정성 부족.
- 적용 변경:
  - `reuse/fresh` + OAuth(Codex CLI) 경로 정리.
  - 인증 완료 시 저장 토큰 재사용 경로 확립.
- 검증 방법:
  - `python -m gaia.cli`에서 `fresh oauth` 로그인.
  - 재실행 시 `reuse`로 즉시 통과 확인.
- 결과:
  - OAuth 기반 로그인 동작.
- 회고/다음 액션:
  - 모델 목록/가용성은 계정별 차이를 고려한 표출 방식 필요.

### [2026-02-18] 크로스플랫폼 실행 안정화(팀 환경)
- 상태: PARTIAL
- 목표: macOS/Windows에서 동일하게 실행되도록 의존성과 런타임 오류 제거.
- 증상/실패:
  - `No module named termios` (Windows)
  - `No module named gaia.cli`
  - `No module named fastapi`
  - `HTTP 500` / DOM 분석 실패
  - Codex CLI 인자/UTF-8 관련 오류
- 원인 분석:
  - OS 전용 모듈 분기 부족.
  - editable install 미실행.
  - 필수 의존성/브라우저 설치 누락.
  - Codex CLI 버전/인자 호환성 차이.
- 적용 변경:
  - 설치 표준화: `python -m pip install -e .`
  - 브라우저 설치: `python -m playwright install chromium`
  - mcp_host 자동 기동/로그 경로 정비.
- 검증 방법:
  - 각 OS에서 CLI 실행 및 단일 목표 테스트.
- 결과:
  - 기본 실행은 가능해졌으나, 환경 편차(버전/인코딩) 이슈는 추적 중.
- 회고/다음 액션:
  - 시작 시 사전 점검(preflight) 메시지 자동화 필요.

### [2026-02-21] Ref-only + Snapshot 수명관리 적용
- 상태: PARTIAL
- 목표: selector 기반 불안정 동작을 줄이고 OpenClaw 스타일로 정렬.
- 증상/실패:
  - 동일 버튼 반복 클릭, stale snapshot 반복, no_state_change 루프.
  - 컨텍스트 전환 단계에서 진행이 멈춤.
- 원인 분석:
  - ref 없는 fallback 액션(특히 scroll)이 ref-only 정책과 충돌.
  - stale 복구 및 재매핑은 있었지만 fallback 분기 일부가 비일관.
- 적용 변경:
  - ref-only 강제 경로 확대.
  - stale/ref 재스냅샷-재매핑 경로 강화.
  - reason_code 기반 탈출 로직 강화.
- 검증 방법:
  - 동일 목표 반복 실행 로그 비교.
- 결과:
  - 이전 대비 반복 루프 빈도 감소, 일부 시나리오 성공률 상승.
- 회고/다음 액션:
  - 제약 기반 fallback과 ref 정책의 정합성(특히 scroll) 추가 보정 필요.

### [2026-02-22] PR #71 리뷰 반영 패치
- 상태: DONE
- 목표: AI 리뷰에서 지적된 핵심 결함(P1/P2) 즉시 수정.
- 증상/실패:
  - 제약 fallback에서 ref 없는 `SCROLL` 생성 가능.
  - 메트릭 숫자 파싱이 `1~3자리`/`<=300` 고정.
  - 세션 ID 초 단위 충돌 가능성.
  - DOM 복구 시 시작 URL 강제 복귀로 컨텍스트 손실 가능.
  - `focus` 액션 경로 불일치.
- 원인 분석:
  - ref-only 정책 도입 중 fallback 분기 일부가 구버전 가정 유지.
  - 메트릭 파서가 특정 도메인 숫자 범위를 암묵 가정.
- 적용 변경:
  - `agent.py`
    - ref 없는 scroll fallback 제거(대상 ref 없으면 `WAIT`).
    - 숫자 파싱 확장(콤마 포함 대형 숫자 처리).
    - `collect_min/apply_target` 기반 동적 upper bound 적용.
    - metric term 불일치 시 무차별 숫자 fallback 축소.
    - 중후반 phase DOM 복구에서 start_url 강제 복귀 방지.
    - `SCROLL`도 `element_id` 필수 검증.
  - `session_store.py`
    - session id를 `time.time_ns()` 기반으로 변경.
  - `intelligent_orchestrator.py`
    - `focus`를 `click`으로 정규화.
- 검증 방법:
  - 동일 목표 재실행:
    - "15학점 과목 담기 → 조합 만들기 → 시간표 적용"
- 결과:
  - `status: success`, `steps: 8`.
  - stale/ref 재매핑 성공 로그 확인.
  - 이전의 스크롤 반복 실패 패턴 대비 개선 확인.
- 회고/다음 액션:
  - 기능은 통과했지만 수행 시간(약 335초) 최적화 필요.
  - 동일 시나리오 3회 반복 성공률/평균 시간 측정 필요.

---

## 다음 업데이트 예정 항목

- [ ] 3회 반복 실행(동일 목표) 성공률/평균 step/time 기록
- [ ] Telegram 경유 개입 질문-응답-재개 흐름 로그 정량화
- [ ] 도메인 변경 시(타 사이트) 제약 파서 오탐률 측정
- [ ] OpenClaw parity 미매핑 항목 추적표 추가

