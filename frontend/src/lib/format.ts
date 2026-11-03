const PRIORITIES = new Set(["urgent", "routine", "other", "not_marked"]);

export function formatReceived(iso: string, now = new Date()): { label: string; full: string } {
  const date = new Date(iso);
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
  const full = new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const day = new Date(date);
  day.setHours(0, 0, 0, 0);
  const diff = (start.getTime() - day.getTime()) / 86_400_000;
  if (diff === 0) return { label: `Today ${time}`, full };
  if (diff === 1) return { label: `Yesterday ${time}`, full };
  const month = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(date);
  return { label: `${month}, ${time}`, full };
}

export function formatClock(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

export function formatClinicalDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return value;
  return `${match[2]}/${match[3]}/${match[1]}`;
}

export function isPriority(value: string | null | undefined): value is "urgent" | "routine" | "other" | "not_marked" {
  return typeof value === "string" && PRIORITIES.has(value);
}

export function historySummary(action: string, reason: string | null): string {
  const summaries: Record<string, string> = {
    uploaded: "Uploaded the referral document",
    processing_started: "Processing started",
    processing_failed: "Processing did not finish",
    ready_for_review: "Ready for review",
    referral_viewed: "Opened the referral",
    document_viewed: "Opened the document",
    field_corrected: "Edited a field",
    specialty_changed: "Changed the destination specialty",
    approved: "Approved the referral",
    rejected: "Rejected the referral",
    reprocess_requested: "Requested another processing attempt",
  };
  const base = summaries[action] ?? "Recorded an update";
  if (action === "rejected" && reason && isPriority(reason) === false && /^[a-z_]+$/.test(reason)) {
    return `${base} (${reason.replaceAll("_", " ")})`;
  }
  return base;
}
