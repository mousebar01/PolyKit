import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createJiti } from "jiti";

const jiti = createJiti(import.meta.url);
const { readMcpConfig, writeProjectMcpEnabled } = await jiti.import("./mcp-config.ts");

async function fixture() {
  const cwd = await mkdtemp(join(tmpdir(), "polykit-agent-mcp-"));
  await mkdir(join(cwd, ".pi"), { recursive: true });
  return cwd;
}

test("merges project sources by field and exposes only descriptive values", async () => {
  const cwd = await fixture();
  try {
    await writeFile(join(cwd, ".mcp.json"), JSON.stringify({
      mcpServers: {
        unique_test_server: {
          url: "https://secret.example.test/mcp",
          headers: { Authorization: "Bearer do-not-return" },
          env: { SECRET: "do-not-return" },
          lifecycle: "eager",
        },
      },
    }));
    await writeFile(join(cwd, ".pi/mcp.json"), JSON.stringify({
      mcpServers: { unique_test_server: { lifecycle: "lazy-keep-alive" } },
    }));

    const view = await readMcpConfig(cwd);
    const server = view.servers.find((entry) => entry.name === "unique_test_server");
    assert.ok(server);
    assert.equal(server.transport, "http");
    assert.equal(server.urlHost, "secret.example.test");
    assert.equal(server.lifecycle, "lazy-keep-alive");
    assert.equal(server.hasHeaders, true);
    assert.equal(server.hasEnv, true);
    assert.equal(server.sourceCount, 2);
    assert.equal(server.source, join(cwd, ".pi/mcp.json"));
    assert.doesNotMatch(JSON.stringify(view), /do-not-return/);
  } finally {
    await rm(cwd, { recursive: true, force: true });
  }
});

test("disable writes only a project override and leaves shared config unchanged", async () => {
  const cwd = await fixture();
  try {
    const shared = JSON.stringify({ mcpServers: { immutable_test_server: { command: "npx", args: ["secret-package"] } } }, null, 2);
    await writeFile(join(cwd, ".mcp.json"), shared);

    assert.equal(await writeProjectMcpEnabled(cwd, "immutable_test_server", false), true);
    assert.equal(await readFile(join(cwd, ".mcp.json"), "utf8"), shared);
    const override = JSON.parse(await readFile(join(cwd, ".pi/mcp.json"), "utf8"));
    assert.deepEqual(override, { mcpServers: { immutable_test_server: { disabled: true } } });
    assert.equal((await readMcpConfig(cwd)).servers.find((entry) => entry.name === "immutable_test_server").enabled, false);
  } finally {
    await rm(cwd, { recursive: true, force: true });
  }
});

test("enable writes an explicit false when the lower source is disabled", async () => {
  const cwd = await fixture();
  try {
    await writeFile(join(cwd, ".mcp.json"), JSON.stringify({
      mcpServers: { disabled_test_server: { command: "node", disabled: true } },
    }));

    assert.equal(await writeProjectMcpEnabled(cwd, "disabled_test_server", true), true);
    const override = JSON.parse(await readFile(join(cwd, ".pi/mcp.json"), "utf8"));
    assert.deepEqual(override, { mcpServers: { disabled_test_server: { disabled: false } } });
    assert.equal((await readMcpConfig(cwd)).servers.find((entry) => entry.name === "disabled_test_server").enabled, true);
  } finally {
    await rm(cwd, { recursive: true, force: true });
  }
});

test("accepts the commented JSON configuration supported by the adapter", async () => {
  const cwd = await fixture();
  try {
    await writeFile(join(cwd, ".mcp.json"), `{
      // Shared MCP clients commonly keep comments here.
      "mcpServers": {
        "commented_test_server": { "command": "node" }
      }
    }`);
    const server = (await readMcpConfig(cwd)).servers.find((entry) => entry.name === "commented_test_server");
    assert.equal(server?.transport, "stdio");
  } finally {
    await rm(cwd, { recursive: true, force: true });
  }
});
