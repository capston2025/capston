# OpenAI Agent Service Integration Guide

## Overview

GAIA 프로젝트에 OpenAI Agent Builder를 통합한 Node.js 마이크로서비스입니다.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      GAIA Python App                        │
│                                                             │
│  ┌─────────────┐         ┌──────────────┐                 │
│  │  Phase 1    │────────▶│ Agent Client │                 │
│  │ (Analyzer)  │         │  (Python)    │                 │
│  └─────────────┘         └──────┬───────┘                 │
│                                 │ HTTP POST                │
└─────────────────────────────────┼─────────────────────────┘
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
                    └─────────────────────────┘
```

## Quick Start

### 1. Start Agent Service

**Option A: Development Mode**
```bash
cd agent-service
npm install
npm run dev
```

**Option B: Docker**
```bash
cd agent-service
docker-compose up -d
```

### 2. Test Service

```bash
# Health check
curl http://localhost:3000/health

# Test analysis
curl -X POST http://localhost:3000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"input_as_text": "테스트 문서"}'
```

### 3. Use in Python

```python
from src.phase1.agent_client import AgentServiceClient

client = AgentServiceClient()
result = client.analyze_document("기획서 내용...")

print(f"Total test cases: {result.summary['total']}")
for tc in result.checklist:
    print(f"- {tc.name}")
```

## API Endpoints

### GET /health
Health check endpoint

**Response:**
```json
{
  "status": "ok",
  "service": "agent-service"
}
```

### POST /api/analyze
Analyze document and generate test cases

**Request:**
```json
{
  "input_as_text": "기획서 내용..."
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "output_text": "{\"checklist\": [...], \"summary\": {...}}"
  }
}
```

## Python Client API

### AgentServiceClient

```python
class AgentServiceClient:
    def __init__(self, base_url: str = "http://localhost:3000")

    def health_check(self) -> bool
        """Check if service is healthy"""

    def analyze_document(self, text: str, timeout: int = 120) -> AnalysisResult
        """Analyze document and return test cases"""
```

### Data Structures

```python
@dataclass
class TestCase:
    id: str
    name: str
    category: str
    priority: str  # MUST, SHOULD, MAY
    precondition: str
    steps: List[str]
    expected_result: str

@dataclass
class AnalysisResult:
    checklist: List[TestCase]
    summary: Dict[str, int]  # {"total": 10, "must": 8, "should": 2, "may": 0}
```

## Docker Deployment

### Build Image

```bash
cd agent-service
docker build -t gaia-agent-service .
```

### Run Container

```bash
docker run -d \
  -p 3000:3000 \
  -e OPENAI_API_KEY=your_key_here \
  --name gaia-agent-service \
  gaia-agent-service
```

### Docker Compose

```bash
# Start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | - | OpenAI API key |
| `PORT` | No | 3000 | Server port |

## Integration with Phase 1

### Before (Direct OpenAI API)

```python
# phase1/analyzer.py
from openai import OpenAI

client = OpenAI()
response = client.chat.completions.create(...)
```

### After (Agent Service)

```python
# phase1/analyzer.py
from phase1.agent_client import AgentServiceClient

agent_client = AgentServiceClient()
result = agent_client.analyze_document(text)
```

## Troubleshooting

### Service Not Starting

```bash
# Check if port is in use
lsof -ti:3000

# Kill existing process
lsof -ti:3000 | xargs kill -9

# Restart service
npm run dev
```

### Connection Refused

```bash
# Verify service is running
curl http://localhost:3000/health

# Check logs
docker-compose logs agent-service
```

### API Key Issues

```bash
# Verify API key
echo $OPENAI_API_KEY

# Test API key
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

## File Structure

```
agent-service/
├── src/
│   ├── server.ts          # Express API server
│   └── workflow.ts        # Agent Builder workflow
├── .env                   # Environment variables
├── .env.example          # Example environment
├── Dockerfile            # Docker image
├── docker-compose.yml    # Docker compose config
├── package.json          # NPM dependencies
├── tsconfig.json         # TypeScript config
└── README.md            # Documentation

gaia/
└── src/
    └── phase1/
        └── agent_client.py  # Python client
```

## Performance

- **Average Response Time**: 5-15 seconds
- **Max Timeout**: 120 seconds (configurable)
- **Max Input Size**: 10MB (JSON body limit)

## Future Enhancements

- [ ] Add request caching (Redis)
- [ ] Add request rate limiting
- [ ] Add batch processing support
- [ ] Add webhook notifications
- [ ] Add metrics/monitoring (Prometheus)
- [ ] Add authentication/API keys
- [ ] Add request queue (Bull/BullMQ)

## Support

For issues, check:
1. Agent service logs: `docker-compose logs -f`
2. Python client errors: Check exception messages
3. OpenAI API status: https://status.openai.com/

## License

Part of the GAIA project.
