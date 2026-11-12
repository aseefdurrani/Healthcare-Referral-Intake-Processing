"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApproveDialog, RejectDialog } from "@/components/referrals/decision-dialogs";
import { FieldGroups } from "@/components/referrals/field-groups";
import type { HighlightTarget } from "@/components/referrals/document-viewer";
import { ProcessingSteps } from "@/components/referrals/processing-steps";
import { ReferenceId } from "@/components/referrals/reference-id";
import { StatusChip } from "@/components/referrals/status-chip";
import { ApiProblem, classifyProblem } from "@/lib/api/problems";
import { useReferral, useReferralDocument, useReferralMutations, useReferralStatus } from "@/lib/api/queries";
import { emptyDraft, flagKind, flagRank, FIELD_CATALOG, specialtyLabel, type ApprovalDraft } from "@/lib/catalog";
import { formatClock, formatElapsed, formatReceived, historySummary } from "@/lib/format";
import type { Priority, ReasonCode } from "@/lib/api/types";

const DocumentViewer = dynamic(
  () => import("@/components/referrals/document-viewer").then((mod) => mod.DocumentViewer),
  { ssr: false, loading: () => <p>Loading document</p> },
);

export function ReferralWorkspace({ referralId }: { referralId: string }) {
  const detailQuery = useReferral(referralId);
  const statusQuery = useReferralStatus(referralId);
  const documentQuery = useReferralDocument(referralId);
  const mutations = useReferralMutations(referralId);
  const queryClient = useQueryClient();
  const detail = detailQuery.data;
  const polled = statusQuery.data?.status;
  const status = statusQuery.data && (!detail || statusQuery.data.version >= detail.version)
    ? polled
    : detail?.status;
  const [highlight, setHighlight] = useState<HighlightTarget | null>(null);
  const [showDocument, setShowDocument] = useState(true);
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [draft, setDraft] = useState<ApprovalDraft | null>(null);
  const [serverMessages, setServerMessages] = useState<string[]>([]);
  const [conflict, setConflict] = useState(false);
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  if (detail && detail.status !== "uploaded" && detail.status !== "processing" && !draft) {
    setDraft(emptyDraft(detail));
  }

  useEffect(() => {
    document.title = `${referralId} · Referral Intake`;
  }, [referralId]);

  useEffect(() => {
    if (status !== "uploaded" && status !== "processing") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [status]);

  useEffect(() => {
    const snapshot = statusQuery.data;
    if (!snapshot || !detail) return;
    if (snapshot.version > detail.version || (snapshot.version === detail.version && snapshot.status !== detail.status)) {
      void queryClient.invalidateQueries({ queryKey: ["referral", referralId], exact: true });
      void queryClient.invalidateQueries({ queryKey: ["referrals"] });
    }
  }, [statusQuery.data, detail, queryClient, referralId]);

  if (detailQuery.isLoading) {
    return (
      <div className="space-y-3" aria-hidden="true">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (detailQuery.error instanceof ApiProblem && classifyProblem(detailQuery.error) === "not_found") {
    return (
      <div>
        <h1 className="text-xl font-semibold">Referral not found</h1>
        <Link href="/referrals" className="text-primary">
          Back to referrals
        </Link>
      </div>
    );
  }
  if (!detail || detailQuery.error) {
    const problem = detailQuery.error instanceof ApiProblem ? detailQuery.error : null;
    return (
      <div role="alert">
        <p>This referral could not be loaded.</p>
        <Button type="button" onClick={() => void detailQuery.refetch()}>
          Retry
        </Button>
        {problem?.correlationId ? <ReferenceId id={problem.correlationId} /> : null}
      </div>
    );
  }

  const step = statusQuery.data?.processing_step ?? null;
  const failure = statusQuery.data?.failure ?? null;
  const title = detail.patient_name ? `Referral for ${detail.patient_name}` : `Referral ${detail.id}`;
  const received = formatReceived(detail.created_at);
  const slow = (status === "uploaded" || status === "processing") && now - startedAt >= 60_000;
  const readOnly = !detail.allowed_actions.includes("edit");
  const openFlags = detail.flags
    .filter((flag) => flag.status === "open")
    .sort((left, right) => flagRank(left.code) - flagRank(right.code));
  const settled = detail.flags.filter((flag) => flag.status !== "open").length;

  function onMutationError(error: unknown) {
    if (error instanceof ApiProblem && classifyProblem(error) === "conflict") setConflict(true);
    if (error instanceof ApiProblem && classifyProblem(error) === "validation") {
      setServerMessages(error.errors.length > 0 ? error.errors.map((item) => item.msg) : [error.detail]);
      setApproveOpen(true);
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Link href="/referrals" className="cursor-pointer text-sm font-medium text-primary underline-offset-2 hover:underline">
          Referrals
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold leading-7">{title}</h1>
          <span className="font-mono text-sm" title={detail.id}>
            {detail.id.slice(0, 8)}
          </span>
          <StatusChip status={status ?? detail.status} step={step} />
        </div>
        <p className="text-sm text-text-muted">
          {detail.document.display_filename} · uploaded {received.label}
        </p>
        <p className="large-screen-note text-sm text-text-muted">This app works best on a larger screen.</p>
        <ProcessingSteps status={status ?? detail.status} step={step} failedStep={failure?.step ?? null} />
      </div>
      {conflict ? (
        <div role="alert" className="rounded-panel border border-border bg-surface p-4">
          <p>This referral changed since you opened it.</p>
          <Button
            type="button"
            onClick={() => {
              setConflict(false);
              void detailQuery.refetch();
              void statusQuery.refetch();
            }}
          >
            Reload
          </Button>
        </div>
      ) : null}
      <div className="review-split gap-6">
        <div className="document-column min-w-0 self-start">
          <Button type="button" variant="outline" className="doc-toggle mb-3" onClick={() => setShowDocument((value) => !value)}>
            {showDocument ? "Hide document" : "Show document"}
          </Button>
          {showDocument && documentQuery.data ? (
            <DocumentViewer
              key={detail.document.id}
              blob={documentQuery.data.blob}
              mediaType={documentQuery.data.mediaType || detail.document.content_type}
              pageCount={detail.document.page_count}
              highlight={highlight}
            />
          ) : null}
          {documentQuery.isLoading ? <Skeleton className="h-80 w-full" /> : null}
          {documentQuery.error ? (
            <div role="alert" className="rounded-panel border border-border bg-surface p-4">
              <p>{documentQuery.error instanceof ApiProblem && documentQuery.error.code === "AWS_SESSION_EXPIRED"
                ? "The local document service session expired. Reconnect AWS, then retry the document."
                : "The document could not be loaded."}</p>
              <Button variant="outline" onClick={() => void documentQuery.refetch()}>Retry document</Button>
            </div>
          ) : null}
        </div>
        <div className="min-w-0 space-y-6">
          {statusQuery.error ? (
            <div role="alert" className="rounded-panel border border-border bg-surface p-4">
              <p>Status updates are unavailable. Your document is saved.</p>
              <Button variant="outline" onClick={() => void statusQuery.refetch()}>Retry status</Button>
            </div>
          ) : null}
          {status === "uploaded" || status === "processing" ? (
            <section aria-live="polite">
              <h2 className="text-base font-semibold">{step === "reading_text" || status === "uploaded" ? "Reading the document" : step === "validating" ? "Checking fields" : "Structuring fields"}</h2>
              <p className="mt-1 text-sm text-text-muted">
                {step === "reading_text" || status === "uploaded"
                  ? "The original document is available while the text is read."
                  : "Text has been read. Now organizing fields and suggesting a specialty."}
              </p>
              <p className="tabular mt-2 text-sm">Elapsed {formatElapsed(now - startedAt)}</p>
              {slow ? (
                <p className="mt-3 text-sm">Taking longer than usual. You can leave this page; processing continues.</p>
              ) : null}
              <div className="mt-4 space-y-2" aria-hidden="true">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
              </div>
            </section>
          ) : null}
          {status === "failed" && failure ? (
            <section role="alert" className="space-y-3">
              <h2 className="text-base font-semibold">Processing did not finish</h2>
              <p>{failure.message}</p>
              <p className="text-sm text-text-muted">Your document is saved.</p>
              <ReferenceId id={failure.run_id} label="Run" />
              <div className="flex gap-2">
                {failure.retryable && detail.allowed_actions.includes("retry") ? (
                  <Button type="button" onClick={() => mutations.retry.mutate(detail.version, { onError: onMutationError })}>
                    Retry processing
                  </Button>
                ) : null}
                {detail.allowed_actions.includes("reject") ? (
                  <Button type="button" variant="outline" onClick={() => setRejectOpen(true)}>
                    Reject
                  </Button>
                ) : null}
              </div>
            </section>
          ) : null}
          {status === "approved" || status === "rejected" ? (
            <section className="rounded-panel border border-border bg-surface-tint p-4">
              <h2 className="text-base font-semibold">{status === "approved" ? "Approved" : "Rejected"}</h2>
              <p className="mt-1 text-sm">
                {detail.destination_specialty
                  ? `Destination ${specialtyLabel(detail.destination_specialty.code === "other" ? null : detail.destination_specialty.code)}${detail.destination_specialty.other_name ? ` ${detail.destination_specialty.other_name}` : ""}. `
                  : null}
                Recorded in this demo only; nothing was sent to a department.
              </p>
            </section>
          ) : null}
          {status === "needs_review" && detail.specialty ? (
            <section className="rounded-panel border border-border bg-surface-tint p-4">
              <p className="text-xs font-medium text-text-muted">Suggested specialty</p>
              <h2 className="text-base font-semibold">{detail.specialty.display_name}</h2>
              {detail.specialty.rationale ? <p className="mt-2 text-sm">{detail.specialty.rationale}</p> : null}
              {detail.specialty.label === "uncertain"
                ? detail.specialty.candidates.slice(0, 2).map((candidate) => (
                    <p key={candidate.label} className="mt-1 text-sm text-text-muted">
                      {specialtyLabel(candidate.label)}: {candidate.reason}
                    </p>
                  ))
                : null}
              {draft && draft.destination && draft.destination !== detail.specialty.label ? (
                <p className="mt-2 text-xs font-medium">Changed from AI suggestion</p>
              ) : null}
            </section>
          ) : null}
          {status === "needs_review" ? (
            <section>
              <h2 className="text-base font-semibold">
                {openFlags.length === 0
                  ? "Nothing else is flagged"
                  : openFlags.length === 1
                    ? "1 review item needs your attention"
                    : `${openFlags.length} review items need your attention`}
              </h2>
              <p className="mt-1 text-sm text-text-muted">Warnings below explain what to verify. Optional fields marked “Not found” do not automatically require action.</p>
              <ul className="mt-3 divide-y divide-border overflow-hidden rounded-panel border border-border bg-surface">
                {openFlags.map((flag) => (
                  <li key={flag.id} className="flex items-start justify-between gap-3 p-3 text-sm">
                    <span>
                      <span className="block font-medium">{flag.field_key ? (FIELD_CATALOG.find((field) => field.key === flag.field_key)?.label ?? flag.field_key) : "Document / specialty"}</span>
                      <span className="mt-1 block text-text-muted">{flag.message}</span>
                      <span className="mt-1 block text-xs text-attention">{flagKind(flag.code)}</span>
                    </span>
                    {flag.field_key ? (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => document.getElementById(`field-${flag.field_key}`)?.scrollIntoView({ block: "center" })}
                      >
                        View field
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
              {settled > 0 ? <p className="mt-2 text-sm text-text-muted">{settled} resolved or acknowledged</p> : null}
            </section>
          ) : null}
          {status === "needs_review" || status === "approved" || status === "rejected" ? (
            <FieldGroups
              fields={detail.fields}
              flags={openFlags}
              readOnly={readOnly}
              saving={mutations.edit.isPending}
              onJump={(target) => { setShowDocument(true); setHighlight(target); }}
              onSave={(key, value) => {
                mutations.edit.mutate(
                  { version: detail.version, fields: [{ key, value }] },
                  { onError: onMutationError, onSuccess: () => setDraft(null) },
                );
              }}
            />
          ) : null}
          <details className="rounded-panel border border-border bg-surface p-4">
            <summary className="cursor-pointer text-base font-semibold">History</summary>
            <ol className="mt-3 space-y-2">
              {detail.history.map((event, index) => (
                <li key={`${event.at}-${event.action}-${index}`} className="grid grid-cols-[auto_auto_minmax(0,1fr)] gap-3 text-sm">
                  <span className="font-mono tabular">{formatClock(event.at)}</span>
                  <span>{event.actor === "staff" ? "Staff" : "System"}</span>
                  <span>{historySummary(event.action, event.reason)}</span>
                </li>
              ))}
            </ol>
          </details>
        </div>
      </div>
      {status === "needs_review" || (status === "failed" && detail.allowed_actions.includes("reject")) ? (
        <div className="sticky bottom-0 z-10 -mx-6 -mb-6 flex items-center justify-end gap-2 border-t border-border bg-surface px-6 py-3">
          <Button type="button" variant="outline" onClick={() => setRejectOpen(true)} disabled={!detail.allowed_actions.includes("reject")}>
            Reject
          </Button>
          {status === "needs_review" ? (
            <Button type="button" onClick={() => setApproveOpen(true)} disabled={!detail.allowed_actions.includes("approve")}>
              Approve
            </Button>
          ) : null}
        </div>
      ) : null}
      {draft ? (
        <ApproveDialog
          open={approveOpen}
          detail={detail}
          draft={draft}
          onDraft={setDraft}
          onClose={() => setApproveOpen(false)}
          pending={mutations.approve.isPending}
          serverMessages={serverMessages}
          onApprove={() => {
            mutations.approve.mutate(
              {
                version: detail.version,
                destination_specialty: {
                  code: draft.destination,
                  other_name: draft.destination === "other" ? draft.otherName.trim() : null,
                },
                sender_priority: draft.priority as Priority,
                not_on_document: draft.absent,
                acknowledgements: Object.entries(draft.acknowledgements)
                  .filter(([, reason]) => reason.trim().length >= 3)
                  .map(([flag_id, reason]) => ({ flag_id, reason: reason.trim() })),
              },
              {
                onError: onMutationError,
                onSuccess: () => setApproveOpen(false),
              },
            );
          }}
        />
      ) : null}
      <RejectDialog
        open={rejectOpen}
        pending={mutations.reject.isPending}
        onClose={() => setRejectOpen(false)}
        onReject={(reason: ReasonCode, note) => {
          mutations.reject.mutate(
            { version: detail.version, reason_code: reason, note: note || undefined },
            { onError: onMutationError, onSuccess: () => setRejectOpen(false) },
          );
        }}
      />
    </div>
  );
}
