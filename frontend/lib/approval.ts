// Profile approval (Stage 3-B): case status actions, readiness wording, the approve request
// and approval wording. Display only: the server decides readiness, checks the fingerprint and
// supplies the attestation text; nothing here is a second source of those rules.

import { API } from "./api";
import { formatDate } from "./labels";
import type { ApprovalStatus, Readiness } from "./types";

export interface StatusAction {
  to: "collecting_evidence" | "in_review";
  label: string;
  /** Reopening a verified case needs a reason, and supersedes its approval. */
  reopen: boolean;
}

/** The status changes the screens offer (decision V4). Closing stays API-only. Verifying is
 * never a status change: only approving the profile verifies a case. */
export function statusActions(status: string): StatusAction[] {
  switch (status) {
    case "draft":
      return [{ to: "collecting_evidence", label: "Start collecting evidence", reopen: false }];
    case "collecting_evidence":
      return [{ to: "in_review", label: "Ready for approval", reopen: false }];
    case "in_review":
      return [{ to: "collecting_evidence", label: "Back to collecting evidence", reopen: false }];
    case "verified":
      return [{ to: "in_review", label: "Reopen…", reopen: true }];
    default:
      return [];
  }
}

export const REOPEN_EXPLANATION =
  "Reopening supersedes the current approval. It stays in the version history, unchanged; approving again creates a new version.";

export const APPROVE_ROLES = new Set(["firm_admin", "adviser"]);

export function canApprove(role: string | null | undefined): boolean {
  return role !== null && role !== undefined && APPROVE_ROLES.has(role);
}

/** "Approved, version 2, by Alex Broker on 20 Nov 2026". */
export function approvalBanner(a: ApprovalStatus): string | null {
  if (a.status !== "approved" || !a.version) return null;
  const who = a.approved_by?.name ? ` by ${a.approved_by.name}` : "";
  const when = a.approved_at ? ` on ${formatDate(a.approved_at.slice(0, 10))}` : "";
  return `Approved, version ${a.version}${who ? `,${who}` : ""}${when}`;
}

export function approvalSummary(a: ApprovalStatus): string {
  if (a.status === "approved") return approvalBanner(a) ?? "Approved";
  const last = a.superseded?.[0];
  return last ? `Not approved (version ${last.version} superseded on reopening)` : "Not approved";
}

export interface ApproveBody {
  fingerprint: string;
  attestation_version: string;
  confirmed: true;
  note?: string;
}

/** The request sends the fingerprint the screen loaded with and only the attestation's
 * version: the server supplies and stores the wording. */
export function approveBody(fingerprint: string, readiness: Readiness, note: string): ApproveBody {
  const body: ApproveBody = { fingerprint, attestation_version: readiness.attestation.version, confirmed: true };
  if (note.trim()) body.note = note.trim();
  return body;
}

/** Where a blocker's guidance points. */
export function blockerLink(caseId: string, code: string, ids: string[]): { href: string; label: string } | null {
  if (code === "documents_outstanding" && ids[0]) return { href: `/cases/${caseId}/documents/${ids[0]}`, label: "Open the first one" };
  if (code === "discrepancies_unacknowledged") return { href: `/cases/${caseId}`, label: "Open the checks" };
  if (code === "case_not_in_review") return { href: `/cases/${caseId}`, label: "Go to the case" };
  return null;
}

/** The evidence pack for one approval (Stage 3-C): a ZIP built only from that approval. */
export function packHref(caseId: string, approvalId: string): string {
  return `${API}/cases/${caseId}/profile/approvals/${approvalId}/pack`;
}
