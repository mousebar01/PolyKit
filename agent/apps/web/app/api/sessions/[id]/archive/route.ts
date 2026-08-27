import { NextResponse } from "next/server";
import { getRpcSession } from "@/lib/rpc-manager";
import { setSessionsArchived } from "@/lib/session-archive";
import { invalidateSessionListCache, listAllSessions } from "@/lib/session-reader";

function collectBranchIds(sessionId: string, sessions: Awaited<ReturnType<typeof listAllSessions>>): string[] {
  const childrenByParent = new Map<string, string[]>();
  for (const session of sessions) {
    if (!session.parentSessionId) continue;
    const children = childrenByParent.get(session.parentSessionId) ?? [];
    children.push(session.id);
    childrenByParent.set(session.parentSessionId, children);
  }

  const result: string[] = [];
  const pending = [sessionId];
  const seen = new Set<string>();
  while (pending.length > 0) {
    const id = pending.pop()!;
    if (seen.has(id)) continue;
    seen.add(id);
    result.push(id);
    for (const childId of childrenByParent.get(id) ?? []) pending.push(childId);
  }
  return result;
}

export const dynamic = "force-dynamic";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const sessions = await listAllSessions();
    if (!sessions.some((session) => session.id === id)) {
      return NextResponse.json({ error: "Session not found" }, { status: 404 });
    }

    const archivedSessionIds = collectBranchIds(id, sessions);
    const busySessionId = archivedSessionIds.find((sessionId) => getRpcSession(sessionId)?.isRunning());
    if (busySessionId) {
      return NextResponse.json(
        { error: "Cannot archive a session while it is running", sessionId: busySessionId },
        { status: 409 },
      );
    }

    setSessionsArchived(archivedSessionIds, true);
    invalidateSessionListCache();
    return NextResponse.json({ ok: true, archivedSessionIds });
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 });
  }
}
