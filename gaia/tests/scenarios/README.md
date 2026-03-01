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
