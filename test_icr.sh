#!/bin/bash
# ICR 측정 자동화 스크립트

echo "======================================"
echo "📊 GAIA ICR 측정 시작"
echo "======================================"

# 변수 설정
FEATURE="로그인"
SPEC_FILE="/Users/coldmans/Downloads/Test Site with UI Elements2/README.md"
URL="https://test-sitev2.vercel.app"
GITHUB_REPO="https://github.com/capston2025/TestSitev2"

echo ""
echo "🎯 측정 대상 기능: $FEATURE"
echo "📄 기획서: $SPEC_FILE"
echo "🌐 URL: $URL"
echo ""

# Step 1: Agent Service가 실행 중인지 확인
echo "Step 1: Agent Service 상태 확인..."
if ! curl -s http://localhost:3000/health > /dev/null; then
    echo "❌ Agent Service가 실행 중이 아닙니다!"
    echo "다음 명령으로 실행하세요:"
    echo "  cd /Users/coldmans/Documents/GitHub/capston/gaia/agent-service && npm start"
    exit 1
fi
echo "✅ Agent Service 실행 중"
echo ""

# Step 2: 플랜 생성
echo "Step 2: 테스트 플랜 생성 중..."
PLAN_OUTPUT=$(python3 << EOF
import sys
sys.path.append('/Users/coldmans/Documents/GitHub/capston/gaia/src')

from phase1.agent_client import AgentClient
import hashlib
import time

# 기획서 읽기
with open("$SPEC_FILE", 'r', encoding='utf-8') as f:
    spec = f.read()

# Agent 클라이언트 생성
client = AgentClient(service_url="http://localhost:3000")

print("📋 기획서 길이:", len(spec), "chars")
print("🚀 플랜 생성 요청 중...")

# 플랜 생성
result = client.generate_plan(
    input_doc=spec,
    feature_query="$FEATURE",
    github_repo="$GITHUB_REPO"
)

# 플랜 저장
url_hash = hashlib.md5("$URL".encode()).hexdigest()[:16]
plan_file = f"artifacts/plans/test-sitev2_vercel_app_{url_hash}_plan.json"

import json
with open(plan_file, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"✅ 플랜 생성 완료: {plan_file}")
print(f"📊 생성된 시나리오 수: {len(result.get('test_scenarios', []))}")
print(plan_file)  # 마지막 라인에 파일 경로 출력
EOF
)

# 플랜 파일 경로 추출 (마지막 라인)
PLAN_FILE=$(echo "$PLAN_OUTPUT" | tail -1)
echo "$PLAN_OUTPUT"
echo ""

# Step 3: ICR 측정
echo "Step 3: ICR 측정 중..."
python3 measure_metrics.py \
  --plan "$PLAN_FILE" \
  --log /tmp/agent-service-metrics-test.log \
  --feature "$FEATURE" \
  --ground-truth ground_truth.json \
  --output "metrics_${FEATURE}_$(date +%Y%m%d_%H%M%S).json"

echo ""
echo "======================================"
echo "✅ ICR 측정 완료!"
echo "======================================"
