import type { FieldStatus, Readability } from "@/lib/api/types";

export function FieldStatusBadge({
  status,
  edited,
  readability,
}: {
  status: FieldStatus;
  edited: boolean;
  readability: Readability | null;
}) {
  if (edited) {
    return <span className="text-xs font-medium leading-4 text-text">Edited</span>;
  }
  if (readability === "unreadable") {
    return (
      <span className="inline-flex items-center gap-1 rounded-control bg-danger-bg px-2 py-0.5 text-xs font-medium leading-4 text-danger">
        <span aria-hidden="true">!</span>
        Unreadable
      </span>
    );
  }
  if (status === "conflicting") {
    return (
      <span className="inline-flex items-center gap-1 rounded-control bg-danger-bg px-2 py-0.5 text-xs font-medium leading-4 text-danger">
        <span aria-hidden="true">!</span>
        Conflicting
      </span>
    );
  }
  if (status === "ambiguous" || status === "not_found" || readability === "verify") {
    const label = status === "not_found" ? "Not found" : status === "ambiguous" ? "Ambiguous" : "Verify against the document";
    return (
      <span className="inline-flex items-center gap-1 rounded-control bg-attention-bg px-2 py-0.5 text-xs font-medium leading-4 text-attention">
        <span aria-hidden="true">!</span>
        {label}
      </span>
    );
  }
  const quiet = status === "normalized" ? "Normalized" : "Explicit";
  return <span className="text-xs font-medium leading-4 text-text-muted">{quiet}</span>;
}
