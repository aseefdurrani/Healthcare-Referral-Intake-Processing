import { chipText } from "@/lib/catalog";
import type { ProcessingStep, ReferralStatus } from "@/lib/api/types";

const TONE: Record<string, string> = {
  Approved: "bg-approved-bg text-approved",
  "Ready for review": "bg-review-bg text-review",
  Failed: "bg-danger-bg text-danger",
};

export function StatusChip({
  status,
  step = null,
}: {
  status: ReferralStatus;
  step?: ProcessingStep | null;
}) {
  const text = chipText(status, step);
  const tone = TONE[text] ?? "bg-surface-subtle text-text-muted";
  return (
    <span className={`inline-flex items-center gap-1 rounded-control px-2 py-0.5 text-xs font-medium leading-4 ${tone}`}>
      {text === "Failed" ? <span aria-hidden="true">!</span> : null}
      {text}
    </span>
  );
}
