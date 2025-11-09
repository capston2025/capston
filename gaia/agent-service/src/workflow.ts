import { Agent, AgentInputItem, Runner, withTrace } from "@openai/agents";
import { OpenAI } from "openai";
import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const openaiClient = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

const DEFAULT_REPO_OWNER = process.env.GAIA_REPO_OWNER ?? "capston2025";
const DEFAULT_REPO_NAME = process.env.GAIA_REPO_NAME ?? "capston";
const DEFAULT_MCP_DIR = process.env.GITHUB_MCP_SERVER_DIR ?? path.join(os.homedir(), "학습", "github-mcp-server");
const DEFAULT_MCP_BIN = process.env.GITHUB_MCP_SERVER_BIN ?? path.join(DEFAULT_MCP_DIR, "github-mcp-server");

const keywordExtractorSystemPrompt = `Extract up to 8 short feature keywords (2-4 words each) from the specification. \nReturn ONLY a JSON array of strings.\nExamples: ["로그인", "장바구니", "회원가입"]`;

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
      return [...new Set(parsed.map((item) => String(item).trim()).filter(Boolean))].slice(0, 8);
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
    const bullets = items.slice(0, 3).map((item: any) => {
      const repo = item.repository?.full_name ?? item.repository?.fullName ?? `${DEFAULT_REPO_OWNER}/${DEFAULT_REPO_NAME}`;
      const filePath = item.path ?? item.Path ?? item.name ?? "unknown";
      const fragment = item.text_matches?.[0]?.fragment ?? item.fragment ?? "";
      const cleaned = fragment ? fragment.replace(/\s+/g, " ").trim().slice(0, 140) : "";
      return `• ${repo}/${filePath}${cleaned ? ` → ${cleaned}` : ""}`;
    });
    return `Keyword: ${keyword}\n${bullets.join("\n")}`;
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
          cleanup(text);
        } else {
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
  const sections: string[] = [];
  for (const keyword of keywords.slice(0, 5)) {
    const query = `repo:${DEFAULT_REPO_OWNER}/${DEFAULT_REPO_NAME} ${keyword}`;
    const raw = await callGithubMcpTool("search_code", { query, perPage: 3 });
    if (raw) {
      const summary = summarizeSearchResult(keyword, raw);
      if (summary) {
        sections.push(summary);
        continue;
      }
    }
    const fallback = buildLocalFallbackContext();
    if (fallback) {
      sections.push(`Keyword: ${keyword}\n${fallback}`);
    }
  }
  return sections.join("\n\n");
}

async function augmentSpecWithRepoContext(specText: string): Promise<string> {
  const keywords = await extractFeatureKeywords(specText);
  if (!keywords.length) {
    return specText;
  }
  const repoContext = await collectRepoContext(keywords);
  if (!repoContext.trim()) {
    return specText;
  }
  return `${specText}\n\n### Repo Context (auto-collected)\n${repoContext}`;
}

// ===== AGENT DEFINITIONS =====

const MODEL = "gpt-4o-mini";

// Agent 1: Broad Feature Extractor (모든 기능 추출)
const broadFeatureAgent = new Agent({
  name: "Broad Feature Agent",
  instructions: `🎯 Goal: 전 사이트/문서를 훑으며 테스트 가능한 모든 기능을 한 줄 단위로 전수조사하라.
- 절대 요약 금지, 유사 기능도 각각 출력
- 입력→검증→저장 등 여러 동작은 각각 라인으로 분리
- [기능명] : category - 설명 형식을 유지
- category는 form / interaction / navigation / data / ui / feedback / accessibility 중 하나
- Plain text only (JSON, Markdown 금지)
- 출력 제한은 최대 30개이다.`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

// Agent 2: Targeted Feature Filter (특정 기능만 필터링)
const targetedFeatureAgent = new Agent({
  name: "Filter Summarizer Agent",
  instructions: `user_request 키워드와 직접적으로 연결된 기능만 필터링하여 [기능명] : category - 설명 형식으로 출력하라.
요청된 기능군에서는 성공/오류/경계/자동동작 등을 모두 세분화하고, 그 외 영역은 무시한다.
출력은 Plain text, 한 줄당 하나의 기능.`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

// Agent 3: Test Case Generator (TC JSON 생성)
const testCaseGeneratorAgent = new Agent({
  name: "Test Case Generator",
  instructions: `주어진 기능 리스트(Plain text)를 GAIA용 TC JSON으로 변환한다.
- 최소 2~5개 variant (정상/오류/경계)를 생성
- steps는 자연어 액션, selector 금지
- expected_result는 상태 기반, 토스트/팝업 대신 화면 상태 확인
- 출력은 { "checklist": [...], "summary": {...} } JSON 하나만 허용
- ID는 TC001-1, TC001-2 형식
- priority는 MUST/SHOULD/MAY
- category는 form/interaction/navigation/data/ui/feedback/accessibility`,
  model: MODEL,
  modelSettings: {
    store: true,
  },
});

// Agent 4: Scenario Splitter (TC → RT JSON 변환)
const scenarioSplitterAgent = new Agent({
  name: "Scenario Splitter",
  instructions: `TC JSON을 받아 올바른 형식의 RT JSON으로 변환한다.

CRITICAL: 각 TC를 별도의 test scenario로 분리해야 함
- TC001-1 → RT001, TC001-2 → RT002, TC002-1 → RT003 (순차 ID)
- 하나의 TC당 하나의 test_scenario 객체를 생성
- test_scenarios 배열에는 최소 2개 이상의 시나리오가 있어야 함

STEP 형식 규칙 (필수):
각 step 객체는 반드시 다음 4개 필드를 포함해야 함:
{
  "description": "사용자가 이해할 수 있는 한글 설명",
  "action": "goto|click|fill|wait|expectVisible|expectTrue 등",
  "selector": "",
  "params": []
}

Step 변환 예시:
- "페이지에 접속" → { "description": "기본 기능 페이지로 이동한다", "action": "goto", "selector": "", "params": ["https://test-sitev2.vercel.app/#basics"] }
- "로그인 버튼 클릭" → { "description": "로그인 버튼을 클릭한다", "action": "click", "selector": "", "params": [] }
- "이메일 입력" → { "description": "이메일 입력란에 'user@test.com'을 입력한다", "action": "fill", "selector": "", "params": ["user@test.com"] }
- "대기" → { "description": "잠시 대기한다", "action": "wait", "selector": "", "params": ["500"] }
- "검증" → { "description": "성공 메시지가 표시되는지 확인한다", "action": "expectVisible", "selector": "", "params": ["로그인되었습니다!"] }

Assertion 형식:
{
  "description": "예상 결과 설명",
  "selector": "",
  "condition": "expectVisible|expectTrue",
  "params": ["검증할 텍스트 또는 조건"]
}

출력 규칙:
- 오직 JSON 객체만 반환 (마크다운 코드 블록 금지, 설명 금지)
- JSON 외의 다른 텍스트 절대 포함 금지
- 첫 문자는 반드시 {, 마지막 문자는 반드시 }
- 모든 step은 description, action, selector, params 4개 필드 필수

출력 형식:
{
  "profile": "realistic-test",
  "url": "https://test-sitev2.vercel.app",
  "test_scenarios": [
    {
      "id": "RT001",
      "priority": "MUST",
      "scenario": "...",
      "steps": [
        { "description": "...", "action": "goto", "selector": "", "params": ["..."] },
        { "description": "...", "action": "fill", "selector": "", "params": ["..."] }
      ],
      "assertion": {
        "description": "...",
        "selector": "",
        "condition": "expectVisible",
        "params": ["..."]
      }
    }
  ]
}`,
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

    // Step 2: Agent 파이프라인 선택
    if (approvalRequest(approvalMessage)) {
      // Targeted pipeline (특정 기능 집중)
      console.log("[AgentBuilder] Using TARGETED pipeline");

      // Agent 2: Filter Summarizer
      const filterResult = await runner.run(targetedFeatureAgent, [...conversationHistory]);
      conversationHistory.push(...filterResult.newItems.map((item) => item.rawItem));
      if (!filterResult.finalOutput) {
        throw new Error("Filter Summarizer Agent returned empty output");
      }
      console.log(`[AgentBuilder] Filter Summarizer completed: ${filterResult.finalOutput.length} chars`);

      // Agent 3: Test Case Generator
      const tcResult = await runner.run(testCaseGeneratorAgent, [...conversationHistory]);
      conversationHistory.push(...tcResult.newItems.map((item) => item.rawItem));
      if (!tcResult.finalOutput) {
        throw new Error("Test Case Generator Agent returned empty output");
      }
      console.log(`[AgentBuilder] Test Case Generator completed: ${tcResult.finalOutput.length} chars`);

      // Agent 4: Scenario Splitter
      const rtResult = await runner.run(scenarioSplitterAgent, [...conversationHistory]);
      conversationHistory.push(...rtResult.newItems.map((item) => item.rawItem));
      if (!rtResult.finalOutput) {
        throw new Error("Scenario Splitter Agent returned empty output");
      }
      console.log(`[AgentBuilder] Scenario Splitter completed: ${rtResult.finalOutput.length} chars`);

      // Scenario Splitter already outputs complete RT JSON, no merge needed
      console.log("[AgentBuilder] Returning RT JSON directly (no merge step)");

      return {
        output_text: rtResult.finalOutput,
      };
    } else {
      // Broad pipeline (모든 기능 추출)
      console.log("[AgentBuilder] Using BROAD pipeline");

      // Agent 1: Broad Feature Agent
      const broadResult = await runner.run(broadFeatureAgent, [...conversationHistory]);
      conversationHistory.push(...broadResult.newItems.map((item) => item.rawItem));
      if (!broadResult.finalOutput) {
        throw new Error("Broad Feature Agent returned empty output");
      }
      console.log(`[AgentBuilder] Broad Feature Agent completed: ${broadResult.finalOutput.length} chars`);

      // Agent 3: Test Case Generator
      const tcResult = await runner.run(testCaseGeneratorAgent, [...conversationHistory]);
      conversationHistory.push(...tcResult.newItems.map((item) => item.rawItem));
      if (!tcResult.finalOutput) {
        throw new Error("Test Case Generator Agent returned empty output");
      }
      console.log(`[AgentBuilder] Test Case Generator completed: ${tcResult.finalOutput.length} chars`);

      // Agent 4: Scenario Splitter
      const rtResult = await runner.run(scenarioSplitterAgent, [...conversationHistory]);
      conversationHistory.push(...rtResult.newItems.map((item) => item.rawItem));
      if (!rtResult.finalOutput) {
        throw new Error("Scenario Splitter Agent returned empty output");
      }
      console.log(`[AgentBuilder] Scenario Splitter completed: ${rtResult.finalOutput.length} chars`);

      // Scenario Splitter already outputs complete RT JSON, no merge needed
      console.log("[AgentBuilder] Returning RT JSON directly (no merge step)");

      return {
        output_text: rtResult.finalOutput,
      };
    }
  });
};
