import { Agent, AgentInputItem, Runner, withTrace } from "@openai/agents";
import { OpenAI } from "openai";
import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const openaiClient = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

const DEFAULT_REPO_OWNER = process.env.GAIA_REPO_OWNER ?? "capston2025";
const DEFAULT_REPO_NAME = process.env.GAIA_REPO_NAME ?? "TestSitev2";
const DEFAULT_MCP_DIR = process.env.GITHUB_MCP_SERVER_DIR ?? path.join(os.homedir(), "학습", "github-mcp-server");
const DEFAULT_MCP_BIN = process.env.GITHUB_MCP_SERVER_BIN ?? path.join(DEFAULT_MCP_DIR, "github-mcp-server");

const keywordExtractorSystemPrompt = `Extract up to 15 code-focused keywords for GitHub code search.
Return ONLY a JSON array of strings.

PRIORITY (search these first):
1. Code patterns: "navigate", "route", "href", "onClick", "onSubmit", "useState", "useEffect"
2. Component names: "LoginForm", "CartModal", "SearchBar", "Pagination"
3. API/Data: "fetch", "api/", "endpoint", "localStorage"
4. UI libraries: "Dialog", "Tabs", "Accordion", "Popover"

EXAMPLES: ["navigate", "route", "onClick", "LoginForm", "CartModal", "useState", "Dialog", "fetch"]
DO NOT use generic words like "https", "vercel", "React", "TypeScript".`;

async function extractFeatureKeywords(specText: string): Promise<string[]> {
  try {
    const completion = await openaiClient.chat.completions.create({
      model: "gpt-4o-mini",
      temperature: 0,
      messages: [
        { role: "system", content: keywordExtractorSystemPrompt },
        { role: "user", content: specText.slice(0, 6000) },
      ],
    });
    const raw = completion.choices?.[0]?.message?.content ?? "[]";
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return [...new Set(parsed.map((item) => String(item).trim()).filter(Boolean))].slice(0, 15);
    }
  } catch (error) {
    console.warn("Keyword extraction failed; falling back to heuristics", error);
  }
  return [...new Set(
    specText
      .split(/\W+/)
      .map((token) => token.trim())
      .filter((token) => token.length > 4)
      .slice(0, 5)
  )];
}

function summarizeSearchResult(keyword: string, payload: string): string | null {
  try {
    const data = JSON.parse(payload);
    const items = data.code_results ?? data.CodeResults ?? data.items ?? [];
    if (!Array.isArray(items) || !items.length) {
      return null;
    }
    // 코드를 더 많이 수집 (140자 → 800자)
    const bullets = items.slice(0, 5).map((item: any) => {
      const repo = item.repository?.full_name ?? item.repository?.fullName ?? `${DEFAULT_REPO_OWNER}/${DEFAULT_REPO_NAME}`;
      const filePath = item.path ?? item.Path ?? item.name ?? "unknown";
      const fragment = item.text_matches?.[0]?.fragment ?? item.fragment ?? "";
      // 코드 스니펫을 더 길게 유지 (URL, 라우팅 정보 파악용)
      const cleaned = fragment ? fragment.replace(/\s+/g, " ").trim().slice(0, 800) : "";
      return `• FILE: ${filePath}\n  CODE: ${cleaned}`;
    });
    return `=== Keyword: ${keyword} ===\n${bullets.join("\n\n")}`;
  } catch (error) {
    console.warn("Failed to summarize MCP search result", error);
    return null;
  }
}

function buildLocalFallbackContext(): string {
  const files = (process.env.GAIA_REPO_FALLBACK_FILES ?? "README.md,PROJECT.md")
    .split(",")
    .map((token) => token.trim())
    .filter(Boolean);
  const sections: string[] = [];
  for (const relative of files) {
    const abs = path.join(process.cwd(), relative);
    if (!fs.existsSync(abs)) continue;
    try {
      sections.push(`[local] ${relative}\n${fs.readFileSync(abs, "utf8").slice(0, 800).trim()}`);
    } catch (error) {
      console.warn(`Failed to read fallback file ${relative}`, error);
    }
  }
  return sections.join("\n\n");
}

function createResponseWaiter(child: ChildProcessWithoutNullStreams) {
  const pending = new Map<number, (value: any) => void>();
  let buffer = "";

  child.stdout.on("data", (chunk) => {
    buffer += chunk.toString();
    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) {
        try {
          const message = JSON.parse(line);
          const resolver = pending.get(message.id);
          if (resolver) {
            pending.delete(message.id);
            resolver(message);
          }
        } catch (error) {
          console.warn("Invalid MCP line", line, error);
        }
      }
      newlineIndex = buffer.indexOf("\n");
    }
  });

  return (id: number, timeoutMs = 15000) =>
    new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Timed out waiting for MCP response ${id}`));
      }, timeoutMs);
      pending.set(id, (value) => {
        clearTimeout(timeout);
        resolve(value);
      });
    });
}

async function callGithubMcpTool(toolName: string, args: Record<string, unknown>): Promise<string | null> {
  console.log(`[MCP] 🔧 Calling GitHub MCP tool: ${toolName}`);
  console.log(`[MCP] 📝 Args:`, JSON.stringify(args, null, 2));

  if (!process.env.GITHUB_PERSONAL_ACCESS_TOKEN) {
    console.warn("[MCP] ❌ GITHUB_PERSONAL_ACCESS_TOKEN not set; skipping MCP call");
    return null;
  }
  console.log(`[MCP] ✅ Token found`);

  if (!fs.existsSync(DEFAULT_MCP_BIN)) {
    console.warn(`[MCP] ❌ github-mcp-server binary missing at ${DEFAULT_MCP_BIN}`);
    return null;
  }
  console.log(`[MCP] ✅ Binary found at ${DEFAULT_MCP_BIN}`);

  return await new Promise((resolve) => {
    const child = spawn(DEFAULT_MCP_BIN, ["stdio", "--toolsets=default", "--read-only"], {
      cwd: DEFAULT_MCP_DIR,
      env: { ...process.env },
      stdio: ["pipe", "pipe", "pipe"],
    });

    const waitForResponse = createResponseWaiter(child);

    const cleanup = (value: string | null) => {
      try {
        child.stdin.end();
        child.kill();
      } catch (error) {
        console.warn("Failed to clean MCP process", error);
      }
      resolve(value);
    };

    child.on("error", (error) => {
      console.warn("github-mcp-server failed", error);
      cleanup(null);
    });

    const initPayload = {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "gaia-agent-builder", version: "0.2" },
      },
    };
    child.stdin.write(`${JSON.stringify(initPayload)}\n`);

    waitForResponse(1)
      .then(() => {
        const callPayload = {
          jsonrpc: "2.0",
          id: 2,
          method: "tools/call",
          params: { name: toolName, arguments: args },
        };
        child.stdin.write(`${JSON.stringify(callPayload)}\n`);
        return waitForResponse(2);
      })
      .then((message: any) => {
        child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: 3, method: "shutdown" })}\n`);
        const text = message?.result?.content?.find((item: any) => item?.type === "text")?.text ?? null;
        if (text && !text.includes("failed to")) {
          console.log("[MCP] ✅ Received response from GitHub MCP");
          console.log("[MCP] 📨 Response snippet:", text.slice(0, 200));
          cleanup(text);
        } else {
          console.warn("[MCP] ⚠️ GitHub MCP returned empty or error response");
          cleanup(null);
        }
      })
      .catch((error) => {
        console.warn("MCP request failed", error);
        cleanup(null);
      });
  });
}

async function collectRepoContext(keywords: string[]): Promise<string> {
  console.log(`[MCP] Fetching repository files from GitHub...`);

  try {
    // Step 1: 루트 디렉토리 내용 가져오기
    const rootContent = await callGithubMcpTool("get_file_contents", {
      owner: DEFAULT_REPO_OWNER,
      repo: DEFAULT_REPO_NAME,
      path: "/"
    });

    if (!rootContent) {
      console.warn("[MCP] Failed to get root directory");
      return buildLocalFallbackContext() || "";
    }

    // Step 2: 모든 파일/폴더 경로 수집
    const allPaths: string[] = [];
    const parseDirectory = (data: any, basePath: string = "") => {
      try {
        const items = Array.isArray(data) ? data : [data];
        for (const item of items) {
          const itemPath = basePath ? `${basePath}/${item.name}` : item.name;

          if (item.type === "file") {
            // 소스 파일만 추가
            if ((itemPath.endsWith(".ts") || itemPath.endsWith(".tsx") || itemPath.endsWith(".js") || itemPath.endsWith(".jsx")) &&
                !itemPath.includes("node_modules") && !itemPath.includes("dist/") && !itemPath.includes(".next/")) {
              allPaths.push(itemPath);
            }
          }
        }
      } catch (e) {
        console.warn(`[MCP] Failed to parse directory:`, e);
      }
    };

    parseDirectory(JSON.parse(rootContent));

    // Test Site with UI Elements2/src 디렉토리 확인 (실제 소스코드 위치)
    try {
      const srcContent = await callGithubMcpTool("get_file_contents", {
        owner: DEFAULT_REPO_OWNER,
        repo: DEFAULT_REPO_NAME,
        path: "Test Site with UI Elements2/src/"
      });
      if (srcContent) {
        parseDirectory(JSON.parse(srcContent), "Test Site with UI Elements2/src");
        console.log(`[MCP] Found Test Site with UI Elements2/src directory`);
      }
    } catch (e) {
      console.log(`[MCP] Failed to read Test Site directory: ${e}`);
    }

    console.log(`[MCP] Found ${allPaths.length} source files`);

    // Step 3: 모든 소스 파일 내용 가져오기
    const fileContents: string[] = [];
    const batchSize = 5;

    for (let i = 0; i < allPaths.length; i += batchSize) {
      const batch = allPaths.slice(i, i + batchSize);
      console.log(`[MCP] Fetching batch ${Math.floor(i / batchSize) + 1}/${Math.ceil(allPaths.length / batchSize)}...`);

      const batchPromises = batch.map(async (filePath: string) => {
        const content = await callGithubMcpTool("get_file_contents", {
          owner: DEFAULT_REPO_OWNER,
          repo: DEFAULT_REPO_NAME,
          path: filePath
        });

        if (content) {
          // base64 디코딩이 필요하면 처리
          let decoded = content;
          try {
            const parsed = JSON.parse(content);
            if (parsed.content && parsed.encoding === "base64") {
              decoded = Buffer.from(parsed.content, "base64").toString("utf-8");
            }
          } catch (e) {
            // 이미 텍스트인 경우
          }
          return `=== FILE: ${filePath} ===\n${decoded}\n`;
        }
        return null;
      });

      const batchResults = await Promise.all(batchPromises);
      fileContents.push(...batchResults.filter(Boolean) as string[]);
    }

    console.log(`[MCP] Successfully fetched ${fileContents.length}/${allPaths.length} files`);
    const totalChars = fileContents.reduce((sum, content) => sum + content.length, 0);
    console.log(`[MCP] Total source code: ${totalChars} chars`);

    return fileContents.join("\n");

  } catch (error) {
    console.error(`[MCP] Error fetching repo files:`, error);
    return buildLocalFallbackContext() || "";
  }
}

async function augmentSpecWithRepoContext(specText: string): Promise<string> {
  console.log("[MCP] Starting GitHub repo file collection...");

  const repoContext = await collectRepoContext([]);
  console.log(`[MCP] Collected repo context: ${repoContext.length} chars`);

  if (!repoContext.trim()) {
    console.log("[MCP] WARNING: Repo context is empty");
    return specText;
  }

  // 전체 소스코드 포함
  const augmented = `${specText}\n\n### Full Repository Source Code (from GitHub) ###\n${repoContext}`;
  console.log(`[MCP] Successfully augmented spec: ${specText.length} → ${augmented.length} chars`);
  return augmented;
}

// ===== AGENT DEFINITIONS =====

const MODEL = "gpt-5";

// Agent 1: TC Simplifier (기획서에서 간결한 테스트 케이스 추출)
const tcSimplifierAgent = new Agent({
  name: "TC Simplifier",
  instructions: `🎯 Goal: 기획서/스펙 문서에서 테스트 케이스를 간결하게 추출하라.

입력: 기획서 텍스트 (+ GitHub 소스코드는 나중에 사용)

출력: 간결한 TC JSON 목록

OUTPUT FORMAT (CRITICAL):
{
  "test_cases": [
    {
      "id": "TC001",
      "name": "로그인 성공",
      "priority": "MUST",
      "steps": [
        "페이지 이동",
        "이메일 입력",
        "비밀번호 입력",
        "로그인 버튼 클릭"
      ],
      "expected": "성공 토스트 표시 및 사용자 정보 노출"
    },
    {
      "id": "TC002",
      "name": "검색 기능",
      "priority": "MUST",
      "steps": [
        "검색창에 '노트북' 입력",
        "300ms 대기",
        "결과 확인"
      ],
      "expected": "노트북 관련 결과 카드 표시"
    }
  ]
}

RULES:
1. Output ONLY valid JSON (no markdown, no extra text)
2. Create EXACTLY 10 TCs (for quick testing)
3. Steps는 자연어로 간결하게 (구체적인 값 포함)
4. Priority: MUST (50%), SHOULD (30%), MAY (20%)
5. 기획서에 명시된 기능 위주로 추출
6. 각 TC는 독립적으로 실행 가능해야 함

예시:
- "로그인 페이지로 이동" → "페이지 이동"
- "이메일 필드에 test@example.com 입력" → "이메일 입력: test@example.com"
- "로그인 버튼을 클릭한다" → "로그인 버튼 클릭"

출력 제한: 정확히 10개 TC`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

// Agent 2: Executable RT Generator (TC + 코드를 보고 실행 가능한 RT 생성)
const executableRTGeneratorAgent = new Agent({
  name: "Executable RT Generator",
  instructions: `🎯 Goal: 간결한 TC와 GitHub 소스코드를 매칭하여 실행 가능한 RT JSON을 생성하라.

입력 1: TC JSON (간결한 자연어 steps)
입력 2: GitHub 전체 소스코드 ("=== FILE: ===" 섹션들)

YOUR TASK: 각 TC의 step을 코드에서 찾아서 실행 가능한 RT step으로 변환

🔍 MATCHING RULES (Step → Action/Selector/Params):

1. **"페이지 이동" / "~로 이동"**
   - 코드에서 URL 찾기: window.location, navigate(), href, <Link to="">
   - Action: "goto"
   - Selector: ""
   - Params: ["https://test-sitev2.vercel.app/path"]

2. **"입력" / "~에 입력"**
   - 코드에서 input 찾기: <input id="x">, placeholder="x", type="email"
   - 자연어에서 값 추출: "이메일 입력: test@example.com" → params=["test@example.com"]
   - Action: "fill"
   - Selector: "input[type=email]" 또는 "#email-input"
   - Params: [추출된 값 또는 "test@example.com"]

3. **"클릭" / "버튼 클릭"**
   - 코드에서 버튼 찾기: <button>텍스트</button>, aria-label, className
   - Action: "click"
   - Selector: "button:has-text('로그인')" 또는 ".login-btn"
   - Params: []

4. **"대기" / "~ms 대기"**
   - Action: "wait"
   - Selector: ""
   - Params: [숫자 추출, 기본값 1000]

5. **"확인" / "표시 확인"**
   - Action: "expectVisible" 또는 "expectText"
   - Selector: 코드에서 결과 영역 찾기
   - Params: [확인할 텍스트]

6. **"선택" / "드롭다운 선택"**
   - 코드에서 <select> 또는 Popover 찾기
   - Action: "select" 또는 "click"
   - Selector: "select[name=x]" 또는 롤 기반
   - Params: [선택할 값]

7. **셀렉터 우선순위:**
   - id > data-testid > aria-label > type > className > text

📋 OUTPUT FORMAT (CRITICAL):
{
  "profile": "test-plan",
  "url": "https://test-sitev2.vercel.app",
  "test_scenarios": [
    {
      "id": "RT001",
      "priority": "MUST",
      "scenario": "로그인 성공",
      "steps": [
        {
          "description": "페이지 이동",
          "action": "goto",
          "selector": "",
          "params": ["https://test-sitev2.vercel.app/#basics"]
        },
        {
          "description": "이메일 입력: test@example.com",
          "action": "fill",
          "selector": "input[type=email]",
          "params": ["test@example.com"]
        },
        {
          "description": "비밀번호 입력",
          "action": "fill",
          "selector": "input[type=password]",
          "params": ["password123"]
        },
        {
          "description": "로그인 버튼 클릭",
          "action": "click",
          "selector": "button:has-text('로그인')",
          "params": []
        }
      ],
      "assertion": {
        "description": "성공 토스트 표시 및 사용자 정보 노출",
        "selector": "",
        "condition": "expectVisible",
        "params": ["toast", "success"]
      }
    }
  ]
}

🚨 CRITICAL RULES:
1. Output ONLY valid JSON (first char: {, last char: })
2. NO markdown code blocks (no \`\`\`json)
3. 모든 TC를 RT로 변환 (입력 10개 → 출력 10개)
4. action은 반드시: goto, fill, click, wait, expectVisible, expectText, select 중 하나
5. selector는 코드에서 실제로 찾은 값 사용
6. params는 자연어 step이나 코드에서 추출
7. 코드에서 찾을 수 없으면 합리적인 기본값 사용
8. ⚠️ NEVER use action="note" or action="" - these are NOT executable!
9. ⚠️ ALWAYS search the "=== FILE: ===" sections for real selectors
10. ⚠️ If you output action="note", YOU HAVE FAILED THE TASK

EXAMPLE MATCHING:
TC Step: "이메일 입력: test@example.com"
Code: <input id="email-input" type="email" placeholder="이메일" />
RT Step:
{
  "description": "이메일 입력: test@example.com",
  "action": "fill",
  "selector": "#email-input",
  "params": ["test@example.com"]
}

TC Step: "로그인 버튼 클릭"
Code: <button className="login-btn" onClick={handleLogin}>로그인</button>
RT Step:
{
  "description": "로그인 버튼 클릭",
  "action": "click",
  "selector": ".login-btn",
  "params": []
}

TC Step: "'-' 버튼 클릭" or "수량 감소"
Code: <button><svg class="lucide lucide-minus"><path d="M5 12h14"/></svg></button>
RT Step:
{
  "description": "'-' 버튼 클릭 (1회)",
  "action": "click",
  "selector": "button:has(svg.lucide-minus)",
  "params": []
}

TC Step: "닫기 버튼 클릭" or "Dialog 닫기"
Code: <button><svg class="lucide lucide-x"><path d="M18 6 6 18"/></svg></button>
RT Step:
{
  "description": "Dialog 닫기",
  "action": "click",
  "selector": "button:has(svg.lucide-x)",
  "params": []
}`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

// Agent 3: Test Case Generator (TC JSON 생성)
const testCaseGeneratorAgent = new Agent({
  name: "Test Case Generator",
  instructions: `Convert feature list to TC JSON format with CONCRETE URLs, selectors, and values.

🚨 CRITICAL: Use specific implementation details from features!
- Feature mentions "/dashboard" → use "https://test-sitev2.vercel.app/dashboard" in steps
- Feature mentions "#search-input" → include this selector in steps
- Feature mentions real values → use those exact values

OUTPUT FORMAT (CRITICAL):
{
  "checklist": [
    {
      "id": "TC001-1",
      "name": "Feature name in Korean",
      "category": "form",
      "priority": "MUST",
      "precondition": "",
      "steps": ["https://test-sitev2.vercel.app/specific-path로 이동", "특정 필드에 구체적 값 입력", "버튼 클릭"],
      "expected_result": "구체적인 URL이나 텍스트 포함"
    }
  ],
  "summary": {"total": 10, "must": 5, "should": 3, "may": 2}
}

CRITICAL RULES:
1. Output ONLY valid JSON (no markdown, no comments, no extra text)
2. First character must be {, last character must be }
3. Create EXACTLY 10 TCs from the input features (for quick testing)
4. Each input feature line → at least 1 TC
5. Priority distribution: MUST (50%), SHOULD (30%), MAY (20%)
6. **Include CONCRETE details in steps**: URLs, paths, IDs, class names, real test values



Example TC:
{
  "id": "TC001-1",
  "name": "실시간 검색 기능 테스트",
  "category": "interaction",
  "priority": "MUST",
  "precondition": "",
  "steps": ["페이지로 이동", "검색어 입력", "300ms 대기", "결과 확인"],
  "expected_result": "검색 결과가 표시됨"
}

Priority Guide:
- MUST: 로그인, 회원가입, 장바구니, 결제, 검색
- SHOULD: 필터, 정렬, 탭, 드롭다운, 토글
- MAY: 뷰 전환, 툴팁, 애니메이션

Create EXACTLY 10 TCs (for quick testing). Focus on the most important features.`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

// Agent 4: Code Mapper (코드에서 구체적인 구현 정보 추출)
const codeMapperAgent = new Agent({
  name: "Code Mapper",
  instructions: `Extract concrete implementation details from GitHub source code for test execution.

INPUT: Test case checklist + Full repository source code

YOUR JOB: For EACH test case, find the concrete implementation details in the code:
- URLs and routes (navigate, href, to, pathname)
- Element selectors (id, className, data-testid, aria-label)
- Button/input text and placeholders
- API endpoints and data structures
- State variable names
- Event handler names

OUTPUT FORMAT (JSON):
{
  "mappings": [
    {
      "tc_id": "TC001-1",
      "tc_name": "로그인 기능",
      "code_details": {
        "url": "/#/auth or /login",
        "selectors": {"email": "input[type=email]#email-input", "password": "input[type=password]", "submit": "button.login-btn"},
        "texts": {"submit_button": "로그인", "error_toast": "필수값 누락"},
        "routes": {"success": "/dashboard", "failure": null}
      }
    }
  ]
}

CRITICAL RULES:
1. Output ONLY valid JSON
2. Create mapping for EVERY TC in the input
3. Search the "=== FILE:" sections for implementation details
4. If you can't find details in code, use reasonable defaults but note in code_details

Example:
TC: "로그인 기능 테스트"
Code found in src/components/AuthForm.tsx:
  - navigate('/dashboard')
  - id="email-input"
  - className="login-btn"
  - toast.error("필수값 누락")
Output mapping with these exact values.`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

// Agent 5: Scenario Splitter (TC + Code Mapping → RT JSON 변환)
const scenarioSplitterAgent = new Agent({
  name: "Scenario Splitter",
  instructions: `You are a test case converter. Convert TCs to RTs using code mappings from previous agent.

INPUT 1: TC checklist JSON
INPUT 2: Code mappings JSON with concrete selectors/URLs/values

YOUR TASK: Merge TC steps with code_details to create executable RT test scenarios.

EXAMPLE:
TC: {"id":"TC001-1", "name":"로그인", "steps":["페이지 이동","이메일 입력","비밀번호 입력","로그인 버튼 클릭"]}
Mapping: {"tc_id":"TC001-1", "code_details":{"url":"/#/auth", "selectors":{"email":"#email-input", "submit":".login-btn"}}}
RT Output:
{
  "id": "RT001",
  "scenario": "로그인",
  "steps": [
    {"description": "페이지 이동", "action": "goto", "selector": "", "params": ["https://test-sitev2.vercel.app/#/auth"]},
    {"description": "이메일 입력", "action": "fill", "selector": "#email-input", "params": ["test@example.com"]},
    {"description": "비밀번호 입력", "action": "fill", "selector": "input[type=password]", "params": ["password123"]},
    {"description": "로그인 버튼 클릭", "action": "click", "selector": ".login-btn", "params": []}
  ]
}

YOUR TASK: Convert EACH item in the checklist array to RT format. If there are 50 items in checklist, you MUST output 50 items in test_scenarios.

OUTPUT FORMAT:
{
  "profile": "realistic-test",
  "url": "https://test-sitev2.vercel.app",
  "test_scenarios": [
    {
      "id": "RT001",
      "priority": "MUST",
      "scenario": "Copy the 'name' field from TC here",
      "steps": [
        {"description": "Convert each step string to this object format", "action": "click", "selector": "", "params": []}
      ],
      "assertion": {
        "description": "Copy the 'expected_result' field from TC here",
        "selector": "",
        "condition": "expectVisible",
        "params": []
      }
    }
  ]
}

CONVERSION RULES:
1. TC "id" → RT "id": TC001-1→RT001, TC001-2→RT002, TC002-1→RT003, TC050-1→RT050
2. TC "name" → RT "scenario": Copy exactly
3. TC "priority" → RT "priority": Copy exactly (MUST/SHOULD/MAY)
4. TC "steps" array (strings) → RT "steps" array (objects): Each string becomes {"description": "the string", "action": "click", "selector": "", "params": []}
5. TC "expected_result" → RT "assertion.description": Copy exactly

CRITICAL: Output ONLY valid JSON. First character {, last character }. NO markdown code blocks.

EXAMPLE:
Input TC: {"id":"TC001-1", "name":"검색 기능", "priority":"MUST", "steps":["페이지 이동","검색어 입력"], "expected_result":"결과 표시"}
Output RT: {
  "id": "RT001",
  "priority": "MUST",
  "scenario": "검색 기능",
  "steps": [
    {"description": "페이지 이동", "action": "goto", "selector": "", "params": ["https://test-sitev2.vercel.app"]},
    {"description": "검색어 입력", "action": "fill", "selector": "", "params": []}
  ],
  "assertion": {"description": "결과 표시", "selector": "", "condition": "expectVisible", "params": []}
}

NOW CONVERT ALL TCs IN THE INPUT TO RTs. DO NOT RETURN AN EMPTY test_scenarios ARRAY.`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

// Agent 5: JSON Merge (여러 RT JSON 병합)
const jsonMergeAgent = new Agent({
  name: "JSON Merge Agent",
  instructions: `여러 RT JSON을 단일 객체로 병합한다.
- profile/url은 첫 번째 입력 사용, pdf_hash 없으면 dummy 값 추가
- test_scenarios 배열을 이어 붙이고 ID 충돌 시 뒤 항목 재번호 부여
- goto params가 # 또는 / 로 시작하면 기본 URL로 절대화
- assertion이 문자열이면 { description, selector:"", condition:"expectVisible", params:[] }로 변환
- 출력: 단일 RT JSON 객체`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

function approvalRequest(_message: string) {
  // TODO: hook up to real approval workflow
  // For now, always use broad feature extraction (false = broadFeatureAgent)
  return false;
}

export interface WorkflowInput {
  input_as_text: string;
}

export interface WorkflowOutput {
  output_text: string;
}

export const runWorkflow = async (workflow: WorkflowInput): Promise<WorkflowOutput> => {
  return await withTrace("GAIA Agent Builder", async () => {
    // Step 1: GitHub MCP를 통해 코드 컨텍스트 수집
    console.log("[AgentBuilder] Augmenting spec with GitHub repo context...");
    const augmentedInput = await augmentSpecWithRepoContext(workflow.input_as_text);
    console.log(`[AgentBuilder] Augmented input length: ${augmentedInput.length}`);

    const runner = new Runner({
      traceMetadata: {
        __trace_source__: "agent-builder",
        workflow_id: "wf_github_mcp_augmented_pipeline",
      },
    });

    const conversationHistory: AgentInputItem[] = [
      {
        role: "user",
        content: [
          {
            type: "input_text",
            text: augmentedInput,
          },
        ],
      },
    ];

    const approvalMessage = "어떤 기능을 집중해서 테스트할까요? (예: 로그인, 장바구니, 회원가입)";

    // Step 2: NEW 2-STAGE PIPELINE
    console.log("[AgentBuilder] 🚀 Using NEW 2-STAGE PIPELINE");
    console.log("[AgentBuilder] Stage 1: TC Simplifier (기획서 → 간결한 TC)");
    console.log("[AgentBuilder] Stage 2: Executable RT Generator (TC + 코드 → 실행가능 RT)");

    // === STAGE 1: TC Simplifier ===
    const tcResult = await runner.run(tcSimplifierAgent, [...conversationHistory]);
    conversationHistory.push(...tcResult.newItems.map((item) => item.rawItem));
    if (!tcResult.finalOutput) {
      throw new Error("TC Simplifier Agent returned empty output");
    }

    // TC 개수 확인
    try {
      const tcOutput = JSON.parse(tcResult.finalOutput);
      const tcCount = tcOutput.test_cases?.length || 0;
      console.log(`[AgentBuilder] ✅ Stage 1 completed: ${tcCount} TCs generated`);
      console.log(`[AgentBuilder] 📝 TC output (first 1500 chars):\n${tcResult.finalOutput.substring(0, 1500)}\n...`);
    } catch (e) {
      console.log(`[AgentBuilder] ⚠️ Stage 1 completed but JSON parse failed`);
      console.log(`[AgentBuilder] TC output:\n${tcResult.finalOutput.substring(0, 1500)}\n...`);
    }

    // === STAGE 2: Executable RT Generator ===
    // 🚨 FIX: GitHub 소스코드를 명시적으로 재주입하여 GPT-5가 action/selector/params를 채울 수 있도록 함
    console.log("[AgentBuilder] Stage 2: Re-injecting GitHub source code for executable RT generation");
    const stage2Input: AgentInputItem[] = [
      {
        role: "user",
        content: [
          {
            type: "input_text",
            text: `Here are the simplified test cases from Stage 1:\n\n${tcResult.finalOutput}\n\n` +
                  `Now, use the full GitHub source code below to generate executable RT JSON with proper action/selector/params.\n\n` +
                  `CRITICAL: Every step MUST have:\n` +
                  `- action: one of [goto, fill, click, wait, expectVisible, expectText, select]\n` +
                  `- selector: found from the source code (not empty!)\n` +
                  `- params: extracted from TC steps or source code\n\n` +
                  `${augmentedInput}`
          }
        ]
      }
    ];
    const rtResult = await runner.run(executableRTGeneratorAgent, stage2Input);
    conversationHistory.push(...rtResult.newItems.map((item) => item.rawItem));
    if (!rtResult.finalOutput) {
      throw new Error("Executable RT Generator Agent returned empty output");
    }

    // RT 검증
    try {
      const tcOutput = JSON.parse(tcResult.finalOutput);
      const tcCount = tcOutput.test_cases?.length || 0;

      const rtOutput = JSON.parse(rtResult.finalOutput);
      const rtCount = rtOutput.test_scenarios?.length || 0;

      console.log(`[AgentBuilder] ✅ Stage 2 completed: ${tcCount} TCs → ${rtCount} RTs`);
      console.log(`[AgentBuilder] 📝 RT output (first 2000 chars):\n${rtResult.finalOutput.substring(0, 2000)}\n...`);

      // 첫 번째 RT 시나리오 상세 출력
      if (rtCount > 0) {
        console.log(`[AgentBuilder] 📋 First RT scenario:`);
        console.log(JSON.stringify(rtOutput.test_scenarios[0], null, 2));

        // action 검증
        const firstStep = rtOutput.test_scenarios[0].steps[0];
        if (firstStep.action === "note" || firstStep.action === "") {
          console.warn(`[AgentBuilder] ⚠️ WARNING: First step has action="${firstStep.action}" - may not be executable!`);
        } else {
          console.log(`[AgentBuilder] ✅ First step action is "${firstStep.action}" - looks executable!`);
        }
      }

      if (rtCount < tcCount) {
        console.warn(`[AgentBuilder] ⚠️ WARNING: Lost ${tcCount - rtCount} scenarios during conversion!`);
      }
    } catch (e) {
      console.log(`[AgentBuilder] ❌ Stage 2 JSON parse failed: ${e}`);
      console.log(`[AgentBuilder] RT output:\n${rtResult.finalOutput.substring(0, 2000)}`);
    }

    console.log("[AgentBuilder] 🎉 2-STAGE PIPELINE COMPLETED");

    return {
      output_text: rtResult.finalOutput,
    };
  });
};
