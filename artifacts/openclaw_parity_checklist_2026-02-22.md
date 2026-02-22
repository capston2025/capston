# OpenClaw parity 점검 체크리스트 (2026-02-22)

비교 기준
- OpenClaw: `openclaw-official@54e5f80`
- Capston: `main` 기준 `gaia/src/phase4/mcp_host.py`

## 1) 주요 보정 로직 비교 결과

### A. strict locator 정책
- OpenClaw: strict 위반을 사용자 친화 에러로 정규화 (`src/browser/pw-tools-core.shared.ts:36-46`)
- Capston(수정 전): 일부 경로에서 `.first`로 첫 요소 강제 선택하여 ambiguous 은닉
  - `mcp_host.py:2945`, `4128`, `4133`, `4339-4343`, `4482`
- 조치: 모두 strict 단일 매치 또는 명시적 에러 반환으로 변경

### B. ref fallback 경로 일관성
- Capston(수정 전): `dom_ref`가 없을 때 selector_hint fallback 실패 사유를 버리고 `dom_ref_missing`으로 고정 반환
  - `mcp_host.py:2710-2714`
- 조치: fallback_error를 우선 반영하도록 수정 (실패 원인 보존)

### C. snapshot/aria 경로
- OpenClaw: `refs=aria`에서 selector/frame 제약 명시 (`src/browser/pw-tools-core.snapshot.ts:104-111`), `_snapshotForAI` 실패 시 role fallback (`src/browser/routes/agent.snapshot.ts:251-255`)
- Capston: aria snapshot 실패 시 broad fallback은 있으나 selector strict 일관성 일부 누락(이번 PR에서 보정)

### D. 에러 정규화 / timeout 회복
- OpenClaw: `toAIFriendlyError`, `normalizeTimeoutMs` 공통 적용 (`src/browser/pw-tools-core.shared.ts:32-69`)
- Capston: 경로별 하드코딩 timeout(예: 10000ms) 및 원문 예외 노출 구간 다수
- 상태: **미해결(다음 PR 권장)**

## 2) main 기준 문제점 체크리스트 (파일/라인 근거)

- [x] `.first` 잔존으로 ambiguous 은닉 (drag target)
  - `gaia/src/phase4/mcp_host.py:2945`
- [x] role/aria snapshot selector 경로 strict 미적용
  - `gaia/src/phase4/mcp_host.py:4128, 4133`
- [x] wait API selector 경로 strict 미적용
  - `gaia/src/phase4/mcp_host.py:4339-4343`
- [x] highlight selector 경로 strict 미적용
  - `gaia/src/phase4/mcp_host.py:4482`
- [x] dom_ref_missing 시 fallback 실패원인 유실
  - `gaia/src/phase4/mcp_host.py:2710-2714`
- [ ] 에러 정규화 공통 계층 부재 (`toAIFriendlyError` 유사)
  - `gaia/src/phase4/mcp_host.py` 전반(액션/대기/스냅샷 경로)
- [ ] timeout clamp 공통 계층 부재 (`normalizeTimeoutMs` 유사)
  - `gaia/src/phase4/mcp_host.py` 전반(고정 10000/60000 혼재)

## 3) 이번 PR 적용 변경 요약

1. `dragAndDrop` target 선택을 strict 단일 매치로 강제
2. role/aria snapshot selector 경로 strict 단일 매치로 강제
3. browser wait selector 경로 strict 적용 + ambiguous/not_found reason 코드 분기
4. highlight selector 경로 strict 적용
5. `dom_ref` 부재 fallback 오류 원인 전달 일관화

## 4) 검증
- `python3 -m py_compile gaia/src/phase4/mcp_host.py` 통과

## 5) 다음 PR 제안
- 공통 `normalize_timeout_ms()` 도입 후 모든 browser/ref action/wait에 적용
- 공통 `to_ai_friendly_error()` 도입 후 strict/visibility/pointer-intercept/timeout 에러 정규화
- `refs=aria + selector/frame` 제약과 메시지 정합성 OpenClaw와 1:1 정렬
