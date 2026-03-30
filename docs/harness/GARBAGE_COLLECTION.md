# Garbage Collection

AI가 이 저장소에서 코드를 추가할수록 가장 먼저 망가지는 것은 “질서”다. 이 문서는 정기적으로 제거해야 하는 것들을 정의한다.

## 삭제 우선순위

1. 죽은 compatibility path
2. 한 번 실패했다고 조용히 다른 backend로 넘어가는 sticky fallback
3. 중복 helper / 중복 closer / 중복 validator
4. 오래된 benchmark 산출물
5. 캐시 / pycache / tmp / output 찌꺼기

## 기본 규칙

- 새 heuristic를 넣기 전, 기존 heuristic를 지울 수 있는지 먼저 본다
- 새 fallback를 넣기 전, fallback를 정말 유지해야 하는지 먼저 증명한다
- source of truth가 아닌 산출물은 git tracked 대상으로 유지하지 않는다

## 대용량 경로 점검

주기적으로 확인:

```bash
du -sh .venv vendor/openclaw/node_modules artifacts gaia/artifacts 2>/dev/null
```

### 흔한 비대화 원인

- `.venv`
- `vendor/openclaw/node_modules`
- `artifacts/benchmarks`
- `artifacts/wrapper_trace`
- `tmp`
- `output`

## Cleanup Trigger

아래 중 하나면 Cleanup 라운드를 돈다.

- 한 작업에서 300줄 이상 추가
- fallback / compatibility branch 추가
- 디렉토리 크기 급증
- 같은 영역에 임시 fix가 2번 이상 누적
- benchmark artifact가 원인 파악 후에도 계속 쌓임

## Cleanup 결과물

Cleanup 후에는 최소 아래를 남긴다.

- 무엇을 삭제했는지
- 왜 삭제 가능한지
- 남겨둔 호환성 경로가 무엇인지
- dir size 변화
- 회귀 테스트 결과
