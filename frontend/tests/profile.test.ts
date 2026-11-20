import { describe, expect, it } from "vitest";

import { isAllowed } from "@/lib/allowlist";
import {
  evidenceHref,
  figureScope,
  formatValue,
  originCounts,
  originSentence,
  periodTable,
  undatedFigures,
} from "@/lib/profile";
import type { Figure, FigureEvidence } from "@/lib/types";

const CASE = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b";
const DOC = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c";
const SOURCE = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5d";
const box = { page_number: 2, x0: 0, y0: 0, x1: 1, y1: 1 };

function evidence(over: Partial<FigureEvidence> = {}): FigureEvidence {
  return {
    document_id: DOC,
    document_version_id: DOC,
    document_type: "payslip",
    file_name: "payslip-2026-05.pdf",
    sha256: "a".repeat(64),
    field: {
      ordinal: 14,
      field_code: "net_pay",
      label_text: "Net Pay",
      raw_text: "2,805.84",
      value_money_minor: 280_584,
      currency: "GBP",
      value_date: null,
      value_text: null,
      value_evidence: box,
      label_evidence: box,
      page_width: 595,
      page_height: 842,
      unit: "pt",
      confidence: null,
    },
    decision: "accept",
    correction_reason: null,
    run_id: DOC,
    provider: "synthetic-fixture",
    schema_version: "payslip/1.0.0",
    reviewed_by: { user_id: null, name: "Alex Broker" },
    reviewed_at: "2026-11-19T00:00:00Z",
    ...over,
  };
}

let n = 0;
function figure(measure: string, over: Partial<Figure> = {}): Figure {
  n += 1;
  return {
    id: `f${n}`,
    item_kind: "income_source",
    item_id: SOURCE,
    measure,
    qualifier: null,
    value: { type: "money", amount_minor: 100_000, currency: "GBP" },
    frequency: "monthly",
    period_start: "2026-05-01",
    period_end: "2026-05-31",
    tax_year: null,
    as_of_date: null,
    source_kind: "document",
    origin: "extracted",
    recorded_by: null,
    recorded_at: "2026-11-19T00:00:00Z",
    evidence: evidence(),
    history: [],
    cited_by: [],
    ...over,
  };
}

describe("the period table", () => {
  const june = { period_start: "2026-06-01", period_end: "2026-06-30" };
  const figures = [
    figure("net_pay", june),
    figure("gross_pay"),
    figure("net_pay"),
    figure("income_tax", june),
    figure("allowance", { qualifier: "car_allowance" }),
    figure("basic_pay", { period_start: null, period_end: null, frequency: "annual", origin: "declared", evidence: null }),
  ];
  const table = periodTable(figures);

  it("has one row per pay period, in date order, labelled by month", () => {
    expect(table.rows.map((r) => r.label)).toEqual(["May 2026", "June 2026"]);
    expect(table.rows[0]?.range).toBe("1 May 2026 to 31 May 2026");
  });

  it("orders columns as a payslip reads, with qualifiers shown", () => {
    expect(table.columns.map((c) => c.label)).toEqual([
      "Gross pay",
      "Allowance (car allowance)",
      "Income tax",
      "Net pay",
    ]);
  });

  it("leaves undated figures out of the table, never summed into it", () => {
    expect(undatedFigures(figures).map((f) => f.measure)).toEqual(["basic_pay"]);
    const cells = table.rows.flatMap((r) => Object.values(r.cells).flat());
    expect(cells).toHaveLength(5);
  });
});

describe("values, origins and evidence", () => {
  it("formats each value type", () => {
    expect(formatValue({ type: "money", amount_minor: 280_584 })).toBe("£2,805.84");
    expect(formatValue({ type: "signed_money", amount_minor: -5_000 })).toBe("-£50.00");
    expect(formatValue({ type: "rate", percent: "4.5" })).toBe("4.5%");
    expect(formatValue({ type: "duration", months: 24 })).toBe("24 months");
    expect(formatValue({ type: "count", count: 2 })).toBe("2");
  });

  it("explains where a figure came from", () => {
    expect(originSentence(figure("net_pay"))).toBe(
      "Extracted from payslip-2026-05.pdf, page 2, “Net Pay”, accepted as read.",
    );
    const corrected = figure("net_pay", {
      origin: "corrected",
      evidence: evidence({ decision: "correct", correction_reason: "Paper copy" }),
    });
    expect(originSentence(corrected)).toBe(
      "Corrected by the broker from payslip-2026-05.pdf, page 2, “Net Pay” (read as 2,805.84).",
    );
    expect(originSentence(figure("basic_pay", { origin: "declared", evidence: null }))).toBe(
      "Declared by the applicant.",
    );
    expect(originSentence(figure("basic_pay", { origin: "broker_entry", evidence: null }))).toBe(
      "Entered by the broker.",
    );
  });

  it("links a document figure to its field on the document", () => {
    expect(evidenceHref(CASE, figure("net_pay"))).toBe(`/cases/${CASE}/documents/${DOC}?field=14`);
    expect(evidenceHref(CASE, figure("basic_pay", { evidence: null }))).toBeNull();
  });

  it("describes what an undated figure applies to, and counts origins", () => {
    expect(figureScope(figure("basic_pay", { frequency: "annual", as_of_date: "2026-06-03" }))).toBe(
      "annual · as of 3 Jun 2026",
    );
    expect(originCounts([figure("a"), figure("b", { origin: "declared" })])).toEqual({
      extracted: 1,
      corrected: 0,
      declared: 1,
      broker_entry: 0,
    });
  });
});

describe("forwarder allowlist for the profile", () => {
  const split = (path: string) => path.split("/");
  it("allows reading the profile and nothing else on it", () => {
    expect(isAllowed("GET", split(`cases/${CASE}/profile`))).toBe(true);
    expect(isAllowed("POST", split(`cases/${CASE}/profile`))).toBe(false);
    expect(isAllowed("GET", split(`cases/${CASE}/profile/${DOC}`))).toBe(false);
  });
});
