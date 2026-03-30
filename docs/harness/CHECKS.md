# Checks

이 문서는 “작업 끝”을 사람 느낌이 아니라 재현 가능한 규칙으로 바꾸기 위한 체크 목록이다.

## 기본 체크

모든 코드 변경 후:

```bash
python scripts/lint_harness_docs.py
.venv/bin/python -m pytest gaia/tests/unit -q
```

## 영역별 체크

### goal-driven / closer / verifier

다음 파일을 건드렸다면:

- `gaia/src/phase4/goal_driven/**`

최소 체크:

```bash
.venv/bin/python -m pytest gaia/tests/unit/test_goal_achievement_runtime.py -q
.venv/bin/python -m pytest gaia/tests/unit/test_goal_verification_helpers.py -q
```

### OpenClaw / dispatch / screenshots

다음 파일을 건드렸다면:

- `gaia/src/phase4/embedded_openclaw_runtime.py`
- `gaia/src/phase4/mcp_openclaw_dispatch_runtime.py`
- `gaia/src/phase4/mcp_local_dispatch_runtime.py`
- `gaia/src/phase4/mcp_host_runtime.py`

최소 체크:

```bash
.venv/bin/python -m pytest gaia/tests/unit/test_embedded_openclaw_runtime.py -q
.venv/bin/python -m pytest gaia/tests/unit/test_mcp_openclaw_dispatch_runtime.py -q
.venv/bin/python -m pytest gaia/tests/unit/test_mcp_local_dispatch_runtime.py -q
```

### benchmark / suite 계약

다음 파일을 건드렸다면:

- `scripts/run_goal_benchmark.py`
- `gaia/harness/**`
- `gaia/tests/scenarios/**`

최소 체크:

```bash
.venv/bin/python -m pytest gaia/tests/unit/test_run_goal_benchmark_script.py -q
```

실제 계약 변경이면 benchmark 1개는 다시 태운다.

예시:

```bash
GAIA_BROWSER_BACKEND=openclaw GAIA_OPENCLAW_HEADLESS=1 GAIA_RAIL_ENABLED=0 \
python scripts/run_goal_benchmark.py \
  --suite artifacts/tmp/timeout420_reruns/INUU_001_HOME_LOGIN_VISIBLE.json \
  --provider openai \
  --model gpt-5.4 \
  --timeout-cap 420
```

## 검증 원칙

- unit test는 회귀 방지용이다
- benchmark는 계약 검증용이다
- trace / artifacts는 판정 근거다
- Verifier agent는 가능하면 독립 세션에서 돌린다
