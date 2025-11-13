"""
정량지표 측정 스크립트
- ICR (Intent Coverage Rate): 의도 커버리지 비율
- ER (Error Rate): 오류율 (미탐지 버그 + 설계실패)
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple
from difflib import SequenceMatcher


def load_json(file_path: str) -> dict:
    """JSON 파일 로드"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def similarity(a: str, b: str) -> float:
    """두 문자열의 유사도 계산 (0.0 ~ 1.0)"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def extract_intents_from_plan(plan_file: str) -> Set[str]:
    """
    GAIA가 생성한 플랜 JSON에서 intent를 추출합니다.

    플랜 파일은 artifacts/plans/ 디렉토리에 저장됩니다.
    test_scenarios의 scenario 필드를 intent로 간주합니다.
    """
    plan = load_json(plan_file)

    intents = set()

    # RT JSON 형식
    if 'test_scenarios' in plan:
        for scenario in plan['test_scenarios']:
            intent = scenario.get('scenario', '').strip()
            if intent:
                intents.add(intent)

    return intents


def match_test_case_to_ground_truth(gaia_test_case: str, ground_truth_intents: List[dict], threshold: float = 0.5) -> Tuple[bool, str, str, float]:
    """
    GAIA가 생성한 test case를 ground_truth의 test_cases와 매칭합니다.

    Returns:
        (matched, matched_intent_name, matched_test_case, similarity_score)
    """
    best_match_intent = None
    best_match_test_case = None
    best_score = 0.0

    for gt_intent in ground_truth_intents:
        intent_name = gt_intent['name_ko']

        # 각 intent의 test_cases와 비교
        for test_case in gt_intent.get('test_cases', []):
            score = similarity(gaia_test_case, test_case)

            if score > best_score:
                best_score = score
                best_match_intent = intent_name
                best_match_test_case = test_case

    matched = best_score >= threshold
    return matched, best_match_intent if matched else "", best_match_test_case if matched else "", best_score


def calculate_icr(plan_file: str, ground_truth_file: str = "ground_truth.json", feature_query: str = None) -> Dict:
    """
    ICR (Intent Coverage Rate) 계산

    특정 feature_query가 주어진 경우:
    - 해당 intent의 test_cases 총 개수를 분모로 사용
    - GAIA가 생성한 test scenarios를 test_cases와 매칭
    - ICR = (매칭된 test cases) / (해당 intent의 총 test cases) * 100

    feature_query가 없는 경우:
    - 전체 test_cases 개수를 분모로 사용 (all intents)
    """
    print("\n" + "="*60)
    print("📊 정량지표 1: ICR (Intent Coverage Rate) 계산")
    print("="*60)

    # Ground truth 로드
    ground_truth = load_json(ground_truth_file)
    all_intents = ground_truth['intents']

    # Feature query로 필터링 (있는 경우)
    target_intents = all_intents
    if feature_query:
        print(f"🎯 Feature Query: '{feature_query}'")
        # feature_query와 가장 유사한 intent 찾기
        best_match_intent = None
        best_match_score = 0.0
        for intent in all_intents:
            score = similarity(feature_query, intent['name_ko'])
            if score > best_match_score:
                best_match_score = score
                best_match_intent = intent

        if best_match_score >= 0.4 and best_match_intent:
            target_intents = [best_match_intent]
            print(f"✅ 매칭된 Intent: '{best_match_intent['name_ko']}' (유사도: {best_match_score:.2%})")
        else:
            print(f"⚠️  매칭된 Intent 없음 (최고 유사도: {best_match_score:.2%}). 전체 intents로 측정합니다.")

    # 총 test cases 개수 계산
    total_test_cases = sum(len(intent.get('test_cases', [])) for intent in target_intents)
    print(f"✅ Ground Truth 로드 완료: {len(target_intents)}개 intent, {total_test_cases}개 test cases")

    # GAIA가 생성한 scenarios 추출
    gaia_test_cases = extract_intents_from_plan(plan_file)
    print(f"✅ GAIA가 생성한 test scenarios: {len(gaia_test_cases)}개")

    # 매칭
    matched_test_cases = []
    unmatched_test_cases = []
    covered_gt_test_cases = set()  # 커버된 ground truth test case 추적

    print("\n🔍 Test Case 매칭 중...")
    for gaia_tc in gaia_test_cases:
        matched, intent_name, gt_test_case, score = match_test_case_to_ground_truth(gaia_tc, target_intents)

        if matched:
            # 같은 ground truth test case에 여러 GAIA test가 매칭될 수 있으므로 set 사용
            covered_gt_test_cases.add(f"{intent_name}::{gt_test_case}")
            matched_test_cases.append({
                'gaia_test_case': gaia_tc,
                'intent': intent_name,
                'ground_truth_test_case': gt_test_case,
                'similarity': score
            })
            print(f"  ✓ '{gaia_tc}' → [{intent_name}] '{gt_test_case}' (유사도: {score:.2%})")
        else:
            unmatched_test_cases.append({
                'gaia_test_case': gaia_tc,
                'best_score': score
            })
            print(f"  ✗ '{gaia_tc}' (매칭 실패, 최고 유사도: {score:.2%})")

    # ICR 계산
    covered_count = len(covered_gt_test_cases)
    icr = (covered_count / total_test_cases) * 100 if total_test_cases > 0 else 0

    result = {
        'feature_query': feature_query,
        'target_intents': [i['name_ko'] for i in target_intents],
        'total_ground_truth_test_cases': total_test_cases,
        'gaia_generated_test_cases': len(gaia_test_cases),
        'covered_test_cases_count': covered_count,
        'icr_percentage': icr,
        'target_80_passed': icr >= 80,
        'stretch_90_passed': icr >= 90,
        'matched_test_cases': matched_test_cases,
        'unmatched_test_cases': unmatched_test_cases
    }

    print("\n" + "="*60)
    print("📈 ICR 계산 결과")
    print("="*60)
    if feature_query:
        print(f"Target Feature: {feature_query}")
        print(f"Target Intents: {', '.join([i['name_ko'] for i in target_intents])}")
    print(f"Ground Truth Test Cases 총 개수: {total_test_cases}")
    print(f"GAIA가 생성한 Test Scenarios: {len(gaia_test_cases)}")
    print(f"커버된 Test Cases: {covered_count}")
    print(f"ICR: {icr:.2f}%")
    print(f"목표 달성 (≥80%): {'✅ PASS' if result['target_80_passed'] else '❌ FAIL'}")
    print(f"스트레치 목표 (≥90%): {'✅ PASS' if result['stretch_90_passed'] else '❌ FAIL'}")

    return result


def extract_bugs_from_logs(log_file: str, audit_file: str = "audit.json") -> Dict:
    """
    실행 로그에서 버그 탐지 결과를 추출합니다.

    로그 파일에서:
    - 실패한 테스트 케이스 추출
    - 각 실패가 시드 버그와 관련있는지 판단
    """
    print("\n" + "="*60)
    print("📊 정량지표 2: ER (Error Rate) 계산")
    print("="*60)

    # Audit 로드
    audit = load_json(audit_file)
    seeded_bugs = audit['seeded_bugs']
    total_seeded = len(seeded_bugs)

    print(f"✅ Audit 로드 완료: {total_seeded}개 시드 버그")

    # 로그 파일 읽기
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            log_content = f.read()
    except FileNotFoundError:
        print(f"❌ 로그 파일을 찾을 수 없습니다: {log_file}")
        return {
            'error': 'Log file not found',
            'total_seeded': total_seeded,
            'detected_bugs': 0,
            'missed_seeded': total_seeded,
            'bad_test_fails': 0,
            'er_percentage': 100.0
        }

    print(f"✅ 로그 파일 로드 완료: {len(log_content)} chars")

    # 실패한 테스트 추출
    failed_tests = []

    # 정규식 패턴: "Testing: ... (Priority: ...)" 다음에 "status": "failed"가 오는 경우
    test_pattern = r'\[(\d+)/\d+\] Testing: (.+?) \(Priority: (\w+)\)'
    status_pattern = r'"status"\s*:\s*"(failed|success|partial)"'

    tests = re.finditer(test_pattern, log_content)

    for test_match in tests:
        test_index = test_match.group(1)
        test_name = test_match.group(2)
        priority = test_match.group(3)

        # 이 테스트의 결과 찾기 (테스트 이름 이후의 status)
        start_pos = test_match.end()
        next_test_match = re.search(test_pattern, log_content[start_pos:])
        end_pos = next_test_match.start() + start_pos if next_test_match else len(log_content)

        test_section = log_content[start_pos:end_pos]
        status_match = re.search(status_pattern, test_section)

        if status_match and status_match.group(1) == 'failed':
            failed_tests.append({
                'index': test_index,
                'name': test_name,
                'priority': priority
            })

    print(f"✅ 실패한 테스트 추출: {len(failed_tests)}개")

    # 시드 버그 탐지 분석
    detected_bugs = []
    missed_seeded = []

    for bug in seeded_bugs:
        bug_id = bug['bug_id']
        bug_desc = bug['description']

        # 로그에서 이 버그와 관련된 실패 찾기
        detected = False

        for failed_test in failed_tests:
            # 간단한 키워드 매칭 (개선 가능)
            if similarity(bug_desc, failed_test['name']) > 0.4:
                detected = True
                detected_bugs.append({
                    'bug_id': bug_id,
                    'bug_description': bug_desc,
                    'detected_by_test': failed_test['name']
                })
                break

        if not detected:
            missed_seeded.append({
                'bug_id': bug_id,
                'bug_description': bug_desc
            })

    print(f"✅ 탐지된 시드 버그: {len(detected_bugs)}개")
    print(f"❌ 미탐지된 시드 버그: {len(missed_seeded)}개")

    # False positive (잘못된 실패) 추정
    # 실패했지만 시드 버그와 매칭 안 되는 케이스
    bad_test_fails = len(failed_tests) - len(detected_bugs)

    print(f"⚠️  False Positive (잘못된 실패): {bad_test_fails}개")

    # ER 계산
    # ER = (missed_seeded + bad_test_fails) / (total_seeded + should_pass) * 100
    # 여기서 should_pass는 정상적으로 통과해야 하는 TC 수
    # 간단화: should_pass = 전체 TC 수 - total_seeded (근사치)

    # 로그에서 전체 테스트 수 추출
    total_tests_match = re.search(r'\[(\d+)/(\d+)\]', log_content)
    total_tests = int(total_tests_match.group(2)) if total_tests_match else 10

    should_pass = total_tests - total_seeded if total_tests > total_seeded else total_tests

    er = ((len(missed_seeded) + bad_test_fails) / (total_seeded + should_pass)) * 100 if (total_seeded + should_pass) > 0 else 0

    result = {
        'total_seeded': total_seeded,
        'detected_bugs': len(detected_bugs),
        'missed_seeded': len(missed_seeded),
        'bad_test_fails': bad_test_fails,
        'total_tests': total_tests,
        'failed_tests_count': len(failed_tests),
        'er_percentage': er,
        'target_20_passed': er <= 20,
        'detected_bug_details': detected_bugs,
        'missed_bug_details': missed_seeded
    }

    print("\n" + "="*60)
    print("📈 ER 계산 결과")
    print("="*60)
    print(f"시드 버그 총 개수: {total_seeded}")
    print(f"탐지된 버그: {len(detected_bugs)}")
    print(f"미탐지된 버그: {len(missed_seeded)}")
    print(f"잘못된 실패 (False Positive): {bad_test_fails}")
    print(f"ER: {er:.2f}%")
    print(f"목표 달성 (≤20%): {'✅ PASS' if result['target_20_passed'] else '❌ FAIL'}")

    return result


def save_results(icr_result: Dict, er_result: Dict, output_file: str = "metrics_result.json"):
    """측정 결과를 JSON 파일로 저장"""
    result = {
        'icr': icr_result,
        'er': er_result,
        'summary': {
            'icr_percentage': icr_result['icr_percentage'],
            'icr_target_passed': icr_result['target_80_passed'],
            'icr_stretch_passed': icr_result['stretch_90_passed'],
            'er_percentage': er_result['er_percentage'],
            'er_target_passed': er_result['target_20_passed']
        }
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n💾 결과 저장 완료: {output_file}")


def main():
    """메인 실행 함수"""
    import argparse

    parser = argparse.ArgumentParser(description='GAIA 정량지표 측정')
    parser.add_argument('--plan', required=True, help='GAIA 플랜 JSON 파일 경로')
    parser.add_argument('--log', required=True, help='실행 로그 파일 경로')
    parser.add_argument('--ground-truth', default='ground_truth.json', help='Ground truth JSON 파일')
    parser.add_argument('--audit', default='audit.json', help='Audit JSON 파일')
    parser.add_argument('--output', default='metrics_result.json', help='결과 저장 파일명')
    parser.add_argument('--feature', default=None, help='특정 기능만 측정 (예: "로그인", "장바구니")')

    args = parser.parse_args()

    print("\n" + "="*60)
    print("🎯 GAIA 정량지표 측정 시작")
    print("="*60)
    print(f"플랜 파일: {args.plan}")
    print(f"로그 파일: {args.log}")
    print(f"Ground Truth: {args.ground_truth}")
    print(f"Audit: {args.audit}")
    if args.feature:
        print(f"Target Feature: {args.feature}")

    # ICR 계산
    icr_result = calculate_icr(args.plan, args.ground_truth, args.feature)

    # ER 계산
    er_result = extract_bugs_from_logs(args.log, args.audit)

    # 결과 저장
    save_results(icr_result, er_result, args.output)

    print("\n" + "="*60)
    print("✅ 측정 완료!")
    print("="*60)


if __name__ == "__main__":
    main()
