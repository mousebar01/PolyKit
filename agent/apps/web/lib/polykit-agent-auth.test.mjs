import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createJiti } from "jiti";

const jiti = createJiti(import.meta.url);

test("keeps a new config unauthenticated until a custom password is set", async () => {
  const directory = mkdtempSync(join(tmpdir(), "polykit-agent-auth-"));
  const previousPath = process.env.POLYKIT_AGENT_CONFIG_PATH;
  process.env.POLYKIT_AGENT_CONFIG_PATH = join(directory, "polykit-agent-config.json");
  try {
    const config = await jiti.import("./polykit-agent-config.ts");
    const auth = await jiti.import("./web-auth.ts");
    config.ensureAgentConfig();
    config.updateAgentConfig({ username: "operator" });
    assert.equal(config.getAgentConfigStatus().passwordConfigured, false);
    assert.equal(auth.isWebPasswordEnabled(), false);
    config.setAgentPassword("custom-password-123");
    assert.equal(config.getAgentConfigStatus().passwordConfigured, true);
    assert.equal(config.getAgentConfigStatus().passwordSource, "config");
    assert.equal(config.getAgentConfigStatus().password, "custom-password-123");
    assert.equal(auth.isValidBasicAuthorization(`Basic ${Buffer.from("operator:custom-password-123", "utf8").toString("base64")}`), true);
    assert.equal(auth.isWebPasswordEnabled(), true);
    assert.equal(auth.isValidBasicAuthorization("Basic invalid"), false);
  } finally {
    if (previousPath === undefined) delete process.env.POLYKIT_AGENT_CONFIG_PATH;
    else process.env.POLYKIT_AGENT_CONFIG_PATH = previousPath;
    rmSync(directory, { recursive: true, force: true });
  }
});
