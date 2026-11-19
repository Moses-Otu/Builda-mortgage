// Building and checking the broker's decisions before they are sent. The server is the
// authority and re-checks everything; this only keeps "Save" honest (disabled until every
// field has a complete decision) and gives early, specific messages.

import type { DecisionKind, ExtractedField, FieldDecisionIn, ReviewIn } from "./types";

const DATE_CODES = new Set(["pay_date", "pay_period_start", "pay_period_end"]);
const TEXT_CODES = new Set(["pay_frequency", "tax_code"]);
/** Fields saved as financial figures (the same list the server uses). */
export const FIGURE_CODES = new Set([
  "gross_pay",
  "net_pay",
  "basic_pay",
  "overtime",
  "commission",
  "bonus",
  "allowance",
  "income_tax",
  "national_insurance",
  "pension_contribution",
  "student_loan_deduction",
  "ytd_gross_pay",
  "ytd_income_tax",
]);
export const QUALIFIED = new Set(["bonus", "allowance"]);

export type ValueKind = "money" | "date" | "text";

export function valueKind(field: Pick<ExtractedField, "field_code">): ValueKind {
  if (field.field_code && DATE_CODES.has(field.field_code)) return "date";
  if (field.field_code && TEXT_CODES.has(field.field_code)) return "text";
  return "money";
}

export interface Draft {
  decision?: DecisionKind;
  /** As typed: "1,234.56" for money, "2026-01-31" for dates, free text otherwise. */
  corrected?: string;
  reason?: string;
  measure?: string;
  qualifier?: string;
}

export type Drafts = Record<number, Draft>;

/** "£1,234.56" or "1234.5" -> 123456 pence; anything else -> null. */
export function parseMoney(text: string): number | null {
  const match = /^\s*£?\s*(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{1,2}))?\s*$/.exec(text);
  if (!match || match[1] === undefined) return null;
  const pounds = Number(match[1].replace(/,/g, ""));
  const pence = Number((match[2] ?? "0").padEnd(2, "0"));
  return pounds * 100 + pence;
}

function isIsoDate(text: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
  const date = new Date(`${text}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === text;
}

/** Whether the field becomes a figure with this draft (coded, or a measure assigned). */
export function savesFigure(field: ExtractedField, draft: Draft): boolean {
  if (!draft.decision || draft.decision === "reject") return false;
  if (field.field_code && FIGURE_CODES.has(field.field_code)) return true;
  return field.field_code === null && Boolean(draft.measure);
}

/** The problem with one draft, or null when it is complete and valid. */
export function draftProblem(field: ExtractedField, draft: Draft | undefined): string | null {
  if (!draft?.decision) return "Choose accept, correct or reject";
  const reason = (draft.reason ?? "").trim();
  if (draft.decision === "correct") {
    const value = (draft.corrected ?? "").trim();
    const kind = valueKind(field);
    if (!value) return "Enter the corrected value";
    if (kind === "money" && parseMoney(value) === null) return "Enter an amount such as 1,234.56";
    if (kind === "date" && !isIsoDate(value)) return "Enter a valid date";
    if (!reason) return "Give a reason for the correction";
  }
  if (draft.decision === "reject" && !reason) return "Give a reason for rejecting";
  if (draft.decision === "accept" && savesFigure(field, draft) && valueKind(field) === "money") {
    if (field.value_money_minor === null) return "The amount could not be read: correct or reject it";
  }
  return null;
}

export function acceptAll(fields: ExtractedField[], drafts: Drafts): Drafts {
  const next: Drafts = { ...drafts };
  for (const field of fields) {
    next[field.ordinal] = { ...next[field.ordinal], decision: "accept" };
  }
  return next;
}

export function problems(fields: ExtractedField[], drafts: Drafts): Map<number, string> {
  const found = new Map<number, string>();
  for (const field of fields) {
    const problem = draftProblem(field, drafts[field.ordinal]);
    if (problem) found.set(field.ordinal, problem);
  }
  return found;
}

export function buildReview(
  fields: ExtractedField[],
  drafts: Drafts,
  incomeSourceId: string,
  frequency?: string,
): ReviewIn {
  const decisions: FieldDecisionIn[] = fields.map((field) => {
    const draft = drafts[field.ordinal];
    if (!draft?.decision || draftProblem(field, draft)) {
      throw new Error(`Field ${field.ordinal} has no complete decision`);
    }
    const out: FieldDecisionIn = { ordinal: field.ordinal, decision: draft.decision };
    const reason = (draft.reason ?? "").trim();
    if (reason && draft.decision !== "accept") out.reason = reason;
    if (draft.decision === "correct") {
      const value = (draft.corrected ?? "").trim();
      const kind = valueKind(field);
      if (kind === "money") out.corrected_money_minor = parseMoney(value) ?? undefined;
      else if (kind === "date") out.corrected_date = value;
      else out.corrected_text = value;
    }
    if (draft.decision !== "reject") {
      if (field.field_code === null && draft.measure) out.measure = draft.measure;
      if (draft.qualifier) out.qualifier = draft.qualifier;
    }
    return out;
  });
  const review: ReviewIn = { income_source_id: incomeSourceId, decisions };
  if (frequency) review.frequency = frequency;
  return review;
}

export function formatMoney(minor: number | null): string {
  if (minor === null) return "—";
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  const pounds = Math.floor(abs / 100).toLocaleString("en-GB");
  return `${sign}£${pounds}.${String(abs % 100).padStart(2, "0")}`;
}
