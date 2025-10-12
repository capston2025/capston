GAIA 프로젝트 컨텍스트
최종 업데이트: 2025-10-11
Phase: 1학기 MVP 구현
Status: 설계 완료, 구현 시작

🎯 프로젝트 개요
이름
GAIA - Goal-oriented Autonomous Intelligence for Adaptive GUI Testing
목적
기획서 기반 자동 QA 테스트 생성 및 실행 시스템
핵심 기능

기획서 자동 분석 → 테스트 플랜 생성
체크리스트 실시간 트래킹 → 기능 발견 추적
LLM Agent 자동 탐색 → 웹사이트 자동 탐색
Gap Detection → 누락 기능 발견


📐 아키텍처 (1학기 MVP)
레이어 구조
User → GUI → Orchestrator
         ↓
    Phase 1 (Spec Analysis)
         ↓
    Checklist Tracker (Runtime)
         ↓
    Phase 4 (LLM Agent + MCP)
         ↓
    Phase 5 (Simple Report)
핵심 컴포넌트
1. Phase 1: Spec Analysis

Input: 기획서 PDF
Process: GPT-4o 단일 분석 (텍스트 압축 + 플랜 생성)
Output: 테스트 플랜 (100개) + 체크리스트 (25개)

2. Checklist Tracker

실시간 상태 관리
Redis 저장
GUI 업데이트

3. Phase 4: LLM Agent

GPT-4o + Playwright MCP
자동 웹 탐색
기능 발견 시 체크리스트 업데이트
핵심: DSL 없이 LLM이 직접 도구 사용

4. Phase 5: Simple Report

체크된 기능 목록
미발견 기능 목록
Coverage 통계


🗂️ 프로젝트 구조
gaia/
├── src/
│   ├── phase1/
│   │   ├── __init__.py
│   │   ├── pdf_loader.py       # PDF 텍스트 추출
│   │   └── analyzer.py         # LLM 분석
│   ├── phase4/
│   │   ├── __init__.py
│   │   └── agent.py            # LLM Agent + MCP
│   ├── phase5/
│   │   ├── __init__.py
│   │   └── report.py           # 리포트 생성
│   ├── tracker/
│   │   ├── __init__.py
│   │   └── checklist.py        # 체크리스트 관리
│   ├── gui/
│   │   ├── __init__.py
│   │   └── main_window.py      # PySide6 GUI
│   └── utils/
│       ├── __init__.py
│       └── config.py           # 설정 관리
├── tests/
│   ├── test_phase1.py
│   ├── test_phase4.py
│   └── test_integration.py
├── artifacts/
│   ├── spec.pdf                # 가상 기획서
│   ├── architecture_v1.png     # 아키텍처 다이어그램
│   └── workflow_v1.png         # 워크플로우 다이어그램
├── docs/
│   ├── PROJECT_CONTEXT.md      # 이 파일
│   ├── PROGRESS.md             # 진행 상황
│   └── IMPLEMENTATION_GUIDE.md # 구현 가이드
├── requirements.txt
├── README.md
└── main.py

🛠️ 기술 스택
Backend

Python 3.10+
GPT-4o: 기획서 분석 및 Agent
GPT-4o: 텍스트 압축
Playwright: 브라우저 자동화
Redis: 실시간 상태 저장
PostgreSQL: 영구 데이터 (선택, 2학기)

Frontend

PySide6: GUI 프레임워크
Qt Signal/Slot: 실시간 업데이트

Libraries
openai>=1.0.0
playwright>=1.40.0
PySide6>=6.6.0
redis>=5.0.0
pypdf>=3.17.0
python-dotenv>=1.0.0

🔑 핵심 설계 결정
1. MVP는 DSL 없음
결정: LLM이 직접 Playwright MCP 도구 사용
이유:

4주 안에 DSL + Parser 구현 불가능
LLM Agent 방식이 더 빠름
2학기에 DSL 추가 가능

2. Phase 3 (Gap Detection) 삭제
결정: 체크리스트 트래킹으로 대체
이유:

초기 DOM만으로는 정확한 Gap 판단 불가
동적 탐색하면서 체크하는 게 더 정확
False Positive 방지

3. Behavior 검증은 2학기
결정: 1학기는 요소 발견만
이유:

상태 변화 추적은 복잡
MVP는 "찾는 것"에 집중
2학기에 VERIFY, ASSERT 추가


📊 데이터 플로우
Phase 1
PDF (25k tokens)
  ↓ GPT-4o
Compressed (5k tokens)
  ↓ GPT-4o
{
  "test_plan": [100개 테스트],
  "checklist": [25개 기능]
}
  ↓
Redis 저장
Phase 4
Checklist (25개, all unchecked)
  ↓
GPT Agent with MCP Tools:
  - playwright.click()
  - playwright.fill()
  - playwright.get_visible_text()
  - checklist_tracker.mark_found()
  ↓ (루프)
Checklist (24개 checked, 1개 unchecked)
  ↓
Redis 업데이트 (실시간)
  ↓
GUI Signal → 화면 업데이트
Phase 5
Redis에서 Checklist 로드
  ↓
{
  "checked": 24,
  "unchecked": 1,
  "coverage": 96%
}
  ↓
GUI에 표시

🎯 1학기 목표
최소 목표 (Pass)

✅ 기획서 → 테스트 플랜 생성
✅ 체크리스트 생성
✅ 웹 네비게이션 (5-10 페이지)
✅ 체크리스트 15/25 체크
✅ GUI 실시간 표시

목표 (Success)

✅ 체크리스트 20/25 체크
✅ 간단한 결과 리포트
✅ 데모 가능

우수 (Excellent)

✅ 체크리스트 24/25 체크
✅ 로그인 → 장바구니 플로우 동작
✅ 발표 완벽


📅 개발 일정
Week 1 (완료)

✅ 아키텍처 설계
✅ 다이어그램 작성
✅ 기획서 작성
✅ Ground Truth 생성

Week 2 (이번 주)

🔄 프로젝트 셋업
🔄 Phase 1 프로토타입
🔄 GUI 기본 창
🔄 통합 테스트

Week 3

⏳ Phase 4 완전 구현
⏳ Playwright MCP 연동
⏳ 체크리스트 트래킹

Week 4

⏳ Phase 5 구현
⏳ GUI 완성
⏳ 통합 테스트

Week 5

⏳ 버그 수정
⏳ 데모 영상 촬영
⏳ 발표 준비


🚫 구현하지 않는 것 (2학기)
❌ DSL 생성 및 Parser
❌ Phase 2 (Initial Check)
❌ Phase 3 (Gap Detection)
❌ Behavior 검증 (VERIFY, ASSERT)
❌ 상태 변화 추적
❌ 배치 처리
❌ 재시도 로직
❌ 동적 테스트 발견
❌ Vector DB (RAG)
❌ 정량지표 측정
❌ 상세 리포트 (Markdown/PDF)

🔧 환경 설정
.env 파일
bashANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
REDIS_URL=redis://localhost:6379
Redis 실행
bashdocker run -d -p 6379:6379 redis:alpine
Playwright 설치
bashplaywright install chromium

💡 중요한 구현 포인트
1. Phase 1 프롬프트
명확한 JSON 구조 요청
예시 포함 (Few-shot)
"JSON만 출력" 강조
마크다운 제거 후처리
2. Phase 4 Agent 프롬프트
체크리스트 명시
도구 사용법 설명
"mark_found 호출" 강조
최대 반복 횟수 제한
3. GUI Real-time Update
python# Signal/Slot 패턴
class WorkerSignals(QObject):
    progress = Signal(dict)
    checklist_update = Signal(dict)
    log_message = Signal(str)

# Worker Thread
class Worker(QThread):
    def run(self):
        # Phase 실행
        signals.checklist_update.emit({
            'feature': 'login',
            'checked': True
        })
4. 체크리스트 매칭
python# 간단한 패턴 매칭 (1학기)
if 'login' in dom.lower() and 'button' in dom:
    checklist['login']['checked'] = True

# LLM 기반 매칭 (선택)
response = await gpt4o.chat(f"Does this DOM contain login? {dom}")

📈 성공 지표
기술적 지표

Coverage: 70%+ (20/25 이상)
GUI 반응성: 1초 이내 업데이트
안정성: 크래시 없이 10분 실행

발표 지표

데모 성공: 3-5분 안에 완료
실시간 표시: 체크리스트 업데이트 보임
결과 명확: 최종 Coverage 표시


🎓 발표 구성

문제 정의 (1분)
최종 비전 (1분) - v3 다이어그램
1학기 범위 (1분) - v1.0 다이어그램
데모 (5분) - 실제 실행
결과 (1분)
Q&A (1분)


📚 참고 자료
다이어그램

artifacts/architecture_v1.png - MVP 아키텍처
artifacts/workflow_v1.png - MVP 워크플로우
artifacts/architecture_v3.png - 최종 비전
artifacts/workflow_v3.png - 최종 비전

문서

artifacts/spec.pdf - 가상 기획서
artifacts/ground_truth.json - Ground Truth 100개
artifacts/metrics.md - 정량지표 측정 방법


🤝 협업 가이드
CLI별 역할
GPT (gpt-cli):

아키텍처 설계
복잡한 로직 구현
프롬프트 엔지니어링

Gemini (gemini-cli):

빠른 프로토타이핑
유틸리티 함수
테스트 코드

Codex (codex-cli):

보일러플레이트 생성
반복 코드 자동화
리팩토링

컨텍스트 공유 방법

이 파일 (PROJECT_CONTEXT.md) 읽기
PROJECT.md에서 최신 진행 상황 확인
IMPLEMENTATION_GUIDE.md에서 구현 가이드 확인
작업 완료 후 PROJECT.md 업데이트