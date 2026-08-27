"use strict";

/* eslint-disable @typescript-eslint/no-require-imports */
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

function resolveNextBin(pkgDir) {
  try {
    return require.resolve("next/dist/bin/next", { paths: [pkgDir] });
  } catch {
    try {
      const nextPkg = require.resolve("next/package.json", { paths: [pkgDir] });
      return path.join(path.dirname(nextPkg), "dist", "bin", "next");
    } catch {
      return path.join(pkgDir, "node_modules", "next", "dist", "bin", "next");
    }
  }
}

function startNextServer(options) {
  const {
    pkgDir,
    port,
    hostname = "127.0.0.1",
    nodeExecutable = process.execPath,
    env = process.env,
    stdio = ["ignore", "pipe", "pipe"],
    detached = process.platform !== "win32",
  } = options;
  const nextDir = path.join(pkgDir, ".next");

  if (!fs.existsSync(nextDir)) {
    throw new Error("Build artifacts not found. Run the polykit-agent build first.");
  }

  const child = spawn(
    nodeExecutable,
    [resolveNextBin(pkgDir), "start", "-p", String(port), "-H", hostname],
    {
      cwd: pkgDir,
      stdio,
      detached,
      env: { ...env, POLYKIT_AGENT_HOSTNAME: hostname },
    },
  );

  return child;
}

module.exports = {
  resolveNextBin,
  startNextServer,
};
