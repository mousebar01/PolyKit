"use strict";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { parseArgs } = require("util");

const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);

function isEnabled(value) {
  return typeof value === "string" && TRUE_VALUES.has(value.trim().toLowerCase());
}

function defaultHostname(env, config) {
  if (env.POLYKIT_AGENT_NETWORK) {
    throw new Error("POLYKIT_AGENT_NETWORK has been removed; choose the access range in PolyKit Agent settings.");
  }
  return config?.network?.mode === "lan" ? "0.0.0.0" : "127.0.0.1";
}

function parseLaunchOptions(args = process.argv.slice(2), env = process.env, config) {
  const { values: cliArgs } = parseArgs({
    args,
    options: {
      port:      { type: "string", short: "p" },
      hostname:  { type: "string", short: "H" },
      "no-open": { type: "boolean" },
    },
    strict: false,
  });

  return {
    port: cliArgs.port ?? env.POLYKIT_AGENT_PORT ?? env.PORT ?? "30001",
    hostname: cliArgs.hostname ?? env.POLYKIT_AGENT_HOSTNAME ?? defaultHostname(env, config),
    openBrowser: !cliArgs["no-open"] && !isEnabled(env.POLYKIT_AGENT_NO_OPEN),
  };
}

module.exports = { defaultHostname, parseLaunchOptions };
