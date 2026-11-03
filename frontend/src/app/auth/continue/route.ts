import { NextRequest, NextResponse } from "next/server";

import { safeNextPath } from "@/lib/auth/paths";

export async function GET(request: NextRequest) {
  const raw = request.cookies.get("intake_next")?.value;
  const next = safeNextPath(raw ? decodeURIComponent(raw) : null);
  const host = request.headers.get("host") ?? request.nextUrl.host;
  const proto = request.nextUrl.protocol === "https:" ? "https:" : "http:";
  const response = NextResponse.redirect(`${proto}//${host}${next}`);
  response.cookies.set("intake_next", "", {
    httpOnly: true,
    sameSite: "strict",
    path: "/",
    maxAge: 0,
    secure: (process.env.AMPLIFY_APP_ORIGIN ?? "").startsWith("https"),
  });
  return response;
}
