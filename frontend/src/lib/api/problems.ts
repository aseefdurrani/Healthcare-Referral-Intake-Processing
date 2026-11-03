import type { ProblemBody } from "./types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class ApiProblem extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string;
  readonly detail: string;
  readonly errors: { loc: Array<string | number>; msg: string }[];

  constructor(input: {
    status: number;
    code: string;
    correlationId: string;
    detail: string;
    title?: string;
    errors?: { loc: Array<string | number>; msg: string }[];
  }) {
    super(input.detail);
    this.name = "ApiProblem";
    this.status = input.status;
    this.code = input.code;
    this.correlationId = input.correlationId;
    this.detail = input.detail;
    this.errors = input.errors ?? [];
  }
}

export function isUuid(value: string | null | undefined): value is string {
  return typeof value === "string" && UUID.test(value);
}

export function shortReference(value: string): string {
  return value.replace(/-/g, "").slice(0, 8);
}

export async function problemFromResponse(response: Response): Promise<ApiProblem> {
  let body: ProblemBody = {};
  try {
    body = (await response.json()) as ProblemBody;
  } catch {
    body = {};
  }
  return new ApiProblem({
    status: response.status,
    code: body.code ?? "UNKNOWN",
    correlationId: body.correlation_id && isUuid(body.correlation_id) ? body.correlation_id : "",
    detail: body.detail || body.title || "The request could not be completed.",
    title: body.title,
    errors: body.errors ?? [],
  });
}

export type UiErrorKind =
  | "session"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "validation"
  | "unavailable"
  | "server";

export function classifyProblem(problem: ApiProblem): UiErrorKind {
  if (problem.status === 401) return "session";
  if (problem.status === 403 || problem.code === "NOT_AUTHORIZED") return "forbidden";
  if (problem.status === 404) return "not_found";
  if (problem.status === 409) return "conflict";
  if (problem.status === 413 || problem.status === 415 || problem.status === 422) return "validation";
  if (problem.status === 503 || problem.code === "AUTH_UNAVAILABLE") return "unavailable";
  return "server";
}

export function isRetryableProblem(error: unknown): boolean {
  if (error instanceof ApiProblem) {
    return error.status === 0 || error.status >= 500;
  }
  return error instanceof TypeError;
}

export function sessionExpiredMessage(): string {
  return "Your session ended. Sign in again to continue.";
}

export function conflictMessage(): string {
  return "This referral changed since you opened it.";
}
