import { describe, expect, it } from "vitest";

import { caseSteps } from "@/lib/case-steps";
import { problems } from "@/lib/decisions";
import { documentStatusView } from "@/lib/failures";
import { formatBytes, frequencyLabel, measureLabel, purposeLabel, roleLabel } from "@/lib/labels";
import {
  acceptRemaining,
  decisionCounts,
  isTypingTarget,
  matchesFilter,
  payslipFrequency,
  saveSummary,
  shortcutFor,
  showConfidence,
  step,
} from "@/lib/review-ui";
import type { DocumentOut, ExtractedField, IncomeSource } from "@/lib/types";

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

const FIELDS = [field(1, "net_pay"), field(2, "gross_pay"), field(3, "income_tax"), field(4, "basic_pay")];

describe("document status in plain English", () => {
  it.each([
    ["extraction_failed", "re-saved in a viewer"],
    ["too_many_pages", "more than 10 pages"],
    ["encrypted_pdf", "password-protected"],
    ["unreadable_pdf", "couldn't be opened as a PDF"],
    ["timeout", "took too long"],
    ["statement_multiple_accounts", "more than one account"],
    ["statement_foreign_currency", "pounds sterling"],
    ["statement_no_table", "No transactions table"],
  ])("explains %s without showing the code", (code, words) => {
    const view = documentStatusView("failed", code);
    expect(view.label).toBe("Failed");
    expect(view.tone).toBe("danger");
    expect(view.explanation).toContain(words);
    expect(view.supportCode).toBeNull();
  });

  it.each(["hash_mismatch", "missing_original", "retries_exhausted", "something_new"])(
    "gives a generic message and keeps %s for support",
    (code) => {
      const view = documentStatusView("failed", code);
      expect(view.explanation).toContain("on our side");
      expect(view.supportCode).toBe(code);
    },
  );

  it("labels the other statuses", () => {
    expect(documentStatusView("received", null)).toMatchObject({ label: "Processing", busy: true });
    expect(documentStatusView("ready", null)).toMatchObject({ label: "Extracting", busy: true });
    expect(documentStatusView("extracted", null)).toMatchObject({ label: "Ready to review", busy: false });
    expect(documentStatusView("reviewed", null)).toMatchObject({ label: "Reviewed", tone: "success" });
    expect(documentStatusView("unrecognised", null).explanation).toContain("doesn't look like a payslip");
  });
});

describe("decision counts and the filter", () => {
  const drafts = {
    1: { decision: "accept" as const },
    2: { decision: "correct" as const, corrected: "10.00" }, // no reason yet: incomplete
    3: { decision: "reject" as const, reason: "Not on the payslip" },
  };
  const open = problems(FIELDS, drafts);

  it("counts chosen decisions and complete ones separately", () => {
    expect(decisionCounts(FIELDS, drafts, open)).toEqual({
      total: 4,
      complete: 2, // net pay accepted, income tax rejected with a reason
      accepted: 1,
      corrected: 1,
      rejected: 1,
    });
  });

  it("filters undecided (incomplete) and changed fields", () => {
    const pick = (filter: "all" | "undecided" | "changed") =>
      FIELDS.filter((f) => matchesFilter(f, drafts, open, filter)).map((f) => f.ordinal);
    expect(pick("all")).toEqual([1, 2, 3, 4]);
    expect(pick("undecided")).toEqual([2, 4]);
    expect(pick("changed")).toEqual([2, 3]);
  });

  it("accepts only the fields with no decision", () => {
    const next = acceptRemaining(FIELDS, drafts);
    expect(next[2]).toEqual(drafts[2]); // the correction is kept
    expect(next[3]).toEqual(drafts[3]); // the rejection is kept
    expect(next[4]).toEqual({ decision: "accept" });
  });
});

describe("save summary", () => {
  it("says exactly what will be saved", () => {
    expect(
      saveSummary({ figures: 9, corrected: 1, rejected: 1, source: "Employment income — Sam Synthetic", frequency: "Monthly" }),
    ).toBe("9 figures will be saved to Employment income — Sam Synthetic, monthly. 1 corrected, 1 rejected.");
  });

  it("handles one figure and a frequency not chosen yet", () => {
    expect(saveSummary({ figures: 1, corrected: 0, rejected: 0, source: "X", frequency: null })).toBe(
      "1 figure will be saved to X. 0 corrected, 0 rejected.",
    );
  });
});

describe("keyboard shortcuts", () => {
  const key = (k: string, mods: Partial<{ ctrlKey: boolean; metaKey: boolean; altKey: boolean }> = {}) =>
    shortcutFor({ key: k, ctrlKey: false, metaKey: false, altKey: false, ...mods });

  it("maps keys to actions", () => {
    expect(key("ArrowDown")).toBe("next");
    expect(key("j")).toBe("next");
    expect(key("ArrowUp")).toBe("previous");
    expect(key("a")).toBe("accept");
    expect(key("C")).toBe("correct");
    expect(key("r")).toBe("reject");
    expect(key("?")).toBe("help");
    expect(key("x")).toBeNull();
  });

  it("leaves modifier combinations to the browser", () => {
    expect(key("a", { ctrlKey: true })).toBeNull();
    expect(key("c", { metaKey: true })).toBeNull();
    expect(key("r", { altKey: true })).toBeNull();
  });

  it("never fires while typing", () => {
    expect(isTypingTarget({ tagName: "INPUT" })).toBe(true);
    expect(isTypingTarget({ tagName: "textarea" })).toBe(true);
    expect(isTypingTarget({ tagName: "SELECT" })).toBe(true);
    expect(isTypingTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
    expect(isTypingTarget({ tagName: "BUTTON" })).toBe(false);
    expect(isTypingTarget({ tagName: "BODY" })).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });

  it("steps through fields and stops at the ends", () => {
    expect(step([3, 5, 8], null, 1)).toBe(3);
    expect(step([3, 5, 8], null, -1)).toBe(8);
    expect(step([3, 5, 8], 3, 1)).toBe(5);
    expect(step([3, 5, 8], 8, 1)).toBe(8);
    expect(step([3, 5, 8], 3, -1)).toBe(3);
    expect(step([3, 5, 8], 99, 1)).toBe(3); // filtered out of view: start again
    expect(step([], 3, 1)).toBeNull();
  });
});

describe("the payslip's own frequency", () => {
  const freq = field(9, "pay_frequency", { value_money_minor: null, currency: null, value_text: "Monthly" });

  it("is read from the accepted field, in the server's words", () => {
    expect(payslipFrequency([freq], { 9: { decision: "accept" } })).toBe("monthly");
    expect(payslipFrequency([{ ...freq, value_text: "4-Weekly" }], { 9: { decision: "accept" } })).toBe("four_weekly");
    expect(payslipFrequency([{ ...freq, value_text: "Two Weekly" }], { 9: { decision: "accept" } })).toBe("fortnightly");
  });
  it("follows a correction", () => {
    expect(payslipFrequency([freq], { 9: { decision: "correct", corrected: "weekly" } })).toBe("weekly");
  });
  it("is unknown when rejected, undecided, unrecognised or absent", () => {
    expect(payslipFrequency([freq], { 9: { decision: "reject", reason: "x" } })).toBeNull();
    expect(payslipFrequency([freq], {})).toBeNull();
    expect(payslipFrequency([{ ...freq, value_text: "Every so often" }], { 9: { decision: "accept" } })).toBeNull();
    expect(payslipFrequency(FIELDS, {})).toBeNull();
  });
});

describe("confidence indicator", () => {
  it("is shown only when low", () => {
    expect(showConfidence(null)).toBe(false);
    expect(showConfidence(0.95)).toBe(false);
    expect(showConfidence(0.8)).toBe(false);
    expect(showConfidence(0.79)).toBe(true);
  });
});

describe("case steps", () => {
  const source: IncomeSource = { id: "s", applicant_id: "a", kind: "employment", name: null, withdrawn_at: null };
  const doc = (status: DocumentOut["status"]): DocumentOut => ({
    id: status,
    case_id: "c",
    applicant_id: "a",
    document_type: "payslip",
    status,
    failure_code: null,
    page_count: 1,
    version: 1,
    created_at: "2026-11-19T00:00:00Z",
    original: { id: "o", original_filename: "p.pdf", size_bytes: 1 },
  });
  const states = (sources: IncomeSource[], docs: DocumentOut[]) => caseSteps(sources, docs).map((s) => s.state);

  it("starts at the income source", () => {
    expect(states([], [])).toEqual(["current", "upcoming", "upcoming"]);
  });
  it("moves on to upload, then review", () => {
    expect(states([source], [])).toEqual(["done", "current", "upcoming"]);
    expect(states([source], [doc("extracted")])).toEqual(["done", "done", "current"]);
  });
  it("is complete once everything uploaded is reviewed", () => {
    expect(states([source], [doc("reviewed")])).toEqual(["done", "done", "done"]);
  });
  it("ignores failed uploads and withdrawn sources", () => {
    expect(states([{ ...source, withdrawn_at: "2026-11-19T00:00:00Z" }], [doc("failed")])).toEqual([
      "current",
      "upcoming",
      "upcoming",
    ]);
  });
  it("ignores bank statements (they have their own confirmation)", () => {
    const statement = { ...doc("extracted"), document_type: "bank_statement" };
    expect(states([source], [statement])).toEqual(["done", "current", "upcoming"]);
  });
  it("describes what is waiting", () => {
    expect(caseSteps([source], [doc("extracted"), doc("extracted")])[2]?.detail).toBe("2 payslips waiting for review");
  });
});

describe("labels", () => {
  it("turns codes into words and keeps unknown codes readable", () => {
    expect(roleLabel("firm_admin")).toBe("Firm admin");
    expect(purposeLabel("further_advance")).toBe("Further advance");
    expect(frequencyLabel("four_weekly")).toBe("Four-weekly");
    expect(measureLabel("ytd_gross_pay")).toBe("Gross pay to date");
    expect(measureLabel("brand_new_code")).toBe("Brand new code");
    expect(formatBytes(7894)).toBe("8 KB");
  });
});
