#!/usr/bin/env python3
"""Test Agent Builder with Korean-enabled UI spec PDF"""
import sys
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase1.agent_client import AgentServiceClient

print("=" * 80)
print("🧪 Testing Agent Builder with Korean UI Components Specification")
print("=" * 80)

# Step 1: Load PDF
print("\n📄 Step 1: Loading Korean PDF...")
loader = PDFLoader()
try:
    result = loader.extract('ui_components_spec_korean.pdf')
    print(f"✅ PDF loaded: {len(result.text)} characters")

    # Verify Korean text
    has_korean = any('\uac00' <= char <= '\ud7a3' for char in result.text[:1000])
    print(f"✅ Korean text verified: {has_korean}")

except Exception as e:
    print(f"❌ Failed to load PDF: {e}")
    sys.exit(1)

# Step 2: Check Agent Service
print("\n🔍 Step 2: Checking Agent Service...")
client = AgentServiceClient()
if not client.health_check():
    print("❌ Agent service is not running!")
    sys.exit(1)
print("✅ Agent service is healthy")

# Step 3: Call Agent Builder
print("\n🤖 Step 3: Calling Agent Builder...")
print("   📊 Analyzing comprehensive UI specification...")
print("   ⏱️  This may take 15-30 seconds (large Korean document)...")
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
        for i, tc in enumerate(must_cases[:10], 1):  # Show first 10
            print(f"   {i}. {tc.id}: {tc.name}")
        if len(must_cases) > 10:
            print(f"   ... and {len(must_cases) - 10} more")

    if should_cases:
        print(f"\n🟡 SHOULD (권장) - {len(should_cases)}개:")
        for i, tc in enumerate(should_cases[:5], 1):  # Show first 5
            print(f"   {i}. {tc.id}: {tc.name}")
        if len(should_cases) > 5:
            print(f"   ... and {len(should_cases) - 5} more")

    if may_cases:
        print(f"\n🟢 MAY (선택) - {len(may_cases)}개:")
        for i, tc in enumerate(may_cases[:5], 1):  # Show first 5
            print(f"   {i}. {tc.id}: {tc.name}")
        if len(may_cases) > 5:
            print(f"   ... and {len(may_cases) - 5} more")

    print("\n" + "=" * 80)
    print("✅ Test Complete!")
    print("=" * 80)
    print(f"\n📄 Total test cases generated: {len(analysis.checklist)}")
    print("\n💡 This is what the GAIA GUI will show:")
    print("   1. Drop PDF → Immediate heuristic checklist")
    print("   2. Background: Agent Builder running...")
    print(f"   3. Complete → {len(analysis.checklist)} AI-generated test cases displayed")

except Exception as e:
    print(f"❌ Agent Builder failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
