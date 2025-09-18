import asyncio
import os
from typing import List, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="GAIA AI Server", description="AI-based QA automation test plan generator")

class TestStep(BaseModel):
    description: str
    action: str
    selector: str
    params: List[Any] = []

class Assertion(BaseModel):
    description: str
    selector: str
    condition: str

class TestScenario(BaseModel):
    id: str
    priority: str
    scenario: str
    steps: List[TestStep]
    assertion: Assertion

class DocumentRequest(BaseModel):
    document_content: str

async def call_gemini_api_mock(document_content: str) -> List[TestScenario]:
    await asyncio.sleep(3)
    
    mock_scenarios = [
        TestScenario(
            id="TC_001",
            priority="High",
            scenario="사용자 로그인 기능 테스트",
            steps=[
                TestStep(
                    description="로그인 페이지로 이동",
                    action="navigate",
                    selector="",
                    params=["https://example.com/login"]
                ),
                TestStep(
                    description="사용자명 입력",
                    action="type",
                    selector="#username",
                    params=["testuser"]
                ),
                TestStep(
                    description="비밀번호 입력",
                    action="type",
                    selector="#password",
                    params=["password123"]
                ),
                TestStep(
                    description="로그인 버튼 클릭",
                    action="click",
                    selector="#login-button",
                    params=[]
                )
            ],
            assertion=Assertion(
                description="대시보드 페이지가 로드되었는지 확인",
                selector="#dashboard",
                condition="visible"
            )
        ),
        TestScenario(
            id="TC_002",
            priority="Medium",
            scenario="회원가입 기능 테스트",
            steps=[
                TestStep(
                    description="회원가입 페이지로 이동",
                    action="navigate",
                    selector="",
                    params=["https://example.com/signup"]
                ),
                TestStep(
                    description="이메일 주소 입력",
                    action="type",
                    selector="#email",
                    params=["test@example.com"]
                ),
                TestStep(
                    description="사용자명 입력",
                    action="type",
                    selector="#username",
                    params=["newuser"]
                ),
                TestStep(
                    description="비밀번호 입력",
                    action="type",
                    selector="#password",
                    params=["newpassword123"]
                ),
                TestStep(
                    description="회원가입 버튼 클릭",
                    action="click",
                    selector="#signup-button",
                    params=[]
                )
            ],
            assertion=Assertion(
                description="회원가입 완료 메시지가 표시되었는지 확인",
                selector="#success-message",
                condition="contains('회원가입이 완료되었습니다')"
            )
        ),
        TestScenario(
            id="TC_003",
            priority="Low",
            scenario="상품 검색 기능 테스트",
            steps=[
                TestStep(
                    description="메인 페이지로 이동",
                    action="navigate",
                    selector="",
                    params=["https://example.com"]
                ),
                TestStep(
                    description="검색어 입력",
                    action="type",
                    selector="#search-input",
                    params=["테스트 상품"]
                ),
                TestStep(
                    description="검색 버튼 클릭",
                    action="click",
                    selector="#search-button",
                    params=[]
                )
            ],
            assertion=Assertion(
                description="검색 결과가 표시되었는지 확인",
                selector="#search-results",
                condition="not_empty"
            )
        )
    ]
    
    return mock_scenarios

@app.get("/")
async def root():
    return {"message": "GAIA AI Server is running."}

@app.post("/generate-test-plan", response_model=List[TestScenario])
async def generate_test_plan(request: DocumentRequest):
    try:
        if not request.document_content.strip():
            raise HTTPException(status_code=400, detail="Document content cannot be empty")
        
        test_scenarios = await call_gemini_api_mock(request.document_content)
        return test_scenarios
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate test plan: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)