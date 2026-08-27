#!/usr/bin/env node
"use strict";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { getUnsupportedNodeVersionMessage, isNodeVersionSupported } = require("./node-version");

if (!isNodeVersionSupported(process.versions.node)) {
  console.error(getUnsupportedNodeVersionMessage(process.versions.node));
  process.exit(1);
}

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { spawn } = require("child_process");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const path = require("path");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { parseLaunchOptions } = require("./polykit-agent-options");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { startNextServer } = require("./polykit-agent-server");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { ensureAgentConfig, hasConfiguredCredential } = require("./polykit-agent-config.cjs");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { consumeAgentRestartRequest, getAgentRestartRequestPath } = require("./polykit-agent-restart.cjs");

const pkgDir = path.join(__dirname, "..");
const loopbackHostnames = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
let child = null;
let restarting = false;
let browserOpened = false;

function launch() {
  let bootstrap;
  try {
    bootstrap = ensureAgentConfig(process.env);
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  }
  let launchOptions;
  try {
    launchOptions = parseLaunchOptions(process.argv.slice(2), process.env, bootstrap.config);
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  }
  const { port, hostname, openBrowser } = launchOptions;
  const passwordEnabled = hasConfiguredCredential(process.env, bootstrap.config);

  if (!loopbackHostnames.has(hostname)) {
    if (passwordEnabled) {
      console.warn(
        `Warning: polykit-agent is listening on ${hostname} with Basic Auth over HTTP. Use HTTPS or a trusted VPN to protect the password in transit.`,
      );
    } else {
      console.warn(
        `Warning: polykit-agent is listening on ${hostname} without authentication. Only use this on a trusted network.`,
      );
    }
  }

  try {
    child = startNextServer({
      pkgDir,
      port,
      hostname,
      stdio: ["inherit", "pipe", "inherit"],
      env: { ...process.env, POLYKIT_AGENT_SUPERVISOR: "1" },
      detached: false,
    });
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  }

  const browserHost = hostname === "0.0.0.0" ? "127.0.0.1" : hostname;
  const url = `http://${browserHost}:${port}`;
  child.stdout.on("data", (chunk) => {
    const text = chunk.toString();
    process.stdout.write(text);
    if (openBrowser && !browserOpened && text.includes("Ready")) {
      browserOpened = true;
      const isWindows = process.platform === "win32";
      const isMac = process.platform === "darwin";
      const openCmd = isWindows ? "start" : isMac ? "open" : "xdg-open";
      const opener = spawn(openCmd, [url], {
        shell: isWindows,
        stdio: "ignore",
        detached: true,
      });
      opener.on("error", (error) => {
        console.warn(`Could not open browser automatically: ${error.message}`);
      });
      opener.unref();
    }
  });

  child.once("exit", (code, signal) => {
    child = null;
    if (restarting) {
      restarting = false;
      setTimeout(launch, 150);
      return;
    }
    if (signal) process.kill(process.pid, signal);
    else process.exit(code ?? 0);
  });
}

function restartChild() {
  if (!child || restarting) return;
  restarting = true;
  console.info(`PolyKit Agent 正在重启以应用新的监听范围（${getAgentRestartRequestPath()}）。`);
  child.kill();
}

// A stale marker is safe to discard when the launcher itself is starting.
consumeAgentRestartRequest();
const restartPoll = setInterval(() => {
  if (consumeAgentRestartRequest()) restartChild();
}, 250);

process.once("exit", () => clearInterval(restartPoll));
launch();
