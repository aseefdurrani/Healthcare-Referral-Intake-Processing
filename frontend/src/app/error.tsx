"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    if (typeof process !== "undefined" && typeof process.stderr?.write === "function") {
      process.stderr.write(
        `${JSON.stringify({ event: "ui.error", name: error.name, reference: error.digest ?? "" })}\n`,
      );
    }
  }, [error]);
  return (
    <main className="mx-auto max-w-lg px-6 py-16">
      <h1 className="text-xl font-semibold">Something went wrong</h1>
      <p className="mt-2 text-sm">The page could not be shown. {error.digest ? `Reference ${error.digest.slice(0, 8)}` : null}</p>
      <Button className="mt-4" type="button" onClick={reset}>
        Retry
      </Button>
    </main>
  );
}
