"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ABSENT_ALLOWED, ACK_CODES, DESTINATIONS, PRIORITY_OPTIONS, approvalGaps, type ApprovalDraft } from "@/lib/catalog";
import type { ReasonCode, ReferralDetail } from "@/lib/api/types";

const REJECT_REASONS: { code: ReasonCode; label: string }[] = [
  { code: "not_a_referral", label: "Not a referral document" },
  { code: "unreadable", label: "Document is unreadable" },
  { code: "duplicate", label: "Duplicate referral" },
  { code: "incomplete", label: "Incomplete referral" },
  { code: "other", label: "Other" },
];

export function ApproveDialog({
  open,
  detail,
  draft,
  onDraft,
  onClose,
  onApprove,
  pending,
  serverMessages,
}: {
  open: boolean;
  detail: ReferralDetail;
  draft: ApprovalDraft;
  onDraft: (draft: ApprovalDraft) => void;
  onClose: () => void;
  onApprove: () => void;
  pending: boolean;
  serverMessages: string[];
}) {
  const gaps = approvalGaps(detail, draft);
  const absentKeys = [...ABSENT_ALLOWED].filter((key) => !detail.fields.find((field) => field.key === key)?.effective_value);
  const ackFlags = detail.flags.filter((flag) => flag.status === "open" && ACK_CODES.has(flag.code));
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? null : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{gaps.length > 0 ? `Approval is waiting on ${gaps.length} items` : "Approve referral"}</DialogTitle>
          <DialogDescription>Nothing is sent outside this demo.</DialogDescription>
        </DialogHeader>
        {gaps.length > 0 ? (
          <ul className="space-y-2 text-sm">
            {gaps.map((gap) => (
              <li key={gap.id}>
                <span aria-hidden="true">! </span>
                {gap.message}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm">Destination {draft.destination === "other" ? draft.otherName : draft.destination}. Nothing is sent outside this demo.</p>
        )}
        <label className="block text-sm">
          Destination specialty
          <Select value={draft.destination || undefined} onValueChange={(destination) => onDraft({ ...draft, destination })}>
            <SelectTrigger className="mt-1 w-full" aria-label="Destination specialty">
              <SelectValue placeholder="Choose a specialty" />
            </SelectTrigger>
            <SelectContent>
              {DESTINATIONS.map(([code, label]) => (
                <SelectItem key={code} value={code}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        {draft.destination === "other" ? (
          <label className="block text-sm">
            Other specialty name
            <input
              className="mt-1 h-9 w-full rounded-control border border-border-control px-2"
              value={draft.otherName}
              onChange={(event) => onDraft({ ...draft, otherName: event.target.value })}
            />
          </label>
        ) : null}
        <label className="block text-sm">
          Sender priority
          <Select value={draft.priority || undefined} onValueChange={(priority) => onDraft({ ...draft, priority })}>
            <SelectTrigger className="mt-1 w-full" aria-label="Sender priority">
              <SelectValue placeholder="Confirm sender priority" />
            </SelectTrigger>
            <SelectContent>
              {PRIORITY_OPTIONS.map(([code, label]) => (
                <SelectItem key={code} value={code}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        {absentKeys.map((key) => (
          <label key={key} className="flex cursor-pointer items-start gap-2.5 text-sm">
            <Checkbox
              className="mt-0.5"
              checked={draft.absent.includes(key)}
              onCheckedChange={(checked) => {
                const absent = checked === true ? [...draft.absent, key] : draft.absent.filter((item) => item !== key);
                onDraft({ ...draft, absent });
              }}
            />
            Confirm {detail.fields.find((field) => field.key === key)?.label ?? key} is not on the document
          </label>
        ))}
        {ackFlags.map((flag) => (
          <label key={flag.id} className="block text-sm">
            Acknowledgement for {flag.message}
            <Textarea
              className="mt-1"
              value={draft.acknowledgements[flag.id] ?? ""}
              onChange={(event) =>
                onDraft({ ...draft, acknowledgements: { ...draft.acknowledgements, [flag.id]: event.target.value } })
              }
            />
          </label>
        ))}
        {serverMessages.length > 0 ? (
          <ul className="text-sm text-danger">
            {serverMessages.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {gaps.length > 0 ? "Go back and resolve" : "Cancel"}
          </Button>
          <Button type="button" disabled={gaps.length > 0 || pending} onClick={onApprove}>
            Approve
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function RejectDialog({
  open,
  onClose,
  onReject,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  onReject: (reason: ReasonCode, note: string) => void;
  pending: boolean;
}) {
  const [reason, setReason] = useState<ReasonCode | "">("");
  const [note, setNote] = useState("");
  const ready = reason !== "" && (reason !== "other" || note.trim().length > 0);
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? null : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reject referral</DialogTitle>
          <DialogDescription>Nothing is sent outside this demo. A reason is required.</DialogDescription>
        </DialogHeader>
        <label className="block text-sm">
          Reason
          <Select value={reason || undefined} onValueChange={(value) => setReason(value as ReasonCode)}>
            <SelectTrigger className="mt-1 w-full" aria-label="Reject reason">
              <SelectValue placeholder="Choose a reason" />
            </SelectTrigger>
            <SelectContent>
              {REJECT_REASONS.map((item) => (
                <SelectItem key={item.code} value={item.code}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="block text-sm">
          Note {reason === "other" ? "(required)" : "(optional)"}
          <Textarea className="mt-1" value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!ready || pending}
            onClick={() => {
              if (reason) onReject(reason, note.trim());
            }}
          >
            Reject referral
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
