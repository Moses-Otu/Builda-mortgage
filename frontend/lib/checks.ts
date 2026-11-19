// The case page's Checks card: grouping, counts, evidence links and the declared salary.
// Display only: the findings, their statuses and keys come from the API unchanged.

import { parseMoney } from "./decisions";
import type { CheckEvidence, Finding, Observation } from "./types";

export interface CheckGroups {
  /** Discrepancies not yet acknowledged, first. */
  open: Finding[];
  /** Discrepancies a broker has acknowledged: still discrepancies. */
  acknowledged: Finding[];
  notRun: Finding[];
  passed: Finding[];
}

export function groupFindings(findings: Finding[]): CheckGroups {
  const groups: CheckGroups = { open: [], acknowledged: [], notRun: [], passed: [] };
  for (const f of findings) {
    if (f.status === "discrepancy") (f.acknowledgement ? groups.acknowledged : groups.open).push(f);
    else if (f.status === "not_run") groups.notRun.push(f);
    else groups.passed.push(f);
  }
  return groups;
}

/** "3 discrepancies (1 acknowledged) · 8 passed · 2 could not run". Acknowledged ones are
 * still counted as discrepancies. */
export function checksSummary(groups: CheckGroups): string {
  const discrepancies = groups.open.length + groups.acknowledged.length;
  const parts = [
    `${discrepancies} ${discrepancies === 1 ? "discrepancy" : "discrepancies"}` +
      (groups.acknowledged.length ? ` (${groups.acknowledged.length} acknowledged)` : ""),
    `${groups.passed.length} passed`,
    `${groups.notRun.length} could not run`,
  ];
  return parts.join(" · ");
}

/** Where an evidence chip goes: the payslip's review screen with that field selected. */
export function evidenceHref(caseId: string, e: CheckEvidence): string | null {
  if (e.source_kind !== "document" || !e.document_id) return null;
  const field = e.field_ordinal !== null ? `?field=${e.field_ordinal}` : "";
  return `/cases/${caseId}/documents/${e.document_id}${field}`;
}

export function evidenceLabel(e: CheckEvidence): string {
  if (e.source_kind === "declared") return e.label;
  const where = e.document_label ? e.document_label.replace(/ payslip$/, "") : "Payslip";
  return `${where} · ${e.label}`;
}

/** The current declared annual basic salary on an income source, if any. */
export function currentDeclaredBasic(observations: Observation[], incomeSourceId: string): Observation | null {
  const current = observations.filter(
    (o) =>
      o.income_source_id === incomeSourceId &&
      o.source_kind === "declared" &&
      o.measure === "basic_pay" &&
      o.frequency === "annual" &&
      !o.retired_at,
  );
  current.sort((a, b) => a.recorded_at.localeCompare(b.recorded_at));
  return current[current.length - 1] ?? null;
}

/** The request body for a declared annual basic salary, or null if the amount isn't valid.
 * A change supersedes the current value (the API retires it in the same transaction). */
export function declaredBasicBody(
  incomeSourceId: string,
  pounds: string,
  supersedes: string | null,
): Record<string, unknown> | null {
  const minor = parseMoney(pounds);
  if (minor === null || minor <= 0) return null;
  const body: Record<string, unknown> = {
    income_source_id: incomeSourceId,
    measure: "basic_pay",
    value: { type: "money", amount_minor: minor },
    frequency: "annual",
    source_kind: "declared",
  };
  if (supersedes) body.supersedes_observation_id = supersedes;
  return body;
}
