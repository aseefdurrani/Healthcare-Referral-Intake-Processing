import { NextRequest, NextResponse } from "next/server";

import { amplifyRunner, MOCK_COOKIE } from "@/lib/auth/session";
import { isMockAuth } from "@/lib/auth/mode";
import { safeNextPath } from "@/lib/auth/paths";

export const runtime = "nodejs";

const SLUGS = new Set(["sign-in", "sign-in-callback", "sign-out", "sign-out-callback"]);

function unavailable() {
  return NextResponse.json(
    {
      type: "about:blank",
      title: "Sign-in service unavailable",
      status: 503,
      detail: "Sign-in is unavailable.",
      code: "AUTH_UNAVAILABLE",
      correlation_id: crypto.randomUUID(),
    },
    { status: 503, headers: { "content-type": "application/problem+json", "cache-control": "no-store" } },
  );
}

function cookieBase() {
  const secure = (process.env.AMPLIFY_APP_ORIGIN ?? "").startsWith("https");
  return {
    httpOnly: true,
    sameSite: "strict" as const,
    path: "/",
    secure,
  };
}

function sameOrigin(request: NextRequest, path: string): string {
  const host = request.headers.get("host") ?? request.nextUrl.host;
  const proto = request.nextUrl.protocol === "https:" ? "https:" : "http:";
  return `${proto}//${host}${path}`;
}

async function mockAuth(request: NextRequest, slug: string) {
  if (slug === "sign-in") {
    const next = safeNextPath(request.nextUrl.searchParams.get("next"));
    const response = NextResponse.redirect(sameOrigin(request, next));
    response.cookies.set(MOCK_COOKIE, "1", { ...cookieBase(), maxAge: 28_800 });
    return response;
  }
  if (slug === "sign-out") {
    const response = NextResponse.redirect(sameOrigin(request, "/sign-in?signed_out=1"));
    response.cookies.set(MOCK_COOKIE, "", { ...cookieBase(), maxAge: 0 });
    process.stderr.write(`${JSON.stringify({ event: "auth.signed_out", at: new Date().toISOString() })}\n`);
    return response;
  }
  return new NextResponse(null, { status: 404 });
}

export async function GET(request: NextRequest, context: { params: Promise<{ slug: string }> }) {
  const { slug } = await context.params;
  if (slug === "sign-up") return new NextResponse(null, { status: 404 });
  if (!SLUGS.has(slug)) return new NextResponse(null, { status: 404 });
  if (isMockAuth()) return mockAuth(request, slug);

  const runner = amplifyRunner();
  if (!runner) return unavailable();
  const handler = runner.createAuthRouteHandlers({
    redirectOnSignInComplete: "/auth/continue",
    redirectOnSignOutComplete: "/sign-in",
  });
  const response = await handler(request, { params: Promise.resolve({ slug }) });
  if (!response) return new NextResponse(null, { status: 404 });
  if (slug === "sign-in") {
    const next = safeNextPath(request.nextUrl.searchParams.get("next"));
    response.headers.append(
      "set-cookie",
      `intake_next=${encodeURIComponent(next)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=300${
        (process.env.AMPLIFY_APP_ORIGIN ?? "").startsWith("https") ? "; Secure" : ""
      }`,
    );
  }
  if (slug === "sign-out-callback") {
    const location = response.headers.get("location");
    if (location && !location.includes("error=")) {
      const headers = new Headers(response.headers);
      headers.set("location", "/sign-in?signed_out=1");
      process.stderr.write(`${JSON.stringify({ event: "auth.signed_out", at: new Date().toISOString() })}\n`);
      return new Response(response.body, { status: response.status, headers });
    }
  }
  return response;
}
