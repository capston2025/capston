# GAIA 포트폴리오 초안 (제출용 뼈대 + 트러블슈팅)

이 문서는 채용 포트폴리오에 바로 옮겨 적을 수 있는 형태로 구성했다.  
상세 실행 이력/원본 로그는 `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`를 증빙으로 사용한다.

## 중간발표 v2 동기화 링크
- 발표 대본(12슬라이드): `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/MIDTERM_15MIN_V2.md`
- 슬라이드 근거 매핑: `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/SLIDE_EVIDENCE_MAP.md`
- 실행 로그 증빙: `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`

---

## 1) 한 줄 소개

`GAIA`는 PDF 기획서를 입력받아 테스트 시나리오를 생성하고, 브라우저를 실제 조작해 검증/리포트까지 수행하는 LLM 기반 웹 QA 자동화 시스템이다.

---

## 2) 프로젝트 개요

### 문제 정의
- 기존 웹 테스트 자동화는 셀렉터 취약성, 시나리오 유지보수 비용, 로그인/상태 전환 실패가 빈번했다.
- 데모/운영 환경에서는 “성공처럼 보이지만 실제 적용이 안 되는” 허위 성공(false positive)이 많았다.

### 목표
- 기획서 기반 시나리오 생성 + 브라우저 실행 + 결과 리포트까지 end-to-end 자동화.
- 실행 안정성 향상: `Ref-only`, `stale 복구`, `세션 연속성`, `사용자 개입 후 재개`.

### 기술 스택
- `Python`, `FastAPI`, `Playwright`, `PySide6`
- LLM: `OpenAI/Gemini` 라우팅
- 패키징/배포: `pyproject`, `Homebrew tap(formula)`

---

## 3) 내가 주도한 핵심 개선

### A. CLI/실행 UX 재설계
- `gaia` 단일 진입점으로 단순화.
- 설정 흐름: provider/model/auth/url/runtime/control.
- Chat Hub 중심 명령(`/test`, `/ai`, `/plan`, `/memory`, `/session`)으로 운영 동선 통합.

### B. 인증 체계 전환
- OpenAI 인증을 `reuse/fresh` 전략으로 분리.
- OAuth(Codex) 경로와 토큰 재사용 경로 정리.
- 인증 실패 시 재시도/가이드 흐름 명확화.

### C. Ref-only 실행 정책 강화
- element 액션은 `snapshot_id + ref_id` 필수.
- selector 직접 실행 경로 차단(정책 일관화).
- `stale_snapshot` 발생 시 재스냅샷 + 재매핑 복구 경로 유지.

### D. 세션 연속성/운영성
- 세션 포인터를 `~/.gaia/sessions`에 저장하고 재실행 시 재사용.
- Telegram 원격 제어(옵션) + 로컬 Chat Hub 공통 dispatcher 운영.

### E. 배포 파이프라인 구축
- Homebrew tap(`homebrew-gaia`) 구성.
- formula/sha256 갱신 루프 안정화.
- 설치/실행 문서 정비.

---

## 4) 아키텍처 관점 성과(전/후)

### Before
- 실행마다 세션/컨텍스트가 자주 끊김.
- selector fallback/legacy 경로가 섞여 정책 불일치.
- 로그인/모달 상황에서 반복 루프, 실패 원인 추적 어려움.

### After
- session_key 기반 세션 재사용으로 컨텍스트 유지.
- ref-only 정책 고정 + reason_code 표준화로 디버깅 가능성 증가.
- 실패 시 `stale/ref` 복구, 사용자 개입 요청, 재개 흐름 정착.

---

## 5) 트러블슈팅 사례 (실제 작업 기반)

아래 케이스는 실제 며칠간 진행한 이슈를 요약한 것이다.

### 사례 1. Homebrew 설치 실패 연쇄
- 증상:
  - checksum mismatch
  - `pyproject.toml/setup.py not found`
  - formula pip install 오류
- 원인:
  - main tarball 체크섬 변동, 패키징 메타/설치 경로 미정합
- 조치:
  - tap 분리, formula 수정, pyproject 기반 installable 패키지 구조 정리
- 결과:
  - `brew reinstall gaia` 설치 가능 상태 확보
- 포인트:
  - 배포 문제를 코드 문제가 아닌 “패키징/릴리즈 정책” 문제로 분리해 해결

### 사례 2. 팀 환경에서 실행 안 됨 (macOS/Windows 편차)
- 증상:
  - `No module named termios`, `No module named fastapi`, `No module named gaia.cli`
  - DOM 500/HTTP 500 에러
- 원인:
  - OS 전용 모듈/설치 누락/브라우저 설치 누락
- 조치:
  - 설치 표준 절차 정리: `python -m pip install -e .`, `python -m playwright install chromium`
  - mcp_host 로그 확인 루트 정리
- 결과:
  - 팀원 다수가 동일 기준으로 실행 가능
- 포인트:
  - “코드 수정”보다 “재현 가능한 설치 표준화”가 먼저라는 운영 관점 확립

### 사례 3. ref-only 정책인데 fallback이 ref-less scroll을 발행
- 증상:
  - 제약 fallback에서 `SCROLL` 반복 실패, `ref_required` 루프
- 원인:
  - 일부 fallback 분기가 ref-only 가정을 위반
- 조치:
  - scroll fallback에 ref 대상 element_id 강제
  - 대상 없으면 `WAIT`로 전환
  - `SCROLL`도 element_id 필수 검증
- 결과:
  - `ref_required` 루프 감소, 진행성 회복
- 포인트:
  - 정책 도입 시 “핵심 실행 경로 + fallback 경로”를 같이 맞춰야 함

### 사례 4. 메트릭 파싱 오판(숫자 300 고정/노이즈)
- 증상:
  - 목표 수치 판단이 왜곡되거나 조기 전환
- 원인:
  - 정규식 `\d{1,3}`, 상한 `<=300`, term 미매칭 시 무차별 숫자 fallback
- 조치:
  - 콤마 포함 큰 수치 파싱
  - `collect_min/apply_target` 기반 동적 상한
  - term 미매칭 시 보수적 추출(문맥 기반 우선)
- 결과:
  - 목표 제약 판정 안정성 개선
- 포인트:
  - LLM 이전에 “입력 신호 품질(파싱/추정)”이 정확도를 결정

### 사례 5. stale snapshot 반복 실패
- 증상:
  - 동일 의도 클릭 반복, stale snapshot 에러 다발
- 원인:
  - DOM 변화 후 ref 수명 불일치, 복구 타이밍 미스
- 조치:
  - stale 시 재스냅샷/재매핑 재시도 경로 유지 및 보강
  - reason_code 기반 전략 전환
- 결과:
  - 실행 로그에 `stale/ref 재매핑 후 성공` 케이스 확인
- 포인트:
  - “복구 실패 자체”보다 “복구를 관측 가능하게 만든 것”이 운영 품질 핵심

### 사례 6. 실제 시나리오 회귀 테스트 비교
- 시나리오:
  - `inuu-timetable`에서 15학점 조합 생성 후 시간표 적용
- 이전:
  - 스크롤/수집 루프 후 실패
- 수정 후:
  - `status: success`, `steps: 8`, `effective=true` 적용 확인
- 포인트:
  - 동일 목표 비교로 개선 효과를 정량 증명

---

## 6) 정량/정성 성과 예시 (작성 가이드)

아래는 포트폴리오 제출 시 채우면 좋은 항목:

- 실행 성공률: `__% -> __%`
- 평균 step: `__ -> __`
- 평균 실행 시간: `__s -> __s`
- 루프성 실패(`ref_required/no_state_change`) 비율: `__% 감소`
- 재현 가능한 설치 성공률(팀원 n명 기준): `__ / __`

정성 성과:
- 실패 원인을 reason_code로 구조화해 디버깅 시간 단축
- 사용자 개입 후 재개(중단 종료 최소화)
- 배포/문서/운영 루틴 정착

---

## 7) 면접에서 말할 핵심 포인트

### Q1. “가장 어려웠던 기술 문제는?”
- A: ref-only 정책 도입 후 fallback 경로가 정책을 깨면서 생긴 무한 실패 루프.
- 해결: fallback까지 ref 기반으로 통일 + 대상 없을 때 WAIT 전략으로 전환.

### Q2. “LLM 프로젝트에서 정확도는 어떻게 올렸나?”
- A: 프롬프트보다 먼저 실행 신호(메트릭 파싱/상태 변화 검증/reason_code)를 안정화했다.
- 결과: 같은 목표 시나리오에서 반복 실패 패턴이 성공 시나리오로 전환.

### Q3. “실무 관점으로 배운 점은?”
- A: 기능 개발보다 설치/배포/로그 표준화가 팀 생산성에 더 큰 영향을 준다.

---

## 8) 제출 패키지 권장 구성

- 문서 1: 프로젝트 소개서(본 문서 기반, 2~4페이지)
- 문서 2: 트러블슈팅 리포트(케이스 3개 상세)
- 문서 3: 실행 로그 증빙(`PORTFOLIO_RUN_LOG.md` 발췌)
- 부록:
  - 주요 스크린샷(GUI, 로그, 성공 결과 화면)
  - PR 링크/커밋 링크

---

## 9) 다음 업데이트 TODO

- [ ] 동일 시나리오 3회 반복 성공률 기록
- [ ] Telegram 원격 개입(질문→응답→재개) 완주 사례 추가
- [ ] 타 도메인 1~2개 추가로 범용성 검증 결과 기재
- [ ] 성능 지표(평균 실행시간/스텝) 표 추가
