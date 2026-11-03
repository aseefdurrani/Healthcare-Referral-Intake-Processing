import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FieldStatusBadge } from "@/components/referrals/field-status";
import { StatusChip } from "@/components/referrals/status-chip";
import { ApproveDialog } from "@/components/referrals/decision-dialogs";
import { classifyProblem, ApiProblem } from "@/lib/api/problems";
import { inboxPollInterval, statusPollInterval } from "@/lib/api/queries";
import { mockAuthRefused } from "@/lib/auth/mode";
import { safeNextPath } from "@/lib/auth/paths";
import { approvalGaps, emptyDraft } from "@/lib/catalog";
import { uploadErrorMessage, validateUpload } from "@/lib/upload-rules";
import type { ReferralDetail } from "@/lib/api/types";

describe("upload rules", () => {
  it("rejects a word document and an oversized file", () => {
    expect(validateUpload({ name: "notes.docx", type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", size: 100 })).toBe("type");
    expect(uploadErrorMessage("notes.docx", "type")).toContain("PDF, PNG, or JPEG");
    expect(validateUpload({ name: "scan.pdf", type: "application/pdf", size: 11_000_000 })).toBe("size");
    expect(validateUpload({ name: "scan.pdf", type: "application/pdf", size: 10_000_000 })).toBeNull();
  });
});

describe("problem mapping", () => {
  function problem(status: number, code: string) {
    return new ApiProblem({ status, code, correlationId: "11111111-1111-4111-8111-111111111111", detail: "detail" });
  }
  it("classifies the status codes the review screen handles", () => {
    expect(classifyProblem(problem(401, "TOKEN_EXPIRED"))).toBe("session");
    expect(classifyProblem(problem(403, "NOT_AUTHORIZED"))).toBe("forbidden");
    expect(classifyProblem(problem(404, "REFERRAL_NOT_FOUND"))).toBe("not_found");
    expect(classifyProblem(problem(409, "VERSION_CONFLICT"))).toBe("conflict");
    expect(classifyProblem(problem(413, "FILE_TOO_LARGE"))).toBe("validation");
    expect(classifyProblem(problem(503, "AUTH_UNAVAILABLE"))).toBe("unavailable");
    expect(classifyProblem(problem(500, "INTERNAL_ERROR"))).toBe("server");
  });
});

describe("auth mode", () => {
  it("refuses mock auth on a demo build", () => {
    expect(mockAuthRefused("mock", "local", "production")).toBe(true);
    expect(mockAuthRefused("mock", "aws-demo", "production")).toBe(true);
    expect(mockAuthRefused("mock", "test", "production")).toBe(false);
    expect(mockAuthRefused("mock", "local", "development")).toBe(false);
    expect(mockAuthRefused("cognito", "local", "production")).toBe(false);
  });
});

describe("polling", () => {
  it("polls status every 2 seconds only while work is in progress", () => {
    expect(statusPollInterval("uploaded")).toBe(2000);
    expect(statusPollInterval("processing")).toBe(2000);
    expect(statusPollInterval("needs_review")).toBe(false);
    expect(statusPollInterval("approved")).toBe(false);
    expect(statusPollInterval("rejected")).toBe(false);
    expect(statusPollInterval("failed")).toBe(false);
    expect(inboxPollInterval(["needs_review", "processing"])).toBe(10_000);
    expect(inboxPollInterval(["approved"])).toBe(false);
  });
});

describe("return paths", () => {
  it("keeps only opaque referral paths", () => {
    expect(safeNextPath("/referrals")).toBe("/referrals");
    expect(safeNextPath("/referrals/11111111-1111-4111-8111-111111111111")).toContain("/referrals/");
    expect(safeNextPath("/referrals?patient=Sample")).toBe("/referrals");
    expect(safeNextPath("//evil.example")).toBe("/referrals");
  });
});

const detail = {
  id: "11111111-1111-4111-8111-111111111111",
  status: "needs_review",
  version: 1,
  created_at: "2026-11-03T14:00:00Z",
  updated_at: "2026-11-03T14:00:00Z",
  document: { id: "doc", display_filename: "sample.pdf", content_type: "application/pdf", size_bytes: 10, page_count: 1 },
  patient_name: null,
  suggested_specialty: "cardiology",
  blocking_flag_count: 1,
  warning_flag_count: 0,
  possible_duplicate_of: null,
  allowed_actions: ["edit", "approve", "reject"],
  fields: [
    { key: "patient.full_name", label: "Patient name", ocr_value: null, suggested_value: null, confirmed_value: null, effective_value: null, field_status: "not_found", readability: null, source: null, edited: false },
    { key: "patient.date_of_birth", label: "Date of birth", ocr_value: "03/14/1958", suggested_value: null, confirmed_value: null, effective_value: null, field_status: "conflicting", readability: null, source: null, edited: false },
  ],
  flags: [],
  specialty: { label: "cardiology", display_name: "Cardiology", rationale: "Chest pain is written on the page.", candidates: [] },
  specialty_decision: null,
  destination_specialty: null,
  current_run: null,
  history: [],
} as ReferralDetail;

describe("approval gate", () => {
  it("lists a missing name and an unresolved conflict", () => {
    const gaps = approvalGaps(detail, { ...emptyDraft(detail), destination: "cardiology", priority: "routine" }, "2026-11-03");
    expect(gaps.map((gap) => gap.message).join(" ")).toMatch(/Patient name/);
    expect(gaps.map((gap) => gap.message).join(" ")).toMatch(/conflicts/);
  });
});

describe("status and field text", () => {
  it("always shows status words", () => {
    render(<StatusChip status="failed" />);
    expect(screen.getByText("Failed")).toBeTruthy();
    render(<StatusChip status="rejected" />);
    expect(screen.getByText("Rejected")).toBeTruthy();
    render(<FieldStatusBadge status="explicit" edited={false} readability={null} />);
    expect(screen.getByText("Explicit")).toBeTruthy();
    render(<FieldStatusBadge status="conflicting" edited={false} readability={null} />);
    expect(screen.getByText("Conflicting")).toBeTruthy();
    render(<FieldStatusBadge status="normalized" edited={true} readability={null} />);
    expect(screen.getByText("Edited")).toBeTruthy();
  });

  it("shows the approval dialog gaps", () => {
    render(
      <ApproveDialog
        open
        detail={detail}
        draft={{ ...emptyDraft(detail), destination: "cardiology", priority: "routine" }}
        onDraft={() => undefined}
        onClose={() => undefined}
        onApprove={() => undefined}
        pending={false}
        serverMessages={[]}
      />,
    );
    expect(screen.getByText(/Patient name needs a real value/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" })).toHaveProperty("disabled", true);
  });
});
