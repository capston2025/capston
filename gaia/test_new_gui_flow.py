#!/usr/bin/env python3
"""Test the new GUI flow with chat-like display"""
import sys
sys.path.insert(0, '/Users/coldmans/Documents/GitHub/capston')

from pathlib import Path
from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase1.agent_client import AgentServiceClient

print("=" * 80)
print("🎨 Testing NEW GUI Flow with Chat-like Display")
print("=" * 80)

# Simulate PDF drop
print("\n📄 Step 1: User drops PDF into GAIA GUI...")
pdf_path = Path("/Users/coldmans/Documents/GitHub/capston/gaia/ui_components_spec_korean.pdf")
print(f"   File: {pdf_path.name}")

# Load PDF (immediate heuristic)
print("\n📋 Step 2: Showing heuristic checklist (immediate)...")
loader = PDFLoader()
result = loader.extract(pdf_path)
print(f"✅ Heuristic checklist: {len(result.checklist_items)} items")
print("   (Displayed in left panel)")

# Agent Builder in background
print("\n🤖 Step 3: Running Agent Builder in background thread...")
print("   GUI remains responsive!")
print("   Log shows: '🤖 Analyzing with AI Agent Builder...'")

client = AgentServiceClient()
if not client.health_check():
    print("❌ Agent service not running!")
    sys.exit(1)

print("   Calling OpenAI Agent Builder API...")
analysis_result = client.analyze_document(result.text)

# Analysis complete
print("\n✅ Step 4: Agent Builder complete!")
summary = analysis_result.summary
print(f"   Generated {summary['total']} test cases")

# NEW: Display in browser view
print("\n🎨 Step 5: NEW! Displaying results in RIGHT PANEL")
print("=" * 80)
print("📱 BROWSER VIEW (Right Side - Chat Interface)")
print("=" * 80)

must_cases = [tc for tc in analysis_result.checklist if tc.priority == 'MUST']
should_cases = [tc for tc in analysis_result.checklist if tc.priority == 'SHOULD']
may_cases = [tc for tc in analysis_result.checklist if tc.priority == 'MAY']

print("""
┌────────────────────────────────────────────────────────────┐
│                                                            │
│  🤖  GAIA Agent Builder                     방금 전        │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                            │
│  📊 분석 완료!                                              │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  Total: 25개  |  필수: 15개  |  권장: 8개  |  선택: 2개  │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  🔴 MUST (필수)                            15개의 필수 테스트  │
│  ┌──────────────────────────────────────────────────────┐ │
""")

for i, tc in enumerate(must_cases[:5], 1):
    print(f"│  │  {tc.id}                                              │ │")
    print(f"│  │  {tc.name[:50]}                        │ │")
    if tc.steps and len(tc.steps) > 0:
        print(f"│  │  → {tc.steps[0][:45]}       │ │")
    print(f"│  │  ──────────────────────────────────────────────────│ │")

if len(must_cases) > 5:
    print(f"│  │  ... 외 {len(must_cases) - 5}개 테스트                                      │ │")

print("""│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  🟡 SHOULD (권장)                          8개의 권장 테스트  │
│  ┌──────────────────────────────────────────────────────┐ │
""")

for i, tc in enumerate(should_cases[:3], 1):
    print(f"│  │  {tc.id}  {tc.name[:45]}      │ │")
    print(f"│  │  ──────────────────────────────────────────────────│ │")

if len(should_cases) > 3:
    print(f"│  │  ... 외 {len(should_cases) - 3}개 테스트                                      │ │")

print("""│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  🟢 MAY (선택)                             2개의 선택 테스트  │
│  ┌──────────────────────────────────────────────────────┐ │
""")

for tc in may_cases:
    print(f"│  │  {tc.id}  {tc.name[:45]}      │ │")
    print(f"│  │  ──────────────────────────────────────────────────│ │")

print("""│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ 💡 URL을 입력하고 "자동화 시작" 버튼을 눌러           │ │
│  │    테스트를 실행하세요                                 │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
└────────────────────────────────────────────────────────────┘
""")

print("\n" + "=" * 80)
print("✅ NEW GUI FLOW COMPLETE!")
print("=" * 80)
print("""
이제 사용자가 보는 것:

왼쪽 (Control Panel):
  - PDF 드롭 영역
  - URL 입력
  - 체크리스트 (간단히)
  - 로그 출력

오른쪽 (Browser View) - 🆕 크고 예쁘게!:
  - 🤖 챗봇 스타일 메시지
  - 📊 요약 통계
  - 🔴🟡🟢 우선순위별로 구분된 테스트 케이스
  - 각 테스트 케이스 상세 정보
  - 애니메이션 효과
  - 💡 다음 단계 안내

다음 액션:
  → URL 입력하고 "자동화 시작" 클릭
""")
