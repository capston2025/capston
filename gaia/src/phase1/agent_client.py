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

        # output_text를 추출해 파싱
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
            raise ValueError(f"Failed to parse output JSON: {e}\nRaw output: {output_text[:200]}")

        # dataclass로 변환
        checklist = [
            TestCase(
                id=tc["id"],
                name=tc["name"],
                category=tc["category"],
                priority=tc["priority"],
                precondition=tc["precondition"],
                steps=tc["steps"],
                expected_result=tc["expected_result"]
            )
            for tc in output_json["checklist"]
        ]

        return AnalysisResult(
            checklist=checklist,
            summary=output_json["summary"]
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
