# GAIA - Goal-oriented Autonomous Intelligence for Adaptive GUI Testing

GAIA is the 1학기 MVP for an autonomous QA assistant. The system ingests a planning PDF, produces GPT-driven UI automation plans, tracks checklist coverage in real time, and coordinates MCP-based browser exploration.

## 🏗️ Architecture Overview

```
gaia/
├── src/
│   ├── phase1/         # Spec PDF ingestion + GPT planning
│   ├── phase4/         # MCP client and agent orchestrator
│   ├── phase5/         # Simple reporting utilities
│   ├── tracker/        # Checklist state tracker
│   ├── gui/            # PySide6 desktop application
│   └── utils/          # Shared config + data models
├── tests/              # Pytest suites for core phases
├── artifacts/          # Specs, diagrams, demo assets
├── docs/               # Project context, progress, guides
├── requirements.txt    # Python dependencies for the MVP
└── main.py             # Desktop entry point
```

### Core Flow

1. **Phase 1 – Spec Analysis**
   - `PDFLoader` extracts raw text from planning PDFs.
   - `SpecAnalyzer` prompts GPT (`gpt-4o` by default) to build structured test scenarios.
2. **Tracker**
   - `ChecklistTracker` maintains the 25-item MVP checklist and exposes coverage metrics.
3. **Phase 4 – Agent + MCP**
   - `MCPClient` talks to the Playwright MCP host for DOM discovery.
   - `AgentOrchestrator` merges DOM insights with GPT output to refine plans and mark checklist hits.
4. **Phase 5 – Reporting**
   - `build_summary` produces coverage snapshots for demos.
5. **GUI**
   - PySide6 desktop app drives the workflow, executes automation workers, and visualises progress.

## 🚀 Getting Started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r gaia/requirements.txt
python -m gaia.main
```

Optional environment overrides:

- `OPENAI_API_KEY` – GPT API key (required for live planning).
- `GAIA_LLM_MODEL` – GPT model override (default: `gpt-4o`).
- `GAIA_WORKFLOW_ID` – Agent Builder workflow ID (e.g. `wf_68ea589f9a948190a518e9b2626ab1d5037b50134b0c56e7`).
- `GAIA_WORKFLOW_VERSION` – Workflow version to invoke (default: `1`).
- `MCP_HOST_URL` – Playwright MCP host (default: `http://localhost:8001`).

For MCP/Playwright execution:

```bash
playwright install chromium
```

### Quick Start Scripts

Once dependencies are installed and `.env` is filled, you can use helper scripts:

```bash
# terminal 1 (Playwright MCP host)
./scripts/run_mcp_host.sh

# terminal 2 (PySide6 GUI)
./scripts/run_gui.sh
```

GUI에서 과거 테스트 플랜을 재사용하려면 1단계 화면의 `이전 테스트 불러오기` 버튼을 눌러
`artifacts/plans/*.json` 파일을 선택하면 됩니다. PDF 분석 없이 바로 자동화를 시작할 수 있습니다.

## 🧪 Tests

```bash
pytest gaia/tests
```

## 🗺️ Documentation

- `gaia/docs/PROJECT_CONTEXT.md` – Full project charter.
- `gaia/docs/PROGRESS.md` – Iteration log.
- `gaia/docs/IMPLEMENTATION_GUIDE.md` – Environment, module, and next-step notes.

## 🤝 Team Workflow

- GPT is the default LLM for all automated planning in this repo.
- Update `gaia/docs/PROGRESS.md` after each milestone.
- Keep checklist coverage visible during demos using the GUI log output.

## 🔧 Recent Improvements (Issue #25)

### LLM Model Upgrade
- **GPT-5 Integration**: Upgraded from `gpt-5-mini` to `gpt-5` for better reasoning and decision-making
  - File: `gaia/src/phase4/llm_vision_client.py:26`
  - Added 60-second timeout to prevent hanging on API calls
  - Increased token limit from 1024 to 2048 for complex responses

### Auto-fix Logic Enhancement
- **Smart Fallback Skip**: Auto-fix now sets confidence to 95% and includes clear reasoning
  - When auto-fix finds exact text match, fallback mechanisms are skipped
  - Prevents unnecessary scroll and vision-based detection attempts
  - File: `gaia/src/phase4/intelligent_orchestrator.py:481`

### Enhanced Debugging
- **Page State Visibility**: Added current URL and DOM element count logging
  - Helps diagnose why elements aren't found
  - Shows reasoning for low confidence decisions
  - Added vision fallback reasoning output
  - Files: `gaia/src/phase4/intelligent_orchestrator.py:502-517`

### Real-time UI Feedback (Critical for Demos)
- **Immediate Progress Updates**: Added `QCoreApplication.processEvents()` for real-time UI responsiveness
  - Expanded `important_keywords` to include progress indicators like "🤖 Step", "📜 Scroll", "📸 Re-analyzing"
  - Forces immediate UI updates so investors can see system activity in real-time
  - File: `gaia/src/gui/main_window.py:722-742`

### Mouse Cursor Visibility (Critical for Demos)
- **SVG Cursor Overlay**: Added visible white arrow cursor at click positions
  - White arrow with black stroke and drop shadow (z-index 9999)
  - Always visible over screenshots for investor presentations
  - File: `gaia/src/gui/main_window.py:807-845`

### DOM Detection Improvements
- **Lenient Opacity Check**: Fixed `isVisible` function to allow fade-in animations
  - Changed from `style.opacity !== '0'` (string) to `parseFloat(style.opacity) > 0.1` (numeric)
  - Allows detection of React elements with animation effects
  - File: `gaia/src/phase4/mcp_host.py:182-191`
- **React SPA Wait Time**: Increased from 2 seconds to 3 seconds for hash navigation
  - Ensures DOM fully populates before analysis
  - File: `gaia/src/phase4/mcp_host.py:428`
- **DOM Coverage**: Increased element limit from 100 to 150 for better detection
  - File: `gaia/src/phase4/llm_vision_client.py:53`
- **Comprehensive ARIA Role Support**: Added all common interactive ARIA roles
  - Now includes: tab, menuitem, menuitemcheckbox, menuitemradio, option, radio, switch, treeitem, link
  - Fixes missing tab elements and other UI components
  - File: `gaia/src/phase4/mcp_host.py:254-270`
- **URL Comparison Fix**: Fixed hash navigation detection (#basics, #features, etc.)
  - Changed to compare with actual `page.url` instead of cached `session.current_url`
  - File: `gaia/src/phase4/mcp_host.py:416-417`

### Bug Fixes
- **MCPConfig Attribute**: Fixed `'IntelligentOrchestrator' object has no attribute 'mcp_url'`
  - Changed from `self.mcp_url` to `self.mcp_config.host_url`
  - File: `gaia/src/phase4/intelligent_orchestrator.py:988`
- **400 Bad Request Fix**: Fixed `_get_page_state()` using non-existent `get_dom_elements` action
  - Changed to correct `analyze_page` action
  - File: `gaia/src/phase4/intelligent_orchestrator.py:989-992`
- **waitForTimeout**: Added to actions not requiring selector
  - Files: `intelligent_orchestrator.py:334`, `mcp_host.py:888`
- **Scroll Direction**: Added support for "up", "down", "top", "bottom" strings
  - File: `gaia/src/phase4/mcp_host.py:446-460`
- **Empty URL Navigation**: Fixed bug causing unwanted page refreshes
  - File: `gaia/src/phase4/mcp_host.py:413`
