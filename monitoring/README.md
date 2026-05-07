# GAIA 모니터링 스택

Prometheus + Pushgateway + Grafana + nginx 기반의 벤치마크 KPI 모니터링.

벤치마크 실행이 끝난 뒤 사용자가 명시적으로 업로드를 켜면
팀원 누구나 Grafana 대시보드에서 확인할 수 있습니다.

---

## 구성 파일

```
monitoring/
├── docker-compose.yml               # 전체 스택 정의
├── prometheus.yml                   # Prometheus 스크레이프 설정
├── shared/                          # 팀 공유 suite 저장소 (runtime 데이터, git 제외)
├── nginx/
│   ├── nginx.conf                   # 토큰 인증 프록시
│   └── tokens/.htpasswd             # 팀 토큰 (서버에서 자동 생성, git 제외)
└── grafana/
    ├── provisioning/                # 데이터소스 · 대시보드 자동 로드
    └── dashboards/gaia_kpi.json     # KPI 대시보드 프리셋
```

---

## 팀장 — 클라우드 VM 서버 세팅 (최초 1회)

### 1. 클라우드 VM 준비 (AWS EC2 t2.micro 추천 · 1년 무료)

보안 그룹 인바운드 규칙:

| 포트 | 허용 대상 | 용도 |
|------|-----------|------|
| 22   | 팀장 IP   | SSH  |
| 9091 | 팀원 IP   | 메트릭 push |
| 3000 | 팀원 IP   | Grafana 대시보드 |

### 2. VM에 Docker 설치
```bash
ssh -i your-key.pem ubuntu@<VM_IP>

curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
exit

# 재접속
ssh -i your-key.pem ubuntu@<VM_IP>
```

### 3. 모니터링 파일 복사
```bash
# 로컬에서 실행
scp -i your-key.pem -r capston/monitoring ubuntu@<VM_IP>:~/monitoring
```

### 4. 서버 세팅 스크립트 실행
```bash
# VM에서 실행
cd ~/monitoring
python3 ../scripts/gaia_monitor_setup.py
```

출력 예시:
```
✅ 세팅 완료! 팀원들에게 아래 명령어를 공유하세요.

  python scripts/gaia_monitor_connect.py \
      http://1.2.3.4:9091 \
      --token xK9mP2qRvL...

Grafana: http://1.2.3.4:3000  (admin / xxxxxxxx)
```

→ 이 명령어를 팀원들에게 공유하면 끝.

---

## 팀원 — 서버 연결 (최초 1회)

팀장에게 받은 명령어를 그대로 붙여넣기:

```bash
python scripts/gaia_monitor_connect.py \
    http://<VM_IP>:9091 \
    --token <토큰>
```

연결 후에는 벤치마크 실행 시 명시적으로 업로드를 켠 경우에만 전송됩니다.

---

## 이후 사용

```bash
# 벤치마크 실행 + 명시적 push
# KPI metrics와 sanitize된 suite JSON이 같이 공유됨
python scripts/run_goal_benchmark.py --suite ... --push-metrics

# 외부 공개 30개 사이트 pack 실행 + 통합 지표 push
PYTHONPATH=. GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python scripts/run_kpi_benchmark_pack.py \
  --suite-manifest gaia/tests/scenarios/external_public_manifest.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix external-public \
  --push-metrics

# 터미널 벤치 모드
python -m gaia.cli --terminal --push-metrics
# 또는 실행 직전 방향키 메뉴에서 "업로드하기" 선택

# 터미널 벤치 모드에서 지표 확인
# "Grafana 열기" 또는 "로컬 결과 보기" 중 선택

# suite JSON 공유
python scripts/sync_shared_suites.py push --suite gaia/tests/scenarios/custom_story_docs_suite.json --key story_docs
python scripts/sync_shared_suites.py pull --suite gaia/tests/scenarios/custom_story_docs_suite.json --key story_docs
python scripts/sync_shared_suites.py list

# 수동으로 push하고 싶을 때
python scripts/push_metrics.py           # 최근 결과 1개
python scripts/push_metrics.py --all     # 전체 결과

# 연결 상태 확인
python scripts/gaia_monitor_connect.py --status
```

GUI 벤치 관리 화면에서는 `모니터링 서버로 메트릭 업로드 (--push-metrics)` 체크박스를 켠 실행만 업로드됩니다.

`--push-metrics` 실행은 KPI metrics와 함께 원본 suite JSON을 sanitize해서 공유합니다.
`run_kpi_benchmark_pack.py --push-metrics`는 각 suite 결과에 더해 최종 pack artifact도 한 번 더 업로드해서 Grafana 상단의 30-site 통합 패널을 채웁니다.
터미널 벤치 모드에서는 모니터링 서버 연결이 이미 있으면 사이트 선택 직후 팀 공유 suite를 자동으로 한 번 가져와 로컬 suite와 병합합니다.
수동으로 다시 맞추고 싶을 때는 `팀 테스트 공유` 메뉴로 현재 사이트의 suite JSON을 별도로 올리거나 가져올 수 있습니다.
공유 시 `password`, `token`, `secret`, `api_key` 등 민감 key는 자동 제거됩니다.

---

## KPI 지표

| 메트릭 | 목표 | 설명 |
|--------|------|------|
| `scenario_success_rate`       | ≥ 70% | 시나리오 성공률 |
| `reproducibility_rate`        | ≥ 80% | 반복 실행 일관성 |
| `progress_stop_failure_rate`  | ≤ 10% | timeout/stuck 비율 |
| `self_recovery_rate`          | ≥ 60% | 자가 회복률 |
| `intervention_rate`           | ≤ 20% | 인간 개입 필요율 |

외부 공개 benchmark pack은 추가로 `gaia_external_pack_*`, `gaia_external_site_*`, `gaia_external_category_*`, `gaia_external_reason_code_count` 메트릭을 보냅니다.
Grafana 상단 overview에서 전체 사이트 수, 총 실행 수, 전체/primary 성공률, 평균 실행 시간, 사이트별 성공률, 카테고리별 성공률, 실패 reason code를 한 화면에 확인할 수 있습니다.
