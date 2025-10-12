# GAIA Implementation Guide

## Running the Desktop MVP

```bash
pip install -r gaia/requirements.txt
python -m gaia.main
```

## Module Overview

- `gaia/src/phase1`: PDF ingestion and Agent Builder-driven planning helpers.
- `gaia/src/phase4`: MCP client and agent orchestration.
- `gaia/src/utils/plan_repository.py`: Loader for precomputed Phase 1 plans and DOM snapshots.
- `gaia/src/tracker`: Checklist tracking helpers.
- `gaia/src/gui`: PySide6 GUI, controller, and worker glue code.
- `gaia/src/phase5`: Simple reporting helpers.

## Environment Variables

- `OPENAI_API_KEY`: API key for workflow execution.
- `GAIA_WORKFLOW_ID`: Agent Builder workflow ID (`wf_...`).
- `GAIA_WORKFLOW_VERSION`: Optional workflow version override (default `1`).
- `GAIA_LLM_MODEL`: Legacy GPT model override (kept for backward compatibility).
- `MCP_HOST_URL`: URL of the Playwright MCP host.

## Suggested Next Tasks

1. Replace the demo automation worker with real Playwright calls.
2. Implement Redis-backed checklist persistence (Phase 2 target).
3. Extend reporting to export Markdown/PDF summaries.
