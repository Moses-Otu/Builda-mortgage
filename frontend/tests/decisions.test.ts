import { describe, expect, it } from "vitest";

import {
  acceptAll,
  buildReview,
  draftProblem,
  formatMoney,
  parseMoney,
  problems,
  savesFigure,
  valueKind,
} from "@/lib/decisions";
import type { ExtractedField } from "@/lib/types";

function field(ordinal: number, field_code: string | null, extra: Partial<ExtractedField> = {}): ExtractedField {
  const evidence = { page_number: 1, x0: 0, y0: 0, x1: 10, y1: 10 };
  return {
    ordinal,
    field_code,
    label_text: field_code ?? "Something",
    raw_text: "£1.00",
    value_money_minor: 100,
    currency: "GBP",
    value_date: null,
    value_text: null,
    value_evidence: evidence,
    label_evidence: evidence,
    page_width: 595,
    page_height: 842,
    unit: "pt",
    confidence: null,
    ...extra,
  };
}

const NET = field(1, "net_pay");
const DATE = field(2, "pay_date", { value_money_minor: null, currency: null, value_date: "2026-01-31" });
const CODE = field(3, "tax_code", { value_money_minor: null, currency: null, value_text: "1257L" });
const LINE = field(4, null, { label_text: "Unpaid Leave", value_money_minor: -500 });
const FIELDS = [NET, DATE, CODE, LINE];

describe("parseMoney", () => {
  it.each([
    ["1,234.56", 123456],
    ["£1,234.56", 123456],
    ["1234.5", 123450],
    ["0", 0],
    ["12", 1200],
  ])("%s -> %s pence", (text, pence) => expect(parseMoney(text)).toBe(pence));

  it.each(["", "abc", "1,23.00", "-5.00", "1.234", "£"])("rejects %j", (text) =>
    expect(parseMoney(text)).toBeNull(),
  );
});

describe("decisions", () => {
  it("knows each field's value type", () => {
    expect([NET, DATE, CODE, LINE].map(valueKind)).toEqual(["money", "date", "text", "money"]);
  });

  it("requires a decision for every field before saving", () => {
    expect(problems(FIELDS, {}).size).toBe(4);
    const all = acceptAll(FIELDS, {});
    expect(problems(FIELDS, all).size).toBe(0);
    expect(Object.values(all).every((d) => d.decision === "accept")).toBe(true);
  });

  it("keeps existing corrections when accepting all", () => {
    const drafts = acceptAll(FIELDS, { 1: { decision: "correct", corrected: "2.00", reason: "x" } });
    expect(drafts[1]?.decision).toBe("accept"); // explicit accept, but the typed value is kept
    expect(drafts[1]?.corrected).toBe("2.00");
  });

  it("requires a typed value and a reason to correct, and a reason to reject", () => {
    expect(draftProblem(NET, { decision: "correct" })).toMatch(/corrected value/);
    expect(draftProblem(NET, { decision: "correct", corrected: "abc", reason: "x" })).toMatch(/amount/);
    expect(draftProblem(NET, { decision: "correct", corrected: "2.00" })).toMatch(/reason/);
    expect(draftProblem(DATE, { decision: "correct", corrected: "2026-02-30", reason: "x" })).toMatch(/date/);
    expect(draftProblem(DATE, { decision: "correct", corrected: "2026-02-28", reason: "x" })).toBeNull();
    expect(draftProblem(NET, { decision: "reject", reason: "  " })).toMatch(/reason/);
    expect(draftProblem(NET, { decision: "reject", reason: "Wrong period" })).toBeNull();
  });

  it("refuses to accept an unreadable amount that would be saved", () => {
    const unreadable = field(5, "gross_pay", { value_money_minor: null, currency: null });
    expect(draftProblem(unreadable, { decision: "accept" })).toMatch(/could not be read/);
  });

  it("only financial fields, or lines given a measure, become figures", () => {
    expect(savesFigure(NET, { decision: "accept" })).toBe(true);
    expect(savesFigure(NET, { decision: "reject", reason: "x" })).toBe(false);
    expect(savesFigure(DATE, { decision: "accept" })).toBe(false);
    expect(savesFigure(CODE, { decision: "accept" })).toBe(false);
    expect(savesFigure(LINE, { decision: "accept" })).toBe(false);
    expect(savesFigure(LINE, { decision: "accept", measure: "overtime" })).toBe(true);
  });

  it("builds the review request exactly", () => {
    const drafts = {
      1: { decision: "correct" as const, corrected: "2,451.00", reason: " Smudged " },
      2: { decision: "correct" as const, corrected: "2026-01-30", reason: "Bank holiday" },
      3: { decision: "reject" as const, reason: "Old code", measure: "x" },
      4: { decision: "accept" as const, measure: "overtime", qualifier: "" },
    };
    expect(buildReview(FIELDS, drafts, "src-1", "monthly")).toEqual({
      income_source_id: "src-1",
      frequency: "monthly",
      decisions: [
        { ordinal: 1, decision: "correct", corrected_money_minor: 245100, reason: "Smudged" },
        { ordinal: 2, decision: "correct", corrected_date: "2026-01-30", reason: "Bank holiday" },
        { ordinal: 3, decision: "reject", reason: "Old code" },
        { ordinal: 4, decision: "accept", measure: "overtime" },
      ],
    });
  });

  it("never sends an incomplete review", () => {
    expect(() => buildReview(FIELDS, { 1: { decision: "accept" } }, "src-1")).toThrow(/Field 2/);
  });

  it("formats money", () => {
    expect(formatMoney(245196)).toBe("£2,451.96");
    expect(formatMoney(-12050)).toBe("-£120.50");
    expect(formatMoney(null)).toBe("—");
  });
});
