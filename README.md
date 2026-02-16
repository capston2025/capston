# GAIA – Goal-oriented Autonomous Intelligence for Adaptive GUI Testing

GAIA는 “기획서만 주어지면 브라우저 테스트 플랜부터 실행·보고까지 자동으로 만들어 준다”는 목표로 설계된 1학기 MVP다. GPT 기반 플래너가 UI 테스트 시나리오와 체크리스트를 만들고, 적응형 스케줄러와 GPT-5 오케스트레이터가 Playwright MCP를 통해 실제 사이트를 탐색한다.

## 왜 GAIA인가?
- **Spec → Test까지 One-click**: PDF 기획서를 투입하면 100+ 시나리오와 25개 체크리스트가 자동 생성된다.
- **Adaptive Execution**: 우선순위·DOM 변화·실패 히스토리를 반영하는 스케줄러가 가장 가치 있는 시나리오부터 반복 실행한다.
- **Investor-ready UX**: PySide6 GUI가 실시간 로그, 스크린샷, 커서 오버레이, 부분 성공률을 보여줘 비개발자도 데모를 진행할 수 있다.

## 시스템 아키텍처
```
          ┌──────────────┐      ┌──────────────┐
          │  Phase 1     │      │ Adaptive     │
Planning  │ PDF Loader + │──┬──▶│ Scheduler    │
PDF →     │ Agent Builder│  │   │ (priority PQ)│
Scenario  └──────────────┘  │   └──────┬───────┘
                             │          │
                             │    Prioritized plan
                             ▼          │
                        ┌───────────────┴──────────────┐
                        │ Phase 4 Execution Layer       │
                        │ ┌───────────────────────────┐ │
                        │ │ Master Orchestrator (GPT-5)│ │
                        │ ├───────────────────────────┤ │
                        │ │ Intelligent Orchestrator   │ │
                        │ │  (GPT-5-mini Vision +      │ │
                        │ │   selector/embedding cache)│ │
                        │ └───────────────────────────┘ │
                        │            │                  │
                        └────────────┼─────────────────┘
                                     │ MCP Actions
                         ┌───────────▼──────────┐
                         │ Playwright MCP Host  │
                         │  (FastAPI + Chromium)│
                         └───────────┬──────────┘
                                     │ DOM/Screenshot
                     ┌───────────────▼────────────────┐
                     │ GUI + Checklist Tracker + Report│
                     └─────────────────────────────────┘
```

### End-to-End Flow
1. **Phase 1 – Spec Analysis**
   - `pdf_loader.PDFLoader`가 PDF 텍스트를 정제하고, `agent_client.AgentServiceClient`가 OpenAI Agent Builder 워크플로(`gpt-4o` 기본)를 호출해 `TestScenario` + 체크리스트를 JSON으로 받는다.
2. **Adaptive Scheduler**
   - `scheduler/adaptive_scheduler.py`가 MUST/SHOULD/MAY, DOM 신규 요소 여부, URL 다양성, 최근 실패 기록 등으로 점수를 계산 후 우선순위 큐에 담아 스트리밍한다.
3. **Phase 4 – Autonomous Execution**
   - `master_orchestrator.py`(GPT-5)가 사이트 맵을 만들고 시나리오를 페이지별로 배분한다.
   - `intelligent_orchestrator.py`(GPT-5-mini Vision)가 DOM 스냅샷+스크린샷을 분석해 액션을 결정, 셀렉터/임베딩 캐시를 재사용한다.
   - Smart Navigation, Aggressive Text Matching, Semantic Embedding Matching이 결합되어 페이지 이동과 요소 탐색이 자동화된다.
4. **Playwright MCP Host**
   - `phase4/mcp_host.py`(FastAPI + Chromium)가 `analyze_page`, `execute_step`를 제공하며 150개 DOM 요소 목록과 스크린샷을 스트리밍한다.
5. **Tracking & Reporting**
   - `tracker.ChecklistTracker`가 커버리지를 업데이트하고, GUI(`gaia/src/gui`)가 실시간 로그·스크린샷·Cursor overlay·4단계 결과(성공/부분/실패/스킵)를 시각화한다.

## 컴포넌트 상세

### Phase 1 – Planner & Checklists
- **PDF Loader**: 섹션·목차 구조를 유지한 채 텍스트를 정제.
- **Agent Builder Client**: OpenAI Agent Workflow(`GAIA_WORKFLOW_ID`)를 호출해 100개 이상의 시나리오와 25개 체크리스트를 생성.
- **Fallback Plans**: API 실패 시 `artifacts/plans/*.json`에 있는 플랜을 GUI에서 즉시 재사용 가능.

### Adaptive Scheduler
- **Priority Queue**: MUST/SHOULD/MAY + DOM 신규성 + 최근 실패를 점수화해 가장 가치 있는 테스트부터 실행.
- **Historical Awareness**: 같은 URL의 반복 실패를 감지해 재시도 우선순위를 높임.
- **Streaming Interface**: Phase 4 오케스트레이터가 큐에서 시나리오 배치를 받아 실행.

### Phase 4 Execution Layer
- **Master Orchestrator (GPT-5)**: 다중 페이지 탐색, Smart Navigation 메모리, 시나리오 배분.
- **Intelligent Orchestrator (GPT-5-mini Vision)**:
  - DOM+Screenshot 병합 분석으로 셀렉터 선택.
  - Selector Cache (`artifacts/cache/selector_cache.json`)와 Embedding Cache(`embedding_cache.json`) 재사용.
  - Aggressive Text Matching → Semantic Matching(`text-embedding-3-small`) → Vision fallback 순으로 요소를 탐색.
  - 부분 성공 판정을 위해 `skipped_steps` 비율을 추적해 4단계 상태(✅ SUCCESS / ⚠️ PARTIAL / ❌ FAILED / ⏭️ SKIPPED)를 리포트.
- **LLM Vision Client**: GPT-5-mini Vision 호출, 150개 DOM 요소 제한, ARIA role 확장, 60초 타임아웃.
- **Playwright MCP Host**:
  - FastAPI + Playwright로 Chromium 제어.
  - Hash 내비게이션 실패 시 버튼 클릭으로 자동 복구(예: Figma Sites).
  - ARIA role 확장, lenient opacity(>0.1) 체크, DOM 150개 수집.

### Tracking, GUI & Reporting
- **Checklist Tracker**: 시나리오에서 참조된 체크리스트 항목을 실시간으로 마킹.
- **PySide6 GUI**:
  - 실시간 로그 하이라이팅, 스크린샷/DOM 업데이트, SVG 커서 오버레이.
  - Smart Navigation 이벤트, Scroll, Vision fallback 등 주요 이벤트를 즉시 렌더 (`QCoreApplication.processEvents()`로 UI 렉 방지).
- **Phase5 Report (WIP)**: 실행 결과를 요약해 회귀 테스트 보고서로 활용 예정.

## Intelligent Capabilities & 안정성
- **페이지-요소 메모리**: 최대 4개 페이지의 내비게이션 요소를 저장하고 다른 페이지에서 자동 탐색.
- **Aggressive Text Matching**: DOM을 분석하기 전 step description에서 한/영 키워드를 모두 추출해 현재 페이지 요소부터 검색.
- **Semantic Matching + Embedding Cache**: `text-embedding-3-small` + 로컬 fallback으로 시맨틱 유사도를 계산하고, 일치 시 LLM 검증.
- **Selector Cache**: 동일한 스텝이 재실행될 때 LLM 호출을 생략해 최대 70%까지 실행 시간을 단축.
- **Hash Navigation Recovery**: Hash 이동 후 DOM 카운트가 비정상적으로 낮으면 홈으로 돌아가 버튼 클릭을 시도해 SPA 콘텐츠를 다시 로드.
- **Explicit Selector Fallback**: 플랜에 기재된 셀렉터가 실패하면 자동으로 LLM 분석으로 전환해 시나리오를 살린다.
- **4-Tier Result Reporting**: 스킵률을 기반으로 PASS와 PARTIAL을 구분해 투자자 데모에서 정직한 결과를 제공한다.

## 실행 방법

### 1. Homebrew 설치 (권장)
```bash
brew tap capston2025/homebrew-gaia
brew install gaia
gaia --help
```
`capston2025`는 배포한 GitHub owner/조직으로 맞춰주세요.

### 2. 소스 실행 환경 (개발/직접 실행)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r gaia/requirements.txt
playwright install chromium
```

### 3. 필수 환경 변수
- `OPENAI_API_KEY`: 필수.
- `GAIA_LLM_MODEL`, `GAIA_LLM_REASONING_EFFORT`, `GAIA_LLM_VERBOSITY`: Planner 튜닝.
- `GAIA_WORKFLOW_ID`, `GAIA_WORKFLOW_VERSION`: Agent Builder 워크플로 선택.
- `MCP_HOST_URL` (기본 `http://localhost:8001`), `MCP_TIMEOUT`.

`.env` 예시:
```env
OPENAI_API_KEY=sk-xxx
GAIA_WORKFLOW_ID=wf_68ea589f...
GAIA_LLM_MODEL=gpt-4o
MCP_HOST_URL=http://localhost:8001
```

### 4. 실행 플로우
1) 터미널 환경에서 MCP Host 실행
```bash
./scripts/run_mcp_host.sh
```

※ `gaia start`는 자동으로 MCP Host를 띄우지 않습니다. 배포/운영 시 `gaia` 실행 전/후로 MCP Host를 별도 관리하세요.

2) 실행 모드
```bash
# 터미널/GUI 선택 UI
gaia start

# 바로 GUI 실행
gaia start gui

# 터미널 모드 실행
gaia start terminal --plan artifacts/plans/sample_plan.json --url https://example.com
gaia start terminal --plan artifacts/plans/sample_plan.json --url https://example.com --format json

# 터미널 결과를 GUI에서 이어서 실행
gaia start gui --resume <run-id>
```

과거 플로우인 `python run_auto_test.py`는 스크립트가 제거되어 더 이상 사용되지 않습니다.
또한 과거에는 `./scripts/run_gui.sh`가 `python -m gaia.main`로 실행됐으나 이제 `gaia start gui`를 사용합니다.

GUI에서 기존 플랜 재사용 시 “이전 테스트 불러오기”로 `artifacts/plans/*.json` 선택 시 즉시 실행할 수 있습니다.

## 워크스페이스 구조
```
gaia/
├── src/
│   ├── phase1/                # PDF 분석 + Agent Builder 연동
│   ├── scheduler/             # 적응형 스케줄러와 상태 머신
│   ├── phase4/
│   │   ├── master_orchestrator.py    # GPT-5 사이트 맵 탐색
│   │   ├── intelligent_orchestrator.py # GPT-5-mini 실행기 + 캐시
│   │   ├── llm_vision_client.py      # Vision/selector LLM 래퍼
│   │   └── mcp_host.py               # FastAPI + Playwright MCP
│   ├── tracker/               # Checklist 커버리지 트래커
│   ├── gui/                   # PySide6 GUI, 워커 스레드
│   ├── phase5/                # 리포트/요약 유틸 (WIP)
│   ├── utils/                 # 설정/데이터 모델 (Pydantic)
│   └── orchestrator.py        # CLI 오케스트레이터
├── artifacts/
│   ├── cache/                 # selector_cache.json, embedding_cache.json
│   └── plans/                 # Planner 출력 저장본
├── scripts/                   # run_mcp_host.sh, run_gui.sh 등
├── tests/                     # Pytest (planner, scheduler, orchestration)
├── homebrew/                  # Homebrew formula
└── README.md
```

## 테스트 & 자동화
```bash
pytest gaia/tests
python test_scheduler_logic.py      # 스케줄러 로직 단독 검증
python test_automation.py           # 단순 시나리오 실행기
python run_local_test.py            # 로컬 UI 테스트 사이트용 데모
```

## 문서 & 진행 관리
- `gaia/docs/PROJECT_CONTEXT.md`: 전체 프로젝트 배경 및 목표.
- `gaia/docs/PROGRESS.md`: 주차별 진행 로그.
- `gaia/docs/IMPLEMENTATION_GUIDE.md`: 환경 구성, 모듈 간 의존성, 다음 단계 메모.
- `ADAPTIVE_SCHEDULER_SUMMARY.md`, `VERIFICATION_REPORT.md`: 스케줄러/검증 관련 요약.
- `artifacts/plans/*.json`: QA 데모/회귀 테스트용 고정 플랜.

---
GAIA는 “학생/초기 팀도 버튼 몇 번이면 회귀 테스트를 돌릴 수 있는” QA 파이프라인을 목표로 계속 진화하고 있다. 새로운 기능을 추가할 때는 `docs/PROGRESS.md`를 업데이트하고, 캐시 구조 변경 시 `artifacts/cache` 포맷을 함께 기록해 주세요.
