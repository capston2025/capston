#!/usr/bin/env python3
"""Test Agent Builder with UI components spec PDF"""
import sys
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase1.agent_client import AgentServiceClient

print("=" * 80)
print("🧪 Testing Agent Builder with UI Components Specification")
print("=" * 80)

# Step 1: Load PDF
print("\n📄 Step 1: Loading PDF...")
loader = PDFLoader()
try:
    result = loader.extract('ui_components_spec.pdf')
    print(f"✅ PDF loaded: {len(result.text)} characters")
    print(f"📝 Heuristic checklist items: {len(result.checklist_items)}")
except Exception as e:
    print(f"❌ Failed to load PDF: {e}")
    sys.exit(1)

# Step 2: Check Agent Service
print("\n🔍 Step 2: Checking Agent Service...")
client = AgentServiceClient()
if not client.health_check():
    print("❌ Agent service is not running!")
    print("   Start it with: cd agent-service && npm run dev")
    sys.exit(1)
print("✅ Agent service is healthy")

# Step 3: Call Agent Builder
print("\n🤖 Step 3: Calling Agent Builder...")
print("   📊 Analyzing comprehensive UI specification...")
print("   ⏱️  This may take 10-20 seconds (large document)...")
print()

try:
    analysis = client.analyze_document(result.text)

    print("=" * 80)
    print("✅ AGENT BUILDER ANALYSIS COMPLETE")
    print("=" * 80)

    print(f"\n📊 Summary:")
    print(f"   Total Test Cases: {analysis.summary['total']}")
    print(f"   MUST (필수):     {analysis.summary['must']}")
    print(f"   SHOULD (권장):   {analysis.summary['should']}")
    print(f"   MAY (선택):      {analysis.summary['may']}")

    print(f"\n📋 Generated Test Cases:\n")

    # Group by priority
    must_cases = [tc for tc in analysis.checklist if tc.priority == 'MUST']
    should_cases = [tc for tc in analysis.checklist if tc.priority == 'SHOULD']
    may_cases = [tc for tc in analysis.checklist if tc.priority == 'MAY']

    if must_cases:
        print(f"🔴 MUST (필수) - {len(must_cases)}개:")
        for tc in must_cases:
            print(f"   • {tc.id}: {tc.name}")
            if tc.steps:
                print(f"     Steps: {len(tc.steps)} steps")

    if should_cases:
        print(f"\n🟡 SHOULD (권장) - {len(should_cases)}개:")
        for tc in should_cases:
            print(f"   • {tc.id}: {tc.name}")

    if may_cases:
        print(f"\n🟢 MAY (선택) - {len(may_cases)}개:")
        for tc in may_cases:
            print(f"   • {tc.id}: {tc.name}")

    print("\n" + "=" * 80)
    print("✅ Test Complete!")
    print("=" * 80)
    print("\n💡 This is what the GAIA GUI will show when you drop the PDF:")
    print("   1. Immediate heuristic checklist from PDF analysis")
    print("   2. Background: Agent Builder generating comprehensive test cases")
    print("   3. Complete: AI-generated test cases replace heuristic checklist")
    print(f"\n📄 Total test cases that will appear in GUI: {len(analysis.checklist)}")

except Exception as e:
    print(f"❌ Agent Builder failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
