"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { shortReference } from "@/lib/api/problems";

export function ReferenceId({ id, label = "Reference" }: { id: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <p className="flex items-center gap-2 text-sm text-text-muted">
      <span>
        {label} <span className="font-mono">{shortReference(id)}</span>
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => {
          void navigator.clipboard.writeText(id).then(() => {
            setCopied(true);
          });
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </p>
  );
}
