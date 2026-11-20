// The financial profile screen (Stage 3-A): grouping figures for display, their values, origins
// and evidence links. Display only: the figures, their order and provenance come from the API
// unchanged (rule profile/1). Nothing here chooses, sums, averages or annualises a figure.

import { formatMoney } from "./decisions";
import { formatDate, measureLabel } from "./labels";
import type { ApprovalStatus, Corroboration, DocumentarySupport, Figure, FigureValue, Origin } from "./types";

export const ORIGINS: Record<Origin, { mark: string; label: string; description: string }> = {
  extracted: { mark: "E", label: "Extracted", description: "Read from the document and accepted as read" },
  corrected: { mark: "C", label: "Corrected", description: "Read from the document and corrected by the broker, with a reason" },
  declared: { mark: "D", label: "Declared", description: "What the applicant says" },
  broker_entry: { mark: "B", label: "Broker entry", description: "Entered by the broker" },
};

/** Payslip columns in reading order: earnings, deductions, net, then year to date. */
const MEASURE_ORDER = [
  "gross_pay",
  "basic_pay",
  "overtime",
  "commission",
  "bonus",
  "allowance",
  "income_tax",
  "national_insurance",
  "pension_contribution",
  "student_loan_deduction",
  "net_pay",
  "ytd_gross_pay",
  "ytd_income_tax",
];

export function measureRank(code: string): number {
  const i = MEASURE_ORDER.indexOf(code);
  return i === -1 ? MEASURE_ORDER.length : i;
}

export function formatValue(value: FigureValue): string {
  switch (value.type) {
    case "money":
    case "signed_money":
      return formatMoney(value.amount_minor ?? null);
    case "rate":
    case "proportion":
      return `${value.percent}%`;
    case "duration":
      return `${value.months} months`;
    case "count":
      return String(value.count);
    case "date":
      return value.date ? formatDate(value.date) : "";
    default:
      return "";
  }
}

/** A column heading: the measure, with its qualifier when it has one. */
export function columnLabel(f: Pick<Figure, "measure" | "qualifier">): string {
  return f.qualifier ? `${measureLabel(f.measure)} (${f.qualifier.replaceAll("_", " ")})` : measureLabel(f.measure);
}

const columnKey = (f: Pick<Figure, "measure" | "qualifier">) => `${f.measure}|${f.qualifier ?? ""}`;

export interface PeriodRow {
  key: string;
  label: string;
  range: string;
  cells: Record<string, Figure[]>;
}

export interface PeriodTable {
  columns: { key: string; label: string }[];
  rows: PeriodRow[];
}

const monthYear = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });

/** One row per pay period (by its dates), one column per measure and qualifier. Figures
 * without a period are not in the table: see {@link undatedFigures}. */
export function periodTable(figures: Figure[]): PeriodTable {
  const dated = figures.filter((f) => f.period_start || f.period_end);
  const columns = new Map<string, { key: string; label: string; rank: number }>();
  const rows = new Map<string, PeriodRow>();
  for (const f of dated) {
    const key = `${f.period_start ?? ""}..${f.period_end ?? ""}`;
    const end = f.period_end ?? f.period_start ?? "";
    const range = [f.period_start, f.period_end].filter(Boolean).map((d) => formatDate(d as string)).join(" to ");
    const row = rows.get(key) ?? { key, label: monthYear(end), range, cells: {} };
    const col = columnKey(f);
    (row.cells[col] ??= []).push(f);
    rows.set(key, row);
    if (!columns.has(col)) columns.set(col, { key: col, label: columnLabel(f), rank: measureRank(f.measure) });
  }
  return {
    columns: [...columns.values()]
      .sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label))
      .map(({ key, label }) => ({ key, label })),
    rows: [...rows.values()].sort((a, b) => a.key.localeCompare(b.key)),
  };
}

export function undatedFigures(figures: Figure[]): Figure[] {
  return figures.filter((f) => !f.period_start && !f.period_end);
}

export function figuresFor(figures: Figure[], itemId: string): Figure[] {
  return figures.filter((f) => f.item_id === itemId);
}

/** "Annual", "as of 3 Jun 2026", "tax year 2026-27": what an undated figure applies to. */
export function figureScope(f: Figure): string {
  const parts: string[] = [];
  if (f.frequency) parts.push(f.frequency.replaceAll("_", "-"));
  if (f.tax_year) parts.push(`tax year ${f.tax_year}`);
  if (f.as_of_date) parts.push(`as of ${formatDate(f.as_of_date)}`);
  return parts.join(" · ");
}

/** The document screen with the field selected and its box highlighted. */
export function evidenceHref(caseId: string, f: Figure): string | null {
  if (!f.evidence) return null;
  return `/cases/${caseId}/documents/${f.evidence.document_id}?field=${f.evidence.field.ordinal}`;
}

/** One plain sentence on where a figure came from. */
export function originSentence(f: Figure): string {
  const e = f.evidence;
  if (!e) return f.origin === "declared" ? "Declared by the applicant." : "Entered by the broker.";
  const where = `${e.file_name ?? "the document"}, page ${e.field.value_evidence.page_number}, “${e.field.label_text}”`;
  if (f.origin === "corrected") {
    const asRead = e.field.raw_text ? ` (read as ${e.field.raw_text})` : "";
    return `Corrected by the broker from ${where}${asRead}.`;
  }
  return `Extracted from ${where}, accepted as read.`;
}

export function originCounts(figures: Figure[]): Record<Origin, number> {
  const out: Record<Origin, number> = { extracted: 0, corrected: 0, declared: 0, broker_entry: 0 };
  for (const f of figures) out[f.origin] += 1;
  return out;
}

// --- "Supported by" (step 3-A2) ---
// Five kinds, kept separate, each stating its own scope. No wording counts evidence: the
// quantity of evidence is not a measure of reliability.

export const NONE_RECORDED = "None recorded";

export const SUPPORT_KINDS = {
  declared: { title: "Declared by the applicant", note: "What the applicant says; not verification." },
  documentary: { title: "Documentary evidence (broker-reviewed)", note: "Each document supports only the figures it carries, for its period." },
  corroborating: { title: "Corroborating evidence", note: "Confirms one fact without supplying the figures." },
  findings: { title: "Reconciliation findings", note: "Where the evidence agrees, disagrees or can't be compared." },
  approval: { title: "Broker approval", note: "A broker's attestation of the whole profile; not a property of the evidence." },
} as const;

const inline = (label: string) => (label.startsWith("National Insurance") ? label : label.charAt(0).toLowerCase() + label.slice(1));

/** "May 2026" when the figures cover one calendar month, else the date range. */
export function periodText(start: string | null, end: string | null): string | null {
  if (!start && !end) return null;
  if (start && end && start.slice(0, 7) === end.slice(0, 7)) return monthYear(end);
  return [start, end].filter(Boolean).map((d) => formatDate(d as string)).join(" to ");
}

const DOCUMENT_TYPES: Record<string, string> = { payslip: "Payslip", bank_statement: "Bank statement" };

/** "Payslip, May 2026: gross pay, basic pay, … net pay (net pay corrected)". */
export function documentaryLine(d: DocumentarySupport): string {
  const kind = DOCUMENT_TYPES[d.document_type] ?? measureLabel(d.document_type);
  if (!d.figures_saved) return `${kind}, reviewed; no figures saved`;
  const period = periodText(d.period_start, d.period_end);
  const measures = [...d.measures].sort((a, b) => measureRank(a) - measureRank(b) || a.localeCompare(b));
  const carried = measures.map((m) => inline(measureLabel(m))).join(", ");
  const corrected = d.corrected_measures.length
    ? ` (${d.corrected_measures.map((m) => inline(measureLabel(m))).join(", ")} corrected)`
    : "";
  return `${kind}${period ? `, ${period}` : ""}: ${carried}${corrected}`;
}

/** "Net pay for May 2026 received on 29 May 2026 into account ending 4821 (joint: A and B)". */
export function corroborationLine(c: Corroboration, payslipMonth: string | null, nameOf: (id: string) => string): string {
  const names = c.holder_ids.map(nameOf);
  const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : (names[0] ?? "");
  const holders = c.joint ? `joint: ${list}` : `held by ${list}`;
  const label = c.account_label ? `${c.account_label}, ` : "";
  const when = c.credit_date ? ` on ${formatDate(c.credit_date)}` : "";
  const amount = c.amount_minor !== null ? ` (${formatValue({ type: "money", amount_minor: c.amount_minor })})` : "";
  return `Net pay${payslipMonth ? ` for ${payslipMonth}` : ""} received${when}${amount} into account ending ${c.account_ending} (${label}${holders})`;
}

export function approvalText(a: ApprovalStatus): string {
  switch (a.status) {
    case "approved":
      return "Approved";
    case "superseded":
      return "Superseded on reopening";
    default:
      return "Not approved";
  }
}
