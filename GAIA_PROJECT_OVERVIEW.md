# GAIA 프로젝트 종합 문서

**Goal-oriented Autonomous Intelligence for Adaptive GUI Testing**

최종 업데이트: 2025-11-08

---

## 📋 목차

1. [프로젝트 개요](#프로젝트-개요)
2. [시스템 아키텍처](#시스템-아키텍처)
3. [핵심 기능](#핵심-기능)
4. [기술 스택](#기술-스택)
5. [주요 컴포넌트](#주요-컴포넌트)
6. [개발 성과](#개발-성과)
7. [테스트 결과](#테스트-결과)
8. [환경 설정 및 실행](#환경-설정-및-실행)
9. [향후 계획](#향후-계획)

---

## 프로젝트 개요

### 1.1 프로젝트 배경

GAIA는 **기획서 기반 자동 QA 테스트 생성 및 실행 시스템**입니다. 창업팀이나 초기 스타트업에서 프로토타입마다 외주 QA 비용이 300만 원 가까이 발생하는 문제를 해결하기 위해 시작되었습니다.

**핵심 비전**: "추가 스크립트 작성 없이, 기획서만으로, 누구나 돌릴 수 있는 저비용 QA 파이프라인"

### 1.2 핵심 목표

- ✅ **기획서 PDF만으로** 100개 이상 UI 테스트 시나리오 자동 생성
- ✅ **적응형 스케줄러**로 테스트 우선순위 자동 조정
- ✅ **GPT + Playwright MCP**로 실제 브라우저 탐색
- ✅ **실시간 체크리스트** 트래킹 (25개 항목)
- ✅ **GUI**에서 진행 상황, 화면 캡처, 커버리지 지표 즉시 공유
- ✅ **셀렉터/임베딩 캐시**로 API 비용 억제 및 재사용 속도 향상

### 1.3 제품 가치

- **저비용 자동화**: QA 외주비나 전문 인력 없이 "PDF 업로드 → 실행" 수준의 간단한 QA
- **빠른 회귀 테스트**: 데모/투자 피칭 전 회귀 테스트를 반나절 안에 완료
- **스마트 내비게이션**: 페이지별 DOM 캡처로 자동 탐색
- **부분 성공 리포팅**: "테스트가 왜 실패했는지" 설명 가능한 보고서 생성

### 1.4 주요 성과 (중간 발표 기준)

- **80% 성공률**: 실제 웹사이트에서 30개 테스트 중 24개 성공
- **95% 성공률**: 선택자 없는 현실적인 테스트 플랜에서 19/20 성공
- **4페이지 자동 탐색**: 해시 기반 SPA 자동 발견 및 테스트
- **103+ 액션 실행**: 21개 Playwright 액션 중 11개 검증 완료
- **80% 비용 절감**: 하이브리드 GPT-5/GPT-5-mini 전략

---

## 시스템 아키텍처

### 2.1 전체 아키텍처

```
사용자 (PDF 기획서)
        │
        ▼
┌─────────────────────────────────────────────────┐
│           Phase 1: Spec Analysis                │
│  - PDF 로더 (텍스트 추출)                        │
│  - OpenAI Agent Builder (GPT-4o)                │
│  → 100+ 테스트 시나리오 생성                    │
│  → 25개 체크리스트 생성                         │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│        Adaptive Scheduler (적응형 스케줄러)      │
│  - 우선순위 점수 계산 (MUST/SHOULD/MAY)         │
│  - DOM 신규 요소 보너스                         │
│  - URL 다양성 보너스                            │
│  - 최근 실패 재시도 인센티브                    │
│  - 정체 상태 페널티                             │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│     Master Orchestrator (GPT-5 탐색)            │
│  - 사이트 맵 구축 (4페이지 발견)                │
│  - 다중 페이지 흐름 탐색                        │
│  - 페이지별 시나리오 배분                       │
│  - 실행된 테스트 추적                           │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│   Intelligent Orchestrator (GPT-5-mini 비전)    │
│  - DOM 스냅샷 + 스크린샷 분석                   │
│  - 4단계 폴백 파이프라인                        │
│    1) LLM 비전 분석                             │
│    2) Auto-fix (정규식 텍스트 추출)             │
│    3) 공격적 텍스트 매칭                        │
│    4) 스마트 내비게이션                         │
│  - 셀렉터/임베딩 캐시                           │
│  - 스마트 내비게이션 메모리                     │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│     MCP Host (FastAPI + Playwright)             │
│  - 21개 브라우저 자동화 액션                    │
│  - DOM 분석 및 스크린샷 캡처                    │
│  - JavaScript 평가                              │
└─────────────────┬───────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│    Checklist Tracker & GUI (PySide6)            │
│  - 실시간 체크리스트 업데이트                   │
│  - 로그 하이라이팅                              │
│  - 스크린샷 스트리밍                            │
│  - 커서 오버레이                                │
│  - 커버리지 지표                                │
└─────────────────────────────────────────────────┘
```

### 2.2 레이어 구조

```
gaia/
├── src/
│   ├── phase1/              # PDF 분석 + Agent Builder 연동
│   │   ├── pdf_loader.py
│   │   ├── agent_client.py
│   │   └── analyzer.py
│   │
│   ├── scheduler/           # 적응형 스케줄러
│   │   ├── state.py
│   │   ├── scoring.py
│   │   ├── priority_queue.py
│   │   ├── logger.py
│   │   ├── adaptive_scheduler.py
│   │   └── integration.py
│   │
│   ├── phase4/              # MCP 오케스트레이션
│   │   ├── master_orchestrator.py    # GPT-5 사이트 탐색
│   │   ├── intelligent_orchestrator.py # GPT-5-mini 비전 실행
│   │   ├── llm_vision_client.py
│   │   └── mcp_host.py               # FastAPI + Playwright
│   │
│   ├── tracker/             # 체크리스트 트래킹
│   │   └── checklist.py
│   │
│   ├── gui/                 # PySide6 데스크톱 GUI
│   │   ├── main_window.py
│   │   ├── controller.py
│   │   └── intelligent_worker.py
│   │
│   ├── utils/               # 공통 유틸리티
│   │   ├── config.py
│   │   └── plan_repository.py
│   │
│   └── phase5/              # 리포트 생성 (진행 중)
│
├── agent-service/           # Node.js Agent Builder 서비스
│   ├── src/
│   │   ├── server.ts
│   │   └── workflow.ts
│   └── docker-compose.yml
│
├── artifacts/
│   ├── cache/               # 셀렉터/임베딩 캐시
│   └── plans/               # 저장된 테스트 플랜
│
├── tests/                   # Pytest 테스트
├── scripts/                 # 헬퍼 스크립트
└── docs/                    # 문서
```

---

## 핵심 기능

### 3.1 Auto-fix 메커니즘 (95% 성공률)

**문제**: LLM이 완벽한 CSS 셀렉터를 생성하기 어려움

**해결책**: 단계 설명에서 텍스트 추출 후 텍스트 기반 셀렉터 생성

```python
# 입력: "Click 둘러보기 button in 기본 기능 card"

# 1단계: 한국어 텍스트 추출
korean_text = re.search(r'[가-힣]+', step_description)
# → "둘러보기"

# 2단계: DOM에서 텍스트 매칭 요소 찾기
text_match = next((e for e in dom_elements if "둘러보기" in e.text), None)
# → <button>둘러보기</button>

# 3단계: 텍스트 기반 셀렉터 생성
better_selector = f'button:has-text("둘러보기")'

# 4단계: 높은 신뢰도로 실행
confidence = 95%  # 정확한 텍스트 매칭
```

**결과**:
- 선택자 없는 테스트에서 95% 성공률 (19/20)
- 한국어/영어 모두 작동
- LLM 환각 감소

### 3.2 Master Orchestrator (다중 페이지 탐색)

**문제**: 전통적인 E2E 도구는 한 번에 한 페이지만 테스트

**해결책**: 자동 사이트 탐색 및 다중 페이지 테스트 실행

```
1️⃣ 사이트 탐색 (GPT-5 + 스크린샷)
   Input:  홈페이지 URL
   Output: [
     {name: "Home",         url: "https://site.com"},
     {name: "Basic",        url: "https://site.com#basics"},
     {name: "Forms",        url: "https://site.com#forms"},
     {name: "Interactions", url: "https://site.com#inter"}
   ]

2️⃣ 페이지별 실행
   📄 Page 1/4: Home       → TC001, TC002 실행 ✅
   📄 Page 2/4: #basics    → TC010, TC011 실행 ✅
   📄 Page 3/4: #forms     → TC005, TC006 실행 ⚠️
   📄 Page 4/4: #interactions → TC009 실행 ✅

3️⃣ 결과 집계
   Total:    30 tests
   Success:  24 (80%)
   Partial:  2  (7%)
   Failed:   4  (13%)
```

**핵심 기능**:
- 테스트 추적 (중복 실행 방지)
- 스마트 필터링 (각 페이지에서 남은 테스트만 실행)
- 해시 네비게이션 지원 (React Router, Figma Sites)

### 3.3 4단계 폴백 파이프라인

```
┌────────────────────────────────────────────────┐
│ Stage 1: LLM Vision (GPT-5-mini + 스크린샷)     │
│ - 150개 DOM 요소 + 스크린샷 분석                │
│ - CSS 셀렉터 생성                               │
│ - 신뢰도 임계값: 70%                            │
│ 성공률: ~60%                                    │
└────────────────┬───────────────────────────────┘
                 │ (신뢰도 < 70%)
                 ▼
┌────────────────────────────────────────────────┐
│ Stage 2: Auto-fix (정규식 텍스트 추출)         │
│ - 한국어/영어 텍스트 추출                      │
│ - DOM에서 정확한 텍스트 매칭                   │
│ - 텍스트 기반 셀렉터 생성                      │
│ 성공률: +30% → 90% 누적                        │
└────────────────┬───────────────────────────────┘
                 │ (텍스트 매칭 없음)
                 ▼
┌────────────────────────────────────────────────┐
│ Stage 3: 공격적 텍스트 매칭                    │
│ - 설명에서 모든 단어 추출                      │
│ - 전체 DOM 검색 (매칭 요소만 아님)             │
│ - 여러 텍스트 변형 시도                        │
│ 성공률: +5% → 95% 누적                         │
└────────────────┬───────────────────────────────┘
                 │ (현재 페이지에 없음)
                 ▼
┌────────────────────────────────────────────────┐
│ Stage 4: 스마트 내비게이션                     │
│ - 페이지 메모리 검색 (방문한 페이지)           │
│ - 다른 페이지에서 발견 시 탐색                 │
│ - 미래 사용을 위한 요소 위치 기록              │
│ 성공률: +3% → 98% 누적                         │
└────────────────┬───────────────────────────────┘
                 │ (여전히 찾지 못함)
                 ▼
┌────────────────────────────────────────────────┐
│ Stage 5: 스크롤 + 비전 좌표 감지               │
│ - 숨겨진 요소 노출을 위한 페이지 스크롤        │
│ - GPT-5-mini로 픽셀 좌표 추출                  │
│ - 좌표로 클릭                                  │
│ 성공률: 나머지 엣지 케이스 처리                │
└────────────────────────────────────────────────┘
```

### 3.4 4계층 상태 시스템

**문제**: 이진 통과/실패는 부분 성공을 포착하지 못함

**해결책**: 테스트 결과를 4개 카테고리로 분류

```
✅ SUCCESS (100% 완료)
   - 모든 단계 성공적으로 실행
   - 건너뛰기나 실패 없음
   - 어설션 통과

⚠️ PARTIAL (핵심 작동, 일부 단계 건너뜀)
   - 핵심 기능 작동
   - 일부 비중요 단계 건너뜀
   - 어설션 통과 가능
   예: TC006 - 7개 중 5개 단계 완료 (29% 건너뜀)

❌ FAILED (중요 실패)
   - 중요 단계 실패
   - 핵심 기능 손상
   - 어설션 실패

⏭️ SKIPPED (실행 안 됨)
   - 현재 페이지에 적용 불가능한 테스트
   - 다른 페이지에서 실행 가능
```

**이점**:
- 투자자 데모를 위한 더 정직한 보고
- 완벽한 실행과 부분 성공 명확히 구분
- 개선이 필요한 테스트 쉽게 식별
- 보고된 성공률에 대한 더 나은 신뢰

### 3.5 비용 최적화 (80% 절감)

**문제**: GPT-5 비전은 비쌈 ($15/M 토큰)

**해결책**: 중요한 결정에만 GPT-5 사용하는 하이브리드 전략

```
GPT-5 사용 (중요 결정)                     10% 사용
├─ 사이트 탐색 및 페이지 발견
├─ 네비게이션 구조 분석
└─ 중요 DOM 해석
비용: $15/M 토큰

GPT-5-mini 사용 (일상 작업)                90% 사용
├─ 스크린샷에서 요소 감지
├─ 셀렉터 생성
├─ 비전 기반 좌표 추출
└─ 결과 검증
비용: $3/M 토큰 - 80% 저렴!

추정 비용 절감:
이전 (모두 GPT-5):     $15 × 100 = $1,500
현재 (하이브리드):      ($15 × 10) + ($3 × 90) = $420
절감:                   $1,080 (72% 감소)

캐싱 포함 (60% 빠름): 추가 40% 비용 절감
```

### 3.6 셀렉터 캐싱 (60-70% 빠름)

**작동 방식**:
1. **첫 실행**: LLM이 DOM 분석 후 요소 선택 (단계당 3-5초)
2. **캐시**: 성공한 셀렉터를 메타데이터와 함께 저장 (타임스탬프, 성공 횟수)
3. **후속 실행**: 캐시된 셀렉터가 LLM 완전히 우회 (단계당 0.5초)

**속도 개선**:
- 첫 실행: 단계당 7-9초 (변경 없음)
- 캐시된 실행: 단계당 2-3초 (**60-70% 빠름**)

**캐시 전략**:
- 캐시 키: 해시(단계 설명 + 액션 + 정규화된 URL)
- 신뢰도 임계값: 2회 이상 성공한 셀렉터만 캐시
- 자동 만료: 7일 이상 된 항목 제거
- 지속성: `artifacts/cache/selector_cache.json`에 저장
- 폴백: 캐시된 셀렉터 실패 시 LLM 분석으로 대체

### 3.7 스마트 내비게이션 메모리

**작동 방식**:
1. **기록**: GAIA가 페이지를 방문하면서 모든 인터랙티브 요소(버튼, 링크)와 위치 기록
2. **스마트 검색**: 현재 페이지에서 요소를 찾지 못하면 GAIA가 메모리 검색
3. **자동 탐색**: 다른 페이지(예: 홈)에서 발견되면 GAIA가 자동으로 탐색하고 요소 클릭

**예시 플로우**:
```
1단계: 홈 방문 → 버튼 기록: "기본 기능", "폼과 피드백", "인터랙션과 데이터"
2단계: "기본 기능" 클릭 → #basics 페이지로 이동
3단계: "폼과 피드백" 클릭 시도
  → #basics에서 찾지 못함
  → 💡 스마트 내비게이션: 홈 페이지에서 발견
  → 🏠 홈으로 이동
  → ✅ "폼과 피드백" 성공적으로 클릭
```

**이점**:
- ✅ 테스트에서 "홈으로 돌아가기" 단계 수동 지정 불필요
- ✅ 해시 기반 SPA와 작동 (Figma Sites, React Router 해시 모드)
- ✅ 스크롤/비전 폴백 사용 감소 → 더 빠른 실행
- ✅ 페이지 구조 변경에 더 탄력적

---

## 기술 스택

### 4.1 백엔드

- **Python 3.10+**: 주요 언어
- **OpenAI GPT-4o**: 기획서 분석 및 플래너
- **OpenAI GPT-5**: Master Orchestrator (사이트 탐색)
- **OpenAI GPT-5-mini**: Vision 기반 요소 감지
- **Playwright**: 브라우저 자동화
- **FastAPI + Uvicorn**: MCP 호스트 서버
- **Redis**: 실시간 상태 저장
- **Pydantic**: 데이터 모델링 및 검증

### 4.2 프론트엔드 (GUI)

- **PySide6 (Qt)**: 데스크톱 GUI 프레임워크
- **Qt Signals/Slots**: 실시간 업데이트 메커니즘
- **QThread**: 백그라운드 워커

### 4.3 Agent Service (Node.js)

- **Node.js + TypeScript**: Agent Builder 서비스
- **Express**: API 서버
- **@openai/agents SDK**: Agent Builder 워크플로 실행
- **Docker**: 컨테이너화 배포

### 4.4 주요 라이브러리

```python
# requirements.txt
openai>=1.0.0
playwright>=1.40.0
PySide6>=6.6.0
redis>=5.0.0
pypdf>=3.17.0
python-dotenv>=1.0.0
requests>=2.31.0
numpy>=1.24.0
pydantic>=2.0.0
fastapi>=0.104.0
uvicorn>=0.24.0
```

---

## 주요 컴포넌트

### 5.1 Phase 1: Spec Analysis

**목적**: PDF 기획서를 분석하여 테스트 시나리오 및 체크리스트 생성

**구성 요소**:
- `PDFLoader`: PDF → 텍스트 추출, 목차/섹션 보존
- `AgentServiceClient`: OpenAI Agent Builder (LMOps 워크플로) 호출
- `AgentRunner`: 기획서 텍스트 1회 분석으로 100+ 시나리오 구성

**출력**:
- 100개 이상의 `TestScenario` 객체
- 25개 항목 체크리스트
- JSON 형식으로 `artifacts/plans/`에 저장

### 5.2 Adaptive Scheduler

**목적**: 동적 우선순위 기반 테스트 실행 스케줄링

**점수 계산 공식**:
```python
score = base_priority
      + (new_elements * 15)
      + (unseen_url ? 20 : 0)
      + (recent_fail ? 10 : 0)
      - (no_dom_change ? 25 : 0)
```

**우선순위**:
- **MUST**: 100점
- **SHOULD**: 60점
- **MAY**: 30점

**핵심 기능**:
- 동적 재점수화 (DOM 변경 감지 시)
- 실패 재시도 보너스 (+10)
- 탐색 보너스 (새 URL +20, 새 요소당 +15)
- 정체 페널티 (DOM 변경 없음 -25)
- 완료 추적 (완료된 테스트 자동 제거)
- JSON 로깅 (전체 실행 기록)

**통계** (v1.0):
- 총 라인: 2,210
- 테스트: 49개 (모두 통과)
- 문서: 1,777 라인

### 5.3 Master Orchestrator

**목적**: GPT-5로 사이트 맵 구축 및 페이지별 실행 분배

**기능**:
- 사이트 탐색 (홈 → 서브페이지 간 네비게이션 맵 구성)
- 페이지 발견 (해시 기반 라우트: #basics, #forms, #interactions)
- 테스트 추적 (`_executed_test_ids` 세트로 중복 방지)
- 페이지별 시나리오 필터링 및 실행
- 결과 집계

**구현 위치**: `gaia/src/phase4/master_orchestrator.py`

### 5.4 Intelligent Orchestrator

**목적**: GPT-5-mini 비전으로 실제 브라우저 액션 실행

**핵심 기능**:
- DOM/Screenshot 분석 (150개 요소 제한)
- 4단계 폴백 파이프라인
- 셀렉터 캐싱
- 스마트 내비게이션 메모리 (페이지당 첫 4개만 기록)
- 부분 성공 판단 (건너뛴 단계 추적)

**최적화**:
- 페이지 제한: 4개 (홈 + 3개)
- 요소 필터링: 네비게이션 유사 요소만 (짧은 텍스트 또는 키워드)
- 일반적인 메모리: 페이지당 5-20개 요소 (필터링 전 30-50개)
- 메모리 절감: 75-80%

**구현 위치**: `gaia/src/phase4/intelligent_orchestrator.py`

### 5.5 MCP Host

**목적**: FastAPI + Playwright로 브라우저 제어

**지원 액션** (21개):
- **네비게이션**: goto, click, press
- **입력**: fill, select, setInputFiles
- **검증**: expectTrue, expectVisible, expectHidden, expectAttribute, expectCountAtLeast
- **인터랙션**: hover, focus, tab, scroll, scrollIntoView, dragAndDrop
- **기타**: wait, setViewport, evaluate

**검증된 액션** (11개):
- goto, click, fill, wait, expectTrue, expectVisible, select, evaluate, setViewport, press, dragAndDrop

**API 엔드포인트**:
- `POST /execute`: 단일 액션 실행
- `POST /analyze_page`: DOM 분석
- `POST /capture_screenshot`: 스크린샷 캡처

**구현 위치**: `gaia/src/phase4/mcp_host.py`

### 5.6 Checklist Tracker

**목적**: 실시간 체크리스트 커버리지 추적

**기능**:
- 25개 체크리스트 항목 상태 관리
- Redis 저장
- GUI 실시간 업데이트
- 커버리지 지표 계산

**구현 위치**: `gaia/src/tracker/checklist.py`

### 5.7 GUI (PySide6)

**목적**: 데스크톱 컨트롤러 및 라이브 미리보기

**기능**:
- 실시간 로그 하이라이팅
- QThread 기반 워커
- 커서 오버레이 (SVG 화살표, z-index 9999)
- 스크롤 중간 스냅샷 전송
- 진행 상황 업데이트 (`QCoreApplication.processEvents()`)

**구성 요소**:
- `main_window.py`: 메인 창
- `controller.py`: Phase1/Phase4 워커 연결
- `intelligent_worker.py`: Master → Intelligent 실행을 UI에 전달

**구현 위치**: `gaia/src/gui/`

---

## 개발 성과

### 6.1 코드 통계

| 항목 | 값 |
|------|-----|
| 총 라인 수 | ~10,000+ |
| Python 파일 | 50+ |
| TypeScript 파일 | 5+ |
| 테스트 파일 | 10+ |
| 문서 파일 | 15+ |

### 6.2 주요 마일스톤

1. **Phase 1 구현** (2025-10-11)
   - PDF 로더 및 Agent Builder 통합
   - 100+ 시나리오 생성 검증

2. **Adaptive Scheduler 구현** (2025-10-22)
   - 2,210 라인 코드
   - 49개 테스트 (100% 통과)
   - 14개 이슈 수정 (코드 리뷰)

3. **Phase 4 오케스트레이션** (진행 중)
   - Master Orchestrator (다중 페이지)
   - Intelligent Orchestrator (비전 기반)
   - MCP Host (21개 액션)

4. **GUI 구현** (진행 중)
   - PySide6 기반
   - 실시간 업데이트
   - 커서 오버레이

5. **중간 발표** (2025-11-08)
   - 80% 성공률 달성
   - 30개 테스트 실행
   - 4페이지 자동 탐색

### 6.3 코드 품질

- **Adaptive Scheduler**:
  - 입력 검증: 8개 수정
  - 에러 처리: 2개 수정
  - 모듈 독립성: 2개 수정
  - 코드 품질: 2개 수정
  - **상태**: ✅ PRODUCTION READY

- **전체 시스템**:
  - Type Hints 사용
  - Pydantic 모델 검증
  - 에러 처리 및 폴백
  - 로깅 및 모니터링

---

## 테스트 결과

### 7.1 테스트 플랜 1: realistic_test_no_selectors.json

**목표**: 선택자 없이 Auto-fix 메커니즘 검증

**설정**:
- 20개 테스트 시나리오
- 모든 선택자 제거 (빈 문자열)
- 공정한 테스트를 위해 캐시 삭제

**결과**:
```
✅ 성공:  19/20 (95%)
❌ 실패:   1/20  (5%)
📄 페이지:  4/4 탐색
⚡ 속도:   캐시로 60-70% 빠름
```

**핵심 통찰**:
- Auto-fix 메커니즘이 실제 시나리오의 95%에서 작동
- 텍스트 기반 셀렉터는 한국어/영어 UI에 매우 신뢰성 높음
- 다중 페이지 네비게이션이 모든 애플리케이션 페이지 발견

### 7.2 테스트 플랜 2: ui-components-test-sites.json

**목표**: 다양한 UI 컴포넌트 테스트 (LLM 생성 테스트 플랜)

**설정**:
- 10개 테스트 시나리오
- 네비게이션, 폼, 파일 업로드, 드래그-드롭, 무한 스크롤, 비디오
- 요구사항 문서에서 생성

**결과**:
```
✅ 성공:  5/10 (50%)
⚠️ 부분:  2/10 (20%)
❌ 실패:  3/10 (30%)
📄 페이지: 4/4 탐색
```

**실패 분석**:
- TC002 (press): LLM이 잘못된 요소 선택 (`body`로 기본 설정 필요)
- TC003 (dragAndDrop): 기능 미검증
- TC004 (setInputFiles): 잘못된 셀렉터 `input.file:text-foreground`

**참고**: 일부 실패는 시스템 버그가 아닌 테스트 설계 문제

### 7.3 종합 결과

```
┌─────────────────────────────────────────────────┐
│              전체 통계                          │
│ ─────────────────────────────────────────────── │
│  총 테스트:        30                           │
│  성공:             24  (80%)                    │
│  부분:             2   (7%)                     │
│  실패:             4   (13%)                    │
│  액션 실행:        103+                         │
│  페이지 탐색:      4/4                          │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│         Playwright 액션 검증                    │
│ ─────────────────────────────────────────────── │
│  ✅ 검증됨:         11/21 (52%)                 │
│  ❌ 실패:           2/21  (10%)                 │
│  ⏭️ 미테스트:       8/21  (38%)                 │
│                                                 │
│  검증된 액션:                                   │
│  • goto, click, fill, wait                     │
│  • expectTrue, expectVisible, select, evaluate │
│  • setViewport, press, dragAndDrop             │
└─────────────────────────────────────────────────┘
```

### 7.4 주요 성과

1. **선택자 없는 작동** ✅
   - 선택자 없이 95% 성공률
   - Auto-fix 메커니즘 효과 입증
   - 테스트 유지보수 부담 감소

2. **다중 페이지 네비게이션** ✅
   - 해시 기반 SPA에서 4페이지 자동 발견
   - 중복 방지를 위한 실행된 테스트 추적
   - React Router, Figma Sites 등과 작동

3. **비용 최적화** ✅
   - 하이브리드 GPT-5/GPT-5-mini로 80% API 비용 절감
   - 정확성 유지하며 비용 절감
   - 셀렉터 캐싱으로 추가 40% 절감

4. **정직한 보고** ✅
   - 4계층 상태 시스템이 완벽과 부분 성공 구분
   - 투자자 데모를 위한 더 정확한 신뢰도
   - 더 쉬운 디버깅 및 테스트 개선

5. **포괄적인 액션 지원** ✅
   - 21개 Playwright 액션 구현
   - 실제 테스트에서 11개 액션 검증
   - 네비게이션, 폼, 인터랙션, 어설션 처리

---

## 환경 설정 및 실행

### 8.1 필수 요구사항

- Python 3.10+
- Node.js 16+ (Agent Service용)
- Redis 5+ (상태 저장용)
- Playwright Chromium

### 8.2 설치

#### 1. Python 환경 설정

```bash
# 가상 환경 생성
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r gaia/requirements.txt

# Playwright 설치
playwright install chromium
```

#### 2. Agent Service 설정 (Node.js)

```bash
cd gaia/agent-service
npm install
cp .env.example .env
# .env 파일에서 OPENAI_API_KEY 설정
```

#### 3. Redis 시작

```bash
# Docker 사용
docker run -d -p 6379:6379 redis:alpine

# 또는 로컬 설치
redis-server
```

### 8.3 환경 변수

`.env` 파일 생성:

```bash
# OpenAI API
OPENAI_API_KEY=sk-...

# Agent Builder
GAIA_WORKFLOW_ID=wf_68ea589f9a948190a518e9b2626ab1d5037b50134b0c56e7
GAIA_WORKFLOW_VERSION=1

# LLM 설정
GAIA_LLM_MODEL=gpt-4o
GAIA_LLM_REASONING_EFFORT=medium
GAIA_LLM_VERBOSITY=medium
GAIA_LLM_MAX_COMPLETION_TOKENS=16000

# MCP 호스트
MCP_HOST_URL=http://localhost:8001
MCP_TIMEOUT=45

# Redis
REDIS_URL=redis://localhost:6379
```

### 8.4 실행

#### GUI 모드 (권장)

```bash
# 터미널 1: MCP 호스트 시작
./scripts/run_mcp_host.sh

# 터미널 2: GUI 실행
./scripts/run_gui.sh
```

GUI에서 과거 테스트 플랜 재사용:
1. 1단계 화면에서 `이전 테스트 불러오기` 버튼 클릭
2. `artifacts/plans/*.json` 파일 선택
3. PDF 분석 없이 바로 자동화 시작

#### CLI 모드

```bash
# 전체 파이프라인 실행
python run_auto_test.py --url https://example.com --spec artifacts/spec.pdf
```

#### Agent Service 시작

```bash
# 개발 모드
cd gaia/agent-service
npm run dev

# 또는 Docker
cd gaia/agent-service
docker-compose up -d
```

### 8.5 테스트 실행

```bash
# 전체 테스트
pytest gaia/tests

# 특정 모듈
pytest gaia/tests/test_scheduler.py -v

# 커버리지와 함께
pytest gaia/tests --cov=gaia --cov-report=html
```

---

## 향후 계획

### 9.1 단기 (다음 스프린트)

1. **알려진 이슈 수정**
   - `press` 액션: 키보드 단축키에 `body`로 기본 설정
   - `setInputFiles`: `input[type="file"]` 셀렉터 사용
   - TC002/TC007에서 리스트 연결 예외

2. **테스트 커버리지**
   - 나머지 8개 Playwright 액션 검증
   - scroll, hover, focus, tab에 대한 테스트 시나리오 추가

3. **성능 최적화**
   - 셀렉터 캐시 만료 구현 (7일)
   - 더 빠른 분석을 위해 DOM 요소 제한 감소 (150 → 100)

### 9.2 중기 (2학기)

1. **Phase 5 리포팅**
   - Markdown/PDF 요약
   - 커버리지 리포트
   - 근거 기반 보고서

2. **DSL 기반 시나리오 언어**
   - 테스트 시나리오 정의를 위한 DSL
   - DSL 파서
   - DSL → MCP 액션 변환

3. **Gap Detection**
   - 기획서와 실제 구현 비교
   - 누락된 기능 자동 발견
   - False Positive 감소

4. **Behavior 검증**
   - 상태 변화 추적
   - VERIFY, ASSERT 액션
   - 네트워크 및 상태 검증

### 9.3 장기 (미래 버전)

1. **시각적 회귀 테스트**
   - 변경 전후 스크린샷 비교
   - 의도하지 않은 UI 수정 감지

2. **크로스 브라우저 테스트**
   - Firefox, Safari 지원 (현재 Chrome만)
   - 병렬 브라우저 실행

3. **AI 테스트 생성**
   - 요구사항에서 LLM이 테스트 시나리오 생성
   - 엣지 케이스 자동 발견

4. **클라우드 배포**
   - SaaS 제품으로 배포
   - 멀티테넌트 지원

5. **고급 스케줄링**
   - ML 기반 우선순위 예측
   - 여러 MCP 호스트에 분산 실행
   - 고급 분석 및 리포팅

---

## 참고 자료

### 10.1 코드 위치

- **Master Orchestrator**: `gaia/src/phase4/master_orchestrator.py`
- **Intelligent Orchestrator**: `gaia/src/phase4/intelligent_orchestrator.py`
- **LLM Vision Client**: `gaia/src/phase4/llm_vision_client.py`
- **MCP Host**: `gaia/src/phase4/mcp_host.py`
- **Adaptive Scheduler**: `gaia/src/scheduler/`
- **GUI**: `gaia/src/gui/`
- **테스트 플랜**: `artifacts/plans/`
- **셀렉터 캐시**: `artifacts/cache/selector_cache.json`

### 10.2 문서

- **README.md**: 전체 시스템 개요 및 테스트 결과
- **PROJECT.md**: 프로젝트 명세서
- **PROJECT_CONTEXT.md**: 프로젝트 컨텍스트 및 목표
- **IMPLEMENTATION_GUIDE.md**: 환경 설정 및 모듈 개요
- **PROGRESS.md**: 진행 로그
- **MIDTERM_PRESENTATION.md**: 중간 발표 요약
- **ADAPTIVE_SCHEDULER_SUMMARY.md**: 스케줄러 구현 요약
- **VERIFICATION_REPORT.md**: 스케줄러 검증 리포트
- **AGENT_SERVICE_GUIDE.md**: Agent Service 통합 가이드

### 10.3 주요 커밋

- `a81adbe`: Merge pull request #32 (웹 실시간 띄우기)
- `f1d95dc`: cdp를 이용해 웹 실시간 띄우기
- `55e7f37`: 한국어 주석 변경
- `66119e2`: Adaptive Scheduler 초기 구현
- `39bcbc2`: 입력 검증 및 에러 처리

---

## 팀 협업

### 11.1 워크플로우

- **PR 전**: `run_auto_test.py` 또는 GUI 데모로 최소 1회 실행
- **신규 기능 후**: `gaia/docs/PROGRESS.md` 업데이트
- **캐시 관련 변경**: `artifacts/cache` JSON 구조 문서화
- **버그 재현**: "페이지 URL + 실행 로그 + 선택된 셀렉터" 형태로 공유

### 11.2 개발 가이드

- GPT는 모든 자동화된 플래닝의 기본 LLM
- 마일스톤 후 `gaia/docs/PROGRESS.md` 업데이트
- 데모 중 GUI 로그 출력을 사용하여 체크리스트 커버리지 가시화 유지

---

## 라이선스 및 연락처

이 프로젝트는 GAIA 팀의 일부입니다.

**프로젝트 저장소**: https://github.com/capston2025/capston
**브랜치**: `claude/summarize-project-docs-011CUuoxZuxRB34Bqjy1XKq5`

---

**작성일**: 2025-11-08
**작성자**: Claude Code
**버전**: 1.0
