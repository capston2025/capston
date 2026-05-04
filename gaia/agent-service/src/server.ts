import dotenv from "dotenv";
import path from "node:path";

// 환경 변수를 먼저 로드한 뒤 나머지 모듈을 import 해야 workflow.ts에서 접근 가능
const defaultEnvPath = path.resolve(__dirname, "../../.env");
dotenv.config({ path: defaultEnvPath });
dotenv.config();

import express, { Request, Response } from "express";
import { runWorkflow, WorkflowInput } from "./workflow";

const app = express();
const PORT = process.env.PORT || 3000;

// 미들웨어 설정
app.use(express.json({ limit: "10mb" }));

// 헬스 체크 엔드포인트
app.get("/health", (req: Request, res: Response) => {
  res.json({ status: "ok", service: "agent-service" });
});

// 워크플로 실행 엔드포인트
app.post("/api/analyze", async (req: Request, res: Response) => {
  try {
    const { input_as_text, feature_query, base_url } = req.body as WorkflowInput;

    if (!input_as_text) {
      res.status(400).json({
        error: "Missing required field: input_as_text"
      });
      return;
    }

    console.log(`[${new Date().toISOString()}] Starting workflow analysis...`);
    console.log(`Input length: ${input_as_text.length} characters`);
    if (feature_query) {
      console.log(`Feature query: "${feature_query}"`);
    }
    if (base_url) {
      console.log(`Base URL: "${base_url}"`);
    }

    const result = await runWorkflow({ input_as_text, feature_query, base_url });

    console.log(`[${new Date().toISOString()}] Workflow completed successfully`);

    res.json({
      success: true,
      data: result
    });
  } catch (error: any) {
    console.error(`[${new Date().toISOString()}] Workflow error:`, error);
    res.status(500).json({
      success: false,
      error: error.message || "Internal server error"
    });
  }
});

// 서버 시작
const server = app.listen(PORT, () => {
  console.log(`🚀 Agent service running on http://localhost:${PORT}`);
  console.log(`   Health check: http://localhost:${PORT}/health`);
  console.log(`   Analysis API: POST http://localhost:${PORT}/api/analyze`);
});

// GPT-5 처리를 위해 타임아웃을 확장합니다 (기본값 120초)
// 대형 문서(50페이지 이상)는 GPT-5가 10~15분이 걸릴 수 있습니다
server.timeout = 1500000; // 25 minutes in milliseconds
console.log(`   ⏱️  Server timeout: ${server.timeout / 1000}s (extended for GPT-5)`);

// 정상 종료 처리
process.on("SIGTERM", () => {
  console.log("SIGTERM signal received: closing HTTP server");
  process.exit(0);
});

process.on("SIGINT", () => {
  console.log("SIGINT signal received: closing HTTP server");
  process.exit(0);
});
