"use client";

// Renders the stored original with pdf.js and draws each extracted value's evidence box over
// it. The PDF is fetched through the forwarder and kept in memory only. Boxes are placed by
// lib/coordinates.placeBox, which refuses to draw when the extraction's page size doesn't
// match the PDF's (a warning is shown instead of a misplaced highlight).

import { Loader2, Maximize2, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { placeBox, type RenderedPage } from "@/lib/coordinates";
import type { DecisionKind, Evidence } from "@/lib/types";

export interface Highlight {
  ordinal: number;
  kind: "value" | "label";
  evidence: Evidence;
  geometry: { page_width: number; page_height: number; unit: string };
}

interface Props {
  url: string;
  highlights: Highlight[];
  selected: number | null;
  hovered: number | null;
  decisions: Record<number, DecisionKind | undefined>;
  onSelect: (ordinal: number) => void;
  onHover: (ordinal: number | null) => void;
}

type PdfModule = typeof import("pdfjs-dist");
type PdfDocument = Awaited<ReturnType<PdfModule["getDocument"]>["promise"]>;

const MIN_SCALE = 0.5;
const MAX_SCALE = 3;
const clampScale = (s: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.round(s * 20) / 20));

export function PdfView({ url, highlights, selected, hovered, decisions, onSelect, onHover }: Props) {
  const [pdf, setPdf] = useState<{ lib: PdfModule; doc: PdfDocument } | null>(null);
  const [pages, setPages] = useState<RenderedPage[]>([]);
  const [scale, setScale] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const viewer = useRef<HTMLDivElement>(null);
  const containers = useRef<Array<HTMLDivElement | null>>([]);
  const renderChain = useRef<Promise<void>>(Promise.resolve());

  /** The scale at which the first page fills the viewer's width. */
  const fitWidth = useCallback(async (doc: PdfDocument) => {
    const page = await doc.getPage(1);
    const width = page.getViewport({ scale: 1 }).width;
    const available = (viewer.current?.clientWidth ?? 800) - 48;
    return clampScale(available / width);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let loaded: PdfDocument | null = null;
    void (async () => {
      try {
        const lib = await import("pdfjs-dist");
        lib.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) throw new Error(`The original could not be loaded (${response.status})`);
        const data = new Uint8Array(await response.arrayBuffer());
        loaded = await lib.getDocument({ data }).promise;
        const initial = await fitWidth(loaded);
        if (!cancelled) {
          setScale(initial);
          setPdf({ lib, doc: loaded });
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "The original could not be shown");
      }
    })();
    return () => {
      cancelled = true;
      void loaded?.destroy();
    };
  }, [url, fitWidth]);

  useEffect(() => {
    if (!pdf || scale === null) return;
    // pdf.js refuses a second render() on a canvas that is still rendering. React may restart
    // this effect at any moment (zoom changes; development runs effects twice), so each run
    // cancels its own in-flight work on cleanup, and the next run starts only after the
    // previous one has fully stopped.
    let cancelled = false;
    const active: Array<{ cancel: () => void }> = [];
    const run = async () => {
      const rendered: RenderedPage[] = [];
      for (let n = 1; n <= pdf.doc.numPages; n++) {
        const page = await pdf.doc.getPage(n);
        const viewport = page.getViewport({ scale });
        const container = containers.current[n - 1];
        if (cancelled || !container) return;
        const canvas = container.querySelector("canvas");
        const textLayer = container.querySelector<HTMLDivElement>(".textLayer");
        if (!canvas || !textLayer) return;
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        const task = page.render({
          canvas,
          viewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        });
        active.push(task);
        await task.promise;
        if (cancelled) return;
        // pdf.js's own (invisible, selectable) text layer: also an independent reference for
        // checking that evidence boxes sit over the right text.
        textLayer.replaceChildren();
        textLayer.style.setProperty("--scale-factor", String(scale));
        textLayer.style.setProperty("--total-scale-factor", String(scale));
        const text = new pdf.lib.TextLayer({
          textContentSource: await page.getTextContent(),
          container: textLayer,
          viewport,
        });
        active.push(text);
        await text.render();
        if (cancelled) return;
        const [x0 = 0, y0 = 0, x1 = 0, y1 = 0] = page.view;
        rendered.push({
          widthPt: x1 - x0,
          heightPt: y1 - y0,
          rotation: page.rotate,
          viewportWidth: viewport.width,
          viewportHeight: viewport.height,
        });
      }
      if (!cancelled) setPages(rendered);
    };
    const done = renderChain.current
      .then(run)
      .catch((e: unknown) => {
        // A cancellation is expected when the effect restarts; anything else is shown.
        if (!cancelled) setError(e instanceof Error ? e.message : "The original could not be shown");
      });
    renderChain.current = done;
    return () => {
      cancelled = true;
      for (const task of active) task.cancel();
    };
  }, [pdf, scale]);

  useEffect(() => {
    if (selected === null) return;
    viewer.current
      ?.querySelector(`[data-ordinal="${selected}"][data-kind="value"]`)
      ?.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
  }, [selected, pages]);

  const count = pdf?.doc.numPages ?? 0;
  const warnings = new Set<string>();

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-slate-200 bg-white px-3 py-2">
        <span className="mr-auto text-xs font-medium text-slate-500">
          {count > 0 ? `Original · ${count} page${count === 1 ? "" : "s"}` : "Original"}
        </span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Zoom out"
          disabled={scale === null || scale <= MIN_SCALE}
          onClick={() => setScale((s) => clampScale((s ?? 1) - 0.25))}
        >
          <Minus />
        </Button>
        <span className="w-12 text-center text-xs tabular-nums text-slate-600">
          {scale === null ? "—" : `${Math.round(scale * 100)}%`}
        </span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Zoom in"
          disabled={scale === null || scale >= MAX_SCALE}
          onClick={() => setScale((s) => clampScale((s ?? 1) + 0.25))}
        >
          <Plus />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={!pdf}
          onClick={() => pdf && void fitWidth(pdf.doc).then(setScale)}
        >
          <Maximize2 /> Fit width
        </Button>
      </div>

      <div ref={viewer} className="min-h-0 flex-1 overflow-auto bg-slate-200/70 p-6">
        {error && <Alert tone="error">{error}</Alert>}
        {!error && !pdf && (
          <div className="flex items-center justify-center gap-2 py-24 text-sm text-slate-500">
            <Loader2 className="size-4 animate-spin" /> Loading the original…
          </div>
        )}
        {Array.from({ length: count }, (_, i) => {
          const pageNumber = i + 1;
          const geometry = pages[i];
          return (
            <div
              key={pageNumber}
              className="pdf-page"
              data-page={pageNumber}
              ref={(el) => {
                containers.current[i] = el;
              }}
              style={geometry ? { width: geometry.viewportWidth, height: geometry.viewportHeight } : undefined}
            >
              <canvas />
              <div className="textLayer" />
              {geometry &&
                highlights
                  .filter((h) => h.evidence.page_number === pageNumber)
                  .map((h) => {
                    const placed = placeBox(h.evidence, h.geometry, geometry);
                    if (!placed.ok) {
                      warnings.add(placed.reason);
                      return null;
                    }
                    const { left, top, width, height } = placed.box;
                    const classes = ["box", h.kind];
                    const decision = decisions[h.ordinal];
                    if (h.kind === "value" && decision) classes.push(decision);
                    if (hovered === h.ordinal) classes.push("hovered");
                    if (selected === h.ordinal) classes.push("selected");
                    return (
                      <div
                        key={`${h.kind}-${h.ordinal}-${selected === h.ordinal ? "s" : ""}`}
                        className={classes.join(" ")}
                        data-ordinal={h.ordinal}
                        data-kind={h.kind}
                        style={{ left, top, width, height }}
                        onClick={() => onSelect(h.ordinal)}
                        onMouseEnter={() => onHover(h.ordinal)}
                        onMouseLeave={() => onHover(null)}
                        title={h.kind === "value" ? "Extracted value" : "Label"}
                      />
                    );
                  })}
            </div>
          );
        })}
        {warnings.size > 0 && (
          <Alert tone="warning" title="Some highlights can't be drawn">
            {[...warnings].join(" ")}
          </Alert>
        )}
      </div>
    </div>
  );
}
