#!/usr/bin/env python3
"""
간단한 자동 테스트 스크립트
GUI 없이 테스트를 실행하고 결과를 출력합니다.
"""
import sys
import os

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'gaia'))

from src.phase4.intelligent_orchestrator import IntelligentOrchestrator
from src.utils.models import TestScenario, TestStep
from src.utils.config import CONFIG
import json

def load_test_file(filepath):
    """테스트 파일 로드"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # TestScenario 객체로 변환
    scenarios = []
    for scenario_data in data.get('test_scenarios', []):
        steps = [TestStep(**step) for step in scenario_data['steps']]
        scenario = TestScenario(
            id=scenario_data['id'],
            priority=scenario_data['priority'],
            scenario=scenario_data['scenario'],
            steps=steps,
            assertion=scenario_data.get('assertion')
        )
        scenarios.append(scenario)

    return data.get('url'), scenarios

def main():
    print("🧪 자동 테스트 시작...\n")

    # 테스트 파일 불러오기
    test_file = 'gaia/ui-components-test-sites.json'
    print(f"📄 테스트 파일 로드: {test_file}")

    try:
        url, scenarios = load_test_file(test_file)
        print(f"✅ {len(scenarios)}개 시나리오 로드 완료")
        print(f"🌐 테스트 URL: {url}\n")
    except Exception as e:
        print(f"❌ 테스트 파일 로드 실패: {e}")
        return 1

    # 오케스트레이터 초기화
    print("🤖 Intelligent Orchestrator 초기화...")
    orchestrator = IntelligentOrchestrator(
        mcp_config=CONFIG.mcp,
        session_id="auto-test-session"
    )

    # 빠른 검증을 위해 첫 번째 시나리오만 실행
    print(f"\n🚀 첫 번째 시나리오만 실행 (빠른 테스트):\n   {scenarios[0].scenario}\n")

    def progress_callback(msg):
        print(msg)

    try:
        results = orchestrator.execute_scenarios(
            url=url,
            scenarios=[scenarios[0]],  # 첫 번째 시나리오만 사용
            progress_callback=progress_callback
        )

        print("\n" + "="*60)
        print("📊 테스트 결과 요약")
        print("="*60)
        print(f"총 시나리오: {results['total']}")
        print(f"✅ 성공: {results['passed']}")
        print(f"❌ 실패: {results['failed']}")
        print(f"⏭️  스킵: {results['skipped']}")
        print("="*60)

        # 상세 결과 출력
        for scenario_result in results['scenarios']:
            print(f"\n[{scenario_result['id']}] {scenario_result.get('scenario', 'N/A')}")
            print(f"상태: {scenario_result['status']}")
            if scenario_result.get('logs'):
                print("로그:")
                for log in scenario_result['logs'][:10]:  # 첫 10개 로그
                    print(f"  {log}")

        return 0 if results['failed'] == 0 else 1

    except Exception as e:
        print(f"\n❌ 테스트 실행 중 에러: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    exit(main())
