import { describe, expect, it } from "vitest";

import { placeBox, type RenderedPage } from "@/lib/coordinates";

const A4 = { page_width: 595, page_height: 842, unit: "pt" };
const page = (scale: number, overrides: Partial<RenderedPage> = {}): RenderedPage => ({
  widthPt: 595,
  heightPt: 842,
  rotation: 0,
  viewportWidth: 595 * scale,
  viewportHeight: 842 * scale,
  ...overrides,
});
const box = { page_number: 1, x0: 100, y0: 200, x1: 150, y1: 212 };

describe("placeBox", () => {
  it("keeps points at scale 1", () => {
    expect(placeBox(box, A4, page(1))).toEqual({
      ok: true,
      box: { left: 100, top: 200, width: 50, height: 12 },
    });
  });

  it.each([0.75, 1.25, 2, 3])("scales with the viewport (scale %s)", (scale) => {
    const placed = placeBox(box, A4, page(scale));
    expect(placed.ok).toBe(true);
    if (placed.ok) {
      expect(placed.box.left).toBeCloseTo(100 * scale);
      expect(placed.box.top).toBeCloseTo(200 * scale);
      expect(placed.box.width).toBeCloseTo(50 * scale);
      expect(placed.box.height).toBeCloseTo(12 * scale);
    }
  });

  it("converts inches to points", () => {
    const inches = { page_width: 595 / 72, page_height: 842 / 72, unit: "inch" };
    const placed = placeBox({ page_number: 1, x0: 1, y0: 2, x1: 1.5, y1: 2.25 }, inches, page(1));
    expect(placed).toEqual({ ok: true, box: { left: 72, top: 144, width: 36, height: 18 } });
  });

  it("places a box at the page edge", () => {
    const edge = { page_number: 1, x0: 545, y0: 830, x1: 595, y1: 842 };
    const placed = placeBox(edge, A4, page(2));
    expect(placed).toEqual({ ok: true, box: { left: 1090, top: 1660, width: 100, height: 24 } });
  });

  it("is the same calculation on page 2 (each page has its own geometry)", () => {
    const second = { ...box, page_number: 2 };
    expect(placeBox(second, A4, page(1.5))).toEqual(placeBox(box, A4, page(1.5)));
  });

  it("uses non-uniform scaling if a viewport is stretched", () => {
    const placed = placeBox(box, A4, page(1, { viewportWidth: 1190, viewportHeight: 842 }));
    expect(placed).toEqual({ ok: true, box: { left: 200, top: 200, width: 100, height: 12 } });
  });

  it("tolerates rounding in the page size", () => {
    expect(placeBox(box, A4, page(1, { widthPt: 595.3, heightPt: 841.8 })).ok).toBe(true);
  });

  it("refuses a page-size mismatch instead of drawing a misplaced box", () => {
    const letter = page(1, { widthPt: 612, heightPt: 792 });
    expect(placeBox(box, A4, letter)).toEqual({
      ok: false,
      reason: expect.stringContaining("does not match"),
    });
  });

  it("refuses rotated pages", () => {
    expect(placeBox(box, A4, page(1, { rotation: 90 })).ok).toBe(false);
    expect(placeBox(box, A4, page(1, { rotation: 360 })).ok).toBe(true);
  });

  it("refuses unknown units and malformed boxes", () => {
    expect(placeBox(box, { ...A4, unit: "px" }, page(1)).ok).toBe(false);
    expect(placeBox({ ...box, x1: 90 }, A4, page(1)).ok).toBe(false);
  });
});
