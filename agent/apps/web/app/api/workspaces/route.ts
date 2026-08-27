import { NextResponse } from "next/server";
import { getHiddenWorkspaceRecords, setWorkspaceHidden } from "@/lib/session-archive";

export const dynamic = "force-dynamic";

export async function GET() {
  const workspaces = Object.entries(getHiddenWorkspaceRecords())
    .map(([root, record]) => ({ root, hiddenAt: record.hiddenAt }))
    .sort((a, b) => b.hiddenAt.localeCompare(a.hiddenAt));
  return NextResponse.json({ workspaces });
}

export async function POST(req: Request) {
  try {
    const body = await req.json() as { root?: unknown; hidden?: unknown };
    const root = typeof body.root === "string" ? body.root.trim() : "";
    if (!root) return NextResponse.json({ error: "root is required" }, { status: 400 });

    const hidden = body.hidden !== false;
    setWorkspaceHidden(root, hidden);
    return NextResponse.json({ ok: true, root, hidden });
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 400 });
  }
}
