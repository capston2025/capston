import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Playwright
from typing import Dict, Any, Optional

app = FastAPI(title="MCP Host", description="Model Context Protocol Host for Browser Automation")

class McpRequest(BaseModel):
    action: str = Field(..., description="The action to perform, e.g., 'analyze_page'.")
    params: Dict[str, Any] = Field(default_factory=dict, description="Parameters for the action.")

# Global Playwright instance
playwright_instance: Optional[Playwright] = None

@app.on_event("startup")
async def startup_event():
    """Initializes the Playwright instance on server startup."""
    global playwright_instance
    print("Initializing Playwright...")
    playwright_instance = await async_playwright().start()
    print("Playwright initialized.")

@app.on_event("shutdown")
async def shutdown_event():
    """Stops the Playwright instance on server shutdown."""
    if playwright_instance:
        print("Stopping Playwright...")
        await playwright_instance.stop()
        print("Playwright stopped.")

async def analyze_page(url: str) -> Dict[str, Any]:
    """
    Analyzes a web page with Playwright to extract interactive elements.
    """
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    browser = await playwright_instance.chromium.launch(headless=True)
    page = await browser.new_page()
    try:
        await page.goto(url, timeout=30000)

        inputs = await page.query_selector_all('input')
        buttons = await page.query_selector_all('button')
        links = await page.query_selector_all('a')

        elements = []
        for input_elem in inputs:
            elem_type = await input_elem.get_attribute('type') or 'text'
            elem_id = await input_elem.get_attribute('id')
            elem_name = await input_elem.get_attribute('name')

            selector = f"input[type='{elem_type}']"
            if elem_id:
                selector = f"#{elem_id}"
            elif elem_name:
                selector = f"input[name='{elem_name}']"

            elements.append({
                "tag": "input",
                "selector": selector,
                "attributes": {
                    "type": elem_type,
                    "id": elem_id,
                    "name": elem_name,
                    "placeholder": await input_elem.get_attribute('placeholder') or "",
                },
                "element_type": "input"
            })

        for button in buttons:
            text = await button.inner_text()
            selector = f"button:has-text('{text}')"
            elements.append({
                "tag": "button",
                "selector": selector,
                "text": text,
                "attributes": {"type": await button.get_attribute('type') or 'button'},
                "element_type": "button"
            })

        for link in links:
            text = await link.inner_text()
            href = await link.get_attribute('href')
            if text and href and not href.startswith('#'):
                 elements.append({
                    "tag": "a",
                    "selector": f"a[href='{href}']",
                    "text": text,
                    "attributes": {"href": href},
                    "element_type": "link"
                })

        return {"elements": elements}
    except Exception as e:
        print(f"Error analyzing page {url}: {e}")
        return {"error": str(e)}
    finally:
        await browser.close()


@app.post("/execute")
async def execute_action(request: McpRequest):
    """
    Executes a browser automation action.
    """
    action = request.action
    params = request.params

    if action == "analyze_page":
        url = params.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="URL parameter is required for 'analyze_page' action.")

        result = await analyze_page(url)
        if "error" in result:
            raise HTTPException(status_code=500, detail=f"Failed to analyze page: {result['error']}")
        return result

    raise HTTPException(status_code=400, detail=f"Action '{action}' not supported.")

@app.get("/")
async def root():
    return {"message": "MCP Host is running."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)