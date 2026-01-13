"""
백준(BOJ) 웹사이트 완전 자율 탐색 테스트
AI가 스스로 사이트를 돌아다니며 모든 요소를 테스트하고 버그를 찾음
"""
import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

# GAIA 모듈 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'gaia'))

from gaia.src.phase4.goal_driven.exploratory_agent import ExploratoryAgent
from gaia.src.phase4.goal_driven.exploratory_models import ExplorationConfig


def print_step_detail(step_number, decision, success, error=None):
    """스텝 상세 정보 출력"""
    print("\n" + "🔹" * 40)
    print(f"📍 Step {step_number} 상세 정보")
    print("🔹" * 40)

    if decision.selected_action:
        print(f"🎯 AI 선택한 액션: {decision.selected_action.action_type.upper()}")
        print(f"📝 대상: {decision.selected_action.description}")
        print(f"💭 AI 판단 이유: {decision.reasoning}")
        print(f"🎲 신뢰도: {decision.confidence * 100:.0f}%")
        if decision.expected_outcome:
            print(f"🔮 예상 결과: {decision.expected_outcome}")

        if decision.input_values:
            print(f"⌨️  입력값: {decision.input_values}")
    else:
        print(f"💭 AI 판단: {decision.reasoning}")

    print(f"✅ 실행 결과: {'성공' if success else '실패'}")
    if error:
        print(f"⚠️  에러: {error}")

    print("🔹" * 40)


def test_baekjoon_exploratory():
    """
    백준 메인 페이지 자율 탐색 테스트
    AI가 스스로 판단하며 사이트 탐색
    """

    # Gemini API 키
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        print("❌ GEMINI_API_KEY 환경 변수가 설정되지 않았습니다")
        return

    print("=" * 60)
    print("🤖 백준 완전 자율 탐색 모드")
    print("=" * 60)
    print("AI가 스스로 판단하며 사이트의 모든 요소를 테스트합니다.")
    print("중간중간 AI의 사고 과정을 실시간으로 보여드립니다.\n")

    # 탐색 설정
    config = ExplorationConfig(
        max_actions=15,  # 적당히 15개 액션으로 제한
        action_timeout=30,
        avoid_destructive=True,  # 삭제 같은 위험한 액션 피하기
    )

    # 실시간 로그 콜백
    def log_callback(message):
        # 중요한 로그만 강조 표시
        if "🤖 LLM 결정:" in message or "🎯 실행:" in message:
            print(f"\n{'='*60}")
            print(message)
            print("=" * 60)
        else:
            print(message)

    # 스크린샷 콜백 (저장)
    screenshots_saved = []
    def screenshot_callback(screenshot_b64):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 스크린샷 수 카운트만
        screenshots_saved.append(timestamp)

    # Agent 초기화
    agent = ExploratoryAgent(
        mcp_host_url="http://localhost:8001",
        gemini_api_key=gemini_api_key,
        session_id="baekjoon_explore",
        config=config,
        log_callback=log_callback,
        screenshot_callback=screenshot_callback,
    )

    print("🚀 탐색 시작: 백준 메인 페이지")
    print(f"   최대 액션: {config.max_actions}개")
    print(f"   타임아웃: {config.action_timeout}초")
    print()

    # 자율 탐색 시작
    result = agent.explore(start_url="https://www.acmicpc.net/")

    # 결과 요약
    print("\n\n" + "=" * 60)
    print("📊 최종 탐색 결과")
    print("=" * 60)
    print(f"총 액션 수: {result.total_actions}")
    print(f"방문한 페이지: {result.total_pages_visited}개")
    print(f"테스트한 요소: {result.total_elements_tested}개")
    print(f"커버리지: {result.get_coverage_percentage():.1f}%")
    print(f"소요 시간: {result.duration_seconds:.1f}초")
    print(f"종료 이유: {result.completion_reason}")

    # 발견한 이슈
    print(f"\n🚨 발견한 이슈: {len(result.issues_found)}개")
    if result.issues_found:
        for issue in result.issues_found:
            print(f"  [{issue.severity.upper()}] {issue.title}")
            print(f"      {issue.description[:100]}...")

    # 스텝별 상세 정보
    print("\n" + "=" * 60)
    print("📝 AI가 수행한 모든 액션 요약")
    print("=" * 60)

    for step in result.steps:
        status = "✅" if step.success else "❌"
        action_desc = "탐색 종료"

        if step.decision.selected_action:
            action = step.decision.selected_action
            action_desc = f"{action.action_type} - {action.description[:50]}"

        print(f"{status} Step {step.step_number}: {action_desc}")
        print(f"   💭 {step.decision.reasoning[:80]}...")

        if step.issues_found:
            print(f"   🚨 이슈 {len(step.issues_found)}개 발견!")

        if step.new_pages_found:
            print(f"   🆕 새 페이지 발견!")

        print()

    print("=" * 60)
    print(f"📸 캡처한 스크린샷: {len(screenshots_saved)}개")
    print("=" * 60)

    # 결과 JSON으로 저장
    result_file = f"artifacts/exploration_results/baekjoon_explore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs("artifacts/exploration_results", exist_ok=True)

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({
            "session_id": result.session_id,
            "start_url": result.start_url,
            "total_actions": result.total_actions,
            "total_pages_visited": result.total_pages_visited,
            "total_elements_tested": result.total_elements_tested,
            "coverage": result.coverage,
            "completion_reason": result.completion_reason,
            "duration_seconds": result.duration_seconds,
            "issues_found": [
                {
                    "id": issue.issue_id,
                    "type": issue.issue_type.value,
                    "severity": issue.severity,
                    "title": issue.title,
                    "description": issue.description,
                    "url": issue.url,
                }
                for issue in result.issues_found
            ],
            "steps": [
                {
                    "step_number": step.step_number,
                    "url": step.url,
                    "success": step.success,
                    "action": step.decision.selected_action.action_type if step.decision.selected_action else None,
                    "reasoning": step.decision.reasoning,
                    "issues_found": len(step.issues_found),
                    "duration_ms": step.duration_ms,
                }
                for step in result.steps
            ],
        }, f, ensure_ascii=False, indent=2)

    print(f"💾 결과 저장: {result_file}")

    return result


if __name__ == "__main__":
    test_baekjoon_exploratory()
