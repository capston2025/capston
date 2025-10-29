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

## 🚀 Smart Navigation (NEW)

### Intelligent Page-Element Memory
GAIA now remembers which UI elements exist on which pages and automatically navigates to find them.

**How it works:**
1. **Record**: As GAIA visits pages, it records all interactive elements (buttons, links) and their locations
2. **Smart Search**: When an element isn't found on the current page, GAIA searches its memory
3. **Auto Navigate**: If found on another page (e.g., home), GAIA automatically navigates there and clicks the element

**Example Flow:**
```
Step 1: Visit home → Record buttons: "기본 기능", "폼과 피드백", "인터랙션과 데이터"
Step 2: Click "기본 기능" → Navigate to #basics page
Step 3: Try to click "폼과 피드백"
  → Not found on #basics
  → 💡 Smart navigation: Found on home page
  → 🏠 Navigate to home
  → ✅ Click "폼과 피드백" successfully
```

**Benefits:**
- ✅ No need to manually specify "go back to home" steps in tests
- ✅ Works with hash-based SPAs (Figma Sites, React Router hash mode)
- ✅ Reduces scroll/vision fallback usage → faster execution
- ✅ More resilient to page structure changes

**Memory Optimization:**
To prevent excessive memory usage and maintain fast search:
- Only records first 4 pages visited (home + 3 others)
- Filters to navigation-like elements (buttons/links with short text or keywords)
- Typical memory: ~5-20 elements per page vs 30-50 without filtering
- Home page always prioritized in search

**Example Memory Footprint:**
```
Before optimization: 82 elements (5 + 33 + 29 + 15)
After optimization:  ~15-20 elements (navigation only)
Reduction: 75-80% memory savings
```

**Implementation:**
- Files: `gaia/src/phase4/intelligent_orchestrator.py:50-51, 89, 102, 547-590, 1167-1198`
- New methods: `_record_page_elements()`, `_find_element_on_other_pages()`
- Data structure: `page_element_map: Dict[url, Dict[text, selector]]`
- Optimization: Page limit (4) + keyword filtering + length limit (30 chars)

## 🚀 Selector Caching (NEW)

### Intelligent Learning from Past Executions
GAIA now remembers successful element selections and reuses them in future test runs, dramatically reducing execution time.

**How it works:**
1. **First Run**: LLM analyzes DOM and selects elements (3-5s per step)
2. **Cache**: Successful selectors are saved with metadata (timestamp, success count)
3. **Subsequent Runs**: Cached selectors bypass LLM entirely (0.5s per step)

**Speed Improvement:**
- First run: 7-9s per step (no change)
- Cached runs: 2-3s per step (**60-70% faster**)

**Cache Strategy:**
- Cache key: Hash of (step description + action + normalized URL)
- Confidence threshold: Only cache selectors with 2+ successful executions
- Auto-expiration: Entries older than 7 days are removed
- Persistence: Saved to `artifacts/cache/selector_cache.json`
- Fallback: If cached selector fails, falls back to LLM analysis

**Example Cache Entry:**
```json
{
  "a1b2c3d4...": {
    "selector": "[data-testid='start-forms']",
    "timestamp": 1730188800,
    "success_count": 3,
    "step_description": "시작하기-폼과 피드백 버튼 클릭으로 /forms 이동"
  }
}
```

**Benefits:**
- ✅ Dramatically faster repeated test runs (investor demos)
- ✅ Reduced OpenAI API costs (fewer LLM calls)
- ✅ Works across different test scenarios that share UI elements
- ✅ Smart fallback if UI changes

**Implementation:**
- Files: `gaia/src/phase4/intelligent_orchestrator.py:56-61, 460-478, 692-699, 1242-1323`
- Methods: `_load_cache()`, `_save_cache()`, `_get_cached_selector()`, `_update_cache()`
- Storage: JSON file with UTF-8 encoding for Korean text support

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

### Explicit Selector Fallback (NEW)
- **Smart Fallback for Invalid Selectors**: When test plans contain invalid selectors (e.g., `[data-testid='search-input']` that doesn't exist), GAIA now automatically falls back to LLM analysis instead of failing immediately
  - **Previous Behavior**: Explicit selector fails → entire test scenario fails
  - **New Behavior**: Explicit selector fails → LLM analyzes DOM and finds correct selector → continues execution
  - **Example**: Plan specifies `[data-testid='search-input']` but actual element is `input[placeholder="검색어를 입력하세요..."]` → LLM finds it automatically
  - File: `gaia/src/phase4/intelligent_orchestrator.py:438-450`
- **Screenshot Updates During Scroll**: Fixed GUI freezing during scroll attempts
  - Changed `send_to_gui=False` to `send_to_gui=True` so investors can see scroll progress
  - File: `gaia/src/phase4/intelligent_orchestrator.py:1062`
- **GOTO URL Sync (CRITICAL)**: Fixed URL not updating after `goto` action
  - **Root Cause**: After `goto #basics`, orchestrator kept using base URL without hash
  - **Impact**: Subsequent steps tried to find `#basics` elements on home page → failed
  - **Solution**: Changed from updating screenshot/DOM separately to using `_get_page_state()` which returns current URL
  - File: `gaia/src/phase4/intelligent_orchestrator.py:411`
- **Figma Sites Hash Navigation Fix (CRITICAL)**: Automatic fallback when direct hash navigation fails
  - **Root Cause**: Figma Sites don't load content when navigating directly to `#basics` URL - only button clicks trigger proper SPA routing
  - **Impact**: `goto https://site.com#basics` results in empty page (DOM < 15 elements)
  - **Solution**: Detect failed hash navigation (low DOM count) → Navigate to home → Use LLM to find and click navigation button → Content loads properly
  - **Example Flow**:
    ```
    goto #basics → DOM: 7 elements (failed)
    ⚠️ Hash navigation failed to load content
    💡 Navigate to home and click button
    🔘 Clicking: button:has-text("기본 기능")
    ✅ Content loaded via button click (DOM: 46 elements)
    ```
  - Files: `gaia/src/phase4/intelligent_orchestrator.py:414-450`
  - Wait time increased: 1s → 3s for SPA hydration

### Bug Fixes
- **Input Placeholder Selector (CRITICAL)**: Fixed invalid CSS selector generation for input fields
  - **Root Cause**: `getUniqueSelector()` was using className for inputs, generating invalid selectors like `input.file:text-foreground.placeholder:text-muted-foreground`
  - **Impact**: LLM rejected selectors for input fields with placeholders, forcing vision fallback
  - **Solution**: Added placeholder attribute check before className fallback for INPUT elements
  - Now generates: `input[placeholder="검색어를 입력하세요..."]` ✅
  - File: `gaia/src/phase4/mcp_host.py:231-234`
- **Hash Navigation Fix (CRITICAL)**: Fixed page reverting to homepage after hash navigation (#basics, #features, etc.)
  - **Root Cause**: After clicking buttons that trigger hash navigation, `current_url` variable remained stale with base URL
  - **Impact**: Subsequent actions passed stale URL to MCP, triggering unwanted re-navigation back to homepage
  - **Solution**: Update `current_url` with actual browser URL after all navigation actions (click, press, goto)
  - Files: `gaia/src/phase4/intelligent_orchestrator.py:697-703, 637-642`
  - Added debug logging in `mcp_host.py:437-440` to track URL comparison
- **Hash Navigation URL Sync (CRITICAL for Figma/SPA sites)**: Fixed DOM becoming 0 after hash navigation
  - **Root Cause**: Multiple issues prevented URL synchronization after hash navigation:
    1. `analyze_page` and `capture_screenshot` compared requested URL with stale `session.current_url` instead of actual browser URL
    2. `analyze_page` didn't return `url` field, so orchestrator couldn't track browser state
    3. Orchestrator's action check was case-sensitive (`"click"` vs `"CLICK"`), skipping URL updates
  - **Impact**: After clicking buttons with hash navigation (e.g., #basics), subsequent DOM analysis would either navigate away or fail to detect elements
  - **Solution**:
    - **MCP Host** (gaia/src/phase4/mcp_host.py:46-63, 390-421, 428-441):
      - Added `normalize_url()` helper function at module level for consistent URL comparison
      - Modified `analyze_page()` to compare with actual `page.url` instead of `session.current_url`
      - `analyze_page()` now returns both `url` and `dom_elements` keys in response
      - Modified `capture_screenshot()` to use same URL comparison logic
      - Both functions now sync `session.current_url = page.url` after any navigation
      - Added 3-second wait after navigation for React/Figma SPA hydration
    - **Orchestrator** (gaia/src/phase4/intelligent_orchestrator.py:654, 719):
      - Fixed case-sensitive action check: `llm_decision["action"].lower()` instead of `llm_decision["action"]`
      - Now correctly triggers URL updates after click/press/goto actions regardless of case
  - This fix enables testing of Figma Sites and other SPAs that use hash-based routing
- **Empty URL in Scroll Actions**: Fixed scroll logic sending empty URL strings to MCP
  - **Root Cause**: Scroll fallback logic was passing empty `url` parameter, causing MCP to navigate to blank page
  - **Impact**: DOM elements became 0 after scroll attempts, breaking element detection
  - **Solution**: Convert empty strings to `None` before sending to MCP
  - File: `gaia/src/phase4/intelligent_orchestrator.py:914`
- **DOM Recovery Logic (CRITICAL for Resilience)**: Added automatic recovery when DOM is empty
  - **Root Cause**: Various navigation issues could leave page in state with 0 DOM elements
  - **Impact**: Tests would fail immediately without attempting recovery
  - **Solution**: When DOM is 0, automatically navigate back to base URL and re-analyze
  - Provides flexible recovery mechanism as suggested - navigate home, re-extract DOM
  - Files: `gaia/src/phase4/intelligent_orchestrator.py:507-522, 1040-1096`
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
