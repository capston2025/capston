"""
OpenAI Agent 서비스 클라이언트.
Node.js 에이전트 서비스와 통신하기 위한 파이썬 클라이언트입니다.
"""

import json
import requests
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class TestCase:
    """테스트 케이스 데이터 구조"""
    id: str
    name: str
    category: str
    priority: str
    precondition: str
    steps: List[str]
    expected_result: str


@dataclass
class AnalysisResult:
    """분석 결과 데이터 구조"""
    checklist: List[TestCase]
    summary: Dict[str, int]


class AgentServiceClient:
    """OpenAI Agent 서비스용 클라이언트"""

    def __init__(self, base_url: str = "http://localhost:3000"):
        """
        에이전트 서비스 클라이언트를 초기화합니다.

        매개변수:
            base_url: 에이전트 서비스의 기본 URL
        """
        self.base_url = base_url.rstrip("/")

    def health_check(self) -> bool:
        """
        에이전트 서비스 상태를 확인합니다.

        반환:
            서비스가 정상인 경우 True, 그렇지 않으면 False
        """
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200 and response.json().get("status") == "ok"
        except Exception as e:
            print(f"Health check failed: {e}")
            return False

    def analyze_document(self, text: str, timeout: int = 1500) -> AnalysisResult:
        """
        문서를 분석해 테스트 케이스를 생성합니다.

        매개변수:
            text: 분석할 문서 텍스트
            timeout: 요청 타임아웃(초). 기본값 1500초(= GPT-5 기준 약 25분)

        반환:
            체크리스트와 요약 정보를 포함한 AnalysisResult

        예외:
            requests.RequestException: 요청이 실패한 경우
            ValueError: 응답 형식이 올바르지 않은 경우
        """
        if not text or not text.strip():
            raise ValueError("Document text cannot be empty")

        # 요청 전송
        # 타임아웃=(connect_timeout, read_timeout)
        # connect_timeout: 서버 연결까지 대기 시간
        # read_timeout: 응답 읽기까지 대기 시간 (GPT-5는 길어질 수 있음)
        response = requests.post(
            f"{self.base_url}/api/analyze",
            json={"input_as_text": text},
            headers={"Content-Type": "application/json"},
            timeout=(10, timeout)  # (연결: 10초, 읽기: 1500초)
        )

        response.raise_for_status()

        # 응답 파싱
        result = response.json()

        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            raise ValueError(f"Analysis failed: {error_msg}")

        # output_text를 추출해 파싱 (이제 RT JSON 형식)
        output_text = result["data"]["output_text"]

        # 마크다운 코드 블록이 있다면 제거
        if output_text.startswith("```json"):
            output_text = output_text[7:]  # ```json 제거
        if output_text.startswith("```"):
            output_text = output_text[3:]  # ``` 제거
        if output_text.endswith("```"):
            output_text = output_text[:-3]  # 마지막 ``` 제거
        output_text = output_text.strip()

        try:
            output_json = json.loads(output_text)
        except json.JSONDecodeError as e:
            # 디버그: 원본 응답 출력
            print(f"DEBUG: Raw output_text: {repr(output_text[:500])}")
            # 전체 output_text를 파일에 저장
            import tempfile
            with open("/tmp/agent_output_error.txt", "w") as f:
                f.write(output_text)
            print(f"DEBUG: Full output saved to /tmp/agent_output_error.txt")
            raise ValueError(f"Failed to parse output JSON: {e}\nRaw output: {output_text[:200]}")

        # 디버그: 받은 JSON 구조 확인
        print(f"[DEBUG] Received JSON keys: {list(output_json.keys())}")
        print(f"[DEBUG] Has test_scenarios: {'test_scenarios' in output_json}")
        print(f"[DEBUG] Has checklist: {'checklist' in output_json}")

        # 🚨 NEW: Agent Service가 이미 완벽한 RT JSON을 반환하므로 그대로 사용
        # RT JSON을 TC로 변환하지 않고 바로 반환
        if 'test_scenarios' in output_json:
            print(f"[DEBUG] RT JSON detected, returning as-is without TC conversion")
            # RT JSON을 그대로 반환 (AnalysisResult 대신 dict 반환)
            return output_json  # 이건 analyzer.py에서 처리

        # OLD: RT JSON 형식을 TC 형식으로 변환 (하위 호환성을 위해 유지)
        # RT JSON: { "profile": "realistic-test", "url": "...", "test_scenarios": [...] }
        # TC 형식으로 변환: { "checklist": [...], "summary": {...} }

        test_scenarios = output_json.get("test_scenarios", [])
        print(f"[DEBUG] Found {len(test_scenarios)} test scenarios")
        checklist = []

        for scenario in test_scenarios:
            # RT scenario를 TC로 변환
            # RT steps를 TC steps로 변환 (description 필드 사용)
            tc_steps = []
            for step in scenario.get("steps", []):
                # description 필드가 있으면 사용, 없으면 action 기반으로 생성
                description = step.get("description", "")
                if description:
                    tc_steps.append(description)
                else:
                    # fallback: description이 없는 경우 action 기반으로 생성
                    action = step.get("action", "")
                    if action == "goto":
                        params = step.get("params", [])
                        tc_steps.append(f"페이지 이동: {params[0] if params else ''}")
                    elif action == "wait":
                        params = step.get("params", [])
                        tc_steps.append(f"대기: {params[0] if params else '0'}ms")
                    elif "expect" in action.lower():
                        params = step.get("params", [])
                        tc_steps.append(f"검증: {params[0] if params else ''}")
                    elif action == "fill":
                        params = step.get("params", [])
                        tc_steps.append(f"입력: {params[0] if params else ''}")
                    elif action == "click":
                        tc_steps.append("클릭")
                    else:
                        tc_steps.append(action)

            # assertion도 description 우선 사용
            assertion_obj = scenario.get("assertion", {})
            if isinstance(assertion_obj, dict):
                expected_result = assertion_obj.get("description", "")
                if not expected_result:
                    # fallback: params에서 추출
                    params = assertion_obj.get("params", [])
                    expected_result = params[0] if params else ""
            else:
                expected_result = ""

            test_case = TestCase(
                id=scenario.get("id", ""),
                name=scenario.get("scenario", ""),
                category="ui",  # 기본값
                priority=scenario.get("priority", "SHOULD"),
                precondition="",  # RT에는 없음
                steps=tc_steps,
                expected_result=expected_result
            )
            checklist.append(test_case)

        summary = {
            "total": len(checklist),
            "must": sum(1 for tc in checklist if tc.priority == "MUST"),
            "should": sum(1 for tc in checklist if tc.priority == "SHOULD"),
            "may": sum(1 for tc in checklist if tc.priority == "MAY"),
        }

        print(f"[DEBUG] Converted {len(checklist)} test cases successfully")
        print(f"[DEBUG] Summary: {summary}")

        return AnalysisResult(
            checklist=checklist,
            summary=summary
        )


# 사용 예시
if __name__ == "__main__":
    client = AgentServiceClient()

    # 상태 확인
    if not client.health_check():
        print("❌ Agent service is not healthy")
        exit(1)

    print("✅ Agent service is healthy")

    # 샘플 문서 분석
    sample_doc = """
온라인 쇼핑몰 웹사이트 기획서

주요 기능:
1. 회원가입 및 로그인
2. 상품 검색
3. 장바구니 담기
4. 결제하기
"""

    print("\n🔍 Analyzing document...")
    result = client.analyze_document(sample_doc)

    print(f"\n📊 Summary:")
    print(f"   Total: {result.summary['total']}")
    print(f"   MUST: {result.summary['must']}")
    print(f"   SHOULD: {result.summary['should']}")
    print(f"   MAY: {result.summary['may']}")

    print(f"\n📋 Test Cases:")
    for tc in result.checklist:
        print(f"\n   [{tc.id}] {tc.name}")
        print(f"   Priority: {tc.priority}")
        print(f"   Category: {tc.category}")
        print(f"   Steps: {' → '.join(tc.steps)}")
