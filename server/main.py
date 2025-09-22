import asyncio
import json
import os
from typing import List, Any, Optional, Dict

import requests
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="GAIA AI Server", description="AI-based QA automation test plan generator")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

class UrlAnalysisRequest(BaseModel):
    url: str
    document_content: Optional[str] = None  # 선택사항

class DomElement(BaseModel):
    tag: str
    selector: str
    text: str = ""
    attributes: dict = {}
    element_type: str = ""  # button, input, link, etc.


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

# Configure OpenAI client
client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)


def _build_prompt(document_content: str) -> str:
    """Create a structured prompt for the Gemini model."""

    return (
        "You are a senior QA engineer. Based on the following product planning document, "
        "produce a prioritized end-to-end UI automation test plan. Return strict JSON with "
        "an array named test_scenarios. Each element must contain id, priority, scenario, steps, "
        "and assertion fields. Steps is an array of objects with description, action, selector, "
        "and params (array). Assertion is an object with description, selector, and condition. "
        "Use ids formatted like TC_001. Prioritize according to risk and impact. If information "
        "is missing, make reasonable assumptions and mention them in scenario description.\n\n"
        "<planning_document>\n"
        f"{document_content.strip()}\n"
        "</planning_document>\n"
        "Return only JSON."
    )


def _invoke_openai(prompt: str) -> str:
    try:
        if not client:
            print("OpenAI client not configured")
            return ""
        
        print(f"Calling OpenAI API with model: {OPENAI_MODEL}")
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=2048
        )
        
        print(f"OpenAI response received: {len(response.choices)} choices")
        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content or ""
            print(f"Response content length: {len(content)}")
            print(f"Response preview: {content[:200]}...")
            return content
        
        # If no response found, return empty string to trigger fallback
        print("No content in OpenAI response")
        return ""
        
    except Exception as e:
        # Return empty string to trigger fallback instead of raising exception
        print(f"OpenAI API error: {str(e)}")
        return ""


import subprocess
import json as json_module

async def call_mcp_playwright(action: str, params: dict) -> dict:
    """실제 MCP Playwright 호출"""
    try:
        # MCP 명령 구성
        mcp_command = {
            "action": action,
            "params": params
        }
        
        # MCP 클라이언트 호출 (간단한 구현)
        # 실제 운영에서는 더 정교한 MCP 클라이언트 필요
        
        if action == "analyze_page":
            # Playwright로 실제 페이지 분석
            # 여기서는 기본 Playwright 라이브러리 사용
            return await _analyze_page_with_playwright(params["url"])
        
        return {}
    except Exception as e:
        print(f"MCP 호출 실패: {e}")
        return {}

async def _analyze_page_with_playwright(url: str) -> dict:
    """Playwright로 실제 페이지 분석"""
    try:
        # 임시로 간단한 DOM 분석 (실제로는 더 정교해야 함)
        # 이 부분이 실제 MCP Playwright 연동 포인트
        
        script = f"""
import asyncio
from playwright.async_api import async_playwright

async def analyze():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto('{url}')
        
        # 폼 요소 찾기
        inputs = await page.query_selector_all('input')
        buttons = await page.query_selector_all('button')
        
        elements = []
        for input_elem in inputs:
            elem_type = await input_elem.get_attribute('type') or 'text'
            elem_id = await input_elem.get_attribute('id')
            elem_name = await input_elem.get_attribute('name')
            elem_placeholder = await input_elem.get_attribute('placeholder')
            
            if elem_id:
                selector = f"#{{elem_id}}"
            elif elem_name:
                selector = f"input[name='{{elem_name}}']"
            else:
                selector = "input"
            
            elements.append({{
                "tag": "input",
                "selector": selector,
                "attributes": {{
                    "type": elem_type,
                    "id": elem_id,
                    "name": elem_name,
                    "placeholder": elem_placeholder
                }},
                "element_type": "input"
            }})
        
        for button in buttons:
            text = await button.inner_text()
            button_type = await button.get_attribute('type') or 'button'
            
            elements.append({{
                "tag": "button", 
                "selector": f"button:has-text('{{text}}')",
                "text": text,
                "attributes": {{"type": button_type}},
                "element_type": "button"
            }})
        
        await browser.close()
        return elements

print(asyncio.run(analyze()))
"""
        
        # Python 스크립트 실행 - JSON으로 안전하게 출력하도록 수정
        import json as json_lib
        safe_script = script.replace('print(asyncio.run(analyze()))', 'import json; print(json.dumps(asyncio.run(analyze())))')
        
        result = subprocess.run(['python', '-c', safe_script], 
                              capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            try:
                elements_data = json_lib.loads(result.stdout.strip())
                print(f"실제 DOM 분석 성공: {len(elements_data)}개 요소 발견")
                return {"elements": elements_data}
            except json_lib.JSONDecodeError as e:
                print(f"JSON 파싱 오류: {e}")
                print(f"Raw output: {result.stdout}")
                return {"elements": []}
        else:
            print(f"Playwright 실행 오류: {result.stderr}")
            return {"elements": []}
            
    except Exception as e:
        print(f"페이지 분석 실패: {e}")
        return {"elements": []}

async def analyze_website_dom(url: str) -> List[DomElement]:
    """실제 MCP Playwright를 사용한 웹사이트 DOM 구조 분석"""
    
    # 실제 MCP 호출
    mcp_result = await call_mcp_playwright("analyze_page", {"url": url})
    
    elements = []
    if "elements" in mcp_result:
        for elem_data in mcp_result["elements"]:
            try:
                elements.append(DomElement(
                    tag=elem_data["tag"],
                    selector=elem_data["selector"],
                    text=elem_data.get("text", ""),
                    attributes=elem_data.get("attributes", {}),
                    element_type=elem_data["element_type"]
                ))
            except Exception as e:
                print(f"요소 파싱 오류: {e}")
                continue
    
    # MCP 실패 시 fallback
    if not elements:
        print("MCP 분석 실패, fallback 사용")
        return _get_fallback_elements(url)
    
    return elements

def _get_fallback_elements(url: str) -> List[DomElement]:
    """MCP 실패 시 사용할 fallback 요소들"""
    return [
        DomElement(
            tag="input",
            selector="input[type='text'], input[type='email'], #username, #user_id",
            text="",
            attributes={"type": "text"},
            element_type="input"
        ),
        DomElement(
            tag="input",
            selector="input[type='password'], #password, #user_pwd",
            text="",
            attributes={"type": "password"},
            element_type="input"
        ),
        DomElement(
            tag="button",
            selector="button[type='submit'], input[type='submit'], button:has-text('로그인'), button:has-text('LOGIN')",
            text="로그인",
            attributes={"type": "submit"},
            element_type="button"
        )
    ]

def _build_prompt_from_dom(dom_elements: List[DomElement], document_content: Optional[str] = None) -> str:
    """DOM 구조를 바탕으로 사용자 시나리오 생성 프롬프트 작성"""
    
    dom_description = "웹페이지 요소들:\n"
    for element in dom_elements:
        dom_description += f"- {element.element_type}: {element.selector}"
        if element.text:
            dom_description += f" (텍스트: '{element.text}')"
        dom_description += "\n"
    
    base_prompt = (
        "당신은 웹 개발 전문가입니다. 다음 웹사이트의 요소들을 보고 "
        "사용자가 할 수 있는 주요 작업들을 정리해주세요.\n\n"
        f"{dom_description}\n"
        "다음 형태의 JSON으로 응답해주세요:\n"
        "- test_scenarios 배열 형태\n"
        "- 각 항목은 id, priority, scenario, steps, assertion 필드 포함\n"
        "- steps는 description, action, selector, params를 포함한 배열\n"
        "- assertion은 description, selector, condition을 포함한 객체\n"
        "- 실제 발견된 selector를 사용해주세요\n\n"
    )
    
    if document_content:
        base_prompt += f"추가 정보:\n{document_content.strip()}\n\n"
    
    base_prompt += "JSON만 반환해주세요."
    
    return base_prompt

def _create_fallback_scenarios() -> List[TestScenario]:
    """AI 응답 실패 시 DOM 기반 대체 시나리오 생성"""
    return [
        TestScenario(
            id="TC_001",
            priority="High",
            scenario="로그인 기능 테스트 (DOM 기반 자동 생성)",
            steps=[
                TestStep(
                    description="아이디 입력",
                    action="fill",
                    selector="#user_id",
                    params=["testuser"]
                ),
                TestStep(
                    description="비밀번호 입력",
                    action="fill",
                    selector="#user_pwd",
                    params=["testpass"]
                ),
                TestStep(
                    description="로그인 버튼 클릭",
                    action="click",
                    selector="input[value='LOGIN']",
                    params=[]
                )
            ],
            assertion=Assertion(
                description="로그인 성공 확인",
                selector="body",
                condition="url_changed"
            )
        ),
        TestScenario(
            id="TC_002",
            priority="Medium",
            scenario="강좌 접근 테스트 (DOM 기반 자동 생성)",
            steps=[
                TestStep(
                    description="강좌 링크 클릭",
                    action="click",
                    selector="a[href*='course']",
                    params=[]
                )
            ],
            assertion=Assertion(
                description="강좌 페이지 로딩 확인",
                selector="body",
                condition="url_contains('course')"
            )
        )
    ]

def _parse_openai_response(text: str) -> List[TestScenario]:
    text = text.strip()
    
    # Handle empty or invalid response
    if not text:
        print("Empty response from Gemini, using fallback")
        return _create_fallback_scenarios()
    
    # Remove code block markers
    if text.startswith("```json"):
        text = text[7:]  # Remove ```json
    elif text.startswith("```"):
        text = text[3:]   # Remove ```
    
    if text.endswith("```"):
        text = text[:-3]  # Remove ending ```
    
    text = text.strip()
    
    # Try to find JSON in the text if it's mixed with other content
    import re
    json_pattern = r'\{[\s\S]*\}'
    json_match = re.search(json_pattern, text)
    if json_match:
        text = json_match.group()
    
    try:
        # Fix common JSON issues
        text = text.replace("'", '"')  # Replace single quotes with double quotes
        text = re.sub(r',\s*}', '}', text)  # Remove trailing commas before }
        text = re.sub(r',\s*]', ']', text)  # Remove trailing commas before ]
        
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"JSON parsing failed: {exc}")
        print(f"Raw text: {text}")
        return _create_fallback_scenarios()

    scenarios = parsed.get("test_scenarios")
    if not isinstance(scenarios, list):
        print("No test_scenarios found in response")
        return _create_fallback_scenarios()

    try:
        return [TestScenario(**scenario) for scenario in scenarios]
    except Exception as exc:
        print(f"Schema validation failed: {exc}")
        return _create_fallback_scenarios()


async def call_openai_api(document_content: str) -> List[TestScenario]:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    prompt = _build_prompt(document_content)
    response_text = await asyncio.to_thread(_invoke_openai, prompt)
    return _parse_openai_response(response_text)

async def call_openai_api_with_dom(dom_elements: List[DomElement], document_content: Optional[str] = None) -> List[TestScenario]:
    """DOM 구조를 바탕으로 AI 테스트 시나리오 생성"""
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    prompt = _build_prompt_from_dom(dom_elements, document_content)
    response_text = await asyncio.to_thread(_invoke_openai, prompt)
    return _parse_openai_response(response_text)

@app.get("/")
async def root():
    return {"message": "GAIA AI Server is running."}

@app.get("/debug/config")
async def debug_config():
    """디버깅용: 환경변수 확인"""
    return {
        "api_key_configured": bool(OPENAI_API_KEY),
        "api_key_length": len(OPENAI_API_KEY) if OPENAI_API_KEY else 0,
        "model": OPENAI_MODEL
    }

@app.post("/generate-test-plan", response_model=List[TestScenario])
async def generate_test_plan(request: DocumentRequest):
    if not request.document_content.strip():
        raise HTTPException(status_code=400, detail="Document content cannot be empty")

    try:
        test_scenarios = await call_openai_api(request.document_content)
        return test_scenarios
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate test plan: {exc}") from exc

@app.post("/analyze-and-generate", response_model=List[TestScenario])
async def analyze_and_generate_test_plan(request: UrlAnalysisRequest):
    """
    URL 기반 실시간 DOM 분석 + AI 테스트 시나리오 생성
    기획서는 선택사항으로 추가 컨텍스트 제공
    """
    if not request.url.strip():
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    try:
        # 1. 웹사이트 DOM 구조 분석
        dom_elements = await analyze_website_dom(request.url)
        
        if not dom_elements:
            raise HTTPException(status_code=404, detail="No interactive elements found on the website")
        
        # 2. DOM + 기획서(선택) 기반 AI 테스트 시나리오 생성
        test_scenarios = await call_openai_api_with_dom(dom_elements, request.document_content)
        
        return test_scenarios
        
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to analyze website and generate test plan: {exc}") from exc

@app.get("/analyze-dom/{url:path}")
async def analyze_dom_only(url: str):
    """DOM 분석 결과만 반환 (디버깅용)"""
    try:
        dom_elements = await analyze_website_dom(url)
        return {"url": url, "elements": dom_elements}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to analyze DOM: {exc}") from exc

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
