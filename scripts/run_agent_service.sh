#!/bin/bash

# 에이전트 서비스 실행 스크립트
# Usage: ./scripts/run_agent_service.sh [dev|prod]

set -e

# 프로젝트 루트 디렉토리로 이동
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_SERVICE_DIR="$PROJECT_ROOT/gaia/agent-service"

echo "🚀 Starting Agent Service..."
echo "📂 Project root: $PROJECT_ROOT"
echo "📂 Agent service directory: $AGENT_SERVICE_DIR"

# agent-service 디렉토리 존재 확인
if [ ! -d "$AGENT_SERVICE_DIR" ]; then
    echo "❌ Error: agent-service directory not found at $AGENT_SERVICE_DIR"
    exit 1
fi

# agent-service 디렉토리로 이동
cd "$AGENT_SERVICE_DIR"

# .env 파일 확인
if [ ! -f "$PROJECT_ROOT/gaia/.env" ]; then
    echo "⚠️  Warning: .env file not found at $PROJECT_ROOT/gaia/.env"
    echo "   The service may not work properly without environment variables."
fi

# node_modules 확인 및 설치
if [ ! -d "node_modules" ]; then
    echo "📦 Installing dependencies..."
    npm install
fi

# 실행 모드 결정 (기본값: dev)
MODE="${1:-dev}"

echo "🔧 Running in $MODE mode..."
echo ""

# 모드에 따라 실행
if [ "$MODE" = "prod" ]; then
    # 프로덕션 모드: 빌드 후 실행
    echo "🔨 Building TypeScript..."
    npm run build

    echo "▶️  Starting production server..."
    npm start
elif [ "$MODE" = "dev" ]; then
    # 개발 모드: ts-node로 직접 실행
    echo "▶️  Starting development server with ts-node..."
    npm run dev
else
    echo "❌ Error: Invalid mode '$MODE'. Use 'dev' or 'prod'."
    exit 1
fi
