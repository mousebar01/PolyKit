import { NextResponse } from "next/server";
import { createMobileDevice, listMobileDevices, renameMobileDevice, revokeMobileDevice } from "@/lib/mobile-device-auth";
import { isWebPasswordEnabled } from "@/lib/web-auth";

function passwordRequired(): NextResponse | null {
  if (isWebPasswordEnabled()) return null;
  return NextResponse.json(
    { error: "请先在设置中配置 PolyKit Agent 访问密码" },
    { status: 409, headers: { "Cache-Control": "no-store" } },
  );
}

export async function GET() {
  const missingPassword = passwordRequired();
  if (missingPassword) return missingPassword;
  return NextResponse.json({ devices: listMobileDevices() }, { headers: { "Cache-Control": "no-store" } });
}

export async function POST(req: Request) {
  const missingPassword = passwordRequired();
  if (missingPassword) return missingPassword;
  try {
    const body = await req.json() as { name?: string };
    const result = await createMobileDevice(typeof body.name === "string" ? body.name : "PolyKit Agent Mobile");
    return NextResponse.json(result, { status: 201, headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 });
  }
}

export async function DELETE(req: Request) {
  const missingPassword = passwordRequired();
  if (missingPassword) return missingPassword;
  try {
    const id = new URL(req.url).searchParams.get("id");
    if (!id) return NextResponse.json({ error: "id is required" }, { status: 400 });
    const removed = await revokeMobileDevice(id);
    return NextResponse.json({ removed }, { status: removed ? 200 : 404 });
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 });
  }
}

export async function PATCH(req: Request) {
  const missingPassword = passwordRequired();
  if (missingPassword) return missingPassword;
  try {
    const body = await req.json() as { id?: string; name?: string };
    if (typeof body.id !== "string" || typeof body.name !== "string" || !body.name.trim()) {
      return NextResponse.json({ error: "id and name are required" }, { status: 400 });
    }
    const device = await renameMobileDevice(body.id, body.name);
    return device
      ? NextResponse.json({ device }, { headers: { "Cache-Control": "no-store" } })
      : NextResponse.json({ error: "Mobile device not found" }, { status: 404 });
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 });
  }
}
