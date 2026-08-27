import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createJiti } from "jiti";

const jiti = createJiti(import.meta.url);

test("writes and consumes a restart request through the shared config directory", async () => {
  const directory = mkdtempSync(join(tmpdir(), "polykit-agent-restart-"));
  const previousConfig = process.env.POLYKIT_AGENT_CONFIG_PATH;
  const previousRestart = process.env.POLYKIT_AGENT_RESTART_PATH;
  process.env.POLYKIT_AGENT_CONFIG_PATH = join(directory, "polykit-agent-config.json");
  delete process.env.POLYKIT_AGENT_RESTART_PATH;
  try {
    const restart = await jiti.import("./polykit-agent-restart.ts");
    restart.requestAgentRestart();
    const requestPath = restart.getAgentRestartRequestPath();
    assert.equal(JSON.parse(readFileSync(requestPath, "utf8")).requestedAt !== undefined, true);
    assert.equal(restart.consumeAgentRestartRequest(), true);
  } finally {
    if (previousConfig === undefined) delete process.env.POLYKIT_AGENT_CONFIG_PATH;
    else process.env.POLYKIT_AGENT_CONFIG_PATH = previousConfig;
    if (previousRestart === undefined) delete process.env.POLYKIT_AGENT_RESTART_PATH;
    else process.env.POLYKIT_AGENT_RESTART_PATH = previousRestart;
    rmSync(directory, { recursive: true, force: true });
  }
});
