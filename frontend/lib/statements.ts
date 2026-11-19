// Bank statement confirmation (Stage 2-B): which account, who holds it, and the request body.
// Confirmation (the extracted information matches the document) is separate from validation
// (the balance checks): confirming never changes or hides a balance discrepancy.

import { buildDecisions, type Drafts } from "./decisions";
import type { AccountMatch, BankAccount, Cell, Evidence, ExtractedField, Integrity, ReviewIn } from "./types";

export type AccountChoice =
  | { kind: "existing"; accountId: string }
  | { kind: "new"; holderIds: string[]; label: string };

/** Existing accounts this statement may be confirmed to (the server enforces the same rules).
 * A fingerprint match allows only that account. A new fingerprint allows only same-ending
 * accounts that were never verified by a fingerprint (they may be the same account); a
 * fingerprinted account with the same ending is a different account. An unverified statement
 * may go to any account with the same ending. */
export function choosableAccounts(accounts: BankAccount[], match: AccountMatch, ending: string | null): BankAccount[] {
  if (match.status === "existing") return accounts.filter((a) => a.id === match.account_id);
  const sameEnding = accounts.filter((a) => ending !== null && a.account_ending === ending);
  if (match.status === "new") return sameEnding.filter((a) => !a.fingerprinted);
  return sameEnding;
}

export function defaultChoice(match: AccountMatch, filingApplicantId: string | null): AccountChoice {
  if (match.status === "existing" && match.account_id) return { kind: "existing", accountId: match.account_id };
  return { kind: "new", holderIds: filingApplicantId ? [filingApplicantId] : [], label: "" };
}

export function choiceProblem(choice: AccountChoice): string | null {
  if (choice.kind === "new" && choice.holderIds.length === 0) return "Choose who holds the account";
  return null;
}

export function buildStatementReview(fields: ExtractedField[], drafts: Drafts, choice: AccountChoice): ReviewIn {
  const decisions = buildDecisions(fields, drafts);
  if (choice.kind === "existing") return { decisions, bank_account_id: choice.accountId };
  const label = choice.label.trim();
  return { decisions, new_account: { holder_ids: [...new Set(choice.holderIds)], label: label || null } };
}

export function cellEvidence(cell: Cell): Evidence {
  const [x0, y0, x1, y1] = cell.box;
  return { page_number: cell.page, x0, y0, x1, y1 };
}

/** Transaction highlights use ordinals above any field's, so rows and fields never collide. */
export const ROW_ORDINAL_BASE = 10_000;

export function holdersText(account: BankAccount, nameOf: (id: string) => string): string {
  const names = account.holders.map((h) => nameOf(h.applicant_id));
  const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : (names[0] ?? "");
  return account.joint ? `joint: ${list}` : `held by ${list}`;
}

export function accountTitle(account: BankAccount): string {
  return `${account.label ? `${account.label}, ` : ""}account ending ${account.account_ending}`;
}

/** One line for the confirm dialog and the audit trail: what the balance checks found. */
export function integritySummary(integrity: Integrity | null | undefined): { tone: "ok" | "warn" | "info"; text: string } {
  if (!integrity) return { tone: "info", text: "The balance checks aren't available." };
  if (integrity.totals === "discrepancy" || integrity.running === "discrepancy") {
    return { tone: "warn", text: "The statement's balances don't add up." };
  }
  if (integrity.totals === "passed" && integrity.running === "passed") {
    return { tone: "ok", text: "The statement's balances add up." };
  }
  return { tone: "info", text: "The balances could only be partly checked." };
}
