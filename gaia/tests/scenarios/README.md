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
- 구성: 한국 사용자에게 익숙한 공개 사이트 중심으로 포털/뉴스/커머스/공공데이터/개발자 문서/금융·게임 카테고리를 섞는다.
- 제약: 로그인, 결제, 장바구니 확정, 글쓰기, 댓글, 삭제, CAPTCHA 우회, 계정 정보 입력은 포함하지 않는다.
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
