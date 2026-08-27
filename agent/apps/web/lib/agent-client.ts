// Client-side helpers for the PolyKit-native Agent API.
//
// Every /agent/* route returns one of:
//   { success: true, data: <result> }
//   { error: string }              (non-2xx)
//
// Call sites previously repeated the same 5-line fetch block 13× in
// hooks/useAgentSession.ts. This helper collapses that down to one line.

export function agentApiPath(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `/agent${normalized}`;
}

export async function sendAgentCommand<T = unknown>(
  sessionId: string,
  command: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(agentApiPath(`/sessions/${encodeURIComponent(sessionId)}/commands`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(command),
  });
  const body = (await res.json().catch(() => ({}))) as {
    success?: boolean;
    data?: T;
    error?: string;
  };
  if (!res.ok || body.error) {
    throw new Error(body.error ?? `HTTP ${res.status}`);
  }
  return body.data as T;
}
