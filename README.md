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
