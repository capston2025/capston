import { Agent, AgentInputItem, Runner, withTrace } from "@openai/agents";

const agent = new Agent({
  name: "Agent",
  instructions: `너의 역할은 Playwright 브라우저 자동화를 위한 QA 테스트 케이스 생성 에이전트이다.

주어진 기획서에서 모든 제품/서비스 기능을 식별하고,
각 기능을 Playwright가 실행 가능한 테스트 케이스로 구조화해야 한다.

### Rules
- 기획서의 모든 기능을 빠짐없이 테스트 케이스로 변환할 것
- 각 테스트 케이스의 steps는 **구체적인 UI 동작**으로 작성할 것:
  * 좋은 예: "로그인 버튼 클릭", "이메일 입력", "검색창에 '노트북' 입력"
  * 나쁜 예: "로그인 처리", "데이터 검증", "시스템 확인"
- steps는 사용자가 실제로 수행하는 동작 순서대로 작성
- expected_result는 눈으로 확인 가능한 결과 (화면 변화, 텍스트 표시, URL 변경 등)
- 우선순위: MUST (핵심 기능) > SHOULD (주요 기능) > MAY (부가 기능)

### Steps 작성 가이드
- 클릭: "[버튼명] 버튼 클릭", "[링크명] 링크 클릭"
- 입력: "[필드명]에 [값] 입력" (예: "이메일에 test@test.com 입력")
- 키 입력: "Enter 키 입력", "Escape 키 입력"
- 확인: "[텍스트] 표시 확인", "[요소] 보이는지 확인"

### Output Format
{
  "checklist": [
    {
      "id": "TC001",
      "name": "기능명 (예: 로그인 성공)",
      "category": "authentication|navigation|search|cart|form|...",
      "priority": "MUST|SHOULD|MAY",
      "precondition": "시작 조건 (예: 로그아웃 상태)",
      "steps": [
        "로그인 버튼 클릭",
        "이메일에 test@test.com 입력",
        "비밀번호에 password123 입력",
        "로그인 버튼 클릭"
      ],
      "expected_result": "대시보드 페이지로 이동하고 환영 메시지 표시"
    }
  ],
  "summary": {
    "total": 10,
    "must": 5,
    "should": 3,
    "may": 2
  }
}

### IMPORTANT
- JSON만 출력, 다른 텍스트/설명/주석 금지
- steps는 반드시 문자열 배열
- 각 step은 구체적인 UI 동작 (추상적인 설명 금지)

### Document to analyze
{input_as_text}`,
  model: "gpt-5",
  modelSettings: {
    reasoning: {
      effort: "medium",
      summary: "auto"
    },
    store: true
  }
});

export interface WorkflowInput {
  input_as_text: string;
}

export interface WorkflowOutput {
  output_text: string;
}

// Main code entrypoint
export const runWorkflow = async (workflow: WorkflowInput): Promise<WorkflowOutput> => {
  return await withTrace("QA 도우미", async () => {
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

    if (!agentResultTemp.finalOutput) {
      throw new Error("Agent result is undefined");
    }

    console.log("Final output length:", agentResultTemp.finalOutput.length);

    const agentResult = {
      output_text: agentResultTemp.finalOutput ?? ""
    };

    return agentResult;
  });
};
