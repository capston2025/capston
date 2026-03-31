import path from "node:path";
import { fileURLToPath } from "node:url";
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..");
const openclawRoot = path.join(repoRoot, "vendor", "openclaw");
const runtimeRoot = path.join(repoRoot, "vendor", "openclaw-runtime");
const outfile = path.join(runtimeRoot, "gaia-embedded-browser-server.bundle.mjs");

const esbuildEntry = path.join(openclawRoot, "node_modules", "esbuild", "lib", "main.js");
try {
  await import(esbuildEntry);
} catch {
  console.error(
    "OpenClaw build dependencies are missing. Reinstall vendor/openclaw build deps before rebuilding the embedded bundle.",
  );
  process.exit(1);
}

const { build } = await import(esbuildEntry);

await build({
  entryPoints: [path.join(openclawRoot, "scripts", "gaia-embedded-browser-server.mjs")],
  outfile,
  bundle: true,
  format: "esm",
  platform: "node",
  target: ["node20"],
  banner: {
    js: 'import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);',
  },
  absWorkingDir: openclawRoot,
  alias: {
    "openclaw/plugin-sdk/browser-support": path.join(openclawRoot, "src", "plugin-sdk", "browser-support.ts"),
  },
  external: ["playwright-core", "sharp"],
  logLevel: "info",
});

console.log(outfile);
