type JsonObject = Record<string, unknown>;

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export interface DisplayToolCall {
  toolName: string;
  input: JsonObject;
}

/** Unwrap the generic MCP proxy shape into the tool call users actually made. */
export function resolveDisplayToolCall(toolName: string, input: JsonObject): DisplayToolCall {
  if (typeof input.tool === "string" && isObject(input.args)) {
    return { toolName: input.tool, input: input.args };
  }
  return { toolName, input };
}

function scalarPreview(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

function stringListPreview(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const items = value
    .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    .map((item) => item.trim());
  if (items.length === 0) return null;
  const visible = items.slice(0, 2).join(" · ");
  return items.length > 2 ? `${visible} · +${items.length - 2}` : visible;
}

export function getDisplayToolPreview(input: JsonObject): string {
  // Batch search tools use `queries`; show the actual search intent before
  // secondary numeric settings such as `numResults`.
  const queries = stringListPreview(input.queries);
  if (queries) return queries.slice(0, 200);
  for (const key of ["query", "pattern", "command", "path", "file_path", "url", "name"]) {
    const preview = scalarPreview(input[key]);
    if (preview) return preview.slice(0, 120);
  }
  for (const value of Object.values(input)) {
    const preview = scalarPreview(value);
    if (preview && typeof value !== "number") return preview.slice(0, 120);
  }
  return "";
}

export function unwrapProxyResultText(text: string): string {
  try {
    const parsed: unknown = JSON.parse(text);
    if (!isObject(parsed) || Object.keys(parsed).length !== 1 || !("result" in parsed)) return text;
    return JSON.stringify(parsed.result, null, 2);
  } catch {
    return text;
  }
}
