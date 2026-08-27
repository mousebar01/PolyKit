import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { parseLaunchOptions } = require("../bin/polykit-agent-options.js");

test("opens the browser by default", () => {
  assert.deepEqual(parseLaunchOptions([], {}), {
    port: "30001",
    hostname: "127.0.0.1",
    openBrowser: true,
  });
});

test("supports the no-open CLI option", () => {
  assert.equal(parseLaunchOptions(["--no-open"], {}).openBrowser, false);
});

test("supports truthy POLYKIT_AGENT_NO_OPEN values", () => {
  for (const value of ["1", "true", "TRUE", "yes", "on"]) {
    assert.equal(parseLaunchOptions([], { POLYKIT_AGENT_NO_OPEN: value }).openBrowser, false);
  }
});

test("does not disable browser opening for false POLYKIT_AGENT_NO_OPEN values", () => {
  for (const value of ["0", "false", "off", ""]) {
    assert.equal(parseLaunchOptions([], { POLYKIT_AGENT_NO_OPEN: value }).openBrowser, true);
  }
});

test("preserves port and hostname options", () => {
  assert.deepEqual(
    parseLaunchOptions(["-p", "8080", "-H", "0.0.0.0"], {}),
    {
      port: "8080",
      hostname: "0.0.0.0",
      openBrowser: true,
    },
  );
});

test("prefers the dedicated port setting over the generic environment port", () => {
  assert.equal(parseLaunchOptions([], { POLYKIT_AGENT_PORT: "30002", PORT: "40000" }).port, "30002");
  assert.equal(parseLaunchOptions(["--port", "30003"], { POLYKIT_AGENT_PORT: "30002" }).port, "30003");
});

test("supports POLYKIT_AGENT_HOSTNAME without trusting the ambient system HOSTNAME", () => {
  assert.equal(
    parseLaunchOptions([], { HOSTNAME: "container-id" }).hostname,
    "127.0.0.1",
  );
  assert.equal(
    parseLaunchOptions([], { POLYKIT_AGENT_HOSTNAME: "0.0.0.0" }).hostname,
    "0.0.0.0",
  );
});

test("uses the persisted network mode and keeps credentials separate", () => {
  assert.equal(parseLaunchOptions([], {}, { network: { mode: "lan" } }).hostname, "0.0.0.0");
  assert.equal(parseLaunchOptions([], {}, { network: { mode: "local" } }).hostname, "127.0.0.1");
});

test("rejects the removed POLYKIT_AGENT_NETWORK entry point", () => {
  assert.throws(() => parseLaunchOptions([], { POLYKIT_AGENT_NETWORK: "lan" }), /POLYKIT_AGENT_NETWORK has been removed/);
});
