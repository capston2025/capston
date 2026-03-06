import { defineConfig } from "@playwright/test";
import path from "path";

const artifactDir =
  process.env.GAIA_RAIL_ARTIFACT_DIR ||
  path.resolve(__dirname, "../artifacts/validation-rail/latest");

export default defineConfig({
  testDir: path.resolve(__dirname, "tests"),
  timeout: 45_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [
    ["line"],
    [path.resolve(__dirname, "src/railReporter.ts")],
  ],
  outputDir: path.join(artifactDir, "pw-output"),
  use: {
    baseURL: process.env.GAIA_RAIL_BASE_URL || "https://inuu-timetable.vercel.app/",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
