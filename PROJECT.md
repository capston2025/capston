GAIA 프로젝트 명세서
최종 업데이트: 2025-10-24
Phase: 1학기 MVP (Alpha)

🎯 프로젝트 배경
- 창업팀 프로토타입마다 외주 QA 비용이 300만 원 가까이 발생 → 초기 팀/동아리는 감당 불가
- “추가 스크립트 작성 없이, 기획서만으로, 누구나 돌릴 수 있는 저비용 QA 파이프라인”을 목표로 GAIA 착수
- 지원금이 없는 학생/동아리도 버튼 몇 번으로 테스트 커버리지를 확보할 수 있도록 설계

🎯 핵심 목표
- 기획서 PDF만으로 100개 이상 UI 테스트 시나리오 자동 생성
- 우선순위/실행 난이도/DOM 변화를 고려하는 적응형 스케줄러로 테스트 순서를 자동 조정
- GPT + Playwright MCP로 실제 브라우저를 탐색하며 체크리스트 25개 항목을 실시간 업데이트
- GUI에서 진행 상황·화면 캡처·커버리지 지표를 즉시 공유해 비개발자도 QA를 실행 가능하게 제공
- 반복 실행 시 API 비용을 억제하고 재사용 속도를 높이기 위한 캐시(셀렉터/임베딩) 유지

💎 제품 가치
- QA 외주비나 전문 인력이 없는 팀에게 “PDF 업로드 → 실행” 수준의 간단한 QA 자동화 제공
- 데모/투자 피칭 전 회귀 테스트를 반나절 안에 끝내어 팀 생산성·정신적 부담 완화
- 스마트 내비게이션과 부분 성공 리포팅으로 “테스트가 왜 실패했는지” 설명 가능한 보고서 생성

🧩 시스템 아키텍처
```
사용자 GUI (PySide6)
        │  로그·화면 스트리밍
        ▼
Master Orchestrator (GPT-5 탐색)
        │  시나리오 배분
        ▼
Intelligent Orchestrator (GPT-5-mini 비전)
        │  Playwright MCP 액션
        ▼
FastAPI MCP Host + Chromium (Playwright)
        │  DOM/Screenshot 피드백
        ▼
Adaptive Scheduler ← Phase1 Planner (OpenAI Agent Builder)
        │            │
        └── Checklist Tracker & Phase5 Report
```

🗂️ 리포 구조
```
gaia/
├── src/
│   ├── phase1/                 # PDF 분석 + OpenAI Agent Builder 연동
│   ├── scheduler/              # 적응형 스케줄러 (점수 계산·큐 관리·상태 추적)
│   ├── phase4/
│   │   ├── master_orchestrator.py   # GPT-5 기반 사이트 맵 탐색
│   │   ├── intelligent_orchestrator.py # GPT-5-mini 비전 실행기 + 캐시 + 스마트 내비게이션
│   │   ├── llm_vision_client.py     # LLM 비전 호출 클라이언트 (모델/비용 제어)
│   │   └── mcp_host.py              # FastAPI + Playwright MCP 호스트
│   ├── tracker/                # ChecklistTracker (커버리지 계산)
│   ├── gui/                    # PySide6 컨트롤러, 실시간 미리보기, 커서 오버레이
│   ├── phase5/                 # 리포트/요약 유틸
│   ├── utils/                  # 설정, 데이터 모델 (Pydantic)
│   └── orchestrator.py         # CLI 오케스트레이터
├── artifacts/
│   ├── cache/                  # selector_cache.json, embedding_cache.json, 실행 메타데이터
│   └── plans/                  # Phase1 출력 저장 (재실행/데모 용도)
├── scripts/                    # run_mcp_host.sh, run_gui.sh
├── run_auto_test.py            # 풀 파이프라인 실행 CLI
└── tests/                      # Pytest (스케줄러, 오케스트레이터 등)
```

⚙️ 모듈 상세
- **Phase1 (gaia/src/phase1)**
  - `PDFLoader`: PDF → 텍스트 추출, 목차/섹션 보존
  - `AgentServiceClient`: OpenAI Agent Builder(LMOps 워크플로) 호출, `TestScenario` + 체크리스트 생성
  - `AgentRunner`: 기획서 텍스트 1회 분석으로 100+ 시나리오 구성
- **Adaptive Scheduler (gaia/src/scheduler)**
  - 우선순위(MUST/SHOULD/MAY), DOM 신규 요소, URL 다양성, 최근 실패를 기반으로 점수 산정
  - Priority Queue + 상태 머신으로 상위 N개 테스트를 반복 실행
  - `integration.py` 가 Phase1 출력 ↔ MCP 실행을 연결
- **Phase4 Orchestration**
  - `MasterOrchestrator`: GPT-5로 홈화면 → 서브페이지 간 내비게이션 맵 구성, 페이지별 실행 분배
  - `IntelligentOrchestrator`: DOM/Screenshot를 GPT-5-mini에 전달해 액션 선택, 스마트 내비게이션 메모리/공격적 텍스트 매칭/부분 성공 판단을 담당
  - `LLMVisionClient`: GPT-5-mini 호출 + 비용 제한, 150개 DOM 요소 목록에 기반한 정밀 셀렉터 생성
  - `mcp_host.py`: FastAPI + Playwright. `analyze_page`, `execute_step`, 스트리밍 스크린샷 제공
- **Tracker & Phase5**
  - `ChecklistTracker`: 시나리오 → 체크리스트 시드, `mark_found`로 커버리지 계산
  - Phase5 유틸은 데모용 커버리지/근거 요약 제공(진행 중)
- **GUI (gaia/src/gui)**
  - 실시간 로그 하이라이팅, QThread 기반 워커, 커서 오버레이, 스크롤 중간 스냅샷 전송
  - `controller.py`가 Phase1/Phase4 워커를 연결, `intelligent_worker.py`가 Master → Intelligent 실행을 UI에 전달

✨ 최신 기능 하이라이트
- 스마트 내비게이션 메모리: 페이지별 DOM 캡처로 다른 페이지 링크 자동 탐색
- 부분 성공 리포팅: 단계별 성공률/스킵률을 계산해 허위 성공률 방지
- Selector & Embedding Cache: 반복 실행 시 LLM 호출 60% 이상 절감, `artifacts/cache`에 영속화
- GPT-5 / GPT-5-mini 하이브리드 전략: 탐색은 GPT-5, 비전·셀렉터 선택은 GPT-5-mini로 비용 최적화
- 라이브 화면 피드백: 스크롤·분석 단계마다 GUI에 즉시 스크린샷/커서 업데이트
- 적응형 스케줄링: DOM 변화·실패 히스토리를 반영해 가장 가치 있는 테스트부터 재시도

🛠️ 기술 스택
- **LLM**: OpenAI GPT-4o (Phase1), GPT-5 (Master), GPT-5-mini (비전)
- **Automation**: Playwright(Chromium), Model Context Protocol, FastAPI(Uvicorn)
- **Desktop**: PySide6 / Qt Signals & Threads
- **Data**: Pydantic 모델, JSON 기반 캐시/리포트
- **기타**: requests, numpy, python-dotenv

🧪 실행 & 환경 설정
1. 가상환경 구성
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r gaia/requirements.txt
   ```
2. OpenAI 키와 옵션 설정 (`.env` 또는 환경 변수)
   - `OPENAI_API_KEY` (필수)
   - `GAIA_LLM_MODEL` (기본 `gpt-4o`)
   - `GAIA_LLM_REASONING_EFFORT`, `GAIA_LLM_VERBOSITY`, `GAIA_LLM_MAX_COMPLETION_TOKENS`
   - `MCP_HOST_URL`(기본 `http://localhost:8001`), `MCP_TIMEOUT`
3. Playwright 준비
   ```bash
   playwright install chromium
   ```
4. 실행 플로
   ```bash
   ./scripts/run_mcp_host.sh      # MCP 호스트 기동
   ./scripts/run_gui.sh           # PySide6 GUI 실행
   # 또는 전체 파이프라인
   python run_auto_test.py --url https://example.com --spec artifacts/spec.pdf
   ```
5. 캐시 폴더(`artifacts/cache`)는 실행 시자동 생성/업데이트

📈 성공 지표
- 커버리지: 체크리스트 25개 중 18개 이상 발견(≥72%)
- 실행 안정성: 10분 연속 실행 시 크래시/세션 종료 0건
- API 비용: 반복 실행 시 플랜 하나당 5,000원 이하 유지 (캐시 사용 기준)
- 데모 품질: 실시간 GUI 지연 ≤ 1초, 부분 성공률 명확히 표시

🚧 리스크 & 대응 전략
- **LLM 응답 불안정** → JSON Schema 검증, 실패 시 자동 재시도 및 최소 샘플 플랜 캐시 사용
- **Playwright 환경 차이** → FastAPI MCP 호스트로 브라우저 제어를 통일, 해시 내비게이션 실패 시 버튼 탐색으로 대체
- **API 비용 증가** → 하이브리드 모델, 셀렉터/임베딩 캐시, 부분 재시작 기능으로 호출 수 감소
- **UI 구조 급변** → DOM 150개 제한 확대, ARIA role 지원, 공격적 텍스트 매칭으로 회복력 향상
- **데모 실패 리스크** → `artifacts/plans/*.json` 재사용, GUI 실시간 모니터링, 부분 성공 리포트로 원인 설명

🗓️ 로드맵 (예시)
- 주차 1: Phase1 프롬프트 안정화, Planner API 래핑, 기본 테스트 시나리오 확보
- 주차 2: Adaptive Scheduler 통합, GUI 실시간 로그/커서 업데이트
- 주차 3: Master/Intelligent Orchestrator 완성, 스마트 내비게이션 및 캐시 도입
- 주차 4: 부분 성공 리포팅·Phase5 요약, 통합 테스트 및 데모 준비
- 이후: DSL 기반 시나리오 언어, Gap Detection, 네트워크/상태 검증 고도화

📦 산출물 & 문서
- `README.md`: 최신 기능/개선 사항 요약
- `gaia/docs/IMPLEMENTATION_GUIDE.md`: 환경 구성 및 모듈 개요
- `gaia/docs/PROGRESS.md`: 진행 로그
- `artifacts/`: 캐시, 플랜, 데모 자료
- 테스트: `pytest gaia/tests` (스케줄러/오케스트레이터 단위 테스트)

💬 협업 가이드
- PR 전 `run_auto_test.py` 혹은 GUI 데모로 최소 1회 실행
- 신규 기능 후 `gaia/docs/PROGRESS.md` 업데이트
- 캐시 관련 변경 시 `artifacts/cache` JSON 구조 문서화
- 버그 재현 정보는 “페이지 URL + 실행 로그 + 선택된 셀렉터” 형태로 공유
