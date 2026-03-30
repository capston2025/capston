# GAIA 중간발표 15분 대본 v2 (12슬라이드)

이 문서는 중간발표용 단일 소스 오브 트루스다.  
PPT 원본은 레포 외부에서 관리하고, 본 문서는 발표 대본/키메시지/증빙 경로를 고정한다.

---

## Slide 1
- 슬라이드 제목: `GAIA: 다중 인터페이스 기반 에이전트 테스트 운영 플랫폼`
- 발표 시간(시작~끝): `0:00 ~ 0:50`
- 발표 대본(최종 문구):
  - 안녕하세요. 저희는 capston의 GAIA 파트를 진행했습니다.
  - GAIA는 단순 테스트 자동화 스크립트가 아니라,
  - 기획서 기반 테스트 생성, 채팅/CLI 오더 실행, 자율 실행까지 지원하는 테스트 운영 플랫폼입니다.
- 키메시지 1줄: `GAIA는 스크립트가 아니라 운영 가능한 테스트 플랫폼이다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_SKELETON.md`
- Q&A 대응 문장: `한 줄로 말하면 “기획서-코드-채팅 입력을 단일 실행 엔진으로 통합한 시스템”입니다.`

## Slide 2
- 슬라이드 제목: `문제정의: 왜 필요했는가`
- 발표 시간(시작~끝): `0:50 ~ 2:00`
- 발표 대본(최종 문구):
  - 기존 GUI 테스트는 작성/유지 비용이 크고, UI가 조금만 바뀌어도 쉽게 깨집니다.
  - 또한 개발자만 쓸 수 있는 도구인 경우가 많아 운영팀/기획팀 활용이 어렵습니다.
  - 저희 목표는 “누가, 어떤 채널에서 요청해도, 같은 테스트 엔진으로 실행되는 구조”입니다.
- 키메시지 1줄: `유지보수 비용과 사용자 장벽을 동시에 낮추는 것이 목표다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_SKELETON.md`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`
- Q&A 대응 문장: `문제의 핵심은 자동화 성공률보다, 변경 대응성과 운영 접근성입니다.`

## Slide 3
- 슬라이드 제목: `핵심 개념: 3-Mode + 3-Interface`
- 발표 시간(시작~끝): `2:00 ~ 3:20`
- 발표 대본(최종 문구):
  - GAIA는 3개의 실행 모드와 3개의 인터페이스를 결합한 구조입니다.
  - 같은 엔진을 공유하면서도 사용자 역할에 맞게 진입점만 바뀝니다.
  - 이게 저희 시스템의 핵심 차별점입니다.
- 키메시지 1줄: `입력 채널이 달라도 실행 엔진은 하나다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/cli.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/chat_hub.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/gui/intelligent_worker.py`
- Q&A 대응 문장: `역할별 진입점만 다르고, 실행 계약은 동일하게 유지합니다.`

| Mode | 설명 |
|---|---|
| Plan-driven | 기획서(PDF/Spec) 기반 테스트 생성 및 실행 |
| Order-driven | CLI/Telegram 명령 기반 즉시 실행 |
| Agent-driven | 자율 계획/실행/복구 |

| Interface | 사용 맥락 |
|---|---|
| GUI | 데모/운영자 시각화 |
| CLI(Code) | 개발자 즉시 실행/자동화 |
| Chat(Telegram) | 원격 명령/개입/재개 |

## Slide 4
- 슬라이드 제목: `아키텍처: 입력 채널 -> 공통 실행 엔진 -> 리포트/알림`
- 발표 시간(시작~끝): `3:20 ~ 4:20`
- 발표 대본(최종 문구):
  - 입력은 기획서, CLI 명령, Telegram 메시지, 에이전트 채팅에서 들어옵니다.
  - 이후 공통 실행 엔진에서 분석-액션-복구를 수행하고, 결과를 로그/리포트/알림 채널로 반환합니다.
  - 즉, “채널은 다양하지만 실행 핵심은 단일화”한 구조입니다.
- 키메시지 1줄: `다중 입력, 단일 실행 코어.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_host.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/agent.py`
- Q&A 대응 문장: `채널별 분기 로직이 아니라 공통 실행 계약을 우선 설계했습니다.`

## Slide 5
- 슬라이드 제목: `시연 A (기본 플로우)`
- 발표 시간(시작~끝): `4:20 ~ 5:40`
- 발표 대본(최종 문구):
  - 먼저 기본 동작입니다. 목표를 주면 페이지 분석 후 액션을 수행하고, 결과를 반환합니다.
  - 여기까지가 최소 동작이고, 다음이 더 중요합니다. 실패 복구입니다.
- 키메시지 1줄: `기본 성공 플로우를 먼저 짧게 증명한다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
  - `/Users/coldmans/Documents/GitHub/capston/scripts/run_hardening.py`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`
- Q&A 대응 문장: `데모 A는 엔진 정상 동작 확인용이고, 데모 B가 차별점 검증 구간입니다.`

## Slide 6
- 슬라이드 제목: `시연 B (복구 플로우)`
- 발표 시간(시작~끝): `5:40 ~ 7:10`
- 발표 대본(최종 문구):
  - 첫 시도가 실패한 상황에서 대체 액션을 자동 시도하는 장면입니다.
  - 핵심은 실패 자체가 아니라 “실패 후 성공 확률을 높이는 설계”입니다.
- 키메시지 1줄: `복구 전략이 실제 성공률을 끌어올린다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/agent.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_ref/action_exception_recovery.py`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`
- Q&A 대응 문장: `실패를 숨기지 않고 reason_code와 복구 경로를 함께 보여주는 구조입니다.`

## Slide 7
- 슬라이드 제목: `확장 기능: 운영 친화 요소`
- 발표 시간(시작~끝): `7:10 ~ 8:40`
- 발표 대본(최종 문구):
  - 저희는 기능 구현에서 끝내지 않고 운영 관점 확장을 같이 진행했습니다.
  - Telegram 오더/알림, CLI 즉시 실행, GUI 데모 실행을 같은 흐름으로 묶었습니다.
  - GPT 구독 인증 기반 접근 경로를 열어 온보딩 마찰을 낮췄습니다.
- 키메시지 1줄: `실행 기능 + 운영 기능을 함께 만든 프로젝트다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/cli.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/chat_hub.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/auth.py`
- Q&A 대응 문장: `온보딩 비용 감소를 위해 인증/채널 설정도 제품 기능으로 다뤘습니다.`

## Slide 8
- 슬라이드 제목: `현재까지 한 일: 무엇을 만들고 검증했는가`
- 발표 시간(시작~끝): `8:40 ~ 10:00`
- 발표 대본(최종 문구):
  - 현재는 핵심 실행 루프, 복구 흐름, 다중 인터페이스 진입, 알림 채널 연동까지 구현했습니다.
  - 즉, 개별 기능이 아니라 “플랫폼 형태”로 통합된 상태입니다.
- 키메시지 1줄: `개별 기능 데모가 아니라 통합 플랫폼 상태다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_SKELETON.md`
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
- Q&A 대응 문장: `핵심 루프/복구/채널 통합이 이미 연결된 상태라는 점이 현재 완료 범위입니다.`

## Slide 9
- 슬라이드 제목: `남은 과제: 기술 부채/리스크`
- 발표 시간(시작~끝): `10:00 ~ 11:20`
- 발표 대본(최종 문구):
  - 남은 과제는 명확합니다.
  - 구조 분리, 예외 정책 통일, wait 전략 개선, 관측성 강화입니다.
  - 이 4가지를 마무리하면 안정성이 크게 올라갑니다.
- 키메시지 1줄: `남은 일은 기능 확장이 아니라 안정성 정리다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/TEAM_PLAYBOOK.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/agent.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_host.py`
- Q&A 대응 문장: `리스크를 알고 있고, 우선순위/기간/지표까지 고정해 대응합니다.`

## Slide 10
- 슬라이드 제목: `정량 목표: 완료 기준을 수치로 정의`
- 발표 시간(시작~끝): `11:20 ~ 12:40`
- 발표 대본(최종 문구):
  - 최종 목표는 데모 완성이 아니라 수치 달성입니다.
  - 성공률, 플레이키, 실행 시간, 알림 누락률, 온보딩 시간을 기준으로 객관적으로 완료 여부를 판단하겠습니다.
- 키메시지 1줄: `완료 기준은 느낌이 아니라 지표다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_SKELETON.md`
  - `/Users/coldmans/Documents/GitHub/capston/scripts/reason_code_report.py`
  - `/Users/coldmans/Documents/GitHub/capston/scripts/run_hardening.py`
- Q&A 대응 문장: `성공률뿐 아니라 재현성/복구율까지 함께 추적합니다.`

## Slide 11
- 슬라이드 제목: `향후 계획: 3주 마감 플랜`
- 발표 시간(시작~끝): `12:40 ~ 14:00`
- 발표 대본(최종 문구):
  - 범위를 늘리지 않고, 핵심 품질 지표 달성 중심으로 마무리하겠습니다.
  - 1주차 모듈 분리/예외 정책, 2주차 wait/로그 개선, 3주차 회귀 검증/문서화를 진행합니다.
  - 발표 이후엔 결과 지표와 함께 최종 리포트를 공유하겠습니다.
- 키메시지 1줄: `범위 고정 + 품질 지표 중심으로 마감한다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/TEAM_PLAYBOOK.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/TEAM_OWNERSHIP.md`
- Q&A 대응 문장: `기능 추가보다 결함 제거와 재현성 확보를 우선합니다.`

## Slide 12
- 슬라이드 제목: `마무리: 한 줄 결론`
- 발표 시간(시작~끝): `14:00 ~ 15:00`
- 발표 대본(최종 문구):
  - GAIA는 “스크립트 자동화 도구”가 아니라,
  - 기획서/코드/채팅을 하나의 실행 엔진으로 연결한 다중 인터페이스 테스트 운영 플랫폼입니다.
  - 이상 발표 마치고 질문 받겠습니다.
- 키메시지 1줄: `GAIA의 본질은 엔진 통합과 운영성이다.`
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/SLIDE_EVIDENCE_MAP.md`
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
- Q&A 대응 문장:
  - 차별점: `3-Mode + 3-Interface로 누가 요청해도 같은 엔진으로 실행됩니다.`
  - 왜 Telegram/CLI/GUI 다 지원: `개발자·운영자·기획자 사용 맥락이 다르기 때문입니다.`
  - 기획서 기반 이점: `요구사항-테스트 추적성과 변경 반영 속도를 확보합니다.`
  - 완성 기준: `성공률/플레이키/시간/알림 지표 달성입니다.`

---

## 발표자 메모 (요약 카드)
- 4축 키워드: `기획서 기반`, `오더형`, `자율형`, `멀티 인터페이스`
- 운영 키워드: `재현성`, `복구력`, `설명가능성`, `온보딩`
- 금지 프레이밍: `“데모용 스크립트”`라는 인상을 주는 표현
