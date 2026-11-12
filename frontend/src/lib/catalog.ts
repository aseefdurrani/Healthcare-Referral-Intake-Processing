import type { ProcessingStep, ReferralDetail, ReferralField, ReferralStatus } from "./api/types";

export const DESTINATIONS = [
  ["cardiology", "Cardiology"],
  ["dermatology", "Dermatology"],
  ["gastroenterology", "Gastroenterology"],
  ["neurology", "Neurology"],
  ["orthopedics", "Orthopedics"],
  ["pulmonology", "Pulmonology"],
  ["other", "Other"],
] as const;

export const PRIORITY_OPTIONS = [
  ["urgent", "Urgent"],
  ["routine", "Routine"],
  ["other", "Other"],
  ["not_marked", "Not marked"],
] as const;

export const SPECIALTY_LABELS: Record<string, string> = {
  cardiology: "Cardiology",
  dermatology: "Dermatology",
  gastroenterology: "Gastroenterology",
  neurology: "Neurology",
  orthopedics: "Orthopedics",
  pulmonology: "Pulmonology",
  other_specialty: "Other specialty",
  uncertain: "Uncertain",
  other: "Other",
  none_stated: "None stated",
};

const CLINICAL = new Set<string>(DESTINATIONS.map(([code]) => code).filter((code) => code !== "other"));

export function specialtyLabel(code: string | null | undefined): string {
  if (!code) return "—";
  return SPECIALTY_LABELS[code] ?? code;
}

export function chipText(status: ReferralStatus, step: ProcessingStep | null = null): string {
  if (status === "uploaded") return "Uploaded";
  if (status === "processing" && step === "reading_text") return "Reading text";
  if (status === "processing" && step === "structuring") return "Structuring";
  if (status === "processing" && step === "validating") return "Checking fields";
  if (status === "processing") return "Processing";
  if (status === "needs_review") return "Ready for review";
  if (status === "approved") return "Approved";
  if (status === "rejected") return "Rejected";
  return "Failed";
}

export function fieldValueType(key: string): "text" | "multiline" | "date" | "phone" | "code" {
  if (key.endsWith("date_of_birth") || key.endsWith("referral_date")) return "date";
  if (key.endsWith("phone") || key.endsWith("fax")) return "phone";
  if (key.endsWith("reason") || key.endsWith("address")) return "multiline";
  if (key.endsWith("npi") || key.endsWith("diagnosis_codes") || key.endsWith("identifiers")) return "code";
  return "text";
}

export const FIELD_CATALOG: { key: string; label: string; group: string; groupLabel: string }[] = [
  ["patient.full_name", "Patient name"],
  ["patient.date_of_birth", "Date of birth"],
  ["patient.phone", "Patient phone"],
  ["patient.email", "Patient email"],
  ["patient.address", "Patient address"],
  ["patient.identifiers", "Patient identifiers"],
].map(([key, label]) => ({ key, label, group: "patient", groupLabel: "Patient" })).concat(
  [
    ["referring_clinician.name", "Referring clinician"],
    ["referring_clinician.organization", "Referring organization"],
    ["referring_clinician.phone", "Referring clinician phone"],
    ["referring_clinician.fax", "Referring clinician fax"],
    ["referring_clinician.npi", "Referring clinician NPI"],
  ].map(([key, label]) => ({ key, label, group: "referring", groupLabel: "Referring provider" })),
  [
    ["referral.referral_date", "Referral date"],
    ["referral.reason", "Reason for referral"],
    ["referral.sender_priority", "Sender priority"],
    ["referral.requested_specialty", "Requested specialty"],
    ["referral.diagnosis_codes", "Diagnosis codes"],
  ].map(([key, label]) => ({ key, label, group: "referral", groupLabel: "Referral" })),
);

export function displayFields(fields: ReferralField[]): ReferralField[] {
  const byKey = new Map(fields.map((field) => [field.key, field]));
  const known = new Set(FIELD_CATALOG.map((item) => item.key));
  const ordered = FIELD_CATALOG.map((item) => {
    return (
      byKey.get(item.key) ?? {
        key: item.key,
        label: item.label,
        ocr_value: null,
        suggested_value: null,
        confirmed_value: null,
        effective_value: null,
        field_status: "not_found" as const,
        readability: null,
        source: null,
        edited: false,
      }
    );
  });
  for (const field of fields) {
    if (!known.has(field.key)) ordered.push(field);
  }
  return ordered;
}

export function groupFields(fields: ReferralField[]): { id: string; label: string; fields: ReferralField[] }[] {
  const groups = new Map<string, { id: string; label: string; fields: ReferralField[] }>();
  for (const field of displayFields(fields)) {
    const catalog = FIELD_CATALOG.find((item) => item.key === field.key);
    const id = catalog?.group ?? "other";
    const label = catalog?.groupLabel ?? "Other";
    const group = groups.get(id) ?? { id, label, fields: [] };
    group.fields.push(field);
    groups.set(id, group);
  }
  return [...groups.values()];
}

const ABSENT_ALLOWED = new Set([
  "patient.phone",
  "patient.email",
  "patient.address",
  "referring_clinician.name",
  "referring_clinician.phone",
  "referring_clinician.fax",
  "referral.reason",
]);

const ACK_CODES = new Set([
  "specialty_mismatch",
  "request_unclear",
  "multiple_patients_suspected",
  "embedded_instructions_detected",
  "illegible_or_incomplete_content",
  "not_a_referral",
]);

const CONTACT = ["patient.phone", "patient.email", "patient.address"];
const REACH = ["referring_clinician.phone", "referring_clinician.fax"];

export interface ApprovalDraft {
  destination: string;
  otherName: string;
  priority: string;
  absent: string[];
  acknowledgements: Record<string, string>;
}

export interface ApprovalGap {
  id: string;
  message: string;
}

export function emptyDraft(detail: ReferralDetail): ApprovalDraft {
  const suggestion = detail.specialty?.label ?? "";
  const saved = detail.destination_specialty;
  const priorityField = detail.fields.find((field) => field.key === "referral.sender_priority");
  const priority = priorityField?.effective_value ?? "";
  return {
    destination: saved?.code || (CLINICAL.has(suggestion) ? suggestion : ""),
    otherName: saved?.other_name ?? "",
    priority: ["urgent", "routine", "other", "not_marked"].includes(priority) ? priority : "",
    absent: [],
    acknowledgements: {},
  };
}

function textOf(detail: ReferralDetail, key: string, absent: Set<string>): string | null {
  if (absent.has(key)) return null;
  const field = detail.fields.find((item) => item.key === key);
  const value = field?.effective_value?.trim();
  return value ? value : null;
}

function plausibleDob(value: string, today: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const born = new Date(`${value}T00:00:00`);
  const current = new Date(`${today}T00:00:00`);
  if (Number.isNaN(born.getTime()) || born > current) return false;
  let age = current.getFullYear() - born.getFullYear();
  const beforeBirthday =
    current.getMonth() < born.getMonth() ||
    (current.getMonth() === born.getMonth() && current.getDate() < born.getDate());
  if (beforeBirthday) age -= 1;
  return age <= 120;
}

export function approvalGaps(detail: ReferralDetail, draft: ApprovalDraft, today = new Date().toISOString().slice(0, 10)): ApprovalGap[] {
  const absent = new Set(draft.absent.filter((key) => ABSENT_ALLOWED.has(key)));
  const gaps: ApprovalGap[] = [];
  const label = (key: string) => detail.fields.find((field) => field.key === key)?.label ?? key;
  if (!textOf(detail, "patient.full_name", absent)) {
    gaps.push({ id: "patient.full_name", message: "Patient name needs a real value. It cannot be confirmed as not on the document." });
  }
  const dob = textOf(detail, "patient.date_of_birth", absent);
  if (!dob || !plausibleDob(dob, today)) {
    gaps.push({ id: "patient.date_of_birth", message: "Date of birth needs a real past date. It cannot be confirmed as not on the document." });
  }
  for (const field of detail.fields) {
    if (field.field_status === "conflicting" && !(field.confirmed_value && field.confirmed_value.trim())) {
      gaps.push({ id: field.key, message: `${label(field.key)} conflicts. Edit a value.` });
    }
  }
  const contactOk = CONTACT.some((key) => textOf(detail, key, absent)) || CONTACT.every((key) => absent.has(key));
  if (!contactOk) {
    gaps.push({ id: "patient.phone", message: "Add a patient phone, address, or email, or confirm that none is on the document." });
  }
  if (!textOf(detail, "referring_clinician.name", absent) && !absent.has("referring_clinician.name")) {
    gaps.push({ id: "referring_clinician.name", message: "Add the referring clinician, or confirm the name is not on the document." });
  }
  const reachOk = REACH.some((key) => textOf(detail, key, absent)) || REACH.every((key) => absent.has(key));
  if (!reachOk) {
    gaps.push({ id: "referring_clinician.phone", message: "Add a clinician phone or fax, or confirm that neither is on the document." });
  }
  if (!textOf(detail, "referral.reason", absent) && !absent.has("referral.reason")) {
    gaps.push({ id: "referral.reason", message: "Add the reason for referral, or confirm it is not on the document." });
  }
  if (!["urgent", "routine", "other", "not_marked"].includes(draft.priority)) {
    gaps.push({ id: "sender_priority", message: "Confirm the sender priority." });
  }
  if (!CLINICAL.has(draft.destination) && draft.destination !== "other") {
    gaps.push({ id: "destination", message: "Choose a destination specialty." });
  } else if (draft.destination === "other" && !draft.otherName.trim()) {
    gaps.push({ id: "destination", message: "Enter a name for the other destination specialty." });
  }
  for (const flag of detail.flags) {
    if (flag.status === "open" && ACK_CODES.has(flag.code)) {
      const note = draft.acknowledgements[flag.id]?.trim() ?? "";
      if (note.length < 3) gaps.push({ id: flag.id, message: `${flag.message} Write an acknowledgement.` });
    }
  }
  return gaps;
}

export function flagRank(code: string): number {
  if (code.includes("MISSING") || code.includes("INVALID_DATE")) return 0;
  if (code.includes("CONFLICT")) return 1;
  if (code.includes("AMBIG")) return 2;
  return 3;
}

export function flagKind(code: string): string {
  const normalized = code.toLowerCase();
  if (normalized.includes("evidence") || normalized.includes("source") || normalized.includes("unsupported") || normalized.includes("unverified")) return "Source verification";
  if (normalized.includes("confidence")) return "OCR confidence";
  if (code.includes("MISSING") || code.includes("INVALID_DATE")) return "Missing";
  if (code.includes("CONFLICT")) return "Conflict";
  if (code.includes("AMBIG")) return "Ambiguous";
  return "Review check";
}

export function defaultDestination(detail: ReferralDetail): string {
  const suggestion = detail.specialty?.label ?? "";
  return CLINICAL.has(suggestion) ? suggestion : "";
}

export { ABSENT_ALLOWED, ACK_CODES, CLINICAL };
