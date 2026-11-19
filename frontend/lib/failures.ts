// What a document's status means to a broker, and what to do about it. Display only: the
// statuses and failure codes come from the API unchanged.

import type { DocumentStatus } from "./types";

export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export interface StatusView {
  label: string;
  tone: StatusTone;
  /** Still being worked on by the worker (the page keeps refreshing). */
  busy: boolean;
  /** Plain-English explanation and next step, for anything that needs one. */
  explanation: string | null;
  /** Shown small, for support, when the explanation is generic. */
  supportCode: string | null;
}

const ON_OUR_SIDE = "Processing failed on our side. Upload the file again, or contact support if it keeps happening.";

const FAILURES: Record<string, string> = {
  extraction_failed:
    "We couldn't read figures from this PDF. Synthetic payslips must be uploaded exactly as generated: a PDF opened and re-saved in a viewer loses its test data.",
  too_many_pages: "This PDF has more than 10 pages. A payslip is usually one or two.",
  encrypted_pdf: "This PDF is password-protected. Upload an unprotected copy.",
  unreadable_pdf: "This file couldn't be opened as a PDF.",
  timeout: "Opening this PDF took too long. Try uploading it again.",
  statement_multiple_accounts:
    "This statement shows more than one account. Upload a statement for a single account.",
  statement_foreign_currency: "This statement isn't in pounds sterling. Only GBP statements are supported.",
  statement_no_table: "No transactions table could be found on this statement.",
};

export function documentStatusView(status: DocumentStatus | string, failureCode: string | null): StatusView {
  switch (status) {
    case "received":
      return { label: "Processing", tone: "info", busy: true, explanation: null, supportCode: null };
    case "ready":
      return { label: "Extracting", tone: "info", busy: true, explanation: null, supportCode: null };
    case "extracted":
      return { label: "Ready to review", tone: "warning", busy: false, explanation: null, supportCode: null };
    case "reviewed":
      return { label: "Reviewed", tone: "success", busy: false, explanation: null, supportCode: null };
    case "unrecognised":
      return {
        label: "Not a payslip",
        tone: "danger",
        busy: false,
        explanation: "This doesn't look like a payslip. Check that the right file was uploaded.",
        supportCode: null,
      };
    case "failed": {
      const known = failureCode ? FAILURES[failureCode] : undefined;
      return {
        label: "Failed",
        tone: "danger",
        busy: false,
        explanation: known ?? ON_OUR_SIDE,
        supportCode: known ? null : failureCode,
      };
    }
    default:
      return { label: status, tone: "neutral", busy: false, explanation: null, supportCode: null };
  }
}
