import path from "path";
import { NextResponse } from "next/server";
import { getAllowedFileRoots, isExistingFilePathAllowed } from "@/lib/file-access";
import { isDirectory, readMcpConfig, writeProjectMcpEnabled } from "@/lib/mcp-config";
import { getRpcSession } from "@/lib/rpc-manager";

export const dynamic = "force-dynamic";

async function validateCwd(value: unknown): Promise<{ cwd: string } | { response: NextResponse }> {
  if (typeof value !== "string" || !value.trim()) {
    return { response: NextResponse.json({ error: "cwd required" }, { status: 400 }) };
  }
  const cwd = path.resolve(value);
  if (!(await isDirectory(cwd))) {
    return { response: NextResponse.json({ error: "Directory does not exist" }, { status: 400 }) };
  }
  if (!isExistingFilePathAllowed(cwd, await getAllowedFileRoots())) {
    return { response: NextResponse.json({ error: "Access denied" }, { status: 403 }) };
  }
  return { cwd };
}

async function runtimeStatus(sessionId: string | null): Promise<{ running: boolean; summary?: string }> {
  if (!sessionId) return { running: false };
  const session = getRpcSession(sessionId);
  if (!session?.isAlive()) return { running: false };
  const state = await session.send({ type: "get_state" }) as {
    extensionStatuses?: Array<{ key: string; text: string }>;
  };
  const status = state.extensionStatuses?.find(({ key }) => key.toLowerCase().includes("mcp"));
  return { running: true, ...(status?.text ? { summary: status.text } : {}) };
}

export async function GET(req: Request) {
  try {
    const url = new URL(req.url);
    const result = await validateCwd(url.searchParams.get("cwd"));
    if ("response" in result) return result.response;
    const sessionId = url.searchParams.get("sessionId");
    return NextResponse.json({
      ...(await readMcpConfig(result.cwd)),
      runtime: await runtimeStatus(sessionId),
    });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}

export async function PATCH(req: Request) {
  try {
    const body = await req.json() as { cwd?: unknown; sessionId?: unknown; server?: unknown; enabled?: unknown };
    const result = await validateCwd(body.cwd);
    if ("response" in result) return result.response;
    if (typeof body.server !== "string" || !body.server.trim() || typeof body.enabled !== "boolean") {
      return NextResponse.json({ error: "server and enabled are required" }, { status: 400 });
    }

    const current = await readMcpConfig(result.cwd);
    if (!current.servers.some((server) => server.name === body.server)) {
      return NextResponse.json({ error: "MCP server not found" }, { status: 404 });
    }
    const changed = await writeProjectMcpEnabled(result.cwd, body.server, body.enabled);

    let reloaded = false;
    if (typeof body.sessionId === "string" && body.sessionId) {
      const session = getRpcSession(body.sessionId);
      if (session?.isAlive()) {
        await session.send({ type: "reload" });
        reloaded = true;
      }
    }

    return NextResponse.json({
      ...(await readMcpConfig(result.cwd)),
      runtime: await runtimeStatus(typeof body.sessionId === "string" ? body.sessionId : null),
      changed,
      reloaded,
    });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}
