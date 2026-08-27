import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname } from "node:path";

const DEFAULT_USERNAME = "pi";
export type AgentNetworkMode = "local" | "lan";

export interface AgentConfig {
  network: { mode: AgentNetworkMode };
  auth: { username: string; password: string | null };
}

export interface AgentConfigStatus {
  configured: boolean;
  source: "config" | "environment" | "none";
  passwordConfigured: boolean;
  passwordSource: "config" | "environment" | "none";
  password: string | null;
  username: string;
  networkMode: AgentNetworkMode;
}

function configPath(): string {
  return process.env.POLYKIT_AGENT_CONFIG_PATH
    || process.env.PI_POLYKIT_AGENT_CONFIG_PATH
    || `${homedir()}/.pi/agent/polykit-agent-config.json`;
}

export function validateUsername(username: unknown): string {
  if (typeof username !== "string" || username.length === 0 || username.length > 128 || /[:\u0000-\u001f\u007f]/.test(username)) {
    throw new Error("PolyKit Agent access username must be 1-128 characters without colon or control characters");
  }
  return username;
}

function assertEnvironment(): void {
  if (process.env.POLYKIT_AGENT_PASSWORD) {
    throw new Error("POLYKIT_AGENT_PASSWORD is no longer supported; put the password in a 0600 file and set POLYKIT_AGENT_PASSWORD_FILE.");
  }
  if (process.env.POLYKIT_AGENT_USERNAME !== undefined) validateUsername(process.env.POLYKIT_AGENT_USERNAME);
  if (process.env.POLYKIT_AGENT_PASSWORD_FILE) {
    const value = readFileSync(process.env.POLYKIT_AGENT_PASSWORD_FILE, "utf8").trim();
    if (!value) throw new Error(`POLYKIT_AGENT_PASSWORD_FILE is empty: ${process.env.POLYKIT_AGENT_PASSWORD_FILE}`);
  }
}

function validate(value: unknown, path: string): AgentConfig {
  if (!value || typeof value !== "object") throw new Error(`Invalid PolyKit Agent config at ${path}; remove the obsolete versioned config and create a new one`);
  const candidate = value as Partial<AgentConfig>;
  if ("version" in candidate) {
    throw new Error(`Invalid PolyKit Agent config at ${path}; the version field is no longer supported, remove this config and restart PolyKit Agent`);
  }
  if ((candidate.auth && "passwordHash" in candidate.auth) || "passwordHash" in candidate) {
    throw new Error(`Invalid PolyKit Agent config at ${path}; passwordHash is no longer supported, set a new password in PolyKit Agent settings`);
  }
  if (!candidate.auth || (candidate.auth.password !== null && typeof candidate.auth.password !== "string")) {
    throw new Error(`Invalid PolyKit Agent config at ${path}; expected network and auth fields without a version`);
  }
  const mode = candidate.network?.mode;
  if (mode !== "local" && mode !== "lan") throw new Error(`Invalid PolyKit Agent network mode at ${path}`);
  const username = validateUsername(candidate.auth.username);
  return {
    network: { mode },
    auth: { username, password: candidate.auth.password ?? null },
  };
}

export function getAgentConfigPath(): string {
  return configPath();
}

export function readAgentConfig(): AgentConfig | null {
  const path = configPath();
  if (!existsSync(path)) return null;
  try {
    return validate(JSON.parse(readFileSync(path, "utf8")), path);
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("Invalid PolyKit Agent config")) throw error;
    throw new Error(`Cannot read PolyKit Agent config at ${path}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function writeConfig(config: AgentConfig): void {
  const path = configPath();
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temporaryPath = `${path}.${process.pid}.tmp`;
  try {
    writeFileSync(temporaryPath, `${JSON.stringify(config, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
    chmodSync(temporaryPath, 0o600);
    renameSync(temporaryPath, path);
    chmodSync(path, 0o600);
  } finally {
    try { unlinkSync(temporaryPath); } catch { /* already renamed */ }
  }
}

function newConfig(): AgentConfig {
  return {
    network: { mode: "local" },
    auth: {
      username: validateUsername(process.env.POLYKIT_AGENT_USERNAME || DEFAULT_USERNAME),
      password: null,
    },
  };
}

export function ensureAgentConfig(): { config: AgentConfig; created: boolean } {
  assertEnvironment();
  const existing = readAgentConfig();
  if (existing) return { config: existing, created: false };
  const config = newConfig();
  writeConfig(config);
  return { config, created: true };
}

export function getEffectiveUsername(config = readAgentConfig()): string {
  return validateUsername(process.env.POLYKIT_AGENT_USERNAME || config?.auth.username || DEFAULT_USERNAME);
}

export function readExternalPassword(): string | null {
  assertEnvironment();
  if (!process.env.POLYKIT_AGENT_PASSWORD_FILE) return null;
  return readFileSync(process.env.POLYKIT_AGENT_PASSWORD_FILE, "utf8").trim();
}

export function setAgentPassword(password: string): AgentConfig {
  if (process.env.POLYKIT_AGENT_PASSWORD_FILE) {
    throw new Error("POLYKIT_AGENT_PASSWORD_FILE is configured; set the credential where it is managed.");
  }
  if (typeof password !== "string" || password.length < 12 || password.length > 512) {
    throw new Error("PolyKit Agent access password must be 12-512 characters");
  }
  const config = readAgentConfig() ?? newConfig();
  const nextConfig: AgentConfig = { ...config, auth: { ...config.auth, password } };
  writeConfig(nextConfig);
  return nextConfig;
}

export function updateAgentConfig(patch: { username?: string; networkMode?: AgentNetworkMode }): AgentConfig {
  const config = readAgentConfig() ?? newConfig();
  const username = patch.username === undefined ? config.auth.username : validateUsername(patch.username);
  const mode = patch.networkMode === undefined ? config.network.mode : patch.networkMode;
  if (mode !== "local" && mode !== "lan") throw new Error("PolyKit Agent network mode must be local or lan");
  const next: AgentConfig = {
    ...config,
    network: { mode },
    auth: { ...config.auth, username },
  };
  writeConfig(next);
  return next;
}

export function getAgentConfigStatus(): AgentConfigStatus {
  const configured = readAgentConfig();
  const source = process.env.POLYKIT_AGENT_PASSWORD_FILE || process.env.POLYKIT_AGENT_USERNAME ? "environment" : configured ? "config" : "none";
  const passwordConfigured = Boolean(process.env.POLYKIT_AGENT_PASSWORD_FILE || configured?.auth.password);
  const passwordSource = process.env.POLYKIT_AGENT_PASSWORD_FILE ? "environment" : passwordConfigured ? "config" : "none";
  return {
    configured: passwordConfigured,
    source,
    passwordConfigured,
    passwordSource,
    password: passwordSource === "config" ? configured?.auth.password ?? null : null,
    username: getEffectiveUsername(configured),
    networkMode: configured?.network.mode ?? "local",
  };
}
