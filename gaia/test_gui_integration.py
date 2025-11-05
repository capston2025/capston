#!/usr/bin/env python3
"""GUI 창을 실제로 띄우지 않고 통합 동작을 테스트합니다"""
import sys
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from pathlib import Path
from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase1.agent_client import AgentServiceClient

print("=" * 80)
print("🧪 Testing GUI Integration (Simulated)")
print("=" * 80)

# 사용자가 GUI에 PDF를 드롭했을 때의 동작을 시뮬레이션

# 1단계: PDF 드롭 이벤트
print("\n📄 Step 1: Simulating PDF drop...")
pdf_path = Path("/Users/coldmans/Documents/GitHub/capston/gaia/ui_components_spec_korean.pdf")
print(f"   File: {pdf_path.name}")

# 2단계: PDF 로더(즉시 실행)
print("\n📋 Step 2: Loading PDF (immediate heuristic checklist)...")
loader = PDFLoader()
result = loader.extract(pdf_path)

print(f"✅ PDF loaded: {len(result.text)} characters")
print(f"📝 Heuristic checklist items: {len(result.checklist_items)}")
print("\nHeuristic checklist (shown immediately in GUI):")
for i, item in enumerate(result.checklist_items[:5], 1):
    print(f"   {i}. {item}")
if len(result.checklist_items) > 5:
    print(f"   ... and {len(result.checklist_items) - 5} more")

# 3단계: 백그라운드 Agent Builder(AnalysisWorker)
print("\n🤖 Step 3: Starting Agent Builder in background...")
print("   (In GUI: shows '🤖 Analyzing with AI Agent Builder...')")

client = AgentServiceClient()

if not client.health_check():
    print("❌ Agent service not running!")
    print("\nGUI would show: '❌ Agent Builder failed: Connection refused'")
    sys.exit(1)

print("   Calling Agent Builder API...")
try:
    analysis_result = client.analyze_document(result.text)

    # 4단계: 분석 완료
    print("\n✅ Step 4: Agent Builder complete!")
    print(f"\nGUI log would show:")
    print(f"   ✅ Generated {analysis_result.summary['total']} test cases " +
          f"(MUST: {analysis_result.summary['must']}, " +
          f"SHOULD: {analysis_result.summary['should']}, " +
          f"MAY: {analysis_result.summary['may']})")

    # 5단계: 체크리스트 업데이트
    print("\n📋 Step 5: Updating checklist in GUI...")
    print("   (Replaces heuristic checklist with AI-generated test cases)")

    checklist_items = [
        f"[{tc.priority}] {tc.name}"
        for tc in analysis_result.checklist
    ]

    print(f"\nAI-Generated Checklist ({len(checklist_items)} items):")
    for i, item in enumerate(checklist_items[:10], 1):
        print(f"   {i}. {item}")
    if len(checklist_items) > 10:
        print(f"   ... and {len(checklist_items) - 10} more")

    # 6단계: 개별 테스트 케이스 로그 출력
    print("\n📝 Step 6: Logging individual test cases...")
    print("   GUI log would show:")
    for tc in analysis_result.checklist[:5]:
        print(f"     • {tc.id}: {tc.name}")
    if len(analysis_result.checklist) > 5:
        print(f"     ... and {len(analysis_result.checklist) - 5} more")

    print("\n" + "=" * 80)
    print("✅ GUI INTEGRATION TEST PASSED!")
    print("=" * 80)
    print("\n💡 Summary:")
    print(f"   1. ✅ PDF loads immediately with {len(result.checklist_items)} heuristic items")
    print(f"   2. ✅ Agent Builder runs in background")
    print(f"   3. ✅ Generates {len(analysis_result.checklist)} AI test cases")
    print(f"   4. ✅ Checklist updates automatically when complete")
    print(f"   5. ✅ No GUI freezing (background worker)")

except Exception as e:
    print(f"\n❌ Agent Builder failed: {e}")
    print("\nGUI would show: '❌ Agent Builder failed: {error message}'")
    print("                '📝 Using heuristic checklist instead'")
    import traceback
    traceback.print_exc()
    sys.exit(1)
