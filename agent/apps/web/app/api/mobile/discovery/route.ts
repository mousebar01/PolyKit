import { hostname } from "node:os";
import { NextResponse } from "next/server";

const noStore = { "Cache-Control": "no-store" };
export const dynamic = "force-dynamic";

/**
 * A deliberately small, unauthenticated LAN discovery response.
 * It contains connection metadata only; the mobile client still needs the
 * configured access username and password for its first login.
 */
export function GET(req: Request) {
  const requestUrl = new URL(req.url);
  const host = req.headers.get("host") ?? requestUrl.host;
  const port = host.match(/:(\d+)$/)?.[1] ?? requestUrl.port;
  return NextResponse.json({
    service: "polykit-agent",
    name: hostname() || "PolyKit Agent",
    protocol: requestUrl.protocol.replace(":", ""),
    port: Number(port || 30001),
    serverUrl: `${requestUrl.protocol}//${host}`,
  }, { headers: noStore });
}
