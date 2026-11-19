import { describe, expect, it } from "vitest";

import { isAllowed } from "@/lib/allowlist";
import {
  checksSummary,
  currentDeclaredBasic,
  declaredBasicBody,
  evidenceHref,
  evidenceLabel,
  groupFindings,
  ofRuleset,
  outcomeLabel,
} from "@/lib/checks";
import { linkedOrdinal, ROW_ORDINAL_BASE } from "@/lib/statements";
import type { CheckEvidence, Finding, Observation } from "@/lib/types";

const CASE = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b";
const DOC = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c";
const SOURCE = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5d";
const KEY = "a".repeat(64);

function finding(status: Finding["status"], acknowledged = false, key = KEY): Finding {
  return {
    key,
    ruleset_version: "payslip-checks/1",
    rule_code: "YTD-GROSS",
    status,
    title: "t",
    message: "m",
    income_source_id: SOURCE,
    applicant_id: SOURCE,
    amounts: {},
    evidence: [],
    acknowledgement: acknowledged ? { note: "n", acknowledged_by_user_id: null, acknowledged_at: "2026-11-19T00:00:00Z" } : null,
  };
}

const evidence = (over: Partial<CheckEvidence> = {}): CheckEvidence => ({
  source_kind: "document",
  observation_id: null,
  field_review_id: null,
  document_id: DOC,
  document_label: "June 2026 payslip",
  field_ordinal: 14,
  label: "Gross Pay To Date",
  measure: "ytd_gross_pay",
  value_minor: 1,
  value_date: null,
  value_text: null,
  ...over,
});

describe("grouping and counts", () => {
  const groups = groupFindings([
    finding("passed"),
    finding("discrepancy", true),
    finding("discrepancy"),
    finding("not_run"),
    finding("passed"),
  ]);

  it("puts open discrepancies first and keeps acknowledged ones as discrepancies", () => {
    expect(groups.open).toHaveLength(1);
    expect(groups.acknowledged).toHaveLength(1);
    expect(groups.acknowledged[0]?.status).toBe("discrepancy");
    expect(groups.notRun).toHaveLength(1);
    expect(groups.passed).toHaveLength(2);
  });

  it("counts acknowledged discrepancies as discrepancies", () => {
    expect(checksSummary(groups)).toBe("2 discrepancies (1 acknowledged) · 2 passed · 1 could not run");
    expect(checksSummary(groupFindings([finding("discrepancy")]))).toBe(
      "1 discrepancy · 0 passed · 0 could not run",
    );
  });
});

describe("evidence", () => {
  it("links document evidence to the payslip with the field selected", () => {
    expect(evidenceHref(CASE, evidence())).toBe(`/cases/${CASE}/documents/${DOC}?field=14`);
    expect(evidenceLabel(evidence())).toBe("June 2026 · Gross Pay To Date");
  });

  it("doesn't link declared figures", () => {
    const declared = evidence({ source_kind: "declared", document_id: null, label: "Declared basic salary (annual)" });
    expect(evidenceHref(CASE, declared)).toBeNull();
    expect(evidenceLabel(declared)).toBe("Declared basic salary (annual)");
  });
});

describe("bank statement checks", () => {
  it("separates the two rule sets", () => {
    const bank = { ...finding("discrepancy"), ruleset_version: "bank-checks/1", rule_code: "SALARY-CREDIT" };
    const all = [finding("passed"), bank];
    expect(ofRuleset(all, "payslip-checks")).toEqual([all[0]]);
    expect(ofRuleset(all, "bank-checks")).toEqual([bank]);
    expect(ofRuleset(all, "bank")).toEqual([]);
  });

  it("labels salary outcomes, never presenting a near match as received", () => {
    expect(outcomeLabel("near")).toBe("Near match · not confirmed");
    expect(outcomeLabel("multiple")).toBe("More than one possible credit");
    expect(outcomeLabel("none")).toBe("No credit found");
    expect(outcomeLabel("exact")).toBeNull();
    expect(outcomeLabel(null)).toBeNull();
  });

  it("links a statement row to the statement with the row selected", () => {
    const row = evidence({
      source_kind: "transaction",
      document_label: "June 2026 statement",
      field_ordinal: null,
      row_number: 7,
      label: "Credit, 30 Jun 2026",
    });
    expect(evidenceHref(CASE, row)).toBe(`/cases/${CASE}/documents/${DOC}?row=7`);
    expect(evidenceLabel(row)).toBe("June 2026 statement · Credit, 30 Jun 2026");
    const value = evidence({ source_kind: "statement", document_label: "June 2026 statement", field_ordinal: 4, label: "Opening balance" });
    expect(evidenceHref(CASE, value)).toBe(`/cases/${CASE}/documents/${DOC}?field=4`);
    expect(evidenceLabel(value)).toBe("June 2026 statement · Opening balance");
  });

  it("selects the linked row or field when the statement opens", () => {
    expect(linkedOrdinal("?row=7")).toBe(ROW_ORDINAL_BASE + 7);
    expect(linkedOrdinal("?field=4")).toBe(4);
    expect(linkedOrdinal("?row=7&field=4")).toBe(ROW_ORDINAL_BASE + 7);
    for (const bad of ["", "?row=0", "?row=-2", "?row=x", "?field=1.5", `?field=${ROW_ORDINAL_BASE}`]) {
      expect(linkedOrdinal(bad)).toBeNull();
    }
  });
});

describe("declared basic salary", () => {
  it("builds the declared, annual basic_pay observation", () => {
    expect(declaredBasicBody(SOURCE, "45,000", null)).toEqual({
      income_source_id: SOURCE,
      measure: "basic_pay",
      value: { type: "money", amount_minor: 4_500_000 },
      frequency: "annual",
      source_kind: "declared",
    });
  });

  it("supersedes the current value instead of editing it", () => {
    expect(declaredBasicBody(SOURCE, "£48,000.50", "prev")).toMatchObject({
      value: { type: "money", amount_minor: 4_800_050 },
      supersedes_observation_id: "prev",
    });
  });

  it("refuses empty, zero and malformed amounts", () => {
    for (const bad of ["", "0", "abc", "-5", "1.234"]) expect(declaredBasicBody(SOURCE, bad, null)).toBeNull();
  });

  it("finds the current declared annual basic for an income source", () => {
    const o = (id: string, over: Partial<Observation> = {}): Observation => ({
      id,
      income_source_id: SOURCE,
      measure: "basic_pay",
      source_kind: "declared",
      frequency: "annual",
      value: { type: "money", amount_minor: 1 },
      retired_at: null,
      recorded_at: `2026-11-19T00:00:0${id}Z`,
      ...over,
    });
    const list = [
      o("1", { retired_at: "2026-11-19T01:00:00Z" }),
      o("2"),
      o("3", { source_kind: "document" }),
      o("4", { measure: "gross_pay" }),
      o("5", { frequency: "monthly" }),
    ];
    expect(currentDeclaredBasic(list, SOURCE)?.id).toBe("2");
    expect(currentDeclaredBasic(list, "other")).toBeNull();
  });
});

describe("forwarder allowlist for checks", () => {
  const split = (path: string) => path.split("/");
  it.each([
    ["GET", `cases/${CASE}/checks`],
    ["POST", `cases/${CASE}/checks/${KEY}/acknowledgement`],
    ["POST", `cases/${CASE}/financial/observations`],
  ])("allows %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(true));

  it.each([
    ["POST", `cases/${CASE}/checks`],
    ["GET", `cases/${CASE}/checks/${KEY}/acknowledgement`],
    ["POST", `cases/${CASE}/checks/${KEY.toUpperCase()}/acknowledgement`],
    ["POST", `cases/${CASE}/checks/${KEY.slice(1)}/acknowledgement`],
    ["POST", `cases/${CASE}/checks/${CASE}/acknowledgement`],
    ["POST", `cases/${CASE}/checks/${KEY}`],
    ["DELETE", `cases/${CASE}/checks/${KEY}/acknowledgement`],
    ["POST", `cases/${CASE}/financial/observations/${DOC}/retirement`],
  ])("refuses %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(false));
});
