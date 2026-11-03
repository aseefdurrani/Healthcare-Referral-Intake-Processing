"use client";

import createClient from "openapi-fetch";

import { signInPath } from "@/lib/auth/paths";
import type { paths } from "./schema";
import { ApiProblem, isRetryableProblem, problemFromResponse } from "./problems";
import type {
  DestinationSpecialty,
  Priority,
  ReasonCode,
  ReferralDetail,
  ReferralPage,
  ReferralStatusBody,
  ReferralSummary,
} from "./types";

export const api = createClient<paths>({
  baseUrl: "/api/backend",
});

api.use({
  onRequest({ request }) {
    request.headers.set("X-Request-ID", crypto.randomUUID());
    return request;
  },
});

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!headers.has("X-Request-ID")) headers.set("X-Request-ID", crypto.randomUUID());
  try {
    return await fetch(`/api/backend${path}`, {
      ...init,
      headers,
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch {
    throw new ApiProblem({
      status: 0,
      code: "NETWORK",
      correlationId: headers.get("X-Request-ID") ?? "",
      detail: "The request could not be completed.",
    });
  }
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw await problemFromResponse(response);
  return (await response.json()) as T;
}

export function redirectToSignIn(): void {
  if (typeof window === "undefined") return;
  const next = `${window.location.pathname}`;
  window.location.assign(`${signInPath(next)}`.replace("/sign-in?", "/sign-in?reason=session_expired&"));
}

export async function getMe(): Promise<{ user_id: string; username: "staff" }> {
  return readJson(await send("/me"));
}

export async function listReferralPage(limit: number, offset: number): Promise<ReferralPage> {
  const result = await api.GET("/referrals", { params: { query: { limit, offset } } });
  if (!result.response.ok || !result.data) throw await problemFromResponse(result.response);
  return result.data;
}

export async function listReferrals(): Promise<{ items: ReferralSummary[]; total: number; truncated: boolean }> {
  const first = await listReferralPage(100, 0);
  let items = first.items;
  if (first.total > items.length) {
    const second = await listReferralPage(100, items.length);
    items = items.concat(second.items);
  }
  return { items, total: first.total, truncated: first.total > items.length };
}

export async function getReferral(id: string): Promise<ReferralDetail> {
  const result = await api.GET("/referrals/{id}", { params: { path: { id } } });
  if (!result.response.ok || !result.data) throw await problemFromResponse(result.response);
  return result.data;
}

export async function getReferralStatus(id: string): Promise<ReferralStatusBody> {
  const result = await api.GET("/referrals/{id}/status", { params: { path: { id } } });
  if (!result.response.ok || !result.data) throw await problemFromResponse(result.response);
  return result.data;
}

export async function getDocument(id: string): Promise<{ blob: Blob; mediaType: string }> {
  const response = await send(`/referrals/${id}/document`);
  if (!response.ok) throw await problemFromResponse(response);
  return {
    blob: await response.blob(),
    mediaType: response.headers.get("content-type") ?? "application/octet-stream",
  };
}

export async function patchReferral(
  id: string,
  body: { version: number; fields: { key: string; value: string | null }[] },
): Promise<ReferralDetail> {
  const result = await api.PATCH("/referrals/{id}", {
    params: { path: { id } },
    body: body as never,
  });
  if (!result.response.ok || !result.data) throw await problemFromResponse(result.response);
  return result.data;
}

export async function approveReferral(
  id: string,
  body: {
    version: number;
    destination_specialty: DestinationSpecialty;
    sender_priority: Priority;
    not_on_document: string[];
    acknowledgements: { flag_id: string; reason: string }[];
  },
): Promise<ReferralDetail> {
  const result = await api.POST("/referrals/{id}/approve", {
    params: { path: { id } },
    body: body as never,
  });
  if (!result.response.ok || !result.data) throw await problemFromResponse(result.response);
  return result.data;
}

export async function rejectReferral(
  id: string,
  body: { version: number; reason_code: ReasonCode; note?: string },
): Promise<ReferralDetail> {
  const result = await api.POST("/referrals/{id}/reject", {
    params: { path: { id } },
    body: body as never,
  });
  if (!result.response.ok || !result.data) throw await problemFromResponse(result.response);
  return result.data;
}

export async function retryReferral(id: string, version: number): Promise<ReferralSummary> {
  const result = await api.POST("/referrals/{id}/retry", {
    params: { path: { id } },
    body: { version } as never,
  });
  if (!result.response.ok || !result.data) throw await problemFromResponse(result.response);
  return result.data;
}

export async function uploadReferral(file: File, idempotencyKey: string): Promise<ReferralSummary> {
  const body = new FormData();
  body.set("file", file);
  body.set("attestation", "true");
  const response = await send("/referrals", {
    method: "POST",
    body,
    headers: { "Idempotency-Key": idempotencyKey },
  });
  return readJson(response);
}

export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  return failureCount < 2 && isRetryableProblem(error);
}
