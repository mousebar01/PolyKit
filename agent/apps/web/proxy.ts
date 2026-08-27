import { NextResponse, type NextRequest } from "next/server";
import {
  isApiRequestAllowed,
  isApiRequestHostAllowed,
  isLoopbackRequest,
} from "@/lib/request-security";
import {
  isValidBasicAuthorization,
  isWebPasswordEnabled,
} from "@/lib/web-auth";
import { isValidMobileBearerAuthorization } from "@/lib/mobile-device-auth";

export function proxy(request: NextRequest) {
  const isApiRequest = request.nextUrl.pathname === "/api"
    || request.nextUrl.pathname.startsWith("/api/");
  const isTrustedRequest = isApiRequest
    ? isApiRequestAllowed(request)
    : isApiRequestHostAllowed(request);

  if (!isTrustedRequest) {
    if (!isApiRequest) {
      return new NextResponse("Untrusted request", { status: 403 });
    }
    return NextResponse.json({ error: "Untrusted API request" }, { status: 403 });
  }

  const authorization = request.headers.get("authorization");
  const isMobileDeviceManagement = request.nextUrl.pathname === "/api/mobile/devices";
  const isMobileDiscovery = request.nextUrl.pathname === "/api/mobile/discovery";
  const isCredentialManagement = request.nextUrl.pathname === "/api/config/security"
    || request.nextUrl.pathname === "/api/config/security/restart";
  const passwordEnabled = isWebPasswordEnabled();
  const validBasicAuthorization = isValidBasicAuthorization(authorization);
  if (
    passwordEnabled
    && !isLoopbackRequest(request)
    && !isMobileDiscovery
    && !validBasicAuthorization
    && (isCredentialManagement || !isApiRequest || isMobileDeviceManagement || !isValidMobileBearerAuthorization(authorization))
  ) {
    return new NextResponse("Authentication required", {
      status: 401,
      headers: {
        "Cache-Control": "no-store",
        "WWW-Authenticate": 'Basic realm="polykit-agent", charset="UTF-8"',
      },
    });
  }

  return NextResponse.next();
}

export const config = { matcher: ["/", "/api/:path*"] };
