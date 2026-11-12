"use client";

import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

import { Button } from "@/components/ui/button";
import type { BoundingBox } from "@/lib/api/types";

pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

export interface HighlightTarget {
  page: number;
  quote: string | null;
  bbox: BoundingBox;
}

export function DocumentViewer({
  blob,
  mediaType,
  pageCount,
  highlight,
}: {
  blob: Blob;
  mediaType: string;
  pageCount: number | null;
  highlight: HighlightTarget | null;
}) {
  const [page, setPage] = useState(1);
  const [scale, setScale] = useState(1);
  const [failed, setFailed] = useState(false);
  const highlightKey = highlight ? `${highlight.page}:${highlight.bbox.left}:${highlight.bbox.top}` : "";
  const [appliedHighlight, setAppliedHighlight] = useState("");
  if (highlight && highlightKey !== appliedHighlight) {
    setAppliedHighlight(highlightKey);
    setPage(highlight.page);
  }
  const image = mediaType.startsWith("image/");
  const imageRef = useRef<HTMLImageElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [pageWidth, setPageWidth] = useState(612);
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setPageWidth(Math.max(1, Math.floor(entry.contentRect.width)));
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);
  const [loadedPages, setLoadedPages] = useState<number | null>(null);
  useEffect(() => {
    if (!image) return;
    const objectUrl = URL.createObjectURL(blob);
    if (imageRef.current) imageRef.current.src = objectUrl;
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob, image]);
  const pages = Math.max(loadedPages ?? pageCount ?? 1, 1);

  return (
    <section className="flex min-h-0 flex-col gap-3" aria-label="Document">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="mr-1 font-medium">Document</span>
        {image ? null : (
          <>
            <Button type="button" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous page
            </Button>
            <span className="tabular">
              page {page} of {pages}
            </span>
            <Button type="button" variant="outline" disabled={page >= pages} onClick={() => setPage(page + 1)}>
              Next page
            </Button>
          </>
        )}
        <Button type="button" variant="outline" onClick={() => setScale(1)}>
          Fit width
        </Button>
        <Button type="button" variant="outline" onClick={() => setScale((value) => Math.max(0.5, value - 0.25))} aria-label="Zoom out">
          −
        </Button>
        <Button type="button" variant="outline" onClick={() => setScale((value) => Math.min(2, value + 0.25))} aria-label="Zoom in">
          +
        </Button>
      </div>
      {highlight ? (
        <p className="rounded-control bg-surface-tint p-3 text-sm">
          Source on p.{highlight.page}
          {highlight.quote ? <span className="mt-1 block">{highlight.quote}</span> : null}
        </p>
      ) : null}
      {failed ? <p>This document could not be displayed.</p> : null}
      <div ref={viewportRef} className="max-h-[calc(100dvh-15rem)] min-h-64 overflow-auto rounded-panel border border-border bg-surface p-3">
        {image ? (
          <div className="relative inline-block" style={{ width: `${scale * 100}%` }}>
            <img ref={imageRef} alt="Referral document" className="block h-auto w-full" onError={() => setFailed(true)} />
            {highlight ? <HighlightBox bbox={highlight.bbox} /> : null}
          </div>
        ) : (
          <Document
            // Keep the fetched Blob stable and use effect-based loading. Suspense
            // retries must never create a new URL or PDF input during render.
            file={blob}
            suspense={false}
            onLoadSuccess={({ numPages }) => setLoadedPages(numPages)}
            onLoadError={() => setFailed(true)}
            onSourceError={() => setFailed(true)}
            error={<p role="alert">This PDF could not be displayed. Reload the document to try again.</p>}
            loading={<p role="status">Loading document</p>}
          >
            <div className="relative inline-block">
              <Page width={pageWidth} suspense={false} devicePixelRatio={Math.min(globalThis.devicePixelRatio || 1, 2)} pageNumber={page} scale={scale} renderTextLayer={false} renderAnnotationLayer={false} />
              {highlight && highlight.page === page ? <HighlightBox bbox={highlight.bbox} /> : null}
            </div>
          </Document>
        )}
      </div>
    </section>
  );
}

function HighlightBox({ bbox }: { bbox: BoundingBox }) {
  return (
    <div
      data-testid="highlight-box"
      className="pointer-events-none absolute border-2 border-danger bg-danger-bg/40"
      style={{
        left: `${bbox.left * 100}%`,
        top: `${bbox.top * 100}%`,
        width: `${bbox.width * 100}%`,
        height: `${bbox.height * 100}%`,
      }}
    />
  );
}
