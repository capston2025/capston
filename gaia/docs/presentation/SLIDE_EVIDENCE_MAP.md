# GAIA 중간발표 v2 슬라이드 증빙 맵

## 문서 목적
- 중간발표 12슬라이드의 주장/메시지를 코드 및 문서 증거와 1:1로 고정한다.
- 발표 직전 혼선을 막기 위해 본 문서를 증빙 단일 기준으로 사용한다.

## 기준 문서 (Source of Truth)
- 대본: `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/MIDTERM_15MIN_V2.md`
- 증빙맵: `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/SLIDE_EVIDENCE_MAP.md`

## 4축 표현 고정
아래 4축 문구는 대본과 동일하게 유지한다.

| 축 | 고정 표현 |
|---|---|
| 기획서 기반 | Plan-driven |
| 오더형 실행 | Order-driven |
| 자율 실행 | Agent-driven |
| 멀티 인터페이스 | GUI / CLI(Code) / Chat(Telegram) |

## 슬라이드 1~12 증빙 매핑

### Slide 1
- 핵심 주장: GAIA는 단순 스크립트가 아니라 다중 인터페이스 기반 테스트 운영 플랫폼이다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/main.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/cli.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/telegram_bridge.py`

### Slide 2
- 핵심 주장: 기존 GUI 테스트의 유지보수/변경 취약성 문제를 해결하기 위해 범용 실행 엔진을 설계했다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_host.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/agent.py`

### Slide 3
- 핵심 주장: 3-Mode와 3-Interface를 같은 엔진 위에 결합했다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/MIDTERM_15MIN_V2.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/chat_hub.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/terminal.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/telegram_bridge.py`

### Slide 4
- 핵심 주장: 입력 채널은 다양하지만 실행 핵심은 단일화되어 있다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/chat_hub.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/intelligent_orchestrator.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_host.py`

### Slide 5 (Demo A: 기본 플로우)
- 핵심 주장: 목표 입력 후 분석-행동-검증의 기본 동작이 수행된다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/README.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/cli.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/chat_hub.py`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`

### Slide 6 (Demo B: 실패 복구 플로우)
- 핵심 주장: 실패 시 대체 전략/복구 체인이 동작한다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_ref_action_exception_recovery.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_ref_verify_fallbacks.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_ref_close_fallbacks.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_ref_post_click_watch.py`

### Slide 7
- 핵심 주장: 운영 친화 기능(텔레그램/CLI/GUI 및 인증 흐름)을 제공한다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/telegram_bridge.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/gui`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/cli.py`
  - `/Users/coldmans/Documents/GitHub/capston/README.md`

### Slide 8
- 핵심 주장: 현재까지 핵심 실행 루프와 인터페이스 통합이 구현됐다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_SKELETON.md`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/TEAM_PLAYBOOK.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/TEAM_OWNERSHIP.md`

### Slide 9
- 핵심 주장: 남은 과제는 구조 분리/예외 정책/관측성 강화다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/agent.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_host.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/TEAM_PLAYBOOK.md`

### Slide 10
- 핵심 주장: 완성 기준은 성공률/재현성/플레이키 등 정량 지표다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_host.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/session/session_store.py`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/memory`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_RUN_LOG.md`

### Slide 11
- 핵심 주장: 3주 마감 계획(리팩토링→관측성→회귀검증)으로 완성도를 높인다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/TEAM_PLAYBOOK.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/TEAM_OWNERSHIP.md`
  - `/Users/coldmans/Documents/GitHub/capston/PORTFOLIO_SKELETON.md`

### Slide 12
- 핵심 주장: GAIA는 테스트 운영 플랫폼으로서 재현 가능성과 설명 가능성을 제공한다.
- 증빙 경로:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/MIDTERM_15MIN_V2.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/SLIDE_EVIDENCE_MAP.md`
  - `/Users/coldmans/Documents/GitHub/capston/README.md`

## Demo 실행 명령 (발표용)
```bash
cd /Users/coldmans/Documents/GitHub/capston
python -m gaia.cli chat --once --goal "https://www.gamegoo.co.kr/ 게시판 상세보기 기능 검증"
```

## 발표 전 체크리스트
- Slide 1~12 모두 대본/증빙이 채워져 있는지 확인.
- Demo A/B 분리 표현이 대본과 동일한지 확인.
- 4축 문구가 대본/증빙맵/README에서 동일한지 확인.
- 발표 링크는 아래 2개 문서만 기준으로 사용:
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/MIDTERM_15MIN_V2.md`
  - `/Users/coldmans/Documents/GitHub/capston/gaia/docs/presentation/SLIDE_EVIDENCE_MAP.md`
