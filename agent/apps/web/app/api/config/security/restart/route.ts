import { NextResponse } from "next/server";
import { getAgentConfigStatus } from "@/lib/polykit-agent-config";
import { isAgentRestartSupported, requestAgentRestart } from "@/lib/polykit-agent-restart";

const noStore = { "Cache-Control": "no-store" };
export const dynamic = "force-dynamic";

export function POST() {
  if (!isAgentRestartSupported()) {
    return NextResponse.json(
      { error: "当前启动方式不支持自动重启，请停止后重新运行 npm run dev 或 npm start。" },
      { status: 409, headers: noStore },
    );
  }

  try {
    requestAgentRestart();
    return NextResponse.json(
      { restarting: true, networkMode: getAgentConfigStatus().networkMode },
      { status: 202, headers: noStore },
    );
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : String(error) },
      { status: 500, headers: noStore },
    );
  }
}
