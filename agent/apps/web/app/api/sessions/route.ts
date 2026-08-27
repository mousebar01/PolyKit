import { NextResponse } from "next/server";
import { listAllSessions } from "@/lib/session-reader";
import { getHiddenWorkspaceRecords, getSessionArchiveRecords } from "@/lib/session-archive";
import { getRunningRpcSessionIds } from "@/lib/rpc-manager";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  try {
    const archiveRecords = getSessionArchiveRecords();
    const hiddenWorkspaceRecords = getHiddenWorkspaceRecords();
    const includeArchived = new URL(req.url).searchParams.get("includeArchived") === "1";
    const sessions = (await listAllSessions()).map((session) => {
      const archive = archiveRecords[session.id];
      const workspaceRoot = session.projectRoot ?? session.cwd;
      const hiddenWorkspace = workspaceRoot ? hiddenWorkspaceRecords[workspaceRoot] : undefined;
      return {
        ...session,
        ...(archive ? { archived: true, archivedAt: archive.archivedAt } : {}),
        ...(hiddenWorkspace ? { workspaceHidden: true, workspaceHiddenAt: hiddenWorkspace.hiddenAt } : {}),
      };
    });
    const visibleSessions = includeArchived ? sessions : sessions.filter((session) => !session.archived);
    return NextResponse.json({ sessions: visibleSessions, runningSessionIds: getRunningRpcSessionIds() });
  } catch (error) {
    return NextResponse.json(
      { error: String(error) },
      { status: 500 }
    );
  }
}
