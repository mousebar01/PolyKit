/* eslint-disable @typescript-eslint/no-require-imports */
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function getAgentConfigPath(env = process.env) {
  return env.POLYKIT_AGENT_CONFIG_PATH
    || env.PI_POLYKIT_AGENT_CONFIG_PATH
    || path.join(os.homedir(), ".pi", "agent", "polykit-agent-config.json");
}

function getAgentRestartRequestPath(env = process.env) {
  return env.POLYKIT_AGENT_RESTART_PATH || path.join(path.dirname(getAgentConfigPath(env)), "polykit-agent-restart.request");
}

function consumeAgentRestartRequest(env = process.env) {
  const requestPath = getAgentRestartRequestPath(env);
  if (!fs.existsSync(requestPath)) return false;
  try {
    fs.unlinkSync(requestPath);
    return true;
  } catch {
    return false;
  }
}

module.exports = { consumeAgentRestartRequest, getAgentRestartRequestPath };
