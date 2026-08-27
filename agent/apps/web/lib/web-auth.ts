import { getEffectiveUsername, readExternalPassword, readAgentConfig } from "./polykit-agent-config";

export const DEFAULT_WEB_AUTH_USERNAME = "pi";

function secretsEqual(actual: string, expected: string): boolean {
  const actualBytes = Buffer.from(actual, "utf8");
  const expectedBytes = Buffer.from(expected, "utf8");
  const length = Math.max(actualBytes.length, expectedBytes.length);
  let difference = actualBytes.length ^ expectedBytes.length;
  for (let index = 0; index < length; index += 1) {
    difference |= (actualBytes[index] ?? 0) ^ (expectedBytes[index] ?? 0);
  }
  return difference === 0;
}

type WebAuthCredential =
  { source: "config" | "environment"; username: string; value: string };

function configuredCredential(): WebAuthCredential | null {
  const config = readAgentConfig();
  const externalPassword = readExternalPassword();
  if (externalPassword !== null) {
    return { source: "environment", username: getEffectiveUsername(config), value: externalPassword };
  }
  if (!config) return null;
  if (!config.auth.password) return null;
  return { source: process.env.POLYKIT_AGENT_USERNAME ? "environment" : "config", username: getEffectiveUsername(config), value: config.auth.password };
}

export function isWebPasswordEnabled(password?: string): boolean {
  if (arguments.length > 0) return typeof password === "string" && password.length > 0;
  return configuredCredential() !== null;
}

export function getWebAuthStatus(): { configured: boolean; source: "environment" | "config" | "none"; username: string } {
  const credential = configuredCredential();
  return {
    configured: credential !== null,
    source: credential?.source ?? "none",
    username: credential?.username ?? DEFAULT_WEB_AUTH_USERNAME,
  };
}

export function isValidBasicAuthorization(
  authorization: string | null,
  password?: string,
  username?: string,
): boolean {
  const credential = arguments.length > 1
    ? (password ? { username: username || DEFAULT_WEB_AUTH_USERNAME, value: password } : null)
    : configuredCredential();
  if (!credential || !authorization) return false;

  const match = /^Basic\s+(\S+)$/i.exec(authorization);
  if (!match) return false;

  let credentials: string;
  try {
    const decoded = Buffer.from(match[1], "base64");
    if (decoded.toString("base64") !== match[1]) return false;
    credentials = new TextDecoder("utf-8", { fatal: true }).decode(decoded);
  } catch {
    return false;
  }

  const separator = credentials.indexOf(":");
  if (separator === -1) return false;

  const suppliedUsername = credentials.slice(0, separator);
  const suppliedPassword = credentials.slice(separator + 1);
  const usernameMatches = secretsEqual(suppliedUsername, credential.username);
  const passwordMatches = secretsEqual(suppliedPassword, credential.value);
  return usernameMatches && passwordMatches;
}
