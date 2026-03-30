import { startBrowserControlServerFromConfig, stopBrowserControlServer } from "../extensions/browser/src/server.ts";

const started = await startBrowserControlServerFromConfig();
if (!started) {
  console.error("[gaia/openclaw] browser control server did not start");
  process.exit(1);
}

let stopping = false;

const shutdown = async (code = 0) => {
  if (stopping) {
    return;
  }
  stopping = true;
  try {
    await stopBrowserControlServer();
  } catch (err) {
    console.error(`[gaia/openclaw] shutdown failed: ${String(err)}`);
  }
  process.exit(code);
};

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    void shutdown(0);
  });
}

process.on("uncaughtException", (err) => {
  console.error(`[gaia/openclaw] uncaught exception: ${String(err)}`);
  void shutdown(1);
});

process.on("unhandledRejection", (reason) => {
  console.error(`[gaia/openclaw] unhandled rejection: ${String(reason)}`);
  void shutdown(1);
});

await new Promise(() => {});
