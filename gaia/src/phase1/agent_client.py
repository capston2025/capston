"""
OpenAI Agent Service Client
Python client for communicating with the Node.js agent service.
"""

import json
import requests
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class TestCase:
    """Test case data structure"""
    id: str
    name: str
    category: str
    priority: str
    precondition: str
    steps: List[str]
    expected_result: str


@dataclass
class AnalysisResult:
    """Analysis result data structure"""
    checklist: List[TestCase]
    summary: Dict[str, int]


class AgentServiceClient:
    """Client for OpenAI Agent Service"""

    def __init__(self, base_url: str = "http://localhost:3000"):
        """
        Initialize the agent service client.

        Args:
            base_url: Base URL of the agent service
        """
        self.base_url = base_url.rstrip("/")

    def health_check(self) -> bool:
        """
        Check if the agent service is healthy.

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200 and response.json().get("status") == "ok"
        except Exception as e:
            print(f"Health check failed: {e}")
            return False

    def analyze_document(self, text: str, timeout: int = 1500) -> AnalysisResult:
        """
        Analyze a document and generate test cases.

        Args:
            text: The document text to analyze
            timeout: Request timeout in seconds (default: 1500s = 25 minutes for GPT-5)

        Returns:
            AnalysisResult containing checklist and summary

        Raises:
            requests.RequestException: If the request fails
            ValueError: If the response format is invalid
        """
        if not text or not text.strip():
            raise ValueError("Document text cannot be empty")

        # Make request
        # timeout=(connect_timeout, read_timeout)
        # connect_timeout: 서버 연결까지 대기 시간
        # read_timeout: 응답 읽기까지 대기 시간 (GPT-5는 길어질 수 있음)
        response = requests.post(
            f"{self.base_url}/api/analyze",
            json={"input_as_text": text},
            headers={"Content-Type": "application/json"},
            timeout=(10, timeout)  # (connect: 10s, read: 1500s)
        )

        response.raise_for_status()

        # Parse response
        result = response.json()

        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            raise ValueError(f"Analysis failed: {error_msg}")

        # Extract and parse output_text
        output_text = result["data"]["output_text"]

        # Strip markdown code blocks if present
        if output_text.startswith("```json"):
            output_text = output_text[7:]  # Remove ```json
        if output_text.startswith("```"):
            output_text = output_text[3:]  # Remove ```
        if output_text.endswith("```"):
            output_text = output_text[:-3]  # Remove trailing ```
        output_text = output_text.strip()

        try:
            output_json = json.loads(output_text)
        except json.JSONDecodeError as e:
            # Debug: print raw response
            print(f"DEBUG: Raw output_text: {repr(output_text[:500])}")
            raise ValueError(f"Failed to parse output JSON: {e}\nRaw output: {output_text[:200]}")

        # Convert to dataclasses
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


# Example usage
if __name__ == "__main__":
    client = AgentServiceClient()

    # Check health
    if not client.health_check():
        print("❌ Agent service is not healthy")
        exit(1)

    print("✅ Agent service is healthy")

    # Analyze a sample document
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
