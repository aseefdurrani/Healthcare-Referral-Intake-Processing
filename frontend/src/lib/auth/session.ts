import "server-only";

import { createServerRunner } from "@aws-amplify/adapter-nextjs";
import { fetchAuthSession } from "aws-amplify/auth/server";
import { cookies } from "next/headers";
import type { NextRequest, NextResponse } from "next/server";

import { isMockAuth } from "./mode";

const MOCK_COOKIE = "intake_session";
const MOCK_EMAIL = "demo.staff@example.test";

export function cognitoConfigured(): boolean {
  return Boolean(
    process.env.COGNITO_USER_POOL_ID &&
      process.env.COGNITO_APP_CLIENT_ID &&
      process.env.COGNITO_DOMAIN &&
      process.env.AMPLIFY_APP_ORIGIN,
  );
}

function amplifyConfig() {
  const origin = process.env.AMPLIFY_APP_ORIGIN ?? "http://localhost:3000";
  return {
    Auth: {
      Cognito: {
        userPoolId: process.env.COGNITO_USER_POOL_ID ?? "",
        userPoolClientId: process.env.COGNITO_APP_CLIENT_ID ?? "",
        loginWith: {
          oauth: {
            domain: process.env.COGNITO_DOMAIN ?? "",
            scopes: ["openid", "email"] as ["openid", "email"],
            redirectSignIn: [`${origin}/api/auth/sign-in-callback`],
            redirectSignOut: [`${origin}/api/auth/sign-out-callback`],
            responseType: "code" as const,
          },
        },
      },
    },
  };
}

let runner: ReturnType<typeof createServerRunner> | null = null;

export function amplifyRunner() {
  if (!cognitoConfigured()) return null;
  if (!runner) {
    runner = createServerRunner({
      config: amplifyConfig(),
      runtimeOptions: {
        cookies: {
          sameSite: "strict",
          maxAge: 28_800,
        },
      },
    });
  }
  return runner;
}

function emailFromIdToken(token: string | undefined): string | null {
  if (!token) return null;
  const segment = token.split(".")[1];
  if (!segment) return null;
  try {
    const payload = JSON.parse(Buffer.from(segment, "base64url").toString("utf8")) as { email?: unknown };
    return typeof payload.email === "string" && payload.email.includes("@") ? payload.email : null;
  } catch {
    return null;
  }
}

export async function readViewer(): Promise<{ email: string } | null> {
  if (isMockAuth()) {
    const jar = await cookies();
    return jar.get(MOCK_COOKIE)?.value === "1" ? { email: MOCK_EMAIL } : null;
  }
  const active = amplifyRunner();
  if (!active) return null;
  try {
    const session = await active.runWithAmplifyServerContext({
      nextServerContext: { cookies },
      operation: (contextSpec) => fetchAuthSession(contextSpec),
    });
    const access = session.tokens?.accessToken?.toString();
    if (!access) return null;
    return { email: emailFromIdToken(session.tokens?.idToken?.toString()) ?? "Staff" };
  } catch {
    return null;
  }
}

export async function readAccessToken(request: NextRequest, response: NextResponse): Promise<string | null> {
  if (isMockAuth()) {
    return request.cookies.get(MOCK_COOKIE)?.value === "1" ? "mock-access-token" : null;
  }
  const active = amplifyRunner();
  if (!active) return null;
  try {
    const session = await active.runWithAmplifyServerContext({
      nextServerContext: { request, response },
      operation: (contextSpec) => fetchAuthSession(contextSpec),
    });
    return session.tokens?.accessToken?.toString() ?? null;
  } catch {
    return null;
  }
}

export { MOCK_COOKIE, MOCK_EMAIL };
