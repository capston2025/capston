# GAIA - AI 기반 QA 자동화 시스템 🚀

귀살대 팀의 캡스톤 프로젝트로, URL 입력만으로 웹사이트를 실시간 분석하여 자동화된 테스트 시나리오를 생성하는 시스템입니다.

## 📋 프로젝트 개요

**GAIA**는 웹사이트의 DOM 구조를 실시간으로 분석하고, AI를 활용해 QA 테스트 시나리오를 자동 생성하는 혁신적인 도구입니다.

### 🎯 핵심 기능
- **실시간 DOM 분석**: Playwright를 통한 실제 웹사이트 요소 추출
- **AI 테스트 시나리오 생성**: OpenAI GPT 모델 기반 자동화된 테스트 케이스 생성
- **웹 UI**: React 기반 사용자 친화적 인터페이스
- **데스크톱 앱**: PyQt6 기반 네이티브 클라이언트

## 🏗️ 프로젝트 구조

```
capston/
├── mcp/                   # MCP Host (Playwright 자동화 서버)
├── server/                # FastAPI 백엔드 서버
│   ├── main.py            # 메인 API 서버 (AI 연동)
│   ├── mock_data/         # 테스트용 목 데이터
│   └── CLAUDE.md          # 상세 개발 진행상황
├── agent/                 # React 웹 프론트엔드
│   └── gaia-agent-ui/     # Vite + React + TypeScript
├── desktop_app/           # PyQt6 데스크톱 애플리케이션
│   └── app/               # 네이티브 클라이언트
├── engine/                # 테스트 엔진 (예정)
└── infra/                 # 인프라 설정 (예정)
```

## ✅ 현재 완료된 기능

### 1. 웹사이트 DOM 실시간 분석
- **Playwright 연동**: 실제 브라우저를 통한 DOM 요소 추출
- **요소 인식**: 버튼, 입력 필드, 링크 등 상호작용 가능한 요소 자동 감지
- **실시간 처리**: URL 입력 즉시 웹사이트 분석 시작

### 2. AI 기반 테스트 시나리오 생성
- **OpenAI GPT 연동**: 고품질 테스트 시나리오 자동 생성
- **구조화된 출력**: JSON 형태의 표준화된 테스트 케이스
- **컨텍스트 인식**: DOM 구조와 기획서를 모두 고려한 지능적 시나리오 생성

### 3. RESTful API 서버
- **FastAPI 기반**: 고성능 비동기 웹 서버
- **CORS 지원**: 프론트엔드와 원활한 통신
- **에러 처리**: 견고한 fallback 메커니즘

### 4. 웹 사용자 인터페이스
- **React + TypeScript**: 모던 웹 프론트엔드
- **실시간 로그**: API 처리 과정 실시간 표시
- **Vite 개발 서버**: 빠른 개발 환경

### 5. 데스크톱 애플리케이션 (개발 중)
- **PyQt6 기반**: 크로스 플랫폼 네이티브 앱
- **임베디드 브라우저**: WebEngine을 통한 실시간 테스트 시각화
- **OS 수준 제어**: PyAutoGUI를 통한 시스템 자동화

## 🚀 실행 방법

### MCP Host 시작
```bash
cd mcp
pip install -r requirements.txt

# Playwright 브라우저 드라이버 설치
playwright install

python main.py
# MCP Host가 http://localhost:8001에서 실행됩니다
```

### 백엔드 서버 시작
```bash
cd server
pip install -r requirements.txt
python main.py
# 서버가 http://localhost:8000에서 실행됩니다
```

### 웹 프론트엔드 시작
```bash
cd agent/gaia-agent-ui
npm install
npm run dev
# 개발 서버가 http://localhost:5173에서 실행됩니다
```

### 데스크톱 앱 시작 (선택사항)
```bash
cd desktop_app
pip install -e .
python -m app
```

## 🛠️ 기술 스택

### 백엔드
- **FastAPI**: Python 웹 프레임워크
- **Playwright**: 브라우저 자동화
- **OpenAI GPT**: AI 모델
- **Pydantic**: 데이터 검증

### 프론트엔드
- **React 19**: UI 프레임워크
- **TypeScript**: 타입 안전성
- **Vite**: 빌드 도구

### 데스크톱
- **PyQt6**: GUI 프레임워크
- **PyAutoGUI**: 시스템 자동화

## 📈 주요 성과

### 🎯 혁신적 달성사항
1. **실제 웹사이트 분석**: 목 데이터 대신 실시간 DOM 분석 구현
2. **End-to-End 파이프라인**: URL 입력부터 테스트 코드 생성까지 완전 자동화
3. **멀티 플랫폼 지원**: 웹, 데스크톱 양쪽 환경 지원

### 📊 기술적 성취
- **13개 실제 요소 분석 성공**: gamegoo.co.kr에서 "바로 매칭", "게시판", "로그인" 등 실제 버튼 인식
- **안전한 JSON 파싱**: 위험한 `eval()` 대신 견고한 파싱 로직 구현
- **견고한 에러 처리**: Fallback 메커니즘으로 서비스 안정성 확보

## 🔮 향후 계획

### Phase 1: AI 모델 최적화
- OpenAI 대신 Claude API 적용 검토
- 로컬 LLM 모델 연동 (Ollama 등)
- 규칙 기반 시나리오 생성 보완

### Phase 2: 데스크톱 앱 완성
- Playwright 워커와 WebView 동기화
- OS 수준 입력 제어 구현
- 실시간 테스트 실행 및 시각화

### Phase 3: 고도화
- 다양한 웹사이트 호환성 확장
- 성능 최적화
- 배포 자동화 (PyInstaller, Docker)

## 🔧 API 사용 예시

### DOM 분석 + 테스트 시나리오 생성
```bash
curl -X POST "http://localhost:8000/analyze-and-generate" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

### DOM 분석만 실행
```bash
curl "http://localhost:8000/analyze-dom/https://example.com"
```

### 테스트 시나리오 실행 (MCP 호스트 직접 호출)
```bash
# MCP 호스트의 /execute 엔드포인트로 직접 테스트 시나리오 실행
curl -X POST "http://localhost:8001/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "execute_scenario",
    "params": {
        "scenario": {
            "id": "TC_LOGIN_01",
            "priority": "High",
            "scenario": "정상적인 로그인 테스트",
            "steps": [
                {
                    "description": "로그인 페이지로 이동",
                    "action": "goto",
                    "selector": "",
                    "params": ["https://example.com/login"]
                },
                {
                    "description": "사용자 이름 입력",
                    "action": "fill",
                    "selector": "#username",
                    "params": ["testuser"]
                },
                {
                    "description": "비밀번호 입력",
                    "action": "fill",
                    "selector": "#password",
                    "params": ["testpass"]
                },
                {
                    "description": "로그인 버튼 클릭",
                    "action": "click",
                    "selector": "button[type=\"submit\"]",
                    "params": []
                }
            ],
            "assertion": {
                "description": "로그인 후 대시보드 URL로 이동했는지 확인",
                "selector": "body",
                "condition": "url_contains",
                "params": ["/dashboard"]
            }
        }
    }
}'
```

## 👥 개발팀: 귀살대

본 프로젝트는 캡스톤 프로젝트의 일환으로 개발되었으며, AI와 자동화 기술을 활용한 QA 혁신을 목표로 합니다.

---

**🚀 "이제 시스템이 실제 웹사이트를 분석합니다!" - 목 데이터를 넘어선 진짜 자동화**