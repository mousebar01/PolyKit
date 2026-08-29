/**
 * Internal Agent runtime sidecar.
 *
 * The browser never talks to this process directly. FastAPI owns the public
 * /agent contract and forwards requests here over loopback with a one-time
 * token. Keeping the pi session lifecycle here lets us reuse the proven
 * AgentSessionWrapper without mounting the old Next.js application.
 */
import http, { type IncomingMessage, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";
import { chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative, resolve } from "node:path";

import { createAgentSessionServices, DefaultPackageManager, getAgentDir, ModelRuntime, parseFrontmatter, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { completeSimple, type AssistantMessage } from "@earendil-works/pi-ai/compat";
import { getSupportedThinkingLevels } from "@earendil-works/pi-ai";
import {
  getRpcSession,
  getRunningRpcSessionIds,
  startRpcSession,
  subscribeRunningSessions,
  type AgentEvent,
} from "../apps/web/lib/rpc-manager";
import {
  buildSessionContext,
  listAllSessions,
  resolveSessionPath,
  resolveSessionIdByPath,
} from "../apps/web/lib/session-reader";
import { resolveVisibleModels, selectInitialModelScope } from "../apps/web/lib/model-scope";
import { projectTrustReloadOptions } from "../apps/web/lib/project-trust";
import { getHiddenWorkspaceRecords, getSessionArchiveRecords, removeSessionArchiveRecord, setSessionsArchived, setWorkspaceHidden } from "../apps/web/lib/session-archive";
import { loadSkillsWithInstallInfo } from "../apps/web/lib/skills-service";
import { readMcpConfig, writeProjectMcpEnabled } from "../apps/web/lib/mcp-config";
import { collectProviderListingInputs } from "../apps/web/lib/provider-listing-runtime";
import { buildApiKeyProviderList, buildOAuthProviderList } from "../apps/web/lib/provider-listing";
import { removeStoredCredentialIfType, storeProviderCredential } from "../apps/web/lib/provider-credential-store";
import { invalidateModelsCache } from "../apps/web/lib/models-cache";
import { configureHttpDispatcher } from "../apps/web/lib/http-dispatcher";
import { resolveModelDiscoveryAuth } from "../apps/web/lib/model-discovery-auth";
import { buildModelsListUrl, parseDiscoveredModels } from "../apps/web/lib/model-discovery";
import {
  flattenModelsDevCatalog,
  recommendModelCatalogPreset,
  searchModelCatalog,
  type ModelCatalogEntry,
} from "../apps/web/lib/model-catalog";

const PORT = Number(process.env.POLYKIT_AGENT_PORT ?? "0");
const TOKEN = process.env.POLYKIT_AGENT_INTERNAL_TOKEN ?? "";
const WORKSPACE_ROOT = resolve(process.env.POLYKIT_WORKSPACE_DIR ?? process.cwd());
const SESSION_DIR = resolve(process.env.PI_CODING_AGENT_SESSION_DIR ?? resolve(getAgentDir(), "sessions"));

const THINKING_LEVELS = new Set(["off", "minimal", "low", "medium", "high", "xhigh", "max"]);

type PendingAuthInput = {
  provider: string;
  resolve: (value: string) => void;
  reject: (error: Error) => void;
};

const pendingAuthInputs = new Map<string, PendingAuthInput>();

// The embedded sidecar does not pass through the Agent CLI entry point, so it
// must install the same undici proxy dispatcher explicitly. Without this,
// provider OAuth/API requests use Node's direct fetch path and ignore the
// proxy configured for the server process.
configureHttpDispatcher();

function isWithinRoot(target: string, root: string): boolean {
  const rel = relative(root, target);
  return rel === "" || (rel !== ".." && !rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) && !rel.startsWith("/"));
}

function json(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(Buffer.from(chunk));
  if (chunks.length === 0) return {};
  const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Request body must be an object");
  return parsed as Record<string, unknown>;
}

function decodeId(value: string): string {
  return decodeURIComponent(value);
}

function validateCwd(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new Error("cwd is required");
  const cwd = resolve(value);
  if (!existsSync(cwd)) throw new Error(`Directory does not exist: ${cwd}`);
  if (!isWithinRoot(cwd, WORKSPACE_ROOT)) throw new Error("Agent sessions must stay inside the PolyKit workspace");
  return cwd;
}

function parseThinkingLevel(value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value === "string" && THINKING_LEVELS.has(value)) return value;
  throw new Error(`Invalid thinking level: ${String(value)}`);
}

async function ensureSession(id: string): Promise<ReturnType<typeof getRpcSession>> {
  let session = getRpcSession(id);
  if (session?.isAlive()) return session;
  const filePath = await resolveSessionPath(id);
  if (!filePath) return undefined;
  ({ session } = await startRpcSession(id, filePath, undefined));
  return session;
}

async function createSession(body: Record<string, unknown>): Promise<unknown> {
  const cwd = validateCwd(body.cwd);
  const { provider, modelId, toolNames, thinkingLevel, ...promptCommand } = body;
  if ((provider && !modelId) || (!provider && modelId)) throw new Error("provider and modelId must be provided together");
  const explicitThinkingLevel = parseThinkingLevel(thinkingLevel);
  if (toolNames !== undefined && (!Array.isArray(toolNames) || toolNames.some((item) => typeof item !== "string"))) {
    throw new Error("toolNames must be an array of strings");
  }

  const tempKey = `__new__${randomUUID()}`;
  const { session, realSessionId } = await startRpcSession(tempKey, "", cwd, {
    sessionDir: SESSION_DIR,
    ...(Array.isArray(toolNames) ? { toolNames: toolNames as string[] } : {}),
    ...(typeof provider === "string" && typeof modelId === "string" ? { initialModel: { provider, modelId } } : {}),
    ...(explicitThinkingLevel ? { thinkingLevel: explicitThinkingLevel as never } : {}),
  });
  const state = await session.send({ type: "get_state" }) as { model?: { id: string; provider: string }; thinkingLevel?: string };

  if (promptCommand.type === "ensure_session") {
    return {
      success: true,
      sessionId: realSessionId,
      data: null,
      model: state.model ? { provider: state.model.provider, modelId: state.model.id } : null,
      thinkingLevel: state.thinkingLevel,
    };
  }
  const data = await session.send(promptCommand);
  return {
    success: true,
    sessionId: realSessionId,
    data,
    model: state.model ? { provider: state.model.provider, modelId: state.model.id } : null,
    thinkingLevel: state.thinkingLevel,
  };
}

async function sendCommand(id: string, body: Record<string, unknown>): Promise<unknown> {
  const session = await ensureSession(id);
  if (!session) throw Object.assign(new Error("Session not found"), { statusCode: 404 });
  return { success: true, data: await session.send(body) };
}

async function sessionState(id: string): Promise<unknown> {
  if (!await resolveSessionPath(id)) throw Object.assign(new Error("Session not found"), { statusCode: 404 });
  const session = getRpcSession(id);
  if (!session?.isAlive()) return { running: false };
  return { running: true, state: await session.send({ type: "get_state" }) };
}

async function sessionContext(id: string, query: URLSearchParams): Promise<unknown> {
  const filePath = await resolveSessionPath(id);
  if (!filePath) throw Object.assign(new Error("Session not found"), { statusCode: 404 });
  const live = getRpcSession(id);
  const sm = live?.isAlive() ? live.inner.sessionManager : SessionManager.open(filePath);
  return {
    context: buildSessionContext(sm.getEntries() as never, query.get("leafId"), {
      deferThinking: query.has("deferThinking"),
      deferToolResultImages: query.has("deferMedia"),
    }),
  };
}

async function sessionDetails(id: string, query: URLSearchParams): Promise<unknown> {
  const filePath = await resolveSessionPath(id);
  if (!filePath) throw Object.assign(new Error("Session not found"), { statusCode: 404 });
  const live = getRpcSession(id);
  const sm = live?.isAlive() ? live.inner.sessionManager : SessionManager.open(filePath);
  const context = buildSessionContext(sm.getEntries() as never, sm.getLeafId(), {
    deferThinking: query.has("deferThinking"),
    deferToolResultImages: query.has("deferMedia"),
  });
  const header = sm.getHeader();
  const parentSessionId = header?.parentSession ? await resolveSessionIdByPath(header.parentSession) : undefined;
  return {
    sessionId: id,
    filePath,
    info: header ? {
      path: filePath,
      id: header.id,
      cwd: header.cwd ?? "",
      name: sm.getSessionName(),
      created: header.timestamp,
      modified: header.timestamp,
      messageCount: context.messages.length,
      firstMessage: "(no messages)",
      ...(parentSessionId ? { parentSessionId } : {}),
    } : null,
    leafId: sm.getLeafId(),
    tree: sm.getTree(),
    context,
  };
}

function bashOutputPath(value: string | null): string | null {
  if (!value) return null;
  const resolved = resolve(value);
  if (dirname(resolved) !== resolve(tmpdir())) return null;
  if (!/^pi-bash-[A-Za-z0-9_-]+\.log$/.test(basename(resolved))) return null;
  return resolved;
}

async function bashOutputIsReferenced(id: string, path: string): Promise<boolean> {
  const filePath = await resolveSessionPath(id);
  const live = getRpcSession(id);
  const entries = live?.isAlive()
    ? live.inner.sessionManager.getEntries()
    : filePath && existsSync(filePath) ? SessionManager.open(filePath).getEntries() : [];
  return entries.some((entry) => {
    const message = (entry as { type?: string; message?: { role?: string; fullOutputPath?: string } }).message;
    return entry.type === "message" && message?.role === "bashExecution" && message.fullOutputPath === path;
  });
}

async function bashOutput(id: string, query: URLSearchParams): Promise<unknown> {
  const path = bashOutputPath(query.get("path"));
  if (!path) throw Object.assign(new Error("Invalid path"), { statusCode: 400 });
  if (!await bashOutputIsReferenced(id, path)) throw Object.assign(new Error("Forbidden"), { statusCode: 403 });
  try {
    const stat = lstatSync(path);
    if (!stat.isFile() || stat.size > 5 * 1024 * 1024) {
      throw Object.assign(new Error("Full output is too large to display"), { statusCode: 413 });
    }
    return { success: true, data: { output: readFileSync(path, "utf8") } };
  } catch (error) {
    if (typeof error === "object" && error && "statusCode" in error) throw error;
    throw Object.assign(new Error("Full output unavailable"), { statusCode: 404 });
  }
}

async function models(cwdValue: string | null): Promise<unknown> {
  const cwd = validateCwd(cwdValue ?? WORKSPACE_ROOT);
  const agentDir = getAgentDir();
  const services = await createAgentSessionServices({
    cwd,
    agentDir,
    ...(projectTrustReloadOptions(cwd, agentDir) ? { resourceLoaderReloadOptions: projectTrustReloadOptions(cwd, agentDir) } : {}),
  });
  const scope = await resolveVisibleModels(services.modelRuntime, services.settingsManager.getEnabledModels());
  const modelList = scope.visible.map((model) => ({ id: model.id, name: model.name, provider: model.provider }));
  const modelsByKey = Object.fromEntries(modelList.map((model) => [`${model.provider}:${model.id}`, model.name]));
  const defaultProvider = services.settingsManager.getDefaultProvider();
  const defaultModelId = services.settingsManager.getDefaultModel();
  const initial = selectInitialModelScope(scope, {
    ...(defaultProvider && defaultModelId ? { defaultModel: { provider: defaultProvider, modelId: defaultModelId } } : {}),
  });
  const thinkingLevels: Record<string, string[]> = {};
  for (const model of scope.visible) thinkingLevels[`${model.provider}:${model.id}`] = getSupportedThinkingLevels(model);
  return {
    models: modelsByKey,
    modelList,
    defaultModel: initial.model ? { provider: initial.model.provider, modelId: initial.model.id } : null,
    thinkingLevels,
    thinkingLevelMaps: {},
    thinkingLevelPins: scope.thinkingLevelPins,
  };
}

function readModelsConfig(): Record<string, unknown> {
  const filePath = join(getAgentDir(), "models.json");
  if (!existsSync(filePath)) return { providers: {} };
  try {
    const parsed = JSON.parse(readFileSync(filePath, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : { providers: {} };
  } catch {
    return { providers: {} };
  }
}

function writeModelsConfig(value: unknown): void {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("models config must be an object");
  const agentDir = getAgentDir();
  mkdirSync(agentDir, { recursive: true, mode: 0o700 });
  const filePath = join(agentDir, "models.json");
  writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  chmodSync(filePath, 0o600);
}

function buildModelDiscoveryHeaders(api: string, apiKey: string | undefined, configured: Record<string, string>): Headers {
  const headers = new Headers(configured);
  if (!headers.has("accept")) headers.set("Accept", "application/json");
  if (!apiKey) return headers;
  if (api === "anthropic-messages") {
    if (!headers.has("x-api-key")) headers.set("x-api-key", apiKey);
    if (!headers.has("anthropic-version")) headers.set("anthropic-version", "2023-06-01");
  } else if (api === "google-generative-ai") {
    if (!headers.has("x-goog-api-key")) headers.set("x-goog-api-key", apiKey);
  } else if (!headers.has("authorization")) {
    headers.set("Authorization", `Bearer ${apiKey}`);
  }
  return headers;
}

async function discoverProviderModels(req: IncomingMessage): Promise<unknown> {
  const body = await readJson(req);
  const providerName = typeof body.providerName === "string" ? body.providerName.trim() : "";
  if (!providerName) throw Object.assign(new Error("providerName is required"), { statusCode: 400 });
  if (!isRecord(body.provider)) throw Object.assign(new Error("provider is required"), { statusCode: 400 });

  const baseUrl = typeof body.provider.baseUrl === "string" ? body.provider.baseUrl.trim() : "";
  if (!baseUrl) throw Object.assign(new Error("Base URL is required"), { statusCode: 400 });
  const api = typeof body.provider.api === "string" && body.provider.api
    ? body.provider.api
    : "openai-completions";
  let endpoint: URL;
  try {
    endpoint = buildModelsListUrl(baseUrl, api);
  } catch {
    throw Object.assign(new Error("Base URL is invalid"), { statusCode: 400 });
  }

  const auth = await resolveModelDiscoveryAuth(providerName, body.provider);
  if (typeof body.provider.apiKey === "string" && body.provider.apiKey.trim() && !auth.apiKey) {
    throw Object.assign(new Error(`No API key found for "${providerName}"`), { statusCode: 400 });
  }

  try {
    const response = await fetch(endpoint, {
      headers: buildModelDiscoveryHeaders(api, auth.apiKey, auth.headers),
      signal: AbortSignal.timeout(20_000),
    });
    const responseText = await response.text();
    if (!response.ok) {
      throw Object.assign(
        new Error(responseText.slice(0, 500) || `Upstream returned HTTP ${response.status}`),
        { statusCode: 502 },
      );
    }
    let payload: unknown;
    try {
      payload = JSON.parse(responseText);
    } catch {
      throw Object.assign(new Error("Upstream model list was not valid JSON"), { statusCode: 502 });
    }
    const models = parseDiscoveredModels(payload);
    if (models.length === 0) {
      throw Object.assign(new Error("No models found in the upstream response"), { statusCode: 502 });
    }
    return { models, endpoint: endpoint.toString() };
  } catch (error) {
    if (typeof error === "object" && error && "statusCode" in error) throw error;
    const statusCode = error instanceof DOMException && error.name === "TimeoutError" ? 504 : 502;
    throw Object.assign(new Error(errorMessage(error)), { statusCode });
  }
}

type ModelCatalogCache = {
  entries: ModelCatalogEntry[];
  expiresAt: number;
  inFlight?: Promise<ModelCatalogEntry[]>;
};

const modelCatalogCache: ModelCatalogCache = { entries: [], expiresAt: 0 };

async function loadModelCatalog(): Promise<ModelCatalogEntry[]> {
  if (modelCatalogCache.entries.length > 0 && modelCatalogCache.expiresAt > Date.now()) {
    return modelCatalogCache.entries;
  }
  if (!modelCatalogCache.inFlight) {
    modelCatalogCache.inFlight = (async () => {
      const response = await fetch("https://models.dev/api.json", {
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(15_000),
      });
      if (!response.ok) throw new Error(`models.dev returned HTTP ${response.status}`);
      const entries = flattenModelsDevCatalog(await response.json());
      if (entries.length === 0) throw new Error("models.dev returned an empty catalog");
      modelCatalogCache.entries = entries;
      modelCatalogCache.expiresAt = Date.now() + 60 * 60 * 1000;
      return entries;
    })().finally(() => {
      modelCatalogCache.inFlight = undefined;
    });
  }
  try {
    return await modelCatalogCache.inFlight;
  } catch (error) {
    if (modelCatalogCache.entries.length > 0) return modelCatalogCache.entries;
    throw error;
  }
}

async function modelCatalog(url: URL): Promise<unknown> {
  const query = (url.searchParams.get("q") ?? "").slice(0, 120);
  const provider = (url.searchParams.get("provider") ?? "").slice(0, 120);
  const baseUrl = (url.searchParams.get("baseUrl") ?? "").slice(0, 500);
  const parsedLimit = Number.parseInt(url.searchParams.get("limit") ?? "50", 10);
  const limit = Number.isFinite(parsedLimit) ? parsedLimit : 50;
  try {
    const entries = await loadModelCatalog();
    return {
      models: searchModelCatalog(entries, query, provider, limit),
      recommendation: recommendModelCatalogPreset(entries, query, provider, baseUrl),
      source: "https://models.dev/api.json",
    };
  } catch (error) {
    throw Object.assign(new Error(errorMessage(error)), { statusCode: 502 });
  }
}

function assistantText(message: AssistantMessage): string {
  return message.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("");
}

async function testConfiguredModel(req: IncomingMessage): Promise<unknown> {
  const body = await readJson(req);
  const providerName = typeof body.providerName === "string" ? body.providerName.trim() : "";
  if (!providerName) throw Object.assign(new Error("providerName is required"), { statusCode: 400 });
  if (!isRecord(body.provider)) throw Object.assign(new Error("provider is required"), { statusCode: 400 });
  if (!isRecord(body.model)) throw Object.assign(new Error("model is required"), { statusCode: 400 });
  const modelId = typeof body.model.id === "string" ? body.model.id.trim() : "";
  if (!modelId) throw Object.assign(new Error("Model ID is required"), { statusCode: 400 });

  const tempDir = mkdtempSync(join(tmpdir(), "polykit-agent-model-test-"));
  try {
    const modelsPath = join(tempDir, "models.json");
    writeFileSync(modelsPath, JSON.stringify({
      providers: {
        [providerName]: {
          ...body.provider,
          models: [{ ...body.model, id: modelId }],
        },
      },
    }, null, 2), "utf8");

    const modelRuntime = await ModelRuntime.create({ modelsPath });
    const loadError = modelRuntime.getError();
    if (loadError) return { ok: false, error: loadError };
    const model = modelRuntime.getModel(providerName, modelId);
    if (!model) return { ok: false, error: `Model not found: ${providerName}/${modelId}` };
    const resolved = await modelRuntime.getAuth(model);
    if (!resolved?.auth.apiKey) return { ok: false, error: `No API key found for "${providerName}"` };

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20_000);
    let status: number | undefined;
    const startedAt = Date.now();
    try {
      const message = await completeSimple(model, {
        messages: [{ role: "user", content: "Reply with OK only.", timestamp: Date.now() }],
      }, {
        apiKey: resolved.auth.apiKey,
        headers: resolved.auth.headers,
        maxTokens: 16,
        timeoutMs: 20_000,
        maxRetries: 0,
        cacheRetention: "none",
        signal: controller.signal,
        onResponse: (response) => { status = response.status; },
      });
      const latencyMs = Date.now() - startedAt;
      if (message.stopReason === "error" || message.stopReason === "aborted") {
        return {
          ok: false,
          error: message.errorMessage ?? (controller.signal.aborted ? "Test timed out" : "Model returned an error"),
          latencyMs,
          status,
        };
      }
      return { ok: true, latencyMs, status, responseText: assistantText(message).slice(0, 300) };
    } finally {
      clearTimeout(timeout);
    }
  } catch (error) {
    return { ok: false, error: errorMessage(error) };
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
}

async function providerListings(): Promise<{
  providers: ReturnType<typeof buildOAuthProviderList>;
  apiKeyProviders: ReturnType<typeof buildApiKeyProviderList>;
}> {
  const services = await createAgentSessionServices({ cwd: WORKSPACE_ROOT, agentDir: getAgentDir() });
  const inputs = await collectProviderListingInputs(services.modelRuntime);
  return {
    providers: buildOAuthProviderList(inputs),
    apiKeyProviders: buildApiKeyProviderList(inputs),
  };
}

function authToken(provider: string): string {
  return `${provider}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function authSseHeaders(): Record<string, string> {
  return {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  };
}

function sendAuthEvent(res: ServerResponse, payload: unknown): void {
  if (res.writableEnded || res.destroyed) return;
  sse(res, payload);
}

function authLogin(providerId: string, req: IncomingMessage, res: ServerResponse): void {
  res.writeHead(200, authSseHeaders());
  const abort = new AbortController();
  const activeTokens = new Set<string>();
  let pendingManualRequest: { token: string; promise: Promise<string> } | undefined;
  let finished = false;

  const createInputRequest = () => {
    const token = authToken(providerId);
    activeTokens.add(token);
    const promise = new Promise<string>((resolveInput, rejectInput) => {
      pendingAuthInputs.set(token, {
        provider: providerId,
        resolve: resolveInput,
        reject: rejectInput,
      });
    });
    return { token, promise };
  };

  const getManualInputRequest = () => {
    if (!pendingManualRequest) {
      pendingManualRequest = createInputRequest();
      pendingManualRequest.promise
        .finally(() => { pendingManualRequest = undefined; })
        .catch(() => {});
    }
    return pendingManualRequest;
  };

  const cleanup = () => {
    for (const token of activeTokens) {
      pendingAuthInputs.get(token)?.reject(new Error("Login cancelled"));
      pendingAuthInputs.delete(token);
    }
    activeTokens.clear();
  };

  const abortOnDisconnect = () => {
    if (!finished) abort.abort();
  };
  res.on("close", abortOnDisconnect);

  void (async () => {
    try {
      const services = await createAgentSessionServices({ cwd: WORKSPACE_ROOT, agentDir: getAgentDir() });
      const modelRuntime = services.modelRuntime;
      const provider = modelRuntime.getProvider(providerId);
      if (!provider?.auth.oauth) {
        sendAuthEvent(res, { type: "error", message: `Unknown provider: ${providerId}` });
        return;
      }

      await modelRuntime.login(providerId, "oauth", {
        prompt: async (prompt) => {
          const request = prompt.type === "manual_code"
            ? getManualInputRequest()
            : createInputRequest();
          if (prompt.type === "select") {
            sendAuthEvent(res, {
              type: "select_request",
              message: prompt.message,
              options: prompt.options,
              token: request.token,
            });
          } else {
            sendAuthEvent(res, {
              type: "prompt_request",
              message: prompt.message,
              placeholder: prompt.placeholder ?? null,
              token: request.token,
            });
          }
          return request.promise;
        },
        notify: (event) => {
          if (event.type === "auth_url") {
            const request = getManualInputRequest();
            sendAuthEvent(res, {
              type: "auth",
              url: event.url,
              instructions: event.instructions ?? null,
              token: request.token,
            });
          } else if (event.type === "device_code") {
            sendAuthEvent(res, {
              type: "device_code",
              userCode: event.userCode,
              verificationUri: event.verificationUri,
              intervalSeconds: event.intervalSeconds ?? null,
              expiresInSeconds: event.expiresInSeconds ?? null,
            });
          } else {
            sendAuthEvent(res, { type: "progress", message: event.message });
          }
        },
        signal: abort.signal,
      });
      sendAuthEvent(res, { type: "success" });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message !== "Login cancelled" && !abort.signal.aborted) {
        sendAuthEvent(res, { type: "error", message });
      } else if (!abort.signal.aborted) {
        sendAuthEvent(res, { type: "cancelled" });
      }
    } finally {
      finished = true;
      cleanup();
      res.end();
    }
  })();
}

async function authLoginInput(providerId: string, req: IncomingMessage): Promise<unknown> {
  const body = await readJson(req);
  const token = typeof body.token === "string" ? body.token : "";
  const code = typeof body.code === "string" ? body.code : "";
  if (!token || !code) throw Object.assign(new Error("token and code required"), { statusCode: 400 });
  if (!token.startsWith(`${providerId}-`)) throw Object.assign(new Error("Token does not match provider"), { statusCode: 400 });
  const callback = pendingAuthInputs.get(token);
  if (!callback || callback.provider !== providerId) throw Object.assign(new Error("No pending login for token"), { statusCode: 404 });
  pendingAuthInputs.delete(token);
  callback.resolve(code);
  return { ok: true, provider: providerId };
}

async function authApiKeyLogin(providerId: string, req: IncomingMessage): Promise<unknown> {
  const body = await readJson(req);
  const apiKey = typeof body.apiKey === "string" ? body.apiKey.trim() : "";
  if (!apiKey) throw Object.assign(new Error("apiKey is required"), { statusCode: 400 });

  const abort = new AbortController();
  req.on("aborted", () => abort.abort());
  const services = await createAgentSessionServices({ cwd: WORKSPACE_ROOT, agentDir: getAgentDir() });
  const apiKeyAuth = services.modelRuntime.getProvider(providerId)?.auth.apiKey;
  if (!apiKeyAuth?.login) throw new Error(`${providerId} does not support API key login`);

  let keySubmitted = false;
  const credential = await apiKeyAuth.login({
    signal: abort.signal,
    notify: () => {},
    prompt: async (prompt) => {
      if (prompt.type === "select") {
        const keyOption = prompt.options.find((option) => option.id === "api-key" || option.id === "bearer-token");
        if (keyOption) return keyOption.id;
        throw new Error(`${providerId} requires interactive authentication setup`);
      }
      if (!keySubmitted && prompt.type === "secret") {
        keySubmitted = true;
        return apiKey;
      }
      throw new Error(`${providerId} requires additional authentication settings`);
    },
  });
  await storeProviderCredential(providerId, credential);
  return { success: true };
}

async function sessions(includeArchived: boolean): Promise<unknown> {
  const archived = getSessionArchiveRecords();
  const hidden = getHiddenWorkspaceRecords();
  const all = (await listAllSessions()).map((session) => {
    const archive = archived[session.id];
    const workspaceRoot = session.projectRoot ?? session.cwd;
    const hiddenWorkspace = workspaceRoot ? hidden[workspaceRoot] : undefined;
    return {
      ...session,
      ...(archive ? { archived: true, archivedAt: archive.archivedAt } : {}),
      ...(hiddenWorkspace ? { workspaceHidden: true, workspaceHiddenAt: hiddenWorkspace.hiddenAt } : {}),
    };
  });
  return {
    sessions: includeArchived ? all : all.filter((session) => !session.archived),
    runningSessionIds: getRunningRpcSessionIds(),
  };
}

async function workspaces(): Promise<unknown> {
  return {
    workspaces: Object.entries(getHiddenWorkspaceRecords())
      .map(([root, record]) => ({ root, hiddenAt: record.hiddenAt }))
      .sort((a, b) => b.hiddenAt.localeCompare(a.hiddenAt)),
  };
}

async function skills(cwdValue: string | null): Promise<unknown> {
  const cwd = validateCwd(cwdValue ?? WORKSPACE_ROOT);
  return loadSkillsWithInstallInfo(cwd);
}

function emptyPluginCounts(): Record<string, number> {
  return { extensions: 0, skills: 0, prompts: 0, themes: 0 };
}

function packageSource(entry: unknown): string {
  return typeof entry === "string"
    ? entry
    : entry && typeof entry === "object" && typeof (entry as { source?: unknown }).source === "string"
      ? (entry as { source: string }).source
      : "";
}

function packageDisabled(settings: SettingsManager, source: string, scope: "global" | "project"): boolean {
  const entries = (scope === "project" ? settings.getProjectSettings() : settings.getGlobalSettings()).packages ?? [];
  const entry = entries.find((candidate) => packageSource(candidate) === source);
  if (!entry || typeof entry === "string" || typeof entry !== "object") return false;
  const record = entry as Record<string, unknown>;
  return ["extensions", "skills", "prompts", "themes"].every((key) => Array.isArray(record[key]) && (record[key] as unknown[]).length === 0);
}

async function plugins(cwdValue: string | null): Promise<unknown> {
  const cwd = validateCwd(cwdValue ?? WORKSPACE_ROOT);
  const agentDir = getAgentDir();
  const trust = projectTrustReloadOptions(cwd, agentDir);
  const projectTrusted = Boolean(trust);
  const settings = SettingsManager.create(cwd, agentDir, { projectTrusted });
  const packageManager = new DefaultPackageManager({ cwd, agentDir, settingsManager: settings });
  const packages = packageManager.listConfiguredPackages().map((pkg) => {
    const scope = pkg.scope === "project" ? "project" : "global";
    const disabled = packageDisabled(settings, pkg.source, scope);
    return {
      source: pkg.source,
      scope,
      filtered: Boolean(pkg.filtered),
      disabled,
      ...(pkg.installedPath ? { installedPath: pkg.installedPath } : {}),
      counts: emptyPluginCounts(),
      resources: [],
      status: disabled ? "disabled" : pkg.installedPath ? "installed" : "missing",
    };
  });
  return {
    packages,
    totals: emptyPluginCounts(),
    diagnostics: packages.length > 0 ? [{ type: "warning", message: "Package resource details are loaded when the Agent runtime starts." }] : [],
    projectResourcesLoaded: projectTrusted,
  };
}

async function setPluginEnabled(body: Record<string, unknown>): Promise<unknown> {
  const cwd = validateCwd(body.cwd ?? WORKSPACE_ROOT);
  const source = typeof body.source === "string" ? body.source.trim() : "";
  const scope = body.scope === "project" ? "project" : "global";
  const action = body.action === "disable" ? "disable" : body.action === "enable" ? "enable" : "";
  if (!source || !action) throw Object.assign(new Error("source and a disable/enable action are required"), { statusCode: 400 });
  const agentDir = getAgentDir();
  const trust = projectTrustReloadOptions(cwd, agentDir);
  if (scope === "project" && !trust) throw Object.assign(new Error("Project resources must be trusted before modifying project plugins"), { statusCode: 403 });
  const settings = SettingsManager.create(cwd, agentDir, { projectTrusted: Boolean(trust) });
  const current = (scope === "project" ? settings.getProjectSettings() : settings.getGlobalSettings()).packages ?? [];
  let changed = false;
  const next = current.map((entry) => {
    if (packageSource(entry) !== source) return entry;
    changed = true;
    if (action === "disable") return { ...(typeof entry === "string" ? { source: entry } : entry), extensions: [], skills: [], prompts: [], themes: [] };
    return source;
  });
  if (!changed) throw Object.assign(new Error("Plugin package not found"), { statusCode: 404 });
  if (scope === "project") settings.setProjectPackages(next);
  else settings.setPackages(next);
  await settings.flush();
  return plugins(cwd);
}

async function toggleSkill(body: Record<string, unknown>): Promise<unknown> {
  const requestedPath = typeof body.filePath === "string" ? resolve(body.filePath) : "";
  const disabled = body.disableModelInvocation;
  if (!requestedPath || typeof disabled !== "boolean") throw Object.assign(new Error("filePath and disableModelInvocation are required"), { statusCode: 400 });
  if (!existsSync(requestedPath)) throw Object.assign(new Error("Skill path is not allowed"), { statusCode: 403 });
  const filePath = realpathSync(requestedPath);
  const home = process.env.HOME ? resolve(process.env.HOME) : null;
  const allowedRoots = [
    WORKSPACE_ROOT,
    resolve(getAgentDir()),
    ...(home ? [join(home, ".agents", "skills"), join(home, ".pi", "agent", "skills")] : []),
  ];
  if (!allowedRoots.some((root) => isWithinRoot(filePath, root))) {
    throw Object.assign(new Error("Skill path is not allowed"), { statusCode: 403 });
  }
  const content = readFileSync(filePath, "utf8");
  const key = "disable-model-invocation";
  const { frontmatter } = parseFrontmatter<Record<string, unknown>>(content);
  const alreadySet = Boolean(frontmatter[key]);
  let updated = content;
  if (disabled && !alreadySet) {
    updated = content.replace(/^---\r?\n/, `---\n${key}: true\n`);
    if (updated === content) updated = `---\n${key}: true\n---\n${content}`;
  } else if (!disabled && alreadySet) {
    updated = content.replace(new RegExp(`^${key}\\s*:.*\\r?\\n`, "m"), "");
  }
  if (updated !== content) writeFileSync(filePath, updated, "utf8");
  return { success: true };
}

async function mcp(cwdValue: string | null, sessionId: string | null): Promise<Record<string, unknown>> {
  const cwd = validateCwd(cwdValue ?? WORKSPACE_ROOT);
  const live = sessionId ? getRpcSession(sessionId) : undefined;
  let runtime: { running: boolean; summary?: string } = { running: false };
  if (live?.isAlive()) {
    const state = await live.send({ type: "get_state" }) as { extensionStatuses?: Array<{ key: string; text: string }> };
    const status = state.extensionStatuses?.find(({ key }) => key.toLowerCase().includes("mcp"));
    runtime = { running: true, ...(status?.text ? { summary: status.text } : {}) };
  }
  return { ...(await readMcpConfig(cwd)), runtime };
}

async function patchMcp(body: Record<string, unknown>): Promise<unknown> {
  const cwd = validateCwd(body.cwd ?? WORKSPACE_ROOT);
  const server = typeof body.server === "string" ? body.server.trim() : "";
  if (!server || typeof body.enabled !== "boolean") throw Object.assign(new Error("server and enabled are required"), { statusCode: 400 });
  const current = await readMcpConfig(cwd);
  if (!current.servers.some((item) => item.name === server)) throw Object.assign(new Error("MCP server not found"), { statusCode: 404 });
  const changed = await writeProjectMcpEnabled(cwd, server, body.enabled);
  const sessionId = typeof body.sessionId === "string" ? body.sessionId : null;
  const live = sessionId ? getRpcSession(sessionId) : undefined;
  if (live?.isAlive()) await live.send({ type: "reload" });
  return { ...(await mcp(cwd, sessionId)), changed, reloaded: Boolean(live?.isAlive()) };
}

function sse(res: ServerResponse, payload: unknown): void {
  res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

async function sessionEvents(id: string, req: IncomingMessage, res: ServerResponse): Promise<void> {
  const session = await ensureSession(id);
  if (!session) return json(res, 404, { error: "Session not found" });
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  sse(res, { type: "connected", sessionId: id });
  const unsubscribe = session.onEvent((event: AgentEvent) => {
    if (["turn_start", "turn_end", "tool_execution_update"].includes(event.type)) return;
    const clientEvent = event.type === "agent_end" ? { type: "agent_end" } : { ...event };
    if (clientEvent.type === "message_update") delete (clientEvent as Record<string, unknown>).assistantMessageEvent;
    sse(res, clientEvent);
  });
  const heartbeat = setInterval(() => res.write(":\n\n"), 30_000);
  const cleanup = () => {
    clearInterval(heartbeat);
    unsubscribe();
  };
  req.on("close", cleanup);
}

async function runningEvents(req: IncomingMessage, res: ServerResponse): Promise<void> {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  const unsubscribe = subscribeRunningSessions((ids) => sse(res, { type: "running", runningSessionIds: ids }));
  sse(res, { type: "running", runningSessionIds: getRunningRpcSessionIds() });
  const heartbeat = setInterval(() => res.write(":\n\n"), 30_000);
  req.on("close", () => {
    clearInterval(heartbeat);
    unsubscribe();
  });
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (TOKEN && req.headers["x-polykit-agent-token"] !== TOKEN) return json(res, 401, { error: "Unauthorized" });
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  const parts = url.pathname.split("/").filter(Boolean);
  try {
    if (req.method === "GET" && url.pathname === "/health") return json(res, 200, { ok: true });
    if (req.method === "GET" && url.pathname === "/sessions") return json(res, 200, await sessions(url.searchParams.get("includeArchived") === "1"));
    if (req.method === "POST" && url.pathname === "/sessions") return json(res, 200, await createSession(await readJson(req)));
    if (req.method === "GET" && url.pathname === "/running/events") return runningEvents(req, res);
    if (req.method === "GET" && url.pathname === "/models") return json(res, 200, await models(url.searchParams.get("cwd")));
    if (req.method === "GET" && url.pathname === "/models-config") return json(res, 200, readModelsConfig());
    if (req.method === "PUT" && url.pathname === "/models-config") {
      const body = await readJson(req);
      writeModelsConfig(body);
      invalidateModelsCache();
      return json(res, 200, { success: true });
    }
    if (req.method === "POST" && url.pathname === "/models-config/discover") {
      return json(res, 200, await discoverProviderModels(req));
    }
    if (req.method === "GET" && url.pathname === "/models-config/catalog") {
      return json(res, 200, await modelCatalog(url));
    }
    if (req.method === "POST" && url.pathname === "/models-config/test") {
      return json(res, 200, await testConfiguredModel(req));
    }
    if (req.method === "GET" && url.pathname === "/auth/providers") return json(res, 200, { providers: (await providerListings()).providers });
    if (req.method === "GET" && url.pathname === "/auth/all-providers") return json(res, 200, { providers: (await providerListings()).apiKeyProviders });
    if (req.method === "GET" && parts[0] === "auth" && parts[1] === "login" && parts[2]) {
      return authLogin(decodeId(parts[2]), req, res);
    }
    if (req.method === "POST" && parts[0] === "auth" && parts[1] === "login" && parts[2]) {
      return json(res, 200, await authLoginInput(decodeId(parts[2]), req));
    }
    if (req.method === "POST" && parts[0] === "auth" && parts[1] === "logout" && parts[2]) {
      const providerId = decodeId(parts[2]);
      const provider = (await createAgentSessionServices({ cwd: WORKSPACE_ROOT, agentDir: getAgentDir() })).modelRuntime.getProvider(providerId);
      if (!provider?.auth.oauth) return json(res, 400, { error: `Unknown provider: ${providerId}` });
      const result = await removeStoredCredentialIfType(providerId, "oauth");
      if (result.status === "type_mismatch") {
        return json(res, 409, { error: `${providerId} is authenticated with an API key, not OAuth` });
      }
      return json(res, 200, { ok: true, ...result });
    }
    if (req.method === "GET" && parts[0] === "auth" && parts[1] === "api-key" && parts[2]) {
      const services = await createAgentSessionServices({ cwd: WORKSPACE_ROOT, agentDir: getAgentDir() });
      const provider = services.modelRuntime.getProvider(decodeId(parts[2]));
      const status = provider ? services.modelRuntime.getProviderAuthStatus(provider.id) : { configured: false };
      return json(res, 200, {
        provider: decodeId(parts[2]),
        displayName: provider?.name ?? decodeId(parts[2]),
        configured: status.configured,
        ...(status.source ? { source: status.source } : {}),
        models: provider ? services.modelRuntime.getModels(provider.id).length : 0,
      });
    }
    if (req.method === "POST" && parts[0] === "auth" && parts[1] === "api-key" && parts[2]) {
      return json(res, 200, await authApiKeyLogin(decodeId(parts[2]), req));
    }
    if (req.method === "DELETE" && parts[0] === "auth" && parts[1] === "api-key" && parts[2]) {
      const result = await removeStoredCredentialIfType(decodeId(parts[2]), "api_key");
      if (result.status === "type_mismatch") {
        return json(res, 409, { error: `${decodeId(parts[2])} is authenticated with OAuth, not an API key` });
      }
      return json(res, 200, { success: true, ...result });
    }
    if (req.method === "GET" && url.pathname === "/skills") return json(res, 200, await skills(url.searchParams.get("cwd")));
    if (req.method === "PATCH" && url.pathname === "/skills") return json(res, 200, await toggleSkill(await readJson(req)));
    if (req.method === "POST" && url.pathname === "/plugins") {
      const body = await readJson(req);
      if (body.action === "disable" || body.action === "enable") return json(res, 200, await setPluginEnabled(body));
      return json(res, 501, { error: "Plugin install, update, and removal require an explicit trusted project action" });
    }
    if ((url.pathname.startsWith("/skills/") || url.pathname === "/plugins") && ["POST", "PATCH"].includes(req.method ?? "")) {
      return json(res, 501, { error: "This Agent management action is not available in the native PolyKit runtime yet" });
    }
    if (req.method === "GET" && url.pathname === "/plugins") {
      return json(res, 200, await plugins(url.searchParams.get("cwd")));
    }
    if (req.method === "GET" && url.pathname === "/workspaces") return json(res, 200, await workspaces());
    if (req.method === "POST" && url.pathname === "/workspaces") {
      const body = await readJson(req);
      const root = typeof body.root === "string" ? resolve(body.root) : "";
      if (!root) throw Object.assign(new Error("root is required"), { statusCode: 400 });
      setWorkspaceHidden(root, body.hidden !== false);
      return json(res, 200, { ok: true, root, hidden: body.hidden !== false });
    }
    if (req.method === "GET" && url.pathname === "/mcp") return json(res, 200, await mcp(url.searchParams.get("cwd"), url.searchParams.get("sessionId")));
    if (req.method === "PATCH" && url.pathname === "/mcp") return json(res, 200, await patchMcp(await readJson(req)));

    if (parts[0] !== "sessions" || !parts[1]) return json(res, 404, { error: "Not found" });
    const id = decodeId(parts[1]);
    if (req.method === "POST" && parts[2] === "restore") {
      if (!await resolveSessionPath(id)) return json(res, 404, { error: "Session not found" });
      setSessionsArchived([id], false);
      return json(res, 200, { ok: true, restoredSessionIds: [id] });
    }
    if (req.method === "DELETE" && parts.length === 2) {
      const filePath = await resolveSessionPath(id);
      if (!filePath) return json(res, 404, { error: "Session not found" });
      const live = getRpcSession(id);
      if (live?.isRunning()) return json(res, 409, { error: "Cannot delete a running session" });
      await live?.shutdown();
      // Session paths are resolved from the SDK session directory, so this is
      // safe to remove after the existence check above.
      const { unlinkSync } = await import("node:fs");
      unlinkSync(filePath);
      removeSessionArchiveRecord(id);
      return json(res, 200, { ok: true });
    }
    if (req.method === "POST" && parts[2] === "commands") return json(res, 200, await sendCommand(id, await readJson(req)));
    if (req.method === "GET" && parts[2] === "events") return sessionEvents(id, req, res);
    if (req.method === "GET" && parts[2] === "state") return json(res, 200, await sessionState(id));
    if (req.method === "GET" && parts[2] === "context") return json(res, 200, await sessionContext(id, url.searchParams));
    if (req.method === "GET" && parts[2] === "entries" && parts[4] === "thinking") return json(res, 200, { thinking: "" });
    if (req.method === "GET" && parts[2] === "bash-output") return json(res, 200, await bashOutput(id, url.searchParams));
    if (req.method === "GET" && parts.length === 2) return json(res, 200, await sessionDetails(id, url.searchParams));
    return json(res, 404, { error: "Not found" });
  } catch (error) {
    const status = typeof error === "object" && error && "statusCode" in error && typeof error.statusCode === "number" ? error.statusCode : 500;
    return json(res, status, { error: errorMessage(error) });
  }
}

const server = http.createServer((req, res) => {
  void handle(req, res);
});

server.listen(PORT, "127.0.0.1", () => {
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : PORT;
  process.stdout.write(`${JSON.stringify({ ready: true, port })}\n`);
});

const shutdown = async () => {
  server.close();
  const { shutdownAllRpcSessions } = await import("../apps/web/lib/rpc-manager");
  await shutdownAllRpcSessions();
};
process.on("SIGTERM", () => { void shutdown().finally(() => process.exit(0)); });
process.on("SIGINT", () => { void shutdown().finally(() => process.exit(0)); });
