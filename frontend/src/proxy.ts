import { NextRequest, NextResponse } from "next/server";

import { safeNextPath } from "@/lib/auth/paths";

const PUBLIC_PREFIXES = ["/sign-in", "/api/auth", "/_next", "/brand", "/favicon.ico", "/pdf.worker.min.mjs"];

function hasSession(request: NextRequest): boolean {
  if (request.cookies.get("intake_session")?.value === "1") return true;
  return request.cookies.getAll().some((cookie) => cookie.name.endsWith(".accessToken") && cookie.value.length > 0);
}

function sameOrigin(request: NextRequest, path: string): string {
  const host = request.headers.get("host") ?? request.nextUrl.host;
  const proto = request.nextUrl.protocol === "https:" ? "https:" : "http:";
  return `${proto}//${host}${path}`;
}

function contentSecurityPolicy(nonce: string): string {
  const dev = process.env.NODE_ENV !== "production";
  const script = dev
    ? `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'unsafe-eval'`
    : `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`;
  return [
    "default-src 'self'",
    script,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' blob: data:",
    "worker-src 'self' blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("x-pathname", request.nextUrl.pathname);
  const csp = contentSecurityPolicy(nonce);
  requestHeaders.set("content-security-policy", csp);

  const { pathname } = request.nextUrl;
  const isPublic = PUBLIC_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`) || pathname === prefix);
  const staff = pathname.startsWith("/referrals") || pathname.startsWith("/auth/continue");
  if (staff && !isPublic && !hasSession(request)) {
    const redirect = NextResponse.redirect(sameOrigin(request, `/sign-in?next=${encodeURIComponent(safeNextPath(pathname))}`));
    redirect.headers.set("content-security-policy", csp);
    return redirect;
  }

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("content-security-policy", csp);
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image).*)"],
};
