#!/bin/bash

# QA Agent Quick Start Script
# This script is used to quickly start and test the QA Agent.
# 이 스크립트는 QA Agent를 빠르게 시작하고 테스트하는 데 사용됩니다.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_SERVICE_DIR="$SCRIPT_DIR/gaia/agent-service"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}     QA Agent Quick Start Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check Node.js
echo -e "${BLUE}🔍 Checking Node.js...${NC}"
if ! command -v node &> /dev/null; then
    echo -e "${RED}❌ Node.js not found. Please install Node.js >= 18.0.0${NC}"
    exit 1
fi
NODE_VERSION=$(node --version)
echo -e "${GREEN}✅ Node.js ${NODE_VERSION} found${NC}"
echo ""

# Check Python
echo -e "${BLUE}🔍 Checking Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python not found. Please install Python >= 3.10${NC}"
    exit 1
fi
PYTHON_VERSION=$(python3 --version)
echo -e "${GREEN}✅ ${PYTHON_VERSION} found${NC}"
echo ""

# Install Agent Service dependencies
echo -e "${BLUE}📦 Installing Agent Service dependencies...${NC}"
cd "$AGENT_SERVICE_DIR"
if [ ! -d "node_modules" ]; then
    npm install
    echo -e "${GREEN}✅ Dependencies installed${NC}"
else
    echo -e "${YELLOW}ℹ️  Dependencies already installed${NC}"
fi
echo ""

# Check for .env file
echo -e "${BLUE}🔍 Checking environment variables...${NC}"
if [ ! -f "$AGENT_SERVICE_DIR/.env" ]; then
    echo -e "${YELLOW}⚠️  .env file not found. Creating from .env.example...${NC}"
    cp "$AGENT_SERVICE_DIR/.env.example" "$AGENT_SERVICE_DIR/.env"
    echo -e "${RED}❗ Please edit gaia/agent-service/.env and add your OPENAI_API_KEY${NC}"
    echo -e "${RED}   Then run this script again.${NC}"
    exit 1
fi

# Check if OPENAI_API_KEY is set
if ! grep -q "OPENAI_API_KEY=sk-" "$AGENT_SERVICE_DIR/.env"; then
    echo -e "${RED}❌ OPENAI_API_KEY not set in .env file${NC}"
    echo -e "${RED}   Please edit gaia/agent-service/.env and add your API key${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Environment variables configured${NC}"
echo ""

# Build TypeScript
echo -e "${BLUE}🔨 Building TypeScript...${NC}"
npm run build
echo -e "${GREEN}✅ Build successful${NC}"
echo ""

# Start the service
echo -e "${BLUE}🚀 Starting Agent Service...${NC}"
echo -e "${YELLOW}   Press Ctrl+C to stop the service${NC}"
echo ""
echo -e "${GREEN}📝 Service will be available at:${NC}"
echo -e "${GREEN}   Health check: http://localhost:3000/health${NC}"
echo -e "${GREEN}   Analysis API: POST http://localhost:3000/api/analyze${NC}"
echo ""
echo -e "${BLUE}========================================${NC}"
echo ""

npm run dev
