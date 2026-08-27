import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(
    { ok: true, version: process.env.NEXT_PUBLIC_APP_VERSION ?? "dev" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
