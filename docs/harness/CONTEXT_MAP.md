# Context Map

목적은 단순하다. 에이전트가 저장소 전체를 읽지 않고도, 작업에 필요한 가장 작은 문맥만 읽게 만드는 것이다.

## 4-Layer Harness Mapping

### 1. Context

- [AGENTS.md](/Users/coldmans/Documents/GitHub/capston/AGENTS.md)
- [docs/harness/context_manifest.json](/Users/coldmans/Documents/GitHub/capston/docs/harness/context_manifest.json)
- [gaia/docs/AGENT_HARNESS_PLAYBOOK.md](/Users/coldmans/Documents/GitHub/capston/gaia/docs/AGENT_HARNESS_PLAYBOOK.md)
- [README.md](/Users/coldmans/Documents/GitHub/capston/README.md)

### 2. Tools

- OpenClaw runtime / dispatch
- CLI / terminal / chat hub entrypoints
- benchmark runner / harness graders
- artifacts / wrapper_trace / benchmark outputs

### 3. Checks

- `gaia/tests/unit`
- `scripts/run_goal_benchmark.py`
- harness graders under `gaia/harness/graders`
- [docs/harness/CHECKS.md](/Users/coldmans/Documents/GitHub/capston/docs/harness/CHECKS.md)

### 4. Garbage Collection

- [docs/harness/GARBAGE_COLLECTION.md](/Users/coldmans/Documents/GitHub/capston/docs/harness/GARBAGE_COLLECTION.md)
- `.gitignore`
- large dirs: `.venv`, `vendor/openclaw/node_modules`, `artifacts/`

## Area Packs

### `repo-entry`

- 언제 쓰나:
  - 어떤 작업이든 첫 진입 시
- 먼저 읽을 문서:
  - [AGENTS.md](/Users/coldmans/Documents/GitHub/capston/AGENTS.md)
  - [README.md](/Users/coldmans/Documents/GitHub/capston/README.md)
  - [docs/harness/CHECKS.md](/Users/coldmans/Documents/GitHub/capston/docs/harness/CHECKS.md)
  - [docs/harness/GARBAGE_COLLECTION.md](/Users/coldmans/Documents/GitHub/capston/docs/harness/GARBAGE_COLLECTION.md)

### `gaia-goal-driven`

- 언제 쓰나:
  - closer, verifier, WAIT completion, goal policy, trace loop
- 핵심 문서:
  - [gaia/docs/AGENT_HARNESS_PLAYBOOK.md](/Users/coldmans/Documents/GitHub/capston/gaia/docs/AGENT_HARNESS_PLAYBOOK.md)
  - [gaia/docs/KPI_BENCHMARK_PROTOCOL.md](/Users/coldmans/Documents/GitHub/capston/gaia/docs/KPI_BENCHMARK_PROTOCOL.md)
- 핵심 코드:
  - [agent.py](/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/agent.py)
  - [goal_completion_helpers.py](/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/goal_completion_helpers.py)
  - [goal_achievement_runtime.py](/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/goal_achievement_runtime.py)
  - [goal_verification_helpers.py](/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/goal_driven/goal_verification_helpers.py)

### `gaia-openclaw`

- 언제 쓰나:
  - browser runtime, profile/session, screenshots, dispatch, target routing
- 핵심 코드:
  - [embedded_openclaw_runtime.py](/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/embedded_openclaw_runtime.py)
  - [mcp_openclaw_dispatch_runtime.py](/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_openclaw_dispatch_runtime.py)
  - [mcp_local_dispatch_runtime.py](/Users/coldmans/Documents/GitHub/capston/gaia/src/phase4/mcp_local_dispatch_runtime.py)

### `benchmark-harness`

- 언제 쓰나:
  - benchmark timeout, suite 계약, expected_signals, grader 판정
- 핵심 문서:
  - [gaia/tests/scenarios/README.md](/Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/README.md)
  - [gaia/docs/KPI_BENCHMARK_PROTOCOL.md](/Users/coldmans/Documents/GitHub/capston/gaia/docs/KPI_BENCHMARK_PROTOCOL.md)
- 핵심 코드:
  - [run_goal_benchmark.py](/Users/coldmans/Documents/GitHub/capston/scripts/run_goal_benchmark.py)
  - [runner.py](/Users/coldmans/Documents/GitHub/capston/gaia/harness/runner.py)
  - [expected_signals.py](/Users/coldmans/Documents/GitHub/capston/gaia/harness/graders/expected_signals.py)
  - [membership.py](/Users/coldmans/Documents/GitHub/capston/gaia/harness/graders/membership.py)

### `runtime-entrypoints`

- 언제 쓰나:
  - CLI/UI/Telegram에서 어떤 backend, profile, screenshot, auth 경로를 타는지 볼 때
- 핵심 코드:
  - [cli.py](/Users/coldmans/Documents/GitHub/capston/gaia/cli.py)
  - [terminal.py](/Users/coldmans/Documents/GitHub/capston/gaia/terminal.py)
  - [chat_hub.py](/Users/coldmans/Documents/GitHub/capston/gaia/chat_hub.py)
  - [auth.py](/Users/coldmans/Documents/GitHub/capston/gaia/auth.py)

### `cleanup-gc`

- 언제 쓰나:
  - 디렉토리 비대화, 중복 fallback, stale artifacts, legacy path 제거
- 핵심 문서:
  - [docs/harness/GARBAGE_COLLECTION.md](/Users/coldmans/Documents/GitHub/capston/docs/harness/GARBAGE_COLLECTION.md)
- 우선 볼 경로:
  - [README.md](/Users/coldmans/Documents/GitHub/capston/README.md)
  - [.gitignore](/Users/coldmans/Documents/GitHub/capston/.gitignore)
  - [scripts/lint_harness_docs.py](/Users/coldmans/Documents/GitHub/capston/scripts/lint_harness_docs.py)
