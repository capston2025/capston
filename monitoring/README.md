# GAIA 모니터링 스택

Prometheus + Pushgateway + Grafana + nginx 기반의 벤치마크 KPI 모니터링.

벤치마크 실행이 끝나면 결과가 자동으로 팀 서버에 업로드되고,
팀원 누구나 Grafana 대시보드에서 확인할 수 있습니다.

---

## 구성 파일

```
monitoring/
├── docker-compose.yml               # 전체 스택 정의
├── prometheus.yml                   # Prometheus 스크레이프 설정
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

연결 후에는 벤치마크 실행하면 **자동으로 업로드**됩니다.

---

## 이후 사용

```bash
# 벤치마크 실행 → 자동 push (별도 명령어 없음)
python scripts/run_goal_benchmark.py --suite ...

# 수동으로 push하고 싶을 때
python scripts/push_metrics.py           # 최근 결과 1개
python scripts/push_metrics.py --all     # 전체 결과

# 연결 상태 확인
python scripts/gaia_monitor_connect.py --status
```

---

## KPI 지표

| 메트릭 | 목표 | 설명 |
|--------|------|------|
| `scenario_success_rate`       | ≥ 70% | 시나리오 성공률 |
| `reproducibility_rate`        | ≥ 80% | 반복 실행 일관성 |
| `progress_stop_failure_rate`  | ≤ 10% | timeout/stuck 비율 |
| `self_recovery_rate`          | ≥ 60% | 자가 회복률 |
| `intervention_rate`           | ≤ 20% | 인간 개입 필요율 |
