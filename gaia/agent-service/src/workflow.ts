import { Agent, AgentInputItem, Runner, withTrace } from "@openai/agents";
import { OpenAI } from "openai";
import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const openaiClient = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

const DEFAULT_REPO_OWNER = process.env.GAIA_REPO_OWNER ?? "capston2025";
const DEFAULT_REPO_NAME = process.env.GAIA_REPO_NAME ?? "capston";
const DEFAULT_MCP_DIR = process.env.GITHUB_MCP_SERVER_DIR ?? path.join(os.homedir(), "학습", "github-mcp-server");
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
  if (!process.env.GITHUB_PERSONAL_ACCESS_TOKEN) {
    console.warn("GITHUB_PERSONAL_ACCESS_TOKEN not set; skipping MCP call");
    return null;
  }
  if (!fs.existsSync(DEFAULT_MCP_BIN)) {
    console.warn(`github-mcp-server binary missing at ${DEFAULT_MCP_BIN}`);
    return null;
  }

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

const broadFeatureAgent = new Agent({
  name: "Broad Feature Agent",
  instructions: `🎯 Goal: 전 사이트/문서를 훑으며 테스트 가능한 모든 기능을 한 줄 단위로 전수조사하라.\n- 절대 요약 금지, 유사 기능도 각각 출력\n- 입력→검증→저장 등 여러 동작은 각각 라인으로 분리\n- [기능명] : category - 설명 형식을 유지\n- category는 form / interaction / navigation / data / ui / feedback / accessibility 중 하나\n- Plain text only (JSON, Markdown 금지)`,
  model: "gpt-5",
  modelSettings: {
    reasoning: { effort: "low", summary: "auto" },
    store: true,
  },
});

const targetedFeatureAgent = new Agent({
  name: "Filter Summarizer Agent",
  instructions: `user_request 키워드와 직접적으로 연결된 기능만 필터링하여 [기능명] : category - 설명 형식으로 출력하라.\n요청된 기능군에서는 성공/오류/경계/자동동작 등을 모두 세분화하고, 그 외 영역은 무시한다.`,
  model: "gpt-5",
  modelSettings: {
    reasoning: { effort: "low", summary: "auto" },
    store: true,
  },
});

const testCaseGeneratorAgent = new Agent({
  name: "Test Case Generator",
  instructions: `주어진 기능 리스트(Plain text)를 GAIA용 TC JSON으로 변환한다.\n- 최소 2~5개 variant (정상/오류/경계)를 생성\n- steps는 자연어 액션, selector 금지\n- expected_result는 상태 기반, 토스트/팝업 대신 화면 상태 확인\n- 출력은 { "checklist": [...], "summary": {...}, "has_next": bool } JSON 하나만 허용`,
  model: "gpt-5",
  modelSettings: {
    reasoning: { effort: "low", summary: "auto" },
    store: true,
  },
});

const scenarioSplitterAgent = new Agent({
  name: "Scenario Splitter",
  instructions: `TC JSON을 받아 loose-mode RT JSON으로 변환한다.\n- TC001-1 → RT001 (순차 ID)\n- category에 따라 goto URL hash 결정 (form→#forms, interaction→#interactions, etc.)\n- "페이지에 접속" → goto + wait(800ms)\n- 입력/클릭 step 앞에 기능명 prefix 붙이기\n- 제출/모달 뒤에는 wait(600ms/500ms) 추가\n- assertion 객체 필수 (expected_result 기반, expectVisible/expectTrue)\n- selector는 항상 ""`,
  model: "gpt-5",
  modelSettings: {
    reasoning: { effort: "low", summary: "auto" },
    store: true,
  },
});

const jsonMergeAgent = new Agent({
  name: "JSON Merge Agent",
  instructions: `여러 RT JSON을 단일 객체로 병합한다.\n- profile/url은 첫 번째 입력 사용, pdf_hash 없으면 dummy 값 추가\n- test_scenarios 배열을 이어 붙이고 ID 충돌 시 뒤 항목 재번호 부여\n- goto params가 # 또는 / 로 시작하면 기본 URL로 절대화\n- assertion이 문자열이면 { description, selector:"", condition:"expectVisible", params:[] }로 변환`,
  model: "gpt-5",
  modelSettings: {
    reasoning: { effort: "low", summary: "auto" },
    store: true,
  },
});

function approvalRequest(_message: string) {
  // TODO: hook up to real approval workflow. For now we always request targeted filtering.
  return true;
}

export interface WorkflowInput {
  input_as_text: string;
}

export interface WorkflowOutput {
  output_text: string;
}

export const runWorkflow = async (workflow: WorkflowInput): Promise<WorkflowOutput> => {
  return await withTrace("GAIA Agent Builder", async () => {
    const augmentedInput = await augmentSpecWithRepoContext(workflow.input_as_text);

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

    const runner = new Runner({
      traceMetadata: {
        __trace_source__: "agent-builder",
        workflow_id: "wf_github_mcp_augmented_pipeline",
      },
    });

    const approvalMessage = "어떤 기능을 집중해서 테스트할까요? (예: 로그인, 장바구니, 회원가입)";
    const pipeline = approvalRequest(approvalMessage)
      ? [targetedFeatureAgent, testCaseGeneratorAgent, scenarioSplitterAgent, jsonMergeAgent]
      : [broadFeatureAgent, testCaseGeneratorAgent, scenarioSplitterAgent, jsonMergeAgent];

    let finalOutput = "";
    for (const agent of pipeline) {
      const result = await runner.run(agent, [...conversationHistory]);
      conversationHistory.push(...result.newItems.map((item) => item.rawItem));
      if (!result.finalOutput) {
        throw new Error("Agent result is undefined");
      }
      finalOutput = result.finalOutput;
    }

    return {
      output_text: finalOutput,
    };
  });
};
