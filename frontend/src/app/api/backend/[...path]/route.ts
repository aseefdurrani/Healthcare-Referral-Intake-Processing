import { NextRequest, NextResponse } from "next/server";

import { isUuid } from "@/lib/api/problems";
import { readAccessToken } from "@/lib/auth/session";

export const runtime = "nodejs";

function backendBase(): string {
  return (process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}

function problem(status: number, code: string, title: string, detail: string) {
  const correlation_id = crypto.randomUUID();
  return NextResponse.json(
    { type: "about:blank", title, status, detail, code, correlation_id },
    {
      status,
      headers: {
        "content-type": "application/problem+json",
        "cache-control": "no-store",
      },
    },
  );
}

function originAllowed(request: NextRequest): boolean {
  const expected = process.env.AMPLIFY_APP_ORIGIN ?? "http://localhost:3000";
  return request.headers.get("origin") === expected;
}

async function relay(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (request.method !== "GET" && request.method !== "HEAD" && !originAllowed(request)) {
    return problem(403, "NOT_AUTHORIZED", "Not authorized", "The request origin is not accepted.");
  }
  const cookieJar = NextResponse.next();
  const token = await readAccessToken(request, cookieJar);
  if (!token) {
    return problem(401, "AUTH_REQUIRED", "Sign in required", "Sign in again to continue.");
  }
  const { path } = await context.params;
  const target = new URL(`${backendBase()}/api/v1/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;
  const headers = new Headers();
  headers.set("authorization", `Bearer ${token}`);
  const incomingId = request.headers.get("x-request-id");
  headers.set("x-request-id", incomingId && isUuid(incomingId) ? incomingId : crypto.randomUUID());
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const idempotency = request.headers.get("idempotency-key");
  if (idempotency) headers.set("idempotency-key", idempotency);
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    return problem(503, "DEPENDENCY_UNAVAILABLE", "Dependency unavailable", "The intake API could not be reached.");
  }
  const outgoing = new Headers();
  for (const name of ["content-type", "cache-control", "content-disposition", "x-content-type-options", "location"]) {
    const value = upstream.headers.get(name);
    if (value) outgoing.set(name, value);
  }
  if (!outgoing.has("cache-control")) outgoing.set("cache-control", "no-store");
  for (const cookie of cookieJar.headers.getSetCookie()) {
    outgoing.append("set-cookie", cookie);
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers: outgoing });
}

export const GET = relay;
export const POST = relay;
export const PATCH = relay;
export const PUT = relay;
export const DELETE = relay;
