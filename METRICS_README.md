# GAIA 정량지표 측정 시스템

GAIA 자동화 테스트의 정량적 품질을 측정하는 시스템입니다.

## 📊 측정 지표

### 1. ICR (Intent Coverage Rate) - 의도 커버리지 비율
**목적**: 특정 기능에 대해 GAIA가 생성한 테스트가 얼마나 포괄적인지 측정

**계산 공식**:
```
ICR = (커버된 Test Cases 수) / (해당 Intent의 전체 Test Cases 수) × 100
```

**목표**:
- 기본 목표: ≥ 80%
- 스트레치 목표: ≥ 90%

**예시**:
- Feature Query: "로그인"
- Ground Truth: 로그인 intent에 6개의 test cases 존재
- GAIA 생성: 10개의 test scenarios (일부는 회원가입 포함)
- 매칭 결과: 6개 중 3개의 test cases 커버
- **ICR: 50.00%**

### 2. ER (Error Rate) - 오류율
**목적**: 테스트 품질 측정 (미탐지 버그 + 잘못된 실패)

**계산 공식**:
```
ER = (미탐지된 시드 버그 + False Positives) / (전체 시드 버그 + 통과해야 할 TC) × 100
```

**목표**: ≤ 20%

**구성 요소**:
- **미탐지된 시드 버그**: audit.json에 정의된 20개 버그 중 테스트가 발견하지 못한 버그 수
- **False Positives**: 실제로는 정상인데 실패로 판정된 테스트 수

---

## 📁 필수 파일

### 1. ground_truth.json
모든 테스트 가능한 intent와 세부 test cases를 정의한 파일

**구조**:
```json
{
  "version": "2.0",
  "description": "Test Site with UI Elements - Ground Truth Table",
  "total_intents": 44,
  "total_test_cases": 334,
  "intents": [
    {
      "id": "intent_001",
      "name_ko": "로그인",
      "category": "auth",
      "test_cases": [
        "정상 로그인 (이메일 + 비밀번호 입력)",
        "로그인 실패 - 비밀번호 미입력",
        "로그인 실패 - 이메일 미입력",
        "로그인 성공 후 사용자 정보 확인",
        "로그인 성공 토스트 메시지 확인",
        "로그인 후 폼 초기화 확인"
      ]
    }
  ]
}
```

**현재 상태**:
- 44개 intents
- 334개 test cases
- 카테고리: auth, search, cart, navigation, selection, media, form, interaction, feedback, data, special

### 2. audit.json
테스트 사이트에 심어진 20개의 버그를 정의한 파일

**구조**:
```json
{
  "description": "Test Site with UI Elements - Seeded Bug Table",
  "version": "1.0",
  "total_bugs": 20,
  "seeded_bugs": [
    {
      "bug_id": "BUG-001",
      "category": "기본 기능",
      "description": "단축키 반응 없음",
      "severity": "medium",
      "seeded": true,
      "detection_method": "단축키 입력 후, 예상된 동작이 없음",
      "expected_behavior": "Ctrl/Cmd + K, Ctrl/Cmd + / 등의 단축키 동작",
      "actual_behavior": "단축키를 눌러도 아무 반응 없음"
    }
  ]
}
```

**버그 분류**:
- 기본 기능: 4개
- 폼 & 피드백: 5개
- 인터랙션 & 데이터: 4개
- 검색 기능: 1개
- 모달 다이얼로그: 1개
- 공유하기: 2개
- 사용자 인증: 1개
- 범위 슬라이더: 1개
- 특별 기능: 1개

---

## 🚀 사용 방법

### 기본 사용법

```bash
python measure_metrics.py \
  --plan artifacts/plans/test-sitev2_vercel_app_xxx_plan.json \
  --log /path/to/execution.log \
  --ground-truth ground_truth.json \
  --audit audit.json \
  --output metrics_result.json
```

### 특정 기능만 측정

```bash
python measure_metrics.py \
  --plan artifacts/plans/test-sitev2_vercel_app_xxx_plan.json \
  --log /path/to/execution.log \
  --feature "로그인" \
  --output metrics_login.json
```

### 매개변수 설명

| 매개변수 | 필수 | 설명 |
|---------|------|------|
| `--plan` | ✅ | GAIA가 생성한 플랜 JSON 파일 경로 |
| `--log` | ✅ | 테스트 실행 로그 파일 경로 |
| `--ground-truth` | ❌ | Ground truth JSON 파일 (기본값: ground_truth.json) |
| `--audit` | ❌ | Audit JSON 파일 (기본값: audit.json) |
| `--feature` | ❌ | 특정 기능만 측정 (예: "로그인", "장바구니") |
| `--output` | ❌ | 결과 저장 파일명 (기본값: metrics_result.json) |

---

## 📋 출력 형식

### 콘솔 출력 예시

```
============================================================
🎯 GAIA 정량지표 측정 시작
============================================================
플랜 파일: artifacts/plans/test-sitev2_vercel_app_b7a49931befc_plan.json
로그 파일: /tmp/execution.log
Ground Truth: ground_truth.json
Audit: audit.json
Target Feature: 로그인

============================================================
📊 정량지표 1: ICR (Intent Coverage Rate) 계산
============================================================
🎯 Feature Query: '로그인'
✅ 매칭된 Intent: '로그인' (유사도: 100.00%)
✅ Ground Truth 로드 완료: 1개 intent, 6개 test cases
✅ GAIA가 생성한 test scenarios: 10개

🔍 Test Case 매칭 중...
  ✓ '로그인 실패 - 비밀번호 미입력' → [로그인] '로그인 실패 - 비밀번호 미입력' (유사도: 100.00%)
  ✓ '로그인 성공' → [로그인] '로그인 성공 토스트 메시지 확인' (유사도: 52.17%)
  ✓ '로그인 실패 - 이메일 형식 오류' → [로그인] '로그인 실패 - 이메일 미입력' (유사도: 76.47%)
  ✗ '회원가입 성공' (매칭 실패, 최고 유사도: 25.00%)

============================================================
📈 ICR 계산 결과
============================================================
Target Feature: 로그인
Target Intents: 로그인
Ground Truth Test Cases 총 개수: 6
GAIA가 생성한 Test Scenarios: 10
커버된 Test Cases: 3
ICR: 50.00%
목표 달성 (≥80%): ❌ FAIL
스트레치 목표 (≥90%): ❌ FAIL
```

### JSON 출력 예시

```json
{
  "icr": {
    "feature_query": "로그인",
    "target_intents": ["로그인"],
    "total_ground_truth_test_cases": 6,
    "gaia_generated_test_cases": 10,
    "covered_test_cases_count": 3,
    "icr_percentage": 50.0,
    "target_80_passed": false,
    "stretch_90_passed": false,
    "matched_test_cases": [
      {
        "gaia_test_case": "로그인 실패 - 비밀번호 미입력",
        "intent": "로그인",
        "ground_truth_test_case": "로그인 실패 - 비밀번호 미입력",
        "similarity": 1.0
      }
    ],
    "unmatched_test_cases": [...]
  },
  "er": {
    "total_seeded": 20,
    "detected_bugs": 5,
    "missed_seeded": 15,
    "bad_test_fails": 2,
    "total_tests": 10,
    "failed_tests_count": 7,
    "er_percentage": 56.67,
    "target_20_passed": false,
    "detected_bug_details": [...],
    "missed_bug_details": [...]
  },
  "summary": {
    "icr_percentage": 50.0,
    "icr_target_passed": false,
    "icr_stretch_passed": false,
    "er_percentage": 56.67,
    "er_target_passed": false
  }
}
```

---

## 🔧 작동 원리

### ICR 계산 과정

1. **Ground Truth 로드**: `ground_truth.json`에서 모든 intents와 test cases 로드
2. **Feature 필터링** (옵션): `--feature` 매개변수가 있으면 해당 intent만 선택
3. **GAIA 시나리오 추출**: 플랜 JSON의 `test_scenarios[].scenario` 필드 추출
4. **유사도 매칭**:
   - 각 GAIA 시나리오를 ground truth의 모든 test cases와 비교
   - `SequenceMatcher`를 사용한 문자열 유사도 계산
   - 임계값 50% 이상이면 매칭 성공
5. **커버리지 계산**:
   - 중복 제거 (같은 GT test case에 여러 GAIA 시나리오가 매칭될 수 있음)
   - ICR = (고유한 커버된 test cases 수) / (전체 test cases 수) × 100

### ER 계산 과정

1. **Audit 로드**: `audit.json`에서 20개 시드 버그 정보 로드
2. **로그 파싱**:
   - 정규식으로 실패한 테스트 추출
   - 패턴: `[n/total] Testing: ... (Priority: ...)` + `"status": "failed"`
3. **버그 매칭**:
   - 각 실패한 테스트를 시드 버그 description과 비교
   - 유사도 40% 이상이면 "버그를 탐지한 것"으로 간주
4. **False Positive 계산**:
   - 실패했지만 시드 버그와 매칭되지 않은 테스트
   - = (전체 실패한 테스트 수) - (탐지된 시드 버그 수)
5. **ER 계산**:
   - ER = (미탐지 버그 + False Positive) / (시드 버그 + 통과할 TC) × 100

---

## ⚙️ 커스터마이징

### 유사도 임계값 조정

`measure_metrics.py` 파일에서 임계값을 조정할 수 있습니다:

```python
# ICR 매칭 임계값 (기본값: 0.5 = 50%)
def match_test_case_to_ground_truth(gaia_test_case: str, ground_truth_intents: List[dict], threshold: float = 0.5):
    ...

# ER 버그 매칭 임계값 (기본값: 0.4 = 40%)
if similarity(bug_desc, failed_test['name']) > 0.4:
    detected = True
```

### Ground Truth 확장

새로운 intent를 추가하려면 `ground_truth.json`에 추가:

```json
{
  "id": "intent_045",
  "name_ko": "새로운_기능",
  "category": "custom",
  "test_cases": [
    "테스트 케이스 1",
    "테스트 케이스 2",
    "테스트 케이스 3"
  ]
}
```

### Audit 확장

새로운 시드 버그를 추가하려면 `audit.json`에 추가:

```json
{
  "bug_id": "BUG-021",
  "category": "새_카테고리",
  "description": "버그 설명",
  "severity": "high|medium|low",
  "seeded": true,
  "detection_method": "어떻게 탐지할 수 있는지",
  "expected_behavior": "예상 동작",
  "actual_behavior": "실제 동작"
}
```

---

## 📝 주의사항

### ICR 측정 시
1. **Feature Query 사용 권장**: 특정 기능만 측정할 때 `--feature` 사용
2. **유사도 매칭의 한계**:
   - "로그인 성공"과 "로그인 성공 토스트 메시지 확인"이 52% 유사도로 매칭됨
   - 너무 세밀한 test case 정의는 매칭률을 낮출 수 있음
3. **중복 카운팅 방지**: 같은 GT test case에 여러 GAIA 시나리오가 매칭되어도 1번만 카운트

### ER 측정 시
1. **로그 파일 형식**:
   - 테스트 실행 로그가 필요 (플랜 생성 로그 아님)
   - `[n/total] Testing: ...` 형식 필요
   - `"status": "failed"` JSON 형식 필요
2. **False Positive의 의미**:
   - 실제로는 잘못된 테스트 설계일 수도 있음
   - 버그가 아닌데 실패하는 경우
3. **버그 매칭의 한계**:
   - 간단한 키워드 매칭 사용
   - 복잡한 버그는 탐지 못할 수 있음

---

## 🎯 개선 방향

### 현재 ICR이 낮은 이유
- Feature Query: "로그인 기능 테스트"로 테스트를 생성했지만 회원가입도 포함됨
- 해결방안: 더 구체적인 feature query 사용 또는 프롬프트 개선

### ER 측정 개선 방향
1. **더 정교한 버그 매칭**:
   - 단순 유사도 대신 의미론적 매칭 사용
   - 버그의 detection_method를 활용한 매칭
2. **로그 파싱 강화**:
   - 다양한 로그 형식 지원
   - 테스트 실패 원인 상세 분석
3. **False Positive 분류**:
   - 설계 오류 vs 실제 버그 구분
   - 자동 분류 로직 추가

---

## 📚 참고

### 관련 파일
- `measure_metrics.py`: 측정 스크립트
- `ground_truth.json`: Intent와 test cases 정의
- `audit.json`: 시드 버그 정의
- `artifacts/plans/*.json`: GAIA가 생성한 테스트 플랜

### 워크플로우
1. GAIA GUI에서 테스트 플랜 생성 → `artifacts/plans/*.json`
2. 테스트 실행 → 실행 로그 저장
3. `measure_metrics.py` 실행 → `metrics_result.json` 생성
4. 결과 분석 및 개선

### 목표 달성 체크리스트
- [ ] ICR ≥ 80% (스트레치: ≥ 90%)
- [ ] ER ≤ 20%
- [ ] 모든 20개 시드 버그 탐지
- [ ] False Positive 최소화 (0개 목표)
