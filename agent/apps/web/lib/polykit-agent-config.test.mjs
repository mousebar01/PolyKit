import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { ensureAgentConfig, readAgentConfig } = require("../bin/polykit-agent-config.cjs");

test("creates a local-only config without a schema version or generated password", () => {
  const directory = mkdtempSync(join(tmpdir(), "polykit-agent-config-"));
  const configPath = join(directory, "polykit-agent-config.json");
  try {
    const result = ensureAgentConfig({ POLYKIT_AGENT_CONFIG_PATH: configPath });
    assert.equal(result.created, true);
    assert.equal("version" in result.config, false);
    assert.equal(result.config.auth.username, "pi");
    assert.equal(result.config.network.mode, "local");
    assert.equal(result.config.auth.password, null);
    assert.equal(readFileSync(configPath, "utf8").includes("\"version\""), false);
    assert.equal(readFileSync(configPath, "utf8").includes("\"password\": null"), true);
    assert.equal(readFileSync(configPath, "utf8").includes("passwordHash"), false);
    if (process.platform !== "win32") assert.equal(statSync(configPath).mode & 0o777, 0o600);
    assert.deepEqual(readAgentConfig({ POLYKIT_AGENT_CONFIG_PATH: configPath }), result.config);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("uses POLYKIT_AGENT_USERNAME for a newly-created config and keeps external password separate", () => {
  const directory = mkdtempSync(join(tmpdir(), "polykit-agent-config-"));
  const configPath = join(directory, "polykit-agent-config.json");
  const passwordPath = join(directory, "password");
  writeFileSync(passwordPath, "deployment-secret\n", { mode: 0o600 });
  try {
    const result = ensureAgentConfig({ POLYKIT_AGENT_CONFIG_PATH: configPath, POLYKIT_AGENT_USERNAME: "operator", POLYKIT_AGENT_PASSWORD_FILE: passwordPath });
    assert.equal(result.config.auth.username, "operator");
    assert.equal(result.config.network.mode, "local");
    assert.deepEqual(readAgentConfig({ POLYKIT_AGENT_CONFIG_PATH: configPath }), result.config);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("rejects versioned configs and the removed plaintext password environment", () => {
  const directory = mkdtempSync(join(tmpdir(), "polykit-agent-config-"));
  const configPath = join(directory, "polykit-agent-config.json");
  try {
    writeFileSync(configPath, JSON.stringify({ version: 1, network: { mode: "lan" }, auth: { passwordHash: "x" } }));
    assert.throws(() => readAgentConfig({ POLYKIT_AGENT_CONFIG_PATH: configPath }), /version field is no longer supported/);
    writeFileSync(configPath, JSON.stringify({ network: { mode: "lan" }, auth: { username: "pi", passwordHash: "legacy" } }));
    assert.throws(() => readAgentConfig({ POLYKIT_AGENT_CONFIG_PATH: configPath }), /passwordHash is no longer supported/);
    assert.throws(() => ensureAgentConfig({ POLYKIT_AGENT_CONFIG_PATH: join(directory, "new.json"), POLYKIT_AGENT_PASSWORD: "secret" }), /POLYKIT_AGENT_PASSWORD is no longer supported/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
