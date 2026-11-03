import type { ProcessingStep, ReferralStatus } from "@/lib/api/types";

const STEPS = [
  { id: "uploaded", label: "Uploaded" },
  { id: "reading_text", label: "Read text" },
  { id: "structuring", label: "Structured" },
  { id: "review", label: "Review" },
  { id: "done", label: "Done" },
] as const;

function stateFor(status: ReferralStatus, step: ProcessingStep | null, failedStep: ProcessingStep | null) {
  return STEPS.map((item) => {
    if (status === "approved" || status === "rejected") return { ...item, state: "done" as const };
    if (status === "failed") {
      const failed = failedStep === "reading_text" ? "reading_text" : failedStep === "validating" ? "structuring" : "structuring";
      const order = ["uploaded", "reading_text", "structuring", "review", "done"];
      const failedIndex = order.indexOf(failed === "reading_text" && item.id === "reading_text" ? item.id : failed);
      if (item.id === "uploaded") return { ...item, state: "done" as const };
      if ((failedStep === "reading_text" && item.id === "reading_text") || (failedStep !== "reading_text" && item.id === "structuring")) {
        return { ...item, state: "failed" as const };
      }
      if (failedStep === "reading_text") return { ...item, state: "waiting" as const };
      if (item.id === "reading_text") return { ...item, state: "done" as const };
      return { ...item, state: "waiting" as const, failedIndex };
    }
    if (status === "needs_review") {
      if (item.id === "review") return { ...item, state: "current" as const };
      if (item.id === "done") return { ...item, state: "waiting" as const };
      return { ...item, state: "done" as const };
    }
    if (item.id === "uploaded") return { ...item, state: "done" as const };
    if (status === "uploaded") return { ...item, state: "waiting" as const };
    if (step === "reading_text" && item.id === "reading_text") return { ...item, state: "current" as const };
    if (step === "structuring" && item.id === "structuring") return { ...item, state: "current" as const };
    if (step === "validating" && item.id === "structuring") return { ...item, state: "current" as const, label: "Checking fields" };
    if (step === "structuring" && item.id === "reading_text") return { ...item, state: "done" as const };
    if (step === "validating" && item.id === "reading_text") return { ...item, state: "done" as const };
    return { ...item, state: "waiting" as const };
  });
}

export function ProcessingSteps({
  status,
  step,
  failedStep = null,
}: {
  status: ReferralStatus;
  step: ProcessingStep | null;
  failedStep?: ProcessingStep | null;
}) {
  const items = stateFor(status, step, failedStep);
  return (
    <ol className="flex flex-wrap gap-3 text-sm" aria-label="Processing progress">
      {items.map((item) => (
        <li key={item.id} className="flex items-center gap-2">
          <span aria-hidden="true">
            {item.state === "done" ? "[x]" : item.state === "current" ? "[~]" : item.state === "failed" ? "[!]" : "[ ]"}
          </span>
          <span>
            {item.label}
            {item.state === "current" ? " in progress" : item.state === "failed" ? " did not finish" : item.state === "waiting" ? "" : " complete"}
          </span>
        </li>
      ))}
    </ol>
  );
}
