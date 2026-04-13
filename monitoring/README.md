# GAIA 모니터링 스택

Prometheus + Pushgateway + Grafana를 사용한 벤치마크 KPI 모니터링.

## 로컬에서 시작하기

```bash
cd monitoring
docker compose up -d
```

Grafana: http://localhost:3000 (admin / changeme)

## 벤치마크 실행 후 메트릭 push

```bash
# 가장 최근 결과 push
python scripts/push_metrics.py

# 모든 기존 결과 한꺼번에 push (초기 마이그레이션)
python scripts/push_metrics.py --all
```

의존성: `pip install requests` (이미 설치되어 있음)

---

## 팀 공유 — 클라우드 VM 배포

### 1. 클라우드 VM 준비

AWS EC2 t2.micro (무료 티어) 또는 GCP e2-micro 추천.

보안 그룹 인바운드 규칙:
| 포트 | 대상 | 용도 |
|------|------|------|
| 22 | 팀원 IP만 | SSH |
| 3000 | 팀원 IP만 | Grafana |
| 9091 | 팀원 IP만 | Pushgateway |

### 2. VM에 Docker + Docker Compose 설치

```bash
# Ubuntu 기준
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

### 3. 이 monitoring 디렉토리를 VM에 복사

```bash
scp -r monitoring/ ubuntu@<VM_IP>:~/gaia-monitoring/
ssh ubuntu@<VM_IP>
cd gaia-monitoring
```

### 4. 비밀번호 설정 후 실행

```bash
GRAFANA_USER=admin GRAFANA_PASSWORD=팀원과_공유할_비밀번호 docker compose up -d
```

또는 `.env` 파일 생성:
```
GRAFANA_USER=admin
GRAFANA_PASSWORD=your_secure_password
```

### 5. 로컬에서 VM으로 메트릭 push

```bash
PUSHGATEWAY_URL=http://<VM_IP>:9091 python scripts/push_metrics.py
```

---

## 주요 KPI 지표

| 메트릭 | 목표 | 설명 |
|--------|------|------|
| `gaia_scenario_success_rate` | ≥ 70% | 시나리오 성공률 |
| `gaia_reproducibility_rate` | ≥ 80% | K회 반복 성공률 |
| `gaia_progress_stop_failure_rate` | ≤ 10% | timeout/stuck 비율 |
| `gaia_self_recovery_rate` | ≥ 60% | 자가 회복률 |
| `gaia_intervention_rate` | ≤ 20% | 인간 개입 필요율 |
