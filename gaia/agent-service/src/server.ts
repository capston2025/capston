import express, { Request, Response } from "express";
import dotenv from "dotenv";
import { runWorkflow, WorkflowInput } from "./workflow";

// Load environment variables
dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(express.json({ limit: "10mb" }));

// Health check endpoint
app.get("/health", (req: Request, res: Response) => {
  res.json({ status: "ok", service: "agent-service" });
});

// Workflow execution endpoint
app.post("/api/analyze", async (req: Request, res: Response) => {
  try {
    const { input_as_text } = req.body as WorkflowInput;

    if (!input_as_text) {
      res.status(400).json({
        error: "Missing required field: input_as_text"
      });
      return;
    }

    console.log(`[${new Date().toISOString()}] Starting workflow analysis...`);
    console.log(`Input length: ${input_as_text.length} characters`);

    const result = await runWorkflow({ input_as_text });

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

// Start server
const server = app.listen(PORT, () => {
  console.log(`🚀 Agent service running on http://localhost:${PORT}`);
  console.log(`   Health check: http://localhost:${PORT}/health`);
  console.log(`   Analysis API: POST http://localhost:${PORT}/api/analyze`);
});

// Increase timeout for GPT-5 processing (default is 120s)
// GPT-5 can take 10-15 minutes for large documents (50+ pages)
server.timeout = 1500000; // 25 minutes in milliseconds
console.log(`   ⏱️  Server timeout: ${server.timeout / 1000}s (extended for GPT-5)`);

// Graceful shutdown
process.on("SIGTERM", () => {
  console.log("SIGTERM signal received: closing HTTP server");
  process.exit(0);
});

process.on("SIGINT", () => {
  console.log("SIGINT signal received: closing HTTP server");
  process.exit(0);
});
