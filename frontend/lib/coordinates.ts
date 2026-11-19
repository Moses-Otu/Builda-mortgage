// Evidence boxes from the API -> positions over the page that pdf.js rendered.
//
// The API gives boxes with the origin at the top left, in `unit` ("pt", or "inch" x 72), on
// a page of page_width x page_height. pdf.js reports the page's own size in points
// (page.view) and renders it into a viewport of CSS pixels. Before placing a box we check the
// two page sizes agree; if they don't, we refuse to draw rather than show a misplaced
// highlight.

import type { Evidence } from "./types";

const POINTS_PER_UNIT: Record<string, number> = { pt: 1, inch: 72 };
/** Page sizes may differ by rounding only. */
export const PAGE_SIZE_TOLERANCE_PT = 0.5;

export interface RenderedPage {
  /** pdf.js page.view width and height, in points */
  widthPt: number;
  heightPt: number;
  /** page rotation in degrees, as reported by pdf.js */
  rotation: number;
  /** rendered viewport size, in CSS pixels */
  viewportWidth: number;
  viewportHeight: number;
}

export interface FieldGeometry {
  page_width: number;
  page_height: number;
  unit: string;
}

export interface ScreenBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export type Placement = { ok: true; box: ScreenBox } | { ok: false; reason: string };

export function placeBox(
  evidence: Evidence,
  field: FieldGeometry,
  page: RenderedPage,
): Placement {
  const factor = POINTS_PER_UNIT[field.unit];
  if (factor === undefined) {
    return { ok: false, reason: `Unknown coordinate unit '${field.unit}'` };
  }
  if (page.rotation % 360 !== 0) {
    return { ok: false, reason: "Rotated pages are not supported for highlighting" };
  }
  const apiWidth = field.page_width * factor;
  const apiHeight = field.page_height * factor;
  if (
    Math.abs(apiWidth - page.widthPt) > PAGE_SIZE_TOLERANCE_PT ||
    Math.abs(apiHeight - page.heightPt) > PAGE_SIZE_TOLERANCE_PT
  ) {
    return {
      ok: false,
      reason: "The page size in the extraction does not match the PDF; highlight not shown",
    };
  }
  const sx = page.viewportWidth / page.widthPt;
  const sy = page.viewportHeight / page.heightPt;
  const { x0, y0, x1, y1 } = evidence;
  if (!(x0 <= x1 && y0 <= y1)) {
    return { ok: false, reason: "Malformed evidence box" };
  }
  return {
    ok: true,
    box: {
      left: x0 * factor * sx,
      top: y0 * factor * sy,
      width: (x1 - x0) * factor * sx,
      height: (y1 - y0) * factor * sy,
    },
  };
}
