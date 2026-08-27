import { mkdir, readFile, rename, stat, writeFile } from "fs/promises";
import { homedir } from "os";
import path from "path";
import { getAgentDir } from "@earendil-works/pi-coding-agent";

type JsonObject = Record<string, unknown>;

export type McpTransport = "stdio" | "http" | "socket" | "unknown";
export type McpScope = "global" | "project";

export interface McpSourceView {
  id: string;
  label: string;
  path: string;
  scope: McpScope;
  editable: boolean;
  exists: boolean;
  serverCount: number;
  error?: string;
}

export interface McpServerView {
  name: string;
  enabled: boolean;
  transport: McpTransport;
  scope: McpScope;
  source: string;
  sourceLabel: string;
  sourceEditable: boolean;
  sourceCount: number;
  command?: string;
  urlHost?: string;
  lifecycle: string;
  auth: "none" | "bearer" | "oauth";
  hasEnv: boolean;
  hasHeaders: boolean;
  directTools: boolean | string[];
}

export interface McpConfigView {
  adapter: { installed: boolean; enabled: boolean; version?: string };
  projectOverridePath: string;
  sources: McpSourceView[];
  servers: McpServerView[];
  warnings: string[];
}

interface SourceSpec {
  id: string;
  label: string;
  path: string;
  scope: McpScope;
  editable: boolean;
}

interface LoadedSource extends SourceSpec {
  exists: boolean;
  servers: Record<string, JsonObject>;
  error?: string;
}

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseJsonConfig(text: string): unknown {
  let result = "";
  let inString = false;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]!;
    const next = text[index + 1];
    if (lineComment) {
      if (char === "\n" || char === "\r") {
        lineComment = false;
        result += char;
      } else {
        result += " ";
      }
      continue;
    }
    if (blockComment) {
      if (char === "*" && next === "/") {
        result += "  ";
        index += 1;
        blockComment = false;
      } else {
        result += char === "\n" || char === "\r" ? char : " ";
      }
      continue;
    }
    if (inString) {
      result += char;
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') {
      inString = true;
      result += char;
    } else if (char === "/" && next === "/") {
      result += "  ";
      index += 1;
      lineComment = true;
    } else if (char === "/" && next === "*") {
      result += "  ";
      index += 1;
      blockComment = true;
    } else {
      result += char;
    }
  }
  return JSON.parse(result);
}

function sourceSpecs(cwd: string): SourceSpec[] {
  const home = homedir();
  return [
    { id: "shared-global", label: "Standard MCP", path: path.join(home, ".config/mcp/mcp.json"), scope: "global", editable: false },
    { id: "agents-global", label: ".agents MCP", path: path.join(home, ".agents/mcp.json"), scope: "global", editable: false },
    { id: "agents-nested-global", label: ".agents nested MCP", path: path.join(home, ".agents/mcp/mcp.json"), scope: "global", editable: false },
    { id: "pi-global", label: "Pi global", path: path.join(getAgentDir(), "mcp.json"), scope: "global", editable: true },
    { id: "shared-project", label: "Project MCP", path: path.join(cwd, ".mcp.json"), scope: "project", editable: false },
    { id: "pi-project", label: "Pi project", path: path.join(cwd, ".pi/mcp.json"), scope: "project", editable: true },
  ];
}

async function loadSource(spec: SourceSpec): Promise<LoadedSource> {
  try {
    const text = await readFile(spec.path, "utf8");
    const parsed = parseJsonConfig(text);
    if (!isObject(parsed)) throw new Error("root value must be an object");
    const rawServers = parsed.mcpServers ?? parsed["mcp-servers"] ?? {};
    if (!isObject(rawServers)) throw new Error("mcpServers must be an object");
    const servers: Record<string, JsonObject> = {};
    for (const [name, value] of Object.entries(rawServers)) {
      if (isObject(value)) servers[name] = value;
    }
    return { ...spec, exists: true, servers };
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return { ...spec, exists: false, servers: {} };
    return {
      ...spec,
      exists: true,
      servers: {},
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function mergeEntry(base: JsonObject | undefined, next: JsonObject): JsonObject {
  const inherited = base ? { ...base } : {};
  if (typeof next.socket === "string") {
    for (const key of ["command", "args", "env", "cwd", "url", "headers", "auth", "bearerToken", "bearerTokenEnv", "oauth"]) {
      delete inherited[key];
    }
  } else if (typeof inherited.socket === "string" && (typeof next.command === "string" || typeof next.url === "string")) {
    delete inherited.socket;
  }
  if (typeof next.url === "string" && typeof inherited.url === "string" && next.url !== inherited.url) {
    for (const key of ["headers", "bearerToken", "bearerTokenEnv"]) delete inherited[key];
    if (inherited.oauth !== false) delete inherited.oauth;
  }
  return { ...inherited, ...next };
}

function transportOf(entry: JsonObject): McpTransport {
  if (typeof entry.socket === "string") return "socket";
  if (typeof entry.command === "string") return "stdio";
  if (typeof entry.url === "string") return "http";
  return "unknown";
}

function authOf(entry: JsonObject): McpServerView["auth"] {
  if (entry.auth === "oauth" || isObject(entry.oauth)) return "oauth";
  if (entry.auth === "bearer" || typeof entry.bearerToken === "string" || typeof entry.bearerTokenEnv === "string") return "bearer";
  return "none";
}

function directToolsOf(value: unknown): boolean | string[] {
  if (typeof value === "boolean") return value;
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === "string");
  return false;
}

function safeHost(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    return new URL(value).host;
  } catch {
    return undefined;
  }
}

async function getAdapterStatus(): Promise<McpConfigView["adapter"]> {
  const agentDir = getAgentDir();
  const packageJsonPath = path.join(agentDir, "npm/node_modules/pi-mcp-adapter/package.json");
  let installed = false;
  let version: string | undefined;
  try {
    const pkg = JSON.parse(await readFile(packageJsonPath, "utf8")) as JsonObject;
    installed = true;
    if (typeof pkg.version === "string") version = pkg.version;
  } catch { /* package is not installed in Pi's package directory */ }

  let enabled = false;
  try {
    const settings = JSON.parse(await readFile(path.join(agentDir, "settings.json"), "utf8")) as JsonObject;
    enabled = Array.isArray(settings.packages)
      && settings.packages.some((item) => typeof item === "string" && (item === "pi-mcp-adapter" || item === "npm:pi-mcp-adapter"));
  } catch { /* settings missing or malformed */ }
  return { installed, enabled, ...(version ? { version } : {}) };
}

export async function readMcpConfig(cwd: string): Promise<McpConfigView> {
  const sources = await Promise.all(sourceSpecs(cwd).map(loadSource));
  const merged = new Map<string, JsonObject>();
  const provenance = new Map<string, LoadedSource>();
  const appearances = new Map<string, number>();

  for (const source of sources) {
    for (const [name, entry] of Object.entries(source.servers)) {
      merged.set(name, mergeEntry(merged.get(name), entry));
      provenance.set(name, source);
      appearances.set(name, (appearances.get(name) ?? 0) + 1);
    }
  }

  const servers = [...merged.entries()].map(([name, entry]): McpServerView => {
    const source = provenance.get(name)!;
    const lifecycle = typeof entry.lifecycle === "string" ? entry.lifecycle : "lazy";
    const directTools = directToolsOf(entry.directTools);
    return {
      name,
      enabled: entry.disabled !== true,
      transport: transportOf(entry),
      scope: source.scope,
      source: source.path,
      sourceLabel: source.label,
      sourceEditable: source.editable,
      sourceCount: appearances.get(name) ?? 1,
      ...(typeof entry.command === "string" ? { command: entry.command } : {}),
      ...(safeHost(entry.url) ? { urlHost: safeHost(entry.url) } : {}),
      lifecycle,
      auth: authOf(entry),
      hasEnv: isObject(entry.env) && Object.keys(entry.env).length > 0,
      hasHeaders: isObject(entry.headers) && Object.keys(entry.headers).length > 0,
      directTools,
    };
  }).sort((a, b) => a.name.localeCompare(b.name));

  return {
    adapter: await getAdapterStatus(),
    projectOverridePath: path.join(cwd, ".pi/mcp.json"),
    sources: sources.map((source) => ({
      id: source.id,
      label: source.label,
      path: source.path,
      scope: source.scope,
      editable: source.editable,
      exists: source.exists,
      serverCount: Object.keys(source.servers).length,
      ...(source.error ? { error: source.error } : {}),
    })),
    servers,
    warnings: sources.filter((source) => source.error).map((source) => `${source.path}: ${source.error}`),
  };
}

export async function writeProjectMcpEnabled(cwd: string, serverName: string, enabled: boolean): Promise<boolean> {
  const filePath = path.join(cwd, ".pi/mcp.json");
  let raw: JsonObject = {};
  try {
    const parsed = parseJsonConfig(await readFile(filePath, "utf8"));
    if (!isObject(parsed)) throw new Error("root value must be an object");
    raw = parsed;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      throw new Error(`Failed to read ${filePath}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  const serverKey = raw.mcpServers !== undefined ? "mcpServers" : raw["mcp-servers"] !== undefined ? "mcp-servers" : "mcpServers";
  const serverValue = raw[serverKey] ?? {};
  if (!isObject(serverValue)) throw new Error(`${serverKey} must be an object`);
  const servers = { ...serverValue };
  const currentValue = servers[serverName];
  if (currentValue !== undefined && !isObject(currentValue)) throw new Error(`Server ${serverName} must be an object`);
  const current = isObject(currentValue) ? currentValue : {};
  const next = enabled
    ? Object.fromEntries(Object.entries(current).filter(([key]) => key !== "disabled"))
    : { ...current, disabled: true };
  if (enabled) {
    let lowerEntry: JsonObject | undefined;
    const lowerSources = await Promise.all(sourceSpecs(cwd)
      .filter((source) => source.id !== "pi-project")
      .map(loadSource));
    for (const source of lowerSources) {
      const entry = source.servers[serverName];
      if (entry) lowerEntry = mergeEntry(lowerEntry, entry);
    }
    if (lowerEntry?.disabled === true) next.disabled = false;
  }

  if (enabled && Object.keys(next).length === 0) delete servers[serverName];
  else servers[serverName] = next;
  const nextRaw = { ...raw, [serverKey]: servers };
  const before = JSON.stringify(raw);
  const after = JSON.stringify(nextRaw);
  if (before === after) return false;

  await mkdir(path.dirname(filePath), { recursive: true });
  const tmpPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(tmpPath, `${JSON.stringify(nextRaw, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  await rename(tmpPath, filePath);
  return true;
}

export async function isDirectory(cwd: string): Promise<boolean> {
  try {
    return (await stat(cwd)).isDirectory();
  } catch {
    return false;
  }
}
