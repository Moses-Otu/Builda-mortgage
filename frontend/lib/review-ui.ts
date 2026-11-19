// Review-screen helpers that don't touch the DOM: counts, the filter, "accept remaining",
// the save summary and keyboard shortcuts. The request itself is still built by
// lib/decisions.buildReview, unchanged.

import type { Drafts } from "./decisions";
import type { DecisionKind, ExtractedField } from "./types";

/** Confidence below this is shown on the row (always labelled an indicator, never proof). */
export const LOW_CONFIDENCE = 0.8;

export function showConfidence(confidence: number | null): boolean {
  return confidence !== null && confidence < LOW_CONFIDENCE;
}

export type Filter = "all" | "undecided" | "changed";

export interface Counts {
  total: number;
  /** Fields with a complete, valid decision. */
  complete: number;
  accepted: number;
  corrected: number;
  rejected: number;
}

export function decisionCounts(fields: ExtractedField[], drafts: Drafts, problems: Map<number, string>): Counts {
  const counts: Counts = { total: fields.length, complete: 0, accepted: 0, corrected: 0, rejected: 0 };
  for (const field of fields) {
    if (!problems.has(field.ordinal)) counts.complete += 1;
    const decision = drafts[field.ordinal]?.decision;
    if (decision === "accept") counts.accepted += 1;
    else if (decision === "correct") counts.corrected += 1;
    else if (decision === "reject") counts.rejected += 1;
  }
  return counts;
}

export function matchesFilter(
  field: ExtractedField,
  drafts: Drafts,
  problems: Map<number, string>,
  filter: Filter,
): boolean {
  if (filter === "undecided") return problems.has(field.ordinal);
  if (filter === "changed") {
    const decision = drafts[field.ordinal]?.decision;
    return decision === "correct" || decision === "reject";
  }
  return true;
}

/** Accept every field that has no decision yet; leaves existing decisions alone. */
export function acceptRemaining(fields: ExtractedField[], drafts: Drafts): Drafts {
  const next: Drafts = { ...drafts };
  for (const field of fields) {
    if (!next[field.ordinal]?.decision) next[field.ordinal] = { ...next[field.ordinal], decision: "accept" };
  }
  return next;
}

// The same words the server recognises (backend documents/review.py, _FREQUENCIES). The server
// stays the authority: this only lets the save dialog ask for a frequency before sending.
const FREQUENCY_WORDS: Record<string, string> = {
  weekly: "weekly",
  fortnightly: "fortnightly",
  twoweekly: "fortnightly",
  "2weekly": "fortnightly",
  fourweekly: "four_weekly",
  "4weekly": "four_weekly",
  monthly: "monthly",
  quarterly: "quarterly",
  annual: "annual",
  annually: "annual",
  yearly: "annual",
};

/** The frequency the payslip itself gives, as reviewed (accepted or corrected), or null. */
export function payslipFrequency(fields: ExtractedField[], drafts: Drafts): string | null {
  const field = fields.find((f) => f.field_code === "pay_frequency");
  if (!field) return null;
  const draft = drafts[field.ordinal];
  if (!draft?.decision || draft.decision === "reject") return null;
  const text = draft.decision === "correct" ? (draft.corrected ?? "") : (field.value_text ?? "");
  return FREQUENCY_WORDS[text.toLowerCase().replace(/[^a-z0-9]/g, "")] ?? null;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The confirmation shown before saving: exactly what the save will do. */
export function saveSummary(args: {
  figures: number;
  corrected: number;
  rejected: number;
  source: string;
  frequency: string | null;
}): string {
  const where = `${plural(args.figures, "figure")} will be saved to ${args.source}`;
  const when = args.frequency ? `, ${args.frequency.toLowerCase()}` : "";
  const changes = `${args.corrected} corrected, ${args.rejected} rejected`;
  return `${where}${when}. ${changes}.`;
}

export type ShortcutAction = "next" | "previous" | DecisionKind | "help";

/** The review shortcut for a key press, or null. Modifier combinations are left to the browser. */
export function shortcutFor(event: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean }): ShortcutAction | null {
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  switch (event.key) {
    case "ArrowDown":
    case "j":
      return "next";
    case "ArrowUp":
    case "k":
      return "previous";
    case "a":
    case "A":
      return "accept";
    case "c":
    case "C":
      return "correct";
    case "r":
    case "R":
      return "reject";
    case "?":
      return "help";
    default:
      return null;
  }
}

/** Shortcuts never fire while the broker is typing. */
export function isTypingTarget(target: { tagName?: string; isContentEditable?: boolean } | null): boolean {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = (target.tagName ?? "").toUpperCase();
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** The ordinal after (or before) ``current`` in ``order``; the first one when nothing is selected. */
export function step(order: number[], current: number | null, direction: 1 | -1): number | null {
  if (order.length === 0) return null;
  const index = current === null ? -1 : order.indexOf(current);
  if (index === -1) return direction === 1 ? (order[0] ?? null) : (order[order.length - 1] ?? null);
  const next = Math.min(order.length - 1, Math.max(0, index + direction));
  return order[next] ?? null;
}
