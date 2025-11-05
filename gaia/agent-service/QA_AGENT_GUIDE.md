# QA Agent 완벽 가이드

## 📋 목차
1. [개요](#개요)
2. [아키텍처](#아키텍처)
3. [설치 및 실행](#설치-및-실행)
4. [사용 방법](#사용-방법)
5. [API 명세](#api-명세)
6. [테스트](#테스트)
7. [문제 해결](#문제-해결)
8. [고급 설정](#고급-설정)

## 개요

QA Agent는 GAIA 시스템의 핵심 구성 요소로, **기획서에서 자동으로 테스트 케이스를 생성**하는 AI 기반 서비스입니다.

### 주요 기능
- 📄 기획서 PDF/텍스트 자동 분석
- 🤖 OpenAI GPT-5를 활용한 지능형 테스트 케이스 생성
- 🎯 우선순위 자동 분류 (MUST/SHOULD/MAY)
- 📊 100개 이상의 테스트 시나리오 자동 생성
- 🔄 Playwright 자동화 테스트와 완벽 연동

### 기술 스택
- **Backend**: Node.js + TypeScript + Express
- **AI**: OpenAI Agent Builder (GPT-5)
- **Client**: Python requests
- **Container**: Docker + Docker Compose

## 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                    GAIA Python App                      │
│                                                         │
│  ┌─────────────┐         ┌──────────────┐             │
│  │  Phase 1    │────────▶│ Agent Client │             │
│  │ (Analyzer)  │         │  (Python)    │             │
│  └─────────────┘         └──────┬───────┘             │
│                                 │ HTTP POST            │
└─────────────────────────────────┼─────────────────────┘
                                  ▼
                    ┌─────────────────────────┐
                    │  Agent Service (Node.js)│
                    │                         │
                    │  - Express API Server   │
                    │  - @openai/agents SDK   │
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │   OpenAI Agent Builder  │
                    │   (Workflow: wf_68ea...) │
                    │   Model: GPT-5          │
                    └─────────────────────────┘
```

### 데이터 흐름

1. **입력**: 사용자가 기획서 PDF 업로드 또는 텍스트 입력
2. **분석**: Python Client가 Agent Service에 POST 요청
3. **처리**: Agent Service가 GPT-5에게 분석 요청
4. **생성**: GPT-5가 100+ 테스트 케이스 생성
5. **반환**: JSON 형식으로 구조화된 테스트 케이스 반환
6. **실행**: Adaptive Scheduler가 우선순위대로 테스트 실행

## 설치 및 실행

### 사전 요구사항

- Node.js >= 18.0.0
- Python >= 3.10
- OpenAI API Key (GPT-5 접근 권한 필요)

### 1. Agent Service 설치

```bash
cd gaia/agent-service
npm install
```

### 2. 환경 변수 설정

`.env` 파일 생성:

```bash
cp .env.example .env
```

`.env` 파일 편집:

```env
OPENAI_API_KEY=your_openai_api_key_here
PORT=3000
```

### 3. Agent Service 실행

#### 방법 A: 개발 모드

```bash
npm run dev
```

#### 방법 B: 프로덕션 빌드

```bash
npm run build
npm start
```

#### 방법 C: Docker 실행

```bash
docker-compose up -d
```

### 4. 서비스 확인

```bash
curl http://localhost:3000/health
```

예상 응답:
```json
{
  "status": "ok",
  "service": "agent-service"
}
```

## 사용 방법

### Python에서 사용

```python
from gaia.src.phase1.agent_client import AgentServiceClient

# 1. 클라이언트 초기화
client = AgentServiceClient(base_url="http://localhost:3000")

# 2. 서비스 상태 확인
if not client.health_check():
    print("Agent service is not running!")
    exit(1)

# 3. 기획서 분석
spec_text = """
온라인 쇼핑몰 기획서

주요 기능:
1. 회원가입 및 로그인
2. 상품 검색 및 조회
3. 장바구니 담기
4. 주문 및 결제
"""

result = client.analyze_document(spec_text, timeout=300)

# 4. 결과 확인
print(f"총 테스트 케이스: {result.summary['total']}")
for tc in result.checklist:
    print(f"[{tc.priority}] {tc.name}")
    print(f"  Steps: {' → '.join(tc.steps)}")
```

### cURL로 직접 호출

```bash
curl -X POST http://localhost:3000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "input_as_text": "온라인 쇼핑몰 기획서\n\n주요 기능:\n1. 회원가입 및 로그인"
  }'
```

### 통합 테스트 실행

```bash
# Python 의존성 설치
pip install -r gaia/requirements.txt

# Agent Service 시작 (별도 터미널)
cd gaia/agent-service && npm run dev

# 통합 테스트 실행
python gaia/test_qa_agent.py
```

## API 명세

### GET /health

서비스 상태 확인

**Request**
```
GET /health
```

**Response**
```json
{
  "status": "ok",
  "service": "agent-service"
}
```

### POST /api/analyze

기획서 분석 및 테스트 케이스 생성

**Request**
```json
{
  "input_as_text": "기획서 텍스트 내용..."
}
```

**Response**
```json
{
  "success": true,
  "data": {
    "output_text": "{\"checklist\": [...], \"summary\": {...}}"
  }
}
```

**output_text 파싱 후 구조**
```json
{
  "checklist": [
    {
      "id": "TC001",
      "name": "로그인 성공 테스트",
      "category": "authentication",
      "priority": "MUST",
      "precondition": "로그아웃 상태",
      "steps": [
        "로그인 버튼 클릭",
        "이메일에 test@test.com 입력",
        "비밀번호에 password123 입력",
        "로그인 버튼 클릭"
      ],
      "expected_result": "대시보드 페이지로 이동하고 환영 메시지 표시"
    }
  ],
  "summary": {
    "total": 25,
    "must": 15,
    "should": 8,
    "may": 2
  }
}
```

### 우선순위 정의

| Priority | 의미 | 실행 순서 |
|----------|------|-----------|
| **MUST** | 핵심 기능, 반드시 동작해야 함 | 1순위 |
| **SHOULD** | 중요 기능, 대부분 동작해야 함 | 2순위 |
| **MAY** | 부가 기능, 선택적 | 3순위 |

## 테스트

### 단위 테스트

```bash
cd gaia/agent-service
npm test
```

### 통합 테스트

```bash
# Agent Service 실행 필요
python gaia/test_qa_agent.py
```

### 테스트 시나리오

1. **Health Check 테스트**: 서비스 정상 동작 확인
2. **Document Analysis 테스트**: 기획서 → 테스트 케이스 생성
3. **JSON Validation 테스트**: 출력 형식 검증

### 예상 테스트 출력

```
============================================================
QA Agent Integration Test Suite
============================================================

🔍 Testing Agent Service Health Check...
✅ Agent service is healthy

🔍 Testing Document Analysis...
✅ Analysis completed successfully

📊 Summary:
   Total test cases: 18
   MUST: 10
   SHOULD: 6
   MAY: 2

📋 Generated Test Cases:

   1. [TC001] 회원가입 성공
      Priority: MUST
      Category: authentication
      Steps: 5 steps
      First step: 회원가입 버튼 클릭

============================================================
Test Results Summary
============================================================
✅ PASSED: Health Check
✅ PASSED: Document Analysis
✅ PASSED: JSON Validation

Total: 3/3 tests passed
```

## 문제 해결

### 문제: Agent Service가 시작되지 않음

**증상**
```
Error: listen EADDRINUSE: address already in use :::3000
```

**해결방법**
```bash
# 포트 사용 중인 프로세스 확인
lsof -ti:3000

# 프로세스 종료
lsof -ti:3000 | xargs kill -9

# 또는 .env에서 포트 변경
PORT=3001
```

### 문제: OpenAI API 에러

**증상**
```
Error: Incorrect API key provided
```

**해결방법**
1. `.env` 파일에서 `OPENAI_API_KEY` 확인
2. API Key가 GPT-5 접근 권한을 가지고 있는지 확인
3. API Key 유효성 테스트:
   ```bash
   curl https://api.openai.com/v1/models \
     -H "Authorization: Bearer $OPENAI_API_KEY"
   ```

### 문제: Python Client 연결 실패

**증상**
```
ConnectionRefusedError: [Errno 111] Connection refused
```

**해결방법**
1. Agent Service가 실행 중인지 확인:
   ```bash
   curl http://localhost:3000/health
   ```

2. 방화벽 설정 확인

3. Docker 사용 시 네트워크 설정 확인:
   ```bash
   docker-compose logs agent-service
   ```

### 문제: 타임아웃 에러

**증상**
```
ReadTimeout: HTTPConnectionPool(host='localhost', port=3000): Read timed out
```

**해결방법**
1. 타임아웃 시간 증가:
   ```python
   result = client.analyze_document(text, timeout=600)  # 10분
   ```

2. 기획서 길이 확인 (너무 긴 경우 분할)

3. GPT-5 응답 시간이 느릴 수 있음 (10-15분)

## 고급 설정

### GPT-5 모델 설정

`src/workflow.ts` 파일에서 모델 설정:

```typescript
const agent = new Agent({
  name: "Agent",
  model: "gpt-5",  // 모델 변경 가능
  modelSettings: {
    reasoning: {
      effort: "medium",  // low, medium, high
      summary: "auto"
    },
    store: true
  }
});
```

### 타임아웃 설정

`src/server.ts` 파일에서 서버 타임아웃:

```typescript
// GPT-5 처리를 위해 25분으로 설정
server.timeout = 1500000; // milliseconds
```

### 로깅 설정

환경 변수로 로깅 레벨 조정:

```env
LOG_LEVEL=debug  # error, warn, info, debug
```

### Docker 환경 변수

`docker-compose.yml`에서 설정:

```yaml
environment:
  - OPENAI_API_KEY=your_key_here
  - PORT=3000
  - LOG_LEVEL=info
```

### 성능 최적화

1. **캐싱**: 동일한 기획서는 결과를 캐싱하여 재사용
2. **배치 처리**: 여러 기획서를 큐에 넣고 순차 처리
3. **병렬 처리**: 독립적인 기획서는 병렬로 처리

## 베스트 프랙티스

### 기획서 작성 팁

✅ **좋은 예시**
```
온라인 쇼핑몰 기획서

1. 회원가입 및 로그인
   - 이메일 인증
   - 소셜 로그인 (구글, 카카오)
   - 비밀번호 찾기

2. 상품 검색 및 조회
   - 키워드 검색
   - 카테고리별 필터
   - 가격순/인기순 정렬
```

❌ **나쁜 예시**
```
쇼핑몰을 만들 예정입니다.
```

### 테스트 케이스 활용

1. **우선순위 기반 실행**: MUST → SHOULD → MAY 순서로 실행
2. **회귀 테스트**: 생성된 테스트 케이스를 저장하여 재사용
3. **커버리지 추적**: Checklist Tracker로 기능 커버리지 모니터링

## 라이선스

GAIA 프로젝트의 일부로 제공됩니다.

## 지원

문제가 발생하면 GitHub Issues에 등록해주세요:
https://github.com/capston2025/capston/issues
