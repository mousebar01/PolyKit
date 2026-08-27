import { existsSync, mkdirSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { getAgentConfigPath } from "./polykit-agent-config";

export function getAgentRestartRequestPath(): string {
  return process.env.POLYKIT_AGENT_RESTART_PATH || join(dirname(getAgentConfigPath()), "polykit-agent-restart.request");
}

/** Ask the outer polykit-agent launcher to restart its Next.js child process. */
export function requestAgentRestart(): void {
  const requestPath = getAgentRestartRequestPath();
  mkdirSync(dirname(requestPath), { recursive: true, mode: 0o700 });
  const temporaryPath = `${requestPath}.${process.pid}.tmp`;
  try {
    writeFileSync(temporaryPath, `${JSON.stringify({ requestedAt: new Date().toISOString() })}\n`, { encoding: "utf8", mode: 0o600 });
    renameSync(temporaryPath, requestPath);
  } finally {
    try { unlinkSync(temporaryPath); } catch { /* already renamed */ }
  }
}

export function isAgentRestartSupported(): boolean {
  return process.env.POLYKIT_AGENT_SUPERVISOR === "1";
}

export function consumeAgentRestartRequest(): boolean {
  const requestPath = getAgentRestartRequestPath();
  if (!existsSync(requestPath)) return false;
  try {
    unlinkSync(requestPath);
    return true;
  } catch {
    return false;
  }
}
