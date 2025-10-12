# GAIA Agent Service

Node.js microservice for executing OpenAI Agent Builder workflows.

## Overview

This service provides an HTTP API to execute QA test case generation using OpenAI's Agent Builder SDK. It's designed to be called by the main GAIA Python application.

## Setup

### 1. Install Dependencies

```bash
npm install
```

### 2. Configure Environment

Copy `.env.example` to `.env` and set your OpenAI API key:

```bash
cp .env.example .env
```

Edit `.env`:
```
OPENAI_API_KEY=your_openai_api_key_here
PORT=3000
```

### 3. Run Development Server

```bash
npm run dev
```

The server will start at `http://localhost:3000`

## API Endpoints

### Health Check

```bash
GET /health
```

Response:
```json
{
  "status": "ok",
  "service": "agent-service"
}
```

### Analyze Document

```bash
POST /api/analyze
```

Request body:
```json
{
  "input_as_text": "기획서 내용..."
}
```

Response:
```json
{
  "success": true,
  "data": {
    "output_text": "{\"checklist\": [...], \"summary\": {...}}"
  }
}
```

## Docker Usage

### Build and Run with Docker Compose

```bash
docker-compose up -d
```

### Build Docker Image

```bash
docker build -t gaia-agent-service .
```

### Run Docker Container

```bash
docker run -d -p 3000:3000 \
  -e OPENAI_API_KEY=your_key_here \
  --name gaia-agent-service \
  gaia-agent-service
```

## Python Client Example

```python
import requests
import json

def analyze_document(text: str) -> dict:
    """Call the agent service to analyze a document."""
    url = "http://localhost:3000/api/analyze"

    response = requests.post(
        url,
        json={"input_as_text": text},
        headers={"Content-Type": "application/json"}
    )

    response.raise_for_status()
    result = response.json()

    if result["success"]:
        # Parse the output_text which contains JSON
        output_json = json.loads(result["data"]["output_text"])
        return output_json
    else:
        raise Exception(f"Analysis failed: {result.get('error')}")

# Usage
with open("spec.pdf", "r") as f:
    spec_text = f.read()

result = analyze_document(spec_text)
print(f"Total test cases: {result['summary']['total']}")
print(f"Checklist: {result['checklist']}")
```

## Production Build

```bash
# Build TypeScript
npm run build

# Run production server
npm start
```

## Architecture Integration

This service integrates with the GAIA project as follows:

```
GAIA Python App (Phase 1)
    ↓ HTTP POST
Agent Service (Node.js) ← OpenAI Agent Builder
    ↓ HTTP Response
GAIA Python App (Checklist Tracker)
```

## Troubleshooting

### Port Already in Use

Change the port in `.env`:
```
PORT=3001
```

### OpenAI API Key Issues

Verify your API key is correct and has access to the Agent Builder workflows:
```bash
curl -H "Authorization: Bearer $OPENAI_API_KEY" https://api.openai.com/v1/models
```

### Connection Refused

Ensure the service is running:
```bash
curl http://localhost:3000/health
```
