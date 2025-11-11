# GAIA Vision-based Clicking 로드맵

## 🎯 목표
셀렉터 기반 → Vision 기반 좌표 클릭으로 전환하여 정확도 향상

---

## 📍 현재 상태 (2025-01-10)

### ✅ 완료된 것
- [x] 데이터 자동 수집 시스템 (`training_data_collector.py`)
- [x] 맥북 학습 스크립트 (`train_local.py`)
- [x] Google Colab 노트북 (`train_ui_detector.ipynb`)
- [x] YOLO 통합 코드 준비 (`ui_detector.py`)
- [x] 문서화 (`TRAIN_UI_DETECTOR.md`)

### ❌ 아직 안 한 것
- [ ] 학습 데이터 수집 (목표: 100-200개)
- [ ] 모델 학습
- [ ] 모델 테스트 및 검증
- [ ] intelligent_orchestrator.py 통합
- [ ] 실전 테스트

---

## 🗓️ 실행 계획

### Phase 1: 데이터 수집 (2-3일)

**목표:** 100-200개 라벨링 데이터 자동 수집

```bash
# 환경 변수 설정
export GAIA_COLLECT_TRAINING_DATA=true

# 평소처럼 테스트 실행
./scripts/run_mcp_host.sh  # 터미널 1
./scripts/run_gui.sh        # 터미널 2

# 진행상황 확인
ls artifacts/training_data/images/*.png | wc -l
```

**예상 수집량:**
- 10번 테스트 = 50-100개
- 50번 테스트 = 200-400개
- 100번 테스트 = 500-1000개

**완료 조건:**
- 최소 100개 이미지 (실용 가능)
- 권장 200개 이상 (좋은 정확도)

---

### Phase 2: 모델 학습 (1-2시간)

**데이터 충분히 모인 후 진행**

#### 옵션 A: 맥북 (추천)
```bash
# 필요한 패키지 설치
pip install ultralytics opencv-python

# 학습 시작
python train_local.py

# 예상 시간:
# M1: 1.5-2시간
# M2 Pro: 1-1.5시간
# M3 Max: 30분-1시간
```

#### 옵션 B: Google Colab
```bash
# 1. 데이터 압축
cd artifacts
zip -r training_data.zip training_data/

# 2. Google Drive 업로드

# 3. Colab에서 train_ui_detector.ipynb 실행
# 예상 시간: 1-1.5시간 (무료 T4 GPU)
```

**완료 조건:**
- `gaia/models/ui_detector.pt` 생성됨
- mAP50 > 0.6 (실용 가능)
- 추론 속도 < 100ms

---

### Phase 3: 모델 테스트 (30분)

**학습 완료 후 반드시 테스트**

```python
# 간단한 테스트 스크립트 작성
from gaia.src.phase4.ui_detector import UIDetector
import base64

detector = UIDetector()

# 테스트 이미지로 추론
with open("test_screenshot.png", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

# 모든 요소 탐지
elements = detector.detect_all_elements(img_b64)
print(f"Found {len(elements)} elements:")
for elem in elements:
    print(f"  {elem['type']}: {elem['confidence']:.2f} at {elem['center']}")

# 특정 요소 찾기
coords = detector.find_element_coordinates(img_b64, "로그인 버튼 클릭")
print(f"Login button at: {coords}")
```

**검증 항목:**
- [ ] 버튼 탐지 정확도
- [ ] 입력란 탐지 정확도
- [ ] 추론 속도 (< 100ms)
- [ ] False positive 비율 (< 10%)

---

### Phase 4: 코드 통합 (1시간)

**모델 검증 완료 후 진행**

#### 4.1. intelligent_orchestrator.py 수정

```python
# gaia/src/phase4/intelligent_orchestrator.py

# Import 추가
from gaia.src.phase4.training_data_collector import TrainingDataCollector
from gaia.src.phase4.ui_detector import get_ui_detector

# __init__에 추가
def __init__(self, ...):
    # ...

    # Training data collector
    self.collect_training_data = os.getenv("GAIA_COLLECT_TRAINING_DATA", "false").lower() == "true"
    self.training_collector = TrainingDataCollector() if self.collect_training_data else None

    # YOLO detector
    self.ui_detector = get_ui_detector()

# Fallback 로직에 YOLO 추가 (line ~1040)
# STEP 3: YOLO Vision-based Detection
if not found_by_text and self.ui_detector.enabled:
    self._log(f"    🎯 Trying YOLO vision detection...", progress_callback)

    yolo_coords = self.ui_detector.find_element_coordinates(
        screenshot_base64=screenshot,
        step_description=step.description
    )

    if yolo_coords:
        x, y = yolo_coords
        yolo_click_success = self._click_at_coordinates(x, y, current_url)
        if yolo_click_success:
            logs.append(f"  ✅ Action executed via YOLO")
            continue

# 성공한 액션에서 데이터 수집 (line ~1320)
if self.training_collector and llm_decision['selector']:
    self.training_collector.collect_sample(
        screenshot_base64=screenshot,
        selector=llm_decision['selector'],
        step_description=step.description,
        mcp_host_url=self.mcp_config.host_url,
        session_id=self.session_id
    )
```

---

### Phase 5: 실전 테스트 (1일)

**통합 완료 후 실전 테스트**

```bash
# 환경 변수 설정 (YOLO 활성화)
export GAIA_COLLECT_TRAINING_DATA=false  # 데이터 수집 끄기

# 테스트 실행
./scripts/run_gui.sh

# 다양한 사이트에서 테스트
python run_auto_test.py --url https://test-site1.com
python run_auto_test.py --url https://test-site2.com
python run_auto_test.py --url https://test-site3.com
```

**측정 지표:**
- 셀렉터 탐색 성공률 (목표: 60% → 85%+)
- LLM 호출 횟수 (목표: 100% → 30%)
- 평균 실행 속도 (목표: 8초 → 3초)
- 비용 절감 (목표: GPT-4V $10 → $0)

**A/B 테스트:**
```bash
# A: YOLO 비활성화 (기존 방식)
# gaia/models/ui_detector.pt 임시 이름 변경
mv gaia/models/ui_detector.pt gaia/models/ui_detector.pt.bak
# 테스트 실행 및 결과 기록

# B: YOLO 활성화 (새 방식)
mv gaia/models/ui_detector.pt.bak gaia/models/ui_detector.pt
# 테스트 실행 및 결과 비교
```

---

## 📊 예상 효과

| 지표 | 현재 | 목표 | 개선율 |
|------|------|------|--------|
| 셀렉터 탐색 성공률 | 60% | 85%+ | +42% |
| 동적 ID 대응 | ❌ | ✅ | - |
| iframe 대응 | ⚠️ | ✅ | - |
| LLM 호출 횟수 | 100% | 30% | -70% |
| 평균 실행 속도 | 8초 | 3초 | 3배 |
| 비용 (1000회) | $10 | $0 | -100% |

---

## 🐛 예상 문제 & 해결

### 문제 1: 데이터 부족
**증상:** 모델 정확도 < 50%
**해결:** 더 많은 테스트 실행 (목표: 200-500개)

### 문제 2: 느린 추론 속도
**증상:** YOLO 추론 > 200ms
**해결:**
- 더 작은 모델 사용 (yolov8n)
- Apple Silicon MPS 확인
- GPU 활성화 확인

### 문제 3: OCR 없이 텍스트 매칭 어려움
**증상:** "회원가입" 버튼 못 찾음
**해결:**
- Phase 6에서 OCR 통합
- 또는 LLM fallback으로 해결

### 문제 4: 학습 중 메모리 부족
**증상:** OOM 에러
**해결:**
- 배치 크기 감소 (16 → 8)
- 이미지 크기 감소 (640 → 416)
- Google Colab 사용

---

## 🚀 Phase 6 (선택): 추가 개선

**Phase 1-5 성공 후 고려**

### 옵션 A: OCR 통합
```python
# YOLO + OCR 조합
elements = yolo_detect_all(screenshot)
for elem in elements:
    text = pytesseract.image_to_string(crop_image(elem.bbox))
    if "회원가입" in text:
        return elem.center
```

### 옵션 B: 더 큰 모델
```bash
# YOLOv8m (medium) - 더 정확
python train_local.py  # MODEL_SIZE="yolov8m.pt"
```

### 옵션 C: 도메인별 모델
```bash
# 전자상거래 사이트 전용 모델
# 로그인 페이지 전용 모델
# 관리자 페이지 전용 모델
```

---

## 📝 메모

### 장점
- ✅ DOM에 의존하지 않음
- ✅ 동적 ID 문제 완전 해결
- ✅ iframe/Shadow DOM 무관
- ✅ 비용 절감 (LLM 호출 최소화)
- ✅ 속도 향상

### 단점
- ⚠️ 초기 학습 데이터 수집 필요
- ⚠️ 모델 학습 시간 (1-2시간)
- ⚠️ OCR 없으면 텍스트 매칭 어려움

### 리스크
- 학습 데이터 부족 시 정확도 낮음
- 새로운 UI 패턴에 대응 못 할 수 있음
- → 해결: 지속적 데이터 수집 및 재학습

---

## ✅ 체크리스트

### Phase 1: 데이터 수집
- [ ] GAIA_COLLECT_TRAINING_DATA=true 설정
- [ ] 100번 이상 테스트 실행
- [ ] 100개 이상 이미지 수집 확인
- [ ] data.yaml 생성 확인

### Phase 2: 모델 학습
- [ ] ultralytics 설치
- [ ] train_local.py 실행
- [ ] gaia/models/ui_detector.pt 생성 확인
- [ ] mAP50 > 0.6 확인

### Phase 3: 모델 테스트
- [ ] 간단한 추론 테스트
- [ ] 속도 벤치마크 (< 100ms)
- [ ] 정확도 확인

### Phase 4: 코드 통합
- [ ] intelligent_orchestrator.py 수정
- [ ] import 추가
- [ ] fallback 로직 추가
- [ ] 데이터 수집 코드 추가

### Phase 5: 실전 테스트
- [ ] 다양한 사이트 테스트
- [ ] 성능 지표 측정
- [ ] A/B 테스트
- [ ] 최종 검증

---

**마지막 업데이트:** 2025-01-10
**다음 단계:** Phase 1 시작 (데이터 수집)
