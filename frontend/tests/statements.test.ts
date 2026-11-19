import { describe, expect, it } from "vitest";

import { isAllowed } from "@/lib/allowlist";
import { draftProblem, parseSignedMoney, valueKind } from "@/lib/decisions";
import {
  accountTitle,
  buildStatementReview,
  cellEvidence,
  choiceProblem,
  choosableAccounts,
  defaultChoice,
  holdersText,
  integritySummary,
} from "@/lib/statements";
import type { AccountMatch, BankAccount, ExtractedField, Integrity } from "@/lib/types";

const CASE = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b";
const ACC = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c";
const ALEX = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5d";
const SAM = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5e";

function field(ordinal: number, field_code: string, extra: Partial<ExtractedField> = {}): ExtractedField {
  const evidence = { page_number: 1, x0: 0, y0: 0, x1: 10, y1: 10 };
  return {
    ordinal,
    field_code,
    label_text: field_code,
    raw_text: "x",
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

function account(id: string, ending: string, holders: string[], extra: Partial<BankAccount> = {}): BankAccount {
  return {
    id,
    account_ending: ending,
    label: null,
    fingerprinted: true,
    joint: holders.length > 1,
    holders: holders.map((h) => ({ applicant_id: h, added_at: "2026-11-19T00:00:00Z" })),
    statement_document_ids: [],
    created_at: "2026-11-19T00:00:00Z",
    ...extra,
  };
}

describe("statement field kinds and money", () => {
  it("knows statement dates, text and money fields", () => {
    expect(valueKind({ field_code: "statement_period_start" })).toBe("date");
    expect(valueKind({ field_code: "account_ending" })).toBe("text");
    expect(valueKind({ field_code: "opening_balance" })).toBe("money");
  });

  it("parses overdrawn balances", () => {
    expect(parseSignedMoney("-500.00")).toBe(-50_000);
    expect(parseSignedMoney("-£1,250.10")).toBe(-125_010);
    expect(parseSignedMoney("(12.00)")).toBe(-1_200);
    expect(parseSignedMoney("12.00")).toBe(1_200);
    expect(parseSignedMoney("abc")).toBeNull();
  });

  it("allows a negative correction only for balances", () => {
    const negative = { decision: "correct" as const, corrected: "-500.00", reason: "Overdrawn" };
    expect(draftProblem(field(1, "closing_balance"), negative)).toBeNull();
    expect(draftProblem(field(2, "total_paid_in"), negative)).toBe("Enter an amount such as 1,234.56");
  });

  it("needs four digits for a corrected account ending", () => {
    const f = field(3, "account_ending", { value_money_minor: null, currency: null, value_text: "4821" });
    expect(draftProblem(f, { decision: "correct", corrected: "48", reason: "x" })).toBe("Enter the last 4 digits");
    expect(draftProblem(f, { decision: "correct", corrected: "4812", reason: "x" })).toBeNull();
  });
});

describe("account choice", () => {
  const a = account(ACC, "4821", [ALEX]);
  const b = account(SAM, "4821", [SAM], { fingerprinted: false });
  const other = account(CASE, "7710", [ALEX]);
  const all = [a, b, other];
  const match = (m: Partial<AccountMatch>): AccountMatch => ({
    status: "new",
    account_id: null,
    same_ending_account_ids: [],
    ...m,
  });

  it("offers only the matched account when the fingerprint matches", () => {
    expect(choosableAccounts(all, match({ status: "existing", account_id: ACC }), "4821")).toEqual([a]);
  });

  it("offers only unverified same-ending accounts for a new fingerprint", () => {
    // a is fingerprinted (a different account); b was never verified (it may be the same one).
    expect(choosableAccounts(all, match({ status: "new", same_ending_account_ids: [ACC, SAM] }), "4821")).toEqual([b]);
  });

  it("offers same-ending accounts when the statement can't be verified", () => {
    expect(choosableAccounts(all, match({ status: "unverified" }), "4821")).toEqual([a, b]);
  });

  it("defaults sensibly and requires a holder for a new account", () => {
    expect(defaultChoice(match({ status: "existing", account_id: ACC }), ALEX)).toEqual({
      kind: "existing",
      accountId: ACC,
    });
    const fresh = defaultChoice(match({ status: "new" }), ALEX);
    expect(fresh).toEqual({ kind: "new", holderIds: [ALEX], label: "" });
    expect(choiceProblem(fresh)).toBeNull();
    expect(choiceProblem({ kind: "new", holderIds: [], label: "" })).toBe("Choose who holds the account");
  });

  it("describes holders and joint accounts", () => {
    const names: Record<string, string> = { [ALEX]: "Alex Example", [SAM]: "Sam Synthetic" };
    const joint = account(ACC, "4821", [ALEX, SAM], { label: "Current account" });
    expect(holdersText(joint, (id) => names[id] ?? "?")).toBe("joint: Alex Example and Sam Synthetic");
    expect(holdersText(a, (id) => names[id] ?? "?")).toBe("held by Alex Example");
    expect(accountTitle(joint)).toBe("Current account, account ending 4821");
  });
});

describe("the confirmation request", () => {
  const fields = [field(1, "opening_balance"), field(2, "account_ending", { value_text: "4821", value_money_minor: null })];
  const drafts = {
    1: { decision: "correct" as const, corrected: "-50.00", reason: "Overdrawn on the paper copy" },
    2: { decision: "accept" as const },
  };

  it("confirms to an existing account", () => {
    expect(buildStatementReview(fields, drafts, { kind: "existing", accountId: ACC })).toEqual({
      decisions: [
        { ordinal: 1, decision: "correct", corrected_money_minor: -5_000, reason: "Overdrawn on the paper copy" },
        { ordinal: 2, decision: "accept" },
      ],
      bank_account_id: ACC,
    });
  });

  it("creates a new account with its holders, without an income source", () => {
    const body = buildStatementReview(fields, drafts, { kind: "new", holderIds: [ALEX, SAM, ALEX], label: " " });
    expect(body.new_account).toEqual({ holder_ids: [ALEX, SAM], label: null });
    expect(body.income_source_id).toBeUndefined();
    expect(body.bank_account_id).toBeUndefined();
  });
});

describe("integrity summary and evidence", () => {
  const base: Integrity = { totals: "passed", totals_message: "", running: "passed", running_message: "", break_row: null };

  it("says plainly whether the balances add up", () => {
    expect(integritySummary(base).tone).toBe("ok");
    expect(integritySummary({ ...base, running: "discrepancy" }).text).toBe("The statement's balances don't add up.");
    expect(integritySummary({ ...base, running: "not_run" }).tone).toBe("info");
    expect(integritySummary(null).tone).toBe("info");
  });

  it("turns a cell into evidence", () => {
    expect(cellEvidence({ text: "54.20", page: 2, box: [1, 2, 3, 4] })).toEqual({
      page_number: 2,
      x0: 1,
      y0: 2,
      x1: 3,
      y1: 4,
    });
  });
});

describe("forwarder allowlist for bank accounts", () => {
  const split = (path: string) => path.split("/");
  it.each([
    ["GET", `cases/${CASE}/bank-accounts`],
    ["POST", `cases/${CASE}/bank-accounts/${ACC}/holders`],
  ])("allows %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(true));

  it.each([
    ["POST", `cases/${CASE}/bank-accounts`],
    ["GET", `cases/${CASE}/bank-accounts/${ACC}`],
    ["DELETE", `cases/${CASE}/bank-accounts/${ACC}/holders`],
    ["POST", `cases/${CASE}/bank-accounts/not-a-uuid/holders`],
    ["POST", `cases/${CASE}/bank-accounts/${ACC}/holders/${ALEX}`],
  ])("refuses %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(false));
});
