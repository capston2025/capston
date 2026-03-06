import fs from "fs";
import path from "path";
import type {
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from "@playwright/test/reporter";

type RailCase = {
  id: string;
  title: string;
  status: string;
  duration_ms: number;
  error: string;
};

class RailReporter implements Reporter {
  private readonly startedAt = Date.now();
  private readonly cases: RailCase[] = [];

  onTestEnd(test: TestCase, result: TestResult): void {
    const status = String(result.status || "unknown");
    const err = result.error ? String(result.error.message || result.error.value || "") : "";
    this.cases.push({
      id: test.id,
      title: test.titlePath().join(" > "),
      status,
      duration_ms: Number(result.duration || 0),
      error: err,
    });
  }

  onEnd(result: FullResult): void {
    const outDir =
      process.env.GAIA_RAIL_ARTIFACT_DIR ||
      path.resolve(process.cwd(), "../artifacts/validation-rail/latest");
    fs.mkdirSync(outDir, { recursive: true });

    const total = this.cases.length;
    const passed = this.cases.filter((c) => c.status === "passed").length;
    const failed = this.cases.filter((c) => c.status === "failed" || c.status === "timedOut").length;
    const skipped = this.cases.filter((c) => c.status === "skipped" || c.status === "interrupted").length;
    const durationMs = Date.now() - this.startedAt;
    const overallStatus = failed > 0 ? "failed" : "passed";

    const summary = {
      schema_version: "gaia.validation.rail.v1",
      status: overallStatus,
      total,
      passed,
      failed,
      skipped,
      duration_ms: durationMs,
      playwright_status: result.status,
      generated_at: Math.floor(Date.now() / 1000),
    };

    fs.writeFileSync(path.join(outDir, "summary.json"), JSON.stringify(summary, null, 2), "utf-8");
    fs.writeFileSync(path.join(outDir, "cases.json"), JSON.stringify(this.cases, null, 2), "utf-8");
  }
}

export default RailReporter;
