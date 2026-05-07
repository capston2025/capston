# GAIA 회귀 시나리오 포맷

모든 시나리오는 아래 필드를 가져야 합니다.

- `id`: 시나리오 고유 ID
- `url`: 시작 URL
- `goal`: 자연어 목표
- `constraints`: 실행 제약(JSON object)
- `expected_signals`: 성공 신호 리스트(JSON array)
- `time_budget_sec`: 시간 예산(초)

예시:

```json
{
  "id": "SCN_001",
  "url": "https://example.com",
  "goal": "핵심 CTA 클릭 후 상태 변화 검증",
  "constraints": {
    "allow_navigation": true,
    "require_ref_only": true
  },
  "expected_signals": [
    "url_change",
    "state_change"
  ],
  "time_budget_sec": 300
}
```

## 외부 공개 사이트 벤치마크

- Manifest: `gaia/tests/scenarios/external_public_manifest.json`
- 범위: 외부 공개 사이트 30개, 사이트당 5개 시나리오, 총 150개 시나리오
- 구성: 한국 사용자에게 익숙한 공개 사이트 중심으로 포털/뉴스/커머스/공공데이터/개발자/금융·게임/채용/문화 카테고리를 섞는다.
- 제약: 로그인, 결제, 장바구니 확정, 글쓰기, 댓글, 삭제, CAPTCHA 우회, 계정 정보 입력은 포함하지 않는다.
- 제외 기준: CAPTCHA 또는 bot-wall 차단이 반복되는 사이트는 primary curated pack에서 빼고, 공개 읽기/탐색이 가능한 사이트로 대체한다.
- 특정 scenario URL에서만 CAPTCHA가 재현되면 같은 사이트의 안정적인 공개 read-only URL로 교체한다.
- 실행 중 새로 CAPTCHA/보안문자/보안 확인 화면이 나오면 일반 실패가 아니라 `BLOCKED_USER_ACTION` + `blocked_captcha`로 분리하고 `primary_success_rate`에서 제외한다.
- CAPTCHA 차단이 재현된 사이트는 다음 primary pack 개정에서 제거하고, 같은 규모를 유지하도록 안정적인 공개 read-only 사이트로 대체한다.
- 문장 기준: UI와 맞지 않는 일반 템플릿 문장 대신 사이트별 실제 공개 업무 흐름(검색 결과, 상세 정보, 가격/랭킹/지도/차트/필터 확인)을 사용한다.
- 지도/경로 시나리오는 hidden tab 클릭보다 공개 deep link나 검색 결과 URL로 먼저 안정화하고, 경로 화면의 출발/도착/이동수단/지도 정보 확인을 read-only 목표로 둔다.
- 실행 예:

```bash
PYTHONPATH=. GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python scripts/run_kpi_benchmark_pack.py \
  --suite-manifest gaia/tests/scenarios/external_public_manifest.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix external-public-20260507 \
  --push-metrics
```
