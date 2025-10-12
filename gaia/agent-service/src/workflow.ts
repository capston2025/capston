import { Agent, AgentInputItem, Runner } from "@openai/agents";

const agent = new Agent({
  name: "QA Analysis Agent",
  instructions: `너의 역할은 QA 자동화 분석 에이전트이다.

주어진 기획서(또는 문서)에서 모든 제품/서비스 기능을 빠짐없이 식별하고,
각 기능을 중복 없이 논리적 순서로 테스트 케이스로 구조화해야 한다.

### Rules
- 명세, 예외, 흐름 등 기능 관련 문장은 전부 포함할 것
- 각 기능은 중복 없이, 기능별 테스트 케이스로 변환할 것
- 각 테스트 케이스에는 다음 항목을 반드시 포함할 것:
  - 전제조건(precondition)
  - 테스트 단계(steps) — 여러 단계일 경우 배열로 작성
  - 예상 결과(expected_result)
  - 중요도(priority): MUST / SHOULD / MAY 중 하나
- 출력은 반드시 아래 JSON 스키마를 **strictly 준수할 것**
- JSON 외 다른 설명, 문장, 주석, 불필요한 텍스트를 출력하지 말 것

### Output Format
{
  "checklist": [
    {
      "id": "TC001",
      "name": "기능명",
      "category": "navigation|authentication|cart|...",
      "priority": "MUST|SHOULD|MAY",
      "precondition": "전제 조건",
      "steps": ["step1", "step2"],
      "expected_result": "예상 결과"
    }
  ],
  "summary": {
    "total": 25,
    "must": 15,
    "should": 8,
    "may": 2
  }
}

### Document to analyze
{input_as_text}`,
  model: "gpt-5"
});

export interface WorkflowInput {
  input_as_text: string;
}

export interface WorkflowOutput {
  output_text: string;
}

// Main code entrypoint
export const runWorkflow = async (workflow: WorkflowInput): Promise<WorkflowOutput> => {
  console.log("🤖 Using Agent:", agent.name);
  console.log("🔧 Model:", (agent as any).model || "unknown");

  const conversationHistory: AgentInputItem[] = [
    {
      role: "user",
      content: [
        {
          type: "input_text",
          text: workflow.input_as_text
        }
      ]
    }
  ];

  const runner = new Runner({
    traceMetadata: {
      __trace_source__: "agent-builder",
      workflow_id: "wf_68ea589f9a948190a518e9b2626ab1d5037b50134b0c56e7"
    }
  });

  const agentResultTemp = await runner.run(
    agent,
    [...conversationHistory]
  );

  conversationHistory.push(...agentResultTemp.newItems.map((item) => item.rawItem));

  // Debug: Log response structure
  console.log("Agent response items count:", agentResultTemp.newItems.length);
  console.log("FinalOutput length:", agentResultTemp.finalOutput?.length || 0);

  // Try to get full output from all items
  let fullOutput = agentResultTemp.finalOutput || "";

  // If finalOutput is incomplete, try to reconstruct from newItems
  if (agentResultTemp.newItems.length > 0) {
    const textItems = agentResultTemp.newItems
      .filter(item => {
        const rawItem = item.rawItem as any;
        return rawItem.role === "assistant" && Array.isArray(rawItem.content);
      })
      .flatMap(item => {
        const rawItem = item.rawItem as any;
        return rawItem.content || [];
      })
      .filter((content: any) => content.type === "output_text")
      .map((content: any) => content.text)
      .join("");

    if (textItems.length > fullOutput.length) {
      console.log("Using reconstructed output from newItems");
      fullOutput = textItems;
    }
  }

  if (!fullOutput) {
    throw new Error("Agent result is undefined");
  }

  console.log("Final output length:", fullOutput.length);

  const agentResult = {
    output_text: fullOutput
  };

  return agentResult;
};
