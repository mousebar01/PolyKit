import { NextResponse } from "next/server";
import {
  getMobileDeviceForAuthorization,
  revokeMobileDeviceForAuthorization,
} from "@/lib/mobile-device-auth";

const noStore = { "Cache-Control": "no-store" };

export async function GET(req: Request) {
  const device = getMobileDeviceForAuthorization(req.headers.get("authorization"));
  if (!device) return NextResponse.json({ error: "Mobile device authentication required" }, { status: 401, headers: noStore });
  return NextResponse.json({ device }, { headers: noStore });
}

export async function DELETE(req: Request) {
  const removed = await revokeMobileDeviceForAuthorization(req.headers.get("authorization"));
  if (!removed) return NextResponse.json({ error: "Mobile device authentication required" }, { status: 401, headers: noStore });
  return NextResponse.json({ removed: true }, { headers: noStore });
}
