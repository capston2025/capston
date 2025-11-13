#!/bin/bash
# ICR 측정 간단 버전 - 기존 플랜 파일 사용

echo "======================================"
echo "📊 GAIA ICR 측정 (기존 플랜 사용)"
echo "======================================"

# 최신 플랜 파일 찾기
LATEST_PLAN=$(ls -t /Users/coldmans/Documents/GitHub/capston/artifacts/plans/test-sitev2*.json 2>/dev/null | head -1)

if [ -z "$LATEST_PLAN" ]; then
    echo "❌ 플랜 파일을 찾을 수 없습니다!"
    echo "다음 중 하나를 실행하세요:"
    echo "  1. GUI로 플랜 생성: python train_local.py"
    echo "  2. 또는 직접 지정: bash $0 <plan_file_path>"
    exit 1
fi

# 인자로 플랜 파일이 주어지면 그것을 사용
if [ -n "$1" ]; then
    PLAN_FILE="$1"
else
    PLAN_FILE="$LATEST_PLAN"
fi

echo ""
echo "📄 플랜 파일: $PLAN_FILE"
echo ""

# 플랜 파일 내용 확인
echo "플랜 내용 확인 중..."
SCENARIO_COUNT=$(python3 -c "import json; data=json.load(open('$PLAN_FILE')); print(len(data.get('test_scenarios', [])))")
echo "✅ 시나리오 수: $SCENARIO_COUNT"
echo ""

# 사용자에게 측정할 기능 입력받기
echo "측정할 기능을 입력하세요 (예: 로그인, 장바구니, 검색)"
echo "Enter를 누르면 전체 측정:"
read -p "기능명: " FEATURE

if [ -z "$FEATURE" ]; then
    echo "전체 기능 측정 모드"
    FEATURE_ARG=""
else
    echo "🎯 측정 대상: $FEATURE"
    FEATURE_ARG="--feature $FEATURE"
fi

# ICR 측정
echo ""
echo "Step: ICR 측정 중..."
OUTPUT_FILE="metrics_result_$(date +%Y%m%d_%H%M%S).json"

cd /Users/coldmans/Documents/GitHub/capston

python3 measure_metrics.py \
  --plan "$PLAN_FILE" \
  --log /tmp/agent-service-metrics-test.log \
  $FEATURE_ARG \
  --ground-truth ground_truth.json \
  --audit audit.json \
  --output "$OUTPUT_FILE"

echo ""
echo "======================================"
echo "✅ 측정 완료!"
echo "📊 결과 파일: $OUTPUT_FILE"
echo "======================================"
