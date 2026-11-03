export type ReferralStatus =
  | "uploaded"
  | "processing"
  | "needs_review"
  | "failed"
  | "approved"
  | "rejected";

export type ProcessingStep = "reading_text" | "structuring" | "validating";
export type FieldStatus = "explicit" | "normalized" | "not_found" | "ambiguous" | "conflicting";
export type Readability = "verify" | "unreadable";
export type Priority = "urgent" | "routine" | "other" | "not_marked";
export type ReasonCode = "not_a_referral" | "unreadable" | "duplicate" | "incomplete" | "other";
export type DestinationCode =
  | "cardiology"
  | "dermatology"
  | "gastroenterology"
  | "neurology"
  | "orthopedics"
  | "pulmonology"
  | "other";

export interface BoundingBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface SourceRef {
  page: number;
  element_ids: string[];
  bbox: BoundingBox;
}

export interface ReferralField {
  key: string;
  label: string;
  ocr_value: string | null;
  suggested_value: string | null;
  confirmed_value: string | null;
  effective_value: string | null;
  field_status: FieldStatus;
  readability: Readability | null;
  source: SourceRef | null;
  edited: boolean;
}

export interface ValidationFlag {
  id: string;
  code: string;
  severity: "blocking" | "warning";
  field_key: string | null;
  message: string;
  status: "open" | "resolved" | "acknowledged";
  acknowledgement_reason: string | null;
}

export interface SpecialtyCandidate {
  label: string;
  reason: string;
}

export interface SpecialtySuggestion {
  label: string;
  display_name: string;
  rationale: string | null;
  candidates: SpecialtyCandidate[];
}

export interface DestinationSpecialty {
  code: string;
  other_name?: string | null;
}

export interface ProcessingRun {
  id: string;
  attempt: number;
  state: "queued" | "running" | "succeeded" | "failed";
  error_code: string | null;
  retryable: boolean | null;
}

export interface DocumentMeta {
  id: string;
  display_filename: string;
  content_type: string;
  size_bytes: number | null;
  page_count: number | null;
}

export interface ReferralSummary {
  id: string;
  status: ReferralStatus;
  version: number;
  created_at: string;
  updated_at: string;
  document: DocumentMeta;
  patient_name: string | null;
  suggested_specialty: string | null;
  blocking_flag_count: number;
  warning_flag_count: number;
  possible_duplicate_of: string | null;
}

export interface HistoryEvent {
  action: string;
  actor: "staff" | "system";
  from_status: ReferralStatus | null;
  to_status: ReferralStatus | null;
  at: string;
  reason: string | null;
}

export interface ReferralDetail extends ReferralSummary {
  allowed_actions: Array<"edit" | "approve" | "reject" | "retry">;
  fields: ReferralField[];
  flags: ValidationFlag[];
  specialty: SpecialtySuggestion | null;
  specialty_decision: "accept_suggested" | "override" | null;
  destination_specialty: DestinationSpecialty | null;
  current_run: ProcessingRun | null;
  history: HistoryEvent[];
}

export interface ReferralPage {
  items: ReferralSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface FailureSummary {
  step: ProcessingStep | null;
  message: string;
  retryable: boolean;
  run_id: string;
}

export interface ReferralStatusBody {
  id: string;
  status: ReferralStatus;
  version: number;
  processing_step: ProcessingStep | null;
  failure: FailureSummary | null;
}

export interface ProblemBody {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  code?: string;
  correlation_id?: string;
  errors?: { loc: Array<string | number>; msg: string }[] | null;
}
