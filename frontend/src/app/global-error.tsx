"use client";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body>
        <main className="mx-auto max-w-lg px-6 py-16">
          <h1>Something went wrong</h1>
          <p>{error.digest ? `Reference ${error.digest.slice(0, 8)}` : "The page could not be shown."}</p>
          <button type="button" onClick={reset}>
            Retry
          </button>
        </main>
      </body>
    </html>
  );
}
