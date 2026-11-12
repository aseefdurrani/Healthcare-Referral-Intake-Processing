"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ReferenceId } from "@/components/referrals/reference-id";
import { StatusChip } from "@/components/referrals/status-chip";
import { UploadDialog } from "@/components/referrals/upload-dialog";
import { ApiProblem } from "@/lib/api/problems";
import { useReferralList } from "@/lib/api/queries";
import { specialtyLabel } from "@/lib/catalog";
import { formatReceived } from "@/lib/format";
import type { ReferralStatus, ReferralSummary } from "@/lib/api/types";

type Tab = "review" | "processing" | "done" | "failed" | "all";

function inTab(status: ReferralStatus, tab: Tab): boolean {
  if (tab === "all") return true;
  if (tab === "review") return status === "needs_review";
  if (tab === "processing") return status === "uploaded" || status === "processing";
  if (tab === "done") return status === "approved" || status === "rejected";
  return status === "failed";
}

const PAGE_SIZE = 25;

export function InboxScreen() {
  const list = useReferralList();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"newest" | "oldest">("newest");
  const [tabChoice, setTabChoice] = useState<Tab | null>(null);
  const [page, setPage] = useState(0);
  const items = list.data?.items ?? [];
  const ready = items.filter((item) => item.status === "needs_review").length;
  const processing = items.filter((item) => item.status === "uploaded" || item.status === "processing").length;
  const failed = items.filter((item) => item.status === "failed").length;
  const done = items.filter((item) => item.status === "approved" || item.status === "rejected").length;
  const tab = tabChoice ?? (ready === 0 ? "all" : "review");
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = items.filter((item) => {
      if (!inTab(item.status, tab)) return false;
      if (!needle) return true;
      return item.id.toLowerCase().includes(needle) || (item.patient_name ?? "").toLowerCase().includes(needle);
    });
    matched.sort((left, right) => {
      const delta = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
      return sort === "newest" ? -delta : delta;
    });
    return matched;
  }, [items, query, sort, tab]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pages - 1);
  const rows = filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold leading-7">Referrals</h1>
          {items.length > 0 ? (
            <p className="tabular mt-1 text-sm text-text-muted">
              {ready} ready for review · {processing} processing · {failed} failed
            </p>
          ) : null}
        </div>
        <Button type="button" onClick={() => setUploadOpen(true)}>
          Upload referral
        </Button>
      </div>
      <UploadDialog open={uploadOpen} onOpenChange={setUploadOpen} />
      {list.isLoading ? <LoadingRows /> : null}
      {list.error ? <ListError error={list.error} onRetry={() => void list.refetch()} /> : null}
      {list.data && items.length === 0 ? (
        <div className="rounded-panel border border-border bg-surface px-6 py-16 text-center">
          <h2 className="text-base font-semibold">No referrals yet</h2>
          <p className="mt-2 text-sm text-text-muted">Upload a synthetic referral PDF or image to start.</p>
          <Button className="mt-4" type="button" onClick={() => setUploadOpen(true)}>
            Upload referral
          </Button>
        </div>
      ) : null}
      {list.data && items.length > 0 ? (
        <>
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Referral status">
            {(
              [
                ["review", `To review ${ready}`],
                ["processing", `Processing ${processing}`],
                ["done", `Done ${done}`],
                ["failed", `Failed ${failed}`],
                ["all", `All ${items.length}`],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={tab === value}
                className={`inline-flex min-h-(--control-height) cursor-pointer items-center rounded-control border px-3 text-sm transition-colors ${
                  tab === value
                    ? "border-brand bg-surface-tint font-medium text-primary"
                    : "border-border-control bg-surface text-text hover:border-brand hover:bg-surface-tint"
                }`}
                onClick={() => {
                  setTabChoice(value);
                  setPage(0);
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <label className="sr-only" htmlFor="referral-search">
              Search referral ID or patient name
            </label>
            <Input
              id="referral-search"
              value={query}
              placeholder="Search referral ID or patient name"
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
            />
            <Select
              value={sort}
              onValueChange={(value) => {
                if (value === "newest" || value === "oldest") setSort(value);
              }}
            >
              <SelectTrigger aria-label="Sort referrals">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="newest">Newest first</SelectItem>
                <SelectItem value="oldest">Oldest first</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {list.data.truncated ? (
            <p className="text-sm text-text-muted">Showing newest {items.length}</p>
          ) : null}
          {filtered.length === 0 ? (
            <p className="text-sm">
              No referrals in {tab === "review" ? "To review" : tab}.{" "}
              <button
                type="button"
                className="cursor-pointer font-medium text-primary underline-offset-2 hover:underline"
                onClick={() => setTabChoice("all")}
              >
                Show all referrals
              </button>
            </p>
          ) : (
            <div className="overflow-hidden rounded-panel border border-border bg-surface">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="text-left">
                    <th className="px-3">Referral ID</th>
                    <th className="px-3">Received</th>
                    <th className="px-3">Patient</th>
                    <th className="px-3">Suggested specialty</th>
                    <th className="px-3">Flags</th>
                    <th className="px-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => (
                    <InboxRow key={item.id} item={item} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex items-center justify-between text-sm">
            <p className="tabular">
              Showing {rows.length} of {filtered.length}
            </p>
            <div className="flex gap-2">
              <Button type="button" variant="outline" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>
                Previous
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={safePage >= pages - 1}
                onClick={() => setPage(safePage + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function InboxRow({ item }: { item: ReferralSummary }) {
  const received = formatReceived(item.created_at);
  const flags =
    item.status === "uploaded" || item.status === "processing"
      ? "—"
      : String(item.blocking_flag_count + item.warning_flag_count);
  return (
    <tr className="border-t border-border hover:bg-surface-tint">
      <td className="px-3 font-mono">
        <Link
          href={`/referrals/${item.id}`}
          className="cursor-pointer font-medium text-primary underline-offset-2 hover:underline"
          title={item.id}
        >
          {item.id.slice(0, 8)}
        </Link>
      </td>
      <td className="tabular px-3" title={received.full}>
        {received.label}
      </td>
      <td className="max-w-48 truncate px-3" title={item.patient_name ?? undefined}>
        {item.patient_name ?? "—"}
      </td>
      <td className="px-3">{specialtyLabel(item.suggested_specialty)}</td>
      <td className="tabular px-3">{flags}</td>
      <td className="px-3">
        <StatusChip status={item.status} />
      </td>
    </tr>
  );
}

function LoadingRows() {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: 8 }, (_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  );
}

function ListError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const problem = error instanceof ApiProblem ? error : null;
  return (
    <div role="alert" className="rounded-panel border border-border bg-surface p-4">
      <p>Referrals could not be loaded. Check that the API is running.</p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={onRetry}>
          Retry
        </Button>
        {problem?.correlationId ? <ReferenceId id={problem.correlationId} /> : null}
      </div>
    </div>
  );
}
