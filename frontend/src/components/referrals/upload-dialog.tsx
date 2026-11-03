"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

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
import { ReferenceId } from "@/components/referrals/reference-id";
import { ApiProblem, classifyProblem } from "@/lib/api/problems";
import { uploadReferral } from "@/lib/api/client";
import { uploadErrorMessage, validateUpload } from "@/lib/upload-rules";
import { formatFileSize } from "@/lib/format";

export function UploadDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [attested, setAttested] = useState(false);
  const [clientError, setClientError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<ApiProblem | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  function choose(next: File | null) {
    setServerError(null);
    if (!next) {
      setFile(null);
      setKey(null);
      setClientError(null);
      return;
    }
    const reason = validateUpload(next);
    if (reason) {
      setFile(null);
      setKey(null);
      setClientError(uploadErrorMessage(next.name, reason));
      return;
    }
    setClientError(null);
    setFile(next);
    setKey(crypto.randomUUID());
  }

  async function submit() {
    if (!file || !key || !attested) return;
    setUploading(true);
    setServerError(null);
    try {
      const summary = await uploadReferral(file, key);
      toast.success("Uploaded. Processing started.");
      onOpenChange(false);
      choose(null);
      setAttested(false);
      router.push(`/referrals/${summary.id}`);
    } catch (error) {
      setServerError(error instanceof ApiProblem ? error : null);
    } finally {
      setUploading(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (uploading) return;
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Upload referral</DialogTitle>
          <DialogDescription>PDF, PNG, or JPEG, up to 10 MB and 10 pages.</DialogDescription>
        </DialogHeader>
        <div
          className={`rounded-panel border border-dashed px-4 py-8 text-center ${dragOver ? "border-brand bg-surface-tint" : "border-border-control"}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            const dropped = event.dataTransfer.files;
            if (dropped.length > 1) {
              setClientError(uploadErrorMessage("files", "count"));
              return;
            }
            choose(dropped[0] ?? null);
          }}
        >
          <p>Drag a file here, or</p>
          <label className="mt-2 inline-block">
            <span className="sr-only">Choose file</span>
            <input
              className="block text-sm"
              type="file"
              accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg"
              onChange={(event) => {
                const list = event.target.files;
                if (list && list.length > 1) {
                  setClientError(uploadErrorMessage("files", "count"));
                  return;
                }
                choose(list?.[0] ?? null);
              }}
            />
          </label>
        </div>
        {file ? (
          <p className="flex items-center justify-between text-sm">
            <span>
              {file.name} <span className="tabular text-text-muted">{formatFileSize(file.size)}</span>
            </span>
            <Button type="button" variant="outline" onClick={() => choose(null)}>
              Remove
            </Button>
          </p>
        ) : null}
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={attested} onCheckedChange={(value) => setAttested(value === true)} />
          This document contains only synthetic data.
        </label>
        {clientError ? (
          <p role="alert" className="text-sm text-danger">
            <span aria-hidden="true">! </span>
            {clientError}
          </p>
        ) : null}
        {serverError ? (
          <div role="alert" className="space-y-2 text-sm text-danger">
            <p>{serverError.detail}</p>
            {serverError.correlationId ? <ReferenceId id={serverError.correlationId} /> : null}
            {classifyProblem(serverError) === "server" ? (
              <Button type="button" variant="outline" onClick={() => void submit()}>
                Retry
              </Button>
            ) : null}
          </div>
        ) : null}
        <DialogFooter>
          {uploading ? null : (
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
          )}
          <Button type="button" disabled={!file || !attested || uploading} onClick={() => void submit()}>
            {uploading ? "Uploading..." : "Upload"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
