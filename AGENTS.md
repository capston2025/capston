# Repository Agent Entry

이 저장소는 크기가 크고 경로가 많다. 전체를 통째로 읽으면 토큰을 낭비하고, 잘못된 레이어를 건드리기 쉽다.

기본 원칙:

1. 먼저 가장 작은 context pack만 읽는다.
2. trace, failing file, symbol reference가 생길 때만 pack 밖으로 확장한다.
3. 구현 전에 Planner, 구현 후 Verifier, 주기적으로 Cleanup을 수행한다.
4. 새 분기나 fallback를 추가하기 전, 지울 수 있는 레거시 경로를 먼저 찾는다.

## Start Here

처음 시작할 때는 아래 순서를 기본으로 쓴다.

1. `python scripts/context_pack.py --area repo-entry`
2. 작업 영역에 맞는 pack 1개만 추가로 로드
3. 필요한 경우에만 pack 밖 파일을 연다

## Context Packs

- `repo-entry`
  - 저장소 전체 입구, 규칙, 체크, 정리 기준
- `gaia-goal-driven`
  - goal-driven runtime, closer, verification, completion
- `gaia-openclaw`
  - OpenClaw embedded runtime, dispatch, screenshot, host
- `benchmark-harness`
  - benchmark runner, graders, scenario/eval 계약
- `runtime-entrypoints`
  - CLI, terminal, chat hub, auth, session 진입점
- `cleanup-gc`
  - 큰 디렉토리, 슬롭 제거, fallback/legacy 제거 기준

전체 목록과 파일 매핑은 [docs/harness/CONTEXT_MAP.md](/Users/coldmans/Documents/GitHub/capston/docs/harness/CONTEXT_MAP.md) 와 [docs/harness/context_manifest.json](/Users/coldmans/Documents/GitHub/capston/docs/harness/context_manifest.json) 이 source of truth다.

## Working Contract

- Planner
  - 문제, 성공 조건, 수정 범위, 검증 방법을 먼저 고정
- Developer
  - 최소 수정, 가능하면 delete-first
- Verifier
  - 다른 관점에서 독립 검증
- Cleanup
  - 죽은 코드, 임시 fallback, stale artifact, 불필요한 대용량 경로 정리

세부 운영 규약은 [gaia/docs/AGENT_HARNESS_PLAYBOOK.md](/Users/coldmans/Documents/GitHub/capston/gaia/docs/AGENT_HARNESS_PLAYBOOK.md) 를 따른다.

## Checks

작업 후 기본 확인:

- `python scripts/lint_harness_docs.py`
- `.venv/bin/python -m pytest gaia/tests/unit -q`

영역별 추가 검증은 [docs/harness/CHECKS.md](/Users/coldmans/Documents/GitHub/capston/docs/harness/CHECKS.md) 를 따른다.

## Garbage Collection

다음은 source of truth가 아니다.

- `artifacts/`
- `tmp/`
- `output/`
- `__pycache__/`
- benchmark rerun 산출물

정리 규칙은 [docs/harness/GARBAGE_COLLECTION.md](/Users/coldmans/Documents/GitHub/capston/docs/harness/GARBAGE_COLLECTION.md) 를 따른다.
