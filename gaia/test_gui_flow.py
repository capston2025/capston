#!/usr/bin/env python3
"""Test the GUI flow without actually opening GUI"""
import sys
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase1.agent_client import AgentServiceClient

print("=" * 60)
print("Testing GAIA PDF → Agent Builder Flow")
print("=" * 60)

# Step 1: Load PDF
print("\n📄 Step 1: Loading PDF...")
loader = PDFLoader()
try:
    result = loader.extract('test_spec.pdf')
    print(f"✅ PDF loaded: {len(result.text)} characters")
    print(f"📝 Heuristic checklist items: {len(result.checklist_items)}")
    for item in result.checklist_items[:3]:
        print(f"   - {item}")
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
print("   (This will take 5-15 seconds...)")
try:
    analysis = client.analyze_document(result.text)
    print(f"✅ Analysis complete!")
    print(f"   Total: {analysis.summary['total']} test cases")
    print(f"   MUST: {analysis.summary['must']}")
    print(f"   SHOULD: {analysis.summary['should']}")
    print(f"   MAY: {analysis.summary['may']}")

    print(f"\n📋 Generated Test Cases:")
    for tc in analysis.checklist:
        print(f"   [{tc.priority}] {tc.name}")

except Exception as e:
    print(f"❌ Agent Builder failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("✅ All steps completed successfully!")
print("=" * 60)
print("\nThis is what should happen in the GUI:")
print("1. Drop PDF → Immediate heuristic checklist")
print("2. Background: Agent Builder running...")
print("3. Complete → AI-generated checklist replaces heuristic one")
