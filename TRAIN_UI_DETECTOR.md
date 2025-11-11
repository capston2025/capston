# UI Object Detection Model Training Guide

GAIA 테스트 시스템에서 자동으로 수집한 UI 요소 데이터를 활용하여 YOLO 객체 인식 모델을 학습하는 가이드입니다.

## 🎯 목표

셀렉터 기반 요소 탐색 → **Vision 기반 좌표 클릭**으로 전환하여:
- 셀렉터 탐색 성공률: 60% → **85%+**
- 동적 ID 문제 완전 해결
- LLM 호출 횟수: 100% → **30%**
- 실행 속도: **3배 향상**
- 비용: GPT-4V $10/1000회 → **$0**

---

## 📊 전체 프로세스

```
1. 데이터 수집 (자동)
   └─> 테스트 실행 중 성공한 클릭에서 자동 수집
   └─> artifacts/training_data/images/*.png
   └─> artifacts/training_data/labels/*.txt (YOLO format)

2. 모델 학습
   ├─> 맥북 (Apple Silicon): train_local.py 실행
   └─> Google Colab: train_ui_detector.ipynb 업로드

3. 모델 배포
   └─> gaia/models/ui_detector.pt 저장
   └─> 자동으로 GAIA에서 사용됨
```

---

## 🚀 Quick Start (맥북)

### Step 1: 데이터 수집 (자동)

```bash
# 환경 변수 설정
export GAIA_COLLECT_TRAINING_DATA=true

# 테스트 실행 (평소대로)
./scripts/run_mcp_host.sh  # 터미널 1
./scripts/run_gui.sh        # 터미널 2

# 또는 CLI로:
python run_auto_test.py --url https://test-sitev2.vercel.app --spec spec.pdf
```

**100번 테스트 실행 = 1,000-2,000개 라벨링 데이터 자동 수집!**

### Step 2: 데이터 확인

```bash
# 수집된 데이터 확인
ls artifacts/training_data/images/*.png | wc -l  # 이미지 개수
ls artifacts/training_data/labels/*.txt | wc -l  # 라벨 개수

# 최소 50개 이상 권장, 200개 이상이면 매우 좋음
```

### Step 3: 모델 학습 (맥북)

```bash
# 필요한 패키지 설치
pip install ultralytics opencv-python

# 학습 시작 (1-2시간)
python train_local.py
```

**맥북 Apple Silicon (M1/M2/M3) 성능:**
- M1: 1.5-2시간
- M2 Pro: 1-1.5시간
- M3 Max: 30분-1시간

### Step 4: 완료!

모델이 자동으로 `gaia/models/ui_detector.pt`에 저장됩니다.
다음 테스트부터 자동으로 사용됩니다!

---

## ☁️ Google Colab 사용 (선택)

맥북이 없거나 더 빠른 학습을 원하면:

### Step 1: 데이터 업로드

```bash
# 데이터 압축
cd artifacts
zip -r training_data.zip training_data/

# Google Drive에 업로드
```

### Step 2: Colab에서 학습

1. Google Colab 접속: https://colab.research.google.com
2. `train_ui_detector.ipynb` 업로드
3. 런타임 → GPU 선택 (무료 T4)
4. 셀 순서대로 실행

**학습 시간: 1-1.5시간 (무료 T4 GPU)**

### Step 3: 모델 다운로드

마지막 셀 실행 시 자동으로 `ui_detector.pt` 다운로드됩니다.

```bash
# 프로젝트 폴더로 복사
cp ~/Downloads/ui_detector.pt gaia/models/
```

---

## 💡 고급 설정

### 더 큰 모델 사용 (정확도↑, 속도↓)

```python
# train_local.py 수정
MODEL_SIZE = "yolov8s.pt"  # small (더 정확)
# MODEL_SIZE = "yolov8m.pt"  # medium (가장 정확)
```

### 더 많은 epoch

```python
EPOCHS = 100  # 기본 50 → 100
```

### 배치 크기 조정

```python
BATCH_SIZE = 32  # 메모리 충분하면 증가
```

---

## 🎓 학습 데이터 이해하기

### YOLO 라벨 형식

```
# artifacts/training_data/labels/button_abc123.txt
0 0.352344 0.445312 0.078125 0.041667
```

형식: `<class_id> <x_center> <y_center> <width> <height>`

- 모든 좌표는 정규화 (0-1 범위)
- class_id: 0=button, 1=input, 2=link, ...

### 클래스 목록

```yaml
0: button      # 버튼
1: input       # 입력란
2: link        # 링크
3: checkbox    # 체크박스
4: radio       # 라디오 버튼
5: dropdown    # 드롭다운
6: text        # 텍스트
7: image       # 이미지
```

---

## 🐛 문제 해결

### "데이터가 충분하지 않습니다"

```bash
# 더 많은 테스트 실행
export GAIA_COLLECT_TRAINING_DATA=true
python run_auto_test.py --url [다른 사이트] --spec spec2.pdf

# 목표: 최소 100개, 권장 200-500개
```

### "MPS device not available"

```bash
# PyTorch 버전 확인
python -c "import torch; print(torch.__version__)"

# 2.0.0 이상이어야 함
pip install --upgrade torch torchvision
```

### "Out of memory"

```python
# train_local.py에서 배치 크기 감소
BATCH_SIZE = 8  # 기본 16 → 8
```

### 맥북이 너무 뜨거워요

```bash
# epochs 줄이기
EPOCHS = 30  # 기본 50 → 30

# 또는 Colab 사용
```

---

## 📈 학습 결과 해석

### mAP50 (주요 지표)

- **0.8 이상**: 🎉 Excellent! 바로 사용 가능
- **0.6-0.8**: ✅ Good! 실전 사용 가능
- **0.4-0.6**: ⚠️ Okay, 더 많은 데이터 필요
- **0.4 미만**: ❌ 데이터 부족 또는 학습 실패

### Precision vs Recall

- **Precision 높음**: 찾은 요소는 정확함
- **Recall 높음**: 모든 요소를 잘 찾음
- 둘 다 0.7 이상이면 이상적

---

## 🎯 다음 단계

### 1. 모델 테스트

```python
# test_ui_detector.py
from ultralytics import YOLO

model = YOLO("gaia/models/ui_detector.pt")
results = model.predict("screenshot.png")

for box in results[0].boxes:
    print(f"{model.names[int(box.cls)]}: {box.conf:.2f}")
```

### 2. GAIA에 통합

모델은 자동으로 사용됩니다! 별도 설정 불필요.

### 3. 지속적 개선

```bash
# 계속 데이터 수집
export GAIA_COLLECT_TRAINING_DATA=true

# 일주일에 한 번 재학습
python train_local.py

# 정확도가 지속적으로 향상됩니다!
```

---

## 💰 비용 분석

### 데이터 수집: $0
- 자동 수집 (테스트 중)

### 학습:
- **맥북**: $0.5 (전기료만)
- **Colab 무료**: $0
- **Colab Pro**: $9.99/월 (선택)

### 추론:
- **로컬 실행**: $0/무제한
- **vs GPT-4V**: $10-100/1000-10000회

### ROI (1년 기준):
```
GPT-4V 비용: $1,200/년
YOLO 비용: $0-2 (초기만)
절감: $1,198 (99.8%)
```

---

## 📚 참고 자료

- [YOLOv8 공식 문서](https://docs.ultralytics.com/)
- [YOLO Training Tips](https://github.com/ultralytics/ultralytics/wiki/Tips-for-Best-Training-Results)
- [Apple Silicon MPS 가이드](https://developer.apple.com/metal/pytorch/)

---

## 🤝 기여하기

더 좋은 학습 방법을 찾으셨나요?

1. Fork this repo
2. 개선 사항 적용
3. Pull Request 생성
4. 팀과 공유!

---

**Happy Training! 🚀**
