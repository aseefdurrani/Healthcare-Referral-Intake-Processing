"use client";

import { useRef, useState } from "react";
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
  const inputRef = useRef<HTMLInputElement>(null);

  function clearPicker() {
    if (inputRef.current) inputRef.current.value = "";
  }

  function choose(next: File | null) {
    setServerError(null);
    if (!next) {
      setFile(null);
      setKey(null);
      setClientError(null);
      clearPicker();
      return;
    }
    const reason = validateUpload(next);
    if (reason) {
      setFile(null);
      setKey(null);
      setClientError(uploadErrorMessage(next.name, reason));
      clearPicker();
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
          className={`flex flex-col items-center rounded-panel border border-dashed px-6 py-8 text-center transition-colors ${dragOver ? "border-brand bg-surface-tint" : "border-border-control bg-canvas"}`}
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
          <p className="text-sm font-medium">Drag a file here</p>
          <p className="mt-1 text-sm text-text-muted">or choose one from your computer</p>
          <input
            ref={inputRef}
            className="sr-only"
            type="file"
            tabIndex={-1}
            aria-hidden="true"
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
          <Button
            type="button"
            variant="outline"
            className="mt-4"
            disabled={uploading}
            onClick={() => inputRef.current?.click()}
          >
            {file ? "Replace file" : "Choose file"}
          </Button>
        </div>
        {file ? (
          <div className="flex items-center gap-3 rounded-control border border-border bg-surface px-3 py-2">
            <div className="min-w-0 flex-1 text-left">
              <p className="truncate text-sm font-medium">{file.name}</p>
              <p className="tabular text-xs text-text-muted">{formatFileSize(file.size)}</p>
            </div>
            <Button type="button" variant="outline" size="sm" disabled={uploading} onClick={() => choose(null)}>
              Remove
            </Button>
          </div>
        ) : null}
        <label className="flex cursor-pointer items-start gap-2.5 rounded-control border border-border bg-surface px-3 py-2.5 text-sm has-[:disabled]:cursor-not-allowed">
          <Checkbox
            className="mt-0.5"
            checked={attested}
            disabled={uploading}
            onCheckedChange={(value) => setAttested(value === true)}
          />
          <span>This document contains only synthetic data.</span>
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
          <Button type="button" variant="outline" disabled={uploading} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" disabled={!file || !attested || uploading} onClick={() => void submit()}>
            {uploading ? "Uploading..." : "Upload"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
